#!/usr/bin/env python3
"""Probe a Stage 1 source file and stamp gate G0.

Runs ffprobe for width/height/fps/duration/has-audio, hashes the local file,
and writes source_manifest.json into the workdir. Stamps G0 (design section 4):
pass only when source_type is twitch_vod or youtube_full_vod AND the probed
height is >= 1080 AND the file has an audio stream. A compilation fails G0;
there is never a fallback to a compilation.

Usage:
    python3 probe_source.py --local-file work/source/vod_window.mp4 \\
        --vod-url https://www.twitch.tv/videos/1234567890 \\
        --source-type twitch_vod --vod-offset-s 7860.0 --workdir work/

Exit code 0 when G0 passes, 1 otherwise. Prints a JSON result to stdout.
"""
import argparse
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import gateio  # noqa: E402

CLEAN_SOURCE_TYPES = ("twitch_vod", "youtube_full_vod")
MIN_HEIGHT = 1080


def _parse_fps(value):
    """Parse an ffprobe frame-rate string like '60/1' or '29.97' to float."""
    if value is None:
        return None
    try:
        if "/" in str(value):
            num, den = str(value).split("/", 1)
            den = float(den)
            if den == 0:
                return None
            return float(num) / den
        return float(value)
    except (ValueError, TypeError):
        return None


def probe(local_file):
    """Return ffprobe-derived facts for local_file, or raise RuntimeError."""
    cmd = [
        "ffprobe", "-v", "quiet", "-print_format", "json",
        "-show_streams", "-show_format", local_file,
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except FileNotFoundError as exc:
        raise RuntimeError("ffprobe not found on PATH: %s" % exc)
    except subprocess.TimeoutExpired:
        raise RuntimeError("ffprobe timed out after 120s on %s" % local_file)
    if proc.returncode != 0:
        raise RuntimeError(
            "ffprobe failed (exit %d): %s" % (proc.returncode, proc.stderr.strip()[:500]))
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("ffprobe output was not valid JSON: %s" % exc)

    width = None
    height = None
    fps = None
    has_audio = False
    for stream in data.get("streams", []):
        codec_type = stream.get("codec_type")
        if codec_type == "video" and width is None:
            width = stream.get("width")
            height = stream.get("height")
            fps = _parse_fps(stream.get("r_frame_rate")) or _parse_fps(
                stream.get("avg_frame_rate"))
        elif codec_type == "audio":
            has_audio = True
    duration = None
    fmt = data.get("format", {})
    if fmt.get("duration") is not None:
        try:
            duration = float(fmt["duration"])
        except (TypeError, ValueError):
            duration = None
    return {
        "width": width,
        "height": height,
        "fps": fps,
        "duration_s": duration,
        "has_audio": has_audio,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Probe a Stage 1 source file (ffprobe + sha256) and stamp gate G0.",
        epilog="Writes source_manifest.json and gate_report.json into --workdir. "
               "G0 passes only for a clean VOD (twitch_vod / youtube_full_vod) "
               "with height >= 1080 and an audio stream; a compilation fails G0.",
    )
    parser.add_argument("--local-file", required=True,
                        help="Path to the downloaded local source file (e.g. work/source/vod_window.mp4).")
    parser.add_argument("--vod-url", required=True,
                        help="URL of the source VOD.")
    parser.add_argument("--source-type", required=True,
                        choices=["twitch_vod", "youtube_full_vod", "compilation"],
                        help="Source type. 'compilation' always fails G0 (SRC-1).")
    parser.add_argument("--vod-offset-s", type=float, default=0.0,
                        help="VOD time at local t=0 (VOD time = local time + offset). Default 0.0.")
    parser.add_argument("--workdir", required=True,
                        help="Build output dir; source_manifest.json and gate_report.json go here.")
    args = parser.parse_args(argv)

    result = {"ok": False}
    try:
        if not os.path.isfile(args.local_file):
            raise RuntimeError("local file not found: %s" % args.local_file)
        facts = probe(args.local_file)
        sha = gateio.sha256_file(args.local_file)
    except RuntimeError as exc:
        result["error"] = str(exc)
        print(json.dumps(result))
        return 1

    manifest = {
        "source_type": args.source_type,
        "vod_url": args.vod_url,
        "local_file": args.local_file,
        "vod_offset_s": args.vod_offset_s,
        "width": facts["width"],
        "height": facts["height"],
        "fps": facts["fps"],
        "duration_s": facts["duration_s"],
        "has_audio": facts["has_audio"],
        "sha256": sha,
    }
    os.makedirs(args.workdir, exist_ok=True)
    manifest_path = os.path.join(args.workdir, "source_manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, sort_keys=True)
        f.write("\n")

    problems = []
    if args.source_type not in CLEAN_SOURCE_TYPES:
        problems.append("source_type %r is not a clean VOD (SRC-1); compilations are never used"
                        % args.source_type)
    if facts["height"] is None:
        problems.append("ffprobe found no video stream, so height is unknown")
    elif facts["height"] < MIN_HEIGHT:
        problems.append("height %s < %d" % (facts["height"], MIN_HEIGHT))
    if not facts["has_audio"]:
        problems.append("no audio stream found in the source file")
    if facts["fps"] is None:
        problems.append("fps could not be read from ffprobe (recorded as a warning, not a G0 failure)")

    # The fps note is evidence only; G0 pass is decided by the three hard conditions.
    hard_problems = [p for p in problems if "fps could not be read" not in p]
    ok = len(hard_problems) == 0
    if ok:
        fix_hint = ""
    elif args.source_type not in CLEAN_SOURCE_TYPES:
        fix_hint = "Re-fetch a clean VOD (twitch_vod or youtube_full_vod). Never fall back to a compilation."
    elif facts["height"] is None or (facts["height"] is not None and facts["height"] < MIN_HEIGHT):
        fix_hint = "Re-fetch the VOD at source quality (height >= 1080)."
    else:
        fix_hint = "Re-fetch the VOD; the file has no audio stream."

    evidence = dict(manifest)
    evidence["problems"] = problems
    # G0's input is the source file. Its sha256 is recorded in evidence and in
    # source_manifest.json; inputs_sha256 is left empty on purpose so that
    # render scripts checking G0-G4 against the EDL hash (see gateio.stamps_ok
    # semantics: any stored key must also appear in the checked dict) are not
    # tripped by a source-file key they do not pass.
    gateio.stamp_gate(args.workdir, "G0", "pass" if ok else "fail",
                      evidence, {}, fix_hint)

    result = {"ok": ok, "gate": "G0", "manifest": manifest_path,
              "manifest_data": manifest, "problems": problems}
    print(json.dumps(result))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

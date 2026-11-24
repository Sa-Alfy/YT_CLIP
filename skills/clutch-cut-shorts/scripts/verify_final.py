#!/usr/bin/env python3
"""verify_final.py — verify short_final.mp4 after Render 2 (G8).

Checks OUT-1, CUT-5, FACE-2, audio-md5 equality with the picture lock,
PROV-1, and duration equality with the picture lock, from
claude-skill-design-2026-11-20.md sections 3-4. Stamps G8 via gateio.

  * OUT-1: 1080x1920, fps 30, audio present (ffprobe).
  * CUT-5: duration 16-38s and == EDL duration_s +-0.1s.
  * FACE-2: no boxblur/gblur in ffmpeg_commands.log filtergraph.
  * Audio md5: extract the audio stream of the final file and of
    picture_lock.mp4 to wav with ffmpeg and compare md5 — equal md5 proves
    captions cannot have drifted from the audio (audio was stream-copied).
  * PROV-1: mp4 metadata comment contains edl_sha256 and captions_sha256
    matching the live hashes of edl.json / captions.ass (and the manifest,
    when build_manifest.json already exists in workdir).
  * Duration == picture-lock duration +-0.05s.

Exit 0 when every check passes, 1 otherwise. Prints JSON to stdout:
  {"ok": bool, "checks": [{rule, pass, evidence}]}
"""

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import constants  # noqa: E402
import gateio  # noqa: E402


def load_json(path, label):
    if not os.path.exists(path):
        raise FileNotFoundError("%s not found: %s" % (label, path))
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def ffprobe(path):
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-show_format",
         "-of", "json", path],
        capture_output=True, text=True, timeout=120)
    if proc.returncode != 0:
        raise RuntimeError("ffprobe failed for %s: %s" % (path, proc.stderr.strip()))
    return json.loads(proc.stdout)


def parse_rate(rate):
    """'30/1' or '30' -> float, or None."""
    try:
        if "/" in str(rate):
            num, den = str(rate).split("/", 1)
            return float(num) / float(den) if float(den) else None
        return float(rate)
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def video_stream(info):
    for s in info.get("streams", []):
        if s.get("codec_type") == "video":
            return s
    return None


def audio_present(info):
    return any(s.get("codec_type") == "audio" for s in info.get("streams", []))


def duration_of(info):
    try:
        return float(info["format"]["duration"])
    except (KeyError, TypeError, ValueError):
        return None


def audio_md5(path):
    """md5 of the decoded audio samples (raw PCM s16le), or None.

    Hashes the raw sample bytes only, never the container. A whole-wav
    md5 is a false positive here: ffmpeg's wav muxer copies the input's
    metadata into the wav LIST chunk, and short_final.mp4 always carries
    the PROV-1 comment tag (edl_sha256/captions_sha256) that picture_lock
    lacks, so the two wav files differ by ~166 bytes even when the audio
    samples are byte-identical (CC-S04, 2026-11-20: bitstream md5 and
    decoded PCM md5 both identical, whole-wav md5 differed).
    """
    with tempfile.NamedTemporaryFile(suffix=".raw", delete=False) as tmp:
        raw_path = tmp.name
    try:
        proc = subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-i", path,
             "-map", "0:a:0", "-c:a", "pcm_s16le", "-f", "s16le", raw_path],
            capture_output=True, text=True, timeout=300)
        if proc.returncode != 0:
            return None, proc.stderr.strip()[-500:]
        h = hashlib.md5()
        with open(raw_path, "rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest(), ""
    finally:
        try:
            os.unlink(raw_path)
        except OSError:
            pass


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Verify short_final.mp4 after Render 2 "
                    "(Stage 1, gate G8: OUT-1, CUT-5, FACE-2, PROV-1, audio md5)."
    )
    ap.add_argument("--final", required=True, help="short_final.mp4")
    ap.add_argument("--picture-lock", required=True, help="picture_lock.mp4")
    ap.add_argument("--edl", required=True, help="edl.json")
    ap.add_argument("--captions", required=True, help="captions.ass")
    ap.add_argument("--workdir", required=True, help="work dir (gate stamps, log)")
    args = ap.parse_args(argv)

    checks = []

    def record(rule, passed, evidence):
        checks.append({"rule": rule, "pass": bool(passed), "evidence": evidence})

    try:
        edl = load_json(args.edl, "EDL")
    except (FileNotFoundError, json.JSONDecodeError, OSError) as e:
        print(json.dumps({"ok": False, "error": str(e), "checks": checks}))
        return 1
    for path, label in ((args.final, "final"), (args.picture_lock, "picture lock"),
                        (args.captions, "captions")):
        if not os.path.exists(path):
            print(json.dumps({"ok": False,
                              "error": "%s not found: %s" % (label, path),
                              "checks": checks}))
            return 1

    try:
        final_info = ffprobe(args.final)
        lock_info = ffprobe(args.picture_lock)
    except (RuntimeError, json.JSONDecodeError, OSError) as e:
        print(json.dumps({"ok": False, "error": str(e), "checks": checks}))
        return 1

    final_dur = duration_of(final_info)
    lock_dur = duration_of(lock_info)

    # --- OUT-1 -----------------------------------------------------------------
    v = video_stream(final_info)
    out_problems = []
    if v is None:
        out_problems.append("no video stream")
        width = height = fps = None
    else:
        width = v.get("width")
        height = v.get("height")
        fps = parse_rate(v.get("avg_frame_rate") or v.get("r_frame_rate"))
        if (width, height) != (constants.W, constants.H):
            out_problems.append("resolution %sx%s, expected %dx%d"
                                % (width, height, constants.W, constants.H))
        if fps is None or abs(fps - constants.FPS) > 0.01:
            out_problems.append("fps %s, expected %d" % (fps, constants.FPS))
    if not audio_present(final_info):
        out_problems.append("no audio stream")
    record("OUT-1", not out_problems,
           "1080x1920, fps 30, audio present (video %sx%s @ %s fps)"
           % (width, height, fps) if not out_problems else "; ".join(out_problems))

    # --- CUT-5 -----------------------------------------------------------------
    try:
        edl_dur = float(edl["target"]["duration_s"])
    except (KeyError, TypeError, ValueError):
        edl_dur = None
    cut_problems = []
    if final_dur is None:
        cut_problems.append("final duration unreadable via ffprobe")
    else:
        if not (constants.MIN_S <= final_dur <= constants.MAX_S):
            cut_problems.append("duration %.3fs outside %.1f-%.1fs"
                                % (final_dur, constants.MIN_S, constants.MAX_S))
        if edl_dur is not None and abs(final_dur - edl_dur) > 0.1:
            cut_problems.append("duration %.3fs != EDL duration_s %.3fs (+-0.1s)"
                                % (final_dur, edl_dur))
    if edl_dur is None:
        cut_problems.append("EDL target.duration_s missing")
    record("CUT-5", not cut_problems,
           "duration %.3fs, EDL duration_s %.3fs, within %.1f-%.1fs and +-0.1s"
           % (final_dur, edl_dur, constants.MIN_S, constants.MAX_S)
           if not cut_problems else "; ".join(cut_problems))

    # --- FACE-2 ----------------------------------------------------------------
    log_path = os.path.join(args.workdir, "ffmpeg_commands.log")
    if not os.path.exists(log_path):
        record("FACE-2", False,
               "ffmpeg_commands.log missing in workdir; cannot check filtergraph")
    else:
        with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
            log_text = f.read()
        lowered = log_text.lower()
        found = [t for t in ("boxblur", "gblur") if t in lowered]
        record("FACE-2", not found,
               "no boxblur/gblur in ffmpeg_commands.log filtergraph"
               if not found else "blur filter(s) in command log: %s" % found)

    # --- Audio md5: final vs picture lock --------------------------------------
    final_md5, final_err = audio_md5(args.final)
    lock_md5, lock_err = audio_md5(args.picture_lock)
    if final_md5 is None:
        record("AUDIO-MD5", False,
               "could not extract audio from final file: %s" % final_err)
    elif lock_md5 is None:
        record("AUDIO-MD5", False,
               "could not extract audio from picture lock: %s" % lock_err)
    else:
        record("AUDIO-MD5", final_md5 == lock_md5,
               "audio md5 final %s == picture-lock %s"
               % (final_md5, lock_md5) if final_md5 == lock_md5 else
               "audio md5 differs: final %s vs picture-lock %s "
               "(audio was changed after Whisper — find out why before retrying)"
               % (final_md5, lock_md5))

    # --- PROV-1 ------------------------------------------------------------------
    edl_sha = gateio.sha256_file(args.edl)
    captions_sha = gateio.sha256_file(args.captions)
    comment = ""
    try:
        comment = (final_info.get("format", {}).get("tags", {}) or {}).get("comment", "") or ""
    except AttributeError:
        comment = ""
    prov_problems = []
    if "edl_sha256=%s" % edl_sha not in comment:
        prov_problems.append("metadata comment lacks edl_sha256=%s" % edl_sha)
    if "captions_sha256=%s" % captions_sha not in comment:
        prov_problems.append("metadata comment lacks captions_sha256=%s" % captions_sha)
    manifest_path = os.path.join(args.workdir, "build_manifest.json")
    if os.path.exists(manifest_path):
        try:
            manifest = load_json(manifest_path, "manifest")
            mfiles = {f.get("path"): f.get("sha256")
                      for f in manifest.get("files", [])}
            for name, sha in (("edl.json", edl_sha), ("captions.ass", captions_sha)):
                if name in mfiles and mfiles[name] != sha:
                    prov_problems.append(
                        "manifest %s sha %s != live %s" % (name, mfiles[name], sha))
        except (json.JSONDecodeError, OSError):
            prov_problems.append("build_manifest.json unreadable")
    record("PROV-1", not prov_problems,
           "metadata comment contains edl_sha256 and captions_sha256 matching "
           "live hashes (comment: %r)" % comment if not prov_problems else
           "; ".join(prov_problems) + " (comment: %r)" % comment)

    # --- Duration == picture-lock duration +-0.05s ------------------------------
    if final_dur is None or lock_dur is None:
        record("DURATION-LOCK", False,
               "final duration %s, picture-lock duration %s (one unreadable)"
               % (final_dur, lock_dur))
    else:
        record("DURATION-LOCK", abs(final_dur - lock_dur) <= 0.05,
               "final %.3fs vs picture-lock %.3fs (delta %.3fs, tol 0.05s)"
               % (final_dur, lock_dur, abs(final_dur - lock_dur)))

    ok = all(c["pass"] for c in checks)
    inputs = {}
    for label, path in (("short_final.mp4", args.final),
                        ("picture_lock.mp4", args.picture_lock),
                        ("edl.json", args.edl),
                        ("captions.ass", args.captions)):
        try:
            inputs[label] = gateio.sha256_file(path)
        except OSError:
            pass
    failing = [c["rule"] for c in checks if not c["pass"]]
    fix_hint = ("Re-run render 2; failing rule(s): %s" % ", ".join(failing)) \
        if failing else ""
    try:
        gateio.stamp_gate(args.workdir, "G8", "pass" if ok else "fail",
                          {"checks": checks}, inputs, fix_hint)
    except (OSError, ValueError) as e:
        print(json.dumps({"ok": False, "error": "could not stamp G8: %s" % e,
                          "checks": checks}))
        return 1

    print(json.dumps({"ok": ok, "checks": checks}))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

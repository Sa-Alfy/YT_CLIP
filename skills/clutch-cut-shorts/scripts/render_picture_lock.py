#!/usr/bin/env python3
"""Render 1: picture-lock (design section 4, S4).

Refuses to run unless gates G0-G4 are stamped "pass" in the workdir's
gate_report.json for the current edl.json (gateio.stamps_ok), and refuses
while any other ffmpeg process is running. Builds a frame-accurate
trim/atrim filtergraph (never -ss with -c copy), composites the EDL layout
(facecam panel + gameplay) onto a 1080x1920 canvas at 30 fps, applies
single-pass loudnorm I=-14, and writes picture_lock.mp4 with no captions and
no title. The exact ffmpeg command is appended (one line, timestamp-prefixed)
to ffmpeg_commands.log in the workdir BEFORE it runs. Uses -threads 1.

Usage:
    python3 render_picture_lock.py --edl edl.json --source work/source/vod_window.mp4 \\
        --out picture_lock.mp4 --workdir work/ [--log work/ffmpeg_commands.log]

Exit code 0 on success, 1 on refusal/failure. Prints a JSON result to stdout.
"""
import argparse
import json
import os
import shlex
import subprocess
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import gateio  # noqa: E402

REQUIRED_GATES = ["G0", "G1", "G2", "G3", "G4"]
OUT_W = 1080
OUT_H = 1920
OUT_FPS = 30
LOUDNORM_I = -14


def _ffmpeg_running():
    """True when any ffmpeg process is currently running (excluding ourselves)."""
    try:
        proc = subprocess.run(["pgrep", "-x", "ffmpeg"],
                              capture_output=True, text=True, timeout=10)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0 and bool(proc.stdout.strip())


def _ffprobe_duration(path):
    """Probed duration in seconds, or None when ffprobe fails."""
    try:
        proc = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json",
             "-show_format", path],
            capture_output=True, text=True, timeout=60)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    try:
        return float(json.loads(proc.stdout).get("format", {}).get("duration"))
    except (ValueError, TypeError, json.JSONDecodeError):
        return None


def _fmt_num(value):
    """Format a crop/scale number without a trailing .0 when integral."""
    if float(value) == int(value):
        return str(int(value))
    return str(value)


def build_filtergraph(edl, source_duration):
    """Build the composite + trim/atrim + concat + loudnorm filtergraph.

    Per segment, in final order: the full source is cropped/scaled into the
    facecam and gameplay panels, overlaid onto a 1080x1920 canvas, then cut
    to the segment's local-file range with trim/atrim + setpts (frame-accurate).
    Segments are concatenated, audio is loudnormed, video is 30 fps CFR.
    """
    layout = edl["layout"]
    face = layout["facecam"]
    game = layout["gameplay"]
    fc, fd = face["src_crop"], face["dst"]
    gc, gd = game["src_crop"], game["dst"]

    parts = []
    # Panels from the source.
    parts.append(
        "[0:v]crop={w}:{h}:{x}:{y},scale={dw}:{dh}:flags=lanczos[game]".format(
            w=_fmt_num(gc["w"]), h=_fmt_num(gc["h"]), x=_fmt_num(gc["x"]), y=_fmt_num(gc["y"]),
            dw=_fmt_num(gd["w"]), dh=_fmt_num(gd["h"])))
    parts.append(
        "[0:v]crop={w}:{h}:{x}:{y},scale={dw}:{dh}:flags=lanczos[face]".format(
            w=_fmt_num(fc["w"]), h=_fmt_num(fc["h"]), x=_fmt_num(fc["x"]), y=_fmt_num(fc["y"]),
            dw=_fmt_num(fd["w"]), dh=_fmt_num(fd["h"])))
    # Black canvas for the length of the source, then overlay both panels.
    canvas_dur = source_duration if source_duration else 60.0
    parts.append(
        "color=c=black:s={w}x{h}:r={fps}:d={d}[base]".format(
            w=OUT_W, h=OUT_H, fps=OUT_FPS, d=canvas_dur))
    parts.append("[base][game]overlay={x}:{y}[bg]".format(
        x=_fmt_num(gd["x"]), y=_fmt_num(gd["y"])))
    parts.append("[bg][face]overlay={x}:{y}[comp]".format(
        x=_fmt_num(fd["x"]), y=_fmt_num(fd["y"])))

    segments = sorted(edl["segments"], key=lambda s: s["final_in"])
    # Explicit split of [comp] into one branch per segment. Relying on
    # ffmpeg's auto-inserted split when [comp] is consumed by several trims
    # is broken in this ffmpeg build: the second and later consumers receive
    # the raw 1920x1080 source instead of the 1080x1920 composite, and the
    # concat filter then fails with a size mismatch. An explicit split works.
    n_seg = len(segments)
    split_labels = "".join("[c%d]" % i for i in range(n_seg))
    parts.append("[comp]split={n}{labels}".format(n=n_seg, labels=split_labels))
    v_labels, a_labels = [], []
    for i, seg in enumerate(segments):
        src_dur = seg["src_out"] - seg["src_in"]
        parts.append(
            "[c{i}]trim=start={s}:end={e},setpts=PTS-STARTPTS,fps={fps}[v{i}]".format(
                i=i, s=seg["src_in"], e=seg["src_out"], fps=OUT_FPS))
        audio_chain = "[0:a]atrim=start={s}:end={e},asetpts=PTS-STARTPTS".format(
            s=seg["src_in"], e=seg["src_out"])
        fade_ms = seg.get("audio_fade_ms") or 0
        if fade_ms > 0:
            fade_s = min(fade_ms / 1000.0, src_dur / 2.0)
            audio_chain += (",afade=t=in:st=0:d={f}"
                            ",afade=t=out:st={st}:d={f}".format(
                                f=fade_s, st=max(0.0, src_dur - fade_s)))
        audio_chain += "[a{i}]".format(i=i)
        parts.append(audio_chain)
        v_labels.append("[v%d]" % i)
        a_labels.append("[a%d]" % i)

    # concat n=v+a inputs must be interleaved per segment ([v0][a0][v1][a1]...):
    # for n segments with v=1,a=1, input 2i is segment i's video and input
    # 2i+1 its audio. Passing all videos then all audios makes ffmpeg fail
    # with "Media type mismatch ... input pad 2i (video)" for any n > 1.
    interleaved = []
    for i in range(len(segments)):
        interleaved.append(v_labels[i])
        interleaved.append(a_labels[i])
    parts.append("".join(interleaved) +
                 "concat=n={n}:v=1:a=1[vcat][acat]".format(n=len(segments)))
    parts.append("[acat]loudnorm=I={i}:TP=-1.5:LRA=11[aout]".format(i=LOUDNORM_I))
    parts.append("[vcat]format=yuv420p[vout]")
    return ";".join(parts), segments


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Render 1: picture-lock (trim/atrim filtergraph, layout composite, "
                    "loudnorm I=-14, no captions, no title).",
        epilog="Refuses unless gates G0-G4 are stamped pass for the current edl.json "
               "and no other ffmpeg process is running. Appends the exact ffmpeg "
               "command (one line, timestamp-prefixed) to ffmpeg_commands.log in "
               "--workdir BEFORE running it. Uses -threads 1.",
    )
    parser.add_argument("--edl", required=True, help="Path to edl.json.")
    parser.add_argument("--source", required=True, help="Path to the local source file.")
    parser.add_argument("--out", required=True, help="Output path for picture_lock.mp4.")
    parser.add_argument("--workdir", required=True,
                        help="Build output dir holding gate_report.json and ffmpeg_commands.log.")
    parser.add_argument("--log", default=None,
                        help="ffmpeg command log path (default: <workdir>/ffmpeg_commands.log).")
    args = parser.parse_args(argv)

    def fail(message, **extra):
        result = {"ok": False, "error": message}
        result.update(extra)
        print(json.dumps(result))
        return 1

    if not os.path.isfile(args.edl):
        return fail("EDL file not found: %s" % args.edl)
    if not os.path.isfile(args.source):
        return fail("Source file not found: %s" % args.source)

    # Gate check: G0-G4 must be stamped pass for the CURRENT edl.json.
    edl_sha = gateio.sha256_file(args.edl)
    ok, problems = gateio.stamps_ok(args.workdir, REQUIRED_GATES,
                                    {os.path.basename(args.edl): edl_sha})
    if not ok:
        return fail("Refusing to render: gate stamps not satisfied.",
                    gates=REQUIRED_GATES, problems=problems,
                    fix_hint="Run probe_source.py (G0) and validate_edl.py (G1/G2/G3) and the "
                             "layout check (G4), then re-run this script without editing edl.json.")

    if _ffmpeg_running():
        return fail("Refusing to render: another ffmpeg process is already running.",
                    fix_hint="Wait for the other encode to finish, then re-run this script.")

    try:
        with open(args.edl, "r", encoding="utf-8") as f:
            edl = json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        return fail("EDL is not valid JSON: %s" % exc)

    source_duration = _ffprobe_duration(args.source)
    filtergraph, segments = build_filtergraph(edl, source_duration)

    cmd = ["ffmpeg", "-y", "-threads", "1", "-i", args.source,
           "-filter_complex", filtergraph,
           "-map", "[vout]", "-map", "[aout]",
           "-r", str(OUT_FPS),
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
           "-c:a", "aac", "-b:a", "192k",
           args.out]
    log_path = args.log or os.path.join(args.workdir, "ffmpeg_commands.log")
    os.makedirs(os.path.dirname(os.path.abspath(log_path)) or ".", exist_ok=True)
    timestamp = datetime.now(timezone.utc).isoformat()
    with open(log_path, "a", encoding="utf-8") as f:
        f.write("%s %s\n" % (timestamp, shlex.join(cmd)))

    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
    if proc.returncode != 0:
        return fail("ffmpeg failed (exit %d)." % proc.returncode,
                    ffmpeg_stderr=proc.stderr.strip()[-2000:],
                    log=log_path)

    if not os.path.isfile(args.out) or os.path.getsize(args.out) == 0:
        return fail("ffmpeg exited 0 but no output file was produced at %s" % args.out,
                    log=log_path)

    probed = _ffprobe_duration(args.out)
    expected = edl.get("target", {}).get("duration_s")
    duration_ok = (probed is not None and expected is not None
                   and abs(probed - expected) <= 0.15)
    result = {"ok": bool(duration_ok), "out": args.out,
              "duration_s": probed, "expected_duration_s": expected,
              "segments": len(segments), "log": log_path}
    if not duration_ok:
        result["error"] = ("picture-lock duration %.3fs != EDL duration_s %s "
                           "(tolerance 0.15s)" % (probed, expected))
        print(json.dumps(result))
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""render_final.py — Render 2: burn captions + persistent hook title (G8 input).

Refuses to run unless gates G0-G7 are all stamped pass (via gateio) and no
other ffmpeg process is running. Burns captions.ass and the persistent hook
title (drawtext, visible from t=0 to the last frame), copies the audio stream
(-c:a copy — audio is never re-encoded after Whisper), writes edl_sha256 and
captions_sha256 into the mp4 metadata comment, outputs 1080x1920 30fps,
-threads 1. The exact ffmpeg command (one line, timestamp-prefixed) is
appended to ffmpeg_commands.log BEFORE it runs.

Exit 0 on success, 1 on refusal/failure. Prints a JSON result to stdout.
"""

import argparse
import json
import os
import shlex
import subprocess
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import constants  # noqa: E402
import gateio  # noqa: E402

REQUIRED_GATES = ["G0", "G1", "G2", "G3", "G4", "G5", "G6", "G7"]

# Hook-title fit (CC-S04 fix, 2026-11-20): a single centred drawtext line at
# fontsize 64 clips on a 1080 px frame once the title is longer than ~27
# chars ("HE DELETED GTA 6. IN FRONT OF 500,000 PEOPLE." measured ~1634 px
# wide, ~277 px clipped off EACH side, audit FAIL on HOOK-1). Titles are
# therefore split into at most two centred lines that fit TITLE_MAX_W.
TITLE_MAX_W = 1000
TITLE_CHAR_W = 0.57  # px width per char per fontsize px (calibrated from the
                     # CC-S04 measurement: 45 chars at 64 px = ~1634 px)


def _estimate_w(text, fontsize):
    """Rough rendered width of drawtext text at a fontsize (px)."""
    return len(text) * TITLE_CHAR_W * fontsize


def _split_hook_lines(text):
    """Split a hook title into 1-2 (text, fontsize, y) lines that fit on
    screen. Short titles stay one line at fontsize 64, y=120 (unchanged
    behaviour). Long titles split into two lines at fontsize 56, preferring
    a sentence-boundary break ('.'/'!'/'?') when the longer line still fits,
    otherwise the word boundary nearest the middle; fontsize drops only if
    a split line still would not fit.
    2026-11-24: a 2-4 word short hook (HOOK-2) renders BIG — fontsize 96,
    one line — because competitor hook titles are huge in frame 1."""
    if len(text) <= 20 and len(text.split()) <= 4:
        return [(text, 96, 110)]
    if _estimate_w(text, 64) <= TITLE_MAX_W:
        return [(text, 64, 120)]
    words = text.split()
    best = None
    for i, w in enumerate(words[:-1]):
        if w.endswith((".", "!", "?")):
            l1 = " ".join(words[:i + 1])
            l2 = " ".join(words[i + 1:])
            m = max(len(l1), len(l2))
            if best is None or m < best[0]:
                best = (m, l1, l2)
    if best is not None and _estimate_w("x" * best[0], 56) <= TITLE_MAX_W:
        return [(best[1], 56, 110), (best[2], 56, 180)]
    total = len(text)
    cum, best_i, best_d = 0, 1, None
    for i in range(len(words) - 1):
        cum += len(words[i]) + 1
        d = abs(cum - total / 2)
        if best_d is None or d < best_d:
            best_d, best_i = d, i + 1
    l1 = " ".join(words[:best_i])
    l2 = " ".join(words[best_i:])
    fs = 56
    while (_estimate_w("x" * max(len(l1), len(l2)), fs) > TITLE_MAX_W
           and fs > 28):
        fs -= 1
    return [(l1, fs, 110), (l2, fs, 110 + fs + 14)]


def load_json(path, label):
    if not os.path.exists(path):
        raise FileNotFoundError("%s not found: %s" % (label, path))
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def ffmpeg_running():
    """True if any ffmpeg process is running (excluding this process tree).

    Checked before spawning ffmpeg, so any ffmpeg pid found here belongs to
    another job. pgrep first, /proc scan as fallback.
    """
    try:
        out = subprocess.run(["pgrep", "-x", "ffmpeg"],
                             capture_output=True, text=True, timeout=10)
        pids = [p.strip() for p in out.stdout.split() if p.strip()]
        return len(pids) > 0
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        for pid in os.listdir("/proc"):
            if not pid.isdigit():
                continue
            try:
                with open(os.path.join("/proc", pid, "comm"),
                          "r", encoding="utf-8") as f:
                    if f.read().strip() == "ffmpeg":
                        return True
            except OSError:
                continue
    except OSError:
        pass
    return False


def find_named_file(workdir, name):
    """Resolve a gate-stamped input name to a file on disk, or None.

    Tries workdir/name, then a recursive basename search under workdir.
    """
    direct = os.path.join(workdir, name)
    if os.path.isfile(direct):
        return direct
    base = os.path.basename(name)
    for root, _dirs, files in os.walk(workdir):
        if base in files:
            return os.path.join(root, base)
    return None


def collect_input_hashes(workdir, edl_path, captions_path):
    """Hash map covering the explicit inputs plus every file the required
    gates were stamped with (so stamps_ok can compare stored hashes)."""
    inputs = {}
    for path in (edl_path, captions_path):
        try:
            inputs[os.path.basename(path)] = gateio.sha256_file(path)
        except OSError:
            pass
    report = gateio.load_gate_report(workdir)
    for gate in REQUIRED_GATES:
        entry = report.get("gates", {}).get(gate) or {}
        for name in (entry.get("inputs_sha256") or {}):
            if name in inputs:
                continue
            found = find_named_file(workdir, name)
            if found is not None:
                try:
                    inputs[name] = gateio.sha256_file(found)
                except OSError:
                    pass
    return inputs


def escape_filter_text(text):
    """Escape drawtext/ass argument text for the ffmpeg filter parser.

    Backslash, single-quote, colon and percent are escaped; the value is
    then wrapped in single quotes, inside which commas are protected.
    """
    text = str(text).replace("\\", "\\\\").replace("'", "\\'")
    text = text.replace(":", "\\:").replace("%", "\\%")
    text = text.replace("\n", " ").replace("\r", " ")
    return "'%s'" % text


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Render 2: burn captions.ass + hook title onto the "
                    "picture lock, audio stream-copied (Stage 1)."
    )
    ap.add_argument("--picture-lock", required=True, help="picture_lock.mp4")
    ap.add_argument("--captions", required=True, help="captions.ass")
    ap.add_argument("--edl", required=True, help="edl.json")
    ap.add_argument("--out", required=True, help="output short_final.mp4")
    ap.add_argument("--workdir", required=True, help="work dir (gate stamps)")
    ap.add_argument("--log", default=None,
                    help="ffmpeg_commands.log path (default: workdir/ffmpeg_commands.log)")
    args = ap.parse_args(argv)

    def fail(msg, **extra):
        result = {"ok": False, "error": msg}
        result.update(extra)
        print(json.dumps(result))
        return 1

    for path, label in ((args.picture_lock, "picture lock"),
                        (args.captions, "captions"), (args.edl, "EDL")):
        if not os.path.exists(path):
            return fail("%s not found: %s" % (label, path))

    try:
        edl = load_json(args.edl, "EDL")
    except (json.JSONDecodeError, OSError) as e:
        return fail("could not read EDL: %s" % e)

    hook = edl.get("hook") or {}
    hook_text = hook.get("text") if isinstance(hook, dict) else None
    if not (isinstance(hook_text, str) and hook_text.strip()):
        return fail("EDL hook.text is missing or empty (HOOK-1); refusing to render")
    try:
        duration_s = float(edl["target"]["duration_s"])
    except (KeyError, TypeError, ValueError):
        return fail("EDL target.duration_s missing or not a number")

    # --- Gate stamps G0-G7 ----------------------------------------------------
    # A corrupt gate_report.json is treated as "no valid stamps" (fail closed
    # with a clean JSON error) rather than a traceback: gateio's contract
    # only promises {"gates": {}} for a *missing* file.
    try:
        inputs = collect_input_hashes(args.workdir, args.edl, args.captions)
        ok, problems = gateio.stamps_ok(args.workdir, REQUIRED_GATES, inputs)
    except (ValueError, OSError) as e:
        return fail("refusing to render: gate_report.json unreadable: %s" % e)
    if not ok:
        return fail("refusing to render: required gate stamps G0-G7 not all pass",
                    gate_problems=problems)

    # --- No other ffmpeg running ----------------------------------------------
    if ffmpeg_running():
        return fail("refusing to render: another ffmpeg process is running")

    edl_sha = gateio.sha256_file(args.edl)
    captions_sha = gateio.sha256_file(args.captions)
    comment = "edl_sha256=%s captions_sha256=%s" % (edl_sha, captions_sha)

    # --- Build the ffmpeg command ---------------------------------------------
    ass_path = os.path.abspath(args.captions)
    # drawtext: persistent title, centred near the top of the facecam panel,
    # visible from t=0 to the last frame. _split_hook_lines keeps the title
    # on screen: one line at 64 px when it fits, otherwise two lines at 56 px
    # (CC-S04: a 45-char title at 64 px clipped ~277 px per side).
    hook_lines = _split_hook_lines(hook_text)
    drawtexts = []
    for line_text, fs, y in hook_lines:
        drawtexts.append(
            "drawtext=text=%s:fontsize=%d:fontcolor=white:borderw=2:"
            "bordercolor=black:box=1:boxcolor=black@0.6:"
            "x=(w-text_w)/2:y=%d:enable='between(t,0,%.3f)'"
            % (escape_filter_text(line_text), fs, y, duration_s)
        )
    filt = ("ass=%s,%s,scale=%d:%d,fps=%d"
            % (escape_filter_text(ass_path), ",".join(drawtexts),
               constants.W, constants.H, constants.FPS))
    out_path = os.path.abspath(args.out)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    cmd = ["ffmpeg", "-y",
           "-i", os.path.abspath(args.picture_lock),
           "-vf", filt,
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
           "-pix_fmt", "yuv420p",
           "-r", str(constants.FPS),
           "-c:a", "copy",
           "-metadata", "comment=%s" % comment,
           "-threads", "1",
           out_path]

    log_path = args.log
    if log_path is None:
        log_path = os.path.join(args.workdir, "ffmpeg_commands.log")
    elif not os.path.isabs(log_path):
        log_path = os.path.join(args.workdir, log_path)
    os.makedirs(os.path.dirname(os.path.abspath(log_path)), exist_ok=True)
    stamp = datetime.now(timezone.utc).astimezone().isoformat()
    with open(log_path, "a", encoding="utf-8") as f:
        f.write("[%s] %s\n" % (stamp, shlex.join(cmd)))

    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        return fail("ffmpeg render failed (exit %d)" % proc.returncode,
                    ffmpeg_stderr=proc.stderr[-2000:] if proc.stderr else "",
                    command=shlex.join(cmd))
    if not os.path.exists(out_path) or os.path.getsize(out_path) == 0:
        return fail("ffmpeg exited 0 but output is missing or empty: %s" % out_path,
                    command=shlex.join(cmd))

    print(json.dumps({
        "ok": True,
        "out": args.out,
        "edl_sha256": edl_sha,
        "captions_sha256": captions_sha,
        "duration_s": duration_s,
        "hook_text": hook_text,
        "hook_lines": [{"text": t, "fontsize": fs, "y": y}
                       for t, fs, y in hook_lines],
        "command": shlex.join(cmd),
    }))
    return 0


if __name__ == "__main__":
    sys.exit(main())

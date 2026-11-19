#!/usr/bin/env python3
"""Unit tests for the v1.1 fixes — no network, no downloads.

Run from the pipeline root: ./venv/bin/python tests/test_units.py
Covers: procutil timeouts (incl. whole-group kill), SRT validation,
segment validation, merge_picks / pick_window, commentary resolution,
ffmpeg filter escaping, cleanup isolation.
"""
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import clip
import procutil
import run_pipeline

PASS = []
FAIL = []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f" — {detail}" if detail and not cond else ""))


class Args:
    def __init__(self, commentary=None):
        self.commentary = commentary


# --- procutil: timeout kills the whole process group ---
try:
    procutil.run(["sleep", "30"], "sleep test", timeout=2)
    check("procutil timeout raises CommandTimeout", False)
except procutil.CommandTimeout as exc:
    check("procutil timeout raises CommandTimeout", True)
    left = subprocess.run(["pgrep", "-f", "sleep 30"], capture_output=True,
                          text=True).stdout.strip()
    check("procutil timeout leaves no orphan process", left == "",
          f"leftover pids: {left}")

try:
    procutil.run(["false"], "false test")
    check("procutil non-zero raises CommandError", False)
except procutil.CommandError as exc:
    check("procutil non-zero raises CommandError", exc.returncode == 1)

res = procutil.run(["true"], "true test")
check("procutil success returns CompletedProcess", res.returncode == 0)

# --- SRT validation ---
tmp = tempfile.mkdtemp()
good_srt = os.path.join(tmp, "good.srt")
with open(good_srt, "w") as fh:
    fh.write("1\n00:00:01,000 --> 00:00:03,500\nhello world\n\n"
             "2\n00:00:04,000 --> 00:00:06,000\nsecond line\n\n")
check("validate_srt accepts a good SRT",
      clip.validate_srt(good_srt, 12.0) == 2)

empty_srt = os.path.join(tmp, "empty.srt")
open(empty_srt, "w").close()
try:
    clip.validate_srt(empty_srt, 12.0)
    check("validate_srt rejects empty SRT", False)
except ValueError:
    check("validate_srt rejects empty SRT", True)

bad_ts = os.path.join(tmp, "bad_ts.srt")
with open(bad_ts, "w") as fh:
    fh.write("1\nnot a timestamp\nhello\n\n")
try:
    clip.validate_srt(bad_ts, 12.0)
    check("validate_srt rejects bad timestamp", False)
except ValueError:
    check("validate_srt rejects bad timestamp", True)

late_srt = os.path.join(tmp, "late.srt")
with open(late_srt, "w") as fh:
    fh.write("1\n00:00:01,000 --> 00:00:20,000\ntoo late\n\n")
try:
    clip.validate_srt(late_srt, 12.0)
    check("validate_srt rejects cue past segment end", False)
except ValueError:
    check("validate_srt rejects cue past segment end", True)

no_text = os.path.join(tmp, "no_text.srt")
with open(no_text, "w") as fh:
    fh.write("1\n00:00:01,000 --> 00:00:03,000\n\n")
try:
    clip.validate_srt(no_text, 12.0)
    check("validate_srt rejects cue with no text", False)
except ValueError:
    check("validate_srt rejects cue with no text", True)

# --- segment validation (needs the test media; skip if absent) ---
media = os.path.join(os.path.dirname(os.path.abspath(__file__)), "media")
seg = os.path.join(media, "seg-12s.mp4")
if os.path.isfile(seg):
    try:
        probe = clip.validate_segment(seg, 30)
        check("validate_segment accepts a good segment",
              probe["streams"][0]["codec_type"] == "video")
    except Exception as exc:
        check("validate_segment accepts a good segment", False, str(exc))
    zero = os.path.join(tmp, "zero.mp4")
    open(zero, "w").close()
    try:
        clip.validate_segment(zero, 30)
        check("validate_segment rejects zero-byte file", False)
    except ValueError:
        check("validate_segment rejects zero-byte file", True)
    part = os.path.join(tmp, "x.mp4.webm.part")
    with open(part, "wb") as fh:
        fh.write(b"junk")
    try:
        clip.validate_segment(part, 30)
        check("validate_segment rejects .part file", False)
    except ValueError:
        check("validate_segment rejects .part file", True)
else:
    print("SKIP segment validation (run tests/gen_test_media.sh first)")

# --- merge_picks / pick_window ---
yt = [{"platform": "youtube", "views_per_hour": 100, "uploader": "A"},
      {"platform": "youtube", "views_per_hour": 50, "uploader": "B"}]
tw = [{"platform": "twitch", "views_per_hour": 500, "uploader": "C"},
      {"platform": "twitch", "views_per_hour": None, "uploader": "D"}]
merged = run_pipeline.merge_picks(yt, tw, 3)
check("merge_picks interleaves Twitch by views/hour",
      [c["uploader"] for c in merged] == ["C", "A", "B"])
check("merge_picks treats None views/hour as 0",
      merged[-1]["uploader"] == "B")
check("merge_picks respects the limit", len(merged) == 3)

check("pick_window twitch uses whole clip",
      run_pipeline.pick_window(
          {"platform": "twitch", "duration_s": 32}, 45)[:2] == (0.0, 32.0))
check("pick_window twitch caps at clip length",
      run_pipeline.pick_window(
          {"platform": "twitch", "duration_s": 60}, 45)[:2] == (0.0, 45.0))
check("pick_window yt long VOD uses 1800s",
      run_pipeline.pick_window(
          {"platform": "youtube", "duration_s": 40000,
           "heatmap_peak": None}, 45)[:2] == (1800.0, 1845.0))
check("pick_window yt heatmap peak wins",
      run_pipeline.pick_window(
          {"platform": "youtube", "duration_s": 40000,
           "heatmap_peak": {"start_s": 5000, "value": 0.9}}, 45)[:2]
      == (5000.0, 5045.0))

# --- commentary resolution ---
cfg_off = {"commentary": {"enabled": False, "audio": None}}
check("resolve_commentary None when disabled",
      clip.resolve_commentary(Args(), cfg_off) is None)
cfg_on = {"commentary": {"enabled": True, "audio": seg if os.path.isfile(seg)
                         else __file__}}
check("resolve_commentary reads config audio",
      clip.resolve_commentary(Args(), cfg_on) == cfg_on["commentary"]["audio"])
check("resolve_commentary CLI wins over config",
      clip.resolve_commentary(Args(__file__), cfg_on) == __file__)
try:
    clip.resolve_commentary(Args(), {"commentary": {"enabled": True,
                                                    "audio": None}})
    check("resolve_commentary fails when enabled but audio unset", False)
except SystemExit as exc:
    check("resolve_commentary fails when enabled but audio unset",
          exc.code == 2)
try:
    clip.resolve_commentary(Args("/nonexistent/file.wav"), cfg_off)
    check("resolve_commentary fails on missing file", False)
except SystemExit as exc:
    check("resolve_commentary fails on missing file", exc.code == 2)

# --- ffmpeg filter escaping ---
esc = clip.ffmpeg_filter_escape("/tmp/a b/c:d'e\\f.mp4")
check("ffmpeg_filter_escape escapes : ' and backslash",
      esc == "/tmp/a b/c\\:d\\'e\\\\f.mp4", esc)

# --- cleanup isolation ---
base = os.path.join(tmp, "segments")
os.makedirs(os.path.join(base, "runA"), exist_ok=True)
os.makedirs(os.path.join(base, "runB"), exist_ok=True)
open(os.path.join(base, "runA", "x.mp4"), "w").close()
open(os.path.join(base, "runB", "keep.mp4"), "w").close()
run_pipeline.cleanup_run_segments(base, "runA")
check("cleanup removes only the current run dir",
      not os.path.exists(os.path.join(base, "runA"))
      and os.path.isfile(os.path.join(base, "runB", "keep.mp4")))

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("FAILED:", ", ".join(FAIL))
    sys.exit(1)

#!/usr/bin/env python3
"""Test run_pipeline wiring with fabricated discovery (no network).

Run from the pipeline root: ./venv/bin/python tests/test_run_wiring.py

Verifies: --config and --run-id are passed to clip.py subprocesses,
Twitch candidates merge into picks by views/hour, cleanup removes
only the current run's segment dir, the digest lands in the config
dir (NOT the v1 digest in the pipeline root).
"""
import json
import os
import subprocess
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import discover
import procutil
import run_pipeline

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"{'PASS' if cond else 'FAIL'}  {name}"
          + (f" — {detail}" if detail and not cond else ""))


CFG_PATH = os.path.join(HERE, "config.test.yaml")
cfg = run_pipeline.load_config(CFG_PATH)

# run_locked() resolves config paths relative to the process cwd, and
# main() chdirs to the config dir first — do the same here.
os.chdir(HERE)

FAKE_YT = [
    {"platform": "youtube", "video_id": "yt1", "title": "YT One",
     "uploader": "CreatorA", "url": "https://youtube.com/watch?v=yt1",
     "views": 1_000_000, "views_per_hour": 100_000.0, "duration_s": 40000,
     "upload_date": "20261119", "age_hours": 10.0, "heatmap_peak": None,
     "discovered_at": "2026-11-19T00:00:00"},
    {"platform": "youtube", "video_id": "yt2", "title": "YT Two",
     "uploader": "CreatorB", "url": "https://youtube.com/watch?v=yt2",
     "views": 500_000, "views_per_hour": 50_000.0, "duration_s": 3600,
     "upload_date": "20261119", "age_hours": 10.0, "heatmap_peak": None,
     "discovered_at": "2026-11-19T00:00:00"},
]
FAKE_TW = [
    {"platform": "twitch", "clip_id": "tw1", "title": "TW Clip One",
     "uploader": "StreamerC", "url": "https://twitch.tv/clip/tw1",
     "views": 200_000, "views_per_hour": 200_000.0, "duration_s": 40,
     "age_hours": 1.0},
    {"platform": "twitch", "clip_id": "tw2", "title": "TW Clip Two",
     "uploader": "StreamerD", "url": "https://twitch.tv/clip/tw2",
     "views": 10_000, "views_per_hour": 5_000.0, "duration_s": 25,
     "age_hours": 2.0},
]

discover.youtube_candidates = lambda cfg: list(FAKE_YT)
discover.twitch_candidates = lambda cfg: list(FAKE_TW)

captured = []
orig_run = procutil.run


def fake_run(cmd, what, timeout=None):
    captured.append({"cmd": cmd, "what": what, "timeout": timeout})
    assert cmd[1] == "clip.py", f"unexpected subprocess: {cmd}"
    out = cmd[cmd.index("--out") + 1]
    clip_dir = os.path.join(HERE, "out-clips")
    os.makedirs(clip_dir, exist_ok=True)
    open(os.path.join(clip_dir, f"{out}.mp4"), "wb").close()
    stats = {"duration_s": 12.0, "width": 720, "height": 1280,
             "codec": "h264", "fps": "30/1", "audio_codec": "aac",
             "size_bytes": 12345, "decode_clean": True,
             "decode_errors": "", "subtitles_burned_in": False,
             "url": cmd[cmd.index("--url") + 1],
             "start": 0, "end": 12, "hook": "X", "subtitles": False,
             "srt_cues": 0, "commentary": False, "status": "ok"}
    with open(os.path.join(clip_dir, f"{out}.json"), "w") as fh:
        json.dump(stats, fh)
    return subprocess.CompletedProcess(cmd, 0, "", "")


procutil.run = fake_run

# Seed segment dirs: this run's dir (to be cleaned) + another run's dir.
seg_base = os.path.join(HERE, "out-segments")
os.makedirs(os.path.join(seg_base, "wiring1"), exist_ok=True)
os.makedirs(os.path.join(seg_base, "otherrun"), exist_ok=True)
open(os.path.join(seg_base, "wiring1", "x.mp4"), "w").close()
open(os.path.join(seg_base, "otherrun", "keep.mp4"), "w").close()

digest_before = open(os.path.join(ROOT, "digest-2026-11-19.md"),
                     "rb").read()

args = types.SimpleNamespace(config="config.test.yaml", clips=2,
                             dry_run=False, no_subtitles=True)
rc = run_pipeline.run_locked(args, cfg, HERE, "wiring1")
check("run_locked exits 0", rc == 0, f"rc={rc}")

check("clip.py got exactly 2 calls (one per pick)", len(captured) == 2,
      str(len(captured)))
for c in captured:
    cmd = c["cmd"]
    check("clip.py call passes --config config.test.yaml",
          "--config" in cmd and cmd[cmd.index("--config") + 1]
          == "config.test.yaml", str(cmd))
    check("clip.py call passes --run-id wiring1",
          "--run-id" in cmd and cmd[cmd.index("--run-id") + 1] == "wiring1",
          str(cmd))
    check("clip.py call has a timeout",
          c["timeout"] is not None and c["timeout"] > 0, str(c["timeout"]))
check("first pick is the Twitch clip (highest views/hour)",
      "twitch.tv" in captured[0]["cmd"][captured[0]["cmd"].index("--url") + 1],
      str(captured[0]["cmd"]))

check("cleanup removed only this run's segment dir",
      not os.path.exists(os.path.join(seg_base, "wiring1"))
      and os.path.isfile(os.path.join(seg_base, "otherrun", "keep.mp4")))

test_digest = os.path.join(HERE, "digest-2026-11-19.md")
check("digest written next to the test config (not the v1 digest)",
      os.path.isfile(test_digest))
check("v1 digest in the pipeline root untouched",
      open(os.path.join(ROOT, "digest-2026-11-19.md"), "rb").read()
      == digest_before)
if os.path.isfile(test_digest):
    text = open(test_digest).read()
    check("digest records the Twitch pick",
          "StreamerC" in text and "twitch" in text.lower())

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("FAILED:", ", ".join(FAIL))
    sys.exit(1)

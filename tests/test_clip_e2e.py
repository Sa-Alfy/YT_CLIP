#!/usr/bin/env python3
"""End-to-end clip.py tests for the v1.1 fixes — no downloads.

Run from the tests/ directory:
    cd tests && ../venv/bin/python test_clip_e2e.py

Uses pre-placed segments (the reuse path) so no yt-dlp download is
needed. Verifies: config propagation, commentary mix via CLI and via
config (with a real spectral check that the game audio is ducked and
the commentary tone is present), commentary misconfig failing fast,
timestamp rounding, hard verification rejecting bad clips, and the
transcription preflight failing loudly when the model is uncached.
"""
import json
import math
import os
import shutil
import struct
import subprocess
import sys
import wave

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PY = os.path.join(ROOT, "venv", "bin", "python")
CLIP = os.path.join(ROOT, "clip.py")
FFMPEG = "ffmpeg"

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"{'PASS' if cond else 'FAIL'}  {name}"
          + (f" — {detail}" if detail and not cond else ""))


def run_clip(args, expect_code=0):
    cmd = [PY, CLIP] + args
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    return r


def place_segment(run_id, out, start, end):
    d = os.path.join(HERE, "out-segments", run_id)
    os.makedirs(d, exist_ok=True)
    dst = os.path.join(d, f"{out}-{start}-{end}.mp4")
    shutil.copyfile(os.path.join(HERE, "media", "seg-12s.mp4"), dst)
    return dst


def goertzel_mag(path, freq, t0=4.0, dur=2.0):
    """Magnitude of `freq` Hz in a slice of an audio file (pure Python).

    Decodes to 8 kHz mono WAV, then runs the Goertzel algorithm.
    """
    wav = os.path.join(HERE, "out-segments", "_goertzel.wav")
    subprocess.run([FFMPEG, "-v", "error", "-y", "-i", path, "-vn",
                    "-ac", "1", "-ar", "8000", wav],
                   check=True, capture_output=True)
    with wave.open(wav, "rb") as fh:
        n = fh.getnframes()
        raw = fh.readframes(n)
    samples = struct.unpack(f"<{n}h", raw)
    sr = 8000
    i0, i1 = int(t0 * sr), int((t0 + dur) * sr)
    seg = samples[i0:i1]
    k = int(round(freq * len(seg) / sr))
    w = 2 * math.pi * k / len(seg)
    cw, coeff = math.cos(w), 2 * math.cos(w)
    s0 = s1 = s2 = 0.0
    for x in seg:
        s0 = x + coeff * s1 - s2
        s2, s1 = s1, s0
    mag = math.sqrt(max(s1 * s1 + s2 * s2 - coeff * s1 * s2, 0.0))
    return mag / len(seg)


# --- Test 1: config propagation (720x1280 proves the test config
# reached clip.py; the default is 1080x1920) ---
place_segment("e2e1", "testclip", 0, 12)
r = run_clip(["--config", "config.test.yaml", "--run-id", "e2e1",
              "--url", "https://example.com/watch?v=dummy",
              "--start", "0", "--end", "12", "--hook", "TEST HOOK",
              "--out", "testclip", "--no-subtitles"])
check("T1 clip.py exits 0", r.returncode == 0, r.stderr[-300:])
out1 = os.path.join(HERE, "out-clips", "testclip.mp4")
stats1 = json.load(open(os.path.join(HERE, "out-clips", "testclip.json")))
check("T1 output is 720x1280 (test config reached clip.py)",
      stats1.get("width") == 720 and stats1.get("height") == 1280,
      str(stats1))
check("T1 stats status ok + actual window recorded",
      stats1.get("status") == "ok" and stats1.get("start") == 0
      and stats1.get("end") == 12 and stats1.get("decode_clean") is True,
      str(stats1))
check("T1 no shared hook.txt left in clips dir",
      not os.path.exists(os.path.join(HERE, "out-clips", "hook.txt")))
check("T1 per-clip hook file lives in the run segment dir",
      os.path.exists(os.path.join(HERE, "out-segments", "e2e1",
                                  "testclip-hook.txt")))
mag200_no_comm = goertzel_mag(out1, 200)
mag1000_no_comm = goertzel_mag(out1, 1000)
check("T1 game tone (200 Hz) present, commentary tone absent",
      mag200_no_comm > 100 and mag1000_no_comm < mag200_no_comm / 10,
      f"200Hz={mag200_no_comm:.1f} 1000Hz={mag1000_no_comm:.1f}")

# --- Test 2: commentary via --commentary (mix + ducking) ---
place_segment("e2e2", "testclip", 0, 12)
r = run_clip(["--config", "config.test.yaml", "--run-id", "e2e2",
              "--url", "https://example.com/watch?v=dummy",
              "--start", "0", "--end", "12", "--hook", "TEST HOOK",
              "--out", "testclip",
              "--commentary", "media/commentary-12s.wav",
              "--no-subtitles"])
check("T2 clip.py with --commentary exits 0", r.returncode == 0,
      r.stderr[-300:])
out2 = os.path.join(HERE, "out-clips", "testclip.mp4")
stats2 = json.load(open(os.path.join(HERE, "out-clips", "testclip.json")))
check("T2 stats record commentary=true",
      stats2.get("commentary") is True, str(stats2))
mag200_comm = goertzel_mag(out2, 200)
mag1000_comm = goertzel_mag(out2, 1000)
# Ducking check, relative balance inside the mix: the game tone was
# recorded at vol 0.5, commentary at vol 0.8, game ducked to 0.45, so
# expected game/commentary magnitude ratio = 0.45 * 0.5 / 0.8 = 0.28.
# (amix normalize=1 also halves the ABSOLUTE level of both tracks,
# so the game tone is ~0.225 of its solo level — that is the safe
# anti-clipping behaviour, not a ducking failure; the relative
# balance is what matters and it is exact.)
rel = mag200_comm / mag1000_comm if mag1000_comm else 0
abs_ratio = mag200_comm / mag200_no_comm if mag200_no_comm else 0
check("T2 commentary tone (1000 Hz) present in output",
      mag1000_comm > 100 and mag1000_comm > mag1000_no_comm * 10,
      f"1000Hz={mag1000_comm:.1f} (no-comm baseline "
      f"{mag1000_no_comm:.1f})")
check("T2 game ducked to 45% of commentary (relative ratio 0.20–0.40)",
      0.20 <= rel <= 0.40, f"game/comm ratio={rel:.2f}")
check("T2 absolute game level ~0.225 of solo (amix normalize halves)",
      0.15 <= abs_ratio <= 0.30, f"abs ratio={abs_ratio:.2f}")

# --- Test 3: commentary via config (no --commentary flag) ---
place_segment("e2e3", "testclip", 0, 12)
r = run_clip(["--config", "config.commentary.yaml", "--run-id", "e2e3",
              "--url", "https://example.com/watch?v=dummy",
              "--start", "0", "--end", "12", "--hook", "TEST HOOK",
              "--out", "testclip", "--no-subtitles"])
check("T3 clip.py with config commentary exits 0", r.returncode == 0,
      r.stderr[-300:])
out3 = os.path.join(HERE, "out-clips", "testclip.mp4")
stats3 = json.load(open(os.path.join(HERE, "out-clips", "testclip.json")))
mag1000_cfg = goertzel_mag(out3, 1000)
check("T3 config commentary actually mixed in (v1 bug fixed)",
      stats3.get("commentary") is True and mag1000_cfg > 100,
      f"1000Hz={mag1000_cfg:.1f} stats={stats3}")

# --- Test 4: commentary misconfig fails fast ---
r = run_clip(["--config", "config.test.yaml", "--run-id", "e2e4",
              "--url", "https://example.com/watch?v=dummy",
              "--start", "0", "--end", "12", "--hook", "X",
              "--out", "testclip",
              "--commentary", "media/does-not-exist.wav",
              "--no-subtitles"])
check("T4 missing commentary file exits 2 with a clear message",
      r.returncode == 2 and "not found" in (r.stderr + r.stdout),
      f"rc={r.returncode} out={r.stdout[-200:]} err={r.stderr[-200:]}")

# --- Test 5: timestamp rounding (0.4–12.4 -> actual 0–12) ---
place_segment("e2e5", "testclip3", 0, 12)
r = run_clip(["--config", "config.test.yaml", "--run-id", "e2e5",
              "--url", "https://example.com/watch?v=dummy",
              "--start", "0.4", "--end", "12.4", "--hook", "TEST",
              "--out", "testclip3", "--no-subtitles"])
check("T5 fractional window exits 0", r.returncode == 0, r.stderr[-300:])
stats5 = json.load(open(os.path.join(HERE, "out-clips", "testclip3.json")))
check("T5 stats record requested AND actual window",
      stats5.get("requested_start") == 0.4
      and stats5.get("requested_end") == 12.4
      and stats5.get("start") == 0 and stats5.get("end") == 12,
      str(stats5))

# --- Test 6: hard verification rejects bad clips ---
sys.path.insert(0, ROOT)
import clip as clipmod
media = os.path.join(HERE, "media")


def expect_reject(name, path, expected):
    try:
        clipmod.verify(path, expected, {"ffprobe_s": 30, "ffmpeg_s": 120})
        check(name, False, "verify() accepted a bad clip")
    except clipmod.VerificationError as exc:
        check(name, True)
        print(f"      reasons: {exc.reasons}")


expect_reject("T6a verify rejects wrong dimensions",
              os.path.join(media, "seg-12s.mp4"),
              {"width": 720, "height": 1280, "duration_s": 12.0})
expect_reject("T6b verify rejects no-audio file",
              os.path.join(media, "video-only.mp4"),
              {"width": 720, "height": 1280, "duration_s": 5.0})
expect_reject("T6c verify rejects corrupt file",
              os.path.join(media, "corrupt.mp4"),
              {"width": 720, "height": 1280, "duration_s": 5.0})
expect_reject("T6d verify rejects truncated file",
              os.path.join(media, "truncated.mp4"),
              {"width": 720, "height": 1280, "duration_s": 12.0})
try:
    st = clipmod.verify(out1, {"width": 720, "height": 1280,
                               "duration_s": 12.0,
                               "subtitles_expected": False},
                        {"ffprobe_s": 30, "ffmpeg_s": 120})
    check("T6e verify accepts the good test clip",
          st["decode_clean"] and st["width"] == 720)
except clipmod.VerificationError as exc:
    check("T6e verify accepts the good test clip", False, str(exc))

# --- Test 7: transcription preflight fails loudly (tiny not cached) ---
r = subprocess.run(
    [PY, CLIP, "--config", "config.yaml", "--run-id", "preflight1",
     "--url", "https://example.com/watch?v=dummy",
     "--start", "0", "--end", "12", "--hook", "X", "--out", "preflight"],
    cwd=ROOT, capture_output=True, text=True, timeout=60)
check("T7 preflight exits 2 fast with 'not cached' message",
      r.returncode == 2 and "not cached" in (r.stderr + r.stdout),
      f"rc={r.returncode} err={r.stderr[-300:]}")

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("FAILED:", ", ".join(FAIL))
    sys.exit(1)

#!/usr/bin/env python3
"""Clip stage: turn a YouTube URL + time window into a vertical clip.

Pipeline for one clip:
1. Download ONLY the requested segment with yt-dlp --download-sections
   (fragmented DASH streams are fetched per-fragment, so a 45s slice of
   a 12-hour VOD stays ~45s of data — never the whole VOD).
2. Transcribe the segment with faster-whisper (tiny) and burn the
   subtitles in, unless --no-subtitles.
3. ffmpeg: horizontal gameplay -> 1080x1920 vertical with a blurred
   background fill, hook text on the first frames, H.264 + AAC, faststart.
4. Hard verification: decode check + dimensions + duration + audio +
   subtitles. A clip that fails verification is DELETED and reported
   as rejected — never kept as "ok".

Commentary track: pass --commentary <audio file> of the same length
as the clip, or set commentary.enabled: true + commentary.audio in
config.yaml — the source audio is ducked to 45% under it. Without it
the clip ships with source audio only (and the digest says so).

v1.1 hardening (GPT review, 2026-11-19):
- Every subprocess call goes through procutil.run(): timeout enforced,
  whole process group killed on timeout (a stalled v1 download hung
  ~8 min with 0 bytes written).
- Float timestamps are ROUNDED explicitly (yt-dlp --download-sections
  truncates to ints); the ACTUAL window used is recorded in the stats.
- Segments are validated (size + ffprobe) before building; zero-byte
  and .part files are never treated as finished segments.
- Transcription preflight: the model cache is checked BEFORE any
  download; if subtitles are enabled but the model is not cached the
  run fails loudly instead of silently shipping hook-only clips.
- Hook text file is per-clip (no shared hook.txt), and filter paths
  are escaped for ffmpeg filtergraph syntax.
- The transcribe-venv path comes from config.yaml, not a hardcoded
  constant.

Usage:
    python clip.py --url <youtube-url> --start 65 --end 110 \\
        --hook "KAI CENAT LOSES IT" --out kai-cenat-01
"""
import argparse
import glob
import json
import os
import re
import subprocess
import sys

import yaml

import procutil

FFMPEG = "ffmpeg"
FFPROBE = "ffprobe"
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
DEFAULT_TRANSCRIBE_PY = os.path.expanduser(
    "~/workspace/transcribe-venv/bin/python")

# Fallback timeouts (seconds) when config.yaml has no timeouts section.
DEFAULT_TIMEOUTS = {
    "download_s": 1800,
    "transcribe_s": 900,
    "ffmpeg_s": 600,
    "ffprobe_s": 120,
}


class VerificationError(Exception):
    """Output verification failed; reasons explain what was wrong."""

    def __init__(self, reasons):
        super().__init__("verification failed: " + "; ".join(reasons))
        self.reasons = reasons


def load_config(path):
    with open(path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def timeouts_from_config(cfg):
    """Timeouts (seconds) for each subprocess stage."""
    t = dict(DEFAULT_TIMEOUTS)
    t.update((cfg.get("timeouts") or {}))
    return t


def run(cmd, what, timeout=None):
    """Log the command, then run it via procutil (timeout + group kill)."""
    print(f"  $ {' '.join(cmd)}")
    return procutil.run(cmd, what, timeout=timeout)


def ffmpeg_filter_escape(path):
    """Escape a file path for use inside an ffmpeg filter argument.

    In filtergraph syntax a backslash, single quote or colon inside an
    option value must be backslash-escaped, or the filter string is
    misparsed (a colon in a path splits the option; a quote ends it).
    """
    return (path.replace("\\", "\\\\")
                .replace("'", "\\'")
                .replace(":", "\\:"))


def resolve_commentary(args, cfg):
    """Work out which commentary audio file to mix in, if any.

    --commentary <file> wins; otherwise config commentary.enabled +
    commentary.audio. Returns the path, or None. Fails loudly when
    commentary is requested but the file is missing — v1 had a bug
    here (the condition could never execute and the config values
    were never read, so v1 clips were straight re-uploads in practice).
    """
    if args.commentary:
        path = args.commentary
    else:
        comm = cfg.get("commentary") or {}
        if not comm.get("enabled"):
            return None
        path = comm.get("audio")
        if not path:
            print("ERROR: commentary.enabled is true but commentary.audio "
                  "is not set in config.yaml — set the audio path or "
                  "disable commentary.", file=sys.stderr)
            raise SystemExit(2)
    if not os.path.isfile(path):
        print(f"ERROR: commentary audio not found: {path} — record the "
              "commentary track or remove the --commentary flag / disable "
              "commentary in config.yaml.", file=sys.stderr)
        raise SystemExit(2)
    return path


def model_cached(model):
    """True if faster-whisper weights for `model` are already in the HF cache.

    Fail-fast check: the sandbox egress is throttled, so downloading a
    75MB+ model mid-run would hang the pipeline for an hour.
    """
    base = os.path.expanduser("~/.cache/huggingface/hub")
    pat = os.path.join(base, f"models--Systran--faster-whisper-{model}",
                       "snapshots", "*", "model.bin")
    return any(os.path.getsize(p) > 10_000_000 for p in glob.glob(pat))


def cached_models():
    """faster-whisper models whose weights are already cached locally."""
    base = os.path.expanduser("~/.cache/huggingface/hub")
    out = []
    for d in glob.glob(os.path.join(base, "models--Systran--faster-whisper-*")):
        name = d.split("faster-whisper-")[-1]
        if model_cached(name):
            out.append(name)
    return sorted(out)


def check_transcription_ready(cfg, transcribe_py):
    """Preflight, run BEFORE any download: subtitles must be possible.

    Returns (enabled, model, language). If subtitles are enabled but
    the model weights are not cached, raises SystemExit(2) with a
    clear message — the run refuses to silently ship hook-only clips.
    """
    if not cfg["clips"].get("subtitles", True):
        return False, None, None
    model = cfg["clips"].get("whisper_model", "tiny")
    language = cfg["clips"].get("whisper_language", "en")
    if not os.path.isfile(transcribe_py):
        print(f"ERROR: transcribe venv python not found: {transcribe_py} "
              "(set paths.transcribe_venv_python in config.yaml).",
              file=sys.stderr)
        raise SystemExit(2)
    if not model_cached(model):
        cached = cached_models()
        hint = ""
        if cached:
            hint = (f" Cached weights exist for: {', '.join(cached)} — "
                    "set clips.whisper_model to one of those,")
        print(f"ERROR: subtitles are enabled but faster-whisper '{model}' "
              f"weights are not cached locally, so transcription would "
              f"hang on a throttled download.{hint} or pre-cache the "
              f"'{model}' model once on a faster network, or re-run with "
              "--no-subtitles (clip ships hook-only, by explicit choice).",
              file=sys.stderr)
        raise SystemExit(2)
    return True, model, language


def ffprobe_json(path, timeout):
    """ffprobe -> parsed JSON. Raises procutil errors on failure/timeout."""
    res = run([FFPROBE, "-v", "quiet", "-print_format", "json",
               "-show_format", "-show_streams", path],
              "ffprobe", timeout=timeout)
    return json.loads(res.stdout)


def validate_segment(path, timeout):
    """Reject zero-byte / partial / unplayable segments before building.

    Returns the ffprobe dict. Raises ValueError with a clear reason.
    """
    if path.endswith((".part", ".ytdl", ".temp")):
        raise ValueError(f"segment is a partial download: {path}")
    try:
        size = os.path.getsize(path)
    except OSError:
        raise ValueError(f"segment file missing: {path}")
    if size <= 0:
        raise ValueError(f"segment is empty (0 bytes): {path}")
    probe = ffprobe_json(path, timeout)
    video = [s for s in probe.get("streams", [])
             if s.get("codec_type") == "video"]
    if not video:
        raise ValueError(f"segment has no video stream: {path}")
    dur = float(probe.get("format", {}).get("duration") or 0)
    if dur <= 0:
        raise ValueError(f"segment has no playable duration: {path}")
    return probe


def download_segment(url, start, end, out_path, timeout):
    """Download only [start, end] of the video (fragment-aware).

    Float timestamps are ROUNDED explicitly — yt-dlp
    --download-sections truncates them to ints, so rounding here
    makes the requested window match the actual one. Returns
    (path, actual_start_s, actual_end_s).
    """
    start_i, end_i = int(round(start)), int(round(end))
    if end_i <= start_i:
        raise ValueError(
            f"empty window after rounding: {start}–{end}s -> "
            f"{start_i}–{end_i}s")
    cmd = [
        sys.executable, "-m", "yt_dlp",
        "--download-sections", f"*{start_i}-{end_i}",
        # 720p cap: YouTube throttles this sandbox's egress to ~12-60 KB/s,
        # so a 45s 1080p segment (~15-25 MB) takes 10-16 min while 720p
        # (~4 MB) takes 1-6 min. Raise to 1080 on a faster network.
        "-f", "bv*[height<=720]+ba/b[height<=720]",
        "--force-keyframes-at-cuts",
        "--socket-timeout", "60",
        "--no-playlist",
        "--retries", "3",
        "--sleep-requests", "1",
        "-o", out_path,
        url,
    ]
    run(cmd, "segment download", timeout=timeout)
    if not os.path.exists(out_path):
        # yt-dlp appends the real extension; find the file it made.
        base = os.path.splitext(out_path)[0]
        found = None
        for f in os.listdir(os.path.dirname(out_path) or "."):
            if f.startswith(os.path.basename(base)):
                if f.endswith((".part", ".ytdl", ".temp")):
                    continue
                found = os.path.join(os.path.dirname(out_path) or ".", f)
                break
        if found is None:
            raise ValueError(
                f"segment download produced no file for {url} "
                f"(window {start_i}–{end_i}s)")
        out_path = found
    validate_segment(out_path, timeout)
    return out_path, start_i, end_i


def transcribe(segment_path, srt_path, model, language, transcribe_py,
               timeout):
    """faster-whisper -> SRT. Runs in the shared transcribe venv.

    The language is set explicitly (no auto-detect — background audio
    plus chatter confuses detection). NOTE: the model is loaded
    once per clip.py process, i.e. once per clip in a pipeline run;
    a persistent transcription worker would avoid the reload, but
    the tiny model loads in seconds on this box. Never run
    transcription concurrently with ffmpeg — RAM is ~600 MB free.
    """
    script = (
        "import sys; from faster_whisper import WhisperModel\n"
        "model = WhisperModel(sys.argv[3], device='cpu', compute_type='int8')\n"
        "segments, _ = model.transcribe(sys.argv[1], beam_size=1, "
        "vad_filter=True, language=sys.argv[4])\n"
        "def fmt(t):\n"
        "    h, m = int(t // 3600), int((t % 3600) // 60)\n"
        "    s, ms = int(t % 60), int((t % 1) * 1000)\n"
        "    return f'{h:02d}:{m:02d}:{s:02d},{ms:03d}'\n"
        "with open(sys.argv[2], 'w', encoding='utf-8') as fh:\n"
        "    for i, s in enumerate(segments, 1):\n"
        "        fh.write(f\"{i}\\n{fmt(s.start)} --> {fmt(s.end)}\\n{s.text.strip()}\\n\\n\")\n"
    )
    run([transcribe_py, "-c", script, segment_path, srt_path, model,
         language], "transcription", timeout=timeout)
    return srt_path


def validate_srt(srt_path, max_end_s):
    """Validate an SRT: non-empty, well-formed timestamps, text present.

    Returns the number of cue entries. Raises ValueError on any
    problem — an empty SRT (e.g. a segment with no speech) fails
    loudly here instead of silently shipping a hook-only clip.
    """
    if not os.path.isfile(srt_path):
        raise ValueError(f"SRT file missing: {srt_path}")
    if os.path.getsize(srt_path) == 0:
        raise ValueError(
            f"SRT is empty: {srt_path} (no speech detected in the "
            "segment — re-run with --no-subtitles if a hook-only clip "
            "is acceptable).")
    ts_re = re.compile(
        r"^(\d{2}):(\d{2}):(\d{2}),(\d{3}) --> "
        r"(\d{2}):(\d{2}):(\d{2}),(\d{3})$")

    def to_s(m, base):
        return (int(m.group(base)) * 3600 + int(m.group(base + 1)) * 60
                + int(m.group(base + 2)) + int(m.group(base + 3)) / 1000.0)

    with open(srt_path, encoding="utf-8") as fh:
        text = fh.read()
    blocks = [b.strip() for b in text.strip().split("\n\n") if b.strip()]
    if not blocks:
        raise ValueError(f"SRT has no cue blocks: {srt_path}")
    for i, block in enumerate(blocks, 1):
        lines = block.splitlines()
        if len(lines) < 3 or not lines[0].strip().isdigit():
            raise ValueError(
                f"SRT block {i} is malformed (expected index/timestamp/"
                f"text): {srt_path}")
        m = ts_re.match(lines[1].strip())
        if not m:
            raise ValueError(
                f"SRT block {i} has an invalid timestamp line "
                f"{lines[1]!r}: {srt_path}")
        start_s = to_s(m, 1)
        end_s = to_s(m, 5)
        if end_s <= start_s:
            raise ValueError(
                f"SRT block {i} ends before it starts: {srt_path}")
        if end_s > max_end_s + 1.0:
            raise ValueError(
                f"SRT block {i} ends at {end_s:.1f}s, past the segment "
                f"({max_end_s:.1f}s): {srt_path}")
        if not " ".join(lines[2:]).strip():
            raise ValueError(f"SRT block {i} has no text: {srt_path}")
    return len(blocks)


def build_vertical(segment_path, out_path, hook, hook_s, srt_path,
                   commentary, cfg, tmp_dir, timeout):
    """ffmpeg: blur-fill vertical + hook text + subtitles + optional commentary.

    The hook text file is per-clip (tmp_dir/<out>-hook.txt) — v1 used
    one shared hook.txt, which is unsafe for concurrent runs. Filter
    paths are escaped for ffmpeg filtergraph syntax.
    """
    cl = cfg["clips"]
    w, h, fps = cl["width"], cl["height"], cl["fps"]
    hook_file = os.path.join(
        tmp_dir, f"{os.path.splitext(os.path.basename(out_path))[0]}-hook.txt")
    with open(hook_file, "w", encoding="utf-8") as fh:
        fh.write(hook.replace("\n", " "))

    fc = (
        "[0:v]split[a][b];"
        f"[a]scale={w}:{h}:force_original_aspect_ratio=increase,"
        f"crop={w}:{h},gblur=sigma=40[bg];"
        f"[b]scale={w}:-2[fg];"
        "[bg][fg]overlay=(W-w)/2:(H-h)/2[base];"
    )
    # Auto-shrink the hook so it fits the 1080px width (drawtext never wraps).
    # Factor 0.70 is conservative for all-caps DejaVu Sans Bold — 0.62
    # still clipped a 30-char hook at the edges (v1 clip 2).
    hook_len = max(1, len(hook))
    hook_fs = max(36, min(72, int(1000 / (hook_len * 0.70))))
    fc += (
        f"[base]drawtext=textfile={ffmpeg_filter_escape(hook_file)}:"
        f"fontfile={FONT}:fontsize={hook_fs}:"
        f"fontcolor=white:box=1:boxcolor=black@0.6:boxborderw=24:"
        f"x=(w-text_w)/2:y=140:enable='lt(t,{hook_s})'[hooked]"
    )
    if srt_path:
        style = ("FontName=DejaVu Sans,FontSize=40,PrimaryColour=&HFFFFFF&,"
                 "OutlineColour=&H000000&,BorderStyle=1,Outline=2,Shadow=1,"
                 "Alignment=2,MarginV=100")
        fc += (f";[hooked]subtitles='{ffmpeg_filter_escape(srt_path)}':"
               f"force_style='{style}'[vout]")
    else:
        fc += ";[hooked]null[vout]"

    cmd = [FFMPEG, "-y", "-i", segment_path]
    audio_maps = ["[vout]"]
    if commentary:
        cmd += ["-i", commentary]
        # Game at 0.45, commentary at 1.0 — the RELATIVE balance is
        # game:commentary = 0.45:1. amix normalize (default on) also
        # halves the absolute level of both tracks (anti-clipping);
        # that is expected, not a ducking failure.
        fc += (";[0:a]volume=0.45[a0];[1:a]volume=1.0[a1];"
               "[a0][a1]amix=inputs=2:duration=first[aout]")
        audio_maps.append("[aout]")
    else:
        audio_maps.append("0:a")
    cmd += [
        "-filter_complex", fc,
        "-map", audio_maps[0], "-map", audio_maps[1],
        "-c:v", "libx264", "-preset", "veryfast", "-crf", str(cl["crf"]),
        "-pix_fmt", "yuv420p", "-r", str(fps),
        "-c:a", "aac", "-b:a", cl["audio_bitrate"],
        "-movflags", "+faststart",
        out_path,
    ]
    run(cmd, "ffmpeg vertical build", timeout=timeout)
    return out_path


def verify(path, expected, timeouts):
    """Hard verification of a finished clip. Returns a stats dict.

    expected: width, height, duration_s (the ACTUAL window length),
    subtitles_expected (bool). ANY failure raises VerificationError —
    decode_clean=false no longer passes as "ok".
    """
    reasons = []
    try:
        probe = ffprobe_json(path, timeouts["ffprobe_s"])
    except (procutil.CommandError, procutil.CommandTimeout) as exc:
        raise VerificationError([f"ffprobe failed: {exc}"])
    streams = probe.get("streams", [])
    v = next((s for s in streams if s.get("codec_type") == "video"), None)
    a = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if v is None:
        reasons.append("no video stream")
    else:
        if v.get("width") != expected["width"]:
            reasons.append(
                f"width {v.get('width')} != {expected['width']}")
        if v.get("height") != expected["height"]:
            reasons.append(
                f"height {v.get('height')} != {expected['height']}")
        if v.get("codec_name") != "h264":
            reasons.append(f"video codec {v.get('codec_name')} != h264")
    if a is None:
        reasons.append("no audio stream")
    try:
        duration = float(probe.get("format", {}).get("duration") or 0)
    except (TypeError, ValueError):
        duration = 0
    if duration <= 0:
        reasons.append("no playable duration")
    elif abs(duration - expected["duration_s"]) > 1.0:
        reasons.append(
            f"duration {duration:.2f}s != expected "
            f"{expected['duration_s']:.2f}s (±1s)")
    size = int(probe.get("format", {}).get("size") or 0)
    if size <= 0:
        reasons.append("output file is empty")

    dec_err = ""
    try:
        run([FFMPEG, "-v", "error", "-i", path, "-f", "null", "-"],
            "decode check", timeout=timeouts["ffmpeg_s"])
    except procutil.CommandError as exc:
        reasons.append(f"decode failed: {exc.stderr.strip()[:200]}")
        dec_err = exc.stderr.strip()[:500]
    except procutil.CommandTimeout as exc:
        reasons.append(f"decode check timed out after {exc.timeout}s")
        dec_err = "timeout"

    if reasons:
        raise VerificationError(reasons)
    return {
        "duration_s": round(duration, 3),
        "width": v["width"], "height": v["height"],
        "codec": v["codec_name"], "fps": v.get("r_frame_rate"),
        "audio_codec": a["codec_name"] if a else None,
        "size_bytes": size,
        "decode_clean": True,
        "decode_errors": dec_err,
        "subtitles_burned_in": bool(expected.get("subtitles_expected")),
    }


def main():
    ap = argparse.ArgumentParser(description="Cut one vertical clip.")
    ap.add_argument("--url", required=True)
    ap.add_argument("--start", type=float, required=True)
    ap.add_argument("--end", type=float, required=True)
    ap.add_argument("--hook", required=True, help="Hook text, first frames.")
    ap.add_argument("--out", required=True, help="Output basename (no ext).")
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--run-id", default=None,
                    help="Per-run id: segments live in segments/<run-id>/ "
                         "and are cleaned up with that run only.")
    ap.add_argument("--no-subtitles", action="store_true")
    ap.add_argument("--commentary", default=None,
                    help="Commentary audio file (same length as clip).")
    args = ap.parse_args()

    try:
        cfg = load_config(args.config)
    except (OSError, ValueError) as exc:
        print(f"ERROR: cannot load config {args.config}: {exc}",
              file=sys.stderr)
        return 1
    timeouts = timeouts_from_config(cfg)
    transcribe_py = os.path.expanduser(
        (cfg.get("paths") or {}).get("transcribe_venv_python")
        or DEFAULT_TRANSCRIBE_PY)

    # Per-run segment dir (v1.1): segments/<run-id>/ isolates one run's
    # downloads so cleanup can never wipe another run's segments.
    seg_base_dir = cfg["paths"]["segments"]
    seg_dir = (os.path.join(seg_base_dir, args.run_id)
               if args.run_id else seg_base_dir)
    clip_dir = cfg["paths"]["clips"]
    os.makedirs(seg_dir, exist_ok=True)
    os.makedirs(clip_dir, exist_ok=True)

    try:
        # --- Preflight (BEFORE any download) ---
        subs_enabled, model, language = (
            (False, None, None) if args.no_subtitles
            else check_transcription_ready(cfg, transcribe_py))
        commentary = resolve_commentary(args, cfg)
        if commentary:
            print(f"  commentary: {commentary} "
                  "(source audio ducked to 45%)")

        # --- Window (rounded explicitly; yt-dlp truncates floats) ---
        start_i, end_i = int(round(args.start)), int(round(args.end))
        if end_i <= start_i:
            print(f"ERROR: empty window after rounding: {args.start}–"
                  f"{args.end}s -> {start_i}–{end_i}s", file=sys.stderr)
            return 1
        expected_len = end_i - start_i
        print(f"== Window {start_i}–{end_i}s (requested {args.start}–"
              f"{args.end}s, rounded) ==")

        # --- Segment: reuse a validated one, else download ---
        # Reuse an already-downloaded segment for the SAME window (the
        # segment name carries start/end, so a different window never
        # reuses stale data; yt-dlp keeps the real container extension,
        # e.g. {out}-60-105.mp4.webm). Partial downloads (.part) are
        # NEVER reused as finished segments.
        seg_base = os.path.join(
            seg_dir, f"{args.out}-{start_i}-{end_i}.mp4")
        seg_path = None
        for cand in sorted(glob.glob(seg_base + "*")):
            if cand.endswith((".part", ".ytdl", ".temp")):
                continue
            if os.path.getsize(cand) <= 0:
                continue
            try:
                validate_segment(cand, timeouts["ffprobe_s"])
            except (ValueError, procutil.CommandError,
                    procutil.CommandTimeout) as exc:
                print(f"  [!] cached segment {cand} invalid ({exc}) — "
                      "re-downloading")
                continue
            seg_path = cand
            break
        actual_start, actual_end = start_i, end_i
        if seg_path:
            print(f"== Reusing validated segment {seg_path} ==")
        else:
            print(f"== Download segment {start_i}–{end_i}s ==")
            seg_path, actual_start, actual_end = download_segment(
                args.url, args.start, args.end, seg_base,
                timeouts["download_s"])

        # --- Subtitles (model loaded once per clip.py process) ---
        srt_path = None
        srt_cues = 0
        if subs_enabled:
            print(f"== Transcribe (faster-whisper {model}, "
                  f"lang={language}) ==")
            srt_path = os.path.join(seg_dir, f"{args.out}.srt")
            transcribe(seg_path, srt_path, model, language,
                       transcribe_py, timeouts["transcribe_s"])
            try:
                seg_probe = ffprobe_json(seg_path, timeouts["ffprobe_s"])
                seg_dur = float(seg_probe.get("format", {})
                                .get("duration") or 0)
            except (procutil.CommandError, procutil.CommandTimeout,
                    ValueError):
                seg_dur = expected_len
            srt_cues = validate_srt(srt_path, seg_dur)
            print(f"  SRT validated: {srt_cues} cues")

        # --- Build ---
        out_path = os.path.join(clip_dir, f"{args.out}.mp4")
        print("== Build vertical clip ==")
        build_vertical(seg_path, out_path, args.hook,
                       cfg["clips"]["hook_duration_s"], srt_path,
                       commentary, cfg, seg_dir, timeouts["ffmpeg_s"])

        # --- Hard verification (rejects bad clips) ---
        print("== Verify (hard gate) ==")
        stats = verify(out_path, {
            "width": cfg["clips"]["width"],
            "height": cfg["clips"]["height"],
            "duration_s": float(actual_end - actual_start),
            "subtitles_expected": bool(srt_path),
        }, timeouts)
        print(json.dumps(stats, indent=2))
        stats_path = os.path.join(clip_dir, f"{args.out}.json")
        with open(stats_path, "w", encoding="utf-8") as fh:
            json.dump({"url": args.url,
                       "requested_start": args.start,
                       "requested_end": args.end,
                       "start": actual_start, "end": actual_end,
                       "hook": args.hook,
                       "subtitles": bool(srt_path), "srt_cues": srt_cues,
                       "commentary": bool(commentary),
                       "status": "ok", **stats},
                      fh, ensure_ascii=False, indent=2)
        print(f"Wrote {out_path}")
        return 0

    except VerificationError as exc:
        # A bad clip must never be kept as "ok": delete the output,
        # record the rejection, fail the process.
        out_path = os.path.join(clip_dir, f"{args.out}.mp4")
        if os.path.exists(out_path):
            os.remove(out_path)
            print(f"  [!] rejected output deleted: {out_path}")
        stats_path = os.path.join(clip_dir, f"{args.out}.json")
        with open(stats_path, "w", encoding="utf-8") as fh:
            json.dump({"url": args.url,
                       "requested_start": args.start,
                       "requested_end": args.end,
                       "hook": args.hook,
                       "subtitles": False, "commentary": False,
                       "status": "rejected",
                       "reasons": exc.reasons},
                      fh, ensure_ascii=False, indent=2)
        print(f"ERROR: clip REJECTED — {exc}", file=sys.stderr)
        return 1
    except (procutil.CommandError, procutil.CommandTimeout,
            ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except SystemExit as exc:
        # Preflight / commentary resolution already printed its message.
        return exc.code if isinstance(exc.code, int) else 1


if __name__ == "__main__":
    raise SystemExit(main())

#!/home/hatch/workspace/transcribe-venv/bin/python
"""Transcribe the picture-lock audio with faster-whisper (design G6).

2026-11-24 self-improvement (caption completion): the old version ran
model "tiny" only, and every build since has shipped gappy captions — words
Whisper-tiny misheard or missed were dropped rather than shown wrong
(Lacy: "What?" uncaptioned, "I"/"his" dropped; Tim: 11 words dropped;
YourRAGE: 23 words shown). Competitor clip channels caption every word.
So this script now runs BOTH "tiny" and "small" (word_timestamps=True,
condition_on_previous_text=False, vad_filter=True) and reconciles them
into one final_transcript.json:

  * A word attested by either run at probability >= WORD_MIN_PROB (0.4)
    survives to captions; build_ass.py still drops anything below 0.4.
  * When both runs hear the same word (same normalized text, starts within
    0.6s — timestamps jitter between runs), the higher-probability entry
    wins and provenance records both. When they disagree at the same time
    (different word, starts within 0.3s), tiny wins and small's word is
    logged as a conflict — small may only ADD words, never replace tiny's.
    Highest-probability-wins across models is wrong: small confidently
    misheard "1998" as "1990" (p 0.95) on Tim while tiny heard it right.
  * Words heard by only one run are kept with that run's probability and
    provenance, so a word tiny missed entirely is no longer silently lost —
    except a small-only word needs attestation: pass --vad vad_final.json
    and it must also have probability >= 0.4 and >= 50% VAD overlap, or
    the union admits hallucinations (Tim test: raw union 95 words with 30
    junk; VAD-attested union 65 words, all kept at G7 vs 60 tiny-only).
  * Per-word "runs" records which model(s) attested it, for the audit.

Times are in final-audio seconds (= final timeline, because the picture-lock
audio IS the final audio). Writes final_transcript.json plus sidecars
final_transcript_tiny.json and final_transcript_small.json next to it:
    {"model": "tiny+small-reconciled",
     "words": [{"word": str, "start": float, "end": float,
                "probability": float, "runs": ["tiny", "small"]}],
     "segments": [{"start": float, "end": float, "text": str}]}

Usage:
    /home/hatch/workspace/transcribe-venv/bin/python transcribe_final.py \\
        --audio picture_lock.mp4 --out final_transcript.json

Exit code 0 on success, 1 on failure. Prints a JSON result to stdout.
"""
import argparse
import json
import os
import sys
import unicodedata

VENV_PYTHON = "/home/hatch/workspace/transcribe-venv/bin/python"
ALIGN_TOL_S = 0.3
# Same-word dedup window: Whisper timestamps jitter between runs (Tim:
# small put "since" at 4.89, tiny at 5.33 — the same word). 0.6s merges
# those without merging genuinely repeated words, which sit >1s apart.
DEDUP_TOL_S = 0.6


def _norm(word):
    return "".join(
        ch for ch in str(word).upper()
        if unicodedata.category(ch)[0] not in ("P", "S", "Z")
    )


def _run_model(model_name, audio):
    """Run one faster-whisper model; return (words, segments)."""
    from faster_whisper import WhisperModel
    model = WhisperModel(model_name, device="cpu", compute_type="int8")
    segments, _info = model.transcribe(
        audio,
        word_timestamps=True,
        condition_on_previous_text=False,
        vad_filter=True,
    )
    words = []
    out_segments = []
    for seg in segments:
        out_segments.append({
            "start": float(seg.start),
            "end": float(seg.end),
            "text": seg.text,
        })
        for word in (seg.words or []):
            words.append({
                "word": word.word,
                "start": float(word.start),
                "end": float(word.end),
                "probability": float(word.probability),
            })
    return words, out_segments


def _vad_overlap_fraction(start, end, speech):
    """Fraction of [start, end] covered by VAD speech intervals."""
    if end <= start or not speech:
        return 0.0
    total = 0.0
    for seg in speech:
        s = max(start, float(seg["start"]))
        e = min(end, float(seg["end"]))
        if e > s:
            total += e - s
    return total / (end - start)


def _reconcile(tiny_words, small_words, speech=None):
    """Union tiny + small words, deduplicating same-word/same-time pairs.

    Same word = same normalized text with starts within ALIGN_TOL_S.
    The higher-probability entry wins; provenance lists both runs.
    CONFLICT RULE (2026-11-24): when small hears a DIFFERENT word at the
    same time as tiny (overlapping span, different norm), tiny wins and
    small's word is discarded as a conflict — small may only ADD words in
    windows where tiny heard nothing. Highest-probability-wins across
    models is wrong: small confidently misheard "1998" as "1990" (p 0.95)
    on Tim while tiny heard it right. A conflict is logged, never auto-
    resolved to the wrong word; the builder resolves it against the
    planning transcript (S1 authority) in the curation pass.
    A small-only word enters the transcript ONLY if it is attested: when
    --vad is given it needs probability >= 0.4 AND >= 50% VAD overlap —
    without that bar the union admits hallucinations and mis-timed
    fragments (measured on Tim, 2026-11-24: raw union 95 words, 30 of
    them junk; VAD-attested union 65 words, all kept at G7 vs 60 before).
    Tiny words are kept as-is; build_ass.py still drops their low ones.
    Returns (merged_words, conflicts).
    """
    merged = []
    conflicts = []
    used_small = [False] * len(small_words)
    for tw in tiny_words:
        best = None
        best_j = None
        for j, sw in enumerate(small_words):
            if used_small[j]:
                continue
            if _norm(sw["word"]) != _norm(tw["word"]):
                continue
            if abs(sw["start"] - tw["start"]) <= DEDUP_TOL_S:
                if best is None or sw["probability"] > best["probability"]:
                    best = sw
                    best_j = j
        if best is not None:
            used_small[best_j] = True
            winner = tw if tw["probability"] >= best["probability"] else best
            merged.append({
                "word": winner["word"],
                "start": float(winner["start"]),
                "end": float(winner["end"]),
                "probability": float(max(tw["probability"], best["probability"])),
                "runs": ["tiny", "small"],
            })
        else:
            merged.append({
                "word": tw["word"],
                "start": float(tw["start"]),
                "end": float(tw["end"]),
                "probability": float(tw["probability"]),
                "runs": ["tiny"],
            })
    for j, sw in enumerate(small_words):
        if not used_small[j]:
            # Conflict: small hears a different word where tiny heard one.
            clash = [tw for tw in tiny_words
                     if abs(tw["start"] - sw["start"]) <= ALIGN_TOL_S
                     and _norm(tw["word"]) != _norm(sw["word"])]
            if clash:
                conflicts.append({
                    "small_word": sw["word"],
                    "small_start": float(sw["start"]),
                    "small_probability": float(sw["probability"]),
                    "tiny_word": clash[0]["word"],
                    "tiny_start": float(clash[0]["start"]),
                    "tiny_probability": float(clash[0]["probability"]),
                    "resolution": "tiny_kept",
                })
                continue
            if speech is not None:
                ov = _vad_overlap_fraction(sw["start"], sw["end"], speech)
                if sw["probability"] < 0.4 or ov < 0.5:
                    continue
            merged.append({
                "word": sw["word"],
                "start": float(sw["start"]),
                "end": float(sw["end"]),
                "probability": float(sw["probability"]),
                "runs": ["small"],
            })
    merged.sort(key=lambda w: (w["start"], w["end"]))
    return merged, conflicts


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Transcribe picture-lock audio with faster-whisper tiny + "
                    "small, reconciled (word timestamps, no previous-text "
                    "conditioning, VAD filter).",
        epilog="MUST be run under the transcribe venv python: %s "
               "(system python3 has no faster-whisper). Example: %s "
               "transcribe_final.py --audio picture_lock.mp4 --out final_transcript.json"
               % (VENV_PYTHON, VENV_PYTHON),
    )
    parser.add_argument("--audio", required=True,
                        help="Picture-lock file (its audio IS the final audio).")
    parser.add_argument("--out", required=True,
                        help="Output path for final_transcript.json.")
    parser.add_argument("--vad", default=None,
                        help="Optional vad_final.json: when given, a small-only "
                             "word enters the reconciled transcript only if it "
                             "has probability >= 0.4 and >= 50%% VAD overlap. "
                             "Run vad.py first, then re-run this script with "
                             "--vad for the attested transcript.")
    args = parser.parse_args(argv)

    if not os.path.isfile(args.audio):
        print(json.dumps({"ok": False, "error": "audio file not found: %s" % args.audio}))
        return 1

    try:
        from faster_whisper import WhisperModel  # noqa: F401
    except ImportError as exc:
        print(json.dumps({"ok": False,
                          "error": "faster-whisper is not importable under this interpreter: %s. "
                                   "Run this script with %s" % (exc, VENV_PYTHON)}))
        return 1

    try:
        tiny_words, tiny_segments = _run_model("tiny", args.audio)
        small_words, small_segments = _run_model("small", args.audio)
    except Exception as exc:  # noqa: BLE001 - report any model failure as JSON
        print(json.dumps({"ok": False, "error": "transcription failed: %s" % exc}))
        return 1

    speech = None
    if args.vad:
        try:
            with open(args.vad, "r", encoding="utf-8") as f:
                speech = json.load(f).get("speech") or []
        except (OSError, json.JSONDecodeError) as exc:
            print(json.dumps({"ok": False, "error": "VAD file unreadable: %s" % exc}))
            return 1

    words, conflicts = _reconcile(tiny_words, small_words, speech)
    # Segments: small hears more complete sentences; fall back to tiny.
    segments = small_segments or tiny_segments

    out_dir = os.path.dirname(os.path.abspath(args.out)) or "."
    base = os.path.splitext(os.path.basename(args.out))[0]
    tiny_path = os.path.join(out_dir, "final_transcript_tiny.json")
    small_path = os.path.join(out_dir, "final_transcript_small.json")
    for path, model_name, mwords, msegs in (
        (tiny_path, "tiny", tiny_words, tiny_segments),
        (small_path, "small", small_words, small_segments),
    ):
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"model": model_name, "words": mwords,
                       "segments": msegs}, f, indent=2)
            f.write("\n")

    transcript = {"model": "tiny+small-reconciled", "words": words,
                  "segments": segments, "conflicts": conflicts}
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(transcript, f, indent=2)
        f.write("\n")

    result = {"ok": True, "out": args.out,
              "model": "tiny+small-reconciled",
              "words": len(words),
              "tiny_words": len(tiny_words),
              "small_words": len(small_words),
              "small_only_words": sum(1 for w in words if w["runs"] == ["small"]),
              "tiny_only_words": sum(1 for w in words if w["runs"] == ["tiny"]),
              "conflicts": len(conflicts),
              "segments": len(segments),
              "duration_s": segments[-1]["end"] if segments else 0.0}
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())

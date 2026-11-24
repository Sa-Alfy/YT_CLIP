#!/usr/bin/env python3
"""build_ass.py — build captions.ass from the FINAL transcript only (G7).

Implements CAP-1..CAP-7 input side of claude-skill-design-2026-11-20.md:
  * ONLY text inputs are final_transcript.json + attribution_review.json
    (CAP-7: never read OCR, subtitles, or any other text source).
  * Words with Whisper probability < WORD_MIN_PROB (0.4), or with < 50% of
    their span inside Silero VAD speech, are dropped and logged to
    dropped_words.json with a reason.
  * attribution_review.json decisions are applied: drop = excluded.
  * Emits ASS: PlayResX 1080, PlayResY 1920, one word per event, ALL-CAPS,
    caption centre y at caption_y_pct from the EDL (must be 60-65% of 1920).
  * Punch words (at most max_punch_ratio of words, each must exist in the
    transcript) are styled distinctly (Punch style: yellow, larger, bold).
    Punch words come from EDL caption_policy.punch_words when present;
    otherwise they are derived from the hook text: transcript words that
    also appear in the hook, in transcript order, capped at the ratio.

Exit 0 on success, 1 on failure. Prints a JSON result to stdout.
"""

import argparse
import json
import os
import sys
import unicodedata

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import constants  # noqa: E402
import gateio  # noqa: E402


def load_json(path, label):
    if not os.path.exists(path):
        raise FileNotFoundError("%s not found: %s" % (label, path))
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def norm(word):
    """Normalize for matching: uppercase, strip punctuation/symbols/spaces."""
    return "".join(
        ch for ch in str(word).upper()
        if unicodedata.category(ch)[0] not in ("P", "S", "Z")
    )


def vad_overlap_fraction(start, end, speech):
    """Fraction of [start, end] covered by VAD speech intervals."""
    if end <= start:
        return 0.0
    total = 0.0
    for seg in speech:
        s = max(start, float(seg["start"]))
        e = min(end, float(seg["end"]))
        if e > s:
            total += e - s
    return total / (end - start)


def ass_time(t):
    """Seconds -> ASS timestamp H:MM:SS.cc (centisecond precision)."""
    cs = int(round(float(t) * 100))
    if cs < 0:
        cs = 0
    h = cs // 360000
    m = (cs // 6000) % 60
    s = (cs // 100) % 60
    c = cs % 100
    return "%d:%02d:%02d.%02d" % (h, m, s, c)


def build_attribution_lookup(reviews):
    """Index attribution reviews by (normalized word, rounded start)."""
    lookup = {}
    for r in reviews or []:
        try:
            key = (norm(r.get("word", "")), round(float(r.get("start", 0.0)), 2))
        except (TypeError, ValueError):
            continue
        lookup.setdefault(key, r)
    return lookup


def attribution_decision(lookup, word, start):
    """Return the review dict for a transcript word, or None.

    Exact (word, start) match first, then same word with start within 0.02s.
    """
    key = (norm(word), round(float(start), 2))
    if key in lookup:
        return lookup[key]
    for (w, s), r in lookup.items():
        if w == key[0] and abs(s - key[1]) <= 0.02:
            return r
    return None


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Build captions.ass from final_transcript.json + "
                    "attribution_review.json (Stage 1, gate G7)."
    )
    ap.add_argument("--transcript", required=True, help="final_transcript.json")
    ap.add_argument("--vad", required=True, help="vad_final.json")
    ap.add_argument("--attribution", required=True, help="attribution_review.json")
    ap.add_argument("--edl", required=True, help="edl.json")
    ap.add_argument("--out", required=True, help="output captions.ass path")
    ap.add_argument("--dropped", required=True, help="output dropped_words.json path")
    args = ap.parse_args(argv)

    try:
        transcript = load_json(args.transcript, "transcript")
        vad = load_json(args.vad, "VAD")
        attribution = load_json(args.attribution, "attribution review")
        edl = load_json(args.edl, "EDL")
    except (FileNotFoundError, json.JSONDecodeError, OSError) as e:
        print(json.dumps({"ok": False, "error": str(e)}))
        return 1

    # --- EDL caption settings -------------------------------------------------
    try:
        caption_y_pct = float(edl["layout"]["caption_y_pct"])
    except (KeyError, TypeError, ValueError):
        print(json.dumps({"ok": False,
                          "error": "EDL layout.caption_y_pct missing or not a number"}))
        return 1
    if not (constants.CAPTION_Y_PCT_MIN <= caption_y_pct <= constants.CAPTION_Y_PCT_MAX):
        print(json.dumps({
            "ok": False,
            "error": "caption_y_pct %.2f outside %d-%d%% of 1920 (CAP-6)"
                     % (caption_y_pct, constants.CAPTION_Y_PCT_MIN,
                        constants.CAPTION_Y_PCT_MAX),
            "caption_y_pct": caption_y_pct,
        }))
        return 1
    caption_y = int(round(caption_y_pct / 100.0 * constants.H))

    policy = edl.get("caption_policy") or {}
    try:
        max_punch_ratio = float(policy.get("max_punch_ratio", 0.25))
    except (TypeError, ValueError):
        max_punch_ratio = 0.25

    words = transcript.get("words") or []
    if not words:
        print(json.dumps({"ok": False, "error": "transcript has no words"}))
        return 1
    speech = vad.get("speech") or []
    reviews = attribution.get("reviews") or []
    lookup = build_attribution_lookup(reviews)

    # --- Drop rules (G7): low probability, <50% VAD overlap, attribution drop
    kept = []
    dropped = []
    for w in words:
        try:
            word = str(w["word"])
            start = float(w["start"])
            end = float(w["end"])
            prob = float(w.get("probability", 1.0))
        except (KeyError, TypeError, ValueError):
            dropped.append({"word": str(w.get("word", "")),
                            "start": w.get("start"), "end": w.get("end"),
                            "reason": "malformed_word_entry"})
            continue
        reasons = []
        if prob < constants.WORD_MIN_PROB:
            reasons.append("low_probability:%.3f<%.1f" % (prob, constants.WORD_MIN_PROB))
        overlap = vad_overlap_fraction(start, end, speech)
        if overlap < constants.VAD_MIN_OVERLAP:
            reasons.append("vad_overlap_lt_50pct:%.2f" % overlap)
        review = attribution_decision(lookup, word, start)
        if review is not None and str(review.get("decision", "")).lower() == "drop":
            reasons.append("attribution_drop")
        if reasons:
            dropped.append({"word": word, "start": start, "end": end,
                            "reason": ";".join(reasons)})
        else:
            kept.append({"word": word, "start": start, "end": end,
                         "probability": prob})

    if not kept:
        print(json.dumps({"ok": False,
                          "error": "all %d transcript words dropped; no captions to emit"
                                   % len(words),
                          "dropped_count": len(dropped)}))
        return 1

    # --- Punch words ----------------------------------------------------------
    kept_norms = {norm(k["word"]) for k in kept}
    explicit = policy.get("punch_words")
    if explicit is not None:
        punch = [str(p) for p in explicit]
        unknown = [p for p in punch if norm(p) not in
                   {norm(w["word"]) for w in words}]
        if unknown:
            print(json.dumps({
                "ok": False,
                "error": "punch words not in transcript (CAP-6): %s" % unknown,
                "punch_words": punch,
            }))
            return 1
        allowed = int(max_punch_ratio * len(kept))
        if len(punch) > allowed:
            print(json.dumps({
                "ok": False,
                "error": "punch words %d exceed max_punch_ratio %.2f of %d kept words (max %d)"
                         % (len(punch), max_punch_ratio, len(kept), allowed),
                "punch_words": punch,
            }))
            return 1
        punch_set = {norm(p) for p in punch}
    else:
        # Derive from the hook text: kept words that also appear in the hook,
        # in transcript order, capped at max_punch_ratio of kept words.
        # 2026-11-24: stopwords are excluded — the old derivation highlighted
        # "THE" / "HE" (Lacy) because they appear in the hook text. Punch is
        # for pictureable words only (numbers, verbs, nouns).
        hook_text = ""
        hook = edl.get("hook") or {}
        if isinstance(hook, dict):
            hook_text = str(hook.get("text", "") or "")
        hook_norms = {norm(tok) for tok in hook_text.split()}
        hook_norms -= set(constants.STOPWORDS_PUNCH)
        allowed = int(max_punch_ratio * len(kept))
        punch_set = set()
        for k in kept:
            if len(punch_set) >= allowed:
                break
            if norm(k["word"]) in hook_norms and norm(k["word"]) not in punch_set:
                punch_set.add(norm(k["word"]))
        punch = [k["word"] for k in kept if norm(k["word"]) in punch_set]

    # --- Emit ASS -------------------------------------------------------------
    lines = []
    lines.append("[Script Info]")
    lines.append("Title: Clutch Cut captions")
    lines.append("ScriptType: v4.00+")
    lines.append("PlayResX: %d" % constants.W)
    lines.append("PlayResY: %d" % constants.H)
    lines.append("WrapStyle: 0")
    lines.append("ScaledBorderAndShadow: yes")
    lines.append("")
    lines.append("[V4+ Styles]")
    lines.append("Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
                 "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
                 "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
                 "Alignment, MarginL, MarginR, MarginV, Encoding")
    # Default: white, bold, centred (Alignment 5 = middle-centre; positioned
    # per-event with \pos so the centre y is exactly caption_y).
    lines.append("Style: Default,Arial,72,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,"
                 "-1,0,0,0,100,100,0,0,1,3,0,5,20,20,20,1")
    # Punch: yellow, larger, bold — distinctly styled (CAP-6).
    lines.append("Style: Punch,Arial,96,&H0000FFFF,&H000000FF,&H00000000,&H80000000,"
                 "-1,0,0,0,100,100,0,0,1,4,0,5,20,20,20,1")
    lines.append("")
    lines.append("[Events]")
    lines.append("Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text")
    for k in kept:
        start_cs = int(round(k["start"] * 100))
        end_cs = int(round(k["end"] * 100))
        if end_cs <= start_cs:
            end_cs = start_cs + 1
        style = "Punch" if norm(k["word"]) in punch_set else "Default"
        text = "{\\pos(%d,%d)}%s" % (constants.W // 2, caption_y, k["word"].strip().upper())
        # 10 fields: Layer, Start, End, Style, Name, MarginL, MarginR,
        # MarginV, Effect (empty), Text. A 9-field line is malformed ASS —
        # libass reads the Text as the Effect and renders nothing.
        lines.append("Dialogue: 0,%s,%s,%s,,0,0,0,,%s"
                     % (ass_time(start_cs / 100.0), ass_time(end_cs / 100.0),
                        style, text))

    out_dir = os.path.dirname(os.path.abspath(args.out))
    os.makedirs(out_dir, exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    dropped_doc = {"dropped": dropped,
                   "total_words": len(words),
                   "kept_words": len(kept),
                   "dropped_count": len(dropped)}
    dropped_dir = os.path.dirname(os.path.abspath(args.dropped))
    os.makedirs(dropped_dir, exist_ok=True)
    with open(args.dropped, "w", encoding="utf-8") as f:
        json.dump(dropped_doc, f, indent=2, ensure_ascii=False)
        f.write("\n")

    result = {
        "ok": True,
        "out": args.out,
        "dropped": args.dropped,
        "total_words": len(words),
        "kept_words": len(kept),
        "dropped_count": len(dropped),
        "captions_sha256": gateio.sha256_file(args.out),
        "caption_y_pct": caption_y_pct,
        "caption_y": caption_y,
        "punch_words": punch,
        "max_punch_ratio": max_punch_ratio,
    }
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())

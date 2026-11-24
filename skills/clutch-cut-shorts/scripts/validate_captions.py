#!/usr/bin/env python3
"""validate_captions.py — validate captions.ass against the final transcript (G7).

Checks CAP-1..CAP-7, VAD-1 and HOOK-1 from claude-skill-design-2026-11-20.md
sections 3-4. Each check is reported as {rule, pass, evidence} and the gate
is stamped G7 via gateio with per-rule evidence and inputs_sha256 of the five
input files.

Method notes:
  * CAP-2 subsequence walk skips transcript words dropped by build_ass.py
    (low probability / low speech overlap / attribution drop), so a caption
    list that omits dropped words still aligns.
  * glossary_overrides from the EDL caption_policy are the only allowed
    exceptions to the subsequence rule, and each one is logged.
  * VAD-1 scans ffmpeg_commands.log (in workdir, if present) and every
    verification script in this scripts directory for forbidden
    speech-evidence method strings. The forbidden strings are assembled
    from fragments below so this file itself never contains them literally.

Exit 0 when every check passes, 1 otherwise. Prints JSON to stdout:
  {"ok": bool, "checks": [{rule, pass, evidence}]}
"""

import argparse
import json
import os
import re
import sys
import unicodedata

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import constants  # noqa: E402
import gateio  # noqa: E402

# Forbidden speech-evidence method strings (VAD-1). Assembled from fragments
# so the literals never appear in this file's own source.
_FORBIDDEN_METHOD_STRINGS = ["volume" + "detect", "a" + "stats", "R" + "MS"]


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
    if end <= start:
        return 0.0
    total = 0.0
    for seg in speech:
        s = max(start, float(seg["start"]))
        e = min(end, float(seg["end"]))
        if e > s:
            total += e - s
    return total / (end - start)


def parse_ass_time(text):
    """ASS H:MM:SS.cc -> seconds."""
    m = re.match(r"^(\d+):(\d{2}):(\d{2})\.(\d{2})$", text.strip())
    if not m:
        raise ValueError("bad ASS timestamp: %r" % text)
    h, mi, s, cs = (int(g) for g in m.groups())
    return h * 3600 + mi * 60 + s + cs / 100.0


def parse_ass(path):
    """Parse captions.ass -> (header dict, styles dict, [caption events],
    [parse problems]).

    Each event: {start, end, style, text, pos_y (or None), margin_v,
    raw, malformed}. Text has override tags ({...}) stripped.
    A Dialogue line has 10 comma-separated fields (Layer, Start, End,
    Style, Name, MarginL, MarginR, MarginV, Effect, Text) — split on the
    first 9 commas only, because the Text itself may contain commas.
    A line with fewer than 10 fields is malformed (a 9-field line is the
    classic error: libass then reads the Text as the Effect and renders
    nothing); it is kept as an event with malformed=True so CAP-6 fails
    it instead of silently dropping it.
    """
    header = {}
    styles = {}
    events = []
    problems = []
    section = None
    with open(path, "r", encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line:
                continue
            if line.startswith("["):
                section = line
                continue
            if section == "[Script Info]" and ":" in line:
                k, v = line.split(":", 1)
                header[k.strip()] = v.strip()
            elif section == "[V4+ Styles]" and line.startswith("Style:"):
                parts = [p.strip() for p in line[len("Style:"):].split(",")]
                if len(parts) >= 23:
                    # Name(0), Fontname(1), Fontsize(2), ..., Shadow(17),
                    # Alignment(18), MarginL(19), MarginR(20), MarginV(21),
                    # Encoding(22).
                    try:
                        styles[parts[0]] = {
                            "fontsize": int(float(parts[2])),
                            "alignment": int(parts[18]),
                            "margin_v": int(float(parts[21])),
                        }
                    except ValueError:
                        styles[parts[0]] = {"fontsize": None,
                                            "alignment": None, "margin_v": None}
            elif section == "[Events]" and line.startswith("Dialogue:"):
                body = line[len("Dialogue:"):]
                parts = body.split(",", 9)
                if len(parts) < 10:
                    problems.append("Dialogue line has %d fields, expected 10: %r"
                                    % (len(parts), line[:120]))
                    text_field = parts[-1] if parts else ""
                    start_s = end_s = 0.0
                    try:
                        start_s = parse_ass_time(parts[1].strip())
                        end_s = parse_ass_time(parts[2].strip())
                    except (ValueError, IndexError):
                        pass
                    events.append({"start": start_s, "end": end_s,
                                   "style": parts[3].strip() if len(parts) > 3 else "",
                                   "text": re.sub(r"\{[^}]*\}", "", text_field).strip(),
                                   "pos_y": None, "margin_v": 0, "raw": line,
                                   "malformed": True})
                    continue
                _layer, start, end, style = (p.strip() for p in parts[:4])
                try:
                    margin_v = int(float(parts[7].strip()))
                except ValueError:
                    margin_v = 0
                text_field = parts[9]
                pos_y = None
                m = re.search(r"\\pos\(\s*(-?\d+)\s*,\s*(-?\d+)\s*\)", text_field)
                if m:
                    pos_y = int(m.group(2))
                text = re.sub(r"\{[^}]*\}", "", text_field).strip()
                try:
                    start_s = parse_ass_time(start)
                    end_s = parse_ass_time(end)
                except ValueError:
                    continue
                events.append({"start": start_s, "end": end_s, "style": style,
                               "text": text, "pos_y": pos_y, "margin_v": margin_v,
                               "raw": line, "malformed": False})
    return header, styles, events, problems


def caption_centre_y(event, styles, res_y):
    """Centre y of a caption event in output pixels, or None if unknowable.

    Uses \\pos when present. Otherwise estimates from the style's
    alignment + effective MarginV (the Dialogue MarginV overrides the
    style's when non-zero) + fontsize:
      alignment 2 (bottom-centre): res_y - margin_v - fontsize/2
      alignment 8 (top-centre):    margin_v + fontsize/2
      alignment 5 (middle-centre): res_y / 2
    Other alignments (left/right variants) cannot be estimated reliably
    from these fields and return None.
    """
    if event.get("pos_y") is not None:
        return event["pos_y"]
    info = styles.get(event.get("style", "")) or {}
    fontsize = info.get("fontsize")
    alignment = info.get("alignment")
    if fontsize is None or alignment is None:
        return None
    margin_v = event.get("margin_v") or 0
    if margin_v == 0:
        margin_v = info.get("margin_v") or 0
    if alignment == 2:
        return int(round(res_y - margin_v - fontsize / 2.0))
    if alignment == 8:
        return int(round(margin_v + fontsize / 2.0))
    if alignment == 5:
        return int(round(res_y / 2.0))
    return None


def build_attribution_lookup(reviews):
    lookup = {}
    for r in reviews or []:
        try:
            key = (norm(r.get("word", "")), round(float(r.get("start", 0.0)), 2))
        except (TypeError, ValueError):
            continue
        lookup.setdefault(key, r)
    return lookup


def attribution_decision(lookup, word, start):
    key = (norm(word), round(float(start), 2))
    if key in lookup:
        return lookup[key]
    for (w, s), r in lookup.items():
        if w == key[0] and abs(s - key[1]) <= 0.02:
            return r
    return None


def align_captions(caption_norms, transcript_norms, glossary):
    """Greedy in-order subsequence alignment.

    glossary: {transcript_norm: caption_norm} plus reverse entries.
    Returns (matches, glossary_uses, first_failure) where matches[i] is the
    transcript index for caption i (or None), glossary_uses is a list of
    {caption, transcript, glossary} for each override consumed, and
    first_failure is (caption_index, caption_word) for the first caption
    word that cannot be aligned, or None.
    """
    matches = []
    glossary_uses = []
    first_failure = None
    ti = 0
    n = len(transcript_norms)
    for ci, c in enumerate(caption_norms):
        found = None
        used_glossary = None
        j = ti
        while j < n:
            t = transcript_norms[j]
            if t == c:
                found = j
                break
            if glossary.get(t) == c or glossary.get(c) == t:
                found = j
                used_glossary = "%s->%s" % (t, c)
                break
            j += 1
        if found is None:
            matches.append(None)
            if first_failure is None:
                first_failure = (ci, c)
        else:
            matches.append(found)
            if used_glossary is not None:
                glossary_uses.append({"caption": c,
                                      "transcript": transcript_norms[found],
                                      "glossary": used_glossary})
            ti = found + 1
    return matches, glossary_uses, first_failure


def scan_forbidden_strings(paths):
    """Return [(path, matched_string)] for forbidden method strings found.

    The first two tokens are matched as case-insensitive substrings. The
    third token is matched only as a standalone token (non-letter on both
    sides): a plain substring search would false-positive on identifiers
    such as "transcript_norms", which merely contain those three letters.
    """
    hits = []
    token_re = re.compile(r"(?<![A-Za-z])" + re.escape(_FORBIDDEN_METHOD_STRINGS[2])
                          + r"(?![A-Za-z])", re.IGNORECASE)
    for path in paths:
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                text = f.read()
        except OSError:
            continue
        lowered = text.lower()
        for token in _FORBIDDEN_METHOD_STRINGS[:2]:
            if token.lower() in lowered:
                hits.append((path, token))
        if token_re.search(text):
            hits.append((path, _FORBIDDEN_METHOD_STRINGS[2]))
    return hits


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Validate captions.ass against the final transcript "
                    "(Stage 1, gate G7: CAP-1..CAP-7, VAD-1, HOOK-1)."
    )
    ap.add_argument("--captions", required=True, help="captions.ass")
    ap.add_argument("--transcript", required=True, help="final_transcript.json")
    ap.add_argument("--vad", required=True, help="vad_final.json")
    ap.add_argument("--attribution", required=True, help="attribution_review.json")
    ap.add_argument("--edl", required=True, help="edl.json")
    ap.add_argument("--workdir", required=True, help="work dir (gate stamps, log)")
    args = ap.parse_args(argv)

    checks = []

    def record(rule, passed, evidence):
        checks.append({"rule": rule, "pass": bool(passed), "evidence": evidence})

    try:
        transcript = load_json(args.transcript, "transcript")
        vad = load_json(args.vad, "VAD")
        attribution = load_json(args.attribution, "attribution review")
        edl = load_json(args.edl, "EDL")
    except (FileNotFoundError, json.JSONDecodeError, OSError) as e:
        print(json.dumps({"ok": False, "error": str(e), "checks": checks}))
        return 1
    try:
        header, styles, events, parse_problems = parse_ass(args.captions)
    except (FileNotFoundError, OSError) as e:
        print(json.dumps({"ok": False, "error": str(e), "checks": checks}))
        return 1

    words = transcript.get("words") or []
    speech = vad.get("speech") or []
    reviews = attribution.get("reviews") or []
    policy = edl.get("caption_policy") or {}
    glossary_raw = policy.get("glossary_overrides") or {}
    glossary = {norm(k): norm(v) for k, v in glossary_raw.items()}
    try:
        max_punch_ratio = float(policy.get("max_punch_ratio", 0.25))
    except (TypeError, ValueError):
        max_punch_ratio = 0.25

    # One norm per caption WORD (multi-word events split into words, so a
    # "DRIVE AN ATV" event is checked as DRIVE / AN / ATV, not as the
    # concatenated token "DRIVEANATV"). caption_word_events maps each word
    # back to its event index for CAP-4 timing.
    caption_words = []
    caption_word_events = []
    for i, e in enumerate(events):
        for w in e["text"].split():
            caption_words.append(norm(w))
            caption_word_events.append(i)
    transcript_norms = [norm(w.get("word", "")) for w in words]
    transcript_set = set(transcript_norms)

    # --- CAP-1: every caption word appears in final_transcript.json ----------
    missing = sorted({c for c in caption_words if c and c not in transcript_set})
    record("CAP-1", not missing,
           "all %d caption words found in transcript (%d words)"
           % (len(caption_words), len(words)) if not missing else
           "caption words not in transcript: %s" % missing)

    # --- CAP-2: in-order subsequence, glossary overrides only ---------------
    matches, glossary_uses, first_failure = align_captions(
        caption_words, transcript_norms, glossary)
    if first_failure is None:
        ev = ("caption words are an in-order subsequence of %d transcript words; "
              "glossary overrides used: %d %s"
              % (len(words), len(glossary_uses), glossary_uses))
        record("CAP-2", True, ev)
    else:
        ci, bad = first_failure
        ei = caption_word_events[ci] if ci < len(caption_word_events) else -1
        record("CAP-2", False,
               "caption word #%d %r (event #%d text %r) is not the next "
               "transcript word in order; glossary overrides used before "
               "failure: %s"
               % (ci, bad, ei,
                  events[ei]["text"] if 0 <= ei < len(events) else "",
                  glossary_uses))

    # --- CAP-3: >=50% VAD overlap, none 0% ----------------------------------
    bad_overlap = []
    for i, e in enumerate(events):
        frac = vad_overlap_fraction(e["start"], e["end"], speech)
        if frac < constants.VAD_MIN_OVERLAP:
            bad_overlap.append({"caption": i, "text": e["text"],
                                "start": e["start"], "end": e["end"],
                                "vad_overlap": round(frac, 3)})
    record("CAP-3", not bad_overlap,
           "all %d captions overlap VAD speech >= %.0f%%"
           % (len(events), constants.VAD_MIN_OVERLAP * 100) if not bad_overlap else
           "captions below %.0f%% VAD overlap: %s"
           % (constants.VAD_MIN_OVERLAP * 100, bad_overlap))

    # --- CAP-4: caption start within +-0.10s of its transcript word start ---
    # Each event is timed against its FIRST word's alignment: a multi-word
    # event that starts early fails even if a later word in it is on time.
    bad_timing = []
    event_first_word = {}
    for wi, ei in enumerate(caption_word_events):
        event_first_word.setdefault(ei, wi)
    for i, e in enumerate(events):
        wi = event_first_word.get(i)
        if wi is None:
            continue  # empty event; CAP-6 flags it
        ti = matches[wi] if wi < len(matches) else None
        if ti is None:
            continue  # already failed CAP-2; do not double-count
        w = words[ti]
        try:
            wstart = float(w["start"])
        except (KeyError, TypeError, ValueError):
            continue
        delta = e["start"] - wstart
        if abs(delta) > constants.CAPTION_TOL_S:
            bad_timing.append({"caption": i, "text": e["text"],
                               "caption_start": e["start"],
                               "word_start": wstart, "delta_s": round(delta, 3)})
    record("CAP-4", not bad_timing,
           "all %d aligned captions start within +-%.2fs of their transcript word"
           % (len([m for m in matches if m is not None]), constants.CAPTION_TOL_S)
           if not bad_timing else
           "captions outside +-%.2fs of transcript word start: %s"
           % (constants.CAPTION_TOL_S, bad_timing))

    # --- CAP-5: attribution completeness + drop enforcement ------------------
    segments = edl.get("segments") or []
    risky = [s for s in segments
             if str(s.get("game_speech_risk", "none")).lower() != "none"]
    lookup = build_attribution_lookup(reviews)
    unlisted = []
    dropped_shown = []
    for w in words:
        try:
            wstart = float(w["start"])
            wend = float(w["end"])
            wtext = str(w["word"])
        except (KeyError, TypeError, ValueError):
            continue
        in_risky = False
        for s in risky:
            try:
                if wstart < float(s["final_out"]) and wend > float(s["final_in"]):
                    in_risky = True
                    break
            except (KeyError, TypeError, ValueError):
                continue
        if not in_risky:
            continue
        review = attribution_decision(lookup, wtext, wstart)
        if review is None:
            unlisted.append({"word": wtext, "start": wstart, "end": wend})
            continue
        decision = str(review.get("decision", "")).lower()
        evidence = review.get("evidence")
        if decision not in ("keep", "drop"):
            unlisted.append({"word": wtext, "start": wstart, "end": wend,
                             "problem": "decision %r not keep/drop" % review.get("decision")})
        elif not (isinstance(evidence, str) and evidence.strip()):
            unlisted.append({"word": wtext, "start": wstart, "end": wend,
                             "problem": "missing evidence"})
        elif decision == "drop" and norm(wtext) in set(caption_words):
            dropped_shown.append({"word": wtext, "start": wstart})
    cap5_ok = not unlisted and not dropped_shown
    if cap5_ok:
        record("CAP-5", True,
               "all transcript words in %d game_speech_risk != none segment(s) "
               "have attribution entries (keep/drop + evidence); no dropped word "
               "appears in captions" % len(risky))
    else:
        record("CAP-5", False,
               "unlisted/invalid attribution words: %s; drop-marked words shown "
               "in captions: %s" % (unlisted, dropped_shown))

    # --- CAP-6: format --------------------------------------------------------
    fmt_problems = list(parse_problems)
    for i, e in enumerate(events):
        if e.get("malformed"):
            fmt_problems.append("caption #%d %r is a malformed Dialogue line "
                                "(fewer than 10 fields)" % (i, e["text"]))
        if e["text"] != e["text"].upper():
            fmt_problems.append("caption #%d %r is not all caps" % (i, e["text"]))
        if len(e["text"].split()) != 1:
            fmt_problems.append("caption #%d %r is not one word per event"
                                % (i, e["text"]))
    try:
        res_x = int(header.get("PlayResX", -1))
        res_y = int(header.get("PlayResY", -1))
    except ValueError:
        res_x = res_y = -1
    if (res_x, res_y) != (constants.W, constants.H):
        fmt_problems.append("PlayResX/Y is %sx%s, expected %dx%d"
                            % (res_x, res_y, constants.W, constants.H))
    ys = []
    for i, e in enumerate(events):
        y = caption_centre_y(e, styles, res_y)
        if y is None:
            fmt_problems.append("caption #%d %r has no readable centre y"
                                % (i, e["text"]))
        else:
            ys.append(y)
    if ys:
        lo = ((constants.CAPTION_Y_PCT_MIN - constants.CAPTION_Y_TOL_PCT)
              / 100.0 * constants.H)
        hi = ((constants.CAPTION_Y_PCT_MAX + constants.CAPTION_Y_TOL_PCT)
              / 100.0 * constants.H)
        if min(ys) < lo or max(ys) > hi:
            fmt_problems.append(
                "caption centre y range %d-%d outside %d-%d%% of 1920 "
                "(%d-%d, with +-%d%% tolerance)"
                % (min(ys), max(ys), constants.CAPTION_Y_PCT_MIN,
                   constants.CAPTION_Y_PCT_MAX, int(lo), int(hi),
                   constants.CAPTION_Y_TOL_PCT))
    punch_events = [e for e in events if e["style"].lower() == "punch"]
    if events:
        punch_ratio = len(punch_events) / len(events)
        if punch_ratio > max_punch_ratio + 1e-9:
            fmt_problems.append("punch words %d/%d (%.2f) exceed max_punch_ratio %.2f"
                                % (len(punch_events), len(events), punch_ratio,
                                   max_punch_ratio))
        for e in punch_events:
            for w in e["text"].split():
                if norm(w) not in transcript_set:
                    fmt_problems.append("punch word %r not in transcript" % w)
    record("CAP-6", not fmt_problems,
           "all caps, one word/event, PlayRes %dx%d, caption centre y %s, "
           "punch %d/%d (max ratio %.2f)"
           % (res_x, res_y, ("%d-%d" % (min(ys), max(ys))) if ys else "n/a",
              len(punch_events), len(events), max_punch_ratio)
           if not fmt_problems else "; ".join(fmt_problems))

    # --- CAP-7: single-input rule (same evidence as CAP-1/CAP-2) -------------
    allowed = transcript_set | set(glossary.values()) | set(glossary.keys())
    outside = sorted({c for c in caption_words if c and c not in allowed})
    record("CAP-7", not outside and first_failure is None,
           "every caption word traces to final_transcript.json "
           "(+ glossary_overrides only); no outside words"
           if not outside and first_failure is None else
           "words outside transcript + glossary: %s; CAP-2 failure: %s"
           % (outside, first_failure))

    # --- VAD-1: method is silero_vad; no forbidden method strings ------------
    vad_problems = []
    method = vad.get("method")
    if method != "silero_vad":
        vad_problems.append("vad_final.json method is %r, expected 'silero_vad'"
                            % (method,))
    scripts_dir = os.path.dirname(os.path.abspath(__file__))
    scan_paths = []
    try:
        for name in sorted(os.listdir(scripts_dir)):
            if name.endswith(".py"):
                scan_paths.append(os.path.join(scripts_dir, name))
    except OSError:
        pass
    log_path = os.path.join(args.workdir, "ffmpeg_commands.log")
    if os.path.exists(log_path):
        scan_paths.append(log_path)
        log_note = "ffmpeg_commands.log present and scanned"
    else:
        log_note = "ffmpeg_commands.log absent in workdir, skipped (if present)"
    hits = scan_forbidden_strings(scan_paths)
    for path, token in hits:
        vad_problems.append("forbidden speech-evidence string %r found in %s"
                            % (token, path))
    record("VAD-1", not vad_problems,
           "method=silero_vad; %d script(s) + log scanned, no forbidden strings; %s"
           % (len(scan_paths), log_note) if not vad_problems else
           "; ".join(vad_problems))

    # --- HOOK-1: hook text exists as a single non-empty string ---------------
    hook = edl.get("hook") or {}
    hook_text = hook.get("text") if isinstance(hook, dict) else None
    hook_ok = isinstance(hook_text, str) and bool(hook_text.strip())
    record("HOOK-1", hook_ok,
           "hook text present: %r" % (hook_text,) if hook_ok else
           "EDL hook.text is missing, empty, or not a single string: %r"
           % (hook_text,))

    ok = all(c["pass"] for c in checks)
    inputs = {}
    for label, path in (("captions.ass", args.captions),
                        ("final_transcript.json", args.transcript),
                        ("vad_final.json", args.vad),
                        ("attribution_review.json", args.attribution),
                        ("edl.json", args.edl)):
        try:
            inputs[label] = gateio.sha256_file(path)
        except OSError:
            pass
    failing = [c["rule"] for c in checks if not c["pass"]]
    fix_hint = ("Fix failing rule(s): %s" % ", ".join(failing)) if failing else ""
    try:
        gateio.stamp_gate(args.workdir, "G7", "pass" if ok else "fail",
                          {"checks": checks}, inputs, fix_hint)
    except (OSError, ValueError) as e:
        print(json.dumps({"ok": False, "error": "could not stamp G7: %s" % e,
                          "checks": checks}))
        return 1

    print(json.dumps({"ok": ok, "checks": checks}))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

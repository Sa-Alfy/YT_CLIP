#!/usr/bin/env python3
"""Validate a Stage 1 EDL against the JSON Schema and the cross-field rules.

Checks (design sections 2-4):
  G1  moment total == sum of criteria scores and >= 10; 7 criteria, each 0-2.
  G2  valid against references/edl.schema.json; final_in contiguous from 0;
      final_out - final_in == src_out - src_in per segment; CUT-5 (duration
      16-38 and == target.duration_s +/- 0.1); CUT-6 (all speed == 1.0);
      timeline_map regenerates identically (run build_timeline_map.py first).
  G3  beat roles in order hook, setup, escalation, climax, consequence,
      payoff; CUT-1 (every chain item covered, consequences after the central
      event in final order); CUT-3 (no 3+ equal-duration segments, none < 1.5s);
      CUT-4 (every source gap between segments has a skips[] entry with
      contains_chain_event false); LOOP-1 (tail.src_out == head.src_in +/- 0.05);
      every segment reason >= 15 chars; every referenced beat_id exists.
  G4  (layout, run by the layout script) FACE-1 (facecam upscale <= 2.5) and
      FACE-2 (panel height 35-40% of 1920, crops in bounds, facecam/gameplay
      crops do not overlap) are checked here too, since the EDL carries the
      layout; the layout script re-checks them against extracted frames.
  HOOK-1  hook text non-empty.

Stamps G1 (moment), G2 (schema + structural) and G3 (story/cuts) in the
workdir's gate_report.json with per-rule evidence and inputs_sha256
{"edl.json": sha256}. G1 is stamped here because the moment check lives in
this script and render_picture_lock.py requires a G1 stamp before render 1.

Usage:
    python3 validate_edl.py --edl edl.json --workdir work/
    python3 validate_edl.py --edl edl.json --workdir work/ --schema refs/edl.schema.json

Exit code 0 when every check passes, 1 otherwise. Prints a JSON result
{"ok": bool, "checks": [{rule, pass, evidence}]} to stdout.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import gateio  # noqa: E402
import constants  # noqa: E402

try:
    import jsonschema
except ImportError:  # pragma: no cover
    jsonschema = None

EXPECTED_BEAT_ROLES = ["hook", "setup", "escalation", "climax", "consequence", "payoff"]
MIN_S = constants.MIN_S
MAX_S = constants.MAX_S
MAX_S_STORY = constants.MAX_S_STORY
MIN_SEG_S = 1.5
LOOP_TOL_S = 0.05
DUR_TOL_S = 0.1
FACE_UPSCALE_MAX = 2.5
OUT_H = 1920


def _check(checks, rule, passed, evidence):
    checks.append({"rule": rule, "pass": bool(passed), "evidence": str(evidence)})


def _rects_overlap(a, b):
    """True when two {x,y,w,h} rects overlap with non-zero area."""
    return (a["x"] < b["x"] + b["w"] and b["x"] < a["x"] + a["w"]
            and a["y"] < b["y"] + b["h"] and b["y"] < a["y"] + a["h"])


def _covering_segment(segments, src_in, min_visible_s):
    """First segment whose kept range covers [src_in, src_in + min_visible_s]."""
    need_end = src_in + min_visible_s
    for seg in segments:
        if seg["src_in"] <= src_in + 1e-9 and seg["src_out"] >= need_end - 1e-9:
            return seg
    return None


def expected_timeline_map(edl):
    """Recompute the timeline map the way build_timeline_map.py does.

    Times are rounded to 6 decimals (same as build_timeline_map.py) so the
    comparison is not defeated by float noise like -56.800000000000004.
    """
    out = []
    for seg in edl.get("segments", []):
        out.append({
            "seg_id": seg["seg_id"],
            "src_in": round(seg["src_in"], 6),
            "src_out": round(seg["src_out"], 6),
            "final_in": round(seg["final_in"], 6),
            "final_out": round(seg["final_out"], 6),
            "offset": round(seg["final_in"] - seg["src_in"], 6),
        })
    return out


def run_checks(edl, schema_path):
    """Run every check. Returns (checks, g2_rules, g3_rules)."""
    checks = []
    g2_rules = ["G2.schema", "G2.final_contiguity", "G2.segment_duration_match",
                "CUT-5", "CUT-6", "G2.timeline_map"]
    g3_rules = ["G3.beat_order", "CUT-1", "CUT-3", "CUT-4", "LOOP-1",
                "G3.segment_reasons", "G3.beat_refs", "G1.moment_total",
                "FACE-1", "FACE-2", "HOOK-1"]

    # ---- G2: JSON Schema ----
    if jsonschema is None:
        _check(checks, "G2.schema", False,
               "jsonschema library is not installed; cannot validate against %s" % schema_path)
    elif not os.path.isfile(schema_path):
        _check(checks, "G2.schema", False,
               "schema file not found: %s (references agent has not written it yet); "
               "schema validation skipped" % schema_path)
    else:
        with open(schema_path, "r", encoding="utf-8") as f:
            schema = json.load(f)
        try:
            jsonschema.validate(edl, schema)
            _check(checks, "G2.schema", True,
                   "EDL validates against %s" % os.path.basename(schema_path))
        except jsonschema.ValidationError as exc:
            _check(checks, "G2.schema", False,
                   "schema violation at %s: %s" % (list(exc.absolute_path), exc.message))

    segments = edl.get("segments", [])
    segs_final = sorted(segments, key=lambda s: s["final_in"])

    # ---- G2: final contiguity from 0 ----
    problems = []
    expect = 0.0
    for seg in segs_final:
        if abs(seg["final_in"] - expect) > 1e-9:
            problems.append("%s final_in %.3f, expected %.3f (gap/overlap)"
                            % (seg["seg_id"], seg["final_in"], expect))
        expect = seg["final_out"]
    _check(checks, "G2.final_contiguity", not problems,
           "final timeline contiguous from 0 to %.3f" % expect if not problems
           else "; ".join(problems))

    # ---- G2: final duration == source duration per segment ----
    problems = []
    for seg in segments:
        final_dur = seg["final_out"] - seg["final_in"]
        src_dur = seg["src_out"] - seg["src_in"]
        if abs(final_dur - src_dur) > DUR_TOL_S:
            problems.append("%s final %.3f != source %.3f"
                            % (seg["seg_id"], final_dur, src_dur))
    _check(checks, "G2.segment_duration_match", not problems,
           "every segment final duration equals its source duration (+/- %.2fs)" % DUR_TOL_S
           if not problems else "; ".join(problems))

    # ---- CUT-5: duration 16-38, or 16-45 for story moments (MOMENT 13-14) ----
    # 2026-11-24: winners run 40-60s; the 38s cap cut story moments short.
    # Only 13-14 moments may exceed 38s.
    total = segs_final[-1]["final_out"] if segs_final else 0.0
    target_dur = edl.get("target", {}).get("duration_s")
    moment_total = (edl.get("moment", {}) or {}).get("total", 0) or 0
    max_s = MAX_S_STORY if moment_total >= 13 else MAX_S
    problems = []
    if not (MIN_S <= total <= max_s):
        problems.append("total duration %.3fs outside %.1f-%.1fs (moment %d/14)"
                        % (total, MIN_S, max_s, moment_total))
    if target_dur is None:
        problems.append("target.duration_s missing")
    elif abs(total - target_dur) > DUR_TOL_S:
        problems.append("total %.3fs != target.duration_s %.3fs (+/- %.2fs)"
                        % (total, target_dur, DUR_TOL_S))
    _check(checks, "CUT-5", not problems,
           "duration %.3fs, target.duration_s %s, within %.1f-%.1fs (moment %d/14)"
           % (total, target_dur, MIN_S, max_s, moment_total)
           if not problems else "; ".join(problems))

    # ---- CUT-6: all speed == 1.0 ----
    bad = [s["seg_id"] for s in segments if s.get("speed") != 1.0]
    _check(checks, "CUT-6", not bad,
           "all %d segments have speed 1.0" % len(segments) if not bad
           else "speed != 1.0 in segments: %s" % ", ".join(bad))

    # ---- REPLAY: payoff-replay segments (2026-11-24) ----
    # A replay re-plays the payoff src range as a normal speed-1.0 segment
    # with a render-time centre-crop zoom (1.2-1.5x). No slow-mo via speed:
    # that would break CUT-5/CUT-6/G2 duration matching.
    by_id = {s["seg_id"]: s for s in segments}
    problems = []
    for seg in segments:
        replay_of = seg.get("replay_of")
        if replay_of is None:
            if "zoom" in seg:
                problems.append("%s has zoom without replay_of" % seg["seg_id"])
            continue
        src = by_id.get(replay_of)
        if src is None:
            problems.append("%s replay_of %r: no such segment" % (seg["seg_id"], replay_of))
            continue
        if seg["seg_id"] == replay_of:
            problems.append("%s replays itself" % seg["seg_id"])
        if not (src["src_in"] - 1e-9 <= seg["src_in"] and seg["src_out"] <= src["src_out"] + 1e-9):
            problems.append("%s src [%.2f, %.2f] outside replayed segment %s [%.2f, %.2f]"
                            % (seg["seg_id"], seg["src_in"], seg["src_out"],
                               replay_of, src["src_in"], src["src_out"]))
        zoom = seg.get("zoom", 1.0)
        if not (constants.REPLAY_ZOOM_MIN <= zoom <= constants.REPLAY_ZOOM_MAX):
            problems.append("%s zoom %.2f outside %.1f-%.1f"
                            % (seg["seg_id"], zoom, constants.REPLAY_ZOOM_MIN,
                               constants.REPLAY_ZOOM_MAX))
    _check(checks, "REPLAY", not problems,
           "replay segments valid" if not problems else "; ".join(problems))

    # ---- G2: timeline_map regenerates identically ----
    expected = expected_timeline_map(edl)
    stored = edl.get("timeline_map")

    def _maps_equal(a, b):
        if len(a) != len(b):
            return False
        for x, y in zip(a, b):
            if set(x) != set(y):
                return False
            for key in x:
                vx, vy = x[key], y[key]
                if isinstance(vx, (int, float)) and isinstance(vy, (int, float)):
                    if abs(vx - vy) > 1e-6:
                        return False
                elif vx != vy:
                    return False
        return True

    if stored is None:
        _check(checks, "G2.timeline_map", False,
               "timeline_map is missing from the EDL; run build_timeline_map.py "
               "(timeline_map is computed, never hand-written)")
    elif _maps_equal(stored, expected):
        _check(checks, "G2.timeline_map", True,
               "timeline_map matches the recomputed map (%d segments)" % len(expected))
    else:
        diffs = []
        for a, b in zip(stored, expected):
            if a != b:
                diffs.append("%s stored %s != computed %s" % (a.get("seg_id"), a, b))
        if len(stored) != len(expected):
            diffs.append("stored has %d entries, computed %d" % (len(stored), len(expected)))
        _check(checks, "G2.timeline_map", False, "; ".join(diffs))

    # ---- G1: moment total ----
    moment = edl.get("moment", {})
    criteria = moment.get("criteria", [])
    total = moment.get("total")
    threshold = moment.get("threshold", 10)
    score_sum = sum(c.get("score", 0) for c in criteria)
    problems = []
    if len(criteria) != 7:
        problems.append("expected 7 criteria, found %d" % len(criteria))
    for c in criteria:
        if not isinstance(c.get("score"), int) or not 0 <= c["score"] <= 2:
            problems.append("criterion %s score %r is not an integer 0-2"
                            % (c.get("id"), c.get("score")))
    if total != score_sum:
        problems.append("total %s != sum of criteria scores %d" % (total, score_sum))
    if isinstance(total, int) and total < threshold:
        problems.append("total %d < threshold %d" % (total, threshold))
    _check(checks, "G1.moment_total", not problems,
           "total %s == sum %d, threshold %d, %d criteria" % (total, score_sum, threshold, len(criteria))
           if not problems else "; ".join(problems))

    # ---- G3: beat order ----
    beats = edl.get("beats", [])
    roles = [b.get("role") for b in beats]
    _check(checks, "G3.beat_order", roles == EXPECTED_BEAT_ROLES,
           "beat roles in order: %s" % roles if roles == EXPECTED_BEAT_ROLES
           else "beat roles %s, expected in order %s" % (roles, EXPECTED_BEAT_ROLES))

    # ---- CUT-1: chain coverage + consequences after central event ----
    chain = edl.get("chain", [])
    central_id = moment.get("central_event_id")
    problems = []
    positions = {}
    for item in chain:
        seg = _covering_segment(segments, item["src_in"], item["min_visible_s"])
        if seg is None:
            problems.append("chain %s (%s, need [%.2f, %.2f]) is not covered by any segment"
                            % (item["event_id"], item["label"], item["src_in"],
                               item["src_in"] + item["min_visible_s"]))
        else:
            # Position in final order: (final_in of covering segment, src_in).
            positions[item["event_id"]] = (seg["final_in"], item["src_in"], seg["seg_id"])
    central = next((i for i in chain if i["event_id"] == central_id), None)
    if central is None:
        problems.append("central_event_id %r does not match any chain event" % central_id)
    elif central_id in positions:
        for item in chain:
            if item.get("role") == "consequence" and item["event_id"] in positions:
                if positions[item["event_id"]] <= positions[central_id]:
                    problems.append("consequence %s (final pos %s) does not appear after "
                                    "central event %s (final pos %s)"
                                    % (item["event_id"], positions[item["event_id"]],
                                       central_id, positions[central_id]))
    _check(checks, "CUT-1", not problems,
           "all %d chain items covered; consequences after central event %s"
           % (len(chain), central_id) if not problems else "; ".join(problems))

    # ---- CUT-3: no 3+ equal-duration segments, none < 1.5s ----
    durations = {}
    for seg in segments:
        durations.setdefault(round(seg["final_out"] - seg["final_in"], 1), []).append(seg["seg_id"])
    problems = []
    for dur, ids in durations.items():
        if len(ids) >= 3:
            problems.append("%d segments share duration ~%.1fs: %s" % (len(ids), dur, ", ".join(ids)))
    short = [s["seg_id"] for s in segments
             if (s["final_out"] - s["final_in"]) < MIN_SEG_S - 1e-9]
    if short:
        problems.append("segments shorter than %.1fs: %s" % (MIN_SEG_S, ", ".join(short)))
    _check(checks, "CUT-3", not problems,
           "%d segments, no 3+ share a duration (+/- 0.1s), none under %.1fs"
           % (len(segments), MIN_SEG_S) if not problems else "; ".join(problems))

    # ---- CUT-4: every source gap between segments has a skips[] entry ----
    skips = edl.get("skips", [])
    segs_src = sorted(segments, key=lambda s: s["src_in"])
    problems = []
    for prev, nxt in zip(segs_src, segs_src[1:]):
        gap_start, gap_end = prev["src_out"], nxt["src_in"]
        if gap_end - gap_start <= 1e-9:
            continue
        covered = any(s["src_in"] <= gap_start + 1e-9 and s["src_out"] >= gap_end - 1e-9
                      and s.get("contains_chain_event") is False for s in skips)
        if not covered:
            problems.append("source gap %.2f-%.2f (between %s and %s) has no skips[] entry "
                            "with contains_chain_event false"
                            % (gap_start, gap_end, prev["seg_id"], nxt["seg_id"]))
    _check(checks, "CUT-4", not problems,
           "every source gap between segments is documented in skips[]" if not problems
           else "; ".join(problems))

    # ---- LOOP-1: tail.src_out == head.src_in +/- 0.05 ----
    loop = edl.get("loop", {})
    by_id = {s["seg_id"]: s for s in segments}
    head = by_id.get(loop.get("head_seg", ""))
    tail = by_id.get(loop.get("tail_seg", ""))
    if head is None or tail is None:
        _check(checks, "LOOP-1", False,
               "loop head_seg %r / tail_seg %r not found among segments"
               % (loop.get("head_seg"), loop.get("tail_seg")))
    else:
        diff = abs(tail["src_out"] - head["src_in"])
        _check(checks, "LOOP-1", diff <= LOOP_TOL_S,
               "tail %s src_out %.3f == head %s src_in %.3f (diff %.3f, tol %.2f)"
               % (tail["seg_id"], tail["src_out"], head["seg_id"], head["src_in"], diff, LOOP_TOL_S)
               if diff <= LOOP_TOL_S else
               "tail %s src_out %.3f != head %s src_in %.3f (diff %.3f > tol %.2f)"
               % (tail["seg_id"], tail["src_out"], head["seg_id"], head["src_in"], diff, LOOP_TOL_S))

    # ---- G3: segment reasons >= 15 chars ----
    short_reasons = [s["seg_id"] for s in segments if len(s.get("reason", "")) < 15]
    _check(checks, "G3.segment_reasons", not short_reasons,
           "every segment reason is >= 15 chars" if not short_reasons
           else "reasons shorter than 15 chars in segments: %s" % ", ".join(short_reasons))

    # ---- G3: every referenced beat_id exists ----
    beat_ids = {b["beat_id"] for b in beats}
    missing = sorted({s["beat_id"] for s in segments if s["beat_id"] not in beat_ids})
    _check(checks, "G3.beat_refs", not missing,
           "every segment beat_id exists in beats[]" if not missing
           else "unknown beat_id(s) referenced: %s" % ", ".join(missing))

    # ---- FACE-1: facecam upscale <= 2.5 ----
    layout = edl.get("layout", {})
    face = layout.get("facecam", {})
    src_crop = face.get("src_crop", {})
    dst = face.get("dst", {})
    problems = []
    if not src_crop or not dst:
        problems.append("layout.facecam src_crop/dst missing")
    else:
        upscale = dst["w"] / src_crop["w"] if src_crop["w"] else float("inf")
        if upscale > FACE_UPSCALE_MAX:
            problems.append("facecam upscale %.2f > %.1f (dst.w %s / src_crop.w %s)"
                            % (upscale, FACE_UPSCALE_MAX, dst["w"], src_crop["w"]))
        else:
            _check(checks, "FACE-1", True,
                   "facecam upscale %.2f <= %.1f" % (upscale, FACE_UPSCALE_MAX))
            problems = None
    if problems:
        _check(checks, "FACE-1", False, "; ".join(problems))

    # ---- FACE-2: panel height 35-40%, crops in bounds, crops do not overlap ----
    problems = []
    source = edl.get("source", {})
    src_w, src_h = source.get("width"), source.get("height")
    game = layout.get("gameplay", {})
    if face and dst:
        pct = dst["h"] / OUT_H * 100
        if not (35 <= pct <= 40):
            problems.append("facecam panel height %.1f%% of 1920, outside 35-40%%" % pct)
    for name, panel in (("facecam", face), ("gameplay", game)):
        crop = panel.get("src_crop") or {}
        if not crop:
            problems.append("layout.%s src_crop missing" % name)
            continue
        if src_w is not None and (crop["x"] + crop["w"] > src_w + 1e-9 or crop["x"] < 0):
            problems.append("%s src_crop x range [%.0f, %.0f] exceeds source width %s"
                            % (name, crop["x"], crop["x"] + crop["w"], src_w))
        if src_h is not None and (crop["y"] + crop["h"] > src_h + 1e-9 or crop["y"] < 0):
            problems.append("%s src_crop y range [%.0f, %.0f] exceeds source height %s"
                            % (name, crop["y"], crop["y"] + crop["h"], src_h))
    if face.get("src_crop") and game.get("src_crop"):
        if _rects_overlap(face["src_crop"], game["src_crop"]):
            problems.append("facecam and gameplay source crops overlap: %s vs %s"
                            % (face["src_crop"], game["src_crop"]))
    _check(checks, "FACE-2", not problems,
           "facecam panel %.1f%% of 1920, both crops in bounds, crops do not overlap"
           % (dst["h"] / OUT_H * 100) if not problems and dst else
           ("all FACE-2 conditions hold" if not problems else "; ".join(problems)))

    # ---- HOOK-1: hook text non-empty ----
    hook_text = (edl.get("hook", {}) or {}).get("text", "")
    _check(checks, "HOOK-1", bool(hook_text and hook_text.strip()),
           "hook text present: %r" % hook_text if hook_text.strip()
           else "hook.text is empty")

    # ---- HOOK-2: short hook + payoff-first opening (2026-11-24) ----
    # Winners lead with a 2-4 word on-screen hook in frame 1 and the payoff
    # visual, not a spoken setup. hook.text is now the SHORT hook (the full
    # YouTube title goes in hook.full_title). S1 must start on the payoff or
    # its immediate setup: head src_in within 15s before the central event.
    hook_words = hook_text.split() if hook_text.strip() else []
    _check(checks, "HOOK-2a", 2 <= len(hook_words) <= 4,
           "hook.text is %d words: %r (must be 2-4)" % (len(hook_words), hook_text))
    moment = edl.get("moment", {}) or {}
    central_id = moment.get("central_event_id")
    chain = edl.get("chain", []) or []
    central = next((c for c in chain if c.get("event_id") == central_id), None)
    segs_sorted = sorted(segments, key=lambda s: s["final_in"])
    if central is None or not segs_sorted:
        _check(checks, "HOOK-2b", False,
               "cannot check opening: central event or segments missing")
    else:
        head = segs_sorted[0]
        near_payoff = (central["src_in"] - 15.0 <= head["src_in"]
                       <= central["src_out"])
        # Continuation-loop hooks open mid-sentence at the loop point
        # (example scooter EDL: s1 starts where s5 ends, completing the
        # reaction) — that is also a payoff-adjacent opening.
        loop = edl.get("loop", {}) or {}
        tail = next((s for s in segments
                     if s["seg_id"] == loop.get("tail_seg")), None)
        loop_open = (tail is not None
                     and loop.get("type") == "continuation_loop"
                     and abs(head["src_in"] - tail["src_out"]) <= LOOP_TOL_S)
        ok = near_payoff or loop_open
        _check(checks, "HOOK-2b", ok,
               "head %s src_in %.2f vs central %s [%.2f, %.2f] (must start "
               "within 15s before the central event, or at the loop point)"
               % (head["seg_id"], head["src_in"], central_id,
                  central["src_in"], central["src_out"]) if not ok else
               "head %s opens on the payoff/setup (src_in %.2f, central %s "
               "[%.2f, %.2f])%s" % (head["seg_id"], head["src_in"], central_id,
                                    central["src_in"], central["src_out"],
                                    " — continuation-loop opening"
                                    if loop_open and not near_payoff else ""))

    return checks, g2_rules, g3_rules


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Validate a Stage 1 EDL against the JSON Schema and cross-field rules (G1/G2/G3).",
        epilog="Stamps G1 (moment), G2 (schema + structural) and G3 (story/cuts) into "
               "gate_report.json in --workdir, with inputs_sha256 {edl.json: sha256}. "
               "Exit 0 only when every check passes.",
    )
    default_schema = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                  "..", "references", "edl.schema.json")
    parser.add_argument("--edl", required=True, help="Path to edl.json.")
    parser.add_argument("--workdir", required=True,
                        help="Build output dir; gate_report.json is written/stamped here.")
    parser.add_argument("--schema", default=default_schema,
                        help="Path to edl.schema.json (default: ../references/edl.schema.json "
                             "relative to this script).")
    args = parser.parse_args(argv)

    if not os.path.isfile(args.edl):
        result = {"ok": False, "error": "EDL file not found: %s" % args.edl, "checks": []}
        print(json.dumps(result))
        return 1
    try:
        with open(args.edl, "r", encoding="utf-8") as f:
            edl = json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        result = {"ok": False, "error": "EDL is not valid JSON: %s" % exc, "checks": []}
        print(json.dumps(result))
        return 1

    checks, g2_rules, g3_rules = run_checks(edl, args.schema)
    ok = all(c["pass"] for c in checks)
    edl_sha = gateio.sha256_file(args.edl)
    inputs = {os.path.basename(args.edl): edl_sha}

    def _stamp_evidence(rules):
        return {c["rule"]: {"pass": c["pass"], "evidence": c["evidence"]}
                for c in checks if c["rule"] in rules}

    g1_ok = all(c["pass"] for c in checks if c["rule"] == "G1.moment_total")
    gateio.stamp_gate(
        args.workdir, "G1", "pass" if g1_ok else "fail",
        _stamp_evidence(["G1.moment_total"]), inputs,
        "" if g1_ok else "Moment does not qualify (total must equal the sum of the "
                        "7 criteria scores and be >= 10); blocked.")
    g2_ok = all(c["pass"] for c in checks if c["rule"] in g2_rules)
    g3_ok = all(c["pass"] for c in checks if c["rule"] in g3_rules)
    os.makedirs(args.workdir, exist_ok=True)
    gateio.stamp_gate(
        args.workdir, "G2", "pass" if g2_ok else "fail",
        _stamp_evidence(g2_rules), inputs,
        "" if g2_ok else "Fix the EDL: " + "; ".join(
            "%s: %s" % (c["rule"], c["evidence"]) for c in checks
            if c["rule"] in g2_rules and not c["pass"]))
    gateio.stamp_gate(
        args.workdir, "G3", "pass" if g3_ok else "fail",
        _stamp_evidence(g3_rules), inputs,
        "" if g3_ok else "Fix the EDL: " + "; ".join(
            "%s: %s" % (c["rule"], c["evidence"]) for c in checks
            if c["rule"] in g3_rules and not c["pass"]))

    result = {"ok": ok, "checks": checks}
    print(json.dumps(result))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Compute timeline_map for a Stage 1 EDL (design section 2).

timeline_map is COMPUTED, never hand-written: for each segment,
offset = final_in - src_in. This script writes timeline_map.json and updates
the EDL's timeline_map field in place. It verifies the final timeline is
contiguous from 0 before writing anything.

Usage:
    python3 build_timeline_map.py --edl edl.json --out timeline_map.json

Exit code 0 on success, 1 on failure. Prints a JSON result to stdout.
"""
import argparse
import json
import os
import sys


def compute_timeline_map(edl):
    """Return [{seg_id, src_in, src_out, final_in, final_out, offset}].

    Times are rounded to 6 decimals: raw float subtraction (e.g. 14.4 - 71.2)
    produces values like -56.800000000000004, which would never compare equal
    to a stored -56.8 and would pollute the EDL.
    """
    entries = []
    for seg in edl.get("segments", []):
        entries.append({
            "seg_id": seg["seg_id"],
            "src_in": round(seg["src_in"], 6),
            "src_out": round(seg["src_out"], 6),
            "final_in": round(seg["final_in"], 6),
            "final_out": round(seg["final_out"], 6),
            "offset": round(seg["final_in"] - seg["src_in"], 6),
        })
    return entries


def check_contiguity(edl):
    """Return a list of contiguity problems (empty when contiguous from 0)."""
    problems = []
    expect = 0.0
    for seg in sorted(edl.get("segments", []), key=lambda s: s["final_in"]):
        if abs(seg["final_in"] - expect) > 1e-9:
            problems.append("%s final_in %.3f, expected %.3f (gap/overlap)"
                            % (seg["seg_id"], seg["final_in"], expect))
        expect = seg["final_out"]
    if not edl.get("segments"):
        problems.append("EDL has no segments")
    return problems


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Compute timeline_map for an EDL (offset = final_in - src_in per segment).",
        epilog="Writes timeline_map.json and updates the EDL's timeline_map field "
               "in place. The map is computed, never hand-written.",
    )
    parser.add_argument("--edl", required=True, help="Path to edl.json (updated in place).")
    parser.add_argument("--out", default=None,
                        help="Path for timeline_map.json (default: timeline_map.json next to the EDL).")
    args = parser.parse_args(argv)

    if not os.path.isfile(args.edl):
        print(json.dumps({"ok": False, "error": "EDL file not found: %s" % args.edl}))
        return 1
    try:
        with open(args.edl, "r", encoding="utf-8") as f:
            edl = json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        print(json.dumps({"ok": False, "error": "EDL is not valid JSON: %s" % exc}))
        return 1

    problems = check_contiguity(edl)
    if problems:
        print(json.dumps({"ok": False, "error": "final timeline is not contiguous from 0",
                          "problems": problems}))
        return 1

    timeline_map = compute_timeline_map(edl)
    out_path = args.out or os.path.join(os.path.dirname(os.path.abspath(args.edl)),
                                        "timeline_map.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(timeline_map, f, indent=2, sort_keys=True)
        f.write("\n")
    edl["timeline_map"] = timeline_map
    with open(args.edl, "w", encoding="utf-8") as f:
        json.dump(edl, f, indent=2, sort_keys=True)
        f.write("\n")

    result = {"ok": True, "out": out_path, "edl": args.edl,
              "segments": len(timeline_map),
              "duration_s": timeline_map[-1]["final_out"] if timeline_map else 0.0,
              "timeline_map": timeline_map}
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())

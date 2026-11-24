#!/usr/bin/env python3
"""build_manifest.py — write build_manifest.json (S9).

REFUSES to write the manifest unless gates G0-G8 are all pass (via gateio).
On success writes build_manifest.json:
  {"files": [{"path": ..., "sha256": ...}], "gates": {"G0": "pass", ...}}

Required artifacts (all hashed, paths relative to workdir):
  source_manifest.json, planning_transcript.json, edl.json, gate_report.json,
  ffmpeg_commands.log, picture_lock.mp4, final_transcript.json, vad_final.json,
  dropped_words.json, attribution_review.json, captions.ass, short_final.mp4,
  frames/ (facecam + gameplay crop stills, if present).

Exit 0 on success, 1 on refusal (gates missing/failing or a required file
missing). Prints a JSON result to stdout.
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import constants  # noqa: E402
import gateio  # noqa: E402

REQUIRED_FILES = [
    "source_manifest.json",
    "planning_transcript.json",
    "edl.json",
    "gate_report.json",
    "ffmpeg_commands.log",
    "picture_lock.mp4",
    "final_transcript.json",
    "vad_final.json",
    "dropped_words.json",
    "attribution_review.json",
    "captions.ass",
    "short_final.mp4",
]


def find_named_file(workdir, name):
    """Resolve a gate-stamped input name to a file on disk, or None."""
    direct = os.path.join(workdir, name)
    if os.path.isfile(direct):
        return direct
    base = os.path.basename(name)
    for root, _dirs, files in os.walk(workdir):
        if base in files:
            return os.path.join(root, base)
    return None


def collect_input_hashes(workdir, edl_path):
    """Hash map covering every file the gates were stamped with, so
    stamps_ok can compare stored hashes against current files."""
    inputs = {}
    report = gateio.load_gate_report(workdir)
    for gate in constants.GATE_ORDER:
        entry = report.get("gates", {}).get(gate) or {}
        for name in (entry.get("inputs_sha256") or {}):
            if name in inputs:
                continue
            found = None
            if os.path.basename(edl_path) == os.path.basename(name):
                found = edl_path
            else:
                found = find_named_file(workdir, name)
            if found is not None and os.path.isfile(found):
                try:
                    inputs[name] = gateio.sha256_file(found)
                except OSError:
                    pass
    return inputs


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Write build_manifest.json — refuses unless gates "
                    "G0-G8 are all pass (Stage 1, S9)."
    )
    ap.add_argument("--workdir", required=True, help="work dir (build output dir)")
    ap.add_argument("--edl", required=True, help="edl.json")
    ap.add_argument("--out", default=None,
                    help="output manifest path (default: workdir/build_manifest.json)")
    args = ap.parse_args(argv)

    def fail(msg, **extra):
        result = {"ok": False, "error": msg}
        result.update(extra)
        print(json.dumps(result))
        return 1

    if not os.path.isdir(args.workdir):
        return fail("workdir not found: %s" % args.workdir)
    if not os.path.exists(args.edl):
        return fail("EDL not found: %s" % args.edl)

    # --- Gates G0-G8 must all be pass ------------------------------------------
    # A corrupt gate_report.json fails closed with a clean JSON error rather
    # than a traceback (gateio's contract only promises {"gates": {}} for a
    # *missing* file).
    try:
        inputs = collect_input_hashes(args.workdir, args.edl)
        ok, problems = gateio.stamps_ok(args.workdir, constants.GATE_ORDER, inputs)
        report = gateio.load_gate_report(args.workdir)
    except (ValueError, OSError) as e:
        return fail("refusing to write manifest: gate_report.json unreadable: %s" % e)
    statuses = {g: (report.get("gates", {}).get(g) or {}).get("status", "missing")
                for g in constants.GATE_ORDER}
    if not ok:
        return fail("refusing to write manifest: gates G0-G8 are not all pass",
                    gate_statuses=statuses, gate_problems=problems)

    # --- Collect required artifacts ---------------------------------------------
    missing = []
    files = []
    for name in REQUIRED_FILES:
        if name == "edl.json":
            path = args.edl
        else:
            path = os.path.join(args.workdir, name)
        if not os.path.isfile(path):
            missing.append(name)
            continue
        files.append({"path": name, "sha256": gateio.sha256_file(path)})

    frames_dir = os.path.join(args.workdir, "frames")
    if os.path.isdir(frames_dir):
        for root, _dirs, names in os.walk(frames_dir):
            for fname in sorted(names):
                fpath = os.path.join(root, fname)
                rel = os.path.relpath(fpath, args.workdir).replace(os.sep, "/")
                files.append({"path": rel, "sha256": gateio.sha256_file(fpath)})

    if missing:
        return fail("refusing to write manifest: required artifact(s) missing",
                    missing_files=missing, gate_statuses=statuses)

    out_path = args.out
    if out_path is None:
        out_path = os.path.join(args.workdir, "build_manifest.json")
    elif not os.path.isabs(out_path):
        out_path = os.path.join(args.workdir, out_path)

    manifest = {
        "files": files,
        "gates": {g: "pass" for g in constants.GATE_ORDER},
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)
        f.write("\n")

    print(json.dumps({"ok": True, "manifest": out_path,
                      "files": len(files), "gates": manifest["gates"]}))
    return 0


if __name__ == "__main__":
    sys.exit(main())

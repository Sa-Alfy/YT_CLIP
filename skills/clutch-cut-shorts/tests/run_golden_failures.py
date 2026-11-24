#!/usr/bin/env python3
"""Golden-failure tests for the Clutch Cut Shorts skill (Stage 1).

Runs the skill's validators against the two failed Shorts (CC-S02 v2, audited
2.5/5; CC-S03 v3, audited 2/5), reconstructed as fixtures from the audit
reports, and asserts the validators FAIL them for the right reasons.

Also runs positive controls: validate_edl.py must PASS the example EDL, and
probe_source.py must PASS a clean VOD and FAIL a compilation source.

Usage:
    python3 tests/run_golden_failures.py [--skill-dir PATH] [--skip-probe]

Exit 0 only if every expected failure is present, every must-pass rule passes
(where the validator reports it), and every positive control passes.
Extra failures a validator reports are listed as warnings, not hard failures,
except that a must-pass rule failing is a hard failure (wrong reason).

Expected failures are matched by substring on the validator's rule id
(case-insensitive), so the test does not depend on the validator's exact
check-id spelling.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

# --- expected results, per fixture README ---

EXPECT = {
    "cc-s02-v2": {
        "validate_edl": {
            "fail": ["CUT-1", "CUT-3", "CUT-4", "FACE-1", "LOOP-1"],
            "pass": ["CUT-5", "CUT-6", "FACE-2", "SCHEMA"],
        },
        "validate_captions": {
            # Verified 2026-11-20: CAP-6 fails — v2's events are multi-word
            # ("HE STOLE", "I FEEL BAD", "THIS SLOW ASS CAR") and CAP-6
            # requires one word per event. VAD-1 PASSES: the method is
            # silero_vad and the log records no loudness-based
            # speech-evidence command (an earlier draft of the log's NOTE
            # named the forbidden methods and tripped the substring scan —
            # fixed by rewording the note). Only HOOK-1 and VAD-1 pass.
            "fail": ["CAP-1", "CAP-2", "CAP-3", "CAP-4", "CAP-5", "CAP-6",
                     "CAP-7"],
            "pass": ["HOOK-1", "VAD-1"],
        },
    },
    "cc-s03-v3": {
        "validate_edl": {
            "fail": ["FACE-1", "FACE-2"],
            "pass": ["CUT-1", "CUT-3", "CUT-4", "CUT-5", "CUT-6", "LOOP-1", "SCHEMA"],
        },
        "validate_captions": {
            "fail": ["CAP-1", "CAP-2", "CAP-3", "CAP-4", "CAP-5", "CAP-6", "CAP-7", "VAD-1"],
            "pass": ["HOOK-1"],
        },
    },
}

# LOOP-1 for cc-s02-v2 is a documented script-strict failure: the audit
# confirmed the loop works visually (frame f061 matches f005), so the auditor
# overrides it. It is expected to fail at script level and is listed above.
# CAP-6 centre-y for cc-s03-v3 is a boundary case (65.1% vs 65% bound):
# the validator carries a +-1% tolerance (constants.CAPTION_Y_TOL_PCT),
# so it passes on position and CAP-6 fails only on the multi-word events.


def run(cmd, timeout=120):
    """Run a command, return (returncode, stdout, stderr)."""
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return p.returncode, p.stdout, p.stderr


def parse_json_output(stdout):
    """Parse validator JSON from stdout (whole output, else last {...} block)."""
    try:
        return json.loads(stdout)
    except json.JSONDecodeError:
        pass
    matches = re.findall(r"\{[^{]*\"ok\"\s*:\s*(?:true|false)[^}]*\}", stdout, re.S)
    if matches:
        try:
            return json.loads(matches[-1])
        except json.JSONDecodeError:
            pass
    # Fall back: find the largest {...} that parses and has a checks list.
    for m in reversed(re.findall(r"\{.*\}", stdout, re.S)):
        try:
            obj = json.loads(m)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and ("checks" in obj or "ok" in obj):
            return obj
    return None


def failed_rules(result):
    """Return the set of rule ids the validator reported as failed."""
    failed = set()
    if not isinstance(result, dict):
        return failed
    for c in result.get("checks", []) or []:
        rid = str(c.get("rule", c.get("id", "")))
        passed = c.get("pass", c.get("passed", c.get("ok", True)))
        if not passed and rid:
            failed.add(rid)
    for f in result.get("failures", []) or []:
        rid = f.get("rule", f.get("id", "")) if isinstance(f, dict) else str(f)
        if rid:
            failed.add(rid)
    return failed


def check_validator(name, cmd, expect_fail, expect_pass, problems, warnings):
    """Run one validator, compare its failed-rule set with expectations."""
    rc, out, err = run(cmd)
    result = parse_json_output(out)
    if result is None:
        problems.append(f"{name}: could not parse validator output (rc={rc}). stdout={out[:500]!r} stderr={err[:500]!r}")
        return
    failed = failed_rules(result)
    ok = result.get("ok", rc == 0)
    if ok:
        problems.append(f"{name}: validator reported overall PASS (ok=true) but the fixture must FAIL.")
    for rule in expect_fail:
        if not any(rule.lower() in f.lower() for f in failed):
            problems.append(f"{name}: expected failure {rule} NOT reported. Failed rules: {sorted(failed)}")
    for rule in expect_pass:
        hit = [f for f in failed if rule.lower() in f.lower()]
        if hit:
            problems.append(f"{name}: rule {rule} must PASS but failed as {hit} (wrong reason).")
    extra = {f for f in failed if not any(e.lower() in f.lower() for e in expect_fail)}
    for f in sorted(extra):
        warnings.append(f"{name}: extra failure reported (not in expected set): {f}")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--skill-dir", default=None, help="skill root (default: parent of tests/)")
    ap.add_argument("--skip-probe", action="store_true", help="skip probe_source.py tests")
    args = ap.parse_args()

    here = os.path.dirname(os.path.abspath(__file__))
    skill_dir = args.skill_dir or os.path.dirname(here)
    scripts = os.path.join(skill_dir, "scripts")
    fixtures = os.path.join(skill_dir, "tests", "fixtures")
    py = sys.executable

    problems = []
    warnings = []

    for fixture, validators in EXPECT.items():
        fdir = os.path.join(fixtures, fixture)
        if not os.path.isdir(fdir):
            problems.append(f"{fixture}: fixture dir missing: {fdir}")
            continue
        # Run in a temp copy so gate stamps (gate_report.json) do not pollute
        # the fixture dir.
        tmpdir = tempfile.mkdtemp(prefix=f"golden-{fixture}-")
        workdir = os.path.join(tmpdir, fixture)
        shutil.copytree(fdir, workdir)
        edl = os.path.join(workdir, "edl.json")
        caps = os.path.join(workdir, "captions.ass")
        transcript = os.path.join(workdir, "final_transcript.json")
        vad = os.path.join(workdir, "vad_final.json")
        attrib = os.path.join(workdir, "attribution_review.json")
        for vname, exp in validators.items():
            script = os.path.join(scripts, f"{vname}.py")
            if not os.path.isfile(script):
                problems.append(f"{fixture}/{vname}: script missing: {script}")
                continue
            if vname == "validate_edl":
                cmd = [py, script, "--edl", edl, "--workdir", workdir]
            else:
                cmd = [py, script, "--captions", caps, "--transcript", transcript,
                       "--vad", vad, "--attribution", attrib, "--edl", edl,
                       "--workdir", workdir]
            check_validator(f"{fixture}/{vname}", cmd, exp["fail"], exp["pass"],
                            problems, warnings)
        shutil.rmtree(tmpdir, ignore_errors=True)

    # Positive control: the example EDL must PASS validate_edl.
    example = os.path.join(skill_dir, "references", "example_edl_scooter.json")
    validate_edl = os.path.join(scripts, "validate_edl.py")
    if os.path.isfile(validate_edl) and os.path.isfile(example):
        with tempfile.TemporaryDirectory() as tmp:
            rc, out, err = run([py, validate_edl, "--edl", example, "--workdir", tmp])
            result = parse_json_output(out)
            if result is None or result.get("ok", rc == 0) is not True:
                problems.append(f"positive control: example EDL must PASS validate_edl (rc={rc}). out={out[:500]!r} err={err[:500]!r}")
            else:
                print("positive control: example EDL PASSES validate_edl")
    else:
        problems.append("positive control: validate_edl.py or example_edl_scooter.json missing")

    # Probe controls (ffprobe only, no encode): compilation must FAIL G0,
    # a clean 1080p file must PASS G0.
    if not args.skip_probe:
        probe = os.path.join(scripts, "probe_source.py")
        mk9 = "/home/hatch/workspace/youtube-clutch-cut/shorts/work/xqc-atv-cows/mk9-1080p-hls.mp4"
        v2mp4 = "/home/hatch/workspace/youtube-clutch-cut/shorts/speed-scooter-bust-v2-2026-11-20.mp4"
        if os.path.isfile(probe) and os.path.isfile(mk9):
            with tempfile.TemporaryDirectory() as tmp:
                rc, out, err = run([py, probe, "--local-file", mk9,
                                    "--vod-url", "https://www.youtube.com/watch?v=fhw_k890INs",
                                    "--source-type", "compilation",
                                    "--vod-offset-s", "0", "--workdir", tmp])
                result = parse_json_output(out)
                if result is None or result.get("ok", rc == 0) is not False:
                    problems.append(f"probe control: compilation source must FAIL G0 (rc={rc}). out={out[:500]!r}")
                else:
                    print("probe control: compilation source FAILS G0 (SRC-1) as expected")
        else:
            warnings.append("probe control: probe_source.py or mk9 file missing, skipped")
        if os.path.isfile(probe) and os.path.isfile(v2mp4):
            with tempfile.TemporaryDirectory() as tmp:
                rc, out, err = run([py, probe, "--local-file", v2mp4,
                                    "--vod-url", "https://www.youtube.com/watch?v=qSyo5d-dVzA",
                                    "--source-type", "youtube_full_vod",
                                    "--vod-offset-s", "0", "--workdir", tmp])
                result = parse_json_output(out)
                if result is None or result.get("ok", rc == 0) is not True:
                    problems.append(f"probe control: clean 1080p VOD must PASS G0 (rc={rc}). out={out[:500]!r}")
                else:
                    print("probe control: clean 1080p source PASSES G0 as expected")

    print()
    for w in warnings:
        print("WARNING: " + w)
    if problems:
        print()
        print(f"GOLDEN-FAILURE TESTS: {len(problems)} PROBLEM(S)")
        for p in problems:
            print("FAIL: " + p)
        return 1
    print("GOLDEN-FAILURE TESTS: all expected failures present, all must-pass rules pass, all positive controls pass.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

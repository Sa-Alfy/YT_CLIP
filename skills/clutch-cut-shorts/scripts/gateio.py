"""Shared gate-stamp helper for Stage 1 gates.

Every gate writes one entry to gate_report.json in the work directory:
{"gate": ..., "status": "pass" | "fail", "evidence": {...},
 "inputs_sha256": {filename: sha256, ...}, "fix_hint": "..."}.

Render scripts and the manifest builder check the stamps with stamps_ok()
before they run. Editing a file after a gate invalidates that gate and every
gate after it, because the stored input hashes no longer match.

Stdlib only. Import as `gateio` from the scripts directory.
"""

import hashlib
import json
import os

GATE_REPORT_FILENAME = "gate_report.json"


def sha256_file(path):
    """Return the hex sha256 digest of the file at path."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def gate_report_path(workdir):
    return os.path.join(workdir, GATE_REPORT_FILENAME)


def load_gate_report(workdir):
    """Return the gate report dict, or {"gates": {}} when the file is missing."""
    path = gate_report_path(workdir)
    if not os.path.exists(path):
        return {"gates": {}}
    with open(path, "r", encoding="utf-8") as handle:
        report = json.load(handle)
    if not isinstance(report, dict) or "gates" not in report:
        return {"gates": {}}
    return report


def save_gate_report(workdir, report):
    """Write the gate report dict to gate_report.json in workdir."""
    os.makedirs(workdir, exist_ok=True)
    path = gate_report_path(workdir)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, sort_keys=True)
        handle.write("\n")


def stamp_gate(workdir, gate, status, evidence, inputs_sha256, fix_hint):
    """Record one gate result in gate_report.json.

    status is "pass" or "fail". evidence is a dict of check results.
    inputs_sha256 maps filenames to the sha256 of each input file as it was
    when the gate ran. fix_hint is a string telling the builder what to fix.
    """
    if status not in ("pass", "fail"):
        raise ValueError("status must be 'pass' or 'fail', got %r" % (status,))
    report = load_gate_report(workdir)
    report["gates"][gate] = {
        "gate": gate,
        "status": status,
        "evidence": evidence,
        "inputs_sha256": inputs_sha256,
        "fix_hint": fix_hint,
    }
    save_gate_report(workdir, report)
    return report["gates"][gate]


def stamps_ok(workdir, required_gates, inputs_sha256):
    """Check that each required gate passed against the given input hashes.

    Returns (ok, problems). A gate fails the check when it is missing from
    gate_report.json, when its status is not "pass", or when any hash stored
    in its inputs_sha256 differs from the given dict (a stored key missing
    from the given dict counts as a mismatch). Keys in the given dict that
    the stored dict does not mention are ignored.
    """
    report = load_gate_report(workdir)
    gates = report.get("gates", {})
    problems = []
    for gate in required_gates:
        entry = gates.get(gate)
        if entry is None:
            problems.append("%s: no stamp in gate_report.json" % gate)
            continue
        if entry.get("status") != "pass":
            problems.append("%s: status is %r, not 'pass'" % (gate, entry.get("status")))
            continue
        stored = entry.get("inputs_sha256") or {}
        for filename, stored_hash in stored.items():
            current_hash = inputs_sha256.get(filename)
            if current_hash is None:
                problems.append(
                    "%s: input %s was stamped but is not in the given hashes"
                    % (gate, filename)
                )
            elif current_hash != stored_hash:
                problems.append(
                    "%s: input %s changed since the gate passed" % (gate, filename)
                )
    return (len(problems) == 0, problems)

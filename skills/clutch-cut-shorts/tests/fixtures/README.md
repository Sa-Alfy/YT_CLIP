# Golden-failure tests — `tests/fixtures/`

Two fixtures, one per failed Short. Each fixture reconstructs the failed
Short's edit decisions and caption timings from its independent audit report
only — no numbers are invented. Read the per-fixture README for the provenance
of every number.

- `cc-s02-v2/` — IShowSpeed grandma-scooter bust v2, audited 2.5/5
  (`AUDIT-v2.md`). Fails CUT-1 (death skipped), CUT-3 (equal-duration
  segments), CUT-4 (undocumented skips), FACE-1 (2.77x upscale), CAP-1/2/3/4/5/7
  (mistimed captions, caption in dead air, paraphrase, cop's line).
- `cc-s03-v3/` — xQc ATV into cows v3, audited 2/5 (`AUDIT-v3.md`). Fails
  FACE-1/FACE-2 (2.91x upscale, 31.7% panel), CAP-1/2/3/4/5/6/7 (captions
  copied from burned-in text and game dialogue, 1.4s early, 0.6s late, NPC
  line, multi-word events), VAD-1 (RMS used as speech verification), and
  TTS-1/PROV-1 at verify_final (TTS mixed in, no provenance). Its
  `captions.ass` is the REAL v3 file, copied unchanged.

## How to run

```bash
python3 tests/run_golden_failures.py [--skill-dir PATH] [--skip-probe]
```

Exit 0 only if every expected failure is present, every must-pass rule passes,
and the positive controls pass (example EDL passes `validate_edl.py`;
compilation source fails G0; clean 1080p source passes G0). Extra failures a
validator reports are listed as warnings. A must-pass rule failing is a hard
failure: it means the validator fails for the wrong reason.

## Rules for fixtures

- Every number traces to an audit report section (cited in the fixture README).
  Approximate word times (split from phrase timings) are marked probability 0.7.
- Fixtures are reconstructions: v2/v3 predated the skill, so they never had an
  EDL, a picture-lock render, or a gate report. The fixture EDL is the edit the
  build *effectively* made.
- CUT-2 is not asserted for either fixture: cut evidence cannot be checked
  without the planning transcript and source-timeline VAD, which neither build
  produced.
- The v2 LOOP-1 failure is script-strict (the audit confirmed the loop works
  visually); the auditor overrides it. The v3 CAP-6 centre-y value (65.1% vs
  the 65% bound) is a font-metrics rounding edge and is not asserted.

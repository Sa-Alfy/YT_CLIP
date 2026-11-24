# Clutch Cut Shorts — CHANGELOG

## v1.2 — 2026-11-24

- **Measurement (social-content-performance skill).** New `Measurement` section in SKILL.md:
  account's own data only (vidIQ / YouTube Studio), per-Short views / stayed-vs-swiped /
  subs / impressions at 24h and 72h vs the channel baseline (best 1,679 views, avg ~320,
  57.5% stayed), compare like with like, change one variable at a time, weekly review
  decides what continues. vidIQ free plan: analytics reads only — never spend credits
  on AI title/thumbnail generation (one AI title rewrite = 50 of 150 monthly credits).
- **Research corroboration trap.** A moment known only from a single write-up needs a
  second corroborating source before it is slated. Lesson from CC-E01 floppy (2026-11-24):
  one outlet's articles contradicted each other and the only real floppy 1v3 was the
  2024 match — the build blocked at S0/G0 rather than mislabelling it.
- **Non-English audio trap.** Captions must match the language actually spoken (Whisper
  in that language); the narrator may paraphrase/translate in English, but never put a
  translated quote in the streamer's mouth. Lesson from CC-S18 Ibai (2026-11-24): the
  tape says "el hombre más feliz del mundo" — the English rendering is a paraphrase,
  never a quote.
- **Broadcast format adaptation trap.** When a moment has no streamer facecam (broadcast /
  caster audio), build full-frame gameplay + caster audio + narrator, drop the facecam
  panel, record FACE-1/FACE-2 as N/A with the adaptation noted in `build_result.json`.
  Never fake a facecam crop.
- **Docs.** `docs/self-improvement-log.md` and `docs/top10-format.md` now ship with the
  skill in this repo (see `docs/`).

## v1.1 — 2026-11-24

- **HOOK-2:** `hook.text` is a 2–4 word short hook, rendered at 96px from t=0; full
  YouTube title moves to `hook.full_title`. S1 opens on the payoff, its immediate
  setup, or the loop point. Enforced by HOOK-2a/HOOK-2b in `validate_edl.py`.
- **Payoff replay:** optional EDL segment fields `replay_of` + `zoom` (1.2–1.5);
  same-speed repeat with render-time centre-crop zoom. Slow-mo via speed change
  stays impossible under CUT-5/CUT-6/G2.
- **Story duration:** 13–14 MOMENT builds may run to 45s (was a 38s hard cap);
  validator CUT-5 enforces conditionally; schema `target.max_s` is now enum [38, 45].
- **Caption reconciliation:** `transcribe_final.py` runs Whisper tiny + small and
  reconciles (small may only ADD words where tiny heard nothing; small-only words
  need p≥0.4 AND ≥50% VAD overlap). Conflicts resolve to tiny and are logged.
  Measured on Tim: pure tiny kept 40 words, reconciled kept 53.
- **Punch stopwords:** hook-derived punch excludes `constants.STOPWORDS_PUNCH`
  (THE/HE/A/OF/…). Verified on Lacy: 17 punch words incl. "THE" 5× → 12 pictureable.
- **Process rules:** volume-burst (3–5 Shorts on big nights), sequel rule, pinned
  comment + fast replies (Studio step — API scopes lack comments), weekly Top 10
  long-form, traps (curl-first fetch, `build_reel.py` over OOM-prone
  `render_picture_lock.py`, punch stopwords, short hook).
- **Honest limits:** naive tiny+small union failed in testing (95 words, 30 junk);
  highest-probability-wins failed (small confidently misheard "1998" as "1990");
  full zero-drop captions still need the Lacy manual curation pass.
- Golden-failure test suite passes (`tests/run_golden_failures.py`).

## v1.0 — 2026-11-19/20

- Initial skill: gates G0–G8, hard rules, EDL schema v1.0, two-render workflow
  (picture lock → final), VAD + Whisper transcription, independent audit,
  hybrid narrator format, MOMENT filter (7 criteria, /14).

# Fixture: CC-S02 v2 — IShowSpeed grandma-scooter bust (audited 2.5/5)

**Video:** `~/workspace/youtube-clutch-cut/shorts/speed-scooter-bust-v2-2026-11-20.mp4`
(32.043s, 1080x1920, md5 `e86e0dd1ea7f0d49af0f84f23bbe97be`)
**Audit:** `~/workspace/youtube-clutch-cut/shorts/work/speed-scooter/AUDIT-v2.md` (20 Nov 2026 ~03:10 Dhaka)

## What this fixture is

A reconstruction of v2's actual edit decisions and caption timings, built ONLY from
the audit's evidence. v2 predates the skill, so it never had an EDL, a picture-lock
render, or a gate report. The fixture EDL is the edit v2 *effectively* made,
expressed in the skill's EDL format so the validators can judge it.

## Provenance of every number

- Segment times and the cut map: AUDIT-v2 "CUT MAP" table (16 fragments of exactly
  2.0s; hard cuts at 2.0, 6.0, 10.0, 16.0, 22.0, 26.0, 30.0s; source jumps
  50s back, 12s, 30s, 10s, 30s, 58s, 158s back). Segment durations in the fixture
  EDL are the beat durations from that table: flash 2s, steal 4s, escape 4s,
  bust 6s, arrest 6s, respawn 4s, road 4s, loop 2s.
- Chain: AUDIT-v2 Complaint 2 — the death/BUSTED screen lives in the 30s source
  gap at src 5756–5784 and is never shown. Fixture chain e4 is that event
  (src 5756.0–5758.5, min_visible_s 2.5); no segment covers it.
- Facecam: AUDIT-v2 Complaint 4 — 390x243 crop upscaled 2.77x to 1080x672 (35%
  panel). Crop position (0,465) and gameplay crop (934x1080 at x=493) are from
  the v3 rebuild of the same source (`buildv3/build_short_v3.py`), which used the
  same source layout; v2's own build did not record rects.
- Caption times and words: AUDIT-v2 "CAPTION-BY-CAPTION TABLE" (20 events).
  `captions.ass` in this fixture is constructed from that table (Anton 96px,
  MarginV 692 gives centre y 1180, ~61% down, as the audit measured).
- Transcript words and times: AUDIT-v2 caption table "What Speed actually says
  (Whisper)" column. Words "know", "I", "see" (5.08–6.40) and "slow" (~21.0)
  are split from the audit's phrase timings and marked probability 0.7
  (approximate); all other words are verbatim from the audit at probability 0.9.
- VAD speech intervals: derived from the audit's speech/silence statements —
  silence 0–3.84 and 14.10–17.86; speech where the audit lists words.
  `method` is recorded as `silero_vad` because the fixture is written in the
  skill's format; v2 itself never ran VAD (its captions came from source-time
  words.json). The VAD-1 *method* check therefore passes for this fixture; the
  caption failures below are the real defects.
- `ffmpeg_commands.log`: reconstructed (marked as such in the file). v2 used no
  TTS, and no loudness-based speech-evidence command is recorded for the v2
  build (the audit's loudness envelope was the auditor's measurement, not the
  builder's verification). TTS-1 and VAD-1 are NOT v2 defects and this
  fixture must not fail them. (The log's NOTE deliberately does not name the
  forbidden methods: the VAD-1 scan is a substring scan, so naming them in a
  note would trip it.)

## Expected validator results (the golden-failure test)

`validate_edl.py` must FAIL with:
- CUT-1 — chain e4 (death/BUSTED, src 5756.0–5758.5) is covered by no segment.
- CUT-3 — four segments have equal duration 4.0s (s2, s3, s6, s7), and the build
  used 16 fragments of exactly 2.0s.
- CUT-4 — source gaps 5704–5734, 5740–5750 and 5756–5784 have no skips[] entry.
- FACE-1 — facecam upscale 2.77x exceeds 2.5.
- LOOP-1 — tail src_out 5690.0 != head src_in 5738.0. DOCUMENTED EXCEPTION: the
  audit confirmed the loop works visually (frame f061 matches f005), so this is
  a script-strict failure the auditor overrides; it is not a story defect.

`validate_captions.py` must FAIL with:
- CAP-1 — "NO!" is not in the transcript (he says "Don't fuck"); "DO NOT MOVE"
  is not in the transcript (Whisper hears only "Oh"); "THIS"/"SLOW"/"ASS"/"CAR"
  at 16.1–17.9 are not spoken there.
- CAP-2 — caption words are not an in-order subsequence of the transcript
  ("CAR" is captioned at 17.1–17.9 but spoken at 28.02, after "MY"/"GOSH").
- CAP-3 — "THIS SLOW ASS CAR" (16.1–17.9) sits in verified silence (14.10–17.86).
- CAP-4 — captions are 0.16–4.4s off their aligned words (verified deltas:
  "HEY, YOU" +0.56, "I FEEL BAD" +1.6, "NOW BRO" +0.6, "I FEEL BAD" +0.76,
  "BRO" +0.16, "SLOW" −4.4 — the last because the in-order alignment pairs
  the 16.6s "SLOW" caption with the "slow" spoken at 21.0).
- CAP-5 — "DO NOT MOVE" is the cop's game-audio line in a game_speech_risk
  segment (s4, risk npc) with no attribution_review.json entry
  (attribution_review.json here is empty).
- CAP-6 — most events are multi-word ("HE STOLE", "A GRANDMAS", "HEY, YOU",
  "I FEEL BAD", "DO NOT MOVE", "THIS SLOW ASS CAR", "I LOST ALL MY MONEY"),
  violating one-word-per-event. All-caps and centre y (1180, 61.5%) pass.
- CAP-7 — same contamination as CAP-1/CAP-2 (words not from the final transcript).
- VAD-1 — PASSES (method silero_vad; the log records no loudness-based
  speech-evidence command). HOOK-1 also PASSES.

Must NOT fail for this fixture: TTS-1, VAD-1, G1 (moment 13/14 qualifies),
CUT-5 (32.0s), CUT-6 (speed 1.0).

CUT-2 is NOT asserted for this fixture: cut evidence cannot be checked without
the planning transcript and source-timeline VAD, which v2 never produced.

# Fixture: CC-S03 v3 — xQc ATV into cows (audited 2/5)

**Video:** `~/workspace/youtube-clutch-cut/shorts/xqc-atv-cows-v3-2026-11-20.mp4`
(25.000s, 1080x1920, md5 `c7ba07f2aa0312e1f9a0134ba3ef7e40`)
**Audit:** `~/workspace/youtube-clutch-cut/shorts/work/xqc-atv-cows/AUDIT-v3.md` (20 Nov 2026 ~02:55 Dhaka)

## What this fixture is

A reconstruction of v3's actual edit decisions and caption timings, built ONLY from
the audit's evidence. v3 predates the skill, so it never had an EDL, a picture-lock
render, or a gate report. The fixture EDL is the edit v3 *effectively* made,
expressed in the skill's EDL format so the validators can judge it.

`captions.ass` in this fixture is the REAL v3 captions file, copied unchanged from
`~/workspace/youtube-clutch-cut/shorts/work/xqc-atv-cows/captions-v3.ass`.

## Provenance of every number

- Caption times and words: AUDIT-v3 "CAPTION-BY-CAPTION TABLE" (15 events) and
  `cues-v3.json` in the v3 work dir (same 15 events, same times).
- Transcript words and times: AUDIT-v3 "Evidence (Whisper-small word timings vs
  v3 caption times)". No speech 0–3.46s; "COWS?" 13.52–14.22, 14.72–15.42,
  15.60–15.70, 16.24–16.58; "SAY IT AGAIN!" 16.98–18.62; TTS words "can't" 4.16,
  "drive" 4.54, "straight" 5.28. The TTS words "XQC" (3.55–3.75), "EVEN"
  (4.25–4.45), "AN" (4.70–4.80), "ATV" (4.80–5.00) are placed inside the TTS
  line's span (3.20–5.89) and marked probability 0.7 (approximate); the CAP-4
  failures for those captions (0.23–0.31s early) do not depend on the
  approximations. The NPC line "Oh look, even more cows!" is NOT in the
  transcript: the audit's Whisper run heard only a single word ("COWS?")
  at 16.24–16.58 in that window.
- VAD speech intervals: TTS 3.20–5.89 (VAD detects the TTS as speech — it IS
  audible speech; its failure is TTS-1, not CAP-3), xQc 13.52–18.62 as above.
  `method` is recorded as `silero_vad` because the fixture is written in the
  skill's format; v3 itself verified captions with RMS, which is the VAD-1
  defect (see below).
- Facecam: AUDIT-v3 finding 4 — facecam crop ~371x209 upscaled 2.9x to 1080x608
  (31.7% panel). Crop rects are from the real v3 build script
  (`build_xqc_v3.py`: facecam crop=371:209:0:540, gameplay crop=889:844:515:124).
- Source: the MidnightKing compilation
  (https://www.youtube.com/watch?v=fhw_k890INs), so the fixture EDL declares
  `source_type: compilation`. That is a G0/SRC-1 failure by design; the audit's
  fix #1 is to get the clean Twitch VOD.
- `ffmpeg_commands.log`: reconstructed (marked as such in the file). It records
  the TTS file being mixed in (`tts-line-test.mp3`, present in the v3 work dir)
  and the RMS verification step (`rms_envelope.py`, per the v3 build note's
  claim that "RMS confirms 'COWS?' (12.1) lands on a loud speech burst").
  The exact TTS synthesis tool is not asserted — only the mixed-in file.

## Expected validator results (the golden-failure test)

`validate_edl.py` must FAIL with:
- FACE-1 — facecam upscale 2.91x exceeds 2.5.
- FACE-2 — facecam panel 608/1920 = 31.7%, below the 35–40% range.

It must PASS the cut checks (CUT-1, CUT-3, CUT-4, CUT-5, CUT-6, LOOP-1, G1):
the audit says v3's structure (flash, approach, crash, loop) is sound; its
failures are captions, TTS, and quality, not the cut structure. SRC-1 is a
probe_source/G0 check (source_type compilation), not a validate_edl check.

`validate_captions.py` must FAIL with:
- CAP-1 — "LOOK!"/"COWS?" at 1.5–3.0 are not in the transcript (no speech
  0–3.46); "EVEN"/"MORE" are not in the transcript (Whisper hears only "COWS?"
  at 16.24–16.58); "REALLY?" is not in the transcript (he says "COWS?").
- CAP-2 — caption words are not an in-order subsequence of the transcript
  ("REALLY?" for "COWS?"; "EVEN MORE COWS!" for a single "COWS?").
- CAP-3 — "LOOK!"/"COWS?" (1.5–3.0) and "COWS?"/"SAY IT" (12.10–13.05) sit
  in verified silence.
- CAP-4 — verified deltas from the in-order alignment: "COWS?" at 2.0 is
  12.72s early, "COWS?" at 2.5 is 13.1s early, "COWS?" at 12.10 is 4.14s
  early, "SAY IT" at 12.55 is 4.43s early. (The early captions steal the
  later words' alignments, so the deltas are larger than the audit's
  in-isolation figures — the sequence as a whole is mistimed.)
- CAP-5 — "EVEN MORE COWS!" is the game's NPC line in segment s3
  (game_speech_risk npc) with no attribution_review.json entry
  (attribution_review.json here is empty).
- CAP-6 — multi-word events ("CAN'T EVEN", "DRIVE AN ATV", "EVEN MORE COWS!",
  "SAY IT AGAIN!", "HE DROVE AN ATV INTO COWS") violate one-word-per-event.
  Centre y passes: the real v3 ASS (MarginV 620, 100px font) centres at
  ~65.1% of 1920, and the validator carries a ±1% tolerance
  (constants.CAPTION_Y_TOL_PCT) because the audit records the position as
  PASS — so CAP-6 fails only on the multi-word events, not on position.
- CAP-7 — same contamination as CAP-1/CAP-2 (words copied from burned-in text
  and the game subtitle, not from the final transcript).
- VAD-1 — `ffmpeg_commands.log` contains an RMS verification step
  (`rms_envelope.py`); the design forbids RMS as speech evidence.

`verify_final.py` (against the real v3 mp4, when run) must FAIL with:
- TTS-1 — the command log references the TTS file being mixed in.
- PROV-1 — v3 wrote no edl_sha256/captions_sha256 provenance into the mp4
  metadata (it predates the skill).

CAP-6 centre-y is a resolved boundary case, not an open one: the real v3 ASS
has MarginV 620 with a 100px font, which puts the caption centre at ~65.1% of
1920 by the usual formula — 0.1% past the 65% bound. The audit records the
position as PASS ("consistently centred at ~65% down"), so the validator
carries a ±1% tolerance on the centre-y band (constants.CAPTION_Y_TOL_PCT)
and passes v3 on position; CAP-6 fails only on the multi-word events.

CUT-2 is NOT asserted for this fixture: cut evidence cannot be checked without
the planning transcript and source-timeline VAD, which v3 never produced.

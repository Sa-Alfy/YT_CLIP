# Stage 1 Hard Rules

A violation of any single rule is an automatic FAIL. The auditor caps the score at 2 when it finds one.

"Script" means the check runs in code and writes evidence to JSON. "Auditor" means the independent auditor re-checks it.

| ID | Rule | Pass condition | Checked by |
|---|---|---|---|
| SRC-1 | Clean VOD only | source_type is twitch_vod or youtube_full_vod | script, auditor |
| SRC-2 | No foreign media | Every -i in ffmpeg_commands.log is in audio.allowed_inputs | script, auditor |
| TTS-1 | No TTS | Final file has exactly 1 audio stream. Command log and work dir contain none of: tts, espeak, piper, elevenlabs, edge-tts, say, bark. Final-audio md5 equals picture-lock audio md5, so nothing was added | script, auditor |
| CAP-1 | Caption source is the final audio | Every caption word is in final_transcript.json, produced by faster-whisper on picture-lock audio | script |
| CAP-2 | No invented words | Normalized caption words are an in-order subsequence of the transcript words. glossary_overrides are the only exceptions, each one logged | script |
| CAP-3 | No caption in silence | Each caption overlaps Silero VAD speech by at least 50 percent, and no caption has 0 percent | script, auditor |
| CAP-4 | Caption timing | Caption start is within plus or minus 0.10 s of its transcript word start. The auditor's own Whisper run matches within plus or minus 0.15 s | script, auditor |
| CAP-5 | Speaker attribution defaults to drop | Every transcript word in a segment with game_speech_risk other than none has an entry in attribution_review.json (keep or drop, plus evidence). Any unlisted word fails. Uncertain means drop | script for completeness, auditor spot-check |
| CAP-6 | Format | All caps, one word per event, PlayResX and PlayResY 1080 by 1920, caption centre y at 60 to 65 percent of 1920, punch words at most max_punch_ratio and each one exists in the transcript | script, auditor |
| CAP-7 | No OCR or subtitle ingestion | build_ass.py has a single input file (final_transcript.json) plus attribution_review.json | script |
| VAD-1 | Speech detection method | speech_evidence.method is silero_vad. No volumedetect, astats, or RMS appears in any verification script or command log | script, auditor |
| CUT-1 | Consequence shown | Every chain item is covered, so the kept range contains src_in to src_in plus min_visible_s, and every consequence item appears after the central event in final order | script |
| CUT-2 | No mid-word cuts | Every cut is silence (at least 0.08 s clear of VAD speech), word_boundary (inside a word gap with at least 0.03 s margin), scene_change, or loop_continuation | script |
| CUT-3 | No fixed-fragment pattern | No 3 or more segments have equal duration within 0.1 s. No segment is shorter than 1.5 s | script |
| CUT-4 | Skips documented | Every source gap between segments has a skips entry with contains_chain_event false | script |
| CUT-5 | Duration | Final duration is 16.0 to 38.0 s, and equals EDL duration_s within 0.1 s | script, auditor |
| CUT-6 | No speed changes | All segment speeds equal 1.0 | script |
| FACE-1 | Facecam upscale | dst width divided by src_crop width is at most 2.5 | script |
| FACE-2 | Layout | Facecam panel height is 35 to 40 percent of 1920. Facecam and gameplay crops do not overlap in the source. Output has no blurred bars, so no boxblur or gblur in the filtergraph | script, auditor |
| HOOK-1 | Persistent title | One title string, same text, visible from t equals 0 to the last frame | script, auditor |
| LOOP-1 | Loop | tail src_out equals head src_in within 0.05 s, or the auditor confirms the first and last frames and words connect | script, auditor |
| OUT-1 | Output spec | 1080 by 1920, fps equals 30, audio present | script, auditor |
| PROV-1 | Provenance | The mp4 metadata comment contains edl_sha256 and captions_sha256 matching the manifest. Both renders used the render scripts, as the log shows | script, auditor |

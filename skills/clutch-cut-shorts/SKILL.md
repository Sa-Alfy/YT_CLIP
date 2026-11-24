# Clutch Cut Shorts Production

## Purpose and scope

- You turn one moment that already passed the MOMENT filter into a validated YouTube Short for The Clutch Cut.
- You work in Stage 1 only, which means you write an EDL, enforce the hard rules, run VAD on the final audio, use a clean source, follow the two-render workflow, and pass gates G0 to G8.
- You do not do moment discovery, speaker identification, or facecam enhancement, since those belong to other stages.
- You build from a moment that is already approved, and you never go looking for a new moment yourself.

## Fixed format

- You build every Short at 1080 by 1920, 30 fps, with a final duration from 16.0 to 38.0 seconds — except story moments (MOMENT total 13–14), which may run to 45.0 seconds, because the payoff needs room (winners run 40–60s; the 38s cap cut story moments short).
- You keep the facecam panel height between 35 and 40 percent of 1920, and you place caption centres between 60 and 65 percent of 1920.
- You never upscale the facecam by more than 2.5 times.
- You loudnorm the audio to -14 LUFS and you never add audio after the picture lock.
- You read every constant from `scripts/constants.py` and you never change a constant to make a build pass.
- **Hook (HOOK-2, 2026-11-24):** `hook.text` is a SHORT hook, 2–4 words ("TASED 3 TIMES", "90 HOURS. 61%."), burned huge from t=0 — the full YouTube title goes in `hook.full_title`, not on screen. The first segment opens on the payoff visual or its immediate setup action (head `src_in` within 15s before the central event), never on a menu, a load screen, or narrator-only black. The title must name one thing you can picture in a glance.
- **Payoff replay (optional, 2026-11-24):** after the climax, one segment may re-play the 1–2s payoff as a normal speed-1.0 segment with `replay_of` (the segment it repeats) and `zoom` 1.2–1.5 (render-time centre-crop zoom). Never slow-mo via speed change — that breaks CUT-5/CUT-6/G2. The replay is the meme second shown twice, bigger.
- **Caption completion (2026-11-24):** captions cover EVERY spoken word. `transcribe_final.py` runs Whisper tiny + small and reconciles them (small may only ADD words where tiny heard nothing; disagreements are logged as conflicts with tiny kept — small confidently misheard "1998" as "1990" on Tim). A word is dropped at G7 only when no run attests it at p≥0.4 and it is not in the planning transcript. Measured on Tim: pure tiny kept 40 words, reconciled kept 53 — a real gain, but not zero drops. When drops remain, escalate to the Lacy curation pattern before dropping them: small chunked + padded-chunked runs, per-segment word choice against the planning transcript and audio-match anchors (Lacy reached 73 kept / 2 dropped that way). Never show a wrong word to fill a gap. Punch words never include stopwords (THE/HE/A/OF/…) — punch is for pictureable words only.

## Inputs

- You start from one `build_request.json` with `moment_id`, `streamer`, `vod_url`, `vod_window_hint` with `start_s` and `end_s`, `moment_filter` with `criteria` and `total`, `title_candidate`, and `output_dir`.
- You treat the `moment_filter` total as a claim you must verify against the EDL moment criteria before gate G1.

## Tools

- You fetch source video with `yt-dlp` and you inspect it with `ffprobe`.
- You cut and encode with `ffmpeg` using trim and atrim filtergraphs, never stream copy for cuts.
- You transcribe with faster-whisper with word timestamps — tiny AND small, reconciled by `scripts/transcribe_final.py` — and you detect speech with silero-vad.
- You never use volumedetect, astats, or RMS as evidence that speech is present.

## Workflow

- S0 Acquire the VOD window with padding and record the real offset, then run `scripts/probe_source.py` for gate G0.
- S1 Write a planning transcript in source timeline time and use it only to choose cuts.
- S2 Write story beats, the consequence chain, and the EDL, then run `scripts/validate_edl.py` and `scripts/build_timeline_map.py` for gates G1, G2, and G3.
- S3 Check layout and facecam crops and extract one still of each crop, then run the layout check for gate G4.
- S4 Render the picture lock with `scripts/render_picture_lock.py`, with final audio, no captions, and no title.
- S5 Check audio integrity against the EDL for gate G5.
- S6 Transcribe and run VAD on the picture lock audio with `scripts/vad.py` first, then `scripts/transcribe_final.py` (tiny + small, reconciled with `--vad` for VAD-attested small-only words — caption completion) for gate G6.
- S7 Build captions from the S6 output only with `scripts/build_ass.py`, review attribution, and run `scripts/validate_captions.py` for gate G7.
- S8 Render the final video with `scripts/render_final.py`, burning captions and title and copying the audio stream untouched.
- S9 Build the manifest with `scripts/build_manifest.py` and hand the result to the auditor.

## Scripts in this layer

- `scripts/constants.py` and `scripts/gateio.py` exist now and are the shared foundation for every later script.
- The other workflow scripts named above are the next build step, and each one must write its gate stamp through `scripts/gateio.py`.
- No render script may run unless the stamps for all prior gates are pass and their recorded input hashes still match the current files.

## The three timelines

- VOD time is the clock inside the original Twitch or YouTube VOD.
- Local-file time is the clock inside the downloaded VOD window file, and local t equals 0 at the start of that file.
- Final time is the clock inside the finished Short, starting at 0.
- You write the EDL only in local-file time, because yt-dlp download sections snap to keyframes and local t equals 0 is almost never the exact requested VOD time.
- You record `vod_offset_s` from the actual downloaded file, and VOD time equals local time plus that offset.
- You download the VOD window with 30 seconds of padding on each side so the moment is fully inside the file.

## EDL

- You write `edl.json` before any ffmpeg call, and no render starts without a validated EDL.
- You follow the schema in [references/edl.schema.json](references/edl.schema.json) and the worked example in [references/example_edl_scooter.json](references/example_edl_scooter.json).
- You never hand-write `timeline_map`, since `scripts/build_timeline_map.py` computes it from the segments.
- You keep every segment at speed 1.0, every cut justified in a reason of at least 15 characters, and every source gap between segments documented in `skips`.

## Hard rules

- You follow every rule in [references/hard_rules.md](references/hard_rules.md).
- A violation of any single hard rule is an automatic FAIL, and the auditor caps the score at 2 when it finds one.
- You never relax a threshold, edit a validator, or skip a check to make a build pass.

## Gates

- Each gate writes one entry to `gate_report.json` with gate, status, evidence, inputs_sha256, and fix_hint.
- Editing a file after a gate invalidates that gate and every gate after it.
- You get at most 3 attempts per gate, and after the third failure the outcome is blocked.

| Gate | Checks | On fail |
|---|---|---|
| G0 Source | SRC-1, ffprobe height at least 1080, readable fps, vod_offset_s recorded, file opens with audio | Re-fetch the VOD at source quality, or blocked with no clean VOD and never fall back to a compilation |
| G1 Moment | Seven criteria each scored 0 to 2, total equals the sum, total at least 10 | Blocked, since the moment does not qualify |
| G2 EDL schema | Valid against edl.schema.json, final_in contiguous from 0, final duration equals source duration per segment, CUT-5, CUT-6, timeline_map regenerates identically | Fix the EDL |
| G3 Story and cuts | Beat order hook, setup, escalation, climax, consequence, payoff, CUT-1 to CUT-4, LOOP-1, HOOK-2 (short hook + payoff-first opening), REPLAY (replay segments valid), every segment reason at least 15 characters, cut evidence checked against the planning transcript and VAD | Fix the EDL, and the error names the segment and the rule |
| G4 Layout and facecam | FACE-1, FACE-2, crops inside source bounds, one still of each crop saved with Laplacian sharpness logged | Choose a different VOD or crop, or blocked when the upscale is over 2.5 times |
| G5 Audio integrity | Duration equals EDL duration_s within 0.05 seconds, one audio stream, SRC-2, TTS-1, picture lock audio md5 recorded | Re-run render 1 |
| G6 Final transcript and VAD | faster-whisper tiny + small, reconciled, with word timestamps on the picture lock audio, Silero VAD on the same audio, both JSON files exist, VAD speech over 0 seconds, transcript has words | Fix and re-run, or blocked when VAD finds no speech |
| G7 Captions | Captions built from final_transcript.json only, low probability or low VAD overlap words dropped and logged, attribution_review.json complete, CAP-1 to CAP-7, VAD-1, HOOK-1 | Fix the captions file or the review file, and never hand-edit timestamps |
| G8 Final | OUT-1, CUT-5, FACE-2, audio md5 equals the picture lock audio md5, PROV-1, duration equals picture lock duration within 0.05 seconds | Re-run render 2, and when the md5 differs find out why before retrying |

## Self-improvement — what the best clip channels taught us (2026-11-24)

Research on the top GTA 6 / gaming clip channels (Speedy Boykins, Nexxo Recaps, DarkViperAU Clips, ClipForge, Kyro) found these edges. The ones marked DONE are already in this skill; the rest are process rules for the orchestrator.

- **Volume burst on big nights (DONE as a slate rule):** when a streamer is live or a huge moment drops, post 3–5 Shorts that day instead of 2 — you cannot predict the winner in advance (Speedy's 42x clip was #18 of 19 that night). The slate must always carry 1–2 spare 12+/14 moments for burst days.
- **Publish while the stream is still live:** the first compilation under 2h after stream start outperformed everything else. Moment research runs the same day the stream happens, not the next morning.
- **Sequel rule (DONE as a slate rule):** when a moment lands, always ask "is there a part 2?" and slate it the same/next day with a sequel title ("…THIS TIME").
- **One-glance pictureable title:** the title must name one thing you can see in a glance (monster, boat blade, gator). A moment whose title needs a paragraph is not a 13/14 moment.
- **Weekly Top 10 countdown long-form:** a narrated multi-streamer Top 10 every Sunday funnels Shorts into long-form watch hours and gives the channel a non-templated spine. Format spec: `~/workspace/youtube-clutch-cut/top10-format.md`. Multi-creator compilations did 25–32x vs below-usual for single-creator ones — this is also why every Short uses a different creator.
- **Pinned comment + fast replies (Studio step, not API):** our OAuth scopes are upload + readonly, so the API cannot pin comments. After each Short goes live, pin a question ("which moment should be #1?") and reply to the first comments within 60 minutes — comment velocity is a 2026 ranking signal.
- **What NOT to copy:** raw re-uploads with no commentary (YPP reused-content rejection), templated mass-produced look (inauthentic-content risk), misleading titles, sexual/bait content, copyrighted music in the VOD. The hybrid narrator is the YPP shield — it stays.

## Measurement — what the social-content-performance skill adds (2026-11-24)

The pipeline builds the videos; the social-content-performance skill decides what the numbers mean. The orchestrator applies it after every publish:

- **Account's own data only.** Measure with vidIQ (`vidiq_channel_videos`, `vidiq_video_stats`) or YouTube Studio. Web results, benchmarks and remembered figures do not count as evidence about this channel.
- **Per Short, at 24h and 72h after publish:** views, stayed-vs-swiped %, subs gained, impressions. Baseline as of 24 Nov 2026: best Short 1,679 views, average ~320, 57.5% stayed. A "hit" = clearly beats 1,679 views AND stayed% ≥ 60%.
- **Compare like with like.** Short vs Short at the same age, same format, same slot. One strong post is an example, not a formula — decide on the full week's set, not one video. Do not infer a cause from one correlation; call a reason "likely" until the data proves it.
- **Change one thing at a time.** Across posts, vary one variable — the format, the hook, or the posting time — so the results show what made the difference. The 3-a-day experiment week varies format in the 13:00 slot only; 09:00 and 19:00 stay GTA 6 core.
- **vidIQ free plan rule.** Analytics reads only. Never spend credits on AI title or thumbnail generation — one AI title rewrite costs 50 of the 150 monthly credits. Our pipeline already makes titles and thumbnails; vidIQ is the measuring tape, not the maker.
- **Weekly review.** Every Tuesday evening the orchestrator reviews the week's set and the winning format/type becomes the new default; losing formats are dropped. No outcome is promised in advance.

## Outputs

- You produce only `build_result.json` plus the artifact list below, and `build_result.json` holds status built or blocked, blocked_reason, and manifest.
- Your output directory must contain `source_manifest.json`, `planning_transcript.json`, `edl.json`, `gate_report.json`, `ffmpeg_commands.log`, `picture_lock.mp4`, `final_transcript.json`, `vad_final.json`, `dropped_words.json`, `attribution_review.json`, `captions.ass`, `short_final.mp4`, a `frames` folder with the facecam and gameplay crop stills, and `build_manifest.json`.
- `build_manifest.json` lists every file with its sha256, and the manifest builder refuses to write it unless G0 to G8 are all pass.
- You never self-score and you never write a narrative verification, since your only verification evidence is script output in `gate_report.json`.

## Subagent contracts

- The builder produces only `build_result.json` and the artifact list, and it reports blocked and stops when a gate cannot pass.
- Blocked is a legitimate success state, not a failure to hide.
- The auditor receives `short_final.mp4`, `edl.json`, `captions.ass`, `build_manifest.json`, `gate_report.json`, `final_transcript.json`, and the source file path, and it never receives the builder reasoning or any self-assessment.
- The auditor re-runs Whisper and Silero VAD on the audio of `short_final.mp4` itself and compares them to `captions.ass` for CAP-3 and CAP-4, and it never trusts the builder transcript.
- The auditor re-runs the script rule checks for SRC, TTS, CUT, FACE, HOOK, and OUT, and it visually inspects frames at every caption start, every segment boundary within 2 frames, every chain event, and the first and last frames.
- The auditor writes `audit_report.json` with rules, score out of 5, verdict PASS or FAIL, and fail reasons, and any rule failure gives FAIL with a score of 2 or less.
- The auditor reports and does not fix, and on FAIL the orchestrator sends the fail reasons back to the builder with at most 2 retries before escalating to the user.

## Failure handling

- You never fall back to a compilation, to TTS audio, or to skipping a gate when a build cannot pass.
- When a gate fails three times, when no clean VOD exists, when the moment scores under 10, or when VAD finds no speech, you report blocked with the reason and stop.
- You never edit a validator, relax a threshold, or call ffmpeg directly to bypass a gate stamp.

## Traps

- You guard the third timeline by downloading with 30 seconds of padding, recording `vod_offset_s` from the actual file, and writing the EDL only in local-file time.
- You remember that VAD does not identify the speaker, so an NPC line passes VAD and Whisper both, and the game_speech_risk flag, default drop, and auditor facecam check are mitigations only, not a fix.
- You keep `glossary_overrides` to proper nouns only, since it is a back door for contamination.
- You expect Whisper to hallucinate on silence and music, so you use the vad filter, condition_on_previous_text set to false, and the G6 and G7 probability thresholds, and you log every dropped word.
- You never change audio after Whisper runs, so render 2 copies the audio stream and loudnorm happens only in render 1.
- You treat the 2.5 times facecam upscale limit as a starting guess, and you log Laplacian sharpness on every build so it can be calibrated against VODs known to look sharper.
- You keep the gates honest with stamps in `gate_report.json`, the provenance tag in the mp4, and the command log, since a builder that calls ffmpeg directly would otherwise bypass them.
- You store per-streamer facecam and gameplay crops in `references/profiles/<streamer>.json` and verify them with an extracted frame on every build, since streamers change overlays.
- You treat a segment with streamer_speaking_expected true and no VAD speech as a G6 failure, since Twitch VODs have muted sections where copyrighted music was removed.
- You make frame-accurate cuts with trim and atrim plus setpts in a filtergraph and constant frame rate output, and you never use -ss with stream copy.
- You never join an intermediate labelled stream (for example `[stack]`) into another filter inside a large filtergraph: this ffmpeg build fails to parse it (`No such filter: ''` right after `fps`), even though the same pattern works in small graphs. Chain captions directly onto the concat output instead (lesson from the 2026-11-21 narrator test, `speed-scooter-narrator-test/build_video.py`).
- You verify the actual resolution of the source fragments before cropping: the Speed scooter fragments were 1280×720, not 1920×1080, so the reel must upscale first or the crops land wrong (same test).
- You never put a literal `%` in drawtext caption text without `expansion=none` on the filter: in this ffmpeg build a literal `%` renders the ENTIRE caption blank, and the `%%` escape does not work (found in CC-S05 QA, 2026-11-21 — the "1,000%" punchline vanished until `expansion=none` was added to every drawtext filter).
- You acquire Twitch VOD windows with curl, not yt-dlp `--download-sections`: the sections download fails on the CloudFront m3u8 through the proxy (CC-S05, 2026-11-21 — the VOD window came down fine with curl). Never retry a timed-out ffmpeg direct-HLS fetch approval — use the curl route instead (Lacy/Tim, 2026-11-24).
- You render the picture lock with the `build_reel.py` pattern (proven S12/S14/S15), not the old `render_picture_lock.py` graph: that graph scales the whole source over a long canvas and was OOM-killed twice on Lacy with no other ffmpeg running.
- You keep punch words pictureable: hook-derived punch excludes stopwords (constants.STOPWORDS_PUNCH) — "THE"/"HE" rendered as yellow punch on Lacy.
- You keep the hook short: `hook.text` is 2–4 words, `hook.full_title` is the YouTube title. A long hook.text fails HOOK-2a.
- You corroborate a moment before it is slated: a moment known only from a single write-up needs a second corroborating source (the VOD, a stats page, or a second outlet) before it goes on the slate. The floppy 1v3 lesson (2026-11-24): one outlet's Day 3 and Day 5 articles contradicted each other on Imperial's elimination, and the only real floppy 1v3 on Inferno was the 2024 match — slating from the single write-up would have meant building a mislabelled match.
- You caption the language actually spoken: when the audio is not English, run Whisper in that language and caption what was really said. The narrator may paraphrase or translate in English, but you never put a translated quote in the streamer's mouth as if they said it (Ibai lesson, 2026-11-24: the tape says "el hombre más feliz del mundo" — the English rendering is a paraphrase, never a quote).
- You adapt to broadcast moments honestly: when a moment has no streamer facecam (broadcast / caster audio), build full-frame gameplay with caster audio plus the narrator, drop the facecam panel entirely, and record FACE-1/FACE-2 as N/A with the format adaptation noted in `build_result.json`. You never fake a facecam crop to satisfy the layout checks.

## Available connectors

- Use `muse.connector_search` to find other connectors and `muse.skill_search` to find related skills.
- If nothing here fits, look on the web for a maintained API or MCP server and set it up with [Custom Connectors and MCPs](/opt/muse/skills/custom-connector-setup/SKILL.md), or build a custom connector.

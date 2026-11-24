# Clutch Cut pipeline — self-improvement log

## 2026-11-24 — competitor research + pipeline v1.1

Research: 12 GTA 6 / gaming clip channels (Speedy Boykins 2.8M, Nexxo Recaps 1.18M, DarkViperAU Clips 502K, ClipForge, Kyro, Asmongold Clips, GTA 6 CLUB, GTAVI Clips). Method: web search + opened channel/video pages, 19–24 Nov 2026.

### What the winners do that we didn't
1. Frame 1 = the payoff visual + a 2–4 word hook title (not a spoken setup).
2. Caption every word (word-by-word karaoke is the standard).
3. Volume bursts on big nights (Speedy's 42x clip was #18 of 19 that night).
4. Publish while the stream is still live (<2h).
5. Weekly multi-streamer Top 10 countdown long-form (25–32x vs below-usual single-creator).
6. Slow-mo replay + zoom punch-in on the payoff second.
7. Pinned comment question + replies within 60 min.

### Implemented (skill v1.1, scripts updated)
- **HOOK-2:** `hook.text` is now a 2–4 word short hook ("TASED 3 TIMES"), burned at 96px from t=0; full YouTube title in `hook.full_title`. S1 must open on the payoff, its immediate setup, or the loop point (continuation-loop opening). Validator-enforced.
- **Payoff replay:** optional EDL segment (`replay_of` + `zoom` 1.2–1.5, speed stays 1.0). Render filter proven on a Tim slice: 2.0s, 1080×1920, 30fps, no OOM.
- **Story duration:** 13–14 moments may run to 45s (was 38s hard cap). Validator-enforced; 12 and below still cap at 38s.
- **Caption reconciliation:** `transcribe_final.py` now runs tiny + small and reconciles (VAD-attested small-only additions; tiny wins conflicts, logged). `build_ass.py` excludes stopwords from hook-derived punch.
- **Process:** volume-burst rule (3–5 Shorts on big nights), sequel rule ("is there a part 2?"), pinned-comment step (Studio — API scope lacks comments), weekly Top 10 format (`top10-format.md`, first episode Sun 29 Nov 13:00).
- **Traps added:** curl-first VOD fetch (never retry a timed-out HLS approval), `build_reel.py` pattern mandated (old picture-lock graph OOM-killed twice).

### Measured on Tim's audio (picture_lock.mp4, same VAD/attribution/EDL)
| Pipeline | Kept | Dropped |
|---|---|---|
| Pure tiny | 40 | 10 |
| Pure small | 57 | 21 |
| Reconciled v3 (shipped) | 53 | 8 |
| Old curated build (4-run + manual) | 60 | 11 |

- Punch fix verified on Lacy: old punch 17 words incl. "THE" 5× + "HE"; new punch 12 pictureable words, zero stopwords.
- Validator tests: golden-failure suite passes; example EDL + short-hook Tim EDL pass fully; HOOK-2a correctly fails old long hooks; CUT-5 correctly allows 40s at 13/14 and fails it at 12/14; REPLAY passes valid, fails zoom 2.0 and out-of-range src.

### What did NOT work (honest)
- **Naive tiny+small union:** 95 words, 30 junk (hallucinations, mis-timed fragments) — kept 65 but dropped 30. Fixed by VAD attestation on small-only words.
- **Highest-probability-wins across models:** small confidently misheard "1998" as "1990" (p 0.95) while tiny heard it right. Fixed by tiny-wins-conflicts + conflict log.
- **"Caption everything" is not solved by automation alone:** reconciled 53 kept vs curated 60. The gap is closed only by the Lacy curation pattern (chunked small runs + planning transcript + audio anchors). That stays as the G6 escalation, not an automatic step.
- **Slow-mo via speed change is impossible under our gates** (CUT-5/CUT-6/G2 require speed 1.0 + duration match). The replay is a same-speed repeat with zoom instead — the meme second shown twice, bigger.

### NOT copied (deliberate)
- Raw re-uploads (YPP reused-content rejection), templated mass-produced look (inauthentic-content risk), misleading titles, sexual/bait content, copyrighted music, campaign-clipping money (different business from YPP).

### Next to test (on live builds, not assumed)
- Short hook + payoff-first S1 on the next build (Summit1g, Fri 19:00) — watch stayed-vs-swiped.
- Top 10 episode 1 (Sun 29 Nov) — watch long-form CTR from Shorts.
- Volume burst on the next big live night — compare per-clip views vs the 2/day baseline.
- Pinned comment + fast replies — watch comment velocity and sub conversion.

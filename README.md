# Viral Clip Pipeline (v1.2 — topics)

Finds viral moments on **any topic** on YouTube (and Twitch, when
API keys are present), cuts short vertical clips with a hook and
subtitles, and produces a review digest. Nothing is posted anywhere —
clips are built for human review and approval first.

GTA 6 is the default preset and the worked example below — the
pipeline was built for the GTA VI launch (2026-11-19) — but the
topic is now a flag, not a hardcoded subject:

```bash
./venv/bin/python run_pipeline.py --topic "Minecraft" --clips 3 --dry-run
./venv/bin/python run_pipeline.py --topic "street food" --clips 3
```

See [Topics](#topics) for how `--topic` works, and the limits that
still apply (Twitch needs a game name; moment selection is still the
fixed window — see [Moment selection](#moment-selection)).

## v1.2 topics (2026-11-19)

- **`--topic <name>`**: a preset name (`gta6`) or any new topic
  (`Minecraft`, `street food`). Search terms are derived from the
  name — the name plus `<name> gameplay` and `<name> walkthrough` —
  unless the topic defines explicit `search_terms`. Creators default
  to none (term search only) unless `--creators` is given, and the
  Twitch game defaults to the topic name. GTA 6 with no `--topic`
  behaves exactly as v1.1.
- **`--game <name>`** overrides the Twitch game for one run (empty
  string = skip Twitch). **`--creators "A,B"`** overrides the
  creator list for one run.
- **`topic:` / `presets:` in config.yaml**: the active topic and
  named presets. The `gta6` preset holds the exact v1 values
  (search terms, the 14 creators, the Twitch game, the exclude
  list). Old configs with top-level `search_terms` / `creators`
  still work — the topic resolver falls back to them.
- **Hooks and digests are topic-aware**: the hook strips the active
  topic name from the title, and the digest title and Twitch note
  name the topic.

## v1.1 fixes (2026-11-19, GPT review — approved by Shariar)

- **Run lock** (`run.lock`, fcntl): a second run refuses to start while
  one is in progress instead of trampling it. The lock is released
  automatically if the holder's process dies, so a stale lock cannot
  exist.
- **Per-run segment dirs** (`segments/<run-id>/`): cleanup removes only
  the current run's downloads. v1 wiped the whole `segments/` dir,
  which contradicted the segment-reuse claim and broke overlapping runs.
- **Subprocess timeouts** (config `timeouts:`): every yt-dlp / ffmpeg /
  ffprobe / transcription call has a timeout; on timeout the whole
  process group is killed and that clip fails — the run never hangs
  (v1 hung ~8 min on a stalled download with 0 bytes written).
- **Config propagation**: `run_pipeline.py` passes `--config` through to
  every `clip.py` call (v1's clip.py always loaded the default
  `config.yaml`).
- **Commentary actually works**: the v1 flag logic could never execute
  and the config values were never read, so v1 clips were straight
  re-uploads in practice. Now `--commentary <file>` wins, else
  `commentary.enabled` + `commentary.audio` from config; a missing
  file fails loudly before any download.
- **Hard verification**: a clip that fails the decode/dimensions/
  duration/audio check is DELETED and recorded as rejected — never
  kept as "ok". Subtitles are validated (non-empty, well-formed
  timestamps) and the transcription model cache is checked BEFORE the
  run starts; if subtitles are enabled but the model is not cached
  the run fails loudly instead of silently shipping hook-only clips.
  Re-run with `--no-subtitles` for an explicit hook-only choice.
- **Timestamps**: float windows are ROUNDED explicitly (yt-dlp
  `--download-sections` truncates to ints) and the ACTUAL window used
  is recorded in the clip's stats JSON. Segments are validated
  (size + ffprobe) before building; zero-byte and `.part` files are
  never treated as finished segments.
- **Twitch picks**: picks merge YouTube + Twitch candidates by
  views/hour (v1 picked YouTube-only even when Twitch clips were
  discovered). Twitch clips are already moments, so the whole clip
  is used.
- **Smaller fixes**: hook text file is per-clip (no shared `hook.txt`);
  ffmpeg filter paths are escaped; the transcribe-venv path is
  configurable (`paths.transcribe_venv_python`); dead
  `heatmap_peak()` removed from `discover.py`.

Known limitation: the faster-whisper model loads once per clip (one
`clip.py` process per clip). The tiny model loads in seconds, so this
is acceptable; a persistent transcription worker is a later
improvement. **Never run transcription concurrently with ffmpeg** —
RAM is ~600 MB free and both are heavy.

## The 5 stages

1. **Discover** — `discover.py` searches YouTube (no API key needed)
   for the active topic's search terms + a creator watchlist, ranks
   candidates by **views/hour** (views / hours since upload). Twitch
   top clips for the topic's game come from the Helix API when keys
   are configured; without keys Twitch is skipped and says so — no
   data is ever faked.
2. **Download the moment only** — `clip.py` uses yt-dlp
   `--download-sections` so a 45-second slice of a 12-hour VOD downloads
   ~45 seconds of fragments, never the whole stream. Full VODs would not
   fit this box (1.5 CPUs, ~600 MB free RAM) and are never downloaded.
   Downloads live in `segments/<run-id>/` and are validated (size +
   ffprobe) before building.
3. **Cut** — ffmpeg turns horizontal gameplay into a 1080x1920 vertical
   clip: gameplay centered over a blurred-background fill, hook text on
   the first 3 seconds, subtitles burned in from faster-whisper (tiny)
   **when the model weights are already cached locally**. The sandbox
   egress is throttled, so a missing model is NOT downloaded mid-run —
   the run fails loudly up front instead (see v1.1). To enable subtitles,
   pre-cache the tiny model once on a faster network (`faster-whisper`
   will download it on first use). H.264 + AAC, faststart. Every clip
   passes a hard verification gate (decode + dimensions + duration +
   audio); failures are deleted and recorded as rejected.
4. **Review digest** — `run_pipeline.py` writes `digest-YYYY-MM-DD.md`
   with the top moments (source links, views, views/hour, duration, why
   picked) and which ones became clips.
5. **Post (after approval)** — not implemented in v1. Destination not
   chosen yet. Posting is always a separate, human-approved step.

## Setup

```bash
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
# Subtitles use faster-whisper (tiny) from the shared transcribe venv:
#   ~/workspace/transcribe-venv  (already has faster-whisper 1.2.1)
# Twitch (optional): put client_id / client_secret in config.yaml
#   (free at https://dev.twitch.tv/console)
```

**If yt-dlp suddenly fails with `SSL: CERTIFICATE_VERIFY_FAILED:
self-signed certificate in chain` after a runtime restart**, the
sandbox egress proxy CA rotated — re-trust it with `./fix-ssl.sh`
(it appends the current proxy CA bundle to the venv's certifi bundle)
and re-run. The sandbox MITMs TLS, so yt-dlp needs the proxy CA in
certifi's bundle; verification stays enabled.

## Usage

```bash
# Full run: discover -> pick top 3 -> clip -> digest (default topic: GTA 6)
./venv/bin/python run_pipeline.py --clips 3

# Any topic: Minecraft (game — Twitch works, game name defaults to "Minecraft")
./venv/bin/python run_pipeline.py --topic "Minecraft" --clips 3 --dry-run

# Any topic: street food (non-game — YouTube-only, Twitch is skipped)
./venv/bin/python run_pipeline.py --topic "street food" --clips 3

# Discovery only (what would be clipped, no downloads)
./venv/bin/python run_pipeline.py --clips 3 --dry-run

# Discovery alone, for one topic
./venv/bin/python discover.py --topic "Minecraft"

# Verify a candidate window shows the content before committing to a
# full segment download (fast — one frame via HTTP range seek):
./venv/bin/python probe.py discovery/discovery-2026-11-19-0804.json 1800
# -> verify/probe-<video_id>-t1800.jpg  (look at it; if it is a loading
#    screen, menu or countdown, probe a later timestamp instead)

# One clip by hand
./venv/bin/python clip.py --url <youtube-url> --start 65 --end 110 \
    --hook "KAI CENAT LOSES IT" --out kai-cenat-01
```

## Topics

The topic decides what discovery searches for. Precedence:
**CLI flags > config `topic:` block > `gta6` preset** (the default).

- `--topic gta6` — the GTA 6 preset, exactly the v1 values: search
  terms `GTA 6 gameplay` / `GTA VI gameplay`, the 14 launch-day
  creators, Twitch game `Grand Theft Auto VI`, the DarkViperAU
  exclusion. A run with no `--topic` is identical to this.
- `--topic "Minecraft"` — an ad-hoc topic, no config edit needed.
  Search terms are derived: `Minecraft`, `Minecraft gameplay`,
  `Minecraft walkthrough`. No creators (term search only) unless
  `--creators` is passed. Twitch game defaults to `Minecraft`.
- `--topic "street food"` — a non-game topic. Terms are derived the
  same way (`street food`, `street food gameplay`, `street food
  walkthrough` — the gameplay/walkthrough terms return little for a
  non-game topic and are harmless). Twitch game defaults to
  `street food`, which finds no Twitch game, so Twitch is skipped
  gracefully — the run is YouTube-only.
- `--topic "Biryani" --creators "Food Insider,Mark Wiens"` — an
  ad-hoc topic with a creator list for one run.
- `--topic "Minecraft" --game ""` — force a YouTube-only run even
  though the topic has a Twitch game.

To make a topic permanent, add it to `config.yaml` — either as the
`topic:` block (the default when no `--topic` is passed) or as a
named entry under `presets:` (then `--topic <name>` loads it whole).
A topic block can set explicit `search_terms` (skips derivation),
`suffixes:` to replace the default `["gameplay", "walkthrough"]`
derivation, `extra_terms:` for additional literal terms, `creators:`,
`twitch_game:` (blank `""` = YouTube-only), `hook_strip:` (words
stripped from titles when building the hook), and
`exclude_video_ids:`.

**Unchanged limits:** Twitch discovery needs a game/category name —
a topic with no matching Twitch game is YouTube-only. Moment
selection is still the fixed window (see below); topics do not
change how a moment is picked, only what is searched for.

## Config (`config.yaml`)

- `topic:` / `presets:` — what discovery searches for (see Topics).
  `presets.gta6` holds the exact v1 values.
- `discovery.max_age_hours` (72) / `min_duration_s` (60) — candidate filters.
- `clips.length_s` (45), `width`/`height` (1080x1920), `fps` (30),
  `crf` (21) — output settings.
- `clips.whisper_language` (`en`) — transcription language, set
  explicitly (no auto-detect).
- `timeouts.*` — per-stage subprocess timeouts (download 1800s,
  transcribe 900s, ffmpeg 600s, ffprobe 120s, clip subprocess 3600s).
- `paths.transcribe_venv_python` — Python with faster-whisper
  installed (default `~/workspace/transcribe-venv/bin/python`).
- `topic.exclude_video_ids` — YouTube IDs to skip when picking
  clips. Add a video here after probing shows its window is not the
  content (e.g. a pre-launch countdown stream); the next-ranked
  candidate takes its slot.
- `twitch.client_id` / `client_secret` — leave blank to skip Twitch.
- `commentary.enabled` / `commentary.audio` — see below.

## Moment selection

For each picked video the clip window is chosen by, in order:

- **Method A — heatmap peak:** yt-dlp exposes YouTube's most-replayed
  data ("heatmap"); the clip starts at the highest peak.
- **Method B — fixed window:** 1800s in for long VODs (>1h), 60s in for
  shorter videos (10s in for short videos) when the video has no
  heatmap data.

The method used is recorded per clip in the digest.

**Why 1800s for long VODs (learned on launch day, 2026-11-19):** the
first minutes of a marathon livestream are loading screens, menus and
setup, not gameplay — the 60s window showed only the GTA VI loading
screen, and the 600s window the PS5 home screen / Rockstar logo.
Launch-day streams can sit in setup for 30+ minutes (one top-ranked
video was still a pre-launch countdown at 1800s and in the settings
menu at 4800s). **Verify before you trust a window:** `probe.py`
grabs a single frame at a timestamp via HTTP range seek (seconds, no
full download) — look at the frame, and only then commit to the
segment. If the probe shows a menu, loading screen or countdown, probe
later or add the video to `exclude_video_ids` in `config.yaml`.

Hook text is auto-shrunk to fit the 1080px width (drawtext never wraps,
so a long hook used to be clipped at the edges).

## Commentary / reaction track (the format that matters)

Clips ship with source audio only unless commentary is supplied.
Record a commentary audio file the same length as the clip, then
either set `commentary.enabled: true` and `commentary.audio: <path>`
in `config.yaml`, or pass `--commentary <file>` to `clip.py` (the
flag wins) — the source audio is ducked to 45% under it. If commentary
is requested but the file is missing, the run fails loudly before
any download. No synthetic voiceover is generated by the pipeline;
voice and language are Shariar's call.

## Copyright — read this

Clipping famous creators' gameplay and re-uploading it as-is risks
copyright strikes: the footage is the creator's, even though Rockstar
generally allows gameplay videos. This pipeline is built for the
**commentary/reaction format** — your voice and edit on top of the
moment, which is the model that survives — or for clips used with the
creator's permission. Straight re-uploads are not the intended use.
Do not use the output to impersonate a creator or pass their footage
off as your own.

## Rate limits & safety

- yt-dlp calls are spaced (`sleep_between_searches_s: 1`) with retries;
  if YouTube throttles, the run logs the failure, skips that search,
  and continues. Back off and re-run later if many searches fail.
- One run at a time: `run.lock` refuses a second concurrent run.
- Raw segment downloads are deleted after clips are built — only the
  current run's `segments/<run-id>/` dir is removed, never another
  run's files. Final MP4s + stats JSONs + the digest are kept.
  (SRTs are intermediates and go with the run dir; the stats JSON
  records whether subtitles were burned in.)
- **CA rotation lesson (2026-11-19):** the sandbox egress proxy CA
  rotated after a runtime restart (~08:22 UTC) and broke SSL mid-run.
  If yt-dlp suddenly fails with certificate errors, run
  `./fix-ssl.sh` first (it re-trusts the current proxy CA) — see
  Setup above.

## Tests

`tests/` has the v1.1 test suite (no downloads; pre-placed segments
and synthetic media only, except one small dry-run discovery):

```bash
./tests/gen_test_media.sh            # synthetic 12s segment + tones
./venv/bin/python tests/test_units.py
./venv/bin/python tests/test_topics.py
cd tests && ../venv/bin/python test_clip_e2e.py
./venv/bin/python tests/test_run_wiring.py
./venv/bin/python tests/test_lock.py
./venv/bin/python run_pipeline.py --config tests/config.test.yaml \
    --clips 2 --dry-run              # real discovery, minimal config
```

Test outputs land in `tests/out-*` and `tests/digest-*.md` (never in
the pipeline root); delete them after a run.

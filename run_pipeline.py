#!/usr/bin/env python3
"""Run the whole pipeline: discover -> pick moments -> clip -> digest.

Usage:
    python run_pipeline.py [--config config.yaml] [--clips 3] [--dry-run]
                           [--no-subtitles]

--dry-run only runs discovery and prints what it WOULD clip.
The digest is written to digest-YYYY-MM-DD.md in the pipeline root.

v1.1 hardening (GPT review, 2026-11-19):
- Run lock (run.lock, fcntl): a second run refuses to start while one
  is in progress instead of trampling it.
- Per-run segment dir segments/<run-id>/: cleanup removes ONLY the
  current run's downloads — v1 wiped the whole segments dir, which
  contradicted the segment-reuse claim and broke overlapping runs.
- --config is passed through to every clip.py subprocess (v1 always
  loaded the default config.yaml in clip.py).
- Picks merge YouTube AND Twitch candidates by views/hour (v1 picked
  YouTube-only even when Twitch clips were discovered).
- Transcription preflight runs BEFORE discovery: subtitles enabled
  with an uncached model fails the run loudly up front.
- clip.py subprocess calls have a timeout; a timeout fails that clip
  instead of hanging the run.
"""
import argparse
import fcntl
import json
import os
import shutil
import sys
from datetime import datetime, timezone

import yaml

import clip
import discover
import procutil


def load_config(path):
    with open(path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def acquire_lock(root, run_id):
    """Exclusive non-blocking lock for this run (run.lock, fcntl).

    Returns the open lock file handle (hold it for the whole run), or
    None when another run already holds the lock. A stale lock cannot
    exist: flock is released automatically when the holder's process
    exits, even if it crashed.
    """
    lock_path = os.path.join(root, "run.lock")
    fh = open(lock_path, "w")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        fh.close()
        print("ERROR: another pipeline run is already in progress "
              "(run.lock is held). Wait for it to finish, or if it "
              "crashed, check that no run_pipeline.py / clip.py process "
              "is still alive and delete run.lock.",
              file=sys.stderr)
        return None
    fh.write(f"pid={os.getpid()}\nrun_id={run_id}\n"
             f"started={datetime.now(timezone.utc).isoformat()}\n")
    fh.flush()
    return fh


def cleanup_run_segments(seg_base_dir, run_id):
    """Delete ONLY this run's segment dir (segments/<run-id>/).

    Never touches other runs' segments or the segments/ root itself.
    """
    run_dir = os.path.join(seg_base_dir, run_id)
    if os.path.isdir(run_dir):
        shutil.rmtree(run_dir)
        print(f"Cleaned this run's segment dir: {run_dir}")
    else:
        print(f"No segment dir to clean: {run_dir}")


def merge_picks(yt, tw, n):
    """Merge YouTube + Twitch candidates into one ranked pick list.

    Ranked by views/hour (None counts as 0). Twitch clips are already
    moments (Helix returns clips, not VODs), so they compete on the
    same signal. v1 picked YouTube-only even when Twitch clips were
    discovered.
    """
    merged = list(yt) + list(tw)
    merged.sort(key=lambda c: (c.get("views_per_hour") or 0), reverse=True)
    return merged[:n]


def pick_window(cand, length_s):
    """Moment window for one candidate. Returns (start, end, method).

    Twitch candidates are already clips: the window is the whole clip.
    YouTube: Method A — heatmap peak (most-replayed); Method B — fixed
    window when no heatmap exists. Long VODs (>1h, e.g. marathon
    livestreams) start at 1800s: the first ~30 minutes are loading
    screens, menus and intros (verified on the v1 run — the 60s window
    showed only the GTA VI loading screen, the 600s window the PS5 home
    screen / Rockstar logo; probe.py frames at 1800s confirmed real
    gameplay for the top picks). Shorter videos start at 60s (10s if
    the video is short). The method is recorded per clip in the digest.
    """
    dur = cand["duration_s"]
    if cand.get("platform") == "twitch":
        end = min(float(length_s), float(dur))
        return 0.0, end, ("twitch clip (whole clip; Helix clips are "
                          "already moments)")
    peak = (cand.get("heatmap_peak") or {})
    if peak.get("start_s") is not None:
        start = float(peak["start_s"])
        start = max(0.0, min(start, max(0.0, dur - length_s)))
        return start, start + length_s, (
            f"heatmap peak (most-replayed, value {peak.get('value')})")
    if dur > 3600:
        start = 1800.0
    elif dur > 120:
        start = 60.0
    else:
        start = 10.0
    start = min(start, max(0.0, dur - length_s))
    return start, start + length_s, (
        "fixed window (no heatmap data; 1800s for long VODs so the game "
        "is actually playing)")


def hook_from_title(title, uploader):
    """Short punchy hook for the first frames (<= ~30 chars)."""
    hook = (title or "").strip()
    # Take only the first pipe-separated segment ("NO PROMOTIONS" style
    # disclaimers after a | must never become the hook).
    hook = hook.split("|")[0]
    for junk in ("GTA 6", "GTA VI", "Grand Theft Auto VI", "GTA6"):
        hook = hook.replace(junk, "")
    hook = " ".join(hook.split()).strip(" -:|•·")
    if len(hook) > 30:
        hook = hook[:30].rsplit(" ", 1)[0]
    # Strip again after truncation — a cut can leave a dangling separator
    # (e.g. "WALKTHROUGH GAMEPLAY PART 1 -").
    hook = hook.strip(" -:|•·")
    if not hook:
        hook = f"{uploader.upper()} PLAYS GTA 6"
    return hook.upper()


def slug(name):
    return "".join(c if c.isalnum() else "-" for c in name.lower()).strip("-")[:40]


def main():
    ap = argparse.ArgumentParser(description="Run the GTA 6 clip pipeline.")
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--clips", type=int, default=3, help="How many clips to make.")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-subtitles", action="store_true",
                    help="Ship hook-only clips (explicit choice; skips the "
                         "transcription preflight).")
    args = ap.parse_args()

    try:
        cfg = load_config(args.config)
    except (OSError, ValueError) as exc:
        print(f"ERROR: cannot load config {args.config}: {exc}",
              file=sys.stderr)
        return 1
    root = os.path.dirname(os.path.abspath(args.config))
    os.chdir(root)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")

    # --- Run lock: refuse to start while another run is in progress ---
    lock_fh = acquire_lock(root, run_id)
    if lock_fh is None:
        return 2
    try:
        return run_locked(args, cfg, root, run_id)
    finally:
        lock_fh.close()  # releases the flock; run.lock keeps last-run info


def run_locked(args, cfg, root, run_id):
    timeouts = dict(clip.DEFAULT_TIMEOUTS)
    timeouts.update((cfg.get("timeouts") or {}))
    clip_timeout = timeouts.get("clip_subprocess_s", 3600)
    seg_base_dir = cfg["paths"]["segments"]
    seg_dir = os.path.join(seg_base_dir, run_id)
    os.makedirs(cfg["paths"]["discovery"], exist_ok=True)
    os.makedirs(cfg["paths"]["clips"], exist_ok=True)
    os.makedirs(seg_dir, exist_ok=True)
    print(f"Run id: {run_id} (segments in {seg_dir})")

    # --- Transcription preflight, BEFORE discovery ---
    # If subtitles are enabled but the model is not cached, fail the
    # run loudly now instead of building hook-only clips silently.
    if not args.no_subtitles and cfg["clips"].get("subtitles", True):
        try:
            transcribe_py = os.path.expanduser(
                (cfg.get("paths") or {}).get("transcribe_venv_python")
                or clip.DEFAULT_TRANSCRIBE_PY)
            clip.check_transcription_ready(cfg, transcribe_py)
        except SystemExit as exc:
            # check_transcription_ready already printed its message.
            return exc.code if isinstance(exc.code, int) else 2

    print("== Discovery ==")
    yt = discover.youtube_candidates(cfg)
    print(f"  {len(yt)} YouTube candidates")
    print("== Heatmap peaks (from full metadata) ==")
    for cand in yt[:5]:
        print(f"  {cand['video_id']}: peak={cand.get('heatmap_peak')}")
    tw = discover.twitch_candidates(cfg)
    print(f"  {len(tw)} Twitch clips")

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d-%H%M")
    disc_path = os.path.join(cfg["paths"]["discovery"], f"discovery-{stamp}.json")
    with open(disc_path, "w", encoding="utf-8") as fh:
        json.dump({"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                   "youtube": yt, "twitch": tw}, fh, ensure_ascii=False, indent=2)
    print(f"Wrote {disc_path}")

    # Exclude videos that probing showed are not gameplay in the window
    # (e.g. pre-launch countdown streams). Listed in config.yaml with
    # the reason; the next-ranked candidate takes the slot.
    excluded = set(cfg.get("exclude_video_ids") or [])
    yt = [c for c in yt if c.get("video_id") not in excluded]
    if excluded:
        print(f"  excluded {len(excluded)} video(s) per config: "
              f"{', '.join(sorted(excluded))}")
    picks = merge_picks(yt, tw, args.clips)
    print(f"\n== Picks ({len(picks)}; YouTube + Twitch merged by v/h) ==")
    for i, c in enumerate(picks, 1):
        print(f"{i}. [{c.get('platform', '?')}] {c['uploader']} — "
              f"{c['title'][:70]} "
              f"({c['views']:,} views, {c['views_per_hour'] or 0:,.0f} v/h)")

    if args.dry_run:
        print("\nDry run — no clips made.")
        cleanup_run_segments(seg_base_dir, run_id)
        return 0

    results = []
    length = cfg["clips"]["length_s"]
    for cand in picks:
        start, end, method = pick_window(cand, length)
        hook = hook_from_title(cand["title"], cand["uploader"])
        out = (f"{datetime.now(timezone.utc).strftime('%Y%m%d')}-"
               f"{slug(cand['uploader'])}-{slug(cand['title'])}")
        print(f"\n== Clip: [{cand.get('platform', '?')}] {cand['uploader']} "
              f"{start:.0f}-{end:.0f}s [{method}] ==")
        print(f"   hook: {hook!r}")
        cmd = [sys.executable, "clip.py", "--config", args.config,
               "--run-id", run_id,
               "--url", cand["url"],
               "--start", str(start), "--end", str(end),
               "--hook", hook, "--out", out]
        if args.no_subtitles:
            cmd.append("--no-subtitles")
        try:
            procutil.run(cmd, f"clip.py {out}", timeout=clip_timeout)
            stats_path = os.path.join(cfg["paths"]["clips"], f"{out}.json")
            with open(stats_path, encoding="utf-8") as fh:
                stats = json.load(fh)
            if stats.get("status") == "rejected":
                raise procutil.CommandError(
                    f"clip.py {out}", 1, "",
                    "; ".join(stats.get("reasons") or ["rejected"]))
            results.append({**cand, "clip_start": start, "clip_end": end,
                            "method": method, "hook": hook,
                            "clip_file": f"{out}.mp4",
                            "clip_stats": stats, "status": "ok"})
        except procutil.CommandTimeout as exc:
            print(f"  [!] clip timed out after {exc.timeout}s for "
                  f"{cand.get('video_id') or cand.get('clip_id')}: {exc}")
            results.append({**cand, "clip_start": start, "clip_end": end,
                            "method": method, "hook": hook, "clip_file": None,
                            "status": f"failed: timed out after "
                                      f"{exc.timeout}s"})
        except procutil.CommandError as exc:
            print(f"  [!] clip failed for "
                  f"{cand.get('video_id') or cand.get('clip_id')}: {exc}")
            results.append({**cand, "clip_start": start, "clip_end": end,
                            "method": method, "hook": hook, "clip_file": None,
                            "status": f"failed: {exc}"})

    # Clean up ONLY this run's segment dir (never a global wipe).
    cleanup_run_segments(seg_base_dir, run_id)

    digest_path = f"digest-{datetime.now(timezone.utc).strftime('%Y-%m-%d')}.md"
    write_digest(digest_path, yt, tw, results, cfg)
    print(f"Wrote {digest_path}")
    return 0


def write_digest(path, yt, tw, results, cfg):
    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    lines = [
        f"# GTA 6 clip pipeline — digest {date}",
        "",
        f"Generated {datetime.now(timezone.utc).isoformat(timespec='seconds')} UTC. "
        f"Ranked by views/hour (views / hours since upload).",
        "",
        "## Top moments found (YouTube)",
        "",
        "| # | Video | Views | Views/h | Dur | Why picked |",
        "|---|-------|-------|---------|-----|------------|",
    ]
    for i, c in enumerate(yt[:10], 1):
        lines.append(
            f"| {i} | [{c['title'][:70]}]({c['url']}) — {c['uploader']} "
            f"| {c['views']:,} | {c['views_per_hour']:,.0f} | {c['duration_s']}s "
            f"| #{i} by views/hour |")
    lines += ["", "## Clips made", ""]
    for r in results:
        lines.append(
            f"- **{r['clip_file'] or '(failed)'}** — "
            f"[{r.get('platform', '?')}] {r['uploader']}: "
            f"[{r['title']}]({r['url']})")
        lines.append(
            f"  - Source: {r['views']:,} views, "
            f"{r['views_per_hour'] or 0:,.0f} v/h, "
            f"{r['duration_s']}s. Window {r['clip_start']:.0f}–{r['clip_end']:.0f}s, "
            f"method: {r['method']}. Hook: “{r['hook']}”.")
        if r["status"] == "ok":
            s = r["clip_stats"]
            lines.append(
                f"  - Clip: {s['duration_s']}s, {s['width']}x{s['height']}, "
                f"{s['codec']}, {s['size_bytes']:,} bytes, decode clean: {s['decode_clean']}, "
                f"subtitles: {s.get('subtitles_burned_in')}, "
                f"commentary: {s.get('commentary')}.")
        else:
            lines.append(f"  - Status: {r['status']}")
    if tw:
        lines += ["", "## Top Twitch clips", ""]
        for i, c in enumerate(tw[:10], 1):
            lines.append(f"{i}. [{c['title']}]({c['url']}) — {c['uploader']}, "
                         f"{c['views']:,} views, "
                         f"{c['views_per_hour'] or 0:,.0f} v/h")
    else:
        lines += ["",
                  "## Twitch",
                  "",
                  "Skipped — no Twitch API keys in config.yaml. "
                  "Add client_id/client_secret to enable Helix top-clips discovery.",
                  ""]
    lines += ["",
              "## Notes",
              "",
              "- Format: commentary/reaction clips (source audio only in v1; "
              "commentary mix is a defined step — set commentary.enabled "
              "+ commentary.audio in config.yaml or pass --commentary to "
              "clip.py; the game audio is ducked to 45% under it). "
              "Straight re-uploads of creators' clips risk copyright strikes.",
              "- Posting destination not chosen yet — clips are NOT posted anywhere.",
              "- v1.1: run lock (run.lock), per-run segment dirs, subprocess "
              "timeouts, hard output verification (bad clips are rejected "
              "and deleted, never kept as ok).",
              ""]
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))


if __name__ == "__main__":
    raise SystemExit(main())

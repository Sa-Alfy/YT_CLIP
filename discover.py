#!/usr/bin/env python3
"""Discovery stage: find viral GTA 6 gameplay on YouTube (no API key needed).

Sources
- YouTube: yt-dlp `ytsearch` over the configured search terms plus a
  per-creator search over the watchlist. Candidates are ranked by
  views/hour (views / hours since upload).
- Twitch: Helix API top clips for the GTA VI category — ONLY when
  client_id/client_secret are set in config.yaml. Without keys the
  Twitch stage is skipped and says so; no data is ever faked.

Outputs (into <pipeline>/discovery/):
- discovery-YYYY-MM-DD-HHMM.json — ranked candidates with full metadata
- printed table for the human running the pipeline

Usage:
    python discover.py [--config config.yaml] [--out discovery]
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone

import yaml

try:
    import yt_dlp
except ImportError:
    print("ERROR: yt-dlp is not installed. Run: ./venv/bin/pip install -r requirements.txt",
          file=sys.stderr)
    sys.exit(1)


def load_config(path):
    with open(path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def age_hours(entry):
    """Hours since upload, from yt-dlp 'timestamp' (unix) when present."""
    ts = entry.get("timestamp")
    if ts:
        return max((time.time() - ts) / 3600.0, 0.1)
    up = entry.get("upload_date")  # YYYYMMDD
    if up:
        try:
            dt = datetime.strptime(up, "%Y%m%d").replace(tzinfo=timezone.utc)
            return max((datetime.now(timezone.utc) - dt).total_seconds() / 3600.0, 0.1)
        except ValueError:
            pass
    return None


def search_youtube(ydl, query, n):
    """One ytsearch call; returns a list of flat entry dicts."""
    info = ydl.extract_info(f"ytsearch{n}:{query}", download=False)
    return (info or {}).get("entries") or []


def youtube_candidates(cfg):
    """Collect + rank YouTube candidates by views/hour.

    Flat search entries lack upload timestamps, so after dedupe we fetch
    full metadata (one small request per video) for the top candidates
    by raw views, then rank the survivors by views/hour. The same full
    fetch also carries the most-replayed heatmap, which is stored on the
    candidate for moment selection.
    """
    disc = cfg["discovery"]
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": True,
        "skip_download": True,
        "socket_timeout": 30,
        "retries": 3,
    }
    queries = [(t, disc["search_results"]) for t in cfg["search_terms"]]
    for creator in cfg["creators"]:
        queries.append((f"GTA 6 {creator}", disc["creator_results"]))

    seen = {}
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        for query, n in queries:
            try:
                entries = search_youtube(ydl, query, n)
            except Exception as exc:  # noqa: BLE001 - one bad search must not kill the run
                print(f"  [!] search failed for {query!r}: {exc}")
                continue
            for e in entries:
                if not e or not e.get("id"):
                    continue
                seen.setdefault(e["id"], e)
            time.sleep(disc.get("sleep_between_searches_s", 1.0))

    # Pre-filter on fields the flat entries DO carry, then full-fetch the best.
    pool = [e for e in seen.values()
            if (e.get("duration") or 0) >= disc["min_duration_s"]]
    pool.sort(key=lambda e: e.get("view_count") or 0, reverse=True)
    pool = pool[: disc.get("full_fetch_limit", 40)]

    full_opts = {"quiet": True, "no_warnings": True, "skip_download": True,
                 "socket_timeout": 30, "retries": 2}
    now = datetime.now(timezone.utc)
    ranked = []
    with yt_dlp.YoutubeDL(full_opts) as ydl:
        for e in pool:
            vid = e["id"]
            try:
                info = ydl.extract_info(
                    f"https://www.youtube.com/watch?v={vid}", download=False)
            except Exception as exc:  # noqa: BLE001
                print(f"  [!] metadata failed for {vid}: {exc}")
                continue
            views = info.get("view_count") or 0
            dur = info.get("duration") or 0
            hours = age_hours(info)
            if hours is None or hours > disc["max_age_hours"]:
                continue
            heat = (info or {}).get("heatmap") or []
            peak = None
            if heat:
                p = max(heat, key=lambda h: h.get("value", 0))
                peak = {"start_s": p.get("start_time"), "end_s": p.get("end_time"),
                        "value": p.get("value")}
            ranked.append({
                "platform": "youtube",
                "video_id": vid,
                "title": info.get("title") or "",
                "uploader": info.get("uploader") or info.get("channel") or "",
                "url": f"https://www.youtube.com/watch?v={vid}",
                "views": views,
                "duration_s": dur,
                "upload_date": info.get("upload_date") or "",
                "age_hours": round(hours, 2),
                "views_per_hour": round(views / hours, 1),
                "heatmap_peak": peak,
                "discovered_at": now.isoformat(timespec="seconds"),
            })
            time.sleep(0.3)
    ranked.sort(key=lambda c: c["views_per_hour"], reverse=True)
    return ranked


def twitch_candidates(cfg):
    """Top Twitch clips for the GTA VI category (Helix). Skips without keys."""
    tw = cfg.get("twitch") or {}
    cid = (tw.get("client_id") or "").strip()
    secret = (tw.get("client_secret") or "").strip()
    if not cid or not secret:
        print("Twitch: no client_id/client_secret in config.yaml — skipping Twitch "
              "discovery (no data faked). Add keys to enable it.")
        return []

    import requests

    token = requests.post(
        "https://id.twitch.tv/oauth2/token",
        params={"client_id": cid, "client_secret": secret,
                "grant_type": "client_credentials"},
        timeout=30).json().get("access_token")
    if not token:
        print("Twitch: could not get an app token — skipping.")
        return []
    headers = {"Client-Id": cid, "Authorization": f"Bearer {token}"}
    game = requests.get("https://api.twitch.tv/helix/games",
                        params={"name": tw.get("game_name", "Grand Theft Auto VI")},
                        headers=headers, timeout=30).json()
    games = game.get("data") or []
    if not games:
        print(f"Twitch: game {tw.get('game_name')!r} not found — skipping.")
        return []
    clips = requests.get(
        "https://api.twitch.tv/helix/clips",
        params={"game_id": games[0]["id"], "first": tw.get("top_clips", 10)},
        headers=headers, timeout=30).json().get("data") or []
    out = []
    for c in clips:
        created = c.get("created_at") or ""
        try:
            dt = datetime.strptime(created, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            hours = max((datetime.now(timezone.utc) - dt).total_seconds() / 3600.0, 0.1)
        except ValueError:
            hours = None
        out.append({
            "platform": "twitch",
            "clip_id": c.get("id"),
            "title": c.get("title") or "",
            "uploader": c.get("broadcaster_name") or "",
            "url": c.get("url") or "",
            "views": c.get("view_count") or 0,
            "duration_s": c.get("duration") or 0,
            "age_hours": round(hours, 2) if hours else None,
            "views_per_hour": round((c.get("view_count") or 0) / hours, 1) if hours else None,
        })
    out.sort(key=lambda c: (c["views_per_hour"] or 0), reverse=True)
    return out


def main():
    ap = argparse.ArgumentParser(description="Discover viral GTA 6 gameplay.")
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--out", default="discovery")
    ap.add_argument("--heatmap-top", type=int, default=5,
                    help="Fetch most-replayed heatmap for the top N YouTube candidates.")
    args = ap.parse_args()

    cfg = load_config(args.config)
    os.makedirs(args.out, exist_ok=True)

    print("== YouTube discovery ==")
    yt = youtube_candidates(cfg)
    print(f"  {len(yt)} candidates in the last {cfg['discovery']['max_age_hours']}h")

    print(f"== Heatmap peaks (from full metadata) ==")
    for cand in yt[: args.heatmap_top]:
        print(f"  {cand['video_id']}: peak={cand.get('heatmap_peak')}")

    print("== Twitch discovery ==")
    tw = twitch_candidates(cfg)
    print(f"  {len(tw)} Twitch clips")

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d-%H%M")
    payload = {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
               "youtube": yt, "twitch": tw}
    out_path = os.path.join(args.out, f"discovery-{stamp}.json")
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    print(f"\nWrote {out_path}")

    print("\n== Top YouTube by views/hour ==")
    for i, c in enumerate(yt[:10], 1):
        print(f"{i:2}. {c['views_per_hour']:>10,.0f} v/h | {c['views']:>9,} views | "
              f"{c['duration_s']:>5}s | {c['uploader']} — {c['title'][:80]}")
    if tw:
        print("\n== Top Twitch clips ==")
        for i, c in enumerate(tw[:10], 1):
            print(f"{i:2}. {c['views_per_hour']:>10,.0f} v/h | {c['views']:>9,} views | "
                  f"{c['uploader']} — {c['title'][:80]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

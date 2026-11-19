#!/usr/bin/env python3
"""Discovery stage: find viral clips on any topic on YouTube (no API key needed).

Sources
- YouTube: yt-dlp `ytsearch` over the active topic's search terms
  plus a per-creator search over the topic's watchlist. Candidates
  are ranked by views/hour (views / hours since upload).
- Twitch: Helix API top clips for the topic's game — ONLY when
  client_id/client_secret are set in config.yaml. Without keys, or
  when the topic has no Twitch game, the Twitch stage is skipped and
  says so; no data is ever faked.

The active topic comes from discover.resolve_topic(): a --topic CLI
flag, else the config's topic: block, else the gta6 preset. Any
subject works — a game, a hobby, a place, a food.

Outputs (into <pipeline>/discovery/):
- discovery-YYYY-MM-DD-HHMM.json — ranked candidates with full metadata
- printed table for the human running the pipeline

Usage:
    python discover.py [--config config.yaml] [--out discovery]
                       [--topic "Minecraft"]
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


def resolve_topic(cfg, args=None):
    """Resolve the active topic. Precedence: CLI flags > config topic
    block > gta6 preset. Returns a dict with keys: name, search_terms,
    creators, twitch_game, hook_strip, exclude_video_ids, source.

    - --topic <preset name> loads presets.<name> whole (e.g. gta6).
    - --topic <anything else> builds an ad-hoc topic from the name:
      search terms are the name plus "<name> gameplay" and "<name>
      walkthrough" (plus extra_terms), creators are empty (term search
      only) unless --creators is given, and the Twitch game defaults
      to the topic name (a non-game topic simply finds no game and
      Twitch is skipped gracefully).
    - A topic block may define `suffixes:` to replace the default
      ["gameplay", "walkthrough"] derivation, and `extra_terms:` for
      additional literal search terms (used only when search_terms
      is not set explicitly).
    - Legacy configs without topic:/presets: fall back to the old
      top-level search_terms / creators / exclude_video_ids and
      twitch.game_name keys (pre-topic configs, e.g. the v1.1 test
      configs keep working unchanged).
    """
    args = args or argparse.Namespace(topic=None, game=None, creators=None)
    cli_topic = getattr(args, "topic", None)
    cli_game = getattr(args, "game", None)
    cli_creators = getattr(args, "creators", None)
    presets = cfg.get("presets") or {}

    if cli_topic:
        if cli_topic in presets:
            topic = dict(presets[cli_topic])
            source = f"preset:{cli_topic}"
        else:
            topic = {"name": cli_topic}
            source = "ad-hoc"
    elif cfg.get("topic"):
        topic = dict(cfg["topic"])
        source = "config topic block"
    elif presets.get("gta6"):
        topic = dict(presets["gta6"])
        source = "preset:gta6 (default)"
    else:
        # Legacy config: no topic:/presets: at all.
        terms = list(cfg.get("search_terms") or [])
        if not terms:
            raise ValueError(
                "config has no topic: block, no presets.gta6, and no "
                "legacy search_terms — set topic: or presets: in "
                "config.yaml, or pass --topic <name>")
        return {
            "name": "GTA 6",
            "search_terms": terms,
            "creators": list(cfg.get("creators") or []),
            "twitch_game": ((cfg.get("twitch") or {}).get("game_name")
                            or "Grand Theft Auto VI"),
            "hook_strip": ["GTA 6", "GTA VI", "Grand Theft Auto VI",
                           "GTA6"],
            "exclude_video_ids": list(cfg.get("exclude_video_ids") or []),
            "source": "legacy top-level keys",
        }

    # CLI overrides (apply to preset, config-block and ad-hoc topics).
    if cli_creators is not None:
        topic["creators"] = [c.strip() for c in cli_creators.split(",")
                             if c.strip()]
    if cli_game is not None:
        topic["twitch_game"] = cli_game

    name = (topic.get("name") or "").strip() or "GTA 6"
    topic["name"] = name
    if not topic.get("search_terms"):
        suffixes = topic.get("suffixes") or ["gameplay", "walkthrough"]
        terms = [name] + [f"{name} {s}" for s in suffixes]
        terms += list(topic.get("extra_terms") or [])
        topic["search_terms"] = terms
    topic["creators"] = list(topic.get("creators") or [])
    if "twitch_game" not in topic or topic["twitch_game"] is None:
        # No game named for this topic: default to the topic name
        # ("Minecraft" IS the Twitch game name). A non-game topic
        # finds no game and Twitch is skipped gracefully. An explicit
        # twitch_game: "" in the topic means YouTube-only.
        topic["twitch_game"] = name
    topic["hook_strip"] = list(topic.get("hook_strip") or [name])
    topic["exclude_video_ids"] = list(topic.get("exclude_video_ids") or [])
    topic["source"] = source
    return topic


def build_queries(topic, disc):
    """(search term, result count) pairs for the active topic.

    Term searches use the topic's search_terms; creator searches use
    "<topic name> <creator>". An empty creator list means term search
    only — no creator queries are built.
    """
    queries = [(t, disc["search_results"]) for t in topic["search_terms"]]
    for creator in topic.get("creators") or []:
        queries.append((f"{topic['name']} {creator}",
                        disc["creator_results"]))
    return queries


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


def youtube_candidates(cfg, topic=None):
    """Collect + rank YouTube candidates by views/hour.

    Flat search entries lack upload timestamps, so after dedupe we fetch
    full metadata (one small request per video) for the top candidates
    by raw views, then rank the survivors by views/hour. The same full
    fetch also carries the most-replayed heatmap, which is stored on the
    candidate for moment selection.
    """
    topic = topic or resolve_topic(cfg)
    disc = cfg["discovery"]
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": True,
        "skip_download": True,
        "socket_timeout": 30,
        "retries": 3,
    }
    queries = build_queries(topic, disc)

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


def twitch_candidates(cfg, topic=None):
    """Top Twitch clips for the topic's game (Helix).

    Skips without keys, and skips when the topic has no Twitch game
    (twitch_game blank = YouTube-only topic). Never fakes data.
    """
    topic = topic or resolve_topic(cfg)
    tw = cfg.get("twitch") or {}
    cid = (tw.get("client_id") or "").strip()
    secret = (tw.get("client_secret") or "").strip()
    if not cid or not secret:
        print("Twitch: no client_id/client_secret in config.yaml — skipping Twitch "
              "discovery (no data faked). Add keys to enable it.")
        return []

    import requests

    game_name = (topic.get("twitch_game") or "").strip()
    if not game_name:
        print(f"Twitch: topic {topic['name']!r} has no Twitch game "
              "— skipping Twitch discovery (YouTube-only topic).")
        return []

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
                        params={"name": game_name},
                        headers=headers, timeout=30).json()
    games = game.get("data") or []
    if not games:
        print(f"Twitch: game {game_name!r} not found — skipping.")
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
    ap = argparse.ArgumentParser(description="Discover viral clips on any topic.")
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--out", default="discovery")
    ap.add_argument("--topic", default=None,
                    help="Topic to discover (a preset name like gta6, or any "
                         "new topic such as Minecraft — search terms are "
                         "derived from the name). Default: the config's "
                         "topic block, else the gta6 preset.")
    ap.add_argument("--game", default=None,
                    help="Twitch game-name override (empty string = skip "
                         "Twitch for this run).")
    ap.add_argument("--creators", default=None,
                    help="Comma-separated creator list override "
                         "(empty string = no creator search).")
    ap.add_argument("--heatmap-top", type=int, default=5,
                    help="Fetch most-replayed heatmap for the top N YouTube candidates.")
    args = ap.parse_args()

    cfg = load_config(args.config)
    topic = resolve_topic(cfg, args)
    print(f"Topic: {topic['name']} ({topic['source']})")
    os.makedirs(args.out, exist_ok=True)

    print("== YouTube discovery ==")
    yt = youtube_candidates(cfg, topic)
    print(f"  {len(yt)} candidates in the last {cfg['discovery']['max_age_hours']}h")

    print(f"== Heatmap peaks (from full metadata) ==")
    for cand in yt[: args.heatmap_top]:
        print(f"  {cand['video_id']}: peak={cand.get('heatmap_peak')}")

    print("== Twitch discovery ==")
    tw = twitch_candidates(cfg, topic)
    print(f"  {len(tw)} Twitch clips")

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d-%H%M")
    payload = {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
               "topic": {"name": topic["name"], "source": topic["source"]},
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

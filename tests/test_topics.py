#!/usr/bin/env python3
"""Topic tests: --topic generalizes the pipeline beyond GTA 6.

Run from the pipeline root: ./venv/bin/python tests/test_topics.py

No network, no downloads. Verifies: the gta6 preset loads with the
exact v1 values, the default run still resolves to GTA 6, CLI --topic
derives search terms for a new topic (game and non-game), --creators
and --game override, empty creators means term search only, hooks
strip the active topic name, legacy pre-topic configs still resolve,
and the digest is topic-aware.
"""
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import discover
import run_pipeline

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"{'PASS' if cond else 'FAIL'}  {name}"
          + (f" — {detail}" if detail and not cond else ""))


def args(topic=None, game=None, creators=None):
    return types.SimpleNamespace(topic=topic, game=game, creators=creators)


CREATORS_14 = ["Kai Cenat", "IShowSpeed", "caseoh_", "Valkyrae",
               "StableRonaldo", "TheBurntPeanut", "ElMariana",
               "DarkViperAU", "Caylus", "Jelly", "Rivers", "Jake Baldino",
               "Cinna", "Payal Gaming"]
TERMS_GTA6 = ["GTA 6 gameplay", "GTA VI gameplay"]

cfg = run_pipeline.load_config(os.path.join(ROOT, "config.yaml"))

# --- 1. gta6 preset holds the exact v1 values ---
preset = (cfg.get("presets") or {}).get("gta6") or {}
check("gta6 preset search terms are the exact v1 values",
      preset.get("search_terms") == TERMS_GTA6,
      str(preset.get("search_terms")))
check("gta6 preset creators are the exact 14 v1 names",
      preset.get("creators") == CREATORS_14,
      str(preset.get("creators")))
check("gta6 preset Twitch game is Grand Theft Auto VI",
      preset.get("twitch_game") == "Grand Theft Auto VI")
check("gta6 preset exclude list is the v1 DarkViperAU video",
      preset.get("exclude_video_ids") == ["KsNI7DeozPA"])

# --- 2. default run (no flags) resolves to GTA 6, unchanged ---
default = discover.resolve_topic(cfg, args())
check("default topic is GTA 6",
      default["name"] == "GTA 6", str(default))
check("default search terms are the exact v1 values",
      default["search_terms"] == TERMS_GTA6)
check("default creators are the exact 14 v1 names",
      default["creators"] == CREATORS_14)
check("default Twitch game is Grand Theft Auto VI",
      default["twitch_game"] == "Grand Theft Auto VI")
check("default exclude list is the v1 video",
      default["exclude_video_ids"] == ["KsNI7DeozPA"])

# --- 3. --topic gta6 loads the preset ---
via_flag = discover.resolve_topic(cfg, args(topic="gta6"))
check("--topic gta6 loads the preset (same values as default)",
      via_flag["search_terms"] == TERMS_GTA6
      and via_flag["creators"] == CREATORS_14
      and via_flag["twitch_game"] == "Grand Theft Auto VI",
      str(via_flag))

# --- 4. --topic Minecraft: ad-hoc derivation (game topic) ---
mc = discover.resolve_topic(cfg, args(topic="Minecraft"))
check("--topic Minecraft derives gameplay/walkthrough terms",
      mc["search_terms"] == ["Minecraft", "Minecraft gameplay",
                             "Minecraft walkthrough"],
      str(mc["search_terms"]))
check("--topic Minecraft has no creators (term search only)",
      mc["creators"] == [])
check("--topic Minecraft defaults the Twitch game to the topic name",
      mc["twitch_game"] == "Minecraft")
check("--topic Minecraft exclude list is empty",
      mc["exclude_video_ids"] == [])

# --- 5. --topic "street food": ad-hoc derivation (non-game topic) ---
sf = discover.resolve_topic(cfg, args(topic="street food"))
check("--topic 'street food' derives terms from the bare name",
      sf["search_terms"] == ["street food", "street food gameplay",
                             "street food walkthrough"],
      str(sf["search_terms"]))
check("--topic 'street food' Twitch game defaults to the topic name",
      sf["twitch_game"] == "street food")

# --- 6. CLI overrides: --creators and --game ---
mc2 = discover.resolve_topic(cfg, args(topic="Minecraft",
                                       creators="Dream, Technoblade",
                                       game=""))
check("--creators override sets the creator list",
      mc2["creators"] == ["Dream", "Technoblade"],
      str(mc2["creators"]))
check("--game '' blanks the Twitch game (YouTube-only run)",
      mc2["twitch_game"] == "")
mc3 = discover.resolve_topic(cfg, args(topic="gta6", game="Minecraft"))
check("--game override wins over the preset game",
      mc3["twitch_game"] == "Minecraft"
      and mc3["search_terms"] == TERMS_GTA6)

# --- 7. suffixes / extra_terms in a topic block ---
custom_cfg = {"topic": {"name": "Biryani", "suffixes": ["recipe", "tour"],
                        "extra_terms": ["best biryani Dhaka"],
                        "creators": []}}
bir = discover.resolve_topic(custom_cfg, args())
check("topic suffixes + extra_terms replace the defaults",
      bir["search_terms"] == ["Biryani", "Biryani recipe", "Biryani tour",
                              "best biryani Dhaka"],
      str(bir["search_terms"]))

# --- 8. build_queries: empty creators = no creator searches ---
disc = {"search_results": 12, "creator_results": 2}
q_mc = discover.build_queries(mc, disc)
check("empty creators builds term queries only",
      q_mc == [("Minecraft", 12), ("Minecraft gameplay", 12),
               ("Minecraft walkthrough", 12)], str(q_mc))
q_gta = discover.build_queries(default, disc)
check("gta6 builds term + per-creator queries",
      len(q_gta) == 2 + 14
      and q_gta[2] == ("GTA 6 Kai Cenat", 2), str(q_gta[:4]))

# --- 9. hooks strip the active topic, fall back with the topic name ---
check("hook strips the Minecraft topic name from the title",
      run_pipeline.hook_from_title(
          "I Built a MINECRAFT City in 100 Days", "Dream", mc)
      == "I BUILT A CITY IN 100 DAYS")
check("hook strips GTA variants for the default topic",
      run_pipeline.hook_from_title(
          "PLAYING GTA 6 FOR THE FIRST TIME (PART 1)", "Kai Cenat",
          default) == "PLAYING FOR THE FIRST TIME")
check("hook fallback uses the topic name, not GTA 6",
      run_pipeline.hook_from_title("Minecraft", "Dream", mc)
      == "DREAM PLAYS MINECRAFT")
check("hook fallback for the default topic is unchanged (GTA 6)",
      run_pipeline.hook_from_title("GTA 6", "Kai Cenat", default)
      == "KAI CENAT PLAYS GTA 6")

# --- 10. legacy pre-topic configs still resolve ---
legacy = run_pipeline.load_config(os.path.join(HERE, "config.test.yaml"))
leg = discover.resolve_topic(legacy, args())
check("legacy config resolves via top-level keys",
      leg["search_terms"] == ["GTA 6 gameplay"]
      and leg["creators"] == ["Kai Cenat"]
      and leg["twitch_game"] == "Grand Theft Auto VI",
      str(leg))

# --- 11. digest is topic-aware ---
import tempfile
tmp = tempfile.mkdtemp()
digest_path = os.path.join(tmp, "digest-test.md")
run_pipeline.write_digest(digest_path, [], [], [], cfg, mc)
text = open(digest_path, encoding="utf-8").read()
check("digest title carries the topic name",
      text.startswith("# Minecraft clip pipeline — digest"),
      text.splitlines()[0] if text else "")
check("digest records the topic + source",
      "Topic: Minecraft (ad-hoc)" in text)
check("digest Twitch note is the YouTube-only one when no keys",
      "no Twitch API keys" in text)

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("FAILED:", ", ".join(FAIL))
    sys.exit(1)

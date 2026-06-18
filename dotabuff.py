"""
OpenDota data module for Dota 2 Meta Bot.

Strategy:
  1. Primary: OpenDota Explorer SQL — exact position + rank bracket wins/picks
  2. Fallback: /heroStats endpoint — bracket-level data (no position filter)

heroStats real field format (verified from API):
  "{tier}_{pos}_pick", "{tier}_{pos}_win"  where tier=1..8, pos=1..5
  e.g. "8_5_pick" = Immortal pos5 picks, "8_5_win" = Immortal pos5 wins

Explorer SQL endpoint:
  GET /api/explorer?sql=SELECT+...
"""

import asyncio
import aiohttp
import logging
from time import time
from urllib.parse import quote

logger = logging.getLogger(__name__)

OPENDOTA_API = "https://api.opendota.com/api"

# Rank tier numbers used in heroStats keys
RANK_TIER_MAP = {
    "herald":   1,
    "guardian": 2,
    "crusader": 3,
    "archon":   4,
    "legend":   5,
    "ancient":  6,
    "divine":   7,
    "immortal": 8,
    "all":      None,
}

# rank_tier → avg_mmr range for Explorer query (approximate)
RANK_MMR_MAP = {
    1: (0, 770),
    2: (770, 1540),
    3: (1540, 2310),
    4: (2310, 3080),
    5: (3080, 3850),
    6: (3850, 4620),
    7: (4620, 5420),
    8: (5420, 99999),
}

# In-memory cache: key → (timestamp, data)
_cache: dict = {}
CACHE_TTL = 60 * 30  # 30 minutes


async def get_top_heroes(position: str, rank: str) -> list[dict]:
    cache_key = f"{position}_{rank}"
    now = time()
    if cache_key in _cache:
        ts, data = _cache[cache_key]
        if now - ts < CACHE_TTL:
            logger.info(f"Cache hit: {cache_key}")
            return data

    async with aiohttp.ClientSession() as session:
        # Try Explorer SQL first (most accurate)
        try:
            heroes = await fetch_via_explorer(session, position, rank)
            if heroes and len(heroes) >= 5:
                logger.info(f"Explorer returned {len(heroes)} heroes")
                _cache[cache_key] = (now, heroes)
                return heroes
        except Exception as e:
            logger.warning(f"Explorer failed: {e}, falling back to heroStats")

        # Fallback: heroStats
        try:
            heroes = await fetch_via_hero_stats(session, position, rank)
            _cache[cache_key] = (now, heroes)
            return heroes
        except Exception as e:
            logger.error(f"heroStats also failed: {e}")
            raise


# ─── Explorer SQL approach ────────────────────────────────────────────────────

async def fetch_via_explorer(
    session: aiohttp.ClientSession, position: str, rank: str
) -> list[dict]:
    """
    Use OpenDota Explorer (Postgres SQL) to get hero win/pick stats
    filtered by lane_role (position) and avg_rank_tier (rank bracket).

    Table: public_matches joined with player_matches.
    We query match_patch data that OpenDota exposes via explorer.
    """
    rank_tier = RANK_TIER_MAP.get(rank)
    lane_role = int(position)  # 1=safe, 2=mid, 3=off, 4=soft, 5=hard

    if rank_tier is not None:
        mmr_min, mmr_max = RANK_MMR_MAP[rank_tier]
        rank_filter = f"AND pm.avg_rank_tier >= {rank_tier * 10} AND pm.avg_rank_tier < {(rank_tier + 1) * 10}"
    else:
        rank_filter = ""

    sql = f"""
SELECT
    hero_id,
    COUNT(*) AS picks,
    SUM(CASE WHEN (player_slot < 128) = radiant_win THEN 1 ELSE 0 END) AS wins
FROM public_player_matches
JOIN public_matches USING (match_id)
WHERE lane_role = {lane_role}
  AND game_mode IN (1, 2, 22)
  {rank_filter}
  AND duration > 900
GROUP BY hero_id
ORDER BY picks DESC
LIMIT 60
""".strip()

    encoded = quote(sql)
    url = f"{OPENDOTA_API}/explorer?sql={encoded}"

    async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
        resp.raise_for_status()
        data = await resp.json()

    rows = data.get("rows", [])
    if not rows:
        raise ValueError("Explorer returned empty rows")

    # Fetch hero names
    hero_names = await fetch_hero_names(session)

    results = []
    total_picks = sum(r.get("picks", 0) or 0 for r in rows)

    for row in rows:
        hero_id = row.get("hero_id")
        picks = row.get("picks", 0) or 0
        wins = row.get("wins", 0) or 0
        if picks < 100:
            continue
        winrate = wins / picks * 100
        pick_pct = picks / total_picks * 100 if total_picks else 0
        results.append({
            "hero_id": hero_id,
            "localized_name": hero_names.get(hero_id, f"Hero #{hero_id}"),
            "winrate": winrate,
            "picks": picks,
            "pickrate": round(pick_pct, 2),
        })

    # Sort by winrate descending, limit to top 10
    results.sort(key=lambda x: x["winrate"], reverse=True)
    return results[:10]


# ─── heroStats fallback ───────────────────────────────────────────────────────

async def fetch_via_hero_stats(
    session: aiohttp.ClientSession, position: str, rank: str
) -> list[dict]:
    """
    Fallback using /heroStats endpoint.
    Real field names in heroStats JSON:
      "{tier}_{pos}_pick" and "{tier}_{pos}_win"
      e.g. "8_5_pick" = immortal hard support picks
    For "all" ranks: sum tiers 1-8.
    """
    url = f"{OPENDOTA_API}/heroStats"
    async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
        resp.raise_for_status()
        hero_stats = await resp.json()

    hero_names = await fetch_hero_names(session)
    rank_tier = RANK_TIER_MAP.get(rank)
    pos = position  # "1".."5"

    results = []
    for hero in hero_stats:
        hero_id = hero.get("id")
        name = hero_names.get(hero_id, hero.get("localized_name", f"Hero #{hero_id}"))

        wins, picks = _extract_wins_picks(hero, rank_tier, pos)
        if picks < 50:
            continue

        winrate = wins / picks * 100
        results.append({
            "hero_id": hero_id,
            "localized_name": name,
            "winrate": winrate,
            "picks": picks,
            "wins": wins,
            "pickrate": 0.0,
        })

    if results:
        max_picks = max(r["picks"] for r in results) or 1
        for r in results:
            r["pickrate"] = round(r["picks"] / max_picks * 100, 1)

    results.sort(key=lambda x: x["winrate"], reverse=True)
    return results[:10]


def _extract_wins_picks(hero: dict, rank_tier, pos: str):
    """
    heroStats field names (actual API):
      "{tier}_{pos}_pick", "{tier}_{pos}_win"  ← position-specific per tier
      "{tier}_pick", "{tier}_win"               ← tier total (no pos)
    """
    if rank_tier is not None:
        # Position + tier specific (best)
        picks = hero.get(f"{rank_tier}_{pos}_pick") or 0
        wins  = hero.get(f"{rank_tier}_{pos}_win")  or 0
        if picks > 0:
            return wins, picks
        # Tier only fallback
        picks = hero.get(f"{rank_tier}_pick") or 0
        wins  = hero.get(f"{rank_tier}_win")  or 0
        return wins, picks
    else:
        # All ranks: sum across tiers 1-8
        total_w, total_p = 0, 0
        for t in range(1, 9):
            total_p += hero.get(f"{t}_{pos}_pick") or 0
            total_w += hero.get(f"{t}_{pos}_win")  or 0
        if total_p > 0:
            return total_w, total_p
        # Ultimate fallback: all picks regardless of position
        for t in range(1, 9):
            total_p += hero.get(f"{t}_pick") or 0
            total_w += hero.get(f"{t}_win")  or 0
        return total_w, total_p


# ─── Hero names ───────────────────────────────────────────────────────────────

_hero_names_cache: dict[int, str] | None = None

async def fetch_hero_names(session: aiohttp.ClientSession) -> dict[int, str]:
    global _hero_names_cache
    if _hero_names_cache:
        return _hero_names_cache
    url = f"{OPENDOTA_API}/heroes"
    async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
        resp.raise_for_status()
        heroes = await resp.json()
    _hero_names_cache = {h["id"]: h["localized_name"] for h in heroes}
    return _hero_names_cache

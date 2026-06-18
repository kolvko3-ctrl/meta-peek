import asyncio
import aiohttp
import logging
from functools import lru_cache
from time import time

logger = logging.getLogger(__name__)

OPENDOTA_API = "https://api.opendota.com/api"

# Position mapping: OpenDota uses 1-5
POSITION_MAP = {
    "1": "is_pos1",
    "2": "is_pos2",
    "3": "is_pos3",
    "4": "is_pos4",
    "5": "is_pos5",
}

# Rank tier mapping for OpenDota
RANK_TIER_MAP = {
    "herald":    "1",  # 1x
    "guardian":  "2",  # 2x
    "crusader":  "3",  # 3x
    "archon":    "4",  # 4x
    "legend":    "5",  # 5x
    "ancient":   "6",  # 6x
    "divine":    "7",  # 7x
    "immortal":  "8",  # 8x
    "all":       None, # no filter
}

# Simple in-memory cache (position+rank → (timestamp, data))
_cache: dict = {}
CACHE_TTL = 60 * 30  # 30 minutes


async def get_top_heroes(position: str, rank: str) -> list[dict]:
    """
    Fetch top-10 heroes for a given position and rank bracket.
    Returns a list of dicts with keys: localized_name, winrate, pickrate
    """
    cache_key = f"{position}_{rank}"
    now = time()

    if cache_key in _cache:
        ts, data = _cache[cache_key]
        if now - ts < CACHE_TTL:
            logger.info(f"Cache hit for {cache_key}")
            return data

    async with aiohttp.ClientSession() as session:
        hero_stats = await fetch_hero_stats(session)
        hero_names = await fetch_hero_names(session)

    ranked_heroes = compute_top_heroes(hero_stats, hero_names, position, rank)
    _cache[cache_key] = (now, ranked_heroes)
    return ranked_heroes


async def fetch_hero_stats(session: aiohttp.ClientSession) -> list[dict]:
    url = f"{OPENDOTA_API}/heroStats"
    async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
        resp.raise_for_status()
        data = await resp.json()
        logger.info(f"Fetched stats for {len(data)} heroes")
        return data


async def fetch_hero_names(session: aiohttp.ClientSession) -> dict[int, str]:
    url = f"{OPENDOTA_API}/heroes"
    async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
        resp.raise_for_status()
        heroes = await resp.json()
        return {h["id"]: h["localized_name"] for h in heroes}


def compute_top_heroes(
    hero_stats: list[dict],
    hero_names: dict[int, str],
    position: str,
    rank: str,
) -> list[dict]:
    """
    OpenDota heroStats contains fields like:
      {rank}_win, {rank}_pick  — where rank is 1..8 (tier)
    For "all" ranks we sum across all tiers.

    Position filtering is trickier — heroStats doesn't filter by lane directly.
    We use the pro_win/pro_pick or all-bracket data and sort by winrate.
    For position-specific data we use the `{rank}_pos{pos}_wins / picks` if available,
    otherwise we fall back to bracket-level winrate.
    """
    rank_tier = RANK_TIER_MAP.get(rank)
    results = []

    for hero in hero_stats:
        hero_id = hero.get("id")
        name = hero_names.get(hero_id, hero.get("localized_name", "Unknown"))

        wins, picks = get_wins_picks(hero, rank_tier, position)

        if picks < 50:  # skip heroes with very low sample size
            continue

        winrate = (wins / picks * 100) if picks > 0 else 0

        # Estimate pickrate relative to total picks in this tier/position
        results.append({
            "hero_id": hero_id,
            "localized_name": name,
            "winrate": winrate,
            "picks": picks,
            "wins": wins,
        })

    # Sort by winrate descending
    results.sort(key=lambda x: x["winrate"], reverse=True)

    # Add pickrate as a % of most-picked hero (relative scale)
    if results:
        max_picks = max(r["picks"] for r in results) or 1
        for r in results:
            r["pickrate"] = round(r["picks"] / max_picks * 100, 1)

    return results[:10]


def get_wins_picks(hero: dict, rank_tier: str | None, position: str) -> tuple[int, int]:
    """
    Try to get position-specific and rank-specific wins/picks.
    OpenDota heroStats structure:
      - "{tier}_{pos_key}_win"  / "{tier}_{pos_key}_pick"  — per tier+pos
      - "{tier}_win" / "{tier}_pick"                        — per tier
      - "pro_win"   / "pro_pick"                            — pro only
    """
    pos_key = f"pos{position}"  # e.g. "pos1", "pos2"

    if rank_tier:
        # Try tier+position specific
        w_key = f"{rank_tier}_{pos_key}_win"
        p_key = f"{rank_tier}_{pos_key}_pick"
        wins = hero.get(w_key, 0) or 0
        picks = hero.get(p_key, 0) or 0

        if picks > 0:
            return wins, picks

        # Fall back to tier-only
        w_key = f"{rank_tier}_win"
        p_key = f"{rank_tier}_pick"
        wins = hero.get(w_key, 0) or 0
        picks = hero.get(p_key, 0) or 0
        return wins, picks

    else:
        # "all" ranks — sum across all tiers (1–8), position-specific first
        total_wins = 0
        total_picks = 0
        for tier in range(1, 9):
            w_key = f"{tier}_{pos_key}_win"
            p_key = f"{tier}_{pos_key}_pick"
            w = hero.get(w_key, 0) or 0
            p = hero.get(p_key, 0) or 0
            total_wins += w
            total_picks += p

        if total_picks > 0:
            return total_wins, total_picks

        # Fall back: sum all tiers without position
        for tier in range(1, 9):
            total_wins += hero.get(f"{tier}_win", 0) or 0
            total_picks += hero.get(f"{tier}_pick", 0) or 0

        return total_wins, total_picks

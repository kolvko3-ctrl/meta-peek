"""
OpenDota data module — ПРАВИЛЬНАЯ реализация.

Что мы узнали из реального API:
  heroStats поля: "1_pick","1_win" ... "8_pick","8_win" — это РАНГ (1=Herald..8=Immortal).
  ПОЗИЦИИ в heroStats НЕТ вообще. "1_pos_pick" не существует.

Решение: OpenDota Explorer SQL через GET /api/explorer?sql=...
  Таблицы: public_player_matches (lane_role 1-5) JOIN public_matches (avg_rank_tier, radiant_win)
  avg_rank_tier: 10-19=Herald, 20-29=Guardian, ..., 70-79=Divine, 80+=Immortal

Фильтры:
  - lane_role = {позиция}      — фильтр по позиции (ТОЧНЫЙ)
  - avg_rank_tier >= X         — фильтр по рангу
  - duration > 600             — убрать аномально короткие матчи
  - lobby_type IN (0,7)        — только публичные и ranked (не турниры, не боты)

Fallback если Explorer не работает: heroStats без позиции, только ранг.
"""

import aiohttp
import logging
from time import time
from urllib.parse import quote

logger = logging.getLogger(__name__)

OPENDOTA_API = "https://api.opendota.com/api"

# avg_rank_tier в OpenDota: десятки = ранг (1=Herald...8=Immortal), единицы = звёзды
RANK_TIER_MAP = {
    "herald":   (10, 19),
    "guardian": (20, 29),
    "crusader": (30, 39),
    "archon":   (40, 49),
    "legend":   (50, 59),
    "ancient":  (60, 69),
    "divine":   (70, 79),
    "immortal": (80, 99),
    "all":      None,
}

# Кэш: ключ → (timestamp, данные)
_cache: dict = {}
_hero_names_cache: dict[int, str] | None = None
CACHE_TTL = 60 * 30  # 30 минут


async def get_top_heroes(position: str, rank: str) -> list[dict]:
    cache_key = f"{position}_{rank}"
    now = time()
    if cache_key in _cache:
        ts, data = _cache[cache_key]
        if now - ts < CACHE_TTL:
            logger.info(f"Cache hit: {cache_key}")
            return data

    async with aiohttp.ClientSession() as session:
        hero_names = await _fetch_hero_names(session)

        # Пробуем Explorer SQL (самый точный источник)
        try:
            heroes = await _fetch_via_explorer(session, hero_names, position, rank)
            if heroes and len(heroes) >= 3:
                logger.info(f"Explorer OK: {len(heroes)} heroes for pos={position} rank={rank}")
                _cache[cache_key] = (now, heroes)
                return heroes
            logger.warning("Explorer returned too few results, trying fallback")
        except Exception as e:
            logger.warning(f"Explorer failed ({e}), using heroStats fallback")

        # Fallback: heroStats (без позиции, только ранг)
        heroes = await _fetch_via_hero_stats(session, hero_names, position, rank)
        _cache[cache_key] = (now, heroes)
        return heroes


# ─── Explorer SQL ─────────────────────────────────────────────────────────────

async def _fetch_via_explorer(
    session: aiohttp.ClientSession,
    hero_names: dict[int, str],
    position: str,
    rank: str,
) -> list[dict]:
    """
    SQL-запрос к OpenDota Explorer.
    Возвращает топ-10 героев по позиции и рангу.

    lane_role в public_player_matches:
      1 = Carry (Safe Lane)
      2 = Mid
      3 = Offlane (Hard Lane)
      4 = Soft Support
      5 = Hard Support
    """
    lane = int(position)
    rank_range = RANK_TIER_MAP.get(rank)

    rank_clause = ""
    if rank_range:
        lo, hi = rank_range
        rank_clause = f"AND pm.avg_rank_tier >= {lo} AND pm.avg_rank_tier <= {hi}"

    sql = (
        "SELECT ppm.hero_id, "
        "COUNT(*) AS picks, "
        "SUM(CASE WHEN (ppm.player_slot < 128) = pm.radiant_win THEN 1 ELSE 0 END) AS wins "
        "FROM public_player_matches ppm "
        "JOIN public_matches pm USING (match_id) "
        f"WHERE ppm.lane_role = {lane} "
        f"AND pm.lobby_type IN (0, 7) "
        f"AND pm.duration > 600 "
        f"{rank_clause} "
        "GROUP BY ppm.hero_id "
        "ORDER BY picks DESC "
        "LIMIT 80"
    )

    url = f"{OPENDOTA_API}/explorer?sql={quote(sql)}"
    logger.info(f"Explorer SQL: {sql[:120]}...")

    async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
        resp.raise_for_status()
        data = await resp.json()

    rows = data.get("rows") or []
    if not rows:
        raise ValueError("Explorer returned 0 rows")

    total_picks = sum(r.get("picks") or 0 for r in rows)
    results = []

    for row in rows:
        hero_id = row.get("hero_id")
        picks = row.get("picks") or 0
        wins = row.get("wins") or 0

        if picks < 200:  # отсеиваем редких героев
            continue

        winrate = wins / picks * 100
        pick_pct = picks / total_picks * 100 if total_picks else 0

        results.append({
            "hero_id": hero_id,
            "localized_name": hero_names.get(hero_id, f"Hero #{hero_id}"),
            "winrate": round(winrate, 2),
            "picks": picks,
            "pickrate": round(pick_pct, 2),
        })

    # Сортируем по winrate (можно переключить на picks для "популярных")
    results.sort(key=lambda x: x["winrate"], reverse=True)
    return results[:10]


# ─── heroStats fallback ───────────────────────────────────────────────────────

async def _fetch_via_hero_stats(
    session: aiohttp.ClientSession,
    hero_names: dict[int, str],
    position: str,
    rank: str,
) -> list[dict]:
    """
    Fallback через /heroStats.
    Позиции нет — фильтруем только по рангу.
    Дополнительно фильтруем героев по их РОЛЯМ (Carry/Support/etc.)
    чтобы не показывать СМ на керри.
    """
    url = f"{OPENDOTA_API}/heroStats"
    async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
        resp.raise_for_status()
        hero_stats = await resp.json()

    # Роли для каждой позиции (фильтр)
    POSITION_ROLES = {
        "1": {"Carry"},
        "2": {"Mid", "Carry", "Nuker", "Initiator"},
        "3": {"Offlane", "Durable", "Initiator", "Disabler", "Carry"},
        "4": {"Support", "Disabler", "Initiator", "Nuker"},
        "5": {"Support", "Disabler", "Nuker"},
    }
    allowed_roles = POSITION_ROLES.get(position, set())

    rank_range = RANK_TIER_MAP.get(rank)
    results = []

    for hero in hero_stats:
        hero_id = hero.get("id")
        name = hero_names.get(hero_id, hero.get("localized_name", f"Hero #{hero_id}"))
        roles = set(hero.get("roles") or [])

        # Фильтр по ролям — главное исправление против СМ на керри
        if position in ("1", "5") and not roles.intersection(allowed_roles):
            continue

        wins, picks = _get_bracket_stats(hero, rank_range)
        if picks < 100:
            continue

        winrate = wins / picks * 100
        results.append({
            "hero_id": hero_id,
            "localized_name": name,
            "winrate": round(winrate, 2),
            "picks": picks,
            "pickrate": 0.0,
        })

    if results:
        max_p = max(r["picks"] for r in results) or 1
        for r in results:
            r["pickrate"] = round(r["picks"] / max_p * 100, 1)

    results.sort(key=lambda x: x["winrate"], reverse=True)
    return results[:10]


def _get_bracket_stats(hero: dict, rank_range) -> tuple[int, int]:
    """
    Реальные поля heroStats: "{tier}_pick", "{tier}_win"
    где tier = 1 (Herald) .. 8 (Immortal).
    """
    if rank_range:
        lo, hi = rank_range
        tier = lo // 10  # 10→1, 20→2, ..., 80→8
        picks = hero.get(f"{tier}_pick") or 0
        wins  = hero.get(f"{tier}_win")  or 0
        return wins, picks
    else:
        # Все ранги — суммируем 1..8
        total_w, total_p = 0, 0
        for t in range(1, 9):
            total_p += hero.get(f"{t}_pick") or 0
            total_w += hero.get(f"{t}_win")  or 0
        return total_w, total_p


# ─── Hero names ───────────────────────────────────────────────────────────────

async def _fetch_hero_names(session: aiohttp.ClientSession) -> dict[int, str]:
    global _hero_names_cache
    if _hero_names_cache:
        return _hero_names_cache
    url = f"{OPENDOTA_API}/heroes"
    async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
        resp.raise_for_status()
        heroes = await resp.json()
    _hero_names_cache = {h["id"]: h["localized_name"] for h in heroes}
    logger.info(f"Loaded {len(_hero_names_cache)} hero names")
    return _hero_names_cache

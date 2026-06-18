"""
Dota 2 meta data — STRATZ GraphQL API.

Почему STRATZ, а не OpenDota:
  - OpenDota heroStats НЕ содержит позиций. Числа 1-8 в полях — это РАНГИ, не позиции.
  - OpenDota Explorer SQL возвращает 400 — публичный доступ ограничен.
  - STRATZ GraphQL поддерживает hero positions (POSITION_1..POSITION_5) и
    rank bracket (HERALD..IMMORTAL) нативно.

STRATZ API: https://api.stratz.com/graphql
Бесплатный, без API ключа (анонимно работает, но лимит 300 req/час).
Для высокой нагрузки можно получить бесплатный ключ на stratz.com/api.

GraphQL запрос для мета героев по позиции:
  heroStats { winHour winDay winWeek winMonth }
  
Для позиции используем: heroStats { stats(bracketIds: [...] positionIds: [...]) { ... } }
"""

import aiohttp
import logging
from time import time

logger = logging.getLogger(__name__)

STRATZ_API = "https://api.stratz.com/graphql"

# STRATZ bracketIds: 1=Herald, 2=Guardian, 3=Crusader, 4=Archon,
#                    5=Legend, 6=Ancient, 7=Divine, 8=Immortal
BRACKET_MAP = {
    "herald":   [1],
    "guardian": [2],
    "crusader": [3],
    "archon":   [4],
    "legend":   [5],
    "ancient":  [6],
    "divine":   [7],
    "immortal": [8],
    "all":      [1, 2, 3, 4, 5, 6, 7, 8],
}

# STRATZ positionIds: POSITION_1=1(Carry), POSITION_2=2(Mid),
#                     POSITION_3=3(Offlane), POSITION_4=4(Soft Sup), POSITION_5=5(Hard Sup)
POSITION_MAP = {
    "1": 1,
    "2": 2,
    "3": 3,
    "4": 4,
    "5": 5,
}

_cache: dict = {}
_hero_names: dict | None = None
CACHE_TTL = 60 * 30  # 30 минут


async def get_top_heroes(position: str, rank: str) -> list[dict]:
    key = f"{position}_{rank}"
    now = time()
    if key in _cache:
        ts, data = _cache[key]
        if now - ts < CACHE_TTL:
            return data

    async with aiohttp.ClientSession() as session:
        names = await _get_hero_names(session)
        result = await _fetch_stratz(session, names, position, rank)

    _cache[key] = (now, result)
    return result


async def _fetch_stratz(session, names, position, rank):
    """
    STRATZ GraphQL: получаем stats всех героев по позиции и рангу,
    возвращаем топ-10 по winRate.
    """
    brackets = BRACKET_MAP.get(rank, [1,2,3,4,5,6,7,8])
    pos_id = POSITION_MAP.get(position, 1)
    bracket_str = "[" + ",".join(str(b) for b in brackets) + "]"

    # STRATZ GraphQL query — heroes stats filtered by position and bracket
    query = """
    {
      heroStats {
        stats(
          bracketIds: %s
          positionIds: [%d]
          gameModeIds: [22]
        ) {
          heroId
          winCount
          matchCount
        }
      }
    }
    """ % (bracket_str, pos_id)

    headers = {
        "Content-Type": "application/json",
        "User-Agent": "Dota2MetaBot/1.0",
    }

    payload = {"query": query.strip()}
    logger.info(f"STRATZ query pos={position} rank={rank} brackets={brackets}")

    async with session.post(
        STRATZ_API,
        json=payload,
        headers=headers,
        timeout=aiohttp.ClientTimeout(total=25),
    ) as resp:
        resp.raise_for_status()
        data = await resp.json()

    if "errors" in data:
        raise RuntimeError(f"STRATZ GraphQL error: {data['errors']}")

    rows = data.get("data", {}).get("heroStats", {}).get("stats") or []
    if not rows:
        raise ValueError(f"STRATZ returned empty stats (pos={position}, rank={rank})")

    logger.info(f"STRATZ returned {len(rows)} heroes")

    total_picks = sum(r.get("matchCount") or 0 for r in rows)
    results = []
    for r in rows:
        hero_id = r.get("heroId")
        wins = r.get("winCount") or 0
        matches = r.get("matchCount") or 0
        if matches < 50:
            continue
        winrate = wins / matches * 100
        pickrate = matches / total_picks * 100 if total_picks else 0
        results.append({
            "hero_id": hero_id,
            "localized_name": names.get(hero_id, f"Hero #{hero_id}"),
            "winrate": round(winrate, 2),
            "picks": matches,
            "pickrate": round(pickrate, 2),
        })

    results.sort(key=lambda x: x["winrate"], reverse=True)
    return results[:10]


async def _get_hero_names(session) -> dict:
    """Получаем имена героев из OpenDota (они не меняются часто)."""
    global _hero_names
    if _hero_names:
        return _hero_names

    # Пробуем STRATZ constants
    try:
        query = """{ constants { heroes { id displayName } } }"""
        async with session.post(
            STRATZ_API,
            json={"query": query},
            headers={"Content-Type": "application/json"},
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            data = await resp.json()
        heroes = data.get("data", {}).get("constants", {}).get("heroes") or []
        if heroes:
            _hero_names = {h["id"]: h["displayName"] for h in heroes if h.get("id")}
            logger.info(f"Loaded {len(_hero_names)} hero names from STRATZ")
            return _hero_names
    except Exception as e:
        logger.warning(f"STRATZ hero names failed: {e}, trying OpenDota")

    # Fallback: OpenDota heroes
    async with session.get(
        "https://api.opendota.com/api/heroes",
        timeout=aiohttp.ClientTimeout(total=10),
    ) as resp:
        heroes = await resp.json()
    _hero_names = {h["id"]: h["localized_name"] for h in heroes}
    logger.info(f"Loaded {len(_hero_names)} hero names from OpenDota")
    return _hero_names

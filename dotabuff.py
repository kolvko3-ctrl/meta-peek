"""
Dota 2 meta data — STRATZ GraphQL API.

STRATZ требует токен. Без него API возвращает 401.
Токен бесплатный: зайди на https://stratz.com/api → войди через Steam → скопируй токен.
Добавь его в Railway как переменную: STRATZ_TOKEN=eyJ...

STRATZ positionIds: 1=Carry, 2=Mid, 3=Offlane, 4=Soft Support, 5=Hard Support
STRATZ bracketIds:  1=Herald, 2=Guardian, 3=Crusader, 4=Archon,
                    5=Legend, 6=Ancient, 7=Divine, 8=Immortal
"""

import os
import aiohttp
import logging
from time import time

logger = logging.getLogger(__name__)

STRATZ_API = "https://api.stratz.com/graphql"

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

POSITION_MAP = {
    "1": 1,  # Carry
    "2": 2,  # Mid
    "3": 3,  # Offlane
    "4": 4,  # Soft Support
    "5": 5,  # Hard Support
}

_cache: dict = {}
_hero_names: dict | None = None
CACHE_TTL = 60 * 30


def _get_token() -> str | None:
    return os.environ.get("STRATZ_TOKEN")


async def get_top_heroes(position: str, rank: str) -> list[dict]:
    token = _get_token()
    if not token:
        raise RuntimeError(
            "STRATZ_TOKEN не задан! Получи бесплатный токен на https://stratz.com/api "
            "и добавь его в Railway Variables как STRATZ_TOKEN=eyJ..."
        )

    key = f"{position}_{rank}"
    now = time()
    if key in _cache:
        ts, data = _cache[key]
        if now - ts < CACHE_TTL:
            return data

    async with aiohttp.ClientSession() as session:
        names = await _get_hero_names(session, token)
        result = await _fetch_stratz(session, token, names, position, rank)

    _cache[key] = (now, result)
    return result


async def _fetch_stratz(session, token, names, position, rank):
    brackets = BRACKET_MAP.get(rank, [1, 2, 3, 4, 5, 6, 7, 8])
    pos_id = POSITION_MAP.get(position, 1)
    bracket_str = "[" + ",".join(str(b) for b in brackets) + "]"

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
        "Authorization": f"Bearer {token}",
        "User-Agent": "Dota2MetaBot/1.0",
    }

    async with session.post(
        STRATZ_API,
        json={"query": query.strip()},
        headers=headers,
        timeout=aiohttp.ClientTimeout(total=25),
    ) as resp:
        if resp.status == 401:
            raise RuntimeError(
                "STRATZ токен недействителен или истёк. "
                "Обнови токен на https://stratz.com/api"
            )
        resp.raise_for_status()
        data = await resp.json()

    if "errors" in data:
        raise RuntimeError(f"STRATZ error: {data['errors']}")

    rows = data.get("data", {}).get("heroStats", {}).get("stats") or []
    if not rows:
        raise ValueError("STRATZ вернул пустые данные")

    logger.info(f"STRATZ: {len(rows)} героев для pos={position} rank={rank}")

    total = sum(r.get("matchCount") or 0 for r in rows)
    results = []
    for r in rows:
        hid = r.get("heroId")
        wins = r.get("winCount") or 0
        matches = r.get("matchCount") or 0
        if matches < 50:
            continue
        results.append({
            "hero_id": hid,
            "localized_name": names.get(hid, f"Hero #{hid}"),
            "winrate": round(wins / matches * 100, 2),
            "picks": matches,
            "pickrate": round(matches / total * 100, 2) if total else 0,
        })

    results.sort(key=lambda x: x["winrate"], reverse=True)
    return results[:10]


async def _get_hero_names(session, token) -> dict:
    global _hero_names
    if _hero_names:
        return _hero_names

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
    }

    # STRATZ constants
    try:
        query = "{ constants { heroes { id displayName } } }"
        async with session.post(
            STRATZ_API,
            json={"query": query},
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            data = await resp.json()
        heroes = data.get("data", {}).get("constants", {}).get("heroes") or []
        if heroes:
            _hero_names = {h["id"]: h["displayName"] for h in heroes if h.get("id")}
            logger.info(f"Загружено {len(_hero_names)} имён героев из STRATZ")
            return _hero_names
    except Exception as e:
        logger.warning(f"STRATZ hero names failed: {e}")

    # Fallback: OpenDota
    async with session.get(
        "https://api.opendota.com/api/heroes",
        timeout=aiohttp.ClientTimeout(total=10),
    ) as resp:
        heroes = await resp.json()
    _hero_names = {h["id"]: h["localized_name"] for h in heroes}
    return _hero_names

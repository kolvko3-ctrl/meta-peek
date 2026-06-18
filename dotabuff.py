"""
Dota 2 meta — STRATZ GraphQL API.

Правильный синтаксис запроса (из реального рабочего проекта на GitHub):
  heroStats {
    winWeek(
      bracketIds: [GUARDIAN, ARCHON],
      positionIds: [POSITION_1],
      gameModeIds: [ALL_PICK_RANKED]
    ) {
      heroId
      matchCount
      winCount
    }
  }

ВАЖНО:
  - bracketIds = enum строки: HERALD, GUARDIAN, CRUSADER, ARCHON, LEGEND, ANCIENT, DIVINE, IMMORTAL
  - positionIds = enum строки: POSITION_1 .. POSITION_5
  - gameModeIds = enum: ALL_PICK_RANKED (22) или просто не указываем
  - endpoint: winWeek (не stats!)
  - токен: Bearer в заголовке Authorization
"""

import os
import aiohttp
import logging
from time import time

logger = logging.getLogger(__name__)

STRATZ_API = "https://api.stratz.com/graphql"

# Enum значения для STRATZ API (строки, не числа!)
BRACKET_ENUM = {
    "herald":   ["HERALD"],
    "guardian": ["GUARDIAN"],
    "crusader": ["CRUSADER"],
    "archon":   ["ARCHON"],
    "legend":   ["LEGEND"],
    "ancient":  ["ANCIENT"],
    "divine":   ["DIVINE"],
    "immortal": ["IMMORTAL"],
    "all":      ["HERALD", "GUARDIAN", "CRUSADER", "ARCHON",
                 "LEGEND", "ANCIENT", "DIVINE", "IMMORTAL"],
}

POSITION_ENUM = {
    "1": "POSITION_1",
    "2": "POSITION_2",
    "3": "POSITION_3",
    "4": "POSITION_4",
    "5": "POSITION_5",
}

_cache: dict = {}
_hero_names: dict | None = None
CACHE_TTL = 60 * 30  # 30 минут


async def get_top_heroes(position: str, rank: str) -> list[dict]:
    token = os.environ.get("STRATZ_TOKEN", "")
    key = f"{position}_{rank}"
    now = time()

    if key in _cache:
        ts, data = _cache[key]
        if now - ts < CACHE_TTL:
            return data

    async with aiohttp.ClientSession() as session:
        names = await _get_hero_names(session, token)
        result = await _query_stratz(session, token, names, position, rank)

    _cache[key] = (now, result)
    return result


async def _query_stratz(session, token, names, position, rank):
    brackets = BRACKET_ENUM.get(rank, ["HERALD","GUARDIAN","CRUSADER","ARCHON",
                                        "LEGEND","ANCIENT","DIVINE","IMMORTAL"])
    pos = POSITION_ENUM.get(position, "POSITION_1")

    bracket_str = ", ".join(brackets)

    # Правильный запрос — winWeek с enum значениями
    query = """{
  heroStats {
    winWeek(
      bracketIds: [%s]
      positionIds: [%s]
    ) {
      heroId
      matchCount
      winCount
    }
  }
}""" % (bracket_str, pos)

    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    logger.info(f"STRATZ query: pos={pos} brackets=[{bracket_str}]")

    async with session.post(
        STRATZ_API,
        json={"query": query},
        headers=headers,
        timeout=aiohttp.ClientTimeout(total=25),
    ) as resp:
        status = resp.status
        body = await resp.json()

        if status == 401:
            raise RuntimeError("STRATZ: токен недействителен (401). Обнови на stratz.com/api")
        if status != 200:
            raise RuntimeError(f"STRATZ HTTP {status}: {body}")

    errors = body.get("errors")
    if errors:
        raise RuntimeError(f"STRATZ GraphQL ошибка: {errors[0].get('message', errors)}")

    rows = (body.get("data") or {}).get("heroStats", {}).get("winWeek") or []
    logger.info(f"STRATZ вернул {len(rows)} строк")

    if not rows:
        raise ValueError("STRATZ вернул пустой список героев")

    total = sum(r.get("matchCount") or 0 for r in rows)
    out = []
    for r in rows:
        hid = r.get("heroId")
        wins = r.get("winCount") or 0
        matches = r.get("matchCount") or 0
        if matches < 50:
            continue
        out.append({
            "hero_id": hid,
            "localized_name": names.get(hid, f"Hero #{hid}"),
            "winrate": round(wins / matches * 100, 2),
            "picks": matches,
            "pickrate": round(matches / total * 100, 2) if total else 0,
        })

    out.sort(key=lambda x: x["winrate"], reverse=True)
    return out[:10]


async def _get_hero_names(session, token) -> dict:
    global _hero_names
    if _hero_names:
        return _hero_names

    # OpenDota — стабильный источник имён
    try:
        async with session.get(
            "https://api.opendota.com/api/heroes",
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            heroes = await resp.json()
        _hero_names = {h["id"]: h["localized_name"] for h in heroes}
        logger.info(f"Загружено {len(_hero_names)} имён из OpenDota")
        return _hero_names
    except Exception as e:
        logger.warning(f"OpenDota hero names failed: {e}")

    # Fallback: STRATZ constants
    try:
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        q = "{ constants { heroes { id displayName } } }"
        async with session.post(
            STRATZ_API, json={"query": q}, headers=headers,
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            data = await resp.json()
        heroes = data.get("data", {}).get("constants", {}).get("heroes") or []
        _hero_names = {h["id"]: h["displayName"] for h in heroes if h.get("id")}
        logger.info(f"Загружено {len(_hero_names)} имён из STRATZ")
        return _hero_names
    except Exception as e:
        logger.warning(f"STRATZ hero names failed: {e}")
        return {}

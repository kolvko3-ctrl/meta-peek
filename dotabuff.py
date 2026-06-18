"""
Dota 2 meta — STRATZ GraphQL API.

Правильный синтаксис (из рабочего GitHub проекта):
  heroStats { winWeek(bracketIds: [GUARDIAN], positionIds: [POSITION_1]) { heroId matchCount winCount } }

STRATZ_TOKEN обязателен — получи бесплатно на stratz.com/api
"""

import os
import json
import aiohttp
import logging
from time import time

logger = logging.getLogger(__name__)

STRATZ_API = "https://api.stratz.com/graphql"

BRACKET_ENUM = {
    "herald":   ["HERALD"],
    "guardian": ["GUARDIAN"],
    "crusader": ["CRUSADER"],
    "archon":   ["ARCHON"],
    "legend":   ["LEGEND"],
    "ancient":  ["ANCIENT"],
    "divine":   ["DIVINE"],
    "immortal": ["IMMORTAL"],
    "all":      ["HERALD","GUARDIAN","CRUSADER","ARCHON","LEGEND","ANCIENT","DIVINE","IMMORTAL"],
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
CACHE_TTL = 60 * 30


async def get_top_heroes(position: str, rank: str) -> list[dict]:
    token = os.environ.get("STRATZ_TOKEN", "").strip()
    if not token:
        raise RuntimeError(
            "STRATZ_TOKEN не задан в Railway Variables!\n"
            "Получи бесплатный токен: stratz.com/api → Login with Steam"
        )

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
    brackets = BRACKET_ENUM.get(rank, list(BRACKET_ENUM["all"]))
    pos = POSITION_ENUM.get(position, "POSITION_1")
    bracket_str = ", ".join(brackets)

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

    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Authorization": f"Bearer {token}",
        "User-Agent": "Dota2MetaBot/1.0",
    }

    logger.info(f"STRATZ → pos={pos} brackets=[{bracket_str}]")

    async with session.post(
        STRATZ_API,
        data=json.dumps({"query": query}),
        headers=headers,
        timeout=aiohttp.ClientTimeout(total=25),
    ) as resp:
        status = resp.status
        raw = await resp.text()
        logger.info(f"STRATZ ← status={status} len={len(raw)}")

    if status == 401:
        raise RuntimeError("STRATZ: токен недействителен (401). Обнови на stratz.com/api")
    if status == 403:
        raise RuntimeError(
            f"STRATZ вернул 403 Forbidden.\n"
            f"Токен: {'задан (' + str(len(token)) + ' символов)' if token else 'НЕ ЗАДАН'}\n"
            f"Ответ: {raw[:150]}"
        )
    if status != 200:
        raise RuntimeError(f"STRATZ HTTP {status}: {raw[:200]}")

    try:
        body = json.loads(raw)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"STRATZ вернул не-JSON (status={status}): {raw[:200]}")

    errors = body.get("errors")
    if errors:
        raise RuntimeError(f"STRATZ GraphQL: {errors[0].get('message', str(errors))}")

    rows = (body.get("data") or {}).get("heroStats", {}).get("winWeek") or []
    logger.info(f"STRATZ вернул {len(rows)} героев")

    if not rows:
        raise ValueError("STRATZ вернул пустой список — попробуй другой ранг")

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

    try:
        async with session.get(
            "https://api.opendota.com/api/heroes",
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            data = await resp.json()
        _hero_names = {h["id"]: h["localized_name"] for h in data}
        logger.info(f"Загружено {len(_hero_names)} имён из OpenDota")
        return _hero_names
    except Exception as e:
        logger.warning(f"OpenDota failed: {e}")

    try:
        q = "{ constants { heroes { id displayName } } }"
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": f"Bearer {token}",
        }
        async with session.post(
            STRATZ_API, data=json.dumps({"query": q}),
            headers=headers, timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            raw = await resp.text()
            body = json.loads(raw)
        heroes = body.get("data", {}).get("constants", {}).get("heroes") or []
        _hero_names = {h["id"]: h["displayName"] for h in heroes if h.get("id")}
        logger.info(f"Загружено {len(_hero_names)} имён из STRATZ")
        return _hero_names
    except Exception as e:
        logger.warning(f"STRATZ constants failed: {e}")
        return {}

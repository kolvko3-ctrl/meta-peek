"""
Билды героев через OpenDota API.

itemPopularity возвращает:
{
  "starting_items":   { "44": 1500, "37": 1200, ... },  ← числовые ID айтемов!
  "early_game_items": { "180": 800, ... },
  "mid_game_items":   { "232": 600, ... },
  "late_game_items":  { "254": 400, ... }
}

/api/constants/items возвращает:
{
  "item_tango": { "id": 44, "dname": "Tango", ... },
  "item_boots":  { "id": 29, "dname": "Boots of Speed", ... },
  ...
}

Значит нужно строить маппинг: item_id (int) → display_name (str)
"""

import asyncio
import aiohttp
import logging
from time import time

logger = logging.getLogger(__name__)
API = "https://api.opendota.com/api"

_item_id_to_name: dict[int, str] | None = None   # 44 → "Tango"
_abilities_map:   dict[str, str]  | None = None   # "shadow_shaman_ether_shock" → "Ether Shock"
_hero_ab_map:     dict | None = None              # "npc_dota_hero_X" → {abilities, skill_points}
_build_cache:     dict = {}
BUILD_CACHE_TTL = 60 * 60  # 1 час


async def get_hero_build(hero_id: int, hero_internal: str) -> dict:
    now = time()
    if hero_id in _build_cache:
        ts, data = _build_cache[hero_id]
        if now - ts < BUILD_CACHE_TTL:
            return data

    async with aiohttp.ClientSession() as session:
        item_map, ab_map, hero_ab = await asyncio.gather(
            _load_items(session),
            _load_abilities(session),
            _load_hero_abilities(session, hero_internal),
        )
        item_pop = await _fetch_item_popularity(session, hero_id)

    build = _parse_build(item_pop, item_map, ab_map, hero_ab)
    _build_cache[hero_id] = (now, build)
    return build


async def _fetch_item_popularity(session, hero_id: int) -> dict:
    url = f"{API}/heroes/{hero_id}/itemPopularity"
    async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
        resp.raise_for_status()
        return await resp.json()


async def _load_items(session) -> dict[int, str]:
    """Возвращает {item_id: display_name}, например {44: "Tango", 29: "Boots of Speed"}"""
    global _item_id_to_name
    if _item_id_to_name:
        return _item_id_to_name

    url = f"{API}/constants/items"
    async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
        data = await resp.json()

    # data = {"item_tango": {"id": 44, "dname": "Tango", ...}, ...}
    result = {}
    for key, val in data.items():
        if not isinstance(val, dict):
            continue
        item_id = val.get("id")
        dname   = val.get("dname") or key.replace("item_", "").replace("_", " ").title()
        if item_id is not None:
            result[int(item_id)] = dname

    logger.info(f"Загружено {len(result)} айтемов")
    _item_id_to_name = result
    return result


async def _load_abilities(session) -> dict[str, str]:
    """Возвращает {internal_name: display_name}"""
    global _abilities_map
    if _abilities_map:
        return _abilities_map

    url = f"{API}/constants/abilities"
    async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
        data = await resp.json()

    result = {}
    for k, v in data.items():
        if isinstance(v, dict):
            result[k] = v.get("dname") or k.replace("_", " ").title()
        else:
            result[k] = k.replace("_", " ").title()

    logger.info(f"Загружено {len(result)} способностей")
    _abilities_map = result
    return result


async def _load_hero_abilities(session, hero_internal: str) -> dict:
    """Возвращает {abilities: [...], skill_points: [...]} для героя"""
    global _hero_ab_map
    if _hero_ab_map is None:
        url = f"{API}/constants/hero_abilities"
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            _hero_ab_map = await resp.json()

    key  = f"npc_dota_hero_{hero_internal}"
    data = _hero_ab_map.get(key) or {}
    return {
        "abilities":    data.get("abilities", []),
        "skill_points": data.get("skill_points", []),
    }


def _parse_build(item_pop: dict, item_map: dict, ab_map: dict, hero_ab: dict) -> dict:
    def top_items(section: dict, limit: int = 6) -> list[str]:
        if not section:
            return []
        # Ключи могут быть строками-числами: {"44": 1500, "29": 800}
        sorted_items = sorted(section.items(), key=lambda x: x[1], reverse=True)
        result = []
        for raw_id, _ in sorted_items:
            try:
                item_id = int(raw_id)
            except (ValueError, TypeError):
                continue
            name = item_map.get(item_id)
            if name and name not in result:
                result.append(name)
            if len(result) >= limit:
                break
        return result

    # Скиллбилд — первые 7 уровней
    abilities   = hero_ab.get("abilities", [])
    skill_pts   = hero_ab.get("skill_points", [])
    skill_build = []

    for lvl, sp in enumerate(skill_pts[:7], start=1):
        idx = sp - 1
        if 0 <= idx < len(abilities):
            ab_internal = abilities[idx]
            # Убираем суффикс _0, _1 и т.д. (таланты)
            display = ab_map.get(ab_internal)
            if not display:
                display = ab_internal.split("special_bonus")[0].replace("_", " ").strip().title()
            skill_build.append(f"Lvl {lvl}: {display}")
        else:
            skill_build.append(f"Lvl {lvl}: Stats")

    return {
        "starting_items": top_items(item_pop.get("starting_items", {}), 6),
        "core_items":     top_items(item_pop.get("mid_game_items", {}), 6),
        "late_items":     top_items(item_pop.get("late_game_items", {}), 4),
        "abilities":      skill_build,
    }

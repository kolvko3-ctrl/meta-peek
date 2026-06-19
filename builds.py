"""
Билды героев через OpenDota API.

Эндпоинты:
  GET /heroes/{id}/itemPopularity  → starting_items, early_game_items, mid_game_items, late_game_items
  GET /constants/items             → item_id → { dname, cost }
  GET /constants/hero_abilities    → hero_name → { abilities: [...], skill_points: [...] }
  GET /constants/ability_ids       → ability_id → ability_name
  GET /constants/abilities         → ability_name → { dname }
"""

import aiohttp
import logging
from time import time

logger = logging.getLogger(__name__)
API = "https://api.opendota.com/api"

# Кэши
_items_cache: dict | None = None          # item_name → dname
_abilities_cache: dict | None = None      # ability_name → dname
_hero_abilities_cache: dict | None = None # hero_name → {abilities, skill_points}
_build_cache: dict = {}                   # hero_id → (ts, build_data)

BUILD_CACHE_TTL = 60 * 60  # 1 час


async def get_hero_build(hero_id: int, hero_name: str) -> dict:
    """
    Возвращает dict с ключами:
      starting_items: list[str]   — стартовые айтемы (названия)
      core_items:     list[str]   — корневые айтемы
      late_items:     list[str]   — поздние айтемы
      abilities:      list[str]   — порядок скиллов (уровни 1-7)
    """
    now = time()
    if hero_id in _build_cache:
        ts, data = _build_cache[hero_id]
        if now - ts < BUILD_CACHE_TTL:
            return data

    async with aiohttp.ClientSession() as session:
        items_map, abilities_map, hero_ab = await _load_constants(session, hero_name)
        item_pop = await _fetch_item_popularity(session, hero_id)

    build = _parse_build(item_pop, items_map, hero_ab, abilities_map)
    _build_cache[hero_id] = (now, build)
    return build


async def _load_constants(session, hero_name: str):
    """Загружаем константы параллельно."""
    import asyncio
    items_task    = asyncio.create_task(_get_items(session))
    abilities_task = asyncio.create_task(_get_abilities(session))
    hero_ab_task  = asyncio.create_task(_get_hero_abilities(session, hero_name))
    return await asyncio.gather(items_task, abilities_task, hero_ab_task)


async def _fetch_item_popularity(session, hero_id: int) -> dict:
    url = f"{API}/heroes/{hero_id}/itemPopularity"
    async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
        resp.raise_for_status()
        return await resp.json()


async def _get_items(session) -> dict:
    """item_internal_name → display_name"""
    global _items_cache
    if _items_cache:
        return _items_cache
    url = f"{API}/constants/items"
    async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
        data = await resp.json()
    # data = { "item_name": { "dname": "...", "cost": ... }, ... }
    _items_cache = {k: v.get("dname", k) for k, v in data.items() if isinstance(v, dict)}
    return _items_cache


async def _get_abilities(session) -> dict:
    """ability_internal_name → display_name"""
    global _abilities_cache
    if _abilities_cache:
        return _abilities_cache
    url = f"{API}/constants/abilities"
    async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
        data = await resp.json()
    # data = { "ability_name": { "dname": "..." }, ... }
    _abilities_cache = {}
    for k, v in data.items():
        if isinstance(v, dict):
            _abilities_cache[k] = v.get("dname") or k
    return _abilities_cache


async def _get_hero_abilities(session, hero_name: str) -> dict:
    """
    Возвращает abilities + skill_points для героя.
    hero_name = internal name (e.g. 'antimage', 'crystal_maiden')
    """
    global _hero_abilities_cache
    if _hero_abilities_cache is None:
        url = f"{API}/constants/hero_abilities"
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            _hero_abilities_cache = await resp.json()

    # hero_abilities ключи — внутренние имена: 'npc_dota_hero_antimage'
    # Ищем по нашему hero_name
    key = f"npc_dota_hero_{hero_name}"
    data = _hero_abilities_cache.get(key) or {}
    return {
        "abilities":    data.get("abilities", []),
        "skill_points": data.get("skill_points", []),  # [1,1,2,1,3,1,4,...] — что качается на каждом уровне
    }


def _parse_build(item_pop: dict, items_map: dict, hero_ab: dict, abilities_map: dict) -> dict:
    """
    item_pop структура:
    {
      "starting_items":   { "item_name": count, ... },
      "early_game_items": { "item_name": count, ... },
      "mid_game_items":   { "item_name": count, ... },
      "late_game_items":  { "item_name": count, ... }
    }
    """
    def top_items(section: dict, limit: int = 6) -> list[str]:
        if not section:
            return []
        sorted_items = sorted(section.items(), key=lambda x: x[1], reverse=True)
        result = []
        for name, _ in sorted_items[:limit]:
            display = items_map.get(name, name.replace("item_", "").replace("_", " ").title())
            if display and display not in result:
                result.append(display)
        return result

    # Скиллбилд — первые 7 уровней
    abilities   = hero_ab.get("abilities", [])
    skill_pts   = hero_ab.get("skill_points", [])
    skill_build = []

    # skill_points: список индексов скиллов (1-based), который качают на каждом уровне
    # Например [1, 2, 1, 3, 1, 4, 2, ...] → уровень 1 качаем скилл 1, уровень 2 — скилл 2, ...
    for lvl, sp in enumerate(skill_pts[:7], start=1):
        idx = sp - 1  # переводим в 0-based
        if 0 <= idx < len(abilities):
            ab_name = abilities[idx]
            display = abilities_map.get(ab_name, ab_name.replace("_", " ").title())
            skill_build.append(f"Lvl {lvl}: {display}")
        else:
            skill_build.append(f"Lvl {lvl}: Stats")

    return {
        "starting_items": top_items(item_pop.get("starting_items", {}), 6),
        "core_items":     top_items(item_pop.get("mid_game_items", {}), 6),
        "late_items":     top_items(item_pop.get("late_game_items", {}), 4),
        "abilities":      skill_build,
    }

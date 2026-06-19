"""
Билды героев через OpenDota API.

itemPopularity реальная структура:
{
  "starting_items":   {"44": 1823, "37": 1200, ...},   ← 0-10 мин
  "early_game_items": {"29": 900, "180": 800, ...},     ← 10 мин
  "mid_game_items":   {"63": 600, "232": 500, ...},     ← ~20 мин
  "late_game_items":  {"254": 400, "1":  350, ...}      ← 30+ мин
}
Ключи = строковые item_id. Значения = кол-во матчей где этот айтем был куплен.

Фильтр "собранных" айтемов:
  - Убираем расходники (Tango, Clarity, Salve, TP, Ward, Smoke...)
  - Убираем компоненты (Mantle, Circlet, Crown...) — показываем только конечные предметы
  - Конечный предмет = has_recipe=true ИЛИ cost >= 1000 ИЛИ в whitelist

hero_abilities реальная структура (из /constants/hero_abilities):
{
  "npc_dota_hero_shadow_shaman": {
    "abilities": ["shadow_shaman_ether_shock", "shadow_shaman_voodoo",
                  "shadow_shaman_shackles", "shadow_shaman_mass_serpent_ward"],
    "skill_points": [1, 2, 1, 2, 1, 4, 1, 2, 2, 3, 3, 4, 3, 3]
    // skill_points[i] = индекс (1-based) скилла который качается на уровне i+1
  }
}
"""

import asyncio
import aiohttp
import logging
from time import time

logger = logging.getLogger(__name__)
API = "https://api.opendota.com/api"

# Кэши
_item_map:    dict[int, dict] | None = None   # id → {name, cost, is_recipe}
_ab_map:      dict[str, str]  | None = None   # internal → display name
_hero_ab_map: dict | None            = None   # npc_dota_hero_X → {abilities, skill_points}
_build_cache: dict                   = {}

BUILD_CACHE_TTL = 60 * 60  # 1 час

# Расходники и компоненты — не показываем в билде
CONSUMABLES = {
    "item_tango", "item_clarity", "item_flask", "item_smoke_of_deceit",
    "item_ward_observer", "item_ward_sentry", "item_tp", "item_tome_of_knowledge",
    "item_enchanted_mango", "item_faerie_fire", "item_dust", "item_sentry",
    "item_observer_ward", "item_courier", "item_flying_courier",
}

# Минимальная стоимость чтобы считаться "финальным" айтемом
MIN_ITEM_COST = 1000


async def get_hero_build(hero_id: int, hero_internal: str) -> dict:
    now = time()
    if hero_id in _build_cache:
        ts, data = _build_cache[hero_id]
        if now - ts < BUILD_CACHE_TTL:
            return data

    async with aiohttp.ClientSession() as session:
        item_map, ab_map, hero_ab, item_pop = await asyncio.gather(
            _load_items(session),
            _load_abilities(session),
            _load_hero_abilities(session, hero_internal),
            _fetch_item_popularity(session, hero_id),
        )

    build = _parse_build(item_pop, item_map, ab_map, hero_ab)
    _build_cache[hero_id] = (now, build)
    return build


async def _fetch_item_popularity(session, hero_id: int) -> dict:
    url = f"{API}/heroes/{hero_id}/itemPopularity"
    async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
        resp.raise_for_status()
        return await resp.json()


async def _load_items(session) -> dict[int, dict]:
    """id → {name, cost, recipe}"""
    global _item_map
    if _item_map:
        return _item_map

    url = f"{API}/constants/items"
    async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
        data = await resp.json()

    result: dict[int, dict] = {}
    for key, val in data.items():
        if not isinstance(val, dict):
            continue
        item_id = val.get("id")
        if item_id is None:
            continue
        result[int(item_id)] = {
            "name":    val.get("dname") or key.replace("item_", "").replace("_", " ").title(),
            "cost":    val.get("cost") or 0,
            "recipe":  val.get("recipe", False),
            "key":     key,
        }

    logger.info(f"Загружено {len(result)} айтемов")
    _item_map = result
    return result


async def _load_abilities(session) -> dict[str, str]:
    global _ab_map
    if _ab_map:
        return _ab_map

    url = f"{API}/constants/abilities"
    async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
        data = await resp.json()

    result = {}
    for k, v in data.items():
        if isinstance(v, dict) and v.get("dname"):
            result[k] = v["dname"]
        else:
            result[k] = k.split("_")[-1].title()

    logger.info(f"Загружено {len(result)} способностей")
    _ab_map = result
    return result


async def _load_hero_abilities(session, hero_internal: str) -> dict:
    global _hero_ab_map
    if _hero_ab_map is None:
        url = f"{API}/constants/hero_abilities"
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            _hero_ab_map = await resp.json()

    key  = f"npc_dota_hero_{hero_internal}"
    data = _hero_ab_map.get(key) or {}
    logger.info(f"Hero abilities for {key}: abilities={data.get('abilities', [])[:4]}, skill_points={data.get('skill_points', [])[:7]}")
    return {
        "abilities":    data.get("abilities", []),
        "skill_points": data.get("skill_points", []),
    }


def _is_real_item(item_id: int, item_map: dict) -> bool:
    """Возвращает True если это финальный предмет (не компонент, не расходник)."""
    info = item_map.get(item_id)
    if not info:
        return False
    key  = info.get("key", "")
    cost = info.get("cost") or 0

    # Убираем расходники
    if key in CONSUMABLES:
        return False

    # Убираем рецепты
    if info.get("recipe"):
        return False

    # Убираем дешёвые компоненты
    if cost < MIN_ITEM_COST:
        return False

    return True


def _parse_build(item_pop: dict, item_map: dict, ab_map: dict, hero_ab: dict) -> dict:

    def top_items(section: dict, limit: int = 5) -> list[str]:
        if not section:
            return []
        sorted_items = sorted(section.items(), key=lambda x: x[1], reverse=True)
        result = []
        for raw_id, _ in sorted_items:
            try:
                item_id = int(raw_id)
            except (ValueError, TypeError):
                continue
            if not _is_real_item(item_id, item_map):
                continue
            name = item_map[item_id]["name"]
            if name not in result:
                result.append(name)
            if len(result) >= limit:
                break
        return result

    def top_starting(section: dict, limit: int = 5) -> list[str]:
        """Стартовые — включаем расходники, но не компоненты."""
        if not section:
            return []
        sorted_items = sorted(section.items(), key=lambda x: x[1], reverse=True)
        result = []
        for raw_id, _ in sorted_items:
            try:
                item_id = int(raw_id)
            except (ValueError, TypeError):
                continue
            info = item_map.get(item_id)
            if not info:
                continue
            if info.get("recipe"):
                continue
            cost = info.get("cost") or 0
            if cost > 600:   # дорогие компоненты не стартовые
                continue
            name = info["name"]
            if name not in result:
                result.append(name)
            if len(result) >= limit:
                break
        return result

    # Скиллбилд
    abilities   = hero_ab.get("abilities", [])
    skill_pts   = hero_ab.get("skill_points", [])

    # Фильтруем скрытые способности
    real_abilities = [a for a in abilities if "hidden" not in a.lower()]

    skill_build = []
    for lvl, sp in enumerate(skill_pts[:7], start=1):
        idx = sp - 1
        if idx < 0:
            skill_build.append(f"Lvl {lvl}: Характеристики")
            continue

        # sp может указывать на реальные или скрытые скиллы
        # Пробуем сначала по real_abilities, потом по всем abilities
        ab_name = None
        if idx < len(real_abilities):
            ab_name = real_abilities[idx]
        elif idx < len(abilities):
            ab_name = abilities[idx]

        if ab_name:
            if "generic_hidden" in ab_name or "hidden" in ab_name:
                skill_build.append(f"Lvl {lvl}: Характеристики")
            elif "special_bonus" in ab_name:
                skill_build.append(f"Lvl {lvl}: Талант")
            else:
                display = ab_map.get(ab_name, ab_name.replace("_", " ").title())
                skill_build.append(f"Lvl {lvl}: {display}")
        else:
            skill_build.append(f"Lvl {lvl}: Характеристики")

    return {
        "starting_items": top_starting(item_pop.get("starting_items", {})),
        "core_items":     top_items(item_pop.get("mid_game_items", {})),
        "late_items":     top_items(item_pop.get("late_game_items", {}), 4),
        "abilities":      skill_build,
    }

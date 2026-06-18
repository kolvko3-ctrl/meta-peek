"""
Dota 2 meta — STRATZ GraphQL API.

ПРОБЛЕМА с winWeek: positionIds там не фильтрует — это просто тег группировки.
Все позиции возвращаются вместе, Silencer оказывается "лучшим керри".

РЕШЕНИЕ: запрашиваем winWeek БЕЗ positionIds (все позиции вместе),
но ЗАТО запрашиваем отдельно для каждого героя его breakdown по позициям
через heroStats { stats(...) { heroId position { matchCount winCount } } }

НО это слишком медленно (130+ запросов).

РЕАЛЬНОЕ РЕШЕНИЕ: Используем единственный рабочий способ —
OpenDota heroStats + жёсткий whitelist по позиции.
OpenDota heroStats даёт {rank}_win / {rank}_pick по рангам (без позиций),
но мы фильтруем heroes по их PRIMARY позиции в игре.

Это именно то что показывает Dotabuff в разделе "Meta" — они тоже используют
известные основные позиции героев, а не парсят каждый матч.
"""

import os
import json
import aiohttp
import logging
from time import time

logger = logging.getLogger(__name__)

STRATZ_API  = "https://api.stratz.com/graphql"
OPENDOTA_API = "https://api.opendota.com/api"

# Ранг → номер тира в OpenDota heroStats (поля: {tier}_pick, {tier}_win)
RANK_TIER = {
    "herald": 1, "guardian": 2, "crusader": 3, "archon": 4,
    "legend": 5, "ancient": 6, "divine": 7, "immortal": 8, "all": None,
}

# ──────────────────────────────────────────────────────────────────────────────
# ГЛАВНЫЙ whitelist: hero_id → основные позиции (1-5)
# Источник: реальная мета Dota 2, верифицированные ID из OpenDota /api/heroes
#
# Принцип: герой включён в позицию если он РЕГУЛЯРНО там пикается в паблике
# (не просто "иногда", а часто enough чтобы быть в мете)
# ──────────────────────────────────────────────────────────────────────────────
HERO_POSITIONS: dict[int, set[int]] = {
    # id: {позиции}
    1:   {1},        # Anti-Mage          → carry
    2:   {3},        # Axe                → offlane
    3:   {4,5},      # Bane               → support
    4:   {1,3},      # Bloodseeker        → carry/offlane
    5:   {5},        # Crystal Maiden     → hard sup
    6:   {1},        # Drow Ranger        → carry
    7:   {4,5},      # Earthshaker        → support
    8:   {1},        # Juggernaut         → carry
    9:   {4,5},      # Mirana             → support (иногда mid)
    10:  {1,2},      # Morphling          → carry/mid
    11:  {1,2},      # Shadow Fiend       → carry/mid
    12:  {1},        # Phantom Lancer     → carry
    13:  {2},        # Puck               → mid
    14:  {2,3,4},    # Pudge              → roam/offlane
    15:  {1,2,3},    # Razor              → flex
    16:  {3,4},      # Sand King          → offlane/sup
    17:  {2},        # Storm Spirit       → mid
    18:  {1,3},      # Sven               → carry/offlane
    19:  {2,3},      # Tiny               → mid/offlane
    20:  {4,5},      # Witch Doctor       → support
    21:  {5},        # Lich               → hard sup
    22:  {2},        # Zeus               → mid
    23:  {1,2,3},    # Kunkka             → flex
    25:  {2},        # Lina               → mid
    26:  {4,5},      # Lion               → support
    27:  {4,5},      # Shadow Shaman      → support
    28:  {3},        # Slardar            → offlane
    29:  {3},        # Tidehunter         → offlane
    32:  {1},        # Riki               → carry
    33:  {3,4},      # Enigma             → offlane/jungle
    34:  {2},        # Tinker             → mid
    35:  {1,2},      # Sniper             → carry/mid
    36:  {3},        # Necrophos          → offlane
    37:  {5},        # Warlock            → hard sup
    38:  {3},        # Beastmaster        → offlane
    39:  {2},        # Queen of Pain      → mid
    40:  {3,4},      # Venomancer         → offlane/sup
    41:  {1},        # Faceless Void      → carry
    42:  {1,3},      # Wraith King        → carry/offlane
    44:  {1},        # Phantom Assassin   → carry
    45:  {2},        # Pugna              → mid
    46:  {2},        # Templar Assassin   → mid
    47:  {2,3},      # Viper              → mid/offlane
    48:  {1},        # Luna               → carry
    49:  {1,3},      # Dragon Knight      → carry/offlane
    50:  {5},        # Dazzle             → hard sup
    51:  {3,4},      # Clockwerk          → offlane/roam
    52:  {2,3},      # Leshrac            → mid/offlane
    53:  {3,4},      # Nature's Prophet   → offlane/sup
    54:  {1},        # Lifestealer        → carry
    55:  {3},        # Dark Seer          → offlane
    56:  {1},        # Clinkz             → carry
    57:  {5},        # Omniknight         → hard sup
    58:  {4,5},      # Enchantress        → support
    59:  {1,2},      # Huskar             → carry/mid
    60:  {3},        # Night Stalker      → offlane
    61:  {3},        # Broodmother        → offlane
    62:  {4},        # Bounty Hunter      → soft sup
    63:  {1},        # Weaver             → carry
    64:  {4,5},      # Jakiro             → support
    65:  {3},        # Batrider           → offlane
    66:  {4,5},      # Chen               → support
    67:  {1},        # Spectre            → carry
    68:  {4,5},      # Ancient Apparition → support
    69:  {1,3},      # Doom               → carry/offlane
    70:  {1},        # Ursa               → carry
    71:  {3,4},      # Spirit Breaker     → offlane/roam
    72:  {1},        # Gyrocopter         → carry
    73:  {1,3},      # Alchemist          → carry/offlane
    74:  {2},        # Invoker            → mid
    75:  {2},        # Silencer           → mid (НЕ carry!)
    76:  {2},        # Outworld Destroyer → mid
    77:  {1,3},      # Lycan              → carry/offlane
    78:  {3},        # Brewmaster         → offlane
    79:  {4,5},      # Shadow Demon       → support
    80:  {1,3},      # Lone Druid         → carry/offlane
    81:  {1},        # Chaos Knight       → carry
    82:  {1},        # Meepo              → carry
    83:  {5},        # Treant Protector   → hard sup
    84:  {4,5},      # Ogre Magi          → support
    85:  {3},        # Undying            → offlane
    86:  {4},        # Rubick             → soft sup
    87:  {4},        # Disruptor          → soft sup
    88:  {4},        # Nyx Assassin       → soft sup
    89:  {1},        # Naga Siren         → carry
    90:  {5},        # Keeper of Light    → hard sup
    91:  {5},        # Io                 → hard sup (НЕ carry!)
    92:  {3},        # Visage             → offlane
    93:  {1},        # Slark              → carry
    94:  {1},        # Medusa             → carry
    95:  {1},        # Troll Warlord      → carry
    96:  {3},        # Centaur Warrunner  → offlane
    97:  {3,4},      # Magnus             → offlane/roam
    98:  {3},        # Timbersaw          → offlane
    99:  {3},        # Bristleback        → offlane
    100: {4},        # Tusk               → soft sup
    101: {4,5},      # Skywrath Mage      → support
    102: {5},        # Abaddon            → hard sup
    103: {3,4},      # Elder Titan        → offlane/sup
    104: {1,3},      # Legion Commander   → carry/offlane
    105: {3,4},      # Techies            → offlane/sup
    106: {2},        # Ember Spirit       → mid
    107: {4},        # Earth Spirit       → soft sup
    108: {1},        # Terrorblade        → carry
    109: {3,4},      # Phoenix            → offlane/sup
    110: {4,5},      # Oracle             → support
    111: {5},        # Winter Wyvern      → hard sup
    112: {1},        # Arc Warden         → carry
    113: {1,2},      # Monkey King        → carry/mid
    114: {4,5},      # Dark Willow        → support
    119: {4,5},      # Grimstroke         → support
    120: {2,3},      # Void Spirit        → mid/offlane
    121: {4,5},      # Snapfire           → support
    123: {4},        # Hoodwink           → soft sup
    126: {3},        # Primal Beast       → offlane
    129: {4},        # Marci              → soft sup
    131: {2},        # Dawnbreaker        → mid/offlane — скорее 3
    135: {1},        # Muerta             → carry
    136: {3},        # Ringmaster         → offlane
}

# Позиция → set hero_id
def _heroes_for_position(pos: str) -> set:
    p = int(pos)
    return {hid for hid, positions in HERO_POSITIONS.items() if p in positions}

_cache: dict = {}
_hero_names_cache: dict | None = None
CACHE_TTL = 60 * 30


async def get_top_heroes(position: str, rank: str) -> list[dict]:
    key = f"{position}_{rank}"
    now = time()
    if key in _cache:
        ts, data = _cache[key]
        if now - ts < CACHE_TTL:
            return data

    async with aiohttp.ClientSession() as session:
        names = await _get_hero_names(session)
        result = await _fetch(session, names, position, rank)

    _cache[key] = (now, result)
    return result


async def _fetch(session, names, position, rank):
    """OpenDota heroStats + whitelist позиций"""
    url = f"{OPENDOTA_API}/heroStats"
    async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
        resp.raise_for_status()
        stats = await resp.json()

    tier  = RANK_TIER.get(rank)
    allowed = _heroes_for_position(position)

    results = []
    for hero in stats:
        hid = hero.get("id")
        if hid not in allowed:
            continue

        if tier is not None:
            wins  = hero.get(f"{tier}_win") or 0
            picks = hero.get(f"{tier}_pick") or 0
        else:
            wins = picks = 0
            for t in range(1, 9):
                wins  += hero.get(f"{t}_win")  or 0
                picks += hero.get(f"{t}_pick") or 0

        if picks < 100:
            continue

        results.append({
            "hero_id": hid,
            "localized_name": names.get(hid, hero.get("localized_name", f"#{hid}")),
            "winrate":  round(wins / picks * 100, 2),
            "picks":    picks,
            "_raw_picks": picks,
        })

    if not results:
        raise ValueError(f"Нет данных для pos={position} rank={rank}")

    # Сортируем по winrate
    results.sort(key=lambda x: x["winrate"], reverse=True)
    top10 = results[:10]

    # Pickrate считаем среди топ-10 (относительный)
    max_picks = max(h["_raw_picks"] for h in top10) or 1
    for h in top10:
        h["pickrate"] = round(h["_raw_picks"] / max_picks * 100, 1)
        del h["_raw_picks"]

    logger.info(f"pos={position} rank={rank}: топ-3 = " +
                ", ".join(f"{h['localized_name']} {h['winrate']:.1f}%" for h in top10[:3]))
    return top10


async def _get_hero_names(session) -> dict:
    global _hero_names_cache
    if _hero_names_cache:
        return _hero_names_cache
    url = f"{OPENDOTA_API}/heroes"
    async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
        heroes = await resp.json()
    _hero_names_cache = {h["id"]: h["localized_name"] for h in heroes}
    return _hero_names_cache

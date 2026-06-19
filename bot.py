import os
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, ContextTypes,
)
from dotabuff import get_top_heroes
from builds import get_hero_build

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

POSITION_NAMES  = {"1":"Carry","2":"Mid","3":"Offlane","4":"Soft Support","5":"Hard Support"}
POSITION_EMOJIS = {"1":"⚔️","2":"🔮","3":"🛡️","4":"🎯","5":"💚"}
RANK_NAMES  = {
    "herald":"Herald","guardian":"Guardian","crusader":"Crusader","archon":"Archon",
    "legend":"Legend","ancient":"Ancient","divine":"Divine","immortal":"Immortal","all":"Все ранги",
}
RANK_EMOJIS = {
    "herald":"⚪","guardian":"🟢","crusader":"🟡","archon":"🟠",
    "legend":"🔵","ancient":"🟣","divine":"💠","immortal":"👑","all":"🌍",
}
MEDALS = ["🥇","🥈","🥉","4.","5.","6.","7.","8.","9.","🔟"]

# ─── Keyboards ────────────────────────────────────────────────────────────────

def kb_positions() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⚔️ Carry (1)", callback_data="pos_1"),
         InlineKeyboardButton("🔮 Mid (2)",   callback_data="pos_2")],
        [InlineKeyboardButton("🛡️ Offlane (3)",  callback_data="pos_3"),
         InlineKeyboardButton("🎯 Soft Sup (4)", callback_data="pos_4")],
        [InlineKeyboardButton("💚 Hard Sup (5)", callback_data="pos_5")],
    ])

def kb_ranks(position: str) -> InlineKeyboardMarkup:
    p = position
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🌍 Все ранги", callback_data=f"rk_{p}_all"),
         InlineKeyboardButton("👑 Immortal",  callback_data=f"rk_{p}_immortal")],
        [InlineKeyboardButton("💠 Divine",    callback_data=f"rk_{p}_divine"),
         InlineKeyboardButton("🟣 Ancient",   callback_data=f"rk_{p}_ancient")],
        [InlineKeyboardButton("🔵 Legend",    callback_data=f"rk_{p}_legend"),
         InlineKeyboardButton("🟠 Archon",    callback_data=f"rk_{p}_archon")],
        [InlineKeyboardButton("🟡 Crusader",  callback_data=f"rk_{p}_crusader"),
         InlineKeyboardButton("🟢 Guardian",  callback_data=f"rk_{p}_guardian")],
        [InlineKeyboardButton("◀️ Назад",     callback_data="menu")],
    ])

def kb_results(position: str, rank: str, heroes: list) -> InlineKeyboardMarkup:
    rows = []
    hero_btns = []
    for h in heroes:
        name  = h["localized_name"]
        label = name if len(name) <= 15 else name[:14] + "…"
        hero_btns.append(
            InlineKeyboardButton(
                f"📖 {label}",
                callback_data=f"bd_{h['hero_id']}_{position}_{rank}"
            )
        )
    for i in range(0, len(hero_btns), 2):
        rows.append(hero_btns[i:i+2])
    rows.append([
        InlineKeyboardButton("🔄 Обновить",     callback_data=f"rk_{position}_{rank}"),
        InlineKeyboardButton("◀️ Сменить ранг", callback_data=f"pos_{position}"),
    ])
    rows.append([InlineKeyboardButton("🏠 Главное меню", callback_data="menu")])
    return InlineKeyboardMarkup(rows)

def kb_build_back(position: str, rank: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("◀️ Назад к топу", callback_data=f"rk_{position}_{rank}"),
        InlineKeyboardButton("🏠 Меню",         callback_data="menu"),
    ]])

# ─── Handlers ─────────────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = update.effective_user.first_name
    await update.message.reply_text(
        f"👋 Привет, <b>{name}</b>!\n\n"
        "🎮 <b>Dota 2 Meta Bot</b>\n"
        "Топ-10 героев по позиции + билды из реальных матчей.\n\n"
        "Выбери свою позицию 👇",
        parse_mode="HTML", reply_markup=kb_positions(),
    )

async def meta_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎮 <b>Выбери позицию:</b>", parse_mode="HTML", reply_markup=kb_positions()
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "ℹ️ <b>Как пользоваться:</b>\n\n"
        "1️⃣ Выбери позицию\n"
        "2️⃣ Выбери ранг\n"
        "3️⃣ Смотри топ-10 героев\n"
        "4️⃣ Нажми на героя → получи билд!\n\n"
        "<b>В билде:</b>\n"
        "🛍 Стартовые айтемы\n"
        "⚔️ Корневые айтемы (core)\n"
        "💎 Поздние айтемы\n"
        "🎯 Скиллбилд (уровни 1–7)\n\n"
        "Данные: OpenDota API • Кэш: 30 мин",
        parse_mode="HTML",
    )

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data  = query.data
    logger.info(f"Callback: {data!r}")

    # ── Главное меню
    if data == "menu":
        await query.edit_message_text(
            "🎮 <b>Выбери позицию:</b>", parse_mode="HTML", reply_markup=kb_positions()
        )
        return

    # ── Выбор позиции  →  "pos_1" .. "pos_5"
    if data.startswith("pos_"):
        position = data[4:]  # "1".."5"
        await query.edit_message_text(
            f"{POSITION_EMOJIS[position]} <b>{POSITION_NAMES[position]} (Pos {position})</b>\n\n"
            "Выбери ранг 👇",
            parse_mode="HTML", reply_markup=kb_ranks(position),
        )
        return

    # ── Выбор ранга  →  "rk_1_immortal", "rk_2_all", etc.
    if data.startswith("rk_"):
        _, position, rank = data.split("_", 2)  # rk | 1 | immortal
        await query.edit_message_text(
            f"{POSITION_EMOJIS[position]} <b>{POSITION_NAMES[position]}</b>  |  "
            f"{RANK_EMOJIS.get(rank,'🌍')} <b>{RANK_NAMES.get(rank, rank)}</b>\n\n"
            "⏳ Загружаю данные из OpenDota...",
            parse_mode="HTML",
        )
        try:
            heroes = await get_top_heroes(position, rank)
            text   = _format_top(heroes, position, rank)
            kb     = kb_results(position, rank, heroes)
        except Exception as e:
            logger.error(f"get_top_heroes: {e}", exc_info=True)
            text = (
                f"❌ <b>Ошибка загрузки данных:</b>\n"
                f"<code>{str(e)[:300]}</code>"
            )
            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton("🔄 Повторить", callback_data=data),
                InlineKeyboardButton("🏠 Меню",      callback_data="menu"),
            ]])
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=kb)
        return

    # ── Билд героя  →  "bd_{hero_id}_{position}_{rank}"
    if data.startswith("bd_"):
        _, hid_str, position, rank = data.split("_", 3)
        hero_id = int(hid_str)

        await query.edit_message_text("⏳ Загружаю билд...", parse_mode="HTML")
        try:
            heroes    = await get_top_heroes(position, rank)
            hero      = next((h for h in heroes if h["hero_id"] == hero_id), None)
            hero_name = hero["localized_name"] if hero else f"Hero #{hero_id}"
            internal  = _internal_name(hero_id)
            build     = await get_hero_build(hero_id, internal)
            text      = _format_build(hero_name, build, position, rank)
        except Exception as e:
            logger.error(f"get_hero_build: {e}", exc_info=True)
            text = (
                f"❌ <b>Ошибка загрузки билда:</b>\n"
                f"<code>{str(e)[:300]}</code>"
            )
        await query.edit_message_text(
            text, parse_mode="HTML",
            reply_markup=kb_build_back(position, rank),
        )
        return

    logger.warning(f"Неизвестный callback: {data!r}")

# ─── Formatting ───────────────────────────────────────────────────────────────

def _wr_bar(wr: float) -> str:
    if wr >= 57: return "🔥🔥🔥"
    if wr >= 54: return "🔥🔥"
    if wr >= 51: return "🔥"
    if wr >= 49: return "⚖️"
    return "📉"

def _format_top(heroes: list, position: str, rank: str) -> str:
    lines = [
        f"{POSITION_EMOJIS[position]} <b>Топ героев — {POSITION_NAMES[position]} (Pos {position})</b>",
        f"{RANK_EMOJIS.get(rank,'🌍')} Ранг: <b>{RANK_NAMES.get(rank, rank)}</b>",
        "━━━━━━━━━━━━━━━━━━━━",
    ]
    for i, h in enumerate(heroes):
        medal = MEDALS[i] if i < len(MEDALS) else f"{i+1}."
        lines.append(
            f"{medal} <b>{h['localized_name']}</b>\n"
            f"   📈 WR: <b>{h['winrate']:.1f}%</b> {_wr_bar(h['winrate'])}   "
            f"🎯 Pick: {h.get('pickrate',0):.1f}%"
        )
    lines += [
        "━━━━━━━━━━━━━━━━━━━━",
        "📖 <i>Нажми на героя ниже чтобы посмотреть билд</i>",
    ]
    return "\n".join(lines)

def _format_build(hero_name: str, build: dict, position: str, rank: str) -> str:
    def fmt_list(items: list) -> str:
        return "\n".join(f"  • {item}" for item in items) if items else "  —"

    lines = [
        f"📖 <b>Билд — {hero_name}</b>",
        f"{POSITION_EMOJIS[position]} {POSITION_NAMES[position]}  |  "
        f"{RANK_EMOJIS.get(rank,'🌍')} {RANK_NAMES.get(rank, rank)}",
        "━━━━━━━━━━━━━━━━━━━━",
        "",
        "🛍 <b>Старт (0–2 мин):</b>",
        fmt_list(build.get("starting_items", [])),
        "",
        "⚔️ <b>Core айтемы (10–20 мин):</b>",
        fmt_list(build.get("core_items", [])),
        "",
        "💎 <b>Late game (25+ мин):</b>",
        fmt_list(build.get("late_items", [])),
        "",
        "🎯 <b>Скиллбилд (уровни 1–7):</b>",
        *([f"  {ab}" for ab in build.get("abilities", [])] or ["  —"]),
        "",
        "━━━━━━━━━━━━━━━━━━━━",
        "🕐 <i>Данные: OpenDota API (реальные матчи)</i>",
    ]
    return "\n".join(lines)

def _internal_name(hero_id: int) -> str:
    TABLE = {
        1:"antimage",2:"axe",3:"bane",4:"bloodseeker",5:"crystal_maiden",
        6:"drow_ranger",7:"earthshaker",8:"juggernaut",9:"mirana",10:"morphling",
        11:"nevermore",12:"phantom_lancer",13:"puck",14:"pudge",15:"razor",
        16:"sand_king",17:"storm_spirit",18:"sven",19:"tiny",20:"witch_doctor",
        21:"lich",22:"zuus",23:"kunkka",25:"lina",26:"lion",27:"shadow_shaman",
        28:"slardar",29:"tidehunter",32:"riki",33:"enigma",34:"tinker",
        35:"sniper",36:"necrolyte",37:"warlock",38:"beastmaster",39:"queenofpain",
        40:"venomancer",41:"faceless_void",42:"skeleton_king",44:"phantom_assassin",
        45:"pugna",46:"templar_assassin",47:"viper",48:"luna",49:"dragon_knight",
        50:"dazzle",51:"rattletrap",52:"leshrac",53:"furion",54:"life_stealer",
        55:"dark_seer",56:"clinkz",57:"omniknight",58:"enchantress",59:"huskar",
        60:"night_stalker",61:"broodmother",62:"bounty_hunter",63:"weaver",
        64:"jakiro",65:"batrider",66:"chen",67:"spectre",68:"ancient_apparition",
        69:"doom_bringer",70:"ursa",71:"spirit_breaker",72:"gyrocopter",
        73:"alchemist",74:"invoker",75:"silencer",76:"obsidian_destroyer",
        77:"lycan",78:"brewmaster",79:"shadow_demon",80:"lone_druid",
        81:"chaos_knight",82:"meepo",83:"treant",84:"ogre_magi",85:"undying",
        86:"rubick",87:"disruptor",88:"nyx_assassin",89:"naga_siren",
        90:"keeper_of_the_light",91:"wisp",92:"visage",93:"slark",94:"medusa",
        95:"troll_warlord",96:"centaur",97:"magnataur",98:"shredder",
        99:"bristleback",100:"tusk",101:"skywrath_mage",102:"abaddon",
        103:"elder_titan",104:"legion_commander",105:"techies",106:"ember_spirit",
        107:"earth_spirit",108:"terrorblade",109:"phoenix",110:"oracle",
        111:"winter_wyvern",112:"arc_warden",113:"monkey_king",114:"dark_willow",
        119:"grimstroke",120:"void_spirit",121:"snapfire",123:"hoodwink",
        126:"primal_beast",129:"marci",135:"muerta",
    }
    return TABLE.get(hero_id, f"hero_{hero_id}")

# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise ValueError("TELEGRAM_BOT_TOKEN не задан!")
    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("meta",  meta_command))
    app.add_handler(CommandHandler("help",  help_command))
    app.add_handler(CallbackQueryHandler(handle_callback))
    logger.info("Бот запущен ✅")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()

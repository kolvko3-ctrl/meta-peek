import os
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)
from dotabuff import get_top_heroes

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

POSITION_NAMES = {
    "1": "Carry",
    "2": "Mid",
    "3": "Offlane",
    "4": "Soft Support",
    "5": "Hard Support",
}

POSITION_EMOJIS = {
    "1": "⚔️",
    "2": "🔮",
    "3": "🛡️",
    "4": "🎯",
    "5": "💚",
}

RANK_NAMES = {
    "herald":   "Herald",
    "guardian": "Guardian",
    "crusader": "Crusader",
    "archon":   "Archon",
    "legend":   "Legend",
    "ancient":  "Ancient",
    "divine":   "Divine",
    "immortal": "Immortal",
    "all":      "Все ранги",
}

RANK_EMOJIS = {
    "herald":   "⚪",
    "guardian": "🟢",
    "crusader": "🟡",
    "archon":   "🟠",
    "legend":   "🔵",
    "ancient":  "🟣",
    "divine":   "💠",
    "immortal": "👑",
    "all":      "🌍",
}


# ─── Keyboards ────────────────────────────────────────────────────────────────

def build_position_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("⚔️ Carry (1)", callback_data="pos_1"),
            InlineKeyboardButton("🔮 Mid (2)",   callback_data="pos_2"),
        ],
        [
            InlineKeyboardButton("🛡️ Offlane (3)",    callback_data="pos_3"),
            InlineKeyboardButton("🎯 Soft Sup (4)", callback_data="pos_4"),
        ],
        [
            InlineKeyboardButton("💚 Hard Sup (5)", callback_data="pos_5"),
        ],
    ])


def build_rank_keyboard(position: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🌍 Все ранги",  callback_data=f"rank_{position}_all"),
            InlineKeyboardButton("👑 Immortal",   callback_data=f"rank_{position}_immortal"),
        ],
        [
            InlineKeyboardButton("💠 Divine",     callback_data=f"rank_{position}_divine"),
            InlineKeyboardButton("🟣 Ancient",    callback_data=f"rank_{position}_ancient"),
        ],
        [
            InlineKeyboardButton("🔵 Legend",     callback_data=f"rank_{position}_legend"),
            InlineKeyboardButton("🟠 Archon",     callback_data=f"rank_{position}_archon"),
        ],
        [
            InlineKeyboardButton("🟡 Crusader",   callback_data=f"rank_{position}_crusader"),
            InlineKeyboardButton("🟢 Guardian",   callback_data=f"rank_{position}_guardian"),
        ],
        [
            InlineKeyboardButton("◀️ Назад к позициям", callback_data="back_to_positions"),
        ],
    ])


def build_result_keyboard(position: str, rank: str, cb_data: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔄 Обновить",         callback_data=cb_data),
            InlineKeyboardButton("◀️ Сменить ранг",     callback_data=f"pos_{position}"),
        ],
        [
            InlineKeyboardButton("🏠 Главное меню",      callback_data="back_to_positions"),
        ],
    ])


# ─── Handlers ─────────────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    name = update.effective_user.first_name
    text = (
        f"👋 Привет, <b>{name}</b>!\n\n"
        "🎮 <b>Dota 2 Meta Bot</b>\n"
        "Показываю топ-10 героев по позиции с актуальным winrate и pickrate.\n\n"
        "Данные берутся из <b>OpenDota</b> в реальном времени.\n\n"
        "Выбери свою позицию 👇"
    )
    await update.message.reply_text(text, parse_mode="HTML",
                                    reply_markup=build_position_keyboard())


async def meta_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "🎮 <b>Выбери позицию:</b>",
        parse_mode="HTML",
        reply_markup=build_position_keyboard(),
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (
        "ℹ️ <b>Как пользоваться:</b>\n\n"
        "/start или /meta — открыть бота\n\n"
        "<b>Шаги:</b>\n"
        "1️⃣ Выбери позицию (1–5)\n"
        "2️⃣ Выбери ранг\n"
        "3️⃣ Получи топ-10 героев!\n\n"
        "<b>Что показывает бот:</b>\n"
        "📈 Winrate — % побед на этой позиции\n"
        "🎯 Pickrate — % от всех пиков в ранге\n"
        "🔥 Индикатор силы героя\n\n"
        "Данные: OpenDota API • Кэш: 30 мин"
    )
    await update.message.reply_text(text, parse_mode="HTML")


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    data = query.data

    # ── Main menu
    if data == "back_to_positions":
        await query.edit_message_text(
            "🎮 <b>Выбери позицию:</b>",
            parse_mode="HTML",
            reply_markup=build_position_keyboard(),
        )
        return

    # ── Position selected → show rank picker
    if data.startswith("pos_"):
        position = data[4:]
        emoji = POSITION_EMOJIS[position]
        name  = POSITION_NAMES[position]
        await query.edit_message_text(
            f"{emoji} <b>{name} (Pos {position})</b>\n\nВыбери ранг 👇",
            parse_mode="HTML",
            reply_markup=build_rank_keyboard(position),
        )
        return

    # ── Rank selected → fetch and show heroes
    if data.startswith("rank_"):
        _, position, rank = data.split("_", 2)
        pos_emoji   = POSITION_EMOJIS[position]
        pos_name    = POSITION_NAMES[position]
        rank_emoji  = RANK_EMOJIS.get(rank, "🌍")
        rank_name   = RANK_NAMES.get(rank, rank.capitalize())

        # Show loading
        await query.edit_message_text(
            f"{pos_emoji} <b>{pos_name}</b>  |  {rank_emoji} <b>{rank_name}</b>\n\n"
            "⏳ Загружаю данные из OpenDota...",
            parse_mode="HTML",
        )

        try:
            heroes = await get_top_heroes(position, rank)
            text = format_response(heroes, position, rank)
        except Exception as e:
            import traceback
            err_detail = traceback.format_exc()
            logger.error(f"Ошибка получения данных:\n{err_detail}")
            # Показываем полную ошибку прямо в боте для отладки
            short = str(e)[:300]
            text = (
                f"❌ <b>Ошибка (debug):</b>\n"
                f"<code>{short}</code>\n\n"
                f"Тип: <code>{type(e).__name__}</code>"
            )

        await query.edit_message_text(
            text,
            parse_mode="HTML",
            reply_markup=build_result_keyboard(position, rank, data),
        )


# ─── Formatting ───────────────────────────────────────────────────────────────

MEDALS = ["🥇", "🥈", "🥉", "4.", "5.", "6.", "7.", "8.", "9.", "🔟"]

def wr_bar(winrate: float) -> str:
    if winrate >= 57:  return "🔥🔥🔥"
    if winrate >= 54:  return "🔥🔥"
    if winrate >= 51:  return "🔥"
    if winrate >= 49:  return "⚖️"
    return "📉"

def format_response(heroes: list, position: str, rank: str) -> str:
    pos_emoji  = POSITION_EMOJIS[position]
    pos_name   = POSITION_NAMES[position]
    rank_emoji = RANK_EMOJIS.get(rank, "🌍")
    rank_name  = RANK_NAMES.get(rank, rank.capitalize())

    lines = [
        f"{pos_emoji} <b>Топ героев — {pos_name} (Pos {position})</b>",
        f"{rank_emoji} Ранг: <b>{rank_name}</b>",
        "━━━━━━━━━━━━━━━━━━━━",
    ]

    if not heroes:
        lines.append("😔 Данных недостаточно для этого фильтра.")
    else:
        for i, h in enumerate(heroes):
            medal    = MEDALS[i] if i < len(MEDALS) else f"{i+1}."
            name     = h["localized_name"]
            winrate  = h["winrate"]
            pickrate = h.get("pickrate", 0)
            bar      = wr_bar(winrate)

            lines.append(
                f"{medal} <b>{name}</b>\n"
                f"   📈 WR: <b>{winrate:.1f}%</b> {bar}   🎯 Pick: {pickrate:.1f}%"
            )

    lines += [
        "━━━━━━━━━━━━━━━━━━━━",
        "🕐 <i>Источник: OpenDota API</i>",
    ]
    return "\n".join(lines)


# ─── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise ValueError("Задай переменную окружения TELEGRAM_BOT_TOKEN")

    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("meta",  meta_command))
    app.add_handler(CommandHandler("help",  help_command))
    app.add_handler(CallbackQueryHandler(handle_callback))

    logger.info("Бот запущен ✅")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()

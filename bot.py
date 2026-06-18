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
    "1": "Carry (Pos 1)",
    "2": "Mid (Pos 2)",
    "3": "Offlane (Pos 3)",
    "4": "Soft Support (Pos 4)",
    "5": "Hard Support (Pos 5)",
}

POSITION_EMOJIS = {
    "1": "⚔️",
    "2": "🔮",
    "3": "🛡️",
    "4": "🎯",
    "5": "💚",
}

RANK_EMOJIS = {
    "herald": "🥉",
    "guardian": "🥈",
    "crusader": "🏅",
    "archon": "🌟",
    "legend": "💫",
    "ancient": "🔶",
    "divine": "💠",
    "immortal": "👑",
    "all": "🌍",
}


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    welcome_text = (
        f"👋 Привет, <b>{user.first_name}</b>!\n\n"
        "🎮 <b>Dota 2 Meta Bot</b> — твой гид по актуальной мете!\n\n"
        "📊 Я покажу топ-10 героев на любую позицию прямо сейчас, "
        "используя данные OpenDota.\n\n"
        "Выбери свою позицию 👇"
    )
    keyboard = build_position_keyboard()
    await update.message.reply_text(
        welcome_text, parse_mode="HTML", reply_markup=keyboard
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    help_text = (
        "ℹ️ <b>Как пользоваться ботом:</b>\n\n"
        "1️⃣ Нажми /start или /meta\n"
        "2️⃣ Выбери свою позицию (1–5)\n"
        "3️⃣ Выбери ранг (или все ранги)\n"
        "4️⃣ Получи топ-10 героев с актуальной статистикой!\n\n"
        "<b>Что показывает бот:</b>\n"
        "• 🏆 Место в топе\n"
        "• 📈 Winrate (% побед)\n"
        "• 🎯 Pickrate (% пиков)\n\n"
        "Данные обновляются каждые несколько часов через OpenDota API."
    )
    await update.message.reply_text(help_text, parse_mode="HTML")


async def meta_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    keyboard = build_position_keyboard()
    await update.message.reply_text(
        "🎮 <b>Выбери свою позицию:</b>",
        parse_mode="HTML",
        reply_markup=keyboard,
    )


def build_position_keyboard() -> InlineKeyboardMarkup:
    keyboard = [
        [
            InlineKeyboardButton("⚔️ Carry (Pos 1)", callback_data="pos_1"),
            InlineKeyboardButton("🔮 Mid (Pos 2)", callback_data="pos_2"),
        ],
        [
            InlineKeyboardButton("🛡️ Offlane (Pos 3)", callback_data="pos_3"),
            InlineKeyboardButton("🎯 Soft Sup (Pos 4)", callback_data="pos_4"),
        ],
        [
            InlineKeyboardButton("💚 Hard Sup (Pos 5)", callback_data="pos_5"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)


def build_rank_keyboard(position: str) -> InlineKeyboardMarkup:
    keyboard = [
        [
            InlineKeyboardButton("🌍 Все ранги", callback_data=f"rank_{position}_all"),
            InlineKeyboardButton("👑 Immortal", callback_data=f"rank_{position}_immortal"),
        ],
        [
            InlineKeyboardButton("💠 Divine", callback_data=f"rank_{position}_divine"),
            InlineKeyboardButton("🔶 Ancient", callback_data=f"rank_{position}_ancient"),
        ],
        [
            InlineKeyboardButton("💫 Legend", callback_data=f"rank_{position}_legend"),
            InlineKeyboardButton("🌟 Archon", callback_data=f"rank_{position}_archon"),
        ],
        [
            InlineKeyboardButton("◀️ Назад", callback_data="back_to_positions"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "back_to_positions":
        keyboard = build_position_keyboard()
        await query.edit_message_text(
            "🎮 <b>Выбери свою позицию:</b>",
            parse_mode="HTML",
            reply_markup=keyboard,
        )
        return

    if data.startswith("pos_"):
        position = data.split("_")[1]
        pos_name = POSITION_NAMES[position]
        pos_emoji = POSITION_EMOJIS[position]
        keyboard = build_rank_keyboard(position)
        await query.edit_message_text(
            f"{pos_emoji} <b>{pos_name}</b>\n\nТеперь выбери свой ранг 👇",
            parse_mode="HTML",
            reply_markup=keyboard,
        )
        return

    if data.startswith("rank_"):
        parts = data.split("_")
        position = parts[1]
        rank = parts[2]

        pos_name = POSITION_NAMES[position]
        pos_emoji = POSITION_EMOJIS[position]
        rank_emoji = RANK_EMOJIS.get(rank, "🌍")

        loading_text = (
            f"{pos_emoji} <b>{pos_name}</b> | {rank_emoji} <b>{rank.capitalize()}</b>\n\n"
            "⏳ Загружаю данные с OpenDota..."
        )
        await query.edit_message_text(loading_text, parse_mode="HTML")

        try:
            heroes = await get_top_heroes(position, rank)
            response = format_heroes_response(heroes, position, rank)
        except Exception as e:
            logger.error(f"Error fetching heroes: {e}")
            response = "❌ Ошибка при получении данных. Попробуй чуть позже."

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("🔄 Обновить", callback_data=data),
                InlineKeyboardButton("◀️ Назад", callback_data=f"pos_{position}"),
            ],
            [
                InlineKeyboardButton("🏠 Главное меню", callback_data="back_to_positions"),
            ],
        ])

        await query.edit_message_text(
            response, parse_mode="HTML", reply_markup=keyboard
        )


def format_heroes_response(heroes: list, position: str, rank: str) -> str:
    pos_name = POSITION_NAMES[position]
    pos_emoji = POSITION_EMOJIS[position]
    rank_emoji = RANK_EMOJIS.get(rank, "🌍")
    rank_name = rank.capitalize() if rank != "all" else "Все ранги"

    medals = ["🥇", "🥈", "🥉"]
    lines = [
        f"{pos_emoji} <b>Топ-10 героев — {pos_name}</b>",
        f"{rank_emoji} Ранг: <b>{rank_name}</b>\n",
        "━━━━━━━━━━━━━━━━━━━",
    ]

    for i, hero in enumerate(heroes[:10], start=1):
        medal = medals[i - 1] if i <= 3 else f"{i}."
        name = hero["localized_name"]
        winrate = hero["winrate"]
        pickrate = hero.get("pickrate", 0)

        wr_bar = get_winrate_bar(winrate)

        lines.append(
            f"{medal} <b>{name}</b>\n"
            f"   📈 WR: <b>{winrate:.1f}%</b> {wr_bar}  🎯 Pick: {pickrate:.1f}%"
        )

    lines.append("━━━━━━━━━━━━━━━━━━━")
    lines.append("🕐 Данные: <i>OpenDota API (реальное время)</i>")
    return "\n".join(lines)


def get_winrate_bar(winrate: float) -> str:
    if winrate >= 56:
        return "🔥🔥🔥"
    elif winrate >= 53:
        return "🔥🔥"
    elif winrate >= 51:
        return "🔥"
    elif winrate >= 49:
        return "⚖️"
    else:
        return "📉"


def main() -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise ValueError("TELEGRAM_BOT_TOKEN не задан!")

    app = Application.builder().token(token).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("meta", meta_command))
    app.add_handler(CallbackQueryHandler(handle_callback))

    logger.info("Бот запущен!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()

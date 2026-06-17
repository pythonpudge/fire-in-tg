import json
import os
from datetime import date, datetime
from typing import Optional, Dict, List

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes,
)

TOKEN = "8805888087:AAEYMCGUWQkWuytZwLCXnAbEz2K6Zd2SyW4"
DATA_FILE = "bot_data.json"

def load_data() -> dict:
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"users": {}, "pairs": {}, "activity": {}}

def save_data(data: dict):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def get_user(user_id: int) -> Optional[dict]:
    data = load_data()
    return data["users"].get(str(user_id))

def upsert_user(user_id: int, username: str):
    data = load_data()
    if str(user_id) not in data["users"]:
        data["users"][str(user_id)] = {"username": username, "created": datetime.now().isoformat()}
    else:
        data["users"][str(user_id)]["username"] = username
    save_data(data)

def get_pair_by_user(user_id: int) -> Optional[dict]:
    data = load_data()
    for pair_id, pair in data["pairs"].items():
        if user_id in [pair["user1_id"], pair["user2_id"]]:
            return pair
    return None

def user_in_pair(user_id: int) -> bool:
    return get_pair_by_user(user_id) is not None

def create_pair(user1_id: int, user2_id: int):
    data = load_data()
    pair_id = str(len(data["pairs"]) + 1)
    data["pairs"][pair_id] = {
        "user1_id": user1_id,
        "user2_id": user2_id,
        "streak": 0,
        "last_common_date": None,
    }
    save_data(data)
    return pair_id

def log_activity(user_id: int, activity_date: str):
    data = load_data()
    user_str = str(user_id)
    if user_str not in data["activity"]:
        data["activity"][user_str] = []
    if activity_date not in data["activity"][user_str]:
        data["activity"][user_str].append(activity_date)
    save_data(data)

def has_activity(user_id: int, activity_date: str) -> bool:
    data = load_data()
    return activity_date in data["activity"].get(str(user_id), [])

def update_pair_streak(pair_id: str, new_streak: int, new_date: str):
    data = load_data()
    if pair_id in data["pairs"]:
        data["pairs"][pair_id]["streak"] = new_streak
        data["pairs"][pair_id]["last_common_date"] = new_date
        save_data(data)

def main_keyboard():
    keyboard = [
        [InlineKeyboardButton("➕ Добавить друга", callback_data="add_friend")],
        [InlineKeyboardButton("🔥 Мои серии", callback_data="my_streaks")]
    ]
    return InlineKeyboardMarkup(keyboard)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    upsert_user(user.id, user.username or "unknown")
    await update.message.reply_text(
        f"Привет, {user.first_name}! 👋\n\n"
        "Этот бот помогает поддерживать ежедневную серию с другом.\n"
        "Нажмите «Добавить друга», чтобы создать пару, "
        "или «Мои серии», чтобы посмотреть статистику.",
        reply_markup=main_keyboard()
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    if query.data == "add_friend":
        await query.edit_message_text(
            "Введите @username вашего друга (без @), чтобы создать пару.\n"
            "Например: `durov`",
            parse_mode="Markdown"
        )
        context.user_data["waiting_for_friend"] = True

    elif query.data == "my_streaks":
        pair = get_pair_by_user(user_id)
        if not pair:
            await query.edit_message_text(
                "Вы пока не в паре. Нажмите «Добавить друга», чтобы создать.",
                reply_markup=main_keyboard()
            )
            return

        partner_id = pair["user2_id"] if pair["user1_id"] == user_id else pair["user1_id"]
        partner_data = get_user(partner_id)
        partner_name = partner_data["username"] if partner_data else "пользователь"

        today = date.today().isoformat()
        user_active = has_activity(user_id, today)
        partner_active = has_activity(partner_id, today)

        streak = pair["streak"]
        last_common = pair["last_common_date"]

        text = (
            f"📊 **Ваша пара с @{partner_name}**\n\n"
            f"Вы: {'✅ писал сегодня' if user_active else '❌ ещё не писал'}\n"
            f"Партнёр: {'✅ писал сегодня' if partner_active else '❌ ещё не писал'}\n\n"
        )
        if streak > 0 and last_common:
            text += f"🔥 Серия: {streak} дня(ей)\n📅 Последний общий день: {last_common}"
        else:
            text += "Пока нет совместных дней. Начните сегодня!"

        await query.edit_message_text(text, reply_markup=main_keyboard())

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = update.message.text.strip()

    if context.user_data.get("waiting_for_friend"):
        context.user_data["waiting_for_friend"] = False

        target_username = text.lstrip("@")
        data = load_data()
        target_user_id = None
        for uid, uinfo in data["users"].items():
            if uinfo.get("username", "").lower() == target_username.lower():
                target_user_id = int(uid)
                break

        if target_user_id is None:
            await update.message.reply_text(
                f"Пользователь @{target_username} не найден. "
                "Попросите его сначала написать /start боту.",
                reply_markup=main_keyboard()
            )
            return

        if target_user_id == user.id:
            await update.message.reply_text("Нельзя создать пару с самим собой.")
            return

        if user_in_pair(user.id):
            await update.message.reply_text("Вы уже состоите в паре. Сначала разорвите её (пока нет команды).")
            return
        if user_in_pair(target_user_id):
            await update.message.reply_text("Этот пользователь уже в паре с другим.")
            return

        create_pair(user.id, target_user_id)
        await update.message.reply_text(
            f"🎉 Пара создана! Теперь вы и @{target_username} вместе.\n"
            "Пишите каждый день, чтобы серия росла! 🔥",
            reply_markup=main_keyboard()
        )
        try:
            await context.bot.send_message(
                chat_id=target_user_id,
                text="🎉 Кто-то создал с вами пару! Теперь вы вместе поддерживаете серию. Пишите каждый день! 🔥",
                reply_markup=main_keyboard()
            )
        except Exception:
            pass
        return

    upsert_user(user.id, user.username or "unknown")
    today = date.today().isoformat()
    log_activity(user.id, today)

    pair = get_pair_by_user(user.id)
    if not pair:
        await update.message.reply_text(
            "Сообщение записано ✅. Чтобы создать пару, нажмите «Добавить друга».",
            reply_markup=main_keyboard()
        )
        return

    partner_id = pair["user2_id"] if pair["user1_id"] == user.id else pair["user1_id"]
    partner_active = has_activity(partner_id, today)

    if not partner_active:
        await update.message.reply_text(
            "✅ Сообщение записано. Ваш партнёр ещё не написал сегодня. "
            "Серия увеличится, когда оба отметятся.",
            reply_markup=main_keyboard()
        )
        return

    last_common = pair["last_common_date"]
    new_streak = pair["streak"]

    if last_common is None:
        new_streak = 1
    elif last_common == (date.today() - timedelta(days=1)).isoformat():
        new_streak += 1
    elif last_common == today:
        await update.message.reply_text(
            "Вы оба уже отметились сегодня! Серия продолжается.",
            reply_markup=main_keyboard()
        )
        return
    else:
        new_streak = 1

    update_pair_streak(pair["pair_id"], new_streak, today)
        partner_name = partner_data["username"] if partner_data else "пользователь"

        today = date.today().isoformat()
        user_active = has_activity(user_id, today)
        partner_active = has_activity(partner_id, today)

        streak = pair["streak"]
        last_common = pair["last_common_date"]

        text = (
            f"📊 **Ваша пара с @{partner_name}**\n\n"
            f"Вы: {'✅ писал сегодня' if user_active else '❌ ещё не писал'}\n"
            f"Партнёр: {'✅ писал сегодня' if partner_active else '❌ ещё не писал'}\n\n"
        )
        if streak > 0 and last_common:
            text += f"🔥 Серия: {streak} дня(ей)\n📅 Последний общий день: {last_common}"
        else:
            text += "Пока нет совместных дней. Начните сегодня!"

        await query.edit_message_text(text, reply_markup=main_keyboard())

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = update.message.text.strip()

    if context.user_data.get("waiting_for_friend"):
        context.user_data["waiting_for_friend"] = False

        target_username = text.lstrip("@")
        data = load_data()
        target_user_id = None
        for uid, uinfo in data["users"].items():
            if uinfo.get("username", "").lower() == target_username.lower():
                target_user_id = int(uid)
                break

        if target_user_id is None:
            await update.message.reply_text(
                f"Пользователь @{target_username} не найден. "
                "Попросите его сначала написать /start боту.",
                reply_markup=main_keyboard()
            )
            return

        if target_user_id == user.id:
            await update.message.reply_text("Нельзя создать пару с самим собой.")
            return

        if user_in_pair(user.id):
            await update.message.reply_text("Вы уже состоите в паре. Сначала разорвите её (пока нет команды).")
            return
        if user_in_pair(target_user_id):
            await update.message.reply_text("Этот пользователь уже в паре с другим.")
            return

        create_pair(user.id, target_user_id)
        await update.message.reply_text(
            f"🎉 Пара создана! Теперь вы и @{target_username} вместе.\n"
            "Пишите каждый день, чтобы серия росла! 🔥",
            reply_markup=main_keyboard()
        )
        try:
            await context.bot.send_message(
                chat_id=target_user_id,
                text="🎉 Кто-то создал с вами пару! Теперь вы вместе поддерживаете серию. Пишите каждый день! 🔥",
                reply_markup=main_keyboard()
            )
        except Exception:
            pass
        return

    upsert_user(user.id, user.username or "unknown")
    today = date.today().isoformat()
    log_activity(user.id, today)

    pair = get_pair_by_user(user.id)
    if not pair:
        await update.message.reply_text(
            "Сообщение записано ✅. Чтобы создать пару, нажмите «Добавить друга».",
            reply_markup=main_keyboard()
        )
        return

    partner_id = pair["user2_id"] if pair["user1_id"] == user.id else pair["user1_id"]
    partner_active = has_activity(partner_id, today)

    if not partner_active:
        await update.message.reply_text(
            "✅ Сообщение записано. Ваш партнёр ещё не написал сегодня. "
            "Серия увеличится, когда оба отметятся.",
            reply_markup=main_keyboard()
        )
        return

    last_common = pair["last_common_date"]
    new_streak = pair["streak"]

    if last_common is None:
        new_streak = 1
    elif last_common == (date.today() - timedelta(days=1)).isoformat():
        new_streak += 1
    elif last_common == today:
        await update.message.reply_text(
            "Вы оба уже отметились сегодня! Серия продолжается.",
            reply_markup=main_keyboard()
        )
        return
    else:
        new_streak = 1

    update_pair_streak(pair["pair_id"], new_streak, today)
    msg = f"🔥 Оба отметились! Серия: {new_streak} дня(ей). Продолжайте!"
    await update.message.reply_text(msg, reply_markup=main_keyboard())
    try:
        await context.bot.send_message(
            chat_id=partner_id,
            text=msg,
            reply_markup=main_keyboard()
        )
    except Exception:
        pass

def main():
    application = Application.builder().token(TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("Бот запущен...")
    application.run_polling()

if __name__ == "__main__":
    main()

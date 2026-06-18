from zoneinfo import ZoneInfo
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

import pytz
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    KeyboardButton,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes,
)

import aiosqlite

TOKEN = os.getenv("TOKEN", "8805888087:AAEYMCGUWQkWuytZwLCXnAbEz2K6Zd2SyW4")
DATABASE = "streak_bot.db"
DEFAULT_TIMEZONE = "UTC"
ADMIN_ID = 123456789

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

class Database:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.conn: Optional[aiosqlite.Connection] = None

    async def connect(self):
        self.conn = await aiosqlite.connect(self.db_path)
        self.conn.row_factory = aiosqlite.Row
        await self.conn.execute("PRAGMA journal_mode=WAL")
        await self.conn.execute("PRAGMA foreign_keys=ON")
        await self._create_tables()

    async def _create_tables(self):
        await self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                timezone TEXT DEFAULT 'UTC'
            );

            CREATE TABLE IF NOT EXISTS pairs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user1_id INTEGER NOT NULL,
                user2_id INTEGER NOT NULL,
                chat_id INTEGER,
                streak_count INTEGER DEFAULT 0,
                last_msg_user1 TEXT,
                last_msg_user2 TEXT,
                start_date TEXT,
                is_active INTEGER DEFAULT 1,
                FOREIGN KEY (user1_id) REFERENCES users(user_id),
                FOREIGN KEY (user2_id) REFERENCES users(user_id)
            );

            CREATE TABLE IF NOT EXISTS daily_log (
                pair_id INTEGER,
                user_id INTEGER,
                date TEXT,
                PRIMARY KEY (pair_id, user_id, date),
                FOREIGN KEY (pair_id) REFERENCES pairs(id)
            );
        """)
        await self.conn.commit()

    async def get_user(self, user_id: int) -> Optional[dict]:
        cursor = await self.conn.execute(
            "SELECT * FROM users WHERE user_id = ?", (user_id,)
        )
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def upsert_user(self, user_id: int, username: str, timezone: str = DEFAULT_TIMEZONE):
        await self.conn.execute(
            """INSERT INTO users (user_id, username, timezone)
               VALUES (?, ?, ?)
               ON CONFLICT(user_id) DO UPDATE SET username=excluded.username""",
            (user_id, username, timezone),
        )
        await self.conn.commit()

    async def set_timezone(self, user_id: int, tz: str):
        await self.conn.execute(
            "UPDATE users SET timezone = ? WHERE user_id = ?", (tz, user_id)
        )
        await self.conn.commit()

    async def get_pair_by_chat(self, chat_id: int) -> Optional[dict]:
        cursor = await self.conn.execute(
            "SELECT * FROM pairs WHERE chat_id = ? AND is_active = 1", (chat_id,)
        )
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def create_pair(self, user1_id: int, user2_id: int, chat_id: int) -> int:
        cursor = await self.conn.execute(
            """INSERT INTO pairs (user1_id, user2_id, chat_id, start_date)
               VALUES (?, ?, ?, ?)""",
            (user1_id, user2_id, chat_id, datetime.now(timezone.utc).isoformat()),
        )
        await self.conn.commit()
        return cursor.lastrowid

    async def update_message_time(self, pair_id: int, user_id: int, timestamp: datetime):
        pair = await self.get_pair_by_id(pair_id)
        if pair is None:
            return
        if pair["user1_id"] == user_id:
            col = "last_msg_user1"
        elif pair["user2_id"] == user_id:
            col = "last_msg_user2"
        else:
            return
        await self.conn.execute(
            f"UPDATE pairs SET {col} = ? WHERE id = ?",
            (timestamp.isoformat(), pair_id),
        )
        await self.conn.commit()

    async def get_pair_by_id(self, pair_id: int) -> Optional[dict]:
        cursor = await self.conn.execute(
            "SELECT * FROM pairs WHERE id = ?", (pair_id,)
        )
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def add_daily_log(self, pair_id: int, user_id: int, date_str: str):
        try:
            await self.conn.execute(
                "INSERT OR IGNORE INTO daily_log (pair_id, user_id, date) VALUES (?, ?, ?)",
                (pair_id, user_id, date_str),
            )
            await self.conn.commit()
        except aiosqlite.IntegrityError:
            pass

    async def get_user_pairs(self, user_id: int) -> list[dict]:
        cursor = await self.conn.execute(
            """SELECT * FROM pairs
               WHERE (user1_id = ? OR user2_id = ?)
                 AND is_active = 1""",
            (user_id, user_id),
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def get_streak_info(self, pair: dict) -> Tuple[int, bool]:
        cursor = await self.conn.execute(
            "SELECT user_id, date FROM daily_log WHERE pair_id = ? ORDER BY date",
            (pair["id"],),
        )
        rows = await cursor.fetchall()
        dates1 = set()
        dates2 = set()
        for row in rows:
            if row[0] == pair["user1_id"]:
                dates1.add(row[1])
            else:
                dates2.add(row[1])
        common_days = sorted(dates1 & dates2)
        if not common_days:
            return 0, False
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
        active_today = today in common_days
        active_yesterday = yesterday in common_days
        streak_days = 0
        if active_today or active_yesterday:
            last_day = today if active_today else yesterday
            check_day = last_day
            while check_day in common_days:
                streak_days += 1
                check_day = (datetime.strptime(check_day, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
        else:
            return 0, False
        is_active = active_today
        return streak_days, is_active

    async def check_and_update_streak(self, pair_id: int, user_id: int):
        pair = await self.get_pair_by_id(pair_id)
        if not pair:
            return
        now = datetime.now(timezone.utc)
        date_str = now.strftime("%Y-%m-%d")
        await self.add_daily_log(pair_id, user_id, date_str)
        streak, is_active = await self.get_streak_info(pair)
        if streak >= 3:
            await self.conn.execute(
                "UPDATE pairs SET streak_count = ? WHERE id = ?",
                (streak, pair_id),
            )
            await self.conn.commit()
            old_streak = pair["streak_count"]
            if old_streak < 3 and streak >= 3:
                await self._notify_streak_start(pair)
            elif streak > old_streak:
                await self._notify_streak_continue(pair, streak)

    async def _notify_streak_start(self, pair: dict):
        pass

db = Database(DATABASE)

def get_user_timezone(user_id: int) -> str:
    pass

async def get_user_timezone_async(user_id: int) -> str:
    user = await db.get_user(user_id)
    return user["timezone"] if user else DEFAULT_TIMEZONE

def local_time_str(dt_utc: datetime, tz_str: str) -> str:
    if tz_str not in pytz.all_timezones:
        tz_str = DEFAULT_TIMEZONE
    local_tz = pytz.timezone(tz_str)
    local_dt = dt_utc.astimezone(local_tz)
    return local_dt.strftime("%d.%m.%Y %H:%M")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await db.upsert_user(user.id, user.username or user.first_name)
    keyboard = [
        [InlineKeyboardButton("🔥 Мои огоньки", callback_data="my_fires")],
        [InlineKeyboardButton("➕ Добавить друга", callback_data="add_friend")],
        [InlineKeyboardButton("💖 Донат", callback_data="donate")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "👋 Привет! Я бот для поддержания «огоньков» — серий ежедневного общения.\n"
        "Выбери действие:",
        reply_markup=reply_markup,
    )

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    pairs = await db.get_user_pairs(user.id)
    if not pairs:
        await update.message.reply_text("У вас пока нет активных серий. Добавьте друга через кнопку «Добавить друга».")
        return
    tz = await get_user_timezone_async(user.id)
    lines = []
    for pair in pairs:
        other_id = pair["user2_id"] if pair["user1_id"] == user.id else pair["user1_id"]
        other = await db.get_user(other_id)
        other_name = f"@{other['username']}" if other and other['username'] else f"ID {other_id}"
        streak, active = await db.get_streak_info(pair)
        if streak >= 3:
            fire = "🔥" if active else "💔"
            lines.append(f"{fire} {other_name} — {streak} дней")
        else:
            lines.append(f"⏳ {other_name} — ещё идёт набор (нужно 3 дня)")
    await update.message.reply_text("📊 Ваши огоньки:\n" + "\n".join(lines))

async def settimezone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    zones = ["UTC", "Europe/Moscow", "Europe/Kiev", "Asia/Almaty", "America/New_York"]
    reply_keyboard = [[KeyboardButton(z) for z in zones[i:i+2]] for i in range(0, len(zones), 2)]
    await update.message.reply_text(
        "Выберите ваш часовой пояс (или отправьте название вручную):",
        reply_markup=ReplyKeyboardMarkup(reply_keyboard, one_time_keyboard=True),
    )

async def handle_timezone_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    tz_input = update.message.text.strip()
    if tz_input in pytz.all_timezones:
        await db.set_timezone(user.id, tz_input)
        await update.message.reply_text(f"✅ Часовой пояс установлен: {tz_input}")
    else:
        await update.message.reply_text("❌ Неверный часовой пояс. Попробуйте снова через /settimezone")

async def inline_button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = update.effective_user
    data = query.data
    if data == "my_fires":
        await show_my_fires(query, user)
    elif data == "add_friend":
        await start_add_friend(query, context)
    elif data == "donate":
        await donate(query)
    else:
        await query.edit_message_text("Неизвестная команда.")

async def show_my_fires(query, user):
    pairs = await db.get_user_pairs(user.id)
    if not pairs:
        await query.edit_message_text("У вас пока нет активных серий. Добавьте друга через кнопку «Добавить друга».")
        return
    tz = await get_user_timezone_async(user.id)
    lines = []
    for pair in pairs:
        other_id = pair["user2_id"] if pair["user1_id"] == user.id else pair["user1_id"]
        other = await db.get_user(other_id)
        other_name = f"@{other['username']}" if other and other['username'] else f"ID {other_id}"
        streak, active = await db.get_streak_info(pair)
        start = datetime.fromisoformat(pair["start_date"]) if pair["start_date"] else None
        if start and streak >= 3:
            duration = (datetime.now(timezone.utc) - start).days + 1
            fire = "🔥" if active else "💔"
            lines.append(f"{fire} С {other_name}: {streak} дней (всего {duration} дн.)")
        else:
            lines.append(f"⏳ С {other_name}: ещё не набрано 3 дня")
    await query.edit_message_text("🔥 Ваши огоньки:\n" + "\n".join(lines))

async def start_add_friend(query, context):
    await query.edit_message_text("Введите @username друга (без @):")
    context.user_data["waiting_for_friend"] = True

async def handle_friend_username(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not context.user_data.get("waiting_for_friend"):
        return
    context.user_data["waiting_for_friend"] = False
    username = update.message.text.strip().lstrip("@")
    cursor = await db.conn.execute(
        "SELECT user_id FROM users WHERE username = ?", (username,)
    )
    row = await cursor.fetchone()
    if not row:
        await update.message.reply_text(
            f"❌ Пользователь @{username} ещё не зарегистрирован в боте.\n"
            "Попросите его написать /start боту, затем повторите попытку."
        )
        return
    friend_id = row[0]
    if friend_id == user.id:
        await update.message.reply_text("❌ Нельзя добавить самого себя.")
        return
    pairs = await db.get_user_pairs(user.id)
    for p in pairs:
        if (p["user1_id"] == friend_id or p["user2_id"] == friend_id):
            await update.message.reply_text("❌ У вас уже есть активная серия с этим пользователем.")
            return
    try:
        chat = await context.bot.create_new_group(
            title=f"🔥 Огонёк: {user.first_name} & {username}",
            user_ids=[friend_id, user.id],
        )
        chat_id = chat.id
    except Exception as e:
        logger.error(f"Failed to create group: {e}")
        await update.message.reply_text("❌ Не удалось создать группу. Убедитесь, что бот может добавлять участников.")
        return
    pair_id = await db.create_pair(user.id, friend_id, chat_id)
    await context.bot.send_message(
        chat_id=chat_id,
        text=f"🎉 Добро пожаловать! @{user.username} и @{username} теперь в одной группе.\n"
             "Обменивайтесь сообщениями каждый день, чтобы зажечь огонёк! 🔥\n"
             "После 3 дней непрерывного общения появится серия.",
    )
    await update.message.reply_text(f"✅ Группа создана! Переходите: {chat.title} (нажмите, чтобы открыть).")

async def donate(query):
    await query.edit_message_text(
        "💖 Поддержать разработчика:\n"
        "пока нету",
        disable_web_page_preview=True,
    )

async def handle_group_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.chat.type.startswith("group"):
        return
    chat_id = update.message.chat.id
    user_id = update.effective_user.id
    pair = await db.get_pair_by_chat(chat_id)
    if not pair:
        return
    await db.update_message_time(pair["id"], user_id, update.message.date)
    await db.check_and_update_streak(pair["id"], user_id)
    pair = await db.get_pair_by_id(pair["id"])
    streak, active = await db.get_streak_info(pair)
    if streak >= 3 and streak != pair["streak_count"]:
        if pair["streak_count"] < 3:
            await update.message.reply_text("🔥 Ура! Вы начали серию! Огонёк зажжён! 🔥")
        else:
            await update.message.reply_text(f"🔥 Серия продолжается! Уже {streak} дней! 🔥")

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.error(msg="Exception while handling an update:", exc_info=context.error)

def main():
    application = Application.builder().token(TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("stats", stats))
    application.add_handler(CommandHandler("settimezone", settimezone))
    application.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE,
        handle_timezone_input,
    ))
    application.add_handler(CallbackQueryHandler(inline_button_handler))
    application.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE,
        handle_friend_username,
    ))
    application.add_handler(MessageHandler(
        filters.TEXT & filters.ChatType.GROUPS,
        handle_group_message,
    ))
async def main():
    await db.connect()
    application = Application.builder().token(TOKEN).build()
    # ... все handlers ...
    logger.info("Бот запущен...")
    await application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    import sys
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())

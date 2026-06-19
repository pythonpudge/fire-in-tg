import asyncio
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
import aiosqlite

# --- КОНФИГУРАЦИЯ ---
TOKEN = "ВАШ_ТОКЕН_БОТА"
TIMEZONE = ZoneInfo('Europe/Moscow')
logging.basicConfig(level=logging.INFO)

bot = Bot(token=TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# --- БАЗА ДАННЫХ ---
async def init_db():
    async with aiosqlite.connect("streaks.db") as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS interactions (
                user1_id INTEGER,
                user2_id INTEGER,
                last_interaction TIMESTAMP,
                streak_start_date TIMESTAMP,
                current_streak INTEGER DEFAULT 0,
                PRIMARY KEY (user1_id, user2_id)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS friends (
                user_id INTEGER,
                friend_id INTEGER,
                PRIMARY KEY (user_id, friend_id)
            )
        """)
        await db.commit()

# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---
async def update_interaction(user1: int, user2: int):
    # Приводим ID к порядку, чтобы хранить одну запись для пары
    u1, u2 = sorted([user1, user2])
    now = datetime.now(TIMEZONE)
    
    async with aiosqlite.connect("streaks.db") as db:
        cursor = await db.execute("SELECT last_interaction, streak_start_date, current_streak FROM interactions WHERE user1_id = ? AND user2_id = ?", (u1, u2))
        row = await cursor.fetchone()
        
        if not row:
            await db.execute("INSERT INTO interactions (user1_id, user2_id, last_interaction) VALUES (?, ?, ?)", (u1, u2, now))
        else:
            last_int, start_date, streak = row
            last_int = datetime.fromisoformat(last_int)
            
            # Логика огонька (3 дня подряд)
            if now.date() - last_int.date() == timedelta(days=1):
                # Продолжаем серию
                await db.execute("UPDATE interactions SET last_interaction = ?, current_streak = current_streak + 1 WHERE user1_id = ? AND user2_id = ?", (now, u1, u2))
            elif now.date() - last_int.date() > timedelta(days=1):
                # Серия сброшена
                await db.execute("UPDATE interactions SET last_interaction = ?, current_streak = 0 WHERE user1_id = ? AND user2_id = ?", (now, u1, u2))
        await db.commit()

# --- КЛАВИАТУРА ---
def main_menu():
    kb = [
        [InlineKeyboardButton(text="🔥 Мои огоньки", callback_data="my_streaks")],
        [InlineKeyboardButton(text="➕ Добавить друга", callback_data="add_friend")],
        [InlineKeyboardButton(text="💰 Донат", callback_data="donate")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=kb)

# --- СОСТОЯНИЯ ---
class AddFriendState(StatesGroup):
    waiting_for_username = State()

# --- ХЕНДЛЕРЫ ---
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer("Привет! Я бот для отслеживания огоньков. Используй меню:", reply_markup=main_menu())

@dp.callback_query(F.data == "my_streaks")
async def show_streaks(callback: types.CallbackQuery):
    async with aiosqlite.connect("streaks.db") as db:
        async with db.execute("SELECT user2_id, current_streak FROM interactions WHERE user1_id = ? OR user2_id = ?", (callback.from_user.id, callback.from_user.id)) as cursor:
            rows = await cursor.fetchall()
    
    if not rows:
        await callback.answer("У вас пока нет активных огоньков.")
        return
    
    text = "Ваши текущие серии:\n"
    for friend_id, streak in rows:
        text += f"ID {friend_id}: {streak} дней 🔥\n"
    await callback.message.edit_text(text, reply_markup=main_menu())

@dp.callback_query(F.data == "add_friend")
async def ask_friend_username(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.answer("Введите @username друга, чтобы добавить его:")
    await state.set_state(AddFriendState.waiting_for_username)

@dp.message(AddFriendState.waiting_for_username)
async def process_add_friend(message: types.Message, state: FSMContext):
    username = message.text.replace("@", "")
    # В реальном коде здесь нужно найти ID пользователя по username через базу данных, 
    # где вы должны были предварительно сохранить пользователей при их первом использовании бота
    await message.answer(f"Пользователь @{username} добавлен в друзья!")
    await state.clear()

@dp.callback_query(F.data == "donate")
async def donate(callback: types.CallbackQuery):
    await callback.message.edit_text("Спасибо за поддержку! Ссылка на донат: https://boosty.to/your-project", reply_markup=main_menu())

# --- ЛОГИКА ОГОНЬКОВ (Middleware) ---
# Чтобы это работало для людей, нужно перехватывать сообщения в чатах
@dp.message(F.chat.type.in_(['private', 'group', 'supergroup']))
async def track_messages(message: types.Message):
    # Тут должна быть проверка, что сообщение от человека
    if message.from_user.is_bot:
        return
    
    pass

# --- ЗАПУСК ---
async def main():
    await init_db()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())

import asyncio
import logging
import sqlite3
import datetime
from zoneinfo import ZoneInfo
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.storage.memory import MemoryStorage

# Настройка
TOKEN = "8805888087:AAEYMCGUWQkWuytZwLCXnAbEz2K6Zd2SyW4"
TIMEZONE = ZoneInfo('Europe/Moscow')
logging.basicConfig(level=logging.INFO)
bot = Bot(token=TOKEN)
dp = Dispatcher(storage=MemoryStorage())

class WriteMessage(StatesGroup):
    choosing_recipient = State() # Выбор получателя
    writing_text = State()       # Написание текста

# Хранилище состояний (в памяти)
storage = MemoryStorage()
bot = Bot(token="8805888087:AAEYMCGUWQkWuytZwLCXnAbEz2K6Zd2SyW4")
dp = Dispatcher(storage=storage)

# --- База данных ---
def init_db():
    conn = sqlite3.connect('streaks.db')
    cursor = conn.cursor()
    # Таблица пользователей
    cursor.execute('''CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, username TEXT)''')
    # Таблица связей (стреки)
    cursor.execute('''CREATE TABLE IF NOT EXISTS streaks (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user1 INTEGER,
                        user2 INTEGER,
                        last_interaction DATE,
                        streak_count INTEGER DEFAULT 0,
                        is_active BOOLEAN DEFAULT 1)''')
    conn.commit()
    conn.close()

# --- Состояния ---
class StreakStates(StatesGroup):
    waiting_for_username = State()

# --- Функции логики ---
def get_user_id_by_username(username):
    conn = sqlite3.connect('streaks.db')
    cursor = conn.cursor()
    cursor.execute("SELECT user_id FROM users WHERE username = ?", (username,))
    res = cursor.fetchone()
    conn.close()
    return res[0] if res else None

# --- Клавиатура ---
def main_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔥 Мои огоньки", callback_data="my_streaks")],
        [InlineKeyboardButton(text="➕ Добавить друга", callback_data="add_friend")],
        [InlineKeyboardButton(text="💰 Донат", callback_data="donate")]
    ])

# --- Обработчики ---

@dp.message(Command("start"))
async def start_cmd(message: types.Message):
    conn = sqlite3.connect('streaks.db')
    conn.execute("INSERT OR IGNORE INTO users (user_id, username) VALUES (?, ?)", 
                 (message.from_user.id, message.from_user.username))
    conn.commit()
    conn.close()
    await message.answer("Привет! Я бот для отслеживания ваших огоньков. Используй кнопки ниже:", reply_markup=main_kb())

@dp.callback_query(F.data == "add_friend")
async def add_friend_start(call: types.CallbackQuery, state: FSMContext):
    await call.message.answer("Пришлите юзернейм друга (без @), с которым хотите начать серию:")
    await state.set_state(StreakStates.waiting_for_username)

@dp.message(StreakStates.waiting_for_username)
async def process_friend_username(message: types.Message, state: FSMContext):
    target_username = message.text.replace("@", "")
    target_id = get_user_id_by_username(target_username)
    
    if not target_id:
        await message.answer("Пользователь не найден. Убедись, что он запускал бота ранее.")
        await state.clear()
        return

    # Создание связи
    conn = sqlite3.connect('streaks.db')
    cursor = conn.cursor()
    cursor.execute("INSERT INTO streaks (user1, user2, last_interaction) VALUES (?, ?, ?)",
                   (message.from_user.id, target_id, datetime.datetime.now(TIMEZONE).date()))
    conn.commit()
    conn.close()
    await message.answer(f"Связь с @{target_username} создана! Начинайте общение.")
    await state.clear()

@dp.callback_query(F.data == "my_streaks")
async def show_streaks(call: types.CallbackQuery):
    conn = sqlite3.connect('streaks.db')
    cursor = conn.cursor()
    cursor.execute("""
        SELECT u.username, s.streak_count 
        FROM streaks s 
        JOIN users u ON (s.user2 = u.user_id OR s.user1 = u.user_id)
        WHERE (s.user1 = ? OR s.user2 = ?) AND s.user1 != u.user_id
    """, (call.from_user.id, call.from_user.id))
    
    results = cursor.fetchall()
    conn.close()
    
    if not results:
        await call.message.answer("У вас пока нет активных серий.")
        return
        
    text = "Ваши серии:\n"
    for name, count in results:
        text += f"🔥 С @{name}: {count} дней\n"
    await call.message.answer(text)
    # Пример данных получателей
RECIPIENTS = {
    "user_1": 123456789, # ID в Telegram
    "user_2": 987654321
}
# 1. Кнопка "Мои огоньки"
@dp.message(F.text == "Мои огоньки")
async def cmd_my_lights(message: Message, state: FSMContext):
    await message.answer("Кому вы хотите написать?", reply_markup=get_recipients_kb())
    await state.set_state(WriteMessage.choosing_recipient)

# 2. Обработка выбора получателя (callback)
@dp.callback_query(WriteMessage.choosing_recipient, F.data.startswith("send_to_"))
async def process_recipient(callback: CallbackQuery, state: FSMContext):
    recipient_key = callback.data.split("_")[-1] # Получаем user_1 или user_2
    
    # Сохраняем ID получателя в контекст состояния
    await state.update_data(chosen_recipient_id=RECIPIENTS[f"user_{recipient_key}"])
    
    await callback.message.answer("Введите текст сообщения:")
    await state.set_state(WriteMessage.writing_text)
    await callback.answer()

# 3. Обработка самого текста сообщения
@dp.message(WriteMessage.writing_text)
async def process_text_message(message: Message, state: FSMContext):
    data = await state.get_data()
    recipient_id = data.get("chosen_recipient_id")
    
    # Отправляем сообщение получателю
    try:
        await bot.send_message(chat_id=recipient_id, text=f"Вам сообщение: {message.text}")
        await message.answer("Сообщение успешно отправлено!")
    except Exception as e:
        await message.answer(f"Ошибка при отправке: {e}")
    
    # Сбрасываем состояние
    await state.clear()


def get_recipients_kb():
    builder = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Алиса", callback_data="send_to_user_1")],
        [InlineKeyboardButton(text="Боб", callback_data="send_to_user_2")]
    ])
    return builder


@dp.callback_query(F.data == "donate")
async def donate_cmd(call: types.CallbackQuery):
    await call.message.answer("Спасибо за поддержку! Ссылка на донат: [ССЫЛКА_НА_ОПЛАТУ]")

# --- Логика учета дней (глобальный обработчик сообщений) ---
@dp.message()
async def track_activity(message: types.Message):
    # Эта функция анализирует все сообщения для накопления 3 дней
    if not message.text: return
    
    now = datetime.datetime.now(TIMEZONE).date()
    
    # Логика обновления:
    # 1. Найти связь, где участвует этот пользователь и кто-то еще
    # 2. Если сегодня сообщение есть - игнорируем (уже засчитано)
    # 3. Если вчера сообщение было - инкрементируем streak_count
    # 4. Если прошло более 1 дня - сбрасываем streak_count (серия прервалась)
    
    conn = sqlite3.connect('streaks.db')
    cursor = conn.cursor()
    cursor.execute("SELECT id, last_interaction, streak_count FROM streaks WHERE user1 = ? OR user2 = ?", 
                   (message.from_user.id, message.from_user.id))
    streak = cursor.fetchone()
    
    if streak:
        s_id, last_date_str, count = streak
        last_date = datetime.datetime.strptime(last_date_str, '%Y-%m-%d').date()
        
        if last_date < now - datetime.timedelta(days=1):
            # Серия прервалась
            cursor.execute("UPDATE streaks SET streak_count = 0, last_interaction = ? WHERE id = ?", (now, s_id))
        elif last_date == now - datetime.timedelta(days=1):
            # Продолжение серии
            cursor.execute("UPDATE streaks SET streak_count = streak_count + 1, last_interaction = ? WHERE id = ?", (now, s_id))
            # --- Логика обновления серии ---
def update_streak(user_id, friend_id):
    conn = sqlite3.connect('streaks.db')
    cursor = conn.cursor()
    
    # Ищем активную серию
    cursor.execute("""
        SELECT id, last_interaction, streak_count 
        FROM streaks 
        WHERE (user1 = ? AND user2 = ?) OR (user1 = ? AND user2 = ?)
    """, (user_id, friend_id, friend_id, user_id))
    
    row = cursor.fetchone()
    if not row:
        return False, "Серия не найдена."

    streak_id, last_date, count = row
    today = datetime.datetime.now(TIMEZONE).date()
    last_date_obj = datetime.datetime.strptime(last_date, '%Y-%m-%d').date()

    # Логика: если уже обновили сегодня
    if last_date_obj == today:
        return False, "Вы уже отправляли огонек сегодня!"
    
    # Если прошел ровно день — увеличиваем
    if (today - last_date_obj).days == 1:
        new_count = count + 1
        cursor.execute("UPDATE streaks SET streak_count = ?, last_interaction = ? WHERE id = ?", 
                       (new_count, today, streak_id))
    # Если пропущено больше дня — сбрасываем
    else:
        cursor.execute("UPDATE streaks SET streak_count = 1, last_interaction = ? WHERE id = ?", 
                       (today, streak_id))
    
    conn.commit()
    conn.close()
    return True, "Огонек отправлен! 🔥"

# --- Обработчик списка серий ---
@dp.callback_query(F.data == "my_streaks")
async def show_my_streaks(call: types.CallbackQuery):
    conn = sqlite3.connect('streaks.db')
    cursor = conn.cursor()
    
    # Запрос для получения имен друзей и данных о сериях
    cursor.execute("""
        SELECT u.username, s.streak_count, s.id, s.user1, s.user2
        FROM streaks s
        JOIN users u ON (u.user_id = CASE WHEN s.user1 = ? THEN s.user2 ELSE s.user1 END)
        WHERE s.user1 = ? OR s.user2 = ?
    """, (call.from_user.id, call.from_user.id, call.from_user.id))
    
    streaks = cursor.fetchall()
    conn.close()

    if not streaks:
        await call.message.edit_text("У вас пока нет активных серий.", reply_markup=main_kb())
        return

    text = "🔥 Ваши текущие серии:\n\n"
    keyboard_buttons = []
    
    for row in streaks:
        username, count, s_id, u1, u2 = row
        text += f"@{username} — {count} дней\n"
        # Кнопка для отправки огонька конкретному другу
        friend_id = u2 if u1 == call.from_user.id else u1
        keyboard_buttons.append([InlineKeyboardButton(text=f"🔥 Огонек для @{username}", callback_data=f"fire_{friend_id}")])

    keyboard_buttons.append([InlineKeyboardButton(text="🔙 Назад", callback_data="back_main")])
    await call.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard_buttons))

# --- Обработчик нажатия на "Огонек" ---
@dp.callback_query(F.data.startswith("fire_"))
async def fire_callback(call: types.CallbackQuery):
    friend_id = int(call.data.split("_")[1])
    success, msg = update_streak(call.from_user.id, friend_id)
    
    await call.answer(msg) # Всплывающее уведомление
    if success:
        # Можно отправить уведомление второму человеку, если есть его ID
        await bot.send_message(friend_id, f"Пользователь @{call.from_user.username} отправил вам огонек! 🔥")
        await show_my_streaks(call) # Обновляем меню

        
        conn.commit()
    conn.close()

async def main():
    init_db()
    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())

import os
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from vkbottle.bot import Bot, Message

# --- 1. ДАННЫЕ И ПЕРСОНАЛ ---
USER_DATA = {
    870757778: ["Специальный Руководитель", "Misha Manlix"],
}

RANK_WEIGHT = {
    "Пользователь": 0, "Модератор": 1, "Старший Модератор": 2, 
    "Администратор": 3, "Старший Администратор": 4, "Зам. Спец. Администратора": 5,
    "Спец. Администратор": 6, "Владелец": 7, "Заместитель Специального Руководителя": 8,
    "Основной Зам Специального Руководителя": 9, "Специальный Руководитель": 10
}

# Список активированных бесед (хранится в оперативной памяти)
ACTIVE_CHATS = set()

# --- 2. ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---

def get_rank(user_id):
    return USER_DATA.get(user_id, ["Пользователь"])[0]

def has_access(user_id, required_rank):
    return RANK_WEIGHT.get(get_rank(user_id), 0) >= RANK_WEIGHT.get(required_rank, 0)

# --- 3. ИНИЦИАЛИЗАЦИЯ ---
bot = Bot(token=os.environ.get("TOKEN"))

# --- 4. КОМАНДА АКТИВАЦИИ (/start) ---

@bot.on.message(text="/start")
async def start_handler(message: Message):
    # 1. Проверка прав пользователя
    if not has_access(message.from_id, "Специальный Руководитель"):
        return "Недостаточно прав!"

    # 2. Проверка прав бота (админка/звезда)
    try:
        members = await bot.api.messages.get_conversation_members(peer_id=message.peer_id)
        # Если запрос прошел, значит у бота есть доступ к участникам (он админ)
        
        ACTIVE_CHATS.add(message.peer_id)
        await message.answer("Проверка прав пройдена успешно. Беседа активирована.")
    except Exception:
        await message.answer("Ошибка: выдайте боту права администратора (звезду) для активации.")

# --- 5. ОСТАЛЬНЫЕ КОМАНДЫ (С проверкой активации) ---

@bot.on.message(text="/stats")
async def stats_handler(message: Message):
    if message.peer_id not in ACTIVE_CHATS:
        return "Ошибка: беседа не активирована. Используйте /start (доступно Спец. Руководству)."
    
    rank = get_rank(message.from_id)
    await message.answer(f"Ваша статистика:\nРоль: {rank}\nID: {message.from_id}")

@bot.on.message(text="/getid")
async def getid_handler(message: Message):
    if message.peer_id not in ACTIVE_CHATS: return
    target_id = message.reply_message.from_id if message.reply_message else message.from_id
    await message.answer(f"ID пользователя: {target_id}")

@bot.on.message(text="/kick")
async def kick_handler(message: Message):
    if message.peer_id not in ACTIVE_CHATS: return
    if not has_access(message.from_id, "Модератор"): return "Недостаточно прав!"
    
    if not message.reply_message: return "Ошибка: ответьте на сообщение."
    
    target_id = message.reply_message.from_id
    if RANK_WEIGHT.get(get_rank(target_id), 0) >= RANK_WEIGHT.get(get_rank(message.from_id), 0):
        return "Ошибка: нельзя исключить равного или старшего."

    try:
        await bot.api.messages.remove_chat_user(chat_id=message.peer_id - 2000000000, user_id=target_id)
        await message.answer(f"Пользователь {target_id} исключен.")
    except:
        await message.answer("Ошибка при исключении.")

@bot.on.message(text="/gstaff")
async def gstaff_handler(message: Message):
    if message.peer_id not in ACTIVE_CHATS: return
    if not has_access(message.from_id, "Заместитель Специального Руководителя"): return "Недостаточно прав!"
    
    res = "Специальное Руководство:\n"
    for uid, data in USER_DATA.items():
        if data[0] in ["Специальный Руководитель", "Основной Зам Специального Руководителя", "Заместитель Специального Руководителя"]:
            res += f"- {data[1]} ({data[0]}) [id{uid}]\n"
    await message.answer(res)

# --- 6. СЕРВЕР ДЛЯ RENDER ---
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b"OK")

def run_port():
    port = int(os.environ.get("PORT", 10000))
    HTTPServer(('0.0.0.0', port), Handler).serve_forever()

threading.Thread(target=run_port, daemon=True).start()
bot.run_forever()

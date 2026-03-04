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

ACTIVE_CHATS = set()

# --- 2. ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---

def get_rank(user_id):
    if user_id in USER_DATA:
        return USER_DATA[user_id][0]
    return "Пользователь"

def has_access(user_id, required_rank):
    user_rank = get_rank(user_id)
    return RANK_WEIGHT.get(user_rank, 0) >= RANK_WEIGHT.get(required_rank, 0)

# --- 3. ИНИЦИАЛИЗАЦИЯ ---
bot = Bot(token=os.environ.get("TOKEN"))

# --- 4. КОМАНДА АКТИВАЦИИ (/start) ---

@bot.on.message(text="/start")
async def start_handler(message: Message):
    # Проверка: является ли пользователь Спец. Руководителем
    if not has_access(message.from_id, "Специальный Руководитель"):
        return ("Вы не можете активировать Беседу, так как не являетесь командой Бота. \n\n"
                "Напишите в личные сообщения [https://vk.com/id870757778|Специальному Руководителю] и ожидайте ответа.")

    try:
        # Проверка прав бота (пытаемся получить список участников)
        await bot.api.messages.get_conversation_members(peer_id=message.peer_id)
        ACTIVE_CHATS.add(message.peer_id)
        await message.answer("Проверка прав пройдена успешно. Беседа активирована.")
    except Exception:
        await message.answer("Ошибка: выдайте боту права администратора (звезду) для активации.")

# --- 5. ОБНОВЛЕННЫЕ КОМАНДЫ ---

@bot.on.message(text="/getid")
async def getid_handler(message: Message):
    if message.peer_id not in ACTIVE_CHATS: return
    target_id = message.reply_message.from_id if message.reply_message else message.from_id
    await message.answer(f"Оригинальная ссылка на ВК:\nhttps://vk.com/id{target_id}")

@bot.on.message(text="/gstaff")
async def gstaff_handler(message: Message):
    if message.peer_id not in ACTIVE_CHATS: return
    if not has_access(message.from_id, "Заместитель Специального Руководителя"): return "Недостаточно прав!"
    
    spec_boss = "– Отсутствует."
    main_deputy = "– Отсутствует."
    deputies = "– Отсутствует.\n– Отсутствует."
    
    for uid, data in USER_DATA.items():
        role, nick = data
        link = f"[https://vk.com/id{uid}|{nick}]"
        if role == "Специальный Руководитель":
            spec_boss = f"– {link}"
        elif role == "Основной Зам Специального Руководителя":
            main_deputy = f"– {link}"
        elif role == "Заместитель Специального Руководителя":
            deputies = f"– {link}\n– Отсутствует."

    response = (
        "MANLIX MANAGER | Команда Бота:\n\n"
        "| Специальный Руководитель:\n"
        f"{spec_boss}\n\n"
        "| Основной зам. Спец. Руководителя:\n"
        f"{main_deputy}\n\n"
        "| Зам. Спец. Руководителя:\n"
        f"{deputies}"
    )
    await message.answer(response)

@bot.on.message(text="/kick")
async def kick_handler(message: Message):
    if message.peer_id not in ACTIVE_CHATS: return
    if not has_access(message.from_id, "Модератор"): return "Недостаточно прав!"
    if not message.reply_message: return "Ошибка: ответьте на сообщение пользователя."
    
    target_id = message.reply_message.from_id
    
    # Нельзя кикнуть того, кто выше по рангу
    if RANK_WEIGHT.get(get_rank(target_id), 0) >= RANK_WEIGHT.get(get_rank(message.from_id), 0):
        return "Ошибка: нельзя исключить равного или старшего по рангу."

    try:
        # В vkbottle для команд в беседе используется peer_id
        await bot.api.messages.remove_chat_user(chat_id=message.peer_id - 2000000000, user_id=target_id)
        await message.answer(f"Пользователь https://vk.com/id{target_id} исключен.")
    except Exception:
        await message.answer("Ошибка: бот должен быть администратором, а цель не должна быть администратором беседы.")

@bot.on.message(text="/mute")
async def mute_handler(message: Message):
    if message.peer_id not in ACTIVE_CHATS: return
    if not has_access(message.from_id, "Модератор"): return "Недостаточно прав!"
    if not message.reply_message: return "Ошибка: ответьте на сообщение пользователя."
    
    target_id = message.reply_message.from_id
    await message.answer(f"Пользователю https://vk.com/id{target_id} выдана блокировка чата (mute).")

# --- СЛУЖЕБНЫЕ ЧАСТИ ---

@bot.on.message(text="/stats")
async def stats_handler(message: Message):
    if message.peer_id not in ACTIVE_CHATS: return
    rank = get_rank(message.from_id)
    await message.answer(f"Ваша статистика:\nРоль: {rank}\nID: {message.from_id}")

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b"OK")

def run_port():
    port = int(os.environ.get("PORT", 10000))
    HTTPServer(('0.0.0.0', port), Handler).serve_forever()

threading.Thread(target=run_port, daemon=True).start()
bot.run_forever()

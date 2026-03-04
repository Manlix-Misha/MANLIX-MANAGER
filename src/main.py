import os
import threading
import re
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from vkbottle.bot import Bot, Message

# --- 1. ДАННЫЕ ---
USER_DATA = {
    870757778: ["Специальный Руководитель", "Misha Manlix"],
}

GBAN_LIST = set() 
ACTIVE_CHATS = set()

RANK_WEIGHT = {
    "Пользователь": 0, "Модератор": 1, "Старший Модератор": 2, 
    "Администратор": 3, "Старший Администратор": 4, "Зам. Спец. Администратора": 5,
    "Спец. Администратор": 6, "Владелец": 7, "Заместитель Специального Руководителя": 8,
    "Основной Зам Специального Руководителя": 9, "Специальный Руководитель": 10
}

# --- 2. ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---

def get_rank(user_id):
    return USER_DATA.get(user_id, ["Пользователь"])[0]

def has_access(user_id, required_rank):
    return RANK_WEIGHT.get(get_rank(user_id), 0) >= RANK_WEIGHT.get(required_rank, 0)

def extract_id(text):
    if not text: return None
    match = re.search(r'id(\d+)', text)
    if match: return int(match.group(1))
    return None

# --- 3. ИНИЦИАЛИЗАЦИЯ ---
bot = Bot(token=os.environ.get("TOKEN"))

# --- 4. КОМАНДА /HELP (РАЗДЕЛЕННАЯ) ---

@bot.on.message(text="/help")
async def help_handler(message: Message):
    # Если беседа не активирована, только Спец. Руководитель может видеть хелп
    if message.peer_id not in ACTIVE_CHATS and not has_access(message.from_id, "Специальный Руководитель"):
        return "Ошибка: беседа не активирована."

    # Сообщение 1: Команды от Пользователя до Владельца
    help1 = (
        "| Команды доступа (User -> Owner):\n"
        "/info — Официальные ресурсы\n"
        "/stats — Ваша статистика\n"
        "/getid — Ссылка на ВК\n"
        "/staff — Состав администрации\n"
        "/ping — Проверка отклика бота\n"
    )
    if has_access(message.from_id, "Модератор"):
        help1 += "/kick — Исключить пользователя\n/mute [ссылка] [время] — Мут\n"
    
    await message.answer(help1)

    # Сообщение 2: Команды для Спец. Руководства
    if has_access(message.from_id, "Заместитель Специального Руководителя"):
        help2 = (
            "| Команды Спец. Руководства:\n"
            "/gstaff — Главное руководство\n"
            "/gbanpl [ссылка] — Глобальный бан\n"
            "/gunbanpl [ссылка] — Разбан\n"
            "/start — Активация (с уведомлением)\n"
            "/sync — Синхронизация (без уведомления)"
        )
        await message.answer(help2)

# --- 5. НОВЫЕ КОМАНДЫ (PING И SYNC) ---

@bot.on.message(text="/ping")
async def ping_handler(message: Message):
    delta = time.time() - message.date
    await message.answer(f"ПОНГ!\nВремя обработки сообщений — {round(delta, 2)} секунд")

@bot.on.message(text="/sync")
async def sync_handler(message: Message):
    if not has_access(message.from_id, "Специальный Руководитель"):
        return "Недостаточно прав!"
    
    ACTIVE_CHATS.add(message.peer_id)
    link = f"https://vk.com/id{message.from_id}"
    nick = USER_DATA[message.from_id][1]
    await message.answer(f"[{link}|{nick}] синхронизировал Беседу с Базой данных!")

# --- 6. ОСТАЛЬНЫЕ КОМАНДЫ ---

@bot.on.message(text="/start")
async def start_handler(message: Message):
    if not has_access(message.from_id, "Специальный Руководитель"):
        return ("Вы не можете активировать Беседу, так как не являетесь командой Бота. \n\n"
                "Напишите в личные сообщения [https://vk.com/id870757778|Специальному Руководителю] и ожидайте ответа.")
    ACTIVE_CHATS.add(message.peer_id)
    await message.answer("Проверка прав пройдена успешно. Беседа активирована.")

@bot.on.message(text="/getid")
async def getid_handler(message: Message):
    if message.peer_id not in ACTIVE_CHATS: return
    target_id = message.reply_message.from_id if message.reply_message else message.from_id
    await message.answer(f"Оригинальная ссылка на ВК:\nhttps://vk.com/id{target_id}")

@bot.on.message(text="/mute <args>")
@bot.on.message(text="/mute")
async def mute_handler(message: Message, args=None):
    if message.peer_id not in ACTIVE_CHATS: return
    if not has_access(message.from_id, "Модератор"): return "Недостаточно прав!"
    
    target_id = message.reply_message.from_id if message.reply_message else extract_id(args)
    parts = args.split() if args else []
    time_val = parts[-1] if len(parts) > 0 and parts[-1].isdigit() else "30"
    
    if not target_id: return "Ошибка: укажите пользователя."
    
    mod_nick = USER_DATA.get(message.from_id, ["", "Admin"])[1]
    mod_link = f"https://vk.com/id{message.from_id}"
    user_link = f"https://vk.com/id{target_id}"
    
    await message.answer(f"[{mod_link}|Модератор {mod_nick}] выдал Блокировку чата [{user_link}|пользователю] на {time_val} минут.")

@bot.on.message(text="/staff")
async def staff_handler(message: Message):
    if message.peer_id not in ACTIVE_CHATS: return
    roles = ["Владелец", "Спец. Администратор", "Зам. Спец. Администратора", "Старший Администратор", "Администратор", "Старший Модератор", "Модератор"]
    res = "Список администрации беседы:\n\n"
    for r in roles:
        res += f"{r}: \n"
        found = False
        for uid, data in USER_DATA.items():
            if data[0] == r:
                res += f"– [id{uid}|{data[1]}]\n"; found = True
        if not found: res += "– Отсутствует.\n"
        res += "\n"
    await message.answer(res)

@bot.on.message(text="/gstaff")
async def gstaff_handler(message: Message):
    if message.peer_id not in ACTIVE_CHATS: return
    if not has_access(message.from_id, "Заместитель Специального Руководителя"): return
    res = (
        "MANLIX MANAGER | Команда Бота:\n\n"
        "| Специальный Руководитель:\n– [https://vk.com/id870757778|Misha Manlix]\n\n"
        "| Основной зам. Спец. Руководителя:\n– Отсутствует.\n\n"
        "| Зам. Спец. Руководителя:\n– Отсутствует."
    )
    await message.answer(res)

# --- ГЛОБАЛЬНЫЙ БАН ---
@bot.on.message(text="/gbanpl <link>")
@bot.on.message(text="/gbanpl")
async def gban_handler(message: Message, link=None):
    if not has_access(message.from_id, "Заместитель Специального Руководителя"): return
    target_id = extract_id(link) if link else (message.reply_message.from_id if message.reply_message else None)
    if not target_id: return "Укажите пользователя."
    GBAN_LIST.add(target_id)
    try: await bot.api.messages.remove_chat_user(chat_id=message.peer_id - 2000000000, user_id=target_id)
    except: pass
    await message.answer(f"Пользователь [id{target_id}|ID {target_id}] занесен в Глобальный Бан-лист.")

@bot.on.message(text="/gunbanpl <link>")
@bot.on.message(text="/gunbanpl")
async def gunban_handler(message: Message, link=None):
    if not has_access(message.from_id, "Заместитель Специального Руководителя"): return
    target_id = extract_id(link) if link else (message.reply_message.from_id if message.reply_message else None)
    if target_id in GBAN_LIST:
        GBAN_LIST.remove(target_id)
        await message.answer(f"Пользователь [id{target_id}|ID {target_id}] вынесен из Гбан-листа.")

# --- СЕРВЕР RENDER ---
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b"OK")

def run_port():
    port = int(os.environ.get("PORT", 10000))
    HTTPServer(('0.0.0.0', port), Handler).serve_forever()

threading.Thread(target=run_port, daemon=True).start()
bot.run_forever()

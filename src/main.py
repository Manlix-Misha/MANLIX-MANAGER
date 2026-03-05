import os
import threading
import re
import time
import json
from http.server import HTTPServer, BaseHTTPRequestHandler
from vkbottle.bot import Bot, Message
from vkbottle import Keyboard, KeyboardButtonColor, Text

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
    match = re.search(r'id(\d+)', str(text))
    return int(match.group(1)) if match else None

# --- 3. ИНИЦИАЛИЗАЦИЯ ---
bot = Bot(token=os.environ.get("TOKEN"))

# --- 4. КОМАНДА /HELP ---
@bot.on.message(text=["/help", "/help <args>"])
async def help_handler(message: Message, args=None):
    if message.peer_id not in ACTIVE_CHATS and not has_access(message.from_id, "Специальный Руководитель"):
        return "Ошибка: беседа не активирована."

    msg1 = "Команды пользователей:\n"
    msg1 += "/info -- Официальные ресурсы\n/stats -- Ваша статистика\n/getid -- Получить ссылку на профиль\n/staff -- Список администрации беседы\n/ping -- Проверка времени отклика\n\n"
    
    if has_access(message.from_id, "Модератор"):
        msg1 += "Команды модерации:\n/kick -- Исключить пользователя\n/mute -- Выдать блокировку чата\n\n"
    
    if has_access(message.from_id, "Администратор"):
        msg1 += "Команды администрации:\n/warn -- Выдать предупреждение\n\n"
        
    if has_access(message.from_id, "Зам. Спец. Администратора"):
        msg1 += "Команды Спец. Администрации:\n/check -- Проверить игрока\n"
    await message.answer(msg1)

    if has_access(message.from_id, "Заместитель Специального Руководителя"):
        msg2 = "Команды руководства:\n\n"
        msg2 += "Команды ЗСР:\n/gstaff -- Список высшего руководства\n/gbanpl -- Выдать глобальный бан\n/gunbanpl -- Снять глобальный бан\n\n"
        if has_access(message.from_id, "Специальный Руководитель"):
            msg2 += "Команды Спец. Руководителя:\n/start -- Активация беседы\n/sync -- Синхронизация беседы"
        await message.answer(msg2)

# --- 5. ИНФО-КОМАНДЫ ---

@bot.on.message(text=["/gstaff", "/gstaff <args>"])
async def gstaff_handler(message: Message, args=None):
    if message.peer_id not in ACTIVE_CHATS or not has_access(message.from_id, "Заместитель Специального Руководителя"): return
    spec_boss = "– [https://vk.com/id870757778|Misha Manlix]"
    main_deputy = "– Отсутствует."
    deputies = [f"– [https://vk.com/id{uid}|{data[1]}]" for uid, data in USER_DATA.items() if data[0] == "Заместитель Специального Руководителя"]
    deputy_str = "\n".join(deputies[:2]) if deputies else "– Отсутствует.\n– Отсутствует."
    if len(deputies) == 1: deputy_str += "\n– Отсутствует."
    
    res = (f"MANLIX MANAGER | Команда Бота:\n\n"
           f"| Специальный Руководитель:\n{spec_boss}\n\n"
           f"| Основной зам. Спец. Руководителя:\n{main_deputy}\n\n"
           f"| Зам. Спец. Руководителя:\n{deputy_str}")
    await message.answer(res)

@bot.on.message(text=["/ping", "/ping <args>"])
async def ping_handler(message: Message, args=None):
    # Исправлено: безопасное получение даты
    msg_time = message.date or time.time()
    delta = time.time() - msg_time
    await message.answer(f"ПОНГ!\nВремя обработки сообщений - {round(delta, 2)} секунд")

# --- 6. МОДЕРАЦИЯ И PAYLOAD ---

@bot.on.message(text=["/mute", "/mute <args>"])
async def mute_handler(message: Message, args=None):
    if message.peer_id not in ACTIVE_CHATS or not has_access(message.from_id, "Модератор"): return
    target_id = message.reply_message.from_id if message.reply_message else extract_id(args)
    if not target_id: return "Ошибка: укажите пользователя."
    
    # Исправлено: безопасный split
    time_v = "30"
    if args:
        parts = args.split()
        if parts and parts[-1].isdigit():
            time_v = parts[-1]
            
    mod_nick = USER_DATA.get(message.from_id, ["", "Admin"])[1]
    await message.answer(f"[id{message.from_id}|Модератор {mod_nick}] выдал Блокировку чата [id{target_id}|пользователю] на {time_v} минут.")

@bot.on.message(func=lambda message: message.payload is not None)
async def payload_handler(message: Message):
    # Исправлено: обработка payload как строки или дикта
    payload = message.payload
    if isinstance(payload, str):
        try: payload = json.loads(payload)
        except: return

    if payload.get("cmd") == "kick_btn":
        if not has_access(message.from_id, "Модератор"): return
        target_id = payload.get("target")
        try:
            await bot.api.messages.remove_chat_user(chat_id=message.peer_id - 2000000000, user_id=target_id)
            await message.answer(f"Пользователь [id{target_id}|ID {target_id}] окончательно исключен.")
        except: pass

# --- 7. СИСТЕМА ВЫХОДА (ИСПРАВЛЕНА) ---
@bot.on.message()
async def action_wrapper(message: Message):
    if not message.action: return
    # Исправлено: поддержка разных версий vkbottle для action.type
    a_type = str(message.action.type)
    if "chat_kick_user" in a_type:
        mid = message.action.member_id
        if mid == message.from_id:
            kb = Keyboard(inline=True).add(Text("Исключить", payload={"cmd": "kick_btn", "target": mid}), color=KeyboardButtonColor.NEGATIVE)
            await message.answer(f"[id{mid}|Пользователь] покинул(а) Беседу.", keyboard=kb)

# --- (ОСТАЛЬНЫЕ КОМАНДЫ: /sync, /start, /stats, /getid, /staff) ---
@bot.on.message(text=["/sync", "/sync <args>"])
async def sync_handler(message: Message, args=None):
    if has_access(message.from_id, "Специальный Руководитель"):
        ACTIVE_CHATS.add(message.peer_id)
        await message.answer("Беседа синхронизирована!")

@bot.on.message(text=["/stats", "/stats <args>"])
async def stats_h(message: Message, args=None):
    if message.peer_id in ACTIVE_CHATS:
        tid = message.reply_message.from_id if message.reply_message else message.from_id
        await message.answer(f"ID: {tid}\nРоль: {get_rank(tid)}")

# --- СЕРВЕР ---
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b"OK")

threading.Thread(target=lambda: HTTPServer(('0.0.0.0', int(os.environ.get("PORT", 10000))), Handler).serve_forever(), daemon=True).start()
bot.run_forever()

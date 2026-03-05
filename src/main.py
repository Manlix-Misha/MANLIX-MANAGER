import os
import threading
import re
import time
import json
import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from vkbottle.bot import Bot, Message
from vkbottle import Keyboard, KeyboardButtonColor, Text

# --- 1. ДАННЫЕ (НЕ ИЗМЕНЯТЬ НАЧАЛО ДЛЯ RENDER) ---
USER_DATA = {
    870757778: ["Специальный Руководитель", "Misha Manlix"],
}

DB_FILE = "chats_db.json"

def load_chats():
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, "r") as f:
                return set(json.load(f))
        except: return set()
    return set()

def save_chats():
    try:
        with open(DB_FILE, "w") as f:
            json.dump(list(ACTIVE_CHATS), f)
    except: pass

ACTIVE_CHATS = load_chats()

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
    if match: return int(match.group(1))
    match_mention = re.search(r'\[id(\d+)\|.*?\]', str(text))
    if match_mention: return int(match_mention.group(1))
    return None

async def check_active(message: Message):
    if message.peer_id not in ACTIVE_CHATS and message.from_id != 870757778:
        await message.answer("Владелец беседы не является командой Бота, я не буду здесь работать.")
        return False
    return True

# --- 3. ИНИЦИАЛИЗАЦИЯ ---
bot = Bot(token=os.environ.get("TOKEN"))

# --- 4. КОМАНДЫ МОДЕРАЦИИ ---

@bot.on.message(text=["/kick", "/kick <args>"])
async def kick_handler(message: Message, args=None):
    if not await check_active(message): return
    if not has_access(message.from_id, "Модератор"): return
    
    target_id = message.reply_message.from_id if message.reply_message else extract_id(args)
    if not target_id: return "Укажите пользователя!"
    
    try:
        await bot.api.messages.remove_chat_user(chat_id=message.peer_id - 2000000000, user_id=target_id)
        res = f"[id{message.from_id}|Модератор MANLIX] исключил(-а) [id{target_id}|пользователя] из Беседы."
        await message.answer(res)
    except: await message.answer("Ошибка: не удалось исключить.")

@bot.on.message(text=["/mute", "/mute <args>"])
async def mute_handler(message: Message, args=None):
    if not await check_active(message): return
    if not has_access(message.from_id, "Модератор"): return
    
    target_id = None
    time_minutes = 30
    reason = "Не указана"

    if message.reply_message:
        target_id = message.reply_message.from_id
        if args:
            parts = args.split(maxsplit=1)
            if parts[0].isdigit():
                time_minutes = int(parts[0])
                if len(parts) > 1: reason = parts[1]
            else:
                reason = args
    elif args:
        parts = args.split()
        target_id = extract_id(parts[0])
        if len(parts) > 1:
            if parts[1].isdigit():
                time_minutes = int(parts[1])
                if len(parts) > 2: reason = " ".join(parts[2:])
            else:
                reason = " ".join(parts[1:])

    if not target_id: return "Укажите пользователя!"
    
    # Исправленный расчет времени
    until_date = datetime.datetime.now() + datetime.timedelta(minutes=time_minutes)
    date_str = until_date.strftime("%d/%m/%Y %H:%M:%S")
    
    kb = Keyboard(inline=True)
    kb.add(Text("Снять мут", payload={"cmd": "unmute", "target": target_id}), color=KeyboardButtonColor.POSITIVE)
    kb.add(Text("Очистить", payload={"cmd": "clear_msgs", "target": target_id}), color=KeyboardButtonColor.NEGATIVE)

    res = (f"[id{message.from_id}|Модератор MANLIX] замутил(-а) [id{target_id}|пользователя]\n"
           f"Причина: {reason}\n"
           f"Мут выдан до: {date_str}")
    await message.answer(res, keyboard=kb)

# --- 5. ОБРАБОТКА КНОПОК (С РЕДАКТИРОВАНИЕМ) ---

@bot.on.message(func=lambda message: message.payload is not None)
async def payload_handler(message: Message):
    payload = message.payload
    if isinstance(payload, str): payload = json.loads(payload)
    if not has_access(message.from_id, "Модератор"): return

    cmd = payload.get("cmd")
    target = payload.get("target")

    if cmd == "unmute":
        new_text = f"[id{message.from_id}|Модератор MANLIX] снял(-а) мут [id{target}|пользователю]"
        await bot.api.messages.edit(
            peer_id=message.peer_id,
            message_id=message.conversation_message_id,
            message=new_text,
            keep_forward_messages=True
        )
    elif cmd == "clear_msgs":
        await message.answer(f"Сообщения пользователя [id{target}|ID {target}] были очищены.")

# --- 6. ОСТАЛЬНЫЕ КОМАНДЫ (БЕЗ ИЗМЕНЕНИЙ) ---

@bot.on.message(text="/staff")
async def staff_handler(message: Message):
    if not await check_active(message): return
    roles = ["Владелец", "Спец. Администратор", "Зам. Спец. Администратора", "Старший Администратор", "Администратор", "Старший Модератор", "Модератор"]
    parts = []
    for r in roles:
        found = [f"– [id{uid}|{data[1]}]" for uid, data in USER_DATA.items() if data[0] == r]
        parts.append(f"{r}: \n" + ("\n".join(found) if found else "– Отсутствует."))
    await message.answer("\n\n".join(parts))

@bot.on.message(text=["/sync", "/start"])
async def activation(message: Message):
    if message.from_id != 870757778: return
    ACTIVE_CHATS.add(message.peer_id)
    save_chats()
    await message.answer("Беседа успешно синхронизирована!")

# --- СЕРВЕР ---
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b"OK")

threading.Thread(target=lambda: HTTPServer(('0.0.0.0', int(os.environ.get("PORT", 10000))), Handler).serve_forever(), daemon=True).start()
bot.run_forever()

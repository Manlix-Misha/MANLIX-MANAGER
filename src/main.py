import os
import threading
import re
import time
import json
import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from vkbottle.bot import Bot, Message
from vkbottle import Keyboard, KeyboardButtonColor, Text

# --- 1. ДАННЫЕ ---
USER_DATA = {
    870757778: ["Специальный Руководитель", "Misha Manlix"],
}

DB_FILE = "chats_db.json"
MUTES_FILE = "mutes.json" # Файл для хранения активных мутов

def load_data(file, default):
    if os.path.exists(file):
        try:
            with open(file, "r") as f: return json.load(f)
        except: return default
    return default

def save_data(file, data):
    try:
        with open(file, "w") as f: json.dump(data, f)
    except: pass

ACTIVE_CHATS = set(load_data(DB_FILE, []))
ACTIVE_MUTES = load_data(MUTES_FILE, {}) # { "user_id": "timestamp_end" }

RANK_WEIGHT = {
    "Пользователь": 0, "Модератор": 1, "Зам. Специального Руководителя": 8, "Специальный Руководитель": 10
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
    if message.from_id == 870757778: return True
    if message.peer_id not in ACTIVE_CHATS:
        await message.answer("Владелец беседы не является командой Бота, я не буду здесь работать.")
        return False
    return True

# --- 3. ИНИЦИАЛИЗАЦИЯ ---
bot = Bot(token=os.environ.get("TOKEN"))

# --- 4. УДАЛЕНИЕ СООБЩЕНИЙ ТЕХ КТО В МУТЕ ---
@bot.on.message()
async def mute_checker(message: Message):
    uid = str(message.from_id)
    if uid in ACTIVE_MUTES:
        end_time = ACTIVE_MUTES[uid]
        if time.time() < end_time:
            try:
                await bot.api.messages.delete(
                    cmids=[message.conversation_message_id],
                    peer_id=message.peer_id,
                    delete_for_all=True
                )
            except: pass
            return
        else:
            del ACTIVE_MUTES[uid]
            save_data(MUTES_FILE, ACTIVE_MUTES)

# --- 5. КОМАНДЫ МОДЕРАЦИИ ---

@bot.on.message(text=["/mute", "/mute <args>"])
async def mute_handler(message: Message, args=None):
    if not await check_active(message) or not has_access(message.from_id, "Модератор"): return
    
    target_id = message.reply_message.from_id if message.reply_message else extract_id(args)
    if not target_id: return "Укажите пользователя!"

    time_min = 30
    reason = "Не указана"
    if args:
        digits = re.findall(r'\d+', args)
        if digits: time_min = int(digits[-1]) # Берем последнее число как время
        # Убираем ID и время из строки, чтобы найти причину
        clean_reason = re.sub(r'\[id\d+\|.*?\]|id\d+|\d+', '', args).strip()
        if clean_reason: reason = clean_reason

    # МСК Время
    end_ts = time.time() + (time_min * 60)
    ACTIVE_MUTES[str(target_id)] = end_ts
    save_data(MUTES_FILE, ACTIVE_MUTES)
    
    date_str = datetime.datetime.fromtimestamp(end_ts + 3*3600).strftime("%d/%m/%Y %H:%M:%S")
    
    kb = Keyboard(inline=True)
    kb.add(Text("Снять мут", payload={"cmd": "unmute", "target": target_id}), color=KeyboardButtonColor.POSITIVE)
    kb.add(Text("Очистить", payload={"cmd": "clear", "target": target_id}), color=KeyboardButtonColor.NEGATIVE)

    await message.answer(f"[id{message.from_id}|Модератор MANLIX] замутил(-а) [id{target_id}|пользователя]\nПричина: {reason}\nМут выдан до: {date_str}", keyboard=kb)

@bot.on.message(text=["/unmute", "/unmute <args>"])
async def unmute_cmd(message: Message, args=None):
    if not await check_active(message) or not has_access(message.from_id, "Модератор"): return
    target_id = message.reply_message.from_id if message.reply_message else extract_id(args)
    if str(target_id) in ACTIVE_MUTES:
        del ACTIVE_MUTES[str(target_id)]
        save_data(MUTES_FILE, ACTIVE_MUTES)
        await message.answer(f"[id{message.from_id}|Модератор MANLIX] снял(-а) мут [id{target_id}|пользователю]")

# --- 6. HELP И ДРУГИЕ КОМАНДЫ ---

@bot.on.message(text="/help")
async def help_handler(message: Message):
    if not await check_active(message): return
    
    msg1 = ("Пользователь:\n/info - официальные ресурсы.\n/stats - статистика.\n/getid - узнать оригинальный ID пользователя.\n\n"
            "Модератор:\n/kick - исключить пользователя из Беседы.\n/mute - выдать Блокировку чата.\n/unmute - снять Блокировку чата.\n\n"
            "Старший Модератор:\nОтсутствуют.\n\nАдминистратор:\nОтсутствуют.\n\nСтарший Администратор:\nОтсутствуют.\n\n"
            "Зам. Спец. Администратора:\nОтсутствуют.\n\nСпец. Администратор:\nОтсутствуют.\n\nВладелец:\nОтсутствуют.")
    await message.answer(msg1)

    if has_access(message.from_id, "Зам. Специального Руководителя"):
        msg2 = ("Зам. Специального Руководителя:\n/gstaff - руководство Бота.\n/gbanpl - Блокировка пользователя во всех игровых Беседах.\n/gunbanpl - Снятие Блокировки во всех игровых Беседах.\n\n"
                "Основной зам. Специального Руководителя:\nОтсутствуют.\n\n"
                "Специальный Руководитель:\n/sync.")
        await message.answer(msg2)

@bot.on.message(text="/getid")
async def getid_handler(message: Message):
    target_id = message.reply_message.from_id if message.reply_message else message.from_id
    await message.answer(f"Оригинальный ID пользователя: {target_id}")

@bot.on.message(text="/sync")
async def sync_handler(message: Message):
    if message.from_id != 870757778: return
    ACTIVE_CHATS.add(message.peer_id)
    save_data(DB_FILE, list(ACTIVE_CHATS))
    await message.answer(f"[id870757778|Misha Manlix] синхронизировал Беседу с Базой данных!")

# --- 7. ОБРАБОТКА PAYLOAD КНОПОК ---
@bot.on.message(func=lambda message: message.payload is not None)
async def payload_handler(message: Message):
    pl = json.loads(message.payload)
    if not has_access(message.from_id, "Модератор"): return

    if pl.get("cmd") == "unmute":
        tid = str(pl.get("target"))
        if tid in ACTIVE_MUTES: del ACTIVE_MUTES[tid]
        save_data(MUTES_FILE, ACTIVE_MUTES)
        await bot.api.messages.edit(peer_id=message.peer_id, conversation_message_id=message.conversation_message_id, 
                                    message=f"[id{message.from_id}|Модератор MANLIX] снял(-а) мут [id{tid}|пользователю]")

# --- СЕРВЕР ---
class Handler(BaseHTTPRequestHandler):
    def do_GET(self): self.send_response(200); self.end_headers(); self.wfile.write(b"OK")
threading.Thread(target=lambda: HTTPServer(('0.0.0.0', int(os.environ.get("PORT", 10000))), Handler).serve_forever(), daemon=True).start()
bot.run_forever()

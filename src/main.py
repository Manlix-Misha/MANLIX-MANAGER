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
MUTES_FILE = "mutes.json"

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
ACTIVE_MUTES = load_data(MUTES_FILE, {}) # { "user_id": timestamp_end }

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
    # Ищем id в упоминаниях [id123|...] или в ссылках vk.com/id123
    match = re.search(r'id(\d+)', str(text))
    if match: return int(match.group(1))
    return None

async def check_active(message: Message):
    if message.from_id == 870757778: return True
    if message.peer_id not in ACTIVE_CHATS:
        await message.answer("Владелец беседы не является командой Бота, я не буду здесь работать.")
        return False
    return True

# --- 3. ИНИЦИАЛИЗАЦИЯ ---
bot = Bot(token=os.environ.get("TOKEN"))

# --- 4. КОМАНДЫ РУКОВОДСТВА ---

@bot.on.message(text="/sync")
async def sync_handler(message: Message):
    if message.from_id != 870757778: return
    ACTIVE_CHATS.add(message.peer_id)
    save_data(DB_FILE, list(ACTIVE_CHATS))
    await message.answer(f"[id870757778|Специальный Руководитель Misha Manlix] синхронизировал Беседу с Базой данных!")

@bot.on.message(text="/gstaff")
async def gstaff_handler(message: Message):
    if not await check_active(message): return
    if not has_access(message.from_id, "Зам. Специального Руководителя"): return
    res = ("MANLIX MANAGER | Команда Бота:\n\n"
           "| Специальный Руководитель:\n– [id870757778|Misha Manlix]\n\n"
           "| Основной зам. Спец. Руководителя:\n– Отсутствует.\n\n"
           "| Зам. Спец. Руководителя:\n– Отсутствует.\n– Отсутствует.")
    await message.answer(res)

# --- 5. КОМАНДЫ МОДЕРАЦИИ ---

@bot.on.message(text=["/mute", "/mute <args>"])
async def mute_handler(message: Message, args=None):
    if not await check_active(message) or not has_access(message.from_id, "Модератор"): return
    
    target_id = message.reply_message.from_id if message.reply_message else extract_id(args)
    if not target_id: return "Укажите пользователя (ответ, ссылка или упоминание)!"

    time_min = 30
    reason = "Не указана"
    
    if args:
        # Ищем все числа в аргументах
        nums = re.findall(r'\d+', args)
        if message.reply_message:
            if nums: time_min = int(nums[0])
            reason = re.sub(r'^\d+\s*', '', args).strip() or "Не указана"
        else:
            # Если нет реплая, первое число — ID, второе — время
            if len(nums) >= 2: time_min = int(nums[1])
            reason = re.sub(r'\[.*?\]|id\d+|\d+', '', args).strip() or "Не указана"

    end_ts = time.time() + (time_min * 60)
    ACTIVE_MUTES[str(target_id)] = end_ts
    save_data(MUTES_FILE, ACTIVE_MUTES)
    
    # МСК Время (UTC+3)
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

@bot.on.message(text=["/kick", "/kick <args>"])
async def kick_handler(message: Message, args=None):
    if not await check_active(message) or not has_access(message.from_id, "Модератор"): return
    target_id = message.reply_message.from_id if message.reply_message else extract_id(args)
    if not target_id: return
    try:
        await bot.api.messages.remove_chat_user(chat_id=message.peer_id - 2000000000, user_id=target_id)
        await message.answer(f"[id{message.from_id}|Модератор MANLIX] исключил(-а) [id{target_id}|пользователя] из Беседы.")
    except: pass

# --- 6. КОМАНДЫ ПОЛЬЗОВАТЕЛЕЙ ---

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
                "Основной зам. Специального Руководителя:\nОтсутствуют.\n\nСпециальный Руководитель:\n/sync.")
        await message.answer(msg2)

@bot.on.message(text="/stats")
async def stats_handler(message: Message):
    tid = message.reply_message.from_id if message.reply_message else message.from_id
    role = get_rank(tid)
    status = "Синхронизировано" if message.peer_id in ACTIVE_CHATS else "Не синхронизировано"
    await message.answer(f"Статистика [id{tid}|пользователя]:\nРоль: {role}\nБеседа: {status}")

@bot.on.message(text="/getid")
async def getid_handler(message: Message):
    if not await check_active(message): return
    target_id = message.reply_message.from_id if message.reply_message else message.from_id
    await message.answer(f"Оригинальный ID пользователя: {target_id}")

# --- 7. ОБРАБОТЧИК КНОПОК И МУТА (В КОНЦЕ!) ---

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

@bot.on.message()
async def mute_checker(message: Message):
    if message.text and message.text.startswith("/"): return # Игнорируем команды
    uid = str(message.from_id)
    if uid in ACTIVE_MUTES:
        if time.time() < ACTIVE_MUTES[uid]:
            try:
                await bot.api.messages.delete(cmids=[message.conversation_message_id], peer_id=message.peer_id, delete_for_all=True)
            except: pass

# --- СЕРВЕР ---
class Handler(BaseHTTPRequestHandler):
    def do_GET(self): self.send_response(200); self.end_headers(); self.wfile.write(b"OK")
threading.Thread(target=lambda: HTTPServer(('0.0.0.0', int(os.environ.get("PORT", 10000))), Handler).serve_forever(), daemon=True).start()
bot.run_forever()

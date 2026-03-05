import os
import threading
import re
import time
import json
import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from vkbottle.bot import Bot, Message
from vkbottle import Keyboard, KeyboardButtonColor, Text, BaseMiddleware

# --- 1. ДАННЫЕ (НЕ ИЗМЕНЯТЬ ДЛЯ RENDER) ---
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
    "Пользователь": 0, "Модератор": 1, "Старший Модератор": 2, 
    "Администратор": 3, "Старший Администратор": 4, "Зам. Спец. Администратора": 5,
    "Спец. Администратор": 6, "Владелец": 7, "Зам. Специального Руководителя": 8,
    "Основной зам. Специального Руководителя": 9, "Специальный Руководитель": 10
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
    return None

async def check_active(message: Message):
    if message.from_id == 870757778: return True
    if message.peer_id not in ACTIVE_CHATS:
        await message.answer("Владелец беседы не является командой Бота, я не буду здесь работать.")
        return False
    return True

# --- 3. ИНИЦИАЛИЗАЦИЯ И МИДЛВАР (ПЕРЕХВАТЧИК МУТА) ---
bot = Bot(token=os.environ.get("TOKEN"))

class MuteMiddleware(BaseMiddleware[Message]):
    async def pre(self):
        if self.event.from_id is None: return
        uid = str(self.event.from_id)
        if uid in ACTIVE_MUTES:
            if time.time() < ACTIVE_MUTES[uid]:
                try:
                    # ДЛЯ УДАЛЕНИЯ БОТ ДОЛЖЕН БЫТЬ АДМИНСТРАТОРОМ БЕСЕДЫ
                    await self.event.ctx_api.messages.delete(
                        cmids=[self.event.conversation_message_id],
                        peer_id=self.event.peer_id,
                        delete_for_all=True
                    )
                except: pass
                self.stop("User is muted") # Моментальная блокировка дальнейших команд
            else:
                del ACTIVE_MUTES[uid]
                save_data(MUTES_FILE, ACTIVE_MUTES)

bot.labeler.message_view.register_middleware(MuteMiddleware())

# --- 4. КОМАНДЫ ПОЛЬЗОВАТЕЛЕЙ И HELP ---

@bot.on.message(text="/help")
async def help_handler(message: Message):
    if not await check_active(message): return
    uid = message.from_id

    # Первый блок (основной)
    msg1 = "Пользователь:\n/info - официальные ресурсы.\n/stats - статистика.\n/getid - узнать оригинальный ID пользователя.\n"
    
    if has_access(uid, "Модератор"):
        msg1 += "\nМодератор:\n/kick - исключить пользователя из Беседы.\n/mute - выдать Блокировку чата.\n/unmute - снять Блокировку чата.\n"
    if has_access(uid, "Старший Модератор"):
        msg1 += "\nСтарший Модератор:\nОтсутствуют.\n"
    if has_access(uid, "Администратор"):
        msg1 += "\nАдминистратор:\nОтсутствуют.\n"
    if has_access(uid, "Старший Администратор"):
        msg1 += "\nСтарший Администратор:\nОтсутствуют.\n"
    if has_access(uid, "Зам. Спец. Администратора"):
        msg1 += "\nЗам. Спец. Администратора:\nОтсутствуют.\n"
    if has_access(uid, "Спец. Администратор"):
        msg1 += "\nСпец. Администратор:\nОтсутствуют.\n"
    if has_access(uid, "Владелец"):
        msg1 += "\nВладелец:\nОтсутствуют.\n"
        
    await message.answer(msg1)

    # Второй блок (руководство)
    if has_access(uid, "Зам. Специального Руководителя"):
        msg2 = "Зам. Специального Руководителя:\n/gstaff - руководство Бота.\n/gbanpl - Блокировка пользователя во всех игровых Беседах.\n/gunbanpl - Снятие Блокировки во всех игровых Беседах.\n"
        if has_access(uid, "Основной зам. Специального Руководителя"):
            msg2 += "\nОсновной зам. Специального Руководителя:\nОтсутствуют.\n"
        if has_access(uid, "Специальный Руководитель"):
            msg2 += "\nСпециальный Руководитель:\n/sync.\n"
        await message.answer(msg2)

@bot.on.message(text=["/getid", "/getid <args>"])
async def getid_handler(message: Message, args=None):
    if not await check_active(message): return
    target_id = message.reply_message.from_id if message.reply_message else message.from_id
    if args:
        ext = extract_id(args)
        if ext: target_id = ext
    await message.answer(f"Оригинальная ссылка [id{target_id}|пользователя]:\nhttps://vk.com/id{target_id}")

@bot.on.message(text="/stats")
async def stats_handler(message: Message):
    tid = message.reply_message.from_id if message.reply_message else message.from_id
    status = "Синхронизировано" if message.peer_id in ACTIVE_CHATS else "Не синхронизировано"
    await message.answer(f"Статистика [id{tid}|пользователя]:\nРоль: {get_rank(tid)}\nБеседа: {status}")

# --- 5. КОМАНДЫ РУКОВОДСТВА ---

@bot.on.message(text="/sync")
async def sync_handler(message: Message):
    if message.from_id != 870757778: return
    ACTIVE_CHATS.add(message.peer_id)
    save_data(DB_FILE, list(ACTIVE_CHATS))
    await message.answer("[id870757778|Специальный Руководитель Misha Manlix] синхронизировал Беседу с Базой данных!")

@bot.on.message(text="/gstaff")
async def gstaff_handler(message: Message):
    if not await check_active(message) or not has_access(message.from_id, "Зам. Специального Руководителя"): return
    res = ("MANLIX MANAGER | Команда Бота:\n\n| Специальный Руководитель:\n– [id870757778|Misha Manlix]\n\n"
           "| Основной зам. Спец. Руководителя:\n– Отсутствует.\n\n| Зам. Спец. Руководителя:\n– Отсутствует.\n– Отсутствует.")
    await message.answer(res)

# --- 6. КОМАНДЫ МОДЕРАЦИИ ---

@bot.on.message(text=["/mute", "/mute <args>"])
async def mute_handler(message: Message, args=None):
    if not await check_active(message) or not has_access(message.from_id, "Модератор"): return
    
    target_id = message.reply_message.from_id if message.reply_message else extract_id(args)
    if not target_id: return "Укажите пользователя!"

    time_min, reason = 30, "Не указана"
    if args:
        nums = re.findall(r'\d+', args)
        if message.reply_message:
            if nums: time_min = int(nums[0])
            reason = re.sub(r'^\d+\s*', '', args).strip() or "Не указана"
        else:
            if len(nums) >= 2: time_min = int(nums[1])
            reason = re.sub(r'\[.*?\]|id\d+|\d+', '', args).strip() or "Не указана"

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
    if not target_id: return
    
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

# --- 7. ОБРАБОТЧИК КНОПОК ---

@bot.on.message(func=lambda message: getattr(message, "payload", None) is not None)
async def payload_handler(message: Message):
    if not has_access(message.from_id, "Модератор"): return
    try:
        pl = json.loads(message.payload)
        if pl.get("cmd") == "unmute":
            tid = str(pl.get("target"))
            if tid in ACTIVE_MUTES: 
                del ACTIVE_MUTES[tid]
                save_data(MUTES_FILE, ACTIVE_MUTES)
            await message.answer(f"[id{message.from_id}|Модератор MANLIX] снял(-а) мут [id{tid}|пользователю]")
    except: pass

# --- СЕРВЕР ---
class Handler(BaseHTTPRequestHandler):
    def do_GET(self): self.send_response(200); self.end_headers(); self.wfile.write(b"OK")
threading.Thread(target=lambda: HTTPServer(('0.0.0.0', int(os.environ.get("PORT", 10000))), Handler).serve_forever(), daemon=True).start()
bot.run_forever()

import os
import threading
import re
import time
import json
import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from vkbottle.bot import Bot, Message
from vkbottle import Keyboard, KeyboardButtonColor, Text, BaseMiddleware

# --- 1. ДАННЫЕ ---
USER_DATA = {
    870757778: ["Специальный Руководитель", "Misha Manlix"],
}

DB_FILE = "chats_db.json"
MUTES_FILE = "mutes.json"

def load_data(file, default):
    if os.path.exists(file):
        try:
            with open(file, "r", encoding="utf-8") as f: return json.load(f)
        except: return default
    return default

def save_data(file, data):
    try:
        with open(file, "w", encoding="utf-8") as f: 
            json.dump(data, f, ensure_ascii=False, indent=4)
    except Exception as e: print(f"Ошибка сохранения {file}: {e}")

ACTIVE_CHATS = set(load_data(DB_FILE, []))
ACTIVE_MUTES = load_data(MUTES_FILE, {})

RANK_WEIGHT = {
    "Пользователь": 0, "Модератор": 1, "Старший Модератор": 2, 
    "Администратор": 3, "Старший Администратор": 4, "Зам. Спец. Администратора": 5,
    "Спец. Администратор": 6, "Владелец": 7, "Зам. Специального Руководителя": 8,
    "Основной зам. Специального Руководителя": 9, "Специальный Руководитель": 10
}

# --- 2. ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---
def get_rank(user_id):
    return USER_DATA.get(int(user_id), ["Пользователь"])[0]

def has_access(user_id, required_rank):
    user_rank = get_rank(user_id)
    return RANK_WEIGHT.get(user_rank, 0) >= RANK_WEIGHT.get(required_rank, 0)

def extract_id(text):
    if not text: return None
    match = re.search(r'id(\d+)', str(text))
    if match: return int(match.group(1))
    digits = re.findall(r'\d+', str(text))
    if digits: return int(digits[0])
    return None

async def check_active(message: Message):
    if int(message.from_id) == 870757778: return True
    if message.peer_id not in ACTIVE_CHATS:
        await message.answer("Владелец беседы не является командой Бота, я не буду здесь работать.")
        return False
    return True

# --- 3. ИНИЦИАЛИЗАЦИЯ И МИДЛВАР ---
bot = Bot(token=os.environ.get("TOKEN"))

class MuteMiddleware(BaseMiddleware):
    async def pre(self):
        if self.event.from_id is None: return
        uid_str = str(self.event.from_id)
        if uid_str in ACTIVE_MUTES:
            if time.time() < ACTIVE_MUTES[uid_str]:
                try:
                    await self.event.ctx_api.messages.delete(
                        cmids=[self.event.conversation_message_id],
                        peer_id=self.event.peer_id, delete_for_all=True
                    )
                except: pass
                self.stop("Muted")
            else:
                del ACTIVE_MUTES[uid_str]
                save_data(MUTES_FILE, ACTIVE_MUTES)

bot.labeler.message_view.middlewares.append(MuteMiddleware)

# --- 4. КОМАНДЫ МОДЕРАЦИИ ---

@bot.on.message(text=["/kick", "/kick <args>"])
async def kick_handler(message: Message, args=None):
    if not await check_active(message) or not has_access(message.from_id, "Модератор"): return
    target_id = message.reply_message.from_id if message.reply_message else extract_id(args)
    if not target_id: return "Укажите пользователя!"
    try:
        await bot.api.messages.remove_chat_user(chat_id=message.peer_id - 2000000000, user_id=target_id)
        await message.answer(f"[id{message.from_id}|Модератор MANLIX] исключил(-а) [id{target_id}|пользователя] из Беседы.")
    except Exception as e: await message.answer(f"Ошибка исключения: {e}")

@bot.on.message(text=["/mute", "/mute <args>"])
async def mute_handler(message: Message, args=None):
    if not await check_active(message) or not has_access(message.from_id, "Модератор"): return
    target_id = message.reply_message.from_id if message.reply_message else extract_id(args)
    if not target_id: return "Укажите пользователя!"
    time_min, reason = 30, "Не указана"
    if args:
        clean_args = re.sub(r'\[id\d+\|.*?\]|id\d+|https://vk.com/\S+', '', args).strip()
        parts = clean_args.split(maxsplit=1)
        if parts:
            if parts[0].isdigit():
                time_min = int(parts[0])
                if len(parts) > 1: reason = parts[1]
            else: reason = clean_args
    end_ts = time.time() + (time_min * 60)
    ACTIVE_MUTES[str(target_id)] = end_ts
    save_data(MUTES_FILE, ACTIVE_MUTES)
    date_str = datetime.datetime.fromtimestamp(end_ts + 3*3600).strftime("%d/%m/%Y %H:%M:%S")
    kb = Keyboard(inline=True).add(Text("Снять мут", {"cmd": "unmute", "target": target_id}), color=KeyboardButtonColor.POSITIVE)
    kb.add(Text("Очистить", {"cmd": "clear"}), color=KeyboardButtonColor.NEGATIVE)
    await message.answer(f"[id{message.from_id}|Модератор MANLIX] выдал(-а) мут [id{target_id}|пользователю]\nПричина: {reason}\nМут выдан до: {date_str}", keyboard=kb)

@bot.on.message(text=["/unmute", "/unmute <args>"])
async def unmute_cmd(message: Message, args=None):
    if not await check_active(message) or not has_access(message.from_id, "Модератор"): return
    tid = message.reply_message.from_id if message.reply_message else extract_id(args)
    if tid and str(tid) in ACTIVE_MUTES:
        del ACTIVE_MUTES[str(tid)]; save_data(MUTES_FILE, ACTIVE_MUTES)
        await message.answer(f"[id{message.from_id}|Модератор MANLIX] снял блокировку чата [id{tid}|пользователю].")

# --- 5. ОБРАБОТЧИК PAYLOAD ---
@bot.on.message(func=lambda message: message.payload is not None)
async def payload_handler(message: Message):
    if not has_access(message.from_id, "Модератор"): return
    try:
        pl = json.loads(message.payload)
        if pl.get("cmd") == "unmute":
            tid = str(pl.get("target"))
            if tid in ACTIVE_MUTES: 
                del ACTIVE_MUTES[tid]; save_data(MUTES_FILE, ACTIVE_MUTES)
                await message.answer(f"[id{message.from_id}|Модератор MANLIX] снял мут с [id{tid}|пользователя]")
        elif pl.get("cmd") == "clear":
            await bot.api.messages.delete(cmids=[message.conversation_message_id], peer_id=message.peer_id, delete_for_all=True)
    except: pass

# --- 6. ИНФО-КОМАНДЫ И HELP ---

@bot.on.message(text="/help")
async def help_handler(message: Message):
    if not await check_active(message): return
    msg1 = (
        "Команды пользователей:\n/info - официальные ресурсы \n/stats - статистика пользователя \n/getid - оригинальная ссылка VK.\n\n"
        "Команды для модераторов:\n/staff\n/kick - исключить пользователя из Беседы. \n/mute - выдать Блокировку чата. \n/unmute - снять Блокировку чата. \n\n"
        "Команды старших модераторов: \nОтсутствуют. \n\nКоманды администраторов:\nОтсутствуют. \n\nКоманды старших администраторов: \nОтсутствуют.\n\n"
        "Команды заместителей спец. администраторов: \nОтсутствуют.\n\nКоманды спец. администраторов:\nОтсутствуют. \n\nКоманды владельца:\nОтсутствуют."
    )
    msg2 = (
        "Команды руководства Бота:\n\nЗам. Спец. Руководителя:\n/gstaff - руководство Бота.\n/gbanpl - Блокировка пользователя во всех игровых Беседах.\n/gunbanpl - снятие Блокировки во всех игровых Беседах.\n\n"
        "Основной Зам. Спец. Руководителя:\nОтсутствуют.\n\nСпец. Руководителя: \n/start - активировать Беседу.\n/sync - синхронизация с базой данных."
    )
    await message.answer(msg1); await message.answer(msg2)

@bot.on.message(text="/staff")
async def staff_handler(message: Message):
    if not await check_active(message) or not has_access(message.from_id, "Модератор"): return
    ranks = ["Владелец", "Спец. Администратор", "Зам. Спец. Администратора", "Старший Администратор", "Администратор", "Старший Модератор", "Модератор"]
    d = {r: [] for r in ranks}
    for k, v in USER_DATA.items():
        if v[0] in d: d[v[0]].append(f"[id{k}|{v[1]}]")
    res = ""
    for r in ranks:
        res += f"{r}: \n" + ("\n".join([f"– {x}" for x in d[r]]) if d[r] else "– Отсутствует.") + "\n\n"
    await message.answer(res.strip())

@bot.on.message(text="/gstaff")
async def gstaff_handler(message: Message):
    if not await check_active(message) or not has_access(message.from_id, "Зам. Специального Руководителя"): return
    hierarchy = {
        "Специальный Руководитель": ["Специальный Руководитель", 1],
        "Основной зам. Специального Руководителя": ["Основной зам. Спец. Руководителя", 1],
        "Зам. Специального Руководителя": ["Зам. Спец. Руководителя", 2]
    }
    staff_data = {role: [] for role in hierarchy.keys()}
    for uid, info in USER_DATA.items():
        role = info[0]
        if role in staff_data: staff_data[role].append(f"[id{uid}|{info[1]}]")
    res = "MANLIX MANAGER | Команда Бота:\n\n"
    for role_key, config in hierarchy.items():
        title, max_slots = config
        res += f"| {title}:\n"
        for person in staff_data[role_key]: res += f"– {person}\n"
        empty_slots = max_slots - len(staff_data[role_key])
        for _ in range(max(0, empty_slots)): res += "– Отсутствует.\n"
        res += "\n"
    await message.answer(res.strip())

@bot.on.message(text=["/getid", "/getid <args>"])
async def getid_handler(message: Message, args=None):
    if not await check_active(message): return
    tid = message.reply_message.from_id if message.reply_message else (extract_id(args) or message.from_id)
    await message.answer(f"Оригинальная ссылка [id{tid}|пользователя]:\nhttps://vk.com/id{tid}")

# --- 7. РУКОВОДСТВО (/START /SYNC) ---

@bot.on.message(text="/start")
async def start_handler(message: Message):
    if not has_access(message.from_id, "Специальный Руководитель"): return
    ACTIVE_CHATS.add(message.peer_id); save_data(DB_FILE, list(ACTIVE_CHATS))
    await message.answer(f"[id{message.from_id}|Модератор MANLIX] активировал Беседу.")

@bot.on.message(text="/sync")
async def sync_handler(message: Message):
    if not has_access(message.from_id, "Специальный Руководитель"): return
    save_data(DB_FILE, list(ACTIVE_CHATS)); save_data(MUTES_FILE, ACTIVE_MUTES)
    await message.answer(f"[id{message.from_id}|Модератор MANLIX] выполнил синхронизацию с базой данных.")

# --- 8. ТЕХНИЧЕСКИЙ СЕРВЕР ---
class Handler(BaseHTTPRequestHandler):
    def do_GET(self): self.send_response(200); self.end_headers(); self.wfile.write(b"ALIVE")

threading.Thread(target=lambda: HTTPServer(('0.0.0.0', int(os.environ.get("PORT", 10000))), Handler).serve_forever(), daemon=True).start()
bot.run_forever()

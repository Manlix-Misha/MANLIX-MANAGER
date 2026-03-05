import os
import threading
import re
import time
import json
import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from vkbottle.bot import Bot, Message
from vkbottle import Keyboard, KeyboardButtonColor, Text, BaseMiddleware

# --- 1. ДАННЫЕ И ЗАГРУЗКА ---
DB_FILE = "chats_db.json"
MUTES_FILE = "mutes.json"
EXTERNAL_DB = "database.json" 

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

def load_external_db():
    return load_data(EXTERNAL_DB, {"chats": {}})

# Инициализация при старте
DATABASE = load_external_db()
ACTIVE_MUTES = load_data(MUTES_FILE, {})
ACTIVE_CHATS_INTERNAL = set(load_data(DB_FILE, []))

RANK_WEIGHT = {
    "Пользователь": 0, "Модератор": 1, "Старший Модератор": 2, 
    "Администратор": 3, "Старший Администратор": 4, "Зам. Спец. Администратора": 5,
    "Спец. Администратор": 6, "Владелец": 7, "Зам. Специального Руководителя": 8,
    "Основной зам. Специального Руководителя": 9, "Специальный Руководитель": 10
}

# --- 2. ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---
def get_rank(peer_id, user_id):
    if int(user_id) == 870757778:
        return "Специальный Руководитель"
    pid_str = str(peer_id)
    if pid_str in DATABASE.get("chats", {}):
        staff = DATABASE["chats"][pid_str].get("staff", {})
        if str(user_id) in staff:
            return staff[str(user_id)][0]
    return "Пользователь"

def has_access(peer_id, user_id, required_rank):
    user_rank = get_rank(peer_id, user_id)
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
    if str(message.peer_id) not in DATABASE.get("chats", {}) and message.peer_id not in ACTIVE_CHATS_INTERNAL:
        await message.answer("Беседа не активирована в системе MANLIX.")
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
                del ACTIVE_MUTES[uid_str]; save_data(MUTES_FILE, ACTIVE_MUTES)

bot.labeler.message_view.middlewares.append(MuteMiddleware)

# --- 4. КОМАНДЫ ПОМОЩИ ---

@bot.on.message(text="/help")
async def help_handler(message: Message):
    if not await check_active(message): return
    
    # Первое сообщение: Команды состава беседы
    msg1 = (
        "Команды пользователей:\n"
        "/info - официальные ресурсы\n"
        "/stats - статистика пользователя\n"
        "/getid - оригинальная ссылка VK.\n\n"
        "Команды для модераторов:\n"
        "/staff - Руководство Беседы\n"
        "/kick - исключить пользователя из Беседы.\n"
        "/mute - выдать Блокировку чата.\n"
        "/unmute - снять Блокировку чата.\n\n"
        "Команды старших модераторов:\nОтсутствуют.\n\n"
        "Команды администраторов:\nОтсутствуют.\n\n"
        "Команды старших администраторов:\nОтсутствуют.\n\n"
        "Команды заместителей спец. администраторов:\nОтсутствуют.\n\n"
        "Команды спец. администраторов:\nОтсутствуют.\n\n"
        "Команды владельца:\nОтсутствуют."
    )
    
    # Второе сообщение: Глобальное руководство
    msg2 = (
        "Команды руководства Бота:\n\n"
        "Зам. Спец. Руководителя:\n"
        "/gstaff - руководство Бота.\n"
        "/gbanpl - Блокировка пользователя во всех игровых Беседах.\n"
        "/gunbanpl - снятие Блокировки во всех игровых Беседах.\n\n"
        "Основной Зам. Спец. Руководителя:\n"
        "Отсутствуют.\n\n"
        "Спец. Руководителя:\n"
        "/start - активировать Беседу.\n"
        "/sync - синхронизация с базой данных."
    )
    
    await message.answer(msg1)
    await message.answer(msg2)

# --- 5. ОСНОВНЫЕ КОМАНДЫ (СТАФФ И МОДЕРАЦИЯ) ---

@bot.on.message(text="/staff")
async def staff_handler(message: Message):
    if not await check_active(message): return
    pid_str = str(message.peer_id)
    ranks = ["Владелец", "Спец. Администратор", "Зам. Спец. Администратора", "Старший Администратор", "Администратор", "Старший Модератор", "Модератор"]
    chat_data = DATABASE.get("chats", {}).get(pid_str, {})
    staff_in_chat = chat_data.get("staff", {})
    response = []
    for rank in ranks:
        members = [f"– [id{uid}|{info[1]}]" for uid, info in staff_in_chat.items() if info[0] == rank]
        response.append(f"{rank}:")
        response.append("\n".join(members) if members else "– Отсутствует.")
        response.append("")
    await message.answer("\n".join(response).strip())

@bot.on.message(text=["/kick", "/kick <args>"])
async def kick_handler(message: Message, args=None):
    if not await check_active(message) or not has_access(message.peer_id, message.from_id, "Модератор"): return
    tid = message.reply_message.from_id if message.reply_message else extract_id(args)
    if not tid: return "Укажите пользователя!"
    try:
        await bot.api.messages.remove_chat_user(chat_id=message.peer_id - 2000000000, user_id=tid)
        await message.answer(f"[id{message.from_id}|Модератор] исключил(-а) [id{tid}|пользователя] из Беседы.")
    except Exception as e: await message.answer(f"Ошибка исключения: {e}")

@bot.on.message(text=["/mute", "/mute <args>"])
async def mute_handler(message: Message, args=None):
    if not await check_active(message) or not has_access(message.peer_id, message.from_id, "Модератор"): return
    tid = message.reply_message.from_id if message.reply_message else extract_id(args)
    if not tid: return "Укажите пользователя!"
    ACTIVE_MUTES[str(tid)] = time.time() + (30 * 60); save_data(MUTES_FILE, ACTIVE_MUTES)
    await message.answer(f"Мут на 30 мин выдан [id{tid}|пользователю]")

# --- 6. ДОПОЛНИТЕЛЬНЫЕ И ГЛОБАЛЬНЫЕ КОМАНДЫ ---

@bot.on.message(text="/chatid")
async def get_peer_id_handler(message: Message):
    if has_access(message.peer_id, message.from_id, "Специальный Руководитель"):
        await message.answer(f"| Айди Беседы:  « {message.peer_id} »")

@bot.on.message(text="/sync")
async def sync_handler(message: Message):
    if not has_access(message.peer_id, message.from_id, "Специальный Руководитель"): return
    global DATABASE
    DATABASE = load_external_db()
    await message.answer("Синхронизация с GitHub завершена успешно.")

@bot.on.message(text="/getid")
async def getid_handler(message: Message):
    tid = message.reply_message.from_id if message.reply_message else message.from_id
    await message.answer(f"Оригинальная ссылка VK:\nhttps://vk.com/id{tid}")

@bot.on.message(text="/stats")
async def stats_handler(message: Message):
    role = get_rank(message.peer_id, message.from_id)
    await message.answer(f"Статистика пользователя:\nРоль: {role}\nID: {message.from_id}")

@bot.on.message(text="/info")
async def info_handler(message: Message):
    await message.answer("Официальные ресурсы:\n(Здесь можно добавить ссылки на группу или правила)")

# --- 7. ТЕХНИЧЕСКИЙ СЕРВЕР ---
class Handler(BaseHTTPRequestHandler):
    def do_GET(self): self.send_response(200); self.end_headers(); self.wfile.write(b"ALIVE")

threading.Thread(target=lambda: HTTPServer(('0.0.0.0', int(os.environ.get("PORT", 10000))), Handler).serve_forever(), daemon=True).start()
bot.run_forever()

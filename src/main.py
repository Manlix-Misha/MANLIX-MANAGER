import os
import threading
import re
import time
import json
import base64
import aiohttp
from http.server import HTTPServer, BaseHTTPRequestHandler
from vkbottle.bot import Bot, Message
from vkbottle.dispatch.rules.base import ChatActionRule

# --- 1. НАСТРОЙКИ ---
GH_TOKEN = os.environ.get("GH_TOKEN")
GH_REPO = os.environ.get("GH_REPO")
GH_PATH = "database.json"
EXTERNAL_DB = "database.json"
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
    except Exception as e: print(f"Ошибка сохранения: {e}")

def load_external_db():
    return load_data(EXTERNAL_DB, {"chats": {}})

DATABASE = load_external_db()
ACTIVE_MUTES = load_data(MUTES_FILE, {})

RANK_WEIGHT = {
    "Пользователь": 0, "Модератор": 1, "Старший Модератор": 2, 
    "Администратор": 3, "Старший Администратор": 4, "Зам. Спец. Администратора": 5,
    "Спец. Администратор": 6, "Владелец": 7, "Зам. Специального Руководителя": 8,
    "Основной зам. Специального Руководителя": 9, "Специальный Руководитель": 10
}

# --- 2. ФУНКЦИИ GITHUB ---

async def push_to_github(updated_db, message_text="Update database"):
    url = f"https://api.github.com/repos/{GH_REPO}/contents/{GH_PATH}"
    headers = {"Authorization": f"token {GH_TOKEN}", "Accept": "application/vnd.github.v3+json"}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers) as resp:
                if resp.status != 200: return False
                sha = (await resp.json())['sha']
            new_content = base64.b64encode(json.dumps(updated_db, ensure_ascii=False, indent=4).encode('utf-8')).decode('utf-8')
            payload = {"message": message_text, "content": new_content, "sha": sha}
            async with session.put(url, headers=headers, json=payload) as put_resp:
                return put_resp.status == 200
    except: return False

async def update_github_db(new_chat_id, title):
    pid_str = str(new_chat_id)
    if pid_str not in DATABASE["chats"]:
        DATABASE["chats"][pid_str] = {
            "manlix_id": len(DATABASE["chats"]) + 1,
            "title": title, "type": "Не указан",
            "staff": { "870757778": ["Специальный Руководитель", "Misha Manlix"] }
        }
        return await push_to_github(DATABASE, f"Add chat {pid_str}")
    return True

# --- 3. ЛОГИКА ---

def get_rank(peer_id, user_id):
    if int(user_id) == 870757778: return "Специальный Руководитель"
    pid_str = str(peer_id)
    staff = DATABASE.get("chats", {}).get(pid_str, {}).get("staff", {})
    return staff.get(str(user_id), ["Пользователь"])[0]

def has_access(peer_id, user_id, required_rank):
    return RANK_WEIGHT.get(get_rank(peer_id, user_id), 0) >= RANK_WEIGHT.get(required_rank, 0)

async def check_active(message: Message):
    if int(message.from_id) == 870757778: return True
    if str(message.peer_id) not in DATABASE.get("chats", {}):
        await message.answer("Владелец беседы — не член команды бота, я не буду здесь работать!\n\nОбратитесь к: [id870757778|Специальный руководитель].")
        return False
    return True

def extract_id(text):
    if not text: return None
    match = re.search(r'id(\d+)', str(text))
    if match: return int(match.group(1))
    digits = re.findall(r'\d+', str(text))
    return int(digits[0]) if digits else None

async def change_staff_rank(message, target_id, new_rank):
    if not target_id: return await message.answer("Укажите пользователя!")
    pid_str = str(message.peer_id)
    try:
        u_info = await bot.api.users.get(user_ids=[target_id])
        name = f"{u_info[0].first_name} {u_info[0].last_name}"
        
        DATABASE["chats"][pid_str]["staff"][str(target_id)] = [new_rank, name]
        if await push_to_github(DATABASE, f"Set {new_rank} for {target_id}"):
            await message.answer(f"[id{message.from_id}|Ник] изменил(-а) уровень прав [id{target_id}|пользователю]")
        else:
            await message.answer("Ошибка сохранения на GitHub!")
    except Exception as e:
        await message.answer(f"Произошла ошибка: {e}")

# --- 4. БОТ ---
bot = Bot(token=os.environ.get("TOKEN"))

@bot.on.chat_message(ChatActionRule("chat_invite_user"))
async def invite_handler(message: Message):
    if message.action.member_id == - (await bot.api.groups.get_by_id())[0].id:
        await message.answer("Бот добавлен в беседу, выдайте мне администратора, а затем введите /sync для синхронизации c базой данных!\n\nТакже с помощью /type Вы можете выбрать тип беседы!")

@bot.on.message(text="/getid")
@bot.on.message(text="/getid <args>")
async def getid_handler(message: Message, args=None):
    if not await check_active(message): return
    # Если есть ответ на сообщение, берем ID оттуда, иначе из аргументов, иначе свой ID
    target_id = message.from_id
    if message.reply_message:
        target_id = message.reply_message.from_id
    elif args:
        extracted = extract_id(args)
        if extracted: target_id = extracted
        
    try:
        u_info = await bot.api.users.get(user_ids=[target_id])
        name = f"{u_info[0].first_name} {u_info[0].last_name}"
        await message.answer(f"Оригинальная ссылка [id{target_id}|{name}]: https://vk.com/id{target_id}")
    except:
        await message.answer(f"Оригинальная ссылка: https://vk.com/id{target_id}")

@bot.on.message(text="/start")
async def start_handler(message: Message):
    if not has_access(message.peer_id, message.from_id, "Специальный Руководитель"): return
    c_info = await bot.api.messages.get_conversations_by_id(peer_ids=[message.peer_id])
    title = c_info.items[0].chat_settings.title if c_info.items else "Беседа MANLIX"
    if await update_github_db(message.peer_id, title):
        global DATABASE
        DATABASE = load_external_db()
        await message.answer("Вы успешно активировали Беседу!")

# --- 5. НАЗНАЧЕНИЯ ---
@bot.on.message(text=["/addmoder", "/addmoder <args>"])
async def add_m(message: Message, args=None):
    if await check_active(message) and has_access(message.peer_id, message.from_id, "Старший Модератор"):
        tid = message.reply_message.from_id if message.reply_message else extract_id(args)
        await change_staff_rank(message, tid, "Модератор")

@bot.on.message(text=["/addsenmoder", "/addsenmoder <args>"])
async def add_sm(message: Message, args=None):
    if await check_active(message) and has_access(message.peer_id, message.from_id, "Администратор"):
        tid = message.reply_message.from_id if message.reply_message else extract_id(args)
        await change_staff_rank(message, tid, "Старший Модератор")

@bot.on.message(text=["/addadmin", "/addadmin <args>"])
async def add_a(message: Message, args=None):
    if await check_active(message) and has_access(message.peer_id, message.from_id, "Старший Администратор"):
        tid = message.reply_message.from_id if message.reply_message else extract_id(args)
        await change_staff_rank(message, tid, "Администратор")

@bot.on.message(text=["/addsa", "/addsa <args>"])
async def add_sa(message: Message, args=None):
    if await check_active(message) and has_access(message.peer_id, message.from_id, "Владелец"):
        tid = message.reply_message.from_id if message.reply_message else extract_id(args)
        await change_staff_rank(message, tid, "Спец. Администратор")

@bot.on.message(text=["/addowner", "/addowner <args>"])
async def add_o(message: Message, args=None):
    if has_access(message.peer_id, message.from_id, "Зам. Специального Руководителя"):
        tid = message.reply_message.from_id if message.reply_message else extract_id(args)
        await change_staff_rank(message, tid, "Владелец")

# --- 6. МОДЕРАЦИЯ И СЕРВИС ---
@bot.on.message(text="/staff")
async def staff_handler(message: Message):
    if not await check_active(message): return
    pid_str = str(message.peer_id)
    ranks = ["Владелец", "Спец. Администратор", "Зам. Спец. Администратора", "Старший Администратор", "Администратор", "Старший Модератор", "Модератор"]
    staff_in_chat = DATABASE.get("chats", {}).get(pid_str, {}).get("staff", {})
    res = []
    for r in ranks:
        m = [f"– [id{u}|{i[1]}]" for u, i in staff_in_chat.items() if i[0] == r]
        res.append(f"{r}:\n" + ("\n".join(m) if m else "– Отсутствует.") + "\n")
    await message.answer("\n".join(res).strip())

@bot.on.message(text="/sync")
async def sync_handler(message: Message):
    if has_access(message.peer_id, message.from_id, "Специальный Руководитель"):
        global DATABASE
        DATABASE = load_external_db()
        await message.answer("Синхронизация с GitHub завершена успешно.")

@bot.on.message(text="/help")
async def help_handler(message: Message):
    if not await check_active(message): return
    m1 = "Команды пользователей:\n/info - официальные ресурсы\n/stats - статистика пользователя\n/getid - оригинальная ссылка VK.\n\nКоманды для модераторов:\n/staff - Руководство Беседы\n/kick - исключить пользователя из Беседы.\n/mute - выдать Блокировку чата.\n/unmute - снять Блокировку чата.\n\nКоманды старших модераторов:\nОтсутствуют.\n\nКоманды администраторов:\nОтсутствуют.\n\nКоманды старших администраторов:\nОтсутствуют.\n\nКоманды заместителей спец. администраторов:\nОтсутствуют.\n\nКоманды спец. администраторов:\nОтсутствуют.\n\nКоманды владельца:\nОтсутствуют."
    m2 = "Команды руководства Бота:\n\nЗам. Спец. Руководителя:\n/gstaff - руководство Бота.\n/gbanpl - Блокировка пользователя во всех игровых Беседах.\n/gunbanpl - снятие Блокировки во всех игровых Беседах.\n\nОсновной Зам. Спец. Руководителя:\nОтсутствуют.\n\nСпец. Руководителя:\n/start - активировать Беседу.\n/sync - синхронизация с базой данных."
    await message.answer(m1); await message.answer(m2)

# --- ТЕХНИЧЕСКИЙ СЕРВЕР ---
class Handler(BaseHTTPRequestHandler):
    def do_GET(self): self.send_response(200); self.end_headers(); self.wfile.write(b"ALIVE")
threading.Thread(target=lambda: HTTPServer(('0.0.0.0', int(os.environ.get("PORT", 10000))), Handler).serve_forever(), daemon=True).start()
bot.run_forever()

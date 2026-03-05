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
GH_PATH = os.environ.get("GH_PATH", "database.json")
EXTERNAL_DB = "database.json"

def load_data(file, default):
    if os.path.exists(file):
        try:
            with open(file, "r", encoding="utf-8") as f: return json.load(f)
        except: return default
    return default

def load_external_db():
    return load_data(EXTERNAL_DB, {"chats": {}})

DATABASE = load_external_db()

RANK_WEIGHT = {
    "Пользователь": 0, "Модератор": 1, "Старший Модератор": 2, 
    "Администратор": 3, "Старший Администратор": 4, "Зам. Спец. Администратора": 5,
    "Спец. Администратор": 6, "Владелец": 7, "Зам. Специального Руководителя": 8,
    "Основной зам. Специального Руководителя": 9, "Специальный Руководитель": 10
}

# --- 2. РАБОТА С GITHUB ---

async def push_to_github(updated_db, message_text="Update database"):
    url = f"https://api.github.com/repos/{GH_REPO}/contents/{GH_PATH}"
    headers = {"Authorization": f"token {GH_TOKEN}", "Accept": "application/vnd.github.v3+json"}
    try:
        connector = aiohttp.TCPConnector(ssl=False)
        async with aiohttp.ClientSession(connector=connector) as session:
            async with session.get(url, headers=headers) as resp:
                if resp.status != 200: return f"Ошибка GitHub: {resp.status}"
                res_json = await resp.json()
                sha = res_json['sha']

            new_json_str = json.dumps(updated_db, ensure_ascii=False, indent=4)
            new_content = base64.b64encode(new_json_str.encode('utf-8')).decode('utf-8')
            payload = {"message": message_text, "content": new_content, "sha": sha}
            async with session.put(url, headers=headers, json=payload) as put_resp:
                return True if put_resp.status in [200, 201] else f"Ошибка записи: {put_resp.status}"
    except Exception as e: return f"Сбой: {str(e)}"

# --- 3. ПРОВЕРКИ И ПРАВА ---

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
        text = (
            "Владелец беседы — не член команды бота, я не буду здесь работать!\n\n"
            "Чтобы я начал работу в данном чате тебе нужно обратиться к моему "
            "специальному руководителю написать ему или пригласи его в данный чат! "
            "Вк его: [https://vk.com/id870757778|Специальный руководитель]."
        )
        await message.answer(text)
        return False
    return True

def extract_id(text):
    if not text: return None
    match = re.search(r'id(\d+)', str(text))
    if match: return int(match.group(1))
    digits = re.findall(r'\d+', str(text))
    return int(digits[0]) if digits else None

async def change_rank(message, target_id, new_rank):
    if not target_id: return await message.answer("Укажите пользователя!")
    pid_str = str(message.peer_id)
    try:
        u_info = await bot.api.users.get(user_ids=[target_id])
        name = f"{u_info[0].first_name} {u_info[0].last_name}"
        DATABASE["chats"][pid_str]["staff"][str(target_id)] = [new_rank, name]
        res = await push_to_github(DATABASE, f"Set {new_rank} for {target_id}")
        if res is True:
            await message.answer(f"[id{message.from_id}|Ник] изменил(-а) уровень прав [id{target_id}|пользователю]")
        else: await message.answer(f"Ошибка сохранения: {res}")
    except Exception as e: await message.answer(f"Ошибка: {e}")

# --- 4. ОСНОВНЫЕ КОМАНДЫ ---

bot = Bot(token=os.environ.get("TOKEN"))

@bot.on.chat_message(ChatActionRule("chat_invite_user"))
async def invite_handler(message: Message):
    g_info = await bot.api.groups.get_by_id()
    if message.action.member_id == -g_info[0].id:
        await message.answer("Бот добавлен в беседу, выдайте мне администратора, а затем введите /sync для синхронизации c базой данных!\n\nТакже с помощью /type Вы можете выбрать тип беседы!")

@bot.on.message(text="/start")
async def start_handler(message: Message):
    if int(message.from_id) != 870757778: return
    c_info = await bot.api.messages.get_conversations_by_id(peer_ids=[message.peer_id])
    title = c_info.items[0].chat_settings.title if c_info.items else "Беседа MANLIX"
    pid_str = str(message.peer_id)
    if pid_str not in DATABASE.get("chats", {}):
        if "chats" not in DATABASE: DATABASE["chats"] = {}
        DATABASE["chats"][pid_str] = {
            "manlix_id": len(DATABASE["chats"]) + 1, "title": title, "type": "Не указан",
            "staff": { "870757778": ["Специальный Руководитель", "Misha Manlix"] }
        }
        res = await push_to_github(DATABASE, f"Start chat {pid_str}")
        if res is True: await message.answer("Вы успешно активировали Беседу!")
        else: await message.answer(f"Ошибка активации: {res}")
    else: await message.answer("Беседа уже активирована.")

@bot.on.message(text="/sync")
async def sync_handler(message: Message):
    if not has_access(message.peer_id, message.from_id, "Специальный Руководитель"): return
    global DATABASE
    DATABASE = load_external_db()
    await message.answer("Синхронизация с GitHub завершена успешно.")

@bot.on.message(text="/getid")
@bot.on.message(text="/getid <args>")
async def getid_handler(message: Message, args=None):
    if not await check_active(message): return
    tid = message.from_id
    if message.reply_message: tid = message.reply_message.from_id
    elif args:
        ext = extract_id(args)
        if ext: tid = ext
    await message.answer(f"Оригинальная ссылка [id{tid}|пользователя]: https://vk.com/id{tid}")

@bot.on.message(text="/gstaff")
async def gstaff_handler(message: Message):
    if not await check_active(message): return
    text = (
        "MANLIX MANAGER | Команда Бота:\n\n"
        "| Специальный Руководитель:\n"
        "– [id870757778|Misha Manlix]\n\n"
        "| Основной зам. Спец. Руководителя:\n"
        "– Отсутствует.\n\n"
        "| Зам. Спец. Руководителя:\n"
        "– Отсутствует.\n"
        "– Отсутствует."
    )
    await message.answer(text)

@bot.on.message(text="/staff")
async def staff_handler(message: Message):
    if not await check_active(message): return
    pid_str = str(message.peer_id)
    ranks = ["Владелец", "Спец. Администратор", "Зам. Спец. Администратора", "Старший Администратор", "Администратор", "Старший Модератор", "Модератор"]
    staff_data = DATABASE.get("chats", {}).get(pid_str, {}).get("staff", {})
    res = []
    for r in ranks:
        m = [f"– [id{u}|{i[1]}]" for u, i in staff_data.items() if i[0] == r]
        res.append(f"{r}:\n" + ("\n".join(m) if m else "– Отсутствует."))
    await message.answer("\n\n".join(res))

# --- 5. КОМАНДЫ НАЗНАЧЕНИЯ ---

@bot.on.message(text=["/addmoder", "/addmoder <args>"])
async def add_mod(m: Message, args=None):
    if await check_active(m) and has_access(m.peer_id, m.from_id, "Старший Модератор"):
        await change_rank(m, m.reply_message.from_id if m.reply_message else extract_id(args), "Модератор")

@bot.on.message(text=["/addsenmoder", "/addsenmoder <args>"])
async def add_sm(m: Message, args=None):
    if await check_active(m) and has_access(m.peer_id, m.from_id, "Администратор"):
        await change_rank(m, m.reply_message.from_id if m.reply_message else extract_id(args), "Старший Модератор")

@bot.on.message(text=["/addadmin", "/addadmin <args>"])
async def add_adm(m: Message, args=None):
    if await check_active(m) and has_access(m.peer_id, m.from_id, "Старший Администратор"):
        await change_rank(m, m.reply_message.from_id if m.reply_message else extract_id(args), "Администратор")

@bot.on.message(text=["/addsenadmin", "/addsenadmin <args>"])
async def add_sa(m: Message, args=None):
    if await check_active(m) and has_access(m.peer_id, m.from_id, "Зам. Спец. Администратора"):
        await change_rank(m, m.reply_message.from_id if m.reply_message else extract_id(args), "Старший Администратор")

@bot.on.message(text=["/addsza", "/addsza <args>"])
async def add_sza(m: Message, args=None):
    if await check_active(m) and has_access(m.peer_id, m.from_id, "Спец. Администратор"):
        await change_rank(m, m.reply_message.from_id if m.reply_message else extract_id(args), "Зам. Спец. Администратора")

@bot.on.message(text=["/addsa", "/addsa <args>"])
async def add_sa_rank(m: Message, args=None):
    if await check_active(m) and has_access(m.peer_id, m.from_id, "Владелец"):
        await change_rank(m, m.reply_message.from_id if m.reply_message else extract_id(args), "Спец. Администратор")

@bot.on.message(text=["/addowner", "/addowner <args>"])
async def add_own(m: Message, args=None):
    if has_access(m.peer_id, m.from_id, "Зам. Специального Руководителя"):
        await change_rank(m, m.reply_message.from_id if m.reply_message else extract_id(args), "Владелец")

# --- 6. МОДЕРАЦИЯ ---

@bot.on.message(text=["/kick", "/kick <args>"])
async def kick_user(m: Message, args=None):
    if not await check_active(m) or not has_access(m.peer_id, m.from_id, "Модератор"): return
    tid = m.reply_message.from_id if m.reply_message else extract_id(args)
    if not tid: return await m.answer("Укажите пользователя!")
    try:
        await bot.api.messages.remove_chat_user(chat_id=m.peer_id-2000000000, user_id=tid)
        await m.answer(f"[id{m.from_id}|Модератор] исключил(-а) [id{tid}|пользователя] из Беседы.")
    except Exception as e: await m.answer(f"Ошибка: {e}")

# --- ТЕХНИЧЕСКИЙ СЕРВЕР ---
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b"ALIVE")

threading.Thread(target=lambda: HTTPServer(('0.0.0.0', int(os.environ.get("PORT", 10000))), Handler).serve_forever(), daemon=True).start()
bot.run_forever()

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

# --- 1. НАСТРОЙКИ (БЕРУТСЯ ИЗ RENDER) ---
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

# Инициализация данных
DATABASE = load_external_db()
ACTIVE_MUTES = load_data(MUTES_FILE, {})

RANK_WEIGHT = {
    "Пользователь": 0, "Модератор": 1, "Старший Модератор": 2, 
    "Администратор": 3, "Старший Администратор": 4, "Зам. Спец. Администратора": 5,
    "Спец. Администратор": 6, "Владелец": 7, "Зам. Специального Руководителя": 8,
    "Основной зам. Специального Руководителя": 9, "Специальный Руководитель": 10
}

# --- 2. ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---

async def update_github_db(new_chat_id, title):
    """Автоматически добавляет чат в database.json на GitHub"""
    url = f"https://api.github.com/repos/{GH_REPO}/contents/{GH_PATH}"
    headers = {"Authorization": f"token {GH_TOKEN}", "Accept": "application/vnd.github.v3+json"}

    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers) as resp:
            if resp.status != 200: return False
            res_data = await resp.json()
            sha = res_data['sha']
            content = base64.b64decode(res_data['content']).decode('utf-8')
            current_db = json.loads(content)

        pid_str = str(new_chat_id)
        if pid_str not in current_db["chats"]:
            m_id = len(current_db["chats"]) + 1
            current_db["chats"][pid_str] = {
                "manlix_id": m_id,
                "title": title,
                "type": "Не указан",
                "staff": { "870757778": ["Владелец", "MANLIX MANAGER"] }
            }
            
            new_content = base64.b64encode(json.dumps(current_db, ensure_ascii=False, indent=4).encode('utf-8')).decode('utf-8')
            payload = {"message": f"Auto-add chat {pid_str}", "content": new_content, "sha": sha}
            async with session.put(url, headers=headers, json=payload) as put_resp:
                return put_resp.status == 200
    return False

def get_rank(peer_id, user_id):
    if int(user_id) == 870757778: return "Специальный Руководитель"
    pid_str = str(peer_id)
    staff = DATABASE.get("chats", {}).get(pid_str, {}).get("staff", {})
    if str(user_id) in staff:
        return staff[str(user_id)][0]
    return "Пользователь"

def has_access(peer_id, user_id, required_rank):
    user_rank = get_rank(peer_id, user_id)
    return RANK_WEIGHT.get(user_rank, 0) >= RANK_WEIGHT.get(required_rank, 0)

async def check_active(message: Message):
    """Проверка активации чата с твоим текстом ошибки"""
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

# --- 3. ИНИЦИАЛИЗАЦИЯ БОТА ---
bot = Bot(token=os.environ.get("TOKEN"))

# Приветствие при добавлении в беседу
@bot.on.chat_message(ChatActionRule("chat_invite_user"))
async def invite_handler(message: Message):
    group_info = await bot.api.groups.get_by_id()
    if message.action.member_id == -group_info[0].id:
        await message.answer(
            "Бот добавлен в беседу, выдайте мне администратора, а затем введите "
            "/sync для синхронизации c базой данных!\n\n"
            "Также с помощью /type Вы можете выбрать тип беседы!"
        )

# --- 4. КОМАНДЫ РУКОВОДСТВА ---

@bot.on.message(text="/start")
async def start_handler(message: Message):
    if not has_access(message.peer_id, message.from_id, "Специальный Руководитель"): return
    chat_info = await bot.api.messages.get_conversations_by_id(peer_ids=[message.peer_id])
    title = chat_info.items[0].chat_settings.title if chat_info.items else "Беседа MANLIX"
    
    if await update_github_db(message.peer_id, title):
        global DATABASE
        DATABASE = load_external_db()
        await message.answer("Вы успешно активировали Беседу!")
    else:
        await message.answer("Ошибка связи с GitHub. Проверьте настройки.")

@bot.on.message(text="/sync")
async def sync_handler(message: Message):
    if not has_access(message.peer_id, message.from_id, "Специальный Руководитель"): return
    global DATABASE
    DATABASE = load_external_db()
    await message.answer("Синхронизация с GitHub завершена успешно.")

@bot.on.message(text="/type")
async def type_handler(message: Message):
    if not await check_active(message): return
    if has_access(message.peer_id, message.from_id, "Специальный Руководитель"):
        await message.answer("Команда настройки типов беседы находится в разработке.")

@bot.on.message(text="/chatid")
async def chatid_handler(message: Message):
    if has_access(message.peer_id, message.from_id, "Специальный Руководитель"):
        await message.answer(f"| Айди Беседы:  « {message.peer_id} »")

# --- 5. КОМАНДЫ ПОМОЩИ И ИНФО ---

@bot.on.message(text="/help")
async def help_handler(message: Message):
    if not await check_active(message): return
    m1 = (
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
    m2 = (
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
    await message.answer(m1)
    await message.answer(m2)

@bot.on.message(text="/staff")
async def staff_handler(message: Message):
    if not await check_active(message): return
    pid_str = str(message.peer_id)
    ranks = ["Владелец", "Спец. Администратор", "Зам. Спец. Администратора", "Старший Администратор", "Администратор", "Старший Модератор", "Модератор"]
    staff_in_chat = DATABASE.get("chats", {}).get(pid_str, {}).get("staff", {})
    res = []
    for r in ranks:
        members = [f"– [id{u}|{i[1]}]" for u, i in staff_in_chat.items() if i[0] == r]
        res.append(f"{r}:")
        res.append("\n".join(members) if members else "– Отсутствует.")
        res.append("")
    await message.answer("\n".join(res).strip())

# --- 6. МОДЕРАЦИЯ ---

@bot.on.message(text=["/kick", "/kick <args>"])
async def kick_handler(message: Message, args=None):
    if not await check_active(message) or not has_access(message.peer_id, message.from_id, "Модератор"): return
    tid = message.reply_message.from_id if message.reply_message else extract_id(args)
    if not tid: return "Укажите пользователя!"
    try:
        await bot.api.messages.remove_chat_user(chat_id=message.peer_id - 2000000000, user_id=tid)
        await message.answer(f"[id{message.from_id}|Модератор] исключил(-а) [id{tid}|пользователя] из Беседы.")
    except Exception as e: await message.answer(f"Ошибка: {e}")

@bot.on.message(text=["/mute", "/mute <args>"])
async def mute_handler(message: Message, args=None):
    if not await check_active(message) or not has_access(message.peer_id, message.from_id, "Модератор"): return
    tid = message.reply_message.from_id if message.reply_message else extract_id(args)
    if not tid: return "Укажите пользователя!"
    ACTIVE_MUTES[str(tid)] = time.time() + (30 * 60)
    save_data(MUTES_FILE, ACTIVE_MUTES)
    await message.answer(f"Мут на 30 мин выдан [id{tid}|пользователю]")

# --- 7. ТЕХНИЧЕСКИЙ СЕРВЕР ---
class Handler(BaseHTTPRequestHandler):
    def do_GET(self): self.send_response(200); self.end_headers(); self.wfile.write(b"ALIVE")
threading.Thread(target=lambda: HTTPServer(('0.0.0.0', int(os.environ.get("PORT", 10000))), Handler).serve_forever(), daemon=True).start()

bot.run_forever()

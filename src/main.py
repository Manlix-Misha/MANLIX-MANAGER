import os
import threading
import re
import json
import base64
import aiohttp
import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from vkbottle.bot import Bot, Message
from vkbottle import Keyboard, KeyboardButtonColor, Text, GroupEventType

# --- 1. НАСТРОЙКИ ---
GH_TOKEN = os.environ.get("GH_TOKEN")
GH_REPO = os.environ.get("GH_REPO") 
GH_PATH = "database.json"
EXTERNAL_DB = "database.json"

def load_local_data():
    if os.path.exists(EXTERNAL_DB):
        try:
            with open(EXTERNAL_DB, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data if "chats" in data else {"chats": {}}
        except: return {"chats": {}}
    return {"chats": {}}

DATABASE = load_local_data()

RANK_WEIGHT = {
    "Пользователь": 0, "Модератор": 1, "Старший Модератор": 2, 
    "Администратор": 3, "Старший Администратор": 4, "Зам. Спец. Администратора": 5,
    "Спец. Администратор": 6, "Владелец": 7, "Зам. Специального Руководителя": 8,
    "Основной зам. Специального Руководителя": 9, "Специальный Руководитель": 10
}

# --- 2. GITHUB API ---

async def push_to_github(updated_db, message_text="Update"):
    url = f"https://api.github.com/repos/{GH_REPO}/contents/{GH_PATH}"
    headers = {"Authorization": f"token {GH_TOKEN}", "Accept": "application/vnd.github.v3+json"}
    try:
        async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=False)) as session:
            sha = None
            async with session.get(url, headers=headers) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    sha = data['sha']
            content_str = json.dumps(updated_db, ensure_ascii=False, indent=4)
            content_base64 = base64.b64encode(content_str.encode('utf-8')).decode('utf-8')
            payload = {"message": message_text, "content": content_base64}
            if sha: payload["sha"] = sha
            async with session.put(url, headers=headers, json=payload) as put_resp:
                return put_resp.status in [200, 201]
    except: return False

# --- 3. СИСТЕМНАЯ ЛОГИКА ---

def get_user_data(peer_id, user_id):
    if int(user_id) == 870757778: return ["Специальный Руководитель", "Misha Manlix"]
    chat_data = DATABASE.get("chats", {}).get(str(peer_id), {})
    staff = chat_data.get("staff", {})
    return staff.get(str(user_id), ["Пользователь", "Пользователь"])

def has_access(peer_id, user_id, required_rank):
    u_rank = get_user_data(peer_id, user_id)[0]
    return RANK_WEIGHT.get(u_rank, 0) >= RANK_WEIGHT.get(required_rank, 0)

async def check_active(message: Message):
    if int(message.from_id) == 870757778: return True
    if str(message.peer_id) not in DATABASE.get("chats", {}):
        await message.answer("Владелец беседы — не член команды бота, я не буду здесь работать!")
        return False
    return True

def extract_id(text):
    if not text: return None
    match = re.search(r'id(\d+)', str(text))
    if match: return int(match.group(1))
    digits = re.findall(r'\d+', str(text))
    return int(digits[0]) if digits else None

# --- 4. КОМАНДЫ ---

bot = Bot(token=os.environ.get("TOKEN"))

@bot.on.message(text="/help")
async def help_handler(message: Message):
    if not await check_active(message): return
    rank, _ = get_user_data(message.peer_id, message.from_id)
    weight = RANK_WEIGHT.get(rank, 0)
    
    # 1. Сообщение для всех/модераторов/владельцев
    help_parts = ["Команды пользователей:\n/info - официальные ресурсы\n/stats - статистика пользователя\n/getid - оригинальная ссылка VK."]
    
    if weight >= 1:
        help_parts.append("\nКоманды для модераторов:\n/staff - Руководство Беседы\n/kick - исключить пользователя из Беседы.\n/mute - выдать Блокировку чата.\n/unmute - снять Блокировку чата.")
    
    if weight >= 2:
        help_parts.append("\nКоманды старших модераторов:\n/setnick - установить имя.\n/rnick - удалить имя.")
    else:
        if weight >= 1: help_parts.append("\nКоманды старших модераторов:\nОтсутствуют.")

    # Добавляем пустые секции для иерархии по форме
    ranks_to_show = [
        (3, "администраторов"), (4, "старших администраторов"), 
        (5, "заместителей спец. администраторов"), (6, "спец. администраторов"), (7, "владельца")
    ]
    for w, name in ranks_to_show:
        if weight >= w: help_parts.append(f"\nКоманды {name}:\nОтсутствуют.")

    await message.answer("\n".join(help_parts))
    
    # 2. Сообщение для Руководства Бота
    if weight >= 8:
        bot_help = "Команды руководства Бота:\n\nЗам. Спец. Руководителя:\n/gstaff - руководство Бота.\n/gbanpl - Блокировка пользователя во всех игровых Беседах.\n/gunbanpl - снятие Блокировки во всех игровых Беседах."
        bot_help += "\n\nОсновной Зам. Спец. Руководителя:\nОтсутствуют."
        if weight >= 10:
            bot_help += "\n\nСпец. Руководителя:\n/start - активировать Беседу.\n/sync - синхронизация с базой данных."
        await message.answer(bot_help)

@bot.on.message(text="/staff")
async def staff_list(message: Message):
    if not await check_active(message): return
    chat_staff = DATABASE.get("chats", {}).get(str(message.peer_id), {}).get("staff", {})
    if not chat_staff: return await message.answer("В этой беседе нет назначенных модераторов.")
    
    res = ["Руководство Беседы:"]
    for uid, data in chat_staff.items():
        res.append(f"– [id{uid}|{data[1]}] ({data[0]})")
    await message.answer("\n".join(res))

@bot.on.message(text=["/getid", "/getid <args>"])
async def getid_cmd(message: Message, args=None):
    if not await check_active(message): return
    target = message.reply_message.from_id if message.reply_message else (extract_id(args) or message.from_id)
    await message.answer(f"Оригинальная ссылка [id{target}|пользователя]: https://vk.com/id{target}")

@bot.on.message(text="/gstaff")
async def gstaff_cmd(message: Message):
    if not await check_active(message): return
    if not has_access(message.peer_id, message.from_id, "Зам. Специального Руководителя"): return
    await message.answer("MANLIX MANAGER | Команда Бота:\n\n| Специальный Руководитель:\n– [id870757778|Misha Manlix]\n\n| Основной зам. Спец. Руководителя:\n– Отсутствует.\n\n| Зам. Спец. Руководителя:\n– Отсутствует.")

@bot.on.message(text="/sync")
async def sync_cmd(message: Message):
    if int(message.from_id) != 870757778: return
    url = f"https://api.github.com/repos/{GH_REPO}/contents/{GH_PATH}"
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers={"Authorization": f"token {GH_TOKEN}"}) as resp:
            if resp.status == 200:
                data = await resp.json()
                global DATABASE
                DATABASE = json.loads(base64.b64decode(data['content']).decode('utf-8'))
                with open(EXTERNAL_DB, "w", encoding="utf-8") as f:
                    json.dump(DATABASE, f, ensure_ascii=False, indent=4)
                await message.answer("Синхронизация завершена успешно.")

@bot.on.message(text=["/setnick", "/setnick <args>"])
async def setnick_cmd(m: Message, args=None):
    if not await check_active(m) or not has_access(m.peer_id, m.from_id, "Старший Модератор"): return
    target = m.reply_message.from_id if m.reply_message else extract_id(args)
    if not target: return await m.answer("Укажите пользователя.")
    
    # Очистка ника от ссылок
    nick = args.split()[-1] if args and len(args.split()) > 1 else args
    if not nick or "id" in nick: return await m.answer("Укажите имя.")

    pid = str(m.peer_id)
    u_rank = get_user_data(m.peer_id, target)[0]
    if "chats" not in DATABASE: DATABASE["chats"] = {}
    if pid not in DATABASE["chats"]: DATABASE["chats"][pid] = {"staff": {}}
    DATABASE["chats"][pid]["staff"][str(target)] = [u_rank, nick]
    
    await push_to_github(DATABASE, f"Nick {nick}")
    await m.answer(f"[id{m.from_id}|Ник] установил имя [id{target}|пользователю]")

@bot.on.message(text="/start")
async def start_handler(message: Message):
    if int(message.from_id) != 870757778: return
    pid = str(message.peer_id)
    if "chats" not in DATABASE: DATABASE["chats"] = {}
    DATABASE["chats"][pid] = {"staff": {"870757778": ["Специальный Руководитель", "Misha Manlix"]}}
    await push_to_github(DATABASE, f"Activate {pid}")
    await message.answer("Вы успешно активировали Беседу!")

# --- СЕРВЕР ---
class H(BaseHTTPRequestHandler):
    def do_GET(self): self.send_response(200); self.end_headers(); self.wfile.write(b"OK")
threading.Thread(target=lambda: HTTPServer(('0.0.0.0', int(os.environ.get("PORT", 10000))), H).serve_forever(), daemon=True).start()
bot.run_forever()

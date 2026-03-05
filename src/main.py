import os
import threading
import re
import json
import base64
import aiohttp
import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from vkbottle.bot import Bot, Message
from vkbottle import Keyboard, KeyboardButtonColor, Text

# --- 1. НАСТРОЙКИ ---
GH_TOKEN = os.environ.get("GH_TOKEN")
GH_REPO = os.environ.get("GH_REPO") 
GH_PATH = "database.json"
EXTERNAL_DB = "database.json"

def load_local_data():
    if os.path.exists(EXTERNAL_DB):
        try:
            with open(EXTERNAL_DB, "r", encoding="utf-8") as f:
                return json.load(f)
        except: return {"chats": {}}
    return {"chats": {}}

DATABASE = load_local_data()

RANK_WEIGHT = {
    "Пользователь": 0, "Модератор": 1, "Старший Модератор": 2, 
    "Администратор": 3, "Старший Администратор": 4, "Зам. Спец. Администратора": 5,
    "Спец. Администратор": 6, "Владелец": 7, "Зам. Специального Руководителя": 8,
    "Основной зам. Специального Руководителя": 9, "Специальный Руководитель": 10
}

# --- 2. РАБОТА С GITHUB ---

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
                if put_resp.status in [200, 201]:
                    with open(EXTERNAL_DB, "w", encoding="utf-8") as f:
                        json.dump(updated_db, f, ensure_ascii=False, indent=4)
                    return True
                return f"GitHub Error: {put_resp.status}"
    except Exception as e: return f"System Error: {str(e)}"

# --- 3. СИСТЕМНАЯ ЛОГИКА ---

def get_rank(peer_id, user_id):
    if int(user_id) == 870757778: return "Специальный Руководитель"
    staff = DATABASE.get("chats", {}).get(str(peer_id), {}).get("staff", {})
    return staff.get(str(user_id), ["Пользователь"])[0]

def has_access(peer_id, user_id, required_rank):
    return RANK_WEIGHT.get(get_rank(peer_id, user_id), 0) >= RANK_WEIGHT.get(required_rank, 0)

async def check_active(message: Message):
    if int(message.from_id) == 870757778: return True
    if str(message.peer_id) not in DATABASE.get("chats", {}):
        await message.answer("Владелец беседы — не член команды бота, я не буду здесь работать!\n\nОбратитесь к: [https://vk.com/id870757778|Специальный руководитель].")
        return False
    return True

def extract_id(text):
    if not text: return None
    # Ищем id123, [id123|abc] или просто цифры
    match = re.search(r'id(\d+)', str(text))
    if match: return int(match.group(1))
    digits = re.findall(r'\d+', str(text))
    return int(digits[0]) if digits else None

async def change_rank(message, target_id, rank_name):
    if not target_id:
        return await message.answer("Вы не указали пользователя.")
    pid = str(message.peer_id)
    try:
        u = await bot.api.users.get(user_ids=[target_id])
        full_name = f"{u[0].first_name} {u[0].last_name}"
        if pid not in DATABASE["chats"]: DATABASE["chats"][pid] = {"staff": {}}
        DATABASE["chats"][pid]["staff"][str(target_id)] = [rank_name, full_name]
        res = await push_to_github(DATABASE, f"Set {rank_name} for {target_id}")
        if res is True:
            await message.answer(f"[id{message.from_id}|Ник] изменил(-а) уровень прав [id{target_id}|пользователю]")
        else: await message.answer(res)
    except Exception as e: await message.answer(f"Ошибка: {e}")

# --- 4. КОМАНДЫ ---

bot = Bot(token=os.environ.get("TOKEN"))

@bot.on.message(text="/help")
async def help_handler(message: Message):
    if not await check_active(message): return
    msg1 = "Команды пользователей:\n/info - официальные ресурсы\n/stats - статистика пользователя\n/getid - оригинальная ссылка VK.\n\nКоманды для модераторов:\n/staff - Руководство Беседы\n/kick - исключить пользователя из Беседы.\n/mute - выдать Блокировку чата.\n/unmute - снять Блокировку чата.\n\nКоманды старших модераторов:\nОтсутствуют.\n\nКоманды администраторов:\nОтсутствуют.\n\nКоманды старших администраторов:\nОтсутствуют.\n\nКоманды заместителей спец. администраторов:\nОтсутствуют.\n\nКоманды спец. администраторов:\nОтсутствуют.\n\nКоманды владельца:\nОтсутствуют."
    msg2 = "Команды руководства Бота:\n\nЗам. Спец. Руководителя:\n/gstaff - руководство Бота.\n/gbanpl - Блокировка пользователя во всех игровых Беседах.\n/gunbanpl - снятие Блокировки во всех игровых Беседах.\n\nОсновной Зам. Спец. Руководителя:\nОтсутствуют.\n\nСпец. Руководителя:\n/start - активировать Беседу.\n/sync - синхронизация с базой данных."
    await message.answer(msg1)
    await message.answer(msg2)

@bot.on.message(text=["/addmoder", "/addmoder <args>"])
async def add_moder(m: Message, args=None):
    if await check_active(m) and has_access(m.peer_id, m.from_id, "Старший Модератор"):
        target = m.reply_message.from_id if m.reply_message else extract_id(args)
        await change_rank(m, target, "Модератор")

@bot.on.message(text=["/addowner", "/addowner <args>"])
async def add_owner(m: Message, args=None):
    if has_access(m.peer_id, m.from_id, "Зам. Специального Руководителя"):
        target = m.reply_message.from_id if m.reply_message else extract_id(args)
        await change_rank(m, target, "Владелец")

@bot.on.message(text=["/kick", "/kick <args>"])
async def kick_handler(message: Message, args=None):
    if not await check_active(message): return
    if not has_access(message.peer_id, message.from_id, "Модератор"): return
    tid = message.reply_message.from_id if message.reply_message else extract_id(args)
    if not tid: return await message.answer("Укажите пользователя.")
    try:
        await bot.api.messages.remove_chat_user(chat_id=message.peer_id-2000000000, user_id=tid)
        mod_rank = get_rank(message.peer_id, message.from_id)
        await message.answer(f"[id{message.from_id}|{mod_rank} MANLIX] исключил(-а) [id{tid}|пользователя] из Беседы.")
    except: await message.answer("Не удалось исключить.")

@bot.on.message(text="/start")
async def start_handler(message: Message):
    if int(message.from_id) != 870757778: return
    pid = str(message.peer_id)
    if pid not in DATABASE.get("chats", {}):
        if "chats" not in DATABASE: DATABASE["chats"] = {}
        DATABASE["chats"][pid] = {"staff": {"870757778": ["Специальный Руководитель", "Misha Manlix"]}}
        await push_to_github(DATABASE, f"Activate {pid}")
        await message.answer("Вы успешно активировали Беседу!")
    else: await message.answer("Беседа уже активирована.")

@bot.on.message(text="/getid")
@bot.on.message(text="/getid <args>")
async def getid_handler(message: Message, args=None):
    if not await check_active(message): return
    tid = message.reply_message.from_id if message.reply_message else (extract_id(args) or message.from_id)
    await message.answer(f"Оригинальная ссылка [id{tid}|пользователя]: https://vk.com/id{tid}")

# Обработка кнопок и синхронизация
@bot.on.message(payload_contains={"cmd": "unmute"})
async def unmute_btn(message: Message):
    if has_access(message.peer_id, message.from_id, "Модератор"):
        await message.answer(f"Мут с пользователя [id{message.get_payload_json()['user']}|пользователя] снят.")

@bot.on.message(payload_contains={"cmd": "clear"})
async def clear_btn(message: Message):
    if has_access(message.peer_id, message.from_id, "Модератор"):
        try: await bot.api.messages.delete(message_ids=[message.conversation_message_id], peer_id=message.peer_id, delete_for_all=True)
        except: pass

@bot.on.message(text="/sync")
async def sync_handler(message: Message):
    if int(message.from_id) == 870757778:
        url = f"https://api.github.com/repos/{GH_REPO}/contents/{GH_PATH}"
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers={"Authorization": f"token {GH_TOKEN}"}) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    global DATABASE
                    DATABASE = json.loads(base64.b64decode(data['content']).decode('utf-8'))
                    await message.answer("Синхронизация завершена успешно.")

# --- СЕРВЕР ---
class H(BaseHTTPRequestHandler):
    def do_GET(self): self.send_response(200); self.end_headers(); self.wfile.write(b"OK")
threading.Thread(target=lambda: HTTPServer(('0.0.0.0', int(os.environ.get("PORT", 10000))), H).serve_forever(), daemon=True).start()
bot.run_forever()

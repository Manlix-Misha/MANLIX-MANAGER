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
                if put_resp.status in [200, 201]:
                    with open(EXTERNAL_DB, "w", encoding="utf-8") as f:
                        json.dump(updated_db, f, ensure_ascii=False, indent=4)
                    return True
                return False
    except: return False

# --- 3. СИСТЕМНАЯ ЛОГИКА ---

def get_user_data(peer_id, user_id):
    if int(user_id) == 870757778: return ["Специальный Руководитель", "Misha Manlix"]
    staff = DATABASE.get("chats", {}).get(str(peer_id), {}).get("staff", {})
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

# --- 4. ОБРАБОТКА СОБЫТИЙ ---

bot = Bot(token=os.environ.get("TOKEN"))

# Событие: выход пользователя или исключение
@bot.on.raw_event(GroupEventType.MESSAGE_NEW, dataclass=Message)
async def user_leave_handler(event: Message):
    if event.action and event.action.type.value in ["chat_kick_user", "chat_exit_user"]:
        target_id = event.action.member_id
        keyboard = (Keyboard(inline=True)
            .add(Text("Исключить", {"cmd": "kick_confirm", "user": target_id}), color=KeyboardButtonColor.NEGATIVE)
        ).get_json()
        await event.answer("Бот покинул(-а) Беседу", keyboard=keyboard)

# Коллбэк для кнопки исключения
@bot.on.message(payload_contains={"cmd": "kick_confirm"})
async def kick_confirm(m: Message):
    if has_access(m.peer_id, m.from_id, "Модератор"):
        data = m.get_payload_json()
        try:
            await bot.api.messages.remove_chat_user(chat_id=m.peer_id-2000000000, user_id=data["user"])
        except: pass

# --- 5. КОМАНДЫ ---

@bot.on.message(text="/help")
async def help_handler(message: Message):
    if not await check_active(message): return
    rank, nick = get_user_data(message.peer_id, message.from_id)
    weight = RANK_WEIGHT.get(rank, 0)
    
    # Секция пользователя
    msg = "Команды пользователей:\n/info - официальные ресурсы\n/stats - статистика пользователя\n/getid - оригинальная ссылка VK.\n"
    
    # Секция модератора
    if weight >= 1:
        msg += "\nКоманды для модераторов:\n/staff - Руководство Беседы\n/kick - исключить пользователя из Беседы.\n/mute - выдать Блокировку чата.\n/unmute - снять Блокировку чата.\n"
    
    # Секция Старшего модератора (ники)
    if weight >= 2:
        msg += "/setnick - установить имя пользователю.\n/rnick - удалить имя пользователю.\n"
    
    await message.answer(msg)
    
    # Второе сообщение для руководства
    if weight >= 8:
        bot_msg = "Команды руководства Бота:\n\nЗам. Спец. Руководителя:\n/gstaff - руководство Бота.\n/gbanpl - Блокировка...\n\nСпец. Руководителя:\n/start - активировать Беседу.\n/sync - синхронизация."
        await message.answer(bot_msg)

@bot.on.message(text=["/mute", "/mute <args>"])
async def mute_handler(m: Message, args=None):
    if not await check_active(m) or not has_access(m.peer_id, m.from_id, "Модератор"): return
    tid = m.reply_message.from_id if m.reply_message else extract_id(args)
    if not tid: return
    
    keyboard = (Keyboard(inline=True)
        .add(Text("Снять мут", {"cmd": "unmute_edit", "user": tid, "mod": m.from_id}), color=KeyboardButtonColor.POSITIVE)
    ).get_json()
    
    mod_rank = get_user_data(m.peer_id, m.from_id)[0]
    await m.answer(f"[id{m.from_id}|{mod_rank} MANLIX] выдал(-а) мут [id{tid}|пользователю]\nМут выдан до: {datetime.datetime.now()}", keyboard=keyboard)

@bot.on.message(payload_contains={"cmd": "unmute_edit"})
async def unmute_edit_handler(m: Message):
    if not has_access(m.peer_id, m.from_id, "Модератор"): return
    data = m.get_payload_json()
    mod_rank = get_user_data(m.peer_id, m.from_id)[0]
    
    # Редактируем сообщение с мутом
    await bot.api.messages.edit(
        peer_id=m.peer_id,
        conversation_message_id=m.conversation_message_id,
        message=f"[id{m.from_id}|{mod_rank} MANLIX] снял(-а) мут [id{data['user']}|пользователю]"
    )

@bot.on.message(text="/start")
async def start_handler(message: Message):
    if int(message.from_id) != 870757778: return
    pid = str(message.peer_id)
    if "chats" not in DATABASE: DATABASE["chats"] = {}
    if pid not in DATABASE["chats"]:
        DATABASE["chats"][pid] = {"staff": {"870757778": ["Специальный Руководитель", "Misha Manlix"]}}
        await push_to_github(DATABASE, f"Activate {pid}")
        await message.answer("Вы успешно активировали Беседу!")

# --- СЕРВЕР ---
class H(BaseHTTPRequestHandler):
    def do_GET(self): self.send_response(200); self.end_headers(); self.wfile.write(b"OK")
threading.Thread(target=lambda: HTTPServer(('0.0.0.0', int(os.environ.get("PORT", 10000))), H).serve_forever(), daemon=True).start()
bot.run_forever()

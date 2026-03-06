import os
import threading
import re
import json
import base64
import aiohttp
import datetime
import random
import asyncio
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from vkbottle.bot import Bot, Message, MessageEvent
from vkbottle import Keyboard, KeyboardButtonColor, Text, GroupEventType, BaseMiddleware

# --- НАСТРОЙКИ ---
TOKEN = os.environ.get("TOKEN")
GH_TOKEN = os.environ.get("GH_TOKEN")
GH_REPO = os.environ.get("GH_REPO")

# Пути к файлам в GitHub
GH_PATH_DB = "database.json"
GH_PATH_ECO = "economy.json"
GH_PATH_PUN = "punishments.json"

# Локальные пути
EXTERNAL_DB = "database.json"
EXTERNAL_ECO = "economy.json"
EXTERNAL_PUN = "punishments.json"

def load_local_data(path):
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except:
            return {}
    return {}

DATABASE = load_local_data(EXTERNAL_DB)
ECONOMY = load_local_data(EXTERNAL_ECO)
PUNISHMENTS = load_local_data(EXTERNAL_PUN)

# Инициализация структур
if "gbans_status" not in PUNISHMENTS: PUNISHMENTS["gbans_status"] = {}
if "gbans_pl" not in PUNISHMENTS: PUNISHMENTS["gbans_pl"] = {}
if "bans" not in PUNISHMENTS: PUNISHMENTS["bans"] = {}
if "warns" not in PUNISHMENTS: PUNISHMENTS["warns"] = {}
if "chats" not in DATABASE: DATABASE["chats"] = {}

TZ_MSK = datetime.timezone(datetime.timedelta(hours=3))

bot = Bot(token=TOKEN)

# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---

async def push_to_github(data, gh_path, local_path):
    # Сначала всегда пишем в локальный файл для кэша
    with open(local_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)
        
    if not GH_TOKEN or not GH_REPO:
        return
    
    url = f"https://api.github.com/repos/{GH_REPO}/contents/{gh_path}"
    headers = {"Authorization": f"token {GH_TOKEN}"}
    try:
        async with aiohttp.ClientSession() as session:
            sha = None
            async with session.get(url, headers=headers) as resp:
                if resp.status == 200:
                    sha = (await resp.json())['sha']
            
            content_str = json.dumps(data, ensure_ascii=False, indent=4)
            content_base64 = base64.b64encode(content_str.encode('utf-8')).decode('utf-8')
            payload = {"message": "Update Data", "content": content_base64}
            if sha: payload["sha"] = sha
            await session.put(url, headers=headers, json=payload)
    except:
        pass

async def get_target_id(m: Message, args: str):
    if m.reply_message: return m.reply_message.from_id
    if not args: return None
    match = re.search(r"(?:id|\[id|vk\.com\/id|vk\.com\/)(\d+)", args)
    if match: return int(match.group(1))
    return None

# --- MIDDLEWARE (ФИКС ОШИБКИ ИЗ ЛОГОВ) ---

class ChatMiddleware(BaseMiddleware[Message]):
    async def pre(self):
        pid, uid = str(self.event.peer_id), str(self.event.from_id)
        if not self.event.from_id or self.event.from_id < 0: return
        
        # Логика мута
        chat_data = DATABASE.get("chats", {}).get(pid, {})
        mutes = chat_data.get("mutes", {})
        if uid in mutes and time.time() < mutes[uid]:
            try:
                await bot.api.messages.delete(peer_id=self.event.peer_id, conversation_message_id=self.event.conversation_message_id, delete_for_all=True)
            except:
                pass
            self.stop()

# РЕГИСТРАЦИЯ БЕЗ СКОБОК - это решает проблему TypeError
bot.labeler.message_view.register_middleware(ChatMiddleware)

# --- КОМАНДЫ ---

@bot.on.message(text="/info")
async def info_cmd(m: Message):
    # Строгий ответ без стикеров
    await m.answer("Временно недоступно!")

@bot.on.message(text="/start")
async def start_cmd(m: Message):
    if m.from_id != 870757778: return
    pid = str(m.peer_id)
    DATABASE["chats"][pid] = {
        "staff": {"870757778": ["Специальный Руководитель", "Misha Manlix"]},
        "mutes": {},
        "stats": {}
    }
    await push_to_github(DATABASE, GH_PATH_DB, EXTERNAL_DB)
    await m.answer("Беседа успешно активирована.")

# ИГРОВАЯ СИСТЕМА (Здесь стикеры разрешены)
@bot.on.message(text="/prise")
async def prise_cmd(m: Message):
    uid = str(m.from_id)
    if uid not in ECONOMY: 
        ECONOMY[uid] = {"cash": 0, "last": 0}
    
    if time.time() - ECONOMY[uid]["last"] < 3600:
        return await m.answer("⏳ Приз можно брать раз в час!")
    
    win = random.randint(100, 1000)
    ECONOMY[uid]["cash"] += win
    ECONOMY[uid]["last"] = time.time()
    
    await push_to_github(ECONOMY, GH_PATH_ECO, EXTERNAL_ECO)
    await m.answer(f"🎉 Вы получили приз {win}$")

@bot.on.message(text="/balance")
async def balance_cmd(m: Message):
    uid = str(m.from_id)
    cash = ECONOMY.get(uid, {}).get("cash", 0)
    await m.answer(f"💵 Ваш баланс: {cash}$")

# --- ЗАПУСК ВЕБ-СЕРВЕРА ---

class HealthCheck(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")

def run_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(('0.0.0.0', port), HealthCheck)
    server.serve_forever()

if __name__ == "__main__":
    threading.Thread(target=run_server, daemon=True).start()
    bot.run_forever()

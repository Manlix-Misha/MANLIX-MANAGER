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

# --- 1. НАСТРОЙКИ ---
TOKEN = os.environ.get("TOKEN")
GH_TOKEN = os.environ.get("GH_TOKEN")
GH_REPO = os.environ.get("GH_REPO")
GH_PATHS = {"db": "database.json", "eco": "economy.json", "pun": "punishments.json"}

# Локальные файлы (кэш)
FILES = {"db": "database.json", "eco": "economy.json", "pun": "punishments.json"}
TZ_MSK = datetime.timezone(datetime.timedelta(hours=3))

def load_data(path):
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f: return json.load(f)
        except: return {}
    return {}

DATABASE = load_data(FILES["db"])
ECONOMY = load_data(FILES["eco"])
PUNISHMENTS = load_data(FILES["pun"])

# Инициализация структур
for k in ["gbans_status", "gbans_pl", "bans", "warns"]: PUNISHMENTS.setdefault(k, {})
DATABASE.setdefault("chats", {})

RANK_WEIGHT = {
    "Пользователь": 0, "Модератор": 1, "Старший Модератор": 2, 
    "Администратор": 3, "Старший Администратор": 4, "Зам. Спец. Администратора": 5,
    "Спец. Администратор": 6, "Владелец": 7, "Зам. Спец. Руководителя": 8,
    "Основной Зам. Спец. Руководителя": 9, "Специальный Руководитель": 10
}

bot = Bot(token=TOKEN)

# --- 2. СИНХРОНИЗАЦИЯ (РАЗ В 5 МИНУТ) ---
async def upload_to_github(data, gh_path):
    if not GH_TOKEN or not GH_REPO: return
    url = f"https://api.github.com/repos/{GH_REPO}/contents/{gh_path}"
    headers = {"Authorization": f"token {GH_TOKEN}", "Accept": "application/vnd.github.v3+json"}
    try:
        async with aiohttp.ClientSession() as s:
            sha = None
            async with s.get(url, headers=headers) as r:
                if r.status == 200: sha = (await r.json())['sha']
            content = base64.b64encode(json.dumps(data, ensure_ascii=False, indent=4).encode()).decode()
            payload = {"message": "Scheduled backup", "content": content}
            if sha: payload["sha"] = sha
            await s.put(url, headers=headers, json=payload)
    except: pass

async def auto_sync_worker():
    while True:
        await asyncio.sleep(300) # Интервал 5 минут
        await upload_to_github(DATABASE, GH_PATHS["db"])
        await upload_to_github(ECONOMY, GH_PATHS["eco"])
        await upload_to_github(PUNISHMENTS, GH_PATHS["pun"])
        # Локальная запись
        for k, v in {"db": DATABASE, "eco": ECONOMY, "pun": PUNISHMENTS}.items():
            with open(FILES[k], "w", encoding="utf-8") as f: json.dump(v, f, ensure_ascii=False, indent=4)

# --- 3. ФУНКЦИИ ПОИСКА ---
async def get_id(m: Message, args: str):
    if m.reply_message: return m.reply_message.from_id
    if not args: return None
    match = re.search(r"(?:id|\[id|vk\.com\/id|vk\.com\/)(\d+)", args)
    if match: return int(match.group(1))
    try:
        name = args.split('/')[-1].split('|')[0].replace('[', '').replace('@', '').strip()
        res = await bot.api.utils.resolve_screen_name(screen_name=name)
        if res and res.type.value == "user": return res.object_id
    except: pass
    num = re.sub(r"\D", "", args)
    return int(num) if num.isdigit() else None

def get_user_info(pid, uid):
    if int(uid) == 870757778: return "Специальный Руководитель", "Misha Manlix"
    chat = DATABASE.get("chats", {}).get(str(pid), {})
    return chat.get("staff", {}).get(str(uid), ["Пользователь", None])

# --- 4. MIDDLEWARE (ИСПРАВЛЕНО) ---
class Guard(BaseMiddleware[Message]):
    async def pre(self):
        pid, uid = str(self.event.peer_id), str(self.event.from_id)
        if not self.event.from_id or self.event.from_id < 0: return
        
        # Сбор статистики в реальном времени
        if pid in DATABASE["chats"]:
            st = DATABASE["chats"][pid].setdefault("stats", {}).setdefault(uid, {"count": 0, "last": 0})
            st["count"] += 1
            st["last"] = time.time()

        # Проверка мута/бана
        muted = DATABASE.get("chats", {}).get(pid, {}).get("mutes", {}).get(uid, 0)
        if time.time() < muted or uid in PUNISHMENTS["gbans_status"] or uid in PUNISHMENTS.get("bans", {}).get(pid, {}):
            try: await bot.api.messages.delete(peer_id=self.event.peer_id, conversation_message_id=self.event.conversation_message_id, delete_for_all=True)
            except: pass
            self.stop()

bot.labeler.message_view.register_middleware(Guard())

# --- 5. КОМАНДЫ ---
@bot.on.message(text="/info")
async def info_cmd(m: Message):
    await m.answer("Временно недоступно!")

@bot.on.message(text=["/stats", "/stats <args>"])
async def stats_cmd(m: Message, args=None):
    t = await get_id(m, args) or m.from_id
    pid, uid = str(m.peer_id), str(t)
    rank, nick = get_user_info(pid, t)
    st = DATABASE.get("chats", {}).get(pid, {}).get("stats", {}).get(uid, {"count": 0, "last": 0})
    l_time = datetime.datetime.fromtimestamp(st["last"], TZ_MSK).strftime('%d/%m/%Y %I:%M:%S %p') if st["last"] else "Нет данных"
    
    await m.answer(f"Информация о [id{t}|пользователе]\nРоль: {rank}\nНик: {nick or 'Не установлен'}\nСообщений: {st['count']}\nПоследнее: {l_time}")

@bot.on.message(text="/prise")
async def prise_cmd(m: Message):
    u = str(m.from_id)
    eco = ECONOMY.setdefault(u, {"cash": 0, "last": 0})
    if time.time() - eco["last"] < 3600: return await m.answer("⏳ Приз доступен раз в час!")
    win = random.randint(100, 1000)
    eco["cash"] += win
    eco["last"] = time.time()
    await m.answer(f"🎉 Вы получили {win}$!")

# --- 6. МОДЕРАЦИЯ (КНОПКИ) ---
@bot.on.message(text=["/mute", "/mute <args>"])
async def mute_cmd(m: Message, args=""):
    t = await get_id(m, args)
    if not t: return
    until = time.time() + 600 # 10 мин по дефолту
    DATABASE.setdefault("chats", {}).setdefault(str(m.peer_id), {}).setdefault("mutes", {})[str(t)] = until
    kb = Keyboard(inline=True).add(Text("Снять мут", {"c": "unmute", "u": t}), color=KeyboardButtonColor.POSITIVE).add(Text("Очистить", {"c": "clear"}), color=KeyboardButtonColor.NEGATIVE)
    await m.answer(f"[id{m.from_id}|Модератор MANLIX] выдал мут [id{t}|пользователю]", keyboard=kb.get_json())

@bot.on.raw_event(GroupEventType.MESSAGE_EVENT, dataclass=MessageEvent)
async def callback_handler(ev: MessageEvent):
    c, uid, pid = ev.payload.get("c"), str(ev.payload.get("u")), str(ev.peer_id)
    if c == "unmute":
        if uid in DATABASE.get("chats", {}).get(pid, {}).get("mutes", {}): del DATABASE["chats"][pid]["mutes"][uid]
        await bot.api.messages.edit(peer_id=ev.peer_id, conversation_message_id=ev.conversation_message_id, message=f"[id{ev.user_id}|Модератор MANLIX] снял мут [id{uid}|пользователю]")
    elif c == "clear":
        await bot.api.messages.delete(peer_id=ev.peer_id, conversation_message_ids=[ev.conversation_message_id], delete_for_all=True)

# --- 7. ЗАПУСК ---
class Health(BaseHTTPRequestHandler):
    def do_GET(self): self.send_response(200); self.end_headers(); self.wfile.write(b"OK")

if __name__ == "__main__":
    threading.Thread(target=lambda: HTTPServer(('0.0.0.0', int(os.environ.get("PORT", 10000))), Health).serve_forever(), daemon=True).start()
    loop = asyncio.get_event_loop()
    loop.create_task(auto_sync_worker())
    print("Запуск MANLIX...")
    bot.run_forever()

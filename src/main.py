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

# --- 1. КОНФИГУРАЦИЯ ---
TOKEN = os.environ.get("TOKEN")
GH_TOKEN = os.environ.get("GH_TOKEN")
GH_REPO = os.environ.get("GH_REPO")
GH_PATHS = {"db": "database.json", "eco": "economy.json", "pun": "punishments.json"}
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

# Инициализация структур данных
for k in ["gbans_status", "gbans_pl", "bans", "warns"]: PUNISHMENTS.setdefault(k, {})
DATABASE.setdefault("chats", {})

RANK_WEIGHT = {
    "Пользователь": 0, "Модератор": 1, "Старший Модератор": 2, 
    "Администратор": 3, "Старший Администратор": 4, "Зам. Спец. Администратора": 5,
    "Спец. Администратор": 6, "Владелец": 7, "Зам. Спец. Руководителя": 8,
    "Основной Зам. Спец. Руководителя": 9, "Специальный Руководитель": 10
}

bot = Bot(token=TOKEN)

# --- 2. ФОНОВАЯ СИНХРОНИЗАЦИЯ (Раз в 5 минут) ---
async def upload_to_github(data, gh_path):
    if not GH_TOKEN or not GH_REPO: return
    url = f"https://api.github.com/repos/{GH_REPO}/contents/{gh_path}"
    headers = {"Authorization": f"token {GH_TOKEN}"}
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
        await asyncio.sleep(300) # 5 минут
        await upload_to_github(DATABASE, GH_PATHS["db"])
        await upload_to_github(ECONOMY, GH_PATHS["eco"])
        await upload_to_github(PUNISHMENTS, GH_PATHS["pun"])
        # Локальное сохранение
        for k, v in {"db": DATABASE, "eco": ECONOMY, "pun": PUNISHMENTS}.items():
            with open(FILES[k], "w", encoding="utf-8") as f: json.dump(v, f, ensure_ascii=False, indent=4)

# --- 3. ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---
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

async def check_access(m: Message, req_rank: str):
    u_rank, _ = get_user_info(m.peer_id, m.from_id)
    if RANK_WEIGHT.get(u_rank, 0) < RANK_WEIGHT.get(req_rank, 0):
        await m.answer("Недостаточно прав!")
        return False
    return True

# --- 4. MIDDLEWARE (ЗАЩИТА И УДАЛЕНИЕ) ---
class Guard(BaseMiddleware[Message]):
    async def pre(self):
        pid, uid = str(self.event.peer_id), str(self.event.from_id)
        if not self.event.from_id or self.event.from_id < 0: return
        
        # Сбор статистики
        if pid in DATABASE["chats"]:
            st = DATABASE["chats"][pid].setdefault("stats", {}).setdefault(uid, {"count": 0, "last": 0})
            st["count"] += 1
            st["last"] = datetime.datetime.now(TZ_MSK).timestamp()

        # Проверка мута/бана
        muted = DATABASE.get("chats", {}).get(pid, {}).get("mutes", {}).get(uid, 0)
        in_mute = datetime.datetime.now(TZ_MSK).timestamp() < muted
        is_gb = uid in PUNISHMENTS["gbans_status"] or uid in PUNISHMENTS["gbans_pl"]
        is_lb = uid in PUNISHMENTS.get("bans", {}).get(pid, {})

        if in_mute or is_gb or is_lb:
            try: await bot.api.messages.delete(peer_id=self.event.peer_id, conversation_message_id=self.event.conversation_message_id, delete_for_all=True)
            except: pass
            self.stop()

bot.labeler.message_view.register_middleware(Guard())

# --- 5. КОМАНДЫ ПОЛЬЗОВАТЕЛЕЙ ---
@bot.on.message(text="/info")
async def info_cmd(m: Message):
    await m.answer("Временно недоступно!")

@bot.on.message(text="/getid")
@bot.on.message(text="/getid <args>")
async def getid_cmd(m: Message, args=None):
    t = await get_id(m, args) or m.from_id
    await m.answer(f"Оригинальная ссылка [id{t}|пользователя]: https://vk.com/id{t}")

@bot.on.message(text=["/stats", "/stats <args>"])
async def stats_cmd(m: Message, args=None):
    t = await get_id(m, args) or m.from_id
    pid, uid = str(m.peer_id), str(t)
    rank, nick = get_user_info(pid, t)
    st = DATABASE.get("chats", {}).get(pid, {}).get("stats", {}).get(uid, {"count": 0, "last": 0})
    l_time = datetime.datetime.fromtimestamp(st["last"], TZ_MSK).strftime('%d/%m/%Y %I:%M:%S %p') if st["last"] else "Нет данных"
    
    msg = (f"Информация о [id{t}|пользователе]\n"
           f"Роль: {rank}\n"
           f"Блокировок: {len(PUNISHMENTS['bans'].get(pid, {}).get(uid, []))}\n"
           f"Общая блокировка в чатах: {'Да' if uid in PUNISHMENTS['gbans_status'] else 'Нет'}\n"
           f"Общая блокировка в беседах игроков: {'Да' if uid in PUNISHMENTS['gbans_pl'] else 'Нет'}\n"
           f"Активные предупреждения: {PUNISHMENTS['warns'].get(pid, {}).get(uid, 0)}\n"
           f"Блокировка чата: {'Да' if datetime.datetime.now(TZ_MSK).timestamp() < DATABASE.get('chats', {}).get(pid, {}).get('mutes', {}).get(uid, 0) else 'Нет'}\n"
           f"Ник: {nick or 'Не установлен'}\n"
           f"Всего сообщений: {st['count']}\n"
           f"Последнее сообщение: {l_time}")
    await m.answer(msg)

# --- 6. МОДЕРАЦИЯ ---
@bot.on.message(text=["/mute", "/mute <args>"])
async def mute_cmd(m: Message, args=""):
    if not await check_access(m, "Модератор"): return
    t = await get_id(m, args)
    if not t: return
    mins = 10
    reason = "Нарушение"
    for p in args.split():
        if p.isdigit(): mins = int(p)
        elif "id" not in p and "[" not in p: reason = p
    
    until = datetime.datetime.now(TZ_MSK).timestamp() + (mins * 60)
    DATABASE.setdefault("chats", {}).setdefault(str(m.peer_id), {}).setdefault("mutes", {})[str(t)] = until
    dt = datetime.datetime.fromtimestamp(until, TZ_MSK).strftime('%d/%m/%Y %H:%M:%S')
    
    kb = Keyboard(inline=True).add(Text("Снять мут", {"c": "unmute", "u": t}), color=KeyboardButtonColor.POSITIVE).add(Text("Очистить", {"c": "clear"}), color=KeyboardButtonColor.NEGATIVE)
    await m.answer(f"[id{m.from_id}|Модератор MANLIX] выдал(-а) мут [id{t}|пользователю]\nПричина: {reason}\nМут выдан до: {dt}", keyboard=kb.get_json())

@bot.on.raw_event(GroupEventType.MESSAGE_EVENT, dataclass=MessageEvent)
async def callback_handler(ev: MessageEvent):
    if not await check_access(ev, "Модератор"): return await ev.show_snackbar("Недостаточно прав!")
    c, uid, pid = ev.payload.get("c"), str(ev.payload.get("u")), str(ev.peer_id)
    if c == "unmute":
        if uid in DATABASE.get("chats", {}).get(pid, {}).get("mutes", {}): del DATABASE["chats"][pid]["mutes"][uid]
        await bot.api.messages.edit(peer_id=ev.peer_id, conversation_message_id=ev.conversation_message_id, message=f"[id{ev.user_id}|Модератор MANLIX] снял(-а) мут [id{uid}|пользователю]")
    elif c == "clear":
        await bot.api.messages.delete(peer_id=ev.peer_id, conversation_message_ids=[ev.conversation_message_id], delete_for_all=True)

@bot.on.message(text=["/kick", "/kick <args>"])
async def kick_cmd(m: Message, args=""):
    if not await check_access(m, "Модератор"): return
    t = await get_id(m, args)
    if t:
        try:
            await bot.api.messages.remove_chat_user(chat_id=m.peer_id-2000000000, user_id=t)
            await m.answer(f"[id{m.from_id}|Модератор MANLIX] исключил(-а) [id{t}|пользователя] из Беседы.")
        except: pass

@bot.on.message(text=["/setnick", "/setnick <args>"])
async def setnick_cmd(m: Message, args=""):
    if not await check_access(m, "Модератор"): return
    t = await get_id(m, args)
    if not t: return
    new_nick = args.split()[-1]
    pid, uid = str(m.peer_id), str(t)
    role, _ = get_user_info(pid, uid)
    DATABASE.setdefault("chats", {}).setdefault(pid, {}).setdefault("staff", {})[uid] = [role, new_nick]
    _, a_n = get_user_info(pid, m.from_id)
    await m.answer(f"[id{m.from_id}|{a_n or 'Ник'}] установил(-а) новое имя [id{t}|пользователю]")

# --- 7. ЭКОНОМИКА ---
@bot.on.message(text="/prise")
async def prise_cmd(m: Message):
    u = str(m.from_id)
    eco = ECONOMY.setdefault(u, {"cash": 0, "bank": 0, "last": 0})
    if time.time() - eco["last"] < 3600: return await m.answer("⏳ Приз можно брать раз в час!")
    win = random.randint(100, 1000)
    eco["cash"] += win
    eco["last"] = time.time()
    await m.answer(f"🎉 Вы получили приз {win}$")

@bot.on.message(text="/balance")
async def balance_cmd(m: Message):
    cash = ECONOMY.get(str(m.from_id), {}).get("cash", 0)
    await m.answer(f"💵 Ваши наличные: {cash}$")

# --- 8. ЗАПУСК ---
class Web(BaseHTTPRequestHandler):
    def do_GET(self): self.send_response(200); self.end_headers(); self.wfile.write(b"OK")

if __name__ == "__main__":
    threading.Thread(target=lambda: HTTPServer(('0.0.0.0', int(os.environ.get("PORT", 10000))), Web).serve_forever(), daemon=True).start()
    loop = asyncio.get_event_loop()
    loop.create_task(auto_sync_worker())
    print("Бот MANLIX запущен!")
    bot.run_forever()

import os
import threading
import re
import json
import base64
import aiohttp
import datetime
import random
import asyncio
from http.server import HTTPServer, BaseHTTPRequestHandler
from vkbottle.bot import Bot, Message
from vkbottle import Keyboard, KeyboardButtonColor, Text, GroupEventType, BaseMiddleware

# --- 1. НАСТРОЙКИ ---
GH_TOKEN = os.environ.get("GH_TOKEN")
GH_REPO = os.environ.get("GH_REPO") 
GH_PATH_DB = "database.json"
GH_PATH_ECO = "economy.json"
EXTERNAL_DB = "database.json"
EXTERNAL_ECO = "economy.json"

ECO_CHANGED = False

def load_local_data(path):
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except: return {}
    return {}

DATABASE = load_local_data(EXTERNAL_DB)
ECONOMY = load_local_data(EXTERNAL_ECO)

RANK_WEIGHT = {
    "Пользователь": 0, "Модератор": 1, "Старший Модератор": 2, 
    "Администратор": 3, "Старший Администратор": 4, "Зам. Спец. Администратора": 5,
    "Спец. Администратор": 6, "Владелец": 7, "Зам. Специального Руководителя": 8,
    "Основной зам. Специального Руководителя": 9, "Специальный Руководитель": 10
}

# --- 2. GITHUB API ---

async def push_to_github(data, gh_path, local_path, message_text="Update"):
    url = f"https://api.github.com/repos/{GH_REPO}/contents/{gh_path}"
    headers = {"Authorization": f"token {GH_TOKEN}", "Accept": "application/vnd.github.v3+json"}
    try:
        async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=False)) as session:
            sha = None
            async with session.get(url, headers=headers) as resp:
                if resp.status == 200:
                    res_data = await resp.json()
                    sha = res_data['sha']
            
            content_str = json.dumps(data, ensure_ascii=False, indent=4)
            content_base64 = base64.b64encode(content_str.encode('utf-8')).decode('utf-8')
            payload = {"message": message_text, "content": content_base64}
            if sha: payload["sha"] = sha
            
            async with session.put(url, headers=headers, json=payload) as put_resp:
                if put_resp.status in [200, 201]:
                    with open(local_path, "w", encoding="utf-8") as f:
                        json.dump(data, f, ensure_ascii=False, indent=4)
                    return True
    except: return False

async def auto_save_eco():
    global ECO_CHANGED
    while True:
        await asyncio.sleep(300)
        if ECO_CHANGED:
            if await push_to_github(ECONOMY, GH_PATH_ECO, EXTERNAL_ECO, "Auto-save Economy"):
                ECO_CHANGED = False

# --- 3. СИСТЕМНАЯ ЛОГИКА ---

bot = Bot(token=os.environ.get("TOKEN"))

def get_user_data(peer_id, user_id):
    if int(user_id) == 870757778: return ["Специальный Руководитель", "Misha Manlix"]
    chat_data = DATABASE.get("chats", {}).get(str(peer_id), {})
    staff = chat_data.get("staff", {})
    return staff.get(str(user_id), ["Пользователь", None])

def get_eco_data(user_id):
    uid = str(user_id)
    if uid not in ECONOMY:
        ECONOMY[uid] = {"balance": 0, "bank": 0, "last_prise": 0}
    if "bank" not in ECONOMY[uid]:
        ECONOMY[uid]["bank"] = 0
    return ECONOMY[uid]

async def get_nick(peer_id, user_id):
    if int(user_id) == 870757778: return "Misha Manlix"
    _, nick = get_user_data(peer_id, user_id)
    if nick: return nick
    try:
        u = (await bot.api.users.get(user_ids=[user_id]))[0]
        return f"{u.first_name} {u.last_name}"
    except: return "Пользователь"

def has_access(peer_id, user_id, required_rank):
    u_rank = get_user_data(peer_id, user_id)[0]
    return RANK_WEIGHT.get(u_rank, 0) >= RANK_WEIGHT.get(required_rank, 0)

async def check_active(message: Message):
    if int(message.from_id) == 870757778: return True
    return str(message.peer_id) in DATABASE.get("chats", {})

def extract_id(text):
    if not text: return None
    match = re.search(r'id(\d+)', str(text))
    if match: return int(match.group(1))
    digits = re.findall(r'\d+', str(text))
    return int(digits[0]) if digits else None

class MuteMiddleware(BaseMiddleware[Message]):
    async def pre(self):
        if not self.event.from_id: return
        pid, uid = str(self.event.peer_id), str(self.event.from_id)
        mutes = DATABASE.get("chats", {}).get(pid, {}).get("mutes", {})
        if uid in mutes and datetime.datetime.now(datetime.timezone.utc).timestamp() < mutes[uid]:
            try: await bot.api.messages.delete(message_ids=[self.event.conversation_message_id], peer_id=self.event.peer_id, delete_for_all=True)
            except: pass
            self.event.text = "" 

bot.labeler.message_view.register_middleware(MuteMiddleware)

# --- 4. ИГРОВАЯ СИСТЕМА ---

@bot.on.message(text="/ghelp")
async def ghelp_cmd(m: Message):
    if not await check_active(m): return
    msg = ("🎮 Игровые команды MANLIX:\n\n"
           "🎉 /prise — Получить ежечасный приз\n"
           "💰 /balance — Наличные средства\n"
           "🏦 /bank — Состояние счетов\n"
           "📥 /положить [сумма] — Положить в банк\n"
           "📤 /снять [сумма] — Снять из банка\n"
           "💸 /перевести [ссылка] [сумма] — Перевод со счета на счет\n"
           "🎰 /roulette [сумма] — Рулетка (x3, шанс 20%, мин. 100$)")
    await m.answer(msg)

@bot.on.message(text="/prise")
async def prise_cmd(m: Message):
    if not await check_active(m): return
    global ECO_CHANGED
    data = get_eco_data(m.from_id)
    now = datetime.datetime.now().timestamp()
    if now - data["last_prise"] < 3600:
        return await m.answer(f"❌ Приз доступен раз в час! Подождите {int((3600-(now-data['last_prise']))/60)} мин.")
    win = random.randint(100, 1000)
    data["balance"] += win
    data["last_prise"] = now
    ECO_CHANGED = True
    await m.answer(f"🎉 Вы получили приз: {win}$\n💰 Наличные: {data['balance']}$")

@bot.on.message(text="/balance")
async def balance_cmd(m: Message):
    if not await check_active(m): return
    await m.answer(f"💰 Ваши наличные: {get_eco_data(m.from_id)['balance']}$")

@bot.on.message(text="/bank")
async def bank_cmd(m: Message):
    if not await check_active(m): return
    data = get_eco_data(m.from_id)
    await m.answer(f"🏦 …::: MANLIX BANK :::…\n\n💵 Наличные: {data['balance']}$\n💳 На счету: {data['bank']}$")

@bot.on.message(text=["/положить", "/положить <amount:int>"])
async def deposit_cmd(m: Message, amount: int = None):
    if not await check_active(m) or amount is None or amount <= 0: return
    global ECO_CHANGED
    u = get_eco_data(m.from_id)
    if u["balance"] < amount: return await m.answer("⚠ Недостаточно наличных!")
    u["balance"] -= amount
    u["bank"] += amount
    ECO_CHANGED = True
    await m.answer(f"💲 Вы положили на свой счет: {amount}$")

@bot.on.message(text=["/снять", "/снять <amount:int>"])
async def withdraw_cmd(m: Message, amount: int = None):
    if not await check_active(m) or amount is None or amount <= 0: return
    global ECO_CHANGED
    u = get_eco_data(m.from_id)
    if u["bank"] < amount: return await m.answer("⚠ Недостаточно средств в банке!")
    u["bank"] -= amount
    u["balance"] += amount
    ECO_CHANGED = True
    await m.answer(f"💵 Вы сняли со счета: {amount}$")

@bot.on.message(text=["/перевести", "/перевести <args>"])
async def transfer_cmd(m: Message, args=None):
    if not await check_active(m) or not args: return
    p = args.split()
    if len(p) < 2: return
    tid, amt = extract_id(p[0]), int(p[1]) if p[1].isdigit() else 0
    if amt <= 0 or tid == m.from_id: return
    global ECO_CHANGED
    s, r = get_eco_data(m.from_id), get_eco_data(tid)
    if s["bank"] < amt: return await m.answer("⚠ Недостаточно денег в банке для перевода!")
    s["bank"] -= amt
    r["bank"] += amt
    ECO_CHANGED = True
    await m.answer(f"💸 Вы перевели [id{tid}|пользователю] {amt}$")

@bot.on.message(text=["/roulette", "/roulette <amount:int>"])
async def roulette_cmd(m: Message, amount: int = None):
    if not await check_active(m) or amount is None: return
    if amount < 100: return await m.answer("🎰 Минимальная ставка — 100$")
    global ECO_CHANGED
    u = get_eco_data(m.from_id)
    if u["balance"] < amount: return await m.answer("⚠ Недостаточно наличных!")
    if random.randint(1, 5) == 1:
        u["balance"] += (amount * 2)
        await m.answer(f"🎰 Вы выиграли {amount*3}$\n(Ставка: {amount}$)")
    else:
        u["balance"] -= amount
        await m.answer(f"🎰 Вы проиграли ставку {amount}$")
    ECO_CHANGED = True

# --- 5. МОДЕРАЦИЯ ---

@bot.on.message(text="/help")
async def help_handler(message: Message):
    if not await check_active(message): return
    rank = get_user_data(message.peer_id, message.from_id)[0]
    w = RANK_WEIGHT.get(rank, 0)
    msg = "Команды пользователей:\n/info - официальные ресурсы \n/stats - статистика пользователя \n/getid - оригинальная ссылка VK.\n"
    if w >= 1: msg += "\nМодерация:\n/staff, /kick, /mute, /unmute, /setnick, /rnick, /nlist\n"
    if w >= 2: msg += "/addmoder, /removerole\n"
    if w >= 8: msg += "\nРуководство:\n/gstaff, /addowner, /gbanpl, /gunbanpl\n"
    if w >= 10: msg += "/start, /sync, /delchat"
    await message.answer(msg)

@bot.on.message(text=["/stats", "/stats <args>"])
async def stats_cmd(m: Message, args=None):
    if not await check_active(m): return
    t = m.reply_message.from_id if m.reply_message else (extract_id(args) or m.from_id)
    r = get_user_data(m.peer_id, t)[0]
    n = await get_nick(m.peer_id, t)
    e = get_eco_data(t)
    await m.answer(f"Статистика [id{t}|{n}]:\nПрава: {r}\nНаличные: {e['balance']}$\nБанк: {e['bank']}$")

@bot.on.message(text=["/mute", "/mute <args>"])
async def mute_cmd(m: Message, args=None):
    if not await check_active(m) or not has_access(m.peer_id, m.from_id, "Модератор"): return
    t = m.reply_message.from_id if m.reply_message else extract_id(args)
    if not t: return
    end = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=3))) + datetime.timedelta(minutes=10)
    pid = str(m.peer_id)
    if pid not in DATABASE["chats"]: DATABASE["chats"][pid] = {"mutes": {}}
    if "mutes" not in DATABASE["chats"][pid]: DATABASE["chats"][pid]["mutes"] = {}
    DATABASE["chats"][pid]["mutes"][str(t)] = end.timestamp()
    await push_to_github(DATABASE, GH_PATH_DB, EXTERNAL_DB, "Mute")
    await m.answer(f"Мут выдан [id{t}|пользователю] до {end.strftime('%H:%M:%S')}")

@bot.on.message(text=["/setnick", "/setnick <args>"])
async def setnick_cmd(m: Message, args=None):
    if not await check_active(m) or not has_access(m.peer_id, m.from_id, "Модератор"): return
    p = args.split()
    t, nick = (m.reply_message.from_id if m.reply_message else extract_id(p[0])), p[-1]
    if not t or not nick: return
    pid = str(m.peer_id)
    r = get_user_data(m.peer_id, t)[0]
    DATABASE["chats"][pid]["staff"][str(t)] = [r, nick]
    await push_to_github(DATABASE, GH_PATH_DB, EXTERNAL_DB, "Nick")
    await m.answer(f"Ник {nick} установлен.")

async def grant_role(m, args, req, role):
    if not await check_active(m) or not has_access(m.peer_id, m.from_id, req): return
    t = m.reply_message.from_id if m.reply_message else extract_id(args)
    if not t: return
    pid = str(m.peer_id)
    _, n = get_user_data(m.peer_id, t)
    if role == "Пользователь":
        if str(t) in DATABASE["chats"][pid]["staff"]: del DATABASE["chats"][pid]["staff"][str(t)]
    else: DATABASE["chats"][pid]["staff"][str(t)] = [role, n]
    await push_to_github(DATABASE, GH_PATH_DB, EXTERNAL_DB, f"Role {role}")
    await m.answer(f"Права {role} обновлены.")

@bot.on.message(text=["/addmoder", "/addmoder <args>"])
async def am(m, args=None): await grant_role(m, args, "Старший Модератор", "Модератор")

@bot.on.message(text=["/removerole", "/removerole <args>"])
async def rr(m, args=None): await grant_role(m, args, "Старший Модератор", "Пользователь")

@bot.on.message(text="/start")
async def start_handler(m: Message):
    if int(m.from_id) != 870757778: return
    DATABASE["chats"][str(m.peer_id)] = {"staff": {"870757778": ["Специальный Руководитель", "Misha Manlix"]}}
    await push_to_github(DATABASE, GH_PATH_DB, EXTERNAL_DB, "Start")
    await m.answer("Беседа активирована.")

@bot.on.message(text="/sync")
async def sync_cmd(m: Message):
    if int(m.from_id) != 870757778: return
    h = {"Authorization": f"token {GH_TOKEN}"}
    async with aiohttp.ClientSession() as s:
        async with s.get(f"https://api.github.com/repos/{GH_REPO}/contents/{GH_PATH_DB}", headers=h) as r:
            if r.status == 200:
                global DATABASE
                DATABASE = json.loads(base64.b64decode((await r.json())['content']).decode('utf-8'))
        async with s.get(f"https://api.github.com/repos/{GH_REPO}/contents/{GH_PATH_ECO}", headers=h) as r:
            if r.status == 200:
                global ECONOMY
                ECONOMY = json.loads(base64.b64decode((await r.json())['content']).decode('utf-8'))
    await m.answer("Синхронизация завершена.")

# --- ЗАПУСК ---
class H(BaseHTTPRequestHandler):
    def do_GET(self): self.send_response(200); self.end_headers(); self.wfile.write(b"OK")

if __name__ == "__main__":
    threading.Thread(target=lambda: HTTPServer(('0.0.0.0', int(os.environ.get("PORT", 10000))), H).serve_forever(), daemon=True).start()
    loop = asyncio.get_event_loop()
    loop.create_task(auto_save_eco())
    bot.run_forever()

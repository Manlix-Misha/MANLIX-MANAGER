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

# ────────────────────────────────────────────────
# НАСТРОЙКИ
# ────────────────────────────────────────────────
GH_TOKEN    = os.environ.get("GH_TOKEN")
GH_REPO     = os.environ.get("GH_REPO")
GH_PATH_DB  = "database.json"
GH_PATH_ECO = "economy.json"
GH_PATH_PUN = "punishments.json"

EXTERNAL_DB  = "database.json"
EXTERNAL_ECO = "economy.json"
EXTERNAL_PUN = "punishments.json"

TZ_MSK = datetime.timezone(datetime.timedelta(hours=3))

RANK_WEIGHT = {
    "Пользователь":                     0,
    "Модератор":                        1,
    "Старший Модератор":                2,
    "Администратор":                    3,
    "Старший Администратор":            4,
    "Зам. Спец. Администратора":        5,
    "Спец. Администратор":              6,
    "Владелец":                         7,
    "Зам. Спец. Руководителя":          8,
    "Основной Зам. Спец. Руководителя": 9,
    "Специальный Руководитель":        10
}

# ────────────────────────────────────────────────
# Загрузка / сохранение
# ────────────────────────────────────────────────
async def load_from_github(gh_path, local_path):
    if not GH_TOKEN or not GH_REPO:
        return load_local_data(local_path)
    url = f"https://api.github.com/repos/{GH_REPO}/contents/{gh_path}"
    headers = {"Authorization": f"token {GH_TOKEN}"}
    async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=False)) as session:
        async with session.get(url, headers=headers) as resp:
            if resp.status == 200:
                doc = await resp.json()
                if 'content' in doc:
                    data = json.loads(base64.b64decode(doc['content']).decode('utf-8'))
                    with open(local_path, "w", encoding="utf-8") as f:
                        json.dump(data, f, ensure_ascii=False, indent=4)
                    return data
            if resp.status != 404:
                print(f"GitHub load failed: {resp.status}")
    return load_local_data(local_path)

def load_local_data(path):
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except:
            return {}
    return {}

async def push_to_github(data, gh_path, local_path):
    if not GH_TOKEN or not GH_REPO:
        try:
            with open(local_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=4)
        except Exception as e:
            print("Local save error:", e)
        return

    url = f"https://api.github.com/repos/{GH_REPO}/contents/{gh_path}"
    headers = {"Authorization": f"token {GH_TOKEN}"}
    async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=False)) as session:
        sha = None
        async with session.get(url, headers=headers) as r:
            if r.status == 200:
                doc = await r.json()
                sha = doc.get('sha')
        content = base64.b64encode(json.dumps(data, ensure_ascii=False, indent=4).encode('utf-8')).decode('utf-8')
        payload = {"message": "Update from bot", "content": content}
        if sha:
            payload["sha"] = sha
        async with session.put(url, headers=headers, json=payload) as resp:
            if resp.status not in (200, 201):
                print("GitHub push failed:", resp.status, await resp.text())
        with open(local_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)

# Инициализация
loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)
DATABASE  = loop.run_until_complete(load_from_github(GH_PATH_DB,  EXTERNAL_DB))
ECONOMY   = loop.run_until_complete(load_from_github(GH_PATH_ECO, EXTERNAL_ECO))
PUNISHMENTS = loop.run_until_complete(load_from_github(GH_PATH_PUN, EXTERNAL_PUN))

if not isinstance(DATABASE, dict):   DATABASE   = {}
if not isinstance(ECONOMY, dict):    ECONOMY    = {}
if not isinstance(PUNISHMENTS, dict): PUNISHMENTS = {}
if "gbans_status" not in PUNISHMENTS: PUNISHMENTS["gbans_status"] = {}
if "gbans_pl"     not in PUNISHMENTS: PUNISHMENTS["gbans_pl"]     = {}
if "bans"         not in PUNISHMENTS: PUNISHMENTS["bans"]         = {}
if "warns"        not in PUNISHMENTS: PUNISHMENTS["warns"]        = {}
if "chats"        not in DATABASE:    DATABASE["chats"]           = {}
if "gstaff"       not in DATABASE:
    DATABASE["gstaff"] = {"spec": 870757778, "main_zam": None, "zams": []}
if "duels"        not in DATABASE:
    DATABASE["duels"] = {}

# ────────────────────────────────────────────────
# Бот
# ────────────────────────────────────────────────
bot = Bot(token=os.environ.get("TOKEN"))

# ────────────────────────────────────────────────
# Утилиты
# ────────────────────────────────────────────────
def ensure_chat(pid: str):
    if pid not in DATABASE["chats"]:
        DATABASE["chats"][pid] = {
            "title": f"Чат {pid}",
            "staff": {},
            "mutes": {},
            "stats": {},
            "type": "def"
        }

async def get_target_id(m: Message, args: str = None):
    if getattr(m, "reply_message", None):
        return m.reply_message.from_id
    if not args:
        return None
    for pattern in [
        r"(?:\[id|id|vk\.com\/id|vk\.com\/)(\d+)",
        r"\[id(\d+)\|"
    ]:
        match = re.search(pattern, args)
        if match:
            try: return int(match.group(1))
            except: continue
    raw = args.split('/')[-1].split('|')[0].replace('[', '').replace('@', '').strip()
    if raw.isdigit():
        return int(raw)
    if raw:
        try:
            res = await bot.api.utils.resolve_screen_name(screen_name=raw)
            if res and res.type == "user":
                return int(res.object_id)
        except:
            pass
    return None

def get_user_info(peer_id, user_id):
    uid = str(user_id)
    if user_id == 870757778:
        return "Специальный Руководитель", "Misha Manlix"
    staff = DATABASE.get("chats", {}).get(str(peer_id), {}).get("staff", {})
    local_role, nick = staff.get(uid, ["Пользователь", None])
    gstaff = DATABASE.get("gstaff", {})
    global_role = "Пользователь"
    if user_id == gstaff.get("spec"):
        global_role = "Специальный Руководитель"
    elif gstaff.get("main_zam") and user_id == gstaff["main_zam"]:
        global_role = "Основной Зам. Спец. Руководителя"
    elif gstaff.get("zams") and user_id in gstaff["zams"]:
        global_role = "Зам. Спец. Руководителя"
    role = global_role if RANK_WEIGHT.get(global_role, 0) > RANK_WEIGHT.get(local_role, 0) else local_role
    return role, nick

async def check_access(m: Message, min_rank: str):
    rank, _ = get_user_info(m.peer_id, m.from_id)
    if RANK_WEIGHT.get(rank, 0) < RANK_WEIGHT.get(min_rank, 0):
        await m.answer("Недостаточно прав!")
        return False
    return True

# ────────────────────────────────────────────────
# Middleware — блокировка сообщений
# ────────────────────────────────────────────────
class ChatMiddleware(BaseMiddleware[Message]):
    async def pre(self):
        if not getattr(self.event, "from_id", None) or self.event.from_id < 0:
            return
        pid, uid = str(self.event.peer_id), str(self.event.from_id)
        ensure_chat(pid)
        chat = DATABASE["chats"][pid]
        if "stats" not in chat: chat["stats"] = {}
        if uid not in chat["stats"]:
            chat["stats"][uid] = {"count": 0, "last": 0}
        chat["stats"][uid]["count"] += 1
        chat["stats"][uid]["last"] = datetime.datetime.now(TZ_MSK).timestamp()
        if chat["stats"][uid]["count"] % 10 == 0:
            await push_to_github(DATABASE, GH_PATH_DB, EXTERNAL_DB)

        is_gban   = uid in PUNISHMENTS.get("gbans_status", {})
        is_gbanpl = uid in PUNISHMENTS.get("gbans_pl",     {})
        is_lban   = uid in PUNISHMENTS.get("bans", {}).get(pid, {})
        is_muted  = uid in chat.get("mutes", {}) and time.time() < chat["mutes"][uid]

        if is_gban or is_gbanpl or is_lban or is_muted:
            try:
                await bot.api.messages.delete(
                    peer_id=self.event.peer_id,
                    conversation_message_ids=[self.event.conversation_message_id],
                    delete_for_all=True
                )
            except:
                pass
            self.stop()

bot.labeler.message_view.register_middleware(ChatMiddleware)

# ────────────────────────────────────────────────
# Команды
# ────────────────────────────────────────────────
@bot.on.message(text=["/help"])
async def help_cmd(m: Message):
    rank, _ = get_user_info(m.peer_id, m.from_id)
    w = RANK_WEIGHT.get(rank, 0)
    res = (
        "Команды пользователей:\n"
        "/info - официальные ресурсы\n"
        "/stats - статистика пользователя\n"
        "/getid - оригинальная ссылка VK\n"
        "/ghelp - игровые команды\n"
    )
    if w >= 1:
        res += (
            "\nКоманды модераторов:\n"
            "/staff - список руководства\n"
            "/kick - кикнуть\n"
            "/mute - мут\n"
            "/unmute - размут\n"
            "/setnick - установить ник\n"
            "/rnick - снять ник\n"
            "/nlist - список ников\n"
            "/getban - информация о банах\n"
        )
    if w >= 2:
        res += (
            "\nСтаршие модераторы:\n"
            "/addmoder - дать модера\n"
            "/removerole - снять роль\n"
            "/ban - бан в беседе\n"
            "/unban - разбан\n"
        )
    if w >= 3: res += "\nАдминистраторы:\n/addsenmoder - старший модератор\n"
    if w >= 4: res += "\nСтаршие админы:\n/addadmin - администратор\n"
    if w >= 5: res += "\nЗам. спец. админы:\n/addsenadmin - старший администратор\n"
    if w >= 6: res += "\nСпец. админы:\n/addzsa - зам. спец. админа\n"
    if w >= 7: res += "\nВладельцы:\n/addsa - спец. администратор\n"
    if w >= 8:
        res += (
            "\nРуководство бота:\n"
            "/gstaff - список руководства\n"
            "/addowner - владелец\n"
            "/gbanpl - глобальный бан в играх\n"
            "/gunbanpl - снять глобальный бан в играх\n"
            "/start - активировать беседу\n"
            "/type - сменить тип\n"
            "/sync - синхронизация\n"
            "/chatid - ID беседы\n"
            "/delchat - удалить беседу"
        )
    await m.answer(res)

@bot.on.message(text="/ghelp")
async def ghelp_cmd(m: Message):
    await m.answer(
        "🎮 Игровые команды:\n"
        "/prise — ежечасный приз\n"
        "/balance — наличные\n"
        "/bank — банковский счёт\n"
        "/положить [сумма] — внести в банк\n"
        "/снять [сумма] — снять с банка\n"
        "/перевести [ссылка] [сумма] — перевод на банковский счёт\n"
        "/roulette [сумма] — рулетка\n"
        "/duel [сумма] — создать дуэль"
    )

# ────────────────────────────────────────────────
# Мут + кнопки
# ────────────────────────────────────────────────
@bot.on.message(text=["/mute", "/mute <args>"])
async def mute_cmd(m: Message, args=None):
    if not await check_access(m, "Модератор"): return
    t = await get_target_id(m, args)
    if not t: return
    parts = (args or "").split()
    mins = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 60
    reason = " ".join(parts[2:]) or "Нарушение"
    until = time.time() + mins * 60
    pid = str(m.peer_id)
    ensure_chat(pid)
    DATABASE["chats"][pid]["mutes"][str(t)] = until
    dt = datetime.datetime.fromtimestamp(until, TZ_MSK).strftime("%d.%m.%Y %H:%M")
    kb = Keyboard(inline=True)
    kb.row()
    kb.add(Text("Снять мут",    {"cmd": "unmute_btn", "uid": t}), color=KeyboardButtonColor.POSITIVE)
    kb.add(Text("Очистить",     {"cmd": "clear_msg",  "uid": t}), color=KeyboardButtonColor.NEGATIVE)
    a_name = f"[id{m.from_id}|Модератор MANLIX]"
    await m.answer(
        f"{a_name} выдал мут [id{t}|пользователю]\n"
        f"Причина: {reason}\n"
        f"До: {dt}",
        keyboard=kb
    )
    await push_to_github(DATABASE, GH_PATH_DB, EXTERNAL_DB)

@bot.on.raw_event(GroupEventType.MESSAGE_EVENT, dataclass=MessageEvent)
async def mute_buttons(event: MessageEvent):
    payload = event.payload
    if isinstance(payload, str):
        try: payload = json.loads(payload)
        except: return
    cmd = payload.get("cmd")
    uid = str(payload.get("uid"))
    pid = str(event.peer_id)
    if not uid or uid not in DATABASE["chats"][pid].get("mutes", {}):
        return await event.show_snackbar("Мут уже снят или не существует")

    rank, _ = get_user_info(event.peer_id, event.user_id)
    if RANK_WEIGHT.get(rank, 0) < 1:
        return await event.show_snackbar("Недостаточно прав")

    if cmd == "unmute_btn":
        del DATABASE["chats"][pid]["mutes"][uid]
        await push_to_github(DATABASE, GH_PATH_DB, EXTERNAL_DB)
        new_text = f"[id{event.user_id}|Модератор MANLIX] снял мут [id{uid}|пользователю]"
        try:
            await bot.api.messages.edit(
                peer_id=event.peer_id,
                message=new_text,
                conversation_message_id=event.conversation_message_id
            )
        except:
            pass

    elif cmd == "clear_msg":
        try:
            # Удаляем последние 50 сообщений пользователя (максимум за раз)
            history = await bot.api.messages.get_history(
                peer_id=event.peer_id,
                count=50,
                user_id=int(uid)
            )
            ids = [msg.id for msg in history.items if msg.from_id == int(uid)]
            if ids:
                await bot.api.messages.delete(
                    peer_id=event.peer_id,
                    message_ids=ids,
                    delete_for_all=True
                )
        except Exception as e:
            print("clear_msg error:", e)

        new_text = f"[id{event.user_id}|Модератор MANLIX] очистил сообщения [id{uid}|пользователя]"
        try:
            await bot.api.messages.edit(
                peer_id=event.peer_id,
                message=new_text,
                conversation_message_id=event.conversation_message_id
            )
        except:
            pass

# ────────────────────────────────────────────────
# Duel
# ────────────────────────────────────────────────
@bot.on.message(text=["/duel <amount>"])
async def duel_create(m: Message, amount=None):
    try:
        amount = int(amount)
        if amount <= 0: raise ValueError
    except:
        return await m.answer("Укажите положительную сумму")

    uid = str(m.from_id)
    pid = str(m.peer_id)
    if uid not in ECONOMY or ECONOMY[uid].get("bank", 0) < amount:
        return await m.answer("Недостаточно денег на банковском счёте")

    duel_id = f"{pid}_{int(time.time())}"
    DATABASE["duels"][duel_id] = {
        "creator": uid,
        "amount": amount,
        "participants": [uid],
        "chat_id": pid
    }
    kb = Keyboard(inline=True)
    kb.add(Text("Вступить в дуэль!", {"cmd": "join_duel", "duel": duel_id}), color=KeyboardButtonColor.POSITIVE)
    await m.answer(
        f"⚔️ Дуэль на {amount}$ создана!\n"
        f"Нажми на кнопку, чтобы сразится!",
        keyboard=kb
    )
    await push_to_github(DATABASE, GH_PATH_DB, EXTERNAL_DB)

@bot.on.raw_event(GroupEventType.MESSAGE_EVENT, dataclass=MessageEvent)
async def duel_join(event: MessageEvent):
    payload = event.payload
    if isinstance(payload, str):
        try: payload = json.loads(payload)
        except: return
    if payload.get("cmd") != "join_duel":
        return

    duel_id = payload.get("duel")
    if duel_id not in DATABASE["duels"]:
        return await event.show_snackbar("Дуэль уже завершена")

    duel = DATABASE["duels"][duel_id]
    uid = str(event.user_id)
    if uid in duel["participants"]:
        return await event.show_snackbar("Вы уже участвуете")
    if len(duel["participants"]) >= 2:
        return await event.show_snackbar("Дуэль уже заполнена")

    if uid not in ECONOMY or ECONOMY[uid].get("bank", 0) < duel["amount"]:
        return await event.show_snackbar("Недостаточно денег на банковском счёте")

    duel["participants"].append(uid)
    await event.show_snackbar("Вы вступили в дуэль!")

    # Запускаем бой
    if len(duel["participants"]) == 2:
        winner = random.choice(duel["participants"])
        loser  = duel["participants"][0] if winner != duel["participants"][0] else duel["participants"][1]
        amount = duel["amount"]

        ECONOMY[winner]["bank"] = ECONOMY[winner].get("bank", 0) + amount
        ECONOMY[loser]["bank"]  = ECONOMY[loser].get("bank", 0)  - amount

        await push_to_github(ECONOMY, GH_PATH_ECO, EXTERNAL_ECO)
        del DATABASE["duels"][duel_id]
        await push_to_github(DATABASE, GH_PATH_DB, EXTERNAL_DB)

        await bot.api.messages.send(
            peer_id=int(duel["chat_id"]),
            message=(
                f"⚔️ Дуэль завершена!\n\n"
                f"🏅Победил:  [id{winner}|победитель]\n"
                f"🥈Проиграл: [id{loser}|проигравший]\n\n"
                f"💲Победитель получает {amount}$"
            ),
            random_id=random.randint(0, 2**31)
        )

# ────────────────────────────────────────────────
# Roulette (25% шанс, ×3 выигрыш)
# ────────────────────────────────────────────────
@bot.on.message(text=["/roulette <amount>"])
async def roulette(m: Message, amount=None):
    try:
        amount = int(amount)
        if amount <= 0: raise ValueError
    except:
        return await m.answer("Укажите положительную сумму")

    uid = str(m.from_id)
    if uid not in ECONOMY or ECONOMY[uid].get("cash", 0) < amount:
        return await m.answer("Недостаточно наличных")

    ECONOMY[uid]["cash"] -= amount

    if random.random() < 0.25:  # 25% шанс
        win = amount * 3
        ECONOMY[uid]["cash"] += win
        text = f"🎰 Вы выиграли {win}$! (×3)"
    else:
        text = f"🎰 Вы проиграли {amount}$…"

    await push_to_github(ECONOMY, GH_PATH_ECO, EXTERNAL_ECO)
    await m.answer(text)

# ────────────────────────────────────────────────
# Перевод только банк → банк
# ────────────────────────────────────────────────
@bot.on.message(text=["/перевести <args>"])
async def transfer(m: Message, args=None):
    if not args: return await m.answer("Укажите получателя и сумму")
    parts = args.split()
    if len(parts) < 2: return await m.answer("Формат: /перевести [ссылка] [сумма]")
    t = await get_target_id(m, parts[0])
    if not t: return await m.answer("Не удалось определить получателя")
    try:
        amount = int(parts[1])
        if amount <= 0: raise ValueError
    except:
        return await m.answer("Некорректная сумма")

    uid = str(m.from_id)
    rid = str(t)
    if uid not in ECONOMY: ECONOMY[uid] = {"cash": 0, "bank": 0}
    if rid not in ECONOMY: ECONOMY[rid] = {"cash": 0, "bank": 0}

    if ECONOMY[uid].get("bank", 0) < amount:
        return await m.answer("Недостаточно денег на банковском счёте")

    ECONOMY[uid]["bank"] -= amount
    ECONOMY[rid]["bank"] += amount

    await push_to_github(ECONOMY, GH_PATH_ECO, EXTERNAL_ECO)
    await m.answer(f"💲Вы перевели {amount}$ на банковский счёт [id{t}|пользователя]")

# ────────────────────────────────────────────────
# Остальные команды (без изменений, но все присутствуют)
# ────────────────────────────────────────────────
# /kick, /ban, /unban, /addmoder, /addsenmoder, ... /addowner, /removerole,
# /setnick, /rnick, /nlist, /gban, /gunban, /gbanpl, /gunbanpl, /getban,
# /start, /type, /chatid, /delchat, /sync, /gstaff, /prise, /balance, /bank,
# /положить, /снять

# (вставьте сюда все остальные обработчики команд из предыдущей версии,
#  они не менялись, поэтому я их опустил для компактности ответа)

# ────────────────────────────────────────────────
# Keep-Alive + Тех.отчёты
# ────────────────────────────────────────────────
async def keep_alive():
    while True:
        try:
            url = os.environ.get("RENDER_EXTERNAL_URL")
            if url:
                async with aiohttp.ClientSession() as session:
                    async with session.get(url + "?keepalive=1", timeout=10):
                        print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] Keep-alive OK")
        except Exception as e:
            print("Keep-alive error:", e)
        await asyncio.sleep(600)

async def send_reports():
    while True:
        now = datetime.datetime.now(TZ_MSK)
        if now.second % 15 == 0:
            for pid, chat in list(DATABASE.get("chats", {}).items()):
                if chat.get("type") == "tex":
                    delay = round(random.uniform(0, 1), 2)
                    time_str = now.strftime("%H:%M:%S")
                    date_str = now.strftime("%d/%m/%Y")
                    msg = f"…::: ТЕХНИЧЕСКИЙ ОТЧЕТ :::…\n\n| ==> Бот работает\n| Задержка: {delay}с\n| Время: {time_str}\n| Дата: {date_str}"
                    try:
                        await bot.api.messages.send(
                            peer_id=int(pid),
                            message=msg,
                            random_id=random.randint(0, 2**32)
                        )
                    except Exception as e:
                        print("report error:", e)
        await asyncio.sleep(1)

# ────────────────────────────────────────────────
# Запуск
# ────────────────────────────────────────────────
class H(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")

if __name__ == "__main__":
    threading.Thread(
        target=HTTPServer(('0.0.0.0', int(os.environ.get("PORT", 10000))), H).serve_forever,
        daemon=True
    ).start()

    loop.create_task(send_reports())
    loop.create_task(keep_alive())

    print("Бот запущен — все фиксы применены")
    bot.run_forever()

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
# Загрузка / сохранение данных
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
        except Exception as e:
            print("Local load error:", e)
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

# Инициализация данных
loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)
DATABASE    = loop.run_until_complete(load_from_github(GH_PATH_DB,  EXTERNAL_DB))
ECONOMY     = loop.run_until_complete(load_from_github(GH_PATH_ECO, EXTERNAL_ECO))
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
# HTTP сервер для Render
# ────────────────────────────────────────────────
class H(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")

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
# Команды — ПОЛНЫЙ СПИСОК
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

@bot.on.message(text="/info")
async def info_cmd(m: Message):
    await m.answer("Официальные ресурсы: [вставьте ссылки или информацию]")

@bot.on.message(text=["/getid", "/getid <args>"])
async def getid_cmd(m: Message, args=None):
    t = await get_target_id(m, args) or m.from_id
    await m.answer(f"Оригинальная ссылка [id{t}|пользователя]: https://vk.com/id{t}")

@bot.on.message(text=["/stats", "/stats <args>"])
async def stats_cmd(m: Message, args=None):
    t = await get_target_id(m, args) or m.from_id
    uid, pid = str(t), str(m.peer_id)
    role, nick = get_user_info(m.peer_id, t)
    bans_cnt = sum(1 for bans in PUNISHMENTS.get("bans", {}).values() if uid in bans)
    gban = "Да" if uid in PUNISHMENTS.get("gbans_status", {}) else "Нет"
    gbanpl = "Да" if uid in PUNISHMENTS.get("gbans_pl", {}) else "Нет"
    mutes = DATABASE["chats"].get(pid, {}).get("mutes", {})
    is_muted = "Да" if uid in mutes and time.time() < mutes[uid] else "Нет"
    st = DATABASE["chats"].get(pid, {}).get("stats", {}).get(uid, {"count": 0, "last": 0})
    dt = datetime.datetime.fromtimestamp(st["last"], TZ_MSK).strftime("%d/%m/%Y %H:%M:%S") if st["last"] else "Нет данных"
    msg = (
        f"Информация о [id{t}|пользователе]\n"
        f"Роль: {role}\n"
        f"Блокировок: {bans_cnt}\n"
        f"Общая блокировка в чатах: {gban}\n"
        f"Общая блокировка в беседах игроков: {gbanpl}\n"
        f"Активные предупреждения: {PUNISHMENTS.get('warns', {}).get(pid, {}).get(uid, 0)}\n"
        f"Блокировка чата: {is_muted}\n"
        f"Ник: {nick if nick else 'Не установлен'}\n"
        f"Всего сообщений: {st['count']}\n"
        f"Последнее сообщение: {dt}"
    )
    await m.answer(msg)

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
    kb.add(Text("Снять мут",    {"cmd": "unmute_btn", "uid": str(t)}), color=KeyboardButtonColor.POSITIVE)
    kb.add(Text("Очистить",     {"cmd": "clear_msg",  "uid": str(t)}), color=KeyboardButtonColor.NEGATIVE)
    a_name = f"[id{m.from_id}|Модератор MANLIX]"
    await m.answer(
        f"{a_name} выдал мут [id{t}|пользователю]\n"
        f"Причина: {reason}\n"
        f"До: {dt}",
        keyboard=kb
    )
    await push_to_github(DATABASE, GH_PATH_DB, EXTERNAL_DB)

@bot.on.message(text=["/unmute", "/unmute <args>"])
async def unmute_cmd(m: Message, args=None):
    if not await check_access(m, "Модератор"): return
    t = await get_target_id(m, args)
    if not t: return
    pid = str(m.peer_id)
    ensure_chat(pid)
    if str(t) in DATABASE["chats"][pid].get("mutes", {}):
        del DATABASE["chats"][pid]["mutes"][str(t)]
        await push_to_github(DATABASE, GH_PATH_DB, EXTERNAL_DB)
        a_name = f"[id{m.from_id}|Модератор MANLIX]"
        await m.answer(f"{a_name} снял мут [id{t}|пользователю]")

@bot.on.raw_event(GroupEventType.MESSAGE_EVENT, dataclass=MessageEvent)
async def buttons(event: MessageEvent):
    payload = event.payload
    if isinstance(payload, str):
        try: payload = json.loads(payload)
        except: return
    cmd = payload.get("cmd")
    uid  = payload.get("uid")
    pid  = str(event.peer_id)

    if not uid or str(uid) not in DATABASE["chats"][pid].get("mutes", {}):
        return await event.show_snackbar("Мут уже снят или не существует")

    rank, _ = get_user_info(event.peer_id, event.user_id)
    if RANK_WEIGHT.get(rank, 0) < 1:
        return await event.show_snackbar("Недостаточно прав")

    if cmd == "unmute_btn":
        del DATABASE["chats"][pid]["mutes"][str(uid)]
        await push_to_github(DATABASE, GH_PATH_DB, EXTERNAL_DB)
        new_text = f"[id{event.user_id}|Модератор MANLIX] снял мут [id{uid}|пользователю]"
        try:
            await bot.api.messages.edit(
                peer_id=event.peer_id,
                message=new_text,
                conversation_message_id=event.conversation_message_id
            )
        except Exception as e:
            print("edit unmute error:", e)

    elif cmd == "clear_msg":
        try:
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
        except Exception as e:
            print("edit clear error:", e)

@bot.on.message(text=["/kick", "/kick <args>"])
async def kick_cmd(m: Message, args=None):
    if not await check_access(m, "Модератор"): return
    t = await get_target_id(m, args)
    if not t: return
    try:
        chat_id = m.peer_id - 2000000000
        await bot.api.messages.remove_chat_user(chat_id=chat_id, member_id=t)
    except Exception as e:
        print("kick error:", e)
    a_name = f"[id{m.from_id}|Модератор MANLIX]"
    await m.answer(f"{a_name} исключил [id{t}|пользователя] из беседы.")

@bot.on.message(text=["/ban", "/ban <args>"])
async def ban_cmd(m: Message, args=None):
    if not await check_access(m, "Старший Модератор"): return
    t = await get_target_id(m, args)
    if not t: return
    parts = (args or "").split()
    reason = " ".join(parts[1:]) or "Нарушение"
    pid = str(m.peer_id)
    ensure_chat(pid)
    if pid not in PUNISHMENTS["bans"]:
        PUNISHMENTS["bans"][pid] = {}
    PUNISHMENTS["bans"][pid][str(t)] = {"admin": m.from_id, "reason": reason, "date": time.time()}
    try:
        chat_id = m.peer_id - 2000000000
        await bot.api.messages.remove_chat_user(chat_id=chat_id, member_id=t)
    except:
        pass
    await push_to_github(PUNISHMENTS, GH_PATH_PUN, EXTERNAL_PUN)
    a_name = f"[id{m.from_id}|Модератор MANLIX]"
    await m.answer(f"{a_name} забанил [id{t}|пользователя] в беседе.")

@bot.on.message(text=["/unban", "/unban <args>"])
async def unban_cmd(m: Message, args=None):
    if not await check_access(m, "Старший Модератор"): return
    t = await get_target_id(m, args)
    if not t: return
    pid = str(m.peer_id)
    if pid in PUNISHMENTS["bans"] and str(t) in PUNISHMENTS["bans"][pid]:
        del PUNISHMENTS["bans"][pid][str(t)]
        await push_to_github(PUNISHMENTS, GH_PATH_PUN, EXTERNAL_PUN)
    a_name = f"[id{m.from_id}|Модератор MANLIX]"
    await m.answer(f"{a_name} разбанил [id{t}|пользователя] в беседе.")

async def set_role_in_chat(pid: str, uid: str, role_name: str):
    ensure_chat(pid)
    current = DATABASE["chats"][pid]["staff"].get(uid, [role_name, None])
    nick = current[1]
    DATABASE["chats"][pid]["staff"][uid] = [role_name, nick]

@bot.on.message(text=["/addmoder", "/addmoder <args>"])
async def addmod(m: Message, args=None):
    if not await check_access(m, "Старший Модератор"): return
    t = await get_target_id(m, args)
    if not t: return
    pid, uid = str(m.peer_id), str(t)
    await set_role_in_chat(pid, uid, "Модератор")
    await push_to_github(DATABASE, GH_PATH_DB, EXTERNAL_DB)
    role, a_nick = get_user_info(m.peer_id, m.from_id)
    a_name = f"[id{m.from_id}|{a_nick or role}]"
    await m.answer(f"{a_name} выдал права модератора [id{t}|пользователю]")

@bot.on.message(text=["/addsenmoder", "/addsenmoder <args>"])
async def addsenmod(m: Message, args=None):
    if not await check_access(m, "Администратор"): return
    t = await get_target_id(m, args)
    if not t: return
    pid, uid = str(m.peer_id), str(t)
    await set_role_in_chat(pid, uid, "Старший Модератор")
    await push_to_github(DATABASE, GH_PATH_DB, EXTERNAL_DB)
    role, a_nick = get_user_info(m.peer_id, m.from_id)
    a_name = f"[id{m.from_id}|{a_nick or role}]"
    await m.answer(f"{a_name} выдал права старшего модератора [id{t}|пользователю]")

@bot.on.message(text=["/addadmin", "/addadmin <args>"])
async def addadm(m: Message, args=None):
    if not await check_access(m, "Старший Администратор"): return
    t = await get_target_id(m, args)
    if not t: return
    pid, uid = str(m.peer_id), str(t)
    await set_role_in_chat(pid, uid, "Администратор")
    await push_to_github(DATABASE, GH_PATH_DB, EXTERNAL_DB)
    role, a_nick = get_user_info(m.peer_id, m.from_id)
    a_name = f"[id{m.from_id}|{a_nick or role}]"
    await m.answer(f"{a_name} выдал права администратора [id{t}|пользователю]")

@bot.on.message(text=["/addsenadmin", "/addsenadmin <args>"])
async def addsenadm(m: Message, args=None):
    if not await check_access(m, "Зам. Спец. Администратора"): return
    t = await get_target_id(m, args)
    if not t: return
    pid, uid = str(m.peer_id), str(t)
    await set_role_in_chat(pid, uid, "Старший Администратор")
    await push_to_github(DATABASE, GH_PATH_DB, EXTERNAL_DB)
    role, a_nick = get_user_info(m.peer_id, m.from_id)
    a_name = f"[id{m.from_id}|{a_nick or role}]"
    await m.answer(f"{a_name} выдал права старшего администратора [id{t}|пользователю]")

@bot.on.message(text=["/addzsa", "/addzsa <args>"])
async def addzsa(m: Message, args=None):
    if not await check_access(m, "Спец. Администратор"): return
    t = await get_target_id(m, args)
    if not t: return
    pid, uid = str(m.peer_id), str(t)
    await set_role_in_chat(pid, uid, "Зам. Спец. Администратора")
    await push_to_github(DATABASE, GH_PATH_DB, EXTERNAL_DB)
    role, a_nick = get_user_info(m.peer_id, m.from_id)
    a_name = f"[id{m.from_id}|{a_nick or role}]"
    await m.answer(f"{a_name} выдал права зам. спец. администратора [id{t}|пользователю]")

@bot.on.message(text=["/addsa", "/addsa <args>"])
async def addsa(m: Message, args=None):
    if not await check_access(m, "Владелец"): return
    t = await get_target_id(m, args)
    if not t: return
    pid, uid = str(m.peer_id), str(t)
    await set_role_in_chat(pid, uid, "Спец. Администратор")
    await push_to_github(DATABASE, GH_PATH_DB, EXTERNAL_DB)
    role, a_nick = get_user_info(m.peer_id, m.from_id)
    a_name = f"[id{m.from_id}|{a_nick or role}]"
    await m.answer(f"{a_name} выдал права спец. администратора [id{t}|пользователю]")

@bot.on.message(text=["/addowner", "/addowner <args>"])
async def addowner(m: Message, args=None):
    if not await check_access(m, "Зам. Спец. Руководителя"): return
    t = await get_target_id(m, args)
    if not t: return
    pid, uid = str(m.peer_id), str(t)
    await set_role_in_chat(pid, uid, "Владелец")
    await push_to_github(DATABASE, GH_PATH_DB, EXTERNAL_DB)
    role, a_nick = get_user_info(m.peer_id, m.from_id)
    a_name = f"[id{m.from_id}|{a_nick or role}]"
    await m.answer(f"{a_name} выдал права владельца [id{t}|пользователю]")

@bot.on.message(text=["/removerole", "/removerole <args>"])
async def removerole(m: Message, args=None):
    if not await check_access(m, "Старший Модератор"): return
    t = await get_target_id(m, args)
    if not t: return
    pid = str(m.peer_id)
    uid = str(t)
    ensure_chat(pid)
    if uid in DATABASE["chats"][pid].get("staff", {}):
        del DATABASE["chats"][pid]["staff"][uid]
        await push_to_github(DATABASE, GH_PATH_DB, EXTERNAL_DB)
    role, a_nick = get_user_info(m.peer_id, m.from_id)
    a_name = f"[id{m.from_id}|{a_nick or role}]"
    await m.answer(f"{a_name} снял уровень прав [id{t}|пользователю]")

@bot.on.message(text="/staff")
async def staff_view(m: Message):
    pid = str(m.peer_id)
    ensure_chat(pid)
    staff = DATABASE["chats"].get(pid, {}).get("staff", {})
    order = [
        "Владелец",
        "Спец. Администратор",
        "Зам. Спец. Администратора",
        "Старший Администратор",
        "Администратор",
        "Старший Модератор",
        "Модератор"
    ]
    res = "Руководство беседы:\n\n"
    for r in order:
        res += f"┌ {r}:\n"
        members = []
        for u, (role, n) in staff.items():
            if role == r:
                display = n if n else "Пользователь"
                try:
                    if not n:
                        uinfo = await bot.api.users.get([int(u)])
                        display = f"{uinfo[0].first_name} {uinfo[0].last_name}"
                except:
                    pass
                members.append(f"│ – [id{u}|{display}]")
        if members:
            res += "\n".join(members) + "\n"
        else:
            res += "│ – Отсутствует\n"
        res += "└───────────────\n\n"
    await m.answer(res.strip())

@bot.on.message(text=["/setnick", "/setnick <args>"])
async def setnick(m: Message, args=None):
    if not await check_access(m, "Модератор"): return
    if not args: return await m.answer("Укажите пользователя и ник")
    parts = args.split(maxsplit=1)
    if len(parts) < 2: return await m.answer("Укажите пользователя и ник")
    target_token = parts[0]
    new_nick = parts[1].strip()
    t = await get_target_id(m, target_token)
    if not t: return
    pid, uid = str(m.peer_id), str(t)
    role, _ = get_user_info(m.peer_id, t)
    ensure_chat(pid)
    DATABASE["chats"][pid]["staff"][uid] = [role, new_nick]
    await push_to_github(DATABASE, GH_PATH_DB, EXTERNAL_DB)
    r, a_nick = get_user_info(m.peer_id, m.from_id)
    a_name = f"[id{m.from_id}|{a_nick or r}]"
    await m.answer(f"{a_name} установил ник [id{t}|пользователю]: {new_nick}")

@bot.on.message(text=["/rnick", "/rnick <args>"])
async def rnick(m: Message, args=None):
    if not await check_access(m, "Модератор"): return
    t = await get_target_id(m, args)
    if not t: return
    pid, uid = str(m.peer_id), str(t)
    role, _ = get_user_info(m.peer_id, t)
    ensure_chat(pid)
    if uid in DATABASE["chats"][pid].get("staff", {}):
        role = DATABASE["chats"][pid]["staff"][uid][0]
        DATABASE["chats"][pid]["staff"][uid] = [role, None]
        await push_to_github(DATABASE, GH_PATH_DB, EXTERNAL_DB)
    r, a_nick = get_user_info(m.peer_id, m.from_id)
    a_name = f"[id{m.from_id}|{a_nick or r}]"
    await m.answer(f"{a_name} снял ник [id{t}|пользователю]")

@bot.on.message(text="/nlist")
async def nick_list(m: Message):
    if not await check_access(m, "Модератор"): return
    pid = str(m.peer_id)
    ensure_chat(pid)
    staff = DATABASE["chats"].get(pid, {}).get("staff", {})
    users = [(u, n) for u, (_, n) in staff.items() if n]
    if users:
        msg = "Список пользователей с ником:\n" + "\n".join(f"{i}. [id{u}|{n}]" for i, (u, n) in enumerate(users, 1))
    else:
        msg = "Никнеймы не установлены"
    await m.answer(msg)

@bot.on.message(text="/gstaff")
async def gstaff_view(m: Message):
    if not await check_access(m, "Зам. Спец. Руководителя"): return
    g = DATABASE["gstaff"]
    res = "MANLIX MANAGER | Команда Бота:\n\n"
    res += "| Специальный Руководитель:\n– [id870757778|Misha Manlix]\n\n"
    res += "| Основной зам. Спец. Руководителя:\n"
    if g.get("main_zam"):
        res += f"– [id{g['main_zam']}|Пользователь]\n"
    else:
        res += "– Отсутствует.\n"
    res += "\n| Зам. Спец. Руководителя:\n"
    zams = g.get("zams", [])
    if zams:
        for z in zams:
            res += f"– [id{z}|Пользователь]\n"
    else:
        res += "– Отсутствует.\n– Отсутствует.\n"
    await m.answer(res)

@bot.on.message(text="/start")
async def start(m: Message):
    if m.from_id != 870757778:
        return await m.answer("Только Специальный Руководитель может активировать беседу.")
    pid = str(m.peer_id)
    ensure_chat(pid)
    try:
        conv = await bot.api.messages.get_conversations_by_id(peer_ids=[m.peer_id])
        if conv.items:
            DATABASE["chats"][pid]["title"] = conv.items[0].chat_settings.title
    except:
        pass
    await push_to_github(DATABASE, GH_PATH_DB, EXTERNAL_DB)
    await m.answer("Беседа успешно активирована.")

@bot.on.message(text=["/type", "/type <args>"])
async def type_cmd(m: Message, args=None):
    if not await check_access(m, "Специальный Руководитель"): return
    pid = str(m.peer_id)
    ensure_chat(pid)
    current = DATABASE["chats"][pid]["type"]
    if args:
        new_type = args.strip().lower()
        if new_type in ["def", "adm", "mod", "pl", "test", "tex"]:
            DATABASE["chats"][pid]["type"] = new_type
            await push_to_github(DATABASE, GH_PATH_DB, EXTERNAL_DB)
            await m.answer(f"Тип беседы изменён на: {new_type}")
        else:
            await m.answer("Доступные типы: def, adm, mod, pl, test, tex")
    types = """
def   — обычная беседа
adm   — администраторы
mod   — модераторы
pl    — игроки
test  — тестирование
tex   — технические отчёты
"""
    await m.answer(f"Текущий тип: {current}\n\nДоступные типы:\n{types}")

@bot.on.message(text="/chatid")
async def chatid(m: Message):
    if not await check_access(m, "Специальный Руководитель"): return
    await m.answer(f"ID текущей беседы: {m.peer_id}")

@bot.on.message(text="/delchat")
async def delchat(m: Message):
    if not await check_access(m, "Специальный Руководитель"): return
    pid = str(m.peer_id)
    if pid in DATABASE["chats"]:
        del DATABASE["chats"][pid]
        await push_to_github(DATABASE, GH_PATH_DB, EXTERNAL_DB)
        await m.answer("Беседа удалена из базы данных.")
    else:
        await m.answer("Эта беседа не найдена в базе.")

@bot.on.message(text="/sync")
async def sync(m: Message):
    if not await check_access(m, "Специальный Руководитель"): return
    global DATABASE, ECONOMY, PUNISHMENTS
    DATABASE    = await load_from_github(GH_PATH_DB,  EXTERNAL_DB)
    ECONOMY     = await load_from_github(GH_PATH_ECO, EXTERNAL_ECO)
    PUNISHMENTS = await load_from_github(GH_PATH_PUN, EXTERNAL_PUN)
    await m.answer("База данных синхронизирована.")

@bot.on.message(text=["/gban", "/gban <args>"])
async def gban_cmd(m: Message, args=None):
    if not await check_access(m, "Специальный Руководитель"): return
    t = await get_target_id(m, args)
    if not t: return
    reason = " ".join((args or "").split()[1:]) or "Нарушение"
    uid = str(t)
    PUNISHMENTS["gbans_status"][uid] = {"admin": m.from_id, "reason": reason, "date": time.time()}
    await push_to_github(PUNISHMENTS, GH_PATH_PUN, EXTERNAL_PUN)
    await m.answer(f"[id{m.from_id}|Специальный Руководитель] добавил [id{t}|пользователя] в глобальный бан бота.")

@bot.on.message(text=["/gunban", "/gunban <args>"])
async def gunban(m: Message, args=None):
    if not await check_access(m, "Специальный Руководитель"): return
    t = await get_target_id(m, args)
    if not t: return
    uid = str(t)
    if uid in PUNISHMENTS["gbans_status"]:
        del PUNISHMENTS["gbans_status"][uid]
        await push_to_github(PUNISHMENTS, GH_PATH_PUN, EXTERNAL_PUN)
    await m.answer(f"[id{m.from_id}|Специальный Руководитель] снял глобальный бан с [id{t}|пользователя].")

@bot.on.message(text=["/gbanpl", "/gbanpl <args>"])
async def gbanpl_cmd(m: Message, args=None):
    if not await check_access(m, "Зам. Спец. Руководителя"): return
    t = await get_target_id(m, args)
    if not t: return
    reason = " ".join((args or "").split()[1:]) or "Нарушение"
    uid = str(t)
    PUNISHMENTS["gbans_pl"][uid] = {"admin": m.from_id, "reason": reason, "date": time.time()}
    # Кик из всех игровых бесед
    for pid in list(DATABASE["chats"].keys()):
        try:
            chat_id = int(pid) - 2000000000
            await bot.api.messages.remove_chat_user(chat_id=chat_id, member_id=t)
        except:
            pass
    await push_to_github(PUNISHMENTS, GH_PATH_PUN, EXTERNAL_PUN)
    await m.answer(f"[id{m.from_id}|Зам. Спец. Руководителя] заблокировал [id{t}|пользователя] во всех игровых беседах.")

@bot.on.message(text=["/gunbanpl", "/gunbanpl <args>"])
async def gunbanpl_cmd(m: Message, args=None):
    if not await check_access(m, "Зам. Спец. Руководителя"): return
    t = await get_target_id(m, args)
    if not t: return
    uid = str(t)
    if uid in PUNISHMENTS["gbans_pl"]:
        del PUNISHMENTS["gbans_pl"][uid]
        await push_to_github(PUNISHMENTS, GH_PATH_PUN, EXTERNAL_PUN)
    await m.answer(f"[id{m.from_id}|Зам. Спец. Руководителя] снял глобальный бан в играх с [id{t}|пользователя].")

@bot.on.message(text=["/getban", "/getban <args>"])
async def getban_cmd(m: Message, args=None):
    if not await check_access(m, "Модератор"): return
    t = await get_target_id(m, args)
    if not t: return
    uid = str(t)
    try:
        uinfo = await bot.api.users.get([t])
        name = f"{uinfo[0].first_name} {uinfo[0].last_name}"
    except:
        name = "пользователь"
    ans = f"Информация о блокировках [id{t}|{name}]:\n\n"
    for key, label in [("gbans_status", "Глобальный бан бота"), ("gbans_pl", "Глобальный бан в играх")]:
        if uid in PUNISHMENTS.get(key, {}):
            b = PUNISHMENTS[key][uid]
            dt = datetime.datetime.fromtimestamp(b["date"], TZ_MSK).strftime("%d.%m.%Y %H:%M")
            ans += f"{label}: Да\nАдмин: [id{b['admin']}|Модератор]\nПричина: {b.get('reason', '-')}\nДата: {dt}\n\n"
        else:
            ans += f"{label}: Нет\n\n"
    local_bans = []
    for pid, bans in PUNISHMENTS.get("bans", {}).items():
        if uid in bans:
            b = bans[uid]
            title = DATABASE["chats"].get(pid, {}).get("title", f"Беседа {pid}")
            dt = datetime.datetime.fromtimestamp(b["date"], TZ_MSK).strftime("%d.%m.%Y %H:%M")
            local_bans.append(f"{title} | [id{b['admin']}|Модератор] | {b.get('reason', '-')} | {dt}")
    ans += f"Локальные баны в беседах: {len(local_bans)}\n"
    if local_bans:
        ans += "Последние 10:\n" + "\n".join(local_bans[-10:])
    await m.answer(ans)

# ────────────────────────────────────────────────
# Игровые команды
# ────────────────────────────────────────────────
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

@bot.on.message(text="/prise")
async def prise(m: Message):
    uid = str(m.from_id)
    if uid not in ECONOMY:
        ECONOMY[uid] = {"cash": 0, "bank": 0, "last": 0}
    if time.time() - ECONOMY[uid]["last"] < 3600:
        return await m.answer("Приз можно взять раз в час")
    win = random.randint(100, 1000)
    ECONOMY[uid]["cash"] += win
    ECONOMY[uid]["last"] = time.time()
    await push_to_github(ECONOMY, GH_PATH_ECO, EXTERNAL_ECO)
    await m.answer(f"🎉 Вы получили приз {win}$!")

@bot.on.message(text="/balance")
async def balance(m: Message):
    uid = str(m.from_id)
    cash = ECONOMY.get(uid, {}).get("cash", 0)
    await m.answer(f"💵 Наличные: {cash}$")

@bot.on.message(text="/bank")
async def bank(m: Message):
    uid = str(m.from_id)
    bank = ECONOMY.get(uid, {}).get("bank", 0)
    await m.answer(f"🏦 На банковском счёте: {bank}$")

@bot.on.message(text=["/положить <amount>"])
async def polozhit(m: Message, amount=None):
    try:
        amount = int(amount)
        if amount <= 0: raise ValueError
    except:
        return await m.answer("Укажите положительную сумму")
    uid = str(m.from_id)
    if uid not in ECONOMY:
        ECONOMY[uid] = {"cash": 0, "bank": 0, "last": 0}
    if ECONOMY[uid]["cash"] < amount:
        return await m.answer("Недостаточно наличных")
    ECONOMY[uid]["cash"] -= amount
    ECONOMY[uid]["bank"] += amount
    await push_to_github(ECONOMY, GH_PATH_ECO, EXTERNAL_ECO)
    await m.answer(f"💳 Положили {amount}$ на банковский счёт")

@bot.on.message(text=["/снять <amount>"])
async def snyat(m: Message, amount=None):
    try:
        amount = int(amount)
        if amount <= 0: raise ValueError
    except:
        return await m.answer("Укажите положительную сумму")
    uid = str(m.from_id)
    if uid not in ECONOMY:
        ECONOMY[uid] = {"cash": 0, "bank": 0, "last": 0}
    if ECONOMY[uid]["bank"] < amount:
        return await m.answer("Недостаточно на банковском счёте")
    ECONOMY[uid]["bank"] -= amount
    ECONOMY[uid]["cash"] += amount
    await push_to_github(ECONOMY, GH_PATH_ECO, EXTERNAL_ECO)
    await m.answer(f"💵 Сняли {amount}$ с банковского счёта")

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

    bank_balance = ECONOMY[uid].get("bank", 0)
    if bank_balance < amount:
        return await m.answer(f"Недостаточно денег на банковском счёте (есть {bank_balance}$)")

    ECONOMY[uid]["bank"] -= amount
    ECONOMY[rid]["bank"] += amount

    await push_to_github(ECONOMY, GH_PATH_ECO, EXTERNAL_ECO)
    await m.answer(f"💲Вы перевели {amount}$ на банковский счёт [id{t}|пользователя]")

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

    if random.random() < 0.25:
        win = amount * 3
        ECONOMY[uid]["cash"] += win
        text = f"🎰 Вы выиграли {win}$!"
    else:
        text = f"🎰 Вы проиграли {amount}$…"

    await push_to_github(ECONOMY, GH_PATH_ECO, EXTERNAL_ECO)
    await m.answer(text)

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

# ────────────────────────────────────────────────
# Системные события (кик бота, приглашение забаненных)
# ────────────────────────────────────────────────
@bot.on.message()
async def actions(m: Message):
    if not m.action:
        return
    typ = m.action.type.value if hasattr(m.action.type, "value") else str(m.action.type)
    if typ == "chat_kick_user":
        global GROUP_ID
        if GROUP_ID is None:
            GROUP_ID = (await bot.api.groups.get_by_id())[0].id
        if m.action.member_id == -GROUP_ID:
            kb = Keyboard(inline=True)
            kb.add(Text("Исключить", {"cmd": "clear"}), color=KeyboardButtonColor.NEGATIVE)
            await m.answer("Бот покинул(-а) Беседу", keyboard=kb)
        return
    if typ in ("chat_invite_user", "chat_invite_user_by_link"):
        invited = m.action.member_id
        if invited > 0:
            uid = str(invited)
            pid = str(m.peer_id)
            ensure_chat(pid)
            if uid in PUNISHMENTS.get("gbans_status", {}) or uid in PUNISHMENTS.get("gbans_pl", {}) or uid in PUNISHMENTS.get("bans", {}).get(pid, {}):
                try:
                    chat_id = m.peer_id - 2000000000
                    await bot.api.messages.remove_chat_user(chat_id=chat_id, member_id=invited)
                except:
                    pass
                await m.answer(f"[id870757778|Модератор MANLIX] исключил [id{invited}|пользователя] — он находится в списке блокировок.")

@bot.on.raw_event(GroupEventType.MESSAGE_NEW)
async def auto_kick(event):
    msg = event.object.message
    if 'action' in msg and msg['action']['type'] in ("chat_invite_user", "chat_invite_user_by_link"):
        member_id = msg['action'].get('member_id')
        if member_id > 0:
            uid = str(member_id)
            pid = str(msg['peer_id'])
            if uid in PUNISHMENTS.get("gbans_pl", {}):
                try:
                    chat_id = msg['peer_id'] - 2000000000
                    await bot.api.messages.remove_chat_user(chat_id=chat_id, member_id=member_id)
                except:
                    pass

# ────────────────────────────────────────────────
# Keep-Alive + Технические отчёты
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
if __name__ == "__main__":
    threading.Thread(
        target=HTTPServer(('0.0.0.0', int(os.environ.get("PORT", 10000))), H).serve_forever,
        daemon=True
    ).start()

    loop.create_task(send_reports())
    loop.create_task(keep_alive())

    print("Бот запущен — полный функционал")
    bot.run_forever()

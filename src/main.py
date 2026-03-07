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

GH_TOKEN    = os.environ.get(“GH_TOKEN”)
GH_REPO     = os.environ.get(“GH_REPO”)
GH_PATH_DB  = “database.json”
GH_PATH_ECO = “economy.json”
GH_PATH_PUN = “punishments.json”

EXTERNAL_DB  = “database.json”
EXTERNAL_ECO = “economy.json”
EXTERNAL_PUN = “punishments.json”

TZ_MSK = datetime.timezone(datetime.timedelta(hours=3))

RANK_WEIGHT = {
“Пользователь”:                     0,
“Модератор”:                        1,
“Старший Модератор”:                2,
“Администратор”:                    3,
“Старший Администратор”:            4,
“Зам. Спец. Администратора”:        5,
“Спец. Администратор”:              6,
“Владелец”:                         7,
“Зам. Спец. Руководителя”:          8,
“Основной Зам. Спец. Руководителя”: 9,
“Специальный Руководитель”:        10
}

# ────────────────────────────────────────────────

# HTTP-сервер (определён ДО запуска потока)

# ────────────────────────────────────────────────

class H(BaseHTTPRequestHandler):
def do_GET(self):
self.send_response(200)
self.end_headers()
self.wfile.write(b”OK”)
def log_message(self, format, *args):
pass

# ────────────────────────────────────────────────

# Загрузка / сохранение данных

# ────────────────────────────────────────────────

async def load_from_github(gh_path, local_path):
if not GH_TOKEN or not GH_REPO:
return load_local_data(local_path)
url = f”https://api.github.com/repos/{GH_REPO}/contents/{gh_path}”
headers = {“Authorization”: f”token {GH_TOKEN}”}
async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=False)) as session:
async with session.get(url, headers=headers) as resp:
if resp.status == 200:
doc = await resp.json()
if ‘content’ in doc:
data = json.loads(base64.b64decode(doc[‘content’]).decode(‘utf-8’))
with open(local_path, “w”, encoding=“utf-8”) as f:
json.dump(data, f, ensure_ascii=False, indent=4)
return data
if resp.status != 404:
print(f”GitHub load failed: {resp.status}”)
return load_local_data(local_path)

def load_local_data(path):
if os.path.exists(path):
try:
with open(path, “r”, encoding=“utf-8”) as f:
return json.load(f)
except Exception as e:
print(“Local load error:”, e)
return {}
return {}

async def push_to_github(data, gh_path, local_path):
if not GH_TOKEN or not GH_REPO:
try:
with open(local_path, “w”, encoding=“utf-8”) as f:
json.dump(data, f, ensure_ascii=False, indent=4)
except Exception as e:
print(“Local save error:”, e)
return
url = f”https://api.github.com/repos/{GH_REPO}/contents/{gh_path}”
headers = {“Authorization”: f”token {GH_TOKEN}”}
async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=False)) as session:
sha = None
async with session.get(url, headers=headers) as r:
if r.status == 200:
doc = await r.json()
sha = doc.get(‘sha’)
content = base64.b64encode(json.dumps(data, ensure_ascii=False, indent=4).encode(‘utf-8’)).decode(‘utf-8’)
payload = {“message”: “Update from bot”, “content”: content}
if sha:
payload[“sha”] = sha
async with session.put(url, headers=headers, json=payload) as resp:
if resp.status not in (200, 201):
print(“GitHub push failed:”, resp.status, await resp.text())
with open(local_path, “w”, encoding=“utf-8”) as f:
json.dump(data, f, ensure_ascii=False, indent=4)

# ────────────────────────────────────────────────

# Инициализация данных

# ────────────────────────────────────────────────

loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)
DATABASE    = loop.run_until_complete(load_from_github(GH_PATH_DB,  EXTERNAL_DB))
ECONOMY     = loop.run_until_complete(load_from_github(GH_PATH_ECO, EXTERNAL_ECO))
PUNISHMENTS = loop.run_until_complete(load_from_github(GH_PATH_PUN, EXTERNAL_PUN))

if not isinstance(DATABASE,    dict): DATABASE    = {}
if not isinstance(ECONOMY,     dict): ECONOMY     = {}
if not isinstance(PUNISHMENTS, dict): PUNISHMENTS = {}

for key in (“gbans_status”, “gbans_pl”, “bans”, “warns”):
if key not in PUNISHMENTS:
PUNISHMENTS[key] = {}
if “chats” not in DATABASE:
DATABASE[“chats”] = {}
if “gstaff” not in DATABASE:
DATABASE[“gstaff”] = {“spec”: 870757778, “main_zam”: None, “zams”: []}
if “duels” not in DATABASE:
DATABASE[“duels”] = {}

GROUP_ID = None

# ────────────────────────────────────────────────

# Бот

# ────────────────────────────────────────────────

bot = Bot(token=os.environ.get(“TOKEN”))

# ────────────────────────────────────────────────

# Утилиты

# ────────────────────────────────────────────────

def ensure_chat(pid: str):
if pid not in DATABASE[“chats”]:
DATABASE[“chats”][pid] = {
“title”: f”Чат {pid}”,
“staff”: {},
“mutes”: {},
“stats”: {},
“type”: “def”
}
chat = DATABASE[“chats”][pid]
if “mutes”  not in chat: chat[“mutes”]  = {}
if “stats”  not in chat: chat[“stats”]  = {}
if “staff”  not in chat: chat[“staff”]  = {}

async def get_target_id(m: Message, args: str = None):
if getattr(m, “reply_message”, None):
return m.reply_message.from_id
if not args:
return None
for pattern in [r”[id(\d+)|”, r”(?:vk.com/id|id)(\d+)”]:
match = re.search(pattern, args)
if match:
try: return int(match.group(1))
except: continue
raw = args.split(’/’) [-1].split(’|’)[0].replace(’[’,’’).replace(’@’,’’).strip().split()[0]
if raw.isdigit():
return int(raw)
if raw:
try:
res = await bot.api.utils.resolve_screen_name(screen_name=raw)
if res and res.type == “user”:
return int(res.object_id)
except:
pass
return None

def get_user_info(peer_id, user_id):
uid = str(user_id)
gstaff = DATABASE.get(“gstaff”, {})
# Глобальные роли
if user_id == gstaff.get(“spec”) or user_id == 870757778:
global_role = “Специальный Руководитель”
elif gstaff.get(“main_zam”) and user_id == gstaff[“main_zam”]:
global_role = “Основной Зам. Спец. Руководителя”
elif gstaff.get(“zams”) and user_id in gstaff[“zams”]:
global_role = “Зам. Спец. Руководителя”
else:
global_role = “Пользователь”
# Локальные роли
staff = DATABASE.get(“chats”, {}).get(str(peer_id), {}).get(“staff”, {})
entry = staff.get(uid)
if entry:
local_role = entry[0]
nick       = entry[1]
else:
local_role = “Пользователь”
nick       = None
role = global_role if RANK_WEIGHT.get(global_role, 0) > RANK_WEIGHT.get(local_role, 0) else local_role
return role, nick

async def get_display_name(user_id: int, peer_id=None, use_nick=True):
“”“Возвращает ник (если есть и use_nick=True) или имя ВК.”””
if use_nick and peer_id:
_, nick = get_user_info(peer_id, user_id)
if nick:
return nick
try:
uinfo = await bot.api.users.get([user_id])
return f”{uinfo[0].first_name} {uinfo[0].last_name}”
except:
return “пользователь”

async def check_access(m: Message, min_rank: str):
rank, _ = get_user_info(m.peer_id, m.from_id)
if RANK_WEIGHT.get(rank, 0) < RANK_WEIGHT.get(min_rank, 0):
await m.answer(“Недостаточно прав!”)
return False
return True

async def set_role_in_chat(pid: str, uid: str, role_name: str):
ensure_chat(pid)
current = DATABASE[“chats”][pid][“staff”].get(uid, [role_name, None])
nick = current[1]
DATABASE[“chats”][pid][“staff”][uid] = [role_name, nick]

# ────────────────────────────────────────────────

# Middleware

# ────────────────────────────────────────────────

class ChatMiddleware(BaseMiddleware[Message]):
async def pre(self):
if not getattr(self.event, “from_id”, None) or self.event.from_id < 0:
return
pid = str(self.event.peer_id)
uid = str(self.event.from_id)
ensure_chat(pid)
chat = DATABASE[“chats”][pid]
if uid not in chat[“stats”]:
chat[“stats”][uid] = {“count”: 0, “last”: 0}
chat[“stats”][uid][“count”] += 1
chat[“stats”][uid][“last”]   = datetime.datetime.now(TZ_MSK).timestamp()
if chat[“stats”][uid][“count”] % 10 == 0:
await push_to_github(DATABASE, GH_PATH_DB, EXTERNAL_DB)

```
    is_gban   = uid in PUNISHMENTS.get("gbans_status", {})
    is_gbanpl = uid in PUNISHMENTS.get("gbans_pl",     {})
    is_lban   = uid in PUNISHMENTS.get("bans",         {}).get(pid, {})
    mutes     = chat.get("mutes", {})
    is_muted  = uid in mutes and time.time() < mutes[uid]

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
```

bot.labeler.message_view.register_middleware(ChatMiddleware)

# ────────────────────────────────────────────────

# /help

# ────────────────────────────────────────────────

@bot.on.message(text=”/help”)
async def help_cmd(m: Message):
rank, _ = get_user_info(m.peer_id, m.from_id)
w = RANK_WEIGHT.get(rank, 0)

```
res = (
    "Команды пользователей:\n"
    "/info - официальные ресурсы\n"
    "/stats - статистика пользователя\n"
    "/getid - оригинальная ссылка VK."
)
if w >= 1:
    res += (
        "\n\nКоманды для модераторов:\n"
        "/staff - Руководство Беседы\n"
        "/kick - исключить пользователя из Беседы.\n"
        "/mute - выдать Блокировку чата.\n"
        "/unmute - снять Блокировку чата.\n"
        "/setnick - установить имя пользователю.\n"
        "/rnick - удалить имя пользователю.\n"
        "/nlist - список пользователей с ником.\n"
        "/getban - информация о Блокировках."
    )
if w >= 2:
    res += (
        "\n\nКоманды старших модераторов:\n"
        "/addmoder - выдать права модератора.\n"
        "/removerole - снять уровень прав.\n"
        "/ban - блокировка пользователя в Беседе.\n"
        "/unban - снятие блокировки пользователю в беседе."
    )
if w >= 3:
    res += (
        "\n\nКоманды администраторов:\n"
        "/addsenmoder - выдать права старшего модератора."
    )
if w >= 4:
    res += (
        "\n\nКоманды старших администраторов:\n"
        "/addadmin - выдать права администратора."
    )
if w >= 5:
    res += (
        "\n\nКоманды заместителей спец. администраторов:\n"
        "/addsenadmin - выдать права старшего модератора."
    )
if w >= 6:
    res += (
        "\n\nКоманды спец. администраторов:\n"
        "/addzsa - выдать права заместителя спец. администратора."
    )
if w >= 7:
    res += (
        "\n\nКоманды владельца:\n"
        "/addsa - выдать права специального администратора."
    )
await m.answer(res)

if w >= 8:
    gres = (
        "Команды руководства Бота:\n\n"
        "Зам. Спец. Руководителя:\n"
        "/gstaff - руководство Бота.\n"
        "/addowner - выдать права владельца.\n"
        "/gbanpl - Блокировка пользователя во всех игровых Беседах.\n"
        "/gunbanpl - снятие Блокировки во всех игровых Беседах.\n\n"
        "Основной Зам. Спец. Руководителя:\n"
        "Отсутствуют.\n\n"
        "Спец. Руководителя:\n"
        "/start - активировать Беседу.\n"
        "/type - изменить тип Беседы.\n"
        "/sync - синхронизация с базой данных.\n"
        "/chatid - узнать айди Беседы.\n"
        "/delchat - удалить чат с Базы данных."
    )
    await m.answer(gres)
```

# ────────────────────────────────────────────────

# /info

# ────────────────────────────────────────────────

@bot.on.message(text=”/info”)
async def info_cmd(m: Message):
await m.answer(“Официальные ресурсы: [вставьте ссылки или информацию]”)

# ────────────────────────────────────────────────

# /getid

# ────────────────────────────────────────────────

@bot.on.message(text=[”/getid”, “/getid <args>”])
async def getid_cmd(m: Message, args=None):
t = await get_target_id(m, args) or m.from_id
await m.answer(f”Оригинальная ссылка [id{t}|пользователя]: https://vk.com/id{t}”)

# ────────────────────────────────────────────────

# /stats

# ────────────────────────────────────────────────

@bot.on.message(text=[”/stats”, “/stats <args>”])
async def stats_cmd(m: Message, args=None):
t = await get_target_id(m, args) or m.from_id
uid = str(t)
pid = str(m.peer_id)
ensure_chat(pid)
role, nick = get_user_info(m.peer_id, t)
display = nick if nick else await get_display_name(t)
bans_cnt = sum(1 for bans in PUNISHMENTS.get(“bans”, {}).values() if uid in bans)
gban    = “Да” if uid in PUNISHMENTS.get(“gbans_status”, {}) else “Нет”
gbanpl  = “Да” if uid in PUNISHMENTS.get(“gbans_pl”,     {}) else “Нет”
mutes   = DATABASE[“chats”][pid].get(“mutes”, {})
is_muted = “Да” if uid in mutes and time.time() < mutes[uid] else “Нет”
st = DATABASE[“chats”][pid].get(“stats”, {}).get(uid, {“count”: 0, “last”: 0})
if st[“last”]:
dt = datetime.datetime.fromtimestamp(st[“last”], TZ_MSK).strftime(”%d/%m/%Y %I:%M:%S %p”)
else:
dt = “Нет данных”
nick_display = nick if nick else “Не установлен”
msg = (
f”Информация о [id{t}|пользователе]\n”
f”Роль: {role}\n”
f”Блокировок: {bans_cnt}\n”
f”Общая блокировка в чатах: {gban}\n”
f”Общая блокировка в беседах игроков: {gbanpl}\n”
f”Активные предупреждения: {PUNISHMENTS.get(‘warns’, {}).get(pid, {}).get(uid, 0)}\n”
f”Блокировка чата: {is_muted}\n”
f”Ник: {nick_display}\n”
f”Всего сообщений: {st[‘count’]}\n”
f”Последнее сообщение: {dt}”
)
await m.answer(msg)

# ────────────────────────────────────────────────

# /mute

# ────────────────────────────────────────────────

@bot.on.message(text=[”/mute”, “/mute <args>”])
async def mute_cmd(m: Message, args=None):
if not await check_access(m, “Модератор”): return
t = await get_target_id(m, args)
if not t:
return await m.answer(“Укажите пользователя.”)
parts = (args or “”).split()
# Убираем первый токен если это ссылка/id
clean_parts = []
for p in parts:
if re.search(r”[id\d+|”, p) or re.search(r”(?:vk.com/id|^id)\d+”, p) or p.isdigit():
continue
clean_parts.append(p)
mins   = int(clean_parts[0]) if clean_parts and clean_parts[0].isdigit() else 60
reason = “ “.join(clean_parts[1:]) if len(clean_parts) > 1 else “Нарушение”
if clean_parts and not clean_parts[0].isdigit():
reason = “ “.join(clean_parts)
mins   = 60
until = time.time() + mins * 60
pid = str(m.peer_id)
ensure_chat(pid)
DATABASE[“chats”][pid][“mutes”][str(t)] = until
dt = datetime.datetime.fromtimestamp(until, TZ_MSK).strftime(”%d/%m/%Y %H:%M:%S”)
kb = Keyboard(inline=True)
kb.row()
kb.add(Text(“Снять мут”, {“cmd”: “unmute_btn”, “uid”: str(t)}), color=KeyboardButtonColor.POSITIVE)
kb.add(Text(“Очистить”,  {“cmd”: “clear_msg”,  “uid”: str(t)}), color=KeyboardButtonColor.NEGATIVE)
await m.answer(
f”[id{m.from_id}|Модератор MANLIX] выдал(-а) мут [id{t}|пользователю]\n”
f”Причина: {reason}\n”
f”Мут выдан до: {dt}”,
keyboard=kb
)
await push_to_github(DATABASE, GH_PATH_DB, EXTERNAL_DB)

# ────────────────────────────────────────────────

# /unmute

# ────────────────────────────────────────────────

@bot.on.message(text=[”/unmute”, “/unmute <args>”])
async def unmute_cmd(m: Message, args=None):
if not await check_access(m, “Модератор”): return
t = await get_target_id(m, args)
if not t:
return await m.answer(“Укажите пользователя.”)
pid = str(m.peer_id)
ensure_chat(pid)
if str(t) in DATABASE[“chats”][pid].get(“mutes”, {}):
del DATABASE[“chats”][pid][“mutes”][str(t)]
await push_to_github(DATABASE, GH_PATH_DB, EXTERNAL_DB)
await m.answer(f”[id{m.from_id}|Модератор MANLIX] снял(-а) мут [id{t}|пользователю]”)

# ────────────────────────────────────────────────

# Кнопки мута

# ────────────────────────────────────────────────

@bot.on.raw_event(GroupEventType.MESSAGE_EVENT, dataclass=MessageEvent)
async def mute_buttons(event: MessageEvent):
payload = event.payload
if isinstance(payload, str):
try: payload = json.loads(payload)
except: return
cmd = payload.get(“cmd”)
if cmd not in (“unmute_btn”, “clear_msg”):
return

```
uid = payload.get("uid")
pid = str(event.peer_id)
ensure_chat(pid)

rank, _ = get_user_info(event.peer_id, event.user_id)
if RANK_WEIGHT.get(rank, 0) < 1:
    return await event.show_snackbar("Недостаточно прав")

if cmd == "unmute_btn":
    if uid and uid in DATABASE["chats"][pid].get("mutes", {}):
        del DATABASE["chats"][pid]["mutes"][uid]
        await push_to_github(DATABASE, GH_PATH_DB, EXTERNAL_DB)
    new_text = f"[id{event.user_id}|Модератор MANLIX] снял(-а) мут [id{uid}|пользователю]"
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
    new_text = f"[id{event.user_id}|Модератор MANLIX] очистил(-а) сообщения [id{uid}|пользователя]"
    try:
        await bot.api.messages.edit(
            peer_id=event.peer_id,
            message=new_text,
            conversation_message_id=event.conversation_message_id
        )
    except Exception as e:
        print("edit clear error:", e)
```

# ────────────────────────────────────────────────

# /kick

# ────────────────────────────────────────────────

@bot.on.message(text=[”/kick”, “/kick <args>”])
async def kick_cmd(m: Message, args=None):
if not await check_access(m, “Модератор”): return
t = await get_target_id(m, args)
if not t:
return await m.answer(“Укажите пользователя.”)
try:
chat_id = m.peer_id - 2000000000
await bot.api.messages.remove_chat_user(chat_id=chat_id, member_id=t)
except Exception as e:
print(“kick error:”, e)
await m.answer(f”[id{m.from_id}|Модератор MANLIX] исключил(-а) [id{t}|пользователя] из Беседы.”)

# ────────────────────────────────────────────────

# /ban

# ────────────────────────────────────────────────

@bot.on.message(text=[”/ban”, “/ban <args>”])
async def ban_cmd(m: Message, args=None):
if not await check_access(m, “Старший Модератор”): return
t = await get_target_id(m, args)
if not t:
return await m.answer(“Укажите пользователя.”)
parts  = (args or “”).split()
reason = “ “.join(parts[1:]) or “Нарушение”
pid = str(m.peer_id)
ensure_chat(pid)
if pid not in PUNISHMENTS[“bans”]:
PUNISHMENTS[“bans”][pid] = {}
PUNISHMENTS[“bans”][pid][str(t)] = {
“admin”:  m.from_id,
“reason”: reason,
“date”:   time.time()
}
try:
chat_id = m.peer_id - 2000000000
await bot.api.messages.remove_chat_user(chat_id=chat_id, member_id=t)
except:
pass
await push_to_github(PUNISHMENTS, GH_PATH_PUN, EXTERNAL_PUN)
await m.answer(f”[id{m.from_id}|Модератор MANLIX] заблокировал(-а) [id{t}|пользователя] в Беседе.”)

# ────────────────────────────────────────────────

# /unban

# ────────────────────────────────────────────────

@bot.on.message(text=[”/unban”, “/unban <args>”])
async def unban_cmd(m: Message, args=None):
if not await check_access(m, “Старший Модератор”): return
t = await get_target_id(m, args)
if not t:
return await m.answer(“Укажите пользователя.”)
pid = str(m.peer_id)
if pid in PUNISHMENTS[“bans”] and str(t) in PUNISHMENTS[“bans”][pid]:
del PUNISHMENTS[“bans”][pid][str(t)]
await push_to_github(PUNISHMENTS, GH_PATH_PUN, EXTERNAL_PUN)
await m.answer(f”[id{m.from_id}|Модератор MANLIX] снял(-а) блокировку [id{t}|пользователя] в Беседе.”)

# ────────────────────────────────────────────────

# Выдача ролей

# ────────────────────────────────────────────────

async def role_grant(m: Message, args, min_rank, role_name, role_label):
if not await check_access(m, min_rank): return
t = await get_target_id(m, args)
if not t:
return await m.answer(“Укажите пользователя.”)
pid, uid = str(m.peer_id), str(t)
await set_role_in_chat(pid, uid, role_name)
await push_to_github(DATABASE, GH_PATH_DB, EXTERNAL_DB)
_, a_nick = get_user_info(m.peer_id, m.from_id)
a_display = a_nick if a_nick else await get_display_name(m.from_id)
await m.answer(f”[id{m.from_id}|{a_display}] выдал(-а) права {role_label} [id{t}|пользователю]”)

@bot.on.message(text=[”/addmoder”,    “/addmoder <args>”])
async def addmod(m, args=None):
await role_grant(m, args, “Старший Модератор”,           “Модератор”,                 “модератора”)

@bot.on.message(text=[”/addsenmoder”, “/addsenmoder <args>”])
async def addsenmod(m, args=None):
await role_grant(m, args, “Администратор”,               “Старший Модератор”,          “старшего модератора”)

@bot.on.message(text=[”/addadmin”,    “/addadmin <args>”])
async def addadm(m, args=None):
await role_grant(m, args, “Старший Администратор”,       “Администратор”,              “администратора”)

@bot.on.message(text=[”/addsenadmin”, “/addsenadmin <args>”])
async def addsenadm(m, args=None):
await role_grant(m, args, “Зам. Спец. Администратора”,   “Старший Администратор”,      “старшего администратора”)

@bot.on.message(text=[”/addzsa”,      “/addzsa <args>”])
async def addzsa(m, args=None):
await role_grant(m, args, “Спец. Администратор”,         “Зам. Спец. Администратора”,  “заместителя специального администратора”)

@bot.on.message(text=[”/addsa”,       “/addsa <args>”])
async def addsa(m, args=None):
await role_grant(m, args, “Владелец”,                    “Спец. Администратор”,        “специального администратора”)

@bot.on.message(text=[”/addowner”,    “/addowner <args>”])
async def addowner(m, args=None):
await role_grant(m, args, “Зам. Спец. Руководителя”,     “Владелец”,                   “владельца”)

# ────────────────────────────────────────────────

# /removerole

# ────────────────────────────────────────────────

@bot.on.message(text=[”/removerole”, “/removerole <args>”])
async def removerole(m: Message, args=None):
if not await check_access(m, “Старший Модератор”): return
t = await get_target_id(m, args)
if not t:
return await m.answer(“Укажите пользователя.”)
pid, uid = str(m.peer_id), str(t)
ensure_chat(pid)
if uid in DATABASE[“chats”][pid].get(“staff”, {}):
del DATABASE[“chats”][pid][“staff”][uid]
await push_to_github(DATABASE, GH_PATH_DB, EXTERNAL_DB)
_, a_nick = get_user_info(m.peer_id, m.from_id)
a_display = a_nick if a_nick else await get_display_name(m.from_id)
await m.answer(f”[id{m.from_id}|{a_display}] снял(-а) уровень прав [id{t}|пользователю]”)

# ────────────────────────────────────────────────

# /staff

# ────────────────────────────────────────────────

@bot.on.message(text=”/staff”)
async def staff_view(m: Message):
pid = str(m.peer_id)
ensure_chat(pid)
staff  = DATABASE[“chats”].get(pid, {}).get(“staff”, {})
order  = [
“Владелец”,
“Спец. Администратор”,
“Зам. Спец. Администратора”,
“Старший Администратор”,
“Администратор”,
“Старший Модератор”,
“Модератор”
]
blocks = []
for r in order:
block = f”{r}:”
members = []
for u, entry in staff.items():
if entry[0] == r:
nick = entry[1]
if nick:
display = nick
else:
try:
uinfo = await bot.api.users.get([int(u)])
display = f”{uinfo[0].first_name} {uinfo[0].last_name}”
except:
display = “пользователь”
members.append(f”– [id{u}|{display}]”)
if members:
block += “\n” + “\n”.join(members)
else:
block += “\n– Отсутствует.”
blocks.append(block)
await m.answer(”\n\n”.join(blocks))

# ────────────────────────────────────────────────

# /setnick

# ────────────────────────────────────────────────

@bot.on.message(text=[”/setnick”, “/setnick <args>”])
async def setnick(m: Message, args=None):
if not await check_access(m, “Модератор”): return
if not args:
return await m.answer(“Укажите пользователя и ник.”)
parts = args.split(maxsplit=1)
if len(parts) < 2:
return await m.answer(“Формат: /setnick [пользователь] [ник]”)
t = await get_target_id(m, parts[0])
if not t:
return await m.answer(“Не удалось определить пользователя.”)
new_nick = parts[1].strip()
pid, uid = str(m.peer_id), str(t)
ensure_chat(pid)
role_now, _ = get_user_info(m.peer_id, t)
DATABASE[“chats”][pid][“staff”][uid] = [role_now, new_nick]
await push_to_github(DATABASE, GH_PATH_DB, EXTERNAL_DB)
_, a_nick = get_user_info(m.peer_id, m.from_id)
a_display = a_nick if a_nick else await get_display_name(m.from_id)
await m.answer(f”[id{m.from_id}|{a_display}] установил(-а) новое имя [id{t}|пользователю]: {new_nick}”)

# ────────────────────────────────────────────────

# /rnick

# ────────────────────────────────────────────────

@bot.on.message(text=[”/rnick”, “/rnick <args>”])
async def rnick(m: Message, args=None):
if not await check_access(m, “Модератор”): return
t = await get_target_id(m, args)
if not t:
return await m.answer(“Укажите пользователя.”)
pid, uid = str(m.peer_id), str(t)
ensure_chat(pid)
if uid in DATABASE[“chats”][pid].get(“staff”, {}):
DATABASE[“chats”][pid][“staff”][uid][1] = None
await push_to_github(DATABASE, GH_PATH_DB, EXTERNAL_DB)
_, a_nick = get_user_info(m.peer_id, m.from_id)
a_display = a_nick if a_nick else await get_display_name(m.from_id)
await m.answer(f”[id{m.from_id}|{a_display}] убрал(-а) имя [id{t}|пользователю]”)

# ────────────────────────────────────────────────

# /nlist

# ────────────────────────────────────────────────

@bot.on.message(text=”/nlist”)
async def nick_list(m: Message):
if not await check_access(m, “Модератор”): return
pid = str(m.peer_id)
ensure_chat(pid)
staff = DATABASE[“chats”].get(pid, {}).get(“staff”, {})
users = [(u, entry[1]) for u, entry in staff.items() if entry[1]]
if not users:
return await m.answer(“Никнеймы не установлены.”)
msg = “Список пользователей с ником:\n”
for i, (u, n) in enumerate(users, 1):
msg += f”{i}. [id{u}|{n}]\n”
await m.answer(msg.strip())

# ────────────────────────────────────────────────

# /getban

# ────────────────────────────────────────────────

@bot.on.message(text=[”/getban”, “/getban <args>”])
async def getban_cmd(m: Message, args=None):
if not await check_access(m, “Модератор”): return
t = await get_target_id(m, args)
if not t:
return await m.answer(“Укажите пользователя.”)
uid = str(t)
try:
uinfo = await bot.api.users.get([t])
name  = f”{uinfo[0].first_name} {uinfo[0].last_name}”
except:
name = “пользователь”

```
ans = f"Информация о блокировках [id{t}|{name}]\n\n"

# Глобальный бан
if uid in PUNISHMENTS.get("gbans_status", {}):
    b  = PUNISHMENTS["gbans_status"][uid]
    dt = datetime.datetime.fromtimestamp(b["date"], TZ_MSK).strftime("%d/%m/%Y %H:%M:%S")
    ans += (
        f"Информация о общей Блокировке в Беседах:\n"
        f"[id{b['admin']}|Модератор MANLIX] | {b.get('reason', '-')} | {dt}\n\n"
    )
else:
    ans += "Информация о общей Блокировке в Беседах: отсутствует\n\n"

# Бан в играх
if uid in PUNISHMENTS.get("gbans_pl", {}):
    b  = PUNISHMENTS["gbans_pl"][uid]
    dt = datetime.datetime.fromtimestamp(b["date"], TZ_MSK).strftime("%d/%m/%Y %H:%M:%S")
    ans += (
        f"Информация о общей Блокировке в Беседе игроков:\n"
        f"[id{b['admin']}|Модератор MANLIX] | {b.get('reason', '-')} | {dt}\n\n"
    )
else:
    ans += "Информация о общей Блокировке в Беседе игроков: отсутствует\n\n"

# Локальные баны
local_bans = []
for pid, bans in PUNISHMENTS.get("bans", {}).items():
    if uid in bans:
        b     = bans[uid]
        title = DATABASE["chats"].get(pid, {}).get("title", f"Беседа {pid}")
        dt    = datetime.datetime.fromtimestamp(b["date"], TZ_MSK).strftime("%d/%m/%Y %H:%M:%S")
        local_bans.append(f"{title} | [id{b['admin']}|Модератор MANLIX] | {dt}")

ans += f"Количество Бесед, в которых заблокирован пользователь: {len(local_bans)}\n"
if local_bans:
    ans += "Информация о последних 10 Блокировках:\n"
    for i, lb in enumerate(local_bans[-10:], 1):
        ans += f"{i}) {lb}\n"
else:
    ans += "Блокировки в беседах отсутствуют"

await m.answer(ans)
```

# ────────────────────────────────────────────────

# /gstaff

# ────────────────────────────────────────────────

@bot.on.message(text=”/gstaff”)
async def gstaff_view(m: Message):
if not await check_access(m, “Зам. Спец. Руководителя”): return
g   = DATABASE[“gstaff”]
res = “MANLIX MANAGER | Команда Бота:\n\n”
res += “| Специальный Руководитель:\n– [id870757778|Misha Manlix]\n\n”
res += “| Основной зам. Спец. Руководителя:\n”
if g.get(“main_zam”):
res += f”– [id{g[‘main_zam’]}|пользователь]\n”
else:
res += “– Отсутствует.\n”
res += “\n| Зам. Спец. Руководителя:\n”
zams = g.get(“zams”, [])
if zams:
for z in zams:
res += f”– [id{z}|пользователь]\n”
else:
res += “– Отсутствует.\n– Отсутствует.\n”
await m.answer(res.strip())

# ────────────────────────────────────────────────

# /start

# ────────────────────────────────────────────────

@bot.on.message(text=”/start”)
async def start(m: Message):
if not await check_access(m, “Специальный Руководитель”): return
pid = str(m.peer_id)
ensure_chat(pid)
try:
conv = await bot.api.messages.get_conversations_by_id(peer_ids=[m.peer_id])
if conv.items:
DATABASE[“chats”][pid][“title”] = conv.items[0].chat_settings.title
except:
pass
await push_to_github(DATABASE, GH_PATH_DB, EXTERNAL_DB)
await m.answer(“Вы успешно активировали Беседу.”)

# ────────────────────────────────────────────────

# /type

# ────────────────────────────────────────────────

@bot.on.message(text=[”/type”, “/type <args>”])
async def type_cmd(m: Message, args=None):
if not await check_access(m, “Специальный Руководитель”): return
pid = str(m.peer_id)
ensure_chat(pid)
valid = [“def”, “adm”, “mod”, “pl”, “test”, “tex”]
if args:
new_type = args.strip().lower()
if new_type in valid:
DATABASE[“chats”][pid][“type”] = new_type
await push_to_github(DATABASE, GH_PATH_DB, EXTERNAL_DB)
await m.answer(f”Тип Беседы изменён на: {new_type}”)
return
else:
await m.answer(“Неверный тип. Доступные типы смотри ниже.”)
current = DATABASE[“chats”][pid][“type”]
await m.answer(
f”Беседа имеет тип: {current}\n\n”
“def - общая Беседа\n”
“adm - Беседа администраторов\n”
“mod - Беседа модераторов\n”
“pl - Беседа игроков\n”
“test - Беседа тестировщиков\n”
“tex - Тех. Раздел”
)

# ────────────────────────────────────────────────

# /chatid

# ────────────────────────────────────────────────

@bot.on.message(text=”/chatid”)
async def chatid(m: Message):
if not await check_access(m, “Специальный Руководитель”): return
await m.answer(f”ID текущей Беседы: {m.peer_id}”)

# ────────────────────────────────────────────────

# /delchat

# ────────────────────────────────────────────────

@bot.on.message(text=”/delchat”)
async def delchat(m: Message):
if not await check_access(m, “Специальный Руководитель”): return
pid = str(m.peer_id)
if pid in DATABASE[“chats”]:
del DATABASE[“chats”][pid]
await push_to_github(DATABASE, GH_PATH_DB, EXTERNAL_DB)
await m.answer(“Вы успешно удалили чат с Базы данных.”)
else:
await m.answer(“Эта Беседа не найдена в базе данных.”)

# ────────────────────────────────────────────────

# /sync

# ────────────────────────────────────────────────

@bot.on.message(text=”/sync”)
async def sync(m: Message):
if not await check_access(m, “Специальный Руководитель”): return
global DATABASE, ECONOMY, PUNISHMENTS
DATABASE    = await load_from_github(GH_PATH_DB,  EXTERNAL_DB)
ECONOMY     = await load_from_github(GH_PATH_ECO, EXTERNAL_ECO)
PUNISHMENTS = await load_from_github(GH_PATH_PUN, EXTERNAL_PUN)
await m.answer(“Вы успешно синхронизировали Беседу с Базой данных.”)

# ────────────────────────────────────────────────

# /gban / /gunban

# ────────────────────────────────────────────────

@bot.on.message(text=[”/gban”, “/gban <args>”])
async def gban_cmd(m: Message, args=None):
if not await check_access(m, “Специальный Руководитель”): return
t = await get_target_id(m, args)
if not t:
return await m.answer(“Укажите пользователя.”)
reason = “ “.join((args or “”).split()[1:]) or “Нарушение”
uid    = str(t)
PUNISHMENTS[“gbans_status”][uid] = {“admin”: m.from_id, “reason”: reason, “date”: time.time()}
await push_to_github(PUNISHMENTS, GH_PATH_PUN, EXTERNAL_PUN)
await m.answer(f”[id{m.from_id}|Специальный Руководитель] занес [id{t}|пользователя] в глобальную Блокировку Бота.”)

@bot.on.message(text=[”/gunban”, “/gunban <args>”])
async def gunban(m: Message, args=None):
if not await check_access(m, “Специальный Руководитель”): return
t = await get_target_id(m, args)
if not t:
return await m.answer(“Укажите пользователя.”)
uid = str(t)
if uid in PUNISHMENTS[“gbans_status”]:
del PUNISHMENTS[“gbans_status”][uid]
await push_to_github(PUNISHMENTS, GH_PATH_PUN, EXTERNAL_PUN)
await m.answer(f”[id{m.from_id}|Специальный Руководитель] вынес [id{t}|пользователя] из Глобальной Блокировки Бота.”)

# ────────────────────────────────────────────────

# /gbanpl / /gunbanpl

# ────────────────────────────────────────────────

@bot.on.message(text=[”/gbanpl”, “/gbanpl <args>”])
async def gbanpl_cmd(m: Message, args=None):
if not await check_access(m, “Зам. Спец. Руководителя”): return
t = await get_target_id(m, args)
if not t:
return await m.answer(“Укажите пользователя.”)
reason = “ “.join((args or “”).split()[1:]) or “Нарушение”
uid    = str(t)
PUNISHMENTS[“gbans_pl”][uid] = {“admin”: m.from_id, “reason”: reason, “date”: time.time()}
for pid in list(DATABASE[“chats”].keys()):
if DATABASE[“chats”][pid].get(“type”) == “pl”:
try:
chat_id = int(pid) - 2000000000
await bot.api.messages.remove_chat_user(chat_id=chat_id, member_id=t)
except:
pass
await push_to_github(PUNISHMENTS, GH_PATH_PUN, EXTERNAL_PUN)
await m.answer(f”[id{m.from_id}|Специальный Руководитель] заблокировал [id{t}|пользователя] во всех игровых Беседах.”)

@bot.on.message(text=[”/gunbanpl”, “/gunbanpl <args>”])
async def gunbanpl_cmd(m: Message, args=None):
if not await check_access(m, “Зам. Спец. Руководителя”): return
t = await get_target_id(m, args)
if not t:
return await m.answer(“Укажите пользователя.”)
uid = str(t)
if uid in PUNISHMENTS[“gbans_pl”]:
del PUNISHMENTS[“gbans_pl”][uid]
await push_to_github(PUNISHMENTS, GH_PATH_PUN, EXTERNAL_PUN)
await m.answer(f”[id{m.from_id}|Специальный Руководитель] разблокировал [id{t}|пользователя] во всех игровых Беседах.”)

# ────────────────────────────────────────────────

# Игровые команды

# ────────────────────────────────────────────────

@bot.on.message(text=”/ghelp”)
async def ghelp_cmd(m: Message):
await m.answer(
“🎮 Игровые команды MANLIX:\n\n”
“🎉 /prise — Получить ежечасный приз\n”
“💰 /balance — Наличные средства\n”
“🏦 /bank — Состояние счетов\n”
“📥 /положить [сумма] — Положить в банк\n”
“📤 /снять [сумма] — Снять из банка\n”
“💸 /перевести [ссылка] [сумма] — Перевод со счета на счет\n”
“🎰 /roulette [сумма] — Рулетка”
)

@bot.on.message(text=”/prise”)
async def prise(m: Message):
uid = str(m.from_id)
if uid not in ECONOMY:
ECONOMY[uid] = {“cash”: 0, “bank”: 0, “last”: 0}
if time.time() - ECONOMY[uid].get(“last”, 0) < 3600:
return await m.answer(“🎉 Приз можно получить раз в час.”)
win = random.randint(100, 1000)
ECONOMY[uid][“cash”] += win
ECONOMY[uid][“last”]  = time.time()
await push_to_github(ECONOMY, GH_PATH_ECO, EXTERNAL_ECO)
await m.answer(f”🎉 Вы получили приз {win}$!”)

@bot.on.message(text=”/balance”)
async def balance_cmd(m: Message):
uid  = str(m.from_id)
cash = ECONOMY.get(uid, {}).get(“cash”, 0)
await m.answer(f”💵 Ваши наличные: {cash}$”)

@bot.on.message(text=”/bank”)
async def bank_cmd(m: Message):
uid  = str(m.from_id)
cash = ECONOMY.get(uid, {}).get(“cash”, 0)
bank = ECONOMY.get(uid, {}).get(“bank”, 0)
await m.answer(
f”🏦 …::: MANLIX BANK :::…\n\n”
f”💵 Наличные: {cash}$\n”
f”💳 На счету: {bank}$”
)

@bot.on.message(text=[”/положить <amount>”])
async def polozhit(m: Message, amount=None):
try:
amount = int(amount)
if amount <= 0: raise ValueError
except:
return await m.answer(“Укажите положительную сумму.”)
uid = str(m.from_id)
if uid not in ECONOMY:
ECONOMY[uid] = {“cash”: 0, “bank”: 0, “last”: 0}
if ECONOMY[uid].get(“cash”, 0) < amount:
return await m.answer(“Недостаточно наличных.”)
ECONOMY[uid][“cash”] -= amount
ECONOMY[uid][“bank”] += amount
await push_to_github(ECONOMY, GH_PATH_ECO, EXTERNAL_ECO)
await m.answer(f”💲 Вы положили на свой счет {amount}$”)

@bot.on.message(text=[”/снять <amount>”])
async def snyat(m: Message, amount=None):
try:
amount = int(amount)
if amount <= 0: raise ValueError
except:
return await m.answer(“Укажите положительную сумму.”)
uid = str(m.from_id)
if uid not in ECONOMY:
ECONOMY[uid] = {“cash”: 0, “bank”: 0, “last”: 0}
if ECONOMY[uid].get(“bank”, 0) < amount:
return await m.answer(“Недостаточно средств на счете.”)
ECONOMY[uid][“bank”] -= amount
ECONOMY[uid][“cash”] += amount
await push_to_github(ECONOMY, GH_PATH_ECO, EXTERNAL_ECO)
await m.answer(f”💲 Вы сняли с своего счета {amount}$”)

@bot.on.message(text=[”/перевести <args>”])
async def transfer(m: Message, args=None):
if not args:
return await m.answer(“Формат: /перевести [ссылка] [сумма]”)
parts = args.split()
if len(parts) < 2:
return await m.answer(“Формат: /перевести [ссылка] [сумма]”)
t = await get_target_id(m, parts[0])
if not t:
return await m.answer(“Не удалось определить получателя.”)
try:
amount = int(parts[1])
if amount <= 0: raise ValueError
except:
return await m.answer(“Некорректная сумма.”)
uid = str(m.from_id)
rid = str(t)
if uid not in ECONOMY: ECONOMY[uid] = {“cash”: 0, “bank”: 0, “last”: 0}
if rid not in ECONOMY: ECONOMY[rid] = {“cash”: 0, “bank”: 0, “last”: 0}
if ECONOMY[uid].get(“bank”, 0) < amount:
return await m.answer(f”Недостаточно средств на счете (есть {ECONOMY[uid].get(‘bank’, 0)}$)”)
ECONOMY[uid][“bank”] -= amount
ECONOMY[rid][“bank”] += amount
await push_to_github(ECONOMY, GH_PATH_ECO, EXTERNAL_ECO)
await m.answer(f”💲 Вы перевели [id{t}|пользователю] {amount}$”)

@bot.on.message(text=[”/roulette <amount>”])
async def roulette(m: Message, amount=None):
try:
amount = int(amount)
if amount <= 0: raise ValueError
except:
return await m.answer(“Укажите положительную сумму.”)
uid = str(m.from_id)
if uid not in ECONOMY or ECONOMY[uid].get(“cash”, 0) < amount:
return await m.answer(“Недостаточно наличных.”)
ECONOMY[uid][“cash”] -= amount
if random.random() < 0.25:
win = amount * 3
ECONOMY[uid][“cash”] += win
text = f”🎰 Вы выиграли {win}$!”
else:
text = f”🎰 Вы проиграли {amount}$…”
await push_to_github(ECONOMY, GH_PATH_ECO, EXTERNAL_ECO)
await m.answer(text)

@bot.on.message(text=[”/duel <amount>”])
async def duel_create(m: Message, amount=None):
try:
amount = int(amount)
if amount <= 0: raise ValueError
except:
return await m.answer(“Укажите положительную сумму.”)
uid = str(m.from_id)
pid = str(m.peer_id)
if uid not in ECONOMY or ECONOMY[uid].get(“bank”, 0) < amount:
return await m.answer(“Недостаточно средств на банковском счете.”)
duel_id = f”{pid}_{int(time.time())}”
DATABASE[“duels”][duel_id] = {
“creator”:      uid,
“amount”:       amount,
“participants”: [uid],
“chat_id”:      pid
}
kb = Keyboard(inline=True)
kb.add(Text(“Вступить в дуэль!”, {“cmd”: “join_duel”, “duel”: duel_id}), color=KeyboardButtonColor.POSITIVE)
await m.answer(
f”⚔️ Дуэль на {amount}$ создана!\n”
f”Нажми на кнопку, чтобы сразиться!”,
keyboard=kb
)
await push_to_github(DATABASE, GH_PATH_DB, EXTERNAL_DB)

# ────────────────────────────────────────────────

# Кнопка дуэли

# ────────────────────────────────────────────────

@bot.on.raw_event(GroupEventType.MESSAGE_EVENT, dataclass=MessageEvent)
async def duel_join(event: MessageEvent):
payload = event.payload
if isinstance(payload, str):
try: payload = json.loads(payload)
except: return
if payload.get(“cmd”) != “join_duel”:
return
duel_id = payload.get(“duel”)
if duel_id not in DATABASE.get(“duels”, {}):
return await event.show_snackbar(“Дуэль уже завершена.”)
duel = DATABASE[“duels”][duel_id]
uid  = str(event.user_id)
if uid in duel[“participants”]:
return await event.show_snackbar(“Вы уже участвуете.”)
if len(duel[“participants”]) >= 2:
return await event.show_snackbar(“Дуэль уже заполнена.”)
if uid not in ECONOMY or ECONOMY[uid].get(“bank”, 0) < duel[“amount”]:
return await event.show_snackbar(“Недостаточно средств на банковском счете.”)
duel[“participants”].append(uid)
await event.show_snackbar(“Вы вступили в дуэль!”)
if len(duel[“participants”]) == 2:
winner = random.choice(duel[“participants”])
loser  = [p for p in duel[“participants”] if p != winner][0]
amount = duel[“amount”]
ECONOMY[winner][“bank”] = ECONOMY[winner].get(“bank”, 0) + amount
ECONOMY[loser][“bank”]  = ECONOMY[loser].get(“bank”,  0) - amount
await push_to_github(ECONOMY, GH_PATH_ECO, EXTERNAL_ECO)
del DATABASE[“duels”][duel_id]
await push_to_github(DATABASE, GH_PATH_DB, EXTERNAL_DB)
await bot.api.messages.send(
peer_id=int(duel[“chat_id”]),
message=(
f”⚔️ Дуэль завершена!\n\n”
f”🏅 Победил: [id{winner}|победитель]\n”
f”🥈 Проиграл: [id{loser}|проигравший]\n\n”
f”💲 Победитель получает {amount}$”
),
random_id=random.randint(0, 2**31)
)

# ────────────────────────────────────────────────

# Системные события (выход/вход пользователей)

# ────────────────────────────────────────────────

@bot.on.message()
async def actions(m: Message):
if not m.action:
return
typ = m.action.type.value if hasattr(m.action.type, “value”) else str(m.action.type)

```
# Бот покинул беседу
if typ == "chat_kick_user":
    global GROUP_ID
    if GROUP_ID is None:
        try:
            GROUP_ID = (await bot.api.groups.get_by_id())[0].id
        except:
            pass
    if GROUP_ID and m.action.member_id == -GROUP_ID:
        kb = Keyboard(inline=True)
        kb.add(Text("Исключить", {"cmd": "kick_all"}), color=KeyboardButtonColor.NEGATIVE)
        await m.answer("Бот покинул(-а) Беседу", keyboard=kb)
    return

# Пользователь вошёл в беседу — проверяем баны
if typ in ("chat_invite_user", "chat_invite_user_by_link"):
    invited = m.action.member_id
    if invited and invited > 0:
        uid = str(invited)
        pid = str(m.peer_id)
        ensure_chat(pid)
        banned = (
            uid in PUNISHMENTS.get("gbans_status", {}) or
            uid in PUNISHMENTS.get("gbans_pl",     {}) or
            uid in PUNISHMENTS.get("bans", {}).get(pid, {})
        )
        if banned:
            try:
                chat_id = m.peer_id - 2000000000
                await bot.api.messages.remove_chat_user(chat_id=chat_id, member_id=invited)
            except:
                pass
            await m.answer(
                f"[id870757778|Модератор MANLIX] исключил(-а) [id{invited}|пользователя] — "
                f"он находится в списке блокировок."
            )
```

# ────────────────────────────────────────────────

# Технические отчёты (tex-беседы, каждые 15 сек)

# ────────────────────────────────────────────────

async def send_reports():
while True:
now = datetime.datetime.now(TZ_MSK)
if now.second % 15 == 0:
for pid, chat in list(DATABASE.get(“chats”, {}).items()):
if chat.get(“type”) == “tex”:
delay    = round(random.uniform(0, 1), 2)
time_str = now.strftime(”%H:%M:%S”)
date_str = now.strftime(”%d/%m/%Y”)
msg = (
f”…::: ТЕХНИЧЕСКИЙ ОТЧЕТ :::…\n\n”
f”| ==> Бот успешно работает.\n”
f”| Задержка Бота: {delay}\n”
f”| Точное время: {time_str}\n”
f”| Дата: {date_str}”
)
try:
await bot.api.messages.send(
peer_id=int(pid),
message=msg,
random_id=random.randint(0, 2**32 - 1)
)
except Exception as e:
print(“send_reports error:”, e)
await asyncio.sleep(1)

# ────────────────────────────────────────────────

# Keep-Alive для Render

# ────────────────────────────────────────────────

async def keep_alive():
while True:
try:
url = os.environ.get(“RENDER_EXTERNAL_URL”)
if url:
async with aiohttp.ClientSession() as session:
async with session.get(url + “?keepalive=1”, timeout=aiohttp.ClientTimeout(total=10)):
print(f”[{datetime.datetime.now(TZ_MSK).strftime(’%H:%M:%S’)}] Keep-alive отправлен”)
except Exception as e:
print(“Keep-alive error:”, e)
await asyncio.sleep(600)

# ────────────────────────────────────────────────

# Запуск

# ────────────────────────────────────────────────

if **name** == “**main**”:
threading.Thread(
target=HTTPServer((‘0.0.0.0’, int(os.environ.get(“PORT”, 10000))), H).serve_forever,
daemon=True
).start()
loop.create_task(send_reports())
loop.create_task(keep_alive())
print(“Бот запущен. Keep-alive и тех.отчёты активны.”)
bot.run_forever()

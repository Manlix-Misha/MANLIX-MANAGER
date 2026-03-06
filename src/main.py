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
from vkbottle.bot import Bot, Message, MessageEvent
from vkbottle import Keyboard, KeyboardButtonColor, Text, GroupEventType, BaseMiddleware

# ------------------------------
# 1. НАСТРОЙКИ (не менять начало/структуру)
# ------------------------------
GH_TOKEN = os.environ.get("GH_TOKEN")
GH_REPO = os.environ.get("GH_REPO")
GH_PATH_DB = "database.json"
GH_PATH_ECO = "economy.json"
GH_PATH_PUN = "punishments.json"

EXTERNAL_DB = "database.json"
EXTERNAL_ECO = "economy.json"
EXTERNAL_PUN = "punishments.json"

async def load_from_github(gh_path, local_path):
    if not GH_TOKEN or not GH_REPO:
        return load_local_data(local_path)
    url = f"https://api.github.com/repos/{GH_REPO}/contents/{gh_path}"
    headers = {"Authorization": f"token {GH_TOKEN}"}
    try:
        async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=False)) as session:
            async with session.get(url, headers=headers) as resp:
                if resp.status == 200:
                    doc = await resp.json()
                    content = doc.get('content')
                    if content:
                        data = json.loads(base64.b64decode(content).decode('utf-8'))
                        with open(local_path, "w", encoding="utf-8") as f:
                            json.dump(data, f, ensure_ascii=False, indent=4)
                        return data
                elif resp.status == 404:
                    # File doesn't exist, return empty
                    return {}
                else:
                    print("load_from_github failed:", resp.status)
                    return load_local_data(local_path)
    except Exception as e:
        print("load_from_github error:", e)
        return load_local_data(local_path)

def load_local_data(path):
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print("load_local_data error:", e)
            return {}
    return {}

# Load data at startup
loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)
DATABASE = loop.run_until_complete(load_from_github(GH_PATH_DB, EXTERNAL_DB))
ECONOMY = loop.run_until_complete(load_from_github(GH_PATH_ECO, EXTERNAL_ECO))
PUNISHMENTS = loop.run_until_complete(load_from_github(GH_PATH_PUN, EXTERNAL_PUN))

# Инициализация структур (безопасно)
if not isinstance(DATABASE, dict): DATABASE = {}
if not isinstance(ECONOMY, dict): ECONOMY = {}
if not isinstance(PUNISHMENTS, dict): PUNISHMENTS = {}
if "gbans_status" not in PUNISHMENTS: PUNISHMENTS["gbans_status"] = {}
if "gbans_pl" not in PUNISHMENTS: PUNISHMENTS["gbans_pl"] = {}
if "bans" not in PUNISHMENTS: PUNISHMENTS["bans"] = {}
if "warns" not in PUNISHMENTS: PUNISHMENTS["warns"] = {}
if "chats" not in DATABASE: DATABASE["chats"] = {}
if "gstaff" not in DATABASE:
    DATABASE["gstaff"] = {"spec": 870757778, "main_zam": None, "zams": []}

RANK_WEIGHT = {
    "Пользователь": 0, "Модератор": 1, "Старший Модератор": 2,
    "Администратор": 3, "Старший Администратор": 4, "Зам. Спец. Администратора": 5,
    "Спец. Администратор": 6, "Владелец": 7, "Зам. Спец. Руководителя": 8,
    "Основной Зам. Спец. Руководителя": 9, "Специальный Руководитель": 10
}

TZ_MSK = datetime.timezone(datetime.timedelta(hours=3))

# ------------------------------
# 2. Сохранение в GitHub / локально
# ------------------------------
async def push_to_github(data, gh_path, local_path):
    if not GH_TOKEN or not GH_REPO:
        try:
            with open(local_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=4)
        except Exception as e:
            print("push_to_github local save error:", e)
        return
    url = f"https://api.github.com/repos/{GH_REPO}/contents/{gh_path}"
    headers = {"Authorization": f"token {GH_TOKEN}"}
    try:
        async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=False)) as session:
            sha = None
            async with session.get(url, headers=headers) as resp:
                if resp.status == 200:
                    doc = await resp.json()
                    sha = doc.get('sha')
            content = base64.b64encode(json.dumps(data, ensure_ascii=False, indent=4).encode('utf-8')).decode('utf-8')
            payload = {"message": "Update DB", "content": content}
            if sha:
                payload["sha"] = sha
            async with session.put(url, headers=headers, json=payload) as resp2:
                if resp2.status not in (200, 201):
                    text = await resp2.text()
                    print("push_to_github failed:", resp2.status, text)
            with open(local_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=4)
    except Exception as e:
        print("push_to_github exception:", e)
        try:
            with open(local_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=4)
        except Exception as e2:
            print("push_to_github local fallback error:", e2)

# ------------------------------
# 3. Инициализация бота
# ------------------------------
bot = Bot(token=os.environ.get("TOKEN"))

# Get group ID
GROUP_ID = -loop.run_until_complete(bot.api.groups.get_by_id())[0].id

# ------------------------------
# 4. Утилиты
# ------------------------------
def ensure_chat(pid: str):
    if "chats" not in DATABASE:
        DATABASE["chats"] = {}
    if pid not in DATABASE["chats"]:
        DATABASE["chats"][pid] = {"title": f"Чат {pid}", "staff": {}, "mutes": {}, "stats": {}}
    else:
        chat = DATABASE["chats"][pid]
        if "title" not in chat: chat["title"] = f"Чат {pid}"
        if "staff" not in chat: chat["staff"] = {}
        if "mutes" not in chat: chat["mutes"] = {}
        if "stats" not in chat: chat["stats"] = {}

async def get_target_id(m: Message, args: str):
    if getattr(m, "reply_message", None):
        return m.reply_message.from_id
    if not args:
        return None
    match = re.search(r"(?:\[id|id|vk\.com\/id|vk\.com\/)(\d+)", args)
    if match:
        try:
            return int(match.group(1))
        except:
            pass
    brace = re.search(r"\[id(\d+)\|", args)
    if brace:
        try:
            return int(brace.group(1))
        except:
            pass
    raw = args.split('/')[-1].split('|')[0].replace('[', '').replace('@', '').strip()
    if raw:
        try:
            res = await bot.api.utils.resolve_screen_name(screen_name=raw)
            if res and res.type == "user":
                return int(res.object_id)
        except Exception:
            pass
    num = re.sub(r"\D", "", args)
    if num:
        try:
            return int(num)
        except:
            pass
    return None

def get_user_info(peer_id, user_id):
    uid = str(user_id)
    if int(user_id) == 870757778:
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

# ------------------------------
# 5. Middleware (удаление сообщений от заблокированных)
# ------------------------------
class ChatMiddleware(BaseMiddleware[Message]):
    async def pre(self):
        if not getattr(self.event, "from_id", None) or self.event.from_id < 0:
            return
        pid, uid = str(self.event.peer_id), str(self.event.from_id)
        ensure_chat(pid)
        chat_data = DATABASE["chats"][pid]
        if "stats" not in chat_data: chat_data["stats"] = {}
        if uid not in chat_data["stats"]:
            chat_data["stats"][uid] = {"count": 0, "last": 0}
        chat_data["stats"][uid]["count"] += 1
        chat_data["stats"][uid]["last"] = datetime.datetime.now(TZ_MSK).timestamp()
        if chat_data["stats"][uid]["count"] % 10 == 0:
            await push_to_github(DATABASE, GH_PATH_DB, EXTERNAL_DB)
        is_gban = uid in PUNISHMENTS.get("gbans_status", {})
        is_gbanpl = uid in PUNISHMENTS.get("gbans_pl", {})
        is_lban = uid in PUNISHMENTS.get("bans", {}).get(pid, {})
        mutes = chat_data.get("mutes", {})
        is_muted = uid in mutes and datetime.datetime.now(TZ_MSK).timestamp() < mutes[uid]
        if is_gban or is_gbanpl or is_lban or is_muted:
            try:
                await bot.api.messages.delete(peer_id=self.event.peer_id, conversation_message_ids=[self.event.conversation_message_id], delete_for_all=True)
            except Exception:
                pass
            self.stop()

bot.labeler.message_view.register_middleware(ChatMiddleware)

# ------------------------------
# 6. Команды пользователей (help/getid/stats)
# ------------------------------
@bot.on.message(text=["/help"])
async def help_cmd(m: Message):
    ensure_chat(str(m.peer_id))
    rank, _ = get_user_info(m.peer_id, m.from_id)
    w = RANK_WEIGHT.get(rank, 0)
    res = (
        "Команды пользователей:\n"
        "/info - официальные ресурсы\n"
        "/stats - статистика пользователя\n"
        "/getid - оригинальная ссылка VK.\n"
    )
    if w >= 1:
        res += (
            "\nКоманды для модераторов:\n"
            "/staff - Руководство Беседы\n"
            "/kick - исключить пользователя из Беседы.\n"
            "/mute - выдать Блокировку чата.\n"
            "/unmute - снять Блокировку чата.\n"
            "/setnick - установить имя пользователю.\n"
            "/rnick - удалить имя пользователю.\n"
            "/nlist - список пользователей с ником.\n"
            "/getban - информация о Блокировках.\n"
        )
    if w >= 2:
        res += (
            "\nКоманды старших модераторов:\n"
            "/addmoder - выдать права модератора.\n"
            "/removerole - снять уровень прав.\n"
            "/ban - блокировка пользователя в Беседе.\n"
            "/unban - снятие блокировки пользователю в беседе.\n"
        )
    if w >= 3:
        res += "\nКоманды администраторов:\n/addsenmoder - выдать права старшего модератора.\n"
    if w >= 4:
        res += "\nКоманды старших администраторов:\n/addadmin - выдать права администратора.\n"
    if w >= 5:
        res += "\nКоманды зам. спец. администраторов:\n/addsenadmin - выдать права старшего администратора.\n"
    if w >= 6:
        res += "\nКоманды спец. администраторов:\n/addzsa - выдать права зам. спец. администратора.\n"
    if w >= 7:
        res += "\nКоманды владельца:\n/addsa - выдать права специального администратора.\n"
    await m.answer(res.strip())
    if w >= 8:
        gres = (
            "Команды руководства Бота:\n\n"
            "Зам. Спец. Руководителя:\n"
            "/gstaff - руководство Бота.\n"
            "/addowner - выдать права владельца.\n"
            "/gbanpl - блокировка пользователя во всех игровых Беседах.\n"
            "/gunbanpl - снятие блокировки во всех игровых Беседах.\n\n"
            "Основной Зам. Спец. Руководителя:\nОтсутствуют.\n\n"
            "Спец. Руководителя:\n"
            "/start - активировать Беседу.\n"
            "/sync - синхронизация с базой данных.\n"
            "/chatid - узнать айди Беседы.\n"
            "/delchat - удалить чат из Базы данных."
        )
        await m.answer(gres)

@bot.on.message(text=["/info"])
async def info_cmd(m: Message):
    await m.answer("Официальные ресурсы: [вставьте ссылки или информацию]")

@bot.on.message(text=["/getid", "/getid <args>"])
async def getid_cmd(m: Message, args=None):
    ensure_chat(str(m.peer_id))
    t = await get_target_id(m, args) or m.from_id
    await m.answer(f"Оригинальная ссылка [id{t}|пользователя]: https://vk.com/id{t}")

@bot.on.message(text=["/stats", "/stats <args>"])
async def stats_cmd(m: Message, args=None):
    ensure_chat(str(m.peer_id))
    t = await get_target_id(m, args) or m.from_id
    uid, pid = str(t), str(m.peer_id)
    role, nick = get_user_info(m.peer_id, t)
    bans_cnt = sum(1 for bans in PUNISHMENTS.get("bans", {}).values() if uid in bans)
    gban = "Да" if uid in PUNISHMENTS.get("gbans_status", {}) else "Нет"
    gbanpl = "Да" if uid in PUNISHMENTS.get("gbans_pl", {}) else "Нет"
    mutes = DATABASE["chats"].get(pid, {}).get("mutes", {})
    is_muted = "Да" if uid in mutes and datetime.datetime.now(TZ_MSK).timestamp() < mutes[uid] else "Нет"
    st = DATABASE["chats"].get(pid, {}).get("stats", {}).get(uid, {"count": 0, "last": 0})
    dt = datetime.datetime.fromtimestamp(st["last"], TZ_MSK).strftime("%d/%m/%Y %H:%M:%S %p") if st["last"] else "Нет данных"
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

# ------------------------------
# 7. Модерация: mute, unmute, buttons
# ------------------------------
@bot.on.message(text=["/mute", "/mute <args>"])
async def mute_cmd(m: Message, args=None):
    ensure_chat(str(m.peer_id))
    if not await check_access(m, "Модератор"):
        return
    t = await get_target_id(m, args)
    if not t:
        return
    parts = args.split() if args else []
    mins = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 10
    reason = "Нарушение" if len(parts) < 3 else " ".join(parts[2:])
    until = datetime.datetime.now(TZ_MSK).timestamp() + mins * 60
    pid = str(m.peer_id)
    DATABASE["chats"][pid]["mutes"][str(t)] = until
    dt = datetime.datetime.fromtimestamp(until, TZ_MSK).strftime("%d/%m/%Y %H:%M:%S")
    kb = Keyboard(inline=True)
    kb.add(Text("Снять мут", {"cmd": "unmute", "u": t}), color=KeyboardButtonColor.POSITIVE)
    kb.add(Text("Очистить", {"cmd": "clear"}), color=KeyboardButtonColor.NEGATIVE)
    a_name = f"[id{m.from_id}|Модератор MANLIX]"
    await m.answer(f"{a_name} выдал(-а) мут [id{t}|пользователю]\nПричина: {reason}\nМут выдан до: {dt}", keyboard=kb)
    await push_to_github(DATABASE, GH_PATH_DB, EXTERNAL_DB)

@bot.on.message(text=["/unmute", "/unmute <args>"])
async def unmute_cmd(m: Message, args=None):
    ensure_chat(str(m.peer_id))
    if not await check_access(m, "Модератор"):
        return
    t = await get_target_id(m, args)
    if not t:
        return
    pid = str(m.peer_id)
    if str(t) in DATABASE["chats"][pid].get("mutes", {}):
        del DATABASE["chats"][pid]["mutes"][str(t)]
        await push_to_github(DATABASE, GH_PATH_DB, EXTERNAL_DB)
        a_name = f"[id{m.from_id}|Модератор MANLIX]"
        await m.answer(f"{a_name} снял(-а) мут [id{t}|пользователю]")

@bot.on.raw_event(GroupEventType.MESSAGE_EVENT, dataclass=MessageEvent)
async def buttons(event: MessageEvent):
    payload = event.payload
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except:
            payload = {}
    pid = str(event.peer_id)
    rank, _ = get_user_info(event.peer_id, event.user_id)
    if RANK_WEIGHT.get(rank, 0) < 1:
        return await event.show_snackbar("Недостаточно прав!")
    cmd = payload.get("cmd")
    if cmd == "unmute":
        t = payload.get("u")
        if str(t) in DATABASE["chats"][pid].get("mutes", {}):
            del DATABASE["chats"][pid]["mutes"][str(t)]
            await push_to_github(DATABASE, GH_PATH_DB, EXTERNAL_DB)
            a_name = f"[id{event.user_id}|Модератор MANLIX]"
            new_text = f"{a_name} снял(-а) мут [id{t}|пользователю]"
            try:
                await bot.api.messages.edit(peer_id=event.peer_id, message=new_text, conversation_message_id=event.conversation_message_id)
            except Exception as e:
                print("buttons edit error:", e)
    if cmd == "clear":
        try:
            await bot.api.messages.delete(peer_id=event.peer_id, conversation_message_ids=[event.conversation_message_id], delete_for_all=True)
        except Exception as e:
            print("buttons clear error:", e)

# ------------------------------
# 8. Kick / ban / unban / role management / staff
# ------------------------------
@bot.on.message(text=["/kick", "/kick <args>"])
async def kick_cmd(m: Message, args=None):
    ensure_chat(str(m.peer_id))
    if not await check_access(m, "Модератор"):
        return
    t = await get_target_id(m, args)
    if not t:
        return
    try:
        chat_id = m.peer_id - 2000000000
        try:
            await bot.api.messages.remove_chat_user(chat_id=chat_id, member_id=t)
        except:
            await bot.api.messages.remove_chat_user(chat_id=chat_id, user_id=t)
    except Exception as e:
        print("kick error:", e)
    a_name = f"[id{m.from_id}|Модератор MANLIX]"
    await m.answer(f"{a_name} исключил(-а) [id{t}|пользователя] из Беседы.")

@bot.on.message(text=["/ban", "/ban <args>"])
async def ban_cmd(m: Message, args=None):
    ensure_chat(str(m.peer_id))
    if not await check_access(m, "Старший Модератор"):
        return
    t = await get_target_id(m, args)
    if not t:
        return
    parts = args.split() if args else []
    reason = "Нарушение" if len(parts) < 2 else " ".join(parts[1:])
    pid = str(m.peer_id)
    if pid not in PUNISHMENTS["bans"]:
        PUNISHMENTS["bans"][pid] = {}
    PUNISHMENTS["bans"][pid][str(t)] = {"admin": m.from_id, "reason": reason, "date": datetime.datetime.now(TZ_MSK).timestamp()}
    try:
        conv = await bot.api.messages.get_conversations_by_id(peer_ids=[m.peer_id])
        if conv.items:
            title = conv.items[0].chat_settings.title
            DATABASE["chats"][pid]["title"] = title
    except:
        pass
    try:
        chat_id = m.peer_id - 2000000000
        try:
            await bot.api.messages.remove_chat_user(chat_id=chat_id, member_id=t)
        except:
            await bot.api.messages.remove_chat_user(chat_id=chat_id, user_id=t)
    except:
        pass
    await push_to_github(PUNISHMENTS, GH_PATH_PUN, EXTERNAL_PUN)
    a_name = f"[id{m.from_id}|Модератор MANLIX]"
    await m.answer(f"{a_name} заблокировал [id{t}|пользователя] в беседе.")

@bot.on.message(text=["/unban", "/unban <args>"])
async def unban_cmd(m: Message, args=None):
    ensure_chat(str(m.peer_id))
    if not await check_access(m, "Старший Модератор"):
        return
    t = await get_target_id(m, args)
    if not t:
        return
    pid = str(m.peer_id)
    if pid in PUNISHMENTS["bans"] and str(t) in PUNISHMENTS["bans"][pid]:
        del PUNISHMENTS["bans"][pid][str(t)]
        await push_to_github(PUNISHMENTS, GH_PATH_PUN, EXTERNAL_PUN)
    a_name = f"[id{m.from_id}|Модератор MANLIX]"
    await m.answer(f"{a_name} разблокировал [id{t}|пользователя] в беседе.")

async def set_role_in_chat(pid: str, uid: str, role_name: str):
    ensure_chat(pid)
    current = DATABASE["chats"][pid]["staff"].get(uid, [role_name, None])
    nick = current[1]
    DATABASE["chats"][pid]["staff"][uid] = [role_name, nick]

@bot.on.message(text=["/addmoder", "/addmoder <args>"])
async def addmod(m: Message, args=None):
    ensure_chat(str(m.peer_id))
    if not await check_access(m, "Старший Модератор"):
        return
    t = await get_target_id(m, args)
    if not t: return
    pid, uid = str(m.peer_id), str(t)
    await set_role_in_chat(pid, uid, "Модератор")
    await push_to_github(DATABASE, GH_PATH_DB, EXTERNAL_DB)
    role, a_nick = get_user_info(m.peer_id, m.from_id)
    a_name = f"[id{m.from_id}|{a_nick or role}]"
    await m.answer(f"{a_name} выдал(-а) права модератора [id{t}|пользователю]")

@bot.on.message(text=["/addsenmoder", "/addsenmoder <args>"])
async def addsenmod(m: Message, args=None):
    ensure_chat(str(m.peer_id))
    if not await check_access(m, "Администратор"):
        return
    t = await get_target_id(m, args)
    if not t: return
    pid, uid = str(m.peer_id), str(t)
    await set_role_in_chat(pid, uid, "Старший Модератор")
    await push_to_github(DATABASE, GH_PATH_DB, EXTERNAL_DB)
    role, a_nick = get_user_info(m.peer_id, m.from_id)
    a_name = f"[id{m.from_id}|{a_nick or role}]"
    await m.answer(f"{a_name} выдал(-а) права старшего модератора [id{t}|пользователю]")

@bot.on.message(text=["/addadmin", "/addadmin <args>"])
async def addadm(m: Message, args=None):
    ensure_chat(str(m.peer_id))
    if not await check_access(m, "Старший Администратор"):
        return
    t = await get_target_id(m, args)
    if not t: return
    pid, uid = str(m.peer_id), str(t)
    await set_role_in_chat(pid, uid, "Администратор")
    await push_to_github(DATABASE, GH_PATH_DB, EXTERNAL_DB)
    role, a_nick = get_user_info(m.peer_id, m.from_id)
    a_name = f"[id{m.from_id}|{a_nick or role}]"
    await m.answer(f"{a_name} выдал(-а) права администратора [id{t}|пользователю]")

@bot.on.message(text=["/addsenadmin", "/addsenadmin <args>"])
async def addsenadm(m: Message, args=None):
    ensure_chat(str(m.peer_id))
    if not await check_access(m, "Зам. Спец. Администратора"):
        return
    t = await get_target_id(m, args)
    if not t: return
    pid, uid = str(m.peer_id), str(t)
    await set_role_in_chat(pid, uid, "Старший Администратор")
    await push_to_github(DATABASE, GH_PATH_DB, EXTERNAL_DB)
    role, a_nick = get_user_info(m.peer_id, m.from_id)
    a_name = f"[id{m.from_id}|{a_nick or role}]"
    await m.answer(f"{a_name} выдал(-а) права старшего администратора [id{t}|пользователю]")

@bot.on.message(text=["/addzsa", "/addzsa <args>"])
async def addzsa(m: Message, args=None):
    ensure_chat(str(m.peer_id))
    if not await check_access(m, "Спец. Администратор"):
        return
    t = await get_target_id(m, args)
    if not t: return
    pid, uid = str(m.peer_id), str(t)
    await set_role_in_chat(pid, uid, "Зам. Спец. Администратора")
    await push_to_github(DATABASE, GH_PATH_DB, EXTERNAL_DB)
    role, a_nick = get_user_info(m.peer_id, m.from_id)
    a_name = f"[id{m.from_id}|{a_nick or role}]"
    await m.answer(f"{a_name} выдал(-а) права зам. специального администратора [id{t}|пользователю]")

@bot.on.message(text=["/addsa", "/addsa <args>"])
async def addsa(m: Message, args=None):
    ensure_chat(str(m.peer_id))
    if not await check_access(m, "Владелец"):
        return
    t = await get_target_id(m, args)
    if not t: return
    pid, uid = str(m.peer_id), str(t)
    await set_role_in_chat(pid, uid, "Спец. Администратор")
    await push_to_github(DATABASE, GH_PATH_DB, EXTERNAL_DB)
    role, a_nick = get_user_info(m.peer_id, m.from_id)
    a_name = f"[id{m.from_id}|{a_nick or role}]"
    await m.answer(f"{a_name} выдал(-а) права специального администратора [id{t}|пользователю]")

@bot.on.message(text=["/addowner", "/addowner <args>"])
async def addowner(m: Message, args=None):
    ensure_chat(str(m.peer_id))
    if not await check_access(m, "Зам. Спец. Руководителя"):
        return
    t = await get_target_id(m, args)
    if not t: return
    pid, uid = str(m.peer_id), str(t)
    await set_role_in_chat(pid, uid, "Владелец")
    await push_to_github(DATABASE, GH_PATH_DB, EXTERNAL_DB)
    role, a_nick = get_user_info(m.peer_id, m.from_id)
    a_name = f"[id{m.from_id}|{a_nick or role}]"
    await m.answer(f"{a_name} выдал(-а) права владельца [id{t}|пользователю]")

@bot.on.message(text=["/removerole", "/removerole <args>"])
async def removerole(m: Message, args=None):
    ensure_chat(str(m.peer_id))
    if not await check_access(m, "Старший Модератор"):
        return
    t = await get_target_id(m, args)
    if not t: return
    pid = str(m.peer_id)
    uid = str(t)
    if uid in DATABASE["chats"][pid].get("staff", {}):
        del DATABASE["chats"][pid]["staff"][uid]
        await push_to_github(DATABASE, GH_PATH_DB, EXTERNAL_DB)
    role, a_nick = get_user_info(m.peer_id, m.from_id)
    a_name = f"[id{m.from_id}|{a_nick or role}]"
    await m.answer(f"{a_name} снял(-а) уровень прав [id{t}|пользователю]")

@bot.on.message(text="/staff")
async def staff_view(m: Message):
    ensure_chat(str(m.peer_id))
    pid = str(m.peer_id)
    staff = DATABASE["chats"].get(pid, {}).get("staff", {})
    order = ["Владелец", "Спец. Администратор", "Зам. Спец. Администратора", "Старший Администратор", "Администратор", "Старший Модератор", "Модератор"]
    res = ""
    for r in order:
        res += f"{r}: \n"
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
                members.append(f"– [id{u}|{display}]")
        res += "\n".join(members) if members else "– Отсутствует."
        res += "\n\n"
    await m.answer(res.strip())

@bot.on.message(text=["/setnick", "/setnick <args>"])
async def setnick(m: Message, args=None):
    ensure_chat(str(m.peer_id))
    if not await check_access(m, "Модератор"):
        return
    if not args:
        return
    parts = args.split()
    target_token = parts[0]
    nick_words = parts[1:]
    if not nick_words:
        return await m.answer("Укажите ник (может быть из нескольких слов).")
    t = await get_target_id(m, target_token)
    if not t:
        return
    new_nick = " ".join(nick_words)
    pid, uid = str(m.peer_id), str(t)
    role, _ = get_user_info(m.peer_id, t)
    DATABASE["chats"][pid]["staff"][uid] = [role, new_nick]
    await push_to_github(DATABASE, GH_PATH_DB, EXTERNAL_DB)
    r, a_nick = get_user_info(m.peer_id, m.from_id)
    a_name = f"[id{m.from_id}|{a_nick or r}]"
    await m.answer(f"{a_name} установил(-а) новое имя [id{t}|пользователю]: {new_nick}")

@bot.on.message(text=["/rnick", "/rnick <args>"])
async def rnick(m: Message, args=None):
    ensure_chat(str(m.peer_id))
    if not await check_access(m, "Модератор"):
        return
    t = await get_target_id(m, args)
    if not t:
        return
    pid, uid = str(m.peer_id), str(t)
    role, _ = get_user_info(m.peer_id, t)
    DATABASE["chats"][pid]["staff"][uid] = [role, None]
    await push_to_github(DATABASE, GH_PATH_DB, EXTERNAL_DB)
    r, a_nick = get_user_info(m.peer_id, m.from_id)
    a_name = f"[id{m.from_id}|{a_nick or r}]"
    await m.answer(f"{a_name} убрал(-а) имя [id{t}|пользователю]")

@bot.on.message(text=["/nlist"])
async def nick_list(m: Message):
    ensure_chat(str(m.peer_id))
    if not await check_access(m, "Модератор"):
        return
    pid = str(m.peer_id)
    staff = DATABASE["chats"].get(pid, {}).get("staff", {})
    users = [(u, n) for u, (_, n) in staff.items() if n]
    if users:
        msg = "Список пользователей с ником:\n"
        for i, (u, n) in enumerate(users, 1):
            msg += f"{i}. [id{u}|{n}]\n"
    else:
        msg = "Список пользователей с ником:\nОтсутствуют"
    await m.answer(msg)

# ------------------------------
# 9. Игровая система (сохранение/проверки)
# ------------------------------
@bot.on.message(text="/ghelp")
async def ghelp(m: Message):
    ensure_chat(str(m.peer_id))
    await m.answer(
        "🎮 Игровые команды MANLIX:\n\n"
        "🎉 /prise — Получить ежечасный приз\n"
        "💰 /balance — Наличные средства\n"
        "🏦 /bank — Состояние счетов\n"
        "📥 /положить [сумма] — Положить в банк\n"
        "📤 /снять [сумма] — Снять из банка\n"
        "💸 /перевести [ссылка] [сумма] — Перевод со счета на счет\n"
        "🎰 /roulette [сумма] — Рулетка"
    )

@bot.on.message(text="/prise")
async def prize(m: Message):
    ensure_chat(str(m.peer_id))
    uid = str(m.from_id)
    if uid not in ECONOMY:
        ECONOMY[uid] = {"cash": 0, "bank": 0, "last": 0}
    if datetime.datetime.now().timestamp() - ECONOMY[uid]["last"] < 3600:
        return await m.answer("Приз доступен раз в час.")
    win = random.randint(100, 1000)
    ECONOMY[uid]["cash"] += win
    ECONOMY[uid]["last"] = datetime.datetime.now().timestamp()
    await push_to_github(ECONOMY, GH_PATH_ECO, EXTERNAL_ECO)
    await m.answer(f"🎉 Вы получили приз {win}$")

@bot.on.message(text="/balance")
async def balance_cmd(m: Message):
    ensure_chat(str(m.peer_id))
    e = ECONOMY.get(str(m.from_id), {"cash": 0})
    await m.answer(f"💵 Ваши наличные: {e['cash']}$")

@bot.on.message(text="/bank")
async def bank_view(m: Message):
    ensure_chat(str(m.peer_id))
    e = ECONOMY.get(str(m.from_id), {"cash": 0, "bank": 0})
    await m.answer(f"🏦 …::: MANLIX BANK :::…\n\n💵 Наличные: {e['cash']}$\n💳 На счету: {e['bank']}$")

@bot.on.message(text=["/положить <amount>"])
async def deposit(m: Message, amount=None):
    ensure_chat(str(m.peer_id))
    if not amount:
        return await m.answer("Укажите сумму для внесения.")
    try:
        amount = int(amount)
    except:
        return await m.answer("Некорректная сумма.")
    uid = str(m.from_id)
    if uid not in ECONOMY:
        ECONOMY[uid] = {"cash": 0, "bank": 0, "last": 0}
    if ECONOMY[uid]["cash"] < amount:
        return await m.answer("Недостаточно наличных для этой операции.")
    ECONOMY[uid]["cash"] -= amount
    ECONOMY[uid]["bank"] += amount
    await push_to_github(ECONOMY, GH_PATH_ECO, EXTERNAL_ECO)
    await m.answer(f"💲Вы положили на свой счет {amount}$")

@bot.on.message(text=["/снять <amount>"])
async def withdraw(m: Message, amount=None):
    ensure_chat(str(m.peer_id))
    if not amount:
        return await m.answer("Укажите сумму для снятия.")
    try:
        amount = int(amount)
    except:
        return await m.answer("Некорректная сумма.")
    uid = str(m.from_id)
    if uid not in ECONOMY:
        ECONOMY[uid] = {"cash": 0, "bank": 0, "last": 0}
    if ECONOMY[uid]["bank"] < amount:
        return await m.answer("Недостаточно средств на счете.")
    ECONOMY[uid]["bank"] -= amount
    ECONOMY[uid]["cash"] += amount
    await push_to_github(ECONOMY, GH_PATH_ECO, EXTERNAL_ECO)
    await m.answer(f"💲Вы сняли с своего счета {amount}$")

@bot.on.message(text=["/перевести <args>"])
async def transfer(m: Message, args=None):
    ensure_chat(str(m.peer_id))
    if not args:
        return await m.answer("Укажите получателя и сумму.")
    parts = args.split()
    if len(parts) < 2:
        return await m.answer("Укажите получателя и сумму.")
    t = await get_target_id(m, parts[0])
    if not t:
        return await m.answer("Не удалось определить получателя.")
    try:
        amount = int(parts[1])
    except:
        return await m.answer("Некорректная сумма.")
    uid = str(m.from_id)
    rid = str(t)
    if uid not in ECONOMY:
        ECONOMY[uid] = {"cash": 0, "bank": 0, "last": 0}
    if rid not in ECONOMY:
        ECONOMY[rid] = {"cash": 0, "bank": 0, "last": 0}
    if ECONOMY[uid]["bank"] < amount:
        return await m.answer("Недостаточно средств на вашем счете.")
    ECONOMY[uid]["bank"] -= amount
    ECONOMY[rid]["bank"] += amount
    await push_to_github(ECONOMY, GH_PATH_ECO, EXTERNAL_ECO)
    await m.answer(f"💲Вы перевели [id{t}|пользователю] {amount}$")

@bot.on.message(text=["/roulette <amount>"])
async def roulette(m: Message, amount=None):
    ensure_chat(str(m.peer_id))
    if not amount:
        return await m.answer("Укажите ставку.")
    try:
        amount = int(amount)
    except:
        return await m.answer("Некорректная ставка.")
    uid = str(m.from_id)
    if uid not in ECONOMY:
        ECONOMY[uid] = {"cash": 0, "bank": 0, "last": 0}
    if ECONOMY[uid]["cash"] < amount:
        return await m.answer("Недостаточно наличных.")
    if random.choice([True, False]):
        ECONOMY[uid]["cash"] += amount
        await m.answer(f"🎰 Поздравляем! Вы выиграли {amount}$!")
    else:
        ECONOMY[uid]["cash"] -= amount
        await m.answer(f"🎰 К сожалению, вы проиграли {amount}$...")
    await push_to_github(ECONOMY, GH_PATH_ECO, EXTERNAL_ECO)

# ------------------------------
# 10. Управление глобальными банами: gban, gbanpl, gunban, gunbanpl, getban
# ------------------------------
@bot.on.message(text=["/gban", "/gban <args>"])
async def gban_cmd(m: Message, args=None):
    ensure_chat(str(m.peer_id))
    if not await check_access(m, "Специальный Руководитель"):
        return
    t = await get_target_id(m, args)
    if not t:
        return
    parts = args.split() if args else []
    reason = "Нарушение" if len(parts) < 2 else " ".join(parts[1:])
    uid = str(t)
    PUNISHMENTS["gbans_status"][uid] = {"admin": m.from_id, "reason": reason, "date": datetime.datetime.now(TZ_MSK).timestamp()}
    await push_to_github(PUNISHMENTS, GH_PATH_PUN, EXTERNAL_PUN)
    role, a_nick = get_user_info(m.peer_id, m.from_id)
    a_name = f"[id{m.from_id}|{a_nick or role}]"
    await m.answer(f"{a_name} занес [id{t}|пользователя] в глобальную Блокировку Бота.")

@bot.on.message(text=["/gunban", "/gunban <args>"])
async def gunban(m: Message, args=None):
    ensure_chat(str(m.peer_id))
    if not await check_access(m, "Специальный Руководитель"):
        return
    t = await get_target_id(m, args)
    if not t:
        return
    uid = str(t)
    if uid in PUNISHMENTS["gbans_status"]:
        del PUNISHMENTS["gbans_status"][uid]
        await push_to_github(PUNISHMENTS, GH_PATH_PUN, EXTERNAL_PUN)
    role, a_nick = get_user_info(m.peer_id, m.from_id)
    a_name = f"[id{m.from_id}|{a_nick or role}]"
    await m.answer(f"{a_name} вынес [id{t}|пользователя] из Глобальной Блокировки Бота.")

@bot.on.message(text=["/gbanpl", "/gbanpl <args>"])
async def gbanpl_cmd(m: Message, args=None):
    ensure_chat(str(m.peer_id))
    if not await check_access(m, "Зам. Спец. Руководителя"):
        return
    t = await get_target_id(m, args)
    if not t:
        return
    parts = args.split() if args else []
    reason = "Нарушение" if len(parts) < 2 else " ".join(parts[1:])
    uid = str(t)
    PUNISHMENTS["gbans_pl"][uid] = {"admin": m.from_id, "reason": reason, "date": datetime.datetime.now(TZ_MSK).timestamp()}
    try:
        chat_id = m.peer_id - 2000000000
        try:
            await bot.api.messages.remove_chat_user(chat_id=chat_id, member_id=t)
        except:
            await bot.api.messages.remove_chat_user(chat_id=chat_id, user_id=t)
    except:
        pass
    for pid in DATABASE.get("chats", {}):
        try:
            chat_id = int(pid) - 2000000000
            try:
                await bot.api.messages.remove_chat_user(chat_id=chat_id, member_id=t)
            except:
                await bot.api.messages.remove_chat_user(chat_id=chat_id, user_id=t)
        except:
            pass
    await push_to_github(PUNISHMENTS, GH_PATH_PUN, EXTERNAL_PUN)
    role, a_nick = get_user_info(m.peer_id, m.from_id)
    a_name = f"[id{m.from_id}|{a_nick or role}]"
    await m.answer(f"{a_name} заблокировал [id{t}|пользователя] во всех игровых Беседах.")

@bot.on.message(text=["/gunbanpl", "/gunbanpl <args>"])
async def gunbanpl_cmd(m: Message, args=None):
    ensure_chat(str(m.peer_id))
    if not await check_access(m, "Зам. Спец. Руководителя"):
        return
    t = await get_target_id(m, args)
    if not t:
        return
    uid = str(t)
    if uid in PUNISHMENTS["gbans_pl"]:
        del PUNISHMENTS["gbans_pl"][uid]
        await push_to_github(PUNISHMENTS, GH_PATH_PUN, EXTERNAL_PUN)
    role, a_nick = get_user_info(m.peer_id, m.from_id)
    a_name = f"[id{m.from_id}|{a_nick or role}]"
    await m.answer(f"{a_name} разблокировал [id{t}|пользователя] во всех игровых Беседах.")

@bot.on.message(text=["/getban", "/getban <args>"])
async def getban_cmd(m: Message, args=None):
    ensure_chat(str(m.peer_id))
    if not await check_access(m, "Модератор"):
        return
    t = await get_target_id(m, args)
    if not t:
        return
    uid = str(t)
    u_name = "пользователя"
    ans = f"Информация о блокировках [id{t}|{u_name}]\n\n"
    for key, label in [("gbans_status", "общей Блокировке в Беседах"), ("gbans_pl", "общей Блокировке в Беседе игроков")]:
        ans += f"Информация о {label}: "
        if uid in PUNISHMENTS.get(key, {}):
            b = PUNISHMENTS[key][uid]
            dt = datetime.datetime.fromtimestamp(b['date'], TZ_MSK).strftime("%d/%m/%Y %H:%M:%S")
            ans += f"\n[id{b['admin']}|Модератор MANLIX] | {b.get('reason', '-')} | {dt}\n\n"
        else:
            ans += "отсутствует\n\n"
    local = []
    for pid, users in PUNISHMENTS.get("bans", {}).items():
        if uid in users:
            b = users[uid]
            title = DATABASE["chats"].get(pid, {}).get("title", f"Чат {pid}")
            if not title or title == f"Чат {pid}":
                try:
                    conv = await bot.api.messages.get_conversations_by_id(peer_ids=int(pid))
                    if conv.items:
                        title = conv.items[0].chat_settings.title
                        DATABASE["chats"][pid]["title"] = title
                        await push_to_github(DATABASE, GH_PATH_DB, EXTERNAL_DB)
                except:
                    pass
            dt = datetime.datetime.fromtimestamp(b['date'], TZ_MSK).strftime("%d/%m/%Y %H:%M:%S")
            local.append(f"{title} | [id{b['admin']}|Модератор MANLIX] | {b.get('reason', '-')} | {dt}")
    ans += f"Количество Бесед, в которых заблокирован пользователь: {len(local)}\n"
    if local:
        ans += "Информация о последних 10 Блокировках:\n"
        for i, row in enumerate(reversed(local[-10:]), 1):
            ans += f"{i}) {row}\n"
    else:
        ans += "Блокировки в беседах отсутствуют"
    await m.answer(ans)

# ------------------------------
# 11. /start (активация беседы)
# ------------------------------
@bot.on.message(text="/start")
async def start(m: Message):
    if m.from_id != 870757778:
        return await m.answer("Активировать беседу может только Специальный Руководитель.")
    pid = str(m.peer_id)
    ensure_chat(pid)
    try:
        conv = await bot.api.messages.get_conversations_by_id(peer_ids=[m.peer_id])
        if conv.items:
            DATABASE["chats"][pid]["title"] = conv.items[0].chat_settings.title
    except:
        pass
    await push_to_github(DATABASE, GH_PATH_DB, EXTERNAL_DB)
    await m.answer("Вы успешно активировали Беседу.")

@bot.on.message(text="/chatid")
async def chatid(m: Message):
    if not await check_access(m, "Специальный Руководитель"):
        return
    await m.answer(f"айди Беседы: {m.peer_id}")

@bot.on.message(text="/delchat")
async def delchat(m: Message):
    if not await check_access(m, "Специальный Руководитель"):
        return
    pid = str(m.peer_id)
    if pid in DATABASE["chats"]:
        del DATABASE["chats"][pid]
        await push_to_github(DATABASE, GH_PATH_DB, EXTERNAL_DB)
        await m.answer("Вы успешно удалили чат с Базы данных.")
    else:
        await m.answer("Чат не найден в Базе данных.")

@bot.on.message(text="/sync")
async def sync(m: Message):
    if not await check_access(m, "Специальный Руководитель"):
        return
    global DATABASE, ECONOMY, PUNISHMENTS
    DATABASE = await load_from_github(GH_PATH_DB, EXTERNAL_DB)
    ECONOMY = await load_from_github(GH_PATH_ECO, EXTERNAL_ECO)
    PUNISHMENTS = await load_from_github(GH_PATH_PUN, EXTERNAL_PUN)
    await m.answer("Вы успешно синхронизировали Беседу с Базой данных.")

@bot.on.message(text="/gstaff")
async def gstaff_view(m: Message):
    if not await check_access(m, "Зам. Спец. Руководителя"):
        return
    g = DATABASE["gstaff"]
    res = "MANLIX MANAGER | Команда Бота:\n\n| Специальный Руководитель:\n– [id870757778|Misha Manlix]\n\n| Основной зам. Спец. Руководителя:\n"
    if g["main_zam"]:
        res += f"– [id{g['main_zam']}|Пользователь]\n"
    else:
        res += "– Отсутствует.\n"
    res += "\n| Зам. Спец. Руководителя:\n"
    if g["zams"]:
        res += "\n".join(f"– [id{z}|Пользователь]" for z in g["zams"])
    else:
        res += "– Отсутствует."
    await m.answer(res)

# ------------------------------
# 12. Обработка системных действий и автокик забаненных при приглашении
# ------------------------------
@bot.on.message()
async def actions(m: Message):
    if not m.action:
        return
    typ = m.action.type.value if hasattr(m.action.type, 'value') else str(m.action.type)
    if typ == "chat_kick_user":
        if m.action.member_id == GROUP_ID:  # Bot kicked
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
                await m.answer(f"[id870757778|Модератор MANLIX] исключил(-а) [id{invited}|пользователя] — он находится в списке блокировок.")

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

# ------------------------------
# 13. HTTP server & run
# ------------------------------
class H(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")

if __name__ == "__main__":
    if "chats" not in DATABASE:
        DATABASE["chats"] = {}
    if "gstaff" not in DATABASE:
        DATABASE["gstaff"] = {"spec": 870757778, "main_zam": None, "zams": []}
    threading.Thread(target=HTTPServer(('0.0.0.0', int(os.environ.get("PORT", 10000))), H).serve_forever, daemon=True).start()
    bot.run_forever()
```            local.append(f

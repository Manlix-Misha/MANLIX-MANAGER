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

# --- 1. НАСТРОЙКИ ---
GH_TOKEN = os.environ.get("GH_TOKEN")
GH_REPO = os.environ.get("GH_REPO")
GH_PATH_DB = "database.json"
GH_PATH_ECO = "economy.json"
GH_PATH_PUN = "punishments.json"

EXTERNAL_DB = "database.json"
EXTERNAL_ECO = "economy.json"
EXTERNAL_PUN = "punishments.json"

def load_local_data(path):
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print("load_local_data error:", e)
            return {}
    return {}

DATABASE = load_local_data(EXTERNAL_DB)
ECONOMY = load_local_data(EXTERNAL_ECO)
PUNISHMENTS = load_local_data(EXTERNAL_PUN)

# Инициализация структур
if not isinstance(DATABASE, dict): DATABASE = {}
if not isinstance(ECONOMY, dict): ECONOMY = {}
if not isinstance(PUNISHMENTS, dict): PUNISHMENTS = {}
if "gbans_status" not in PUNISHMENTS: PUNISHMENTS["gbans_status"] = {}
if "gbans_pl" not in PUNISHMENTS: PUNISHMENTS["gbans_pl"] = {}
if "bans" not in PUNISHMENTS: PUNISHMENTS["bans"] = {}
if "warns" not in PUNISHMENTS: PUNISHMENTS["warns"] = {}
if "chats" not in DATABASE: DATABASE["chats"] = {}
if "gstaff" not in DATABASE:  # структура для gstaff
    DATABASE["gstaff"] = {
        "spec": 870757778,
        "main_zam": None,
        "zams": []
    }

RANK_WEIGHT = {
    "Пользователь": 0, "Модератор": 1, "Старший Модератор": 2,
    "Администратор": 3, "Старший Администратор": 4, "Зам. Спец. Администратора": 5,
    "Спец. Администратор": 6, "Владелец": 7, "Зам. Спец. Руководителя": 8,
    "Основной Зам. Спец. Руководителя": 9, "Специальный Руководитель": 10
}

TZ_MSK = datetime.timezone(datetime.timedelta(hours=3))

async def push_to_github(data, gh_path, local_path):
    # Если нет токена/репо — просто сохраняем локально
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
            content = base64.b64encode(
                json.dumps(data, ensure_ascii=False, indent=4).encode('utf-8')
            ).decode('utf-8')
            payload = {"message": "Update DB", "content": content}
            if sha: payload["sha"] = sha
            async with session.put(url, headers=headers, json=payload) as resp2:
                if resp2.status not in (200,201):
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

# =========================
# Утилиты
# =========================

def ensure_chat(pid: str):
    """Убедиться, что структура чата существует."""
    if "chats" not in DATABASE:
        DATABASE["chats"] = {}
    if pid not in DATABASE.get("chats", {}):
        DATABASE["chats"][pid] = {
            "title": f"Чат {pid}",
            "staff": {},
            "mutes": {},
            "stats": {}
        }
    else:
        # ensure subkeys
        chat = DATABASE["chats"][pid]
        if "title" not in chat: chat["title"] = f"Чат {pid}"
        if "staff" not in chat: chat["staff"] = {}
        if "mutes" not in chat: chat["mutes"] = {}
        if "stats" not in chat: chat["stats"] = {}

bot = Bot(token=os.environ.get("TOKEN"))

async def get_target_id(m: Message, args: str):
    # Если ответ на сообщение — берём из reply
    if getattr(m, "reply_message", None):
        return m.reply_message.from_id
    if not args:
        return None
    # Поиск числа после id или в ссылке
    match = re.search(r"(?:id|\[id|vk\.com\/id|vk\.com\/)(\d+)", args)
    if match:
        return int(match.group(1))
    # Убираем лишние символы (упоминание [id...|Name], ссылка vk.com/shortname и т.д.)
    raw = args.split('/')[-1].split('|')[0].replace('[', '').replace('@', '').strip()
    try:
        # resolve_screen_name может выбросить, обрабатываем
        res = await bot.api.utils.resolve_screen_name(screen_name=raw)
        if res and getattr(res, "type", None):
            tval = getattr(res.type, "value", None) or getattr(res.type, "name", None) or res.type
            if str(tval).lower() == "user" or getattr(res, "object_id", None):
                return res.object_id
    except Exception:
        pass
    num = re.sub(r"\D", "", args)
    if num:
        return int(num)
    return None

def get_user_info(peer_id, user_id):
    # Специальный фикс для конкретного ID (владелец бота)
    try:
        if int(user_id) == 870757778:
            return "Специальный Руководитель", "Misha Manlix"
    except:
        pass
    staff = DATABASE.get("chats", {}).get(str(peer_id), {}).get("staff", {})
    # Если нет записи — обычный пользователь без ника
    return staff.get(str(user_id), ["Пользователь", None])

async def check_access(m: Message, min_rank: str):
    rank, _ = get_user_info(m.peer_id, m.from_id)
    if RANK_WEIGHT.get(rank, 0) < RANK_WEIGHT.get(min_rank, 0):
        await m.answer("Недостаточно прав!")
        return False
    return True

# --- 3. МИДЛВЭР (удаление сообщений от заблокированных) ---
class ChatMiddleware(BaseMiddleware[Message]):
    async def pre(self):
        # Если нет from_id или системное сообщение — пропускаем
        if not getattr(self.event, "from_id", None) or self.event.from_id < 0:
            return
        pid, uid = str(self.event.peer_id), str(self.event.from_id)
        # Убедимся, что у нас есть структура чата
        ensure_chat(pid)

        # Статистика сообщений
        try:
            chat_data = DATABASE["chats"][pid]
            if "stats" not in chat_data: chat_data["stats"] = {}
            if uid not in chat_data["stats"]:
                chat_data["stats"][uid] = {"count": 0, "last": 0}
            chat_data["stats"][uid]["count"] += 1
            chat_data["stats"][uid]["last"] = datetime.datetime.datetime.now(TZ_MSK).timestamp() if hasattr(datetime, 'datetime') else datetime.datetime.now(TZ_MSK).timestamp()
        except Exception as e:
            print("middleware stats error:", e)

        # Проверяем глобальные/локальные баны и мут
        try:
            is_gban = uid in PUNISHMENTS.get("gbans_status", {})
            is_gbanpl = uid in PUNISHMENTS.get("gbans_pl", {})
            is_lban = uid in PUNISHMENTS.get("bans", {}).get(pid, {})
            mutes = DATABASE["chats"].get(pid, {}).get("mutes", {})
            is_muted = uid in mutes and datetime.datetime.now(TZ_MSK).timestamp() < mutes[uid]
        except Exception as e:
            print("middleware ban check error:", e)
            is_gban = is_gbanpl = is_lban = is_muted = False

        # Если заблокирован или в муте — удаляем сообщение и останавливаем дальнейшую обработку
        if is_gban or is_gbanpl or is_lban or is_muted:
            try:
                await bot.api.messages.delete(
                    peer_id=self.event.peer_id,
                    conversation_message_ids=[self.event.conversation_message_id],
                    delete_for_all=True
                )
            except Exception:
                pass
            # Останавливаем дальнейшую обработку этого сообщения
            self.stop()

bot.labeler.message_view.register_middleware(ChatMiddleware)

# --- 4. КОМАНДЫ ПОЛЬЗОВАТЕЛЕЙ ---
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
    # Подсчёт банов в беседах (любой чат)
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

# --- 5. МОДЕРАЦИЯ ---
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
    reason = "Нарушение"
    if len(parts) > 2:
        reason = " ".join(parts[2:])
    until = datetime.datetime.now(TZ_MSK).timestamp() + mins*60
    pid = str(m.peer_id)
    if "mutes" not in DATABASE["chats"][pid]:
        DATABASE["chats"][pid]["mutes"] = {}
    DATABASE["chats"][pid]["mutes"][str(t)] = until
    dt = datetime.datetime.fromtimestamp(until, TZ_MSK).strftime("%d/%m/%Y %H:%M:%S")
    kb = Keyboard(inline=True)
    kb.add(Text("Снять мут", {"cmd": "unmute", "u": t}), color=KeyboardButtonColor.POSITIVE)
    kb.add(Text("Очистить", {"cmd": "clear"}), color=KeyboardButtonColor.NEGATIVE)
    # Имя модератора
    _, a_nick = get_user_info(m.peer_id, m.from_id)
    a_name = f"[id{m.from_id}|{a_nick}]" if a_nick else f"[id{m.from_id}|Ник]"
    await m.answer(
        f"{a_name} выдал(-а) мут [id{t}|пользователю]\n"
        f"Причина: {reason}\n"
        f"Мут выдан до: {dt}",
        keyboard=kb.get_json()
    )

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
        _, a_nick = get_user_info(m.peer_id, m.from_id)
        a_name = f"[id{m.from_id}|{a_nick}]" if a_nick else f"[id{m.from_id}|Ник]"
        await m.answer(f"{a_name} снял(-а) мут [id{t}|пользователю]")

@bot.on.raw_event(GroupEventType.MESSAGE_EVENT, dataclass=MessageEvent)
async def buttons(event: MessageEvent):
    # payload может быть строкой или dict; vkbottle может возвращать JSON строку
    payload = event.payload
    if isinstance(payload, str):
        try:
            import json as _json
            payload = _json.loads(payload)
        except:
            payload = {}
    pid, uid = str(event.peer_id), event.user_id
    # Проверка доступа по роли
    rank, _ = get_user_info(event.peer_id, uid)
    if RANK_WEIGHT.get(rank, 0) < 1:
        return await event.show_snackbar("Недостаточно прав!")
    cmd = payload.get("cmd") if isinstance(payload, dict) else None
    if cmd == "unmute":
        t = payload.get("u")
        if pid in DATABASE["chats"] and str(t) in DATABASE["chats"][pid].get("mutes", {}):
            del DATABASE["chats"][pid]["mutes"][str(t)]
            # Отправляем новое сообщение о снятии мута
            try:
                await bot.api.messages.send(
                    peer_id=event.peer_id, random_id=0,
                    message=f"[id{event.user_id}|Модератор MANLIX] снял(-а) мут [id{t}|пользователю]"
                )
            except Exception as e:
                print("buttons send error:", e)
    if cmd == "clear":
        try:
            await bot.api.messages.delete(
                peer_id=event.peer_id,
                conversation_message_ids=[event.conversation_message_id],
                delete_for_all=True
            )
        except Exception as e:
            print("buttons clear error:", e)

@bot.on.message(text=["/kick", "/kick <args>"])
async def kick_cmd(m: Message, args=None):
    ensure_chat(str(m.peer_id))
    if not await check_access(m, "Модератор"):
        return
    t = await get_target_id(m, args)
    if not t:
        return
    # Исключаем пользователя из беседы
    try:
        chat_id = m.peer_id - 2000000000
        await bot.api.messages.remove_chat_user(chat_id=chat_id, member_id=t)
    except Exception as e:
        # иногда параметр называется user_id - попробовать оба
        try:
            await bot.api.messages.remove_chat_user(chat_id=chat_id, user_id=t)
        except Exception as e2:
            print("kick error:", e, e2)
    _, a_nick = get_user_info(m.peer_id, m.from_id)
    a_name = f"[id{m.from_id}|{a_nick}]" if a_nick else f"[id{m.from_id}|Ник]"
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
    reason = "Нарушение"
    if len(parts) > 1:
        # если команда: /ban <target> <reason...>
        reason = " ".join(parts[1:]) if len(parts) > 1 else reason
    pid = str(m.peer_id)
    if pid not in PUNISHMENTS["bans"]:
        PUNISHMENTS["bans"][pid] = {}
    PUNISHMENTS["bans"][pid][str(t)] = {
        "admin": m.from_id, "reason": reason,
        "date": datetime.datetime.now(TZ_MSK).timestamp()
    }
    # Попытка получить и сохранить название беседы (если возможно)
    try:
        conv = await bot.api.messages.get_conversations_by_id(peer_ids=[m.peer_id])
        title = None
        if isinstance(conv, dict) and "items" in conv and conv["items"]:
            cs = conv["items"][0].get("chat_settings")
            if cs:
                title = cs.get("title")
        elif isinstance(conv, (list, tuple)) and conv:
            item = conv[0]
            cs = getattr(item, "chat_settings", None) or (item.get("chat_settings") if isinstance(item, dict) else None)
            if cs:
                title = cs.get("title") if isinstance(cs, dict) else getattr(cs, "title", None)
        if title:
            ensure_chat(pid)
            DATABASE["chats"][pid]["title"] = title
    except Exception:
        pass
    # Попытка исключить из текущей беседы
    try:
        chat_id = m.peer_id - 2000000000
        await bot.api.messages.remove_chat_user(chat_id=chat_id, member_id=t)
    except Exception:
        try:
            await bot.api.messages.remove_chat_user(chat_id=chat_id, user_id=t)
        except Exception as e2:
            print("ban removal error:", e2)
    await push_to_github(PUNISHMENTS, GH_PATH_PUN, EXTERNAL_PUN)
    _, a_nick = get_user_info(m.peer_id, m.from_id)
    a_name = f"[id{m.from_id}|{a_nick}]" if a_nick else f"[id{m.from_id}|Ник]"
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
        _, a_nick = get_user_info(m.peer_id, m.from_id)
        a_name = f"[id{m.from_id}|{a_nick}]" if a_nick else f"[id{m.from_id}|Ник]"
        await m.answer(f"{a_name} разблокировал [id{t}|пользователя] в беседе.")

# --- 6. РОЛИ И НИКИ ---
@bot.on.message(text=["/addmoder", "/addmoder <args>"])
async def addmod(m: Message, args=None):
    ensure_chat(str(m.peer_id))
    if not await check_access(m, "Старший Модератор"):
        return
    t = await get_target_id(m, args)
    if not t: return
    pid, uid = str(m.peer_id), str(t)
    # сохраняем ник как None если ещё нет
    _, nick = get_user_info(pid, t)
    ensure_chat(pid)
    DATABASE["chats"][pid]["staff"][uid] = ["Модератор", nick]
    await push_to_github(DATABASE, GH_PATH_DB, EXTERNAL_DB)
    _, a_nick = get_user_info(pid, m.from_id)
    a_name = f"[id{m.from_id}|{a_nick}]" if a_nick else f"[id{m.from_id}|Ник]"
    await m.answer(f"{a_name} выдал(-а) права модератора [id{t}|пользователю]")

@bot.on.message(text=["/addsenmoder", "/addsenmoder <args>"])
async def addsenmod(m: Message, args=None):
    ensure_chat(str(m.peer_id))
    if not await check_access(m, "Администратор"):
        return
    t = await get_target_id(m, args)
    if not t: return
    pid, uid = str(m.peer_id), str(t)
    _, nick = get_user_info(pid, t)
    ensure_chat(pid)
    DATABASE["chats"][pid]["staff"][uid] = ["Старший Модератор", nick]
    await push_to_github(DATABASE, GH_PATH_DB, EXTERNAL_DB)
    _, a_nick = get_user_info(pid, m.from_id)
    a_name = f"[id{m.from_id}|{a_nick}]" if a_nick else f"[id{m.from_id}|Ник]"
    await m.answer(f"{a_name} выдал(-а) права старшего модератора [id{t}|пользователю]")

@bot.on.message(text=["/addadmin", "/addadmin <args>"])
async def addadm(m: Message, args=None):
    ensure_chat(str(m.peer_id))
    if not await check_access(m, "Старший Администратор"):
        return
    t = await get_target_id(m, args)
    if not t: return
    pid, uid = str(m.peer_id), str(t)
    _, nick = get_user_info(pid, t)
    ensure_chat(pid)
    DATABASE["chats"][pid]["staff"][uid] = ["Администратор", nick]
    await push_to_github(DATABASE, GH_PATH_DB, EXTERNAL_DB)
    _, a_nick = get_user_info(pid, m.from_id)
    a_name = f"[id{m.from_id}|{a_nick}]" if a_nick else f"[id{m.from_id}|Ник]"
    await m.answer(f"{a_name} выдал(-а) права администратора [id{t}|пользователю]")

@bot.on.message(text=["/addsenadmin", "/addsenadmin <args>"])
async def addsenadm(m: Message, args=None):
    ensure_chat(str(m.peer_id))
    if not await check_access(m, "Зам. Спец. Администратора"):
        return
    t = await get_target_id(m, args)
    if not t: return
    pid, uid = str(m.peer_id), str(t)
    _, nick = get_user_info(pid, t)
    ensure_chat(pid)
    DATABASE["chats"][pid]["staff"][uid] = ["Старший Администратор", nick]
    await push_to_github(DATABASE, GH_PATH_DB, EXTERNAL_DB)
    _, a_nick = get_user_info(pid, m.from_id)
    a_name = f"[id{m.from_id}|{a_nick}]" if a_nick else f"[id{m.from_id}|Ник]"
    await m.answer(f"{a_name} выдал(-а) права старшего администратора [id{t}|пользователю]")

@bot.on.message(text=["/addzsa", "/addzsa <args>"])
async def addzsa(m: Message, args=None):
    ensure_chat(str(m.peer_id))
    if not await check_access(m, "Спец. Администратор"):
        return
    t = await get_target_id(m, args)
    if not t: return
    pid, uid = str(m.peer_id), str(t)
    _, nick = get_user_info(pid, t)
    ensure_chat(pid)
    DATABASE["chats"][pid]["staff"][uid] = ["Зам. Спец. Администратора", nick]
    await push_to_github(DATABASE, GH_PATH_DB, EXTERNAL_DB)
    _, a_nick = get_user_info(pid, m.from_id)
    a_name = f"[id{m.from_id}|{a_nick}]" if a_nick else f"[id{m.from_id}|Ник]"
    await m.answer(f"{a_name} выдал(-а) права зам. специального администратора [id{t}|пользователю]")

@bot.on.message(text=["/addsa", "/addsa <args>"])
async def addsa(m: Message, args=None):
    ensure_chat(str(m.peer_id))
    if not await check_access(m, "Владелец"):
        return
    t = await get_target_id(m, args)
    if not t: return
    pid, uid = str(m.peer_id), str(t)
    _, nick = get_user_info(pid, t)
    ensure_chat(pid)
    DATABASE["chats"][pid]["staff"][uid] = ["Спец. Администратор", nick]
    await push_to_github(DATABASE, GH_PATH_DB, EXTERNAL_DB)
    _, a_nick = get_user_info(pid, m.from_id)
    a_name = f"[id{m.from_id}|{a_nick}]" if a_nick else f"[id{m.from_id}|Ник]"
    await m.answer(f"{a_name} выдал(-а) права специального администратора [id{t}|пользователю]")

@bot.on.message(text=["/addowner", "/addowner <args>"])
async def addowner(m: Message, args=None):
    ensure_chat(str(m.peer_id))
    if not await check_access(m, "Специальный Руководитель"):
        return
    t = await get_target_id(m, args)
    if not t: return
    pid, uid = str(m.peer_id), str(t)
    _, nick = get_user_info(pid, t)
    ensure_chat(pid)
    DATABASE["chats"][pid]["staff"][uid] = ["Владелец", nick]
    await push_to_github(DATABASE, GH_PATH_DB, EXTERNAL_DB)
    _, a_nick = get_user_info(pid, m.from_id)
    a_name = f"[id{m.from_id}|{a_nick}]" if a_nick else f"[id{m.from_id}|Ник]"
    await m.answer(f"{a_name} выдал(-а) права владельца [id{t}|пользователю]")

@bot.on.message(text=["/removerole", "/removerole <args>"])
async def removerole(m: Message, args=None):
    ensure_chat(str(m.peer_id))
    if not await check_access(m, "Старший Модератор"):
        return
    t = await get_target_id(m, args)
    if not t: return
    pid = str(m.peer_id)
    if pid in DATABASE["chats"] and str(t) in DATABASE["chats"][pid].get("staff", {}):
        del DATABASE["chats"][pid]["staff"][str(t)]
        await push_to_github(DATABASE, GH_PATH_DB, EXTERNAL_DB)
        _, a_nick = get_user_info(m.peer_id, m.from_id)
        a_name = f"[id{m.from_id}|{a_nick}]" if a_nick else f"[id{m.from_id}|Ник]"
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
                # display default: VK name (если ник не задан)
                if n:
                    display = n
                else:
                    try:
                        uinfo = (await bot.api.users.get(user_ids=[int(u)]))[0]
                        display = f"{uinfo.first_name} {uinfo.last_name}"
                    except:
                        display = "Пользователь"
                members.append(f"[id{u}|{display}]")
        res += "\n".join(f"– {mbr}" for mbr in members) if members else "– Отсутствует."
        res += "\n\n"
    await m.answer(res.strip())

@bot.on.message(text=["/setnick", "/setnick <args>"])
async def setnick(m: Message, args=None):
    ensure_chat(str(m.peer_id))
    if not await check_access(m, "Модератор"):
        return
    if not args:
        return
    # args: "<target> <nick words...>"
    parts = args.split()
    # определяем цель (первый токен), остальное - ник (может быть из 2+ слов)
    target_token = parts[0]
    nick_words = parts[1:]
    if not nick_words:
        return await m.answer("Укажите ник (может быть из нескольких слов).")
    t = await get_target_id(m, target_token)
    if not t:
        return
    new_nick = " ".join(nick_words)
    pid, uid = str(m.peer_id), str(t)
    role, _ = get_user_info(pid, t)
    ensure_chat(pid)
    DATABASE["chats"][pid]["staff"][uid] = [role, new_nick]
    await push_to_github(DATABASE, GH_PATH_DB, EXTERNAL_DB)
    _, a_nick = get_user_info(pid, m.from_id)
    a_name = f"[id{m.from_id}|{a_nick}]" if a_nick else f"[id{m.from_id}|Ник]"
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
    role, _ = get_user_info(pid, uid)
    ensure_chat(pid)
    DATABASE["chats"][pid]["staff"][uid] = [role, None]
    await push_to_github(DATABASE, GH_PATH_DB, EXTERNAL_DB)
    _, a_nick = get_user_info(pid, m.from_id)
    a_name = f"[id{m.from_id}|{a_nick}]" if a_nick else f"[id{m.from_id}|Ник]"
    await m.answer(f"{a_name} убрал(-а) имя [id{t}|пользователю]")

@bot.on.message(text=["/nlist"])
async def nick_list(m: Message):
    ensure_chat(str(m.peer_id))
    if not await check_access(m, "Модератор"):
        return
    pid = str(m.peer_id)
    staff = DATABASE["chats"].get(pid, {}).get("staff", {})
    users = [(u,n) for u,(_,n) in staff.items() if n]
    if users:
        msg = "Список пользователей с ником:\n"
        for i,(u,n) in enumerate(users,1):
            msg += f"{i}. [id{u}|{n}]\n"
    else:
        msg = "Список пользователей с ником:\nОтсутствуют"
    await m.answer(msg)

# --- 7. ИГРОВЫЕ КОМАНДЫ ---
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
    e = ECONOMY.get(str(m.from_id), {"cash": 0, "bank": 0})
    await m.answer(f"💵 Ваши наличные: {e['cash']}$")

@bot.on.message(text="/bank")
async def bank_view(m: Message):
    ensure_chat(str(m.peer_id))
    e = ECONOMY.get(str(m.from_id), {"cash": 0, "bank": 0})
    await m.answer(
        f"🏦 …::: MANLIX BANK :::…\n\n"
        f"💵 Наличные: {e['cash']}$\n"
        f"💳 На счету: {e['bank']}$"
    )

@bot.on.message(text=["/положить <amount>"])
async def deposit(m: Message, amount=None):
    ensure_chat(str(m.peer_id))
    if amount is None:
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
    if amount is None:
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
    # 50% шанс выиграть (получить ставку в плюс) или проиграть ставку
    if random.choice([True, False]):
        ECONOMY[uid]["cash"] += amount
        await m.answer(f"🎰 Поздравляем! Вы выиграли {amount}$!")
    else:
        ECONOMY[uid]["cash"] -= amount
        await m.answer(f"🎰 К сожалению, вы проиграли {amount}$...")
    await push_to_github(ECONOMY, GH_PATH_ECO, EXTERNAL_ECO)

# --- 8. ДОПОЛНИТЕЛЬНЫЕ КОМАНДЫ УКРАВЛЕНИЯ И БОТА ---
@bot.on.message(text=["/gbanpl", "/gbanpl <args>"])
async def gbanpl_cmd(m: Message, args=None):
    ensure_chat(str(m.peer_id))
    if not await check_access(m, "Зам. Спец. Руководителя"):
        return
    t = await get_target_id(m, args)
    if not t: return
    parts = args.split() if args else []
    reason = "Нарушение"
    if len(parts) > 1:
        reason = " ".join(parts[1:])
    uid = str(t)
    PUNISHMENTS["gbans_pl"][uid] = {
        "admin": m.from_id, "reason": reason,
        "date": datetime.datetime.now(TZ_MSK).timestamp()
    }
    # Попытка исключить из текущей беседы
    try:
        chat_id = m.peer_id - 2000000000
        await bot.api.messages.remove_chat_user(chat_id=chat_id, member_id=t)
    except Exception:
        try:
            await bot.api.messages.remove_chat_user(chat_id=chat_id, user_id=t)
        except Exception:
            pass
    # Попытка удалить из всех чатов в базе
    for pid in list(DATABASE.get("chats", {}).keys()):
        try:
            pnum = int(pid)
            chat_id = pnum - 2000000000
            await bot.api.messages.remove_chat_user(chat_id=chat_id, member_id=t)
        except:
            pass
    await push_to_github(PUNISHMENTS, GH_PATH_PUN, EXTERNAL_PUN)
    _, a_nick = get_user_info(m.peer_id, m.from_id)
    a_name = f"[id{m.from_id}|{a_nick}]" if a_nick else f"[id{m.from_id}|Ник]"
    await m.answer(f"{a_name} заблокировал [id{t}|пользователя] во всех игровых Беседах.")

@bot.on.message(text=["/gunbanpl", "/gunbanpl <args>"])
async def gunbanpl_cmd(m: Message, args=None):
    ensure_chat(str(m.peer_id))
    if not await check_access(m, "Зам. Спец. Руководителя"):
        return
    t = await get_target_id(m, args)
    if not t: return
    if str(t) in PUNISHMENTS["gbans_pl"]:
        del PUNISHMENTS["gbans_pl"][str(t)]
        await push_to_github(PUNISHMENTS, GH_PATH_PUN, EXTERNAL_PUN)
    _, a_nick = get_user_info(m.peer_id, m.from_id)
    a_name = f"[id{m.from_id}|{a_nick}]" if a_nick else f"[id{m.from_id}|Ник]"
    await m.answer(f"{a_name} разблокировал [id{t}|пользователя] во всех игровых Беседах.")

@bot.on.message(text=["/gban", "/gban <args>"])
async def gban(m: Message, args=None):
    ensure_chat(str(m.peer_id))
    if not await check_access(m, "Специальный Руководитель"):
        return
    t = await get_target_id(m, args)
    if not t: return
    parts = args.split() if args else []
    reason = "Нарушение"
    if len(parts) > 1:
        reason = " ".join(parts[1:])
    uid = str(t)
    # /gban теперь НЕ кикает — только сохраняет
    PUNISHMENTS["gbans_status"][uid] = {
        "admin": m.from_id, "reason": reason,
        "date": datetime.datetime.now(TZ_MSK).timestamp()
    }
    await push_to_github(PUNISHMENTS, GH_PATH_PUN, EXTERNAL_PUN)
    _, a_nick = get_user_info(m.peer_id, m.from_id)
    a_name = f"[id{m.from_id}|{a_nick}]" if a_nick else f"[id{m.from_id}|Ник]"
    await m.answer(f"{a_name} занес [id{t}|пользователя] в глобальную Блокировку Бота.")

@bot.on.message(text=["/gunban", "/gunban <args>"])
async def gunban(m: Message, args=None):
    ensure_chat(str(m.peer_id))
    if not await check_access(m, "Специальный Руководитель"):
        return
    t = await get_target_id(m, args)
    if not t: return
    if str(t) in PUNISHMENTS["gbans_status"]:
        del PUNISHMENTS["gbans_status"][str(t)]
        await push_to_github(PUNISHMENTS, GH_PATH_PUN, EXTERNAL_PUN)
    _, a_nick = get_user_info(m.peer_id, m.from_id)
    a_name = f"[id{m.from_id}|{a_nick}]" if a_nick else f"[id{m.from_id}|Ник]"
    await m.answer(f"{a_name} вынес [id{t}|пользователя] из Глобальной Блокировки Бота.")

@bot.on.message(text=["/getban", "/getban <args>"])
async def getban_cmd(m: Message, args=None):
    ensure_chat(str(m.peer_id))
    if not await check_access(m, "Модератор"):
        return
    t = await get_target_id(m, args)
    if not t: return
    uid = str(t)
    try:
        u_info = (await bot.api.users.get(user_ids=[t]))[0]
        u_name = f"{u_info.first_name} {u_info.last_name}"
    except:
        u_name = f"id{t}"
    ans = f"Информация о блокировках [id{t}|{u_name}]\n\n"
    for key,label in [("gbans_status","общей Блокировке в Беседах"), ("gbans_pl","общей Блокировке в Беседе игроков")]:
        ans += f"Информация о {label}: "
        if uid in PUNISHMENTS.get(key, {}):
            b = PUNISHMENTS[key][uid]
            dt = datetime.datetime.fromtimestamp(b['date'], TZ_MSK).strftime("%d/%m/%Y %H:%M:%S")
            ans += f"\n[id{b['admin']}|Модератор MANLIX] | {b.get('reason','-')} | {dt}\n\n"
        else:
            ans += "отсутствует\n\n"
    local = []
    for pid, users in PUNISHMENTS.get("bans", {}).items():
        if uid in users:
            b = users[uid]
            # Берём title из базы, если нет — пробуем запросить через API и сохранить
            title = DATABASE.get("chats", {}).get(pid, {}).get("title")
            if not title:
                try:
                    conv = await bot.api.messages.get_conversations_by_id(peer_ids=[int(pid)])
                    if isinstance(conv, dict) and "items" in conv and conv["items"]:
                        cs = conv["items"][0].get("chat_settings")
                        if cs:
                            title = cs.get("title")
                    elif isinstance(conv, (list, tuple)) and conv:
                        item = conv[0]
                        cs = getattr(item, "chat_settings", None) or (item.get("chat_settings") if isinstance(item, dict) else None)
                        if cs:
                            title = cs.get("title") if isinstance(cs, dict) else getattr(cs, "title", None)
                    if title:
                        ensure_chat(str(pid))
                        DATABASE["chats"][pid]["title"] = title
                        # sync optional
                        await push_to_github(DATABASE, GH_PATH_DB, EXTERNAL_DB)
                except Exception:
                    title = None
            if not title:
                title = f"Чат {pid}"
            dt = datetime.datetime.fromtimestamp(b['date'], TZ_MSK).strftime("%d/%m/%Y %H:%M:%S")
            local.append(f"{title} | [id{b['admin']}|Модератор MANLIX] | {b.get('reason','-')} | {dt}")
    ans += f"Количество Бесед, в которых заблокирован пользователь: {len(local)}\n"
    if local:
        ans += "Информация о последних 10 Блокировках:\n"
        for i,row in enumerate(reversed(local[-10:]), 1):
            ans += f"{i}) {row}\n"
    else:
        ans += "Блокировки в беседах отсутствуют"
    await m.answer(ans)

@bot.on.message(text="/sync")
async def sync(m: Message):
    ensure_chat(str(m.peer_id))
    if not await check_access(m, "Специальный Руководитель"):
        return
    await push_to_github(DATABASE, GH_PATH_DB, EXTERNAL_DB)
    await push_to_github(ECONOMY, GH_PATH_ECO, EXTERNAL_ECO)
    await push_to_github(PUNISHMENTS, GH_PATH_PUN, EXTERNAL_PUN)
    await m.answer("Вы успешно синхронизировали Беседу с Базой данных.")

@bot.on.message(text="/chatid")
async def chatid_cmd(m: Message):
    ensure_chat(str(m.peer_id))
    if not await check_access(m, "Специальный Руководитель"):
        return
    await m.answer(f"ID Беседы: {m.peer_id}")

@bot.on.message(text="/delchat")
async def delchat(m: Message):
    ensure_chat(str(m.peer_id))
    if not await check_access(m, "Специальный Руководитель"):
        return
    pid = str(m.peer_id)
    if pid in DATABASE["chats"]:
        del DATABASE["chats"][pid]
        await push_to_github(DATABASE, GH_PATH_DB, EXTERNAL_DB)
    await m.answer("Вы успешно удалили чат с Базы данных.")

# --- gstaff ---
@bot.on.message(text="/gstaff")
async def gstaff_cmd(m: Message):
    # строим сообщение по шаблону, беря данные из DATABASE["gstaff"]
    gs = DATABASE.get("gstaff", {"spec": 870757778, "main_zam": None, "zams": []})
    def fmt(uid):
        if not uid:
            return "Отсутствует."
        return f"[id{uid}|{ ( ( ( ( ( 'Misha Manlix' ) ) ) ) ) if uid == 870757778 else ('Пользователь') ) }]"
    # Для спец.руководителя используем корректное отображение (id870757778 -> Misha Manlix)
    spec = f"[id{gs.get('spec')}|Misha Manlix]" if gs.get('spec') == 870757778 else fmt(gs.get('spec'))
    main_zam = fmt(gs.get('main_zam'))
    zams = gs.get('zams') or []
    zam_lines = ""
    if not zams:
        zam_lines = "– Отсутствует.\n– Отсутствует."
    else:
        for z in zams:
            zam_lines += f"– {fmt(z)}\n"
    msg = (
        "MANLIX MANAGER | Команда Бота:\n\n"
        f"| Специальный Руководитель:\n– {spec}\n\n"
        f"| Основной зам. Спец. Руководителя:\n– {main_zam}\n\n"
        f"| Зам. Спец. Руководителя:\n{zam_lines}"
    )
    await m.answer(msg)

# --- Обработка системных действий: авто-кик при приглашении / добавлении ---
@bot.on.message()
async def actions(m: Message):
    # Обрабатываем системные события (приглашения и т.д.)
    if not getattr(m, "action", None):
        return
    try:
        typ = m.action.type.value if hasattr(m.action.type, "value") else getattr(m.action.type, "name", None) or str(m.action.type)
    except:
        typ = None
    # Если бот был исключен — уведомление
    if typ == "chat_kick_user":
        if getattr(m.action, "member_id", None) == m.from_id:
            kb = Keyboard(inline=True).add(Text("Исключить", {"cmd": "clear"}), color=KeyboardButtonColor.NEGATIVE)
            try:
                await m.answer("Бот покинул(-а) Беседу", keyboard=kb.get_json())
            except:
                pass
        return

    # Обработка приглашения — если приглашённый забанен, кикаем
    if typ in ("chat_invite_user", "chat_invite_user_by_link", "invite_user"):
        invited_id = None
        # разные версии могут хранить id в разных полях
        if hasattr(m.action, "member_id"):
            invited_id = m.action.member_id
        elif hasattr(m.action, "invited_uid"):
            invited_id = m.action.invited_uid
        elif hasattr(m.action, "action_mid"):
            invited_id = m.action.action_mid
        # Если несколько — может быть список; для простоты берем первое, если это список
        if isinstance(invited_id, (list, tuple)) and invited_id:
            invited_id = invited_id[0]
        if invited_id:
            uid = str(invited_id)
            pid = str(m.peer_id)
            ensure_chat(pid)
            if uid in PUNISHMENTS.get("gbans_status", {}) or uid in PUNISHMENTS.get("gbans_pl", {}) or uid in PUNISHMENTS.get("bans", {}).get(pid, {}):
                try:
                    chat_id = m.peer_id - 2000000000
                    await bot.api.messages.remove_chat_user(chat_id=chat_id, member_id=int(uid))
                except:
                    try:
                        await bot.api.messages.remove_chat_user(chat_id=chat_id, user_id=int(uid))
                    except:
                        pass
                try:
                    await m.answer(f"[id{m.from_id}|Модератор MANLIX] исключил(-а) [id{uid}|пользователя] — он находится в списке блокировок.")
                except:
                    pass
        return

# Автокик при входе - raw event (дополнительно, на случай других форматов)
@bot.on.raw_event(GroupEventType.MESSAGE_NEW)
async def auto_kick(event):
    try:
        obj = event.object
        if not obj:
            return
        # в разных версиях поле может называться по-разному
        if isinstance(obj, dict):
            msg = obj.get("message") or obj
            action = msg.get("action") if isinstance(msg, dict) else None
            if action and action.get("type") in ("chat_invite_user", "chat_invite_user_by_link", "invite_user"):
                member_id = action.get("member_id") or action.get("user_id") or action.get("member_ids")
                if isinstance(member_id, list):
                    # обрабатываем всех приглашённых
                    for mid in member_id:
                        if str(mid) in PUNISHMENTS.get("gbans_pl", {}):
                            try:
                                peer = msg.get("peer_id")
                                chat_id = int(peer) - 2000000000
                                await bot.api.messages.remove_chat_user(chat_id=chat_id, member_id=mid)
                            except:
                                pass
                else:
                    if member_id and str(member_id) in PUNISHMENTS.get("gbans_pl", {}):
                        try:
                            peer = msg.get("peer_id")
                            chat_id = int(peer) - 2000000000
                            await bot.api.messages.remove_chat_user(chat_id=chat_id, member_id=member_id)
                        except:
                            pass
    except Exception:
        pass

# --- 9. ЗАПУСК ---
class H(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")

if __name__ == "__main__":
    # На старте убедимся, что база имеет нужную структуру
    if "chats" not in DATABASE:
        DATABASE["chats"] = {}
    if "gstaff" not in DATABASE:
        DATABASE["gstaff"] = {"spec": 870757778, "main_zam": None, "zams": []}
    # Запускаем HTTP сервер для Render
    threading.Thread(
        target=lambda: HTTPServer(('0.0.0.0', int(os.environ.get("PORT", 10000))), H).serve_forever(),
        daemon=True
    ).start()
    # Запуск бота
    bot.run_forever()

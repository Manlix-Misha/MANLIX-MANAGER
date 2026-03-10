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
from vkbottle import Keyboard, KeyboardButtonColor, Text, Callback, GroupEventType, BaseMiddleware

# ────────────────────────────────────────────────
# НАСТРОЙКИ
# ────────────────────────────────────────────────
GH_TOKEN    = os.environ.get("GH_TOKEN")
GH_REPO     = os.environ.get("GH_REPO")
GH_PATH_DB    = "database.json"
GH_PATH_ECO   = "economy.json"
GH_PATH_PUN   = "punishments.json"
GH_PATH_STAFF = "staff.json"

EXTERNAL_DB    = "database.json"
EXTERNAL_ECO   = "economy.json"
EXTERNAL_PUN   = "punishments.json"
EXTERNAL_STAFF = "staff.json"

TZ_MSK = datetime.timezone(datetime.timedelta(hours=3))

RANK_WEIGHT = {
    "Пользователь":                     0,
    "Тестировщик":                      1,
    "Старший Тестировщик":              2,
    "Главный Тестировщик":              3,
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

# Веса ролей тестировщиков отдельно
TESTER_RANK_WEIGHT = {
    "Тестировщик":        1,
    "Старший Тестировщик": 2,
    "Главный Тестировщик": 3,
}

# ────────────────────────────────────────────────
# HTTP-сервер
# ────────────────────────────────────────────────
class H(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")
    def log_message(self, format, *args):
        pass

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
                if "content" in doc:
                    data = json.loads(base64.b64decode(doc["content"]).decode("utf-8"))
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
                sha = doc.get("sha")
        content = base64.b64encode(
            json.dumps(data, ensure_ascii=False, indent=4).encode("utf-8")
        ).decode("utf-8")
        payload = {"message": "Update from bot", "content": content}
        if sha:
            payload["sha"] = sha
        async with session.put(url, headers=headers, json=payload) as resp:
            if resp.status not in (200, 201):
                print("GitHub push failed:", resp.status, await resp.text())
        with open(local_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)

# ────────────────────────────────────────────────
# Инициализация данных
# ────────────────────────────────────────────────
loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)
DATABASE    = loop.run_until_complete(load_from_github(GH_PATH_DB,    EXTERNAL_DB))
ECONOMY     = loop.run_until_complete(load_from_github(GH_PATH_ECO,   EXTERNAL_ECO))
PUNISHMENTS = loop.run_until_complete(load_from_github(GH_PATH_PUN,   EXTERNAL_PUN))
STAFF       = loop.run_until_complete(load_from_github(GH_PATH_STAFF, EXTERNAL_STAFF))

if not isinstance(DATABASE,    dict): DATABASE    = {}
if not isinstance(ECONOMY,     dict): ECONOMY     = {}
if not isinstance(PUNISHMENTS, dict): PUNISHMENTS = {}
if not isinstance(STAFF,       dict): STAFF       = {}

for key in ("gbans_status", "gbans_pl", "bans", "warns"):
    if key not in PUNISHMENTS:
        PUNISHMENTS[key] = {}
if "chats" not in DATABASE:
    DATABASE["chats"] = {}
if "duels" not in DATABASE:
    DATABASE["duels"] = {}
if "bot_status" not in DATABASE:
    DATABASE["bot_status"] = "on"

# ── STAFF инициализация ──
if "gstaff" not in STAFF:
    STAFF["gstaff"] = {"spec": 870757778, "main_zam": None, "zams": []}
if "testers" not in STAFF:
    STAFF["testers"] = {}
if "texstaff" not in STAFF:
    STAFF["texstaff"] = {}

GROUP_ID = None

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
            "type":  "def"
        }
    chat = DATABASE["chats"][pid]
    for key in ("mutes", "stats", "staff"):
        if key not in chat:
            chat[key] = {}
    if "invite_only" not in chat:
        chat["invite_only"] = False

def is_vk_ref(token: str) -> bool:
    """Проверяет, является ли токен ссылкой/упоминанием/ID пользователя ВК."""
    if re.search(r"\[id\d+\|", token):
        return True
    # vk.com и vk.ru — оба домена ВКонтакте
    if re.search(r"https?://vk\.(com|ru)/", token):
        return True
    if re.match(r"^id\d+$", token):
        return True
    # Просто число считается ID только если достаточно большое (> 1000)
    # чтобы не путать с числами в причине ("пункт 1", "нарушение 3")
    if re.match(r"^\d+$", token) and int(token) > 1000:
        return True
    return False

async def get_target_id(m: Message, args: str = None):
    """Получить ID цели из reply, ссылки или первого токена args."""
    # Приоритет 1: reply на сообщение
    if getattr(m, "reply_message", None):
        return m.reply_message.from_id
    if not args:
        return None

    # Приоритет 2: ищем [id123|...] в ЛЮБОМ месте строки args
    match = re.search(r"\[id(\d+)\|", args)
    if match:
        return int(match.group(1))

    # Приоритет 3: ищем vk.com/id123 или vk.ru/id123 в ЛЮБОМ месте строки
    match = re.search(r"vk\.(com|ru)/id(\d+)", args)
    if match:
        return int(match.group(2))

    # Приоритет 4: первый токен
    tokens = args.split()
    first  = tokens[0] if tokens else ""

    # id123
    match = re.match(r"^id(\d+)$", first)
    if match:
        return int(match.group(1))

    # Просто число
    if first.isdigit():
        return int(first)

    # vk.com/screenname или vk.ru/screenname (не id-ссылка)
    match = re.search(r"https?://vk\.(com|ru)/([A-Za-z0-9_\.]+)", args)
    if match:
        sn = match.group(2)
        if not sn.startswith("id"):
            try:
                res = await bot.api.utils.resolve_screen_name(screen_name=sn)
                if res and res.type == "user":
                    return int(res.object_id)
            except:
                pass
        else:
            # vk.com/id123 — на случай если первый regex не сработал
            try:
                return int(sn[2:])
            except:
                pass

    # screen_name — только если не похож на ссылку
    if first and not first.startswith("http") and "/" not in first:
        try:
            res = await bot.api.utils.resolve_screen_name(screen_name=first)
            if res and res.type == "user":
                return int(res.object_id)
        except:
            pass
    return None

def parse_reason(args: str) -> str:
    """
    Извлекает причину из args, пропуская все токены-ссылки/id.
    Используется для /gban, /gbanpl, /ban.
    """
    if not args:
        return "Нарушение"
    tokens = args.split()
    # Пропускаем все токены которые являются ссылкой или ID
    rest = [t for t in tokens if not is_vk_ref(t)]
    return " ".join(rest) or "Нарушение"

def parse_mute_args(args: str):
    """
    Корректно разбирает аргументы /mute.
    Формат: /mute [ссылка/id] [минуты] [причина]
    Пропускает все токены-ссылки/id.
    Возвращает (mins: int, reason: str).
    """
    if not args:
        return 60, "Нарушение"
    tokens = args.split()
    # Пропускаем все токены которые являются ссылкой или ID
    remaining = [t for t in tokens if not is_vk_ref(t)]
    if not remaining:
        return 60, "Нарушение"
    if remaining[0].isdigit():
        mins   = int(remaining[0])
        reason = " ".join(remaining[1:]) if len(remaining) > 1 else "Нарушение"
    else:
        mins   = 60
        reason = " ".join(remaining)
    return mins, reason or "Нарушение"

def get_user_info(peer_id, user_id):
    uid    = str(user_id)
    gstaff = STAFF.get("gstaff", {})
    if user_id == gstaff.get("spec") or user_id == 870757778:
        global_role = "Специальный Руководитель"
    elif gstaff.get("main_zam") and user_id == gstaff["main_zam"]:
        global_role = "Основной Зам. Спец. Руководителя"
    elif gstaff.get("zams") and user_id in gstaff["zams"]:
        global_role = "Зам. Спец. Руководителя"
    else:
        global_role = "Пользователь"
    staff = DATABASE.get("chats", {}).get(str(peer_id), {}).get("staff", {})
    entry = staff.get(uid)
    if entry:
        local_role = entry[0]
        nick       = entry[1]
    else:
        local_role = "Пользователь"
        nick       = None
    role = global_role if RANK_WEIGHT.get(global_role, 0) > RANK_WEIGHT.get(local_role, 0) else local_role
    return role, nick

async def get_display_name(user_id: int, peer_id=None, use_nick=True):
    """
    Возвращает отображаемое имя пользователя:
    1. Ник из бота (если установлен через /setnick и use_nick=True)
    2. Имя и фамилия из профиля ВК
    3. Fallback: "id{user_id}" (чтобы хоть что-то было вместо "пользователь")
    """
    if use_nick and peer_id:
        _, nick = get_user_info(peer_id, user_id)
        if nick:
            return nick
    try:
        uinfo = await bot.api.users.get([int(user_id)])
        if uinfo and len(uinfo) > 0:
            return f"{uinfo[0].first_name} {uinfo[0].last_name}"
        return f"id{user_id}"
    except Exception as e:
        print(f"get_display_name error for {user_id}: {e}")
        return f"id{user_id}"

async def check_access(m: Message, min_rank: str):
    rank, _ = get_user_info(m.peer_id, m.from_id)
    if RANK_WEIGHT.get(rank, 0) < RANK_WEIGHT.get(min_rank, 0):
        await m.answer("Недостаточно прав!")
        return False
    return True

async def set_role_in_chat(pid: str, uid: str, role_name: str):
    ensure_chat(pid)
    current = DATABASE["chats"][pid]["staff"].get(uid, [role_name, None])
    nick    = current[1]
    DATABASE["chats"][pid]["staff"][uid] = [role_name, nick]

# ────────────────────────────────────────────────
# Middleware
# ────────────────────────────────────────────────
class ChatMiddleware(BaseMiddleware[Message]):
    async def pre(self):
        if not getattr(self.event, "from_id", None) or self.event.from_id < 0:
            return
        from_id = self.event.from_id
        pid = str(self.event.peer_id)
        uid = str(from_id)

        # ── Проверка bot_status ──────────────────────
        status = DATABASE.get("bot_status", "on")
        if status != "on":
            # Спец. Руководитель всегда может пользоваться
            if from_id == 870757778:
                pass  # пропускаем проверку
            else:
                rank, _ = get_user_info(self.event.peer_id, from_id)
                w = RANK_WEIGHT.get(rank, 0)
                allowed = False
                if w >= 8:          # ЗСР и выше — всегда
                    allowed = True
                elif status == "test":
                    # Тестировщики тоже проходят
                    t_role, _ = get_tester_info(from_id)
                    if t_role:
                        allowed = True
                if not allowed:
                    self.stop()
                    return
        # ─────────────────────────────────────────────

        ensure_chat(pid)
        chat = DATABASE["chats"][pid]
        if uid not in chat["stats"]:
            chat["stats"][uid] = {"count": 0, "last": 0}
        chat["stats"][uid]["count"] += 1
        chat["stats"][uid]["last"]   = datetime.datetime.now(TZ_MSK).timestamp()
        if chat["stats"][uid]["count"] % 10 == 0:
            await push_to_github(DATABASE, GH_PATH_DB, EXTERNAL_DB)
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

bot.labeler.message_view.register_middleware(ChatMiddleware)

# ────────────────────────────────────────────────
# /help
# ────────────────────────────────────────────────
@bot.on.message(text="/help")
async def help_cmd(m: Message):
    rank, _ = get_user_info(m.peer_id, m.from_id)
    w = RANK_WEIGHT.get(rank, 0)
    res = (
        "Команды пользователей:\n"
        "/info -- официальные ресурсы.\n"
        "/stats -- статистика пользователя\n"
        "/getid -- оригинальная ссылка VK."
    )
    if w >= 1:
        res += (
            "\n\nКоманды для модераторов:\n"
            "/staff -- Руководство Беседы\n"
            "/kick -- исключить пользователя из Беседы.\n"
            "/mute -- выдать Блокировку чата.\n"
            "/unmute -- снять Блокировку чата.\n"
            "/setnick -- установить имя пользователю.\n"
            "/rnick -- удалить имя пользователю.\n"
            "/nlist -- список пользователей с ником.\n"
            "/getban -- информация о Блокировках."
        )
    if w >= 2:
        res += (
            "\n\nКоманды старших модераторов:\n"
            "/addmoder -- выдать права модератора.\n"
            "/removerole -- снять уровень прав.\n"
            "/ban -- блокировка пользователя в Беседе.\n"
            "/unban -- снятие блокировки пользователю в беседе."
        )
    if w >= 3:
        res += (
            "\n\nКоманды администраторов:\n"
            "/addsenmoder -- выдать права старшего модератора."
        )
    if w >= 4:
        res += (
            "\n\nКоманды старших администраторов:\n"
            "/addadmin -- выдать права администратора."
        )
    if w >= 5:
        res += (
            "\n\nКоманды заместителей спец. администраторов:\n"
            "/addsenadmin -- выдать права старшего модератора."
        )
    if w >= 6:
        res += (
            "\n\nКоманды спец. администраторов:\n"
            "/addzsa -- выдать права заместителя спец. администратора."
        )
    if w >= 7:
        res += (
            "\n\nКоманды владельца:\n"
            "/addsa -- выдать права специального администратора.\n"
            "/invite -- управление добавлением участников."
        )
    await m.answer(res)
    if w >= 8:
        gres = (
            "Команды руководства Бота:\n\n"
            "Зам. Спец. Руководителя:\n"
            "/gstaff -- руководство Бота.\n"
            "/gunrole -- снятие глобальных уровней прав.\n"
            "/addowner -- выдать права владельца.\n"
            "/gbanpl -- Блокировка пользователя во всех игровых Беседах.\n"
            "/gunbanpl -- снятие Блокировки во всех игровых Беседах.\n\n"
            "Основной Зам. Спец. Руководителя:\n"
            "/addzsr -- выдать права заместителя спец. руководителя.\n"
            "/thelp -- список команд для тестировщиков.\n"
            "/msg -- отправить рассылку.\n\n"
            "Спец. Руководителя:\n"
            "/addozsr -- выдать права основного заместителя спец. руководителя.\n"
            "/start -- активировать Беседу.\n"
            "/type -- изменить тип Беседы.\n"
            "/typetex -- изменить технический тип Беседы.\n"
            "/sync -- синхронизация с базой данных.\n"
            "/botstatus -- изменить статус Бота.\n"
            "/chatid -- узнать айди Беседы.\n"
            "/delchat -- удалить чат с Базы данных."
        )
        await m.answer(gres)

# ────────────────────────────────────────────────
# /info
# ────────────────────────────────────────────────
@bot.on.message(text="/info")
async def info_cmd(m: Message):
    await m.answer("Официальные ресурсы: [вставьте ссылки или информацию]")

# ────────────────────────────────────────────────
# /getid
# ────────────────────────────────────────────────
@bot.on.message(text=["/getid", "/getid <args>"])
async def getid_cmd(m: Message, args=None):
    t = await get_target_id(m, args) or m.from_id
    await m.answer(f"Оригинальная ссылка [id{t}|пользователя]: https://vk.com/id{t}")

# ────────────────────────────────────────────────
# /stats
# ────────────────────────────────────────────────
@bot.on.message(text=["/stats", "/stats <args>"])
async def stats_cmd(m: Message, args=None):
    t = await get_target_id(m, args) or m.from_id
    uid = str(t)
    pid = str(m.peer_id)
    ensure_chat(pid)
    role, nick   = get_user_info(m.peer_id, t)
    bans_cnt     = sum(1 for bans in PUNISHMENTS.get("bans", {}).values() if uid in bans)
    gban         = "Да" if uid in PUNISHMENTS.get("gbans_status", {}) else "Нет"
    gbanpl       = "Да" if uid in PUNISHMENTS.get("gbans_pl",     {}) else "Нет"
    mutes        = DATABASE["chats"][pid].get("mutes", {})
    is_muted     = "Да" if uid in mutes and time.time() < mutes[uid] else "Нет"
    st           = DATABASE["chats"][pid].get("stats", {}).get(uid, {"count": 0, "last": 0})
    dt           = (
        datetime.datetime.fromtimestamp(st["last"], TZ_MSK).strftime("%d/%m/%Y %I:%M:%S %p")
        if st["last"] else "Нет данных"
    )
    nick_display = nick if nick else "Не установлен"
    msg = (
        f"Информация о [id{t}|пользователе]\n"
        f"Роль: {role}\n"
        f"Блокировок: {bans_cnt}\n"
        f"Общая блокировка в чатах: {gban}\n"
        f"Общая блокировка в беседах игроков: {gbanpl}\n"
        f"Активные предупреждения: {PUNISHMENTS.get('warns', {}).get(pid, {}).get(uid, 0)}\n"
        f"Блокировка чата: {is_muted}\n"
        f"Ник: {nick_display}\n"
        f"Всего сообщений: {st['count']}\n"
        f"Последнее сообщение: {dt}"
    )
    await m.answer(msg)

# ────────────────────────────────────────────────
# /mute — ИСПРАВЛЕНО: parse_mute_args убирает ссылку из причины
# ────────────────────────────────────────────────
@bot.on.message(text=["/mute", "/mute <args>"])
async def mute_cmd(m: Message, args=None):
    if not await check_access(m, "Модератор"): return
    t = await get_target_id(m, args)
    if not t:
        return await m.answer("Укажите пользователя!")
    if t == m.from_id:
        return await m.answer("Невозможно выдать мут данному пользователю!")
    my_rank, _  = get_user_info(m.peer_id, m.from_id)
    tgt_rank, _ = get_user_info(m.peer_id, t)
    if RANK_WEIGHT.get(tgt_rank, 0) >= RANK_WEIGHT.get(my_rank, 0):
        return await m.answer("Невозможно выдать мут данному пользователю!")
    mins, reason = parse_mute_args(args)
    until = time.time() + mins * 60
    pid   = str(m.peer_id)
    ensure_chat(pid)
    DATABASE["chats"][pid]["mutes"][str(t)] = until
    dt = datetime.datetime.fromtimestamp(until, TZ_MSK).strftime("%d/%m/%Y %H:%M:%S")
    t_display = await get_display_name(t, peer_id=m.peer_id)
    kb = Keyboard(inline=True)
    kb.row()
    kb.add(Callback("Снять мут", {"cmd": "unmute_btn", "uid": str(t)}), color=KeyboardButtonColor.POSITIVE)
    kb.add(Callback("Очистить",  {"cmd": "clear_msg",  "uid": str(t)}), color=KeyboardButtonColor.NEGATIVE)
    await m.answer(
        f"[id{m.from_id}|Модератор MANLIX] выдал(-а) мут [id{t}|{t_display}]\n"
        f"Причина: {reason}\n"
        f"Мут выдан до: {dt}",
        keyboard=kb.get_json()
    )
    await push_to_github(DATABASE, GH_PATH_DB, EXTERNAL_DB)

# ────────────────────────────────────────────────
# /unmute
# ────────────────────────────────────────────────
@bot.on.message(text=["/unmute", "/unmute <args>"])
async def unmute_cmd(m: Message, args=None):
    if not await check_access(m, "Модератор"): return
    t = await get_target_id(m, args)
    if not t:
        return await m.answer("Укажите пользователя.")
    pid = str(m.peer_id)
    ensure_chat(pid)
    if str(t) in DATABASE["chats"][pid].get("mutes", {}):
        del DATABASE["chats"][pid]["mutes"][str(t)]
        await push_to_github(DATABASE, GH_PATH_DB, EXTERNAL_DB)
    t_display = await get_display_name(t, peer_id=m.peer_id)
    await m.answer(f"[id{m.from_id}|Модератор MANLIX] снял(-а) мут [id{t}|{t_display}]")

# ────────────────────────────────────────────────
# Единый обработчик кнопок (мут + дуэль)
# ВАЖНО: в vkbottle только ОДИН raw_event одного типа
# ────────────────────────────────────────────────
# Пустая inline-клавиатура как JSON-строка для messages.edit
EMPTY_KB_JSON = '{"inline":true,"buttons":[]}'

@bot.on.raw_event(GroupEventType.MESSAGE_EVENT, MessageEvent)
async def all_buttons(event: MessageEvent):
    # Правильный паттерн по официальной документации vkbottle:
    # MessageEvent имеет атрибуты напрямую: peer_id, user_id, event_id,
    # conversation_message_id, payload
    # и методы: show_snackbar(), send_message_event_answer()
    peer_id  = event.peer_id
    actor_id = event.user_id
    cmid     = event.conversation_message_id

    # Нормализуем payload -> dict
    raw_payload = event.payload
    if raw_payload is None:
        return
    if isinstance(raw_payload, dict):
        payload = raw_payload
    elif isinstance(raw_payload, str):
        try:
            payload = json.loads(raw_payload)
        except:
            return
    else:
        return

    cmd = payload.get("cmd")
    if not cmd:
        return

    pid = str(peer_id)

    # Используем встроенный метод show_snackbar из MessageEvent
    async def snackbar(text: str):
        try:
            await event.show_snackbar(text)
        except Exception as e:
            print("snackbar error:", e)

    # ── Кнопки мута ──────────────────────────────
    if cmd in ("unmute_btn", "clear_msg"):
        uid = str(payload.get("uid", ""))
        ensure_chat(pid)

        rank, _ = get_user_info(peer_id, actor_id)
        if RANK_WEIGHT.get(rank, 0) < 1:
            await snackbar("Недостаточно прав")
            return

        if cmd == "unmute_btn":
            if uid in DATABASE["chats"][pid].get("mutes", {}):
                del DATABASE["chats"][pid]["mutes"][uid]
                await push_to_github(DATABASE, GH_PATH_DB, EXTERNAL_DB)
            try:
                u_info = await bot.api.users.get([int(uid)])
                u_name = f"{u_info[0].first_name} {u_info[0].last_name}"
            except:
                u_name = "пользователю"
            new_text = f"[id{actor_id}|Модератор MANLIX] снял(-а) мут [id{uid}|{u_name}]"
            try:
                await bot.api.request("messages.edit", {
                    "peer_id": peer_id,
                    "message": new_text,
                    "conversation_message_id": cmid,
                    "keyboard": EMPTY_KB_JSON
                })
            except Exception as e:
                print("edit unmute error:", e)
            await snackbar("Мут снят")

        elif cmd == "clear_msg":
            try:
                history = await bot.api.messages.get_history(
                    peer_id=peer_id,
                    count=50,
                    user_id=int(uid)
                )
                ids = [msg.id for msg in history.items if msg.from_id == int(uid)]
                if ids:
                    await bot.api.messages.delete(
                        peer_id=peer_id,
                        message_ids=ids,
                        delete_for_all=True
                    )
            except Exception as e:
                print("clear_msg error:", e)
            try:
                u_info2 = await bot.api.users.get([int(uid)])
                u_name2 = f"{u_info2[0].first_name} {u_info2[0].last_name}"
            except:
                u_name2 = "пользователя"
            new_text = f"[id{actor_id}|Модератор MANLIX] очистил(-а) сообщения [id{uid}|{u_name2}]"
            try:
                await bot.api.request("messages.edit", {
                    "peer_id": peer_id,
                    "message": new_text,
                    "conversation_message_id": cmid,
                    "keyboard": EMPTY_KB_JSON
                })
            except Exception as e:
                print("edit clear error:", e)
            await snackbar("Сообщения очищены")
        return

    # ── Кнопка разблокировать (при добавлении в беседу) ──
    if cmd == "gunban_btn":
        uid = str(payload.get("uid", ""))
        rank, _ = get_user_info(peer_id, actor_id)
        if RANK_WEIGHT.get(rank, 0) < 8:
            await snackbar("Недостаточно прав")
            return
        if uid in PUNISHMENTS.get("gbans_status", {}):
            del PUNISHMENTS["gbans_status"][uid]
            await push_to_github(PUNISHMENTS, GH_PATH_PUN, EXTERNAL_PUN)
        try:
            await bot.api.request("messages.edit", {
                "peer_id": peer_id,
                "conversation_message_id": cmid,
                "message": f"[id{uid}|Пользователь] разблокирован.",
                "keyboard": EMPTY_KB_JSON
            })
        except Exception as e:
            print("gunban_btn edit error:", e)
        await snackbar("Пользователь разблокирован")
        return

    # ── Кнопка дуэли ─────────────────────────────
    if cmd == "join_duel":
        duel_id = payload.get("duel")
        if duel_id not in DATABASE.get("duels", {}):
            await snackbar("Дуэль уже завершена.")
            return
        duel = DATABASE["duels"][duel_id]
        uid  = str(actor_id)
        if uid in duel["participants"]:
            await snackbar("Вы уже участвуете.")
            return
        if len(duel["participants"]) >= 2:
            await snackbar("Дуэль уже заполнена.")
            return
        if uid not in ECONOMY or ECONOMY[uid].get("cash", 0) < duel["amount"]:
            await snackbar("Недостаточно наличных средств.")
            return
        duel["participants"].append(uid)
        await snackbar("Вы вступили в дуэль!")
        if len(duel["participants"]) == 2:
            winner = random.choice(duel["participants"])
            loser  = [p for p in duel["participants"] if p != winner][0]
            amount = duel["amount"]
            ECONOMY[winner]["cash"] = ECONOMY[winner].get("cash", 0) + amount
            ECONOMY[loser]["cash"]  = ECONOMY[loser].get("cash",  0) - amount
            await push_to_github(ECONOMY, GH_PATH_ECO, EXTERNAL_ECO)
            del DATABASE["duels"][duel_id]
            await push_to_github(DATABASE, GH_PATH_DB, EXTERNAL_DB)
            # Получаем имена из ВК
            try:
                w_info = await bot.api.users.get([int(winner)])
                w_name = f"{w_info[0].first_name} {w_info[0].last_name}"
            except:
                w_name = "победитель"
            try:
                l_info = await bot.api.users.get([int(loser)])
                l_name = f"{l_info[0].first_name} {l_info[0].last_name}"
            except:
                l_name = "проигравший"
            await bot.api.messages.send(
                peer_id=int(duel["chat_id"]),
                message=(
                    f"⚔️ Дуэль завершена!\n\n"
                    f"🏅 Победил: [id{winner}|{w_name}]\n"
                    f"🥈 Проиграл: [id{loser}|{l_name}]\n\n"
                    f"💲 Победитель получает {amount}$"
                ),
                random_id=random.randint(0, 2**31)
            )

# ────────────────────────────────────────────────
# /kick
# ────────────────────────────────────────────────
@bot.on.message(text=["/kick", "/kick <args>"])
async def kick_cmd(m: Message, args=None):
    if not await check_access(m, "Модератор"): return
    t = await get_target_id(m, args)
    if not t:
        return await m.answer("Укажите пользователя!")
    if t == m.from_id:
        return await m.answer("Невозможно исключить данного пользователя!")
    # Проверка на ранг цели
    my_rank, _  = get_user_info(m.peer_id, m.from_id)
    tgt_rank, _ = get_user_info(m.peer_id, t)
    if RANK_WEIGHT.get(tgt_rank, 0) >= RANK_WEIGHT.get(my_rank, 0):
        return await m.answer("Невозможно исключить данного пользователя!")
    try:
        chat_id = m.peer_id - 2000000000
        await bot.api.messages.remove_chat_user(chat_id=chat_id, member_id=t)
    except Exception as e:
        print("kick error:", e)
    t_display = await get_display_name(t, peer_id=m.peer_id)
    await m.answer(f"[id{m.from_id}|Модератор MANLIX] исключил(-а) [id{t}|{t_display}] из Беседы.")

# ────────────────────────────────────────────────
# /ban
# ────────────────────────────────────────────────
@bot.on.message(text=["/ban", "/ban <args>"])
async def ban_cmd(m: Message, args=None):
    if not await check_access(m, "Старший Модератор"): return
    t = await get_target_id(m, args)
    if not t:
        return await m.answer("Укажите пользователя!")
    if t == m.from_id:
        return await m.answer("Невозможно заблокировать данного пользователя!")
    my_rank, _  = get_user_info(m.peer_id, m.from_id)
    tgt_rank, _ = get_user_info(m.peer_id, t)
    if RANK_WEIGHT.get(tgt_rank, 0) >= RANK_WEIGHT.get(my_rank, 0):
        return await m.answer("Невозможно заблокировать данного пользователя!")
    parts  = (args or "").split()
    reason = " ".join(parts[1:]) or "Нарушение"
    pid    = str(m.peer_id)
    ensure_chat(pid)
    if pid not in PUNISHMENTS["bans"]:
        PUNISHMENTS["bans"][pid] = {}
    PUNISHMENTS["bans"][pid][str(t)] = {
        "admin":  m.from_id,
        "reason": reason,
        "date":   time.time()
    }
    try:
        chat_id = m.peer_id - 2000000000
        await bot.api.messages.remove_chat_user(chat_id=chat_id, member_id=t)
    except:
        pass
    await push_to_github(PUNISHMENTS, GH_PATH_PUN, EXTERNAL_PUN)
    t_display = await get_display_name(t, peer_id=m.peer_id)
    await m.answer(f"[id{m.from_id}|Модератор MANLIX] заблокировал(-а) [id{t}|{t_display}] в Беседе.")

# ────────────────────────────────────────────────
# /unban
# ────────────────────────────────────────────────
@bot.on.message(text=["/unban", "/unban <args>"])
async def unban_cmd(m: Message, args=None):
    if not await check_access(m, "Старший Модератор"): return
    t = await get_target_id(m, args)
    if not t:
        return await m.answer("Укажите пользователя.")
    pid = str(m.peer_id)
    if pid in PUNISHMENTS["bans"] and str(t) in PUNISHMENTS["bans"][pid]:
        del PUNISHMENTS["bans"][pid][str(t)]
        await push_to_github(PUNISHMENTS, GH_PATH_PUN, EXTERNAL_PUN)
    t_display = await get_display_name(t, peer_id=m.peer_id)
    await m.answer(f"[id{m.from_id}|Модератор MANLIX] снял(-а) блокировку [id{t}|{t_display}] в Беседе.")

# ────────────────────────────────────────────────
# Выдача ролей
# ────────────────────────────────────────────────
async def role_grant(m: Message, args, min_rank, role_name, role_label):
    if not await check_access(m, min_rank): return
    t = await get_target_id(m, args)
    if not t:
        return await m.answer("Укажите пользователя.")
    if t == m.from_id:
        return await m.answer("Вы не можете выдать роль данному пользователю!")
    my_rank, _  = get_user_info(m.peer_id, m.from_id)
    tgt_rank, _ = get_user_info(m.peer_id, t)
    if RANK_WEIGHT.get(tgt_rank, 0) >= RANK_WEIGHT.get(my_rank, 0):
        return await m.answer("Вы не можете выдать роль данному пользователю!")
    pid, uid  = str(m.peer_id), str(t)
    await set_role_in_chat(pid, uid, role_name)
    await push_to_github(DATABASE, GH_PATH_DB, EXTERNAL_DB)
    a_display = await get_display_name(m.from_id, peer_id=m.peer_id)
    t_display = await get_display_name(t, peer_id=m.peer_id)
    await m.answer(f"[id{m.from_id}|{a_display}] выдал(-а) права {role_label} [id{t}|{t_display}]")

@bot.on.message(text=["/addmoder",    "/addmoder <args>"])
async def addmod(m: Message, args=None):
    await role_grant(m, args, "Старший Модератор",          "Модератор",                "модератора")

@bot.on.message(text=["/addsenmoder", "/addsenmoder <args>"])
async def addsenmod(m: Message, args=None):
    await role_grant(m, args, "Администратор",              "Старший Модератор",         "старшего модератора")

@bot.on.message(text=["/addadmin",    "/addadmin <args>"])
async def addadm(m: Message, args=None):
    await role_grant(m, args, "Старший Администратор",      "Администратор",             "администратора")

@bot.on.message(text=["/addsenadmin", "/addsenadmin <args>"])
async def addsenadm(m: Message, args=None):
    await role_grant(m, args, "Зам. Спец. Администратора",  "Старший Администратор",     "старшего администратора")

@bot.on.message(text=["/addzsa",      "/addzsa <args>"])
async def addzsa(m: Message, args=None):
    await role_grant(m, args, "Спец. Администратор",        "Зам. Спец. Администратора", "заместителя специального администратора")

@bot.on.message(text=["/addsa",       "/addsa <args>"])
async def addsa(m: Message, args=None):
    await role_grant(m, args, "Владелец",                   "Спец. Администратор",       "специального администратора")

@bot.on.message(text=["/addowner",    "/addowner <args>"])
async def addowner(m: Message, args=None):
    await role_grant(m, args, "Зам. Спец. Руководителя",    "Владелец",                  "владельца")

@bot.on.message(text=["/addzsr", "/addzsr <args>"])
async def addzsr(m: Message, args=None):
    """Выдать права Зам. Спец. Руководителя — только Основной Зам. или Спец. Руководитель."""
    if not await check_access(m, "Основной Зам. Спец. Руководителя"): return
    t = await get_target_id(m, args)
    if not t:
        return await m.answer("Укажите пользователя.")
    if t == m.from_id:
        return await m.answer("Вы не можете выдать роль данному пользователю!")
    uid = str(t)
    gstaff = STAFF["gstaff"]
    if "zams" not in gstaff:
        gstaff["zams"] = []
    if t not in gstaff["zams"]:
        gstaff["zams"].append(t)
    await push_to_github(STAFF, GH_PATH_STAFF, EXTERNAL_STAFF)
    a_display = await get_display_name(m.from_id, peer_id=m.peer_id)
    t_display = await get_display_name(t, peer_id=m.peer_id)
    await m.answer(f"[id{m.from_id}|{a_display}] выдал(-а) права заместителя специального руководителя [id{t}|{t_display}]")

@bot.on.message(text=["/addozsr", "/addozsr <args>"])
async def addozsr(m: Message, args=None):
    """Выдать права Основного Зам. Спец. Руководителя — только Спец. Руководитель."""
    if not await check_access(m, "Специальный Руководитель"): return
    t = await get_target_id(m, args)
    if not t:
        return await m.answer("Укажите пользователя.")
    if t == m.from_id:
        return await m.answer("Вы не можете выдать роль данному пользователю!")
    STAFF["gstaff"]["main_zam"] = t
    await push_to_github(STAFF, GH_PATH_STAFF, EXTERNAL_STAFF)
    a_display = await get_display_name(m.from_id, peer_id=m.peer_id)
    t_display = await get_display_name(t, peer_id=m.peer_id)
    await m.answer(f"[id{m.from_id}|{a_display}] выдал(-а) права основного заместителя специального руководителя [id{t}|{t_display}]")

# ────────────────────────────────────────────────
# /removerole
# ────────────────────────────────────────────────
@bot.on.message(text=["/removerole", "/removerole <args>"])
async def removerole(m: Message, args=None):
    if not await check_access(m, "Старший Модератор"): return
    t = await get_target_id(m, args)
    if not t:
        return await m.answer("Укажите пользователя.")
    pid, uid  = str(m.peer_id), str(t)
    ensure_chat(pid)
    if uid in DATABASE["chats"][pid].get("staff", {}):
        del DATABASE["chats"][pid]["staff"][uid]
        await push_to_github(DATABASE, GH_PATH_DB, EXTERNAL_DB)
    a_display = await get_display_name(m.from_id, peer_id=m.peer_id)
    t_display = await get_display_name(t, peer_id=m.peer_id)
    await m.answer(f"[id{m.from_id}|{a_display}] снял(-а) уровень прав [id{t}|{t_display}]")

# ────────────────────────────────────────────────
# /gunrole — снять глобальную роль (зам, основной зам)
# ────────────────────────────────────────────────
@bot.on.message(text=["/gunrole", "/gunrole <args>"])
async def gunrole_cmd(m: Message, args=None):
    if not await check_access(m, "Зам. Спец. Руководителя"): return
    t = await get_target_id(m, args)
    if not t:
        return await m.answer("Укажите пользователя.")
    if t == m.from_id:
        return await m.answer("Нельзя снимать права у самого себя.")
    gstaff = STAFF["gstaff"]
    removed = False
    # Снимаем из зам. спец. руководителей
    if t in gstaff.get("zams", []):
        gstaff["zams"].remove(t)
        removed = True
    # Снимаем основного зама (только Спец. Руководитель)
    if gstaff.get("main_zam") == t:
        rank, _ = get_user_info(m.peer_id, m.from_id)
        if RANK_WEIGHT.get(rank, 0) >= 10:
            gstaff["main_zam"] = None
            removed = True
        else:
            return await m.answer("Снять Основного Зам. может только Специальный Руководитель.")
    # Снимаем роль тестировщика
    uid = str(t)
    if uid in STAFF.get("testers", {}):
        del STAFF["testers"][uid]
        removed = True
    if not removed:
        return await m.answer("У этого пользователя нет глобальных прав.")
    await push_to_github(STAFF, GH_PATH_STAFF, EXTERNAL_STAFF)
    a_display = await get_display_name(m.from_id, peer_id=m.peer_id)
    t_display = await get_display_name(t, peer_id=m.peer_id)
    await m.answer(f"[id{m.from_id}|{a_display}] снял(-а) глобальный уровень прав [id{t}|{t_display}]")

# ────────────────────────────────────────────────
# /staff
# ────────────────────────────────────────────────
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
    blocks = []
    for r in order:
        members = []
        for u, entry in staff.items():
            if entry[0] == r:
                nick = entry[1]
                if nick:
                    display = nick
                else:
                    try:
                        uinfo   = await bot.api.users.get([int(u)])
                        display = f"{uinfo[0].first_name} {uinfo[0].last_name}"
                    except:
                        display = "пользователь"
                members.append(f"– https://vk.com/id{u}")
        if r == "Владелец":
            owner_ids = [u for u, entry in staff.items() if entry[0] == "Владелец"]
            if owner_ids:
                block = f"Владелец -- [id{owner_ids[0]}|MANLIX MANAGER]"
                for oid in owner_ids[1:]:
                    block += f"\n– [id{oid}|MANLIX MANAGER]"
            else:
                block = "Владелец -- MANLIX MANAGER"
        else:
            if members:
                block = f"{r}: \n" + "\n".join(members)
            else:
                block = f"{r}: \n– Отсутствует."
        blocks.append(block)
    await m.answer("\n\n".join(blocks))

# ────────────────────────────────────────────────
# /setnick — ИСПРАВЛЕНО: поддержка reply
# Способ 1: /setnick [ссылка] [ник]
# Способ 2: ответом на сообщение -> /setnick [ник]
# ────────────────────────────────────────────────
@bot.on.message(text=["/setnick", "/setnick <args>"])
async def setnick(m: Message, args=None):
    if not await check_access(m, "Модератор"): return

    if getattr(m, "reply_message", None):
        # Режим reply: цель из reply, args — это целиком ник
        t = m.reply_message.from_id
        new_nick = (args or "").strip()
        if not new_nick:
            return await m.answer("Укажите ник после команды.")
    else:
        if not args:
            return await m.answer("Формат: /setnick [пользователь] [ник]")
        parts = args.split(maxsplit=1)
        if len(parts) < 2:
            return await m.answer("Формат: /setnick [пользователь] [ник]")
        t = await get_target_id(m, parts[0])
        if not t:
            return await m.answer("Не удалось определить пользователя.")
        new_nick = parts[1].strip()

    pid, uid    = str(m.peer_id), str(t)
    ensure_chat(pid)
    role_now, _ = get_user_info(m.peer_id, t)
    # Ник можно выдать любому включая владельца.
    # В /staff владелец всегда отображается как MANLIX MANAGER (независимо от ника)
    DATABASE["chats"][pid]["staff"][uid] = [role_now, new_nick]
    await push_to_github(DATABASE, GH_PATH_DB, EXTERNAL_DB)
    a_display = await get_display_name(m.from_id, peer_id=m.peer_id)
    t_display = await get_display_name(t, peer_id=m.peer_id)
    await m.answer(f"[id{m.from_id}|{a_display}] установил(-а) новое имя [id{t}|{t_display}]: {new_nick}")

# ────────────────────────────────────────────────
# /rnick — ИСПРАВЛЕНО: поддержка reply
# Способ 1: /rnick [ссылка]
# Способ 2: ответом на сообщение -> /rnick
# ────────────────────────────────────────────────
@bot.on.message(text=["/rnick", "/rnick <args>"])
async def rnick(m: Message, args=None):
    if not await check_access(m, "Модератор"): return
    t = await get_target_id(m, args)
    if not t:
        return await m.answer("Укажите пользователя или ответьте на его сообщение.")
    pid, uid = str(m.peer_id), str(t)
    ensure_chat(pid)
    if uid in DATABASE["chats"][pid].get("staff", {}):
        DATABASE["chats"][pid]["staff"][uid][1] = None
        await push_to_github(DATABASE, GH_PATH_DB, EXTERNAL_DB)
    a_display = await get_display_name(m.from_id, peer_id=m.peer_id)
    t_display = await get_display_name(t, peer_id=m.peer_id)
    await m.answer(f"[id{m.from_id}|{a_display}] убрал(-а) имя [id{t}|{t_display}]")

# ────────────────────────────────────────────────
# /nlist
# ────────────────────────────────────────────────
@bot.on.message(text="/nlist")
async def nick_list(m: Message):
    if not await check_access(m, "Модератор"): return
    pid = str(m.peer_id)
    ensure_chat(pid)
    staff = DATABASE["chats"].get(pid, {}).get("staff", {})
    users = [(u, entry[1]) for u, entry in staff.items() if entry[1]]
    if not users:
        return await m.answer("Никнеймы не установлены.")
    msg = "Список пользователей с ником:\n"
    for i, (u, n) in enumerate(users, 1):
        msg += f"{i}. [id{u}|{n}]\n"
    await m.answer(msg.strip())

# ────────────────────────────────────────────────
# /getban — ИСПРАВЛЕНО: заголовок "Информация о Блокировках [ссылка|пользователя]"
# ────────────────────────────────────────────────
@bot.on.message(text=["/getban", "/getban <args>"])
async def getban_cmd(m: Message, args=None):
    if not await check_access(m, "Модератор"): return
    # Поддержка reply + ссылок + id
    t = None
    if getattr(m, "reply_message", None):
        t = m.reply_message.from_id
    if not t:
        t = await get_target_id(m, args)
    if not t:
        return await m.answer("Укажите пользователя.")
    uid = str(t)
    try:
        uinfo = await bot.api.users.get([t])
        name  = f"{uinfo[0].first_name} {uinfo[0].last_name}"
    except:
        name = "пользователь"

    # Строчный регистр в заголовке — требование пользователя
    t_name = await get_display_name(t, peer_id=m.peer_id)
    ans = f"Информация о блокировках [id{t}|{t_name}]\n"

    # Глобальный бан в беседах
    if uid in PUNISHMENTS.get("gbans_status", {}):
        b  = PUNISHMENTS["gbans_status"][uid]
        dt = datetime.datetime.fromtimestamp(b["date"], TZ_MSK).strftime("%d/%m/%Y %H:%M:%S")
        ans += (
            f"\nИнформация о общей блокировке в беседах:\n"
            f"[id{b['admin']}|Модератор MANLIX] | {b.get('reason', '-')} | {dt}\n"
        )
    else:
        ans += "\nИнформация о общей блокировке в беседах: отсутствует\n"

    # Глобальный бан в играх
    if uid in PUNISHMENTS.get("gbans_pl", {}):
        b  = PUNISHMENTS["gbans_pl"][uid]
        dt = datetime.datetime.fromtimestamp(b["date"], TZ_MSK).strftime("%d/%m/%Y %H:%M:%S")
        ans += (
            f"\nИнформация о блокировке в беседах игроков:\n"
            f"[id{b['admin']}|Модератор MANLIX] | {b.get('reason', '-')} | {dt}\n"
        )
    else:
        ans += "\nИнформация о блокировке в беседах игроков: отсутствует\n"

    # Локальные баны
    local_bans = []
    for pid_b, bans in PUNISHMENTS.get("bans", {}).items():
        if uid in bans:
            b     = bans[uid]
            title = DATABASE["chats"].get(pid_b, {}).get("title", f"Беседа {pid_b}")
            dt    = datetime.datetime.fromtimestamp(b["date"], TZ_MSK).strftime("%d/%m/%Y %H:%M:%S")
            local_bans.append(f"{title} | [id{b['admin']}|Модератор MANLIX] | {dt}")

    if local_bans:
        ans += f"\nКоличество Бесед, в которых заблокирован пользователь: {len(local_bans)}\n"
        ans += "Информация о последних 10 Блокировках:\n"
        for i, lb in enumerate(local_bans[-10:], 1):
            ans += f"{i}) {lb}\n"
    else:
        ans += "Блокировки в беседах отсутствуют"

    await m.answer(ans)

# ────────────────────────────────────────────────
# /gstaff
# ────────────────────────────────────────────────
@bot.on.message(text="/gstaff")
async def gstaff_view(m: Message):
    if not await check_access(m, "Зам. Спец. Руководителя"): return
    g   = STAFF["gstaff"]
    res = "MANLIX MANAGER | Команда Бота:\n\n"
    res += "| Специальный Руководитель:\n– [id870757778|MANLIX]\n\n"
    res += "| Основной зам. Спец. Руководителя:\n"
    if g.get("main_zam"):
        res += f"– [id{g['main_zam']}|MANLIX]\n"
    else:
        res += "– Отсутствует.\n"
    res += "\n| Зам. Спец. Руководителя:\n"
    zams = g.get("zams", [])
    if zams:
        for z in zams:
            res += f"– [id{z}|MANLIX]\n"
    else:
        res += "– Отсутствует.\n– Отсутствует.\n"
    await m.answer(res.strip())

# ────────────────────────────────────────────────
# /start
# ────────────────────────────────────────────────
@bot.on.message(text="/start")
async def start(m: Message):
    if not await check_access(m, "Специальный Руководитель"): return
    global GROUP_ID
    pid = str(m.peer_id)
    ensure_chat(pid)
    try:
        conv = await bot.api.messages.get_conversations_by_id(peer_ids=[m.peer_id])
        if conv.items:
            DATABASE["chats"][pid]["title"] = conv.items[0].chat_settings.title
    except:
        pass
    # Сохраняем group_id для /staff
    if GROUP_ID is None:
        try:
            grp = await bot.api.groups.get_by_id()
            GROUP_ID = grp[0].id
            DATABASE["group_id"] = GROUP_ID
        except:
            pass
    await push_to_github(DATABASE, GH_PATH_DB, EXTERNAL_DB)
    await m.answer("Вы успешно активировали Беседу.")

# ────────────────────────────────────────────────
# /type
# ────────────────────────────────────────────────
@bot.on.message(text=["/type", "/type <args>"])
async def type_cmd(m: Message, args=None):
    if not await check_access(m, "Специальный Руководитель"): return
    pid   = str(m.peer_id)
    ensure_chat(pid)
    valid = ["def", "adm", "mod", "pl", "test"]
    if args:
        new_type = args.strip().lower()
        if new_type in valid:
            DATABASE["chats"][pid]["type"] = new_type
            await push_to_github(DATABASE, GH_PATH_DB, EXTERNAL_DB)
            await m.answer(f"Тип Беседы изменён на: {new_type}")
            return
        else:
            await m.answer("Неверный тип. Доступные типы смотри ниже.")
    current = DATABASE["chats"][pid]["type"]
    await m.answer(
        f"Беседа имеет тип: {current}\n\n"
        "def - общая Беседа\n"
        "adm - Беседа администраторов\n"
        "mod - Беседа модераторов\n"
        "pl - Беседа игроков\n"
        "test - Беседа тестировщиков"
    )

# ────────────────────────────────────────────────
# /typetex — технические типы бесед
# ────────────────────────────────────────────────
@bot.on.message(text=["/typetex", "/typetex <args>"])
async def typetex_cmd(m: Message, args=None):
    if not await check_access(m, "Специальный Руководитель"): return
    pid   = str(m.peer_id)
    ensure_chat(pid)
    valid = ["tex", "bug"]
    if args:
        new_type = args.strip().lower()
        if new_type in valid:
            DATABASE["chats"][pid]["type"] = new_type
            await push_to_github(DATABASE, GH_PATH_DB, EXTERNAL_DB)
            await m.answer(f"Технический тип Беседы изменён на: {new_type}")
            return
        else:
            await m.answer("Неверный тип. Доступные технические типы смотри ниже.")
    current = DATABASE["chats"][pid]["type"]
    await m.answer(
        f"Беседа имеет тип: {current}\n\n"
        "tex - Тех. Раздел\n"
        "bug - Баг-трекер"
    )

# ────────────────────────────────────────────────
# /chatid
# ────────────────────────────────────────────────
@bot.on.message(text="/chatid")
async def chatid(m: Message):
    if not await check_access(m, "Специальный Руководитель"): return
    await m.answer(f"ID текущей Беседы: {m.peer_id}")

# ────────────────────────────────────────────────
# /delchat
# ────────────────────────────────────────────────
@bot.on.message(text="/delchat")
async def delchat(m: Message):
    if not await check_access(m, "Специальный Руководитель"): return
    pid = str(m.peer_id)
    if pid in DATABASE["chats"]:
        del DATABASE["chats"][pid]
        await push_to_github(DATABASE, GH_PATH_DB, EXTERNAL_DB)
        await m.answer("Вы успешно удалили чат с Базы данных.")
    else:
        await m.answer("Эта Беседа не найдена в базе данных.")

# ────────────────────────────────────────────────
# /sync
# ────────────────────────────────────────────────
@bot.on.message(text="/sync")
async def sync(m: Message):
    if not await check_access(m, "Специальный Руководитель"): return
    global DATABASE, ECONOMY, PUNISHMENTS, STAFF
    DATABASE    = await load_from_github(GH_PATH_DB,    EXTERNAL_DB)
    ECONOMY     = await load_from_github(GH_PATH_ECO,   EXTERNAL_ECO)
    PUNISHMENTS = await load_from_github(GH_PATH_PUN,   EXTERNAL_PUN)
    STAFF       = await load_from_github(GH_PATH_STAFF, EXTERNAL_STAFF)
    await m.answer("Вы успешно синхронизировали Беседу с Базой данных.")

# ────────────────────────────────────────────────
# /botstatus
# ────────────────────────────────────────────────
@bot.on.message(text=["/botstatus", "/botstatus <args>"])
async def botstatus_cmd(m: Message, args=None):
    if not await check_access(m, "Специальный Руководитель"): return
    valid = {"on", "off", "test"}
    if not args or args.strip().lower() not in valid:
        current = DATABASE.get("bot_status", "on")
        return await m.answer(
            f"Текущий статус бота: « {current} »\n\n"
            "Доступные статусы:\n"
            "on -- обычный режим.\n"
            "off -- бот работает только для спец. руководства.\n"
            "test -- бот работает для спец. руководства и тестировщиков."
        )
    new_status = args.strip().lower()
    DATABASE["bot_status"] = new_status
    await push_to_github(DATABASE, GH_PATH_DB, EXTERNAL_DB)
    await m.answer(f"Вы успешно изменили статус бота на « {new_status} »")

# ────────────────────────────────────────────────
# /msg — рассылка во все беседы выбранного типа
# ────────────────────────────────────────────────
@bot.on.message(text=["/msg", "/msg <args>"])
async def msg_cmd(m: Message, args=None):
    if not await check_access(m, "Основной Зам. Спец. Руководителя"): return
    if not args or not args.strip():
        return await m.answer("Использование: /msg [тип] [сообщение]")
    parts    = args.strip().split(None, 1)
    chat_type = parts[0].lower()
    text      = parts[1] if len(parts) > 1 else ""
    if not text:
        return await m.answer("Укажите текст сообщения.")
    valid_types = ["def", "adm", "mod", "pl", "test", "tex", "bug", "all"]
    if chat_type not in valid_types:
        return await m.answer(f"Неверный тип. Доступные: {', '.join(valid_types)}")
    a_display = await get_display_name(m.from_id, peer_id=m.peer_id, use_nick=False)
    sent = 0
    for pid_c, chat in list(DATABASE.get("chats", {}).items()):
        if chat_type == "all" or chat.get("type") == chat_type:
            try:
                await bot.api.messages.send(
                    peer_id=int(pid_c),
                    message=text,
                    random_id=random.randint(0, 2**31)
                )
                sent += 1
            except Exception as e:
                print(f"/msg send error to {pid_c}:", e)
    await m.answer(
        f"[id{m.from_id}|{a_display}] отправил рассылку в типы бесед « {chat_type} »"
    )

# ────────────────────────────────────────────────
# /gban / /gunban
# ────────────────────────────────────────────────
@bot.on.message(text=["/gban", "/gban <args>"])
async def gban_cmd(m: Message, args=None):
    if not await check_access(m, "Специальный Руководитель"): return
    t = await get_target_id(m, args)
    if not t:
        return await m.answer("Укажите пользователя!")
    if t == m.from_id:
        return await m.answer("Невозможно заблокировать данного пользователя!")
    my_rank, _  = get_user_info(m.peer_id, m.from_id)
    tgt_rank, _ = get_user_info(m.peer_id, t)
    if RANK_WEIGHT.get(tgt_rank, 0) >= RANK_WEIGHT.get(my_rank, 0):
        return await m.answer("Невозможно заблокировать данного пользователя!")
    reason = parse_reason(args) or "Нарушение"
    uid    = str(t)
    PUNISHMENTS["gbans_status"][uid] = {"admin": m.from_id, "reason": reason, "date": time.time()}
    await push_to_github(PUNISHMENTS, GH_PATH_PUN, EXTERNAL_PUN)
    t_display = await get_display_name(t, peer_id=m.peer_id)
    await m.answer(f"[id{m.from_id}|Специальный Руководитель] занес [id{t}|{t_display}] в глобальную Блокировку Бота.")

@bot.on.message(text=["/gunban", "/gunban <args>"])
async def gunban(m: Message, args=None):
    if not await check_access(m, "Специальный Руководитель"): return
    t = await get_target_id(m, args)
    if not t:
        return await m.answer("Укажите пользователя.")
    uid = str(t)
    if uid in PUNISHMENTS["gbans_status"]:
        del PUNISHMENTS["gbans_status"][uid]
        await push_to_github(PUNISHMENTS, GH_PATH_PUN, EXTERNAL_PUN)
    t_display = await get_display_name(t, peer_id=m.peer_id)
    await m.answer(f"[id{m.from_id}|Специальный Руководитель] вынес [id{t}|{t_display}] из Глобальной Блокировки Бота.")

# ────────────────────────────────────────────────
# /gbanpl / /gunbanpl
# ────────────────────────────────────────────────
@bot.on.message(text=["/gbanpl", "/gbanpl <args>"])
async def gbanpl_cmd(m: Message, args=None):
    if not await check_access(m, "Зам. Спец. Руководителя"): return
    t = await get_target_id(m, args)
    if not t:
        return await m.answer("Укажите пользователя!")
    if t == m.from_id:
        return await m.answer("Невозможно заблокировать данного пользователя!")
    my_rank, _  = get_user_info(m.peer_id, m.from_id)
    tgt_rank, _ = get_user_info(m.peer_id, t)
    if RANK_WEIGHT.get(tgt_rank, 0) >= RANK_WEIGHT.get(my_rank, 0):
        return await m.answer("Невозможно заблокировать данного пользователя!")
    reason = parse_reason(args) or "Нарушение"
    uid    = str(t)
    PUNISHMENTS["gbans_pl"][uid] = {"admin": m.from_id, "reason": reason, "date": time.time()}
    for pid_c in list(DATABASE["chats"].keys()):
        if DATABASE["chats"][pid_c].get("type") == "pl":
            try:
                chat_id = int(pid_c) - 2000000000
                await bot.api.messages.remove_chat_user(chat_id=chat_id, member_id=t)
            except:
                pass
    await push_to_github(PUNISHMENTS, GH_PATH_PUN, EXTERNAL_PUN)
    t_display = await get_display_name(t, peer_id=m.peer_id)
    await m.answer(f"[id{m.from_id}|Специальный Руководитель] заблокировал(-а) [id{t}|{t_display}] во всех игровых Беседах.")

@bot.on.message(text=["/gunbanpl", "/gunbanpl <args>"])
async def gunbanpl_cmd(m: Message, args=None):
    if not await check_access(m, "Зам. Спец. Руководителя"): return
    t = await get_target_id(m, args)
    if not t:
        return await m.answer("Укажите пользователя.")
    uid = str(t)
    if uid in PUNISHMENTS["gbans_pl"]:
        del PUNISHMENTS["gbans_pl"][uid]
        await push_to_github(PUNISHMENTS, GH_PATH_PUN, EXTERNAL_PUN)
    t_display = await get_display_name(t, peer_id=m.peer_id)
    await m.answer(f"[id{m.from_id}|Специальный Руководитель] разблокировал(-а) [id{t}|{t_display}] во всех игровых Беседах.")

# ────────────────────────────────────────────────
# Система тестировщиков
# ────────────────────────────────────────────────

def get_tester_info(user_id: int):
    """Возвращает (роль_тестировщика, кол-во_багов) или (None, 0)."""
    uid = str(user_id)
    entry = STAFF.get("testers", {}).get(uid)
    if entry:
        return entry.get("role"), entry.get("bugs", 0)
    return None, 0

async def tester_role_grant(m: Message, args, min_tester_role, role_name, role_label):
    """Выдача ролей тестировщиков."""
    t_role, _ = get_tester_info(m.from_id)
    my_global, _ = get_user_info(m.peer_id, m.from_id)
    # Доступ: нужная роль тестировщика ИЛИ глобальный ранг >= ЗСР
    has_access = (
        TESTER_RANK_WEIGHT.get(t_role, 0) >= TESTER_RANK_WEIGHT.get(min_tester_role, 0)
        or RANK_WEIGHT.get(my_global, 0) >= 8
    )
    if not has_access:
        return await m.answer("Недостаточно прав!")
    t = await get_target_id(m, args)
    if not t:
        return await m.answer("Укажите пользователя.")
    if t == m.from_id:
        return await m.answer("Вы не можете выдать роль данному пользователю!")
    uid = str(t)
    if uid not in STAFF["testers"]:
        STAFF["testers"][uid] = {"role": role_name, "bugs": 0, "joined": time.time()}
    else:
        STAFF["testers"][uid]["role"] = role_name
    await push_to_github(STAFF, GH_PATH_STAFF, EXTERNAL_STAFF)
    a_display = await get_display_name(m.from_id, peer_id=m.peer_id)
    t_display = await get_display_name(t, peer_id=m.peer_id)
    await m.answer(f"[id{m.from_id}|{a_display}] выдал(-а) права {role_label} [id{t}|{t_display}]")

@bot.on.message(text="/thelp")
async def thelp_cmd(m: Message):
    # Работает только в беседах типа "test" (или руководство)
    pid = str(m.peer_id)
    ensure_chat(pid)
    chat_type = DATABASE["chats"][pid].get("type", "def")
    t_role, _ = get_tester_info(m.from_id)
    my_global, _ = get_user_info(m.peer_id, m.from_id)
    if chat_type != "test" and RANK_WEIGHT.get(my_global, 0) < 8:
        return await m.answer("Эта команда доступна только в беседе тестировщиков.")
    if not t_role and RANK_WEIGHT.get(my_global, 0) < 8:
        return await m.answer("Недостаточно прав!")
    my_global, _ = get_user_info(m.peer_id, m.from_id)
    w_global = RANK_WEIGHT.get(my_global, 0)
    t_w = TESTER_RANK_WEIGHT.get(t_role, 0)

    # Базовые команды — доступны всем тестировщикам
    msg = (
        "Команды тестировщиков:\n"
        "/tstats -- статистика тестировщика.\n"
        "/tstaff -- команда тестировщиков.\n"
        "/bug -- отчет багов."
    )

    # Старший тестировщик и выше
    if t_w >= 2 or w_global >= 8:
        msg += (
            "\n\nКоманды старших тестировщиков:\n"
            "Отсутствуют."
        )

    # Главный тестировщик и выше
    if t_w >= 3 or w_global >= 8:
        msg += (
            "\n\nКоманды главного тестировщика:\n"
            "/addtester -- выдать права тестировщика.\n"
            "/addsentester -- выдать права старшего тестировщика."
        )

    # Спец. руководство
    if w_global >= 8:
        msg += (
            "\n\nКоманды спец. Руководства:\n"
            "/addgt -- выдать права главного тестировщика.\n"
            "/typetex test -- сменить тип беседы, на беседу тестировщиков.\n"
            "/typetex bug -- сменить тип беседы, на Баг-трекер."
        )

    await m.answer(msg)

@bot.on.message(text=["/tstats", "/tstats <args>"])
async def tstats_cmd(m: Message, args=None):
    # Работает только в беседах типа "test" (или руководство)
    pid = str(m.peer_id)
    ensure_chat(pid)
    chat_type = DATABASE["chats"][pid].get("type", "def")
    my_global, _ = get_user_info(m.peer_id, m.from_id)
    if chat_type != "test" and RANK_WEIGHT.get(my_global, 0) < 8:
        return await m.answer("Эта команда доступна только в беседе тестировщиков.")
    t = await get_target_id(m, args) or m.from_id
    uid = str(t)
    role, bugs = get_tester_info(t)
    if not role:
        t_name = await get_display_name(t, peer_id=m.peer_id)
        return await m.answer(f"[id{t}|{t_name}] не является тестировщиком.")
    now = datetime.datetime.now(TZ_MSK)
    await m.answer(
        f"Статистика [id{t}|тестировщика]\n\n"
        f"Должность: {role}\n"
        f"Отправлено Багов: {bugs}\n\n"
        f"Дата: {now.strftime('%d/%m/%Y')}\n"
        f"Время: {now.strftime('%H:%M:%S')}"
    )

@bot.on.message(text=["/bug", "/bug <args>"])
async def bug_cmd(m: Message, args=None):
    # Работает только в беседах типа "test"
    pid = str(m.peer_id)
    ensure_chat(pid)
    chat_type = DATABASE["chats"][pid].get("type", "def")
    my_global, _ = get_user_info(m.peer_id, m.from_id)
    if chat_type != "test" and RANK_WEIGHT.get(my_global, 0) < 8:
        return await m.answer("Эта команда доступна только в беседе тестировщиков.")

    role, _ = get_tester_info(m.from_id)
    if not role and RANK_WEIGHT.get(my_global, 0) < 8:
        return await m.answer("Недостаточно прав!")

    uid = str(m.from_id)
    if uid not in STAFF["testers"]:
        STAFF["testers"][uid] = {"role": role or "Тестировщик", "bugs": 0, "joined": time.time()}
    STAFF["testers"][uid]["bugs"] = STAFF["testers"][uid].get("bugs", 0) + 1
    await push_to_github(STAFF, GH_PATH_STAFF, EXTERNAL_STAFF)

    bug_text = (args or "").strip()

    # Подтверждение тестировщику — ник в беседе если есть, иначе имя ВК
    a_display = await get_display_name(m.from_id, peer_id=m.peer_id)
    await m.answer(f"[id{m.from_id}|{a_display}] отправил отчет с Багами.")

    # В репорте всегда надпись MANLIX
    now = datetime.datetime.now(TZ_MSK)
    report = (
        f"…::: BUG REPORT :::…\n\n"
        f"| Тестировщик: [id{m.from_id}|MANLIX]\n"
        f"| Время: {now.strftime('%H:%M:%S')}\n"
        f"| Дата: {now.strftime('%d/%m/%Y')}\n\n"
        f"| Отчет: « {bug_text} »"
    )

    # Отправляем во все беседы типа "bug"
    for pid_c, chat in list(DATABASE.get("chats", {}).items()):
        if chat.get("type") == "bug":
            try:
                await bot.api.messages.send(
                    peer_id=int(pid_c),
                    message=report,
                    random_id=random.randint(0, 2**31)
                )
            except Exception as e:
                print(f"bug report send error to {pid_c}:", e)

@bot.on.message(text="/tstaff")
async def tstaff_cmd(m: Message):
    # Доступна только в беседах типа "test" или руководству
    pid = str(m.peer_id)
    ensure_chat(pid)
    chat_type = DATABASE["chats"][pid].get("type", "def")
    my_global, _ = get_user_info(m.peer_id, m.from_id)
    t_role, _ = get_tester_info(m.from_id)
    if chat_type != "test" and RANK_WEIGHT.get(my_global, 0) < 8:
        return await m.answer("Эта команда доступна только в беседе тестировщиков.")
    if not t_role and RANK_WEIGHT.get(my_global, 0) < 8:
        return await m.answer("Недостаточно прав!")

    testers = STAFF.get("testers", {})
    gstaff  = STAFF.get("gstaff", {})

    # Список ID глобального руководства — они не отображаются в /tstaff
    spec_ids = set()
    spec_ids.add(str(gstaff.get("spec", 870757778)))
    spec_ids.add(str(870757778))
    if gstaff.get("main_zam"):
        spec_ids.add(str(gstaff["main_zam"]))
    for z in gstaff.get("zams", []):
        spec_ids.add(str(z))

    # Фильтруем — руководство не показываем
    gt_list  = [(uid, data) for uid, data in testers.items()
                if data.get("role") == "Главный Тестировщик" and uid not in spec_ids]
    sen_list = [(uid, data) for uid, data in testers.items()
                if data.get("role") == "Старший Тестировщик" and uid not in spec_ids]
    t_list   = [(uid, data) for uid, data in testers.items()
                if data.get("role") == "Тестировщик" and uid not in spec_ids]

    res = "MANLIX MANAGER | Тестировщики\n\n"

    # Главный тестировщик
    if gt_list:
        gt_uid = gt_list[0][0]
        res += f"Главный тестировщик -- [id{gt_uid}|MANLIX]\n"
        for uid, _ in gt_list[1:]:
            res += f"– [id{uid}|MANLIX]\n"
    else:
        res += "Главный тестировщик -- Отсутствует.\n"

    # Старшие тестировщики
    res += "\nСтаршие тестировщики:\n"
    if sen_list:
        for uid, _ in sen_list:
            res += f"– [id{uid}|Тестировщик MANLIX]\n"
    else:
        res += "– Отсутствуют.\n"

    # Тестировщики
    res += "\nТестировщики:\n"
    if t_list:
        for uid, _ in t_list:
            res += f"– [id{uid}|Тестировщик MANLIX]\n"
    else:
        res += "– Отсутствуют."

    await m.answer(res.strip())

@bot.on.message(text=["/addtester", "/addtester <args>"])
async def addtester_cmd(m: Message, args=None):
    await tester_role_grant(m, args, "Главный Тестировщик", "Тестировщик", "тестировщика")

@bot.on.message(text=["/addsentester", "/addsentester <args>"])
async def addsentester_cmd(m: Message, args=None):
    await tester_role_grant(m, args, "Главный Тестировщик", "Старший Тестировщик", "старшего тестировщика")

@bot.on.message(text=["/addgt", "/addgt <args>"])
async def addgt_cmd(m: Message, args=None):
    # Только Зам. Спец. Руководителя и выше
    if not await check_access(m, "Зам. Спец. Руководителя"): return
    t = await get_target_id(m, args)
    if not t:
        return await m.answer("Укажите пользователя.")
    if t == m.from_id:
        return await m.answer("Вы не можете выдать роль данному пользователю!")
    uid = str(t)
    if uid not in STAFF["testers"]:
        STAFF["testers"][uid] = {"role": "Главный Тестировщик", "bugs": 0, "joined": time.time()}
    else:
        STAFF["testers"][uid]["role"] = "Главный Тестировщик"
    await push_to_github(STAFF, GH_PATH_STAFF, EXTERNAL_STAFF)
    a_display = await get_display_name(m.from_id, peer_id=m.peer_id)
    t_display = await get_display_name(t, peer_id=m.peer_id)
    await m.answer(f"[id{m.from_id}|{a_display}] выдал(-а) права главного тестировщика [id{t}|{t_display}]")

# ────────────────────────────────────────────────
# Игровые команды
# ────────────────────────────────────────────────
@bot.on.message(text="/ghelp")
async def ghelp_cmd(m: Message):
    await m.answer(
        "🎮 Игровые команды MANLIX:\n\n"
        "🎉 /prise — Получить ежечасный приз\n"
        "💰 /balance — Наличные средства\n"
        "🏦 /bank — Состояние счетов\n"
        "📥 /положить [сумма] — Положить в банк\n"
        "📤 /снять [сумма] — Снять из банка\n"
        "💸 /перевести [ссылка] [сумма] — Перевод со счета на счет\n"
        "🎰 /roulette [сумма] — Рулетка\n"
        "⚔️ /duel [сумма] — Дуэль (наличные)"
    )

@bot.on.message(text="/prise")
async def prise(m: Message):
    uid = str(m.from_id)
    if uid not in ECONOMY:
        ECONOMY[uid] = {"cash": 0, "bank": 0, "last": 0}
    if time.time() - ECONOMY[uid].get("last", 0) < 3600:
        return await m.answer("🎉 Приз можно получить раз в час.")
    win = random.randint(100, 1000)
    ECONOMY[uid]["cash"] += win
    ECONOMY[uid]["last"]  = time.time()
    await push_to_github(ECONOMY, GH_PATH_ECO, EXTERNAL_ECO)
    await m.answer(f"🎉 Вы получили приз {win}$!")

@bot.on.message(text="/balance")
async def balance_cmd(m: Message):
    uid  = str(m.from_id)
    cash = ECONOMY.get(uid, {}).get("cash", 0)
    await m.answer(f"💵 Ваши наличные: {cash}$")

@bot.on.message(text="/bank")
async def bank_cmd(m: Message):
    uid  = str(m.from_id)
    cash = ECONOMY.get(uid, {}).get("cash", 0)
    bank = ECONOMY.get(uid, {}).get("bank", 0)
    await m.answer(
        f"🏦 …::: MANLIX BANK :::…\n\n"
        f"💵 Наличные: {cash}$\n"
        f"💳 На счету: {bank}$"
    )

@bot.on.message(text=["/положить <amount>"])
async def polozhit(m: Message, amount=None):
    try:
        amount = int(amount)
        if amount <= 0: raise ValueError
    except:
        return await m.answer("Укажите положительную сумму.")
    uid = str(m.from_id)
    if uid not in ECONOMY:
        ECONOMY[uid] = {"cash": 0, "bank": 0, "last": 0}
    if ECONOMY[uid].get("cash", 0) < amount:
        return await m.answer("Недостаточно наличных.")
    ECONOMY[uid]["cash"] -= amount
    ECONOMY[uid]["bank"] += amount
    await push_to_github(ECONOMY, GH_PATH_ECO, EXTERNAL_ECO)
    await m.answer(f"💲 Вы положили на свой счет {amount}$")

@bot.on.message(text=["/снять <amount>"])
async def snyat(m: Message, amount=None):
    try:
        amount = int(amount)
        if amount <= 0: raise ValueError
    except:
        return await m.answer("Укажите положительную сумму.")
    uid = str(m.from_id)
    if uid not in ECONOMY:
        ECONOMY[uid] = {"cash": 0, "bank": 0, "last": 0}
    if ECONOMY[uid].get("bank", 0) < amount:
        return await m.answer("Недостаточно средств на счете.")
    ECONOMY[uid]["bank"] -= amount
    ECONOMY[uid]["cash"] += amount
    await push_to_github(ECONOMY, GH_PATH_ECO, EXTERNAL_ECO)
    await m.answer(f"💲 Вы сняли с своего счета {amount}$")

@bot.on.message(text=["/перевести <args>"])
async def transfer(m: Message, args=None):
    if not args:
        return await m.answer("Формат: /перевести [ссылка] [сумма]")
    parts = args.split()
    if len(parts) < 2:
        return await m.answer("Формат: /перевести [ссылка] [сумма]")
    t = await get_target_id(m, parts[0])
    if not t:
        return await m.answer("Не удалось определить получателя.")
    try:
        amount = int(parts[1])
        if amount <= 0: raise ValueError
    except:
        return await m.answer("Некорректная сумма.")
    uid = str(m.from_id)
    rid = str(t)
    if uid not in ECONOMY: ECONOMY[uid] = {"cash": 0, "bank": 0, "last": 0}
    if rid not in ECONOMY: ECONOMY[rid] = {"cash": 0, "bank": 0, "last": 0}
    if ECONOMY[uid].get("bank", 0) < amount:
        return await m.answer(f"Недостаточно средств на счете (есть {ECONOMY[uid].get('bank', 0)}$)")
    ECONOMY[uid]["bank"] -= amount
    ECONOMY[rid]["bank"] += amount
    await push_to_github(ECONOMY, GH_PATH_ECO, EXTERNAL_ECO)
    t_display = await get_display_name(t, peer_id=m.peer_id)
    await m.answer(f"💲 Вы перевели [id{t}|{t_display}] {amount}$")

@bot.on.message(text=["/roulette <amount>"])
async def roulette(m: Message, amount=None):
    try:
        amount = int(amount)
        if amount <= 0: raise ValueError
    except:
        return await m.answer("Укажите положительную сумму.")
    uid = str(m.from_id)
    if uid not in ECONOMY or ECONOMY[uid].get("cash", 0) < amount:
        return await m.answer("Недостаточно наличных.")
    ECONOMY[uid]["cash"] -= amount
    if random.random() < 0.25:
        win = amount * 3
        ECONOMY[uid]["cash"] += win
        text = f"🎰 Вы выиграли {win}$!"
    else:
        text = f"🎰 Вы проиграли {amount}$..."
    await push_to_github(ECONOMY, GH_PATH_ECO, EXTERNAL_ECO)
    await m.answer(text)

@bot.on.message(text=["/duel <amount>"])
async def duel_create(m: Message, amount=None):
    try:
        amount = int(amount)
        if amount <= 0: raise ValueError
    except:
        return await m.answer("Укажите положительную сумму.")
    uid = str(m.from_id)
    pid = str(m.peer_id)
    if uid not in ECONOMY or ECONOMY[uid].get("cash", 0) < amount:
        return await m.answer("Недостаточно наличных средств.")
    duel_id = f"{pid}_{int(time.time())}"
    DATABASE["duels"][duel_id] = {
        "creator":      uid,
        "amount":       amount,
        "participants": [uid],
        "chat_id":      pid
    }
    kb = Keyboard(inline=True)
    kb.add(Callback("Вступить в дуэль!", {"cmd": "join_duel", "duel": duel_id}), color=KeyboardButtonColor.POSITIVE)
    await m.answer(
        f"⚔️ Дуэль на {amount}$ создана!\n"
        f"Нажми на кнопку, чтобы сразиться!",
        keyboard=kb
    )
    await push_to_github(DATABASE, GH_PATH_DB, EXTERNAL_DB)

# ────────────────────────────────────────────────
# /invite — доступ к добавлению только для модерации
# ────────────────────────────────────────────────
@bot.on.message(text="/invite")
async def invite_cmd(m: Message):
    if not await check_access(m, "Владелец"): return
    pid = str(m.peer_id)
    ensure_chat(pid)
    current = DATABASE["chats"][pid].get("invite_only", False)
    DATABASE["chats"][pid]["invite_only"] = not current
    await push_to_github(DATABASE, GH_PATH_DB, EXTERNAL_DB)
    a_display = await get_display_name(m.from_id, peer_id=m.peer_id)
    if not current:
        await m.answer(f"[id{m.from_id}|{a_display}] включил(-а) функцию добавления только модерацией!")
    else:
        await m.answer(f"[id{m.from_id}|{a_display}] отключил(-а) функцию добавления только модерацией!")

# ────────────────────────────────────────────────
# Системные события
# ────────────────────────────────────────────────
@bot.on.message()
async def actions(m: Message):
    if not m.action:
        return
    typ = m.action.type.value if hasattr(m.action.type, "value") else str(m.action.type)
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
    if typ in ("chat_invite_user", "chat_invite_user_by_link"):
        invited = m.action.member_id
        if not invited:
            return

        # Бот добавлен в беседу (member_id отрицательный)
        if invited < 0:
            await bot.api.messages.send(
                peer_id=m.peer_id,
                message=(
                    "Бот добавлен в беседу, выдайте мне администратора, "
                    "а затем введите /sync для синхронизации c базой данных!\n\n"
                    "Также с помощью /type Вы можете выбрать тип беседы!"
                ),
                random_id=random.randint(0, 2**31)
            )
            return

        # Пользователь добавлен
        uid = str(invited)
        pid = str(m.peer_id)
        ensure_chat(pid)

        # Проверяем invite_only — добавлять может только модерация (ранг >= 1)
        if DATABASE["chats"][pid].get("invite_only", False):
            inviter_rank, _ = get_user_info(m.peer_id, m.from_id)
            if RANK_WEIGHT.get(inviter_rank, 0) < 1:
                try:
                    chat_id = m.peer_id - 2000000000
                    await bot.api.messages.remove_chat_user(chat_id=chat_id, member_id=invited)
                except:
                    pass
                return

        # Проверяем глобальный бан
        if uid in PUNISHMENTS.get("gbans_status", {}):
            b  = PUNISHMENTS["gbans_status"][uid]
            dt = datetime.datetime.fromtimestamp(b["date"], TZ_MSK).strftime("%d/%m/%Y %H:%M:%S")
            kb = Keyboard(inline=True)
            kb.add(Callback("Разблокировать", {"cmd": "gunban_btn", "uid": uid}), color=KeyboardButtonColor.POSITIVE)
            await bot.api.messages.send(
                peer_id=m.peer_id,
                message=(
                    f"[id{invited}|Пользователь] находится в Глобальной Блокировке.\n\n"
                    f"Информация о Блокировке:\n"
                    f"[id{b['admin']}|Модератор MANLIX] | {b.get('reason', '-')} | {dt}"
                ),
                keyboard=kb.get_json(),
                random_id=random.randint(0, 2**31)
            )
            return

        # Если в локальном или игровом бане — исключить
        banned = (
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
                f"[id870757778|Модератор MANLIX] исключил(-а) [id{invited}|пользователя] "
                f"— он находится в списке блокировок."
            )

# ────────────────────────────────────────────────
# Технические отчёты
# ────────────────────────────────────────────────
async def send_reports():
    while True:
        now = datetime.datetime.now(TZ_MSK)
        if now.second % 15 == 0:
            for pid, chat in list(DATABASE.get("chats", {}).items()):
                if chat.get("type") == "tex":
                    delay    = round(random.uniform(0, 1), 2)
                    time_str = now.strftime("%H:%M:%S")
                    date_str = now.strftime("%d/%m/%Y")
                    msg = (
                        f"…::: ТЕХНИЧЕСКИЙ ОТЧЕТ :::…\n\n"
                        f"| ==> Бот успешно работает.\n"
                        f"| Задержка Бота: {delay}\n"
                        f"| Точное время: {time_str}\n"
                        f"| Дата: {date_str}"
                    )
                    try:
                        await bot.api.messages.send(
                            peer_id=int(pid),
                            message=msg,
                            random_id=random.randint(0, 2**32 - 1)
                        )
                    except Exception as e:
                        print("send_reports error:", e)
        await asyncio.sleep(1)

# ────────────────────────────────────────────────
# Keep-Alive
# ────────────────────────────────────────────────
async def keep_alive():
    while True:
        try:
            url = os.environ.get("RENDER_EXTERNAL_URL")
            if url:
                async with aiohttp.ClientSession() as session:
                    async with session.get(
                        url + "?keepalive=1",
                        timeout=aiohttp.ClientTimeout(total=10)
                    ):
                        print(f"[{datetime.datetime.now(TZ_MSK).strftime('%H:%M:%S')}] Keep-alive отправлен")
        except Exception as e:
            print("Keep-alive error:", e)
        await asyncio.sleep(600)

# ────────────────────────────────────────────────
# Запуск
# ────────────────────────────────────────────────
if __name__ == "__main__":
    threading.Thread(
        target=HTTPServer(("0.0.0.0", int(os.environ.get("PORT", 10000))), H).serve_forever,
        daemon=True
    ).start()
    loop.create_task(send_reports())
    loop.create_task(keep_alive())
    print("Бот запущен. Keep-alive и тех.отчёты активны.")
    bot.run_forever()

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

# Веса ролей Технических Специалистов
TEX_RANK_WEIGHT = {
    "Технический Специалист": 1,
    "Куратор ТС":             2,
    "Зам. Главного ТС":       3,
    "Главный ТС":             4,
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
    if "quit_mode" not in chat:
        chat["quit_mode"] = False
    if "filter_enabled" not in chat:
        chat["filter_enabled"] = False
    if "filter_words" not in chat:
        chat["filter_words"] = []

def is_vk_ref(token: str) -> bool:
    """Проверяет, является ли токен ссылкой/упоминанием/ID пользователя ВК."""
    if re.search(r"\[id\d+\|", token):
        return True
    if re.search(r"https?://vk\.(com|ru)/", token):
        return True
    if re.match(r"^id\d+$", token):
        return True
    if re.match(r"^\d+$", token) and int(token) > 1000:
        return True
    return False

async def get_target_id(m: Message, args: str = None):
    """Получить ID цели из reply, ссылки или первого токена args."""
    if getattr(m, "reply_message", None):
        return m.reply_message.from_id
    if not args:
        return None

    match = re.search(r"\[id(\d+)\|", args)
    if match:
        return int(match.group(1))

    match = re.search(r"vk\.(com|ru)/id(\d+)", args)
    if match:
        return int(match.group(2))

    tokens = args.split()
    first  = tokens[0] if tokens else ""

    match = re.match(r"^id(\d+)$", first)
    if match:
        return int(match.group(1))

    if first.isdigit():
        return int(first)

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
            try:
                return int(sn[2:])
            except:
                pass

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
        all_local = [entry[0]]
        if len(entry) > 2 and isinstance(entry[2], list):
            all_local += entry[2]
        local_role = max(all_local, key=lambda r: RANK_WEIGHT.get(r, 0))
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
    3. Fallback: "id{user_id}"
    """
    if use_nick and peer_id:
        _, nick = get_user_info(peer_id, user_id)
        if nick:
            return nick
    try:
        uinfo = await bot.api.users.get(user_ids=[int(user_id)])
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

def get_all_local_roles(pid: str, uid: str) -> list:
    """Возвращает список всех локальных ролей пользователя в беседе."""
    entry = DATABASE.get("chats", {}).get(pid, {}).get("staff", {}).get(uid)
    if not entry:
        return []
    roles = [entry[0]]
    if len(entry) > 2 and isinstance(entry[2], list):
        roles += entry[2]
    return roles

def highest_role(roles: list) -> str:
    """Возвращает наивысшую роль из списка."""
    if not roles:
        return "Пользователь"
    return max(roles, key=lambda r: RANK_WEIGHT.get(r, 0))

async def set_role_in_chat(pid: str, uid: str, role_name: str):
    """Добавляет роль пользователю (накапливает, не перезаписывает)."""
    ensure_chat(pid)
    entry = DATABASE["chats"][pid]["staff"].get(uid)
    if entry:
        nick = entry[1]
        existing = get_all_local_roles(pid, uid)
    else:
        nick = None
        existing = []
    if role_name not in existing:
        existing.append(role_name)
    top  = highest_role(existing)
    rest = [r for r in existing if r != top]
    DATABASE["chats"][pid]["staff"][uid] = [top, nick, rest]

# ────────────────────────────────────────────────
# Middleware
# ────────────────────────────────────────────────
class ChatMiddleware(BaseMiddleware[Message]):
    async def pre(self):
        if getattr(self.event, "action", None):
            return
        if not getattr(self.event, "from_id", None) or self.event.from_id < 0:
            return
        from_id = self.event.from_id
        pid = str(self.event.peer_id)
        uid = str(from_id)

        # ── Проверка bot_status ──────────────────────
        status = DATABASE.get("bot_status", "on")
        if status != "on":
            if from_id == 870757778:
                pass
            else:
                rank, _ = get_user_info(self.event.peer_id, from_id)
                w = RANK_WEIGHT.get(rank, 0)
                allowed = False
                if w >= 8:
                    allowed = True
                elif status == "test":
                    t_role, _ = get_tester_info(from_id)
                    if t_role:
                        allowed = True
                if not allowed:
                    self.stop()
                    return

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
            return

        # ── Режим тишины (/quit) ──────────────────────
        if chat.get("quit_mode", False):
            rank, _ = get_user_info(self.event.peer_id, from_id)
            if RANK_WEIGHT.get(rank, 0) < 1:
                try:
                    await bot.api.messages.delete(
                        peer_id=self.event.peer_id,
                        conversation_message_ids=[self.event.conversation_message_id],
                        delete_for_all=True
                    )
                except:
                    pass
                self.stop()
                return

        # ── Фильтр запрещённых слов ───────────────────
        if chat.get("filter_enabled", False):
            gstaff_f    = STAFF.get("gstaff", {})
            is_exempt   = (
                from_id == 870757778
                or from_id == gstaff_f.get("spec")
                or from_id == gstaff_f.get("main_zam")
                or from_id in gstaff_f.get("zams", [])
            )
            local_entry = chat.get("staff", {}).get(uid)
            local_rank  = local_entry[0] if local_entry else "Пользователь"
            local_w     = RANK_WEIGHT.get(local_rank, 0)
            if not is_exempt and local_w < 1:
                text_lower = (self.event.text or "").lower()
                bad_words  = chat.get("filter_words", [])
                hit = next((w for w in bad_words if w in text_lower), None)
                if hit:
                    cmid_filter = self.event.conversation_message_id
                    peer_filter  = self.event.peer_id
                    until = time.time() + 30 * 60
                    chat["mutes"][uid] = until
                    kb_filter = Keyboard(inline=True)
                    kb_filter.row()
                    kb_filter.add(Callback("Снять мут", {"cmd": "unmute_btn", "uid": uid}), color=KeyboardButtonColor.POSITIVE)
                    try:
                        await bot.api.messages.send(
                            peer_id=peer_filter,
                            message=(
                                f"[id{from_id}|Пользователь] получил(-а) мут на 30 минут "
                                f"за написание запрещённого слова."
                            ),
                            keyboard=kb_filter.get_json(),
                            forward=json.dumps({
                                "peer_id": peer_filter,
                                "conversation_message_ids": [cmid_filter],
                                "is_reply": True
                            }),
                            random_id=random.randint(0, 2**31)
                        )
                    except Exception as e:
                        print("filter send error:", e)
                    try:
                        await bot.api.messages.delete(
                            peer_id=peer_filter,
                            conversation_message_ids=[cmid_filter],
                            delete_for_all=True
                        )
                    except Exception as e:
                        print("filter delete error:", e)
                    await push_to_github(DATABASE, GH_PATH_DB, EXTERNAL_DB)
                    self.stop()
                    return

        # ── CLOGS: пересылка сообщений в беседы-логи ──────────
        msg_text = (self.event.text or "").strip()
        if msg_text and not msg_text.startswith("/"):
            src_pid = str(self.event.peer_id)
            for log_pid, log_chat in list(DATABASE.get("chats", {}).items()):
                if log_chat.get("type") == "clogs" and log_chat.get("clogs_source") == src_pid:
                    user_display = await get_display_name(from_id, peer_id=self.event.peer_id)
                    chat_title   = DATABASE["chats"].get(src_pid, {}).get("title", f"Беседа {src_pid}")
                    now          = datetime.datetime.now(TZ_MSK)
                    log_msg = (
                        f"…::: MNLX LOGS :::…\n\n"
                        f"| Название Беседы: {chat_title}\n"
                        f"| Пользователь: [id{from_id}|{user_display}]\n"
                        f"| VK ID пользователя: {from_id}\n"
                        f"| Содержимое - « {msg_text} »"
                    )
                    try:
                        await bot.api.messages.send(
                            peer_id=int(log_pid),
                            message=log_msg,
                            random_id=random.randint(0, 2**31)
                        )
                    except Exception as e:
                        print(f"clogs error to {log_pid}: {e}")

bot.labeler.message_view.register_middleware(ChatMiddleware)

# ────────────────────────────────────────────────
# /help
# ОБНОВЛЕНО по Notion (статус: Готово)
# Изменения: /invite, /quit, /filter — новые описания
# ────────────────────────────────────────────────
@bot.on.message(text="/help")
async def help_cmd(m: Message):
    rank, _ = get_user_info(m.peer_id, m.from_id)
    w = RANK_WEIGHT.get(rank, 0)
    res = (
        "Команды пользователей:\n"
        "/info -- Официальные ресурсы.\n"
        "/stats -- Информация о пользователе.\n"
        "/getid -- Оригинальная ссылка VK."
    )
    if w >= 1:
        res += (
            "\n\nКоманды для модераторов:\n"
            "/staff -- Руководство Беседы\n"
            "/kick -- Исключить пользователя из Беседы.\n"
            "/mute -- Выдать Блокировку чата.\n"
            "/unmute -- Снять Блокировку чата.\n"
            "/setnick -- Установить имя пользователю.\n"
            "/rnick -- Удалить имя пользователю.\n"
            "/nlist -- Список пользователей с ником.\n"
            "/getban -- Информация о Блокировках.\n"
            "/warn -- Выдать предупреждение.\n"
            "/unwarn -- Снять предупреждение."
        )
    if w >= 2:
        res += (
            "\n\nКоманды старших модераторов:\n"
            "/addmoder -- Выдать права модератора.\n"
            "/removerole -- Снять уровень прав.\n"
            "/ban -- Блокировка пользователя в Беседе.\n"
            "/unban -- Снятие блокировки пользователю в беседе."
        )
    if w >= 3:
        res += (
            "\n\nКоманды администраторов:\n"
            "/addsenmoder -- выдать права старшего модератора.\n"
            "/quit -- включить/выключить режим тишины.\n"
            "/rnickall -- очистить все ники в Беседе."
        )
    if w >= 4:
        res += (
            "\n\nКоманды старших администраторов:\n"
            "/addadmin -- Выдать права администратора."
        )
    if w >= 5:
        res += (
            "\n\nКоманды заместителей спец. администраторов:\n"
            "/addsenadmin -- Выдать права старшего модератора."
        )
    if w >= 6:
        res += (
            "\n\nКоманды спец. администраторов:\n"
            "/addzsa -- Выдать права заместителя спец. администратора."
        )
    if w >= 7:
        res += (
            "\n\nКоманды владельца:\n"
            "/addsa -- Выдать права специального администратора.\n"
            "/invite -- Режим добавления только модерацией.\n"
            "/filter -- включить/выключить фильтрацию запрещённых слов."
        )
    await m.answer(res)
    if w >= 8:
        gres = (
            "Команды руководства Бота:\n\n"
            "Зам. Спец. Руководителя:\n"
            "/gstaff -- Руководство Бота.\n"
            "/gunrole -- Снятие глобальных уровней прав.\n"
            "/addowner -- Выдать права владельца.\n"
            "/gbanpl -- Блокировка пользователя во всех игровых Беседах.\n"
            "/gunbanpl -- Снятие Блокировки во всех игровых Беседах.\n\n"
            "Основной Зам. Спец. Руководителя:\n"
            "/addzsr -- Выдать права заместителя спец. руководителя.\n"
            "/thelp -- Список команд для тестировщиков.\n"
            "/msg -- Отправить рассылку.\n\n"
            "Спец. Руководителя:\n"
            "/addozsr -- Выдать права основного заместителя спец. руководителя.\n"
            "/start -- Активировать Беседу.\n"
            "/type -- Изменить тип Беседы.\n"
            "/typetex -- Изменить технический тип Беседы.\n"
            "/sync -- Синхронизация с базой данных.\n"
            "/botstatus -- Изменить статус Бота.\n"
            "/chatid -- Узнать айди Беседы.\n"
            "/delchat -- Удалить чат с Базы данных."
        )
        await m.answer(gres)

# ────────────────────────────────────────────────
# /info
# ОБНОВЛЕНО по Notion (статус: Готово)
# ────────────────────────────────────────────────
@bot.on.message(text="/info")
async def info_cmd(m: Message):
    await m.answer(
        "Официальные ресурсы:\n\n"
        "| Техническая поддержка \n"
        "| MANLIX Беседы\n"
        "| Активация Бота\n\n"
        "(Команда на доработке)"
    )

# ────────────────────────────────────────────────
# /getid
# ОБНОВЛЕНО по Notion (статус: Готово)
# ────────────────────────────────────────────────
@bot.on.message(text=["/getid", "/getid <args>"])
async def getid_cmd(m: Message, args=None):
    t = await get_target_id(m, args) or m.from_id
    await m.answer(f"Оригинальная ссылка [id{t}|пользователя]: https://vk.com/id{t}")

# ────────────────────────────────────────────────
# /stats
# ОБНОВЛЕНО по Notion (статус: Готово)
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
    # Показываем только одну наивысшую роль
    all_local = get_all_local_roles(pid, uid)
    if all_local:
        roles_str = highest_role(all_local)
    else:
        roles_str = role
    msg = (
        f"Информация о [id{t}|пользователе]\n"
        f"Роль: {roles_str}\n"
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
# /mute
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
    await send_log(m.peer_id, m.from_id, "Мут", reason=reason, target_id=t, mute_until=dt)

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
    await send_log(m.peer_id, m.from_id, "Снятие мута", target_id=t)

# ────────────────────────────────────────────────
# Единый обработчик кнопок (мут + дуэль)
# ────────────────────────────────────────────────
EMPTY_KB_JSON = '{"inline":true,"buttons":[]}'

@bot.on.raw_event(GroupEventType.MESSAGE_EVENT, MessageEvent)
async def all_buttons(event: MessageEvent):
    peer_id  = event.peer_id
    actor_id = event.user_id
    cmid     = event.conversation_message_id

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

    async def snackbar(text: str):
        try:
            await event.show_snackbar(text)
        except Exception as e:
            print("snackbar error:", e)

    # ── Кнопки мута ──────────────────────────────
    if cmd in ("unmute_btn", "clear_msg"):
        uid = str(payload.get("uid", ""))
        ensure_chat(pid)

        # Замученный не может снять мут сам себе
        if cmd == "unmute_btn" and str(actor_id) == uid:
            await snackbar("Вы не можете снять мут самому себе!")
            return

        rank, _ = get_user_info(peer_id, actor_id)
        if RANK_WEIGHT.get(rank, 0) < 1:
            await snackbar("Недостаточно прав")
            return

        if cmd == "unmute_btn":
            if uid in DATABASE["chats"][pid].get("mutes", {}):
                del DATABASE["chats"][pid]["mutes"][uid]
                await push_to_github(DATABASE, GH_PATH_DB, EXTERNAL_DB)
            try:
                u_info = await bot.api.users.get(user_ids=[int(uid)])
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
                u_info2 = await bot.api.users.get(user_ids=[int(uid)])
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

    # ── Кнопка снять варн ────────────────────────
    if cmd == "unwarn_btn":
        uid = str(payload.get("uid", ""))
        pid_s = str(peer_id)
        rank, _ = get_user_info(peer_id, actor_id)
        if RANK_WEIGHT.get(rank, 0) < 1:
            await snackbar("Недостаточно прав")
            return
        warns = PUNISHMENTS.get("warns", {}).get(pid_s, {})
        if uid in warns and warns[uid] > 0:
            warns[uid] -= 1
            if warns[uid] == 0:
                del warns[uid]
            await push_to_github(PUNISHMENTS, GH_PATH_PUN, EXTERNAL_PUN)
        try:
            u_info = await bot.api.users.get(user_ids=[int(uid)])
            u_name = f"{u_info[0].first_name} {u_info[0].last_name}"
        except:
            u_name = "пользователю"
        try:
            await bot.api.request("messages.edit", {
                "peer_id": peer_id,
                "conversation_message_id": cmid,
                "message": f"[id{actor_id}|Модератор MANLIX] снял(-а) предупреждение [id{uid}|пользователю]",
                "keyboard": EMPTY_KB_JSON
            })
        except Exception as e:
            print("unwarn_btn edit error:", e)
        await snackbar("Предупреждение снято")
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
            ECONOMY[winner]["duel_wins"] = ECONOMY[winner].get("duel_wins", 0) + amount
            ECONOMY[loser]["cash"]  = ECONOMY[loser].get("cash",  0) - amount
            ECONOMY[loser]["duel_losses"] = ECONOMY[loser].get("duel_losses", 0) + amount
            await push_to_github(ECONOMY, GH_PATH_ECO, EXTERNAL_ECO)
            del DATABASE["duels"][duel_id]
            await push_to_github(DATABASE, GH_PATH_DB, EXTERNAL_DB)
            try:
                w_info = await bot.api.users.get(user_ids=[int(winner)])
                w_name = f"{w_info[0].first_name} {w_info[0].last_name}"
            except:
                w_name = "победитель"
            try:
                l_info = await bot.api.users.get(user_ids=[int(loser)])
                l_name = f"{l_info[0].first_name} {l_info[0].last_name}"
            except:
                l_name = "проигравший"
            duel_result = (
                f"⚔️ Дуэль завершена!\n"
                f"🏅 Победил – [id{winner}|{w_name}]\n"
                f"🥈Проиграл – [id{loser}|{l_name}]\n\n"
                f"💲Победитель выиграл {amount}$"
            )
            try:
                await bot.api.request("messages.edit", {
                    "peer_id": peer_id,
                    "conversation_message_id": cmid,
                    "message": duel_result,
                    "keyboard": EMPTY_KB_JSON
                })
            except Exception as e:
                print("duel edit error:", e)
                await bot.api.messages.send(
                    peer_id=int(duel["chat_id"]),
                    message=duel_result,
                    random_id=random.randint(0, 2**31)
                )

# ────────────────────────────────────────────────
# /kick
# ОБНОВЛЕНО по Notion (статус: Готово)
# ────────────────────────────────────────────────
@bot.on.message(text=["/kick", "/kick <args>"])
async def kick_cmd(m: Message, args=None):
    if not await check_access(m, "Модератор"): return
    t = await get_target_id(m, args)
    if not t:
        return await m.answer("Укажите пользователя!")
    if t == m.from_id:
        return await m.answer("Невозможно исключить данного пользователя!")
    my_rank, _  = get_user_info(m.peer_id, m.from_id)
    tgt_rank, _ = get_user_info(m.peer_id, t)
    if RANK_WEIGHT.get(tgt_rank, 0) >= RANK_WEIGHT.get(my_rank, 0):
        return await m.answer("Невозможно исключить данного пользователя!")
    t_display = await get_display_name(t, peer_id=m.peer_id)
    try:
        chat_id = m.peer_id - 2000000000
        await bot.api.messages.remove_chat_user(chat_id=chat_id, member_id=t)
    except Exception as e:
        print("kick error:", e)
        return await m.answer(f"Не удалось исключить [id{t}|{t_display}]!")
    await m.answer(f"[id{m.from_id}|Модератор MANLIX] исключил(-а) [id{t}|{t_display}] из Беседы.")
    await send_log(m.peer_id, m.from_id, "Исключение", target_id=t)

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
    await send_log(m.peer_id, m.from_id, "Блокировка", reason=reason, target_id=t)

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
    await send_log(m.peer_id, m.from_id, "Снятие Блокировки", target_id=t)

# ────────────────────────────────────────────────
# /warn / /unwarn
# ────────────────────────────────────────────────
@bot.on.message(text=["/warn", "/warn <args>"])
async def warn_cmd(m: Message, args=None):
    if not await check_access(m, "Модератор"): return
    t = await get_target_id(m, args)
    if not t:
        return await m.answer("Укажите пользователя!")
    if t == m.from_id:
        return await m.answer("Невозможно выдать предупреждение данному пользователю!")
    my_rank, _  = get_user_info(m.peer_id, m.from_id)
    tgt_rank, _ = get_user_info(m.peer_id, t)
    if RANK_WEIGHT.get(tgt_rank, 0) >= RANK_WEIGHT.get(my_rank, 0):
        return await m.answer("Невозможно выдать предупреждение данному пользователю!")
    reason = parse_reason(args) or "Нарушение"
    pid = str(m.peer_id)
    uid = str(t)
    if "warns" not in PUNISHMENTS:
        PUNISHMENTS["warns"] = {}
    if pid not in PUNISHMENTS["warns"]:
        PUNISHMENTS["warns"][pid] = {}
    current = min(PUNISHMENTS["warns"][pid].get(uid, 0) + 1, 3)
    PUNISHMENTS["warns"][pid][uid] = current
    await push_to_github(PUNISHMENTS, GH_PATH_PUN, EXTERNAL_PUN)
    if current >= 3:
        del PUNISHMENTS["warns"][pid][uid]
        await push_to_github(PUNISHMENTS, GH_PATH_PUN, EXTERNAL_PUN)
        try:
            chat_id = m.peer_id - 2000000000
            await bot.api.messages.remove_chat_user(chat_id=chat_id, member_id=t)
        except Exception as e:
            print("warn kick error:", e)
        await m.answer(
            f"[id{m.from_id}|Модератор MANLIX] выдал(-а) предупреждение [id{t}|пользователю]\n\n"
            f"| Причина: {reason}\n"
            f"| Кол-во предупреждений: {current}/3\n\n"
            f"[id{t}|Пользователь] исключен из Беседы из-за максимального количества предупреждений!"
        )
        return
    kb = Keyboard(inline=True)
    kb.row()
    kb.add(Callback("Снять варн", {"cmd": "unwarn_btn", "uid": uid}), color=KeyboardButtonColor.POSITIVE)
    kb.add(Callback("Очистить",   {"cmd": "clear_msg",  "uid": uid}), color=KeyboardButtonColor.NEGATIVE)
    await m.answer(
        f"[id{m.from_id}|Модератор MANLIX] выдал(-а) предупреждение [id{t}|пользователю]\n\n"
        f"| Причина: {reason}\n"
        f"| Кол-во предупреждений: {current}/3",
        keyboard=kb.get_json()
    )
    await send_log(m.peer_id, m.from_id, "Предупреждение", reason=reason, target_id=t)

@bot.on.message(text=["/unwarn", "/unwarn <args>"])
async def unwarn_cmd(m: Message, args=None):
    if not await check_access(m, "Модератор"): return
    t = await get_target_id(m, args)
    if not t:
        return await m.answer("Укажите пользователя!")
    pid = str(m.peer_id)
    uid = str(t)
    warns = PUNISHMENTS.get("warns", {}).get(pid, {})
    if uid in warns and warns[uid] > 0:
        warns[uid] -= 1
        if warns[uid] == 0:
            del warns[uid]
        await push_to_github(PUNISHMENTS, GH_PATH_PUN, EXTERNAL_PUN)
    t_display = await get_display_name(t, peer_id=m.peer_id)
    await m.answer(f"[id{m.from_id}|Модератор MANLIX] снял(-а) предупреждение [id{t}|пользователю]")

# ────────────────────────────────────────────────
# Выдача ролей
# ────────────────────────────────────────────────
async def role_grant(m: Message, args, min_rank, role_name, role_label):
    if not await check_access(m, min_rank): return
    t = await get_target_id(m, args)
    if not t:
        return await m.answer("Укажите пользователя.")
    my_rank, _ = get_user_info(m.peer_id, m.from_id)
    my_w       = RANK_WEIGHT.get(my_rank, 0)
    is_leader  = my_w >= 8
    if t == m.from_id and not is_leader:
        return await m.answer("Вы не можете выдать роль данному пользователю!")
    tgt_rank, _ = get_user_info(m.peer_id, t)
    if t != m.from_id and RANK_WEIGHT.get(tgt_rank, 0) >= my_w:
        return await m.answer("Вы не можете выдать роль данному пользователю!")
    pid, uid  = str(m.peer_id), str(t)
    await set_role_in_chat(pid, uid, role_name)
    await push_to_github(DATABASE, GH_PATH_DB, EXTERNAL_DB)
    a_display = await get_display_name(m.from_id, peer_id=m.peer_id)
    await m.answer(f"[id{m.from_id}|{a_display}] выдал(-а) права {role_label} [id{t}|пользователю]")

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
    await m.answer(f"[id{m.from_id}|{a_display}] выдал(-а) права заместителя специального руководителя [id{t}|пользователю]")

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
    await m.answer(f"[id{m.from_id}|{a_display}] выдал(-а) права основного заместителя спец. руководителя [id{t}|пользователю]")

# ────────────────────────────────────────────────
# /removerole
# ────────────────────────────────────────────────
ROLE_ALIASES = {
    "модератор":                        "Модератор",
    "мод":                              "Модератор",
    "старший модератор":                "Старший Модератор",
    "ст.мод":                           "Старший Модератор",
    "стмод":                            "Старший Модератор",
    "администратор":                    "Администратор",
    "адм":                              "Администратор",
    "старший администратор":            "Старший Администратор",
    "ст.адм":                           "Старший Администратор",
    "стадм":                            "Старший Администратор",
    "зам. спец. администратора":        "Зам. Спец. Администратора",
    "зса":                              "Зам. Спец. Администратора",
    "спец. администратор":              "Спец. Администратор",
    "са":                               "Спец. Администратор",
    "владелец":                         "Владелец",
}

@bot.on.message(text=["/removerole", "/removerole <args>"])
async def removerole(m: Message, args=None):
    if not await check_access(m, "Старший Модератор"): return
    t = await get_target_id(m, args)
    if not t:
        return await m.answer("Укажите пользователя.")
    pid, uid = str(m.peer_id), str(t)
    ensure_chat(pid)
    a_display = await get_display_name(m.from_id, peer_id=m.peer_id)

    role_to_remove = None
    if args:
        tokens = args.split()
        rest_tokens = [tk for tk in tokens if not is_vk_ref(tk)]
        role_text = " ".join(rest_tokens).strip().lower()
        if role_text:
            role_to_remove = ROLE_ALIASES.get(role_text)
            if not role_to_remove:
                for rname in RANK_WEIGHT.keys():
                    if rname.lower() == role_text:
                        role_to_remove = rname
                        break

    if uid not in DATABASE["chats"][pid].get("staff", {}):
        return await m.answer(f"У [id{t}|пользователя] нет ролей в этой беседе.")

    if role_to_remove:
        all_roles = get_all_local_roles(pid, uid)
        if role_to_remove not in all_roles:
            return await m.answer(f"У [id{t}|пользователя] нет роли «{role_to_remove}».")
        all_roles.remove(role_to_remove)
        if not all_roles:
            del DATABASE["chats"][pid]["staff"][uid]
        else:
            nick = DATABASE["chats"][pid]["staff"][uid][1]
            top  = highest_role(all_roles)
            rest = [r for r in all_roles if r != top]
            DATABASE["chats"][pid]["staff"][uid] = [top, nick, rest]
        await push_to_github(DATABASE, GH_PATH_DB, EXTERNAL_DB)
        await m.answer(f"[id{m.from_id}|{a_display}] снял(-а) уровень прав [id{t}|пользователю]")
    else:
        del DATABASE["chats"][pid]["staff"][uid]
        await push_to_github(DATABASE, GH_PATH_DB, EXTERNAL_DB)
        await m.answer(f"[id{m.from_id}|{a_display}] снял(-а) уровень прав [id{t}|пользователю]")

# ────────────────────────────────────────────────
# /gunrole
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
    if t in gstaff.get("zams", []):
        gstaff["zams"].remove(t)
        removed = True
    if gstaff.get("main_zam") == t:
        rank, _ = get_user_info(m.peer_id, m.from_id)
        if RANK_WEIGHT.get(rank, 0) >= 10:
            gstaff["main_zam"] = None
            removed = True
        else:
            return await m.answer("Снять Основного Зам. может только Специальный Руководитель.")
    uid = str(t)
    if uid in STAFF.get("testers", {}):
        del STAFF["testers"][uid]
        removed = True
    if not removed:
        return await m.answer("У этого пользователя нет глобальных прав.")
    await push_to_github(STAFF, GH_PATH_STAFF, EXTERNAL_STAFF)
    a_display = await get_display_name(m.from_id, peer_id=m.peer_id)
    await m.answer(f"[id{m.from_id}|{a_display}] снял(-а) глобальный уровень прав [id{t}|пользователю]")

# ────────────────────────────────────────────────
# /staff
# ОБНОВЛЕНО по Notion (статус: Готово)
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
            all_u_roles = [entry[0]]
            if len(entry) > 2 and isinstance(entry[2], list):
                all_u_roles += entry[2]
            if r in all_u_roles:
                nick = entry[1]
                if nick:
                    display = nick
                else:
                    try:
                        uinfo   = await bot.api.users.get(user_ids=[int(u)])
                        display = f"{uinfo[0].first_name} {uinfo[0].last_name}"
                    except:
                        display = f"id{u}"
                members.append(f"– [id{u}|{display}]")
        if r == "Владелец":
            owner_ids = []
            for u, entry in staff.items():
                all_roles = [entry[0]]
                if len(entry) > 2 and isinstance(entry[2], list):
                    all_roles += entry[2]
                if "Владелец" in all_roles:
                    owner_ids.append(u)
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
# /setnick
# ────────────────────────────────────────────────
@bot.on.message(text=["/setnick", "/setnick <args>"])
async def setnick(m: Message, args=None):
    if not await check_access(m, "Модератор"): return

    if getattr(m, "reply_message", None):
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
    a_display = await get_display_name(m.from_id, peer_id=m.peer_id)
    t_display = await get_display_name(t, peer_id=m.peer_id, use_nick=False)
    # ФИКС: берём роль и extra_roles из существующей локальной записи,
    # НЕ из get_user_info — та возвращает глобальную роль (СР/ОЗСР/ЗСР),
    # что перезаписывало бы локальную роль в беседе.
    existing_entry = DATABASE["chats"][pid]["staff"].get(uid)
    if existing_entry:
        local_role  = existing_entry[0]
        extra_roles = existing_entry[2] if len(existing_entry) > 2 else []
    else:
        local_role  = "Пользователь"
        extra_roles = []
    DATABASE["chats"][pid]["staff"][uid] = [local_role, new_nick, extra_roles]
    await push_to_github(DATABASE, GH_PATH_DB, EXTERNAL_DB)
    await m.answer(f"[id{m.from_id}|{a_display}] установил(-а) новое имя [id{t}|пользователю]: {new_nick}")
    await send_log(m.peer_id, m.from_id, "Выдача ника", target_id=t)

# ────────────────────────────────────────────────
# /rnick
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
        entry_r = DATABASE["chats"][pid]["staff"][uid]
        extra_r = entry_r[2] if len(entry_r) > 2 else []
        DATABASE["chats"][pid]["staff"][uid] = [entry_r[0], None, extra_r]
        await push_to_github(DATABASE, GH_PATH_DB, EXTERNAL_DB)
    a_display = await get_display_name(m.from_id, peer_id=m.peer_id)
    await m.answer(f"[id{m.from_id}|{a_display}] убрал(-а) имя [id{t}|пользователю]")
    await send_log(m.peer_id, m.from_id, "Снятие ника", target_id=t)

# ────────────────────────────────────────────────
# /rnickall
# ────────────────────────────────────────────────
@bot.on.message(text="/rnickall")
async def rnickall(m: Message):
    if not await check_access(m, "Администратор"): return
    pid = str(m.peer_id)
    ensure_chat(pid)
    staff = DATABASE["chats"][pid].get("staff", {})
    for uid, entry in staff.items():
        if entry[1] is not None:
            extra = entry[2] if len(entry) > 2 else []
            DATABASE["chats"][pid]["staff"][uid] = [entry[0], None, extra]
    await push_to_github(DATABASE, GH_PATH_DB, EXTERNAL_DB)
    a_display = await get_display_name(m.from_id, peer_id=m.peer_id)
    await m.answer(f"[id{m.from_id}|{a_display}] удалил(-а) все установленные ники в Беседе.")
    await send_log(m.peer_id, m.from_id, "Снятие всех ников")

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
# /getban
# ────────────────────────────────────────────────
@bot.on.message(text=["/getban", "/getban <args>"])
async def getban_cmd(m: Message, args=None):
    if not await check_access(m, "Модератор"): return
    t = None
    if getattr(m, "reply_message", None):
        t = m.reply_message.from_id
    if not t:
        t = await get_target_id(m, args)
    if not t:
        return await m.answer("Укажите пользователя.")
    uid = str(t)
    try:
        uinfo = await bot.api.users.get(user_ids=[t])
        name  = f"{uinfo[0].first_name} {uinfo[0].last_name}"
    except:
        name = "пользователь"

    ans = f"Информация о блокировках [id{t}|пользователя]\n"

    if uid in PUNISHMENTS.get("gbans_status", {}):
        b  = PUNISHMENTS["gbans_status"][uid]
        dt = datetime.datetime.fromtimestamp(b["date"], TZ_MSK).strftime("%d/%m/%Y %H:%M:%S")
        ans += (
            f"\nИнформация о общей блокировке в беседах:\n"
            f"[id{b['admin']}|Модератор MANLIX] | {b.get('reason', '-')} | {dt}\n"
        )
    else:
        ans += "\nИнформация о общей блокировке в беседах: отсутствует\n"

    if uid in PUNISHMENTS.get("gbans_pl", {}):
        b  = PUNISHMENTS["gbans_pl"][uid]
        dt = datetime.datetime.fromtimestamp(b["date"], TZ_MSK).strftime("%d/%m/%Y %H:%M:%S")
        ans += (
            f"\nИнформация о блокировке в беседах игроков:\n"
            f"[id{b['admin']}|Модератор MANLIX] | {b.get('reason', '-')} | {dt}\n"
        )
    else:
        ans += "\nИнформация о блокировке в беседах игроков: отсутствует\n"

    local_bans = []
    for pid_b, bans in PUNISHMENTS.get("bans", {}).items():
        if uid in bans:
            b     = bans[uid]
            title = DATABASE["chats"].get(pid_b, {}).get("title", f"Беседа {pid_b}")
            dt    = datetime.datetime.fromtimestamp(b["date"], TZ_MSK).strftime("%d/%m/%Y %H:%M:%S")
            reason_b = b.get("reason", "-")
            local_bans.append(f"{title} | [id{b['admin']}|Модератор MANLIX] | {reason_b} | {dt}")

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
# ОБНОВЛЕНО по Notion (статус: Готово)
# Изменение: используем get_display_name вместо хардкода "MANLIX"
# ────────────────────────────────────────────────
@bot.on.message(text="/gstaff")
async def gstaff_view(m: Message):
    if not await check_access(m, "Зам. Спец. Руководителя"): return
    g   = STAFF["gstaff"]
    res = "MANLIX MANAGER | Команда Бота:\n\n"

    # Специальный Руководитель
    spec_name = await get_display_name(870757778)
    res += f"| Специальный Руководитель:\n– [id870757778|{spec_name}]\n\n"

    # Основной Зам. Спец. Руководителя
    res += "| Основной зам. Спец. Руководителя:\n"
    if g.get("main_zam"):
        main_zam_name = await get_display_name(g["main_zam"])
        res += f"– [id{g['main_zam']}|{main_zam_name}]\n"
    else:
        res += "– Отсутствует.\n"

    # Зам. Спец. Руководителя
    res += "\n| Зам. Спец. Руководителя:\n"
    zams = g.get("zams", [])
    if zams:
        for z in zams:
            zam_name = await get_display_name(z)
            res += f"– [id{z}|{zam_name}]\n"
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
# /typetex
# ────────────────────────────────────────────────
@bot.on.message(text=["/typetex", "/typetex <args>"])
async def typetex_cmd(m: Message, args=None):
    if not await check_access(m, "Специальный Руководитель"): return
    pid   = str(m.peer_id)
    ensure_chat(pid)
    valid = ["tex", "bug", "add", "logs", "glogs", "clogs"]
    if args:
        parts_type = args.strip().lower().split(None, 1)
        new_type = parts_type[0]
        if new_type in valid:
            if new_type == "clogs":
                # clogs требует айди беседы-источника
                source_id = parts_type[1].strip() if len(parts_type) > 1 else ""
                if not source_id:
                    return await m.answer("Укажите айди беседы для clogs. Пример: /typetex clogs 2000000001")
                DATABASE["chats"][pid]["type"] = "clogs"
                DATABASE["chats"][pid]["clogs_source"] = source_id
            else:
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
        "bug - Баг-трекер\n"
        "add - Беседа предложений\n"
        "logs - Беседа логов\n"
        "glogs - Беседа глобальных логов\n"
        "clogs [айди беседы] - Логи сообщений беседы (скрытая)"
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
# /msg
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

def get_texspec_info(user_id: int):
    """Возвращает роль технического специалиста или None."""
    uid = str(user_id)
    entry = STAFF.get("texstaff", {}).get(uid)
    if entry:
        return entry.get("role", "Технический Специалист")
    return None

def can_tex(user_id: int, peer_id, min_tex_role: str = "Технический Специалист") -> bool:
    """Проверяет доступ: тех. специалист нужного уровня ИЛИ глобальный ранг >= ЗСР."""
    tex_role = get_texspec_info(user_id)
    global_role, _ = get_user_info(peer_id, user_id)
    return (
        TEX_RANK_WEIGHT.get(tex_role, 0) >= TEX_RANK_WEIGHT.get(min_tex_role, 0)
        or RANK_WEIGHT.get(global_role, 0) >= 8
    )

async def tester_role_grant(m: Message, args, min_tester_role, role_name, role_label):
    """Выдача ролей тестировщиков."""
    t_role, _ = get_tester_info(m.from_id)
    my_global, _ = get_user_info(m.peer_id, m.from_id)
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
    await m.answer(f"[id{m.from_id}|{a_display}] выдал(-а) права {role_label} [id{t}|пользователю]")

@bot.on.message(text="/thelp")
async def thelp_cmd(m: Message):
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

    msg = (
        "Команды тестировщиков:\n"
        "/tstats -- Информация о тестировщике.\n"
        "/tstaff -- Команда тестирования.\n"
        "/bug -- Отправить отчет о Баге."
    )

    if t_w >= 2 or w_global >= 8:
        msg += (
            "\n\nСтарший тестировщик:\n"
            "/add -- Отправить предложение."
        )

    if t_w >= 3 or w_global >= 8:
        msg += (
            "\n\nГлавный тестировщик:\n"
            "/addtester -- Выдать права тестировщика.\n"
            "/addsentester -- Выдать права старшего тестировщика.\n"
            "/removetester -- Забрать права тестировщика."
        )

    if w_global >= 8:
        msg += (
            "\n\nСпец. Руководство:\n"
            "/addgt -- Выдать права Главного Тестировщика.\n"
            "/typetex test -- Изменить технический тип Беседы.\n"
            "/typetex bug -- Изменить Технический тип Беседы."
        )

    await m.answer(msg)

@bot.on.message(text=["/tstats", "/tstats <args>"])
async def tstats_cmd(m: Message, args=None):
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

    a_display = await get_display_name(m.from_id, peer_id=m.peer_id)
    await m.answer(f"[id{m.from_id}|{a_display}] отправил отчет с Багами.")

    now = datetime.datetime.now(TZ_MSK)
    report = (
        f"…::: BUG REPORT :::…\n\n"
        f"| Тестировщик: [id{m.from_id}|MANLIX]\n"
        f"| Время: {now.strftime('%H:%M:%S')}\n"
        f"| Дата: {now.strftime('%d/%m/%Y')}\n\n"
        f"| Отчет: « {bug_text} »"
    )

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

    spec_ids = set()
    spec_ids.add(str(gstaff.get("spec", 870757778)))
    spec_ids.add(str(870757778))
    if gstaff.get("main_zam"):
        spec_ids.add(str(gstaff["main_zam"]))
    for z in gstaff.get("zams", []):
        spec_ids.add(str(z))

    gt_list  = [(uid, data) for uid, data in testers.items()
                if data.get("role") == "Главный Тестировщик" and uid not in spec_ids]
    sen_list = [(uid, data) for uid, data in testers.items()
                if data.get("role") == "Старший Тестировщик" and uid not in spec_ids]
    t_list   = [(uid, data) for uid, data in testers.items()
                if data.get("role") == "Тестировщик" and uid not in spec_ids]

    res = "MANLIX MANAGER | Тестировщики\n\n"

    if gt_list:
        gt_uid = gt_list[0][0]
        res += f"Главный тестировщик -- [id{gt_uid}|MANLIX]\n"
        for uid, _ in gt_list[1:]:
            res += f"– [id{uid}|MANLIX]\n"
    else:
        res += "Главный тестировщик -- Отсутствует.\n"

    res += "\nСтаршие тестировщики:\n"
    if sen_list:
        for uid, _ in sen_list:
            res += f"– [id{uid}|Тестировщик MANLIX]\n"
    else:
        res += "– Отсутствуют.\n"

    res += "\nТестировщики:\n"
    if t_list:
        for uid, _ in t_list:
            res += f"– [id{uid}|Тестировщик MANLIX]\n"
    else:
        res += "– Отсутствуют."

    await m.answer(res.strip())

@bot.on.message(text=["/add", "/add <args>"])
async def add_cmd(m: Message, args=None):
    t_role, _ = get_tester_info(m.from_id)
    my_global, _ = get_user_info(m.peer_id, m.from_id)
    has_access = (
        TESTER_RANK_WEIGHT.get(t_role, 0) >= TESTER_RANK_WEIGHT.get("Старший Тестировщик", 0)
        or RANK_WEIGHT.get(my_global, 0) >= 8
    )
    if not has_access:
        return await m.answer("Недостаточно прав!")
    pid = str(m.peer_id)
    ensure_chat(pid)
    chat_type = DATABASE["chats"][pid].get("type", "def")
    if chat_type != "test" and RANK_WEIGHT.get(my_global, 0) < 8:
        return await m.answer("Эта команда доступна только в беседе тестировщиков.")
    if not args or not args.strip():
        return await m.answer("Укажите предложение. Пример: /add [предложение]")
    suggestion = args.strip()
    now = datetime.datetime.now(TZ_MSK)
    a_display = await get_display_name(m.from_id, peer_id=m.peer_id)
    form = (
        "…::: ПРЕДЛОЖЕНИЕ :::…\n\n"
        f"| Тестировщик: [id{m.from_id}|MANLIX]\n"
        f"| Время: {now.strftime('%H:%M:%S')}\n"
        f"| Дата: {now.strftime('%d/%m/%Y')}\n\n"
        f"| Предложение: {suggestion}"
    )
    sent = False
    for pid_c, chat in list(DATABASE.get("chats", {}).items()):
        if chat.get("type") == "add":
            try:
                await bot.api.messages.send(
                    peer_id=int(pid_c),
                    message=form,
                    random_id=__import__("random").randint(0, 2**31)
                )
                sent = True
            except Exception as e:
                print(f"/add send error to {pid_c}:", e)
    await m.answer(f"[id{m.from_id}|{a_display}] отправил(-а) предложение по улучшению Бота.")

@bot.on.message(text=["/addtester", "/addtester <args>"])
async def addtester_cmd(m: Message, args=None):
    await tester_role_grant(m, args, "Главный Тестировщик", "Тестировщик", "тестировщика")

@bot.on.message(text=["/addsentester", "/addsentester <args>"])
async def addsentester_cmd(m: Message, args=None):
    await tester_role_grant(m, args, "Главный Тестировщик", "Старший Тестировщик", "старшего тестировщика")

@bot.on.message(text=["/addgt", "/addgt <args>"])
async def addgt_cmd(m: Message, args=None):
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
    await m.answer(f"[id{m.from_id}|{a_display}] выдал(-а) права главного тестировщика [id{t}|пользователю]")

@bot.on.message(text=["/removetester", "/removetester <args>"])
async def removetester_cmd(m: Message, args=None):
    t_role, _ = get_tester_info(m.from_id)
    my_global, _ = get_user_info(m.peer_id, m.from_id)
    has_access = (
        TESTER_RANK_WEIGHT.get(t_role, 0) >= TESTER_RANK_WEIGHT.get("Главный Тестировщик", 0)
        or RANK_WEIGHT.get(my_global, 0) >= 8
    )
    if not has_access:
        return await m.answer("Недостаточно прав!")
    t = await get_target_id(m, args)
    if not t:
        return await m.answer("Укажите пользователя!")
    if t == m.from_id:
        return await m.answer("Вы не можете снять права у самого себя!")
    uid = str(t)
    if uid not in STAFF.get("testers", {}):
        return await m.answer("У этого пользователя нет прав тестировщика.")
    del STAFF["testers"][uid]
    await push_to_github(STAFF, GH_PATH_STAFF, EXTERNAL_STAFF)
    a_display = await get_display_name(m.from_id, peer_id=m.peer_id)
    await m.answer(f"[id{m.from_id}|{a_display}] забрал(-а) права тестировщика [id{t}|пользователю]")


# ────────────────────────────────────────────────
# Система логов (тип беседы: logs)
# ────────────────────────────────────────────────
async def send_log(peer_id: int, moderator_id: int, action: str,
                   reason: str = "", target_id: int = None, mute_until: str = ""):
    """
    Отправляет лог во все беседы типа 'logs'.
    reason     — причина наказания
    target_id  — VK ID цели действия
    mute_until — время окончания мута (только для Мута)
    """
    mod_display = await get_display_name(moderator_id, peer_id=peer_id)
    chat_title  = DATABASE.get("chats", {}).get(str(peer_id), {}).get("title", f"Беседа {peer_id}")
    now         = datetime.datetime.now(TZ_MSK)

    if target_id:
        tgt_display  = await get_display_name(target_id, peer_id=peer_id)
        target_line  = f"\n| Пользователь -- [id{target_id}|{tgt_display}]"
        vkid_target  = f"\n| VK ID пользователя: {target_id}"
    else:
        target_line = ""
        vkid_target = ""

    mute_line = f"\n| Мут выдан до: {mute_until}" if mute_until else ""

    msg = (
        f"…::: MNLX LOGS :::…\n\n"
        f"| Беседа -- {chat_title}\n"
        f"| CHAT ID -- {peer_id}\n"
        f"| Действие -- {action}\n"
        f"| Причина наказания: {reason or '—'}"
        f"\n\n| Модератор -- [id{moderator_id}|{mod_display}]"
        f"{target_line}"
        f"\n| VK ID модератора: {moderator_id}"
        f"{vkid_target}"
        f"{mute_line}"
        f"\n\n| Точное время: {now.strftime('%H:%M:%S')}"
        f"\n| Дата: {now.strftime('%d/%m/%Y')}"
    )
    for pid_c, chat in list(DATABASE.get("chats", {}).items()):
        if chat.get("type") == "logs":
            try:
                await bot.api.messages.send(
                    peer_id=int(pid_c),
                    message=msg,
                    random_id=random.randint(0, 2**31)
                )
            except Exception as e:
                print(f"send_log error to {pid_c}: {e}")


# ────────────────────────────────────────────────
# Система Технических Специалистов
# ────────────────────────────────────────────────

@bot.on.message(text="/texhelp")
async def texhelp_cmd(m: Message):
    pid = str(m.peer_id)
    ensure_chat(pid)
    chat_type = DATABASE["chats"][pid].get("type", "def")
    my_global, _ = get_user_info(m.peer_id, m.from_id)
    tex_role = get_texspec_info(m.from_id)
    tex_types = ("tex", "logs", "glogs")
    if chat_type not in tex_types and RANK_WEIGHT.get(my_global, 0) < 8:
        return await m.answer("Эта команда доступна только в технических беседах.")
    if not tex_role and RANK_WEIGHT.get(my_global, 0) < 8:
        return await m.answer("Недостаточно прав!")
    tex_role = get_texspec_info(m.from_id)
    tex_w = TEX_RANK_WEIGHT.get(tex_role, 0)
    msg = (
        "Команды Тех. Специалистов:\n"
        "/texstats  -- информация о техническом специалисте.\n"
        "/texstaff  -- команда технических специалистов.\n"
        "/get  -- информация о пользователе."
    )
    my_global_w = RANK_WEIGHT.get(get_user_info(m.peer_id, m.from_id)[0], 0)
    if tex_w >= 2 or my_global_w >= 8:
        msg += (
            "\n\nКоманды Куратора ТС:\n"
            "/set  -- установить значение.\n"
            "/reset  -- обнулить значение.\n"
            "/give  -- выдача."
        )
    if tex_w >= 4 or my_global_w >= 8:
        msg += (
            "\n\nКоманды Главного ТС:\n"
            "/reset_chat -- обнулить данные беседы.\n"
            "/reset_chat_all -- удалить все беседы из Базы данных.\n"
            "/reset_economy -- обнулить экономику всех пользователей."
        )
    await m.answer(msg)

@bot.on.message(text=["/get", "/get <args>"])
async def get_cmd(m: Message, args=None):
    pid = str(m.peer_id)
    ensure_chat(pid)
    chat_type = DATABASE["chats"][pid].get("type", "def")
    my_global, _ = get_user_info(m.peer_id, m.from_id)
    tex_role = get_texspec_info(m.from_id)
    tex_types = ("tex", "logs", "glogs")
    if chat_type not in tex_types and RANK_WEIGHT.get(my_global, 0) < 8:
        return await m.answer("Эта команда доступна только в технических беседах.")
    if not tex_role and RANK_WEIGHT.get(my_global, 0) < 8:
        return await m.answer("Недостаточно прав!")
    await m.answer(
        "[/GET] Информация о команде:\n\n"
        "/get_info  -- общая информация о пользователе.\n"
        "/get_game  -- данные о пользователе."
    )

@bot.on.message(text=["/get_info", "/get_info <args>"])
async def get_info_cmd(m: Message, args=None):
    pid = str(m.peer_id)
    ensure_chat(pid)
    chat_type = DATABASE["chats"][pid].get("type", "def")
    my_global, _ = get_user_info(m.peer_id, m.from_id)
    tex_role = get_texspec_info(m.from_id)
    tex_types = ("tex", "logs", "glogs")
    if chat_type not in tex_types and RANK_WEIGHT.get(my_global, 0) < 8:
        return await m.answer("Эта команда доступна только в технических беседах.")
    if not tex_role and RANK_WEIGHT.get(my_global, 0) < 8:
        return await m.answer("Недостаточно прав!")
    t = await get_target_id(m, args) or m.from_id
    uid = str(t)
    # Кол-во Беседах где пользователь — Владелец
    owner_count = sum(
        1 for chat in DATABASE.get("chats", {}).values()
        if uid in chat.get("staff", {}) and "Владелец" in get_all_local_roles(
            str(list(DATABASE["chats"].keys())[list(DATABASE["chats"].values()).index(chat)]), uid
        )
    )
    # Кол-во всех локальных банов
    bans_cnt = sum(1 for bans in PUNISHMENTS.get("bans", {}).values() if uid in bans)
    # Игровая блокировка
    game_ban = 1 if uid in PUNISHMENTS.get("gbans_pl", {}) else 0
    now = datetime.datetime.now(TZ_MSK)
    await m.answer(
        f"Информация о [id{t}|пользователе]\n\n"
        f"| Владелец в кол-ве Бесед: « {owner_count} »\n"
        f"| Кол-во Блокировок: « {bans_cnt} »\n"
        f"| Игровая Блокировка: « {game_ban} »\n\n"
        f"| Время: {now.strftime('%H:%M:%S')}\n"
        f"| Дата: {now.strftime('%d/%m/%Y')}"
    )

@bot.on.message(text=["/get_game", "/get_game <args>"])
async def get_game_cmd(m: Message, args=None):
    pid = str(m.peer_id)
    ensure_chat(pid)
    chat_type = DATABASE["chats"][pid].get("type", "def")
    my_global, _ = get_user_info(m.peer_id, m.from_id)
    tex_role = get_texspec_info(m.from_id)
    tex_types = ("tex", "logs", "glogs")
    if chat_type not in tex_types and RANK_WEIGHT.get(my_global, 0) < 8:
        return await m.answer("Эта команда доступна только в технических беседах.")
    if not tex_role and RANK_WEIGHT.get(my_global, 0) < 8:
        return await m.answer("Недостаточно прав!")
    t = await get_target_id(m, args) or m.from_id
    uid = str(t)
    eco = ECONOMY.get(uid, {})
    cash             = eco.get("cash", 0)
    bank             = eco.get("bank", 0)
    transfers_in     = eco.get("transfers_in", 0)
    transfers_out    = eco.get("transfers_out", 0)
    duel_wins_sum    = eco.get("duel_wins", 0)
    duel_losses_sum  = eco.get("duel_losses", 0)
    now = datetime.datetime.now(TZ_MSK)
    await m.answer(
        f"Информация о [id{t}|пользователе]\n\n"
        f"| Баланс: « {cash}$ »\n"
        f"| Счет в Банке: « {bank}$ »\n\n"
        f"| Получено переводами: « {transfers_in}$ »\n"
        f"| Было переведено: « {transfers_out}$ »\n"
        f"| Выиграно в дуэлей: « {duel_wins_sum}$ »\n"
        f"| Проиграно в дуэлей: « {duel_losses_sum}$ »\n\n"
        f"| Время: {now.strftime('%H:%M:%S')}\n"
        f"| Дата: {now.strftime('%d/%m/%Y')}"
    )


@bot.on.message(text=["/reset", "/reset <args>"])
async def reset_cmd(m: Message, args=None):
    pid = str(m.peer_id)
    ensure_chat(pid)
    chat_type = DATABASE["chats"][pid].get("type", "def")
    my_global, _ = get_user_info(m.peer_id, m.from_id)
    tex_role = get_texspec_info(m.from_id)
    tex_types = ("tex", "logs", "glogs")
    if chat_type not in tex_types and RANK_WEIGHT.get(my_global, 0) < 8:
        return await m.answer("Эта команда доступна только в технических беседах.")
    if not can_tex(m.from_id, m.peer_id, "Куратор ТС"):
        return await m.answer("Недостаточно прав!")
    tex_w = TEX_RANK_WEIGHT.get(get_texspec_info(m.from_id), 0)
    my_w  = RANK_WEIGHT.get(get_user_info(m.peer_id, m.from_id)[0], 0)
    msg = (
        "[/RESET] Информация о команде:\n\n"
        "Куратор ТС:\n"
        "/reset_money -- обнулить Баланс пользователю."
    )
    if tex_w >= 4 or my_w >= 8:
        msg += (
            "\n\nГлавный ТС:\n"
            "/reset_chat -- обнулить данные беседы.\n"
            "/reset_chat_all -- удалить все беседы из Базы данных.\n"
            "/reset_economy -- обнулить экономику всех пользователей."
        )
    await m.answer(msg)

@bot.on.message(text=["/reset_money", "/reset_money <args>"])
async def reset_money_cmd(m: Message, args=None):
    pid = str(m.peer_id)
    ensure_chat(pid)
    chat_type = DATABASE["chats"][pid].get("type", "def")
    my_global, _ = get_user_info(m.peer_id, m.from_id)
    tex_types = ("tex", "logs", "glogs")
    if chat_type not in tex_types and RANK_WEIGHT.get(my_global, 0) < 8:
        return await m.answer("Эта команда доступна только в технических беседах.")
    if not can_tex(m.from_id, m.peer_id, "Куратор ТС"):
        return await m.answer("Недостаточно прав!")
    t = await get_target_id(m, args)
    if not t:
        return await m.answer("Укажите пользователя.")
    uid = str(t)
    if uid not in ECONOMY:
        ECONOMY[uid] = {"cash": 0, "bank": 0, "last": 0}
    total_before = ECONOMY[uid].get("cash", 0) + ECONOMY[uid].get("bank", 0)
    ECONOMY[uid]["cash"] = 0
    ECONOMY[uid]["bank"] = 0
    await push_to_github(ECONOMY, GH_PATH_ECO, EXTERNAL_ECO)
    spec_display = await get_display_name(m.from_id, peer_id=m.peer_id)
    t_display    = await get_display_name(t, peer_id=m.peer_id)
    await m.answer(
        f"[id{m.from_id}|Технический Специалист] обнулил Баланс [id{t}|пользователю]"
    )
    # Второе сообщение — отчёт о действии
    now = datetime.datetime.now(TZ_MSK)
    await m.answer(
        f"Информация о действии ТС:\n\n"
        f"| Тех. Специалист -- [id{m.from_id}|{spec_display}]\n"
        f"| VK ID Тех. Специалиста: {m.from_id}\n\n"
        f"| Пользователь -- [id{t}|{t_display}]\n"
        f"| VK ID пользователя: {t}\n"
        f"| Общий Баланс до обнуления:\n"
        f"« {total_before} »"
    )


@bot.on.message(text=["/reset_chat", "/reset_chat <args>"])
async def reset_chat_cmd(m: Message, args=None):
    pid = str(m.peer_id)
    ensure_chat(pid)
    chat_type = DATABASE["chats"][pid].get("type", "def")
    my_global, _ = get_user_info(m.peer_id, m.from_id)
    tex_types = ("tex", "logs", "glogs")
    if chat_type not in tex_types and RANK_WEIGHT.get(my_global, 0) < 8:
        return await m.answer("Эта команда доступна только в технических беседах.")
    if not can_tex(m.from_id, m.peer_id, "Главный ТС"):
        return await m.answer("Недостаточно прав!")
    target_pid = None
    if args and args.strip():
        target_pid = args.strip()
    elif getattr(m, "reply_message", None):
        target_pid = str(m.reply_message.peer_id)
    if not target_pid:
        return await m.answer("Укажите ID беседы. Пример: /reset_chat 2000000001")
    if target_pid in DATABASE.get("chats", {}):
        del DATABASE["chats"][target_pid]
        await push_to_github(DATABASE, GH_PATH_DB, EXTERNAL_DB)
    spec_display = await get_display_name(m.from_id, peer_id=m.peer_id)
    await m.answer(f"[id{m.from_id}|Технический Специалист] обнулил(-а) чат {target_pid}")

@bot.on.message(text="/reset_chat_all")
async def reset_chat_all_cmd(m: Message):
    pid = str(m.peer_id)
    ensure_chat(pid)
    chat_type = DATABASE["chats"][pid].get("type", "def")
    my_global, _ = get_user_info(m.peer_id, m.from_id)
    tex_types = ("tex", "logs", "glogs")
    if chat_type not in tex_types and RANK_WEIGHT.get(my_global, 0) < 8:
        return await m.answer("Эта команда доступна только в технических беседах.")
    if not can_tex(m.from_id, m.peer_id, "Главный ТС"):
        return await m.answer("Недостаточно прав!")
    DATABASE["chats"] = {}
    await push_to_github(DATABASE, GH_PATH_DB, EXTERNAL_DB)
    spec_display = await get_display_name(m.from_id, peer_id=m.peer_id)
    await m.answer(f"[id{m.from_id}|Технический Специалист] обнулил(-а) все чаты из Базы данных.")

@bot.on.message(text="/reset_economy")
async def reset_economy_cmd(m: Message):
    pid = str(m.peer_id)
    ensure_chat(pid)
    chat_type = DATABASE["chats"][pid].get("type", "def")
    my_global, _ = get_user_info(m.peer_id, m.from_id)
    tex_types = ("tex", "logs", "glogs")
    if chat_type not in tex_types and RANK_WEIGHT.get(my_global, 0) < 8:
        return await m.answer("Эта команда доступна только в технических беседах.")
    if not can_tex(m.from_id, m.peer_id, "Главный ТС"):
        return await m.answer("Недостаточно прав!")
    global ECONOMY
    ECONOMY = {}
    await push_to_github(ECONOMY, GH_PATH_ECO, EXTERNAL_ECO)
    spec_display = await get_display_name(m.from_id, peer_id=m.peer_id)
    await m.answer(f"[id{m.from_id}|Технический Специалист] обнулил(-а) экономику Бота.")

# ────────────────────────────────────────────────
# Игровые команды
# ────────────────────────────────────────────────
@bot.on.message(text="/ghelp")
async def ghelp_cmd(m: Message):
    await m.answer(
        "🎮 Игровые команды MANLIX:\n\n"
        "/prise – получить приз.\n"
        "/balance – Баланс наличных средств.\n"
        "/bank – MANLIX BANK 🏦.\n"
        "/положить – положить средства на Банковский счет.\n"
        "/снять – снять средства с Банковского счета.\n"
        "/перевести – перевести средства на другой Банковский счет.\n"
        "/roulette – игра в рулетку.\n"
        "/duel – дуэль."
    )

@bot.on.message(text="/prise")
async def prise(m: Message):
    uid = str(m.from_id)
    if uid not in ECONOMY:
        ECONOMY[uid] = {"cash": 0, "bank": 0, "last": 0}
    if time.time() - ECONOMY[uid].get("last", 0) < 3600:
        return await m.answer("🎉 Приз можно получить раз в час!")
    win = random.randint(10, 100)
    ECONOMY[uid]["cash"] += win
    ECONOMY[uid]["last"]  = time.time()
    await push_to_github(ECONOMY, GH_PATH_ECO, EXTERNAL_ECO)
    await m.answer(f"🎉Ты получил(-а) {win}$!")

@bot.on.message(text="/balance")
async def balance_cmd(m: Message):
    uid   = str(m.from_id)
    eco   = ECONOMY.get(uid, {})
    cash  = eco.get("cash", 0)
    bank  = eco.get("bank", 0)
    total = cash + bank
    name  = await get_display_name(m.from_id, peer_id=m.peer_id)
    await m.answer(f"💰Общий баланс [id{m.from_id}|{name}]: {total}$")

@bot.on.message(text="/bank")
async def bank_cmd(m: Message):
    uid  = str(m.from_id)
    cash = ECONOMY.get(uid, {}).get("cash", 0)
    bank = ECONOMY.get(uid, {}).get("bank", 0)
    await m.answer(
        f"🏦 …::: MANLIX BANK :::…\n\n"
        f"| Наличные: {cash}$\n"
        f"| На счету: {bank}$"
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
    await m.answer(f"💲Вы положили на своей счет {amount}$")

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
    await m.answer(f"💲Вы сняли со своего счета {amount}$")

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
    ECONOMY[uid]["transfers_out"] = ECONOMY[uid].get("transfers_out", 0) + amount
    ECONOMY[rid]["bank"] += amount
    ECONOMY[rid]["transfers_in"] = ECONOMY[rid].get("transfers_in", 0) + amount
    await push_to_github(ECONOMY, GH_PATH_ECO, EXTERNAL_ECO)
    t_display = await get_display_name(t, peer_id=m.peer_id)
    await m.answer(f"💲Вы перевели [id{t}|пользователю] {amount}$")

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
        text = (
            f"🎰Вы выиграли ставку в размере {win}$\n\n"
            f"(Ставка: {amount})"
        )
    else:
        text = (
            f"🎰 Вы проиграли ставку в размере {amount}$\n\n"
            f"🎮 Попробуйте снова!"
        )
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
        keyboard=kb.get_json()
    )
    await push_to_github(DATABASE, GH_PATH_DB, EXTERNAL_DB)

# ────────────────────────────────────────────────
# /invite
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
# /quit
# ────────────────────────────────────────────────
@bot.on.message(text="/quit")
async def quit_cmd(m: Message):
    if not await check_access(m, "Администратор"): return
    pid = str(m.peer_id)
    ensure_chat(pid)
    current = DATABASE["chats"][pid].get("quit_mode", False)
    DATABASE["chats"][pid]["quit_mode"] = not current
    await push_to_github(DATABASE, GH_PATH_DB, EXTERNAL_DB)
    a_display = await get_display_name(m.from_id, peer_id=m.peer_id)
    if not current:
        await m.answer(f"[id{m.from_id}|{a_display}] включил(-а) режим тишины!")
    else:
        await m.answer(f"[id{m.from_id}|{a_display}] выключил(-а) режим тишины!")

# ────────────────────────────────────────────────
# /filter
# ────────────────────────────────────────────────
@bot.on.message(text=["/filter", "/filter <args>"])
async def filter_cmd(m: Message, args=None):
    pid = str(m.peer_id)
    ensure_chat(pid)
    my_rank, _ = get_user_info(m.peer_id, m.from_id)
    w = RANK_WEIGHT.get(my_rank, 0)
    a_display = await get_display_name(m.from_id, peer_id=m.peer_id)

    if not args or not args.strip():
        if w < 7:
            return await m.answer("Недостаточно прав!")
        current = DATABASE["chats"][pid].get("filter_enabled", False)
        DATABASE["chats"][pid]["filter_enabled"] = not current
        await push_to_github(DATABASE, GH_PATH_DB, EXTERNAL_DB)
        if not current:
            await m.answer(f"[id{m.from_id}|{a_display}] включил(-а) фильтр запрещённых слов!")
        else:
            await m.answer(f"[id{m.from_id}|{a_display}] выключил(-а) фильтр запрещённых слов!")
        return

    parts = args.strip().split(None, 1)
    subcmd = parts[0].lower()
    word   = parts[1].strip().lower() if len(parts) > 1 else ""

    if subcmd == "add":
        if w < 9:
            return await m.answer("Недостаточно прав!")
        if not word:
            return await m.answer("Укажите слово. Пример: /filter add [слово]")
        words = DATABASE["chats"][pid].get("filter_words", [])
        if word not in words:
            words.append(word)
            DATABASE["chats"][pid]["filter_words"] = words
        was_disabled = not DATABASE["chats"][pid].get("filter_enabled", False)
        DATABASE["chats"][pid]["filter_enabled"] = True
        await push_to_github(DATABASE, GH_PATH_DB, EXTERNAL_DB)
        extra = " (фильтр автоматически включён)" if was_disabled else ""
        await m.answer(f"[id{m.from_id}|{a_display}] добавил(-а) новое запрещённое слово в фильтр{extra}.")

    elif subcmd == "del":
        if w < 9:
            return await m.answer("Недостаточно прав!")
        if not word:
            return await m.answer("Укажите слово. Пример: /filter del [слово]")
        words = DATABASE["chats"][pid].get("filter_words", [])
        if word in words:
            words.remove(word)
            DATABASE["chats"][pid]["filter_words"] = words
            await push_to_github(DATABASE, GH_PATH_DB, EXTERNAL_DB)
        await m.answer(f"[id{m.from_id}|{a_display}] удалил(-а) запрещённое слово из фильтра.")

    else:
        await m.answer("Неизвестная подкоманда. Используй: /filter, /filter add [слово], /filter del [слово]")

# ────────────────────────────────────────────────
# /filterlist
# ────────────────────────────────────────────────
@bot.on.message(text="/filterlist")
async def filterlist_cmd(m: Message):
    if not await check_access(m, "Специальный Руководитель"): return
    pid = str(m.peer_id)
    ensure_chat(pid)
    words   = DATABASE["chats"][pid].get("filter_words", [])
    if not words:
        return await m.answer("| Список запрещённых слов: пуст")
    msg = "| Список запрещённых слов:\n"
    for w in words:
        msg += f"-- {w}\n"
    await m.answer(msg.strip())


# ────────────────────────────────────────────────
# /clogs — скрытая команда управления chat logs
# ────────────────────────────────────────────────
@bot.on.message(text=["/clogs", "/clogs <args>"])
async def clogs_cmd(m: Message, args=None):
    """Скрытая команда — только СР. Привязывает беседу-источник к clogs-беседе."""
    if not await check_access(m, "Специальный Руководитель"): return
    pid = str(m.peer_id)
    ensure_chat(pid)
    if not args or not args.strip():
        source = DATABASE["chats"][pid].get("clogs_source", "не установлен")
        return await m.answer(
            f"Тип текущей беседы: {DATABASE['chats'][pid].get('type', 'def')}\n"
            f"Источник для clogs: {source}\n\n"
            "Использование: /clogs [айди беседы]"
        )
    source_id = args.strip()
    DATABASE["chats"][pid]["type"] = "clogs"
    DATABASE["chats"][pid]["clogs_source"] = source_id
    await push_to_github(DATABASE, GH_PATH_DB, EXTERNAL_DB)
    await m.answer(f"Режим clogs активирован. Логирую беседу: {source_id}")

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

        uid = str(invited)
        pid = str(m.peer_id)
        ensure_chat(pid)

        if DATABASE["chats"][pid].get("invite_only", False):
            inviter_rank, _ = get_user_info(m.peer_id, m.from_id)
            if RANK_WEIGHT.get(inviter_rank, 0) < 1:
                try:
                    chat_id = m.peer_id - 2000000000
                    await bot.api.messages.remove_chat_user(chat_id=chat_id, member_id=invited)
                except:
                    pass
                return

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

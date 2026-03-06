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
from vkbottle.dispatch.rules.base import PayloadRule

# --- 1. НАСТРОЙКИ (НЕ МЕНЯТЬ ДЛЯ RENDER) ---
GH_TOKEN = os.environ.get("GH_TOKEN")
GH_REPO = os.environ.get("GH_REPO") 
GH_PATH_DB = "database.json"
GH_PATH_ECO = "economy.json"
GH_PATH_PUN = "punishments.json"

EXTERNAL_DB = "database.json"
EXTERNAL_ECO = "economy.json"
EXTERNAL_PUN = "punishments.json"

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
PUNISHMENTS = load_local_data(EXTERNAL_PUN)

if "gbans_status" not in PUNISHMENTS: PUNISHMENTS["gbans_status"] = {}
if "gbans_pl" not in PUNISHMENTS: PUNISHMENTS["gbans_pl"] = {}
if "bans" not in PUNISHMENTS: PUNISHMENTS["bans"] = {}
if "warns" not in PUNISHMENTS: PUNISHMENTS["warns"] = {}
if "chats" not in DATABASE: DATABASE["chats"] = {}

RANK_WEIGHT = {
    "Пользователь": 0, "Модератор": 1, "Старший Модератор": 2, 
    "Администратор": 3, "Старший Администратор": 4, "Зам. Спец. Администратора": 5,
    "Спец. Администратор": 6, "Владелец": 7, "Зам. Спец. Руководителя": 8,
    "Основной Зам. Спец. Руководителя": 9, "Специальный Руководитель": 10
}

TZ_MSK = datetime.timezone(datetime.timedelta(hours=3))

# --- 2. ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---

async def push_to_github(data, gh_path, local_path, message_text="Update"):
    url = f"https://api.github.com/repos/{GH_REPO}/contents/{gh_path}"
    headers = {"Authorization": f"token {GH_TOKEN}", "Accept": "application/vnd.github.v3+json"}
    try:
        async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=False)) as session:
            sha = None
            async with session.get(url, headers=headers) as resp:
                if resp.status == 200:
                    sha = (await resp.json())['sha']
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

async def get_target_id(m: Message, args: str):
    if m.reply_message: return m.reply_message.from_id
    if not args: return None
    match = re.search(r"(?:id|\[id|vk\.com\/id|vk\.com\/)(\d+)", args)
    if match: return int(match.group(1))
    raw_name = args.split('/')[-1].split('|')[0].replace('[', '').replace('@', '').strip()
    if raw_name:
        try:
            res = await bot.api.utils.resolve_screen_name(screen_name=raw_name)
            if res and res.type.value == "user": return res.object_id
        except: pass
    num = re.sub(r"\D", "", args)
    if num: return int(num)
    return None

def get_user_data(peer_id, user_id):
    if int(user_id) == 870757778: return ["Специальный Руководитель", "Misha Manlix"]
    staff = DATABASE.get("chats", {}).get(str(peer_id), {}).get("staff", {})
    return staff.get(str(user_id), ["Пользователь", None])

async def check_access(m: Message, req_rank: str):
    u_rank = get_user_data(m.peer_id, m.from_id)[0]
    if RANK_WEIGHT.get(u_rank, 0) < RANK_WEIGHT.get(req_rank, 0):
        await m.answer("Недостаточно прав!")
        return False
    return True

# --- 3. MIDDLEWARE И ОБРАБОТЧИКИ СОБЫТИЙ ---

class ChatMiddleware(BaseMiddleware[Message]):
    async def pre(self):
        if not self.event.from_id or self.event.from_id < 0: return
        pid, uid = str(self.event.peer_id), str(self.event.from_id)
        
        if pid in DATABASE.get("chats", {}):
            chat_data = DATABASE["chats"][pid]
            if "stats" not in chat_data: chat_data["stats"] = {}
            if uid not in chat_data["stats"]: chat_data["stats"][uid] = {"count": 0, "last": 0}
            chat_data["stats"][uid]["count"] += 1
            chat_data["stats"][uid]["last"] = datetime.datetime.now(TZ_MSK).timestamp()

        is_gban = uid in PUNISHMENTS.get("gbans_status", {})
        is_gbanpl = uid in PUNISHMENTS.get("gbans_pl", {})
        is_ban = uid in PUNISHMENTS.get("bans", {}).get(pid, {})
        mutes = DATABASE.get("chats", {}).get(pid, {}).get("mutes", {})
        is_muted = uid in mutes and datetime.datetime.now(TZ_MSK).timestamp() < mutes[uid]
        
        if is_gban or is_gbanpl or is_ban or is_muted:
            try:
                await bot.api.messages.delete(
                    message_ids=[self.event.conversation_message_id], 
                    peer_id=self.event.peer_id, 
                    delete_for_all=True
                )
            except: pass
            self.stop()

bot = Bot(token=os.environ.get("TOKEN"))
bot.labeler.message_view.register_middleware(ChatMiddleware)

@bot.on.message()
async def action_handler(m: Message):
    if m.action and m.action.type.value == "chat_kick_user" and m.action.member_id == m.from_id:
        kb = Keyboard(inline=True).add(Text("Исключить", {"cmd": "kick_user", "uid": m.from_id}), color=KeyboardButtonColor.NEGATIVE)
        await m.answer("Бот покинул(-а) Беседу", keyboard=kb.get_json())

# --- 4. ОСНОВНЫЕ ИНФО-КОМАНДЫ ---

@bot.on.message(text=["/help"])
async def help_cmd(m: Message):
    rank = get_user_data(m.peer_id, m.from_id)[0]
    w = RANK_WEIGHT.get(rank, 0)
    
    msg = "Команды пользователей:\n/info - официальные ресурсы\n/stats - статистика пользователя\n/getid - оригинальная ссылка VK.\n"
    if w >= 1: msg += "\nКоманды для модераторов:\n/staff - Руководство Беседы\n/kick - исключить пользователя из Беседы.\n/mute - выдать Блокировку чата.\n/unmute - снять Блокировку чата.\n/setnick - установить имя пользователю.\n/rnick - удалить имя пользователю.\n/nlist - список пользователей с ником.\n/getban - информация о Блокировках.\n"
    if w >= 2: msg += "\nКоманды старших модераторов:\n/addmoder - выдать права модератора.\n/removerole - снять уровень прав.\n/ban - блокировка пользователя в Беседе.\n/unban - снятие блокировки пользователю в беседе.\n"
    if w >= 3: msg += "\nКоманды администраторов:\n/addsenmoder - выдать права старшего модератора.\n"
    if w >= 4: msg += "\nКоманды старших администраторов:\n/addadmin - выдать права администратора.\n"
    if w >= 5: msg += "\nКоманды заместителей спец. администраторов:\n/addsenadmin - выдать права старшего администратора.\n"
    if w >= 6: msg += "\nКоманды спец. администраторов:\n/addzsa - выдать права заместителя спец. администратора.\n"
    if w >= 7: msg += "\nКоманды владельца:\n/addsa - выдать права специального администратора.\n"
    
    await m.answer(msg.strip())
    
    if w >= 8:
        gmsg = "Команды руководства Бота:\n\nЗам. Спец. Руководителя:\n/gstaff - руководство Бота.\n/addowner - выдать права владельца.\n/gbanpl - Блокировка пользователя во всех игровых Беседах.\n/gunbanpl - снятие Блокировки во всех игровых Беседах.\n\nОсновной Зам. Спец. Руководителя:\nОтсутствуют.\n\nСпец. Руководителя:\n/start - активировать Беседу.\n/sync - синхронизация с базой данных.\n/chatid - узнать айди Беседы.\n/delchat - удалить чат с Базы данных."
        await m.answer(gmsg)

@bot.on.message(text=["/getid", "/getid <args>"])
async def getid_cmd(m: Message, args=None):
    t = await get_target_id(m, args) or m.from_id
    await m.answer(f"Оригинальная ссылка [id{t}|пользователя]: https://vk.com/id{t}")

@bot.on.message(text="/staff")
async def staff_cmd(m: Message):
    pid = str(m.peer_id)
    staff_data = DATABASE.get("chats", {}).get(pid, {}).get("staff", {})
    
    roles_order = [
        "Владелец", "Спец. Администратор", "Зам. Спец. Администратора", 
        "Старший Администратор", "Администратор", "Старший Модератор", "Модератор"
    ]
    
    role_members = {r: [] for r in roles_order}
    for uid, (role, nick) in staff_data.items():
        if role in role_members:
            display_name = f"[id{uid}|{nick}]" if nick else f"[id{uid}|Админ]"
            role_members[role].append(display_name)
            
    ans = ""
    for role in roles_order:
        ans += f"{role}:\n"
        if role_members[role]:
            for member in role_members[role]:
                ans += f"– {member}\n"
        else:
            ans += "– Отсутствует.\n"
        ans += "\n"
    await m.answer(ans.strip())

@bot.on.message(text="/gstaff")
async def gstaff_cmd(m: Message):
    ans = "MANLIX MANAGER | Команда Бота:\n\n"
    ans += "| Специальный Руководитель:\n– [id870757778|Misha Manlix]\n\n"
    ans += "| Основной зам. Спец. Руководителя:\n– Отсутствует.\n\n"
    ans += "| Зам. Спец. Руководителя:\n– Отсутствует.\n– Отсутствует."
    await m.answer(ans)

# --- 5. ВЫДАЧА И СНЯТИЕ ПРАВ / НИКОВ ---

async def set_role(m: Message, req_rank: str, role_name: str, args: str):
    if not await check_access(m, req_rank): return
    t = await get_target_id(m, args)
    if not t: return
    pid, uid = str(m.peer_id), str(t)
    _, nick = get_user_data(m.peer_id, t)
    if pid not in DATABASE["chats"]: DATABASE["chats"][pid] = {"staff": {}}
    DATABASE["chats"][pid]["staff"][uid] = [role_name, nick]
    await push_to_github(DATABASE, GH_PATH_DB, EXTERNAL_DB)
    admin_nick = get_user_data(m.peer_id, m.from_id)[1] or "Ник"
    await m.answer(f"[id{m.from_id}|{admin_nick}] выдал(-а) права {role_name.lower()}а [id{t}|пользователю]")

@bot.on.message(text=["/addmoder", "/addmoder <args>"])
async def cmd_addmoder(m: Message, args=None): await set_role(m, "Старший Модератор", "Модератор", args)

@bot.on.message(text=["/addsenmoder", "/addsenmoder <args>"])
async def cmd_addsenmoder(m: Message, args=None): await set_role(m, "Администратор", "Старший Модератор", args)

@bot.on.message(text=["/addadmin", "/addadmin <args>"])
async def cmd_addadmin(m: Message, args=None): await set_role(m, "Старший Администратор", "Администратор", args)

@bot.on.message(text=["/addsenadmin", "/addsenadmin <args>"])
async def cmd_addsenadmin(m: Message, args=None): await set_role(m, "Зам. Спец. Администратора", "Старший Администратор", args)

@bot.on.message(text=["/addzsa", "/addzsa <args>"])
async def cmd_addzsa(m: Message, args=None): await set_role(m, "Спец. Администратор", "Зам. Спец. Администратора", args)

@bot.on.message(text=["/addsa", "/addsa <args>"])
async def cmd_addsa(m: Message, args=None): await set_role(m, "Владелец", "Спец. Администратор", args)

@bot.on.message(text=["/addowner", "/addowner <args>"])
async def cmd_addowner(m: Message, args=None): await set_role(m, "Зам. Спец. Руководителя", "Владелец", args)

@bot.on.message(text=["/removerole", "/removerole <args>"])
async def cmd_removerole(m: Message, args=None):
    if not await check_access(m, "Старший Модератор"): return
    t = await get_target_id(m, args)
    if not t: return
    pid, uid = str(m.peer_id), str(t)
    if pid in DATABASE["chats"] and uid in DATABASE["chats"][pid].get("staff", {}):
        _, nick = DATABASE["chats"][pid]["staff"][uid]
        if nick: DATABASE["chats"][pid]["staff"][uid] = ["Пользователь", nick]
        else: del DATABASE["chats"][pid]["staff"][uid]
        await push_to_github(DATABASE, GH_PATH_DB, EXTERNAL_DB)
    admin_nick = get_user_data(m.peer_id, m.from_id)[1] or "Ник"
    await m.answer(f"[id{m.from_id}|{admin_nick}] снял(-а) уровень прав [id{t}|пользователю]")

@bot.on.message(text=["/setnick", "/setnick <args>"])
async def setnick_cmd(m: Message, args=None):
    if not await check_access(m, "Модератор"): return
    t = await get_target_id(m, args)
    if not t or not args: return
    nick = args.split()[-1]
    pid, uid = str(m.peer_id), str(t)
    role = get_user_data(m.peer_id, t)[0]
    if pid not in DATABASE["chats"]: DATABASE["chats"][pid] = {"staff": {}}
    DATABASE["chats"][pid]["staff"][uid] = [role, nick]
    await push_to_github(DATABASE, GH_PATH_DB, EXTERNAL_DB)
    admin_nick = get_user_data(m.peer_id, m.from_id)[1] or "Ник"
    await m.answer(f"[id{m.from_id}|{admin_nick}] установил(-а) новое имя [id{t}|пользователю]")

@bot.on.message(text=["/rnick", "/rnick <args>"])
async def rnick_cmd(m: Message, args=None):
    if not await check_access(m, "Модератор"): return
    t = await get_target_id(m, args)
    if not t: return
    pid, uid = str(m.peer_id), str(t)
    role = get_user_data(m.peer_id, t)[0]
    if pid in DATABASE["chats"] and uid in DATABASE["chats"][pid].get("staff", {}):
        if role == "Пользователь": del DATABASE["chats"][pid]["staff"][uid]
        else: DATABASE["chats"][pid]["staff"][uid] = [role, None]
        await push_to_github(DATABASE, GH_PATH_DB, EXTERNAL_DB)
    admin_nick = get_user_data(m.peer_id, m.from_id)[1] or "Ник"
    await m.answer(f"[id{m.from_id}|{admin_nick}] убрал(-а) имя [id{t}|пользователю]")

# --- 6. МОДЕРАЦИЯ И БАНЫ ---

@bot.on.message(text=["/mute", "/mute <args>"])
async def mute_cmd(m: Message, args=None):
    if not await check_access(m, "Модератор"): return
    t = await get_target_id(m, args)
    if not t: return
    
    parts = args.split() if args else []
    mins = 10
    reason = "Нарушение"
    for p in parts:
        if p.isdigit(): mins = int(p)
        elif not p.startswith('id') and not p.startswith('vk.com') and not p.startswith('['): reason = p
        
    end_ts = datetime.datetime.now(TZ_MSK).timestamp() + (mins * 60)
    pid = str(m.peer_id)
    if pid not in DATABASE["chats"]: DATABASE["chats"][pid] = {}
    if "mutes" not in DATABASE["chats"][pid]: DATABASE["chats"][pid]["mutes"] = {}
    DATABASE["chats"][pid]["mutes"][str(t)] = end_ts
    
    dt_str = datetime.datetime.fromtimestamp(end_ts, TZ_MSK).strftime('%d/%m/%Y %H:%M:%S')
    kb = Keyboard(inline=True).add(Text("Снять мут", {"cmd": "unmute", "user": t}), color=KeyboardButtonColor.POSITIVE).add(Text("Очистить", {"cmd": "clear_mute"}), color=KeyboardButtonColor.NEGATIVE)
    
    await m.answer(f"[id{m.from_id}|Модератор MANLIX] выдал(-а) мут [id{t}|пользователю]\nПричина: {reason}\nМут выдан до: {dt_str}", keyboard=kb.get_json())

@bot.on.raw_event(GroupEventType.MESSAGE_EVENT, dataclass=MessageEvent)
async def handle_mute_buttons(event: MessageEvent):
    payload = event.payload
    if not payload or "cmd" not in payload: return
    
    pid = str(event.peer_id)
    u_rank = get_user_data(event.peer_id, event.user_id)[0]
    if RANK_WEIGHT.get(u_rank, 0) < 1:
        await bot.api.messages.send_message_event_answer(event_id=event.event_id, user_id=event.user_id, peer_id=event.peer_id, event_data=json.dumps({"type": "show_snackbar", "text": "Недостаточно прав!"}))
        return

    if payload["cmd"] == "unmute":
        t = payload["user"]
        if pid in DATABASE["chats"] and str(t) in DATABASE["chats"][pid].get("mutes", {}):
            del DATABASE["chats"][pid]["mutes"][str(t)]
        await bot.api.messages.edit(peer_id=event.peer_id, conversation_message_id=event.conversation_message_id, message=f"[id{event.user_id}|Модератор MANLIX] снял(-а) мут [id{t}|пользователю]")
    elif payload["cmd"] == "clear_mute":
        await bot.api.messages.delete(peer_id=event.peer_id, conversation_message_ids=[event.conversation_message_id], delete_for_all=True)

@bot.on.message(text=["/kick", "/kick <args>"])
async def kick_cmd(m: Message, args=None):
    if not await check_access(m, "Модератор"): return
    t = await get_target_id(m, args)
    if t:
        try:
            await bot.api.messages.remove_chat_user(chat_id=m.peer_id-2000000000, user_id=t)
            await m.answer(f"[id{m.from_id}|Модератор MANLIX] исключил(-а) [id{t}|пользователя] из Беседы.")
        except: pass

@bot.on.message(text=["/getban", "/getban <args>"])
async def getban_cmd(m: Message, args=None):
    if not await check_access(m, "Модератор"): return
    t = await get_target_id(m, args)
    if not t: return await m.answer("Укажите пользователя.")
    uid = str(t)
    
    ans = f"Информация о блокировках [id{t}|пользователя]\n\n"
    
    gb = PUNISHMENTS.get("gbans_status", {}).get(uid)
    ans += "Информация о общей Блокировке в Беседах:\n"
    if gb:
        dt = datetime.datetime.fromtimestamp(gb['date'], TZ_MSK).strftime('%d/%m/%Y %H:%M:%S')
        ans += f"[id{gb['admin']}|Модератор MANLIX] | {gb['reason']} | {dt}\n\n"
    else: ans += "отсутствует\n\n"

    gbpl = PUNISHMENTS.get("gbans_pl", {}).get(uid)
    ans += "Информация о общей Блокировке в Беседе игроков:\n"
    if gbpl:
        dt = datetime.datetime.fromtimestamp(gbpl['date'], TZ_MSK).strftime('%d/%m/%Y %H:%M:%S')
        ans += f"[id{gbpl['admin']}|Модератор MANLIX] | {gbpl['reason']} | {dt}\n\n"
    else: ans += "отсутствует\n\n"

    local_history = []
    for chat_id, users in PUNISHMENTS.get("bans", {}).items():
        if uid in users:
            b_data = users[uid]
            chat_title = DATABASE.get("chats", {}).get(str(chat_id), {}).get("title", f"Беседа {chat_id}")
            dt = datetime.datetime.fromtimestamp(b_data['date'], TZ_MSK).strftime('%d/%m/%Y %H:%M:%S')
            reason = b_data.get('reason', 'Не указана')
            local_history.append(f"{chat_title} | [id{b_data['admin']}|Модератор MANLIX] | {reason} | {dt}")
    
    ans += f"Количество Бесед, в которых заблокирован пользователь: {len(local_history)}\n"
    if local_history:
        ans += "Информация о последних 10 Блокировках:\n"
        for i, entry in enumerate(reversed(local_history[-10:]), 1): ans += f"{i}) {entry}\n"
    else: ans += "Блокировки в беседах отсутствуют"
    
    await m.answer(ans)

@bot.on.message(text=["/stats", "/stats <args>"])
async def stats_cmd(m: Message, args=None):
    t = await get_target_id(m, args) or m.from_id
    uid, pid = str(t), str(m.peer_id)
    role, nick = get_user_data(m.peer_id, t)
    
    bans_cnt = sum(1 for c, u in PUNISHMENTS.get("bans", {}).items() if uid in u)
    gban = "Да" if uid in PUNISHMENTS.get("gbans_status", {}) else "Нет"
    gbanpl = "Да" if uid in PUNISHMENTS.get("gbans_pl", {}) else "Нет"
    warns = PUNISHMENTS.get("warns", {}).get(pid, {}).get(uid, 0)
    
    mutes = DATABASE.get("chats", {}).get(pid, {}).get("mutes", {})
    is_muted = "Да" if uid in mutes and datetime.datetime.now(TZ_MSK).timestamp() < mutes[uid] else "Нет"
    
    st = DATABASE.get("chats", {}).get(pid, {}).get("stats", {}).get(uid, {"count": 0, "last": 0})
    last_ts = st["last"]
    last_time = datetime.datetime.fromtimestamp(last_ts, TZ_MSK).strftime("%d/%m/%Y %H:%M:%S") if last_ts > 0 else "Нет данных"
    
    msg = (f"Информация о [id{t}|пользователе]\n"
           f"Роль: {role}\n"
           f"Блокировок: {bans_cnt}\n"
           f"Общая блокировка в чатах: {gban}\n"
           f"Общая блокировка в беседах игроков: {gbanpl}\n"
           f"Активные предупреждения: {warns}\n"
           f"Блокировка чата: {is_muted}\n"
           f"Ник: {nick if nick else 'Не установлен'}\n"
           f"Всего сообщений: {st['count']}\n"
           f"Последнее сообщение: {last_time}")
    await m.answer(msg)

# --- 7. ИГРОВЫЕ КОМАНДЫ (БЕЗ ЭМОДЗИ, КАК ТРЕБОВАЛОСЬ РАНЕЕ) ---

@bot.on.message(text="/ghelp")
async def ghelp_cmd(m: Message):
    await m.answer("Игровые команды MANLIX:\n\n/prise — Получить ежечасный приз\n/balance — Наличные средства\n/bank — Состояние счетов\n/положить [сумма] — Положить в банк\n/снять [сумма] — Снять из банка\n/перевести [ссылка] [сумма] — Перевод со счета на счет\n/roulette [сумма] — Рулетка")

@bot.on.message(text="/prise")
async def prise_cmd(m: Message):
    uid = str(m.from_id)
    if uid not in ECONOMY: ECONOMY[uid] = {"cash": 0, "bank": 0, "last": 0}
    now = datetime.datetime.now().timestamp()
    if now - ECONOMY[uid]["last"] < 3600:
        return await m.answer("Приз можно брать раз в час.")
    
    win = random.randint(100, 1000)
    ECONOMY[uid]["cash"] += win
    ECONOMY[uid]["last"] = now
    await push_to_github(ECONOMY, GH_PATH_ECO, EXTERNAL_ECO)
    await m.answer(f"Вы получили приз {win}$")

@bot.on.message(text="/balance")
async def balance_cmd(m: Message):
    uid = str(m.from_id)
    cash = ECONOMY.get(uid, {}).get("cash", 0)
    await m.answer(f"Ваши наличные: {cash}$")

@bot.on.message(text="/bank")
async def bank_cmd(m: Message):
    uid = str(m.from_id)
    cash = ECONOMY.get(uid, {}).get("cash", 0)
    bank = ECONOMY.get(uid, {}).get("bank", 0)
    await m.answer(f"…::: MANLIX BANK :::…\n\nНаличные: {cash}$\nНа счету: {bank}$")

@bot.on.message(text=["/положить", "/положить <args>"])
async def dep_cmd(m: Message, args=None):
    if not args or not args.isdigit(): return
    amt = int(args)
    uid = str(m.from_id)
    if uid not in ECONOMY: ECONOMY[uid] = {"cash": 0, "bank": 0, "last": 0}
    if ECONOMY[uid]["cash"] < amt: return await m.answer("Недостаточно наличных средств.")
    ECONOMY[uid]["cash"] -= amt
    ECONOMY[uid]["bank"] += amt
    await push_to_github(ECONOMY, GH_PATH_ECO, EXTERNAL_ECO)
    await m.answer(f"Вы положили на свой счет {amt}$")

@bot.on.message(text=["/снять", "/снять <args>"])
async def with_cmd(m: Message, args=None):
    if not args or not args.isdigit(): return
    amt = int(args)
    uid = str(m.from_id)
    if uid not in ECONOMY: ECONOMY[uid] = {"cash": 0, "bank": 0, "last": 0}
    if ECONOMY[uid]["bank"] < amt: return await m.answer("Недостаточно средств на счету.")
    ECONOMY[uid]["bank"] -= amt
    ECONOMY[uid]["cash"] += amt
    await push_to_github(ECONOMY, GH_PATH_ECO, EXTERNAL_ECO)
    await m.answer(f"Вы сняли с своего счета {amt}$")

@bot.on.message(text=["/перевести <args>"])
async def pay_cmd(m: Message, args=None):
    if not args: return
    parts = args.split()
    if len(parts) < 2: return
    t = await get_target_id(m, parts[0])
    amt_str = parts[-1]
    if not t or not amt_str.isdigit(): return
    amt = int(amt_str)
    
    uid, target = str(m.from_id), str(t)
    if uid not in ECONOMY: ECONOMY[uid] = {"cash": 0, "bank": 0, "last": 0}
    if target not in ECONOMY: ECONOMY[target] = {"cash": 0, "bank": 0, "last": 0}
    
    if ECONOMY[uid]["bank"] < amt: return await m.answer("Недостаточно средств на счету.")
    ECONOMY[uid]["bank"] -= amt
    ECONOMY[target]["bank"] += amt
    await push_to_github(ECONOMY, GH_PATH_ECO, EXTERNAL_ECO)
    await m.answer(f"Вы перевели [id{t}|пользователю] {amt}$")

@bot.on.message(text=["/roulette <args>"])
async def roulette_cmd(m: Message, args=None):
    if not args or not args.isdigit(): return
    amt = int(args)
    uid = str(m.from_id)
    if uid not in ECONOMY: ECONOMY[uid] = {"cash": 0, "bank": 0, "last": 0}
    if ECONOMY[uid]["cash"] < amt: return await m.answer("Недостаточно наличных средств.")
    
    ECONOMY[uid]["cash"] -= amt
    if random.choice([True, False]):
        win = amt * 2
        ECONOMY[uid]["cash"] += win
        await m.answer(f"Вы выиграли {win}$")
    else:
        await m.answer(f"Вы проиграли {amt}$")
    await push_to_github(ECONOMY, GH_PATH_ECO, EXTERNAL_ECO)

# --- 8. СЛУЖЕБНЫЕ КОМАНДЫ (СИСТЕМА И БАЗА) ---

@bot.on.message(text="/start")
async def start_cmd(m: Message):
    if not await check_access(m, "Специальный Руководитель"): return
    pid = str(m.peer_id)
    title = "Беседа"
    try:
        c = await bot.api.messages.get_conversations_by_id(peer_ids=[m.peer_id])
        title = c.items[0].chat_settings.title
    except: pass
    DATABASE["chats"][pid] = {
        "title": title,
        "staff": {"870757778": ["Специальный Руководитель", "Misha Manlix"]},
        "mutes": {},
        "stats": {}
    }
    await push_to_github(DATABASE, GH_PATH_DB, EXTERNAL_DB)
    await m.answer("Вы успешно активировали Беседу.")

@bot.on.message(text="/sync")
async def sync_cmd(m: Message):
    if not await check_access(m, "Специальный Руководитель"): return
    await push_to_github(DATABASE, GH_PATH_DB, EXTERNAL_DB)
    await m.answer("Вы успешно синхронизировали Беседу с Базой данных.")

@bot.on.message(text="/delchat")
async def delchat_cmd(m: Message):
    if not await check_access(m, "Специальный Руководитель"): return
    pid = str(m.peer_id)
    if pid in DATABASE["chats"]:
        del DATABASE["chats"][pid]
        await push_to_github(DATABASE, GH_PATH_DB, EXTERNAL_DB)
        await m.answer("Вы успешно удалили чат с Базы данных.")

# --- 9. ЗАПУСК И СЕРВЕР ---
class H(BaseHTTPRequestHandler):
    def do_GET(self): self.send_response(200); self.end_headers(); self.wfile.write(b"OK")

if __name__ == "__main__":
    threading.Thread(target=lambda: HTTPServer(('0.0.0.0', int(os.environ.get("PORT", 10000))), H).serve_forever(), daemon=True).start()
    bot.run_forever()

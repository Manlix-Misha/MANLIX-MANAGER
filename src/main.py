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

if "gbans_pl" not in PUNISHMENTS: PUNISHMENTS["gbans_pl"] = []
if "gbans_status" not in PUNISHMENTS: PUNISHMENTS["gbans_status"] = {}
if "bans" not in PUNISHMENTS: PUNISHMENTS["bans"] = {}
if "warns" not in PUNISHMENTS: PUNISHMENTS["warns"] = {}

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
    staff = DATABASE.get("chats", {}).get(str(peer_id), {}).get("staff", {})
    return staff.get(str(user_id), ["Пользователь", None])

def get_eco_data(user_id):
    uid = str(user_id)
    if uid not in ECONOMY: ECONOMY[uid] = {"balance": 0, "bank": 0, "last_prise": 0}
    if "bank" not in ECONOMY[uid]: ECONOMY[uid]["bank"] = 0
    return ECONOMY[uid]

async def get_nick(peer_id, user_id, clickable=False):
    if int(user_id) == 870757778:
        return f"[id870757778|Misha Manlix]" if clickable else "Misha Manlix"
    _, nick = get_user_data(peer_id, user_id)
    if nick:
        return f"[id{user_id}|{nick}]" if clickable else nick
    try:
        u = (await bot.api.users.get(user_ids=[user_id]))[0]
        return f"[id{user_id}|{u.first_name} {u.last_name}]" if clickable else f"{u.first_name} {u.last_name}"
    except: 
        return f"[id{user_id}|Пользователь]" if clickable else "Пользователь"

async def check_active(m: Message):
    if int(m.from_id) == 870757778: return True
    if str(m.from_id) in PUNISHMENTS.get("gbans_pl", []): return False
    pid = str(m.peer_id)
    if str(m.from_id) in PUNISHMENTS.get("bans", {}).get(pid, []):
        try: await bot.api.messages.remove_chat_user(chat_id=m.peer_id-2000000000, user_id=m.from_id)
        except: pass
        return False
    return pid in DATABASE.get("chats", {})

async def check_access(m: Message, req_rank: str):
    u_rank = get_user_data(m.peer_id, m.from_id)[0]
    if RANK_WEIGHT.get(u_rank, 0) < RANK_WEIGHT.get(req_rank, 0):
        await m.answer("Недостаточно прав!")
        return False
    return True

def extract_id(text):
    if not text: return None
    match = re.search(r'id(\d+)', str(text))
    if match: return int(match.group(1))
    digits = re.findall(r'\d+', str(text))
    return int(digits[0]) if digits else None

class ChatMiddleware(BaseMiddleware[Message]):
    async def pre(self):
        if not self.event.from_id: return
        pid, uid = str(self.event.peer_id), str(self.event.from_id)
        
        # Обновление статистики сообщений
        if pid in DATABASE.get("chats", {}):
            if "stats" not in DATABASE["chats"][pid]: DATABASE["chats"][pid]["stats"] = {}
            if uid not in DATABASE["chats"][pid]["stats"]: DATABASE["chats"][pid]["stats"][uid] = {"count": 0, "last": 0}
            DATABASE["chats"][pid]["stats"][uid]["count"] += 1
            DATABASE["chats"][pid]["stats"][uid]["last"] = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=3))).timestamp()

        # Проверка блокировок и мутов для удаления сообщений
        is_gban_pl = uid in PUNISHMENTS.get("gbans_pl", [])
        is_ban = uid in PUNISHMENTS.get("bans", {}).get(pid, [])
        mutes = DATABASE.get("chats", {}).get(pid, {}).get("mutes", {})
        is_muted = uid in mutes and datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=3))).timestamp() < mutes[uid]
        
        if is_gban_pl or is_ban or is_muted:
            try: await bot.api.messages.delete(message_ids=[self.event.conversation_message_id], peer_id=self.event.peer_id, delete_for_all=True)
            except: pass
            self.event.text = "" 

bot.labeler.message_view.register_middleware(ChatMiddleware)

# --- 4. КОМАНДЫ ПОЛЬЗОВАТЕЛЕЙ И МОДЕРАЦИИ ---
@bot.on.message(text="/help")
async def help_handler(m: Message):
    if not await check_active(m): return
    rank = get_user_data(m.peer_id, m.from_id)[0]
    w = RANK_WEIGHT.get(rank, 0)
    
    msg1 = "Команды пользователей:\n/info - официальные ресурсы \n/stats - статистика пользователя \n/getid - оригинальная ссылка VK.\n"
    if w >= 1: msg1 += "\nКоманды для модераторов:\n/staff - Руководство Беседы \n/kick - исключить пользователя из Беседы. \n/mute - выдать Блокировку чата. \n/unmute - снять Блокировку чата.\n/setnick - установить имя пользователю.\n/rnick - удалить имя пользователю.\n/nlist - список пользователей с ником.\n/getban - информация о Блокировках.\n"
    if w >= 2: msg1 += "\nКоманды старших модераторов: \n/addmoder - выдать права модератора. \n/removerole - снять уровень прав.\n/ban - блокировка пользователя в Беседе. \n/unban - снятие блокировки пользователю в беседе.\n"
    if w >= 3: msg1 += "\nКоманды администраторов:\n/addsenmoder - выдать права старшего модератора. \n"
    if w >= 4: msg1 += "\nКоманды старших администраторов: \n/addadmin - выдать права администратора.\n"
    if w >= 5: msg1 += "\nКоманды заместителей спец. администраторов: \n/addsenadmin - выдать права старшего модератора.\n"
    if w >= 6: msg1 += "\nКоманды спец. администраторов:\n/addzsa - выдать права заместителя спец. администратора. \n"
    if w >= 7: msg1 += "\nКоманды владельца:\n/addsa - выдать права специального администратора. \n"
    await m.answer(msg1.strip())
    
    if w >= 8:
        msg2 = "Команды руководства Бота:\n\nЗам. Спец. Руководителя:\n/gstaff - руководство Бота.\n/addowner - выдать права владельца.\n/gbanpl - Блокировка пользователя во всех игровых Беседах.\n/gunbanpl - снятие Блокировки во всех игровых Беседах. \n\nОсновной Зам. Спец. Руководителя:\nОтсутствуют.\n"
        if w >= 10: msg2 += "\nСпец. Руководителя: \n/start - активировать Беседу.\n/sync - синхронизация с базой данных.\n/chatid - узнать айди Беседы.\n/delchat - удалить чат с Базы данных."
        await m.answer(msg2.strip())

@bot.on.message(text="/info")
async def info_cmd(m: Message):
    if await check_active(m): await m.answer("Официальные ресурсы MANLIX: [Укажите ссылки]")

@bot.on.message(text=["/getid", "/getid <args>"])
async def getid_cmd(m: Message, args=None):
    if not await check_active(m): return
    t = m.reply_message.from_id if m.reply_message else (extract_id(args) or m.from_id)
    await m.answer(f"Оригинальная ссылка [id{t}|пользователя]: https://vk.com/id{t}")

@bot.on.message(text=["/stats", "/stats <args>"])
async def stats_cmd(m: Message, args=None):
    if not await check_active(m): return
    t = m.reply_message.from_id if m.reply_message else (extract_id(args) or m.from_id)
    uid, pid = str(t), str(m.peer_id)
    role, nick = get_user_data(m.peer_id, t)
    
    bans_count = len([c for c, users in PUNISHMENTS.get("bans", {}).items() if uid in users])
    is_gban = "Да" if uid in PUNISHMENTS.get("gbans_status", {}) else "Нет"
    is_gbanpl = "Да" if uid in PUNISHMENTS.get("gbans_pl", []) else "Нет"
    warns = PUNISHMENTS.get("warns", {}).get(pid, {}).get(uid, 0)
    
    mutes = DATABASE.get("chats", {}).get(pid, {}).get("mutes", {})
    is_muted = "Да" if uid in mutes and datetime.datetime.now(datetime.timezone.utc).timestamp() < mutes[uid] else "Нет"
    
    stats = DATABASE.get("chats", {}).get(pid, {}).get("stats", {}).get(uid, {"count": 0, "last": 0})
    msg_count = stats["count"]
    last_time = datetime.datetime.fromtimestamp(stats["last"], datetime.timezone(datetime.timedelta(hours=3))).strftime("%d/%m/%Y %I:%M:%S %p") if stats["last"] > 0 else "Нет данных"
    
    await m.answer(f"Информация о [id{t}|пользователе]\nРоль: {role}\nБлокировок: {bans_count}\nОбщая блокировка в чатах: {is_gban}\nОбщая блокировка в беседах игроков: {is_gbanpl}\nАктивные предупреждения: {warns}\nБлокировка чата: {is_muted}\nНик: {nick if nick else 'Не установлен'}\nВсего сообщений: {msg_count}\nПоследнее сообщение: {last_time}")

@bot.on.message(text=["/getban", "/getban <args>"])
async def getban_cmd(m: Message, args=None):
    if not await check_active(m) or not await check_access(m, "Модератор"): return
    t = m.reply_message.from_id if m.reply_message else extract_id(args)
    if not t: return
    uid = str(t)
    vk_nick = await get_nick(m.peer_id, t, clickable=True)
    
    gban_status = "присутствует" if uid in PUNISHMENTS.get("gbans_status", {}) else "отсутствует"
    gbanpl_status = "присутствует" if uid in PUNISHMENTS.get("gbans_pl", []) else "отсутствует"
    local_bans = [c for c, users in PUNISHMENTS.get("bans", {}).items() if uid in users]
    
    ans = f"Информация о блокировках {vk_nick}\n\nИнформация о общей блокировке в беседах: {gban_status}\n\nИнформация о блокировке в беседах игроков: {gbanpl_status}\n"
    if local_bans: ans += f"Блокировки в беседах: присутствуют ({len(local_bans)} шт.)"
    else: ans += "Блокировки в беседах отсутствуют"
    await m.answer(ans)

@bot.on.message(text="/gstaff")
async def gstaff_cmd(m: Message):
    if not await check_active(m) or not await check_access(m, "Зам. Специального Руководителя"): return
    await m.answer("MANLIX MANAGER | Команда Бота:\n\n| Специальный Руководитель:\n– [id870757778|Misha Manlix]\n\n| Основной зам. Спец. Руководителя:\n– Отсутствует.\n\n| Зам. Спец. Руководителя:\n– Отсутствует.\n– Отсутствует.")

@bot.on.message(text="/staff")
async def staff_list(m: Message):
    if not await check_active(m) or not await check_access(m, "Модератор"): return
    staff_data = DATABASE.get("chats", {}).get(str(m.peer_id), {}).get("staff", {})
    ranks_order = ["Владелец", "Спец. Администратор", "Зам. Спец. Администратора", "Старший Администратор", "Администратор", "Старший Модератор", "Модератор"]
    res = []
    for r in ranks_order:
        res.append(f"{r}:")
        found = False
        for uid, data in staff_data.items():
            if data[0] == r:
                res.append(f"– [id{uid}|{data[1] if data[1] else 'Админ'}]")
                found = True
        if not found: res.append("– Отсутствует.")
        res.append("")
    await m.answer("\n".join(res).strip())

@bot.on.message(text="/nlist")
async def nlist_cmd(m: Message):
    if not await check_active(m) or not await check_access(m, "Модератор"): return
    staff_data = DATABASE.get("chats", {}).get(str(m.peer_id), {}).get("staff", {})
    res = ["Список пользователей с ником:"]
    idx = 1
    for uid, data in staff_data.items():
        if data[1]:
            res.append(f"{idx}. [id{uid}|{data[1]}]")
            idx += 1
    if idx == 1: res.append("Пусто.")
    await m.answer("\n".join(res))

# --- 5. СИСТЕМА НАКАЗАНИЙ ---
@bot.on.raw_event(GroupEventType.MESSAGE_NEW, dataclass=Message)
async def user_leave_handler(event: Message):
    if event.action and event.action.type.value in ["chat_kick_user", "chat_exit_user"]:
        kb = (Keyboard(inline=True).add(Text("Исключить", {"cmd": "kick_confirm", "user": event.action.member_id}), color=KeyboardButtonColor.NEGATIVE)).get_json()
        await event.answer("Бот покинул(-а) Беседу", keyboard=kb)

@bot.on.message(text=["/kick", "/kick <args>"])
async def kick_cmd(m: Message, args=None):
    if not await check_active(m) or not await check_access(m, "Модератор"): return
    t = m.reply_message.from_id if m.reply_message else extract_id(args)
    if not t: return
    try:
        await bot.api.messages.remove_chat_user(chat_id=m.peer_id-2000000000, user_id=t)
        await m.answer(f"[id{m.from_id}|Модератор MANLIX] исключил(-а) [id{t}|пользователя] из Беседы.")
    except: pass

@bot.on.message(payload_contains={"cmd": "kick_confirm"})
async def kick_confirm(m: Message):
    if await check_access(m, "Модератор"):
        try: await bot.api.messages.remove_chat_user(chat_id=m.peer_id-2000000000, user_id=m.get_payload_json()["user"])
        except: pass

@bot.on.message(text=["/mute", "/mute <args>"])
async def mute_cmd(m: Message, args=None):
    if not await check_active(m) or not await check_access(m, "Модератор"): return
    t = m.reply_message.from_id if m.reply_message else extract_id(args)
    if not t: return
    parts = args.split() if args else []
    try:
        minutes = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else (int(parts[0]) if parts and parts[0].isdigit() else 10)
        reason = " ".join(parts[2:]) if len(parts) > 2 else (" ".join(parts[1:]) if parts and not parts[0].isdigit() else "Не указана")
    except: minutes, reason = 10, "Не указана"
    
    end = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=3))) + datetime.timedelta(minutes=minutes)
    pid = str(m.peer_id)
    if pid not in DATABASE["chats"]: DATABASE["chats"][pid] = {"mutes": {}}
    if "mutes" not in DATABASE["chats"][pid]: DATABASE["chats"][pid]["mutes"] = {}
    DATABASE["chats"][pid]["mutes"][str(t)] = end.timestamp()
    await push_to_github(DATABASE, GH_PATH_DB, EXTERNAL_DB, "Mute")
    
    kb = Keyboard(inline=True)
    kb.add(Text("Снять мут", {"cmd": "unmute_edit", "user": t}), color=KeyboardButtonColor.POSITIVE)
    kb.add(Text("Очистить", {"cmd": "clear"}), color=KeyboardButtonColor.NEGATIVE)
    
    msg = f"[id{m.from_id}|Модератор MANLIX] выдал(-а) мут [id{t}|пользователю]\nПричина: {reason}\nМут выдан до: {end.strftime('%d/%m/%Y %H:%M:%S')}"
    await m.answer(msg, keyboard=kb.get_json())

@bot.on.message(payload_contains={"cmd": "unmute_edit"})
async def unmute_edit(m: Message):
    if await check_access(m, "Модератор"):
        t = m.get_payload_json()["user"]
        pid = str(m.peer_id)
        if pid in DATABASE["chats"] and str(t) in DATABASE["chats"][pid].get("mutes", {}):
            del DATABASE["chats"][pid]["mutes"][str(t)]
            await push_to_github(DATABASE, GH_PATH_DB, EXTERNAL_DB, "Unmute")
        await bot.api.messages.edit(peer_id=m.peer_id, conversation_message_id=m.conversation_message_id, message=f"[id{m.from_id}|Модератор MANLIX] снял(-а) мут [id{t}|пользователю]")

@bot.on.message(payload_contains={"cmd": "clear"})
async def clear_edit(m: Message):
    if await check_access(m, "Модератор"):
        try: await bot.api.messages.delete(message_ids=[m.conversation_message_id], peer_id=m.peer_id, delete_for_all=True)
        except: pass

@bot.on.message(text=["/unmute", "/unmute <args>"])
async def unmute_cmd(m: Message, args=None):
    if not await check_active(m) or not await check_access(m, "Модератор"): return
    t = m.reply_message.from_id if m.reply_message else extract_id(args)
    if not t: return
    pid = str(m.peer_id)
    if pid in DATABASE["chats"] and str(t) in DATABASE["chats"][pid].get("mutes", {}):
        del DATABASE["chats"][pid]["mutes"][str(t)]
        await push_to_github(DATABASE, GH_PATH_DB, EXTERNAL_DB, "Unmute")
    await m.answer(f"[id{m.from_id}|Модератор MANLIX] снял(-а) мут [id{t}|пользователю]")

@bot.on.message(text=["/ban", "/ban <args>"])
async def ban_cmd(m: Message, args=None):
    if not await check_active(m) or not await check_access(m, "Старший Модератор"): return
    t = m.reply_message.from_id if m.reply_message else extract_id(args)
    if not t: return
    pid = str(m.peer_id)
    if pid not in PUNISHMENTS["bans"]: PUNISHMENTS["bans"][pid] = []
    if str(t) not in PUNISHMENTS["bans"][pid]: PUNISHMENTS["bans"][pid].append(str(t))
    await push_to_github(PUNISHMENTS, GH_PATH_PUN, EXTERNAL_PUN, "Ban")
    try: await bot.api.messages.remove_chat_user(chat_id=m.peer_id-2000000000, user_id=t)
    except: pass
    await m.answer(f"[id{m.from_id}|Модератор MANLIX] заблокировал [id{t}|пользователя] в Беседе.")

@bot.on.message(text=["/unban", "/unban <args>"])
async def unban_cmd(m: Message, args=None):
    if not await check_active(m) or not await check_access(m, "Старший Модератор"): return
    t = extract_id(args)
    if not t: return
    pid = str(m.peer_id)
    if pid in PUNISHMENTS["bans"] and str(t) in PUNISHMENTS["bans"][pid]:
        PUNISHMENTS["bans"][pid].remove(str(t))
        await push_to_github(PUNISHMENTS, GH_PATH_PUN, EXTERNAL_PUN, "Unban")
        await m.answer(f"[id{m.from_id}|Модератор MANLIX] разблокировал [id{t}|пользователю] в беседе.")

@bot.on.message(text=["/warn", "/warn <args>"])
async def warn_cmd(m: Message, args=None):
    if not await check_active(m) or not await check_access(m, "Модератор"): return
    t = m.reply_message.from_id if m.reply_message else extract_id(args)
    if not t: return
    pid, uid = str(m.peer_id), str(t)
    if pid not in PUNISHMENTS["warns"]: PUNISHMENTS["warns"][pid] = {}
    PUNISHMENTS["warns"][pid][uid] = PUNISHMENTS["warns"][pid].get(uid, 0) + 1
    
    current_warns = PUNISHMENTS["warns"][pid][uid]
    if current_warns >= 3:
        PUNISHMENTS["warns"][pid][uid] = 0
        if pid not in PUNISHMENTS["bans"]: PUNISHMENTS["bans"][pid] = []
        PUNISHMENTS["bans"][pid].append(uid)
        await push_to_github(PUNISHMENTS, GH_PATH_PUN, EXTERNAL_PUN, "Warn Ban")
        try: await bot.api.messages.remove_chat_user(chat_id=m.peer_id-2000000000, user_id=t)
        except: pass
        await m.answer(f"[id{t}|Пользователь] получил 3/3 варна и был забанен.")
    else:
        await push_to_github(PUNISHMENTS, GH_PATH_PUN, EXTERNAL_PUN, "Warn")
        await m.answer(f"[id{m.from_id}|Модератор MANLIX] выдал варн [id{t}|пользователю] ({current_warns}/3)")

@bot.on.message(text=["/rwarn", "/rwarn <args>"])
async def rwarn_cmd(m: Message, args=None):
    if not await check_active(m) or not await check_access(m, "Модератор"): return
    t = m.reply_message.from_id if m.reply_message else extract_id(args)
    if not t: return
    pid, uid = str(m.peer_id), str(t)
    if pid in PUNISHMENTS["warns"] and uid in PUNISHMENTS["warns"][pid] and PUNISHMENTS["warns"][pid][uid] > 0:
        PUNISHMENTS["warns"][pid][uid] -= 1
        await push_to_github(PUNISHMENTS, GH_PATH_PUN, EXTERNAL_PUN, "Remove Warn")
        await m.answer(f"[id{m.from_id}|Модератор MANLIX] снял варн [id{t}|пользователю] ({PUNISHMENTS['warns'][pid][uid]}/3)")

# --- 6. УПРАВЛЕНИЕ РОЛЯМИ И НИКАМИ ---
@bot.on.message(text=["/setnick", "/setnick <args>"])
async def setnick_cmd(m: Message, args=None):
    if not await check_active(m) or not await check_access(m, "Модератор"): return
    p = args.split()
    t, nick = (m.reply_message.from_id if m.reply_message else extract_id(p[0])), p[-1]
    if not t or not nick: return
    pid = str(m.peer_id)
    r = get_user_data(m.peer_id, t)[0]
    if "chats" not in DATABASE: DATABASE["chats"] = {}
    if pid not in DATABASE["chats"]: DATABASE["chats"][pid] = {"staff": {}}
    DATABASE["chats"][pid]["staff"][str(t)] = [r, nick]
    await push_to_github(DATABASE, GH_PATH_DB, EXTERNAL_DB, "Nick")
    admin_nick = await get_nick(m.peer_id, m.from_id)
    await m.answer(f"[id{m.from_id}|{admin_nick}] установил(-а) новое имя [id{t}|пользователю]")

@bot.on.message(text=["/rnick", "/rnick <args>"])
async def rnick_cmd(m: Message, args=None):
    if not await check_active(m) or not await check_access(m, "Модератор"): return
    t = m.reply_message.from_id if m.reply_message else extract_id(args)
    if not t: return
    pid = str(m.peer_id)
    r = get_user_data(m.peer_id, t)[0]
    if pid in DATABASE["chats"] and str(t) in DATABASE["chats"][pid].get("staff", {}):
        if r == "Пользователь": del DATABASE["chats"][pid]["staff"][str(t)]
        else: DATABASE["chats"][pid]["staff"][str(t)] = [r, None]
    await push_to_github(DATABASE, GH_PATH_DB, EXTERNAL_DB, "Remove nick")
    admin_nick = await get_nick(m.peer_id, m.from_id)
    await m.answer(f"[id{m.from_id}|{admin_nick}] убрал(-а) имя [id{t}|пользователю]")

async def grant_role(m: Message, args, req_rank, role_name, action_text):
    if not await check_active(m) or not await check_access(m, req_rank): return
    t = m.reply_message.from_id if m.reply_message else extract_id(args)
    if not t: return
    pid = str(m.peer_id)
    _, target_nick = get_user_data(m.peer_id, t)
    if "chats" not in DATABASE: DATABASE["chats"] = {}
    if pid not in DATABASE["chats"]: DATABASE["chats"][pid] = {"staff": {}}
    
    if role_name == "Пользователь":
        if target_nick: DATABASE["chats"][pid]["staff"][str(t)] = ["Пользователь", target_nick]
        elif str(t) in DATABASE["chats"][pid]["staff"]: del DATABASE["chats"][pid]["staff"][str(t)]
    else:
        DATABASE["chats"][pid]["staff"][str(t)] = [role_name, target_nick]
        
    await push_to_github(DATABASE, GH_PATH_DB, EXTERNAL_DB, f"Role {role_name}")
    admin_nick = await get_nick(m.peer_id, m.from_id)
    await m.answer(f"[id{m.from_id}|{admin_nick}] {action_text} [id{t}|пользователю]")

@bot.on.message(text=["/addmoder", "/addmoder <args>"])
async def am(m, args=None): await grant_role(m, args, "Старший Модератор", "Модератор", "выдал(-а) права модератора")

@bot.on.message(text=["/addsenmoder", "/addsenmoder <args>"])
async def asm(m, args=None): await grant_role(m, args, "Администратор", "Старший Модератор", "выдал(-а) права старшего модератора")

@bot.on.message(text=["/addadmin", "/addadmin <args>"])
async def aa(m, args=None): await grant_role(m, args, "Старший Администратор", "Администратор", "выдал(-а) права администратора")

@bot.on.message(text=["/addsenadmin", "/addsenadmin <args>"])
async def asa(m, args=None): await grant_role(m, args, "Зам. Спец. Администратора", "Старший Администратор", "выдал(-а) права старшего администратора")

@bot.on.message(text=["/addzsa", "/addzsa <args>"])
async def azsa(m, args=None): await grant_role(m, args, "Спец. Администратор", "Зам. Спец. Администратора", "выдал(-а) права заместителя специального администратора")

@bot.on.message(text=["/addsa", "/addsa <args>"])
async def asa2(m, args=None): await grant_role(m, args, "Владелец", "Спец. Администратор", "выдал(-а) права специального администратора")

@bot.on.message(text=["/addowner", "/addowner <args>"])
async def ao(m, args=None): await grant_role(m, args, "Зам. Специального Руководителя", "Владелец", "выдал(-а) права владельца")

@bot.on.message(text=["/removerole", "/removerole <args>"])
async def rr(m, args=None): await grant_role(m, args, "Старший Модератор", "Пользователь", "снял(-а) уровень прав")

# --- 7. ГЛОБАЛЬНЫЕ КОМАНДЫ (Спец. Руководитель и Замы) ---
@bot.on.message(text=["/gbanpl", "/gbanpl <args>"])
async def gbanpl_cmd(m: Message, args=None):
    if not await check_access(m, "Зам. Специального Руководителя"): return
    t = m.reply_message.from_id if m.reply_message else extract_id(args)
    if t:
        if str(t) not in PUNISHMENTS["gbans_pl"]: PUNISHMENTS["gbans_pl"].append(str(t))
        await push_to_github(PUNISHMENTS, GH_PATH_PUN, EXTERNAL_PUN, "GBanPL")
        await m.answer(f"[id{m.from_id}|Специальный Руководитель] заблокировал [id{t}|пользователя] во всех игровых Беседах.")

@bot.on.message(text=["/gunbanpl", "/gunbanpl <args>"])
async def gunbanpl_cmd(m: Message, args=None):
    if not await check_access(m, "Зам. Специального Руководителя"): return
    t = extract_id(args)
    if t and str(t) in PUNISHMENTS["gbans_pl"]:
        PUNISHMENTS["gbans_pl"].remove(str(t))
        await push_to_github(PUNISHMENTS, GH_PATH_PUN, EXTERNAL_PUN, "GUnbanPL")
        await m.answer(f"[id{m.from_id}|Специальный Руководитель] разблокировал [id{t}|пользователя] во всех игровых Беседах.")

@bot.on.message(text=["/gban", "/gban <args>"])
async def gban_cmd(m: Message, args=None):
    if not await check_access(m, "Зам. Специального Руководителя"): return
    t = m.reply_message.from_id if m.reply_message else extract_id(args)
    parts = args.split() if args else []
    reason = " ".join(parts[1:]) if len(parts) > 1 else "Не указана"
    if t:
        PUNISHMENTS["gbans_status"][str(t)] = reason
        await push_to_github(PUNISHMENTS, GH_PATH_PUN, EXTERNAL_PUN, "GBan Status")
        await m.answer(f"[id{m.from_id}|Специальный Руководитель] занес [id{t}|пользователя] в глобальную Блокировку Бота.")

@bot.on.message(text=["/gunban", "/gunban <args>"])
async def gunban_cmd(m: Message, args=None):
    if not await check_access(m, "Зам. Специального Руководителя"): return
    t = extract_id(args)
    if t and str(t) in PUNISHMENTS["gbans_status"]:
        del PUNISHMENTS["gbans_status"][str(t)]
        await push_to_github(PUNISHMENTS, GH_PATH_PUN, EXTERNAL_PUN, "GUnban Status")
        await m.answer(f"[id{m.from_id}|Специальный Руководитель] вынес [id{t}|пользователя] из Глобальной Блокировки Бота.")

@bot.on.message(text="/start")
async def start_handler(m: Message):
    if int(m.from_id) != 870757778: return
    DATABASE["chats"][str(m.peer_id)] = {"staff": {"870757778": ["Специальный Руководитель", "Misha Manlix"]}}
    await push_to_github(DATABASE, GH_PATH_DB, EXTERNAL_DB, "Start")
    await m.answer("Вы успешно активировали Беседу.")

@bot.on.message(text="/sync")
async def sync_cmd(m: Message):
    if int(m.from_id) != 870757778: return
    h = {"Authorization": f"token {GH_TOKEN}"}
    async with aiohttp.ClientSession() as s:
        for p, gh in [(EXTERNAL_DB, GH_PATH_DB), (EXTERNAL_ECO, GH_PATH_ECO), (EXTERNAL_PUN, GH_PATH_PUN)]:
            async with s.get(f"https://api.github.com/repos/{GH_REPO}/contents/{gh}", headers=h) as r:
                if r.status == 200:
                    content = json.loads(base64.b64decode((await r.json())['content']).decode('utf-8'))
                    if "database" in p: global DATABASE; DATABASE = content
                    if "economy" in p: global ECONOMY; ECONOMY = content
                    if "punish" in p: global PUNISHMENTS; PUNISHMENTS = content
    await m.answer("Вы успешно синхронизировали Беседу с Базой данных.")

@bot.on.message(text="/delchat")
async def delchat_cmd(m: Message):
    if int(m.from_id) != 870757778: return
    if str(m.peer_id) in DATABASE.get("chats", {}):
        del DATABASE["chats"][str(m.peer_id)]
        await push_to_github(DATABASE, GH_PATH_DB, EXTERNAL_DB, "Delchat")
    await m.answer("Вы успешно удалили чат с Базы данных.")

@bot.on.message(text="/chatid")
async def chatid_cmd(m: Message):
    if int(m.from_id) != 870757778: return
    await m.answer(f"ID: {m.peer_id}")

# --- 8. ИГРОВЫЕ КОМАНДЫ ---
@bot.on.message(text="/ghelp")
async def ghelp_cmd(m: Message):
    if not await check_active(m): return
    msg = ("Игровые команды MANLIX:\n\n"
           "/prise — Получить ежечасный приз\n"
           "/balance — Наличные средства\n"
           "/bank — Состояние счетов\n"
           "/положить [сумма] — Положить в банк\n"
           "/снять [сумма] — Снять из банка\n"
           "/перевести [ссылка] [сумма] — Перевод со счета на счет\n"
           "/roulette [сумма] — Рулетка")
    await m.answer(msg)

@bot.on.message(text="/prise")
async def prise_cmd(m: Message):
    if not await check_active(m): return
    global ECO_CHANGED
    data = get_eco_data(m.from_id)
    now = datetime.datetime.now().timestamp()
    if now - data["last_prise"] < 3600:
        return await m.answer(f"Приз доступен раз в час!")
    win = random.randint(100, 1000)
    data["balance"] += win
    data["last_prise"] = now
    ECO_CHANGED = True
    await m.answer(f"Вы получили приз {win}$")

@bot.on.message(text="/balance")
async def balance_cmd(m: Message):
    if not await check_active(m): return
    await m.answer(f"Ваши наличные: {get_eco_data(m.from_id)['balance']}$")

@bot.on.message(text="/bank")
async def bank_cmd(m: Message):
    if not await check_active(m): return
    data = get_eco_data(m.from_id)
    await m.answer(f"…::: MANLIX BANK :::…\n\nНаличные: {data['balance']}$\nНа счету: {data['bank']}$")

@bot.on.message(text=["/положить", "/положить <amount:int>"])
async def deposit_cmd(m: Message, amount: int = None):
    if not await check_active(m) or amount is None or amount <= 0: return
    global ECO_CHANGED
    u = get_eco_data(m.from_id)
    if u["balance"] < amount: return await m.answer("Недостаточно наличных!")
    u["balance"] -= amount
    u["bank"] += amount
    ECO_CHANGED = True
    await m.answer(f"Вы положили на свой счет {amount}$")

@bot.on.message(text=["/снять", "/снять <amount:int>"])
async def withdraw_cmd(m: Message, amount: int = None):
    if not await check_active(m) or amount is None or amount <= 0: return
    global ECO_CHANGED
    u = get_eco_data(m.from_id)
    if u["bank"] < amount: return await m.answer("Недостаточно средств в банке!")
    u["bank"] -= amount
    u["balance"] += amount
    ECO_CHANGED = True
    await m.answer(f"Вы сняли с своего счета {amount}$")

@bot.on.message(text=["/перевести", "/перевести <args>"])
async def transfer_cmd(m: Message, args=None):
    if not await check_active(m) or not args: return
    p = args.split()
    if len(p) < 2: return
    tid, amt = extract_id(p[0]), int(p[1]) if p[1].isdigit() else 0
    if amt <= 0 or tid == m.from_id: return
    global ECO_CHANGED
    s, r = get_eco_data(m.from_id), get_eco_data(tid)
    if s["bank"] < amt: return await m.answer("Недостаточно денег в банке!")
    s["bank"] -= amt
    r["bank"] += amt
    ECO_CHANGED = True
    await m.answer(f"Вы перевели [id{tid}|пользователю] {amt}$")

@bot.on.message(text=["/roulette", "/roulette <amount:int>"])
async def roulette_cmd(m: Message, amount: int = None):
    if not await check_active(m) or amount is None: return
    if amount < 100: return await m.answer("Минимальная ставка — 100$")
    global ECO_CHANGED
    u = get_eco_data(m.from_id)
    if u["balance"] < amount: return await m.answer("Недостаточно наличных!")
    if random.randint(1, 5) == 1:
        win = amount * 3
        u["balance"] += (win - amount)
        await m.answer(f"Вы выиграли {win}$\n(Ставка: {amount}$ )")
    else:
        u["balance"] -= amount
        await m.answer(f"Вы проиграли ставку {amount}$")
    ECO_CHANGED = True

# --- ЗАПУСК ---
class H(BaseHTTPRequestHandler):
    def do_GET(self): self.send_response(200); self.end_headers(); self.wfile.write(b"OK")

if __name__ == "__main__":
    threading.Thread(target=lambda: HTTPServer(('0.0.0.0', int(os.environ.get("PORT", 10000))), H).serve_forever(), daemon=True).start()
    loop = asyncio.get_event_loop()
    loop.create_task(auto_save_eco())
    bot.run_forever()

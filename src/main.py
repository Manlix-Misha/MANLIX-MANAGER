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

# --- 1. НАСТРОЙКИ (Начало кода не меняется для Render) ---
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
        except: return {}
    return {}

DATABASE = load_local_data(EXTERNAL_DB)
ECONOMY = load_local_data(EXTERNAL_ECO)
PUNISHMENTS = load_local_data(EXTERNAL_PUN)

# Инициализация структур
for d in [DATABASE, ECONOMY, PUNISHMENTS]:
    if not isinstance(d, dict): d = {}
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

# --- 2. СИСТЕМНЫЕ ФУНКЦИИ ---

async def push_to_github(data, gh_path, local_path):
    url = f"https://api.github.com/repos/{GH_REPO}/contents/{gh_path}"
    headers = {"Authorization": f"token {GH_TOKEN}"}
    try:
        async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=False)) as session:
            sha = None
            async with session.get(url, headers=headers) as resp:
                if resp.status == 200: sha = (await resp.json())['sha']
            content = base64.b64encode(json.dumps(data, ensure_ascii=False, indent=4).encode('utf-8')).decode('utf-8')
            payload = {"message": "Update DB", "content": content}
            if sha: payload["sha"] = sha
            await session.put(url, headers=headers, json=payload)
            with open(local_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=4)
    except: pass

async def get_target_id(m: Message, args: str):
    if m.reply_message: return m.reply_message.from_id
    if not args: return None
    match = re.search(r"(?:id|\[id|vk\.com\/id|vk\.com\/)(\d+)", args)
    if match: return int(match.group(1))
    raw = args.split('/')[-1].split('|')[0].replace('[', '').replace('@', '').strip()
    try:
        res = await bot.api.utils.resolve_screen_name(screen_name=raw)
        if res and res.type.value == "user": return res.object_id
    except: pass
    num = re.sub(r"\D", "", args)
    if num: return int(num)
    return None

def get_user_info(peer_id, user_id):
    if int(user_id) == 870757778: return "Специальный Руководитель", "Misha Manlix"
    staff = DATABASE.get("chats", {}).get(str(peer_id), {}).get("staff", {})
    return staff.get(str(user_id), ["Пользователь", None])

async def check_access(m: Message, min_rank: str):
    rank, _ = get_user_info(m.peer_id, m.from_id)
    if RANK_WEIGHT.get(rank, 0) < RANK_WEIGHT.get(min_rank, 0):
        await m.answer("Недостаточно прав!")
        return False
    return True

# --- 3. MIDDLEWARE (ЗАЩИТА И ЛОГИ) ---

class ChatMiddleware(BaseMiddleware[Message]):
    async def pre(self):
        if not self.event.from_id or self.event.from_id < 0: return
        pid, uid = str(self.event.peer_id), str(self.event.from_id)
        
        # Статистика
        if pid in DATABASE["chats"]:
            chat_data = DATABASE["chats"][pid]
            if "stats" not in chat_data: chat_data["stats"] = {}
            if uid not in chat_data["stats"]: chat_data["stats"][uid] = {"count": 0, "last": 0}
            chat_data["stats"][uid]["count"] += 1
            chat_data["stats"][uid]["last"] = datetime.datetime.now(TZ_MSK).timestamp()

        # Проверка банов и мута
        is_gban = uid in PUNISHMENTS["gbans_status"]
        is_gbanpl = uid in PUNISHMENTS["gbans_pl"]
        is_lban = uid in PUNISHMENTS["bans"].get(pid, {})
        mutes = DATABASE["chats"].get(pid, {}).get("mutes", {})
        is_muted = uid in mutes and datetime.datetime.now(TZ_MSK).timestamp() < mutes[uid]

        if is_gban or is_gbanpl or is_lban or is_muted:
            try: await bot.api.messages.delete(peer_id=self.event.peer_id, conversation_message_ids=[self.event.conversation_message_id], delete_for_all=True)
            except: pass
            self.stop()

bot = Bot(token=os.environ.get("TOKEN"))
bot.labeler.message_view.register_middleware(ChatMiddleware)

# --- 4. КОМАНДЫ ПОЛЬЗОВАТЕЛЕЙ ---

@bot.on.message(text=["/help"])
async def help_cmd(m: Message):
    rank, _ = get_user_info(m.peer_id, m.from_id)
    w = RANK_WEIGHT.get(rank, 0)
    
    res = "Команды пользователей:\n/info - официальные ресурсы\n/stats - статистика пользователя\n/getid - оригинальная ссылка VK.\n"
    if w >= 1: res += "\nКоманды для модераторов:\n/staff - Руководство Беседы\n/kick - исключить пользователя из Беседы.\n/mute - выдать Блокировку чата.\n/unmute - снять Блокировку чата.\n/setnick - установить имя пользователю.\n/rnick - удалить имя пользователю.\n/nlist - список пользователей с ником.\n/getban - информация о Блокировках.\n"
    if w >= 2: res += "\nКоманды старших модераторов:\n/addmoder - выдать права модератора.\n/removerole - снять уровень прав.\n/ban - блокировка пользователя в Беседе.\n/unban - снятие блокировки пользователю в беседе.\n"
    if w >= 3: res += "\nКоманды администраторов:\n/addsenmoder - выдать права старшего модератора.\n"
    if w >= 4: res += "\nКоманды старших администраторов:\n/addadmin - выдать права администратора.\n"
    if w >= 5: res += "\nКоманды заместителей спец. администраторов:\n/addsenadmin - выдать права старшего модератора.\n"
    if w >= 6: res += "\nКоманды спец. администраторов:\n/addzsa - выдать права заместителя спец. администратора.\n"
    if w >= 7: res += "\nКоманды владельца:\n/addsa - выдать права специального администратора.\n"
    
    await m.answer(res.strip())
    
    if w >= 8:
        gres = "Команды руководства Бота:\n\nЗам. Спец. Руководителя:\n/gstaff - руководство Бота.\n/addowner - выдать права владельца.\n/gbanpl - Блокировка пользователя во всех игровых Беседах.\n/gunbanpl - снятие Блокировки во всех игровых Беседах.\n\nОсновной Зам. Спец. Руководителя:\nОтсутствуют.\n\nСпец. Руководителя:\n/start - активировать Беседу.\n/sync - синхронизация с базой данных.\n/chatid - узнать айди Беседы.\n/delchat - удалить чат с Базы данных."
        await m.answer(gres)

@bot.on.message(text=["/getid", "/getid <args>"])
async def getid_cmd(m: Message, args=None):
    t = await get_target_id(m, args) or m.from_id
    await m.answer(f"Оригинальная ссылка [id{t}|пользователя]: https://vk.com/id{t}")

@bot.on.message(text="/stats")
@bot.on.message(text="/stats <args>")
async def stats_cmd(m: Message, args=None):
    t = await get_target_id(m, args) or m.from_id
    uid, pid = str(t), str(m.peer_id)
    role, nick = get_user_info(m.peer_id, t)
    
    bans_cnt = sum(1 for c in PUNISHMENTS["bans"] if uid in PUNISHMENTS["bans"][c])
    gban = "Да" if uid in PUNISHMENTS["gbans_status"] else "Нет"
    gbanpl = "Да" if uid in PUNISHMENTS["gbans_pl"] else "Нет"
    
    mutes = DATABASE["chats"].get(pid, {}).get("mutes", {})
    is_muted = "Да" if uid in mutes and datetime.datetime.now(TZ_MSK).timestamp() < mutes[uid] else "Нет"
    
    st = DATABASE["chats"].get(pid, {}).get("stats", {}).get(uid, {"count": 0, "last": 0})
    dt = datetime.datetime.fromtimestamp(st["last"], TZ_MSK).strftime("%d/%m/%Y %H:%M:%S %p") if st["last"] else "Нет данных"
    
    msg = (f"Информация о [id{t}|пользователе]\n"
           f"Роль: {role}\n"
           f"Блокировок: {bans_cnt}\n"
           f"Общая блокировка в чатах: {gban}\n"
           f"Общая блокировка в беседах игроков: {gbanpl}\n"
           f"Активные предупреждения: {PUNISHMENTS['warns'].get(pid, {}).get(uid, 0)}\n"
           f"Блокировка чата: {is_muted}\n"
           f"Ник: {nick if nick else 'Не установлен'}\n"
           f"Всего сообщений: {st['count']}\n"
           f"Последнее сообщение: {dt}")
    await m.answer(msg)

# --- 5. МОДЕРАЦИЯ ---

@bot.on.message(text=["/mute", "/mute <args>"])
async def mute_cmd(m: Message, args=None):
    if not await check_access(m, "Модератор"): return
    t = await get_target_id(m, args)
    if not t: return
    
    mins = 10
    reason = "Нарушение"
    if args:
        find_mins = re.findall(r"\s(\d+)", args)
        if find_mins: mins = int(find_mins[0])
        parts = args.split()
        if len(parts) > 2: reason = " ".join(parts[2:])
    
    until = datetime.datetime.now(TZ_MSK).timestamp() + (mins * 60)
    pid = str(m.peer_id)
    if "mutes" not in DATABASE["chats"][pid]: DATABASE["chats"][pid]["mutes"] = {}
    DATABASE["chats"][pid]["mutes"][str(t)] = until
    
    dt = datetime.datetime.fromtimestamp(until, TZ_MSK).strftime("%d/%m/%Y %H:%M:%S")
    kb = Keyboard(inline=True).add(Text("Снять мут", {"cmd": "unmute", "u": t}), color=KeyboardButtonColor.POSITIVE).add(Text("Очистить", {"cmd": "clear"}), color=KeyboardButtonColor.NEGATIVE)
    
    await m.answer(f"[id{m.from_id}|Модератор MANLIX] выдал(-а) мут [id{t}|пользователю]\nПричина: {reason}\nМут выдан до: {dt}", keyboard=kb.get_json())

@bot.on.raw_event(GroupEventType.MESSAGE_EVENT, dataclass=MessageEvent)
async def buttons(event: MessageEvent):
    pid, uid = str(event.peer_id), event.user_id
    payload = event.payload
    if not payload: return
    
    rank, _ = get_user_info(event.peer_id, uid)
    if RANK_WEIGHT.get(rank, 0) < 1:
        return await event.show_snackbar("Недостаточно прав!")

    if payload["cmd"] == "unmute":
        t = payload["u"]
        if pid in DATABASE["chats"] and str(t) in DATABASE["chats"][pid].get("mutes", {}):
            del DATABASE["chats"][pid]["mutes"][str(t)]
            await bot.api.messages.edit(peer_id=event.peer_id, conversation_message_id=event.conversation_message_id, message=f"[id{uid}|Модератор MANLIX] снял(-а) мут [id{t}|пользователю]")
    
    if payload["cmd"] == "clear":
        await bot.api.messages.delete(peer_id=event.peer_id, conversation_message_ids=[event.conversation_message_id], delete_for_all=True)

@bot.on.message(text=["/getban", "/getban <args>"])
async def getban_cmd(m: Message, args=None):
    if not await check_access(m, "Модератор"): return
    t = await get_target_id(m, args)
    if not t: return
    uid = str(t)
    
    # Получение ника через API для корректности заголовка
    u_info = (await bot.api.users.get(user_ids=[t]))[0]
    ans = f"Информация о блокировках [id{t}|{u_info.first_name} {u_info.last_name}]\n\n"
    
    for key, label in [("gbans_status", "общей Блокировке в Беседах"), ("gbans_pl", "общей Блокировке в Беседе игроков")]:
        ans += f"Информация о {label}: "
        if uid in PUNISHMENTS[key]:
            b = PUNISHMENTS[key][uid]
            dt = datetime.datetime.fromtimestamp(b['date'], TZ_MSK).strftime("%d/%m/%Y %H:%M:%S")
            ans += f"\n[id{b['admin']}|Модератор MANLIX] | {b['reason']} | {dt}\n\n"
        else: ans += "отсутствует\n\n"
        
    local = []
    for pid, users in PUNISHMENTS["bans"].items():
        if uid in users:
            b = users[uid]
            title = DATABASE["chats"].get(pid, {}).get("title", f"Чат {pid}")
            dt = datetime.datetime.fromtimestamp(b['date'], TZ_MSK).strftime("%d/%m/%Y %H:%M:%S")
            local.append(f"{title} | [id{b['admin']}|Модератор MANLIX] | {b.get('reason','-')} | {dt}")
            
    ans += f"Количество Бесед, в которых заблокирован пользователь: {len(local)}\n"
    if local:
        ans += "Информация о последних 10 Блокировках:\n"
        for i, row in enumerate(reversed(local[-10:]), 1): ans += f"{i}) {row}\n"
    else: ans += "Блокировки в беседах отсутствуют"
    await m.answer(ans)

# --- 6. РОЛИ И НИКИ ---

async def role_logic(m: Message, args, role_name, label):
    if not await check_access(m, label): return
    t = await get_target_id(m, args)
    if not t: return
    pid, uid = str(m.peer_id), str(t)
    _, nick = get_user_info(pid, uid)
    if pid not in DATABASE["chats"]: DATABASE["chats"][pid] = {"staff": {}}
    DATABASE["chats"][pid]["staff"][uid] = [role_name, nick]
    await push_to_github(DATABASE, GH_PATH_DB, EXTERNAL_DB)
    _, a_nick = get_user_info(pid, m.from_id)
    a_name = f"[id{m.from_id}|{a_nick}]" if a_nick else f"[id{m.from_id}|Ник]"
    await m.answer(f"{a_name} выдал(-а) права {role_name.lower()}а [id{t}|пользователю]")

@bot.on.message(text=["/addmoder", "/addmoder <args>"])
async def addmod(m: Message, args=None): await role_logic(m, args, "Модератор", "Старший Модератор")

@bot.on.message(text=["/addsenmoder", "/addsenmoder <args>"])
async def addsenmod(m: Message, args=None): await role_logic(m, args, "Старший Модератор", "Администратор")

@bot.on.message(text=["/addadmin", "/addadmin <args>"])
async def addadm(m: Message, args=None): await role_logic(m, args, "Администратор", "Старший Администратор")

@bot.on.message(text=["/addsenadmin", "/addsenadmin <args>"])
async def addsenadm(m: Message, args=None): await role_logic(m, args, "Старший Администратор", "Зам. Спец. Администратора")

@bot.on.message(text=["/addzsa", "/addzsa <args>"])
async def addzsa(m: Message, args=None): await role_logic(m, args, "Зам. Спец. Администратора", "Спец. Администратор")

@bot.on.message(text=["/addsa", "/addsa <args>"])
async def addsa(m: Message, args=None): await role_logic(m, args, "Спец. Администратор", "Владелец")

@bot.on.message(text=["/addowner", "/addowner <args>"])
async def addowner(m: Message, args=None): await role_logic(m, args, "Владелец", "Зам. Спец. Руководителя")

@bot.on.message(text="/staff")
async def staff_view(m: Message):
    pid = str(m.peer_id)
    staff = DATABASE["chats"].get(pid, {}).get("staff", {})
    order = ["Владелец", "Спец. Администратор", "Зам. Спец. Администратора", "Старший Администратор", "Администратор", "Старший Модератор", "Модератор"]
    res = ""
    for r in order:
        res += f"{r}: \n"
        members = [f"[id{u}|{n if n else 'Админ'}]" for u, (role, n) in staff.items() if role == r]
        res += "\n".join([f"– {m}" for m in members]) if members else "– Отсутствует."
        res += "\n\n"
    await m.answer(res.strip())

@bot.on.message(text=["/setnick", "/setnick <args>"])
async def setnick(m: Message, args=None):
    if not await check_access(m, "Модератор"): return
    t = await get_target_id(m, args)
    if not t or not args: return
    new_nick = args.split()[-1]
    pid, uid = str(m.peer_id), str(t)
    role, _ = get_user_info(pid, uid)
    DATABASE["chats"][pid]["staff"][uid] = [role, new_nick]
    await push_to_github(DATABASE, GH_PATH_DB, EXTERNAL_DB)
    _, a_nick = get_user_info(pid, m.from_id)
    a_name = f"[id{m.from_id}|{a_nick}]" if a_nick else f"[id{m.from_id}|Ник]"
    await m.answer(f"{a_name} установил(-а) новое имя [id{t}|пользователю]")

# --- 7. ИГРОВАЯ СИСТЕМА (С ИСКЛЮЧЕНИЕМ ДЛЯ СМАЙЛИКОВ) ---

@bot.on.message(text="/ghelp")
async def ghelp(m: Message):
    await m.answer("🎮 Игровые команды MANLIX:\n\n🎉 /prise — Получить ежечасный приз\n💰 /balance — Наличные средства\n🏦 /bank — Состояние счетов\n📥 /положить [сумма] — Положить в банк\n📤 /снять [сумма] — Снять из банка\n💸 /перевести [ссылка] [сумма] — Перевод со счета на счет\n🎰 /roulette [сумма] — Рулетка")

@bot.on.message(text="/prise")
async def prize(m: Message):
    uid = str(m.from_id)
    if uid not in ECONOMY: ECONOMY[uid] = {"cash": 0, "bank": 0, "last": 0}
    if datetime.datetime.now().timestamp() - ECONOMY[uid]["last"] < 3600:
        return await m.answer("Приз доступен раз в час.")
    win = random.randint(100, 1000)
    ECONOMY[uid]["cash"] += win
    ECONOMY[uid]["last"] = datetime.datetime.now().timestamp()
    await push_to_github(ECONOMY, GH_PATH_ECO, EXTERNAL_ECO)
    await m.answer(f"🎉 Вы получили приз {win}$")

@bot.on.message(text="/bank")
async def bank_view(m: Message):
    e = ECONOMY.get(str(m.from_id), {"cash": 0, "bank": 0})
    await m.answer(f"🏦 …::: MANLIX BANK :::…\n\n💵 Наличные: {e['cash']}$\n💳 На счету: {e['bank']}$")

# --- 8. СИСТЕМНЫЕ СОБЫТИЯ ---

@bot.on.message()
async def actions(m: Message):
    if m.action and m.action.type.value == "chat_kick_user":
        if m.action.member_id == m.from_id:
            kb = Keyboard(inline=True).add(Text("Исключить", {"cmd": "clear"}), color=KeyboardButtonColor.NEGATIVE)
            await m.answer("Бот покинул(-а) Беседу", keyboard=kb.get_json())

@bot.on.message(text="/start")
async def start(m: Message):
    if not await check_access(m, "Специальный Руководитель"): return
    pid = str(m.peer_id)
    DATABASE["chats"][pid] = {"title": "Чат", "staff": {"870757778": ["Специальный Руководитель", "Misha Manlix"]}, "mutes": {}, "stats": {}}
    await push_to_github(DATABASE, GH_PATH_DB, EXTERNAL_DB)
    await m.answer("Вы успешно активировали Беседу.")

# --- 9. ЗАПУСК ---
class H(BaseHTTPRequestHandler):
    def do_GET(self): self.send_response(200); self.end_headers(); self.wfile.write(b"OK")

if __name__ == "__main__":
    threading.Thread(target=lambda: HTTPServer(('0.0.0.0', int(os.environ.get("PORT", 10000))), H).serve_forever(), daemon=True).start()
    bot.run_forever()

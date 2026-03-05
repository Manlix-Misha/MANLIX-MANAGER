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
EXTERNAL_DB = "database.json"
EXTERNAL_ECO = "economy.json"

# Флаг для отложенного сохранения экономики
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
                    res_data = await resp.json()
                    sha = res_data['sha']
            
            content_str = json.dumps(data, ensure_ascii=False, indent=4)
            content_base64 = base64.b64encode(content_str.encode('utf-8')).decode('utf-8')
            payload = {"message": message_text, "content": content_base64}
            if sha: payload["sha"] = sha
            
            async with session.put(url, headers=headers, json=payload) as put_resp:
                if put_resp.status in [200, 201]:
                    with open(local_path, "w", encoding="utf-8") as f:
                        json.dump(data, f, ensure_ascii=False, indent=4)
                    return True
                return False
    except: return False

# Фоновая задача для сохранения экономики раз в 5 минут
async def auto_save_eco():
    global ECO_CHANGED
    while True:
        await asyncio.sleep(300)
        if ECO_CHANGED:
            if await push_to_github(ECONOMY, GH_PATH_ECO, EXTERNAL_ECO, "Auto-save Economy"):
                ECO_CHANGED = False

# --- 3. СИСТЕМНАЯ ЛОГИКА И MIDDLEWARE ---

bot = Bot(token=os.environ.get("TOKEN"))

def get_user_data(peer_id, user_id):
    if int(user_id) == 870757778: return ["Специальный Руководитель", "Misha Manlix"]
    chat_data = DATABASE.get("chats", {}).get(str(peer_id), {})
    staff = chat_data.get("staff", {})
    return staff.get(str(user_id), ["Пользователь", None])

def get_eco_data(user_id):
    uid = str(user_id)
    if uid not in ECONOMY:
        ECONOMY[uid] = {"balance": 0, "last_prise": 0}
    return ECONOMY[uid]

async def get_nick(peer_id, user_id):
    if int(user_id) == 870757778: return "Misha Manlix"
    _, nick = get_user_data(peer_id, user_id)
    if nick: return nick
    try:
        u = (await bot.api.users.get(user_ids=[user_id]))[0]
        return f"{u.first_name} {u.last_name}"
    except: return "Пользователь"

def has_access(peer_id, user_id, required_rank):
    u_rank = get_user_data(peer_id, user_id)[0]
    return RANK_WEIGHT.get(u_rank, 0) >= RANK_WEIGHT.get(required_rank, 0)

async def check_active(message: Message):
    if int(message.from_id) == 870757778: return True
    if str(message.peer_id) not in DATABASE.get("chats", {}):
        return False
    return True

def extract_id(text):
    if not text: return None
    match = re.search(r'id(\d+)', str(text))
    if match: return int(match.group(1))
    digits = re.findall(r'\d+', str(text))
    return int(digits[0]) if digits else None

class MuteMiddleware(BaseMiddleware[Message]):
    async def pre(self):
        if not self.event.from_id: return
        pid = str(self.event.peer_id)
        uid = str(self.event.from_id)
        if pid in DATABASE.get("chats", {}) and "mutes" in DATABASE["chats"][pid]:
            if uid in DATABASE["chats"][pid]["mutes"]:
                if datetime.datetime.now(datetime.timezone.utc).timestamp() < DATABASE["chats"][pid]["mutes"][uid]:
                    try: await bot.api.messages.delete(message_ids=[self.event.conversation_message_id], peer_id=self.event.peer_id, delete_for_all=True)
                    except: pass
                    self.event.text = "" 

bot.labeler.message_view.register_middleware(MuteMiddleware)

# --- 4. ИГРОВЫЕ КОМАНДЫ ---

@bot.on.message(text="/ghelp")
async def ghelp_cmd(m: Message):
    if not await check_active(m): return
    msg = ("🎮 Игровые команды MANLIX:\n\n"
           "/prise — получить ежечасный приз ($100-$1000)\n"
           "/balance — проверить свой баланс\n"
           "/bank — информация о личном счете")
    await m.answer(msg)

@bot.on.message(text="/prise")
async def prise_cmd(m: Message):
    if not await check_active(m): return
    global ECO_CHANGED
    user_id = m.from_id
    data = get_eco_data(user_id)
    
    now = datetime.datetime.now().timestamp()
    if now - data["last_prise"] < 3600:
        remain = int((3600 - (now - data["last_prise"])) / 60)
        return await m.answer(f"❌ Приз доступен раз в час! Подождите {remain} мин.")
    
    win = random.randint(100, 1000)
    data["balance"] += win
    data["last_prise"] = now
    ECO_CHANGED = True
    await m.answer(f"🎁 Вы получили приз: ${win}!\n💰 Текущий баланс: ${data['balance']}")

@bot.on.message(text="/balance")
async def balance_cmd(m: Message):
    if not await check_active(m): return
    data = get_eco_data(m.from_id)
    await m.answer(f"💰 Ваш баланс: ${data['balance']}")

@bot.on.message(text="/bank")
async def bank_cmd(m: Message):
    if not await check_active(m): return
    data = get_eco_data(m.from_id)
    await m.answer(f"🏦 Личный счет в MANLIX BANK:\n💵 Баланс: ${data['balance']}")

# --- 5. СОБЫТИЯ БЕСЕДЫ ---

@bot.on.raw_event(GroupEventType.MESSAGE_NEW, dataclass=Message)
async def user_leave_handler(event: Message):
    if event.action and event.action.type.value in ["chat_kick_user", "chat_exit_user"]:
        keyboard = (Keyboard(inline=True)
            .add(Text("Исключить", {"cmd": "kick_confirm", "user": event.action.member_id}), color=KeyboardButtonColor.NEGATIVE)
        ).get_json()
        await event.answer("Бот покинул(-а) Беседу", keyboard=keyboard)

@bot.on.message(payload_contains={"cmd": "kick_confirm"})
async def kick_confirm(m: Message):
    if has_access(m.peer_id, m.from_id, "Модератор"):
        try: await bot.api.messages.remove_chat_user(chat_id=m.peer_id-2000000000, user_id=m.get_payload_json()["user"])
        except: pass

# --- 6. КОМАНДЫ ПОЛЬЗОВАТЕЛЕЙ / ИНФО ---

@bot.on.message(text="/help")
async def help_handler(message: Message):
    if not await check_active(message): return
    rank = get_user_data(message.peer_id, message.from_id)[0]
    weight = RANK_WEIGHT.get(rank, 0)
    
    msg = "Команды пользователей:\n/info - официальные ресурсы \n/stats - статистика пользователя \n/getid - оригинальная ссылка VK.\n"
    
    if weight >= 1: msg += "\nКоманды для модераторов:\n/staff - Руководство Беседы \n/kick - исключить пользователя из Беседы. \n/mute - выдать Блокировку чата. \n/unmute - снять Блокировку чата.\n/setnick - установить имя пользователю.\n/rnick - удалить имя пользователю.\n/nlist - список пользователей с ником.\n"
    if weight >= 2: msg += "\nКоманды старших модераторов: \n/addmoder - выдать права модератора. \n/removerole - снять уровень прав.\n"
    if weight >= 3: msg += "\nКоманды администраторов:\n/addsenmoder - выдать права старшего модератора. \n"
    if weight >= 4: msg += "\nКоманды старших администраторов: \n/addadmin - выдать права администратора.\n"
    if weight >= 5: msg += "\nКоманды заместителей спец. администраторов: \n/addsenadmin - выдать права старшего модератора.\n"
    if weight >= 6: msg += "\nКоманды спец. администраторов:\n/addzsa - выдать права заместителя спец. администратора. \n"
    if weight >= 7: msg += "\nКоманды владельца:\n/addsa - выдать права специального администратора. \n"

    await message.answer(msg.strip())
    
    if weight >= 8:
        bot_help = "Команды руководства Бота:\n\nЗам. Спец. Руководителя:\n/gstaff - руководство Бота.\n/addowner - выдать права владельца.\n/gbanpl - Блокировка пользователя во всех игровых Беседах.\n/gunbanpl - снятие Блокировки во всех игровых Беседах. \n\nОсновной Зам. Спец. Руководителя:\nОтсутствуют."
        if weight >= 10: bot_help += "\n\nСпец. Руководителя: \n/start - активировать Беседу.\n/sync - синхронизация с базой данных.\n/chatid - узнать айди Беседы.\n/delchat - удалить чат с Базы данных."
        await message.answer(bot_help)

@bot.on.message(text="/info")
async def info_cmd(m: Message):
    if await check_active(m): await m.answer("Официальные ресурсы MANLIX: [Укажите ссылки]")

@bot.on.message(text=["/getid", "/getid <args>"])
async def getid_cmd(m: Message, args=None):
    if not await check_active(m): return
    target = m.reply_message.from_id if m.reply_message else (extract_id(args) or m.from_id)
    await m.answer(f"Оригинальная ссылка [id{target}|пользователя]: https://vk.com/id{target}")

@bot.on.message(text=["/stats", "/stats <args>"])
async def stats_cmd(m: Message, args=None):
    if not await check_active(m): return
    target = m.reply_message.from_id if m.reply_message else (extract_id(args) or m.from_id)
    rank = get_user_data(m.peer_id, target)[0]
    nick = await get_nick(m.peer_id, target)
    balance = get_eco_data(target)["balance"]
    await m.answer(f"Статистика [id{target}|пользователя]:\nНик: {nick}\nУровень прав: {rank}\nБаланс: ${balance}")

@bot.on.message(text="/gstaff")
async def gstaff_cmd(m: Message):
    if not await check_active(m): return
    await m.answer("MANLIX MANAGER | Команда Бота:\n\n| Специальный Руководитель:\n– [id870757778|Misha Manlix]\n\n| Основной зам. Спец. Руководителя:\n– Отсутствует.\n\n| Зам. Спец. Руководителя:\n– Отсутствует.")

@bot.on.message(text="/staff")
async def staff_list(m: Message):
    if not await check_active(m): return
    staff_data = DATABASE.get("chats", {}).get(str(m.peer_id), {}).get("staff", {})
    ranks_order = ["Владелец", "Спец. Администратор", "Зам. Спец. Администратора", "Старший Администратор", "Администратор", "Старший Модератор", "Модератор"]
    res = []
    for r in ranks_order:
        res.append(f"{r}:")
        found = False
        for uid, data in staff_data.items():
            if data[0] == r:
                res.append(f"– [id{uid}|{data[1] if data[1] else 'Пользователь'}]")
                found = True
        if not found: res.append("– Отсутствует.")
        res.append("")
    await m.answer("\n".join(res).strip())

@bot.on.message(text="/nlist")
async def nlist_cmd(m: Message):
    if not await check_active(m) or not has_access(m.peer_id, m.from_id, "Модератор"): return
    staff_data = DATABASE.get("chats", {}).get(str(m.peer_id), {}).get("staff", {})
    res = ["Список пользователей с ником:"]
    idx = 1
    for uid, data in staff_data.items():
        if data[1]:
            res.append(f"{idx}. [id{uid}|{data[1]}]")
            idx += 1
    if idx == 1: res.append("Пусто.")
    await m.answer("\n".join(res))

# --- 7. НАКАЗАНИЯ ---

@bot.on.message(text=["/mute", "/mute <args>"])
async def mute_cmd(m: Message, args=None):
    if not await check_active(m) or not has_access(m.peer_id, m.from_id, "Модератор"): return
    target = m.reply_message.from_id if m.reply_message else extract_id(args)
    if not target: return
    parts = args.split() if args else []
    try:
        minutes = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else (int(parts[0]) if parts and parts[0].isdigit() else 10)
        reason = " ".join(parts[2:]) if len(parts) > 2 else (" ".join(parts[1:]) if parts and not parts[0].isdigit() else "Не указана")
    except: minutes, reason = 10, "Не указана"
    moscow_tz = datetime.timezone(datetime.timedelta(hours=3))
    end_time = datetime.datetime.now(moscow_tz) + datetime.timedelta(minutes=minutes)
    pid = str(m.peer_id)
    if "chats" not in DATABASE: DATABASE["chats"] = {}
    if pid not in DATABASE["chats"]: DATABASE["chats"][pid] = {}
    if "mutes" not in DATABASE["chats"][pid]: DATABASE["chats"][pid]["mutes"] = {}
    DATABASE["chats"][pid]["mutes"][str(target)] = end_time.timestamp()
    await push_to_github(DATABASE, GH_PATH_DB, EXTERNAL_DB, f"Mute {target}")
    keyboard = (Keyboard(inline=True).add(Text("Снять мут", {"cmd": "unmute_edit", "user": target}), color=KeyboardButtonColor.POSITIVE).add(Text("Очистить", {"cmd": "clear"}), color=KeyboardButtonColor.NEGATIVE)).get_json()
    await m.answer(f"[id{m.from_id}|Модератор] выдал мут [id{target}|пользователю] до {end_time.strftime('%H:%M:%S')}", keyboard=keyboard)

@bot.on.message(payload_contains={"cmd": "unmute_edit"})
async def unmute_edit(m: Message):
    if has_access(m.peer_id, m.from_id, "Модератор"):
        target = m.get_payload_json()["user"]
        pid = str(m.peer_id)
        if pid in DATABASE["chats"] and str(target) in DATABASE["chats"][pid].get("mutes", {}):
            del DATABASE["chats"][pid]["mutes"][str(target)]
            await push_to_github(DATABASE, GH_PATH_DB, EXTERNAL_DB, f"Unmute {target}")
        await bot.api.messages.edit(peer_id=m.peer_id, conversation_message_id=m.conversation_message_id, message=f"Мут с [id{target}|пользователя] снят.")

@bot.on.message(payload_contains={"cmd": "clear"})
async def clear_edit(m: Message):
    if has_access(m.peer_id, m.from_id, "Модератор"):
        try: await bot.api.messages.delete(message_ids=[m.conversation_message_id], peer_id=m.peer_id, delete_for_all=True)
        except: pass

@bot.on.message(text=["/unmute", "/unmute <args>"])
async def unmute_cmd(m: Message, args=None):
    if not await check_active(m) or not has_access(m.peer_id, m.from_id, "Модератор"): return
    target = m.reply_message.from_id if m.reply_message else extract_id(args)
    if not target: return
    pid = str(m.peer_id)
    if pid in DATABASE["chats"] and str(target) in DATABASE["chats"][pid].get("mutes", {}):
        del DATABASE["chats"][pid]["mutes"][str(target)]
        await push_to_github(DATABASE, GH_PATH_DB, EXTERNAL_DB, f"Unmute {target}")
    await m.answer(f"Мут снят с [id{target}|пользователя].")

@bot.on.message(text=["/kick", "/kick <args>"])
async def kick_cmd(m: Message, args=None):
    if not await check_active(m) or not has_access(m.peer_id, m.from_id, "Модератор"): return
    target = m.reply_message.from_id if m.reply_message else extract_id(args)
    if not target: return
    try:
        await bot.api.messages.remove_chat_user(chat_id=m.peer_id-2000000000, user_id=target)
        await m.answer(f"Пользователь [id{target}|исключен].")
    except: pass

# --- 8. ВЫДАЧА НИКОВ И ПРАВ ---

@bot.on.message(text=["/setnick", "/setnick <args>"])
async def setnick_cmd(m: Message, args=None):
    if not await check_active(m) or not has_access(m.peer_id, m.from_id, "Модератор"): return
    target = m.reply_message.from_id if m.reply_message else extract_id(args)
    nick = args.split()[-1] if args and len(args.split()) > 1 else args
    if not target or not nick or "id" in nick: return
    pid = str(m.peer_id)
    u_rank = get_user_data(m.peer_id, target)[0]
    if "chats" not in DATABASE: DATABASE["chats"] = {}
    if pid not in DATABASE["chats"]: DATABASE["chats"][pid] = {"staff": {}}
    DATABASE["chats"][pid]["staff"][str(target)] = [u_rank, nick]
    await push_to_github(DATABASE, GH_PATH_DB, EXTERNAL_DB, f"Nick {nick}")
    await m.answer(f"Ник установлен для [id{target}|пользователя].")

@bot.on.message(text=["/rnick", "/rnick <args>"])
async def rnick_cmd(m: Message, args=None):
    if not await check_active(m) or not has_access(m.peer_id, m.from_id, "Модератор"): return
    target = m.reply_message.from_id if m.reply_message else extract_id(args)
    if not target: return
    pid = str(m.peer_id)
    u_rank = get_user_data(m.peer_id, target)[0]
    if pid in DATABASE["chats"] and str(target) in DATABASE["chats"][pid].get("staff", {}):
        if u_rank == "Пользователь": del DATABASE["chats"][pid]["staff"][str(target)]
        else: DATABASE["chats"][pid]["staff"][str(target)] = [u_rank, None]
    await push_to_github(DATABASE, GH_PATH_DB, EXTERNAL_DB, "Remove nick")
    await m.answer(f"Ник удален.")

async def grant_role(m: Message, args, req_rank, role_name, action_text):
    if not await check_active(m) or not has_access(m.peer_id, m.from_id, req_rank): return
    target = m.reply_message.from_id if m.reply_message else extract_id(args)
    if not target: return
    pid = str(m.peer_id)
    _, target_nick = get_user_data(m.peer_id, target)
    if "chats" not in DATABASE: DATABASE["chats"] = {}
    if pid not in DATABASE["chats"]: DATABASE["chats"][pid] = {"staff": {}}
    if role_name == "Пользователь" and str(target) in DATABASE["chats"][pid]["staff"]:
        del DATABASE["chats"][pid]["staff"][str(target)]
    else: DATABASE["chats"][pid]["staff"][str(target)] = [role_name, target_nick]
    await push_to_github(DATABASE, GH_PATH_DB, EXTERNAL_DB, f"Role {role_name}")
    await m.answer(f"Права {role_name} выданы [id{target}|пользователю].")

@bot.on.message(text=["/addmoder", "/addmoder <args>"])
async def addmoder_cmd(m: Message, args=None): await grant_role(m, args, "Старший Модератор", "Модератор", "выдал права модератора")

@bot.on.message(text=["/addsenmoder", "/addsenmoder <args>"])
async def addsenmoder_cmd(m: Message, args=None): await grant_role(m, args, "Администратор", "Старший Модератор", "выдал права ст. модератора")

@bot.on.message(text=["/addadmin", "/addadmin <args>"])
async def addadmin_cmd(m: Message, args=None): await grant_role(m, args, "Старший Администратор", "Администратор", "выдал права админа")

@bot.on.message(text=["/addsenadmin", "/addsenadmin <args>"])
async def addsenadmin_cmd(m: Message, args=None): await grant_role(m, args, "Зам. Спец. Администратора", "Старший Администратор", "выдал права ст. админа")

@bot.on.message(text=["/addzsa", "/addzsa <args>"])
async def addzsa_cmd(m: Message, args=None): await grant_role(m, args, "Спец. Администратор", "Зам. Спец. Администратора", "выдал права ЗСА")

@bot.on.message(text=["/addsa", "/addsa <args>"])
async def addsa_cmd(m: Message, args=None): await grant_role(m, args, "Владелец", "Спец. Администратор", "выдал права СА")

@bot.on.message(text=["/addowner", "/addowner <args>"])
async def addowner_cmd(m: Message, args=None): await grant_role(m, args, "Зам. Специального Руководителя", "Владелец", "выдал права Владельца")

@bot.on.message(text=["/removerole", "/removerole <args>"])
async def removerole_cmd(m: Message, args=None): await grant_role(m, args, "Старший Модератор", "Пользователь", "снял права")

# --- 9. СИСТЕМНЫЕ КОМАНДЫ ---

@bot.on.message(text="/start")
async def start_handler(m: Message):
    if int(m.from_id) != 870757778: return
    pid = str(m.peer_id)
    if "chats" not in DATABASE: DATABASE["chats"] = {}
    DATABASE["chats"][pid] = {"staff": {"870757778": ["Специальный Руководитель", "Misha Manlix"]}}
    await push_to_github(DATABASE, GH_PATH_DB, EXTERNAL_DB, f"Activate {pid}")
    await m.answer("Беседа активирована.")

@bot.on.message(text="/sync")
async def sync_cmd(m: Message):
    if int(m.from_id) != 870757778: return
    # Синхронизация системной базы
    url = f"https://api.github.com/repos/{GH_REPO}/contents/{GH_PATH_DB}"
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers={"Authorization": f"token {GH_TOKEN}"}) as resp:
            if resp.status == 200:
                data = await resp.json()
                global DATABASE
                DATABASE = json.loads(base64.b64decode(data['content']).decode('utf-8'))
                with open(EXTERNAL_DB, "w", encoding="utf-8") as f:
                    json.dump(DATABASE, f, ensure_ascii=False, indent=4)
                await m.answer("Синхронизация завершена.")

@bot.on.message(text="/chatid")
async def chatid_cmd(m: Message):
    if int(m.from_id) != 870757778: return
    await m.answer(f"ID: {m.peer_id}")

@bot.on.message(text="/delchat")
async def delchat_cmd(m: Message):
    if int(m.from_id) != 870757778: return
    pid = str(m.peer_id)
    if pid in DATABASE.get("chats", {}):
        del DATABASE["chats"][pid]
        await push_to_github(DATABASE, GH_PATH_DB, EXTERNAL_DB, "Delete chat")
        await m.answer("Чат удален из базы.")

# --- СЕРВЕР И ЗАПУСК ---
class H(BaseHTTPRequestHandler):
    def do_GET(self): self.send_response(200); self.end_headers(); self.wfile.write(b"OK")

if __name__ == "__main__":
    threading.Thread(target=lambda: HTTPServer(('0.0.0.0', int(os.environ.get("PORT", 10000))), H).serve_forever(), daemon=True).start()
    
    loop = asyncio.get_event_loop()
    loop.create_task(auto_save_eco()) # Запуск фонового сохранения денег
    bot.run_forever()

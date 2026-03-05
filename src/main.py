import os
import threading
import re
import json
import base64
import aiohttp
import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from vkbottle.bot import Bot, Message
from vkbottle import Keyboard, KeyboardButtonColor, Text, GroupEventType

# --- 1. НАСТРОЙКИ ---
GH_TOKEN = os.environ.get("GH_TOKEN")
GH_REPO = os.environ.get("GH_REPO") 
GH_PATH = "database.json"
EXTERNAL_DB = "database.json"

def load_local_data():
    if os.path.exists(EXTERNAL_DB):
        try:
            with open(EXTERNAL_DB, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data if "chats" in data else {"chats": {}}
        except: return {"chats": {}}
    return {"chats": {}}

DATABASE = load_local_data()

RANK_WEIGHT = {
    "Пользователь": 0, "Модератор": 1, "Старший Модератор": 2, 
    "Администратор": 3, "Старший Администратор": 4, "Зам. Спец. Администратора": 5,
    "Спец. Администратор": 6, "Владелец": 7, "Зам. Специального Руководителя": 8,
    "Основной зам. Специального Руководителя": 9, "Специальный Руководитель": 10
}

# --- 2. GITHUB API ---

async def push_to_github(updated_db, message_text="Update"):
    url = f"https://api.github.com/repos/{GH_REPO}/contents/{GH_PATH}"
    headers = {"Authorization": f"token {GH_TOKEN}", "Accept": "application/vnd.github.v3+json"}
    try:
        async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=False)) as session:
            sha = None
            async with session.get(url, headers=headers) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    sha = data['sha']
            content_str = json.dumps(updated_db, ensure_ascii=False, indent=4)
            content_base64 = base64.b64encode(content_str.encode('utf-8')).decode('utf-8')
            payload = {"message": message_text, "content": content_base64}
            if sha: payload["sha"] = sha
            async with session.put(url, headers=headers, json=payload) as put_resp:
                if put_resp.status in [200, 201]:
                    with open(EXTERNAL_DB, "w", encoding="utf-8") as f:
                        json.dump(updated_db, f, ensure_ascii=False, indent=4)
                    return True
                return False
    except: return False

# --- 3. СИСТЕМНАЯ ЛОГИКА ---

bot = Bot(token=os.environ.get("TOKEN"))

def get_user_data(peer_id, user_id):
    if int(user_id) == 870757778: return ["Специальный Руководитель", "Misha Manlix"]
    chat_data = DATABASE.get("chats", {}).get(str(peer_id), {})
    staff = chat_data.get("staff", {})
    return staff.get(str(user_id), ["Пользователь", None])

async def get_mention(peer_id, user_id):
    if int(user_id) == 870757778: return "[id870757778|Misha Manlix]"
    rank, nick = get_user_data(peer_id, user_id)
    if nick: return f"[id{user_id}|{nick}]"
    try:
        u = (await bot.api.users.get(user_ids=[user_id]))[0]
        return f"[id{user_id}|{u.first_name} {u.last_name}]"
    except: return f"[id{user_id}|пользователь]"

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

# --- 4. СОБЫТИЯ БЕСЕДЫ ---

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

# --- 5. КОМАНДЫ ПОЛЬЗОВАТЕЛЕЙ / ИНФО ---

@bot.on.message(text="/help")
async def help_handler(message: Message):
    if not await check_active(message): return
    rank = get_user_data(message.peer_id, message.from_id)[0]
    weight = RANK_WEIGHT.get(rank, 0)
    
    msg = "Команды пользователей:\n/info - официальные ресурсы \n/stats - статистика пользователя \n/getid - оригинальная ссылка VK.\n"
    
    if weight >= 1:
        msg += "\nКоманды для модераторов:\n/staff - Руководство Беседы \n/kick - исключить пользователя из Беседы. \n/mute - выдать Блокировку чата. \n/unmute - снять Блокировку чата.\n/setnick - установить имя пользователю.\n/rnick - удалить имя пользователю.\n/nlist - список пользователей с ником.\n"
    
    if weight >= 2: msg += "\nКоманды старших модераторов: \n/addmoder - выдать права модератора. \n/removerole - снять уровень прав.\n"
    elif weight >= 1: msg += "\nКоманды старших модераторов: \nОтсутствуют.\n"
    
    if weight >= 3: msg += "\nКоманды администраторов:\n/addsenmoder - выдать права старшего модератора. \n"
    elif weight >= 1: msg += "\nКоманды администраторов:\nОтсутствуют.\n"
    
    if weight >= 4: msg += "\nКоманды старших администраторов: \n/addadmin - выдать права администратора.\n"
    elif weight >= 1: msg += "\nКоманды старших администраторов: \nОтсутствуют.\n"
    
    if weight >= 5: msg += "\nКоманды заместителей спец. администраторов: \n/addsenadmin - выдать права старшего администратора.\n"
    elif weight >= 1: msg += "\nКоманды заместителей спец. администраторов: \nОтсутствуют.\n"
    
    if weight >= 6: msg += "\nКоманды спец. администраторов:\n/addzsa - выдать права заместителя спец. администратора. \n"
    elif weight >= 1: msg += "\nКоманды спец. администраторов:\nОтсутствуют. \n"
    
    if weight >= 7: msg += "\nКоманды владельца:\n/addsa - выдать права специального администратора. \n"
    elif weight >= 1: msg += "\nКоманды владельца:\nОтсутствуют. \n"

    await message.answer(msg.strip())
    
    if weight >= 8:
        bot_help = "Команды руководства Бота:\n\nЗам. Спец. Руководителя:\n/gstaff - руководство Бота.\n/addowner - выдать права владельца.\n/gbanpl - Блокировка пользователя во всех игровых Беседах.\n/gunbanpl - снятие Блокировки во всех игровых Беседах. \n\nОсновной Зам. Спец. Руководителя:\nОтсутствуют."
        if weight >= 10: bot_help += "\n\nСпец. Руководителя: \n/start - активировать Беседу.\n/sync - синхронизация с базой данных.\n/chatid - узнать айди Беседы.\n/delchat - удалить чат с Базы данных."
        await message.answer(bot_help)

@bot.on.message(text=["/getid", "/getid <args>"])
async def getid_cmd(m: Message, args=None):
    if not await check_active(m): return
    target = m.reply_message.from_id if m.reply_message else (extract_id(args) or m.from_id)
    target_mention = await get_mention(m.peer_id, target)
    await m.answer(f"Оригинальная ссылка {target_mention}: https://vk.com/id{target}")

@bot.on.message(text="/gstaff")
async def gstaff_cmd(m: Message):
    if not await check_active(m): return
    await m.answer("MANLIX MANAGER | Команда Бота:\n\n| Специальный Руководитель:\n– [id870757778|Misha Manlix]\n\n| Основной зам. Спец. Руководителя:\n– Отсутствует.\n\n| Зам. Спец. Руководителя:\n– Отсутствует.\n– Отсутствует.")

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
                res.append(f"– [id{uid}|{data[1]}]")
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

# --- 6. МОДЕРАЦИЯ И АДМИНИСТРИРОВАНИЕ ---

@bot.on.message(text=["/mute", "/mute <args>"])
async def mute_cmd(m: Message, args=None):
    if not await check_active(m) or not has_access(m.peer_id, m.from_id, "Модератор"): return
    target = m.reply_message.from_id if m.reply_message else extract_id(args)
    if not target: return
    
    parts = args.split() if args else []
    try:
        minutes = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else (int(parts[0]) if parts and parts[0].isdigit() else 10)
        reason = " ".join(parts[2:]) if len(parts) > 2 else (" ".join(parts[1:]) if parts and not parts[0].isdigit() else "Не указана")
    except:
        minutes, reason = 10, "Не указана"

    time_str = (datetime.datetime.now() + datetime.timedelta(minutes=minutes)).strftime("%d/%m/%Y %H:%M:%S")
    mod_mention = await get_mention(m.peer_id, m.from_id)
    target_mention = await get_mention(m.peer_id, target)
    
    keyboard = (Keyboard(inline=True)
        .add(Text("Снять мут", {"cmd": "unmute_edit", "user": target}), color=KeyboardButtonColor.POSITIVE)
        .add(Text("Очистить", {"cmd": "clear"}), color=KeyboardButtonColor.NEGATIVE)
    ).get_json()
    
    await m.answer(f"{mod_mention} выдал(-а) мут {target_mention}\nПричина: {reason}\nМут выдан до: {time_str}", keyboard=keyboard)

@bot.on.message(payload_contains={"cmd": "unmute_edit"})
async def unmute_edit(m: Message):
    if has_access(m.peer_id, m.from_id, "Модератор"):
        target = m.get_payload_json()["user"]
        mod_mention = await get_mention(m.peer_id, m.from_id)
        target_mention = await get_mention(m.peer_id, target)
        await bot.api.messages.edit(peer_id=m.peer_id, conversation_message_id=m.conversation_message_id, message=f"{mod_mention} снял(-а) мут {target_mention}")

@bot.on.message(payload_contains={"cmd": "clear"})
async def clear_edit(m: Message):
    if has_access(m.peer_id, m.from_id, "Модератор"):
        try: await bot.api.messages.delete(message_ids=[m.conversation_message_id], peer_id=m.peer_id, delete_for_all=True)
        except: pass

@bot.on.message(text=["/kick", "/kick <args>"])
async def kick_cmd(m: Message, args=None):
    if not await check_active(m) or not has_access(m.peer_id, m.from_id, "Модератор"): return
    target = m.reply_message.from_id if m.reply_message else extract_id(args)
    if not target: return
    try:
        await bot.api.messages.remove_chat_user(chat_id=m.peer_id-2000000000, user_id=target)
        mod_mention = await get_mention(m.peer_id, m.from_id)
        target_mention = await get_mention(m.peer_id, target)
        await m.answer(f"{mod_mention} исключил(-а) {target_mention} из Беседы.")
    except: pass

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
    
    await push_to_github(DATABASE, f"Nick {nick}")
    mod_mention = await get_mention(m.peer_id, m.from_id)
    target_mention = await get_mention(m.peer_id, target)
    await m.answer(f"{mod_mention} установил(-а) новое имя {target_mention}")

@bot.on.message(text=["/rnick", "/rnick <args>"])
async def rnick_cmd(m: Message, args=None):
    if not await check_active(m) or not has_access(m.peer_id, m.from_id, "Модератор"): return
    target = m.reply_message.from_id if m.reply_message else extract_id(args)
    if not target: return
    
    pid = str(m.peer_id)
    u_rank = get_user_data(m.peer_id, target)[0]
    try:
        u = (await bot.api.users.get(user_ids=[target]))[0]
        real_name = f"{u.first_name} {u.last_name}"
    except: real_name = "Пользователь"

    if pid in DATABASE["chats"] and str(target) in DATABASE["chats"][pid].get("staff", {}):
        if u_rank == "Пользователь": del DATABASE["chats"][pid]["staff"][str(target)]
        else: DATABASE["chats"][pid]["staff"][str(target)] = [u_rank, real_name]
        
    await push_to_github(DATABASE, "Remove nick")
    mod_mention = await get_mention(m.peer_id, m.from_id)
    await m.answer(f"{mod_mention} убрал(-а) имя [id{target}|{real_name}]")

# --- 7. ВЫДАЧА ПРАВ ---

async def grant_role(m: Message, args, req_rank, role_name, action_text):
    if not await check_active(m) or not has_access(m.peer_id, m.from_id, req_rank): return
    target = m.reply_message.from_id if m.reply_message else extract_id(args)
    if not target: return
    
    pid = str(m.peer_id)
    _, nick = get_user_data(m.peer_id, target)
    if not nick:
        try:
            u = (await bot.api.users.get(user_ids=[target]))[0]
            nick = f"{u.first_name} {u.last_name}"
        except: nick = "Пользователь"

    if "chats" not in DATABASE: DATABASE["chats"] = {}
    if pid not in DATABASE["chats"]: DATABASE["chats"][pid] = {"staff": {}}
    
    if role_name == "Пользователь" and str(target) in DATABASE["chats"][pid]["staff"]:
        del DATABASE["chats"][pid]["staff"][str(target)]
    else:
        DATABASE["chats"][pid]["staff"][str(target)] = [role_name, nick]
        
    await push_to_github(DATABASE, f"Role {role_name}")
    mod_mention = await get_mention(m.peer_id, m.from_id)
    target_mention = await get_mention(m.peer_id, target)
    await m.answer(f"{mod_mention} {action_text} {target_mention}")

@bot.on.message(text=["/addmoder", "/addmoder <args>"])
async def addmoder_cmd(m: Message, args=None): await grant_role(m, args, "Старший Модератор", "Модератор", "выдал(-а) права модератора")

@bot.on.message(text=["/addsenmoder", "/addsenmoder <args>"])
async def addsenmoder_cmd(m: Message, args=None): await grant_role(m, args, "Администратор", "Старший Модератор", "выдал(-а) права старшего модератора")

@bot.on.message(text=["/addadmin", "/addadmin <args>"])
async def addadmin_cmd(m: Message, args=None): await grant_role(m, args, "Старший Администратор", "Администратор", "выдал(-а) права администратора")

@bot.on.message(text=["/addsenadmin", "/addsenadmin <args>"])
async def addsenadmin_cmd(m: Message, args=None): await grant_role(m, args, "Зам. Спец. Администратора", "Старший Администратор", "выдал(-а) права старшего администратора")

@bot.on.message(text=["/addzsa", "/addzsa <args>"])
async def addzsa_cmd(m: Message, args=None): await grant_role(m, args, "Спец. Администратор", "Зам. Спец. Администратора", "выдал(-а) права заместителя специального администратора")

@bot.on.message(text=["/addsa", "/addsa <args>"])
async def addsa_cmd(m: Message, args=None): await grant_role(m, args, "Владелец", "Спец. Администратор", "выдал(-а) права специального администратора")

@bot.on.message(text=["/addowner", "/addowner <args>"])
async def addowner_cmd(m: Message, args=None): await grant_role(m, args, "Зам. Специального Руководителя", "Владелец", "выдал(-а) права владельца")

@bot.on.message(text=["/removerole", "/removerole <args>"])
async def removerole_cmd(m: Message, args=None): await grant_role(m, args, "Старший Модератор", "Пользователь", "снял(-а) уровень прав")

# --- 8. СИСТЕМНЫЕ КОМАНДЫ ---

@bot.on.message(text="/start")
async def start_handler(m: Message):
    if int(m.from_id) != 870757778: return
    pid = str(m.peer_id)
    if "chats" not in DATABASE: DATABASE["chats"] = {}
    DATABASE["chats"][pid] = {"staff": {"870757778": ["Специальный Руководитель", "Misha Manlix"]}}
    await push_to_github(DATABASE, f"Activate {pid}")
    await m.answer("Вы успешно активировали Беседу.")

@bot.on.message(text="/sync")
async def sync_cmd(m: Message):
    if int(m.from_id) != 870757778: return
    url = f"https://api.github.com/repos/{GH_REPO}/contents/{GH_PATH}"
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers={"Authorization": f"token {GH_TOKEN}"}) as resp:
            if resp.status == 200:
                data = await resp.json()
                global DATABASE
                DATABASE = json.loads(base64.b64decode(data['content']).decode('utf-8'))
                with open(EXTERNAL_DB, "w", encoding="utf-8") as f:
                    json.dump(DATABASE, f, ensure_ascii=False, indent=4)
                await m.answer("Вы успешно синхронизировали Беседу с Базой данных.")

@bot.on.message(text="/chatid")
async def chatid_cmd(m: Message):
    if int(m.from_id) != 870757778: return
    await m.answer(f"ID Беседы: {m.peer_id}")

@bot.on.message(text="/delchat")
async def delchat_cmd(m: Message):
    if int(m.from_id) != 870757778: return
    pid = str(m.peer_id)
    if pid in DATABASE.get("chats", {}):
        del DATABASE["chats"][pid]
        await push_to_github(DATABASE, f"Delete {pid}")
        await m.answer("Вы успешно удалили чат с Базы данных.")

# --- СЕРВЕР ---
class H(BaseHTTPRequestHandler):
    def do_GET(self): self.send_response(200); self.end_headers(); self.wfile.write(b"OK")
threading.Thread(target=lambda: HTTPServer(('0.0.0.0', int(os.environ.get("PORT", 10000))), H).serve_forever(), daemon=True).start()
bot.run_forever()

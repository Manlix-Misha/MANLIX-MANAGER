import os
import threading
import re
import json
import base64
import aiohttp
from http.server import HTTPServer, BaseHTTPRequestHandler
from vkbottle.bot import Bot, Message
from vkbottle.dispatch.rules.base import ChatActionRule

# --- 1. НАСТРОЙКИ ---
GH_TOKEN = os.environ.get("GH_TOKEN")
GH_REPO = os.environ.get("GH_REPO") 
GH_PATH = "database.json"
EXTERNAL_DB = "database.json"

def load_local_data():
    if os.path.exists(EXTERNAL_DB):
        try:
            with open(EXTERNAL_DB, "r", encoding="utf-8") as f:
                return json.load(f)
        except: return {"chats": {}}
    return {"chats": {}}

DATABASE = load_local_data()

RANK_WEIGHT = {
    "Пользователь": 0, "Модератор": 1, "Старший Модератор": 2, 
    "Администратор": 3, "Старший Администратор": 4, "Зам. Спец. Администратора": 5,
    "Спец. Администратор": 6, "Владелец": 7, "Зам. Специального Руководителя": 8,
    "Основной зам. Специального Руководителя": 9, "Специальный Руководитель": 10
}

# --- 2. РАБОТА С GITHUB (ИСПРАВЛЕННЫЙ 404) ---

async def push_to_github(updated_db, message_text="Update"):
    url = f"https://api.github.com/repos/{GH_REPO}/contents/{GH_PATH}"
    headers = {"Authorization": f"token {GH_TOKEN}", "Accept": "application/vnd.github.v3+json"}
    
    try:
        async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=False)) as session:
            # Пытаемся получить SHA файла
            sha = None
            async with session.get(url, headers=headers) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    sha = data['sha']
                elif resp.status != 404:
                    return f"GitHub Error: {resp.status}"

            # Кодируем данные
            content_str = json.dumps(updated_db, ensure_ascii=False, indent=4)
            content_base64 = base64.b64encode(content_str.encode('utf-8')).decode('utf-8')
            
            payload = {"message": message_text, "content": content_base64}
            if sha: payload["sha"] = sha # Если файл был, добавляем SHA для обновления
            
            async with session.put(url, headers=headers, json=payload) as put_resp:
                if put_resp.status in [200, 201]:
                    with open(EXTERNAL_DB, "w", encoding="utf-8") as f:
                        json.dump(updated_db, f, ensure_ascii=False, indent=4)
                    return True
                return f"GitHub Push Error: {put_resp.status}"
    except Exception as e:
        return f"System Error: {str(e)}"

# --- 3. СИСТЕМНАЯ ЛОГИКА ---

def get_rank(peer_id, user_id):
    if int(user_id) == 870757778: return "Специальный Руководитель"
    staff = DATABASE.get("chats", {}).get(str(peer_id), {}).get("staff", {})
    return staff.get(str(user_id), ["Пользователь"])[0]

def has_access(peer_id, user_id, required_rank):
    u_rank = get_rank(peer_id, user_id)
    return RANK_WEIGHT.get(u_rank, 0) >= RANK_WEIGHT.get(required_rank, 0)

async def check_active(message: Message):
    if int(message.from_id) == 870757778: return True
    pid = str(message.peer_id)
    if pid not in DATABASE.get("chats", {}):
        await message.answer("Владелец беседы — не член команды бота, я не буду здесь работать!\n\nЧтобы я начал работу в данном чате тебе нужно обратиться к моему специальному руководителю написать ему или пригласи его в данный чат! Вк его: [https://vk.com/id870757778|Специальный руководитель].")
        return False
    return True

def extract_id(text):
    if not text: return None
    match = re.search(r'id(\d+)', str(text))
    if match: return int(match.group(1))
    digits = re.findall(r'\d+', str(text))
    return int(digits[0]) if digits else None

async def change_staff_rank(message, target_id, new_rank):
    if not target_id: return await message.answer("Укажите пользователя!")
    pid = str(message.peer_id)
    try:
        u_info = await bot.api.users.get(user_ids=[target_id])
        name = f"{u_info[0].first_name} {u_info[0].last_name}"
        if "chats" not in DATABASE: DATABASE["chats"] = {}
        if pid not in DATABASE["chats"]: DATABASE["chats"][pid] = {"staff": {}}
        DATABASE["chats"][pid]["staff"][str(target_id)] = [new_rank, name]
        
        res = await push_to_github(DATABASE, f"Set {new_rank} for {target_id}")
        if res is True:
            await message.answer(f"[id{message.from_id}|Ник] изменил(-а) уровень прав [id{target_id}|пользователю]")
        else: await message.answer(res)
    except Exception as e: await message.answer(f"Ошибка: {e}")

# --- 4. БОТ И КОМАНДЫ ---

bot = Bot(token=os.environ.get("TOKEN"))

@bot.on.message(text="/start")
async def start_handler(message: Message):
    if int(message.from_id) != 870757778: return
    pid = str(message.peer_id)
    if "chats" not in DATABASE: DATABASE["chats"] = {}
    
    if pid not in DATABASE["chats"]:
        try:
            c = await bot.api.messages.get_conversations_by_id(peer_ids=[message.peer_id])
            title = c.items[0].chat_settings.title
        except: title = "Беседа MANLIX"
        
        DATABASE["chats"][pid] = {
            "manlix_id": len(DATABASE["chats"]) + 1,
            "title": title,
            "staff": {"870757778": ["Специальный Руководитель", "Misha Manlix"]}
        }
        res = await push_to_github(DATABASE, f"Activate chat {pid}")
        if res is True: await message.answer("Вы успешно активировали Беседу!")
        else: await message.answer(f"Ошибка активации: {res}")
    else:
        await message.answer("Беседа уже активирована.")

@bot.on.message(text="/getid")
@bot.on.message(text="/getid <args>")
async def getid_handler(message: Message, args=None):
    if not await check_active(message): return
    tid = message.from_id
    if message.reply_message: tid = message.reply_message.from_id
    elif args:
        ext = extract_id(args)
        if ext: tid = ext
    await message.answer(f"Оригинальная ссылка [id{tid}|пользователя]: https://vk.com/id{tid}")

@bot.on.message(text="/gstaff")
async def gstaff_handler(message: Message):
    if not await check_active(message): return
    await message.answer("MANLIX MANAGER | Команда Бота:\n\n| Специальный Руководитель:\n– [id870757778|Misha Manlix]\n\n| Основной зам. Спец. Руководителя:\n– Отсутствует.\n\n| Зам. Спец. Руководителя:\n– Отсутствует.\n– Отсутствует.")

@bot.on.message(text="/staff")
async def staff_handler(message: Message):
    if not await check_active(message): return
    pid = str(message.peer_id)
    st = DATABASE.get("chats", {}).get(pid, {}).get("staff", {})
    ranks = ["Владелец", "Спец. Администратор", "Зам. Спец. Администратора", "Старший Администратор", "Администратор", "Старший Модератор", "Модератор"]
    out = ["MANLIX MANAGER"]
    for r in ranks:
        us = [f"– [id{u}|{info[1]}]" for u, info in st.items() if info[0] == r]
        out.append(f"{r}:\n" + ("\n".join(us) if us else "– Отсутствует."))
    await message.answer("\n\n".join(out))

# --- 5. КОМАНДЫ НАЗНАЧЕНИЯ ---

@bot.on.message(text=["/addmoder", "/addmoder <args>"])
async def a_mod(m: Message, args=None):
    if await check_active(m) and has_access(m.peer_id, m.from_id, "Старший Модератор"):
        await change_staff_rank(m, m.reply_message.from_id if m.reply_message else extract_id(args), "Модератор")

@bot.on.message(text=["/addsenmoder", "/addsenmoder <args>"])
async def a_smod(m: Message, args=None):
    if await check_active(m) and has_access(m.peer_id, m.from_id, "Администратор"):
        await change_staff_rank(m, m.reply_message.from_id if m.reply_message else extract_id(args), "Старший Модератор")

@bot.on.message(text=["/addadmin", "/addadmin <args>"])
async def a_adm(m: Message, args=None):
    if await check_active(m) and has_access(m.peer_id, m.from_id, "Старший Администратор"):
        await change_staff_rank(m, m.reply_message.from_id if m.reply_message else extract_id(args), "Администратор")

@bot.on.message(text=["/addsenadmin", "/addsenadmin <args>"])
async def a_sadm(m: Message, args=None):
    if await check_active(m) and has_access(m.peer_id, m.from_id, "Зам. Спец. Администратора"):
        await change_staff_rank(m, m.reply_message.from_id if m.reply_message else extract_id(args), "Старший Администратор")

@bot.on.message(text=["/addsza", "/addsza <args>"])
async def a_sza(m: Message, args=None):
    if await check_active(m) and has_access(m.peer_id, m.from_id, "Спец. Администратор"):
        await change_staff_rank(m, m.reply_message.from_id if m.reply_message else extract_id(args), "Зам. Спец. Администратора")

@bot.on.message(text=["/addsa", "/addsa <args>"])
async def a_sa(m: Message, args=None):
    if await check_active(m) and has_access(m.peer_id, m.from_id, "Владелец"):
        await change_staff_rank(m, m.reply_message.from_id if m.reply_message else extract_id(args), "Спец. Администратор")

@bot.on.message(text=["/addowner", "/addowner <args>"])
async def a_own(m: Message, args=None):
    if has_access(m.peer_id, m.from_id, "Зам. Специального Руководителя"):
        await change_staff_rank(m, m.reply_message.from_id if m.reply_message else extract_id(args), "Владелец")

@bot.on.message(text="/sync")
async def sync(message: Message):
    if int(message.from_id) != 870757778: return
    url = f"https://api.github.com/repos/{GH_REPO}/contents/{GH_PATH}"
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers={"Authorization": f"token {GH_TOKEN}"}) as resp:
            if resp.status == 200:
                data = await resp.json()
                global DATABASE
                DATABASE = json.loads(base64.b64decode(data['content']).decode('utf-8'))
                await message.answer("Синхронизация завершена.")

# --- СЕРВЕР ---
class H(BaseHTTPRequestHandler):
    def do_GET(self): self.send_response(200); self.end_headers(); self.wfile.write(b"OK")
threading.Thread(target=lambda: HTTPServer(('0.0.0.0', int(os.environ.get("PORT", 10000))), H).serve_forever(), daemon=True).start()
bot.run_forever()

import os
import threading
import re
import time
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

# Веса ролей для проверки иерархии
RANK_WEIGHT = {
    "Пользователь": 0, "Модератор": 1, "Старший Модератор": 2, 
    "Администратор": 3, "Старший Администратор": 4, "Зам. Спец. Администратора": 5,
    "Спец. Администратор": 6, "Владелец": 7, "Зам. Специального Руководителя": 8,
    "Основной зам. Специального Руководителя": 9, "Специальный Руководитель": 10
}

# --- 2. ФУНКЦИИ ВЗАИМОДЕЙСТВИЯ С GITHUB ---

async def push_to_github(updated_db, message_text="Update"):
    url = f"https://api.github.com/repos/{GH_REPO}/contents/{GH_PATH}"
    headers = {"Authorization": f"token {GH_TOKEN}", "Accept": "application/vnd.github.v3+json"}
    try:
        connector = aiohttp.TCPConnector(ssl=False)
        async with aiohttp.ClientSession(connector=connector) as session:
            # Получаем SHA текущего файла
            async with session.get(url, headers=headers) as resp:
                if resp.status != 200:
                    return f"GitHub Error: {resp.status} (Check GH_REPO or file path)"
                data = await resp.json()
                sha = data['sha']

            # Подготовка контента
            content_str = json.dumps(updated_db, ensure_ascii=False, indent=4)
            content_base64 = base64.b64encode(content_str.encode('utf-8')).decode('utf-8')
            
            payload = {"message": message_text, "content": content_base64, "sha": sha}
            async with session.put(url, headers=headers, json=payload) as put_resp:
                if put_resp.status in [200, 201]:
                    # Синхронизируем локальный файл для кэша
                    with open(EXTERNAL_DB, "w", encoding="utf-8") as f:
                        json.dump(updated_db, f, ensure_ascii=False, indent=4)
                    return True
                return f"GitHub Push Error: {put_resp.status}"
    except Exception as e:
        return f"System Error: {str(e)}"

# --- 3. ВСПОМОГАТЕЛЬНАЯ ЛОГИКА ---

def get_rank(peer_id, user_id):
    if int(user_id) == 870757778: return "Специальный Руководитель"
    pid_str = str(peer_id)
    chat_data = DATABASE.get("chats", {}).get(pid_str, {})
    staff = chat_data.get("staff", {})
    return staff.get(str(user_id), ["Пользователь"])[0]

def has_access(peer_id, user_id, required_rank):
    user_rank = get_rank(peer_id, user_id)
    return RANK_WEIGHT.get(user_rank, 0) >= RANK_WEIGHT.get(required_rank, 0)

async def check_active(message: Message):
    # Твой ID всегда имеет доступ
    if int(message.from_id) == 870757778: return True
    
    pid_str = str(message.peer_id)
    if pid_str not in DATABASE.get("chats", {}):
        text = (
            "Владелец беседы — не член команды бота, я не буду здесь работать!\n\n"
            "Чтобы я начал работу в данном чате тебе нужно обратиться к моему "
            "специальному руководителю написать ему или пригласи его в данный чат! "
            "Вк его: [https://vk.com/id870757778|Специальный руководитель]."
        )
        await message.answer(text)
        return False
    return True

def extract_id(text):
    if not text: return None
    match = re.search(r'id(\d+)', str(text))
    if match: return int(match.group(1))
    digits = re.findall(r'\d+', str(text))
    return int(digits[0]) if digits else None

async def change_staff_rank(message, target_id, new_rank):
    if not target_id:
        return await message.answer("Укажите пользователя!")
    
    pid_str = str(message.peer_id)
    try:
        user_info = await bot.api.users.get(user_ids=[target_id])
        name = f"{user_info[0].first_name} {user_info[0].last_name}"
        
        # Обновляем структуру данных
        if pid_str not in DATABASE["chats"]: DATABASE["chats"][pid_str] = {"staff": {}}
        DATABASE["chats"][pid_str]["staff"][str(target_id)] = [new_rank, name]
        
        # Сохранение на GitHub
        res = await push_to_github(DATABASE, f"Update rank: {new_rank} for {target_id}")
        if res is True:
            await message.answer(f"[id{message.from_id}|Ник] изменил(-а) уровень прав [id{target_id}|пользователю]")
        else:
            await message.answer(f"Ошибка сохранения: {res}")
    except Exception as e:
        await message.answer(f"Произошла ошибка: {e}")

# --- 4. ИНИЦИАЛИЗАЦИЯ БОТА ---
bot = Bot(token=os.environ.get("TOKEN"))

# --- 5. КОМАНДЫ УПРАВЛЕНИЯ БОТОМ ---

@bot.on.message(text="/start")
async def start_handler(message: Message):
    if int(message.from_id) != 870757778: return
    
    pid_str = str(message.peer_id)
    if pid_str not in DATABASE.get("chats", {}):
        try:
            c_info = await bot.api.messages.get_conversations_by_id(peer_ids=[message.peer_id])
            title = c_info.items[0].chat_settings.title
        except: title = "Беседа MANLIX"
        
        if "chats" not in DATABASE: DATABASE["chats"] = {}
        DATABASE["chats"][pid_str] = {
            "manlix_id": len(DATABASE["chats"]) + 1,
            "title": title,
            "staff": {"870757778": ["Специальный Руководитель", "Misha Manlix"]}
        }
        
        res = await push_to_github(DATABASE, f"Activate chat {pid_str}")
        if res is True: await message.answer("Вы успешно активировали Беседу!")
        else: await message.answer(f"Ошибка: {res}")
    else:
        await message.answer("Беседа уже активирована.")

@bot.on.message(text="/sync")
async def sync_handler(message: Message):
    if int(message.from_id) != 870757778: return
    url = f"https://api.github.com/repos/{GH_REPO}/contents/{GH_PATH}"
    headers = {"Authorization": f"token {GH_TOKEN}"}
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers) as resp:
            if resp.status == 200:
                data = await resp.json()
                global DATABASE
                DATABASE = json.loads(base64.b64decode(data['content']).decode('utf-8'))
                with open(EXTERNAL_DB, "w", encoding="utf-8") as f:
                    json.dump(DATABASE, f, ensure_ascii=False, indent=4)
                await message.answer("Синхронизация с GitHub завершена успешно.")

# --- 6. КОМАНДЫ ИНФОРМАЦИИ ---

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
    text = (
        "MANLIX MANAGER | Команда Бота:\n\n"
        "| Специальный Руководитель:\n"
        "– [id870757778|Misha Manlix]\n\n"
        "| Основной зам. Спец. Руководителя:\n"
        "– Отсутствует.\n\n"
        "| Зам. Спец. Руководителя:\n"
        "– Отсутствует.\n"
        "– Отсутствует."
    )
    await message.answer(text)

@bot.on.message(text="/staff")
async def staff_handler(message: Message):
    if not await check_active(message): return
    pid_str = str(message.peer_id)
    st = DATABASE.get("chats", {}).get(pid_str, {}).get("staff", {})
    ranks = ["Владелец", "Спец. Администратор", "Зам. Спец. Администратора", "Старший Администратор", "Администратор", "Старший Модератор", "Модератор"]
    
    output = ["MANLIX MANAGER"]
    for r in ranks:
        users = [f"– [id{u}|{info[1]}]" for u, info in st.items() if info[0] == r]
        output.append(f"{r}:\n" + ("\n".join(users) if users else "– Отсутствует."))
    
    await message.answer("\n\n".join(output))

# --- 7. КОМАНДЫ НАЗНАЧЕНИЯ ПРАВ ---

@bot.on.message(text=["/addmoder", "/addmoder <args>"])
async def add_mod(m: Message, args=None):
    if await check_active(m) and has_access(m.peer_id, m.from_id, "Старший Модератор"):
        tid = m.reply_message.from_id if m.reply_message else extract_id(args)
        await change_staff_rank(m, tid, "Модератор")

@bot.on.message(text=["/addsenmoder", "/addsenmoder <args>"])
async def add_smod(m: Message, args=None):
    if await check_active(m) and has_access(m.peer_id, m.from_id, "Администратор"):
        tid = m.reply_message.from_id if m.reply_message else extract_id(args)
        await change_staff_rank(m, tid, "Старший Модератор")

@bot.on.message(text=["/addadmin", "/addadmin <args>"])
async def add_adm(m: Message, args=None):
    if await check_active(m) and has_access(m.peer_id, m.from_id, "Старший Администратор"):
        tid = m.reply_message.from_id if m.reply_message else extract_id(args)
        await change_staff_rank(m, tid, "Администратор")

@bot.on.message(text=["/addsenadmin", "/addsenadmin <args>"])
async def add_sadm(m: Message, args=None):
    if await check_active(m) and has_access(m.peer_id, m.from_id, "Зам. Спец. Администратора"):
        tid = m.reply_message.from_id if m.reply_message else extract_id(args)
        await change_staff_rank(m, tid, "Старший Администратор")

@bot.on.message(text=["/addsza", "/addsza <args>"])
async def add_sza(m: Message, args=None):
    if await check_active(m) and has_access(m.peer_id, m.from_id, "Спец. Администратор"):
        tid = m.reply_message.from_id if m.reply_message else extract_id(args)
        await change_staff_rank(m, tid, "Зам. Спец. Администратора")

@bot.on.message(text=["/addsa", "/addsa <args>"])
async def add_sa_rank(m: Message, args=None):
    if await check_active(m) and has_access(m.peer_id, m.from_id, "Владелец"):
        tid = m.reply_message.from_id if m.reply_message else extract_id(args)
        await change_staff_rank(m, tid, "Спец. Администратор")

@bot.on.message(text=["/addowner", "/addowner <args>"])
async def add_owner_rank(m: Message, args=None):
    if has_access(m.peer_id, m.from_id, "Зам. Специального Руководителя"):
        tid = m.reply_message.from_id if m.reply_message else extract_id(args)
        await change_staff_rank(m, tid, "Владелец")

# --- 8. ТЕХНИЧЕСКАЯ ЧАСТЬ (RENDER) ---
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b"ALIVE")

threading.Thread(target=lambda: HTTPServer(('0.0.0.0', int(os.environ.get("PORT", 10000))), Handler).serve_forever(), daemon=True).start()
bot.run_forever()

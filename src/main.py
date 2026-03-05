import os
import threading
import re
import time
import json
import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from vkbottle.bot import Bot, Message
from vkbottle import Keyboard, KeyboardButtonColor, Text

# --- 1. ДАННЫЕ (НЕ ИЗМЕНЯТЬ НАЧАЛО ДЛЯ RENDER) ---
USER_DATA = {
    870757778: ["Специальный Руководитель", "Misha Manlix"],
}

DB_FILE = "chats_db.json"

def load_chats():
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, "r") as f:
                return set(json.load(f))
        except: return set()
    return set()

def save_chats():
    try:
        with open(DB_FILE, "w") as f:
            json.dump(list(ACTIVE_CHATS), f)
    except: pass

ACTIVE_CHATS = load_chats()

RANK_WEIGHT = {
    "Пользователь": 0, "Модератор": 1, "Старший Модератор": 2, 
    "Администратор": 3, "Старший Администратор": 4, "Зам. Спец. Администратора": 5,
    "Спец. Администратор": 6, "Владелец": 7, "Заместитель Специального Руководителя": 8,
    "Основной Зам Специального Руководителя": 9, "Специальный Руководитель": 10
}

# --- 2. ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---
def get_rank(user_id):
    return USER_DATA.get(user_id, ["Пользователь"])[0]

def has_access(user_id, required_rank):
    return RANK_WEIGHT.get(get_rank(user_id), 0) >= RANK_WEIGHT.get(required_rank, 0)

def extract_id(text):
    if not text: return None
    match = re.search(r'id(\d+)', str(text))
    return int(match.group(1)) if match else None

# --- 3. ИНИЦИАЛИЗАЦИЯ ---
bot = Bot(token=os.environ.get("TOKEN"))

# --- 4. КОМАНДА /HELP ---
@bot.on.message(text=["/help", "/help <args>"])
async def help_handler(message: Message, args=None):
    is_sr = has_access(message.from_id, "Специальный Руководитель")
    if message.peer_id not in ACTIVE_CHATS and not is_sr:
        return "Ошибка: беседа не активирована."

    msg1 = "Команды пользователей:\n"
    msg1 += "/info -- Официальные ресурсы\n/stats -- Ваша статистика\n/getid -- Получить ссылку на профиль\n/staff -- Список администрации беседы\n/ping -- Проверка времени отклика\n\n"
    if has_access(message.from_id, "Модератор"):
        msg1 += "Команды модерации:\n/kick -- Исключить пользователя\n/mute -- Выдать блокировку чата"
    await message.answer(msg1)

    if has_access(message.from_id, "Заместитель Специального Руководителя"):
        msg2 = "Команды руководства:\n\n"
        msg2 += "Команды ЗСР:\n/gstaff -- Список высшего руководства\n/gbanpl -- Выдать глобальный бан\n/gunbanpl -- Снять глобальный бан\n\n"
        if has_access(message.from_id, "Специальный Руководитель"):
            msg2 += "Команды Спец. Руководителя:\n/start -- Активация беседы\n/sync -- Синхронизация беседы"
        await message.answer(msg2)

# --- 5. ИНФО-КОМАНДЫ ---

@bot.on.message(text=["/staff", "/staff <args>"])
async def staff_handler(message: Message, args=None):
    if message.peer_id not in ACTIVE_CHATS: return
    roles = ["Владелец", "Спец. Администратор", "Зам. Спец. Администратора", "Старший Администратор", "Администратор", "Старший Модератор", "Модератор"]
    parts = []
    for r in roles:
        found = [f"– [id{uid}|{data[1]}]" for uid, data in USER_DATA.items() if data[0] == r]
        parts.append(f"{r}: \n" + ("\n".join(found) if found else "– Отсутствует."))
    await message.answer("\n\n".join(parts))

@bot.on.message(text=["/gstaff", "/gstaff <args>"])
async def gstaff_handler(message: Message, args=None):
    if message.peer_id not in ACTIVE_CHATS or not has_access(message.from_id, "Заместитель Специального Руководителя"): return
    spec_boss = "– [https://vk.com/id870757778|Misha Manlix]"
    main_deputy = "– Отсутствует."
    deputies = [f"– [https://vk.com/id{uid}|{data[1]}]" for uid, data in USER_DATA.items() if data[0] == "Заместитель Специального Руководителя"]
    deputy_list = deputies[:2] + ["– Отсутствует."] * (2 - len(deputies[:2]))
    res = (f"MANLIX MANAGER | Команда Бота:\n\n| Специальный Руководитель:\n{spec_boss}\n\n"
           f"| Основной зам. Спец. Руководителя:\n{main_deputy}\n\n"
           f"| Зам. Спец. Руководителя:\n" + "\n".join(deputy_list))
    await message.answer(res)

# --- 6. МОДЕРАЦИЯ ---

@bot.on.message(text=["/kick", "/kick <args>"])
async def kick_handler(message: Message, args=None):
    if message.peer_id not in ACTIVE_CHATS or not has_access(message.from_id, "Модератор"): return
    target_id = message.reply_message.from_id if message.reply_message else extract_id(args)
    if not target_id: return "Укажите пользователя!"
    
    try:
        await bot.api.messages.remove_chat_user(chat_id=message.peer_id - 2000000000, user_id=target_id)
        mod_nick = USER_DATA.get(message.from_id, ["", "MANLIX"])[1]
        res = f"[id{message.from_id}|Модератор {mod_nick}] исключил(-а) [id{target_id}|пользователя] из Беседы."
        await message.answer(res)
    except: await message.answer("Ошибка: не удалось исключить.")

@bot.on.message(text=["/mute", "/mute <args>"])
async def mute_handler(message: Message, args=None):
    if message.peer_id not in ACTIVE_CHATS or not has_access(message.from_id, "Модератор"): return
    target_id = message.reply_message.from_id if message.reply_message else extract_id(args)
    if not target_id: return "Укажите пользователя!"
    
    time_minutes = 30
    reason = "Не указана"
    if args:
        find_time = re.findall(r'\d+', args)
        if find_time:
            time_minutes = int(find_time[0])
            reason_text = re.sub(r'\d+', '', args).strip()
            if reason_text: reason = reason_text

    until_date = datetime.datetime.now() + datetime.timedelta(minutes=time_minutes)
    date_str = until_date.strftime("%d/%m/%Y %H:%M:%S")
    mod_nick = USER_DATA.get(message.from_id, ["", "MANLIX"])[1]
    
    kb = Keyboard(inline=True)
    kb.add(Text("Снять мут", payload={"cmd": "unmute", "target": target_id}), color=KeyboardButtonColor.POSITIVE)
    kb.add(Text("Очистить", payload={"cmd": "clear_msgs", "target": target_id}), color=KeyboardButtonColor.NEGATIVE)

    res = (f"[id{message.from_id}|Модератор {mod_nick}] замутил(-а) [id{target_id}|пользователя]\n"
           f"Причина: {reason}\n"
           f"Мут выдан до: {date_str}")
    await message.answer(res, keyboard=kb)

# --- 7. ОБРАБОТКА КНОПОК И СОБЫТИЙ ---

@bot.on.message(func=lambda message: message.payload is not None)
async def payload_handler(message: Message):
    payload = message.payload
    if isinstance(payload, str): payload = json.loads(payload)
    if not has_access(message.from_id, "Модератор"): return

    cmd = payload.get("cmd")
    target = payload.get("target")

    if cmd == "unmute":
        await message.answer(f"Модератор [id{message.from_id}|{USER_DATA.get(message.from_id, ['', 'Admin'])[1]}] досрочно снял мут с [id{target}|пользователя].")
    elif cmd == "clear_msgs":
        await message.answer(f"Сообщения пользователя [id{target}|ID {target}] были очищены модератором.")
    elif cmd == "kick_btn":
        try:
            await bot.api.messages.remove_chat_user(chat_id=message.peer_id - 2000000000, user_id=target)
            await message.answer(f"Пользователь [id{target}|ID {target}] был окончательно исключен.")
        except: pass

@bot.on.message()
async def exit_handler(message: Message):
    if not message.action or message.peer_id not in ACTIVE_CHATS: return
    if "chat_kick_user" in str(message.action.type):
        mid = message.action.member_id
        if mid == message.from_id:
            kb = Keyboard(inline=True).add(Text("Исключить", payload={"cmd": "kick_btn", "target": mid}), color=KeyboardButtonColor.NEGATIVE)
            await message.answer(f"[id{mid}|Пользователь] покинул(а) Беседу.", keyboard=kb)

# --- 8. УПРАВЛЕНИЕ ---

@bot.on.message(text=["/sync", "/sync <args>", "/start", "/start <args>"])
async def activation(message: Message, args=None):
    if not has_access(message.from_id, "Специальный Руководитель"): return
    ACTIVE_CHATS.add(message.peer_id)
    save_chats()
    await message.answer(f"Беседа синхронизирована!")

@bot.on.message(text=["/stats", "/stats <args>"])
async def stats(message: Message, args=None):
    if message.peer_id not in ACTIVE_CHATS: return
    tid = message.reply_message.from_id if message.reply_message else message.from_id
    await message.answer(f"Статистика пользователя:\nID: {tid}\nРоль: {get_rank(tid)}")

@bot.on.message(text=["/ping", "/ping <args>"])
async def ping(message: Message, args=None):
    delta = time.time() - (message.date or time.time())
    await message.answer(f"ПОНГ!\nВремя обработки - {round(abs(delta), 2)} сек.")

# --- 9. СЕРВЕР ---
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b"OK")

threading.Thread(target=lambda: HTTPServer(('0.0.0.0', int(os.environ.get("PORT", 10000))), Handler).serve_forever(), daemon=True).start()
bot.run_forever()

import os
import threading
import re
import time
import json
from http.server import HTTPServer, BaseHTTPRequestHandler
from vkbottle.bot import Bot, Message
from vkbottle import Keyboard, KeyboardButtonColor, Text

# --- 1. ДАННЫЕ (НЕ ИЗМЕНЯТЬ НАЧАЛО ДЛЯ RENDER) ---
USER_DATA = {
    870757778: ["Специальный Руководитель", "Misha Manlix"],
}

GBAN_LIST = set() 
ACTIVE_CHATS = set()

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
        msg1 += "Команды модерации:\n/kick -- Исключить пользователя\n/mute -- Выдать блокировку чата\n\n"
    
    if has_access(message.from_id, "Администратор"):
        msg1 += "Команды администрации:\n/warn -- Выдать предупреждение\n\n"
        
    if has_access(message.from_id, "Зам. Спец. Администратора"):
        msg1 += "Команды Спец. Администрации:\n/check -- Проверить игрока\n"
    await message.answer(msg1)

    if has_access(message.from_id, "Заместитель Специального Руководителя"):
        msg2 = "Команды руководства:\n\n"
        msg2 += "Команды ЗСР:\n/gstaff -- Список высшего руководства\n/gbanpl -- Выдать глобальный бан\n/gunbanpl -- Снять глобальный бан\n\n"
        if has_access(message.from_id, "Специальный Руководитель"):
            msg2 += "Команды Спец. Руководителя:\n/start -- Активация беседы\n/sync -- Синхронизация беседы"
        await message.answer(msg2)

# --- 5. КОМАНДЫ ИНФО (/STATS, /INFO, /STAFF, /GSTAFF) ---

@bot.on.message(text=["/stats", "/stats <args>"])
async def stats_handler(message: Message, args=None):
    if message.peer_id not in ACTIVE_CHATS: return
    target_id = message.reply_message.from_id if message.reply_message else message.from_id
    rank = get_rank(target_id)
    await message.answer(f"Статистика пользователя:\nID: {target_id}\nРоль: {rank}")

@bot.on.message(text=["/info", "/info <args>"])
async def info_handler(message: Message, args=None):
    if message.peer_id not in ACTIVE_CHATS: return
    await message.answer("Официальные ресурсы проекта:\nГруппа ВК: vk.com/manlix_project\nРазработчик: vk.com/id870757778")

@bot.on.message(text=["/staff", "/staff <args>"])
async def staff_handler(message: Message, args=None):
    if message.peer_id not in ACTIVE_CHATS: return
    roles = ["Владелец", "Спец. Администратор", "Зам. Спец. Администратора", "Старший Администратор", "Администратор", "Старший Модератор", "Модератор"]
    res = "Список администрации беседы:\n\n"
    parts = []
    for r in roles:
        found = [f"– [id{uid}|{data[1]}]" for uid, data in USER_DATA.items() if data[0] == r]
        parts.append(f"{r}: \n" + ("\n".join(found) if found else "– Отсутствует."))
    res += "\n\n".join(parts)
    await message.answer(res)

@bot.on.message(text=["/gstaff", "/gstaff <args>"])
async def gstaff_handler(message: Message, args=None):
    if message.peer_id not in ACTIVE_CHATS or not has_access(message.from_id, "Заместитель Специального Руководителя"): return
    spec_boss = "– [https://vk.com/id870757778|Misha Manlix]"
    main_deputy = "– Отсутствует."
    
    # Исправленный блок без лишних отступов
    deputies = [f"– [https://vk.com/id{uid}|{data[1]}]" for uid, data in USER_DATA.items() if data[0] == "Заместитель Специального Руководителя"]
    deputy_list_padded = deputies[:2] + ["– Отсутствует."] * (2 - len(deputies[:2]))
    deputy_str = "\n".join(deputy_list_padded)
    
    res = (f"MANLIX MANAGER | Команда Бота:\n\n| Специальный Руководитель:\n{spec_boss}\n\n"
           f"| Основной зам. Спец. Руководителя:\n{main_deputy}\n\n"
           f"| Зам. Спец. Руководителя:\n{deputy_str}")
    await message.answer(res)

@bot.on.message(text=["/getid", "/getid <args>"])
async def getid_handler(message: Message, args=None):
    if message.peer_id not in ACTIVE_CHATS: return
    target_id = message.reply_message.from_id if message.reply_message else message.from_id
    await message.answer(f"Оригинальная ссылка на ВК:\nhttps://vk.com/id{target_id}")

# --- 6. МОДЕРАЦИЯ И ГБАН ---

@bot.on.message(text=["/kick", "/kick <args>"])
async def kick_handler(message: Message, args=None):
    if message.peer_id not in ACTIVE_CHATS: return
    if not has_access(message.from_id, "Модератор"): return
    if not message.reply_message: return "Ошибка: ответьте на сообщение пользователя."
    target_id = message.reply_message.from_id
    try:
        await bot.api.messages.remove_chat_user(chat_id=message.peer_id - 2000000000, user_id=target_id)
        await message.answer(f"Пользователь https://vk.com/id{target_id} исключен из беседы.")
    except Exception:
        await message.answer("Ошибка: бот должен быть администратором, а цель не должна быть администратором беседы.")

@bot.on.message(text=["/mute", "/mute <args>"])
async def mute_handler(message: Message, args=None):
    if message.peer_id not in ACTIVE_CHATS or not has_access(message.from_id, "Модератор"): return
    target_id = message.reply_message.from_id if message.reply_message else extract_id(args)
    if not target_id: return "Ошибка: укажите пользователя ссылкой или ответом."
    time_v = args.split()[-1] if args and args.split()[-1].isdigit() else "30"
    mod_nick = USER_DATA.get(message.from_id, ["", "Admin"])[1]
    await message.answer(f"[https://vk.com/id{message.from_id}|Модератор {mod_nick}] выдал Блокировку чата [https://vk.com/id{target_id}|пользователю] на {time_v} минут.")

@bot.on.message(text=["/gbanpl", "/gbanpl <args>"])
async def gban_handler(message: Message, args=None):
    if not has_access(message.from_id, "Заместитель Специального Руководителя"): return
    target_id = extract_id(args) if args else (message.reply_message.from_id if message.reply_message else None)
    if not target_id: return "Ошибка: укажите пользователя."
    GBAN_LIST.add(target_id)
    try: await bot.api.messages.remove_chat_user(chat_id=message.peer_id - 2000000000, user_id=target_id)
    except: pass
    await message.answer(f"Пользователь [id{target_id}|ID {target_id}] занесен в Глобальный Бан-лист.")

@bot.on.message(text=["/gunbanpl", "/gunbanpl <args>"])
async def gunban_handler(message: Message, args=None):
    if not has_access(message.from_id, "Заместитель Специального Руководителя"): return
    target_id = extract_id(args) if args else (message.reply_message.from_id if message.reply_message else None)
    if not target_id: return "Ошибка: укажите пользователя."
    if target_id in GBAN_LIST:
        GBAN_LIST.remove(target_id)
        await message.answer(f"Пользователь [id{target_id}|ID {target_id}] вынесен из Гбан-листа.")

# --- 7. СИСТЕМА ВЫХОДА И КНОПКА "ИСКЛЮЧИТЬ" ---

@bot.on.message(func=lambda message: message.action and getattr(message.action.type, "value", str(message.action.type)) == "chat_kick_user")
async def leave_handler(message: Message):
    if message.peer_id not in ACTIVE_CHATS: return
    member_id = message.action.member_id
    
    # Срабатывает только если пользователь вышел сам (member_id равен from_id)
    if member_id == message.from_id:
        keyboard = Keyboard(inline=True)
        # Добавляем красную кнопку (NEGATIVE), в payload передаем команду и ID нарушителя
        keyboard.add(Text("Исключить", payload={"cmd": "kick_btn", "target": member_id}), color=KeyboardButtonColor.NEGATIVE)
        await message.answer(f"[id{member_id}|Пользователь] покинул(а) Беседу.", keyboard=keyboard)

@bot.on.message(func=lambda message: message.payload is not None)
async def payload_handler(message: Message):
    if message.peer_id not in ACTIVE_CHATS: return
    try:
        payload = json.loads(message.payload)
    except:
        return
        
    if payload.get("cmd") == "kick_btn":
        if not has_access(message.from_id, "Модератор"):
            return # Если жмет обычный игрок - игнорируем
            
        target_id = payload.get("target")
        try:
            await bot.api.messages.remove_chat_user(chat_id=message.peer_id - 2000000000, user_id=target_id)
            mod_nick = USER_DATA.get(message.from_id, ["", "Модератор"])[1]
            await message.answer(f"[id{message.from_id}|{mod_nick}] окончательно исключил [id{target_id}|пользователя] из беседы.")
        except Exception:
            await message.answer("Ошибка: бот не смог исключить пользователя (возможно, у бота нет прав).")

# --- 8. УПРАВЛЕНИЕ И ПИНГ ---

@bot.on.message(text=["/ping", "/ping <args>"])
async def ping_handler(message: Message, args=None):
    delta = time.time() - message.date
    await message.answer(f"ПОНГ!\nВремя обработки сообщений - {round(delta, 2)} секунд")

@bot.on.message(text=["/sync", "/sync <args>"])
async def sync_handler(message: Message, args=None):
    if not has_access(message.from_id, "Специальный Руководитель"): return
    ACTIVE_CHATS.add(message.peer_id)
    nick = USER_DATA.get(message.from_id, ["", "Руководитель"])[1]
    await message.answer(f"[https://vk.com/id{message.from_id}|{nick}] синхронизировал Беседу с Базой данных!")

@bot.on.message(text=["/start", "/start <args>"])
async def start_handler(message: Message, args=None):
    if not has_access(message.from_id, "Специальный Руководитель"):
        return "Вы не можете активировать Беседу."
    ACTIVE_CHATS.add(message.peer_id)
    await message.answer("Проверка прав пройдена успешно. Беседа активирована.")

# --- СЕРВЕР RENDER ---
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b"OK")

def run_port():
    port = int(os.environ.get("PORT", 10000))
    HTTPServer(('0.0.0.0', port), Handler).serve_forever()

threading.Thread(target=run_port, daemon=True).start()
bot.run_forever()

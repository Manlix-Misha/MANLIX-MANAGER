import os
import threading
import re
import time
import json
from http.server import HTTPServer, BaseHTTPRequestHandler
from vkbottle.bot import Bot, Message
from vkbottle import Keyboard, KeyboardButtonColor, Text

# --- 1. ДАННЫЕ (СТРОГО ДЛЯ RENDER) ---
USER_DATA = {
    870757778: ["Специальный Руководитель", "Misha Manlix"],
}

GBAN_LIST = set() 
ACTIVE_CHATS = set() # Сюда добавляются ID после /sync

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

# --- 3. ИНИЦИАЛИЗАЦИЯ ---
bot = Bot(token=os.environ.get("TOKEN"))

# --- 4. КОМАНДЫ АКТИВАЦИИ (ВЫСШИЙ ПРИОРИТЕТ) ---

@bot.on.message(text=["/sync", "/sync <args>", "/start", "/start <args>"])
async def activation_handler(message: Message, args=None):
    # Проверка: только Специальный Руководитель может активировать
    if not has_access(message.from_id, "Специальный Руководитель"):
        return "У вас нет прав для активации бота в этой беседе."
    
    ACTIVE_CHATS.add(message.peer_id)
    nick = USER_DATA.get(message.from_id, ["", "Руководитель"])[1]
    
    if "/sync" in message.text.lower():
        await message.answer(f"[id{message.from_id}|{nick}] синхронизировал Беседу с Базой данных!")
    else:
        await message.answer("Проверка прав пройдена успешно. Беседа активирована.")

# --- 5. КОМАНДА /HELP ---
@bot.on.message(text=["/help", "/help <args>"])
async def help_handler(message: Message, args=None):
    is_sr = has_access(message.from_id, "Специальный Руководитель")
    if message.peer_id not in ACTIVE_CHATS and not is_sr:
        return "Ошибка: беседа не активирована. Напишите /sync"

    msg1 = "Команды пользователей:\n"
    msg1 += "/info -- Официальные ресурсы\n/stats -- Ваша статистика\n/getid -- Получить ссылку на профиль\n/staff -- Список администрации беседы\n/ping -- Проверка времени отклика\n\n"
    
    if has_access(message.from_id, "Модератор"):
        msg1 += "Команды модерации:\n/kick -- Исключить пользователя\n/mute -- Выдать блокировку чата\n\n"
    
    if has_access(message.from_id, "Заместитель Специального Руководителя"):
        msg2 = "Команды руководства:\n\n"
        msg2 += "Команды ЗСР:\n/gstaff -- Список высшего руководства\n/gbanpl -- Выдать глобальный бан\n/gunbanpl -- Снять глобальный бан\n\n"
        if has_access(message.from_id, "Специальный Руководитель"):
            msg2 += "Команды Спец. Руководителя:\n/start -- Активация беседы\n/sync -- Синхронизация беседы"
        await message.answer(msg2)
    await message.answer(msg1)

# --- 6. ОСТАЛЬНЫЕ КОМАНДЫ (РАБОТАЮТ ТОЛЬКО ПОСЛЕ SYNC) ---

@bot.on.message(text=["/stats", "/stats <args>"])
async def stats_handler(message: Message, args=None):
    if message.peer_id not in ACTIVE_CHATS: return
    tid = message.reply_message.from_id if message.reply_message else message.from_id
    await message.answer(f"Статистика пользователя:\nID: {tid}\nРоль: {get_rank(tid)}")

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

@bot.on.message(text=["/ping", "/ping <args>"])
async def ping_handler(message: Message, args=None):
    delta = time.time() - (message.date or time.time())
    await message.answer(f"ПОНГ!\nВремя обработки - {round(abs(delta), 2)} сек.")

# --- 7. СЕРВЕР (ДЛЯ RENDER) ---
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b"OK")

def run_port():
    port = int(os.environ.get("PORT", 10000))
    HTTPServer(('0.0.0.0', port), Handler).serve_forever()

threading.Thread(target=run_port, daemon=True).start()
bot.run_forever()

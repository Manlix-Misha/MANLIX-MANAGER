import os
import threading
import re
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from vkbottle.bot import Bot, Message

# --- 1. ДАННЫЕ ---
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
    match = re.search(r'id(\d+)', text)
    if match: return int(match.group(1))
    return None

# --- 3. ИНИЦИАЛИЗАЦИЯ ---
bot = Bot(token=os.environ.get("TOKEN"))

# --- 4. ОБНОВЛЕННАЯ КОМАНДА /HELP ---
@bot.on.message(text="/help")
async def help_handler(message: Message):
    if message.peer_id not in ACTIVE_CHATS and not has_access(message.from_id, "Специальный Руководитель"):
        return "Ошибка: беседа не активирована."

    # --- СООБЩЕНИЕ №1 (ОБЩЕЕ) ---
    help_msg = (
        "Команды пользователей:\n"
        "/info -- Официальные ресурсы\n"
        "/stats -- Ваша статистика\n"
        "/getid -- Получить ссылку на профиль\n"
        "/staff -- Состав администрации беседы\n"
        "/ping -- Проверка времени отклика\n\n"
    )
    
    if has_access(message.from_id, "Модератор"):
        help_msg += (
            "Команды модераторов:\n"
            "/kick -- Исключить пользователя из беседы\n"
            "/mute -- Выдать блокировку чата\n\n"
        )
        
    await message.answer(help_msg)

    # --- СООБЩЕНИЕ №2 (ДЛЯ РУКОВОДСТВА) ---
    if has_access(message.from_id, "Заместитель Специального Руководителя"):
        guide_msg = "Команды руководства:\n\n"
        
        # Блок для ЗСР
        guide_msg += (
            "Команды ЗСР:\n"
            "/gstaff -- Список высшего руководства\n"
            "/gbanpl -- Выдать глобальный бан игроку\n"
            "/gunbanpl -- Снять глобальный бан игрока\n\n"
        )
        
        # Блок для Спец. Руководителя
        if has_access(message.from_id, "Специальный Руководитель"):
            guide_msg += (
                "Команды Спец. Руководителя:\n"
                "/start -- Активация беседы с уведомлением\n"
                "/sync -- Синхронизация беседы с базой\n"
            )
            
        await message.answer(guide_msg)

# --- 5. ОСТАЛЬНЫЕ КОМАНДЫ (БЕЗ ИЗМЕНЕНИЙ) ---

@bot.on.message(text="/ping")
async def ping_handler(message: Message):
    delta = time.time() - message.date
    await message.answer(f"ПОНГ!\nВремя обработки сообщений - {round(delta, 2)} секунд")

@bot.on.message(text="/sync")
async def sync_handler(message: Message):
    if not has_access(message.from_id, "Специальный Руководитель"): return
    ACTIVE_CHATS.add(message.peer_id)
    link = f"https://vk.com/id{message.from_id}"
    nick = USER_DATA[message.from_id][1]
    await message.answer(f"[{link}|{nick}] синхронизировал Беседу с Базой данных!")

@bot.on.message(text="/staff")
async def staff_handler(message: Message):
    if message.peer_id not in ACTIVE_CHATS: return
    roles = ["Владелец", "Спец. Администратор", "Зам. Спец. Администратора", "Старший Администратор", "Администратор", "Старший Модератор", "Модератор"]
    res_parts = []
    for r in roles:
        found = [f"– [id{uid}|{data[1]}]" for uid, data in USER_DATA.items() if data[0] == r]
        res_parts.append(f"{r}:\n" + ("\n".join(found) if found else "– Отсутствует."))
    await message.answer("\n\n".join(res_parts))

@bot.on.message(text="/gstaff")
async def gstaff_handler(message: Message):
    if message.peer_id not in ACTIVE_CHATS: return
    if not has_access(message.from_id, "Заместитель Специального Руководителя"): return
    spec_boss = "– [https://vk.com/id870757778|Misha Manlix]"
    main_deputy = "– Отсутствует."
    deputy_list = [f"– [https://vk.com/id{uid}|{data[1]}]" for uid, data in USER_DATA.items() if data[0] == "Заместитель Специального Руководителя"]
    deputy_str = "\n".join(deputy_list[:2]) + ("\n– Отсутствует." * (2 - len(deputy_list)))
    res = f"MANLIX MANAGER | Команда Бота:\n\n| Специальный Руководитель:\n{spec_boss}\n\n| Основной зам. Спец. Руководителя:\n{main_deputy}\n\n| Зам. Спец. Руководителя:\n{deputy_str}"
    await message.answer(res)

@bot.on.message(text="/mute <args>")
@bot.on.message(text="/mute")
async def mute_handler(message: Message, args=None):
    if message.peer_id not in ACTIVE_CHATS or not has_access(message.from_id, "Модератор"): return
    target_id = message.reply_message.from_id if message.reply_message else extract_id(args)
    if not target_id: return
    time_v = args.split()[-1] if args and args.split()[-1].isdigit() else "30"
    mod_nick = USER_DATA.get(message.from_id, ["", "Admin"])[1]
    await message.answer(f"[https://vk.com/id{message.from_id}|Модератор {mod_nick}] выдал Блокировку чата [https://vk.com/id{target_id}|пользователю] на {time_v} минут.")

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b"OK")

def run_port():
    port = int(os.environ.get("PORT", 10000))
    HTTPServer(('0.0.0.0', port), Handler).serve_forever()

threading.Thread(target=run_port, daemon=True).start()
bot.run_forever()

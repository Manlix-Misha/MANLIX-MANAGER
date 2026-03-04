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
MUTE_LIST = {} # {user_id: timestamp_end}

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
    # Поиск просто цифр, если введена только цифра ID
    digits = re.findall(r'\d+', text)
    if digits: return int(digits[-1])
    return None

# --- 3. ИНИЦИАЛИЗАЦИЯ ---
bot = Bot(token=os.environ.get("TOKEN"))

# --- 4. СИСТЕМА УДАЛЕНИЯ ПРИ МУТЕ ---
@bot.on.message()
async def mute_watcher(message: Message):
    if message.from_id in MUTE_LIST:
        if time.time() < MUTE_LIST[message.from_id]:
            try:
                await message.ctx_api.messages.delete(
                    cmids=[message.conversation_message_id],
                    peer_id=message.peer_id,
                    delete_for_all=True
                )
                return
            except:
                pass
    if message.text.startswith("/"):
        return

# --- 5. ОБНОВЛЕННАЯ КОМАНДА /HELP ---
@bot.on.message(text="/help")
async def help_handler(message: Message):
    if message.peer_id not in ACTIVE_CHATS and not has_access(message.from_id, "Специальный Руководитель"):
        return "Ошибка: беседа не активирована."

    help_msg = (
        "Команды пользователей:\n"
        "/info -- Официальные ресурсы\n"
        "/stats -- Ваша статистика\n"
        "/getid -- Получить ссылку на профиль\n\n"
    )
    
    if has_access(message.from_id, "Модератор"):
        help_msg += (
            "Команды модераторов:\n"
            "/kick -- Исключить пользователя\n"
            "/mute -- Выдать блокировку чата\n\n"
        )
        
    await message.answer(help_msg)

# --- 6. КОМАНДЫ ПОЛЬЗОВАТЕЛЕЙ ---

@bot.on.message(text="/info")
async def info_handler(message: Message):
    await message.answer("Официальные ресурсы проекта: (ссылка)")

@bot.on.message(text="/stats")
async def stats_handler(message: Message):
    await message.answer(f"Ваша статистика:\nID: {message.from_id}\nРанг: {get_rank(message.from_id)}")

@bot.on.message(text="/getid")
async def getid_handler(message: Message):
    target_id = message.reply_message.from_id if message.reply_message else message.from_id
    await message.answer(f"Ссылка на профиль: vk.com/id{target_id}")

# --- 7. КОМАНДЫ МОДЕРАЦИИ ---

@bot.on.message(text=["/kick", "/kick <args>"])
async def kick_handler(message: Message, args=None):
    if not has_access(message.from_id, "Модератор"): return
    target_id = message.reply_message.from_id if message.reply_message else extract_id(args)
    if not target_id: return "Укажите пользователя ссылкой или ответом."
    
    try:
        await message.ctx_api.messages.remove_chat_user(chat_id=message.peer_id - 2000000000, user_id=target_id)
        await message.answer(f"Пользователь vk.com/id{target_id} исключен.")
    except Exception as e:
        await message.answer(f"Ошибка при исключении: {e}")

@bot.on.message(text=["/mute", "/mute <args>"])
async def mute_handler(message: Message, args=None):
    if not has_access(message.from_id, "Модератор"): return
    target_id = message.reply_message.from_id if message.reply_message else extract_id(args)
    if not target_id: return "Укажите пользователя."

    minutes = 30
    if args and args.split()[-1].isdigit():
        minutes = int(args.split()[-1])

    MUTE_LIST[target_id] = time.time() + (minutes * 60)
    mod_nick = USER_DATA.get(message.from_id, ["", "Admin"])[1]
    await message.answer(f"Модератор {mod_nick} выдал блокировку чата пользователю vk.com/id{target_id} на {minutes} минут.")

# --- 8. ОРИГИНАЛЬНЫЕ КОМАНДЫ РУКОВОДСТВА (БЕЗ ИЗМЕНЕНИЙ) ---

@bot.on.message(text="/sync")
async def sync_handler(message: Message):
    if not has_access(message.from_id, "Специальный Руководитель"): return
    ACTIVE_CHATS.add(message.peer_id)
    nick = USER_DATA[message.from_id][1]
    await message.answer(f"ID{message.from_id} | {nick} синхронизировал Беседу с Базой данных!")

@bot.on.message(text="/gstaff")
async def gstaff_handler(message: Message):
    if message.peer_id not in ACTIVE_CHATS: return
    if not has_access(message.from_id, "Заместитель Специального Руководителя"): return
    spec_boss = "- https://vk.com/id870757778 | Misha Manlix"
    main_deputy = "- Отсутствует."
    deputy_list = [f"- https://vk.com/id{uid} | {data[1]}" for uid, data in USER_DATA.items() if data[0] == "Заместитель Специального Руководителя"]
    deputy_str = "\n".join(deputy_list[:2]) + ("\n- Отсутствует." * (2 - len(deputy_list)))
    res = f"MANLIX MANAGER | Команда Бота:\n\n| Специальный Руководитель:\n{spec_boss}\n\n| Основной зам. Спец. Руководителя:\n{main_deputy}\n\n| Зам. Спец. Руководителя:\n{deputy_str}"
    await message.answer(res)

# --- СИСТЕМНОЕ ---
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b"OK")

def run_port():
    port = int(os.environ.get("PORT", 10000))
    HTTPServer(('0.0.0.0', port), Handler).serve_forever()

threading.Thread(target=run_port, daemon=True).start()
bot.run_forever()

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

ACTIVE_CHATS = set()
MUTE_LIST = {}
LAST_MSG_IDS = {} # Защита от дублей

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
    user_rank = get_rank(user_id)
    return RANK_WEIGHT.get(user_rank, 0) >= RANK_WEIGHT.get(required_rank, 0)

async def is_muted(message: Message):
    if has_access(message.from_id, "Модератор"): return False
    if message.from_id in MUTE_LIST:
        if time.time() < MUTE_LIST[message.from_id]:
            try:
                await message.ctx_api.messages.delete(
                    cmids=[message.conversation_message_id],
                    peer_id=message.peer_id,
                    delete_for_all=True
                )
            except: pass
            return True
    return False

# --- 3. ИНИЦИАЛИЗАЦИЯ ---
bot = Bot(token=os.environ.get("TOKEN"))

# --- 4. КОМАНДА /HELP ---
@bot.on.message(text="/help")
async def help_handler(message: Message):
    # Защита от дублей сообщений (та самая "залупа" со скриншота)
    mid = message.conversation_message_id
    if LAST_MSG_IDS.get(message.peer_id) == mid: return
    LAST_MSG_IDS[message.peer_id] = mid

    if await is_muted(message): return
    if has_access(message.from_id, "Специальный Руководитель"):
        ACTIVE_CHATS.add(message.peer_id)
    
    if message.peer_id not in ACTIVE_CHATS: return

    # Отправляем строго по одному разу разными сообщениями
    await message.answer("Команды пользователей:\n/info - Официальные ресурсы\n/stats - Ваша статистика\n/getid - Получить ссылку на профиль")
    
    if has_access(message.from_id, "Модератор"):
        await message.answer("Команды модераторов:\n/kick - Исключить пользователя\n/mute - Выдать блокировку чата")
        
    if has_access(message.from_id, "Заместитель Специального Руководителя"):
        await message.answer("Команды руководства:\n/gstaff - Список высшего руководства\n/gbanpl - Выдать глобальный бан\n/gunbanpl - Снять глобальный бан")
        
    if has_access(message.from_id, "Специальный Руководитель"):
        await message.answer("Команды Спец. Руководителя:\n/sync - Синхронизация беседы")

# --- 5. КОМАНДЫ МОДЕРАЦИИ И ПОЛЬЗОВАТЕЛЕЙ ---
@bot.on.message(text="/getid")
async def getid_handler(message: Message):
    target_id = message.reply_message.from_id if message.reply_message else message.from_id
    await message.answer(f"Ссылка на профиль: [id{target_id}|vk.com/id{target_id}]")

@bot.on.message(text="/sync")
async def sync_handler(message: Message):
    if not has_access(message.from_id, "Специальный Руководитель"): return
    ACTIVE_CHATS.add(message.peer_id)
    nick = USER_DATA[message.from_id][1]
    await message.answer(f"[id{message.from_id}|{nick}] синхронизировал Беседу с Базой данных!")

# --- 6. GSTAFF (ИСПРАВЛЕН ФОРМАТ) ---
@bot.on.message(text="/gstaff")
async def gstaff_handler(message: Message):
    mid = message.conversation_message_id
    if LAST_MSG_IDS.get(message.peer_id) == mid: return
    LAST_MSG_IDS[message.peer_id] = mid

    if await is_muted(message): return
    if has_access(message.from_id, "Специальный Руководитель"):
        ACTIVE_CHATS.add(message.peer_id)
    
    if message.peer_id not in ACTIVE_CHATS: return
    if not has_access(message.from_id, "Заместитель Специального Руководителя"): return
    
    # Ссылка БЕЗ https:// — только так она будет синей в ВК
    spec_boss = "- [id870757778|Misha Manlix]"
    
    res = (
        "MANLIX MANAGER | Команда Бота:\n\n"
        "| Специальный Руководитель:\n"
        f"{spec_boss}\n\n"
        "| Основной зам. Спец. Руководителя:\n"
        "- Отсутствует.\n\n"
        "| Зам. Спец. Руководителя:\n"
        "- Отсутствует.\n"
        "- Отсутствует."
    )
    await message.answer(res)

# --- 7. СИСТЕМНОЕ ---
@bot.on.message()
async def global_handler(message: Message):
    await is_muted(message)

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b"OK")

def run_port():
    port = int(os.environ.get("PORT", 10000))
    HTTPServer(('0.0.0.0', port), Handler).serve_forever()

threading.Thread(target=run_port, daemon=True).start()
bot.run_forever()

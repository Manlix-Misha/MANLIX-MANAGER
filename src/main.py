import os
import threading
import re
import time
import json
import datetime
import traceback
from http.server import HTTPServer, BaseHTTPRequestHandler
from vkbottle.bot import Bot, Message
from vkbottle import Keyboard, KeyboardButtonColor, Text, BaseMiddleware

# --- 1. ДАННЫЕ (НЕ ИЗМЕНЯТЬ ДЛЯ RENDER) ---
USER_DATA = {
    870757778: ["Специальный Руководитель", "Misha Manlix"],
}

DB_FILE = "chats_db.json"
MUTES_FILE = "mutes.json"

def load_data(file, default):
    if os.path.exists(file):
        try:
            with open(file, "r") as f: return json.load(f)
        except: return default
    return default

def save_data(file, data):
    try:
        with open(file, "w") as f: json.dump(data, f)
    except Exception as e:
        print(f"Ошибка сохранения {file}: {e}")

ACTIVE_CHATS = set(load_data(DB_FILE, []))
ACTIVE_MUTES = load_data(MUTES_FILE, {}) # { "user_id": timestamp_end }

RANK_WEIGHT = {
    "Пользователь": 0, "Модератор": 1, "Старший Модератор": 2, 
    "Администратор": 3, "Старший Администратор": 4, "Зам. Спец. Администратора": 5,
    "Спец. Администратор": 6, "Владелец": 7, "Зам. Специального Руководителя": 8,
    "Основной зам. Специального Руководителя": 9, "Специальный Руководитель": 10
}

# --- 2. ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---
def get_rank(user_id):
    return USER_DATA.get(int(user_id), ["Пользователь"])[0]

def has_access(user_id, required_rank):
    user_rank = get_rank(user_id)
    return RANK_WEIGHT.get(user_rank, 0) >= RANK_WEIGHT.get(required_rank, 0)

def extract_id(text):
    if not text: return None
    match = re.search(r'id(\d+)', str(text))
    if match: return int(match.group(1))
    digits = re.findall(r'\d+', str(text))
    if digits: return int(digits[0])
    return None

async def check_active(message: Message):
    if int(message.from_id) == 870757778: return True
    if message.peer_id not in ACTIVE_CHATS:
        await message.answer("Владелец беседы не является командой Бота, я не буду здесь работать.")
        return False
    return True

# --- 3. ИНИЦИАЛИЗАЦИЯ И МИДЛВАР ---
bot = Bot(token=os.environ.get("TOKEN"))

class MuteMiddleware(BaseMiddleware[Message]):
    async def pre(self):
        if self.event.from_id is None: return
        uid_str = str(self.event.from_id)
        if uid_str in ACTIVE_MUTES:
            if time.time() < ACTIVE_MUTES[uid_str]:
                try:
                    await self.event.ctx_api.messages.delete(
                        cmids=[self.event.conversation_message_id],
                        peer_id=self.event.peer_id,
                        delete_for_all=True
                    )
                except Exception as e:
                    print(f"Не удалось удалить сообщение мута: {e}")
                self.stop("User is muted")
            else:
                del ACTIVE_MUTES[uid_str]
                save_data(MUTES_FILE, ACTIVE_MUTES)

bot.labeler.message_view.middlewares.append(MuteMiddleware())

# --- 4. КОМАНДЫ ---

@bot.on.message(text="/help")
async def help_handler(message: Message):
    if not await check_active(message): return
    uid = message.from_id
    
    sections = {
        "Пользователь": "/info, /stats, /getid",
        "Модератор": "/kick, /mute, /unmute",
        "Зам. Специального Руководителя": "/gstaff, /gbanpl, /gunbanpl",
        "Специальный Руководитель": "/sync"
    }
    
    res = "СПИСОК ДОСТУПНЫХ КОМАНД:\n\n"
    for rank, cmds in sections.items():
        if has_access(uid, rank):
            res += f"- {rank}:\n{cmds}\n\n"
            
    await message.answer(res)

@bot.on.message(text=["/getid", "/getid <args>"])
async def getid_handler(message: Message, args=None):
    if not await check_active(message): return
    target_id = message.reply_message.from_id if message.reply_message else message.from_id
    if args:
        ext = extract_id(args)
        if ext: target_id = ext
    await message.answer(f"Ссылка на [id{target_id}|пользователя]:\nhttps://vk.com/id{target_id}")

@bot.on.message(text="/stats")
async def stats_handler(message: Message):
    tid = message.reply_message.from_id if message.reply_message else message.from_id
    status = "Синхронизировано" if message.peer_id in ACTIVE_CHATS else "Нет связи"
    await message.answer(f"Профиль [id{tid}|пользователя]:\nРоль: {get_rank(tid)}\nЧат: {status}")

@bot.on.message(text="/sync")
async def sync_handler(message: Message):
    if int(message.from_id) != 870757778: return
    ACTIVE_CHATS.add(message.peer_id)
    save_data(DB_FILE, list(ACTIVE_CHATS))
    await message.answer("Система: Беседа успешно синхронизирована с базой данных!")

@bot.on.message(text="/gstaff")
async def gstaff_handler(message: Message):
    if not await check_active(message) or not has_access(message.from_id, "Зам. Специального Руководителя"): return
    
    staff_list = "КОМАНДА ПРОЕКТА:\n\n"
    for uid, data in USER_DATA.items():
        staff_list += f"- {data[0]}: [id{uid}|{data[1]}]\n"
    
    await message.answer(staff_list)

@bot.on.message(text=["/mute", "/mute <args>"])
async def mute_handler(message: Message, args=None):
    if not await check_active(message) or not has_access(message.from_id, "Модератор"): return
    
    target_id = message.reply_message.from_id if message.reply_message else extract_id(args)
    if not target_id: return "Укажите пользователя (реплаем или через id)!"

    time_min = 30
    reason = "Не указана"
    
    if args:
        all_nums = re.findall(r'\d+', args)
        if not message.reply_message and len(all_nums) >= 2:
            time_min = int(all_nums[1])
        elif message.reply_message and all_nums:
            time_min = int(all_nums[0])
            
        clean_reason = re.sub(r'\[.*?\]|id\d+|\d+', '', args).strip()
        if clean_reason: reason = clean_reason

    end_ts = time.time() + (time_min * 60)
    ACTIVE_MUTES[str(target_id)] = end_ts
    save_data(MUTES_FILE, ACTIVE_MUTES)
    
    date_str = datetime.datetime.fromtimestamp(end_ts + 3*3600).strftime("%H:%M:%S")
    
    kb = Keyboard(inline=True)
    kb.add(Text("Снять мут", payload={"cmd": "unmute", "target": target_id}), color=KeyboardButtonColor.POSITIVE)

    await message.answer(f"Ограничение доступа: [id{message.from_id}|Модератор] выдал мут [id{target_id}|пользователю]\nПричина: {reason}\nСрок: до {date_str} (МСК)", keyboard=kb)

@bot.on.message(text=["/unmute", "/unmute <args>"])
async def unmute_cmd(message: Message, args=None):
    if not await check_active(message) or not has_access(message.from_id, "Модератор"): return
    target_id = message.reply_message.from_id if message.reply_message else extract_id(args)
    if not target_id: return
    
    uid_str = str(target_id)
    if uid_str in ACTIVE_MUTES:
        del ACTIVE_MUTES[uid_str]
        save_data(MUTES_FILE, ACTIVE_MUTES)
        await message.answer(f"Мут для [id{target_id}|пользователя] снят.")

@bot.on.message(text=["/kick", "/kick <args>"])
async def kick_handler(message: Message, args=None):
    if not await check_active(message) or not has_access(message.from_id, "Модератор"): return
    target_id = message.reply_message.from_id if message.reply_message else extract_id(args)
    if not target_id: return
    
    try:
        await bot.api.messages.remove_chat_user(chat_id=message.peer_id - 2000000000, user_id=target_id)
        await message.answer(f"Пользователь [id{target_id}|исключен] из беседы.")
    except Exception as e:
        await message.answer(f"Ошибка исключения: {e}")

@bot.on.message(func=lambda message: getattr(message, "payload", None) is not None)
async def payload_handler(message: Message):
    if not has_access(message.from_id, "Модератор"): return
    try:
        pl = json.loads(message.payload)
        if pl.get("cmd") == "unmute":
            tid = str(pl.get("target"))
            if tid in ACTIVE_MUTES: 
                del ACTIVE_MUTES[tid]
                save_data(MUTES_FILE, ACTIVE_MUTES)
            await message.answer(f"Модератор снял мут с пользователя [id{tid}|через кнопку]")
    except Exception:
        print(traceback.format_exc())

# --- СЕРВЕР ---
class Handler(BaseHTTPRequestHandler):
    def do_GET(self): 
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"SERVER IS ALIVE")

def run_server():
    server_address = ('0.0.0.0', int(os.environ.get("PORT", 10000)))
    httpd = HTTPServer(server_address, Handler)
    httpd.serve_forever()

threading.Thread(target=run_server, daemon=True).start()
bot.run_forever()

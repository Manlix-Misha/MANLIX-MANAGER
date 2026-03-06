import os
import threading
import re
import json
import base64
import aiohttp
import datetime
import random
from http.server import HTTPServer, BaseHTTPRequestHandler

from vkbottle.bot import Bot, Message, MessageEvent
from vkbottle import Keyboard, KeyboardButtonColor, Text, GroupEventType, BaseMiddleware

# ==============================
# НАСТРОЙКИ (НЕ МЕНЯЕМ ДЛЯ RENDER)
# ==============================

TOKEN = os.environ.get("TOKEN")

bot = Bot(token=TOKEN)

DATABASE = {"chats": {}}
ECONOMY = {}
PUNISHMENTS = {
    "gbans_status": {},
    "gbans_pl": {},
    "bans": {},
    "warns": {}
}

TZ_MSK = datetime.timezone(datetime.timedelta(hours=3))

# ==============================
# РАНГИ
# ==============================

RANK_WEIGHT = {
    "Пользователь": 0,
    "Модератор": 1,
    "Старший Модератор": 2,
    "Администратор": 3,
    "Старший Администратор": 4,
    "Зам. Спец. Администратора": 5,
    "Спец. Администратор": 6,
    "Владелец": 7,
    "Зам. Спец. Руководителя": 8,
    "Основной Зам. Спец. Руководителя": 9,
    "Специальный Руководитель": 10
}

# ==============================
# ПОЛУЧЕНИЕ ID
# ==============================

async def get_target_id(m: Message, args):

    if m.reply_message:
        return m.reply_message.from_id

    if not args:
        return None

    match = re.search(r"(?:id|\[id|vk\.com\/id|vk\.com\/)(\d+)", args)

    if match:
        return int(match.group(1))

    raw = args.split("/")[-1]

    try:
        res = await bot.api.utils.resolve_screen_name(screen_name=raw)
        if res:
            return res.object_id
    except:
        pass

    num = re.sub(r"\D", "", args)

    if num:
        return int(num)

    return None

# ==============================
# ПОЛУЧЕНИЕ РОЛИ
# ==============================

def get_user_info(peer_id, user_id):

    if user_id == 870757778:
        return "Специальный Руководитель", "Misha Manlix"

    staff = DATABASE["chats"].get(str(peer_id), {}).get("staff", {})

    return staff.get(str(user_id), ["Пользователь", None])

# ==============================
# ПРОВЕРКА ПРАВ
# ==============================

async def check_access(m: Message, role):

    rank, _ = get_user_info(m.peer_id, m.from_id)

    if RANK_WEIGHT.get(rank, 0) < RANK_WEIGHT.get(role, 0):
        await m.answer("Недостаточно прав!")
        return False

    return True

# ==============================
# MIDDLEWARE
# ==============================

class ChatMiddleware(BaseMiddleware[Message]):

    async def pre(self):

        if not self.event.from_id:
            return

        uid = str(self.event.from_id)
        pid = str(self.event.peer_id)

        if pid not in DATABASE["chats"]:
            return

        # мут

        mutes = DATABASE["chats"][pid].get("mutes", {})

        if uid in mutes:

            if datetime.datetime.now(TZ_MSK).timestamp() < mutes[uid]:

                try:
                    await bot.api.messages.delete(
                        peer_id=self.event.peer_id,
                        conversation_message_ids=[self.event.conversation_message_id],
                        delete_for_all=True
                    )
                except:
                    pass

                self.stop()

bot.labeler.message_view.register_middleware(ChatMiddleware)

# ==============================
# START
# ==============================

@bot.on.message(text="/start")
async def start(m: Message):

    if not await check_access(m, "Специальный Руководитель"):
        return

    pid = str(m.peer_id)

    DATABASE["chats"][pid] = {
        "title": "Чат",
        "staff": {
            "870757778": ["Специальный Руководитель", "Misha Manlix"]
        },
        "mutes": {}
    }

    await m.answer("Вы успешно активировали Беседу.")

# ==============================
# HELP
# ==============================

@bot.on.message(text="/help")
async def help_cmd(m: Message):

    await m.answer(
        "Команды:\n"
        "/info\n"
        "/stats\n"
        "/getid\n"
        "/staff\n"
        "/mute\n"
        "/kick"
    )

# ==============================
# GETID
# ==============================

@bot.on.message(text=["/getid", "/getid <args>"])
async def getid(m: Message, args=None):

    t = await get_target_id(m, args) or m.from_id

    await m.answer(
        f"Оригинальная ссылка [id{t}|пользователя]: https://vk.com/id{t}"
    )

# ==============================
# MUTE
# ==============================

@bot.on.message(text=["/mute <args>"])
async def mute(m: Message, args):

    if not await check_access(m, "Модератор"):
        return

    t = await get_target_id(m, args)

    if not t:
        return

    mins = int(args.split()[1])

    until = datetime.datetime.now(TZ_MSK).timestamp() + mins * 60

    pid = str(m.peer_id)

    DATABASE["chats"][pid]["mutes"][str(t)] = until

    dt = datetime.datetime.fromtimestamp(until, TZ_MSK).strftime("%d/%m/%Y %H:%M:%S")

    kb = Keyboard(inline=True)

    kb.add(
        Text("Снять мут", {"cmd": "unmute", "u": t}),
        color=KeyboardButtonColor.POSITIVE
    )

    kb.add(
        Text("Очистить", {"cmd": "clear"}),
        color=KeyboardButtonColor.NEGATIVE
    )

    await m.answer(
        f"[id{m.from_id}|Модератор MANLIX] выдал мут [id{t}|пользователю]\n"
        f"Мут до: {dt}",
        keyboard=kb.get_json()
    )

# ==============================
# КНОПКИ
# ==============================

@bot.on.raw_event(GroupEventType.MESSAGE_EVENT, dataclass=MessageEvent)
async def buttons(event: MessageEvent):

    payload = event.payload

    if not payload:
        return

    pid = str(event.peer_id)

    if payload["cmd"] == "unmute":

        t = str(payload["u"])

        if t in DATABASE["chats"][pid]["mutes"]:
            del DATABASE["chats"][pid]["mutes"][t]

        await bot.api.messages.edit(
            peer_id=event.peer_id,
            conversation_message_id=event.conversation_message_id,
            message=f"[id{event.user_id}|Модератор MANLIX] снял мут [id{t}|пользователю]"
        )

    if payload["cmd"] == "clear":

        await bot.api.messages.delete(
            peer_id=event.peer_id,
            conversation_message_ids=[event.conversation_message_id],
            delete_for_all=True
        )

# ==============================
# BAN
# ==============================

@bot.on.message(text=["/ban <args>"])
async def ban(m: Message, args):

    if not await check_access(m, "Старший Модератор"):
        return

    t = await get_target_id(m, args)

    pid = str(m.peer_id)

    if pid not in PUNISHMENTS["bans"]:
        PUNISHMENTS["bans"][pid] = {}

    PUNISHMENTS["bans"][pid][str(t)] = {
        "admin": m.from_id,
        "date": datetime.datetime.now(TZ_MSK).timestamp()
    }

    try:
        await bot.api.messages.remove_chat_user(
            chat_id=m.peer_id - 2000000000,
            user_id=t
        )
    except:
        pass

    await m.answer(
        f"[id{m.from_id}|Модератор MANLIX] заблокировал [id{t}|пользователя]"
    )

# ==============================
# GBAN
# ==============================

@bot.on.message(text=["/gban <args>"])
async def gban(m: Message, args):

    if not await check_access(m, "Зам. Спец. Руководителя"):
        return

    t = await get_target_id(m, args)

    PUNISHMENTS["gbans_status"][str(t)] = {
        "admin": m.from_id,
        "date": datetime.datetime.now(TZ_MSK).timestamp()
    }

    try:
        await bot.api.messages.remove_chat_user(
            chat_id=m.peer_id - 2000000000,
            user_id=t
        )
    except:
        pass

    await m.answer(
        f"[id{m.from_id}|Специальный Руководитель] занес [id{t}|пользователя] в глобальный бан"
    )

# ==============================
# СИСТЕМА КИКА ПРИ ДОБАВЛЕНИИ
# ==============================

@bot.on.message()
async def join_handler(m: Message):

    if not m.action:
        return

    if m.action.type.value == "chat_invite_user":

        uid = str(m.action.member_id)

        if uid in PUNISHMENTS["gbans_status"]:

            try:
                await bot.api.messages.remove_chat_user(
                    chat_id=m.peer_id - 2000000000,
                    user_id=int(uid)
                )
            except:
                pass

# ==============================
# HTTP SERVER ДЛЯ RENDER
# ==============================

class Handler(BaseHTTPRequestHandler):

    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")

# ==============================
# ЗАПУСК
# ==============================

if __name__ == "__main__":

    threading.Thread(
        target=lambda: HTTPServer(
            ('0.0.0.0', int(os.environ.get("PORT", 10000))),
            Handler
        ).serve_forever(),
        daemon=True
    ).start()

    bot.run_forever()

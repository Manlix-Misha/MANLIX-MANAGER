import os
import asyncio
from vkbottle.bot import Bot, Message

# Бот берет токен из настроек Render
bot = Bot(token=os.getenv("vk1.a.zO2-xuoj9MmSPQh_BW4OfZMyUnGiNi51dcVOi8p9LftPJYnlDZ5XACnWU2ocXcjwtTGhml3s3HY7Wz_67ZToAO8G9Um9MYozF2CvpqLgsC2U8rWPmSuRwUqGwI0u95SrWqyjO7d6hMgH1iHjC4swP8d2p_-6EI44sxY9zLFMPaAyfIc-9CE4pwRjINH6PVL9IzXipRJUWUAMallsQ0oa1g"))

# КОМАНДЫ ПОЛЬЗОВАТЕЛЕЙ
@bot.on.message(text="/info")
async def info(message: Message):
    await message.answer("🤖 MANLIX MANAGER — Официальный бот проекта.")

@bot.on.message(text="/getid")
async def get_id(message: Message):
    uid = message.reply_message.from_id if message.reply_message else message.from_id
    await message.answer(f"🆔 ID: {uid}")

# КОМАНДЫ МОДЕРАТОРОВ
@bot.on.message(text="/kick")
async def kick(message: Message):
    if not message.reply_message:
        return await message.answer("⚠️ Ответьте на сообщение игрока.")
    await bot.api.messages.remove_chat_user(chat_id=message.chat_id, member_id=message.reply_message.from_id)
    await message.answer("✅ Игрок исключен.")

# КОМАНДЫ ВЛАДЕЛЬЦА
@bot.on.message(text="/pin")
async def pin(message: Message):
    if message.reply_message:
        await bot.api.messages.pin(peer_id=message.peer_id, message_id=message.reply_message.id)
        await message.answer("📌 Сообщение закреплено.")

if __name__ == "__main__":
    bot.run_forever()

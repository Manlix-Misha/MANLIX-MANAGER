import os
import sys

# Проверяем, видит ли Render наш токен
token = os.getenv("TOKEN")

if not token:
    print("❌ ОШИБКА: Переменная TOKEN не найдена в настройках Render!")
    sys.exit(1)
else:
    print(f"✅ Токен найден, длина: {len(token)} символов")

try:
    from vkbottle.bot import Bot
    print("✅ Библиотека vkbottle успешно загружена")
    bot = Bot(token=token)
    print("🚀 Попытка запуска бота...")
    bot.run_forever()
except Exception as e:
    print(f"❌ КРИТИЧЕСКАЯ ОШИБКА ПРИ ЗАПУСКЕ: {e}")

if __name__ == "__main__":
    bot.run_forever()

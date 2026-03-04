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


import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")

def run_port():
    server = HTTPServer(('0.0.0.0', 10000), Handler)
    server.serve_forever()

# Запускаем "пустой" порт в отдельном потоке
threading.Thread(target=run_port, daemon=True).start()

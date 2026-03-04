import os
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from vkbottle.bot import Bot, Message

# 1. Сначала ПОРТ для Render
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")

def run_port():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(('0.0.0.0', port), Handler)
    server.serve_forever()

threading.Thread(target=run_port, daemon=True).start()
print("✅ Порт-обманка запущен")

# 2. Потом твой БОТ
token = os.environ.get("TOKEN")
bot = Bot(token=token)

@bot.on.message(text="Привет")
async def hi_handler(message: Message):
    await message.answer("Привет! Я работаю!")

print("🚀 Бот запускается...")
bot.run_forever()

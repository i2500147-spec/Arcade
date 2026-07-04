import os
import re
import logging
import threading
from datetime import datetime, timedelta, timezone

import httpx
from flask import Flask
from dotenv import load_dotenv

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    ConversationHandler,
    filters,
)

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
OWNER_ID = int(os.getenv("OWNER_ID", "0"))
SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
PORT = int(os.getenv("PORT", "10000"))

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ============================================================
# FLASK (для Render Web Service)
# ============================================================

flask_app = Flask(__name__)

@flask_app.route("/")
@flask_app.route("/health")
def health():
    return "OK", 200

def run_flask():
    flask_app.run(host="0.0.0.0", port=PORT)

# ============================================================
# ДАЛЬШЕ ВЕСЬ КОД КЛОДА (бот)
# ============================================================

def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is required")

    # Запускаем Flask
    threading.Thread(target=run_flask, daemon=True).start()
    logger.info(f"Flask on port {PORT}")

    # Запускаем бота
    app = Application.builder().token(BOT_TOKEN).build()
    # ... обработчики ...
    app.run_polling()

if __name__ == "__main__":
    main()

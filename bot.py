import os
import re
import time
import logging
import asyncio
import threading
import hashlib
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler

import httpx
from flask import Flask, jsonify
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# ============================================================
# CONFIG
# ============================================================

BOT_TOKEN = os.environ.get("BOT_TOKEN")
ADMIN_IDS = set(int(x) for x in os.environ.get("ADMIN_IDS", "").split(",") if x.strip().isdigit())
ADMIN_CHAT_ID = os.environ.get("ADMIN_CHAT_ID")
CHAT_LINK = os.environ.get("CHAT_LINK", "")
REQUIRE_SUBSCRIPTION = os.environ.get("REQUIRE_SUBSCRIPTION", "false").lower() == "true"
SUBSCRIPTION_CHAT_ID = os.environ.get("SUBSCRIPTION_CHAT_ID")
OWNER_ID = int(os.environ.get("OWNER_ID", "0") or 0)
SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

MAPS = ["Sandstone", "Rust", "Province", "Breeze", "Dune", "Zone 7", "Hanami"]
LOBBY_COUNT = 6
LOBBY_SIZE = 10

PREMIUM_PRICES = {
    "1_day": {"label": "1 день", "stars": 50, "days": 1},
    "1_week": {"label": "1 неделя", "stars": 350, "days": 7},
    "1_month": {"label": "1 месяц", "stars": 1000, "days": 30},
}

RANKS = [
    (0, 799, "🥉", 1), (800, 899, "🥉", 2), (900, 999, "🥉", 3),
    (1000, 1099, "🥈", 4), (1100, 1199, "🥈", 5), (1200, 1299, "🥈", 6),
    (1300, 1399, "🥇", 7), (1400, 1499, "🥇", 8), (1500, 1699, "💎", 9),
    (1700, 999999, "👑", 10),
]

# ============================================================
# LOGGING
# ============================================================

os.makedirs("logs", exist_ok=True)
logger = logging.getLogger("strange_faceit")
logger.setLevel(logging.INFO)
handler = RotatingFileHandler("logs/bot.log", maxBytes=10*1024*1024, backupCount=5, encoding="utf-8")
handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
logger.addHandler(handler)
logger.addHandler(logging.StreamHandler())

BOT_START_TIME = time.time()

# ============================================================
# SUPABASE HELPERS
# ============================================================

_http_client = None

def get_client():
    global _http_client
    if _http_client is None:
        _http_client = httpx.AsyncClient(
            base_url=f"{SUPABASE_URL}/rest/v1",
            headers={"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}", "Content-Type": "application/json"},
            timeout=30.0,
        )
    return _http_client

async def sb_select(table, params=None):
    try:
        r = await get_client().get(f"/{table}", params=params or {})
        if r.status_code < 400:
            return r.json()
        return []
    except:
        return []

async def sb_insert(table, data):
    try:
        r = await get_client().post(f"/{table}", json=data, headers={"Prefer": "return=representation"})
        if r.status_code < 400:
            return r.json()
        return []
    except:
        return []

async def sb_update(table, params, data):
    try:
        r = await get_client().patch(f"/{table}", params=params, json=data, headers={"Prefer": "return=representation"})
        if r.status_code < 400:
            return r.json()
        return []
    except:
        return []

async def sb_delete(table, params):
    try:
        r = await get_client().delete(f"/{table}", params=params)
        return r.status_code < 400
    except:
        return False

async def get_player_by_telegram(tid):
    rows = await sb_select("players", {"Telegram_id": f"eq.{tid}", "limit": "1"})
    return rows[0] if rows else None

async def get_player_by_nick(nick):
    rows = await sb_select("players", {"nick": f"eq.{nick}", "limit": "1"})
    return rows[0] if rows else None

async def get_player_by_game_id(gid):
    rows = await sb_select("players", {"game_id": f"eq.{gid}", "limit": "1"})
    return rows[0] if rows else None

def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

def calc_rank(elo):
    for low, high, emoji, rank in RANKS:
        if low <= elo <= high:
            return rank, emoji
    return 1, "🥉"

# ============================================================
# SUBSCRIPTION CHECK
# ============================================================

async def check_subscription(context, user_id):
    if not REQUIRE_SUBSCRIPTION or not SUBSCRIPTION_CHAT_ID:
        return True
    try:
        member = await context.bot.get_chat_member(SUBSCRIPTION_CHAT_ID, user_id)
        return member.status in ("member", "administrator", "creator")
    except:
        return False

def subscription_prompt_kb():
    buttons = []
    if CHAT_LINK:
        buttons.append([InlineKeyboardButton("📢 Подписаться", url=CHAT_LINK)])
    buttons.append([InlineKeyboardButton("✅ Я подписался", callback_data="check_sub")])
    return InlineKeyboardMarkup(buttons)

# ============================================================
# STATE
# ============================================================

USER_STATE = {}
TICKETS = {}
TICKET_COUNTER = 0

def set_state(user_id, flow, step, data=None):
    USER_STATE[user_id] = {"flow": flow, "step": step, "data": data or {}}

def get_state(user_id):
    return USER_STATE.get(user_id)

def clear_state(user_id):
    USER_STATE.pop(user_id, None)

# ============================================================
# KEYBOARDS
# ============================================================

def entry_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔑 Вход", callback_data="login_start")],
        [InlineKeyboardButton("📝 Регистрация", callback_data="register_start")],
        [InlineKeyboardButton("🆘 Поддержка", callback_data="support")],
    ])

def main_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔍 Найти матч", callback_data="find_match"),
         InlineKeyboardButton("🎉 Пати", callback_data="party_menu")],
        [InlineKeyboardButton("👤 Профиль", callback_data="profile"),
         InlineKeyboardButton("🏆 Топ", callback_data="leaderboard")],
        [InlineKeyboardButton("📊 Статистика", callback_data="stats"),
         InlineKeyboardButton("📝 История", callback_data="history")],
        [InlineKeyboardButton("📢 Жалобы", callback_data="reports_menu"),
         InlineKeyboardButton("💎 Премиум", callback_data="premium_menu")],
        [InlineKeyboardButton("🆘 Поддержка", callback_data="support")],
    ])

def back_kb(callback="back_to_menu"):
    return InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data=callback)]])

def support_reply_kb(ticket_id):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✍️ Ответить", callback_data=f"support_reply_{ticket_id}")]
    ])

def support_close_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Ок", callback_data="support_ok"),
         InlineKeyboardButton("🔒 Закрыть вопрос", callback_data="support_close")]
    ])

# ============================================================
# REGISTER / LOGIN FLOW
# ============================================================

GAME_ID_RE = re.compile(r"^\d{8,15}$")
NICK_RE = re.compile(r"^[A-Za-z0-9_]{2,32}$")
PASSWORD_RE = re.compile(r"^[A-Za-z0-9]{8,}$")

async def cmd_start(update, context):
    user = update.effective_user
    clear_state(user.id)

    if not await check_subscription(context, user.id):
        await update.message.reply_text(
            "📢 Чтобы пользоваться ботом, подпишись на наш чат:",
            reply_markup=subscription_prompt_kb()
        )
        return

    player = await get_player_by_telegram(user.id)
    if player:
        await update.message.reply_text(f"С возвращением, {player.get('nick')}!", reply_markup=main_kb())
        return

    await update.message.reply_text("Добро пожаловать в Strange Faceit!", reply_markup=entry_kb())

async def cb_register_start(update, context):
    q = update.callback_query
    await q.answer()
    user_id = q.from_user.id
    if await get_player_by_telegram(user_id):
        await q.edit_message_text("Вы уже зарегистрированы.", reply_markup=back_kb())
        return
    set_state(user_id, "register", "await_nick")
    await q.edit_message_text("📝 Регистрация\n\nВведите ваш игровой ник (2-32 символа):")

async def cb_login_start(update, context):
    q = update.callback_query
    await q.answer()
    user_id = q.from_user.id
    if await get_player_by_telegram(user_id):
        await q.edit_message_text("Вы уже вошли.", reply_markup=back_kb())
        return
    set_state(user_id, "login", "await_nick")
    await q.edit_message_text("🔑 Вход\n\nВведите ваш игровой ник:")

async def handle_text_message(update, context):
    user = update.effective_user
    state = get_state(user.id)
    if not state:
        return

    flow, step = state["flow"], state["step"]
    text = update.message.text.strip()

    if flow == "register":
        await handle_register_step(update, context, step, text)
    elif flow == "login":
        await handle_login_step(update, context, step, text)
    elif flow == "support":
        await handle_support_message(update, context, text)

async def handle_register_step(update, context, step, text):
    user = update.effective_user
    state = get_state(user.id)

    if step == "await_nick":
        if not NICK_RE.match(text):
            await update.message.reply_text("❌ Ник должен быть 2-32 символа (латиница, цифры, _). Попробуйте:")
            return
        if await get_player_by_nick(text):
            await update.message.reply_text("❌ Этот ник уже занят. Введите другой:")
            return
        state["data"]["nick"] = text
        state["step"] = "await_game_id"
        await update.message.reply_text("Введите ваш ID в Standoff 2 (8-15 цифр):")

    elif step == "await_game_id":
        if not GAME_ID_RE.match(text):
            await update.message.reply_text("❌ ID должен быть 8-15 цифр. Попробуйте:")
            return
        if await get_player_by_game_id(text):
            await update.message.reply_text("❌ Этот ID уже зарегистрирован.\n\nЕсли это ваш аккаунт, попробуйте Вход или обратитесь в поддержку.")
            return
        state["data"]["game_id"] = text
        state["step"] = "await_password"
        await update.message.reply_text("Придумайте пароль (минимум 8 символов, латиница и цифры):")

    elif step == "await_password":
        if not PASSWORD_RE.match(text):
            await update.message.reply_text("❌ Пароль должен быть минимум 8 символов (латиница и цифры). Попробуйте:")
            return
        state["data"]["password"] = hash_password(text)
        
        # Регистрируем
        data = state["data"]
        await sb_insert("players", {
            "Telegram_id": user.id,
            "game_id": data["game_id"],
            "nick": data["nick"],
            "password": data["password"],
            "approved": True,
            "elo": 1000,
            "wins": 0,
            "losses": 0,
            "matches": 0,
            "mvp": 0,
        })
        clear_state(user.id)
        await update.message.reply_text(
            f"✅ Регистрация завершена!\n\n"
            f"Ник: {data['nick']}\n"
            f"ID: {data['game_id']}\n\n"
            f"Добро пожаловать в Strange Faceit!",
            reply_markup=main_kb()
        )

async def handle_login_step(update, context, step, text):
    user = update.effective_user
    state = get_state(user.id)

    if step == "await_nick":
        player = await get_player_by_nick(text)
        if not player:
            await update.message.reply_text("❌ Игрок с таким ником не найден. Попробуйте снова или зарегистрируйтесь:")
            return
        state["data"]["player"] = player
        state["step"] = "await_game_id"
        await update.message.reply_text("Введите ваш ID в Standoff 2:")

    elif step == "await_game_id":
        player = state["data"]["player"]
        if player.get("game_id") != text:
            await update.message.reply_text("❌ ID не совпадает с ником. Попробуйте снова:")
            return
        state["step"] = "await_password"
        await update.message.reply_text("Введите пароль:")

    elif step == "await_password":
        player = state["data"]["player"]
        if player.get("password") != hash_password(text):
            await update.message.reply_text("❌ Неверный пароль. Попробуйте снова:")
            return
        clear_state(user.id)
        await update.message.reply_text(f"✅ Вход выполнен! С возвращением, {player.get('nick')}!", reply_markup=main_kb())

# ============================================================
# SUPPORT
# ============================================================

async def cb_support(update, context):
    q = update.callback_query
    await q.answer()
    user_id = q.from_user.id
    player = await get_player_by_telegram(user_id)
    if not player:
        await q.edit_message_text("Сначала зарегистрируйтесь!", reply_markup=back_kb())
        return
    set_state(user_id, "support", "await_message")
    await q.edit_message_text("🆘 Поддержка\n\nОпишите вашу проблему одним сообщением:")

async def handle_support_message(update, context, text):
    user = update.effective_user
    state = get_state(user.id)
    player = await get_player_by_telegram(user.id)
    
    global TICKET_COUNTER
    TICKET_COUNTER += 1
    ticket_id = TICKET_COUNTER
    
    TICKETS[ticket_id] = {
        "user_id": user.id,
        "nick": player.get("nick"),
        "message": text,
        "status": "open"
    }
    
    clear_state(user.id)
    await update.message.reply_text("✅ Ваш запрос отправлен администраторам. Ожидайте ответа.")
    
    if ADMIN_CHAT_ID:
        await context.bot.send_message(
            ADMIN_CHAT_ID,
            f"🆘 Новый запрос в поддержку #{ticket_id}\n\n"
            f"👤 Ник: {player.get('nick')}\n"
            f"🆔 ID: {player.get('game_id')}\n"
            f"📝 Сообщение:\n{text}",
            reply_markup=support_reply_kb(ticket_id)
        )

async def cb_support_reply(update, context):
    q = update.callback_query
    await q.answer()
    if q.from_user.id not in ADMIN_IDS and q.from_user.id != OWNER_ID:
        await q.answer("Нет прав", show_alert=True)
        return
    
    ticket_id = int(q.data.split("_")[2])
    ticket = TICKETS.get(ticket_id)
    if not ticket or ticket["status"] == "closed":
        await q.edit_message_text("⚠️ Запрос уже закрыт.")
        return
    
    await q.edit_message_text(
        f"✍️ Ответ на запрос #{ticket_id}\n\n"
        f"Пользователь: {ticket['nick']}\n"
        f"Вопрос: {ticket['message']}\n\n"
        f"Введите ваш ответ:"
    )
    set_state(q.from_user.id, "admin_reply", "await_reply", {"ticket_id": ticket_id})

async def handle_admin_reply(update, context):
    user = update.effective_user
    state = get_state(user.id)
    if not state or state["flow"] != "admin_reply":
        return
    
    ticket_id = state["data"]["ticket_id"]
    ticket = TICKETS.get(ticket_id)
    if not ticket:
        await update.message.reply_text("❌ Запрос не найден.")
        return
    
    reply_text = update.message.text.strip()
    
    # Отправляем ответ пользователю
    await context.bot.send_message(
        ticket["user_id"],
        f"📩 Ответ администратора на ваш запрос #{ticket_id}\n\n"
        f"Администратор: @{user.username or 'администратор'}\n"
        f"Ответ: {reply_text}",
        reply_markup=support_close_kb()
    )
    
    clear_state(user.id)
    await update.message.reply_text("✅ Ответ отправлен пользователю.")
    
    # Отправляем админу подтверждение
    await context.bot.send_message(
        ADMIN_CHAT_ID,
        f"✅ Ответ на запрос #{ticket_id} отправлен.\n\n"
        f"Текст: {reply_text}",
        reply_markup=support_close_kb()
    )

async def cb_support_ok(update, context):
    q = update.callback_query
    await q.answer()
    await q.edit_message_text("✅ Принято. Спасибо!")

async def cb_support_close(update, context):
    q = update.callback_query
    await q.answer()
    # Пытаемся найти тикет по сообщению
    for tid, ticket in TICKETS.items():
        if ticket["user_id"] == q.from_user.id and ticket["status"] == "open":
            ticket["status"] = "closed"
            await q.edit_message_text("🔒 Вопрос закрыт.")
            return
    await q.edit_message_text("⚠️ Вопрос уже закрыт.")

# ============================================================
# ADMIN: RESET PASSWORD
# ============================================================

async def cb_admin_panel(update, context):
    q = update.callback_query
    await q.answer()
    if q.from_user.id not in ADMIN_IDS and q.from_user.id != OWNER_ID:
        await q.answer("Нет прав", show_alert=True)
        return
    
    await q.edit_message_text(
        "👑 АДМИН-ПАНЕЛЬ\n\nВыберите действие:",
        reply_markup=admin_kb()
    )

def admin_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Сброс пароля", callback_data="admin_reset_password")],
        [InlineKeyboardButton("📊 Аналитика", callback_data="admin_analytics")],
        [InlineKeyboardButton("📝 История матчей", callback_data="admin_history")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="back_to_menu")],
    ])

async def cb_admin_reset_password(update, context):
    q = update.callback_query
    await q.answer()
    if q.from_user.id not in ADMIN_IDS and q.from_user.id != OWNER_ID:
        await q.answer("Нет прав", show_alert=True)
        return
    
    players = await sb_select("players", {"approved": "eq.true", "order": "nick.asc"})
    if not players:
        await q.edit_message_text("Нет зарегистрированных игроков.", reply_markup=back_kb())
        return
    
    buttons = []
    for p in players[:20]:
        buttons.append([InlineKeyboardButton(
            f"@{p['nick']}",
            callback_data=f"reset_pass_{p['Telegram_id']}"
        )])
    buttons.append([InlineKeyboardButton("⬅️ Назад", callback_data="admin_back")])
    
    await q.edit_message_text(
        "🔄 СБРОС ПАРОЛЯ\n\nВыберите игрока:",
        reply_markup=InlineKeyboardMarkup(buttons)
    )

async def cb_reset_password(update, context):
    q = update.callback_query
    await q.answer()
    if q.from_user.id not in ADMIN_IDS and q.from_user.id != OWNER_ID:
        await q.answer("Нет прав", show_alert=True)
        return
    
    tid = int(q.data.split("_")[2])
    player = await get_player_by_telegram(tid)
    if not player:
        await q.edit_message_text("❌ Игрок не найден.")
        return
    
    await sb_update("players", {"Telegram_id": f"eq.{tid}"}, {"password": None})
    await context.bot.send_message(
        tid,
        "🔄 Ваш пароль был сброшен администратором.\n\n"
        "Введите новый пароль (минимум 8 символов, латиница и цифры):"
    )
    set_state(tid, "reset_password", "await_password", {"nick": player["nick"]})
    
    await q.edit_message_text(f"✅ Пароль для @{player['nick']} сброшен.")

async def handle_reset_password(update, context):
    user = update.effective_user
    state = get_state(user.id)
    if not state or state["flow"] != "reset_password":
        return
    
    text = update.message.text.strip()
    if not PASSWORD_RE.match(text):
        await update.message.reply_text("❌ Пароль должен быть минимум 8 символов (латиница и цифры). Попробуйте:")
        return
    
    await sb_update("players", {"Telegram_id": f"eq.{user.id}"}, {"password": hash_password(text)})
    clear_state(user.id)
    await update.message.reply_text("✅ Пароль успешно обновлён!", reply_markup=main_kb())

# ============================================================
# PLACEHOLDERS
# ============================================================

async def cb_back_to_menu(update, context):
    q = update.callback_query
    await q.answer()
    clear_state(q.from_user.id)
    player = await get_player_by_telegram(q.from_user.id)
    if player:
        await q.edit_message_text("Главное меню:", reply_markup=main_kb())
    else:
        await q.edit_message_text("Выберите действие:", reply_markup=entry_kb())

async def cb_profile(update, context):
    q = update.callback_query
    await q.answer()
    player = await get_player_by_telegram(q.from_user.id)
    if not player:
        await q.edit_message_text("Профиль не найден.", reply_markup=back_kb())
        return
    rank, emoji = calc_rank(player.get("elo", 1000))
    text = (
        f"👤 Профиль\n\n"
        f"Ник: {player.get('nick')}\n"
        f"ID: {player.get('game_id')}\n"
        f"Ранг: {emoji} #{rank}\n"
        f"ELO: {player.get('elo', 1000)}\n"
        f"Матчи: {player.get('matches', 0)}\n"
        f"Победы: {player.get('wins', 0)}\n"
        f"Поражения: {player.get('losses', 0)}\n"
        f"MVP: {player.get('mvp', 0)}"
    )
    await q.edit_message_text(text, reply_markup=back_kb())

async def cb_leaderboard(update, context):
    q = update.callback_query
    await q.answer()
    top = await sb_select("players", {"order": "elo.desc", "limit": "10", "approved": "eq.true"})
    if not top:
        await q.edit_message_text("Топ пуст.", reply_markup=back_kb())
        return
    lines = ["🏆 Топ игроков\n"]
    for i, p in enumerate(top, 1):
        rank, emoji = calc_rank(p.get("elo", 1000))
        lines.append(f"{i}. {p.get('nick')} — {p.get('elo')} ELO {emoji}")
    await q.edit_message_text("\n".join(lines), reply_markup=back_kb())

# ============================================================
# HEALTH CHECK
# ============================================================

flask_app = Flask(__name__)

@flask_app.route("/health")
def health():
    return jsonify({"status": "ok", "uptime": round(time.time() - BOT_START_TIME, 1)})

def run_flask():
    flask_app.run(host="0.0.0.0", port=8080)

# ============================================================
# MAIN
# ============================================================

def ensure_event_loop():
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())

def main():
    ensure_event_loop()
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is not set")
    if not SUPABASE_URL or not SUPABASE_KEY:
        raise RuntimeError("SUPABASE_URL / SUPABASE_KEY are not set")

    threading.Thread(target=run_flask, daemon=True).start()

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))

    app.add_handler(CallbackQueryHandler(cb_check_sub, pattern="^check_sub$"))
    app.add_handler(CallbackQueryHandler(cb_back_to_menu, pattern="^back_to_menu$"))
    app.add_handler(CallbackQueryHandler(cb_support, pattern="^support$"))

    app.add_handler(CallbackQueryHandler(cb_register_start, pattern="^register_start$"))
    app.add_handler(CallbackQueryHandler(cb_login_start, pattern="^login_start$"))
    app.add_handler(CallbackQueryHandler(cb_support_reply, pattern="^support_reply_\\d+$"))
    app.add_handler(CallbackQueryHandler(cb_support_ok, pattern="^support_ok$"))
    app.add_handler(CallbackQueryHandler(cb_support_close, pattern="^support_close$"))
    app.add_handler(CallbackQueryHandler(cb_admin_panel, pattern="^admin_panel$"))
    app.add_handler(CallbackQueryHandler(cb_admin_reset_password, pattern="^admin_reset_password$"))
    app.add_handler(CallbackQueryHandler(cb_reset_password, pattern="^reset_pass_\\d+$"))
    app.add_handler(CallbackQueryHandler(cb_profile, pattern="^profile$"))
    app.add_handler(CallbackQueryHandler(cb_leaderboard, pattern="^leaderboard$"))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_message))

    logger.info("🤖 Strange Faceit запущен!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()

import os
import re
import asyncio
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
    PreCheckoutQueryHandler,
)

load_dotenv()

# ============================================================
# CONFIG
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
OWNER_ID = int(os.getenv("OWNER_ID", "0"))
SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
PORT = int(os.getenv("PORT", "10000"))

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ============================================================
# EVENT LOOP FIX (для Python 3.14+)
# ============================================================

def ensure_event_loop():
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

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
# SUPABASE HELPERS
# ============================================================

SB_HEADERS = {
    "apikey": SUPABASE_KEY or "",
    "Authorization": f"Bearer {SUPABASE_KEY}" if SUPABASE_KEY else "",
    "Content-Type": "application/json",
}

def sb_client():
    return httpx.Client(base_url=f"{SUPABASE_URL}/rest/v1", headers=SB_HEADERS, timeout=15)

def get_player_by_tg(tg_id: int):
    try:
        with sb_client() as c:
            r = c.get("/players", params={"Telegram_id": f"eq.{tg_id}"})
            r.raise_for_status()
            data = r.json()
            return data[0] if data else None
    except Exception as e:
        logger.error(f"get_player_by_tg error: {e}")
        return None

def get_player_by_nick(nick: str):
    try:
        with sb_client() as c:
            r = c.get("/players", params={"nick": f"eq.{nick}"})
            r.raise_for_status()
            data = r.json()
            return data[0] if data else None
    except Exception as e:
        logger.error(f"get_player_by_nick error: {e}")
        return None

def get_player_by_game_id(game_id: str):
    try:
        with sb_client() as c:
            r = c.get("/players", params={"game_id": f"eq.{game_id}"})
            r.raise_for_status()
            data = r.json()
            return data[0] if data else None
    except Exception as e:
        logger.error(f"get_player_by_game_id error: {e}")
        return None

def create_player(tg_id: int, nick: str, game_id: str, password: str):
    try:
        with sb_client() as c:
            r = c.post("/players", json={
                "Telegram_id": tg_id,
                "nick": nick,
                "game_id": game_id,
                "password": password,
            })
            r.raise_for_status()
            return True
    except Exception as e:
        logger.error(f"create_player error: {e}")
        return False

def update_player(tg_id: int, fields: dict):
    try:
        with sb_client() as c:
            r = c.patch("/players", params={"Telegram_id": f"eq.{tg_id}"}, json=fields)
            r.raise_for_status()
            return True
    except Exception as e:
        logger.error(f"update_player error: {e}")
        return False

def top_players(limit=10):
    try:
        with sb_client() as c:
            r = c.get("/players", params={"order": "elo.desc", "limit": str(limit)})
            r.raise_for_status()
            return r.json()
    except Exception as e:
        logger.error(f"top_players error: {e}")
        return []

def all_players(limit=100):
    try:
        with sb_client() as c:
            r = c.get("/players", params={"limit": str(limit)})
            r.raise_for_status()
            return r.json()
    except Exception as e:
        logger.error(f"all_players error: {e}")
        return []

# ============================================================
# VALIDATION
# ============================================================

NICK_RE = re.compile(r"^[A-Za-z0-9_]{2,32}$")
ID_RE = re.compile(r"^\d{8,15}$")
PASS_RE = re.compile(r"^(?=.*[A-Za-z])(?=.*\d)[A-Za-z0-9]{8,}$")

def valid_nick(s): return bool(NICK_RE.match(s))
def valid_game_id(s): return bool(ID_RE.match(s))
def valid_password(s): return bool(PASS_RE.match(s))

def rank_for_elo(elo):
    ranks = [(0, "Без ранга"), (900, "Бронза"), (1100, "Серебро"), (1300, "Золото"),
             (1500, "Платина"), (1700, "Алмаз"), (1900, "Мастер"), (2100, "Элита"), (2300, "Легенда")]
    result = ranks[0][1]
    for threshold, name in ranks:
        if elo >= threshold:
            result = name
    return result

def is_premium(player):
    until = player.get("premium_until")
    if not until:
        return False
    try:
        dt = datetime.fromisoformat(until.replace("Z", "+00:00"))
        return dt > datetime.now(timezone.utc)
    except:
        return False

def display_nick(player):
    return f"{'💎 ' if is_premium(player) else ''}{player['nick']}"

# ============================================================
# KEYBOARDS
# ============================================================

def start_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔑 Вход", callback_data="login")],
        [InlineKeyboardButton("📝 Регистрация", callback_data="register")],
        [InlineKeyboardButton("🆘 Поддержка", callback_data="support")],
    ])

def main_menu_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("👤 Профиль", callback_data="profile"),
         InlineKeyboardButton("🏆 Топ", callback_data="top")],
        [InlineKeyboardButton("💎 Премиум", callback_data="premium"),
         InlineKeyboardButton("✏️ Сменить ник", callback_data="changenick")],
        [InlineKeyboardButton("🆘 Поддержка", callback_data="support")],
        [InlineKeyboardButton("🚪 Выйти", callback_data="logout")],
    ])

def back_keyboard():
    return InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data="back")]])

# ============================================================
# CONVERSATION STATES
# ============================================================

REG_NICK, REG_ID, REG_PASS, LOGIN_NICK, LOGIN_ID, LOGIN_PASS, SUPPORT_MSG, ADMIN_REPLY, RESET_PASS = range(9)

# ============================================================
# COMMAND: /start
# ============================================================

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    user = update.effective_user
    player = get_player_by_tg(user.id)
    
    if player:
        await update.message.reply_text(
            f"👋 С возвращением, {player['nick']}!",
            reply_markup=main_menu_keyboard()
        )
    else:
        await update.message.reply_text(
            "👋 Добро пожаловать в Strange Faceit!\n\nВыберите действие:",
            reply_markup=start_keyboard()
        )
    return ConversationHandler.END

# ============================================================
# COMMAND: /admin
# ============================================================

async def cmd_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("⛔ Нет доступа.")
        return

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Сброс пароля", callback_data="admin_reset")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="back")],
    ])
    await update.message.reply_text("🛠 Админ-панель", reply_markup=keyboard)

# ============================================================
# COMMAND: /cancel
# ============================================================

async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("❌ Отменено.", reply_markup=start_keyboard())
    return ConversationHandler.END

# ============================================================
# REGISTRATION
# ============================================================

async def reg_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if get_player_by_tg(query.from_user.id):
        await query.edit_message_text("Уже зарегистрированы.", reply_markup=start_keyboard())
        return ConversationHandler.END
    await query.edit_message_text("📝 Регистрация\n\nВведите ник (2-32 символа, латиница/цифры/_):")
    return REG_NICK

async def reg_nick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    nick = update.message.text.strip()
    if not valid_nick(nick):
        await update.message.reply_text("❌ Некорректный ник. Попробуйте:")
        return REG_NICK
    if get_player_by_nick(nick):
        await update.message.reply_text("❌ Ник занят. Введите другой:")
        return REG_NICK
    context.user_data["nick"] = nick
    await update.message.reply_text("Введите ID в Standoff 2 (8-15 цифр):")
    return REG_ID

async def reg_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    game_id = update.message.text.strip()
    if not valid_game_id(game_id):
        await update.message.reply_text("❌ Некорректный ID. Попробуйте:")
        return REG_ID
    if get_player_by_game_id(game_id):
        await update.message.reply_text("❌ ID занят. Если это ваш аккаунт — обратитесь в поддержку.")
        return ConversationHandler.END
    context.user_data["game_id"] = game_id
    await update.message.reply_text("Придумайте пароль (мин. 8 символов, латиница + цифры):")
    return REG_PASS

async def reg_pass(update: Update, context: ContextTypes.DEFAULT_TYPE):
    password = update.message.text.strip()
    if not valid_password(password):
        await update.message.reply_text("❌ Слабый пароль. Попробуйте:")
        return REG_PASS

    tg_id = update.effective_user.id
    nick = context.user_data["nick"]
    game_id = context.user_data["game_id"]

    if create_player(tg_id, nick, game_id, password):
        context.user_data.clear()
        await update.message.reply_text(f"✅ Регистрация успешна! Добро пожаловать, {nick}!", reply_markup=main_menu_keyboard())
    else:
        await update.message.reply_text("❌ Ошибка регистрации. Попробуйте позже.", reply_markup=start_keyboard())

    return ConversationHandler.END

# ============================================================
# LOGIN
# ============================================================

async def login_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if get_player_by_tg(query.from_user.id):
        await query.edit_message_text("Уже вошли.", reply_markup=main_menu_keyboard())
        return ConversationHandler.END
    await query.edit_message_text("🔑 Вход\n\nВведите ник:")
    return LOGIN_NICK

async def login_nick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    nick = update.message.text.strip()
    player = get_player_by_nick(nick)
    if not player:
        await update.message.reply_text("❌ Ник не найден. Попробуйте:")
        return LOGIN_NICK
    context.user_data["login_player"] = player
    await update.message.reply_text("Введите ID:")
    return LOGIN_ID

async def login_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    game_id = update.message.text.strip()
    player = context.user_data.get("login_player")
    if player.get("game_id") != game_id:
        await update.message.reply_text("❌ ID не совпадает. Попробуйте:")
        return LOGIN_ID
    await update.message.reply_text("Введите пароль:")
    return LOGIN_PASS

async def login_pass(update: Update, context: ContextTypes.DEFAULT_TYPE):
    password = update.message.text.strip()
    player = context.user_data.get("login_player")
    if player.get("password") != password:
        await update.message.reply_text("❌ Неверный пароль. Попробуйте:")
        return LOGIN_PASS

    tg_id = update.effective_user.id
    if player.get("Telegram_id") != tg_id:
        update_player(player["Telegram_id"], {"Telegram_id": tg_id})

    context.user_data.clear()
    await update.message.reply_text(f"✅ Вход выполнен! С возвращением, {player['nick']}!", reply_markup=main_menu_keyboard())
    return ConversationHandler.END

# ============================================================
# PROFILE
# ============================================================

async def menu_profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    player = get_player_by_tg(query.from_user.id)
    if not player:
        await query.edit_message_text("Сначала войдите.", reply_markup=start_keyboard())
        return

    rank = rank_for_elo(player.get("elo", 1000))
    premium = "✅ Активен" if is_premium(player) else "❌ Нет"

    text = (
        f"👤 Профиль\n\n"
        f"Ник: {display_nick(player)}\n"
        f"ID: {player.get('game_id')}\n"
        f"Ранг: {rank}\n"
        f"ELO: {player.get('elo', 1000)}\n"
        f"Матчи: {player.get('matches', 0)}\n"
        f"Победы: {player.get('wins', 0)}\n"
        f"Поражения: {player.get('losses', 0)}\n"
        f"MVP: {player.get('mvp', 0)}\n"
        f"Премиум: {premium}"
    )
    await query.edit_message_text(text, reply_markup=back_keyboard())

# ============================================================
# TOP
# ============================================================

async def menu_top(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    players = top_players(10)
    if not players:
        await query.edit_message_text("🏆 Топ пуст.", reply_markup=back_keyboard())
        return
    lines = ["🏆 Топ игроков:\n"]
    for i, p in enumerate(players, 1):
        tag = "💎" if is_premium(p) else ""
        lines.append(f"{i}. {tag} {p['nick']} — {p['elo']} ELO")
    await query.edit_message_text("\n".join(lines), reply_markup=back_keyboard())

# ============================================================
# PREMIUM
# ============================================================

PREMIUM_PRICES = {
    "1_day": {"label": "1 день — 5 ⭐", "stars": 5, "days": 1},
    "1_week": {"label": "1 неделя — 39 ⭐", "stars": 39, "days": 7},
    "1_month": {"label": "1 месяц — 111 ⭐", "stars": 111, "days": 30},
}

async def menu_premium(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    player = get_player_by_tg(query.from_user.id)
    if not player:
        await query.edit_message_text("Сначала войдите.", reply_markup=start_keyboard())
        return

    kb = [[InlineKeyboardButton(v["label"], callback_data=f"buy_{k}")] for k, v in PREMIUM_PRICES.items()]
    kb.append([InlineKeyboardButton("⬅️ Назад", callback_data="back")])
    await query.edit_message_text(
        "💎 Премиум\n\nДаёт:\n• 💎 Тег рядом с ником\n• Бесплатную смену ника\n\nВыберите период:",
        reply_markup=InlineKeyboardMarkup(kb)
    )

async def premium_buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    plan_key = query.data.replace("buy_", "")
    plan = PREMIUM_PRICES.get(plan_key)
    if not plan:
        return

    await context.bot.send_invoice(
        chat_id=query.from_user.id,
        title="Премиум",
        description=plan["label"],
        payload=f"premium_{plan_key}",
        provider_token="",
        currency="XTR",
        prices=[{"label": plan["label"], "amount": plan["stars"]}],
    )

async def precheckout_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.pre_checkout_query.answer(ok=True)

async def successful_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    payload = update.message.successful_payment.invoice_payload
    plan_key = payload.replace("premium_", "")
    plan = PREMIUM_PRICES.get(plan_key)
    tg_id = update.effective_user.id

    player = get_player_by_tg(tg_id)
    if not player or not plan:
        await update.message.reply_text("❌ Ошибка активации. Обратитесь в поддержку.")
        return

    now = datetime.now(timezone.utc)
    current = player.get("premium_until")
    if current:
        try:
            current_dt = datetime.fromisoformat(current.replace("Z", "+00:00"))
            if current_dt > now:
                now = current_dt
        except:
            pass
    new_until = now + timedelta(days=plan["days"])
    update_player(tg_id, {"premium_until": new_until.isoformat()})
    await update.message.reply_text(f"✅ Премиум активирован до {new_until.strftime('%Y-%m-%d %H:%M')} UTC!")

# ============================================================
# CHANGE NICK
# ============================================================

async def changenick_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    player = get_player_by_tg(query.from_user.id)
    if not player:
        await query.edit_message_text("Сначала войдите.", reply_markup=start_keyboard())
        return ConversationHandler.END

    if not is_premium(player):
        await query.edit_message_text("✏️ Смена ника только для премиум!", reply_markup=back_keyboard())
        return ConversationHandler.END

    await query.edit_message_text("Введите новый ник (2-32 символа, латиница/цифры/_):")
    return REG_NICK  # используем тот же шаг, что и в регистрации

async def changenick_apply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    nick = update.message.text.strip()
    if not valid_nick(nick):
        await update.message.reply_text("❌ Некорректный ник. Попробуйте:")
        return REG_NICK
    if get_player_by_nick(nick):
        await update.message.reply_text("❌ Ник занят. Введите другой:")
        return REG_NICK

    tg_id = update.effective_user.id
    if update_player(tg_id, {"nick": nick}):
        await update.message.reply_text(f"✅ Ник изменён на {nick}!", reply_markup=main_menu_keyboard())
    else:
        await update.message.reply_text("❌ Ошибка смены ника.", reply_markup=main_menu_keyboard())
    return ConversationHandler.END

# ============================================================
# SUPPORT
# ============================================================

SUPPORT_TICKETS = {}
_ticket_counter = 0

def next_ticket():
    global _ticket_counter
    _ticket_counter += 1
    return _ticket_counter

async def support_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("🆘 Напишите ваше сообщение в поддержку:")
    return SUPPORT_MSG

async def support_receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ticket_id = next_ticket()
    SUPPORT_TICKETS[ticket_id] = {"user_id": user.id, "username": user.username or str(user.id)}

    if OWNER_ID:
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("✍️ Ответить", callback_data=f"reply_{ticket_id}")]])
        await context.bot.send_message(
            OWNER_ID,
            f"🆘 Тикет #{ticket_id}\nОт: @{SUPPORT_TICKETS[ticket_id]['username']}\n\n{update.message.text}",
            reply_markup=kb
        )

    await update.message.reply_text("✅ Сообщение отправлено. Мы ответим вам здесь.", reply_markup=main_menu_keyboard())
    return ConversationHandler.END

async def admin_reply_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.from_user.id != OWNER_ID:
        return

    ticket_id = int(query.data.replace("reply_", ""))
    if ticket_id not in SUPPORT_TICKETS:
        await query.edit_message_text("Тикет закрыт.")
        return

    context.user_data["reply_ticket"] = ticket_id
    await query.message.reply_text(f"Введите ответ для тикета #{ticket_id}:")
    return ADMIN_REPLY

async def admin_reply_send(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ticket_id = context.user_data.get("reply_ticket")
    ticket = SUPPORT_TICKETS.get(ticket_id)
    if not ticket:
        await update.message.reply_text("Тикет не найден.")
        return ConversationHandler.END

    try:
        await context.bot.send_message(
            ticket["user_id"],
            f"🆘 Ответ поддержки:\n\n{update.message.text}"
        )
        await update.message.reply_text("✅ Ответ отправлен.")
    except:
        await update.message.reply_text("❌ Не удалось отправить.")
    SUPPORT_TICKETS.pop(ticket_id, None)
    context.user_data.clear()
    return ConversationHandler.END

# ============================================================
# ADMIN: RESET PASSWORD
# ============================================================

async def admin_reset_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.from_user.id != OWNER_ID:
        return

    players = all_players(50)
    if not players:
        await query.edit_message_text("Нет игроков.")
        return

    kb = [[InlineKeyboardButton(p["nick"], callback_data=f"reset_{p['Telegram_id']}")] for p in players]
    kb.append([InlineKeyboardButton("⬅️ Назад", callback_data="back")])
    await query.edit_message_text("Выберите игрока для сброса пароля:", reply_markup=InlineKeyboardMarkup(kb))

async def admin_reset_pick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.from_user.id != OWNER_ID:
        return

    tg_id = int(query.data.replace("reset_", ""))
    context.user_data["reset_tg_id"] = tg_id
    await query.message.reply_text("Введите новый пароль (мин. 8 символов, латиница+цифры):")
    return RESET_PASS

async def admin_reset_apply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    password = update.message.text.strip()
    if not valid_password(password):
        await update.message.reply_text("❌ Слабый пароль. Попробуйте:")
        return RESET_PASS

    tg_id = context.user_data.get("reset_tg_id")
    if update_player(tg_id, {"password": password}):
        await update.message.reply_text("✅ Пароль сброшен.")
        try:
            await context.bot.send_message(tg_id, "🔑 Ваш пароль был сброшен администратором.")
        except:
            pass
    else:
        await update.message.reply_text("❌ Ошибка сброса.")

    context.user_data.clear()
    return ConversationHandler.END

# ============================================================
# BACK / LOGOUT
# ============================================================

async def back(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    player = get_player_by_tg(query.from_user.id)
    if player:
        await query.edit_message_text("Главное меню:", reply_markup=main_menu_keyboard())
    else:
        await query.edit_message_text("Выберите действие:", reply_markup=start_keyboard())

async def logout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("Вы вышли.")
    context.user_data.clear()
    await query.edit_message_text("Вы вышли.", reply_markup=start_keyboard())

# ============================================================
# ERROR HANDLER
# ============================================================

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Update {update} caused error: {context.error}")

# ============================================================
# MAIN
# ============================================================

def main():
    ensure_event_loop()
    
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN is required")
        return
    if not SUPABASE_URL or not SUPABASE_KEY:
        logger.error("SUPABASE_URL and SUPABASE_KEY are required")
        return

    threading.Thread(target=run_flask, daemon=True).start()
    logger.info(f"Flask running on port {PORT}")

    app = Application.builder().token(BOT_TOKEN).build()

    # ============================================================
    # COMMAND HANDLERS
    # ============================================================
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("admin", cmd_admin))
    app.add_handler(CommandHandler("cancel", cmd_cancel))

    # ============================================================
    # CONVERSATION: REGISTRATION
    # ============================================================
    reg_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(reg_start, pattern="^register$")],
        states={
            REG_NICK: [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_nick)],
            REG_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_id)],
            REG_PASS: [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_pass)],
        },
        fallbacks=[CommandHandler("cancel", cmd_cancel)],
    )
    app.add_handler(reg_conv)

    # ============================================================
    # CONVERSATION: LOGIN
    # ============================================================
    login_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(login_start, pattern="^login$")],
        states={
            LOGIN_NICK: [MessageHandler(filters.TEXT & ~filters.COMMAND, login_nick)],
            LOGIN_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, login_id)],
            LOGIN_PASS: [MessageHandler(filters.TEXT & ~filters.COMMAND, login_pass)],
        },
        fallbacks=[CommandHandler("cancel", cmd_cancel)],
    )
    app.add_handler(login_conv)

    # ============================================================
    # CONVERSATION: CHANGE NICK (reuses REG_NICK state)
    # ============================================================
    changenick_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(changenick_start, pattern="^changenick$")],
        states={
            REG_NICK: [MessageHandler(filters.TEXT & ~filters.COMMAND, changenick_apply)],
        },
        fallbacks=[CommandHandler("cancel", cmd_cancel)],
    )
    app.add_handler(changenick_conv)

    # ============================================================
    # CONVERSATION: SUPPORT
    # ============================================================
    support_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(support_start, pattern="^support$")],
        states={
            SUPPORT_MSG: [MessageHandler(filters.TEXT & ~filters.COMMAND, support_receive)],
        },
        fallbacks=[CommandHandler("cancel", cmd_cancel)],
    )
    app.add_handler(support_conv)

    # ============================================================
    # CONVERSATION: ADMIN REPLY
    # ============================================================
    admin_reply_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_reply_start, pattern="^reply_")],
        states={
            ADMIN_REPLY: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_reply_send)],
        },
        fallbacks=[CommandHandler("cancel", cmd_cancel)],
    )
    app.add_handler(admin_reply_conv)

    # ============================================================
    # CONVERSATION: ADMIN RESET PASSWORD
    # ============================================================
    admin_reset_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_reset_list, pattern="^admin_reset$")],
        states={
            ADMIN_REPLY: [CallbackQueryHandler(admin_reset_pick, pattern="^reset_")],  # временно используем ADMIN_REPLY
            RESET_PASS: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_reset_apply)],
        },
        fallbacks=[CommandHandler("cancel", cmd_cancel)],
    )
    app.add_handler(admin_reset_conv)

    # ============================================================
    # CALLBACK HANDLERS
    # ============================================================
    app.add_handler(CallbackQueryHandler(back, pattern="^back$"))
    app.add_handler(CallbackQueryHandler(logout, pattern="^logout$"))
    app.add_handler(CallbackQueryHandler(menu_profile, pattern="^profile$"))
    app.add_handler(CallbackQueryHandler(menu_top, pattern="^top$"))
    app.add_handler(CallbackQueryHandler(menu_premium, pattern="^premium$"))
    app.add_handler(CallbackQueryHandler(premium_buy, pattern="^buy_"))

    # ============================================================
    # PAYMENT HANDLERS
    # ============================================================
    app.add_handler(PreCheckoutQueryHandler(precheckout_callback))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment))

    # ============================================================
    # ERROR HANDLER
    # ============================================================
    app.add_error_handler(error_handler)

    logger.info("🤖 Bot started, polling...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()

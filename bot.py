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
    """Создаёт event loop если его нет (для Python 3.14+)"""
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
    with sb_client() as c:
        r = c.get("/players", params={"Telegram_id": f"eq.{tg_id}", "select": "*"})
        r.raise_for_status()
        data = r.json()
        return data[0] if data else None

def get_player_by_nick(nick: str):
    with sb_client() as c:
        r = c.get("/players", params={"nick": f"eq.{nick}", "select": "*"})
        r.raise_for_status()
        data = r.json()
        return data[0] if data else None

def get_player_by_game_id(game_id: str):
    with sb_client() as c:
        r = c.get("/players", params={"game_id": f"eq.{game_id}", "select": "*"})
        r.raise_for_status()
        data = r.json()
        return data[0] if data else None

def create_player(tg_id: int, nick: str, game_id: str, password: str):
    payload = {
        "Telegram_id": tg_id,
        "nick": nick,
        "game_id": game_id,
        "password": password,
    }
    with sb_client() as c:
        r = c.post("/players", json=payload, headers={**SB_HEADERS, "Prefer": "return=representation"})
        r.raise_for_status()
        return r.json()

def update_player(tg_id: int, fields: dict):
    with sb_client() as c:
        r = c.patch(
            "/players",
            params={"Telegram_id": f"eq.{tg_id}"},
            json=fields,
            headers={**SB_HEADERS, "Prefer": "return=representation"},
        )
        r.raise_for_status()
        return r.json()

def top_players(limit=10):
    with sb_client() as c:
        r = c.get(
            "/players",
            params={"select": "nick,elo,premium_until", "order": "elo.desc", "limit": str(limit)},
        )
        r.raise_for_status()
        return r.json()

def all_players(limit=200):
    with sb_client() as c:
        r = c.get("/players", params={"select": "Telegram_id,nick,game_id", "limit": str(limit)})
        r.raise_for_status()
        return r.json()

# ============================================================
# VALIDATION
# ============================================================

NICK_RE = re.compile(r"^[A-Za-z0-9_]{2,32}$")
ID_RE = re.compile(r"^\d{8,15}$")
PASS_RE = re.compile(r"^(?=.*[A-Za-z])(?=.*\d)[A-Za-z0-9]{8,}$")

def valid_nick(s: str) -> bool:
    return bool(NICK_RE.match(s))

def valid_game_id(s: str) -> bool:
    return bool(ID_RE.match(s))

def valid_password(s: str) -> bool:
    return bool(PASS_RE.match(s))

def rank_for_elo(elo: int) -> str:
    RANKS = [
        (0, "Без ранга"),
        (900, "Бронза"),
        (1100, "Серебро"),
        (1300, "Золото"),
        (1500, "Платина"),
        (1700, "Алмаз"),
        (1900, "Мастер"),
        (2100, "Элита"),
        (2300, "Легенда"),
    ]
    rank = RANKS[0][1]
    for threshold, name in RANKS:
        if elo >= threshold:
            rank = name
    return rank

def is_premium(player: dict) -> bool:
    until = player.get("premium_until")
    if not until:
        return False
    try:
        dt = datetime.fromisoformat(until.replace("Z", "+00:00"))
    except Exception:
        return False
    return dt > datetime.now(timezone.utc)

def display_nick(player: dict) -> str:
    tag = "💎 " if is_premium(player) else ""
    return f"{tag}{player['nick']}"

# ============================================================
# KEYBOARDS
# ============================================================

def start_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔑 Вход", callback_data="start_login")],
        [InlineKeyboardButton("📝 Регистрация", callback_data="start_register")],
        [InlineKeyboardButton("🆘 Поддержка", callback_data="start_support")],
    ])

def main_menu_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("👤 Профиль", callback_data="menu_profile"),
         InlineKeyboardButton("🏆 Топ игроков", callback_data="menu_top")],
        [InlineKeyboardButton("💎 Премиум", callback_data="menu_premium"),
         InlineKeyboardButton("✏️ Сменить ник", callback_data="menu_changenick")],
        [InlineKeyboardButton("🎮 Найти игру", callback_data="menu_findgame"),
         InlineKeyboardButton("📜 Правила", callback_data="menu_rules")],
        [InlineKeyboardButton("🆘 Поддержка", callback_data="menu_support"),
         InlineKeyboardButton("🚪 Выйти", callback_data="menu_logout")],
    ])

def back_to_menu_keyboard():
    return InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ В меню", callback_data="menu_back")]])

# ============================================================
# CONVERSATION STATES
# ============================================================

(REG_NICK, REG_ID, REG_PASS,
 LOGIN_NICK, LOGIN_ID, LOGIN_PASS,
 SUPPORT_MSG,
 ADMIN_RESET_PICK, ADMIN_RESET_PASS,
 ADMIN_REPLY_MSG,
 CHANGE_NICK) = range(11)

# ============================================================
# SUPPORT TICKETS (in-memory)
# ============================================================

SUPPORT_TICKETS = {}
_ticket_counter = 0

def next_ticket_id():
    global _ticket_counter
    _ticket_counter += 1
    return _ticket_counter

# ============================================================
# START / MENU
# ============================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text(
        "👋 Добро пожаловать в матчмейкинг-бот Standoff 2!\n\n"
        "Выберите действие:",
        reply_markup=start_keyboard(),
    )
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("Отменено.", reply_markup=back_to_menu_keyboard())
    await update.message.reply_text("Выберите действие:", reply_markup=start_keyboard())
    return ConversationHandler.END

async def show_main_menu(update_or_query, context, edit=False):
    text = "🏠 Главное меню"
    if edit:
        await update_or_query.edit_message_text(text, reply_markup=main_menu_keyboard())
    else:
        await update_or_query.message.reply_text(text, reply_markup=main_menu_keyboard())

async def menu_back(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await show_main_menu(query, context, edit=True)

async def logout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("Вы вышли из аккаунта")
    context.user_data.clear()
    await query.edit_message_text("Вы вышли. Выберите действие:", reply_markup=start_keyboard())

# ============================================================
# REGISTRATION
# ============================================================

async def reg_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    tg_id = query.from_user.id
    if get_player_by_tg(tg_id):
        await query.edit_message_text("У вас уже есть аккаунт. Используйте «Вход».", reply_markup=start_keyboard())
        return ConversationHandler.END
    await query.edit_message_text(
        "📝 Регистрация\n\nВведите ник (2-32 символа, латиница/цифры/подчёркивание):"
    )
    return REG_NICK

async def reg_nick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    nick = update.message.text.strip()
    if not valid_nick(nick):
        await update.message.reply_text("❌ Некорректный ник. Только латиница, цифры, _ (2-32 символа). Попробуйте снова:")
        return REG_NICK
    if get_player_by_nick(nick):
        await update.message.reply_text("❌ Этот ник уже занят. Введите другой:")
        return REG_NICK
    context.user_data["reg_nick"] = nick
    await update.message.reply_text("Введите ваш ID в Standoff 2 (8-15 цифр):")
    return REG_ID

async def reg_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    game_id = update.message.text.strip()
    if not valid_game_id(game_id):
        await update.message.reply_text("❌ Некорректный ID. Введите 8-15 цифр:")
        return REG_ID
    if get_player_by_game_id(game_id):
        await update.message.reply_text(
            "❌ Этот игровой ID уже зарегистрирован другим аккаунтом.\n"
            "Если это ошибка, обратитесь в поддержку: /start → 🆘 Поддержка."
        )
        return ConversationHandler.END
    context.user_data["reg_id"] = game_id
    await update.message.reply_text("Придумайте пароль (минимум 8 символов, латиница + цифры):")
    return REG_PASS

async def reg_pass(update: Update, context: ContextTypes.DEFAULT_TYPE):
    password = update.message.text.strip()
    if not valid_password(password):
        await update.message.reply_text("❌ Пароль слишком слабый. Нужно минимум 8 символов, латиница + цифры:")
        return REG_PASS

    tg_id = update.effective_user.id
    nick = context.user_data["reg_nick"]
    game_id = context.user_data["reg_id"]

    try:
        create_player(tg_id, nick, game_id, password)
    except httpx.HTTPStatusError as e:
        logger.error(f"Registration error: {e.response.text}")
        await update.message.reply_text("❌ Ошибка регистрации. Попробуйте /start снова.")
        context.user_data.clear()
        return ConversationHandler.END

    context.user_data.clear()
    await update.message.reply_text(f"✅ Регистрация успешна! Добро пожаловать, {nick}.")
    await show_main_menu(update, context)
    return ConversationHandler.END

# ============================================================
# LOGIN
# ============================================================

async def login_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("🔑 Вход\n\nВведите ваш ник:")
    return LOGIN_NICK

async def login_nick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    nick = update.message.text.strip()
    player = get_player_by_nick(nick)
    if not player:
        await update.message.reply_text("❌ Игрок с таким ником не найден. Попробуйте снова или /cancel:")
        return LOGIN_NICK
    context.user_data["login_player"] = player
    await update.message.reply_text("Введите ваш игровой ID:")
    return LOGIN_ID

async def login_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    game_id = update.message.text.strip()
    player = context.user_data.get("login_player")
    if not player or player.get("game_id") != game_id:
        await update.message.reply_text("❌ ID не совпадает. Попробуйте снова или /cancel:")
        return LOGIN_ID
    await update.message.reply_text("Введите пароль:")
    return LOGIN_PASS

async def login_pass(update: Update, context: ContextTypes.DEFAULT_TYPE):
    password = update.message.text.strip()
    player = context.user_data.get("login_player")
    if not player or player.get("password") != password:
        await update.message.reply_text("❌ Неверный пароль. Попробуйте снова или /cancel:")
        return LOGIN_PASS

    tg_id = update.effective_user.id
    if player.get("Telegram_id") != tg_id:
        try:
            update_player(player["Telegram_id"], {"Telegram_id": tg_id})
        except Exception as e:
            logger.error(f"Failed to relink account: {e}")

    context.user_data.clear()
    await update.message.reply_text(f"✅ Добро пожаловать, {player['nick']}!")
    await show_main_menu(update, context)
    return ConversationHandler.END

# ============================================================
# PROFILE / TOP / PREMIUM
# ============================================================

async def menu_profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    player = get_player_by_tg(query.from_user.id)
    if not player:
        await query.edit_message_text("Сначала войдите в аккаунт.", reply_markup=start_keyboard())
        return

    rank = rank_for_elo(player.get("elo", 1000))
    premium_line = "Нет"
    if is_premium(player):
        until = player["premium_until"][:16].replace("T", " ")
        premium_line = f"Активен до {until}"

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
        f"Премиум: {premium_line}"
    )
    await query.edit_message_text(text, reply_markup=back_to_menu_keyboard())

async def menu_top(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    players = top_players(10)
    if not players:
        text = "🏆 Топ игроков\n\nПока нет игроков."
    else:
        lines = ["🏆 Топ-10 игроков по ELO:\n"]
        for i, p in enumerate(players, 1):
            tag = "💎 " if is_premium(p) else ""
            lines.append(f"{i}. {tag}{p['nick']} — {p['elo']} ELO")
        text = "\n".join(lines)
    await query.edit_message_text(text, reply_markup=back_to_menu_keyboard())

async def menu_premium(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    player = get_player_by_tg(query.from_user.id)
    if not player:
        await query.edit_message_text("Сначала войдите в аккаунт.", reply_markup=start_keyboard())
        return

    PREMIUM_PRICES = {
        "1_day": {"label": "1 день — 5 ⭐", "stars": 5, "days": 1},
        "1_week": {"label": "1 неделя — 39 ⭐", "stars": 39, "days": 7},
        "1_month": {"label": "1 месяц — 111 ⭐", "stars": 111, "days": 30},
    }

    kb = [
        [InlineKeyboardButton(v["label"], callback_data=f"premium_buy_{k}")]
        for k, v in PREMIUM_PRICES.items()
    ]
    kb.append([InlineKeyboardButton("⬅️ В меню", callback_data="menu_back")])

    text = (
        "💎 Премиум-подписка\n\n"
        "Даёт:\n"
        "• Премиальный тег 💎 рядом с ником\n"
        "• Бесплатную смену ника без ограничений\n\n"
        "Выберите период оплаты (в Telegram Stars):"
    )
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb))

async def premium_buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    plan_key = query.data.replace("premium_buy_", "")
    PREMIUM_PRICES = {
        "1_day": {"label": "1 день — 5 ⭐", "stars": 5, "days": 1},
        "1_week": {"label": "1 неделя — 39 ⭐", "stars": 39, "days": 7},
        "1_month": {"label": "1 месяц — 111 ⭐", "stars": 111, "days": 30},
    }
    plan = PREMIUM_PRICES.get(plan_key)
    if not plan:
        await query.answer("Неизвестный план", show_alert=True)
        return

    await context.bot.send_invoice(
        chat_id=query.from_user.id,
        title="Premium подписка",
        description=plan["label"],
        payload=f"premium_{plan_key}_{query.from_user.id}",
        provider_token="",
        currency="XTR",
        prices=[{"label": plan["label"], "amount": plan["stars"]}],
    )

async def precheckout_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.pre_checkout_query
    await query.answer(ok=True)

async def successful_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    payload = update.message.successful_payment.invoice_payload
    parts = payload.split("_")
    plan_key = "_".join(parts[1:-1])
    PREMIUM_PRICES = {
        "1_day": {"label": "1 день — 5 ⭐", "stars": 5, "days": 1},
        "1_week": {"label": "1 неделя — 39 ⭐", "stars": 39, "days": 7},
        "1_month": {"label": "1 месяц — 111 ⭐", "stars": 111, "days": 30},
    }
    plan = PREMIUM_PRICES.get(plan_key)
    tg_id = update.effective_user.id

    player = get_player_by_tg(tg_id)
    if not player or not plan:
        await update.message.reply_text("Оплата прошла, но произошла ошибка активации. Обратитесь в поддержку.")
        return

    now = datetime.now(timezone.utc)
    current_until = None
    if player.get("premium_until"):
        try:
            current_until = datetime.fromisoformat(player["premium_until"].replace("Z", "+00:00"))
        except Exception:
            current_until = None
    base = current_until if current_until and current_until > now else now
    new_until = base + timedelta(days=plan["days"])

    update_player(tg_id, {"premium_until": new_until.isoformat()})
    await update.message.reply_text(
        f"✅ Премиум активирован до {new_until.strftime('%Y-%m-%d %H:%M')} UTC. Спасибо!",
        reply_markup=main_menu_keyboard(),
    )

# ============================================================
# CHANGE NICK
# ============================================================

async def changenick_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    player = get_player_by_tg(query.from_user.id)
    if not player:
        await query.edit_message_text("Сначала войдите в аккаунт.", reply_markup=start_keyboard())
        return ConversationHandler.END

    if not is_premium(player):
        await query.edit_message_text(
            "✏️ Смена ника доступна только премиум-игрокам.\n\n"
            "Оформите 💎 Премиум в главном меню.",
            reply_markup=back_to_menu_keyboard(),
        )
        return ConversationHandler.END

    await query.edit_message_text("Введите новый ник (2-32 символа, латиница/цифры/подчёркивание):")
    return CHANGE_NICK

async def changenick_apply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    nick = update.message.text.strip()
    if not valid_nick(nick):
        await update.message.reply_text("❌ Некорректный ник. Попробуйте снова:")
        return CHANGE_NICK
    if get_player_by_nick(nick):
        await update.message.reply_text("❌ Этот ник уже занят. Введите другой:")
        return CHANGE_NICK

    tg_id = update.effective_user.id
    try:
        update_player(tg_id, {"nick": nick})
    except httpx.HTTPStatusError:
        await update.message.reply_text("❌ Не удалось сменить ник. Попробуйте позже.")
        return ConversationHandler.END

    await update.message.reply_text(f"✅ Ник изменён на {nick}.")
    await show_main_menu(update, context)
    return ConversationHandler.END

# ============================================================
# SUPPORT
# ============================================================

async def support_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("🆘 Напишите ваше сообщение в поддержку одним сообщением (или /cancel):")
    return SUPPORT_MSG

async def support_receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = update.message.text

    ticket_id = next_ticket_id()
    SUPPORT_TICKETS[ticket_id] = {
        "user_id": user.id,
        "username": user.username or user.first_name or str(user.id),
    }

    if OWNER_ID:
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✍️ Ответить", callback_data=f"admin_reply_{ticket_id}")]
        ])
        try:
            await context.bot.send_message(
                chat_id=OWNER_ID,
                text=f"🆘 Новый тикет #{ticket_id}\nОт: @{SUPPORT_TICKETS[ticket_id]['username']} (id {user.id})\n\n{text}",
                reply_markup=kb,
            )
        except Exception as e:
            logger.error(f"Failed to notify owner: {e}")

    await update.message.reply_text("✅ Сообщение отправлено в поддержку. Мы ответим вам здесь же.")
    await show_main_menu(update, context)
    return ConversationHandler.END

async def admin_reply_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.from_user.id != OWNER_ID:
        await query.answer("Нет доступа", show_alert=True)
        return ConversationHandler.END

    ticket_id = int(query.data.replace("admin_reply_", ""))
    if ticket_id not in SUPPORT_TICKETS:
        await query.edit_message_text("Тикет не найден или уже закрыт.")
        return ConversationHandler.END

    context.user_data["reply_ticket"] = ticket_id
    await query.message.reply_text(f"Введите ответ для тикета #{ticket_id} (или /cancel):")
    return ADMIN_REPLY_MSG

async def admin_reply_send(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ticket_id = context.user_data.get("reply_ticket")
    ticket = SUPPORT_TICKETS.get(ticket_id)
    if not ticket:
        await update.message.reply_text("Тикет не найден.")
        return ConversationHandler.END

    try:
        await context.bot.send_message(
            chat_id=ticket["user_id"],
            text=f"🆘 Ответ поддержки:\n\n{update.message.text}",
        )
        await update.message.reply_text("✅ Ответ отправлен пользователю.")
    except Exception as e:
        logger.error(f"Failed to send reply: {e}")
        await update.message.reply_text("❌ Не удалось отправить ответ (пользователь мог заблокировать бота).")

    SUPPORT_TICKETS.pop(ticket_id, None)
    context.user_data.clear()
    return ConversationHandler.END

# ============================================================
# ADMIN PANEL
# ============================================================

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("⛔ Нет доступа.")
        return ConversationHandler.END

    kb = [[InlineKeyboardButton("🔑 Сброс пароля игрока", callback_data="admin_resetpass")]]
    await update.message.reply_text("🛠 Админ-панель", reply_markup=InlineKeyboardMarkup(kb))
    return ConversationHandler.END

async def admin_resetpass_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.from_user.id != OWNER_ID:
        return ConversationHandler.END

    players = all_players(50)
    if not players:
        await query.edit_message_text("Нет зарегистрированных игроков.")
        return ConversationHandler.END

    kb = [
        [InlineKeyboardButton(f"{p['nick']} ({p['game_id']})", callback_data=f"admin_reset_{p['Telegram_id']}")]
        for p in players
    ]
    await query.edit_message_text("Выберите игрока для сброса пароля:", reply_markup=InlineKeyboardMarkup(kb))
    return ADMIN_RESET_PICK

async def admin_resetpass_pick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    tg_id = int(query.data.replace("admin_reset_", ""))
    context.user_data["reset_tg_id"] = tg_id
    await query.message.reply_text("Введите новый пароль для игрока (мин. 8 символов, латиница+цифры):")
    return ADMIN_RESET_PASS

async def admin_resetpass_apply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    password = update.message.text.strip()
    if not valid_password(password):
        await update.message.reply_text("❌ Пароль слишком слабый. Попробуйте снова:")
        return ADMIN_RESET_PASS

    tg_id = context.user_data.get("reset_tg_id")
    try:
        update_player(tg_id, {"password": password})
        await update.message.reply_text("✅ Пароль сброшен.")
        try:
            await context.bot.send_message(chat_id=tg_id, text="🔑 Ваш пароль был сброшен администратором.")
        except Exception:
            pass
    except Exception as e:
        logger.error(f"Reset password failed: {e}")
        await update.message.reply_text("❌ Ошибка сброса пароля.")

    context.user_data.clear()
    return ConversationHandler.END

# ============================================================
# PLACEHOLDERS
# ============================================================

async def menu_findgame(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "🎮 Поиск игры\n\nФункция матчмейкинга скоро появится! Следите за обновлениями.",
        reply_markup=back_to_menu_keyboard(),
    )

async def menu_rules(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    text = (
        "📜 Правила\n\n"
        "1. Уважайте других игроков.\n"
        "2. Читерство и договорные матчи запрещены.\n"
        "3. Оскорбления и токсичность караются баном.\n"
        "4. По всем вопросам — обращайтесь в поддержку."
    )
    await query.edit_message_text(text, reply_markup=back_to_menu_keyboard())

# ============================================================
# ERROR HANDLER
# ============================================================

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Update {update} caused error {context.error}")

async def unknown_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

# ============================================================
# MAIN
# ============================================================

def main():
    ensure_event_loop()
    
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is required")
    if not SUPABASE_URL or not SUPABASE_KEY:
        raise RuntimeError("SUPABASE_URL and SUPABASE_KEY are required")

    # Запускаем Flask в отдельном потоке
    threading.Thread(target=run_flask, daemon=True).start()
    logger.info(f"Flask running on port {PORT}")

    # Создаём приложение
    application = Application.builder().token(BOT_TOKEN).build()

    # Conversation: Регистрация
    reg_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(reg_start, pattern="^start_register$")],
        states={
            REG_NICK: [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_nick)],
            REG_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_id)],
            REG_PASS: [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_pass)],
        },
        fallbacks=[CommandHandler("cancel", cancel), CommandHandler("start", start)],
    )
    application.add_handler(reg_conv)

    # Conversation: Вход
    login_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(login_start, pattern="^start_login$")],
        states={
            LOGIN_NICK: [MessageHandler(filters.TEXT & ~filters.COMMAND, login_nick)],
            LOGIN_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, login_id)],
            LOGIN_PASS: [MessageHandler(filters.TEXT & ~filters.COMMAND, login_pass)],
        },
        fallbacks=[CommandHandler("cancel", cancel), CommandHandler("start", start)],
    )
    application.add_handler(login_conv)

    # Conversation: Смена ника
    changenick_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(changenick_start, pattern="^menu_changenick$")],
        states={
            CHANGE_NICK: [MessageHandler(filters.TEXT & ~filters.COMMAND, changenick_apply)],
        },
        fallbacks=[CommandHandler("cancel", cancel), CommandHandler("start", start)],
    )
    application.add_handler(changenick_conv)

    # Conversation: Поддержка (пользователь)
    support_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(support_start, pattern="^start_support$"),
            CallbackQueryHandler(support_start, pattern="^menu_support$"),
        ],
        states={
            SUPPORT_MSG: [MessageHandler(filters.TEXT & ~filters.COMMAND, support_receive)],
        },
        fallbacks=[CommandHandler("cancel", cancel), CommandHandler("start", start)],
    )
    application.add_handler(support_conv)

    # Conversation: Ответ админа в поддержку
    admin_reply_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_reply_start, pattern="^admin_reply_")],
        states={
            ADMIN_REPLY_MSG: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_reply_send)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    application.add_handler(admin_reply_conv)

    # Conversation: Сброс пароля (админ)
    admin_reset_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_resetpass_list, pattern="^admin_resetpass$")],
        states={
            ADMIN_RESET_PICK: [CallbackQueryHandler(admin_resetpass_pick, pattern="^admin_reset_")],
            ADMIN_RESET_PASS: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_resetpass_apply)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    application.add_handler(admin_reset_conv)

    # Menu callbacks
    application.add_handler(CallbackQueryHandler(menu_back, pattern="^menu_back$"))
    application.add_handler(CallbackQueryHandler(logout, pattern="^menu_logout$"))
    application.add_handler(CallbackQueryHandler(menu_profile, pattern="^menu_profile$"))
    application.add_handler(CallbackQueryHandler(menu_top, pattern="^menu_top$"))
    application.add_handler(CallbackQueryHandler(menu_premium, pattern="^menu_premium$"))
    application.add_handler(CallbackQueryHandler(premium_buy, pattern="^premium_buy_"))
    application.add_handler(CallbackQueryHandler(menu_findgame, pattern="^menu_findgame$"))
    application.add_handler(CallbackQueryHandler(menu_rules, pattern="^menu_rules$"))

    # Payments
    from telegram.ext import PreCheckoutQueryHandler
    application.add_handler(PreCheckoutQueryHandler(precheckout_callback))
    application.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment))

    # Fallback for stray callbacks
    application.add_handler(CallbackQueryHandler(unknown_callback))

    # Error handler
    application.add_error_handler(error_handler)

    # Start bot
    logger.info("🤖 Bot started, polling...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()

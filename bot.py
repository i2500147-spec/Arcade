import os
import re
import asyncio
import logging
import threading
import requests
import random
import string
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

# ============================== КОНФИГ ==============================

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

# ============================== EVENT LOOP FIX ==============================

def ensure_event_loop():
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

# ============================== FLASK ==============================

flask_app = Flask(__name__)

@flask_app.route("/")
@flask_app.route("/health")
def health():
    return "OK", 200

def run_flask():
    flask_app.run(host="0.0.0.0", port=PORT)

# ============================== SUPABASE ==============================

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
        payload = {
            "Telegram_id": tg_id,
            "nick": nick,
            "game_id": game_id,
            "password": password,
        }
        logger.info(f"создание игрока: {payload}")
        with sb_client() as c:
            r = c.post("/players", json=payload)
            logger.info(f"статус: {r.status_code}")
            logger.info(f"ответ: {r.text}")
            r.raise_for_status()
            return True
    except Exception as e:
        logger.error(f"ошибка создания игрока: {e}")
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

# ============================== ВАЛИДАЦИЯ ==============================

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
    tag = "💎 " if is_premium(player) else ""
    return f"{tag}{player['nick']}"

# ============================== КЛАВИАТУРЫ ==============================

def start_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Вход", callback_data="login")],
        [InlineKeyboardButton("Регистрация", callback_data="register")],
        [InlineKeyboardButton("Поддержка", callback_data="support")],
    ])

def main_menu_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Найти матч", callback_data="find_match"),
         InlineKeyboardButton("Пати", callback_data="party")],
        [InlineKeyboardButton("Профиль", callback_data="profile"),
         InlineKeyboardButton("Топ", callback_data="top")],
        [InlineKeyboardButton("Статистика", callback_data="stats"),
         InlineKeyboardButton("История", callback_data="history")],
        [InlineKeyboardButton("Жалобы", callback_data="reports")],
        [InlineKeyboardButton("Премиум", callback_data="premium_menu")],
        [InlineKeyboardButton("Сменить ник", callback_data="changenick")],
        [InlineKeyboardButton("Выйти", callback_data="logout")],
    ])

def back_keyboard():
    return InlineKeyboardMarkup([[InlineKeyboardButton("Назад", callback_data="back")]])

def stats_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Назад", callback_data="back"),
         InlineKeyboardButton("Расширенная статистика", callback_data="extended_stats")]
    ])

def premium_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Пробный период (24ч)", callback_data="premium_trial")],
        [InlineKeyboardButton("1 день — 5 ⭐", callback_data="buy_1_day")],
        [InlineKeyboardButton("1 неделя — 30 ⭐", callback_data="buy_1_week")],
        [InlineKeyboardButton("1 месяц — 100 ⭐", callback_data="buy_1_month")],
        [InlineKeyboardButton("Промокоды", callback_data="promo_menu")],
        [InlineKeyboardButton("Сменить ник", callback_data="changenick")],
        [InlineKeyboardButton("Назад", callback_data="back")],
    ])

def promo_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Ввести промокод", callback_data="promo_enter")],
        [InlineKeyboardButton("Назад", callback_data="premium_menu")],
    ])

# ============================== ПРОМОКОДЫ ==============================

PROMOCODES = {}

def generate_promo_code():
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))

# ============================== СОСТОЯНИЯ ==============================

REG_NICK, REG_ID, REG_PASS, LOGIN_NICK, LOGIN_ID, LOGIN_PASS, SUPPORT_MSG, ADMIN_REPLY, RESET_PASS, PROMO_ENTER = range(10)

# ============================== /START ==============================

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info(f"команда /start от {update.effective_user.id}")
    context.user_data.clear()
    user = update.effective_user
    player = get_player_by_tg(user.id)

    if player:
        await update.message.reply_text(
            f"С возвращением, {player['nick']}!",
            reply_markup=main_menu_keyboard()
        )
    else:
        await update.message.reply_text(
            "Добро пожаловать в Strange Faceit!\n\nВыберите действие:",
            reply_markup=start_keyboard()
        )
    return ConversationHandler.END

# ============================== /CANCEL ==============================

async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("Отменено.", reply_markup=start_keyboard())
    return ConversationHandler.END

# ============================== /ADMIN ==============================

async def cmd_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("Нет доступа.")
        return

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("Сброс пароля", callback_data="admin_reset")],
        [InlineKeyboardButton("Создать промокод", callback_data="admin_promo")],
        [InlineKeyboardButton("Назад", callback_data="back")],
    ])
    await update.message.reply_text("Админ-панель", reply_markup=keyboard)

async def admin_promo_create(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.from_user.id != OWNER_ID:
        return

    await query.edit_message_text(
        "Введите промокод и количество дней в формате:\n/promo НАЗВАНИЕ ДНЕЙ\n\nПример:\n/promo SUMMER2024 7"
    )

# ============================== РЕГИСТРАЦИЯ ==============================

async def reg_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if get_player_by_tg(query.from_user.id):
        await query.edit_message_text("Уже зарегистрированы.", reply_markup=start_keyboard())
        return ConversationHandler.END

    await query.edit_message_text(
        "Регистрация\n\nВведите ник (2-32 символа, латиница/цифры/_):"
    )
    return REG_NICK

async def reg_nick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    nick = update.message.text.strip()
    if not valid_nick(nick):
        await update.message.reply_text("Некорректный ник. Попробуйте:")
        return REG_NICK
    if get_player_by_nick(nick):
        await update.message.reply_text("Ник занят. Введите другой:")
        return REG_NICK
    context.user_data["nick"] = nick
    await update.message.reply_text("Введите ID в Standoff 2 (8-15 цифр):")
    return REG_ID

async def reg_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    game_id = update.message.text.strip()
    if not valid_game_id(game_id):
        await update.message.reply_text("Некорректный ID. Попробуйте:")
        return REG_ID
    if get_player_by_game_id(game_id):
        await update.message.reply_text("ID занят. Обратитесь в поддержку.")
        return ConversationHandler.END
    context.user_data["game_id"] = game_id
    await update.message.reply_text("Придумайте пароль (мин. 8 символов, латиница + цифры):")
    return REG_PASS

async def reg_pass(update: Update, context: ContextTypes.DEFAULT_TYPE):
    password = update.message.text.strip()
    if not valid_password(password):
        await update.message.reply_text("Слабый пароль. Попробуйте:")
        return REG_PASS

    tg_id = update.effective_user.id
    nick = context.user_data["nick"]
    game_id = context.user_data["game_id"]

    if create_player(tg_id, nick, game_id, password):
        context.user_data.clear()
        await update.message.reply_text(
            f"Регистрация успешна! Добро пожаловать, {nick}!",
            reply_markup=main_menu_keyboard()
        )
    else:
        await update.message.reply_text("Ошибка регистрации. Попробуйте позже.", reply_markup=start_keyboard())

    return ConversationHandler.END

# ============================== ВХОД ==============================

async def login_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if get_player_by_tg(query.from_user.id):
        await query.edit_message_text("Уже вошли.", reply_markup=main_menu_keyboard())
        return ConversationHandler.END
    await query.edit_message_text("Вход\n\nВведите ник:")
    return LOGIN_NICK

async def login_nick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    nick = update.message.text.strip()
    player = get_player_by_nick(nick)
    if not player:
        await update.message.reply_text("Ник не найден. Попробуйте:")
        return LOGIN_NICK
    context.user_data["login_player"] = player
    await update.message.reply_text("Введите ID:")
    return LOGIN_ID

async def login_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    game_id = update.message.text.strip()
    player = context.user_data.get("login_player")
    if player.get("game_id") != game_id:
        await update.message.reply_text("ID не совпадает. Попробуйте:")
        return LOGIN_ID
    await update.message.reply_text("Введите пароль:")
    return LOGIN_PASS

async def login_pass(update: Update, context: ContextTypes.DEFAULT_TYPE):
    password = update.message.text.strip()
    player = context.user_data.get("login_player")
    if player.get("password") != password:
        await update.message.reply_text("Неверный пароль. Попробуйте:")
        return LOGIN_PASS

    tg_id = update.effective_user.id
    if player.get("Telegram_id") != tg_id:
        update_player(player["Telegram_id"], {"Telegram_id": tg_id})

    context.user_data.clear()
    await update.message.reply_text(
        f"Вход выполнен! С возвращением, {player['nick']}!",
        reply_markup=main_menu_keyboard()
    )
    return ConversationHandler.END

# ============================== ПРОФИЛЬ ==============================

async def menu_profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    player = get_player_by_tg(query.from_user.id)
    if not player:
        await query.edit_message_text("Сначала войдите.", reply_markup=start_keyboard())
        return

    rank = rank_for_elo(player.get("elo", 1000))
    premium_status = "Активен" if is_premium(player) else "Нет"

    text = (
        f"Профиль\n\n"
        f"Ник: {display_nick(player)}\n"
        f"ID: {player.get('game_id')}\n"
        f"Ранг: {rank}\n"
        f"ELO: {player.get('elo', 1000)}\n"
        f"Матчи: {player.get('matches', 0)}\n"
        f"Победы: {player.get('wins', 0)}\n"
        f"Поражения: {player.get('losses', 0)}\n"
        f"MVP: {player.get('mvp', 0)}\n"
        f"Премиум: {premium_status}"
    )
    await query.edit_message_text(text, reply_markup=back_keyboard())

# ============================== ТОП ==============================

async def menu_top(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    players = top_players(10)
    if not players:
        await query.edit_message_text("Топ пуст.", reply_markup=back_keyboard())
        return

    lines = ["Топ игроков:\n"]
    for i, p in enumerate(players, 1):
        tag = "💎" if is_premium(p) else ""
        lines.append(f"{i}. {tag} {p['nick']} — {p['elo']} ELO")
    await query.edit_message_text("\n".join(lines), reply_markup=back_keyboard())

# ============================== СТАТИСТИКА ==============================

async def menu_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    player = get_player_by_tg(query.from_user.id)
    if not player:
        await query.edit_message_text("Сначала войдите.", reply_markup=start_keyboard())
        return

    kills = player.get("kills", 0)
    deaths = player.get("deaths", 0)
    hs = player.get("headshots", 0)
    avg_kd = round(kills / deaths, 2) if deaths else kills
    hs_pct = round((hs / kills) * 100, 1) if kills else 0

    text = (
        f"Статистика\n\n"
        f"AVG K/D: {avg_kd}\n"
        f"HS%: {hs_pct}%\n"
        f"Любимая карта: {player.get('fav_map', '—')}"
    )
    await query.edit_message_text(text, reply_markup=stats_keyboard())

async def extended_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    player = get_player_by_tg(query.from_user.id)
    if not player:
        await query.edit_message_text("Сначала войдите.", reply_markup=start_keyboard())
        return

    if is_premium(player):
        text = (
            f"Расширенная статистика (премиум)\n\n"
            f"Всего матчей: {player.get('matches', 0)}\n"
            f"Винрейт: {round(player.get('wins', 0) / max(1, player.get('matches', 0)) * 100, 1)}%\n"
            f"Любимая карта: {player.get('fav_map', '—')}\n"
            f"MVP: {player.get('mvp', 0)}\n\n"
            f"Доступна полная история матчей."
        )
    else:
        text = "Расширенная статистика доступна только с премиум-подпиской."

    await query.edit_message_text(text, reply_markup=back_keyboard())

# ============================== ПРЕМИУМ ==============================

PREMIUM_PRICES = {
    "1_day": {"label": "1 день — 5 ⭐", "stars": 5, "days": 1},
    "1_week": {"label": "1 неделя — 30 ⭐", "stars": 30, "days": 7},
    "1_month": {"label": "1 месяц — 100 ⭐", "stars": 100, "days": 30},
}

async def menu_premium(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    player = get_player_by_tg(query.from_user.id)
    if not player:
        await query.edit_message_text("Сначала войдите.", reply_markup=start_keyboard())
        return

    text = (
        "Премиум\n\n"
        "Даёт:\n"
        "- Тег 💎\n"
        "- Смену ника\n"
        "- Расширенную статистику\n"
        "- x2 ELO за победу\n\n"
        f"Текущий статус: {'активен' if is_premium(player) else 'не активен'}"
    )
    await query.edit_message_text(text, reply_markup=premium_keyboard())

async def premium_trial(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    player = get_player_by_tg(query.from_user.id)
    if not player:
        await query.edit_message_text("Сначала войдите.", reply_markup=start_keyboard())
        return

    if is_premium(player):
        await query.edit_message_text("У вас уже есть активный премиум.", reply_markup=premium_keyboard())
        return

    until = datetime.now(timezone.utc) + timedelta(days=1)
    if update_player(player["Telegram_id"], {"premium_until": until.isoformat()}):
        await query.edit_message_text(
            "Пробный период активирован! 24 часа премиума.\n\n"
            "Теперь доступно:\n"
            "- Тег 💎\n"
            "- Смена ника\n"
            "- Расширенная статистика\n"
            "- x2 ELO за победу",
            reply_markup=premium_keyboard()
        )
    else:
        await query.edit_message_text("Ошибка активации. Попробуйте позже.", reply_markup=premium_keyboard())

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
        description=f"Премиум-подписка {plan['label']}",
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
        await update.message.reply_text("Ошибка активации. Обратитесь в поддержку.")
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
    if update_player(tg_id, {"premium_until": new_until.isoformat()}):
        await update.message.reply_text(
            f"Премиум активирован до {new_until.strftime('%Y-%m-%d %H:%M')} UTC!\n\n"
            f"Теперь доступно:\n"
            f"- Тег 💎\n"
            f"- Смена ника\n"
            f"- Расширенная статистика\n"
            f"- x2 ELO за победу",
            reply_markup=main_menu_keyboard()
        )
    else:
        await update.message.reply_text("Ошибка активации. Обратитесь в поддержку.")

# ============================== ПРОМОКОДЫ ==============================

async def promo_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "Промокоды\n\nВведите промокод, чтобы получить бесплатные дни премиума.",
        reply_markup=promo_keyboard()
    )

async def promo_enter(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("Введите промокод:")
    return PROMO_ENTER

async def promo_apply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    code = update.message.text.strip().upper()
    player = get_player_by_tg(update.effective_user.id)
    if not player:
        await update.message.reply_text("Сначала войдите.", reply_markup=start_keyboard())
        return ConversationHandler.END

    promo = PROMOCODES.get(code)
    if not promo:
        await update.message.reply_text("Неверный промокод.", reply_markup=premium_keyboard())
        return ConversationHandler.END

    days = promo.get("days", 0)
    now = datetime.now(timezone.utc)
    current = player.get("premium_until")
    if current:
        try:
            current_dt = datetime.fromisoformat(current.replace("Z", "+00:00"))
            if current_dt > now:
                now = current_dt
        except:
            pass

    new_until = now + timedelta(days=days)
    if update_player(player["Telegram_id"], {"premium_until": new_until.isoformat()}):
        await update.message.reply_text(
            f"Промокод активирован! +{days} дней премиума.",
            reply_markup=premium_keyboard()
        )
    else:
        await update.message.reply_text("Ошибка активации.", reply_markup=premium_keyboard())

    return ConversationHandler.END

# ============================== /PROMO (админ) ==============================

async def cmd_promo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("Нет доступа.")
        return

    args = context.args
    if len(args) < 2:
        await update.message.reply_text("Использование: /promo НАЗВАНИЕ ДНЕЙ")
        return

    name = args[0].upper()
    try:
        days = int(args[1])
    except:
        await update.message.reply_text("Количество дней должно быть числом.")
        return

    if days <= 0:
        await update.message.reply_text("Количество дней должно быть больше 0.")
        return

    PROMOCODES[name] = {"days": days}
    await update.message.reply_text(f"Промокод {name} создан! +{days} дней премиума.")

# ============================== ВСПОМОГАТЕЛЬНОЕ ==============================

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
    await query.answer()
    context.user_data.clear()
    await query.edit_message_text("Вы вышли.", reply_markup=start_keyboard())

# ============================== ПОДДЕРЖКА ==============================

SUPPORT_TICKETS = {}
_ticket_counter = 0

def next_ticket():
    global _ticket_counter
    _ticket_counter += 1
    return _ticket_counter

async def support_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("Напишите ваше сообщение в поддержку:")
    return SUPPORT_MSG

async def support_receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ticket_id = next_ticket()
    SUPPORT_TICKETS[ticket_id] = {"user_id": user.id, "username": user.username or str(user.id)}

    if OWNER_ID:
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("Ответить", callback_data=f"reply_{ticket_id}")]])
        await context.bot.send_message(
            OWNER_ID,
            f"Новый тикет #{ticket_id}\nОт: @{SUPPORT_TICKETS[ticket_id]['username']}\n\n{update.message.text}",
            reply_markup=kb
        )

    await update.message.reply_text("Сообщение отправлено. Мы ответим вам здесь.", reply_markup=main_menu_keyboard())
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
            f"Ответ поддержки:\n\n{update.message.text}"
        )
        await update.message.reply_text("Ответ отправлен.")
    except:
        await update.message.reply_text("Не удалось отправить.")

    SUPPORT_TICKETS.pop(ticket_id, None)
    context.user_data.clear()
    return ConversationHandler.END

# ============================== СМЕНА НИКА ==============================

async def changenick_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    player = get_player_by_tg(query.from_user.id)
    if not player:
        await query.edit_message_text("Сначала войдите.", reply_markup=start_keyboard())
        return ConversationHandler.END

    if not is_premium(player):
        await query.edit_message_text("Смена ника только для премиум!", reply_markup=back_keyboard())
        return ConversationHandler.END

    await query.edit_message_text("Введите новый ник (2-32 символа, латиница/цифры/_):")
    return REG_NICK

async def changenick_apply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    nick = update.message.text.strip()
    if not valid_nick(nick):
        await update.message.reply_text("Некорректный ник. Попробуйте:")
        return REG_NICK
    if get_player_by_nick(nick):
        await update.message.reply_text("Ник занят. Введите другой:")
        return REG_NICK

    tg_id = update.effective_user.id
    if update_player(tg_id, {"nick": nick}):
        await update.message.reply_text(f"Ник изменён на {nick}!", reply_markup=main_menu_keyboard())
    else:
        await update.message.reply_text("Ошибка смены ника.", reply_markup=main_menu_keyboard())
    return ConversationHandler.END

# ============================== НОВЫЕ ОБРАБОТЧИКИ ==============================

async def menu_find_match(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("Поиск матча в разработке...", reply_markup=back_keyboard())

async def menu_party(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("Пати в разработке...", reply_markup=back_keyboard())

async def menu_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    player = get_player_by_tg(query.from_user.id)
    if not player:
        await query.edit_message_text("Сначала войдите.", reply_markup=start_keyboard())
        return
    await query.edit_message_text("История матчей в разработке...", reply_markup=back_keyboard())

async def menu_reports(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("Жалобы в разработке...", reply_markup=back_keyboard())

# ============================== АДМИН: СБРОС ПАРОЛЯ ==============================

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
    kb.append([InlineKeyboardButton("Назад", callback_data="back")])
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
        await update.message.reply_text("Слабый пароль. Попробуйте:")
        return RESET_PASS

    tg_id = context.user_data.get("reset_tg_id")
    if update_player(tg_id, {"password": password}):
        await update.message.reply_text("Пароль сброшен.")
        try:
            await context.bot.send_message(tg_id, "Ваш пароль был сброшен администратором.")
        except:
            pass
    else:
        await update.message.reply_text("Ошибка сброса.")

    context.user_data.clear()
    return ConversationHandler.END

# ============================== ОШИБКИ ==============================

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Update {update} caused error: {context.error}")

# ============================== MAIN ==============================

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

    try:
        resp = requests.get(f"https://api.telegram.org/bot{BOT_TOKEN}/deleteWebhook")
        logger.info(f"webhook deleted: {resp.json()}")
    except Exception as e:
        logger.error(f"webhook delete error: {e}")

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("admin", cmd_admin))
    app.add_handler(CommandHandler("cancel", cmd_cancel))
    app.add_handler(CommandHandler("promo", cmd_promo))

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

    changenick_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(changenick_start, pattern="^changenick$")],
        states={
            REG_NICK: [MessageHandler(filters.TEXT & ~filters.COMMAND, changenick_apply)],
        },
        fallbacks=[CommandHandler("cancel", cmd_cancel)],
    )
    app.add_handler(changenick_conv)

    support_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(support_start, pattern="^support$")],
        states={
            SUPPORT_MSG: [MessageHandler(filters.TEXT & ~filters.COMMAND, support_receive)],
        },
        fallbacks=[CommandHandler("cancel", cmd_cancel)],
    )
    app.add_handler(support_conv)

    admin_reply_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_reply_start, pattern="^reply_")],
        states={
            ADMIN_REPLY: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_reply_send)],
        },
        fallbacks=[CommandHandler("cancel", cmd_cancel)],
    )
    app.add_handler(admin_reply_conv)

    promo_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(promo_enter, pattern="^promo_enter$")],
        states={
            PROMO_ENTER: [MessageHandler(filters.TEXT & ~filters.COMMAND, promo_apply)],
        },
        fallbacks=[CommandHandler("cancel", cmd_cancel)],
    )
    app.add_handler(promo_conv)

    admin_reset_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_reset_list, pattern="^admin_reset$")],
        states={
            ADMIN_REPLY: [CallbackQueryHandler(admin_reset_pick, pattern="^reset_")],
            RESET_PASS: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_reset_apply)],
        },
        fallbacks=[CommandHandler("cancel", cmd_cancel)],
    )
    app.add_handler(admin_reset_conv)

    app.add_handler(CallbackQueryHandler(back, pattern="^back$"))
    app.add_handler(CallbackQueryHandler(logout, pattern="^logout$"))
    app.add_handler(CallbackQueryHandler(menu_profile, pattern="^profile$"))
    app.add_handler(CallbackQueryHandler(menu_top, pattern="^top$"))
    app.add_handler(CallbackQueryHandler(menu_stats, pattern="^stats$"))
    app.add_handler(CallbackQueryHandler(extended_stats, pattern="^extended_stats$"))
    app.add_handler(CallbackQueryHandler(menu_premium, pattern="^premium_menu$"))
    app.add_handler(CallbackQueryHandler(premium_trial, pattern="^premium_trial$"))
    app.add_handler(CallbackQueryHandler(premium_buy, pattern="^buy_"))
    app.add_handler(CallbackQueryHandler(promo_menu, pattern="^promo_menu$"))
    app.add_handler(CallbackQueryHandler(admin_promo_create, pattern="^admin_promo$"))
    app.add_handler(CallbackQueryHandler(menu_find_match, pattern="^find_match$"))
    app.add_handler(CallbackQueryHandler(menu_party, pattern="^party$"))
    app.add_handler(CallbackQueryHandler(menu_history, pattern="^history$"))
    app.add_handler(CallbackQueryHandler(menu_reports, pattern="^reports$"))

    app.add_handler(PreCheckoutQueryHandler(precheckout_callback))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment))

    app.add_error_handler(error_handler)

    logger.info("Bot started, polling...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()

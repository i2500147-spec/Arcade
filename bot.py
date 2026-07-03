import os
import re
import time
import logging
import asyncio
import threading
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
from google import genai
import PIL.Image

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

# Gemini AI
gemini_client = genai.Client()

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
        return r.json() if r.status_code < 400 else []
    except Exception as e:
        logger.error(f"sb_select error: {e}")
        return []

async def sb_insert(table, data, upsert=False):
    try:
        headers = {"Prefer": "return=representation"}
        if upsert:
            headers["Prefer"] = "resolution=merge-duplicates,return=representation"
        r = await get_client().post(f"/{table}", json=data, headers=headers)
        return r.json() if r.status_code < 400 else []
    except Exception as e:
        logger.error(f"sb_insert error: {e}")
        return []

async def sb_update(table, params, data):
    try:
        r = await get_client().patch(f"/{table}", params=params, json=data, headers={"Prefer": "return=representation"})
        return r.json() if r.status_code < 400 else []
    except Exception as e:
        logger.error(f"sb_update error: {e}")
        return []

async def sb_delete(table, params):
    try:
        r = await get_client().delete(f"/{table}", params=params)
        return r.status_code < 400
    except Exception as e:
        logger.error(f"sb_delete error: {e}")
        return False

async def get_player(tid):
    rows = await sb_select("players", {"telegram_id": f"eq.{tid}", "limit": "1"})
    return rows[0] if rows else None

async def get_player_by_game_id(gid):
    rows = await sb_select("players", {"game_id": f"eq.{gid}", "limit": "1"})
    return rows[0] if rows else None

async def get_player_by_nick(nick):
    rows = await sb_select("players", {"nick": f"eq.{nick}", "limit": "1"})
    return rows[0] if rows else None

async def is_banned(tid):
    rows = await sb_select("bans", {"telegram_id": f"eq.{tid}", "limit": "1"})
    return len(rows) > 0

def calc_rank(elo):
    for low, high, emoji, rank in RANKS:
        if low <= elo <= high:
            return rank, emoji
    return 1, "🥉"

def is_premium_active(player):
    until = player.get("premium_until")
    if not until:
        return False
    try:
        dt = datetime.fromisoformat(until.replace("Z", "+00:00"))
        return dt > datetime.now(timezone.utc)
    except:
        return False

def format_premium_status(player):
    until = player.get("premium_until")
    if not until:
        return "нет"
    try:
        dt = datetime.fromisoformat(until.replace("Z", "+00:00"))
        if dt < datetime.now(timezone.utc):
            return "истёк"
        return dt.strftime("%d.%m.%Y %H:%M UTC")
    except:
        return "нет"

# ============================================================
# GEMINI AI CHECK
# ============================================================

async def check_profile_with_gemini(photo_id, expected_id, expected_nick, context):
    """Проверяет скриншот профиля через Gemini AI"""
    try:
        # Скачиваем фото
        file = await context.bot.get_file(photo_id)
        file_path = f"temp_{int(time.time())}.jpg"
        await file.download_to_drive(file_path)
        
        # Открываем изображение
        img = PIL.Image.open(file_path)
        
        # Отправляем в Gemini
        response = gemini_client.interactions.create(
            model="gemini-2.0-flash-exp",
            input=[
                "На этом скриншоте из игры Standoff 2 найди ID игрока (8-15 цифр) и его ник. Ответь строго в формате: ID: [цифры] | Ник: [текст]",
                img
            ]
        )
        
        os.remove(file_path)
        
        # Парсим ответ
        text = response.output_text
        id_match = re.search(r'ID:\s*(\d{8,15})', text)
        nick_match = re.search(r'Ник:\s*([A-Za-z0-9_]+)', text)
        
        if not id_match or not nick_match:
            return False, "Не удалось распознать ID или ник на скриншоте"
        
        found_id = id_match.group(1)
        found_nick = nick_match.group(1)
        
        if found_id != expected_id:
            return False, f"ID не совпадает (на скриншоте: {found_id})"
        
        if found_nick.lower() != expected_nick.lower():
            return False, f"Ник не совпадает (на скриншоте: {found_nick})"
        
        return True, "✅ Проверка пройдена!"
        
    except Exception as e:
        logger.error(f"Gemini check error: {e}")
        return None, f"Ошибка Gemini: {e}"

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

# ============================================================
# STATE
# ============================================================

USER_STATE = {}

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

def admin_approve_kb(tid):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Впустить", callback_data=f"approve_{tid}"),
         InlineKeyboardButton("❌ Отказать", callback_data=f"reject_{tid}")]
    ])

def premium_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⭐ 1 день — 50 звёзд", callback_data="premium_1_day")],
        [InlineKeyboardButton("⭐ 1 неделя — 350 звёзд", callback_data="premium_1_week")],
        [InlineKeyboardButton("⭐ 1 месяц — 1000 звёзд", callback_data="premium_1_month")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="back_to_menu")],
    ])

def platforms_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📱 Phone", callback_data="platform_Phone")],
        [InlineKeyboardButton("💻 PC", callback_data="platform_PC")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="back_to_menu")],
    ])

def lobbies_kb(platform, lobbies):
    buttons = []
    for i in range(6):
        count = len(lobbies.get(i, []))
        status = f"{count}/10"
        if count < 10:
            buttons.append([InlineKeyboardButton(f"Лобби {i+1} ({status})", callback_data=f"join_lobby_{platform}_{i}")])
        else:
            buttons.append([InlineKeyboardButton(f"🔒 Лобби {i+1} ({status})", callback_data="lobby_full")])
    buttons.append([InlineKeyboardButton("⬅️ Назад", callback_data="find_match")])
    return InlineKeyboardMarkup(buttons)

# ============================================================
# COMMANDS
# ============================================================

async def cmd_start(update, context):
    user = update.effective_user
    clear_state(user.id)

    if await is_banned(user.id):
        await update.message.reply_text("⛔ Вы забанены.")
        return

    if not await check_subscription(context, user.id):
        await update.message.reply_text("Подпишись на наш чат:", reply_markup=subscription_prompt_kb())
        return

    player = await get_player(user.id)
    if player and player.get("approved"):
        await update.message.reply_text(f"С возвращением, {player.get('nick')}!", reply_markup=main_kb())
        return

    pending = await sb_select("pending", {"telegram_id": f"eq.{user.id}", "limit": "1"})
    if pending:
        await update.message.reply_text("⏳ Ваша заявка на рассмотрении.")
        return

    await update.message.reply_text("Добро пожаловать в Strange Faceit!", reply_markup=entry_kb())

def subscription_prompt_kb():
    buttons = []
    if CHAT_LINK:
        buttons.append([InlineKeyboardButton("📢 Подписаться", url=CHAT_LINK)])
    buttons.append([InlineKeyboardButton("✅ Я подписался", callback_data="check_sub")])
    return InlineKeyboardMarkup(buttons)

# ============================================================
# CALLBACKS
# ============================================================

async def cb_check_sub(update, context):
    q = update.callback_query
    await q.answer()
    if await check_subscription(context, q.from_user.id):
        await q.edit_message_text("Спасибо!", reply_markup=entry_kb())
    else:
        await q.answer("Вы ещё не подписались", show_alert=True)

async def cb_back_to_menu(update, context):
    q = update.callback_query
    await q.answer()
    clear_state(q.from_user.id)
    player = await get_player(q.from_user.id)
    if player and player.get("approved"):
        await q.edit_message_text("Главное меню:", reply_markup=main_kb())
    else:
        await q.edit_message_text("Выберите действие:", reply_markup=entry_kb())

async def cb_support(update, context):
    q = update.callback_query
    await q.answer()
    text = "🆘 Поддержка\n\nОпишите проблему в чате."
    if CHAT_LINK:
        text += f"\n\n{CHAT_LINK}"
    await q.edit_message_text(text, reply_markup=back_kb())

async def cb_register_start(update, context):
    q = update.callback_query
    await q.answer()
    user_id = q.from_user.id
    if await get_player(user_id):
        await q.edit_message_text("Вы уже зарегистрированы.", reply_markup=back_kb())
        return
    set_state(user_id, "register", "await_game_id")
    await q.edit_message_text("📝 Регистрация\n\nВведите ID в Standoff 2 (8-15 цифр):")

async def cb_login_start(update, context):
    q = update.callback_query
    await q.answer()
    user_id = q.from_user.id
    set_state(user_id, "login", "await_game_id")
    await q.edit_message_text("🔑 Вход\n\nВведите ваш ID:")

async def cb_profile(update, context):
    q = update.callback_query
    await q.answer()
    player = await get_player(q.from_user.id)
    if not player:
        await q.edit_message_text("Профиль не найден.", reply_markup=back_kb())
        return
    rank, emoji = calc_rank(player.get("elo", 1000))
    matches = player.get("matches", 0)
    wins = player.get("wins", 0)
    losses = player.get("losses", 0)
    winrate = round((wins / matches) * 100, 1) if matches else 0
    premium = " 💎" if is_premium_active(player) else ""
    text = (
        f"👤 Профиль\n\n"
        f"Ник: {player.get('nick')}{premium}\n"
        f"ID: {player.get('game_id')}\n"
        f"Ранг: {emoji} #{rank}\n"
        f"ELO: {player.get('elo', 1000)}\n"
        f"Матчи: {matches}\n"
        f"Победы: {wins}\n"
        f"Поражения: {losses}\n"
        f"Винрейт: {winrate}%\n"
        f"MVP: {player.get('mvp', 0)}\n"
        f"💎 Премиум: {format_premium_status(player)}"
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
        premium = " 💎" if is_premium_active(p) else ""
        lines.append(f"{i}. {p.get('nick')}{premium} — {p.get('elo')} ELO {emoji}")
    await q.edit_message_text("\n".join(lines), reply_markup=back_kb())

async def cb_stats(update, context):
    q = update.callback_query
    await q.answer()
    player = await get_player(q.from_user.id)
    if not player:
        await q.edit_message_text("Профиль не найден.", reply_markup=back_kb())
        return
    kills = player.get("kills", 0)
    deaths = player.get("deaths", 0)
    hs = player.get("headshots", 0)
    avg_kd = round(kills / deaths, 2) if deaths else float(kills)
    hs_pct = round((hs / kills) * 100, 1) if kills else 0
    text = (
        f"📊 Статистика\n\n"
        f"AVG K/D: {avg_kd}\n"
        f"HS%: {hs_pct}%\n"
        f"Любимая карта: {player.get('fav_map') or '—'}"
    )
    await q.edit_message_text(text, reply_markup=back_kb())

async def cb_premium_menu(update, context):
    q = update.callback_query
    await q.answer()
    text = (
        "💎 ПРЕМИУМ\n\n"
        "🔥 x2 ELO, любой тег, бесплатные турниры\n"
        "⭐ 1 день — 50 звёзд\n"
        "⭐ 1 неделя — 350 звёзд\n"
        "⭐ 1 месяц — 1000 звёзд"
    )
    await q.edit_message_text(text, reply_markup=premium_kb())

async def cb_find_match(update, context):
    q = update.callback_query
    await q.answer()
    await q.edit_message_text("Выберите платформу:", reply_markup=platforms_kb())

async def cb_platform(update, context):
    q = update.callback_query
    await q.answer()
    platform = q.data.split("_")[1]
    await q.edit_message_text(f"{platform} Лобби:", reply_markup=lobbies_kb(platform, {}))

# ============================================================
# PLACEHOLDERS
# ============================================================

async def cb_party_menu(update, context):
    q = update.callback_query
    await q.answer("В разработке", show_alert=True)

async def cb_history(update, context):
    q = update.callback_query
    await q.answer("В разработке", show_alert=True)

async def cb_reports_menu(update, context):
    q = update.callback_query
    await q.answer("В разработке", show_alert=True)

# ============================================================
# REGISTRATION HANDLERS
# ============================================================

GAME_ID_RE = re.compile(r"^\d{8,15}$")

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

async def handle_register_step(update, context, step, text):
    user = update.effective_user
    state = get_state(user.id)

    if step == "await_game_id":
        if not GAME_ID_RE.match(text):
            await update.message.reply_text("❌ 8-15 цифр. Попробуйте:")
            return
        if await get_player_by_game_id(text):
            await update.message.reply_text("❌ ID занят. Введите другой:")
            return
        state["data"]["game_id"] = text
        state["step"] = "await_nick"
        await update.message.reply_text("Введите игровой ник:")

    elif step == "await_nick":
        if not (2 <= len(text) <= 32):
            await update.message.reply_text("❌ 2-32 символа. Попробуйте:")
            return
        if await get_player_by_nick(text):
            await update.message.reply_text("❌ Ник занят. Введите другой:")
            return
        state["data"]["nick"] = text
        state["step"] = "await_photo"
        await update.message.reply_text("📷 Отправьте скриншот профиля:")

# ============================================================
# REGISTER PHOTO WITH AI
# ============================================================

async def handle_register_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    state = get_state(user.id)
    if not state or state["flow"] != "register" or state["step"] != "await_photo":
        return

    photo_id = update.message.photo[-1].file_id
    data = state["data"]
    
    # Проверяем через Gemini
    await update.message.reply_text("🤖 Проверяю скриншот через AI...")
    
    is_valid, message = await check_profile_with_gemini(
        photo_id, 
        data["game_id"], 
        data["nick"], 
        context
    )
    
    if is_valid is True:
        # ✅ Всё правильно — регистрируем сразу
        await sb_insert("players", {
            "telegram_id": user.id,
            "game_id": data["game_id"],
            "nick": data["nick"],
            "photo": photo_id,
            "approved": True,
            "elo": 1000,
            "wins": 0,
            "losses": 0,
            "matches": 0,
            "mvp": 0,
        }, upsert=True)
        clear_state(user.id)
        await update.message.reply_text(
            f"✅ {message}\n\nРегистрация завершена! Используйте /start.",
            reply_markup=main_kb()
        )
        return
    
    elif is_valid is False:
        # ❌ Не совпадает — отправляем админу
        await sb_insert("pending", {
            "telegram_id": user.id,
            "game_id": data["game_id"],
            "nick": data["nick"],
            "photo": photo_id,
        }, upsert=True)
        clear_state(user.id)
        
        await update.message.reply_text(
            f"⚠️ {message}\n\nЗаявка отправлена администраторам на проверку."
        )
        
        if ADMIN_CHAT_ID:
            caption = (
                f"📝 Заявка на регистрацию (AI не пропустил)\n"
                f"Telegram: @{user.username or '—'} (ID: {user.id})\n"
                f"ID: {data['game_id']}\n"
                f"Ник: {data['nick']}\n"
                f"Причина: {message}"
            )
            await context.bot.send_photo(
                ADMIN_CHAT_ID,
                photo_id,
                caption=caption,
                reply_markup=admin_approve_kb(user.id),
            )
        return
    
    else:
        # ⚠️ Ошибка AI — отправляем админу
        await sb_insert("pending", {
            "telegram_id": user.id,
            "game_id": data["game_id"],
            "nick": data["nick"],
            "photo": photo_id,
        }, upsert=True)
        clear_state(user.id)
        
        await update.message.reply_text(
            f"⚠️ {message}\n\nЗаявка отправлена администраторам на проверку."
        )
        
        if ADMIN_CHAT_ID:
            caption = (
                f"📝 Заявка на регистрацию (ошибка AI)\n"
                f"Telegram: @{user.username or '—'} (ID: {user.id})\n"
                f"ID: {data['game_id']}\n"
                f"Ник: {data['nick']}"
            )
            await context.bot.send_photo(
                ADMIN_CHAT_ID,
                photo_id,
                caption=caption,
                reply_markup=admin_approve_kb(user.id),
            )
        return

# ============================================================
# APPROVE / REJECT
# ============================================================

async def cb_approve_registration(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if q.from_user.id not in ADMIN_IDS and q.from_user.id != OWNER_ID:
        await q.answer("Нет прав", show_alert=True)
        return

    tid = int(q.data.split("_")[1])

    existing = await get_player(tid)
    if existing:
        await sb_delete("pending", {"telegram_id": f"eq.{tid}"})
        await q.edit_message_caption(caption=(q.message.caption or "") + "\n\n✅ Уже зарегистрирован")
        return

    pending = await sb_select("pending", {"telegram_id": f"eq.{tid}", "limit": "1"})
    if not pending:
        await q.edit_message_caption(caption=(q.message.caption or "") + "\n\n⚠️ Заявка не найдена")
        return

    p = pending[0]
    await sb_insert("players", {
        "telegram_id": tid,
        "game_id": p["game_id"],
        "nick": p["nick"],
        "photo": p["photo"],
        "approved": True,
        "elo": 1000,
        "wins": 0,
        "losses": 0,
        "matches": 0,
        "mvp": 0,
    }, upsert=True)
    await sb_delete("pending", {"telegram_id": f"eq.{tid}"})

    await q.edit_message_caption(caption=(q.message.caption or "") + "\n\n✅ Одобрено")
    await context.bot.send_message(tid, "✅ Заявка одобрена! Используйте /start.")

async def cb_reject_registration(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if q.from_user.id not in ADMIN_IDS and q.from_user.id != OWNER_ID:
        await q.answer("Нет прав", show_alert=True)
        return

    tid = int(q.data.split("_")[1])

    existing = await get_player(tid)
    if existing:
        await sb_delete("pending", {"telegram_id": f"eq.{tid}"})
        await q.edit_message_caption(caption=(q.message.caption or "") + "\n\n✅ Уже зарегистрирован")
        return

    pending = await sb_select("pending", {"telegram_id": f"eq.{tid}", "limit": "1"})
    if not pending:
        await q.edit_message_caption(caption=(q.message.caption or "") + "\n\n⚠️ Заявка уже обработана")
        return

    await sb_delete("pending", {"telegram_id": f"eq.{tid}"})
    await q.edit_message_caption(caption=(q.message.caption or "") + "\n\n❌ Отказано")
    await context.bot.send_message(tid, "❌ Заявка отклонена.")

# ============================================================
# LOGIN HANDLERS
# ============================================================

async def handle_login_step(update, context, step, text):
    user = update.effective_user
    state = get_state(user.id)

    if step == "await_game_id":
        player = await get_player_by_game_id(text)
        if not player:
            await update.message.reply_text("❌ Игрок не найден.")
            return
        state["data"]["player"] = player
        state["step"] = "await_nick"
        await update.message.reply_text("Введите ник:")

    elif step == "await_nick":
        player = state["data"]["player"]
        if player.get("nick") != text:
            await update.message.reply_text("❌ Ник не совпадает.")
            return
        if player.get("banned"):
            clear_state(user.id)
            await update.message.reply_text("⛔ Аккаунт забанен.")
            return
        if not player.get("approved"):
            clear_state(user.id)
            await update.message.reply_text("⏳ Аккаунт не подтверждён.")
            return

        await sb_update("players", {"telegram_id": f"eq.{player['telegram_id']}"}, {"telegram_id": user.id})
        clear_state(user.id)
        await update.message.reply_text(f"✅ Вход выполнен! С возвращением, {player.get('nick')}!", reply_markup=main_kb())

# ============================================================
# HEALTH CHECK
# ============================================================

flask_app = Flask(__name__)

@flask_app.route("/health")
def health():
    count = None
    try:
        with httpx.Client(base_url=f"{SUPABASE_URL}/rest/v1", headers={"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}, timeout=5) as c:
            r = c.head("/players", params={"select": "telegram_id"})
            if "content-range" in r.headers:
                count = int(r.headers["content-range"].split("/")[-1])
    except:
        pass
    return jsonify({"status": "ok", "uptime": round(time.time() - BOT_START_TIME, 1), "players": count})

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
    app.add_handler(CallbackQueryHandler(cb_approve_registration, pattern="^approve_\\d+$"))
    app.add_handler(CallbackQueryHandler(cb_reject_registration, pattern="^reject_\\d+$"))

    app.add_handler(CallbackQueryHandler(cb_profile, pattern="^profile$"))
    app.add_handler(CallbackQueryHandler(cb_leaderboard, pattern="^leaderboard$"))
    app.add_handler(CallbackQueryHandler(cb_stats, pattern="^stats$"))
    app.add_handler(CallbackQueryHandler(cb_history, pattern="^history$"))
    app.add_handler(CallbackQueryHandler(cb_find_match, pattern="^find_match$"))
    app.add_handler(CallbackQueryHandler(cb_party_menu, pattern="^party_menu$"))
    app.add_handler(CallbackQueryHandler(cb_reports_menu, pattern="^reports_menu$"))
    app.add_handler(CallbackQueryHandler(cb_premium_menu, pattern="^premium_menu$"))
    app.add_handler(CallbackQueryHandler(cb_platform, pattern="^platform_"))

    app.add_handler(MessageHandler(filters.PHOTO, handle_register_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_message))

    logger.info("🤖 Strange Faceit запущен!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()

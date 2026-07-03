import os
import re
import json
import time
import logging
import asyncio
import threading
from datetime import datetime, timezone, timedelta
from logging.handlers import RotatingFileHandler

import httpx
from flask import Flask, jsonify
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    Application,
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
ADMIN_IDS = set(
    int(x) for x in os.environ.get("ADMIN_IDS", "").split(",") if x.strip().isdigit()
)
GENERAL_CHAT_ID = os.environ.get("GENERAL_CHAT_ID")
ADMIN_CHAT_ID = os.environ.get("ADMIN_CHAT_ID")
CHAT_LINK = os.environ.get("CHAT_LINK", "")
REQUIRE_SUBSCRIPTION = os.environ.get("REQUIRE_SUBSCRIPTION", "false").lower() == "true"
SUBSCRIPTION_CHAT_ID = os.environ.get("SUBSCRIPTION_CHAT_ID")
OWNER_ID = int(os.environ.get("OWNER_ID", "0") or 0)
SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

MAPS = ["Sandstone", "Rust", "Province", "Breeze", "Dune", "Zone 7", "Hanami"]
LOBBY_PLATFORMS = ["Phone", "PC"]
LOBBY_COUNT = 6
LOBBY_SIZE = 10
CALIBRATION_MATCHES = 10

# НОВЫЕ ЦЕНЫ (снижены в 3 раза)
PREMIUM_PRICES = {
    "1_day": {"label": "1 день", "stars": 5, "days": 1},
    "1_week": {"label": "1 неделя", "stars": 39, "days": 7},
    "1_month": {"label": "1 месяц", "stars": 111, "days": 30},
}

RANKS = [
    (0, 799, "🥉", 1),
    (800, 899, "🥉", 2),
    (900, 999, "🥉", 3),
    (1000, 1099, "🥈", 4),
    (1100, 1199, "🥈", 5),
    (1200, 1299, "🥈", 6),
    (1300, 1399, "🥇", 7),
    (1400, 1499, "🥇", 8),
    (1500, 1699, "💎", 9),
    (1700, 999999, "👑", 10),
]

# ============================================================
# LOGGING
# ============================================================

os.makedirs("logs", exist_ok=True)
logger = logging.getLogger("strange_faceit")
logger.setLevel(logging.INFO)

_file_handler = RotatingFileHandler(
    "logs/bot.log", maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
)
_file_handler.setFormatter(
    logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
)
_console_handler = logging.StreamHandler()
_console_handler.setFormatter(
    logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
)
logger.addHandler(_file_handler)
logger.addHandler(_console_handler)

BOT_START_TIME = time.time()

# ============================================================
# SUPABASE HELPERS (исправлены ошибки)
# ============================================================

_http_client: httpx.AsyncClient | None = None

def get_http_client() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None:
        _http_client = httpx.AsyncClient(
            base_url=f"{SUPABASE_URL}/rest/v1",
            headers={
                "apikey": SUPABASE_KEY,
                "Authorization": f"Bearer {SUPABASE_KEY}",
                "Content-Type": "application/json",
            },
            timeout=30.0,
        )
    return _http_client

async def sb_select(table: str, params: dict | None = None) -> list[dict]:
    try:
        client = get_http_client()
        r = await client.get(f"/{table}", params=params or {})
        if r.status_code >= 400:
            logger.error(f"sb_select {table} failed: {r.status_code} {r.text}")
            return []
        return r.json()
    except Exception as e:
        logger.error(f"sb_select {table} exception: {e}")
        return []

async def sb_insert(table: str, data: dict | list[dict], upsert: bool = False) -> list[dict]:
    try:
        client = get_http_client()
        headers = {"Prefer": "return=representation"}
        if upsert:
            headers["Prefer"] = "resolution=merge-duplicates,return=representation"
        r = await client.post(f"/{table}", json=data, headers=headers)
        if r.status_code >= 400:
            logger.error(f"sb_insert {table} failed: {r.status_code} {r.text}")
            return []
        return r.json()
    except Exception as e:
        logger.error(f"sb_insert {table} exception: {e}")
        return []

async def sb_update(table: str, params: dict, data: dict) -> list[dict]:
    try:
        client = get_http_client()
        r = await client.patch(
            f"/{table}",
            params=params,
            json=data,
            headers={"Prefer": "return=representation"},
        )
        if r.status_code >= 400:
            logger.error(f"sb_update {table} failed: {r.status_code} {r.text}")
            return []
        return r.json()
    except Exception as e:
        logger.error(f"sb_update {table} exception: {e}")
        return []

async def sb_delete(table: str, params: dict) -> bool:
    try:
        client = get_http_client()
        r = await client.delete(f"/{table}", params=params)
        if r.status_code >= 400:
            logger.error(f"sb_delete {table} failed: {r.status_code} {r.text}")
            return False
        return True
    except Exception as e:
        logger.error(f"sb_delete {table} exception: {e}")
        return False

# ---- domain-specific helpers ----

async def get_player(telegram_id: int) -> dict | None:
    rows = await sb_select("players", {"telegram_id": f"eq.{telegram_id}", "limit": "1"})
    return rows[0] if rows else None

async def get_player_by_game_id(game_id: str) -> dict | None:
    rows = await sb_select("players", {"game_id": f"eq.{game_id}", "limit": "1"})
    return rows[0] if rows else None

async def get_player_by_nick(nick: str) -> dict | None:
    rows = await sb_select("players", {"nick": f"eq.{nick}", "limit": "1"})
    return rows[0] if rows else None

async def is_banned(telegram_id: int) -> bool:
    rows = await sb_select("bans", {"telegram_id": f"eq.{telegram_id}", "limit": "1"})
    return len(rows) > 0

def calc_rank(elo: int) -> tuple[int, str]:
    for low, high, emoji, rank in RANKS:
        if low <= elo <= high:
            return rank, emoji
    return 1, "🥉"

def format_premium_status(player: dict) -> str:
    until = player.get("premium_until")
    if not until:
        return "нет"
    try:
        dt = datetime.fromisoformat(until.replace("Z", "+00:00"))
    except Exception:
        return "нет"
    if dt < datetime.now(timezone.utc):
        return "истёк"
    return dt.strftime("%d.%m.%Y %H:%M UTC")

def is_premium_active(player: dict) -> bool:
    until = player.get("premium_until")
    if not until:
        return False
    try:
        dt = datetime.fromisoformat(until.replace("Z", "+00:00"))
    except Exception:
        return False
    return dt > datetime.now(timezone.utc)

# ============================================================
# SUBSCRIPTION CHECK
# ============================================================

async def check_subscription(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> bool:
    if not REQUIRE_SUBSCRIPTION or not SUBSCRIPTION_CHAT_ID:
        return True
    try:
        member = await context.bot.get_chat_member(SUBSCRIPTION_CHAT_ID, user_id)
        return member.status in ("member", "administrator", "creator")
    except Exception as e:
        logger.warning(f"subscription check failed for {user_id}: {e}")
        return False

def subscription_prompt_kb() -> InlineKeyboardMarkup:
    buttons = []
    if CHAT_LINK:
        buttons.append([InlineKeyboardButton("📢 Подписаться", url=CHAT_LINK)])
    buttons.append([InlineKeyboardButton("✅ Я подписался", callback_data="check_sub")])
    return InlineKeyboardMarkup(buttons)

# ============================================================
# CONVERSATION STATE
# ============================================================

USER_STATE: dict[int, dict] = {}

def set_state(user_id: int, flow: str, step: str, data: dict | None = None):
    USER_STATE[user_id] = {"flow": flow, "step": step, "data": data or {}}

def get_state(user_id: int) -> dict | None:
    return USER_STATE.get(user_id)

def clear_state(user_id: int):
    USER_STATE.pop(user_id, None)

# ============================================================
# KEYBOARDS
# ============================================================

def entry_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔑 Вход", callback_data="login_start")],
        [InlineKeyboardButton("📝 Регистрация", callback_data="register_start")],
        [InlineKeyboardButton("🆘 Поддержка", callback_data="support")],
    ])

def main_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔍 Найти матч", callback_data="find_match"),
         InlineKeyboardButton("🎉 Пати", callback_data="party_menu")],
        [InlineKeyboardButton("👤 Профиль", callback_data="profile"),
         InlineKeyboardButton("🏆 Топ игроков", callback_data="leaderboard")],
        [InlineKeyboardButton("📊 Статистика", callback_data="stats"),
         InlineKeyboardButton("📝 История", callback_data="history")],
        [InlineKeyboardButton("📢 Жалобы", callback_data="reports_menu"),
         InlineKeyboardButton("💎 Премиум", callback_data="premium_menu")],
        [InlineKeyboardButton("🆘 Поддержка", callback_data="support")],
    ])

def back_kb(callback_data: str = "back_to_menu") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data=callback_data)]])

def admin_approve_kb(telegram_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Впустить", callback_data=f"approve_{telegram_id}"),
         InlineKeyboardButton("❌ Отказать", callback_data=f"reject_{telegram_id}")]
    ])

# ============================================================
# /start
# ============================================================

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    clear_state(user.id)

    if await is_banned(user.id):
        await update.message.reply_text("⛔ Вы забанены и не можете пользоваться ботом.")
        return

    if not await check_subscription(context, user.id):
        await update.message.reply_text(
            "Чтобы пользоваться ботом, подпишись на наш чат:",
            reply_markup=subscription_prompt_kb(),
        )
        return

    player = await get_player(user.id)

    if player and player.get("approved"):
        await update.message.reply_text(
            f"С возвращением, {player.get('nick')}! 👋",
            reply_markup=main_menu_keyboard(),
        )
        return

    pending_rows = await sb_select("pending", {"telegram_id": f"eq.{user.id}", "limit": "1"})
    if pending_rows:
        await update.message.reply_text("⏳ Ваша заявка уже на рассмотрении у администраторов. Ожидайте.")
        return

    await update.message.reply_text(
        "Добро пожаловать в Strange Faceit — матчмейкинг для Standoff 2!\n\n"
        "Выберите действие:",
        reply_markup=entry_keyboard(),
    )

async def cb_check_sub(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    if await check_subscription(context, user_id):
        await query.edit_message_text(
            "Спасибо за подписку! Выберите действие:",
            reply_markup=entry_keyboard(),
        )
    else:
        await query.answer("Вы ещё не подписались 🙁", show_alert=True)

async def cb_back_to_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    clear_state(query.from_user.id)
    player = await get_player(query.from_user.id)
    if player and player.get("approved"):
        await query.edit_message_text("Главное меню:", reply_markup=main_menu_keyboard())
    else:
        await query.edit_message_text("Выберите действие:", reply_markup=entry_keyboard())

async def cb_support(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    text = "🆘 Поддержка\n\nЕсли у вас возникла проблема, опишите её в чате поддержки."
    if CHAT_LINK:
        text += f"\n\n{CHAT_LINK}"
    await query.edit_message_text(text, reply_markup=back_kb())

# ============================================================
# REGISTRATION FLOW
# ============================================================

GAME_ID_RE = re.compile(r"^\d{8,15}$")

async def cb_register_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    existing = await get_player(user_id)
    if existing:
        await query.edit_message_text(
            "Вы уже зарегистрированы. Используйте /start.", reply_markup=back_kb()
        )
        return

    set_state(user_id, "register", "await_game_id")
    await query.edit_message_text(
        "📝 Регистрация\n\nВведите ваш ID в Standoff 2 (8-15 цифр):"
    )

async def cb_login_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    set_state(user_id, "login", "await_game_id")
    await query.edit_message_text("🔑 Вход\n\nВведите ваш ID в Standoff 2:")

async def handle_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    state = get_state(user.id)
    if not state:
        return

    flow = state["flow"]
    step = state["step"]
    text = (update.message.text or "").strip()

    if flow == "register":
        await handle_register_step(update, context, step, text)
    elif flow == "login":
        await handle_login_step(update, context, step, text)
    elif flow == "admin":
        await handle_admin_text_step(update, context, step, text)
    elif flow == "party":
        await handle_party_text_step(update, context, step, text)
    elif flow == "report":
        await handle_report_text_step(update, context, step, text)

async def handle_register_step(update: Update, context: ContextTypes.DEFAULT_TYPE, step: str, text: str):
    user = update.effective_user
    state = get_state(user.id)

    if step == "await_game_id":
        if not GAME_ID_RE.match(text):
            await update.message.reply_text("❌ ID должен состоять из 8-15 цифр. Попробуйте снова:")
            return
        if await get_player_by_game_id(text):
            await update.message.reply_text("❌ Этот ID уже занят. Введите другой:")
            return
        state["data"]["game_id"] = text
        state["step"] = "await_nick"
        await update.message.reply_text("Отлично! Теперь введите ваш игровой ник:")
        return

    if step == "await_nick":
        if not (2 <= len(text) <= 32):
            await update.message.reply_text("❌ Ник должен быть от 2 до 32 символов. Попробуйте снова:")
            return
        if await get_player_by_nick(text):
            await update.message.reply_text("❌ Этот ник уже занят. Введите другой:")
            return
        state["data"]["nick"] = text
        state["step"] = "await_photo"
        await update.message.reply_text("📷 Отправьте скриншот вашего профиля из игры:")
        return

async def handle_register_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    state = get_state(user.id)
    if not state or state["flow"] != "register" or state["step"] != "await_photo":
        return

    photo_id = update.message.photo[-1].file_id
    data = state["data"]

    await sb_insert(
        "pending",
        {
            "telegram_id": user.id,
            "game_id": data["game_id"],
            "nick": data["nick"],
            "photo": photo_id,
        },
        upsert=True,
    )
    clear_state(user.id)

    await update.message.reply_text(
        "✅ Заявка отправлена на рассмотрение администраторам. Ожидайте подтверждения."
    )

    if ADMIN_CHAT_ID:
        caption = (
            f"📝 Новая заявка на регистрацию\n\n"
            f"Telegram: @{user.username or '—'} (ID: {user.id})\n"
            f"Игровой ID: {data['game_id']}\n"
            f"Ник: {data['nick']}"
        )
        try:
            await context.bot.send_photo(
                chat_id=ADMIN_CHAT_ID,
                photo=photo_id,
                caption=caption,
                reply_markup=admin_approve_kb(user.id),
            )
        except Exception as e:
            logger.error(f"failed to notify admin chat: {e}")

async def cb_approve_registration(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.from_user.id not in ADMIN_IDS and query.from_user.id != OWNER_ID:
        await query.answer("Недостаточно прав.", show_alert=True)
        return

    telegram_id = int(query.data.split("_", 1)[1])
    pending_rows = await sb_select("pending", {"telegram_id": f"eq.{telegram_id}", "limit": "1"})
    if not pending_rows:
        await query.edit_message_caption(caption=(query.message.caption or "") + "\n\n⚠️ Заявка не найдена.")
        return
    p = pending_rows[0]

    await sb_insert(
        "players",
        {
            "telegram_id": telegram_id,
            "game_id": p["game_id"],
            "nick": p["nick"],
            "photo": p["photo"],
            "approved": True,
            "username": None,
        },
        upsert=True,
    )
    await sb_delete("pending", {"telegram_id": f"eq.{telegram_id}"})

    new_caption = (query.message.caption or "") + "\n\n✅ Одобрено"
    await query.edit_message_caption(caption=new_caption)

    try:
        await context.bot.send_message(
            chat_id=telegram_id,
            text="✅ Ваша заявка одобрена! Добро пожаловать в Strange Faceit.\n\nИспользуйте /start.",
        )
    except Exception as e:
        logger.error(f"failed to notify approved user {telegram_id}: {e}")

async def cb_reject_registration(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.from_user.id not in ADMIN_IDS and query.from_user.id != OWNER_ID:
        await query.answer("Недостаточно прав.", show_alert=True)
        return

    telegram_id = int(query.data.split("_", 1)[1])
    await sb_delete("pending", {"telegram_id": f"eq.{telegram_id}"})

    new_caption = (query.message.caption or "") + "\n\n❌ Отказано"
    await query.edit_message_caption(caption=new_caption)

    try:
        await context.bot.send_message(
            chat_id=telegram_id,
            text="❌ Ваша заявка на регистрацию была отклонена администраторами.",
        )
    except Exception as e:
        logger.error(f"failed to notify rejected user {telegram_id}: {e}")

# ============================================================
# LOGIN FLOW
# ============================================================

async def handle_login_step(update: Update, context: ContextTypes.DEFAULT_TYPE, step: str, text: str):
    user = update.effective_user
    state = get_state(user.id)

    if step == "await_game_id":
        player = await get_player_by_game_id(text)
        if not player:
            await update.message.reply_text("❌ Игрок с таким ID не найден. Попробуйте снова или зарегистрируйтесь:")
            return
        state["data"]["game_id"] = text
        state["data"]["player"] = player
        state["step"] = "await_nick"
        await update.message.reply_text("Введите ваш игровой ник:")
        return

    if step == "await_nick":
        player = state["data"]["player"]
        if player.get("nick") != text:
            await update.message.reply_text("❌ Ник не совпадает с указанным ID. Попробуйте снова:")
            return

        if player.get("banned"):
            clear_state(user.id)
            await update.message.reply_text("⛔ Этот аккаунт забанен.")
            return

        if not player.get("approved"):
            clear_state(user.id)
            await update.message.reply_text("⏳ Этот аккаунт ещё не подтверждён администраторами.")
            return

        await sb_update(
            "players",
            {"telegram_id": f"eq.{player['telegram_id']}"},
            {"telegram_id": user.id, "username": user.username},
        )
        clear_state(user.id)
        await update.message.reply_text(
            f"✅ Вход выполнен. С возвращением, {player.get('nick')}!",
            reply_markup=main_menu_keyboard(),
        )
        return

# ============================================================
# PROFILE
# ============================================================

def build_profile_text(player: dict) -> str:
    rank, emoji = calc_rank(player.get("elo", 1000))
    matches = player.get("matches", 0)
    wins = player.get("wins", 0)
    losses = player.get("losses", 0)
    winrate = round((wins / matches) * 100, 1) if matches else 0.0

    premium_tag = " 💎" if is_premium_active(player) else ""
    display_nick = f"{player.get('nick')}{premium_tag}"

    lines = [
        f"👤 Профиль игрока",
        f"",
        f"Ник: {display_nick}",
        f"Игровой ID: {player.get('game_id')}",
        f"Ранг: {emoji} #{rank}",
        f"ELO: {player.get('elo', 1000)}",
        f"",
        f"Матчи: {matches}",
        f"Победы: {wins}",
        f"Поражения: {losses}",
        f"Винрейт: {winrate}%",
        f"MVP: {player.get('mvp', 0)}",
        f"",
        f"💎 Премиум: {format_premium_status(player)}",
    ]
    return "\n".join(lines)

async def cb_profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    player = await get_player(query.from_user.id)
    if not player:
        await query.edit_message_text("Профиль не найден. Используйте /start.")
        return
    await query.edit_message_text(build_profile_text(player), reply_markup=back_kb())

# ============================================================
# LEADERBOARD
# ============================================================

async def cb_leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    top = await sb_select("players", {"order": "elo.desc", "limit": "10", "approved": "eq.true"})
    if not top:
        await query.edit_message_text("Таблица лидеров пуста.", reply_markup=back_kb())
        return
    lines = ["🏆 Топ игроков\n"]
    for i, p in enumerate(top, start=1):
        rank, emoji = calc_rank(p.get("elo", 1000))
        premium = " 💎" if is_premium_active(p) else ""
        lines.append(f"{i}. {p.get('nick')}{premium} — {p.get('elo')} ELO {emoji}")
    await query.edit_message_text("\n".join(lines), reply_markup=back_kb())

# ============================================================
# STATS
# ============================================================

async def cb_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    player = await get_player(query.from_user.id)
    if not player:
        await query.edit_message_text("Профиль не найден.", reply_markup=back_kb())
        return
    kills = player.get("kills", 0)
    deaths = player.get("deaths", 0)
    headshots = player.get("headshots", 0)
    avg_kd = round(kills / deaths, 2) if deaths else float(kills)
    hs_pct = round((headshots / kills) * 100, 1) if kills else 0.0
    text = (
        "📊 Расширенная статистика\n\n"
        f"AVG K/D: {avg_kd}\n"
        f"HS%: {hs_pct}%\n"
        f"Любимая карта: {player.get('fav_map') or '—'}\n"
    )
    await query.edit_message_text(text, reply_markup=back_kb())

# ============================================================
# PLACEHOLDERS (будут заполнены позже)
# ============================================================

async def cb_find_match(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("🔍 Поиск матча\n\nВыберите платформу:", reply_markup=platforms_kb())

def platforms_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📱 Phone", callback_data="platform_Phone")],
        [InlineKeyboardButton("💻 PC", callback_data="platform_PC")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="back_to_menu")],
    ])

async def cb_platform(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    platform = query.data.split("_")[1]
    await query.edit_message_text(
        f"📱 {platform} Лобби\n\nВыберите лобби:",
        reply_markup=lobbies_kb(platform, {})
    )

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

async def cb_party_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("Пати будут добавлены позже.", show_alert=True)

async def cb_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("История матчей будет добавлена позже.", show_alert=True)

async def cb_reports_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("Жалобы будут добавлены позже.", show_alert=True)

async def cb_premium_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    text = "💎 Премиум-подписка\n\nТолько для премиум-игроков:\n• 💎 Значок рядом с ником\n• Возможность сменить ник\n\nТарифы:"
    await query.edit_message_text(text, reply_markup=premium_kb())

def premium_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⭐ 1 день — 5 звёзд", callback_data="premium_1_day")],
        [InlineKeyboardButton("⭐ 1 неделя — 39 звёзд", callback_data="premium_1_week")],
        [InlineKeyboardButton("⭐ 1 месяц — 111 звёзд", callback_data="premium_1_month")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="back_to_menu")],
    ])

# ============================================================
# HEALTH CHECK
# ============================================================

flask_app = Flask(__name__)

@flask_app.route("/health")
def health():
    player_count = None
    try:
        with httpx.Client(
            base_url=f"{SUPABASE_URL}/rest/v1",
            headers={"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}", "Prefer": "count=exact"},
            timeout=5.0,
        ) as client:
            r = client.head("/players", params={"select": "telegram_id"})
            content_range = r.headers.get("content-range")
            if content_range and "/" in content_range:
                player_count = int(content_range.split("/")[-1])
    except Exception as e:
        logger.warning(f"health check failed: {e}")
    return jsonify({"status": "ok", "uptime_seconds": round(time.time() - BOT_START_TIME, 1), "players": player_count})

def run_flask():
    flask_app.run(host="0.0.0.0", port=8080)

# ============================================================
# ADMIN TEXT HANDLERS (заглушки)
# ============================================================

async def handle_admin_text_step(update, context, step, text):
    pass

async def handle_party_text_step(update, context, step, text):
    pass

async def handle_report_text_step(update, context, step, text):
    pass

# ============================================================
# MAIN
# ============================================================

def main():
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
if __name__ == "__main__":
    main()

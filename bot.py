#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Stranger Faceit — Telegram бот для матчмейкинга в Standoff 2.
Версия: 3.2 (регистрация через фото с ручным подтверждением)
"""

import asyncio
import json
import logging
import os
import random
import re
import shutil
import string
import sys
import threading
import time
from collections import defaultdict, Counter
from datetime import datetime, timezone, timedelta
from io import BytesIO
from logging.handlers import RotatingFileHandler

from flask import Flask, jsonify
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import TelegramError, Forbidden, BadRequest
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler,
    MessageHandler, ContextTypes, filters,
)
from telegram.request import HTTPXRequest
from dotenv import load_dotenv

load_dotenv()

# ===================== ЛОГИРОВАНИЕ =====================
os.makedirs("logs", exist_ok=True)

logger = logging.getLogger("stranger_faceit")
logger.setLevel(logging.INFO)

console = logging.StreamHandler(sys.stdout)
console.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
logger.addHandler(console)

file_handler = RotatingFileHandler("logs/bot.log", maxBytes=10 * 1024 * 1024, backupCount=5)
file_handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
logger.addHandler(file_handler)

# ===================== ПЕРЕМЕННЫЕ ОКРУЖЕНИЯ =====================
REQUIRED_ENV = ["BOT_TOKEN", "ADMIN_IDS", "GENERAL_CHAT_ID", "ADMIN_CHAT_ID"]
missing_vars = [v for v in REQUIRED_ENV if not os.environ.get(v)]
if missing_vars:
    logger.error(f"❌ Отсутствуют переменные: {', '.join(missing_vars)}")
    sys.exit(1)

BOT_TOKEN = os.environ.get("BOT_TOKEN")
ADMIN_IDS = [int(x) for x in os.environ.get("ADMIN_IDS", "").split(",") if x.strip()]
GENERAL_CHAT_ID = int(os.environ.get("GENERAL_CHAT_ID"))
ADMIN_CHAT_ID = int(os.environ.get("ADMIN_CHAT_ID"))
CHAT_LINK = os.environ.get("CHAT_LINK", "")
REQUIRE_SUBSCRIPTION = int(os.environ.get("REQUIRE_SUBSCRIPTION", "1"))
SUBSCRIPTION_CHAT_ID = int(os.environ.get("SUBSCRIPTION_CHAT_ID", str(GENERAL_CHAT_ID)))
OWNER_ID = int(os.environ.get("OWNER_ID", str(ADMIN_IDS[0] if ADMIN_IDS else 0)))

logger.info("✅ Все переменные загружены")

# ===================== КОНСТАНТЫ =====================
DATA_FILE = "players.json"
PENDING_FILE = "pending.json"
LOBBIES_FILE = "lobbies.json"
PARTIES_FILE = "parties.json"
REPORTS_FILE = "reports.json"
HISTORY_FILE = "match_history.json"
ANALYTICS_FILE = "analytics.json"

MAPS = ["Sandstone", "Rust", "Province", "Breeze", "Dune", "Zone 7", "Hanami"]
MAP_EMOJI = {
    "Sandstone": "🏜️", "Rust": "🏭", "Province": "🏘️", "Breeze": "🌬️",
    "Dune": "🏝️", "Zone 7": "☢️", "Hanami": "🌸",
}
PLATFORMS = ["Phone", "PC"]
LOBBIES_PER_PLATFORM = 6
LOBBY_SIZE = 10
TEAM_SIZE = 5
MAX_PARTY_SIZE = 5

CALIBRATION_GAMES = 10
CALIBRATION_BASE_ELO = 500
READY_CHECK_TIMEOUT_SECONDS = 60
RESULT_UNLOCK_DELAY_SECONDS = 30
MAX_REPORT_LEN = 500

LEVEL_THRESHOLDS = [
    (1, 0, 500), (2, 501, 750), (3, 751, 900), (4, 901, 1050),
    (5, 1051, 1200), (6, 1201, 1350), (7, 1351, 1530),
    (8, 1531, 1750), (9, 1751, 2000), (10, 2001, 10 ** 9),
]
RANK_EMOJI = {1: "🥉", 2: "🥉", 3: "🥉", 4: "🥈", 5: "🥈", 6: "🥈", 7: "🥇", 8: "🥇", 9: "💎", 10: "👑"}

MOSCOW_TZ = timezone(timedelta(hours=3))

CONNECT_TIMEOUT = 120
READ_TIMEOUT = 120
WRITE_TIMEOUT = 120
POOL_TIMEOUT = 120

# ===================== RATE LIMITER =====================
class RateLimiter:
    def __init__(self, max_requests=30, time_window=60):
        self.max_requests = max_requests
        self.time_window = time_window
        self.requests = defaultdict(list)

    def is_allowed(self, user_id: int) -> bool:
        now = time.time()
        self.requests[user_id] = [t for t in self.requests[user_id] if now - t < self.time_window]
        if len(self.requests[user_id]) >= self.max_requests:
            return False
        self.requests[user_id].append(now)
        return True

rate_limiter = RateLimiter(max_requests=30, time_window=60)

# ===================== ХРАНИЛИЩЕ =====================
_LOCK = threading.Lock()
PLAYER_CACHE = {"data": None, "timestamp": 0, "ttl": 60}

def _load_json(path, default):
    with _LOCK:
        if not os.path.exists(path):
            return default
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return default

def _save_json(path, data):
    with _LOCK:
        tmp_path = path + ".tmp"
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, path)
        except Exception:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

def load_players():
    return _load_json(DATA_FILE, {})

def save_players(p):
    _save_json(DATA_FILE, p)
    invalidate_cache()

def load_pending():
    return _load_json(PENDING_FILE, {})

def save_pending(p):
    _save_json(PENDING_FILE, p)

def load_lobbies():
    default = {p: [[] for _ in range(LOBBIES_PER_PLATFORM)] for p in PLATFORMS}
    data = _load_json(LOBBIES_FILE, default)
    for p in PLATFORMS:
        if p not in data or not isinstance(data[p], list) or len(data[p]) != LOBBIES_PER_PLATFORM:
            data[p] = [[] for _ in range(LOBBIES_PER_PLATFORM)]
    return data

def save_lobbies(l):
    _save_json(LOBBIES_FILE, l)

def load_parties():
    return _load_json(PARTIES_FILE, {})

def save_parties(p):
    _save_json(PARTIES_FILE, p)

def load_reports():
    return _load_json(REPORTS_FILE, {})

def save_reports(r):
    _save_json(REPORTS_FILE, r)

def load_history():
    return _load_json(HISTORY_FILE, {"matches": []})

def save_history(h):
    _save_json(HISTORY_FILE, h)

def append_history(entry):
    h = load_history()
    h.setdefault("matches", []).append(entry)
    save_history(h)

def load_analytics():
    return _load_json(ANALYTICS_FILE, {"map_picks": {}, "online_samples": [], "match_timestamps": []})

def save_analytics(a):
    _save_json(ANALYTICS_FILE, a)

def get_players_cached():
    now = time.time()
    if PLAYER_CACHE["data"] and (now - PLAYER_CACHE["timestamp"] < PLAYER_CACHE["ttl"]):
        return PLAYER_CACHE["data"].copy()
    data = load_players()
    PLAYER_CACHE["data"] = data
    PLAYER_CACHE["timestamp"] = now
    return data.copy()

def invalidate_cache():
    PLAYER_CACHE["data"] = None
    PLAYER_CACHE["timestamp"] = 0

def find_party_of(parties, user_id):
    uid = int(user_id)
    for leader_id, party in parties.items():
        if uid in party.get("members", []):
            return leader_id, party
    return None, None

# ===================== АУДИТ =====================
def audit_log(action: str, user_id: int, details: dict):
    log_entry = {
        "timestamp": datetime.now().isoformat(),
        "action": action, "user_id": user_id, "details": details,
    }
    try:
        with open("audit.log", "a", encoding="utf-8") as f:
            f.write(json.dumps(log_entry, ensure_ascii=False) + "\n")
    except Exception:
        pass

# ===================== ВАЛИДАЦИЯ =====================
def sanitize_input(text: str) -> str:
    text = re.sub(r'[\x00-\x1f\x7f-\x9f]', '', text)
    return text.strip()[:MAX_REPORT_LEN]

def validate_standoff_id(id_text: str) -> bool:
    if not id_text.isdigit():
        return False
    return 8 <= len(id_text) <= 15

def is_banned(player) -> bool:
    if not player:
        return False
    ban = player.get('ban')
    if not ban:
        return False
    if ban is True:
        return True
    if isinstance(ban, (int, float)):
        return ban > time.time()
    return False

# ===================== ИГРОКИ =====================
def new_player(sid, tg_username):
    return {
        "reg": 0, "sid": sid, "name": "", "tag": "", "tg_username": tg_username,
        "elo": 0, "level": 0, "wins": 0, "losses": 0, "matches": 0, "mvps": 0,
        "rank": "🎯", "ban": None,
        "total_kills": 0, "total_deaths": 0, "hs_kills": 0,
        "elo_history": [],
        "maps": {m: {"wins": 0, "losses": 0} for m in MAPS},
        "calib": 0, "calib_elo_buffer": 0, "platform": None,
    }

def get_player(players, user_id):
    return players.get(str(user_id))

def find_by_sid(players, sid):
    for uid, p in players.items():
        if p.get("sid") == sid:
            return uid
    return None

def find_by_tag(players, tag):
    tag_clean = tag.lstrip("@").lower()
    for uid, p in players.items():
        if p.get("tag", "").lower() == tag_clean:
            return uid
    return None

def find_by_telegram_username(players, username):
    username_clean = username.lstrip("@").lower()
    for uid, p in players.items():
        if p.get("tg_username", "").lower() == username_clean:
            return uid
    return None

def gen_unique_tag(players):
    while True:
        candidate = f"defend_{random.randint(0, 999999):06d}"
        if not find_by_tag(players, candidate):
            return candidate

def level_from_elo(elo):
    for lvl, lo, hi in LEVEL_THRESHOLDS:
        if lo <= elo <= hi:
            return lvl
    return 10 if elo > LEVEL_THRESHOLDS[-1][2] else 1

def rank_label(level):
    return f"{RANK_EMOJI.get(level, '🥉')} {level}"

def compute_match_points(is_winner, kills, deaths, is_mvp):
    if is_winner:
        points = 9 + (kills * 0.5) - (deaths * 0.3)
    else:
        points = -15 + (kills * 0.5) - (deaths * 0.3)
    if is_mvp:
        points += 3
    return round(points)

def apply_match_result(player, is_winner, kills, deaths, hs, is_mvp):
    points = compute_match_points(is_winner, kills, deaths, is_mvp)
    snapshot_before = {
        "matches": player["matches"], "wins": player["wins"], "losses": player["losses"],
        "mvps": player["mvps"], "calib": player["calib"],
        "calib_elo_buffer": player["calib_elo_buffer"], "elo": player["elo"],
        "level": player["level"], "rank": player["rank"],
        "total_kills": player["total_kills"], "total_deaths": player["total_deaths"],
        "hs_kills": player["hs_kills"], "elo_history": list(player.get("elo_history", [])),
    }
    player["matches"] += 1
    player["total_kills"] = player.get("total_kills", 0) + kills
    player["total_deaths"] = player.get("total_deaths", 0) + deaths
    player["hs_kills"] = player.get("hs_kills", 0) + hs
    if is_winner:
        player["wins"] += 1
    else:
        player["losses"] += 1
    if is_mvp:
        player["mvps"] += 1

    if player["calib"] < CALIBRATION_GAMES:
        player["calib"] += 1
        player["calib_elo_buffer"] += points
        if player["calib"] >= CALIBRATION_GAMES:
            final_elo = max(0, CALIBRATION_BASE_ELO + player["calib_elo_buffer"])
            player["elo"] = final_elo
            player["level"] = level_from_elo(final_elo)
            player["rank"] = rank_label(player["level"])
            player.setdefault("elo_history", []).append(final_elo)
            return {
                "delta": 0, "old_elo": 0, "new_elo": final_elo, "calibrating": False,
                "just_finished_calibration": True, "calib_progress": None,
                "_snapshot_before": snapshot_before,
            }
        return {
            "delta": 0, "old_elo": 0, "new_elo": 0, "calibrating": True,
            "just_finished_calibration": False,
            "calib_progress": f"{player['calib']}/{CALIBRATION_GAMES}",
            "_snapshot_before": snapshot_before,
        }

    old_elo = player["elo"]
    new_elo = max(0, old_elo + points)
    player["elo"] = new_elo
    player["level"] = level_from_elo(new_elo)
    player["rank"] = rank_label(player["level"])
    player.setdefault("elo_history", []).append(new_elo)
    player["elo_history"] = player["elo_history"][-30:]
    return {
        "delta": points, "old_elo": old_elo, "new_elo": new_elo, "calibrating": False,
        "just_finished_calibration": False, "calib_progress": None,
        "_snapshot_before": snapshot_before,
    }

def rollback_match_result(player, snapshot_before):
    for key, value in snapshot_before.items():
        player[key] = value

def apply_map_result(player, map_name, is_winner):
    if map_name not in player.get("maps", {}):
        player.setdefault("maps", {})[map_name] = {"wins": 0, "losses": 0}
    if is_winner:
        player["maps"][map_name]["wins"] += 1
    else:
        player["maps"][map_name]["losses"] += 1

def elo_display(player):
    if player["calib"] < CALIBRATION_GAMES:
        return f"Калибровка {player['calib']}/{CALIBRATION_GAMES}"
    return f"{player['rank']} • {player['elo']} ELO"

def gen_match_id():
    date_part = datetime.now().strftime("%Y%m%d")
    rand_part = "".join(random.choices(string.digits, k=3))
    return f"M-{date_part}-{rand_part}"

# ===================== ВЕТО =====================
def start_veto(captain_a_id, captain_b_id):
    pool = MAPS.copy()
    random.shuffle(pool)
    return {
        "pool": pool, "banned": [], "turn": captain_a_id,
        "captain_a": captain_a_id, "captain_b": captain_b_id, "final_map": None,
    }

def veto_ban(veto, captain_id, map_name):
    if veto["final_map"] is not None:
        return False, "Вето уже завершено."
    if captain_id != veto["turn"]:
        return False, "Сейчас не ваша очередь банить."
    if map_name not in veto["pool"]:
        return False, "Эта карта уже забанена или не существует."
    veto["pool"].remove(map_name)
    veto["banned"].append({"by": captain_id, "map": map_name})
    if len(veto["pool"]) == 1:
        veto["final_map"] = veto["pool"][0]
    else:
        veto["turn"] = veto["captain_b"] if veto["turn"] == veto["captain_a"] else veto["captain_a"]
    return True, None

# ===================== КЛАВИАТУРЫ =====================
def kb_start_auth():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔑 Вход", callback_data="auth:login")],
        [InlineKeyboardButton("📝 Регистрация", callback_data="auth:register")],
        [InlineKeyboardButton("🆘 Поддержка", callback_data="auth:support")],
    ])

def kb_subscribe():
    rows = []
    if CHAT_LINK:
        rows.append([InlineKeyboardButton("➡️ Перейти в чат", url=CHAT_LINK)])
    rows.append([InlineKeyboardButton("✅ Я подписался", callback_data="sub:check")])
    return InlineKeyboardMarkup(rows)

def kb_main_menu(in_party: bool = False):
    party_label = "🎉 Пати" if not in_party else "🎉 Пати (моя группа)"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔍 Найти матч", callback_data="menu:find")],
        [InlineKeyboardButton(party_label, callback_data="menu:party")],
        [InlineKeyboardButton("👤 Профиль", callback_data="menu:profile")],
        [InlineKeyboardButton("🏆 Топ игроков", callback_data="menu:top")],
        [InlineKeyboardButton("📊 Расширенная статистика", callback_data="menu:stats")],
        [InlineKeyboardButton("📝 История матчей", callback_data="menu:history")],
        [InlineKeyboardButton("📢 Жалобы", callback_data="menu:complaints")],
        [InlineKeyboardButton("🆘 Поддержка", callback_data="menu:support")],
    ])

def kb_back_main():
    return InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data="menu:main")]])

def kb_reg_done():
    return InlineKeyboardMarkup([[InlineKeyboardButton("✅ Отправить фото", callback_data="auth:send_photo")]])

def kb_platforms():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📱 Phone", callback_data="platform:Phone")],
        [InlineKeyboardButton("💻 PC", callback_data="platform:PC")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="menu:main")],
    ])

def kb_lobbies(platform, lobbies, min_free_slots=1):
    rows = []
    for i in range(3):
        row = []
        for j in [i, i + 3]:
            if j < LOBBIES_PER_PLATFORM:
                count = len(lobbies[platform][j])
                free = LOBBY_SIZE - count
                label = f"Лобби {j + 1} ({count}/{LOBBY_SIZE})"
                if free >= min_free_slots:
                    row.append(InlineKeyboardButton(label, callback_data=f"lobby:{platform}:{j}"))
                else:
                    row.append(InlineKeyboardButton(f"🔒 {label}", callback_data="lobby:full"))
        rows.append(row)
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="menu:find")])
    return InlineKeyboardMarkup(rows)

def kb_in_lobby(platform, idx):
    return InlineKeyboardMarkup([[InlineKeyboardButton("🚪 Выйти", callback_data=f"lobby_leave:{platform}:{idx}")]])

def kb_veto(available_maps):
    rows, row = [], []
    for m in available_maps:
        emoji = MAP_EMOJI.get(m, "")
        row.append(InlineKeyboardButton(f"{emoji} {m}", callback_data=f"veto_ban:{m}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return InlineKeyboardMarkup(rows)

def kb_skip_stats():
    return InlineKeyboardMarkup([[InlineKeyboardButton("⏭ Пропустить статистику", callback_data="stats:skip")]])

def kb_send_results():
    return InlineKeyboardMarkup([[InlineKeyboardButton("📤 Отправить результаты", callback_data="result:send")]])

def kb_admin_review(pending_id):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ ПОДТВЕРДИТЬ", callback_data=f"admin_ok:{pending_id}")],
        [InlineKeyboardButton("❌ ОТКАЗАТЬ", callback_data=f"admin_no:{pending_id}")],
    ])

def kb_party_menu(is_leader: bool, party_size: int):
    rows = []
    if is_leader and party_size < MAX_PARTY_SIZE:
        rows.append([InlineKeyboardButton("➕ Пригласить игрока", callback_data="party:invite")])
    rows.append([InlineKeyboardButton("🚪 Покинуть пати", callback_data="party:leave")])
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="menu:main")])
    return InlineKeyboardMarkup(rows)

def kb_party_invite_response(leader_id):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Принять", callback_data=f"party_accept:{leader_id}")],
        [InlineKeyboardButton("❌ Отказать", callback_data=f"party_decline:{leader_id}")],
    ])

def kb_ready_check():
    return InlineKeyboardMarkup([[InlineKeyboardButton("✅ Подтвердить", callback_data="ready:confirm")]])

def kb_players_list(players_items, prefix, page=0, per_page=10, back_cb="menu:main"):
    total_pages = max(1, (len(players_items) + per_page - 1) // per_page)
    page = max(0, min(page, total_pages - 1))
    start = page * per_page
    end = min(start + per_page, len(players_items))
    rows = []
    for uid, p in players_items[start:end]:
        rows.append([InlineKeyboardButton(f"@{p.get('tag', uid)}", callback_data=f"{prefix}:{uid}:{page}")])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️", callback_data=f"{prefix}_page:{page - 1}"))
    nav.append(InlineKeyboardButton(f"{page + 1}/{total_pages}", callback_data="noop"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton("➡️", callback_data=f"{prefix}_page:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data=back_cb)])
    return InlineKeyboardMarkup(rows)

def kb_complaint_actions(target_uid):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✍️ Написать жалобу", callback_data=f"complaint_write:{target_uid}")],
        [InlineKeyboardButton("👁 Посмотреть жалобы", callback_data=f"complaint_view:{target_uid}")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="menu:complaints")],
    ])

def kb_confirm_yes_no(action, target_uid):
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Да", callback_data=f"{action}_yes:{target_uid}"),
        InlineKeyboardButton("❌ Нет", callback_data=f"{action}_no:{target_uid}"),
    ]])

def kb_admin_panel():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 Аналитика", callback_data="admin:analytics")],
        [InlineKeyboardButton("📝 История матчей", callback_data="admin:history")],
        [InlineKeyboardButton("🆔 Отвязать айди", callback_data="admin:unlink")],
        [InlineKeyboardButton("🔨 Забанить", callback_data="admin:ban")],
        [InlineKeyboardButton("📊 ELO", callback_data="admin:elo")],
        [InlineKeyboardButton("✏️ Изменить ID", callback_data="admin:change_id")],
        [InlineKeyboardButton("✏️ Изменить ник", callback_data="admin:change_nick")],
        [InlineKeyboardButton("📢 Жалобы (топ)", callback_data="admin:complaints_top")],
    ])

def kb_admin_elo_action():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Выдать ELO", callback_data="admin:elo_add")],
        [InlineKeyboardButton("➖ Убавить ELO", callback_data="admin:elo_remove")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="admin:back")],
    ])

def kb_history_nav(page, total_pages):
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️", callback_data=f"admin_history_page:{page - 1}"))
    nav.append(InlineKeyboardButton(f"{page + 1}/{max(1, total_pages)}", callback_data="noop"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton("➡️", callback_data=f"admin_history_page:{page + 1}"))
    rows = [nav] if nav else []
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="admin:back")])
    return InlineKeyboardMarkup(rows)

# ===================== ВСПОМОГАТЕЛЬНОЕ =====================
async def safe_delete(message):
    if message is None:
        return
    try:
        await message.delete()
    except (BadRequest, TelegramError):
        pass

async def safe_send(bot, chat_id, text=None, photo=None, **kwargs):
    try:
        if photo is not None:
            return await bot.send_photo(chat_id=chat_id, photo=photo, caption=text, **kwargs)
        return await bot.send_message(chat_id=chat_id, text=text, **kwargs)
    except Forbidden:
        logger.warning("Не удалось отправить сообщение %s", chat_id)
    except TelegramError:
        logger.exception("Ошибка отправки в чат %s", chat_id)
    return None

async def is_subscribed(bot, user_id):
    if not REQUIRE_SUBSCRIPTION or not SUBSCRIPTION_CHAT_ID:
        return True
    try:
        member = await bot.get_chat_member(SUBSCRIPTION_CHAT_ID, user_id)
        return member.status in ("member", "administrator", "creator")
    except TelegramError:
        return True

async def check_banned(update: Update) -> bool:
    players = get_players_cached()
    player = get_player(players, update.effective_user.id)
    if is_banned(player):
        await update.effective_message.reply_text("⛔ Вы забанены.")
        return True
    return False

async def require_subscription(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    if REQUIRE_SUBSCRIPTION and not await is_subscribed(context.bot, update.effective_user.id):
        await update.effective_message.reply_text(
            "📢 Подпишись на наш чат!", reply_markup=kb_subscribe(),
        )
        return False
    return True

async def update_lobby_for_all(platform: str, lobby_idx: int, context: ContextTypes.DEFAULT_TYPE):
    lobbies = load_lobbies()
    players_list = lobbies[platform][lobby_idx]
    players = get_players_cached()
    lines = []
    for uid in players_list:
        p = players.get(str(uid), {})
        tag = p.get("tag", str(uid))
        elo = p.get("elo", 0)
        lines.append(f"@{tag} ({elo} ELO)")
    text = f"📋 ЛОББИ {lobby_idx + 1} ({len(players_list)}/{LOBBY_SIZE})\n\nИГРОКИ:\n"
    for i, p in enumerate(lines, 1):
        text += f"{i}. {p}\n"
    text += f"\nОжидание: {len(players_list)}/{LOBBY_SIZE}"
    for uid in players_list:
        await safe_send(context.bot, uid, text, reply_markup=kb_in_lobby(platform, lobby_idx))

def main_menu_text(player):
    tag = player.get('tag', '')
    elo = elo_display(player)
    wins = player.get('wins', 0)
    losses = player.get('losses', 0)
    matches = player.get('matches', 0)
    return (
        f"🏠 ГЛАВНОЕ МЕНЮ\n\n"
        f"👤 @{tag}\n"
        f"📊 {elo}\n"
        f"🏆 Побед: {wins} | Поражений: {losses}\n"
        f"🎯 Матчей: {matches}"
    )

def profile_text(player):
    winrate = round((player['wins'] / player['matches'] * 100) if player['matches'] > 0 else 0, 1)
    text = (
        f"📊 МОЙ ПРОФИЛЬ\n\n"
        f"👤 @{player['tag']}\n"
        f"🆔 {player['sid']}\n"
        f"🏅 {player['rank']}\n"
        f"📊 {elo_display(player)}\n\n"
        f"📈 СТАТИСТИКА\n"
        f"🎯 Матчей: {player['matches']}\n"
        f"🏆 Побед: {player['wins']}\n"
        f"Поражений: {player['losses']}\n"
        f"Winrate: {winrate}%\n"
        f"⭐ MVP: {player['mvps']}\n"
    )
    if player.get('calib', 0) < CALIBRATION_GAMES:
        text += f"📌 Калибровка: {player['calib']}/{CALIBRATION_GAMES}\n"
    text += "\n📊 ПО КАРТАМ\n"
    for map_name, stats in player.get('maps', {}).items():
        total = stats['wins'] + stats['losses']
        if total > 0:
            rate = round((stats['wins'] / total * 100), 1)
            text += f"{MAP_EMOJI.get(map_name, '')} {map_name}: {stats['wins']}-{stats['losses']} ({rate}%)\n"
    return text

def sparkline(values):
    if not values:
        return "нет данных"
    blocks = "▁▂▃▄▅▆▇█"
    lo, hi = min(values), max(values)
    if hi == lo:
        return blocks[3] * len(values)
    out = ""
    for v in values:
        idx = int((v - lo) / (hi - lo) * (len(blocks) - 1))
        out += blocks[idx]
    return out

def extended_stats_text(player):
    matches = player.get('matches', 0)
    kills = player.get('total_kills', 0)
    deaths = player.get('total_deaths', 0)
    hs = player.get('hs_kills', 0)
    avg_kd = round(kills / deaths, 2) if deaths > 0 else float(kills)
    hs_pct = round((hs / kills * 100), 1) if kills > 0 else 0.0
    fav_map, fav_games = None, 0
    for m, s in player.get('maps', {}).items():
        total = s['wins'] + s['losses']
        if total > fav_games:
            fav_games = total
            fav_map = m
    fav_map_text = f"{MAP_EMOJI.get(fav_map, '')} {fav_map} ({fav_games} игр)" if fav_map else "нет данных"
    elo_hist = player.get('elo_history', [])[-10:]
    graph = sparkline(elo_hist)
    text = (
        f"📊 РАСШИРЕННАЯ СТАТИСТИКА\n\n"
        f"👤 @{player['tag']}\n\n"
        f"AVG KD: {avg_kd}\n"
        f"HS%: {hs_pct}%\n"
        f"Любимая карта: {fav_map_text}\n\n"
        f"ELO (последние {len(elo_hist)} матчей):\n{graph}\n"
    )
    if elo_hist:
        text += f"Значения: {', '.join(str(v) for v in elo_hist)}\n"
    return text

def personal_history_text(uid, history):
    matches = [m for m in history.get("matches", []) if uid in m.get("all_players", [])]
    matches = matches[-20:][::-1]
    if not matches:
        return "📝 ИСТОРИЯ МАТЧЕЙ\n\nПока нет сыгранных матчей."
    lines = ["📝 ИСТОРИЯ МАТЧЕЙ (последние 20)\n"]
    for m in matches:
        result = "🏆 Победа" if uid in m.get("winners", []) else "❌ Поражение"
        stats = m.get("stats", {}).get(uid, {})
        kd = f"{stats.get('kills', 0)}/{stats.get('deaths', 0)}"
        mvp = " ⭐" if m.get("mvp") == uid else ""
        lines.append(f"{m['match_id']} | {MAP_EMOJI.get(m.get('map'), '')} {m.get('map')} | {result} | {kd}{mvp}")
    return "\n".join(lines)

def admin_history_page_text(history, page, per_page=10):
    matches = history.get("matches", [])[::-1]
    total_pages = max(1, (len(matches) + per_page - 1) // per_page)
    start = page * per_page
    chunk = matches[start:start + per_page]
    if not chunk:
        return "📝 ИСТОРИЯ МАТЧЕЙ (ВСЕ)\n\nНет данных.", total_pages
    lines = [f"📝 ИСТОРИЯ МАТЧЕЙ (ВСЕ) — стр. {page + 1}/{total_pages}\n"]
    for m in chunk:
        ts = m.get("timestamp", "")
        lines.append(f"{m['match_id']} | {MAP_EMOJI.get(m.get('map'), '')} {m.get('map')} | {ts}")
    return "\n".join(lines), total_pages

def complaints_players_text():
    return "📢 ЖАЛОБЫ\n\nВыбери игрока:"

def complaint_view_text(target_tag, reports_list):
    if not reports_list:
        return f"Жалоб на @{target_tag} пока нет."
    lines = [f"ЖАЛОБЫ НА @{target_tag} ({len(reports_list)})\n"]
    for r in reports_list:
        lines.append(f"• {r.get('text', '')}")
    return "\n".join(lines)

def update_analytics_on_match(map_name):
    a = load_analytics()
    a.setdefault("map_picks", {})
    a["map_picks"][map_name] = a["map_picks"].get(map_name, 0) + 1
    a.setdefault("match_timestamps", []).append(datetime.now(MOSCOW_TZ).isoformat())
    a["match_timestamps"] = a["match_timestamps"][-500:]
    save_analytics(a)

def sample_online(count):
    a = load_analytics()
    a.setdefault("online_samples", []).append(count)
    a["online_samples"] = a["online_samples"][-200:]
    save_analytics(a)

def build_analytics_text(players, pending):
    a = load_analytics()
    map_picks = a.get("map_picks", {})
    top_map = max(map_picks.items(), key=lambda x: x[1])[0] if map_picks else "нет данных"
    online_samples = a.get("online_samples", [])
    avg_online = round(sum(online_samples) / len(online_samples), 1) if online_samples else 0
    elos = [p.get("elo", 0) for p in players.values() if p.get("reg") == 1 and p.get("calib", 0) >= CALIBRATION_GAMES]
    avg_elo = round(sum(elos) / len(elos), 1) if elos else 0
    hours = []
    for ts in a.get("match_timestamps", []):
        try:
            hours.append(datetime.fromisoformat(ts).hour)
        except Exception:
            pass
    peak_hour = Counter(hours).most_common(1)[0][0] if hours else None
    peak_text = f"{peak_hour}:00 - {(peak_hour + 1) % 24}:00 (МСК)" if peak_hour is not None else "нет данных"
    active_matches = sum(1 for v in pending.values() if v.get("status") == "awaiting_review")
    reports = load_reports()
    all_texts = []
    for lst in reports.values():
        for r in lst:
            all_texts.append(r.get("text", "").lower())
    categories = {"читер": 0, "оскорбления": 0, "слив": 0, "афк": 0, "токсик": 0, "другое": 0}
    keywords = {
        "читер": ["чит", "aim", "wallhack", "аим", "вх"],
        "оскорбления": ["оскорб", "мат", "хам"],
        "слив": ["слил", "слив", "throw"],
        "афк": ["афк", "afk", "не играл"],
        "токсик": ["токсич", "токсик"],
    }
    for t in all_texts:
        matched = False
        for cat, kws in keywords.items():
            if any(k in t for k in kws):
                categories[cat] += 1
                matched = True
                break
        if not matched:
            categories["другое"] += 1
    top_categories = sorted(categories.items(), key=lambda x: x[1], reverse=True)
    top_categories_text = "\n".join([f"  • {c}: {n}" for c, n in top_categories if n > 0]) or "  нет данных"
    return (
        f"📊 АНАЛИТИКА\n\n"
        f"Карта: {MAP_EMOJI.get(top_map, '')} {top_map}\n"
        f"Средний онлайн: {avg_online}\n"
        f"Средний ELO: {avg_elo}\n"
        f"Пиковое время: {peak_text}\n"
        f"Активных матчей: {active_matches}\n\n"
        f"Топ жалоб:\n{top_categories_text}"
    )

# ===================== READY-CHECK =====================
READY_CHECKS_BY_ID = {}
READY_CHECKS = {}
_rc_counter = 0

def _next_rc_id():
    global _rc_counter
    _rc_counter += 1
    return f"rc{_rc_counter}"

async def ready_check_timer(rc_id: str, timeout: int, context: ContextTypes.DEFAULT_TYPE):
    await asyncio.sleep(timeout)
    rc = READY_CHECKS_BY_ID.get(rc_id)
    if rc and rc["status"] == "pending":
        await finalize_ready_check(rc_id, context, timed_out=True)

async def start_ready_check(platform: str, lobby_idx: int, context: ContextTypes.DEFAULT_TYPE):
    lobbies = load_lobbies()
    players_list = lobbies[platform][lobby_idx].copy()
    lobbies[platform][lobby_idx] = []
    save_lobbies(lobbies)
    rc_id = _next_rc_id()
    rc = {
        "players": players_list, "confirmed": set(), "platform": platform,
        "lobby_idx": lobby_idx, "status": "pending", "created_at": time.time(),
    }
    READY_CHECKS_BY_ID[rc_id] = rc
    for uid in players_list:
        READY_CHECKS[uid] = {"id": rc_id}
    for uid in players_list:
        await safe_send(
            context.bot, uid,
            f"👥 Лобби набрано! ({LOBBY_SIZE}/{LOBBY_SIZE})\n\n"
            f"У тебя есть {READY_CHECK_TIMEOUT_SECONDS} секунд, чтобы подтвердить.\n"
            f"Если не успеешь — будешь удалён.",
            reply_markup=kb_ready_check(),
        )
    sample_online(len(players_list))
    asyncio.create_task(ready_check_timer(rc_id, READY_CHECK_TIMEOUT_SECONDS, context))

async def finalize_ready_check(rc_id: str, context: ContextTypes.DEFAULT_TYPE, timed_out: bool = False):
    rc = READY_CHECKS_BY_ID.get(rc_id)
    if not rc or rc["status"] != "pending":
        return
    rc["status"] = "done"
    confirmed = [uid for uid in rc["players"] if uid in rc["confirmed"]]
    not_confirmed = [uid for uid in rc["players"] if uid not in rc["confirmed"]]
    for uid in rc["players"]:
        READY_CHECKS.pop(uid, None)
    if not_confirmed:
        for uid in not_confirmed:
            await safe_send(context.bot, uid, "❌ Ты не подтвердил вовремя и был удалён.")
        lobbies = load_lobbies()
        lobbies[rc["platform"]][rc["lobby_idx"]] = confirmed.copy()
        save_lobbies(lobbies)
        for uid in confirmed:
            await update_lobby_for_all(rc["platform"], rc["lobby_idx"], context)
        return
    await start_match(rc["platform"], rc["players"], context)

# ===================== /start =====================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not rate_limiter.is_allowed(update.effective_user.id):
        await update.message.reply_text("⚠️ Слишком много запросов. Подождите 1 минуту.")
        return
    if await check_banned(update):
        return
    if not await require_subscription(update, context):
        return
    user = update.effective_user
    players = get_players_cached()
    player = get_player(players, user.id)
    if not player or player.get("reg") != 1:
        await update.message.reply_text(
            "🎮 STRANGER FACEIT\n\nДобро пожаловать! Выбери действие:",
            reply_markup=kb_start_auth(),
        )
        return
    parties = load_parties()
    leader_id, _ = find_party_of(parties, user.id)
    await update.message.reply_text(
        main_menu_text(player),
        reply_markup=kb_main_menu(in_party=bool(leader_id)),
    )

# ===================== /admin =====================
async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("❌ Доступ запрещен.")
        return
    await update.message.reply_text("👑 АДМИН-ПАНЕЛЬ\n\nВыбери действие:", reply_markup=kb_admin_panel())

# ===================== ФОТО / РЕЗУЛЬТАТЫ =====================
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not rate_limiter.is_allowed(update.effective_user.id):
        await update.message.reply_text("⚠️ Слишком много запросов. Подождите 1 минуту.")
        return
    if await check_banned(update):
        return

    # ---- РЕГИСТРАЦИЯ: ОТПРАВКА ФОТО НА ПРОВЕРКУ АДМИНАМ ----
    if context.user_data.get('auth_step') == 'reg_photo':
        pending_sid = context.user_data.get('reg_pending_sid')
        pending_tag = context.user_data.get('reg_pending_tag')
        if not pending_sid or not pending_tag:
            await update.message.reply_text("❌ Сессия регистрации истекла. Начни заново.")
            return
        pending_id = f"reg_{int(time.time())}_{update.effective_user.id}"
        pending = load_pending()
        pending[pending_id] = {
            "type": "registration",
            "user_id": update.effective_user.id,
            "sid": pending_sid,
            "tag": pending_tag,
            "photo_id": update.message.photo[-1].file_id,
            "status": "pending"
        }
        save_pending(pending)
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Впустить", callback_data=f"reg_ok:{pending_id}")],
            [InlineKeyboardButton("❌ Отказать", callback_data=f"reg_no:{pending_id}")],
        ])
        await safe_send(
            context.bot, ADMIN_CHAT_ID,
            f"📸 Новая заявка на регистрацию!\n\n"
            f"Ник: {pending_tag}\n"
            f"ID: {pending_sid}\n"
            f"Telegram: @{update.effective_user.username or 'нет юза'}",
            photo=update.message.photo[-1].file_id,
            reply_markup=kb,
        )
        await update.message.reply_text("⏳ Твоя заявка отправлена админам. Жди ответа.")
        context.user_data.clear()
        return

    # ---- ОБЫЧНЫЙ МАТЧ ----
    match = context.user_data.get('match')
    if not match or match.get('status') != 'in_progress':
        await update.message.reply_text("❌ Сейчас нет матча, ожидающего скриншот.")
        return
    if match.get('host') != update.effective_user.id:
        await update.message.reply_text("❌ Только хост может отправлять результат.")
        return
    if time.time() < match.get('result_unlock_time', 0):
        remaining = int(match['result_unlock_time'] - time.time())
        await update.message.reply_text(f"⏳ Отправка доступна через {remaining} сек.")
        return
    photo_sizes = update.message.photo
    if not photo_sizes:
        return
    file = await context.bot.get_file(photo_sizes[-1].file_id)
    if file.file_size and file.file_size > 5 * 1024 * 1024:
        await update.message.reply_text("❌ Слишком большой файл (макс 5 MB)")
        return
    context.user_data['match_photo'] = photo_sizes[-1].file_id
    await update.message.reply_text(
        "✅ Скриншот принят!\n\nТеперь объяви победившую сторону:\n/winner ct или /winner t"
    )

async def winner_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not rate_limiter.is_allowed(update.effective_user.id):
        await update.message.reply_text("⚠️ Слишком много запросов. Подождите 1 минуту.")
        return
    if await check_banned(update):
        return
    match = context.user_data.get('match')
    if not match or match.get('status') != 'in_progress':
        await update.message.reply_text("❌ Нет активного матча.")
        return
    if match.get('host') != update.effective_user.id:
        await update.message.reply_text("❌ Только хост может объявить результат.")
        return
    args = context.args
    if not args or args[0].lower() not in ("ct", "t"):
        await update.message.reply_text("Использование: /winner ct  или  /winner t")
        return
    if not context.user_data.get('match_photo'):
        await update.message.reply_text("📸 Сначала отправь скриншот результата.")
        return
    side = args[0].lower()
    match['winner_side'] = side
    match['status'] = 'awaiting_winning_team'
    context.user_data['match'] = match
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔵 Команда А", callback_data="winteam:a")],
        [InlineKeyboardButton("🔴 Команда Б", callback_data="winteam:b")],
    ])
    await update.message.reply_text(
        f"✅ Победила сторона: {side.upper()}\n\nКакая команда играла за {side.upper()} и победила?",
        reply_markup=kb,
    )

# ===================== CALLBACK =====================
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if not rate_limiter.is_allowed(update.effective_user.id):
            await update.callback_query.answer("⚠️ Слишком много запросов. Подождите 1 минуту.", show_alert=True)
            return
        if await check_banned(update):
            return
        query = update.callback_query
        await query.answer()
        user = query.from_user
        user_id = str(user.id)
        data = query.data
        players = get_players_cached()
        player = get_player(players, user.id)

        if data == "noop":
            return

        # ---------- ПОДПИСКА ----------
        if data == "sub:check":
            if await is_subscribed(context.bot, user.id):
                await safe_delete(query.message)
                if not player or player.get("reg") != 1:
                    await query.message.reply_text(
                        "✅ Подписка подтверждена!\n\n🎮 STRANGER FACEIT\n\nВыбери действие:",
                        reply_markup=kb_start_auth(),
                    )
                else:
                    parties = load_parties()
                    leader_id, _ = find_party_of(parties, user.id)
                    await query.message.reply_text(
                        main_menu_text(player),
                        reply_markup=kb_main_menu(bool(leader_id)),
                    )
            else:
                await query.answer("❌ Подписка не найдена.", show_alert=True)
            return

        # ---------- ВХОД / РЕГИСТРАЦИЯ / ПОДДЕРЖКА ----------
        if data == "auth:login":
            await safe_delete(query.message)
            context.user_data.clear()
            context.user_data['auth_step'] = 'login_id'
            await query.message.reply_text(
                "🔑 ВХОД\n\nВведи свой ID в Standoff 2:",
                reply_markup=kb_back_main(),
            )
            return

        if data == "auth:register":
            await safe_delete(query.message)
            context.user_data.clear()
            context.user_data['auth_step'] = 'reg_id'
            await query.message.reply_text(
                "📝 РЕГИСТРАЦИЯ\n\nВведи свой ID в Standoff 2:",
                reply_markup=kb_back_main(),
            )
            return

        if data == "auth:support":
            await safe_delete(query.message)
            context.user_data['support_mode'] = True
            await query.message.reply_text(
                "🆘 ПОДДЕРЖКА\n\nОпиши свою проблему:",
                reply_markup=kb_back_main(),
            )
            return

        if data == "auth:send_photo":
            pending_sid = context.user_data.get('reg_pending_sid')
            pending_tag = context.user_data.get('reg_pending_tag')
            if not pending_sid or not pending_tag:
                await query.answer("❌ Сессия истекла. Начни заново.", show_alert=True)
                return
            await safe_delete(query.message)
            await query.message.reply_text(
                f"📸 Отправь скриншот профиля из Standoff 2\n\n"
                f"На скриншоте должны быть видны:\n"
                f"• ID: {pending_sid}\n"
                f"• Ник: {pending_tag}"
            )
            context.user_data['auth_step'] = 'reg_photo'
            return

        # ===== РЕГИСТРАЦИЯ: ПОДТВЕРЖДЕНИЕ АДМИНОМ =====
        if data.startswith("reg_ok:") or data.startswith("reg_no:"):
            if user.id not in ADMIN_IDS:
                await query.answer("❌ Только для администраторов.", show_alert=True)
                return
            action, pending_id = data.split(":", 1)
            pending = load_pending()
            record = pending.get(pending_id)
            if not record or record.get("type") != "registration":
                await query.answer("❌ Заявка не найдена.", show_alert=True)
                return
            if action == "reg_ok":
                players_data = load_players()
                new_p = new_player(record["sid"], record["tag"])
                new_p["reg"] = 1
                new_p["name"] = record["tag"]
                new_p["tag"] = record["tag"]
                new_p["tg_username"] = record["tag"]
                players_data[str(record["user_id"])] = new_p
                save_players(players_data)
                invalidate_cache()
                record["status"] = "approved"
                save_pending(pending)
                await safe_send(
                    context.bot, record["user_id"],
                    "✅ Регистрация подтверждена!\n\nДобро пожаловать в Stranger Faceit!\nНапиши /start"
                )
                await safe_delete(query.message)
                await query.message.reply_text(f"✅ Игрок @{record['tag']} зарегистрирован!")
            else:
                record["status"] = "rejected"
                save_pending(pending)
                await safe_send(
                    context.bot, record["user_id"],
                    "❌ Регистрация отклонена.\nОбратись в поддержку."
                )
                await safe_delete(query.message)
                await query.message.reply_text(f"❌ Заявка @{record['tag']} отклонена.")
            return

        # ---------- ГЛАВНОЕ МЕНЮ ----------
        if data == "menu:main":
            await safe_delete(query.message)
            context.user_data.clear()
            if not player or player.get("reg") != 1:
                await query.message.reply_text(
                    "🎮 STRANGER FACEIT\n\nВыбери действие:",
                    reply_markup=kb_start_auth(),
                )
                return
            parties = load_parties()
            leader_id, _ = find_party_of(parties, user.id)
            await query.message.reply_text(
                main_menu_text(player),
                reply_markup=kb_main_menu(bool(leader_id)),
            )
            return

        # ---------- ПРОФИЛЬ ----------
        if data == "menu:profile":
            await safe_delete(query.message)
            if not player or player.get("reg") != 1:
                await query.message.reply_text("Сначала зарегистрируйся!", reply_markup=kb_start_auth())
                return
            await query.message.reply_text(profile_text(player), reply_markup=kb_back_main())
            return

        # ---------- РАСШИРЕННАЯ СТАТИСТИКА ----------
        if data == "menu:stats":
            await safe_delete(query.message)
            if not player or player.get("reg") != 1:
                await query.message.reply_text("Сначала зарегистрируйся!", reply_markup=kb_start_auth())
                return
            await query.message.reply_text(extended_stats_text(player), reply_markup=kb_back_main())
            return

        # ---------- ИСТОРИЯ МАТЧЕЙ ----------
        if data == "menu:history":
            await safe_delete(query.message)
            if not player or player.get("reg") != 1:
                await query.message.reply_text("Сначала зарегистрируйся!", reply_markup=kb_start_auth())
                return
            history = load_history()
            await query.message.reply_text(personal_history_text(user_id, history), reply_markup=kb_back_main())
            return

        # ---------- ТОП ----------
        if data == "menu:top":
            await safe_delete(query.message)
            sorted_players = sorted(
                [p for p in players.values() if p.get("reg") == 1 and p.get("calib", 0) >= CALIBRATION_GAMES],
                key=lambda x: x.get("elo", 0), reverse=True,
            )[:10]
            text = "🏆 ТОП ИГРОКОВ\n\n"
            if not sorted_players:
                text += "Пока нет игроков, завершивших калибровку.\n"
            for i, p in enumerate(sorted_players, 1):
                medal = {1: "👑", 2: "🥈", 3: "🥉"}.get(i, f"{i}.")
                text += f"{medal} @{p['tag']}\n    {p['elo']} ELO | {p['rank']}\n"
            total = len([p for p in players.values() if p.get("reg") == 1])
            text += f"\nВсего игроков: {total}"
            await query.message.reply_text(text, reply_markup=kb_back_main())
            return

        # ---------- ПОДДЕРЖКА ----------
        if data == "menu:support":
            await safe_delete(query.message)
            context.user_data['support_mode'] = True
            await query.message.reply_text(
                "🆘 ПОДДЕРЖКА\n\nОпиши свою проблему:",
                reply_markup=kb_back_main(),
            )
            return

        # ---------- ЖАЛОБЫ ----------
        if data == "menu:complaints":
            await safe_delete(query.message)
            all_players = sorted(
                [(uid, p) for uid, p in players.items() if p.get("reg") == 1],
                key=lambda x: x[1].get('tag', ''),
            )
            context.user_data['complaints_players'] = all_players
            await query.message.reply_text(
                complaints_players_text(),
                reply_markup=kb_players_list(all_players, "complaint", 0, back_cb="menu:main"),
            )
            return

        if data.startswith("complaint_page:"):
            page = int(data.split(":")[1])
            all_players = context.user_data.get('complaints_players', [])
            await query.message.edit_text(
                complaints_players_text(),
                reply_markup=kb_players_list(all_players, "complaint", page, back_cb="menu:main"),
            )
            return

        if data.startswith("complaint:"):
            _, target_uid, page = data.split(":")
            target_p = players.get(target_uid)
            if not target_p:
                await query.answer("❌ Игрок не найден.", show_alert=True)
                return
            await safe_delete(query.message)
            await query.message.reply_text(
                f"👤 @{target_p.get('tag', target_uid)}\n\nВыбери действие:",
                reply_markup=kb_complaint_actions(target_uid),
            )
            return

        if data.startswith("complaint_write:"):
            target_uid = data.split(":")[1]
            if target_uid == user_id:
                await query.answer("❌ Нельзя пожаловаться на себя.", show_alert=True)
                return
            reports = load_reports()
            existing = reports.get(target_uid, [])
            if any(r.get("by") == user_id for r in existing):
                await query.answer("❌ Ты уже жаловался на этого игрока.", show_alert=True)
                return
            context.user_data['complaint_target'] = target_uid
            await safe_delete(query.message)
            await query.message.reply_text(f"✍️ Опиши жалобу на @{players.get(target_uid, {}).get('tag', target_uid)}:")
            return

        if data.startswith("complaint_view:"):
            target_uid = data.split(":")[1]
            target_p = players.get(target_uid, {})
            reports = load_reports()
            reports_list = reports.get(target_uid, [])
            await safe_delete(query.message)
            await query.message.reply_text(
                complaint_view_text(target_p.get('tag', target_uid), reports_list),
                reply_markup=kb_back_main(),
            )
            return

        # ---------- ПАТИ ----------
        if data == "menu:party":
            await safe_delete(query.message)
            if not player or player.get("reg") != 1:
                await query.message.reply_text("Сначала зарегистрируйся!", reply_markup=kb_start_auth())
                return
            parties = load_parties()
            leader_id, party = find_party_of(parties, user.id)
            if not party:
                await query.message.reply_text(
                    "🎉 ПАТИ\n\nТы пока не в группе.\nПригласи друга!",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("➕ Пригласить игрока", callback_data="party:invite")],
                        [InlineKeyboardButton("⬅️ Назад", callback_data="menu:main")],
                    ]),
                )
                return
            is_leader = (int(leader_id) == user.id)
            text = party_text(parties, leader_id, players)
            await query.message.reply_text(
                text,
                reply_markup=kb_party_menu(is_leader, len(party["members"])),
            )
            return

        if data == "party:invite":
            await safe_delete(query.message)
            parties = load_parties()
            leader_id, party = find_party_of(parties, user.id)
            if not party:
                parties[user_id] = {"leader": user.id, "members": [user.id], "pending_invite": None}
                save_parties(parties)
                leader_id, party = user_id, parties[user_id]
            if int(leader_id) != user.id:
                await query.message.reply_text("❌ Только лидер может приглашать.")
                return
            if len(party["members"]) >= MAX_PARTY_SIZE:
                await query.message.reply_text("❌ Пати заполнена (максимум 5 человек).")
                return
            context.user_data['party_invite_mode'] = True
            await query.message.reply_text(
                "👤 Введите Telegram юзернейм игрока:",
                reply_markup=kb_back_main(),
            )
            return

        if data == "party:leave":
            await safe_delete(query.message)
            parties = load_parties()
            leader_id, party = find_party_of(parties, user.id)
            if not party:
                await query.message.reply_text("Ты не в пати.")
                return
            if int(leader_id) == user.id:
                for uid in party["members"]:
                    if uid != user.id:
                        await safe_send(context.bot, uid, "🎉 Пати расформирована лидером.")
                del parties[leader_id]
                save_parties(parties)
                await query.message.reply_text("🚪 Пати расформирована.")
            else:
                party["members"].remove(user.id)
                parties[leader_id] = party
                save_parties(parties)
                await safe_send(context.bot, int(leader_id), f"🎉 @{player['tag']} покинул пати.")
                await query.message.reply_text("🚪 Ты покинул пати.")
            return

        if data.startswith("party_accept:") or data.startswith("party_decline:"):
            action, leader_id_str = data.split(":", 1)
            parties = load_parties()
            party = parties.get(leader_id_str)
            await safe_delete(query.message)
            if not party or not party.get("pending_invite") or party["pending_invite"].get("target") != user.id:
                await query.message.reply_text("❌ Приглашение не действительно.")
                return
            leader_uid = int(leader_id_str)
            leader_player = players.get(leader_id_str, {})
            if action == "party_decline":
                party["pending_invite"] = None
                parties[leader_id_str] = party
                save_parties(parties)
                await query.message.reply_text("❌ Приглашение отклонено.")
                await safe_send(context.bot, leader_uid, f"❌ @{player['tag']} отклонил приглашение.")
                return
            target_leader, target_party = find_party_of(parties, user.id)
            if target_party and int(target_leader) == user.id:
                del parties[str(user.id)]
                save_parties(parties)
                parties = load_parties()
                party = parties.get(leader_id_str)
                if not party:
                    await query.message.reply_text("❌ Пати больше не существует.")
                    return
            if len(party["members"]) >= MAX_PARTY_SIZE:
                await query.message.reply_text("❌ Пати уже заполнена.")
                party["pending_invite"] = None
                parties[leader_id_str] = party
                save_parties(parties)
                return
            lobbies = load_lobbies()
            for plt in PLATFORMS:
                for i, lobby in enumerate(lobbies[plt]):
                    if user.id in lobby:
                        lobby.remove(user.id)
            save_lobbies(lobbies)
            party["members"].append(user.id)
            party["pending_invite"] = None
            parties[leader_id_str] = party
            save_parties(parties)
            await query.message.reply_text(
                f"✅ Ты присоединился к пати @{leader_player.get('tag', leader_id_str)}!",
                reply_markup=kb_back_main(),
            )
            text = party_text(parties, leader_id_str, players)
            for uid in party["members"]:
                is_leader_uid = (uid == leader_uid)
                await safe_send(
                    context.bot, uid, text,
                    reply_markup=kb_party_menu(is_leader_uid, len(party["members"])),
                )
            return

        # ---------- ПОИСК МАТЧА ----------
        if data == "menu:find":
            await safe_delete(query.message)
            if not player or player.get("reg") != 1:
                await query.message.reply_text("Сначала зарегистрируйся!", reply_markup=kb_start_auth())
                return
            await query.message.reply_text("📱 ВЫБЕРИ ПЛАТФОРМУ", reply_markup=kb_platforms())
            return

        if data.startswith("platform:"):
            platform = data.split(":")[1]
            lobbies = load_lobbies()
            parties = load_parties()
            leader_id, party = find_party_of(parties, user.id)
            if party and int(leader_id) != user.id:
                await safe_delete(query.message)
                await query.message.reply_text(
                    "❌ Только лидер пати может выбирать лобби.",
                    reply_markup=kb_back_main(),
                )
                return
            min_free = len(party["members"]) if party else 1
            await safe_delete(query.message)
            await query.message.reply_text(
                f"📱 {platform.upper()} ЛОББИ" + (f"\n(нужно {min_free} мест для пати)" if party else ""),
                reply_markup=kb_lobbies(platform, lobbies, min_free_slots=min_free),
            )
            return

        if data == "lobby:full":
            await query.answer("❌ Недостаточно свободных мест.", show_alert=True)
            return

        if data.startswith("lobby:"):
            _, platform, idx_str = data.split(":")
            idx = int(idx_str)
            lobbies = load_lobbies()
            parties = load_parties()
            leader_id, party = find_party_of(parties, user.id)
            members_to_add = party["members"] if party else [user.id]
            if len(lobbies[platform][idx]) + len(members_to_add) > LOBBY_SIZE:
                await query.answer("❌ Недостаточно места для всей пати!", show_alert=True)
                return
            for plt in PLATFORMS:
                for i, lobby in enumerate(lobbies[plt]):
                    for m in members_to_add:
                        if m in lobby:
                            lobby.remove(m)
            for m in members_to_add:
                if m not in lobbies[platform][idx]:
                    lobbies[platform][idx].append(m)
            save_lobbies(lobbies)
            await safe_delete(query.message)
            await update_lobby_for_all(platform, idx, context)
            if len(lobbies[platform][idx]) >= LOBBY_SIZE:
                await start_ready_check(platform, idx, context)
            return

        if data.startswith("lobby_leave:"):
            _, platform, idx_str = data.split(":")
            idx = int(idx_str)
            lobbies = load_lobbies()
            parties = load_parties()
            leader_id, party = find_party_of(parties, user.id)
            members_to_remove = party["members"] if (party and int(leader_id) == user.id) else [user.id]
            for m in members_to_remove:
                if m in lobbies[platform][idx]:
                    lobbies[platform][idx].remove(m)
            save_lobbies(lobbies)
            await safe_delete(query.message)
            await update_lobby_for_all(platform, idx, context)
            for m in members_to_remove:
                await safe_send(context.bot, m, "🚪 Вышел из лобби", reply_markup=kb_platforms())
            return

        # ---------- READY-CHECK ----------
        if data == "ready:confirm":
            ready_state = READY_CHECKS.get(user.id)
            if not ready_state:
                await query.answer("❌ Нет активной проверки готовности.", show_alert=True)
                return
            rc_id = ready_state["id"]
            rc = READY_CHECKS_BY_ID.get(rc_id)
            if not rc or rc["status"] != "pending":
                await query.answer("❌ Проверка готовности уже завершена.", show_alert=True)
                return
            rc["confirmed"].add(user.id)
            await safe_delete(query.message)
            await query.message.reply_text("✅ Готовность подтверждена! Ждём остальных...")
            if len(rc["confirmed"]) >= len(rc["players"]):
                await finalize_ready_check(rc_id, context)
            return

        # ---------- ВЕТО ----------
        if data.startswith("veto_ban:"):
            map_name = data.split(":", 1)[1]
            veto = context.user_data.get('veto')
            if not veto:
                await query.answer("❌ Вето не активно", show_alert=True)
                return
            success, error = veto_ban(veto, user_id, map_name)
            if not success:
                await query.answer(f"❌ {error}", show_alert=True)
                return
            await safe_delete(query.message)
            if veto["final_map"]:
                match = context.user_data.get('match', {})
                match['map'] = veto['final_map']
                match['status'] = 'in_progress'
                match['result_unlock_time'] = time.time() + RESULT_UNLOCK_DELAY_SECONDS
                context.user_data['match'] = match
                match_id = context.user_data.get('match_id')
                host_p = players.get(str(match.get('host')), {})
                final_text = (
                    f"🏆 Матч сформирован!\n\n"
                    f"ID: {match_id}\n"
                    f"Игра до 13\n"
                    f"Карта: {MAP_EMOJI.get(veto['final_map'], '')} {veto['final_map']}\n"
                    f"Раунд: 1:50\n"
                    f"Хост: @{host_p.get('tag', match.get('host'))}\n\n"
                    f"После матча хост отправляет фото, затем:\n/winner ct или /winner t"
                )
                for uid in match.get('players', []):
                    await safe_send(context.bot, uid, final_text)
                update_analytics_on_match(veto['final_map'])
                asyncio.create_task(_notify_result_ready(match.get('host'), match_id, context))
                return
            next_player = players.get(veto["turn"], {})
            tag = next_player.get("tag", veto["turn"])
            available = veto["pool"]
            await query.message.reply_text(
                f"🗺️ ВЕТО\n\nХод: @{tag}\nДоступные карты:\n" +
                "\n".join([f"• {MAP_EMOJI.get(m, '')} {m}" for m in available]),
                reply_markup=kb_veto(available),
            )
            return

        # ---------- ВЫБОР ПОБЕДИВШЕЙ КОМАНДЫ ----------
        if data.startswith("winteam:"):
            match = context.user_data.get('match')
            if not match or match.get('status') != 'awaiting_winning_team':
                await query.answer("❌ Нет матча, ожидающего выбор.", show_alert=True)
                return
            if match.get('host') != user.id:
                await query.answer("❌ Только хост может указать победителя.", show_alert=True)
                return
            choice = data.split(":", 1)[1]
            match['winner_team'] = choice
            match['status'] = 'awaiting_stats'
            context.user_data['match'] = match
            context.user_data['stats_mode'] = True
            context.user_data['stats_buffer'] = {}
            await safe_delete(query.message)
            await query.message.reply_text(
                "Теперь введи статистику каждого игрока в формате:\n@ник убийства-смерти-хс\n\n"
                "Например:\n@Vasya 18-9-5\n\nОтправляй по одному игроку.",
                reply_markup=kb_skip_stats(),
            )
            return

        if data == "stats:skip":
            await safe_delete(query.message)
            await finalize_match(update, context, skip_stats=True)
            return

        if data == "result:send":
            await query.answer()
            return

        # ================= АДМИН-ПАНЕЛЬ =================
        if user.id == OWNER_ID and data.startswith("admin:"):
            action = data.split(":", 1)[1]
            if action == "back":
                await safe_delete(query.message)
                context.user_data.pop('admin_action', None)
                context.user_data.pop('admin_target', None)
                await query.message.reply_text("👑 АДМИН-ПАНЕЛЬ\n\nВыбери действие:", reply_markup=kb_admin_panel())
                return
            if action == "analytics":
                pending = load_pending()
                await safe_delete(query.message)
                await query.message.reply_text(
                    build_analytics_text(players, pending),
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data="admin:back")]]),
                )
                return
            if action == "complaints_top":
                await safe_delete(query.message)
                reports = load_reports()
                counts = sorted(((players.get(uid, {}).get('tag', uid), len(lst)) for uid, lst in reports.items()),
                                 key=lambda x: x[1], reverse=True)[:10]
                text = "📢 ТОП ЖАЛОБ ПО ИГРОКАМ\n\n" + ("\n".join(f"@{t} — {n}" for t, n in counts) if counts else "Нет жалоб.")
                await query.message.reply_text(text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data="admin:back")]]))
                return
            if action == "history":
                history = load_history()
                text, total_pages = admin_history_page_text(history, 0)
                await safe_delete(query.message)
                await query.message.reply_text(text, reply_markup=kb_history_nav(0, total_pages))
                return
            if action == "unlink":
                all_players = sorted([(uid, p) for uid, p in players.items() if p.get("reg") == 1], key=lambda x: x[1].get('tag', ''))
                context.user_data['admin_unlink_players'] = all_players
                await safe_delete(query.message)
                await query.message.reply_text(
                    "🆔 ОТВЯЗАТЬ АЙДИ\n\nВыбери игрока:",
                    reply_markup=kb_players_list(all_players, "unlink", 0, back_cb="admin:back"),
                )
                return
            if action == "ban":
                all_players = sorted([(uid, p) for uid, p in players.items() if p.get("reg") == 1], key=lambda x: x[1].get('tag', ''))
                context.user_data['admin_ban_players'] = all_players
                await safe_delete(query.message)
                await query.message.reply_text(
                    "🔨 ЗАБАНИТЬ ИГРОКА\n\nВыбери игрока:",
                    reply_markup=kb_players_list(all_players, "ban", 0, back_cb="admin:back"),
                )
                return
            if action == "elo":
                await safe_delete(query.message)
                await query.message.reply_text("📊 УПРАВЛЕНИЕ ELO\n\nВыбери действие:", reply_markup=kb_admin_elo_action())
                return
            if action in ("elo_add", "elo_remove"):
                sub_action = "add" if action == "elo_add" else "remove"
                context.user_data['admin_action'] = f"elo_{sub_action}"
                all_players = sorted(
                    [(uid, p) for uid, p in players.items() if p.get("reg") == 1],
                    key=lambda x: x[1].get('elo', 0), reverse=True,
                )
                context.user_data[f'admin_elo_{sub_action}_players'] = all_players
                await safe_delete(query.message)
                await query.message.reply_text(
                    "👥 Выбери игрока:",
                    reply_markup=kb_players_list(all_players, f"eloact_{sub_action}", 0, back_cb="admin:back"),
                )
                return
            if action in ("change_id", "change_nick"):
                context.user_data['admin_action'] = action
                all_players = sorted([(uid, p) for uid, p in players.items() if p.get("reg") == 1], key=lambda x: x[1].get('tag', ''))
                context.user_data['admin_change_players'] = all_players
                await safe_delete(query.message)
                label = "ID" if action == "change_id" else "ник"
                await query.message.reply_text(
                    f"✏️ Выбери игрока для изменения {label}:",
                    reply_markup=kb_players_list(all_players, f"chg_{action}", 0, back_cb="admin:back"),
                )
                return

        if data.startswith("admin_history_page:"):
            page = int(data.split(":")[1])
            history = load_history()
            text, total_pages = admin_history_page_text(history, page)
            await query.message.edit_text(text, reply_markup=kb_history_nav(page, total_pages))
            return

        for prefix in ["unlink", "ban", "eloact_add", "eloact_remove", "chg_change_id", "chg_change_nick"]:
            if data.startswith(f"{prefix}_page:"):
                page = int(data.split(":")[1])
                key_map = {
                    "unlink": "admin_unlink_players", "ban": "admin_ban_players",
                    "eloact_add": "admin_elo_add_players", "eloact_remove": "admin_elo_remove_players",
                    "chg_change_id": "admin_change_players", "chg_change_nick": "admin_change_players",
                }
                lst = context.user_data.get(key_map[prefix], [])
                await query.message.edit_text(
                    query.message.text or "Выбери игрока:",
                    reply_markup=kb_players_list(lst, prefix, page, back_cb="admin:back"),
                )
                return

        if data.startswith("unlink:") and user.id == OWNER_ID:
            _, target_uid, _page = data.split(":")
            target_p = players.get(target_uid)
            if not target_p:
                await query.answer("❌ Игрок не найден.", show_alert=True)
                return
            await safe_delete(query.message)
            await query.message.reply_text(
                f"⚠️ Отвязать айди и сбросить ВСЮ статистику игрока @{target_p.get('tag', target_uid)}?",
                reply_markup=kb_confirm_yes_no("unlink", target_uid),
            )
            return

        if data.startswith("unlink_yes:") and user.id == OWNER_ID:
            target_uid = data.split(":")[1]
            fresh_players = load_players()
            target_p = fresh_players.get(target_uid)
            if target_p:
                audit_log("admin_unlink", user.id, {"target": target_uid, "old_data": target_p})
                del fresh_players[target_uid]
                save_players(fresh_players)
                await safe_send(context.bot, int(target_uid), "🆔 Твой аккаунт был отвязан. Статистика сброшена.")
            await safe_delete(query.message)
            await query.message.reply_text("✅ Айди отвязан, статистика сброшена (см. audit.log).")
            return

        if data.startswith("unlink_no:") and user.id == OWNER_ID:
            await safe_delete(query.message)
            await query.message.reply_text("Отменено.", reply_markup=kb_admin_panel())
            return

        if data.startswith("ban:") and user.id == OWNER_ID:
            _, target_uid, _page = data.split(":")
            target_p = players.get(target_uid)
            if not target_p:
                await query.answer("❌ Игрок не найден.", show_alert=True)
                return
            await safe_delete(query.message)
            await query.message.reply_text(
                f"⚠️ Забанить игрока @{target_p.get('tag', target_uid)}? Статистика НЕ будет сброшена.",
                reply_markup=kb_confirm_yes_no("ban", target_uid),
            )
            return

        if data.startswith("ban_yes:") and user.id == OWNER_ID:
            target_uid = data.split(":")[1]
            fresh_players = load_players()
            target_p = fresh_players.get(target_uid)
            if target_p:
                target_p['ban'] = True
                save_players(fresh_players)
                audit_log("admin_ban", user.id, {"target": target_uid})
                await safe_send(context.bot, int(target_uid), "🔨 Ты был забанен.")
            await safe_delete(query.message)
            await query.message.reply_text("✅ Игрок забанен.")
            return

        if data.startswith("ban_no:") and user.id == OWNER_ID:
            await safe_delete(query.message)
            await query.message.reply_text("Отменено.", reply_markup=kb_admin_panel())
            return

        if data.startswith("eloact_add:") or data.startswith("eloact_remove:"):
            if user.id != OWNER_ID:
                return
            parts = data.split(":")
            sub_action = "add" if data.startswith("eloact_add:") else "remove"
            target_uid = parts[1]
            target_p = players.get(target_uid)
            if not target_p:
                await query.answer("❌ Игрок не найден.", show_alert=True)
                return
            context.user_data['admin_target'] = target_uid
            context.user_data['admin_action'] = f"elo_{sub_action}"
            context.user_data['admin_input_mode'] = 'elo_amount'
            await safe_delete(query.message)
            action_type = "выдать" if sub_action == "add" else "убавить"
            await query.message.reply_text(
                f"🎯 Игрок: @{target_p['tag']}\nТекущий ELO: {target_p['elo']}\n\nВведи сумму ELO для {action_type}:"
            )
            return

        if data.startswith("chg_change_id:") or data.startswith("chg_change_nick:"):
            if user.id != OWNER_ID:
                return
            is_id = data.startswith("chg_change_id:")
            parts = data.split(":")
            target_uid = parts[1]
            target_p = players.get(target_uid)
            if not target_p:
                await query.answer("❌ Игрок не найден.", show_alert=True)
                return
            context.user_data['admin_target'] = target_uid
            context.user_data['admin_input_mode'] = 'change_id' if is_id else 'change_nick'
            await safe_delete(query.message)
            label = "новый ID (8-15 цифр)" if is_id else "новый ник"
            await query.message.reply_text(f"✏️ Введи {label} для @{target_p['tag']}:")
            return

        # ===== АДМИН: ПОДТВЕРЖДЕНИЕ МАТЧА =====
        if data.startswith("admin_ok:") or data.startswith("admin_no:"):
            if user.id not in ADMIN_IDS:
                await query.answer("❌ Только для администраторов.", show_alert=True)
                return
            action, pending_id = data.split(":", 1)
            pending = load_pending()
            record = pending.get(pending_id)
            if not record:
                await query.answer("❌ Заявка не найдена.", show_alert=True)
                return
            players_data = load_players()
            if action == "admin_ok":
                audit_log("admin_confirm_match", user.id, {"match_id": pending_id})
                record['status'] = 'confirmed'
                record['confirmed_by'] = players_data.get(str(user.id), {}).get('tag', str(user.id))
                pending[pending_id] = record
                save_pending(pending)
                for uid, summary in record['player_results'].items():
                    p = players_data.get(uid)
                    if not p:
                        continue
                    summary_text = (
                        f"✅ Матч подтверждён администратором!\n\n"
                        f"ID: {record['match_id']}\n"
                        f"Карта: {MAP_EMOJI.get(record['map_name'], '')} {record['map_name']}\n"
                        f"{'🏆 Победа' if summary['is_winner'] else 'Поражение'}\n"
                    )
                    if summary.get('calibrating'):
                        summary_text += f"Калибровка: {p['calib']}/{CALIBRATION_GAMES}\n"
                    elif summary.get('just_finished_calibration'):
                        summary_text += f"Калибровка завершена! ELO: {summary['new_elo']}\n"
                    else:
                        summary_text += f"{summary['delta']:+d} ELO → {summary['new_elo']}\n"
                    if summary.get('mvp'):
                        summary_text += "⭐ MVP матча!\n"
                    await safe_send(context.bot, int(uid), summary_text)
                save_players(players_data)
                await safe_delete(query.message)
                await query.message.reply_text(f"✅ Матч {record['match_id']} подтверждён.")
                return
            else:
                audit_log("admin_reject_match", user.id, {"match_id": pending_id})
                for uid, summary in record['player_results'].items():
                    p = players_data.get(uid)
                    if not p:
                        continue
                    snapshot = summary.get('_snapshot_before')
                    if snapshot:
                        rollback_match_result(p, snapshot)
                    map_name = record['map_name']
                    if map_name in p.get('maps', {}):
                        if summary['is_winner']:
                            p['maps'][map_name]['wins'] = max(0, p['maps'][map_name]['wins'] - 1)
                        else:
                            p['maps'][map_name]['losses'] = max(0, p['maps'][map_name]['losses'] - 1)
                    await safe_send(
                        context.bot, int(uid),
                        f"❌ Результат матча {record['match_id']} отклонён. Изменения отменены.",
                    )
                save_players(players_data)
                record['status'] = 'rejected'
                pending[pending_id] = record
                save_pending(pending)
                await safe_delete(query.message)
                await query.message.reply_text(f"❌ Матч {record['match_id']} отклонён.")
                return

    except Exception as e:
        logger.error(f"Ошибка в button_callback: {e}", exc_info=True)
        try:
            await update.callback_query.message.reply_text("❌ Произошла ошибка. Попробуйте снова.")
        except Exception:
            pass

async def _notify_result_ready(host_id, match_id, context: ContextTypes.DEFAULT_TYPE):
    await asyncio.sleep(RESULT_UNLOCK_DELAY_SECONDS)
    if host_id is None:
        return
    await safe_send(
        context.bot, host_id,
        f"📤 Можешь отправить скриншот результата матча {match_id}, затем /winner ct или /winner t.",
        reply_markup=kb_send_results(),
    )

def party_text(parties, leader_id, players):
    party = parties.get(str(leader_id)) or parties.get(leader_id)
    if not party:
        return None
    lines = ["🎉 ПАТИ\n"]
    for uid in party["members"]:
        p = players.get(str(uid), {})
        tag = p.get("tag", str(uid))
        crown = "👑 " if uid == party["leader"] else "• "
        lines.append(f"{crown}@{tag}")
    lines.append(f"\nСостав: {len(party['members'])}/{MAX_PARTY_SIZE}")
    return "\n".join(lines)

# ===================== ОБРАБОТЧИК СООБЩЕНИЙ =====================
STATS_LINE_RE = re.compile(r"^@?([A-Za-z0-9_]+)\s+(\d+)\s*-\s*(\d+)(?:\s*-\s*(\d+))?$")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if not rate_limiter.is_allowed(update.effective_user.id):
            await update.message.reply_text("⚠️ Слишком много запросов. Подождите 1 минуту.")
            return
        if await check_banned(update):
            return
        user = update.effective_user
        user_id = str(user.id)
        text = update.message.text.strip()
        if len(text) > 4096:
            await update.message.reply_text("❌ Слишком длинное сообщение")
            return
        players = get_players_cached()

        # ---- АДМИН: ввод суммы ELO ----
        if context.user_data.get('admin_input_mode') == 'elo_amount' and user.id == OWNER_ID:
            try:
                amount = int(text)
                if amount <= 0:
                    await update.message.reply_text("❌ Сумма должна быть положительным числом.")
                    return
                action = context.user_data.get('admin_action')
                target_uid = context.user_data.get('admin_target')
                fresh_players = load_players()
                target_player = fresh_players.get(target_uid)
                if not target_player:
                    await update.message.reply_text("❌ Игрок не найден.")
                    return
                if action == 'elo_add':
                    target_player['elo'] += amount
                else:
                    target_player['elo'] = max(0, target_player['elo'] - amount)
                target_player['level'] = level_from_elo(target_player['elo'])
                target_player['rank'] = rank_label(target_player['level'])
                audit_log(f"admin_{action}", user.id, {"target": target_uid, "amount": amount, "new_elo": target_player['elo']})
                save_players(fresh_players)
                await update.message.reply_text(f"✅ ELO обновлён. Новый ELO @{target_player['tag']}: {target_player['elo']}")
                context.user_data.pop('admin_input_mode', None)
                context.user_data.pop('admin_action', None)
                context.user_data.pop('admin_target', None)
                return
            except ValueError:
                await update.message.reply_text("❌ Введи число (например: 50)")
                return

        # ---- АДМИН: смена ID/ника ----
        if context.user_data.get('admin_input_mode') in ('change_id', 'change_nick') and user.id == OWNER_ID:
            mode = context.user_data['admin_input_mode']
            target_uid = context.user_data.get('admin_target')
            fresh_players = load_players()
            target_player = fresh_players.get(target_uid)
            if not target_player:
                await update.message.reply_text("❌ Игрок не найден.")
                return
            if mode == 'change_id':
                if not validate_standoff_id(text):
                    await update.message.reply_text("❌ ID должен быть числом из 8-15 цифр!")
                    return
                if find_by_sid(fresh_players, text):
                    await update.message.reply_text("❌ Этот ID уже занят!")
                    return
                old_sid = target_player['sid']
                target_player['sid'] = text
                audit_log("admin_change_id", user.id, {"target": target_uid, "old": old_sid, "new": text})
                await update.message.reply_text(f"✅ ID изменён на {text}.")
            else:
                if find_by_tag(fresh_players, text):
                    await update.message.reply_text("❌ Этот ник уже занят!")
                    return
                old_tag = target_player['tag']
                target_player['tag'] = text
                target_player['name'] = text
                audit_log("admin_change_nick", user.id, {"target": target_uid, "old": old_tag, "new": text})
                await update.message.reply_text(f"✅ Ник изменён на {text}.")
            save_players(fresh_players)
            context.user_data.pop('admin_input_mode', None)
            context.user_data.pop('admin_target', None)
            return

        # ---- ВХОД ----
        if context.user_data.get('auth_step') == 'login_id':
            if not validate_standoff_id(text):
                await update.message.reply_text("❌ ID должен быть числом из 8-15 цифр!")
                return
            owner_uid = find_by_sid(players, text)
            if not owner_uid:
                await update.message.reply_text("❌ Игрок с таким ID не найден.")
                return
            context.user_data['login_sid'] = text
            context.user_data['login_owner_uid'] = owner_uid
            context.user_data['auth_step'] = 'login_name'
            await update.message.reply_text(f"✅ ID найден!\n\nТеперь введи свой ник в Standoff 2:")
            return

        if context.user_data.get('auth_step') == 'login_name':
            owner_uid = context.user_data.get('login_owner_uid')
            fresh_players = load_players()
            owner_p = fresh_players.get(owner_uid)
            if not owner_p:
                await update.message.reply_text("❌ Ошибка. Начни заново.")
                context.user_data.clear()
                return
            if text.lstrip("@").lower() != owner_p.get('tag', '').lower():
                await update.message.reply_text("❌ Ник не совпадает. Попробуй снова.")
                return
            if owner_uid != user_id:
                fresh_players[user_id] = owner_p
                del fresh_players[owner_uid]
                fresh_players[user_id]['tg_username'] = user.username or str(user.id)
                save_players(fresh_players)
            context.user_data.clear()
            player = fresh_players.get(user_id)
            await update.message.reply_text("✅ Вход выполнен!")
            parties = load_parties()
            leader_id, _ = find_party_of(parties, user.id)
            await update.message.reply_text(
                main_menu_text(player),
                reply_markup=kb_main_menu(bool(leader_id)),
            )
            return

        # ---- РЕГИСТРАЦИЯ ----
        if context.user_data.get('auth_step') == 'reg_id':
            if not validate_standoff_id(text):
                await update.message.reply_text("❌ ID должен быть числом из 8-15 цифр!")
                return
            if find_by_sid(players, text):
                await update.message.reply_text("❌ Этот ID уже зарегистрирован!")
                return
            context.user_data['reg_pending_sid'] = text
            context.user_data['auth_step'] = 'reg_nick'
            await update.message.reply_text(
                f"✅ ID принят!\n\nТеперь введи свой НАСТОЯЩИЙ ник в Standoff 2:"
            )
            return

        if context.user_data.get('auth_step') == 'reg_nick':
            if find_by_tag(players, text):
                await update.message.reply_text("❌ Этот ник уже занят! Введи другой:")
                return
            context.user_data['reg_pending_tag'] = text
            context.user_data['auth_step'] = None
            await update.message.reply_text(
                f"✅ Ник принят: {text}\n\n"
                f"📸 Теперь отправь скриншот профиля из Standoff 2\n"
                f"На скриншоте должны быть видны:\n"
                f"• Твой ID: {context.user_data.get('reg_pending_sid')}\n"
                f"• Твой ник: {text}\n\n"
                f"Администраторы проверят и подтвердят регистрацию.",
                reply_markup=kb_reg_done(),
            )
            context.user_data['auth_step'] = 'reg_photo_wait'
            return

        # ---- ПРИГЛАШЕНИЕ В ПАТИ ----
        if context.user_data.get('party_invite_mode'):
            context.user_data['party_invite_mode'] = False
            target_uid = find_by_telegram_username(players, text)
            inviter = get_player(players, user.id)
            if not target_uid:
                await update.message.reply_text("❌ Игрок с таким Telegram юзернеймом не найден.")
                return
            if int(target_uid) == user.id:
                await update.message.reply_text("❌ Нельзя пригласить самого себя.")
                return
            parties = load_parties()
            leader_id, party = find_party_of(parties, user.id)
            if not party or int(leader_id) != user.id:
                await update.message.reply_text("❌ Ты не лидер пати.")
                return
            if int(target_uid) in party["members"]:
                await update.message.reply_text("❌ Этот игрок уже в пати.")
                return
            target_leader, target_party = find_party_of(parties, int(target_uid))
            if target_party:
                await update.message.reply_text("❌ Игрок уже в другой пати.")
                return
            if len(party["members"]) >= MAX_PARTY_SIZE:
                await update.message.reply_text("❌ Пати заполнена.")
                return
            party["pending_invite"] = {"target": int(target_uid), "invited_at": time.time()}
            parties[leader_id] = party
            save_parties(parties)
            target_player = players.get(str(target_uid), {})
            await update.message.reply_text(f"✅ Приглашение отправлено @{target_player.get('tag', target_uid)}!")
            sent = await safe_send(
                context.bot, int(target_uid),
                f"🎉 Игрок @{inviter['tag']} приглашает в пати.",
                reply_markup=kb_party_invite_response(leader_id),
            )
            if sent is None:
                await update.message.reply_text("⚠️ Не удалось доставить приглашение.")
            return

        # ---- ПОДДЕРЖКА ----
        if context.user_data.get('support_mode'):
            player = get_player(players, user.id)
            tag = player.get("tag", user_id) if player else user_id
            msg = f"🆘 Поддержка\n\n👤 @{tag} (ID: {user_id})\n📝 {text}"
            await safe_send(context.bot, ADMIN_CHAT_ID, msg)
            context.user_data['support_mode'] = False
            if player and player.get("reg") == 1:
                await update.message.reply_text("✅ Запрос отправлен!", reply_markup=kb_main_menu())
            else:
                await update.message.reply_text("✅ Запрос отправлен!", reply_markup=kb_start_auth())
            return

        # ---- ЖАЛОБА (текст) ----
        if context.user_data.get('complaint_target'):
            target_uid = context.user_data.pop('complaint_target')
            report_text = sanitize_input(text)
            if not report_text:
                await update.message.reply_text("❌ Текст жалобы не может быть пустым.")
                return
            reports = load_reports()
            reports.setdefault(target_uid, [])
            if any(r.get("by") == user_id for r in reports[target_uid]):
                await update.message.reply_text("❌ Ты уже жаловался на этого игрока.")
                return
            reports[target_uid].append({"by": user_id, "text": report_text, "timestamp": datetime.now().isoformat()})
            save_reports(reports)
            target_p = players.get(target_uid, {})
            await update.message.reply_text(f"✅ Жалоба на @{target_p.get('tag', target_uid)} отправлена.")
            return

        # ---- СТАТИСТИКА МАТЧА ----
        if context.user_data.get('stats_mode'):
            await handle_stats_input(update, context, text)
            return

    except Exception as e:
        logger.error(f"Ошибка в handle_message: {e}", exc_info=True)
        await update.message.reply_text("❌ Произошла ошибка. Попробуйте снова.")

async def handle_stats_input(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str):
    match = context.user_data.get('match')
    if not match:
        context.user_data['stats_mode'] = False
        await update.message.reply_text("❌ Нет активного матча.")
        return
    if match.get('host') != update.effective_user.id:
        await update.message.reply_text("❌ Статистику вводит только хост.")
        return
    m = STATS_LINE_RE.match(text)
    if not m:
        await update.message.reply_text(
            "❌ Неверный формат. Используй: @ник убийства-смерти-хс\nНапример: @Vasya 18-9-5",
            reply_markup=kb_skip_stats(),
        )
        return
    tag, kills_str, deaths_str, hs_str = m.groups()
    players = get_players_cached()
    uid = find_by_tag(players, tag)
    if not uid or int(uid) not in match.get('players', []):
        await update.message.reply_text(f"❌ Игрок @{tag} не найден в этом матче.", reply_markup=kb_skip_stats())
        return
    buffer = context.user_data.setdefault('stats_buffer', {})
    buffer[uid] = {"kills": int(kills_str), "deaths": int(deaths_str), "hs": int(hs_str) if hs_str else 0}
    remaining = [str(p) for p in match['players'] if str(p) not in buffer]
    if remaining:
        await update.message.reply_text(
            f"✅ Записано: @{tag} {kills_str}-{deaths_str}\n\nОсталось: {len(remaining)}",
            reply_markup=kb_skip_stats(),
        )
        return
    await finalize_match(update, context, skip_stats=False)

# ===================== ЗАВЕРШЕНИЕ МАТЧА =====================
async def finalize_match(update: Update, context: ContextTypes.DEFAULT_TYPE, skip_stats: bool):
    match = context.user_data.get('match')
    if not match:
        return
    players = load_players()
    winning_team = match['team_a'] if match.get('winner_team') == 'a' else match['team_b']
    losing_team = match['team_b'] if match.get('winner_team') == 'a' else match['team_a']
    stats_buffer = context.user_data.get('stats_buffer', {}) if not skip_stats else {}
    mvp_uid = None
    if stats_buffer:
        best_kills = -1
        for uid in [str(u) for u in winning_team]:
            s = stats_buffer.get(uid)
            if s and s['kills'] > best_kills:
                best_kills = s['kills']
                mvp_uid = uid
    map_name = match.get('map')
    match_id = match.get('match_id') or gen_match_id()
    player_results = {}
    winners_card, losers_card = [], []
    match_stats_record = {}
    for uid in [str(u) for u in winning_team]:
        p = players.get(uid)
        if not p:
            continue
        s = stats_buffer.get(uid, {"kills": 0, "deaths": 0, "hs": 0})
        is_mvp = (uid == mvp_uid)
        result = apply_match_result(p, True, s['kills'], s['deaths'], s.get('hs', 0), is_mvp)
        apply_map_result(p, map_name, True)
        player_results[uid] = {**result, "is_winner": True, "mvp": is_mvp}
        match_stats_record[uid] = s
        winners_card.append({
            "tag": p['tag'], "kd": f"{s['kills']}/{s['deaths']}", "mvp": is_mvp,
            "calibrating": result['calibrating'], "delta": result['delta'], "elo": result['new_elo'] or p['elo'],
        })
    for uid in [str(u) for u in losing_team]:
        p = players.get(uid)
        if not p:
            continue
        s = stats_buffer.get(uid, {"kills": 0, "deaths": 0, "hs": 0})
        result = apply_match_result(p, False, s['kills'], s['deaths'], s.get('hs', 0), False)
        apply_map_result(p, map_name, False)
        player_results[uid] = {**result, "is_winner": False, "mvp": False}
        match_stats_record[uid] = s
        losers_card.append({
            "tag": p['tag'], "kd": f"{s['kills']}/{s['deaths']}",
            "calibrating": result['calibrating'], "delta": result['delta'], "elo": result['new_elo'] or p['elo'],
        })
    save_players(players)
    invalidate_cache()
    append_history({
        "match_id": match_id, "map": map_name,
        "all_players": [str(u) for u in match.get('players', [])],
        "winners": [str(u) for u in winning_team], "losers": [str(u) for u in losing_team],
        "stats": match_stats_record, "mvp": mvp_uid,
        "timestamp": datetime.now(MOSCOW_TZ).isoformat(),
    })
    match_photo = context.user_data.get('match_photo')
    pending = load_pending()
    pending_id = match_id
    pending[pending_id] = {
        "match_id": match_id, "map_name": map_name, "player_results": player_results,
        "status": "awaiting_review", "match_photo": match_photo,
    }
    save_pending(pending)
    target_chat = ADMIN_CHAT_ID or (ADMIN_IDS[0] if ADMIN_IDS else None)
    if target_chat:
        if match_photo:
            await safe_send(context.bot, target_chat, f"📸 Скриншот результата матча {match_id}", photo=match_photo)
        winners_text = "\n".join([f"🏆 {p['tag']} ({p['kd']})" for p in winners_card])
        losers_text = "\n".join([f"❌ {p['tag']} ({p['kd']})" for p in losers_card])
        await safe_send(
            context.bot, target_chat,
            f"📋 Матч на проверку\nID: {match_id}\nКарта: {map_name}\n\n"
            f"🔵 ПОБЕДА:\n{winners_text}\n\n🔴 ПОРАЖЕНИЕ:\n{losers_text}",
            reply_markup=kb_admin_review(pending_id),
        )
    context.user_data['stats_mode'] = False
    context.user_data['stats_buffer'] = {}
    context.user_data['match'] = None
    context.user_data['match_id'] = None
    context.user_data['veto'] = None
    context.user_data['match_photo'] = None
    await update.effective_message.reply_text(
        "✅ Результат отправлен админам на проверку.\nКак только подтвердят — получишь уведомление."
    )

# ===================== ЗАПУСК МАТЧА =====================
def _find_subset_with_sum(groups, target_sum):
    n = len(groups)
    sizes = [len(g) for g in groups]
    def backtrack(i, remaining, chosen):
        if remaining == 0:
            return chosen
        if i >= n or remaining < 0:
            return None
        res = backtrack(i + 1, remaining - sizes[i], chosen + [i])
        if res is not None:
            return res
        return backtrack(i + 1, remaining, chosen)
    return backtrack(0, target_sum, [])

def _build_teams_with_parties(players_list, parties):
    groups = []
    seen = set()
    for uid in players_list:
        if uid in seen:
            continue
        leader_id, party = find_party_of(parties, uid)
        if party and all(m in players_list for m in party["members"]):
            group = [m for m in party["members"] if m in players_list]
            groups.append(group)
            seen.update(group)
        else:
            groups.append([uid])
            seen.add(uid)
    random.shuffle(groups)
    total = sum(len(g) for g in groups)
    team_size = total // 2
    chosen_indices = _find_subset_with_sum(groups, team_size)
    if chosen_indices is None:
        logger.warning("Не удалось разбить группы — использую запасной вариант.")
        team_a, team_b = [], []
        for group in sorted(groups, key=len, reverse=True):
            if len(team_a) + len(group) <= team_size:
                team_a.extend(group)
            elif len(team_b) + len(group) <= (total - team_size):
                team_b.extend(group)
            else:
                for m in group:
                    (team_a if len(team_a) < team_size else team_b).append(m)
        return team_a, team_b
    chosen_set = set(chosen_indices)
    team_a = [uid for i in chosen_indices for uid in groups[i]]
    team_b = [uid for i, g in enumerate(groups) if i not in chosen_set for uid in g]
    return team_a, team_b

async def start_match(platform: str, players_list: list, context: ContextTypes.DEFAULT_TYPE):
    parties = load_parties()
    players_list = players_list.copy()
    team_a, team_b = _build_teams_with_parties(players_list, parties)
    players = get_players_cached()
    def top_elo_in(team):
        return max(team, key=lambda uid: players.get(str(uid), {}).get('elo', 0))
    captain_a = top_elo_in(team_a)
    captain_b = top_elo_in(team_b)
    host = top_elo_in(players_list)
    match_id = gen_match_id()
    match = {
        "match_id": match_id, "platform": platform, "players": players_list,
        "team_a": team_a, "team_b": team_b, "captain_a": captain_a, "captain_b": captain_b,
        "host": host, "map": None, "status": "veto", "winner_team": None,
        "created_at": datetime.now().isoformat(),
    }
    veto = start_veto(str(captain_a), str(captain_b))
    for uid in players_list:
        udata = context.application.user_data[uid]
        udata['match'] = match
        udata['match_id'] = match_id
        udata['veto'] = veto
        team_label = "🔵 Команда А" if uid in team_a else "🔴 Команда Б"
        host_note = "\n🖥️ Ты хост этого матча!" if uid == host else ""
        await safe_send(
            context.bot, uid,
            f"🎮 Матч найден!\n\nID: {match_id}\nПлатформа: {platform}\nСобрано 10 игроков!\n"
            f"Твоя команда: {team_label}{host_note}\n\nНачинается бан карт...",
        )
    first_player = players.get(str(captain_a), {})
    tag = first_player.get("tag", str(captain_a))
    available = veto["pool"]
    await safe_send(
        context.bot, captain_a,
        f"🗺️ ВЕТО\n\nХод: @{tag}\nДоступные карты:\n" +
        "\n".join([f"• {MAP_EMOJI.get(m, '')} {m}" for m in available]),
        reply_markup=kb_veto(available),
    )

# ===================== HEALTH CHECK =====================
app_flask = Flask(__name__)
start_time = time.time()

@app_flask.route('/health')
def health():
    try:
        players = load_players()
        pending = load_pending()
        return jsonify({
            'status': 'ok', 'timestamp': datetime.now().isoformat(),
            'players': len(players), 'pending': len(pending),
            'uptime_seconds': int(time.time() - start_time),
        })
    except Exception as e:
        return jsonify({'status': 'error', 'error': str(e)}), 500

def run_health_server():
    try:
        app_flask.run(host='0.0.0.0', port=8080, debug=False, use_reloader=False)
    except Exception as e:
        logger.error(f"Health check не запущен: {e}")

# ===================== ЗАПУСК =====================
def _ensure_event_loop():
    import asyncio
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

def main():
    _ensure_event_loop()
    os.makedirs("backups", exist_ok=True)
    os.makedirs("logs", exist_ok=True)
    health_thread = threading.Thread(target=run_health_server, daemon=True)
    health_thread.start()
    logger.info("Health check запущен на порту 8080")
    request = HTTPXRequest(
        connect_timeout=CONNECT_TIMEOUT, read_timeout=READ_TIMEOUT,
        write_timeout=WRITE_TIMEOUT, pool_timeout=POOL_TIMEOUT,
    )
    app = ApplicationBuilder().token(BOT_TOKEN).request(request).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("winner", winner_command))
    app.add_handler(CommandHandler("admin", admin_command))
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    async def error_handler(update, context):
        logger.error(f"Update {update} вызвал ошибку: {context.error}", exc_info=True)
    app.add_error_handler(error_handler)
    logger.info("=" * 50)
    logger.info("🤖 Stranger Faceit 3.2 запущен!")
    logger.info(f"👑 Админы: {ADMIN_IDS}")
    logger.info(f"👑 Владелец: {OWNER_ID}")
    logger.info(f"🏠 Общий чат: {GENERAL_CHAT_ID}")
    logger.info(f"🔒 Админ-чат: {ADMIN_CHAT_ID}")
    logger.info("=" * 50)
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()

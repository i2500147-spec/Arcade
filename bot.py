#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Stranger Faceit — Telegram бот для матчмейкинга в Standoff 2.
Версия: 2.0 (безопасная и стабильная)
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
from dotenv import load_dotenv
load_dotenv()  # Загружаем переменные из .env
from collections import defaultdict
from datetime import datetime
from io import BytesIO
from logging.handlers import RotatingFileHandler

from flask import Flask, jsonify
from PIL import Image, ImageDraw, ImageFont
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import TelegramError, Forbidden, BadRequest
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler,
    MessageHandler, ContextTypes, filters,
)
from telegram.request import HTTPXRequest

# ===== НАСТРОЙКА ЛОГИРОВАНИЯ =====
os.makedirs("logs", exist_ok=True)

logger = logging.getLogger("stranger_faceit")
logger.setLevel(logging.INFO)

# Консольный вывод
console = logging.StreamHandler(sys.stdout)
console.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
logger.addHandler(console)

# Файловый вывод с ротацией
file_handler = RotatingFileHandler(
    "logs/bot.log",
    maxBytes=10*1024*1024,  # 10 MB
    backupCount=5
)
file_handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
logger.addHandler(file_handler)

# ===== ПРОВЕРКА ПЕРЕМЕННЫХ ОКРУЖЕНИЯ =====
REQUIRED_ENV = ["BOT_TOKEN", "ADMIN_IDS", "GENERAL_CHAT_ID", "ADMIN_CHAT_ID"]
missing_vars = [var for var in REQUIRED_ENV if not os.environ.get(var)]
if missing_vars:
    raise ValueError(f"❌ Отсутствуют обязательные переменные: {', '.join(missing_vars)}")

BOT_TOKEN = os.environ.get("BOT_TOKEN")
ADMIN_IDS = [int(x) for x in os.environ.get("ADMIN_IDS", "").split(",") if x.strip()]
GENERAL_CHAT_ID = int(os.environ.get("GENERAL_CHAT_ID"))
ADMIN_CHAT_ID = int(os.environ.get("ADMIN_CHAT_ID"))
CHAT_LINK = os.environ.get("CHAT_LINK", "")
REQUIRE_SUBSCRIPTION = int(os.environ.get("REQUIRE_SUBSCRIPTION", "1"))
SUBSCRIPTION_CHAT_ID = int(os.environ.get("SUBSCRIPTION_CHAT_ID", str(GENERAL_CHAT_ID)))

# ===== КОНСТАНТЫ =====
DATA_FILE = "players.json"
PENDING_FILE = "pending.json"
LOBBIES_FILE = "lobbies.json"
PARTIES_FILE = "parties.json"

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

LEVEL_THRESHOLDS = [
    (1, 0, 500), (2, 501, 750), (3, 751, 900), (4, 901, 1050),
    (5, 1051, 1200), (6, 1201, 1350), (7, 1351, 1530),
    (8, 1531, 1750), (9, 1751, 2000), (10, 2001, 10**9),
]
RANK_EMOJI = {1: "🥉", 2: "🥉", 3: "🥉", 4: "🥈", 5: "🥈", 6: "🥈", 7: "🥇", 8: "🥇", 9: "💎", 10: "👑"}

CONNECT_TIMEOUT = 120
READ_TIMEOUT = 120
WRITE_TIMEOUT = 120
POOL_TIMEOUT = 120

# ===== RATE LIMITER =====
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

# ===== ХРАНИЛИЩЕ С БЛОКИРОВКОЙ =====
_LOCK = threading.Lock()
PLAYER_CACHE = {"data": None, "timestamp": 0, "ttl": 60}

def _load_json(path, default):
    with _LOCK:
        if not os.path.exists(path):
            return default
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            logger.error(f"Ошибка чтения {path}: {e}")
            # Пытаемся восстановить из бэкапа
            backup_path = f"backups/{os.path.basename(path).replace('.json', '')}_latest.json"
            if os.path.exists(backup_path):
                logger.info(f"Восстанавливаем из бэкапа {backup_path}")
                with open(backup_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            return default

def _save_json(path, data):
    with _LOCK:
        tmp_path = path + ".tmp"
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, path)
            backup_data()  # Автоматический бэкап
        except Exception as e:
            logger.error(f"Ошибка сохранения {path}: {e}")
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            raise

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

def get_players_cached():
    """Получение игроков с кэшированием"""
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

def backup_data():
    """Создание бэкапа данных"""
    backup_dir = "backups"
    os.makedirs(backup_dir, exist_ok=True)
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    files = [DATA_FILE, PENDING_FILE, LOBBIES_FILE, PARTIES_FILE]
    
    for file in files:
        if os.path.exists(file):
            name = os.path.basename(file).replace('.json', '')
            backup_name = f"{backup_dir}/{name}_{timestamp}.json"
            shutil.copy2(file, backup_name)
    
    # Оставляем только 5 последних бэкапов
    backups = sorted([f for f in os.listdir(backup_dir) if f.endswith('.json')])
    if len(backups) > 5:
        for old_file in backups[:-5]:
            os.remove(os.path.join(backup_dir, old_file))

def find_party_of(parties, user_id):
    uid = int(user_id)
    for leader_id, party in parties.items():
        if uid in party.get("members", []):
            return leader_id, party
    return None, None

# ===== АУДИТ =====
def audit_log(action: str, user_id: int, details: dict):
    """Логирование действий администраторов"""
    log_entry = {
        "timestamp": datetime.now().isoformat(),
        "action": action,
        "user_id": user_id,
        "details": details
    }
    try:
        with open("audit.log", "a", encoding="utf-8") as f:
            f.write(json.dumps(log_entry, ensure_ascii=False) + "\n")
    except Exception as e:
        logger.error(f"Ошибка аудита: {e}")

# ===== ВАЛИДАЦИЯ =====
def sanitize_input(text: str) -> str:
    """Очистка ввода от опасных символов"""
    text = re.sub(r'[\x00-\x1f\x7f-\x9f]', '', text)
    return text.strip()[:100]

def validate_standoff_id(id_text: str) -> bool:
    """Проверка ID Standoff 2"""
    if not id_text.isdigit():
        return False
    if len(id_text) < 8 or len(id_text) > 15:
        return False
    return True

def validate_username(username: str) -> bool:
    """Проверка ника"""
    return bool(re.match(r'^[A-Za-z0-9_]{3,20}$', username))

def is_banned(player) -> bool:
    """Проверка бана"""
    if not player or not player.get('ban'):
        return False
    ban_until = player['ban']
    if isinstance(ban_until, (int, float)):
        return ban_until > time.time()
    return False

# ===== ИГРОКИ =====
def new_player(sid):
    return {
        "reg": 0, "sid": sid, "name": "", "tag": "", "elo": 0, "level": 0,
        "wins": 0, "losses": 0, "matches": 0, "mvps": 0, "rank": "🎯", "ban": None,
        "history": [], "maps": {m: {"wins": 0, "losses": 0} for m in MAPS},
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

def apply_match_result(player, is_winner, kills, deaths, is_mvp):
    points = compute_match_points(is_winner, kills, deaths, is_mvp)
    snapshot_before = {
        "matches": player["matches"], "wins": player["wins"], "losses": player["losses"],
        "mvps": player["mvps"], "calib": player["calib"],
        "calib_elo_buffer": player["calib_elo_buffer"], "elo": player["elo"],
        "level": player["level"], "rank": player["rank"],
    }
    player["matches"] += 1
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

# ===== ВЕТО =====
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

# ===== КАРТОЧКА МАТЧА =====
CARD_W, CARD_H = 900, 620
CARD_BG = (18, 20, 28)
CARD_PANEL = (28, 31, 42)
WIN_COLOR = (76, 175, 128)
LOSE_COLOR = (214, 79, 79)
TEXT_MAIN = (235, 236, 240)
TEXT_DIM = (150, 154, 165)
GOLD = (240, 190, 80)
_FONT_CACHE = {}

def _font(size, bold=False):
    cache_key = (size, bold)
    if cache_key in _FONT_CACHE:
        return _FONT_CACHE[cache_key]
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/system/fonts/Roboto-Bold.ttf" if bold else "/system/fonts/Roboto-Regular.ttf",
        "/system/fonts/NotoSans-Bold.ttf" if bold else "/system/fonts/NotoSans-Regular.ttf",
        os.path.expanduser("~/fonts/DejaVuSans-Bold.ttf") if bold else os.path.expanduser("~/fonts/DejaVuSans.ttf"),
    ]
    for path in candidates:
        if path and os.path.exists(path):
            try:
                font = ImageFont.truetype(path, size)
                _FONT_CACHE[cache_key] = font
                return font
            except OSError:
                continue
    font = ImageFont.load_default()
    _FONT_CACHE[cache_key] = font
    return font

def render_match_card(match_report):
    img = Image.new("RGB", (CARD_W, CARD_H), CARD_BG)
    draw = ImageDraw.Draw(img)
    title_font = _font(30, bold=True)
    sub_font = _font(18)
    map_emoji = MAP_EMOJI.get(match_report["map_name"], "🗺️")
    header = f"{map_emoji}  {match_report['map_name']}"
    draw.text((CARD_W // 2, 30), header, font=title_font, fill=GOLD, anchor="ma")
    draw.text((CARD_W // 2, 72), match_report["match_id"], font=sub_font, fill=TEXT_DIM, anchor="ma")
    col_w = 380
    left_x = 40
    right_x = CARD_W - 40 - col_w
    top_y = 190
    draw.text((left_x, top_y - 34), "🔵 ПОБЕДА", font=_font(22, bold=True), fill=WIN_COLOR)
    draw.text((right_x, top_y - 34), "🔴 ПОРАЖЕНИЕ", font=_font(22, bold=True), fill=LOSE_COLOR)
    y = top_y
    for pl in match_report["winners"]:
        mvp_star = " ⭐" if pl.get("mvp") else ""
        name = f"@{pl['tag']}{mvp_star}"
        sub = f"{pl['kd']} • Калибровка" if pl.get("calibrating") else f"{pl['kd']} • {pl['delta']:+d} ELO → {pl['elo']}"
        row_h = 64
        draw.rounded_rectangle([left_x, y, left_x + col_w, y + row_h], radius=10, fill=CARD_PANEL)
        draw.rectangle([left_x, y, left_x + 5, y + row_h], fill=WIN_COLOR)
        draw.text((left_x + 20, y + 10), name, font=_font(22, bold=True), fill=TEXT_MAIN)
        draw.text((left_x + 20, y + 36), sub, font=_font(16), fill=TEXT_DIM)
        y += row_h + 10
    y = top_y
    for pl in match_report["losers"]:
        name = f"@{pl['tag']}"
        sub = f"{pl['kd']} • Калибровка" if pl.get("calibrating") else f"{pl['kd']} • {pl['delta']:+d} ELO → {pl['elo']}"
        row_h = 64
        draw.rounded_rectangle([right_x, y, right_x + col_w, y + row_h], radius=10, fill=CARD_PANEL)
        draw.rectangle([right_x, y, right_x + 5, y + row_h], fill=LOSE_COLOR)
        draw.text((right_x + 20, y + 10), name, font=_font(22, bold=True), fill=TEXT_MAIN)
        draw.text((right_x + 20, y + 36), sub, font=_font(16), fill=TEXT_DIM)
        y += row_h + 10
    footer_y = CARD_H - 50
    footer = f"✅ Проверил: @{match_report['confirmed_by']}" if match_report.get("confirmed_by") else "Stranger Faceit"
    draw.text((CARD_W // 2, footer_y), footer, font=_font(18), fill=TEXT_DIM, anchor="ma")
    buf = BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    buf.name = "match_card.png"
    return buf

# ===== КЛАВИАТУРЫ =====
def kb_register():
    return InlineKeyboardMarkup([[InlineKeyboardButton("📝 Регистрация", callback_data="reg:start")]])

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
        [InlineKeyboardButton("🆘 Поддержка", callback_data="menu:support")],
    ])

def kb_back_main():
    return InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data="menu:main")]])

def kb_platforms():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📱 Phone", callback_data="platform:Phone")],
        [InlineKeyboardButton("💻 PC", callback_data="platform:PC")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="menu:main")],
    ])

def kb_lobbies(platform, lobbies, min_free_slots=1):
    rows = []
    for i in range(LOBBIES_PER_PLATFORM):
        count = len(lobbies[platform][i])
        free = LOBBY_SIZE - count
        label = f"Лобби {i + 1} ({count}/{LOBBY_SIZE})"
        if free >= min_free_slots:
            rows.append([InlineKeyboardButton(label, callback_data=f"lobby:{platform}:{i}")])
        else:
            rows.append([InlineKeyboardButton(f"🔒 {label}", callback_data="lobby:full")])
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
        [
            InlineKeyboardButton("✅ Принять", callback_data=f"party_accept:{leader_id}"),
            InlineKeyboardButton("❌ Отказать", callback_data=f"party_decline:{leader_id}"),
        ]
    ])

def kb_ready_check():
    return InlineKeyboardMarkup([[InlineKeyboardButton("✅ Подтвердить", callback_data="ready:confirm")]])

# ===== ВСПОМОГАТЕЛЬНОЕ =====
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
        logger.warning("Не удалось отправить сообщение %s: бот заблокирован или ЛС недоступны", chat_id)
    except TelegramError:
        logger.exception("Ошибка отправки сообщения в чат %s", chat_id)
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
    """Проверка бана пользователя"""
    players = get_players_cached()
    player = get_player(players, update.effective_user.id)
    if is_banned(player):
        await update.effective_message.reply_text(
            f"⛔ Вы забанены до {datetime.fromtimestamp(player['ban']).strftime('%d.%m.%Y %H:%M')}"
        )
        return True
    return False

async def require_subscription(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Проверка подписки"""
    if REQUIRE_SUBSCRIPTION and not await is_subscribed(context.bot, update.effective_user.id):
        await update.effective_message.reply_text(
            "📢 Подпишись на наш чат, чтобы использовать бота!",
            reply_markup=kb_subscribe()
        )
        return False
    return True

def main_menu_text(player):
    return (
        f"🏠 *ГЛАВНОЕ МЕНЮ*\n\n"
        f"👤 @{player['tag']}\n"
        f"📊 {elo_display(player)}\n"
        f"🏆 Побед: {player['wins']} | ❌ Поражений: {player['losses']}\n"
        f"🎯 Матчей: {player['matches']}"
    )

def profile_text(player):
    winrate = round((player['wins'] / player['matches'] * 100) if player['matches'] > 0 else 0, 1)
    text = (
        f"📊 *МОЙ ПРОФИЛЬ*\n\n"
        f"👤 @{player['tag']}\n"
        f"🆔 {player['sid']}\n"
        f"🏅 {player['rank']}\n"
        f"📊 {elo_display(player)}\n\n"
        f"📈 *СТАТИСТИКА*\n─────────────────\n"
        f"🎯 Матчей: {player['matches']}\n"
        f"🏆 Побед: {player['wins']}\n"
        f"❌ Поражений: {player['losses']}\n"
        f"📊 Winrate: {winrate}%\n"
        f"⭐ MVP: {player['mvps']}\n"
    )
    if player.get('calib', 0) < CALIBRATION_GAMES:
        text += f"📌 Калибровка: {player['calib']}/{CALIBRATION_GAMES}\n"
    text += "\n📊 *ПО КАРТАМ*\n─────────────────\n"
    for map_name, stats in player['maps'].items():
        total = stats['wins'] + stats['losses']
        if total > 0:
            rate = round((stats['wins'] / total * 100), 1)
            text += f"{MAP_EMOJI.get(map_name, '')} {map_name}: {stats['wins']}-{stats['losses']} ({rate}%)\n"
    return text

def party_text(parties, leader_id, players):
    party = parties.get(str(leader_id)) or parties.get(leader_id)
    if not party:
        return None
    lines = ["🎉 *ПАТИ*\n"]
    for uid in party["members"]:
        p = players.get(str(uid), {})
        tag = p.get("tag", str(uid))
        crown = "👑 " if uid == party["leader"] else "• "
        lines.append(f"{crown}@{tag}")
    lines.append(f"\n👥 Состав: {len(party['members'])}/{MAX_PARTY_SIZE}")
    return "\n".join(lines)

# ===== READY-CHECK (без JobQueue) =====
READY_CHECKS_BY_ID = {}
READY_CHECKS = {}
_rc_counter = 0

def _next_rc_id():
    global _rc_counter
    _rc_counter += 1
    return f"rc{_rc_counter}"

async def ready_check_timer(rc_id: str, timeout: int, context: ContextTypes.DEFAULT_TYPE):
    """Таймер для ready-check через asyncio.sleep"""
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
        "lobby_idx": lobby_idx, "status": "pending", "created_at": time.time()
    }
    READY_CHECKS_BY_ID[rc_id] = rc
    for uid in players_list:
        READY_CHECKS[uid] = {"id": rc_id}

    for uid in players_list:
        await safe_send(
            context.bot, uid,
            f"👥 *Лобби набрано!* ({LOBBY_SIZE}/{LOBBY_SIZE})\n\n"
            f"У тебя есть {READY_CHECK_TIMEOUT_SECONDS} секунд, чтобы подтвердить участие.\n"
            f"Если не успеешь — будешь удалён(а) из матча.",
            reply_markup=kb_ready_check(), parse_mode="Markdown",
        )

    # Запускаем таймер через asyncio
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
            await safe_send(
                context.bot, uid,
                "❌ Ты не подтвердил(а) готовность вовремя и был(а) удалён(а) из матча.",
                reply_markup=kb_platforms(),
            )
        lobbies = load_lobbies()
        lobbies[rc["platform"]][rc["lobby_idx"]] = confirmed.copy()
        save_lobbies(lobbies)
        for uid in confirmed:
            count = len(confirmed)
            await safe_send(
                context.bot, uid,
                f"⚠️ Не все игроки подтвердили готовность. Матч отменён.\n"
                f"Ты остаёшься в лобби {rc['lobby_idx'] + 1} ({count}/{LOBBY_SIZE}).",
                reply_markup=kb_in_lobby(rc["platform"], rc["lobby_idx"]),
            )
        return

    await start_match(rc["platform"], rc["players"], context)

# ===== ОСНОВНЫЕ ОБРАБОТЧИКИ =====
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
            "🎮 *STRANGER FACEIT*\n\nДобро пожаловать! Для начала игры необходимо "
            "зарегистрироваться.\nНажми на кнопку ниже.",
            reply_markup=kb_register(), parse_mode="Markdown",
        )
        return
    
    parties = load_parties()
    leader_id, _ = find_party_of(parties, user.id)
    await update.message.reply_text(
        main_menu_text(player), reply_markup=kb_main_menu(in_party=bool(leader_id)),
        parse_mode="Markdown",
    )

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not rate_limiter.is_allowed(update.effective_user.id):
        await update.message.reply_text("⚠️ Слишком много запросов. Подождите 1 минуту.")
        return
    
    if await check_banned(update):
        return
    
    match = context.user_data.get('match')
    if not match or match.get('status') != 'in_progress':
        await update.message.reply_text("❌ Сейчас нет матча, ожидающего скриншот результата.")
        return
    
    photo_sizes = update.message.photo
    if not photo_sizes:
        return
    
    # Проверяем размер файла
    file = await context.bot.get_file(photo_sizes[-1].file_id)
    if file.file_size > 5 * 1024 * 1024:  # 5 MB
        await update.message.reply_text("❌ Слишком большой файл (макс 5 MB)")
        return
    
    file_id = photo_sizes[-1].file_id
    context.user_data['match_photo'] = file_id
    await update.message.reply_text(
        "✅ Скриншот результата принят!\n\nТеперь объяви победившую сторону:\n"
        "`/winner ct` или `/winner t`",
        parse_mode="Markdown",
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
    
    args = context.args
    if not args or args[0].lower() not in ("ct", "t"):
        await update.message.reply_text("Использование: /winner ct  или  /winner t")
        return
    
    if not context.user_data.get('match_photo'):
        await update.message.reply_text("📸 Сначала отправь скриншот результата матча (фото).")
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
        f"✅ Победила сторона: *{side.upper()}*\n\nКакая команда играла за {side.upper()} "
        f"и победила?",
        reply_markup=kb, parse_mode="Markdown",
    )

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

        # ===== ПОДПИСКА =====
        if data == "sub:check":
            if await is_subscribed(context.bot, user.id):
                await safe_delete(query.message)
                if not player or player.get("reg") != 1:
                    await query.message.reply_text(
                        "✅ Подписка подтверждена!\n\n🎮 *STRANGER FACEIT*\n\nНажми кнопку для регистрации",
                        reply_markup=kb_register(), parse_mode="Markdown",
                    )
                else:
                    parties = load_parties()
                    leader_id, _ = find_party_of(parties, user.id)
                    await query.message.reply_text(
                        main_menu_text(player), reply_markup=kb_main_menu(bool(leader_id)),
                        parse_mode="Markdown",
                    )
            else:
                await query.answer("❌ Подписка не найдена. Подпишись и попробуй снова.", show_alert=True)
            return

        # ===== РЕГИСТРАЦИЯ =====
        if data == "reg:start":
            await safe_delete(query.message)
            await query.message.reply_text(
                "📝 *РЕГИСТРАЦИЯ*\n\nШаг 1 из 2:\nВведи свой *ID в Standoff 2*\n\nПример: `1002929387`",
                reply_markup=kb_back_main(), parse_mode="Markdown",
            )
            context.user_data['reg_step'] = 'id'
            return

        # ===== ГЛАВНОЕ МЕНЮ =====
        if data == "menu:main":
            await safe_delete(query.message)
            context.user_data.pop('reg_step', None)
            context.user_data.pop('support_mode', None)
            context.user_data.pop('party_invite_mode', None)
            if not player or player.get("reg") != 1:
                await query.message.reply_text(
                    "🎮 *STRANGER FACEIT*\n\nНажми кнопку для регистрации",
                    reply_markup=kb_register(), parse_mode="Markdown",
                )
                return
            parties = load_parties()
            leader_id, _ = find_party_of(parties, user.id)
            await query.message.reply_text(
                main_menu_text(player), reply_markup=kb_main_menu(bool(leader_id)), parse_mode="Markdown",
            )
            return

        # ===== ПРОФИЛЬ =====
        if data == "menu:profile":
            await safe_delete(query.message)
            if not player or player.get("reg") != 1:
                await query.message.reply_text("Сначала зарегистрируйся!", reply_markup=kb_register())
                return
            await query.message.reply_text(profile_text(player), reply_markup=kb_back_main(), parse_mode="Markdown")
            return

        # ===== ТОП =====
        if data == "menu:top":
            await safe_delete(query.message)
            sorted_players = sorted(
                [p for p in players.values() if p.get("reg") == 1 and p.get("calib", 0) >= CALIBRATION_GAMES],
                key=lambda x: x.get("elo", 0), reverse=True,
            )[:10]
            text = "🏆 *ТОП ИГРОКОВ*\n\n"
            if not sorted_players:
                text += "Пока нет игроков, завершивших калибровку.\n"
            for i, p in enumerate(sorted_players, 1):
                medal = {1: "👑", 2: "🥈", 3: "🥉"}.get(i, f"{i}.")
                text += f"{medal} @{p['tag']}\n    📊 {p['elo']} ELO | {p['rank']}\n"
            total = len([p for p in players.values() if p.get("reg") == 1])
            text += f"\n📊 Всего игроков: {total}"
            await query.message.reply_text(text, reply_markup=kb_back_main(), parse_mode="Markdown")
            return

        # ===== ПОДДЕРЖКА =====
        if data == "menu:support":
            await safe_delete(query.message)
            context.user_data['support_mode'] = True
            await query.message.reply_text(
                "🆘 *ПОДДЕРЖКА*\n\nОпиши свою проблему одним сообщением.\nАдминистраторы получат уведомление.",
                reply_markup=kb_back_main(), parse_mode="Markdown",
            )
            return

        # ===== ПАТИ =====
        if data == "menu:party":
            await safe_delete(query.message)
            if not player or player.get("reg") != 1:
                await query.message.reply_text("Сначала зарегистрируйся!", reply_markup=kb_register())
                return
            parties = load_parties()
            leader_id, party = find_party_of(parties, user.id)
            if not party:
                await query.message.reply_text(
                    "🎉 *ПАТИ*\n\nТы пока не в группе.\nПригласи друга, чтобы играть в одной команде!",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("➕ Пригласить игрока", callback_data="party:invite")],
                        [InlineKeyboardButton("⬅️ Назад", callback_data="menu:main")],
                    ]),
                    parse_mode="Markdown",
                )
                return
            is_leader = (int(leader_id) == user.id)
            text = party_text(parties, leader_id, players)
            await query.message.reply_text(
                text, reply_markup=kb_party_menu(is_leader, len(party["members"])), parse_mode="Markdown",
            )
            return

        # ===== ПРИГЛАШЕНИЕ В ПАТИ =====
        if data == "party:invite":
            await safe_delete(query.message)
            parties = load_parties()
            leader_id, party = find_party_of(parties, user.id)
            if not party:
                parties[user_id] = {"leader": user.id, "members": [user.id], "pending_invite": None}
                save_parties(parties)
                leader_id, party = user_id, parties[user_id]
            if int(leader_id) != user.id:
                await query.message.reply_text("❌ Приглашать может только лидер пати.", reply_markup=kb_back_main())
                return
            if len(party["members"]) >= MAX_PARTY_SIZE:
                await query.message.reply_text("❌ Пати уже заполнена (максимум 5 человек).", reply_markup=kb_back_main())
                return
            context.user_data['party_invite_mode'] = True
            await query.message.reply_text(
                "👤 Введите юзернейм игрока, которого хотите пригласить (например: `@vasyapetlin`):",
                reply_markup=kb_back_main(), parse_mode="Markdown",
            )
            return

        # ===== ВЫХОД ИЗ ПАТИ =====
        if data == "party:leave":
            await safe_delete(query.message)
            parties = load_parties()
            leader_id, party = find_party_of(parties, user.id)
            if not party:
                await query.message.reply_text("Ты не в пати.", reply_markup=kb_back_main())
                return
            if int(leader_id) == user.id:
                for uid in party["members"]:
                    if uid != user.id:
                        await safe_send(context.bot, uid, "🎉 Пати расформирована лидером.")
                del parties[leader_id]
                save_parties(parties)
                await query.message.reply_text("🚪 Пати расформирована.", reply_markup=kb_back_main())
            else:
                party["members"].remove(user.id)
                parties[leader_id] = party
                save_parties(parties)
                await safe_send(context.bot, int(leader_id), f"🎉 @{player['tag']} покинул(а) пати.")
                await query.message.reply_text("🚪 Ты покинул(а) пати.", reply_markup=kb_back_main())
            return

        # ===== ПРИНЯТЬ/ОТКЛОНИТЬ ПРИГЛАШЕНИЕ =====
        if data.startswith("party_accept:") or data.startswith("party_decline:"):
            action, leader_id_str = data.split(":", 1)
            parties = load_parties()
            party = parties.get(leader_id_str)
            await safe_delete(query.message)

            if not party or not party.get("pending_invite") or party["pending_invite"].get("target") != user.id:
                await query.message.reply_text("❌ Это приглашение больше не действительно.")
                return

            leader_uid = int(leader_id_str)
            leader_player = players.get(leader_id_str, {})

            if action == "party_decline":
                party["pending_invite"] = None
                parties[leader_id_str] = party
                save_parties(parties)
                await query.message.reply_text("❌ Приглашение отклонено.", reply_markup=kb_back_main())
                await safe_send(
                    context.bot, leader_uid,
                    f"❌ @{player['tag']} отклонил(а) приглашение в пати." if player else "Приглашение отклонено.",
                )
                return

            # === party_accept ===
            # *** ИСПРАВЛЕНИЕ TOCTOU: проверяем, не создал ли игрок себе пати ***
            target_leader, target_party = find_party_of(parties, user.id)
            if target_party and int(target_leader) == user.id:
                # Удаляем его сольную пати
                del parties[str(user.id)]
                save_parties(parties)
                # Перезагружаем данные
                parties = load_parties()
                party = parties.get(leader_id_str)
                if not party:
                    await query.message.reply_text("❌ Пати больше не существует.")
                    return

            if len(party["members"]) >= MAX_PARTY_SIZE:
                await query.message.reply_text("❌ Пати уже заполнена.", reply_markup=kb_back_main())
                party["pending_invite"] = None
                parties[leader_id_str] = party
                save_parties(parties)
                return

            # Удаляем игрока из любых лобби
            lobbies = load_lobbies()
            changed_lobby = False
            for plt in PLATFORMS:
                for i, lobby in enumerate(lobbies[plt]):
                    if user.id in lobby:
                        lobby.remove(user.id)
                        changed_lobby = True
            if changed_lobby:
                save_lobbies(lobbies)

            party["members"].append(user.id)
            party["pending_invite"] = None
            parties[leader_id_str] = party
            save_parties(parties)

            await query.message.reply_text(
                f"✅ Ты присоединился(-ась) к пати @{leader_player.get('tag', leader_id_str)}!",
                reply_markup=kb_back_main(),
            )
            text = party_text(parties, leader_id_str, players)
            for uid in party["members"]:
                is_leader_uid = (uid == leader_uid)
                await safe_send(
                    context.bot, uid, text,
                    reply_markup=kb_party_menu(is_leader_uid, len(party["members"])),
                    parse_mode="Markdown",
                )
            return

        # ===== ПОИСК МАТЧА =====
        if data == "menu:find":
            await safe_delete(query.message)
            if not player or player.get("reg") != 1:
                await query.message.reply_text("Сначала зарегистрируйся!", reply_markup=kb_register())
                return
            await query.message.reply_text("📱 *ВЫБЕРИ ПЛАТФОРМУ*", reply_markup=kb_platforms(), parse_mode="Markdown")
            return

        if data.startswith("platform:"):
            platform = data.split(":")[1]
            lobbies = load_lobbies()
            parties = load_parties()
            leader_id, party = find_party_of(parties, user.id)

            if party and int(leader_id) != user.id:
                await safe_delete(query.message)
                await query.message.reply_text(
                    "❌ Только лидер пати может выбирать лобби. Дождись, пока лидер найдёт матч.",
                    reply_markup=kb_back_main(),
                )
                return

            min_free = len(party["members"]) if party else 1
            await safe_delete(query.message)
            await query.message.reply_text(
                f"📱 *{platform.upper()} ЛОББИ*" + (f"\n(нужно {min_free} свободных мест для пати)" if party else ""),
                reply_markup=kb_lobbies(platform, lobbies, min_free_slots=min_free),
                parse_mode="Markdown",
            )
            return

        if data == "lobby:full":
            await query.answer("❌ Недостаточно свободных мест в этом лобби.", show_alert=True)
            return

        if data.startswith("lobby:"):
            _, platform, idx_str = data.split(":")
            idx = int(idx_str)
            lobbies = load_lobbies()
            parties = load_parties()
            leader_id, party = find_party_of(parties, user.id)

            members_to_add = party["members"] if party else [user.id]

            if len(lobbies[platform][idx]) + len(members_to_add) > LOBBY_SIZE:
                await query.answer("❌ Недостаточно места для всей пати в этом лобби!", show_alert=True)
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

            players_list_txt = []
            for uid in lobbies[platform][idx]:
                p = players.get(str(uid), {})
                tag = p.get("tag", str(uid))
                elo = p.get("elo", 0)
                players_list_txt.append(f"@{tag} ({elo} ELO)")
            text = f"📋 *ЛОББИ {idx + 1}* ({len(lobbies[platform][idx])}/{LOBBY_SIZE})\n\n👥 *ИГРОКИ:*\n"
            for i, p in enumerate(players_list_txt, 1):
                text += f"{i}. {p}\n"
            text += f"\n⏳ Ожидание: {len(lobbies[platform][idx])}/{LOBBY_SIZE}"

            await safe_delete(query.message)
            for m in members_to_add:
                await safe_send(context.bot, m, text, reply_markup=kb_in_lobby(platform, idx), parse_mode="Markdown")

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
            for m in members_to_remove:
                await safe_send(context.bot, m, "🚪 Вышел из лобби", reply_markup=kb_platforms())
            return

        # ===== READY-CHECK =====
        if data == "ready:confirm":
            ready_state = READY_CHECKS.get(user.id)
            if not ready_state:
                await query.answer("❌ Для тебя сейчас нет активной проверки готовности.", show_alert=True)
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

        # ===== ВЕТО =====
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
                context.user_data['match'] = match
                match_id = context.user_data.get('match_id')
                final_text = (
                    f"🏆 *Матч сформирован!*\n\n🆔 {match_id}\n"
                    f"📍 Финальная карта: {MAP_EMOJI.get(veto['final_map'], '')} {veto['final_map']}\n\n"
                    f"📌 После матча капитан отправляет команду:\n`/winner ct` или `/winner t`\n"
                    f"а затем статистику игроков."
                )
                for uid in match.get('players', []):
                    await safe_send(context.bot, uid, final_text, parse_mode="Markdown")
                return
            next_player = players.get(veto["turn"], {})
            tag = next_player.get("tag", veto["turn"])
            available = veto["pool"]
            await query.message.reply_text(
                f"🗺️ *ВЕТО*\n\nХод: @{tag}\nДоступные карты:\n" +
                "\n".join([f"• {MAP_EMOJI.get(m, '')} {m}" for m in available]),
                reply_markup=kb_veto(available), parse_mode="Markdown",
            )
            return

        # ===== ВЫБОР ПОБЕДИВШЕЙ КОМАНДЫ =====
        if data.startswith("winteam:"):
            match = context.user_data.get('match')
            if not match or match.get('status') != 'awaiting_winning_team':
                await query.answer("❌ Нет матча, ожидающего выбор команды.", show_alert=True)
                return
            choice = data.split(":", 1)[1]
            match['winner_team'] = choice
            match['status'] = 'awaiting_stats'
            context.user_data['match'] = match
            context.user_data['stats_mode'] = True
            context.user_data['stats_buffer'] = {}
            await safe_delete(query.message)
            await query.message.reply_text(
                "Теперь введи статистику каждого игрока в формате:\n`@ник убийства-смерти`\n\n"
                "Например:\n`@Vasya 18-9`\n\nОтправляй по одному игроку за сообщение, либо "
                "пропусти статистику.",
                reply_markup=kb_skip_stats(), parse_mode="Markdown",
            )
            return

        if data == "stats:skip":
            await safe_delete(query.message)
            await finalize_match(update, context, skip_stats=True)
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
                        f"✅ *Матч подтверждён администратором!*\n\n🆔 {record['match_id']}\n"
                        f"📍 Карта: {MAP_EMOJI.get(record['map_name'], '')} {record['map_name']}\n"
                        f"{'🏆 Победа' if summary['is_winner'] else '❌ Поражение'}\n"
                    )
                    if summary.get('calibrating'):
                        summary_text += f"📌 Калибровка: {p['calib']}/{CALIBRATION_GAMES}\n"
                    elif summary.get('just_finished_calibration'):
                        summary_text += f"🎉 Калибровка завершена! Стартовый ELO: {summary['new_elo']}\n"
                    else:
                        summary_text += f"📊 {summary['delta']:+d} ELO → {summary['new_elo']}\n"
                    if summary.get('mvp'):
                        summary_text += "⭐ MVP матча!\n"
                    await safe_send(context.bot, int(uid), summary_text, parse_mode="Markdown")
                
                save_players(players_data)
                await safe_delete(query.message)
                await query.message.reply_text(f"✅ Матч {record['match_id']} подтверждён и разослан игрокам.")
                return
            
            else:  # admin_no
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
                        f"❌ Результат матча {record['match_id']} отклонён администратором.\n"
                        f"Изменения ELO/статистики отменены.",
                    )
                
                save_players(players_data)
                record['status'] = 'rejected'
                pending[pending_id] = record
                save_pending(pending)
                await safe_delete(query.message)
                await query.message.reply_text(f"❌ Матч {record['match_id']} отклонён, изменения откачены.")
                return

    except Exception as e:
        logger.error(f"Ошибка в button_callback: {e}", exc_info=True)
        try:
            await update.callback_query.edit_message_text("❌ Произошла ошибка. Попробуйте снова.")
        except:
            pass

# ===== ОБРАБОТЧИК СООБЩЕНИЙ =====
STATS_LINE_RE = re.compile(r"^@?([A-Za-z0-9_]+)\s+(\d+)\s*-\s*(\d+)$")

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
        
        # Ограничение длины сообщения
        if len(text) > 4096:
            await update.message.reply_text("❌ Слишком длинное сообщение (макс 4096 символов)")
            return
        
        players = get_players_cached()

        # ===== РЕГИСТРАЦИЯ =====
        if context.user_data.get('reg_step'):
            step = context.user_data['reg_step']
            if step == 'id':
                if not validate_standoff_id(text):
                    await update.message.reply_text(
                        "❌ ID должен быть числом из 8-15 цифр!", reply_markup=kb_back_main(),
                    )
                    return
                if find_by_sid(players, text):
                    await update.message.reply_text("❌ Этот ID уже зарегистрирован!", reply_markup=kb_back_main())
                    return
                context.user_data['reg_sid'] = text
                context.user_data['reg_step'] = 'name'
                await update.message.reply_text(
                    "✅ ID принят!\n\nШаг 2 из 2:\nВведи свой *ник в Standoff 2*\n\nПример: `Vasya`",
                    reply_markup=kb_back_main(), parse_mode="Markdown",
                )
                return
            
            if step == 'name':
                if not validate_username(text):
                    await update.message.reply_text(
                        "❌ Ник должен содержать только латиницу, цифры и _, длина 3-20 символов!",
                        reply_markup=kb_back_main(),
                    )
                    return
                if find_by_tag(players, text):
                    await update.message.reply_text("❌ Этот ник уже занят!", reply_markup=kb_back_main())
                    return
                sid = context.user_data['reg_sid']
                player = new_player(sid)
                player["reg"] = 1
                player["name"] = text
                player["tag"] = text
                players[user_id] = player
                save_players(players)
                invalidate_cache()
                context.user_data['reg_step'] = None
                context.user_data['reg_sid'] = None
                await update.message.reply_text(
                    f"✅ *РЕГИСТРАЦИЯ ЗАВЕРШЕНА!*\n\n👤 @{text}\n🆔 `{sid}`\n"
                    f"📊 Начало калибровки (0/{CALIBRATION_GAMES})\n\nДобро пожаловать в Stranger Faceit! 🎉",
                    reply_markup=kb_main_menu(), parse_mode="Markdown",
                )
                return

        # ===== ПРИГЛАШЕНИЕ В ПАТИ =====
        if context.user_data.get('party_invite_mode'):
            context.user_data['party_invite_mode'] = False
            target_uid = find_by_tag(players, text)
            inviter = get_player(players, user.id)
            if not target_uid:
                await update.message.reply_text(
                    "❌ Игрок с таким юзернеймом не найден среди зарегистрированных в боте.",
                    reply_markup=kb_back_main(),
                )
                return
            if int(target_uid) == user.id:
                await update.message.reply_text("❌ Нельзя пригласить самого себя.", reply_markup=kb_back_main())
                return

            parties = load_parties()
            leader_id, party = find_party_of(parties, user.id)
            if not party or int(leader_id) != user.id:
                await update.message.reply_text("❌ Ты не лидер пати.", reply_markup=kb_back_main())
                return
            if int(target_uid) in party["members"]:
                await update.message.reply_text("❌ Этот игрок уже в твоей пати.", reply_markup=kb_back_main())
                return
            target_leader, target_party = find_party_of(parties, int(target_uid))
            if target_party:
                await update.message.reply_text("❌ Этот игрок уже состоит в другой пати.", reply_markup=kb_back_main())
                return
            if len(party["members"]) >= MAX_PARTY_SIZE:
                await update.message.reply_text("❌ Пати уже заполнена.", reply_markup=kb_back_main())
                return

            party["pending_invite"] = {"target": int(target_uid), "invited_at": time.time()}
            parties[leader_id] = party
            save_parties(parties)

            await update.message.reply_text(
                f"✅ Приглашение отправлено игроку @{text}.", reply_markup=kb_back_main(),
            )
            sent = await safe_send(
                context.bot, int(target_uid),
                f"🎉 Игрок @{inviter['tag']} отправляет приглашение в пати.",
                reply_markup=kb_party_invite_response(leader_id),
            )
            if sent is None:
                await update.message.reply_text(
                    "⚠️ Не удалось доставить приглашение (у игрока закрыты ЛС с ботом)."
                )
            return

        # ===== ПОДДЕРЖКА =====
        if context.user_data.get('support_mode'):
            player = get_player(players, user.id)
            tag = player.get("tag", user_id) if player else user_id
            target_chat = ADMIN_CHAT_ID or None
            msg = f"🆘 *Запрос в поддержку*\n\n👤 @{tag} (ID: {user_id})\n📝 Сообщение:\n{text}"
            if target_chat:
                await safe_send(context.bot, target_chat, msg, parse_mode="Markdown")
            else:
                for admin_id in ADMIN_IDS:
                    await safe_send(context.bot, admin_id, msg, parse_mode="Markdown")
            context.user_data['support_mode'] = False
            await update.message.reply_text("✅ Запрос отправлен администраторам!", reply_markup=kb_main_menu())
            return

        # ===== СТАТИСТИКА МАТЧА =====
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
    
    m = STATS_LINE_RE.match(text)
    if not m:
        await update.message.reply_text(
            "❌ Неверный формат. Используй: `@ник убийства-смерти`\nНапример: `@Vasya 18-9`",
            reply_markup=kb_skip_stats(), parse_mode="Markdown",
        )
        return
    
    tag, kills_str, deaths_str = m.groups()
    players = get_players_cached()
    uid = find_by_tag(players, tag)
    if not uid or int(uid) not in match.get('players', []):
        await update.message.reply_text(f"❌ Игрок @{tag} не найден в этом матче.", reply_markup=kb_skip_stats())
        return
    
    buffer = context.user_data.setdefault('stats_buffer', {})
    buffer[uid] = {"kills": int(kills_str), "deaths": int(deaths_str)}
    remaining = [str(p) for p in match['players'] if str(p) not in buffer]
    if remaining:
        await update.message.reply_text(
            f"✅ Записано: @{tag} {kills_str}-{deaths_str}\n\nОсталось игроков: {len(remaining)}\n"
            f"Продолжай вводить статистику или пропусти оставшихся.",
            reply_markup=kb_skip_stats(),
        )
        return
    await finalize_match(update, context, skip_stats=False)

# ===== ЗАВЕРШЕНИЕ МАТЧА =====
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

    for uid in [str(u) for u in winning_team]:
        p = players.get(uid)
        if not p:
            continue
        s = stats_buffer.get(uid, {"kills": 0, "deaths": 0})
        is_mvp = (uid == mvp_uid)
        result = apply_match_result(p, True, s['kills'], s['deaths'], is_mvp)
        apply_map_result(p, map_name, True)
        player_results[uid] = {**result, "is_winner": True, "mvp": is_mvp}
        winners_card.append({
            "tag": p['tag'], "kd": f"{s['kills']}/{s['deaths']}", "mvp": is_mvp,
            "calibrating": result['calibrating'], "delta": result['delta'],
            "elo": result['new_elo'] or p['elo'],
        })

    for uid in [str(u) for u in losing_team]:
        p = players.get(uid)
        if not p:
            continue
        s = stats_buffer.get(uid, {"kills": 0, "deaths": 0})
        result = apply_match_result(p, False, s['kills'], s['deaths'], False)
        apply_map_result(p, map_name, False)
        player_results[uid] = {**result, "is_winner": False, "mvp": False}
        losers_card.append({
            "tag": p['tag'], "kd": f"{s['kills']}/{s['deaths']}",
            "calibrating": result['calibrating'], "delta": result['delta'],
            "elo": result['new_elo'] or p['elo'],
        })

    save_players(players)
    invalidate_cache()
    
    match_photo = context.user_data.get('match_photo')
    pending = load_pending()
    pending_id = match_id
    pending[pending_id] = {
        "match_id": match_id, "map_name": map_name, "player_results": player_results,
        "status": "awaiting_review", "match_photo": match_photo,
    }
    save_pending(pending)

    card_report = {
        "match_id": match_id, "map_name": map_name, "score": None,
        "winners": winners_card, "losers": losers_card, "confirmed_by": None,
    }
    card_image = render_match_card(card_report)
    target_chat = ADMIN_CHAT_ID or (ADMIN_IDS[0] if ADMIN_IDS else None)
    if target_chat:
        if match_photo:
            await safe_send(context.bot, target_chat, f"📸 Скриншот результата матча {match_id}", photo=match_photo)
        await safe_send(
            context.bot, target_chat, f"📋 *Матч на проверку*\n🆔 {match_id}",
            photo=card_image, parse_mode="Markdown", reply_markup=kb_admin_review(pending_id),
        )

    context.user_data['stats_mode'] = False
    context.user_data['stats_buffer'] = {}
    context.user_data['match'] = None
    context.user_data['match_id'] = None
    context.user_data['veto'] = None
    context.user_data['match_photo'] = None
    await update.effective_message.reply_text(
        "✅ Результат отправлен администраторам на проверку.\n"
        "Как только матч будет подтверждён, ты получишь уведомление в ЛС."
    )

# ===== ЗАПУСК МАТЧА =====
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
        logger.warning("Не удалось разбить группы на равные команды без разрыва пати — использую запасной вариант.")
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

    def pick_captain(team):
        for uid in team:
            leader_id, party = find_party_of(parties, uid)
            if party and int(leader_id) == uid and len(party["members"]) > 1:
                return uid
        return random.choice(team)

    captain_a = pick_captain(team_a)
    captain_b = pick_captain(team_b)

    match_id = gen_match_id()
    players = get_players_cached()
    match = {
        "match_id": match_id, "platform": platform, "players": players_list,
        "team_a": team_a, "team_b": team_b, "captain_a": captain_a, "captain_b": captain_b,
        "map": None, "status": "veto", "winner_team": None,
        "created_at": datetime.now().isoformat(),
    }

    veto = start_veto(str(captain_a), str(captain_b))

    for uid in players_list:
        udata = context.application.user_data[uid]
        udata['match'] = match
        udata['match_id'] = match_id
        udata['veto'] = veto

        team_label = "🔵 Команда А" if uid in team_a else "🔴 Команда Б"
        await safe_send(
            context.bot, uid,
            f"🎮 *Матч найден!*\n\n🆔 {match_id}\n📱 {platform}\n👥 Собрано 10 игроков!\n"
            f"Твоя команда: {team_label}\n\nНачинается бан карт...",
            parse_mode="Markdown",
        )

    first_player = players.get(str(captain_a), {})
    tag = first_player.get("tag", str(captain_a))
    available = veto["pool"]
    await safe_send(
        context.bot, captain_a,
        f"🗺️ *ВЕТО*\n\nХод: @{tag}\nДоступные карты:\n" +
        "\n".join([f"• {MAP_EMOJI.get(m, '')} {m}" for m in available]),
        parse_mode="Markdown", reply_markup=kb_veto(available),
    )

# ===== HEALTH CHECK =====
app_flask = Flask(__name__)
start_time = time.time()

@app_flask.route('/health')
def health():
    try:
        players = load_players()
        pending = load_pending()
        return jsonify({
            'status': 'ok',
            'timestamp': datetime.now().isoformat(),
            'players': len(players),
            'pending': len(pending),
            'uptime_seconds': int(time.time() - start_time)
        })
    except Exception as e:
        return jsonify({'status': 'error', 'error': str(e)}), 500

def run_health_server():
    try:
        app_flask.run(host='0.0.0.0', port=8080, debug=False, use_reloader=False)
    except Exception as e:
        logger.error(f"Health check сервер не запущен: {e}")

# ===== ЗАПУСК =====
def main():
    # Создаем папки
    os.makedirs("backups", exist_ok=True)
    os.makedirs("logs", exist_ok=True)
    
    # Запускаем health check в отдельном потоке
    health_thread = threading.Thread(target=run_health_server, daemon=True)
    health_thread.start()
    logger.info("Health check сервер запущен на порту 8080")
    
    # Настройка бота
    request = HTTPXRequest(
        connect_timeout=CONNECT_TIMEOUT,
        read_timeout=READ_TIMEOUT,
        write_timeout=WRITE_TIMEOUT,
        pool_timeout=POOL_TIMEOUT,
    )
    
    app = ApplicationBuilder().token(BOT_TOKEN).request(request).build()
    
    # Обработчики команд
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("winner", winner_command))
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    # Глобальный обработчик ошибок
    async def error_handler(update, context):
        logger.error(f"Update {update} вызвал ошибку: {context.error}", exc_info=True)
    app.add_error_handler(error_handler)
    
    logger.info("="*50)
    logger.info("🤖 Stranger Faceit запущен!")
    logger.info(f"👑 Админы: {ADMIN_IDS}")
    logger.info(f"🏠 Общий чат: {GENERAL_CHAT_ID}")
    logger.info(f"🔒 Админ-чат: {ADMIN_CHAT_ID}")
    logger.info(f"📁 Данные: {DATA_FILE}, {PENDING_FILE}, {LOBBIES_FILE}, {PARTIES_FILE}")
    logger.info("="*50)
    
    # Запуск бота
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()

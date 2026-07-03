#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Stranger Faceit — Telegram бот для матчмейкинга в Standoff 2.
Для запуска на Render.com
"""

import asyncio
import json
import logging
import os
import random
import re
import string
import threading
from datetime import datetime
from io import BytesIO
from typing import Optional

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import TelegramError, Forbidden, BadRequest
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, MessageHandler, ContextTypes, filters
from telegram.request import HTTPXRequest
from PIL import Image, ImageDraw, ImageFont

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger("stranger_faceit")

# ===== КОНФИГ =====
BOT_TOKEN = "8280414108:AAGkt0FPZY7PwADKXMhJlLBuZHaJXkNUh6U"
ADMIN_IDS = [8131755675]
GENERAL_CHAT_ID = -1004404404847
ADMIN_CHAT_ID = -1004398372551
CHAT_LINK = "https://t.me/+Gt7b_p6ywxc3Yjli"
REQUIRE_SUBSCRIPTION = 1
SUBSCRIPTION_CHAT_ID = -1004404404847

DATA_FILE = "players.json"
PENDING_FILE = "pending.json"
LOBBIES_FILE = "lobbies.json"

MAPS = ["Sandstone", "Rust", "Province", "Breeze", "Dune", "Zone 7", "Hanami"]
MAP_EMOJI = {"Sandstone": "🏜️", "Rust": "🏭", "Province": "🏘️", "Breeze": "🌬️", "Dune": "🏝️", "Zone 7": "☢️", "Hanami": "🌸"}
PLATFORMS = ["Phone", "PC"]
LOBBIES_PER_PLATFORM = 6
LOBBY_SIZE = 10
TEAM_SIZE = 5
CALIBRATION_GAMES = 10
CALIBRATION_BASE_ELO = 500

LEVEL_THRESHOLDS = [(1, 0, 500), (2, 501, 750), (3, 751, 900), (4, 901, 1050), (5, 1051, 1200), (6, 1201, 1350), (7, 1351, 1530), (8, 1531, 1750), (9, 1751, 2000), (10, 2001, 10**9)]
RANK_EMOJI = {1: "🥉", 2: "🥉", 3: "🥉", 4: "🥈", 5: "🥈", 6: "🥈", 7: "🥇", 8: "🥇", 9: "💎", 10: "👑"}

CONNECT_TIMEOUT = 120
READ_TIMEOUT = 120
WRITE_TIMEOUT = 120
POOL_TIMEOUT = 120

# ===== ХРАНИЛИЩЕ =====
_LOCK = threading.Lock()

def _load_json(path, default):
    if not os.path.exists(path): return default
    try:
        with open(path, "r", encoding="utf-8") as f: return json.load(f)
    except: return default

def _save_json(path, data):
    with _LOCK:
        tmp_path = path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f: json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, path)

def load_players(): return _load_json(DATA_FILE, {})
def save_players(p): _save_json(DATA_FILE, p)
def load_pending(): return _load_json(PENDING_FILE, {})
def save_pending(p): _save_json(PENDING_FILE, p)
def load_lobbies():
    default = {p: [[] for _ in range(LOBBIES_PER_PLATFORM)] for p in PLATFORMS}
    data = _load_json(LOBBIES_FILE, default)
    for p in PLATFORMS:
        if p not in data or not isinstance(data[p], list) or len(data[p]) != LOBBIES_PER_PLATFORM:
            data[p] = [[] for _ in range(LOBBIES_PER_PLATFORM)]
    return data
def save_lobbies(l): _save_json(LOBBIES_FILE, l)

# ===== ИГРОКИ =====
def new_player(sid):
    return {"reg": 0, "sid": sid, "name": "", "tag": "", "elo": 0, "level": 0, "wins": 0, "losses": 0, "matches": 0, "mvps": 0, "rank": "🎯", "ban": None, "history": [], "maps": {m: {"wins": 0, "losses": 0} for m in MAPS}, "calib": 0, "calib_elo_buffer": 0, "platform": None}

def get_player(players, user_id): return players.get(str(user_id))
def find_by_sid(players, sid):
    for uid, p in players.items():
        if p.get("sid") == sid: return uid
    return None
def find_by_tag(players, tag):
    tag_clean = tag.lstrip("@").lower()
    for uid, p in players.items():
        if p.get("tag", "").lower() == tag_clean: return uid
    return None

def level_from_elo(elo):
    for lvl, lo, hi in LEVEL_THRESHOLDS:
        if lo <= elo <= hi: return lvl
    return 10 if elo > LEVEL_THRESHOLDS[-1][2] else 1

def rank_label(level): return f"{RANK_EMOJI.get(level, '🥉')} {level}"

def compute_match_points(is_winner, kills, deaths, is_mvp):
    if is_winner: points = 9 + (kills * 0.5) - (deaths * 0.3)
    else: points = -15 + (kills * 0.5) - (deaths * 0.3)
    if is_mvp: points += 3
    return round(points)

def apply_match_result(player, is_winner, kills, deaths, is_mvp):
    points = compute_match_points(is_winner, kills, deaths, is_mvp)
    snapshot_before = {"matches": player["matches"], "wins": player["wins"], "losses": player["losses"], "mvps": player["mvps"], "calib": player["calib"], "calib_elo_buffer": player["calib_elo_buffer"], "elo": player["elo"], "level": player["level"], "rank": player["rank"]}
    player["matches"] += 1
    if is_winner: player["wins"] += 1
    else: player["losses"] += 1
    if is_mvp: player["mvps"] += 1
    if player["calib"] < CALIBRATION_GAMES:
        player["calib"] += 1
        player["calib_elo_buffer"] += points
        if player["calib"] >= CALIBRATION_GAMES:
            final_elo = max(0, CALIBRATION_BASE_ELO + player["calib_elo_buffer"])
            player["elo"] = final_elo
            player["level"] = level_from_elo(final_elo)
            player["rank"] = rank_label(player["level"])
            return {"delta": 0, "old_elo": 0, "new_elo": final_elo, "calibrating": False, "just_finished_calibration": True, "calib_progress": None, "_snapshot_before": snapshot_before}
        else:
            return {"delta": 0, "old_elo": 0, "new_elo": 0, "calibrating": True, "just_finished_calibration": False, "calib_progress": f"{player['calib']}/{CALIBRATION_GAMES}", "_snapshot_before": snapshot_before}
    else:
        old_elo = player["elo"]
        new_elo = max(0, old_elo + points)
        player["elo"] = new_elo
        player["level"] = level_from_elo(new_elo)
        player["rank"] = rank_label(player["level"])
        return {"delta": points, "old_elo": old_elo, "new_elo": new_elo, "calibrating": False, "just_finished_calibration": False, "calib_progress": None, "_snapshot_before": snapshot_before}

def rollback_match_result(player, snapshot_before):
    for key, value in snapshot_before.items(): player[key] = value

def apply_map_result(player, map_name, is_winner):
    if map_name not in player.get("maps", {}): player.setdefault("maps", {})[map_name] = {"wins": 0, "losses": 0}
    if is_winner: player["maps"][map_name]["wins"] += 1
    else: player["maps"][map_name]["losses"] += 1

def elo_display(player):
    if player["calib"] < CALIBRATION_GAMES: return f"Калибровка {player['calib']}/{CALIBRATION_GAMES}"
    return f"{player['rank']} • {player['elo']} ELO"

def gen_match_id():
    date_part = datetime.now().strftime("%Y%m%d")
    rand_part = "".join(random.choices(string.digits, k=3))
    return f"M-{date_part}-{rand_part}"

# ===== ВЕТО =====
def start_veto(captain_a_id, captain_b_id):
    pool = MAPS.copy()
    random.shuffle(pool)
    return {"pool": pool, "banned": [], "turn": captain_a_id, "captain_a": captain_a_id, "captain_b": captain_b_id, "final_map": None}

def veto_ban(veto, captain_id, map_name):
    if veto["final_map"] is not None: return False, "Вето уже завершено."
    if captain_id != veto["turn"]: return False, "Сейчас не ваша очередь банить."
    if map_name not in veto["pool"]: return False, "Эта карта уже забанена или не существует."
    veto["pool"].remove(map_name)
    veto["banned"].append({"by": captain_id, "map": map_name})
    if len(veto["pool"]) == 1: veto["final_map"] = veto["pool"][0]
    else: veto["turn"] = veto["captain_b"] if veto["turn"] == veto["captain_a"] else veto["captain_a"]
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
    if cache_key in _FONT_CACHE: return _FONT_CACHE[cache_key]
    candidates = ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/system/fonts/Roboto-Bold.ttf" if bold else "/system/fonts/Roboto-Regular.ttf", "/system/fonts/NotoSans-Bold.ttf" if bold else "/system/fonts/NotoSans-Regular.ttf", os.path.expanduser("~/fonts/DejaVuSans-Bold.ttf") if bold else os.path.expanduser("~/fonts/DejaVuSans.ttf")]
    for path in candidates:
        if path and os.path.exists(path):
            try:
                font = ImageFont.truetype(path, size)
                _FONT_CACHE[cache_key] = font
                return font
            except OSError: continue
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
        if pl.get("calibrating"): sub = f"{pl['kd']} • Калибровка"
        else: sub = f"{pl['kd']} • {pl['delta']:+d} ELO → {pl['elo']}"
        row_h = 64
        draw.rounded_rectangle([left_x, y, left_x+col_w, y+row_h], radius=10, fill=CARD_PANEL)
        draw.rectangle([left_x, y, left_x+5, y+row_h], fill=WIN_COLOR)
        draw.text((left_x+20, y+10), name, font=_font(22, bold=True), fill=TEXT_MAIN)
        draw.text((left_x+20, y+36), sub, font=_font(16), fill=TEXT_DIM)
        y += row_h + 10
    y = top_y
    for pl in match_report["losers"]:
        name = f"@{pl['tag']}"
        if pl.get("calibrating"): sub = f"{pl['kd']} • Калибровка"
        else: sub = f"{pl['kd']} • {pl['delta']:+d} ELO → {pl['elo']}"
        row_h = 64
        draw.rounded_rectangle([right_x, y, right_x+col_w, y+row_h], radius=10, fill=CARD_PANEL)
        draw.rectangle([right_x, y, right_x+5, y+row_h], fill=LOSE_COLOR)
        draw.text((right_x+20, y+10), name, font=_font(22, bold=True), fill=TEXT_MAIN)
        draw.text((right_x+20, y+36), sub, font=_font(16), fill=TEXT_DIM)
        y += row_h + 10
    footer_y = CARD_H - 50
    if match_report.get("confirmed_by"): footer = f"✅ Проверил: @{match_report['confirmed_by']}"
    else: footer = "Stranger Faceit"
    draw.text((CARD_W // 2, footer_y), footer, font=_font(18), fill=TEXT_DIM, anchor="ma")
    buf = BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    buf.name = "match_card.png"
    return buf

# ===== КЛАВИАТУРЫ =====
def kb_register(): return InlineKeyboardMarkup([[InlineKeyboardButton("📝 Регистрация", callback_data="reg:start")]])
def kb_subscribe():
    rows = []
    if CHAT_LINK: rows.append([InlineKeyboardButton("➡️ Перейти в чат", url=CHAT_LINK)])
    rows.append([InlineKeyboardButton("✅ Я подписался", callback_data="sub:check")])
    return InlineKeyboardMarkup(rows)
def kb_main_menu():
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔍 Найти матч", callback_data="menu:find")], [InlineKeyboardButton("👤 Профиль", callback_data="menu:profile")], [InlineKeyboardButton("🏆 Топ игроков", callback_data="menu:top")], [InlineKeyboardButton("🆘 Поддержка", callback_data="menu:support")]])
def kb_back_main(): return InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data="menu:main")]])
def kb_platforms(): return InlineKeyboardMarkup([[InlineKeyboardButton("📱 Phone", callback_data="platform:Phone")], [InlineKeyboardButton("💻 PC", callback_data="platform:PC")], [InlineKeyboardButton("⬅️ Назад", callback_data="menu:main")]])
def kb_lobbies(platform, lobbies):
    rows = []
    for i in range(LOBBIES_PER_PLATFORM):
        count = len(lobbies[platform][i])
        rows.append([InlineKeyboardButton(f"Лобби {i + 1} ({count}/{LOBBY_SIZE})", callback_data=f"lobby:{platform}:{i}")])
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="menu:find")])
    return InlineKeyboardMarkup(rows)
def kb_in_lobby(platform, idx): return InlineKeyboardMarkup([[InlineKeyboardButton("🚪 Выйти", callback_data=f"lobby_leave:{platform}:{idx}")]])
def kb_veto(available_maps):
    rows = []
    row = []
    for m in available_maps:
        emoji = MAP_EMOJI.get(m, "")
        row.append(InlineKeyboardButton(f"{emoji} {m}", callback_data=f"veto_ban:{m}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row: rows.append(row)
    return InlineKeyboardMarkup(rows)
def kb_skip_stats(): return InlineKeyboardMarkup([[InlineKeyboardButton("⏭ Пропустить статистику", callback_data="stats:skip")]])
def kb_admin_review(pending_id): return InlineKeyboardMarkup([[InlineKeyboardButton("✅ ПОДТВЕРДИТЬ", callback_data=f"admin_ok:{pending_id}")], [InlineKeyboardButton("❌ ОТКАЗАТЬ", callback_data=f"admin_no:{pending_id}")]])

# ===== ВСПОМОГАТЕЛЬНОЕ =====
async def safe_delete(message):
    if message is None: return
    try: await message.delete()
    except BadRequest: pass
    except TelegramError: pass

async def safe_send(bot, chat_id, text=None, photo=None, **kwargs):
    try:
        if photo is not None: return await bot.send_photo(chat_id=chat_id, photo=photo, caption=text, **kwargs)
        return await bot.send_message(chat_id=chat_id, text=text, **kwargs)
    except Forbidden: logger.warning("Не удалось отправить сообщение %s", chat_id)
    except TelegramError: logger.exception("Ошибка отправки")
    return None

async def is_subscribed(bot, user_id):
    if not REQUIRE_SUBSCRIPTION or not SUBSCRIPTION_CHAT_ID: return True
    try:
        member = await bot.get_chat_member(SUBSCRIPTION_CHAT_ID, user_id)
        return member.status in ("member", "administrator", "creator")
    except TelegramError: return True

def main_menu_text(player):
    return f"🏠 *ГЛАВНОЕ МЕНЮ*\n\n👤 @{player['tag']}\n📊 {elo_display(player)}\n🏆 Побед: {player['wins']} | ❌ Поражений: {player['losses']}\n🎯 Матчей: {player['matches']}"

def profile_text(player):
    winrate = round((player['wins'] / player['matches'] * 100) if player['matches'] > 0 else 0, 1)
    text = f"📊 *МОЙ ПРОФИЛЬ*\n\n👤 @{player['tag']}\n🆔 {player['sid']}\n🏅 {player['rank']}\n📊 {elo_display(player)}\n\n📈 *СТАТИСТИКА*\n─────────────────\n🎯 Матчей: {player['matches']}\n🏆 Побед: {player['wins']}\n❌ Поражений: {player['losses']}\n📊 Winrate: {winrate}%\n⭐ MVP: {player['mvps']}\n"
    if player.get('calib', 0) < CALIBRATION_GAMES: text += f"📌 Калибровка: {player['calib']}/{CALIBRATION_GAMES}\n"
    text += f"\n📊 *ПО КАРТАМ*\n─────────────────\n"
    for map_name, stats in player['maps'].items():
        total = stats['wins'] + stats['losses']
        if total > 0:
            rate = round((stats['wins'] / total * 100), 1)
            text += f"{MAP_EMOJI.get(map_name, '')} {map_name}: {stats['wins']}-{stats['losses']} ({rate}%)\n"
    return text

# ===== ОСНОВНЫЕ ОБРАБОТЧИКИ =====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    players = load_players()
    player = get_player(players, user.id)
    if REQUIRE_SUBSCRIPTION and not await is_subscribed(context.bot, user.id):
        await update.message.reply_text("📢 *Для использования бота подпишись на наш чат.*", reply_markup=kb_subscribe(), parse_mode="Markdown")
        return
    if not player or player.get("reg") != 1:
        await update.message.reply_text("🎮 *STRANGER FACEIT*\n\nДобро пожаловать! Для начала игры необходимо зарегистрироваться.\nНажми на кнопку ниже.", reply_markup=kb_register(), parse_mode="Markdown")
        return
    await update.message.reply_text(main_menu_text(player), reply_markup=kb_main_menu(), parse_mode="Markdown")

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    match = context.user_data.get('match')
    if not match or match.get('status') != 'in_progress':
        await update.message.reply_text("❌ Сейчас нет матча, ожидающего скриншот результата.")
        return
    photo_sizes = update.message.photo
    if not photo_sizes: return
    file_id = photo_sizes[-1].file_id
    context.user_data['match_photo'] = file_id
    await update.message.reply_text("✅ Скриншот результата принят!\n\nТеперь объяви победившую сторону:\n`/winner ct` или `/winner t`", parse_mode="Markdown")

async def winner_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔵 Команда А", callback_data="winteam:a")], [InlineKeyboardButton("🔴 Команда Б", callback_data="winteam:b")]])
    await update.message.reply_text(f"✅ Победила сторона: *{side.upper()}*\n\nКакая команда играла за {side.upper()} и победила?", reply_markup=kb, parse_mode="Markdown")

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = query.from_user
    user_id = str(user.id)
    data = query.data
    players = load_players()
    player = get_player(players, user.id)

    if data == "sub:check":
        if await is_subscribed(context.bot, user.id):
            await safe_delete(query.message)
            if not player or player.get("reg") != 1:
                await query.message.reply_text("✅ Подписка подтверждена!\n\n🎮 *STRANGER FACEIT*\n\nНажми кнопку для регистрации", reply_markup=kb_register(), parse_mode="Markdown")
            else:
                await query.message.reply_text(main_menu_text(player), reply_markup=kb_main_menu(), parse_mode="Markdown")
        else:
            await query.answer("❌ Подписка не найдена. Подпишись и попробуй снова.", show_alert=True)
        return

    if data == "reg:start":
        await safe_delete(query.message)
        await query.message.reply_text("📝 *РЕГИСТРАЦИЯ*\n\nШаг 1 из 2:\nВведи свой *ID в Standoff 2*\n\nПример: `1002929387`", reply_markup=kb_back_main(), parse_mode="Markdown")
        context.user_data['reg_step'] = 'id'
        return

    if data == "menu:main":
        await safe_delete(query.message)
        context.user_data.pop('reg_step', None)
        context.user_data.pop('support_mode', None)
        if not player or player.get("reg") != 1:
            await query.message.reply_text("🎮 *STRANGER FACEIT*\n\nНажми кнопку для регистрации", reply_markup=kb_register(), parse_mode="Markdown")
            return
        await query.message.reply_text(main_menu_text(player), reply_markup=kb_main_menu(), parse_mode="Markdown")
        return

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
        await safe_delete(query.message)
        await query.message.reply_text(f"📱 *{platform.upper()} ЛОББИ*", reply_markup=kb_lobbies(platform, lobbies), parse_mode="Markdown")
        return

    if data.startswith("lobby:"):
        _, platform, idx_str = data.split(":")
        idx = int(idx_str)
        lobbies = load_lobbies()
        if len(lobbies[platform][idx]) >= LOBBY_SIZE:
            await query.answer("❌ Лобби заполнено!", show_alert=True)
            return
        for p in PLATFORMS:
            for i, lobby in enumerate(lobbies[p]):
                if user.id in lobby:
                    lobbies[p][i].remove(user.id)
        lobbies[platform][idx].append(user.id)
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
        await query.message.reply_text(text, reply_markup=kb_in_lobby(platform, idx), parse_mode="Markdown")
        if len(lobbies[platform][idx]) >= LOBBY_SIZE:
            await start_match(platform, idx, context)
        return

    if data.startswith("lobby_leave:"):
        _, platform, idx_str = data.split(":")
        idx = int(idx_str)
        lobbies = load_lobbies()
        if user.id in lobbies[platform][idx]:
            lobbies[platform][idx].remove(user.id)
            save_lobbies(lobbies)
        await safe_delete(query.message)
        await query.message.reply_text("🚪 Вышел из лобби", reply_markup=kb_platforms())
        return

    if data == "menu:profile":
        await safe_delete(query.message)
        if not player or player.get("reg") != 1:
            await query.message.reply_text("Сначала зарегистрируйся!", reply_markup=kb_register())
            return
        await query.message.reply_text(profile_text(player), reply_markup=kb_back_main(), parse_mode="Markdown")
        return

    if data == "menu:top":
        await safe_delete(query.message)
        sorted_players = sorted([p for p in players.values() if p.get("reg") == 1 and p.get("calib", 0) >= CALIBRATION_GAMES], key=lambda x: x.get("elo", 0), reverse=True)[:10]
        text = "🏆 *ТОП ИГРОКОВ*\n\n"
        if not sorted_players: text += "Пока нет игроков, завершивших калибровку.\n"
        for i, p in enumerate(sorted_players, 1):
            medal = {1: "👑", 2: "🥈", 3: "🥉"}.get(i, f"{i}.")
            text += f"{medal} @{p['tag']}\n    📊 {p['elo']} ELO | {p['rank']}\n"
        total = len([p for p in players.values() if p.get("reg") == 1])
        text += f"\n📊 Всего игроков: {total}"
        await query.message.reply_text(text, reply_markup=kb_back_main(), parse_mode="Markdown")
        return

    if data == "menu:support":
        await safe_delete(query.message)
        context.user_data['support_mode'] = True
        await query.message.reply_text("🆘 *ПОДДЕРЖКА*\n\nОпиши свою проблему одним сообщением.\nАдминистраторы получат уведомление.", reply_markup=kb_back_main(), parse_mode="Markdown")
        return

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
            final_text = f"🏆 *Матч сформирован!*\n\n🆔 {match_id}\n📍 Финальная карта: {MAP_EMOJI.get(veto['final_map'], '')} {veto['final_map']}\n\n📌 После матча капитан отправляет команду:\n`/winner ct` или `/winner t`\nа затем статистику игроков."
            for uid in match.get('players', []):
                await safe_send(context.bot, uid, final_text, parse_mode="Markdown")
            return
        next_player = players.get(veto["turn"], {})
        tag = next_player.get("tag", veto["turn"])
        available = veto["pool"]
        await query.message.reply_text(f"🗺️ *ВЕТО*\n\nХод: @{tag}\nДоступные карты:\n" + "\n".join([f"• {MAP_EMOJI.get(m, '')} {m}" for m in available]), reply_markup=kb_veto(available), parse_mode="Markdown")
        return

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
        await query.message.reply_text("Теперь введи статистику каждого игрока в формате:\n`@ник убийства-смерти`\n\nНапример:\n`@Vasya 18-9`\n\nОтправляй по одному игроку за сообщение, либо пропусти статистику.", reply_markup=kb_skip_stats(), parse_mode="Markdown")
        return

    if data == "stats:skip":
        await safe_delete(query.message)
        await finalize_match(update, context, skip_stats=True)
        return

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
        players = load_players()
        if action == "admin_ok":
            record['status'] = 'confirmed'
            record['confirmed_by'] = players.get(str(user.id), {}).get('tag', str(user.id))
            pending[pending_id] = record
            save_pending(pending)
            for uid, summary in record['player_results'].items():
                p = players.get(uid)
                if not p: continue
                summary_text = f"✅ *Матч подтверждён администратором!*\n\n🆔 {record['match_id']}\n📍 Карта: {MAP_EMOJI.get(record['map_name'], '')} {record['map_name']}\n{'🏆 Победа' if summary['is_winner'] else '❌ Поражение'}\n"
                if summary.get('calibrating'):
                    summary_text += f"📌 Калибровка: {p['calib']}/{CALIBRATION_GAMES}\n"
                elif summary.get('just_finished_calibration'):
                    summary_text += f"🎉 Калибровка завершена! Стартовый ELO: {summary['new_elo']}\n"
                else:
                    summary_text += f"📊 {summary['delta']:+d} ELO → {summary['new_elo']}\n"
                if summary.get('mvp'): summary_text += "⭐ MVP матча!\n"
                await safe_send(context.bot, int(uid), summary_text, parse_mode="Markdown")
            save_players(players)
            await safe_delete(query.message)
            await query.message.reply_text(f"✅ Матч {record['match_id']} подтверждён и разослан игрокам.")
            return
        else:
            for uid, summary in record['player_results'].items():
                p = players.get(uid)
                if not p: continue
                snapshot = summary.get('_snapshot_before')
                if snapshot: rollback_match_result(p, snapshot)
                map_name = record['map_name']
                if map_name in p.get('maps', {}):
                    if summary['is_winner']:
                        p['maps'][map_name]['wins'] = max(0, p['maps'][map_name]['wins'] - 1)
                    else:
                        p['maps'][map_name]['losses'] = max(0, p['maps'][map_name]['losses'] - 1)
                await safe_send(context.bot, int(uid), f"❌ Результат матча {record['match_id']} отклонён администратором.\nИзменения ELO/статистики отменены.")
            save_players(players)
            record['status'] = 'rejected'
            pending[pending_id] = record
            save_pending(pending)
            await safe_delete(query.message)
            await query.message.reply_text(f"❌ Матч {record['match_id']} отклонён, изменения откачены.")
            return

# ===== ОБРАБОТЧИК СООБЩЕНИЙ =====
STATS_LINE_RE = re.compile(r"^@?([A-Za-z0-9_]+)\s+(\d+)\s*-\s*(\d+)$")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = str(user.id)
    text = update.message.text.strip()
    players = load_players()

    if context.user_data.get('reg_step'):
        step = context.user_data['reg_step']
        if step == 'id':
            if not text.isdigit() or len(text) < 9 or len(text) > 10:
                await update.message.reply_text("❌ ID должен быть числом из 9-10 цифр!", reply_markup=kb_back_main())
                return
            if find_by_sid(players, text):
                await update.message.reply_text("❌ Этот ID уже зарегистрирован!", reply_markup=kb_back_main())
                return
            context.user_data['reg_sid'] = text
            context.user_data['reg_step'] = 'name'
            await update.message.reply_text("✅ ID принят!\n\nШаг 2 из 2:\nВведи свой *ник в Standoff 2*\n\nПример: `Vasya`", reply_markup=kb_back_main(), parse_mode="Markdown")
            return
        if step == 'name':
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
            context.user_data['reg_step'] = None
            context.user_data['reg_sid'] = None
            await update.message.reply_text(f"✅ *РЕГИСТРАЦИЯ ЗАВЕРШЕНА!*\n\n👤 @{text}\n🆔 `{sid}`\n📊 Начало калибровки (0/{CALIBRATION_GAMES})\n\nДобро пожаловать в Stranger Faceit! 🎉", reply_markup=kb_main_menu(), parse_mode="Markdown")
            return

    if context.user_data.get('support_mode'):
        player = get_player(players, user.id)
        tag = player.get("tag", user_id) if player else user_id
        target_chat = ADMIN_CHAT_ID or None
        if target_chat:
            await safe_send(context.bot, target_chat, f"🆘 *Запрос в поддержку*\n\n👤 @{tag} (ID: {user_id})\n📝 Сообщение:\n{text}", parse_mode="Markdown")
        else:
            for admin_id in ADMIN_IDS:
                await safe_send(context.bot, admin_id, f"🆘 *Запрос в поддержку*\n\n👤 @{tag} (ID: {user_id})\n📝 Сообщение:\n{text}", parse_mode="Markdown")
        context.user_data['support_mode'] = False
        await update.message.reply_text("✅ Запрос отправлен администраторам!", reply_markup=kb_main_menu())
        return

    if context.user_data.get('stats_mode'):
        await handle_stats_input(update, context, text)
        return

async def handle_stats_input(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str):
    match = context.user_data.get('match')
    if not match:
        context.user_data['stats_mode'] = False
        await update.message.reply_text("❌ Нет активного матча.")
        return
    m = STATS_LINE_RE.match(text)
    if not m:
        await update.message.reply_text("❌ Неверный формат. Используй: `@ник убийства-смерти`\nНапример: `@Vasya 18-9`", reply_markup=kb_skip_stats(), parse_mode="Markdown")
        return
    tag, kills_str, deaths_str = m.groups()
    players = load_players()
    uid = find_by_tag(players, tag)
    if not uid or int(uid) not in match.get('players', []):
        await update.message.reply_text(f"❌ Игрок @{tag} не найден в этом матче.", reply_markup=kb_skip_stats())
        return
    buffer = context.user_data.setdefault('stats_buffer', {})
    buffer[uid] = {"kills": int(kills_str), "deaths": int(deaths_str)}
    remaining = [str(p) for p in match['players'] if str(p) not in buffer]
    if remaining:
        await update.message.reply_text(f"✅ Записано: @{tag} {kills_str}-{deaths_str}\n\nОсталось игроков: {len(remaining)}\nПродолжай вводить статистику или пропусти оставшихся.", reply_markup=kb_skip_stats())
        return
    await finalize_match(update, context, skip_stats=False)

# ===== ЗАВЕРШЕНИЕ МАТЧА =====
async def finalize_match(update: Update, context: ContextTypes.DEFAULT_TYPE, skip_stats: bool):
    match = context.user_data.get('match')
    if not match: return
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
        if not p: continue
        s = stats_buffer.get(uid, {"kills": 0, "deaths": 0})
        is_mvp = (uid == mvp_uid)
        result = apply_match_result(p, True, s['kills'], s['deaths'], is_mvp)
        apply_map_result(p, map_name, True)
        player_results[uid] = {**result, "is_winner": True, "mvp": is_mvp}
        winners_card.append({"tag": p['tag'], "kd": f"{s['kills']}/{s['deaths']}", "mvp": is_mvp, "calibrating": result['calibrating'], "delta": result['delta'], "elo": result['new_elo'] or p['elo']})
    for uid in [str(u) for u in losing_team]:
        p = players.get(uid)
        if not p: continue
        s = stats_buffer.get(uid, {"kills": 0, "deaths": 0})
        result = apply_match_result(p, False, s['kills'], s['deaths'], False)
        apply_map_result(p, map_name, False)
        player_results[uid] = {**result, "is_winner": False, "mvp": False}
        losers_card.append({"tag": p['tag'], "kd": f"{s['kills']}/{s['deaths']}", "calibrating": result['calibrating'], "delta": result['delta'], "elo": result['new_elo'] or p['elo']})
    save_players(players)
    match_photo = context.user_data.get('match_photo')
    pending = load_pending()
    pending_id = match_id
    pending[pending_id] = {"match_id": match_id, "map_name": map_name, "player_results": player_results, "status": "awaiting_review", "match_photo": match_photo}
    save_pending(pending)
    card_report = {"match_id": match_id, "map_name": map_name, "score": None, "winners": winners_card, "losers": losers_card, "confirmed_by": None}
    card_image = render_match_card(card_report)
    target_chat = ADMIN_CHAT_ID or (ADMIN_IDS[0] if ADMIN_IDS else None)
    if target_chat:
        if match_photo:
            await safe_send(context.bot, target_chat, f"📸 Скриншот результата матча {match_id}", photo=match_photo)
        await safe_send(context.bot, target_chat, f"📋 *Матч на проверку*\n🆔 {match_id}", photo=card_image, parse_mode="Markdown", reply_markup=kb_admin_review(pending_id))
    context.user_data['stats_mode'] = False
    context.user_data['stats_buffer'] = {}
    context.user_data['match'] = None
    context.user_data['match_id'] = None
    context.user_data['veto'] = None
    context.user_data['match_photo'] = None
    await update.effective_message.reply_text("✅ Результат отправлен администраторам на проверку.\nКак только матч будет подтверждён, ты получишь уведомление в ЛС.")

# ===== ЗАПУСК МАТЧА =====
async def start_match(platform: str, lobby_idx: int, context: ContextTypes.DEFAULT_TYPE):
    lobbies = load_lobbies()
    players_list = lobbies[platform][lobby_idx].copy()
    lobbies[platform][lobby_idx] = []
    save_lobbies(lobbies)
    random.shuffle(players_list)
    team_a = players_list[:TEAM_SIZE]
    team_b = players_list[TEAM_SIZE:]
    captain_a = team_a[0]
    captain_b = team_b[0]
    match_id = gen_match_id()
    players = load_players()
    match = {"match_id": match_id, "platform": platform, "players": players_list, "team_a": team_a, "team_b": team_b, "captain_a": captain_a, "captain_b": captain_b, "map": None, "status": "veto", "winner_team": None, "created_at": datetime.now().isoformat()}
    context.user_data['match'] = match
    context.user_data['match_id'] = match_id
    veto = start_veto(str(captain_a), str(captain_b))
    context.user_data['veto'] = veto
    for uid in players_list:
        team_label = "🔵 Команда А" if uid in team_a else "🔴 Команда Б"
        await safe_send(context.bot, uid, f"🎮 *Матч найден!*\n\n🆔 {match_id}\n📱 {platform}\n👥 Собрано 10 игроков!\nТвоя команда: {team_label}\n\nНачинается бан карт...", parse_mode="Markdown")
    first_player = players.get(str(captain_a), {})
    tag = first_player.get("tag", str(captain_a))
    available = veto["pool"]
    await safe_send(context.bot, captain_a, f"🗺️ *ВЕТО*\n\nХод: @{tag}\nДоступные карты:\n" + "\n".join([f"• {MAP_EMOJI.get(m, '')} {m}" for m in available]), parse_mode="Markdown", reply_markup=kb_veto(available))

# ===== ЗАПУСК =====
async def main():
    request = HTTPXRequest(connect_timeout=CONNECT_TIMEOUT, read_timeout=READ_TIMEOUT, write_timeout=WRITE_TIMEOUT, pool_timeout=POOL_TIMEOUT)
    app = ApplicationBuilder().token(BOT_TOKEN).request(request).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("winner", winner_command))
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print("🤖 Stranger Faceit запущен!")
    print(f"👑 Админы: {ADMIN_IDS}")
    print(f"🏠 Общий чат: {GENERAL_CHAT_ID}")
    print(f"🔒 Админ-чат: {ADMIN_CHAT_ID}")
    await app.initialize()
    await app.start()
    await app.updater.start_polling()
    while True:
        await asyncio.sleep(1)

if __name__ == "__main__":
    asyncio.run(main())

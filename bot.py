#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Stranger Faceit v3.5 — Supabase REST (без конфликтов зависимостей)
"""
import asyncio, json, logging, os, random, re, string, sys, threading, time
from collections import defaultdict, Counter
from datetime import datetime, timezone, timedelta
from logging.handlers import RotatingFileHandler

from flask import Flask, jsonify
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import TelegramError, Forbidden, BadRequest
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, MessageHandler, ContextTypes, filters, PreCheckoutQueryHandler
from telegram.request import HTTPXRequest
from dotenv import load_dotenv
import httpx

load_dotenv()

# ===== ЛОГИ =====
os.makedirs("logs", exist_ok=True)
logger = logging.getLogger("stranger_faceit")
logger.setLevel(logging.INFO)
console = logging.StreamHandler(sys.stdout)
console.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
logger.addHandler(console)
file_handler = RotatingFileHandler("logs/bot.log", maxBytes=10*1024*1024, backupCount=5)
file_handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
logger.addHandler(file_handler)

# ===== ПЕРЕМЕННЫЕ =====
REQUIRED = ["BOT_TOKEN", "ADMIN_IDS", "GENERAL_CHAT_ID", "ADMIN_CHAT_ID", "SUPABASE_URL", "SUPABASE_KEY"]
if missing := [v for v in REQUIRED if not os.environ.get(v)]:
    logger.error(f"❌ Отсутствуют: {', '.join(missing)}")
    sys.exit(1)

BOT_TOKEN = os.environ["BOT_TOKEN"]
ADMIN_IDS = [int(x) for x in os.environ["ADMIN_IDS"].split(",") if x.strip()]
GENERAL_CHAT_ID = int(os.environ["GENERAL_CHAT_ID"])
ADMIN_CHAT_ID = int(os.environ["ADMIN_CHAT_ID"])
CHAT_LINK = os.environ.get("CHAT_LINK", "")
REQUIRE_SUBSCRIPTION = int(os.environ.get("REQUIRE_SUBSCRIPTION", "1"))
SUBSCRIPTION_CHAT_ID = int(os.environ.get("SUBSCRIPTION_CHAT_ID", str(GENERAL_CHAT_ID)))
OWNER_ID = int(os.environ.get("OWNER_ID", str(ADMIN_IDS[0] if ADMIN_IDS else 0)))
SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_KEY"]

# ===== SUPABASE REST =====
async def sb(method, path, data=None):
    url = f"{SUPABASE_URL}/rest/v1/{path}"
    headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}", "Content-Type": "application/json", "Prefer": "return=representation"}
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.request(method, url, headers=headers, json=data)
        return r.json() if r.status_code < 400 else None

async def load_players():
    d = await sb("GET", "players")
    return {row['user_id']: row['data'] for row in d} if d else {}
async def save_players(p):
    await sb("DELETE", "players?user_id=neq.null")
    for uid, data in p.items():
        await sb("POST", "players", {"user_id": uid, "data": data})

async def load_pending():
    d = await sb("GET", "pending")
    return {row['id']: row['data'] for row in d} if d else {}
async def save_pending(p):
    await sb("DELETE", "pending?id=neq.null")
    for pid, data in p.items():
        await sb("POST", "pending", {"id": pid, "data": data})

async def load_lobbies():
    d = await sb("GET", "lobbies")
    if d:
        return {row['platform']: row['lobby_data'] for row in d}
    return {p: [[] for _ in range(6)] for p in ["Phone", "PC"]}
async def save_lobbies(l):
    await sb("DELETE", "lobbies?platform=neq.null")
    for platform, data in l.items():
        await sb("POST", "lobbies", {"platform": platform, "lobby_data": data})

async def load_parties():
    d = await sb("GET", "parties")
    return {row['leader_id']: row['data'] for row in d} if d else {}
async def save_parties(p):
    await sb("DELETE", "parties?leader_id=neq.null")
    for lid, data in p.items():
        await sb("POST", "parties", {"leader_id": lid, "data": data})

async def load_reports():
    d = await sb("GET", "reports")
    r = {}
    if d:
        for row in d:
            r.setdefault(row['target_uid'], []).append(row['report_data'])
    return r
async def save_reports(r):
    await sb("DELETE", "reports?target_uid=neq.null")
    for target, reports in r.items():
        for report in reports:
            await sb("POST", "reports", {"target_uid": target, "report_data": report})

async def load_history():
    d = await sb("GET", "match_history?order=id.desc&limit=100")
    return {"matches": [row['data'] for row in d]} if d else {"matches": []}
async def append_history(entry):
    await sb("POST", "match_history", {"data": entry})

async def load_analytics():
    d = await sb("GET", "analytics")
    a = {"map_picks": {}, "online_samples": [], "match_timestamps": []}
    if d:
        for row in d:
            a[row['key']] = row['value']
    return a
async def save_analytics(a):
    await sb("DELETE", "analytics?key=neq.null")
    for key, value in a.items():
        await sb("POST", "analytics", {"key": key, "value": value})

# ===== КОНСТАНТЫ =====
MAPS = ["Sandstone","Rust","Province","Breeze","Dune","Zone 7","Hanami"]
MAP_EMOJI = {m: e for m,e in zip(MAPS, ["🏜️","🏭","🏘️","🌬️","🏝️","☢️","🌸"])}
PLATFORMS = ["Phone","PC"]
LOBBIES_PER_PLATFORM = 6
LOBBY_SIZE = 10
MAX_PARTY_SIZE = 5
CALIBRATION_GAMES = 10
CALIBRATION_BASE_ELO = 500
READY_CHECK_TIMEOUT = 60
RESULT_UNLOCK_DELAY = 30
MAX_REPORT_LEN = 500
PREMIUM_PRICES = {"day":50, "week":350, "month":1000}
PREMIUM_DURATIONS = {"day":86400, "week":604800, "month":2592000}
LEVEL_THRESHOLDS = [(1,0,500),(2,501,750),(3,751,900),(4,901,1050),(5,1051,1200),(6,1201,1350),(7,1351,1530),(8,1531,1750),(9,1751,2000),(10,2001,10**9)]
RANK_EMOJI = {1:"🥉",2:"🥉",3:"🥉",4:"🥈",5:"🥈",6:"🥈",7:"🥇",8:"🥇",9:"💎",10:"👑"}
MOSCOW_TZ = timezone(timedelta(hours=3))

# ===== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ =====
def is_premium(p): return p and p.get('premium_until',0) > time.time()
def get_premium_time_left(p): return max(0, int(p.get('premium_until',0) - time.time())) if p else 0
def format_premium_time(s):
    if s <= 0: return "не активен"
    return f"{s//86400} дн {(s%86400)//3600} ч" if s > 86400 else f"{s//3600} ч"
def new_player(sid, tg_username):
    return {"reg":0, "sid":sid, "name":"", "tag":"", "tg_username":tg_username, "elo":0, "level":0,
            "wins":0, "losses":0, "matches":0, "mvps":0, "rank":"🎯", "ban":None, "total_kills":0,
            "total_deaths":0, "hs_kills":0, "elo_history":[], "maps":{m:{"wins":0,"losses":0} for m in MAPS},
            "calib":0, "calib_elo_buffer":0, "platform":None, "premium_until":0, "tag_changes":0}
def get_player(players, uid): return players.get(str(uid))
def find_by_sid(players, sid):
    for uid,p in players.items():
        if p.get("sid") == sid: return uid
    return None
def find_by_tag(players, tag):
    tag = tag.lstrip("@").lower()
    for uid,p in players.items():
        if p.get("tag", "").lower() == tag: return uid
    return None
def find_by_telegram_username(players, username):
    username = username.lstrip("@").lower()
    for uid,p in players.items():
        if p.get("tg_username", "").lower() == username: return uid
    return None
def level_from_elo(elo):
    for lvl,lo,hi in LEVEL_THRESHOLDS:
        if lo <= elo <= hi: return lvl
    return 10 if elo > LEVEL_THRESHOLDS[-1][2] else 1
def rank_label(lvl): return f"{RANK_EMOJI.get(lvl,'🥉')} {lvl}"
def compute_points(is_winner,kills,deaths,is_mvp):
    return round((9 + kills*0.5 - deaths*0.3) if is_winner else (-15 + kills*0.5 - deaths*0.3) + (3 if is_mvp else 0))
def apply_match_result(player, is_winner, kills, deaths, hs, is_mvp):
    points = compute_points(is_winner,kills,deaths,is_mvp)
    if is_premium(player): points *= 2
    snapshot = {k:player[k] for k in ["matches","wins","losses","mvps","calib","calib_elo_buffer","elo","level","rank","total_kills","total_deaths","hs_kills","elo_history"]}
    player["matches"] += 1
    player["total_kills"] = player.get("total_kills",0) + kills
    player["total_deaths"] = player.get("total_deaths",0) + deaths
    player["hs_kills"] = player.get("hs_kills",0) + hs
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
            player.setdefault("elo_history",[]).append(final_elo)
            return {"delta":0,"old_elo":0,"new_elo":final_elo,"calibrating":False,"just_finished_calibration":True,"calib_progress":None,"_snapshot_before":snapshot}
        return {"delta":0,"old_elo":0,"new_elo":0,"calibrating":True,"just_finished_calibration":False,"calib_progress":f"{player['calib']}/{CALIBRATION_GAMES}","_snapshot_before":snapshot}
    old_elo = player["elo"]
    new_elo = max(0, old_elo + points)
    player["elo"] = new_elo
    player["level"] = level_from_elo(new_elo)
    player["rank"] = rank_label(player["level"])
    player.setdefault("elo_history",[]).append(new_elo)
    player["elo_history"] = player["elo_history"][-30:]
    return {"delta":points,"old_elo":old_elo,"new_elo":new_elo,"calibrating":False,"just_finished_calibration":False,"calib_progress":None,"_snapshot_before":snapshot}
def rollback_match_result(player, snapshot):
    for k,v in snapshot.items(): player[k] = v
def elo_display(p):
    if p["calib"] < CALIBRATION_GAMES: return f"Калибровка {p['calib']}/{CALIBRATION_GAMES}"
    return f"{p['rank']} {'💎' if is_premium(p) else ''}• {p['elo']} ELO"
def gen_match_id(): return f"M-{datetime.now().strftime('%Y%m%d')}-{''.join(random.choices(string.digits,k=3))}"
def start_veto(ca, cb):
    pool = MAPS.copy(); random.shuffle(pool)
    return {"pool":pool, "banned":[], "turn":ca, "captain_a":ca, "captain_b":cb, "final_map":None}
def veto_ban(veto, captain_id, map_name):
    if veto["final_map"]: return False, "Вето завершено."
    if captain_id != veto["turn"]: return False, "Не ваша очередь."
    if map_name not in veto["pool"]: return False, "Карта уже забанена."
    veto["pool"].remove(map_name)
    veto["banned"].append({"by": captain_id, "map": map_name})
    if len(veto["pool"]) == 1: veto["final_map"] = veto["pool"][0]
    else: veto["turn"] = veto["captain_b"] if veto["turn"] == veto["captain_a"] else veto["captain_a"]
    return True, None
def find_party_of(parties, uid):
    uid = int(uid)
    for lid, party in parties.items():
        if uid in party.get("members", []): return lid, party
    return None, None
def audit_log(action, user_id, details):
    try:
        with open("audit.log", "a", encoding="utf-8") as f:
            f.write(json.dumps({"timestamp":datetime.now().isoformat(),"action":action,"user_id":user_id,"details":details}, ensure_ascii=False)+"\n")
    except: pass
def sanitize_input(text): return re.sub(r'[\x00-\x1f\x7f-\x9f]','',text).strip()[:MAX_REPORT_LEN]
def validate_id(t): return t.isdigit() and 8 <= len(t) <= 15
def sparkline(vals):
    if not vals: return "нет данных"
    blocks = "▁▂▃▄▅▆▇█"
    lo, hi = min(vals), max(vals)
    if hi == lo: return blocks[3] * len(vals)
    return ''.join(blocks[int((v-lo)/(hi-lo)*(len(blocks)-1))] for v in vals)

# ===== КЛАВИАТУРЫ =====
def kb_start_auth():
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔑 Вход", callback_data="auth:login")],
                                 [InlineKeyboardButton("📝 Регистрация", callback_data="auth:register")],
                                 [InlineKeyboardButton("🆘 Поддержка", callback_data="auth:support")]])
def kb_subscribe():
    rows = []
    if CHAT_LINK: rows.append([InlineKeyboardButton("➡️ Перейти в чат", url=CHAT_LINK)])
    rows.append([InlineKeyboardButton("✅ Я подписался", callback_data="sub:check")])
    return InlineKeyboardMarkup(rows)
def kb_main_menu(in_party=False):
    party_label = "🎉 Пати" if not in_party else "🎉 Пати (моя группа)"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔍 Найти матч", callback_data="menu:find"), InlineKeyboardButton(party_label, callback_data="menu:party")],
        [InlineKeyboardButton("👤 Профиль", callback_data="menu:profile"), InlineKeyboardButton("🏆 Топ", callback_data="menu:top")],
        [InlineKeyboardButton("📊 Статистика", callback_data="menu:stats"), InlineKeyboardButton("📝 История", callback_data="menu:history")],
        [InlineKeyboardButton("📢 Жалобы", callback_data="menu:complaints"), InlineKeyboardButton("💎 Премиум", callback_data="menu:premium")],
        [InlineKeyboardButton("🆘 Поддержка", callback_data="menu:support")]])
def kb_back_main(): return InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data="menu:main")]])
def kb_reg_done(): return InlineKeyboardMarkup([[InlineKeyboardButton("✅ Отправить фото", callback_data="auth:send_photo")]])
def kb_platforms():
    return InlineKeyboardMarkup([[InlineKeyboardButton("📱 Phone", callback_data="platform:Phone")],
                                 [InlineKeyboardButton("💻 PC", callback_data="platform:PC")],
                                 [InlineKeyboardButton("⬅️ Назад", callback_data="menu:main")]])
def kb_lobbies(platform, lobbies, min_free=1):
    rows = []
    for i in range(3):
        row = []
        for j in [i, i+3]:
            if j < LOBBIES_PER_PLATFORM:
                count = len(lobbies[platform][j])
                free = LOBBY_SIZE - count
                label = f"Лобби {j+1} ({count}/{LOBBY_SIZE})"
                if free >= min_free: row.append(InlineKeyboardButton(label, callback_data=f"lobby:{platform}:{j}"))
                else: row.append(InlineKeyboardButton(f"🔒 {label}", callback_data="lobby:full"))
        rows.append(row)
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="menu:find")])
    return InlineKeyboardMarkup(rows)
def kb_in_lobby(platform, idx): return InlineKeyboardMarkup([[InlineKeyboardButton("🚪 Выйти", callback_data=f"lobby_leave:{platform}:{idx}")]])
def kb_veto(available_maps):
    rows = []
    row = []
    for m in available_maps:
        row.append(InlineKeyboardButton(f"{MAP_EMOJI.get(m,'')} {m}", callback_data=f"veto_ban:{m}"))
        if len(row) == 2: rows.append(row); row = []
    if row: rows.append(row)
    return InlineKeyboardMarkup(rows)
def kb_skip_stats(): return InlineKeyboardMarkup([[InlineKeyboardButton("⏭ Пропустить статистику", callback_data="stats:skip")]])
def kb_send_results(): return InlineKeyboardMarkup([[InlineKeyboardButton("📤 Отправить результаты", callback_data="result:send")]])
def kb_admin_review(pid): return InlineKeyboardMarkup([[InlineKeyboardButton("✅ ПОДТВЕРДИТЬ", callback_data=f"admin_ok:{pid}")],[InlineKeyboardButton("❌ ОТКАЗАТЬ", callback_data=f"admin_no:{pid}")]])
def kb_party_menu(is_leader, size):
    rows = []
    if is_leader and size < MAX_PARTY_SIZE: rows.append([InlineKeyboardButton("➕ Пригласить игрока", callback_data="party:invite")])
    rows.append([InlineKeyboardButton("🚪 Покинуть пати", callback_data="party:leave")])
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="menu:main")])
    return InlineKeyboardMarkup(rows)
def kb_party_invite_response(lid): return InlineKeyboardMarkup([[InlineKeyboardButton("✅ Принять", callback_data=f"party_accept:{lid}"), InlineKeyboardButton("❌ Отказать", callback_data=f"party_decline:{lid}")]])
def kb_ready_check(): return InlineKeyboardMarkup([[InlineKeyboardButton("✅ Подтвердить", callback_data="ready:confirm")]])
def kb_players_list(items, prefix, page=0, per_page=10, back_cb="menu:main"):
    total = max(1, (len(items)+per_page-1)//per_page)
    page = max(0, min(page, total-1))
    start, end = page*per_page, min((page+1)*per_page, len(items))
    rows = [[InlineKeyboardButton(f"@{p.get('tag',uid)}", callback_data=f"{prefix}:{uid}:{page}")] for uid,p in items[start:end]]
    nav = []
    if page > 0: nav.append(InlineKeyboardButton("⬅️", callback_data=f"{prefix}_page:{page-1}"))
    nav.append(InlineKeyboardButton(f"{page+1}/{total}", callback_data="noop"))
    if page < total-1: nav.append(InlineKeyboardButton("➡️", callback_data=f"{prefix}_page:{page+1}"))
    if nav: rows.append(nav)
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data=back_cb)])
    return InlineKeyboardMarkup(rows)
def kb_complaint_actions(target_uid):
    return InlineKeyboardMarkup([[InlineKeyboardButton("✍️ Написать жалобу", callback_data=f"complaint_write:{target_uid}")],
                                 [InlineKeyboardButton("👁 Посмотреть жалобы", callback_data=f"complaint_view:{target_uid}")],
                                 [InlineKeyboardButton("⬅️ Назад", callback_data="menu:complaints")]])
def kb_confirm_yes_no(action, target_uid):
    return InlineKeyboardMarkup([[InlineKeyboardButton("✅ Да", callback_data=f"{action}_yes:{target_uid}")],
                                 [InlineKeyboardButton("❌ Нет", callback_data=f"{action}_no:{target_uid}")]])
def kb_premium_menu():
    return InlineKeyboardMarkup([[InlineKeyboardButton("⭐ 1 день — 50 звёзд", callback_data="premium:day")],
                                 [InlineKeyboardButton("⭐ 1 неделя — 350 звёзд", callback_data="premium:week")],
                                 [InlineKeyboardButton("⭐ 1 месяц — 1000 звёзд", callback_data="premium:month")],
                                 [InlineKeyboardButton("⬅️ Назад", callback_data="menu:main")]])
def kb_admin_panel():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 Аналитика", callback_data="admin:analytics"), InlineKeyboardButton("📝 История матчей", callback_data="admin:history")],
        [InlineKeyboardButton("🆔 Отвязать айди", callback_data="admin:unlink"), InlineKeyboardButton("🔨 Забанить", callback_data="admin:ban")],
        [InlineKeyboardButton("📊 ELO", callback_data="admin:elo"), InlineKeyboardButton("✏️ Изменить ID", callback_data="admin:change_id")],
        [InlineKeyboardButton("✏️ Изменить ник", callback_data="admin:change_nick"), InlineKeyboardButton("📢 Жалобы (топ)", callback_data="admin:complaints_top")],
        [InlineKeyboardButton("🏷️ Выдать тег", callback_data="admin:give_tag")]])
def kb_admin_elo_action():
    return InlineKeyboardMarkup([[InlineKeyboardButton("➕ Выдать ELO", callback_data="admin:elo_add")],
                                 [InlineKeyboardButton("➖ Убавить ELO", callback_data="admin:elo_remove")],
                                 [InlineKeyboardButton("⬅️ Назад", callback_data="admin:back")]])
def kb_history_nav(page, total):
    nav = []
    if page > 0: nav.append(InlineKeyboardButton("⬅️", callback_data=f"admin_history_page:{page-1}"))
    nav.append(InlineKeyboardButton(f"{page+1}/{max(1,total)}", callback_data="noop"))
    if page < total-1: nav.append(InlineKeyboardButton("➡️", callback_data=f"admin_history_page:{page+1}"))
    rows = [nav] if nav else []
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="admin:back")])
    return InlineKeyboardMarkup(rows)

# ===== ВСПОМОГАТЕЛЬНЫЕ АСИНХ =====
async def safe_delete(msg):
    if msg:
        try: await msg.delete()
        except: pass
async def safe_send(bot, chat_id, text=None, photo=None, **kwargs):
    try:
        if photo: return await bot.send_photo(chat_id=chat_id, photo=photo, caption=text, **kwargs)
        return await bot.send_message(chat_id=chat_id, text=text, **kwargs)
    except (Forbidden, TelegramError) as e:
        logger.warning(f"Ошибка отправки: {e}")
    return None
async def is_subscribed(bot, uid):
    if not REQUIRE_SUBSCRIPTION or not SUBSCRIPTION_CHAT_ID: return True
    try:
        m = await bot.get_chat_member(SUBSCRIPTION_CHAT_ID, uid)
        return m.status in ("member","administrator","creator")
    except: return True
async def check_banned(update):
    p = get_player(await load_players(), update.effective_user.id)
    if p and p.get('ban'):
        await update.effective_message.reply_text("⛔ Вы забанены.")
        return True
    return False
async def require_subscription(update, context):
    if REQUIRE_SUBSCRIPTION and not await is_subscribed(context.bot, update.effective_user.id):
        await update.effective_message.reply_text("📢 Подпишись на наш чат!", reply_markup=kb_subscribe())
        return False
    return True
async def update_lobby_for_all(platform, idx, context):
    lobbies = await load_lobbies()
    players = await load_players()
    players_list = lobbies[platform][idx]
    lines = [f"@{players.get(str(uid),{}).get('tag',str(uid))} {'💎' if is_premium(players.get(str(uid))) else ''}({players.get(str(uid),{}).get('elo',0)} ELO)" for uid in players_list]
    text = f"📋 ЛОББИ {idx+1} ({len(players_list)}/{LOBBY_SIZE})\n\nИГРОКИ:\n" + "\n".join(f"{i}. {p}" for i,p in enumerate(lines,1)) + f"\n\nОжидание: {len(players_list)}/{LOBBY_SIZE}"
    for uid in players_list:
        await safe_send(context.bot, uid, text, reply_markup=kb_in_lobby(platform, idx))

# ===== ТЕКСТОВЫЕ ФУНКЦИИ =====
def main_menu_text(p):
    return f"🏠 ГЛАВНОЕ МЕНЮ\n\n👤 @{p.get('tag','')}\n📊 {elo_display(p)}\n🏆 Побед: {p.get('wins',0)} | Поражений: {p.get('losses',0)}\n🎯 Матчей: {p.get('matches',0)}\n{'💎 Премиум активен!' if is_premium(p) else ''}"
def profile_text(p):
    wr = round((p['wins']/p['matches']*100) if p['matches']>0 else 0,1)
    prem = f"💎 Премиум активен! (осталось {format_premium_time(get_premium_time_left(p))})" if is_premium(p) else "Нет премиума"
    text = f"📊 МОЙ ПРОФИЛЬ\n\n👤 @{p['tag']}\n🆔 {p['sid']}\n🏅 {p['rank']}\n📊 {elo_display(p)}\n💎 {prem}\n\n📈 СТАТИСТИКА\n🎯 Матчей: {p['matches']}\n🏆 Побед: {p['wins']}\nПоражений: {p['losses']}\nWinrate: {wr}%\n⭐ MVP: {p['mvps']}\n"
    if p.get('calib',0) < CALIBRATION_GAMES: text += f"📌 Калибровка: {p['calib']}/{CALIBRATION_GAMES}\n"
    text += "\n📊 ПО КАРТАМ\n"
    for map_name, stats in p.get('maps',{}).items():
        total = stats['wins'] + stats['losses']
        if total > 0: text += f"{MAP_EMOJI.get(map_name,'')} {map_name}: {stats['wins']}-{stats['losses']} ({round(stats['wins']/total*100,1)}%)\n"
    return text
def extended_stats_text(p):
    kills, deaths, hs = p.get('total_kills',0), p.get('total_deaths',0), p.get('hs_kills',0)
    avg_kd = round(kills/deaths,2) if deaths > 0 else float(kills)
    fav_map, fav_games = None, 0
    for m,s in p.get('maps',{}).items():
        total = s['wins']+s['losses']
        if total > fav_games: fav_games, fav_map = total, m
    elo_hist = p.get('elo_history',[])[-10:]
    return f"📊 РАСШИРЕННАЯ СТАТИСТИКА\n\n👤 @{p['tag']}\n\n🔫 AVG KD: {avg_kd}\n🎯 HS%: {round(hs/kills*100,1) if kills>0 else 0}%\n🗺️ Любимая карта: {f'{MAP_EMOJI.get(fav_map,"")} {fav_map} ({fav_games} игр)' if fav_map else 'нет данных'}\n\n📈 ELO (последние {len(elo_hist)} матчей):\n{sparkline(elo_hist)}\n" + (f"Значения: {', '.join(str(v) for v in elo_hist)}\n" if elo_hist else "")
def personal_history_text(uid, history):
    matches = [m for m in history.get("matches",[]) if uid in m.get("all_players",[])][-20:][::-1]
    if not matches: return "📝 ИСТОРИЯ МАТЧЕЙ\n\nПока нет сыгранных матчей."
    lines = ["📝 ИСТОРИЯ МАТЧЕЙ (последние 20)\n"]
    for m in matches:
        result = "🏆 Победа" if uid in m.get("winners",[]) else "❌ Поражение"
        stats = m.get("stats",{}).get(uid,{})
        lines.append(f"{m['match_id']} | {MAP_EMOJI.get(m.get('map'),'')} {m.get('map')} | {result} | {stats.get('kills',0)}/{stats.get('deaths',0)}{' ⭐' if m.get('mvp')==uid else ''}")
    return "\n".join(lines)
def admin_history_page_text(history, page, per_page=10):
    matches = history.get("matches",[])[::-1]
    total = max(1, (len(matches)+per_page-1)//per_page)
    start, end = page*per_page, min((page+1)*per_page, len(matches))
    chunk = matches[start:end]
    if not chunk: return "📝 ИСТОРИЯ МАТЧЕЙ (ВСЕ)\n\nНет данных.", total
    lines = [f"📝 ИСТОРИЯ МАТЧЕЙ (ВСЕ) — стр. {page+1}/{total}\n"]
    for m in chunk:
        lines.append(f"{m['match_id']} | {MAP_EMOJI.get(m.get('map'),'')} {m.get('map')} | {m.get('timestamp','')}")
    return "\n".join(lines), total
def complaints_players_text(): return "📢 ЖАЛОБЫ\n\nВыбери игрока:"
def complaint_view_text(target_tag, reports_list):
    if not reports_list: return f"Жалоб на @{target_tag} пока нет."
    return f"ЖАЛОБЫ НА @{target_tag} ({len(reports_list)})\n" + "\n".join(f"• {r.get('text','')}" for r in reports_list)
def premium_text():
    return "💎 *ПРЕМИУМ-ПОДПИСКА*\n\n🔥 Преимущества:\n✅ Любой тег (даже занятый)\n✅ Бесплатные турниры\n✅ x2 ELO за победы\n✅ Смена ника (2 раза бесплатно, далее 50⭐)\n\n⭐ *Тарифы:*\n• 1 день — 50 звёзд\n• 1 неделя — 350 звёзд\n• 1 месяц — 1000 звёзд\n\nВыбери тариф:"
def build_analytics_text(players, pending):
    a = asyncio.run(load_analytics())
    map_picks = a.get("map_picks", {})
    top_map = max(map_picks.items(), key=lambda x: x[1])[0] if map_picks else "нет данных"
    online_samples = a.get("online_samples", [])
    avg_online = round(sum(online_samples)/len(online_samples),1) if online_samples else 0
    elos = [p.get("elo",0) for p in players.values() if p.get("reg")==1 and p.get("calib",0)>=CALIBRATION_GAMES]
    avg_elo = round(sum(elos)/len(elos),1) if elos else 0
    hours = [datetime.fromisoformat(ts).hour for ts in a.get("match_timestamps",[]) if ts]
    peak_hour = Counter(hours).most_common(1)[0][0] if hours else None
    categories = {"читер":0,"оскорбления":0,"слив":0,"афк":0,"токсик":0,"другое":0}
    keywords = {"читер":["чит","aim","wallhack","аим","вх"], "оскорбления":["оскорб","мат","хам"], "слив":["слил","слив","throw"], "афк":["афк","afk","не играл"], "токсик":["токсич","токсик"]}
    for lst in (asyncio.run(load_reports())).values():
        for r in lst:
            t = r.get("text","").lower()
            matched = False
            for cat, kws in keywords.items():
                if any(k in t for k in kws):
                    categories[cat] += 1
                    matched = True
                    break
            if not matched: categories["другое"] += 1
    return f"📊 АНАЛИТИКА\n\n🗺️ Карта: {MAP_EMOJI.get(top_map,'')} {top_map}\n👥 Средний онлайн: {avg_online}\n📈 Средний ELO: {avg_elo}\n⏰ Пиковое время: {f'{peak_hour}:00 - {(peak_hour+1)%24}:00 (МСК)' if peak_hour else 'нет данных'}\n🎮 Активных матчей: {sum(1 for v in pending.values() if v.get('status')=='awaiting_review')}\n\n📢 Топ жалоб:\n" + "\n".join(f"  • {c}: {n}" for c,n in sorted(categories.items(), key=lambda x:x[1], reverse=True) if n>0)
def party_text(parties, lid, players):
    party = parties.get(str(lid)) or parties.get(lid)
    if not party: return None
    return "🎉 ПАТИ\n" + "\n".join(f"{'👑 ' if uid==party['leader'] else '• '}@{players.get(str(uid),{}).get('tag',str(uid))} {'💎' if is_premium(players.get(str(uid))) else ''}" for uid in party["members"]) + f"\n\nСостав: {len(party['members'])}/{MAX_PARTY_SIZE}"

# ===== READY-CHECK =====
READY_CHECKS_BY_ID, READY_CHECKS, _rc_counter = {}, {}, 0
def _next_rc_id():
    global _rc_counter; _rc_counter += 1; return f"rc{_rc_counter}"
async def ready_check_timer(rc_id, timeout, context):
    await asyncio.sleep(timeout)
    rc = READY_CHECKS_BY_ID.get(rc_id)
    if rc and rc["status"] == "pending": await finalize_ready_check(rc_id, context, timed_out=True)
async def start_ready_check(platform, lobby_idx, context):
    lobbies = await load_lobbies()
    players_list = lobbies[platform][lobby_idx].copy()
    lobbies[platform][lobby_idx] = []
    await save_lobbies(lobbies)
    rc_id = _next_rc_id()
    READY_CHECKS_BY_ID[rc_id] = {"players": players_list, "confirmed": set(), "platform": platform, "lobby_idx": lobby_idx, "status": "pending", "created_at": time.time()}
    for uid in players_list: READY_CHECKS[uid] = {"id": rc_id}
    for uid in players_list:
        await safe_send(context.bot, uid, f"👥 Лобби набрано! ({LOBBY_SIZE}/{LOBBY_SIZE})\n\nУ тебя есть {READY_CHECK_TIMEOUT} секунд, чтобы подтвердить.\nЕсли не успеешь — будешь удалён.", reply_markup=kb_ready_check())
    asyncio.create_task(ready_check_timer(rc_id, READY_CHECK_TIMEOUT, context))
async def finalize_ready_check(rc_id, context, timed_out=False):
    rc = READY_CHECKS_BY_ID.get(rc_id)
    if not rc or rc["status"] != "pending": return
    rc["status"] = "done"
    confirmed = [uid for uid in rc["players"] if uid in rc["confirmed"]]
    not_confirmed = [uid for uid in rc["players"] if uid not in rc["confirmed"]]
    for uid in rc["players"]: READY_CHECKS.pop(uid, None)
    if not_confirmed:
        for uid in not_confirmed: await safe_send(context.bot, uid, "❌ Ты не подтвердил вовремя и был удалён.")
        lobbies = await load_lobbies()
        lobbies[rc["platform"]][rc["lobby_idx"]] = confirmed.copy()
        await save_lobbies(lobbies)
        for uid in confirmed: await update_lobby_for_all(rc["platform"], rc["lobby_idx"], context)
        return
    await start_match(rc["platform"], rc["players"], context)

# ===== ОСНОВНЫЕ ОБРАБОТЧИКИ =====
async def start(update, context):
    if await check_banned(update) or not await require_subscription(update, context): return
    players = await load_players()
    p = get_player(players, update.effective_user.id)
    if not p or p.get("reg") != 1:
        await update.message.reply_text("🎮 STRANGER FACEIT\n\nДобро пожаловать! Выбери действие:", reply_markup=kb_start_auth())
        return
    parties = await load_parties()
    lid, _ = find_party_of(parties, update.effective_user.id)
    await update.message.reply_text(main_menu_text(p), reply_markup=kb_main_menu(bool(lid)))
async def admin_command(update, context):
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("❌ Доступ запрещен."); return
    await update.message.reply_text("👑 АДМИН-ПАНЕЛЬ\n\nВыбери действие:", reply_markup=kb_admin_panel())
async def handle_photo(update, context):
    if await check_banned(update): return
    if context.user_data.get('auth_step') == 'reg_photo':
        pending_sid, pending_tag = context.user_data.get('reg_pending_sid'), context.user_data.get('reg_pending_tag')
        if not pending_sid or not pending_tag:
            await update.message.reply_text("❌ Сессия истекла. Начни заново."); return
        pid = f"reg_{int(time.time())}_{update.effective_user.id}"
        pending = await load_pending()
        pending[pid] = {"type":"registration","user_id":update.effective_user.id,"sid":pending_sid,"tag":pending_tag,"photo_id":update.message.photo[-1].file_id,"status":"pending"}
        await save_pending(pending)
        await safe_send(context.bot, ADMIN_CHAT_ID, f"📸 Новая заявка на регистрацию!\n\nНик: {pending_tag}\nID: {pending_sid}\nTelegram: @{update.effective_user.username or 'нет юза'}", photo=update.message.photo[-1].file_id, reply_markup=kb_admin_review(pid))
        await update.message.reply_text("⏳ Твоя заявка отправлена админам. Жди ответа.")
        context.user_data.clear(); return
    match = context.user_data.get('match')
    if not match or match.get('status') != 'in_progress':
        await update.message.reply_text("❌ Нет матча, ожидающего скриншот."); return
    if match.get('host') != update.effective_user.id:
        await update.message.reply_text("❌ Только хост может отправлять результат."); return
    if time.time() < match.get('result_unlock_time',0):
        await update.message.reply_text(f"⏳ Отправка доступна через {int(match['result_unlock_time']-time.time())} сек."); return
    context.user_data['match_photo'] = update.message.photo[-1].file_id
    await update.message.reply_text("✅ Скриншот принят!\n\nТеперь объяви победившую сторону:\n/winner ct или /winner t")
async def winner_command(update, context):
    if await check_banned(update): return
    match = context.user_data.get('match')
    if not match or match.get('status') != 'in_progress':
        await update.message.reply_text("❌ Нет активного матча."); return
    if match.get('host') != update.effective_user.id:
        await update.message.reply_text("❌ Только хост может объявить результат."); return
    args = context.args
    if not args or args[0].lower() not in ("ct","t"):
        await update.message.reply_text("Использование: /winner ct  или  /winner t"); return
    if not context.user_data.get('match_photo'):
        await update.message.reply_text("📸 Сначала отправь скриншот результата."); return
    match['winner_side'] = args[0].lower()
    match['status'] = 'awaiting_winning_team'
    context.user_data['match'] = match
    await update.message.reply_text(f"✅ Победила сторона: {args[0].upper()}\n\nКакая команда играла за {args[0].upper()} и победила?", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔵 Команда А", callback_data="winteam:a"), InlineKeyboardButton("🔴 Команда Б", callback_data="winteam:b")]]))

# ===== CALLBACK =====
async def button_callback(update, context):
    try:
        q = update.callback_query
        await q.answer()
        user, data = q.from_user, q.data
        players = await load_players()
        p = get_player(players, user.id)
        if data == "noop": return
        if data == "sub:check":
            if await is_subscribed(context.bot, user.id):
                await safe_delete(q.message)
                if not p or p.get("reg") != 1: await q.message.reply_text("✅ Подписка подтверждена!\n\n🎮 STRANGER FACEIT\n\nВыбери действие:", reply_markup=kb_start_auth())
                else:
                    parties = await load_parties()
                    lid, _ = find_party_of(parties, user.id)
                    await q.message.reply_text(main_menu_text(p), reply_markup=kb_main_menu(bool(lid)))
            else: await q.answer("❌ Подписка не найдена.", show_alert=True)
            return
        if data in ["auth:login","auth:register","auth:support"]:
            await safe_delete(q.message)
            context.user_data.clear()
            if data == "auth:login":
                context.user_data['auth_step'] = 'login_id'
                await q.message.reply_text("🔑 ВХОД\n\nВведи свой ID в Standoff 2:", reply_markup=kb_back_main())
            elif data == "auth:register":
                context.user_data['auth_step'] = 'reg_id'
                await q.message.reply_text("📝 РЕГИСТРАЦИЯ\n\nВведи свой ID в Standoff 2:", reply_markup=kb_back_main())
            else:
                context.user_data['support_mode'] = True
                await q.message.reply_text("🆘 ПОДДЕРЖКА\n\nОпиши свою проблему:", reply_markup=kb_back_main())
            return
        if data == "auth:send_photo":
            sid, tag = context.user_data.get('reg_pending_sid'), context.user_data.get('reg_pending_tag')
            if not sid or not tag:
                await q.answer("❌ Сессия истекла. Начни заново.", show_alert=True); return
            await safe_delete(q.message)
            await q.message.reply_text(f"📸 Отправь скриншот профиля из Standoff 2\n\nНа скриншоте должны быть видны:\n• ID: {sid}\n• Ник: {tag}")
            context.user_data['auth_step'] = 'reg_photo'
            return
        if data.startswith("reg_ok:") or data.startswith("reg_no:"):
            if user.id not in ADMIN_IDS:
                await q.answer("❌ Только для админов.", show_alert=True); return
            action, pid = data.split(":",1)
            pending = await load_pending()
            record = pending.get(pid)
            if not record or record.get("type") != "registration":
                await q.answer("❌ Заявка не найдена.", show_alert=True); return
            if action == "reg_ok":
                players_data = await load_players()
                np = new_player(record["sid"], record["tag"])
                np["reg"] = 1; np["name"] = record["tag"]; np["tag"] = record["tag"]; np["tg_username"] = record["tag"]
                players_data[str(record["user_id"])] = np
                await save_players(players_data)
                record["status"] = "approved"; await save_pending(pending)
                await safe_send(context.bot, record["user_id"], "✅ Регистрация подтверждена!\n\nДобро пожаловать в Stranger Faceit!\nНапиши /start")
                await safe_delete(q.message); await q.message.reply_text(f"✅ Игрок @{record['tag']} зарегистрирован!")
            else:
                record["status"] = "rejected"; await save_pending(pending)
                await safe_send(context.bot, record["user_id"], "❌ Регистрация отклонена.\nОбратись в поддержку.")
                await safe_delete(q.message); await q.message.reply_text(f"❌ Заявка @{record['tag']} отклонена.")
            return
        if data == "menu:main":
            await safe_delete(q.message); context.user_data.clear()
            if not p or p.get("reg") != 1:
                await q.message.reply_text("🎮 STRANGER FACEIT\n\nВыбери действие:", reply_markup=kb_start_auth()); return
            parties = await load_parties()
            lid, _ = find_party_of(parties, user.id)
            await q.message.reply_text(main_menu_text(p), reply_markup=kb_main_menu(bool(lid))); return
        if data == "menu:profile":
            await safe_delete(q.message)
            if not p or p.get("reg") != 1: await q.message.reply_text("Сначала зарегистрируйся!", reply_markup=kb_start_auth()); return
            await q.message.reply_text(profile_text(p), reply_markup=kb_back_main()); return
        if data == "menu:stats":
            await safe_delete(q.message)
            if not p or p.get("reg") != 1: await q.message.reply_text("Сначала зарегистрируйся!", reply_markup=kb_start_auth()); return
            await q.message.reply_text(extended_stats_text(p), reply_markup=kb_back_main()); return
        if data == "menu:history":
            await safe_delete(q.message)
            if not p or p.get("reg") != 1: await q.message.reply_text("Сначала зарегистрируйся!", reply_markup=kb_start_auth()); return
            await q.message.reply_text(personal_history_text(str(user.id), await load_history()), reply_markup=kb_back_main()); return
        if data == "menu:top":
            await safe_delete(q.message)
            sorted_players = sorted([p for p in (await load_players()).values() if p.get("reg")==1 and p.get("calib",0)>=CALIBRATION_GAMES], key=lambda x:x.get("elo",0), reverse=True)[:10]
            text = "🏆 ТОП ИГРОКОВ\n\n" + ("\n".join(f"{['👑','🥈','🥉'][i] if i<3 else f'{i+1}.'} @{p['tag']} {'💎' if is_premium(p) else ''}\n    {p['elo']} ELO | {p['rank']}" for i,p in enumerate(sorted_players)) if sorted_players else "Пока нет игроков, завершивших калибровку.\n") + f"\n\nВсего игроков: {len([p for p in (await load_players()).values() if p.get('reg')==1])}"
            await q.message.reply_text(text, reply_markup=kb_back_main()); return
        if data == "menu:support":
            await safe_delete(q.message); context.user_data['support_mode'] = True
            await q.message.reply_text("🆘 ПОДДЕРЖКА\n\nОпиши свою проблему:", reply_markup=kb_back_main()); return
        if data == "menu:premium":
            await safe_delete(q.message)
            if not p or p.get("reg") != 1: await q.message.reply_text("Сначала зарегистрируйся!", reply_markup=kb_start_auth()); return
            await q.message.reply_text(premium_text(), reply_markup=kb_premium_menu(), parse_mode="Markdown"); return
        if data.startswith("premium:"):
            period = data.split(":")[1]
            if period not in PREMIUM_PRICES:
                await q.answer("❌ Неверный тариф.", show_alert=True); return
            await safe_delete(q.message)
            await context.bot.send_invoice(chat_id=user.id, title=f"Премиум-подписка {{'day':'1 день','week':'1 неделя','month':'1 месяц'}[period]}", description=f"x2 ELO, любой тег, бесплатные турниры", payload=f"premium_{period}", currency="XTR", prices=[{"label": "{'day':'1 день','week':'1 неделя','month':'1 месяц'}[period]", "amount": PREMIUM_PRICES[period]}], start_parameter=f"premium_{period}")
            return
        if data == "menu:complaints":
            await safe_delete(q.message)
            all_players = sorted([(uid,p) for uid,p in (await load_players()).items() if p.get("reg")==1], key=lambda x:x[1].get('tag',''))
            context.user_data['complaints_players'] = all_players
            await q.message.reply_text(complaints_players_text(), reply_markup=kb_players_list(all_players, "complaint", 0, back_cb="menu:main")); return
        if data.startswith("complaint_page:"):
            page = int(data.split(":")[1])
            all_players = context.user_data.get('complaints_players', [])
            await q.message.edit_text(complaints_players_text(), reply_markup=kb_players_list(all_players, "complaint", page, back_cb="menu:main")); return
        if data.startswith("complaint:"):
            _, target_uid, page = data.split(":")
            target_p = (await load_players()).get(target_uid)
            if not target_p: await q.answer("❌ Игрок не найден.", show_alert=True); return
            await safe_delete(q.message)
            await q.message.reply_text(f"👤 @{target_p.get('tag',target_uid)}\n\nВыбери действие:", reply_markup=kb_complaint_actions(target_uid)); return
        if data.startswith("complaint_write:"):
            target_uid = data.split(":")[1]
            if target_uid == str(user.id): await q.answer("❌ Нельзя пожаловаться на себя.", show_alert=True); return
            reports = await load_reports()
            if any(r.get("by") == str(user.id) for r in reports.get(target_uid, [])):
                await q.answer("❌ Ты уже жаловался на этого игрока.", show_alert=True); return
            context.user_data['complaint_target'] = target_uid
            await safe_delete(q.message)
            await q.message.reply_text(f"✍️ Опиши жалобу на @{(await load_players()).get(target_uid,{}).get('tag',target_uid)}:"); return
        if data.startswith("complaint_view:"):
            target_uid = data.split(":")[1]
            target_p = (await load_players()).get(target_uid, {})
            reports = await load_reports()
            await safe_delete(q.message)
            await q.message.reply_text(complaint_view_text(target_p.get('tag',target_uid), reports.get(target_uid, [])), reply_markup=kb_back_main()); return
        if data == "menu:party":
            await safe_delete(q.message)
            if not p or p.get("reg") != 1: await q.message.reply_text("Сначала зарегистрируйся!", reply_markup=kb_start_auth()); return
            parties = await load_parties()
            lid, party = find_party_of(parties, user.id)
            if not party:
                await q.message.reply_text("🎉 ПАТИ\n\nТы пока не в группе.\nПригласи друга!", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("➕ Пригласить игрока", callback_data="party:invite")],[InlineKeyboardButton("⬅️ Назад", callback_data="menu:main")]])); return
            await q.message.reply_text(party_text(parties, lid, await load_players()), reply_markup=kb_party_menu(int(lid)==user.id, len(party["members"]))); return
        if data == "party:invite":
            await safe_delete(q.message)
            parties = await load_parties()
            lid, party = find_party_of(parties, user.id)
            if not party:
                parties[str(user.id)] = {"leader": user.id, "members": [user.id], "pending_invite": None}
                await save_parties(parties)
                lid, party = str(user.id), parties[str(user.id)]
            if int(lid) != user.id: await q.message.reply_text("❌ Только лидер может приглашать.", reply_markup=kb_back_main()); return
            if len(party["members"]) >= MAX_PARTY_SIZE: await q.message.reply_text("❌ Пати заполнена (максимум 5 человек).", reply_markup=kb_back_main()); return
            context.user_data['party_invite_mode'] = True
            await q.message.reply_text("👤 Введите Telegram юзернейм игрока:", reply_markup=kb_back_main()); return
        if data == "party:leave":
            await safe_delete(q.message)
            parties = await load_parties()
            lid, party = find_party_of(parties, user.id)
            if not party: await q.message.reply_text("Ты не в пати.", reply_markup=kb_back_main()); return
            if int(lid) == user.id:
                for uid in party["members"]:
                    if uid != user.id: await safe_send(context.bot, uid, "🎉 Пати расформирована лидером.")
                del parties[lid]; await save_parties(parties); await q.message.reply_text("🚪 Пати расформирована.", reply_markup=kb_back_main())
            else:
                party["members"].remove(user.id)
                parties[lid] = party; await save_parties(parties)
                await safe_send(context.bot, int(lid), f"🎉 @{p['tag']} покинул пати.")
                await q.message.reply_text("🚪 Ты покинул пати.", reply_markup=kb_back_main())
            return
        if data.startswith("party_accept:") or data.startswith("party_decline:"):
            action, lid = data.split(":",1)
            parties = await load_parties()
            party = parties.get(lid)
            await safe_delete(q.message)
            if not party or not party.get("pending_invite") or party["pending_invite"].get("target") != user.id:
                await q.message.reply_text("❌ Приглашение не действительно."); return
            if action == "party_decline":
                party["pending_invite"] = None; parties[lid] = party; await save_parties(parties)
                await q.message.reply_text("❌ Приглашение отклонено."); await safe_send(context.bot, int(lid), f"❌ @{p['tag']} отклонил приглашение."); return
            target_lid, target_party = find_party_of(parties, user.id)
            if target_party and int(target_lid) == user.id:
                del parties[str(user.id)]; await save_parties(parties); parties = await load_parties(); party = parties.get(lid)
                if not party: await q.message.reply_text("❌ Пати больше не существует."); return
            if len(party["members"]) >= MAX_PARTY_SIZE:
                await q.message.reply_text("❌ Пати уже заполнена.")
                party["pending_invite"] = None; parties[lid] = party; await save_parties(parties); return
            lobbies = await load_lobbies()
            for plt in PLATFORMS:
                for i, lobby in enumerate(lobbies[plt]):
                    if user.id in lobby: lobby.remove(user.id)
            await save_lobbies(lobbies)
            party["members"].append(user.id); party["pending_invite"] = None; parties[lid] = party; await save_parties(parties)
            await q.message.reply_text(f"✅ Ты присоединился к пати @{(await load_players()).get(lid,{}).get('tag',lid)}!", reply_markup=kb_back_main())
            for uid in party["members"]:
                await safe_send(context.bot, uid, party_text(parties, lid, await load_players()), reply_markup=kb_party_menu(uid==int(lid), len(party["members"])))
            return
        if data == "menu:find":
            await safe_delete(q.message)
            if not p or p.get("reg") != 1: await q.message.reply_text("Сначала зарегистрируйся!", reply_markup=kb_start_auth()); return
            await q.message.reply_text("📱 ВЫБЕРИ ПЛАТФОРМУ", reply_markup=kb_platforms()); return
        if data.startswith("platform:"):
            platform = data.split(":")[1]
            lobbies = await load_lobbies()
            parties = await load_parties()
            lid, party = find_party_of(parties, user.id)
            if party and int(lid) != user.id:
                await safe_delete(q.message); await q.message.reply_text("❌ Только лидер пати может выбирать лобби.", reply_markup=kb_back_main()); return
            await safe_delete(q.message)
            await q.message.reply_text(f"📱 {platform.upper()} ЛОББИ" + (f"\n(нужно {len(party['members']) if party else 1} мест для пати)" if party else ""), reply_markup=kb_lobbies(platform, lobbies, len(party["members"]) if party else 1)); return
        if data == "lobby:full":
            await q.answer("❌ Недостаточно свободных мест.", show_alert=True); return
        if data.startswith("lobby:"):
            _, platform, idx_str = data.split(":")
            idx = int(idx_str)
            lobbies = await load_lobbies()
            parties = await load_parties()
            lid, party = find_party_of(parties, user.id)
            members_to_add = party["members"] if party else [user.id]
            if len(lobbies[platform][idx]) + len(members_to_add) > LOBBY_SIZE:
                await q.answer("❌ Недостаточно места для всей пати!", show_alert=True); return
            for plt in PLATFORMS:
                for lobby in lobbies[plt]:
                    for m in members_to_add:
                        if m in lobby: lobby.remove(m)
            for m in members_to_add:
                if m not in lobbies[platform][idx]: lobbies[platform][idx].append(m)
            await save_lobbies(lobbies)
            await safe_delete(q.message)
            await update_lobby_for_all(platform, idx, context)
            if len(lobbies[platform][idx]) >= LOBBY_SIZE: await start_ready_check(platform, idx, context)
            return
        if data.startswith("lobby_leave:"):
            _, platform, idx_str = data.split(":")
            idx = int(idx_str)
            lobbies = await load_lobbies()
            parties = await load_parties()
            lid, party = find_party_of(parties, user.id)
            members_to_remove = party["members"] if (party and int(lid) == user.id) else [user.id]
            for m in members_to_remove:
                if m in lobbies[platform][idx]: lobbies[platform][idx].remove(m)
            await save_lobbies(lobbies)
            await safe_delete(q.message)
            await update_lobby_for_all(platform, idx, context)
            for m in members_to_remove: await safe_send(context.bot, m, "🚪 Вышел из лобби", reply_markup=kb_platforms())
            return
        if data == "ready:confirm":
            rs = READY_CHECKS.get(user.id)
            if not rs: await q.answer("❌ Нет активной проверки.", show_alert=True); return
            rc = READY_CHECKS_BY_ID.get(rs["id"])
            if not rc or rc["status"] != "pending": await q.answer("❌ Проверка уже завершена.", show_alert=True); return
            rc["confirmed"].add(user.id)
            await safe_delete(q.message); await q.message.reply_text("✅ Готовность подтверждена! Ждём остальных...")
            if len(rc["confirmed"]) >= len(rc["players"]): await finalize_ready_check(rs["id"], context)
            return
        if data.startswith("veto_ban:"):
            map_name = data.split(":",1)[1]
            veto = context.user_data.get('veto')
            if not veto: await q.answer("❌ Вето не активно", show_alert=True); return
            success, error = veto_ban(veto, str(user.id), map_name)
            if not success: await q.answer(f"❌ {error}", show_alert=True); return
            await safe_delete(q.message)
            if veto["final_map"]:
                match = context.user_data.get('match', {})
                match['map'] = veto['final_map']; match['status'] = 'in_progress'; match['result_unlock_time'] = time.time() + RESULT_UNLOCK_DELAY
                context.user_data['match'] = match
                host_p = (await load_players()).get(str(match.get('host')), {})
                for uid in match.get('players', []):
                    await safe_send(context.bot, uid, f"🏆 Матч сформирован!\n\nID: {context.user_data.get('match_id')}\nИгра до 13\nКарта: {MAP_EMOJI.get(veto['final_map'],'')} {veto['final_map']}\nРаунд: 1:50\nХост: @{host_p.get('tag', match.get('host'))}\n\nПосле матча хост отправляет фото, затем:\n/winner ct или /winner t")
                asyncio.create_task(_notify_result_ready(match.get('host'), context.user_data.get('match_id'), context))
                return
            next_player = (await load_players()).get(veto["turn"], {})
            await q.message.reply_text(f"🗺️ ВЕТО\n\nХод: @{next_player.get('tag', veto['turn'])}\nДоступные карты:\n" + "\n".join(f"• {MAP_EMOJI.get(m,'')} {m}" for m in veto["pool"]), reply_markup=kb_veto(veto["pool"])); return
        if data.startswith("winteam:"):
            match = context.user_data.get('match')
            if not match or match.get('status') != 'awaiting_winning_team':
                await q.answer("❌ Нет матча, ожидающего выбор.", show_alert=True); return
            if match.get('host') != user.id: await q.answer("❌ Только хост может указать победителя.", show_alert=True); return
            choice = data.split(":",1)[1]
            match['winner_team'] = choice; match['status'] = 'awaiting_stats'
            context.user_data['match'] = match; context.user_data['stats_mode'] = True; context.user_data['stats_buffer'] = {}
            await safe_delete(q.message)
            await q.message.reply_text("Теперь введи статистику каждого игрока в формате:\n@ник убийства-смерти-хс\n\nНапример:\n@Vasya 18-9-5\n\nОтправляй по одному игроку.", reply_markup=kb_skip_stats()); return
        if data == "stats:skip":
            await safe_delete(q.message); await finalize_match(update, context, skip_stats=True); return
        if data == "result:send": await q.answer(); return

        # ADMIN PANEL
        if user.id == OWNER_ID and data.startswith("admin:"):
            action = data.split(":",1)[1]
            if action == "back":
                await safe_delete(q.message); context.user_data.pop('admin_action',None); context.user_data.pop('admin_target',None)
                await q.message.reply_text("👑 АДМИН-ПАНЕЛЬ\n\nВыбери действие:", reply_markup=kb_admin_panel()); return
            if action == "analytics":
                await safe_delete(q.message)
                await q.message.reply_text(build_analytics_text(await load_players(), await load_pending()), reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data="admin:back")]])); return
            if action == "complaints_top":
                await safe_delete(q.message)
                reports = await load_reports()
                counts = sorted(((await load_players()).get(uid,{}).get('tag',uid), len(lst)) for uid,lst in reports.items() if lst, key=lambda x:x[1], reverse=True)[:10]
                await q.message.reply_text("📢 ТОП ЖАЛОБ ПО ИГРОКАМ\n\n" + ("\n".join(f"@{t} — {n}" for t,n in counts) if counts else "Нет жалоб."), reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data="admin:back")]])); return
            if action == "history":
                history = await load_history()
                text, total = admin_history_page_text(history, 0)
                await safe_delete(q.message); await q.message.reply_text(text, reply_markup=kb_history_nav(0, total)); return
            if action == "unlink":
                all_players = sorted([(uid,p) for uid,p in (await load_players()).items() if p.get("reg")==1], key=lambda x:x[1].get('tag',''))
                context.user_data['admin_unlink_players'] = all_players
                await safe_delete(q.message)
                await q.message.reply_text("🆔 ОТВЯЗАТЬ АЙДИ\n\nВыбери игрока:", reply_markup=kb_players_list(all_players, "unlink", 0, back_cb="admin:back")); return
            if action == "ban":
                all_players = sorted([(uid,p) for uid,p in (await load_players()).items() if p.get("reg")==1], key=lambda x:x[1].get('tag',''))
                context.user_data['admin_ban_players'] = all_players
                await safe_delete(q.message)
                await q.message.reply_text("🔨 ЗАБАНИТЬ ИГРОКА\n\nВыбери игрока:", reply_markup=kb_players_list(all_players, "ban", 0, back_cb="admin:back")); return
            if action == "elo":
                await safe_delete(q.message); await q.message.reply_text("📊 УПРАВЛЕНИЕ ELO\n\nВыбери действие:", reply_markup=kb_admin_elo_action()); return
            if action in ("elo_add","elo_remove"):
                sub = "add" if action=="elo_add" else "remove"
                context.user_data['admin_action'] = f"elo_{sub}"
                all_players = sorted([(uid,p) for uid,p in (await load_players()).items() if p.get("reg")==1], key=lambda x:x[1].get('elo',0), reverse=True)
                context.user_data[f'admin_elo_{sub}_players'] = all_players
                await safe_delete(q.message)
                await q.message.reply_text("👥 Выбери игрока:", reply_markup=kb_players_list(all_players, f"eloact_{sub}", 0, back_cb="admin:back")); return
            if action in ("change_id","change_nick"):
                context.user_data['admin_action'] = action
                all_players = sorted([(uid,p) for uid,p in (await load_players()).items() if p.get("reg")==1], key=lambda x:x[1].get('tag',''))
                context.user_data['admin_change_players'] = all_players
                await safe_delete(q.message)
                await q.message.reply_text(f"✏️ Выбери игрока для изменения {'ID' if action=='change_id' else 'ник'}:", reply_markup=kb_players_list(all_players, f"chg_{action}", 0, back_cb="admin:back")); return
            if action == "give_tag":
                all_players = sorted([(uid,p) for uid,p in (await load_players()).items() if p.get("reg")==1], key=lambda x:x[1].get('tag',''))
                context.user_data['admin_give_tag_players'] = all_players
                await safe_delete(q.message)
                await q.message.reply_text("🏷️ ВЫДАТЬ ТЕГ\n\nВыбери игрока:", reply_markup=kb_players_list(all_players, "give_tag", 0, back_cb="admin:back")); return

        if data.startswith("admin_history_page:"):
            page = int(data.split(":")[1])
            history = await load_history()
            text, total = admin_history_page_text(history, page)
            await q.message.edit_text(text, reply_markup=kb_history_nav(page, total)); return
        for prefix in ["unlink","ban","eloact_add","eloact_remove","chg_change_id","chg_change_nick","give_tag"]:
            if data.startswith(f"{prefix}_page:"):
                page = int(data.split(":")[1])
                key_map = {"unlink":"admin_unlink_players","ban":"admin_ban_players","eloact_add":"admin_elo_add_players","eloact_remove":"admin_elo_remove_players","chg_change_id":"admin_change_players","chg_change_nick":"admin_change_players","give_tag":"admin_give_tag_players"}
                lst = context.user_data.get(key_map[prefix], [])
                await q.message.edit_text(q.message.text or "Выбери игрока:", reply_markup=kb_players_list(lst, prefix, page, back_cb="admin:back")); return
        if data.startswith("unlink:") and user.id == OWNER_ID:
            _, target_uid, _ = data.split(":")
            target_p = (await load_players()).get(target_uid)
            if not target_p: await q.answer("❌ Игрок не найден.", show_alert=True); return
            await safe_delete(q.message)
            await q.message.reply_text(f"⚠️ Отвязать айди и сбросить ВСЮ статистику игрока @{target_p.get('tag',target_uid)}?", reply_markup=kb_confirm_yes_no("unlink", target_uid)); return
        if data.startswith("unlink_yes:") and user.id == OWNER_ID:
            target_uid = data.split(":")[1]
            players_data = await load_players()
            if target_uid in players_data:
                audit_log("admin_unlink", user.id, {"target": target_uid, "old_data": players_data[target_uid]})
                del players_data[target_uid]
                await save_players(players_data)
                await safe_send(context.bot, int(target_uid), "🆔 Твой аккаунт был отвязан. Статистика сброшена.")
            await safe_delete(q.message); await q.message.reply_text("✅ Айди отвязан, статистика сброшена (см. audit.log)."); return
        if data.startswith("unlink_no:") and user.id == OWNER_ID:
            await safe_delete(q.message); await q.message.reply_text("Отменено.", reply_markup=kb_admin_panel()); return
        if data.startswith("ban:") and user.id == OWNER_ID:
            _, target_uid, _ = data.split(":")
            target_p = (await load_players()).get(target_uid)
            if not target_p: await q.answer("❌ Игрок не найден.", show_alert=True); return
            await safe_delete(q.message)
            await q.message.reply_text(f"⚠️ Забанить игрока @{target_p.get('tag',target_uid)}? Статистика НЕ будет сброшена.", reply_markup=kb_confirm_yes_no("ban", target_uid)); return
        if data.startswith("ban_yes:") and user.id == OWNER_ID:
            target_uid = data.split(":")[1]
            players_data = await load_players()
            if target_uid in players_data:
                players_data[target_uid]['ban'] = True
                await save_players(players_data); audit_log("admin_ban", user.id, {"target": target_uid})
                await safe_send(context.bot, int(target_uid), "🔨 Ты был забанен.")
            await safe_delete(q.message); await q.message.reply_text("✅ Игрок забанен."); return
        if data.startswith("ban_no:") and user.id == OWNER_ID:
            await safe_delete(q.message); await q.message.reply_text("Отменено.", reply_markup=kb_admin_panel()); return
        if data.startswith("eloact_add:") or data.startswith("eloact_remove:"):
            if user.id != OWNER_ID: return
            sub = "add" if data.startswith("eloact_add:") else "remove"
            target_uid = data.split(":")[1]
            target_p = (await load_players()).get(target_uid)
            if not target_p: await q.answer("❌ Игрок не найден.", show_alert=True); return
            context.user_data['admin_target'] = target_uid
            context.user_data['admin_action'] = f"elo_{sub}"
            context.user_data['admin_input_mode'] = 'elo_amount'
            await safe_delete(q.message)
            await q.message.reply_text(f"🎯 Игрок: @{target_p['tag']}\nТекущий ELO: {target_p['elo']}\n\nВведи сумму ELO для {'выдачи' if sub=='add' else 'убавки'}:"); return
        if data.startswith("chg_change_id:") or data.startswith("chg_change_nick:"):
            if user.id != OWNER_ID: return
            is_id = data.startswith("chg_change_id:")
            target_uid = data.split(":")[1]
            target_p = (await load_players()).get(target_uid)
            if not target_p: await q.answer("❌ Игрок не найден.", show_alert=True); return
            context.user_data['admin_target'] = target_uid
            context.user_data['admin_input_mode'] = 'change_id' if is_id else 'change_nick'
            await safe_delete(q.message)
            await q.message.reply_text(f"✏️ Введи {'новый ID (8-15 цифр)' if is_id else 'новый ник'} для @{target_p['tag']}:"); return
        if data.startswith("give_tag:") and user.id == OWNER_ID:
            _, target_uid, _ = data.split(":")
            target_p = (await load_players()).get(target_uid)
            if not target_p: await q.answer("❌ Игрок не найден.", show_alert=True); return
            context.user_data['admin_give_tag_target'] = target_uid
            await safe_delete(q.message)
            await q.message.reply_text(f"🏷️ Введи новый тег для @{target_p.get('tag',target_uid)}:")
            context.user_data['admin_input_mode'] = 'give_tag'; return
        if data.startswith("admin_ok:") or data.startswith("admin_no:"):
            if user.id not in ADMIN_IDS:
                await q.answer("❌ Только для администраторов.", show_alert=True); return
            action, pid = data.split(":",1)
            pending = await load_pending()
            record = pending.get(pid)
            if not record:
                await q.answer("❌ Заявка не найдена.", show_alert=True); return
            players_data = await load_players()
            if action == "admin_ok":
                audit_log("admin_confirm_match", user.id, {"match_id": pid})
                record['status'] = 'confirmed'
                record['confirmed_by'] = players_data.get(str(user.id), {}).get('tag', str(user.id))
                pending[pid] = record
                await save_pending(pending)
                for uid, summary in record['player_results'].items():
                    p = players_data.get(uid)
                    if not p: continue
                    text = f"✅ Матч подтверждён!\n\nID: {record['match_id']}\nКарта: {MAP_EMOJI.get(record['map_name'],'')} {record['map_name']}\n{'🏆 Победа' if summary['is_winner'] else 'Поражение'}\n"
                    if summary.get('calibrating'): text += f"Калибровка: {p['calib']}/{CALIBRATION_GAMES}\n"
                    elif summary.get('just_finished_calibration'): text += f"Калибровка завершена! ELO: {summary['new_elo']}\n"
                    else: text += f"{summary['delta']:+d} ELO → {summary['new_elo']}\n"
                    if summary.get('mvp'): text += "⭐ MVP матча!\n"
                    await safe_send(context.bot, int(uid), text)
                await save_players(players_data)
                await safe_delete(q.message); await q.message.reply_text(f"✅ Матч {record['match_id']} подтверждён.")
            else:
                audit_log("admin_reject_match", user.id, {"match_id": pid})
                for uid, summary in record['player_results'].items():
                    p = players_data.get(uid)
                    if not p: continue
                    if summary.get('_snapshot_before'): rollback_match_result(p, summary['_snapshot_before'])
                    map_name = record['map_name']
                    if map_name in p.get('maps', {}):
                        if summary['is_winner']: p['maps'][map_name]['wins'] = max(0, p['maps'][map_name]['wins']-1)
                        else: p['maps'][map_name]['losses'] = max(0, p['maps'][map_name]['losses']-1)
                    await safe_send(context.bot, int(uid), f"❌ Результат матча {record['match_id']} отклонён. Изменения отменены.")
                await save_players(players_data)
                record['status'] = 'rejected'
                pending[pid] = record
                await save_pending(pending)
                await safe_delete(q.message); await q.message.reply_text(f"❌ Матч {record['match_id']} отклонён.")
    except Exception as e:
        logger.error(f"Ошибка в button_callback: {e}", exc_info=True)
        try: await update.callback_query.message.reply_text("❌ Произошла ошибка. Попробуйте снова.")
        except: pass

# ===== ПЛАТЕЖИ =====
async def pre_checkout_query(update, context):
    await update.pre_checkout_query.answer(ok=True)
async def successful_payment(update, context):
    payload = update.message.successful_payment.invoice_payload
    if not payload.startswith("premium_"): return
    period = payload.split("_")[1]
    if period not in PREMIUM_DURATIONS: return
    players = await load_players()
    p = get_player(players, update.effective_user.id)
    if p:
        p['premium_until'] = int(time.time()) + PREMIUM_DURATIONS[period]
        await save_players(players)
        await update.message.reply_text(f"✅ Премиум-подписка активирована!\n\n📅 Период: {{'day':'1 день','week':'1 неделя','month':'1 месяц'}}[period]\n💎 Теперь тебе доступны:\n• x2 ELO\n• Любой тег\n• Бесплатные турниры\n• Смена ника (2 раза бесплатно, далее 50⭐)")
    else:
        await update.message.reply_text("❌ Ошибка: игрок не найден. Обратись в поддержку.")
async def _notify_result_ready(host_id, match_id, context):
    await asyncio.sleep(RESULT_UNLOCK_DELAY)
    if host_id:
        await safe_send(context.bot, host_id, f"📤 Можешь отправить скриншот результата матча {match_id}, затем /winner ct или /winner t.", reply_markup=kb_send_results())

# ===== ОБРАБОТЧИК СООБЩЕНИЙ =====
STATS_LINE_RE = re.compile(r"^@?([A-Za-z0-9_]+)\s+(\d+)\s*-\s*(\d+)(?:\s*-\s*(\d+))?$")
async def handle_message(update, context):
    try:
        user, text = update.effective_user, update.message.text.strip()
        players = await load_players()
        if context.user_data.get('admin_input_mode') == 'elo_amount' and user.id == OWNER_ID:
            try:
                amount = int(text)
                if amount <= 0:
                    await update.message.reply_text("❌ Сумма должна быть положительным числом."); return
                action, target_uid = context.user_data.get('admin_action'), context.user_data.get('admin_target')
                players_data = await load_players()
                tp = players_data.get(target_uid)
                if not tp: await update.message.reply_text("❌ Игрок не найден."); return
                tp['elo'] = max(0, tp['elo'] + (amount if action=='elo_add' else -amount))
                tp['level'] = level_from_elo(tp['elo']); tp['rank'] = rank_label(tp['level'])
                audit_log(f"admin_{action}", user.id, {"target": target_uid, "amount": amount, "new_elo": tp['elo']})
                await save_players(players_data); await update.message.reply_text(f"✅ ELO обновлён. Новый ELO @{tp['tag']}: {tp['elo']}")
                context.user_data.pop('admin_input_mode',None); context.user_data.pop('admin_action',None); context.user_data.pop('admin_target',None); return
            except ValueError: await update.message.reply_text("❌ Введи число (например: 50)"); return
        if context.user_data.get('admin_input_mode') in ('change_id','change_nick') and user.id == OWNER_ID:
            mode, target_uid = context.user_data['admin_input_mode'], context.user_data.get('admin_target')
            players_data = await load_players()
            tp = players_data.get(target_uid)
            if not tp: await update.message.reply_text("❌ Игрок не найден."); return
            if mode == 'change_id':
                if not validate_id(text): await update.message.reply_text("❌ ID должен быть числом из 8-15 цифр!"); return
                if find_by_sid(players_data, text): await update.message.reply_text("❌ Этот ID уже занят!"); return
                audit_log("admin_change_id", user.id, {"target": target_uid, "old": tp['sid'], "new": text}); tp['sid'] = text
            else:
                if find_by_tag(players_data, text): await update.message.reply_text("❌ Этот ник уже занят!"); return
                audit_log("admin_change_nick", user.id, {"target": target_uid, "old": tp['tag'], "new": text}); tp['tag'] = text; tp['name'] = text
            await save_players(players_data); await update.message.reply_text(f"✅ {'ID' if mode=='change_id' else 'Ник'} изменён на {text}.")
            context.user_data.pop('admin_input_mode',None); context.user_data.pop('admin_target',None); return
        if context.user_data.get('admin_input_mode') == 'give_tag' and user.id == OWNER_ID:
            target_uid, fresh_players = context.user_data.get('admin_give_tag_target'), await load_players()
            tp = fresh_players.get(target_uid)
            if not tp: await update.message.reply_text("❌ Игрок не найден."); return
            old_tag = tp['tag']; tp['tag'] = text; tp['name'] = text
            audit_log("admin_give_tag", user.id, {"target": target_uid, "old": old_tag, "new": text})
            await save_players(fresh_players); await update.message.reply_text(f"✅ Тег @{old_tag} изменён на: {text}")
            context.user_data.pop('admin_input_mode',None); context.user_data.pop('admin_give_tag_target',None); return
        if context.user_data.get('auth_step') == 'login_id':
            if not validate_id(text): await update.message.reply_text("❌ ID должен быть числом из 8-15 цифр!"); return
            owner_uid = find_by_sid(players, text)
            if not owner_uid: await update.message.reply_text("❌ Игрок с таким ID не найден."); return
            context.user_data['login_sid'], context.user_data['login_owner_uid'], context.user_data['auth_step'] = text, owner_uid, 'login_name'
            await update.message.reply_text(f"✅ ID найден!\n\nТеперь введи свой ник в Standoff 2:"); return
        if context.user_data.get('auth_step') == 'login_name':
            owner_uid, fresh_players = context.user_data.get('login_owner_uid'), await load_players()
            owner_p = fresh_players.get(owner_uid)
            if not owner_p: await update.message.reply_text("❌ Ошибка. Начни заново."); context.user_data.clear(); return
            if text.lstrip("@").lower() != owner_p.get('tag','').lower():
                await update.message.reply_text("❌ Ник не совпадает. Попробуй снова."); return
            if owner_uid != str(user.id):
                fresh_players[str(user.id)] = owner_p; del fresh_players[owner_uid]
                fresh_players[str(user.id)]['tg_username'] = user.username or str(user.id)
                await save_players(fresh_players)
            context.user_data.clear()
            p = fresh_players.get(str(user.id))
            parties = await load_parties()
            lid, _ = find_party_of(parties, user.id)
            await update.message.reply_text("✅ Вход выполнен!"); await update.message.reply_text(main_menu_text(p), reply_markup=kb_main_menu(bool(lid))); return
        if context.user_data.get('auth_step') == 'reg_id':
            if not validate_id(text): await update.message.reply_text("❌ ID должен быть числом из 8-15 цифр!"); return
            if find_by_sid(players, text): await update.message.reply_text("❌ Этот ID уже зарегистрирован!"); return
            context.user_data['reg_pending_sid'], context.user_data['auth_step'] = text, 'reg_nick'
            await update.message.reply_text(f"✅ ID принят!\n\nТеперь введи свой НАСТОЯЩИЙ ник в Standoff 2:"); return
        if context.user_data.get('auth_step') == 'reg_nick':
            if find_by_tag(players, text): await update.message.reply_text("❌ Этот ник уже занят! Введи другой:"); return
            context.user_data['reg_pending_tag'] = text
            await update.message.reply_text(f"✅ Ник принят: {text}\n\n📸 Теперь отправь скриншот профиля из Standoff 2\nНа скриншоте должны быть видны:\n• Твой ID: {context.user_data.get('reg_pending_sid')}\n• Твой ник: {text}\n\nАдминистраторы проверят и подтвердят регистрацию.", reply_markup=kb_reg_done())
            context.user_data['auth_step'] = 'reg_photo_wait'; return
        if context.user_data.get('party_invite_mode'):
            context.user_data['party_invite_mode'] = False
            target_uid = find_by_telegram_username(players, text)
            if not target_uid: await update.message.reply_text("❌ Игрок с таким Telegram юзернеймом не найден."); return
            if int(target_uid) == user.id: await update.message.reply_text("❌ Нельзя пригласить самого себя."); return
            parties = await load_parties()
            lid, party = find_party_of(parties, user.id)
            if not party or int(lid) != user.id: await update.message.reply_text("❌ Ты не лидер пати."); return
            if int(target_uid) in party["members"]: await update.message.reply_text("❌ Этот игрок уже в пати."); return
            if find_party_of(parties, int(target_uid))[1]: await update.message.reply_text("❌ Игрок уже в другой пати."); return
            if len(party["members"]) >= MAX_PARTY_SIZE: await update.message.reply_text("❌ Пати заполнена."); return
            party["pending_invite"] = {"target": int(target_uid), "invited_at": time.time()}
            parties[lid] = party; await save_parties(parties)
            await update.message.reply_text(f"✅ Приглашение отправлено @{players.get(str(target_uid),{}).get('tag',target_uid)}!")
            await safe_send(context.bot, int(target_uid), f"🎉 Игрок @{players.get(str(user.id),{}).get('tag',user.id)} приглашает в пати.", reply_markup=kb_party_invite_response(lid))
            return
        if context.user_data.get('support_mode'):
            p = get_player(players, user.id)
            await safe_send(context.bot, ADMIN_CHAT_ID, f"🆘 Поддержка\n\n👤 @{p.get('tag',user.id) if p else user.id} (ID: {user.id})\n📝 {text}")
            context.user_data['support_mode'] = False
            await update.message.reply_text("✅ Запрос отправлен!", reply_markup=kb_main_menu() if (p and p.get('reg')==1) else kb_start_auth())
            return
        if context.user_data.get('complaint_target'):
            target_uid = context.user_data.pop('complaint_target')
            report_text = sanitize_input(text)
            if not report_text: await update.message.reply_text("❌ Текст жалобы не может быть пустым."); return
            reports = await load_reports()
            reports.setdefault(target_uid, [])
            if any(r.get("by") == str(user.id) for r in reports[target_uid]):
                await update.message.reply_text("❌ Ты уже жаловался на этого игрока."); return
            reports[target_uid].append({"by": str(user.id), "text": report_text, "timestamp": datetime.now().isoformat()})
            await save_reports(reports)
            await update.message.reply_text(f"✅ Жалоба на @{(await load_players()).get(target_uid,{}).get('tag',target_uid)} отправлена.")
            return
        if context.user_data.get('stats_mode'): await handle_stats_input(update, context, text)
    except Exception as e:
        logger.error(f"Ошибка в handle_message: {e}", exc_info=True)
        await update.message.reply_text("❌ Произошла ошибка. Попробуйте снова.")

async def handle_stats_input(update, context, text):
    match, m = context.user_data.get('match'), STATS_LINE_RE.match(text)
    if not match: context.user_data['stats_mode'] = False; await update.message.reply_text("❌ Нет активного матча."); return
    if match.get('host') != update.effective_user.id: await update.message.reply_text("❌ Статистику вводит только хост."); return
    if not m: await update.message.reply_text("❌ Неверный формат. Используй: @ник убийства-смерти-хс\nНапример: @Vasya 18-9-5", reply_markup=kb_skip_stats()); return
    tag, kills_str, deaths_str, hs_str = m.groups()
    players = await load_players()
    uid = find_by_tag(players, tag)
    if not uid or int(uid) not in match.get('players', []):
        await update.message.reply_text(f"❌ Игрок @{tag} не найден в этом матче.", reply_markup=kb_skip_stats()); return
    buffer = context.user_data.setdefault('stats_buffer', {})
    buffer[uid] = {"kills": int(kills_str), "deaths": int(deaths_str), "hs": int(hs_str) if hs_str else 0}
    remaining = [str(p) for p in match['players'] if str(p) not in buffer]
    if remaining:
        await update.message.reply_text(f"✅ Записано: @{tag} {kills_str}-{deaths_str}\n\nОсталось: {len(remaining)}", reply_markup=kb_skip_stats())
    else: await finalize_match(update, context, skip_stats=False)

async def finalize_match(update, context, skip_stats):
    match = context.user_data.get('match')
    if not match: return
    players = await load_players()
    winning_team = match['team_a'] if match.get('winner_team') == 'a' else match['team_b']
    losing_team = match['team_b'] if match.get('winner_team') == 'a' else match['team_a']
    stats_buffer = context.user_data.get('stats_buffer', {}) if not skip_stats else {}
    mvp_uid = None
    if stats_buffer:
        best_kills = -1
        for uid in [str(u) for u in winning_team]:
            if stats_buffer.get(uid, {}).get('kills', 0) > best_kills:
                best_kills = stats_buffer[uid]['kills']; mvp_uid = uid
    map_name, match_id = match.get('map'), match.get('match_id') or gen_match_id()
    player_results, winners_card, losers_card, match_stats_record = {}, [], [], {}
    for uid in [str(u) for u in winning_team]:
        p = players.get(uid)
        if not p: continue
        s = stats_buffer.get(uid, {"kills":0,"deaths":0,"hs":0})
        is_mvp = (uid == mvp_uid)
        result = apply_match_result(p, True, s['kills'], s['deaths'], s.get('hs',0), is_mvp)
        apply_map_result(p, map_name, True)
        player_results[uid] = {**result, "is_winner": True, "mvp": is_mvp}
        match_stats_record[uid] = s
        winners_card.append({"tag":p['tag'], "kd":f"{s['kills']}/{s['deaths']}", "mvp":is_mvp, "calibrating":result['calibrating'], "delta":result['delta'], "elo":result['new_elo'] or p['elo']})
    for uid in [str(u) for u in losing_team]:
        p = players.get(uid)
        if not p: continue
        s = stats_buffer.get(uid, {"kills":0,"deaths":0,"hs":0})
        result = apply_match_result(p, False, s['kills'], s['deaths'], s.get('hs',0), False)
        apply_map_result(p, map_name, False)
        player_results[uid] = {**result, "is_winner": False, "mvp": False}
        match_stats_record[uid] = s
        losers_card.append({"tag":p['tag'], "kd":f"{s['kills']}/{s['deaths']}", "calibrating":result['calibrating'], "delta":result['delta'], "elo":result['new_elo'] or p['elo']})
    await save_players(players)
    await append_history({"match_id":match_id, "map":map_name, "all_players":[str(u) for u in match.get('players',[])], "winners":[str(u) for u in winning_team], "losers":[str(u) for u in losing_team], "stats":match_stats_record, "mvp":mvp_uid, "timestamp":datetime.now(MOSCOW_TZ).isoformat()})
    pending = await load_pending()
    pending[match_id] = {"match_id":match_id, "map_name":map_name, "player_results":player_results, "status":"awaiting_review", "match_photo":context.user_data.get('match_photo')}
    await save_pending(pending)
    if ADMIN_CHAT_ID:
        if context.user_data.get('match_photo'):
            await safe_send(context.bot, ADMIN_CHAT_ID, f"📸 Скриншот результата матча {match_id}", photo=context.user_data['match_photo'])
        await safe_send(context.bot, ADMIN_CHAT_ID, f"📋 Матч на проверку\nID: {match_id}\nКарта: {map_name}\n\n🔵 ПОБЕДА:\n" + "\n".join(f"🏆 {p['tag']} ({p['kd']})" for p in winners_card) + "\n\n🔴 ПОРАЖЕНИЕ:\n" + "\n".join(f"❌ {p['tag']} ({p['kd']})" for p in losers_card), reply_markup=kb_admin_review(match_id))
    context.user_data['stats_mode'], context.user_data['stats_buffer'], context.user_data['match'], context.user_data['match_id'], context.user_data['veto'], context.user_data['match_photo'] = False, {}, None, None, None, None
    await update.effective_message.reply_text("✅ Результат отправлен админам на проверку.\nКак только подтвердят — получишь уведомление.")

# ===== ЗАПУСК МАТЧА =====
def find_subset_with_sum(groups, target):
    n = len(groups)
    sizes = [len(g) for g in groups]
    def backtrack(i, rem, chosen):
        if rem == 0: return chosen
        if i >= n or rem < 0: return None
        res = backtrack(i+1, rem-sizes[i], chosen+[i])
        if res is not None: return res
        return backtrack(i+1, rem, chosen)
    return backtrack(0, target, [])
def build_teams_with_parties(players_list, parties):
    groups, seen = [], set()
    for uid in players_list:
        if uid in seen: continue
        lid, party = find_party_of(parties, uid)
        if party and all(m in players_list for m in party["members"]):
            group = [m for m in party["members"] if m in players_list]
            groups.append(group); seen.update(group)
        else: groups.append([uid]); seen.add(uid)
    random.shuffle(groups)
    total, team_size = sum(len(g) for g in groups), len(players_list)//2
    chosen = find_subset_with_sum(groups, team_size)
    if chosen is None:
        team_a, team_b = [], []
        for group in sorted(groups, key=len, reverse=True):
            if len(team_a) + len(group) <= team_size: team_a.extend(group)
            elif len(team_b) + len(group) <= (total - team_size): team_b.extend(group)
            else:
                for m in group:
                    (team_a if len(team_a) < team_size else team_b).append(m)
        return team_a, team_b
    chosen_set = set(chosen)
    return [uid for i in chosen for uid in groups[i]], [uid for i,g in enumerate(groups) if i not in chosen_set for uid in g]
async def start_match(platform, players_list, context):
    parties, players = await load_parties(), await load_players()
    team_a, team_b = build_teams_with_parties(players_list.copy(), parties)
    def top_elo(team): return max(team, key=lambda uid: players.get(str(uid),{}).get('elo',0))
    captain_a, captain_b, host = top_elo(team_a), top_elo(team_b), top_elo(players_list)
    match_id = gen_match_id()
    match = {"match_id":match_id, "platform":platform, "players":players_list, "team_a":team_a, "team_b":team_b, "captain_a":captain_a, "captain_b":captain_b, "host":host, "map":None, "status":"veto", "winner_team":None, "created_at":datetime.now().isoformat()}
    veto = start_veto(str(captain_a), str(captain_b))
    for uid in players_list:
        context.application.user_data[uid]['match'], context.application.user_data[uid]['match_id'], context.application.user_data[uid]['veto'] = match, match_id, veto
        await safe_send(context.bot, uid, f"🎮 Матч найден!\n\nID: {match_id}\nПлатформа: {platform}\nСобрано 10 игроков!\nТвоя команда: {'🔵 Команда А' if uid in team_a else '🔴 Команда Б'}{'\n🖥️ Ты хост этого матча!' if uid == host else ''}\n\nНачинается бан карт...")
    await safe_send(context.bot, captain_a, f"🗺️ ВЕТО\n\nХод: @{players.get(str(captain_a),{}).get('tag', captain_a)}\nДоступные карты:\n" + "\n".join(f"• {MAP_EMOJI.get(m,'')} {m}" for m in veto["pool"]), reply_markup=kb_veto(veto["pool"]))

# ===== HEALTH CHECK =====
app_flask = Flask(__name__)
start_time = time.time()
@app_flask.route('/health')
def health():
    return jsonify({'status':'ok', 'timestamp':datetime.now().isoformat(), 'players':len(asyncio.run(load_players())), 'uptime_seconds':int(time.time()-start_time)})
def run_health_server():
    try: app_flask.run(host='0.0.0.0', port=8080, debug=False, use_reloader=False)
    except Exception as e: logger.error(f"Health check не запущен: {e}")

# ===== ЗАПУСК =====
def ensure_event_loop():
    try: asyncio.get_event_loop()
    except RuntimeError: asyncio.set_event_loop(asyncio.new_event_loop())
def main():
    ensure_event_loop()
    os.makedirs("backups", exist_ok=True); os.makedirs("logs", exist_ok=True)
    threading.Thread(target=run_health_server, daemon=True).start()
    app = ApplicationBuilder().token(BOT_TOKEN).request(HTTPXRequest(connect_timeout=120, read_timeout=120, write_timeout=120, pool_timeout=120)).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("winner", winner_command))
    app.add_handler(CommandHandler("admin", admin_command))
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(PreCheckoutQueryHandler(pre_checkout_query))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment))
    app.add_error_handler(lambda u,c: logger.error(f"Update {u} вызвал ошибку: {c.error}", exc_info=True))
    logger.info("="*50); logger.info("🤖 Stranger Faceit 3.5 запущен!"); logger.info(f"👑 Админы: {ADMIN_IDS}"); logger.info(f"👑 Владелец: {OWNER_ID}"); logger.info(f"🏠 Общий чат: {GENERAL_CHAT_ID}"); logger.info(f"🔒 Админ-чат: {ADMIN_CHAT_ID}"); logger.info("="*50)
    app.run_polling(drop_pending_updates=True)
if __name__ == "__main__": main()

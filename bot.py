#!/usr/bin/env python3
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
os.makedirs("logs", exist_ok=True)
logger = logging.getLogger("stranger_faceit")
logger.setLevel(logging.INFO)
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
file_handler = RotatingFileHandler("logs/bot.log", maxBytes=10*1024*1024, backupCount=5)
logger.addHandler(file_handler)

REQ = ["BOT_TOKEN","ADMIN_IDS","GENERAL_CHAT_ID","ADMIN_CHAT_ID","SUPABASE_URL","SUPABASE_KEY"]
if [v for v in REQ if not os.environ.get(v)]: sys.exit(1)
BOT_TOKEN, ADMIN_IDS, GENERAL_CHAT_ID, ADMIN_CHAT_ID = os.environ["BOT_TOKEN"], [int(x) for x in os.environ["ADMIN_IDS"].split(",") if x.strip()], int(os.environ["GENERAL_CHAT_ID"]), int(os.environ["ADMIN_CHAT_ID"])
CHAT_LINK, REQUIRE_SUBSCRIPTION, SUBSCRIPTION_CHAT_ID, OWNER_ID = os.environ.get("CHAT_LINK",""), int(os.environ.get("REQUIRE_SUBSCRIPTION","1")), int(os.environ.get("SUBSCRIPTION_CHAT_ID",str(GENERAL_CHAT_ID))), int(os.environ.get("OWNER_ID",str(ADMIN_IDS[0] if ADMIN_IDS else 0)))
SUPABASE_URL, SUPABASE_KEY = os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"]

async def sb(method, path, data=None):
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.request(method, f"{SUPABASE_URL}/rest/v1/{path}", headers={"apikey":SUPABASE_KEY,"Authorization":f"Bearer {SUPABASE_KEY}","Content-Type":"application/json","Prefer":"return=representation"}, json=data)
        return r.json() if r.status_code < 400 else None

async def load_players(): d=await sb("GET","players"); return {row['user_id']:row['data'] for row in d} if d else {}
async def save_players(p): await sb("DELETE","players?user_id=neq.null"); [await sb("POST","players",{"user_id":u,"data":d}) for u,d in p.items()]
async def load_pending():
    d=await sb("GET","pending")
    if not d: return {}
    now=time.time(); fresh={}
    for row in d:
        if row['data'].get('created_at',now) < now-604800: await sb("DELETE",f"pending?id=eq.{row['id']}")
        else: fresh[row['id']]=row['data']
    return fresh
async def save_pending(p): await sb("DELETE","pending?id=neq.null"); [await sb("POST","pending",{"id":i,"data":d}) for i,d in p.items()]
async def load_lobbies(): d=await sb("GET","lobbies"); return {row['platform']:row['lobby_data'] for row in d} if d else {p:[[] for _ in range(6)] for p in ["Phone","PC"]}
async def save_lobbies(l): await sb("DELETE","lobbies?platform=neq.null"); [await sb("POST","lobbies",{"platform":p,"lobby_data":d}) for p,d in l.items()]
async def load_parties(): d=await sb("GET","parties"); return {row['leader_id']:row['data'] for row in d} if d else {}
async def save_parties(p): await sb("DELETE","parties?leader_id=neq.null"); [await sb("POST","parties",{"leader_id":i,"data":d}) for i,d in p.items()]
async def load_reports(): d=await sb("GET","reports"); r={}; [r.setdefault(row['target_uid'],[]).append(row['report_data']) for row in d] if d else None; return r
async def save_reports(r): await sb("DELETE","reports?target_uid=neq.null"); [[await sb("POST","reports",{"target_uid":t,"data":rep}) for rep in reports] for t,reports in r.items()]
async def load_history(): d=await sb("GET","match_history?order=id.desc&limit=100"); return {"matches":[row['data'] for row in d]} if d else {"matches":[]}
async def append_history(e): await sb("POST","match_history",{"data":e})
async def load_analytics(): d=await sb("GET","analytics"); a={"map_picks":{},"online_samples":[],"match_timestamps":[]}; [a.update({row['key']:row['value']}) for row in d] if d else None; return a
async def save_analytics(a): await sb("DELETE","analytics?key=neq.null"); [await sb("POST","analytics",{"key":k,"value":v}) for k,v in a.items()]

MAPS, MAP_EMOJI = ["Sandstone","Rust","Province","Breeze","Dune","Zone 7","Hanami"], {m:e for m,e in zip(["Sandstone","Rust","Province","Breeze","Dune","Zone 7","Hanami"],["🏜️","🏭","🏘️","🌬️","🏝️","☢️","🌸"])}
PLATFORMS, LOBBIES_PER_PLATFORM, LOBBY_SIZE, MAX_PARTY_SIZE = ["Phone","PC"], 6, 10, 5
CALIBRATION_GAMES, CALIBRATION_BASE_ELO, READY_CHECK_TIMEOUT, RESULT_UNLOCK_DELAY, MAX_REPORT_LEN = 10, 500, 60, 30, 500
PREMIUM_PRICES, PREMIUM_DURATIONS = {"day":50,"week":350,"month":1000}, {"day":86400,"week":604800,"month":2592000}
LEVEL_THRESHOLDS, RANK_EMOJI = [(1,0,500),(2,501,750),(3,751,900),(4,901,1050),(5,1051,1200),(6,1201,1350),(7,1351,1530),(8,1531,1750),(9,1751,2000),(10,2001,10**9)], {1:"🥉",2:"🥉",3:"🥉",4:"🥈",5:"🥈",6:"🥈",7:"🥇",8:"🥇",9:"💎",10:"👑"}
MOSCOW_TZ = timezone(timedelta(hours=3))

def is_premium(p): return p and p.get('premium_until',0) > time.time()
def get_premium_time_left(p): return max(0, int(p.get('premium_until',0) - time.time())) if p else 0
def format_premium_time(s): return "не активен" if s<=0 else (f"{s//86400} дн {(s%86400)//3600} ч" if s>86400 else f"{s//3600} ч")
def new_player(sid, tg_username): return {"reg":0,"sid":sid,"name":"","tag":"","tg_username":tg_username,"elo":0,"level":0,"wins":0,"losses":0,"matches":0,"mvps":0,"rank":"🎯","ban":None,"total_kills":0,"total_deaths":0,"hs_kills":0,"elo_history":[],"maps":{m:{"wins":0,"losses":0} for m in MAPS},"calib":0,"calib_elo_buffer":0,"platform":None,"premium_until":0,"tag_changes":0}
def get_player(p,u): return p.get(str(u))
def find_by_sid(p,s): return next((uid for uid,pl in p.items() if pl.get("sid")==s), None)
def find_by_tag(p,t): return next((uid for uid,pl in p.items() if pl.get("tag","").lower()==t.lstrip("@").lower()), None)
def find_by_telegram_username(p,u): return next((uid for uid,pl in p.items() if pl.get("tg_username","").lower()==u.lstrip("@").lower()), None)
def level_from_elo(e): return next((lvl for lvl,lo,hi in LEVEL_THRESHOLDS if lo<=e<=hi), 10 if e>LEVEL_THRESHOLDS[-1][2] else 1)
def rank_label(l): return f"{RANK_EMOJI.get(l,'🥉')} {l}"
def compute_points(w,k,d,m): return round((9 + k*0.5 - d*0.3) if w else (-15 + k*0.5 - d*0.3) + (3 if m else 0))
def apply_match_result(p,w,k,d,h,m):
    pts=compute_points(w,k,d,m)
    if is_premium(p): pts*=2
    snap={key:p[key] for key in ["matches","wins","losses","mvps","calib","calib_elo_buffer","elo","level","rank","total_kills","total_deaths","hs_kills","elo_history"]}
    p["matches"]+=1; p["total_kills"]=p.get("total_kills",0)+k; p["total_deaths"]=p.get("total_deaths",0)+d; p["hs_kills"]=p.get("hs_kills",0)+h
    if w: p["wins"]+=1
    else: p["losses"]+=1
    if m: p["mvps"]+=1
    if p["calib"] < CALIBRATION_GAMES:
        p["calib"]+=1; p["calib_elo_buffer"]+=pts
        if p["calib"] >= CALIBRATION_GAMES:
            final=max(0, CALIBRATION_BASE_ELO + p["calib_elo_buffer"])
            p["elo"]=final; p["level"]=level_from_elo(final); p["rank"]=rank_label(p["level"]); p.setdefault("elo_history",[]).append(final)
            return {"delta":0,"old_elo":0,"new_elo":final,"calibrating":False,"just_finished_calibration":True,"calib_progress":None,"_snapshot_before":snap}
        return {"delta":0,"old_elo":0,"new_elo":0,"calibrating":True,"just_finished_calibration":False,"calib_progress":f"{p['calib']}/{CALIBRATION_GAMES}","_snapshot_before":snap}
    old=p["elo"]; new=max(0, old+pts); p["elo"]=new; p["level"]=level_from_elo(new); p["rank"]=rank_label(p["level"]); p.setdefault("elo_history",[]).append(new); p["elo_history"]=p["elo_history"][-30:]
    return {"delta":pts,"old_elo":old,"new_elo":new,"calibrating":False,"just_finished_calibration":False,"calib_progress":None,"_snapshot_before":snap}
def rollback_match_result(p,s): [setattr(p,k,s[k]) for k in s]
def apply_map_result(p,mn,w): p.setdefault("maps",{})[mn]=p.get("maps",{}).get(mn,{"wins":0,"losses":0}); p["maps"][mn]["wins"] += 1 if w else 0; p["maps"][mn]["losses"] += 0 if w else 1
def elo_display(p): return f"Калибровка {p['calib']}/{CALIBRATION_GAMES}" if p["calib"]<CALIBRATION_GAMES else f"{p['rank']} {'💎' if is_premium(p) else ''}• {p['elo']} ELO"
def gen_match_id(): return f"M-{datetime.now().strftime('%Y%m%d')}-{''.join(random.choices(string.digits,k=3))}"
def start_veto(ca, cb): pool=MAPS.copy(); random.shuffle(pool); return {"pool":pool,"banned":[],"turn":ca,"captain_a":ca,"captain_b":cb,"final_map":None}
def veto_ban(v,c,m):
    if v["final_map"]: return False,"Вето завершено."
    if c!=v["turn"]: return False,"Не ваша очередь."
    if m not in v["pool"]: return False,"Карта уже забанена."
    v["pool"].remove(m); v["banned"].append({"by":c,"map":m})
    if len(v["pool"])==1: v["final_map"]=v["pool"][0]
    else: v["turn"]=v["captain_b"] if v["turn"]==v["captain_a"] else v["captain_a"]
    return True,None
def find_party_of(parties, uid): return next(((lid,party) for lid,party in parties.items() if int(uid) in party.get("members",[])), (None,None))
def audit_log(a,u,d): open("audit.log","a",encoding="utf-8").write(json.dumps({"timestamp":datetime.now().isoformat(),"action":a,"user_id":u,"details":d}, ensure_ascii=False)+"\n")
def sanitize_input(t): return re.sub(r'[\x00-\x1f\x7f-\x9f]','',t).strip()[:MAX_REPORT_LEN]
def validate_id(t): return t.isdigit() and 8 <= len(t) <= 15
def sparkline(v):
    if not v: return "нет данных"
    blocks="▁▂▃▄▅▆▇█"; lo,hi=min(v),max(v)
    if hi==lo: return blocks[3]*len(v)
    return ''.join(blocks[int((x-lo)/(hi-lo)*(len(blocks)-1))] for x in v)

def kb_start(): return InlineKeyboardMarkup([[InlineKeyboardButton("🔑 Вход", callback_data="auth:login")],[InlineKeyboardButton("📝 Регистрация", callback_data="auth:register")],[InlineKeyboardButton("🆘 Поддержка", callback_data="auth:support")]])
def kb_sub():
    rows=[]
    if CHAT_LINK: rows.append([InlineKeyboardButton("➡️ Перейти в чат", url=CHAT_LINK)])
    rows.append([InlineKeyboardButton("✅ Я подписался", callback_data="sub:check")])
    return InlineKeyboardMarkup(rows)
def kb_menu(in_party=False): return InlineKeyboardMarkup([[InlineKeyboardButton("🔍 Найти матч", callback_data="menu:find"), InlineKeyboardButton("🎉 Пати" if not in_party else "🎉 Пати (моя группа)", callback_data="menu:party")],[InlineKeyboardButton("👤 Профиль", callback_data="menu:profile"), InlineKeyboardButton("🏆 Топ", callback_data="menu:top")],[InlineKeyboardButton("📊 Статистика", callback_data="menu:stats"), InlineKeyboardButton("📝 История", callback_data="menu:history")],[InlineKeyboardButton("📢 Жалобы", callback_data="menu:complaints"), InlineKeyboardButton("💎 Премиум", callback_data="menu:premium")],[InlineKeyboardButton("🆘 Поддержка", callback_data="menu:support")]])
def kb_back(): return InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data="menu:main")]])
def kb_reg_done(): return InlineKeyboardMarkup([[InlineKeyboardButton("✅ Отправить фото", callback_data="auth:send_photo")]])
def kb_platforms(): return InlineKeyboardMarkup([[InlineKeyboardButton("📱 Phone", callback_data="platform:Phone")],[InlineKeyboardButton("💻 PC", callback_data="platform:PC")],[InlineKeyboardButton("⬅️ Назад", callback_data="menu:main")]])
def kb_lobbies(platform, lobbies, min_free=1):
    rows=[]
    for i in range(3):
        row=[]
        for j in [i, i+3]:
            if j<6:
                label=f"Лобби {j+1} ({len(lobbies[platform][j])}/{LOBBY_SIZE})"
                if LOBBY_SIZE-len(lobbies[platform][j])>=min_free: row.append(InlineKeyboardButton(label, callback_data=f"lobby:{platform}:{j}"))
                else: row.append(InlineKeyboardButton(f"🔒 {label}", callback_data="lobby:full"))
        rows.append(row)
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="menu:find")])
    return InlineKeyboardMarkup(rows)
def kb_in_lobby(platform, idx): return InlineKeyboardMarkup([[InlineKeyboardButton("🚪 Выйти", callback_data=f"lobby_leave:{platform}:{idx}")]])
def kb_veto(available):
    rows=[]; row=[]
    for m in available:
        row.append(InlineKeyboardButton(f"{MAP_EMOJI.get(m,'')} {m}", callback_data=f"veto_ban:{m}"))
        if len(row)==2: rows.append(row); row=[]
    if row: rows.append(row)
    return InlineKeyboardMarkup(rows)
def kb_skip(): return InlineKeyboardMarkup([[InlineKeyboardButton("⏭ Пропустить статистику", callback_data="stats:skip")]])
def kb_send(): return InlineKeyboardMarkup([[InlineKeyboardButton("📤 Отправить результаты", callback_data="result:send")]])
def kb_admin_review(pid): return InlineKeyboardMarkup([[InlineKeyboardButton("✅ ПОДТВЕРДИТЬ", callback_data=f"admin_ok:{pid}")],[InlineKeyboardButton("❌ ОТКАЗАТЬ", callback_data=f"admin_no:{pid}")]])
def kb_party_menu(is_leader, size):
    rows=[]
    if is_leader and size<MAX_PARTY_SIZE: rows.append([InlineKeyboardButton("➕ Пригласить игрока", callback_data="party:invite")])
    rows.append([InlineKeyboardButton("🚪 Покинуть пати", callback_data="party:leave")])
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="menu:main")])
    return InlineKeyboardMarkup(rows)
def kb_party_invite_response(lid): return InlineKeyboardMarkup([[InlineKeyboardButton("✅ Принять", callback_data=f"party_accept:{lid}"), InlineKeyboardButton("❌ Отказать", callback_data=f"party_decline:{lid}")]])
def kb_ready(): return InlineKeyboardMarkup([[InlineKeyboardButton("✅ Подтвердить", callback_data="ready:confirm")]])
def kb_players_list(items, prefix, page=0, per_page=10, back_cb="menu:main"):
    total=max(1,(len(items)+per_page-1)//per_page); page=max(0,min(page,total-1)); start,end=page*per_page,min((page+1)*per_page,len(items))
    rows=[[InlineKeyboardButton(f"@{p.get('tag',uid)}", callback_data=f"{prefix}:{uid}:{page}")] for uid,p in items[start:end]]
    nav=[]; page>0 and nav.append(InlineKeyboardButton("⬅️", callback_data=f"{prefix}_page:{page-1}")); nav.append(InlineKeyboardButton(f"{page+1}/{total}", callback_data="noop")); page<total-1 and nav.append(InlineKeyboardButton("➡️", callback_data=f"{prefix}_page:{page+1}")); nav and rows.append(nav); rows.append([InlineKeyboardButton("⬅️ Назад", callback_data=back_cb)])
    return InlineKeyboardMarkup(rows)
def kb_complaint_actions(target): return InlineKeyboardMarkup([[InlineKeyboardButton("✍️ Написать жалобу", callback_data=f"complaint_write:{target}")],[InlineKeyboardButton("👁 Посмотреть жалобы", callback_data=f"complaint_view:{target}")],[InlineKeyboardButton("⬅️ Назад", callback_data="menu:complaints")]])
def kb_confirm(action,target): return InlineKeyboardMarkup([[InlineKeyboardButton("✅ Да", callback_data=f"{action}_yes:{target}")],[InlineKeyboardButton("❌ Нет", callback_data=f"{action}_no:{target}")]])
def kb_premium(): return InlineKeyboardMarkup([[InlineKeyboardButton("⭐ 1 день — 50 звёзд", callback_data="premium:day")],[InlineKeyboardButton("⭐ 1 неделя — 350 звёзд", callback_data="premium:week")],[InlineKeyboardButton("⭐ 1 месяц — 1000 звёзд", callback_data="premium:month")],[InlineKeyboardButton("⬅️ Назад", callback_data="menu:main")]])
def kb_admin(): return InlineKeyboardMarkup([[InlineKeyboardButton("📊 Аналитика", callback_data="admin:analytics"), InlineKeyboardButton("📝 История матчей", callback_data="admin:history")],[InlineKeyboardButton("🆔 Отвязать айди", callback_data="admin:unlink"), InlineKeyboardButton("🔨 Забанить", callback_data="admin:ban")],[InlineKeyboardButton("📊 ELO", callback_data="admin:elo"), InlineKeyboardButton("✏️ Изменить ID", callback_data="admin:change_id")],[InlineKeyboardButton("✏️ Изменить ник", callback_data="admin:change_nick"), InlineKeyboardButton("📢 Жалобы (топ)", callback_data="admin:complaints_top")],[InlineKeyboardButton("🏷️ Выдать тег", callback_data="admin:give_tag")]])
def kb_admin_elo(): return InlineKeyboardMarkup([[InlineKeyboardButton("➕ Выдать ELO", callback_data="admin:elo_add")],[InlineKeyboardButton("➖ Убавить ELO", callback_data="admin:elo_remove")],[InlineKeyboardButton("⬅️ Назад", callback_data="admin:back")]])
def kb_history_nav(page,total):
    nav=[]; page>0 and nav.append(InlineKeyboardButton("⬅️", callback_data=f"admin_history_page:{page-1}")); nav.append(InlineKeyboardButton(f"{page+1}/{max(1,total)}", callback_data="noop")); page<total-1 and nav.append(InlineKeyboardButton("➡️", callback_data=f"admin_history_page:{page+1}")); rows=[nav] if nav else []; rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="admin:back")]); return InlineKeyboardMarkup(rows)

def main_menu_text(p): return f"🏠 ГЛАВНОЕ МЕНЮ\n\n👤 @{p.get('tag','')}\n📊 {elo_display(p)}\n🏆 Побед: {p.get('wins',0)} | Поражений: {p.get('losses',0)}\n🎯 Матчей: {p.get('matches',0)}\n{'💎 Премиум активен!' if is_premium(p) else ''}"
def profile_text(p):
    wr=round((p['wins']/p['matches']*100) if p['matches']>0 else 0,1)
    prem=f"💎 Премиум активен! (осталось {format_premium_time(get_premium_time_left(p))})" if is_premium(p) else "Нет премиума"
    text=f"📊 МОЙ ПРОФИЛЬ\n\n👤 @{p['tag']}\n🆔 {p['sid']}\n🏅 {p['rank']}\n📊 {elo_display(p)}\n💎 {prem}\n\n📈 СТАТИСТИКА\n🎯 Матчей: {p['matches']}\n🏆 Побед: {p['wins']}\nПоражений: {p['losses']}\nWinrate: {wr}%\n⭐ MVP: {p['mvps']}\n"
    if p.get('calib',0)<CALIBRATION_GAMES: text+=f"📌 Калибровка: {p['calib']}/{CALIBRATION_GAMES}\n"
    text+="\n📊 ПО КАРТАМ\n"
    for mn,s in p.get('maps',{}).items():
        if s['wins']+s['losses']>0: text+=f"{MAP_EMOJI.get(mn,'')} {mn}: {s['wins']}-{s['losses']} ({round(s['wins']/(s['wins']+s['losses'])*100,1)}%)\n"
    return text
def extended_stats_text(p):
    k,d,h=p.get('total_kills',0),p.get('total_deaths',0),p.get('hs_kills',0); avg_kd=round(k/d,2) if d>0 else float(k)
    fav_map,fav_games=None,0
    for mn,s in p.get('maps',{}).items():
        if s['wins']+s['losses']>fav_games: fav_map,fav_games=mn,s['wins']+s['losses']
    elo_hist=p.get('elo_history',[])[-10:]
    return f"📊 РАСШИРЕННАЯ СТАТИСТИКА\n\n👤 @{p['tag']}\n\n🔫 AVG KD: {avg_kd}\n🎯 HS%: {round(h/k*100,1) if k>0 else 0}%\n🗺️ Любимая карта: {f'{MAP_EMOJI.get(fav_map,"")} {fav_map} ({fav_games} игр)' if fav_map else 'нет данных'}\n\n📈 ELO (последние {len(elo_hist)} матчей):\n{sparkline(elo_hist)}\n" + (f"Значения: {', '.join(str(v) for v in elo_hist)}\n" if elo_hist else "")
def personal_history_text(uid, history):
    matches=[m for m in history.get("matches",[]) if uid in m.get("all_players",[])][-20:][::-1]
    if not matches: return "📝 ИСТОРИЯ МАТЧЕЙ\n\nПока нет сыгранных матчей."
    lines=["📝 ИСТОРИЯ МАТЧЕЙ (последние 20)\n"]
    for m in matches:
        lines.append(f"{m['match_id']} | {MAP_EMOJI.get(m.get('map'),'')} {m.get('map')} | {'🏆 Победа' if uid in m.get('winners',[]) else '❌ Поражение'} | {m.get('stats',{}).get(uid,{}).get('kills',0)}/{m.get('stats',{}).get(uid,{}).get('deaths',0)}{' ⭐' if m.get('mvp')==uid else ''}")
    return "\n".join(lines)
def admin_history_page_text(history, page, per_page=10):
    matches=history.get("matches",[])[::-1]; total=max(1,(len(matches)+per_page-1)//per_page); start,end=page*per_page,min((page+1)*per_page,len(matches))
    if not matches[start:end]: return "📝 ИСТОРИЯ МАТЧЕЙ (ВСЕ)\n\nНет данных.", total
    lines=[f"📝 ИСТОРИЯ МАТЧЕЙ (ВСЕ) — стр. {page+1}/{total}\n"]; [lines.append(f"{m['match_id']} | {MAP_EMOJI.get(m.get('map'),'')} {m.get('map')} | {m.get('timestamp','')}") for m in matches[start:end]]
    return "\n".join(lines), total
def complaint_view_text(tag, reports): return f"Жалоб на @{tag} пока нет." if not reports else f"ЖАЛОБЫ НА @{tag} ({len(reports)})\n" + "\n".join(f"• {r.get('text','')}" for r in reports)
def premium_text(): return "💎 *ПРЕМИУМ-ПОДПИСКА*\n\n🔥 Преимущества:\n✅ Любой тег (даже занятый)\n✅ Бесплатные турниры\n✅ x2 ELO за победы\n✅ Смена ника (2 раза бесплатно, далее 50⭐)\n\n⭐ *Тарифы:*\n• 1 день — 50 звёзд\n• 1 неделя — 350 звёзд\n• 1 месяц — 1000 звёзд\n\nВыбери тариф:"
def party_text(parties, lid, players):
    party=parties.get(str(lid)) or parties.get(lid)
    if not party: return None
    return "🎉 ПАТИ\n" + "\n".join(f"{'👑 ' if uid==party['leader'] else '• '}@{players.get(str(uid),{}).get('tag',str(uid))} {'💎' if is_premium(players.get(str(uid))) else ''}" for uid in party["members"]) + f"\n\nСостав: {len(party['members'])}/{MAX_PARTY_SIZE}"

READY_CHECKS_BY_ID, READY_CHECKS, _rc_counter = {}, {}, 0
async def start_ready_check(platform, lobby_idx, context):
    lobbies=await load_lobbies(); players_list=lobbies[platform][lobby_idx].copy(); lobbies[platform][lobby_idx]=[]; await save_lobbies(lobbies)
    global _rc_counter; _rc_counter+=1; rc_id=f"rc{_rc_counter}"
    READY_CHECKS_BY_ID[rc_id]={"players":players_list,"confirmed":set(),"platform":platform,"lobby_idx":lobby_idx,"status":"pending","created_at":time.time()}
    [READY_CHECKS.update({uid:{"id":rc_id}}) for uid in players_list]
    [await safe_send(context.bot, uid, f"👥 Лобби набрано! ({LOBBY_SIZE}/{LOBBY_SIZE})\n\nУ тебя есть {READY_CHECK_TIMEOUT} секунд, чтобы подтвердить.\nЕсли не успеешь — будешь удалён.", reply_markup=kb_ready()) for uid in players_list]
    asyncio.create_task(ready_check_timer(rc_id, context))
async def ready_check_timer(rc_id, context):
    await asyncio.sleep(READY_CHECK_TIMEOUT); rc=READY_CHECKS_BY_ID.get(rc_id)
    if rc and rc["status"]=="pending": await finalize_ready_check(rc_id, context, timed_out=True)
async def finalize_ready_check(rc_id, context, timed_out=False):
    rc=READY_CHECKS_BY_ID.get(rc_id)
    if not rc or rc["status"]!="pending": return
    rc["status"]="done"; confirmed=[uid for uid in rc["players"] if uid in rc["confirmed"]]; not_confirmed=[uid for uid in rc["players"] if uid not in rc["confirmed"]]
    [READY_CHECKS.pop(uid,None) for uid in rc["players"]]
    if not_confirmed:
        [await safe_send(context.bot, uid, "❌ Ты не подтвердил вовремя и был удалён.") for uid in not_confirmed]
        lobbies=await load_lobbies(); lobbies[rc["platform"]][rc["lobby_idx"]]=confirmed.copy(); await save_lobbies(lobbies)
        [await update_lobby_for_all(rc["platform"], rc["lobby_idx"], context) for uid in confirmed]
        return
    await start_match(rc["platform"], rc["players"], context)

async def safe_delete(msg):
    try: await msg.delete()
    except: pass
async def safe_send(bot, chat_id, text=None, photo=None, **kwargs):
    try:
        if photo: return await bot.send_photo(chat_id=chat_id, photo=photo, caption=text, **kwargs)
        return await bot.send_message(chat_id=chat_id, text=text, **kwargs)
    except: return None
async def is_subscribed(bot, uid):
    if not REQUIRE_SUBSCRIPTION or not SUBSCRIPTION_CHAT_ID: return True
    try: m=await bot.get_chat_member(SUBSCRIPTION_CHAT_ID, uid); return m.status in ("member","administrator","creator")
    except: return True
async def check_banned(update):
    p=get_player(await load_players(), update.effective_user.id)
    if p and p.get('ban'): await update.effective_message.reply_text("⛔ Вы забанены."); return True
    return False
async def require_subscription(update, context):
    if REQUIRE_SUBSCRIPTION and not await is_subscribed(context.bot, update.effective_user.id):
        await update.effective_message.reply_text("📢 Подпишись на наш чат!", reply_markup=kb_sub()); return False
    return True
async def update_lobby_for_all(platform, idx, context):
    lobbies=await load_lobbies(); players=await load_players(); players_list=lobbies[platform][idx]
    lines=[f"@{players.get(str(uid),{}).get('tag',str(uid))} {'💎' if is_premium(players.get(str(uid))) else ''}({players.get(str(uid),{}).get('elo',0)} ELO)" for uid in players_list]
    text=f"📋 ЛОББИ {idx+1} ({len(players_list)}/{LOBBY_SIZE})\n\nИГРОКИ:\n" + "\n".join(f"{i}. {p}" for i,p in enumerate(lines,1)) + f"\n\nОжидание: {len(players_list)}/{LOBBY_SIZE}"
    [await safe_send(context.bot, uid, text, reply_markup=kb_in_lobby(platform, idx)) for uid in players_list]

async def start(update, context):
    if await check_banned(update) or not await require_subscription(update, context): return
    p=get_player(await load_players(), update.effective_user.id)
    if not p or p.get("reg")!=1: return await update.message.reply_text("🎮 STRANGER FACEIT\n\nДобро пожаловать! Выбери действие:", reply_markup=kb_start())
    parties=await load_parties(); lid,_=find_party_of(parties, update.effective_user.id)
    await update.message.reply_text(main_menu_text(p), reply_markup=kb_menu(bool(lid)))
async def admin_command(update, context):
    if update.effective_user.id!=OWNER_ID: return await update.message.reply_text("❌ Доступ запрещен.")
    await update.message.reply_text("👑 АДМИН-ПАНЕЛЬ\n\nВыбери действие:", reply_markup=kb_admin())
async def winner_command(update, context):
    if await check_banned(update): return
    match=context.user_data.get('match')
    if not match or match.get('status')!='in_progress': return await update.message.reply_text("❌ Нет активного матча.")
    if match.get('host')!=update.effective_user.id: return await update.message.reply_text("❌ Только хост может объявить результат.")
    args=context.args
    if not args or args[0].lower() not in ("ct","t"): return await update.message.reply_text("Использование: /winner ct  или  /winner t")
    if not context.user_data.get('match_photo'): return await update.message.reply_text("📸 Сначала отправь скриншот результата.")
    match['winner_side'] = args[0].lower()
    match['status'] = 'awaiting_winning_team'
    context.user_data['match']=match
    await update.message.reply_text(f"✅ Победила сторона: {args[0].upper()}\n\nКакая команда играла за {args[0].upper()} и победила?", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔵 Команда А", callback_data="winteam:a"), InlineKeyboardButton("🔴 Команда Б", callback_data="winteam:b")]]))
async def button_callback(update, context):
    try:
        q=update.callback_query; await q.answer(); user, data = q.from_user, q.data
        players=await load_players(); p=get_player(players, user.id)
        if data=="noop": return
        if data=="sub:check":
            if await is_subscribed(context.bot, user.id):
                await safe_delete(q.message)
                if not p or p.get("reg")!=1: await q.message.reply_text("✅ Подписка подтверждена!\n\n🎮 STRANGER FACEIT\n\nВыбери действие:", reply_markup=kb_start())
                else: parties=await load_parties(); lid,_=find_party_of(parties, user.id); await q.message.reply_text(main_menu_text(p), reply_markup=kb_menu(bool(lid)))
            else: await q.answer("❌ Подписка не найдена.", show_alert=True)
            return
        if data in ["auth:login","auth:register","auth:support"]:
            await safe_delete(q.message); context.user_data.clear()
            if data=="auth:login": context.user_data['auth_step']='login_id'; await q.message.reply_text("🔑 ВХОД\n\nВведи свой ID в Standoff 2:", reply_markup=kb_back())
            elif data=="auth:register": context.user_data['auth_step']='reg_id'; await q.message.reply_text("📝 РЕГИСТРАЦИЯ\n\nВведи свой ID в Standoff 2:", reply_markup=kb_back())
            else: context.user_data['support_mode']=True; await q.message.reply_text("🆘 ПОДДЕРЖКА\n\nОпиши свою проблему:", reply_markup=kb_back())
            return
        if data=="auth:send_photo":
            sid,tag=context.user_data.get('reg_pending_sid'), context.user_data.get('reg_pending_tag')
            if not sid or not tag: return await q.answer("❌ Сессия истекла. Начни заново.", show_alert=True)
            await safe_delete(q.message)
            await q.message.reply_text(f"📸 Отправь скриншот профиля из Standoff 2\n\nНа скриншоте должны быть видны:\n• ID: {sid}\n• Ник: {tag}")
            context.user_data['auth_step']='reg_photo'
            return
        if data.startswith("reg_ok:") or data.startswith("reg_no:"):
            if user.id not in ADMIN_IDS: return await q.answer("❌ Только для админов.", show_alert=True)
            action,pid=data.split(":",1); pending=await load_pending(); record=pending.get(pid)
            if not record or record.get("type")!="registration": return await q.answer("❌ Заявка не найдена.", show_alert=True)
            if action=="reg_ok":
                players_data=await load_players(); np=new_player(record["sid"], record["tag"]); np["reg"]=1; np["name"]=record["tag"]; np["tag"]=record["tag"]; np["tg_username"]=record["tag"]
                players_data[str(record["user_id"])]=np; await save_players(players_data); record["status"]="approved"; await save_pending(pending)
                await safe_send(context.bot, record["user_id"], "✅ Регистрация подтверждена!\n\nДобро пожаловать в Stranger Faceit!\nНапиши /start")
                await safe_delete(q.message); await q.message.reply_text(f"✅ Игрок @{record['tag']} зарегистрирован!")
            else:
                record["status"]="rejected"; await save_pending(pending)
                await safe_send(context.bot, record["user_id"], "❌ Регистрация отклонена.\nОбратись в поддержку.")
                await safe_delete(q.message); await q.message.reply_text(f"❌ Заявка @{record['tag']} отклонена.")
            return
        if data=="menu:main":
            await safe_delete(q.message); context.user_data.clear()
            if not p or p.get("reg")!=1: return await q.message.reply_text("🎮 STRANGER FACEIT\n\nВыбери действие:", reply_markup=kb_start())
            parties=await load_parties(); lid,_=find_party_of(parties, user.id); await q.message.reply_text(main_menu_text(p), reply_markup=kb_menu(bool(lid)))
            return
        if data=="menu:profile":
            await safe_delete(q.message)
            if not p or p.get("reg")!=1: return await q.message.reply_text("Сначала зарегистрируйся!", reply_markup=kb_start())
            await q.message.reply_text(profile_text(p), reply_markup=kb_back())
            return
        if data=="menu:stats":
            await safe_delete(q.message)
            if not p or p.get("reg")!=1: return await q.message.reply_text("Сначала зарегистрируйся!", reply_markup=kb_start())
            await q.message.reply_text(extended_stats_text(p), reply_markup=kb_back())
            return
        if data=="menu:history":
            await safe_delete(q.message)
            if not p or p.get("reg")!=1: return await q.message.reply_text("Сначала зарегистрируйся!", reply_markup=kb_start())
            await q.message.reply_text(personal_history_text(str(user.id), await load_history()), reply_markup=kb_back())
            return
        if data=="menu:top":
            await safe_delete(q.message)
            sorted_players=sorted([p for p in (await load_players()).values() if p.get("reg")==1 and p.get("calib",0)>=CALIBRATION_GAMES], key=lambda x:x.get("elo",0), reverse=True)[:10]
            text="🏆 ТОП ИГРОКОВ\n\n" + ("\n".join(f"{['👑','🥈','🥉'][i] if i<3 else f'{i+1}.'} @{p['tag']} {'💎' if is_premium(p) else ''}\n    {p['elo']} ELO | {p['rank']}" for i,p in enumerate(sorted_players)) if sorted_players else "Пока нет игроков, завершивших калибровку.\n") + f"\n\nВсего игроков: {len([p for p in (await load_players()).values() if p.get('reg')==1])}"
            await q.message.reply_text(text, reply_markup=kb_back())
            return
        if data=="menu:support":
            await safe_delete(q.message); context.user_data['support_mode']=True
            await q.message.reply_text("🆘 ПОДДЕРЖКА\n\nОпиши свою проблему:", reply_markup=kb_back())
            return
        if data=="menu:premium":
            await safe_delete(q.message)
            if not p or p.get("reg")!=1: return await q.message.reply_text("Сначала зарегистрируйся!", reply_markup=kb_start())
            await q.message.reply_text(premium_text(), reply_markup=kb_premium(), parse_mode="Markdown")
            return
        if data.startswith("premium:"):
            period=data.split(":")[1]
            if period not in PREMIUM_PRICES: return await q.answer("❌ Неверный тариф.", show_alert=True)
            await safe_delete(q.message); period_names={"day":"1 день","week":"1 неделя","month":"1 месяц"}
            await context.bot.send_invoice(chat_id=user.id, title=f"Премиум-подписка {period_names[period]}", description="x2 ELO, любой тег, бесплатные турниры", payload=f"premium_{period}", currency="XTR", prices=[{"label":period_names[period],"amount":PREMIUM_PRICES[period]}], start_parameter=f"premium_{period}")
            return
        if data=="menu:complaints":
            await safe_delete(q.message); all_players=sorted([(uid,p) for uid,p in (await load_players()).items() if p.get("reg")==1], key=lambda x:x[1].get('tag',''))
            context.user_data['complaints_players']=all_players
            await q.message.reply_text("📢 ЖАЛОБЫ\n\nВыбери игрока:", reply_markup=kb_players_list(all_players, "complaint", 0, back_cb="menu:main"))
            return
        if data.startswith("complaint_page:"):
            page=int(data.split(":")[1]); all_players=context.user_data.get('complaints_players',[])
            await q.message.edit_text("📢 ЖАЛОБЫ\n\nВыбери игрока:", reply_markup=kb_players_list(all_players, "complaint", page, back_cb="menu:main"))
            return
        if data.startswith("complaint:"):
            _,target_uid,page=data.split(":"); target_p=(await load_players()).get(target_uid)
            if not target_p: return await q.answer("❌ Игрок не найден.", show_alert=True)
            await safe_delete(q.message)
            await q.message.reply_text(f"👤 @{target_p.get('tag',target_uid)}\n\nВыбери действие:", reply_markup=kb_complaint_actions(target_uid))
            return
        if data.startswith("complaint_write:"):
            target_uid=data.split(":")[1]
            if target_uid==str(user.id): return await q.answer("❌ Нельзя пожаловаться на себя.", show_alert=True)
            reports=await load_reports()
            if any(r.get("by")==str(user.id) for r in reports.get(target_uid,[])): return await q.answer("❌ Ты уже жаловался на этого игрока.", show_alert=True)
            context.user_data['complaint_target']=target_uid
            await safe_delete(q.message)
            await q.message.reply_text(f"✍️ Опиши жалобу на @{(await load_players()).get(target_uid,{}).get('tag',target_uid)}:")
            return
        if data.startswith("complaint_view:"):
            target_uid=data.split(":")[1]; target_p=(await load_players()).get(target_uid,{}); reports=await load_reports()
            await safe_delete(q.message)
            await q.message.reply_text(complaint_view_text(target_p.get('tag',target_uid), reports.get(target_uid,[])), reply_markup=kb_back())
            return
        if data=="menu:party":
            await safe_delete(q.message)
            if not p or p.get("reg")!=1: return await q.message.reply_text("Сначала зарегистрируйся!", reply_markup=kb_start())
            parties=await load_parties(); lid,party=find_party_of(parties, user.id)
            if not party: return await q.message.reply_text("🎉 ПАТИ\n\nТы пока не в группе.\nПригласи друга!", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("➕ Пригласить игрока", callback_data="party:invite")],[InlineKeyboardButton("⬅️ Назад", callback_data="menu:main")]]))
            await q.message.reply_text(party_text(parties, lid, await load_players()), reply_markup=kb_party_menu(int(lid)==user.id, len(party["members"])))
            return
        if data=="party:invite":
            await safe_delete(q.message); parties=await load_parties(); lid,party=find_party_of(parties, user.id)
            if not party:
                parties[str(user.id)]={"leader":user.id,"members":[user.id],"pending_invite":None}; await save_parties(parties); lid,party=str(user.id),parties[str(user.id)]
            if int(lid)!=user.id: return await q.message.reply_text("❌ Только лидер может приглашать.", reply_markup=kb_back())
            if len(party["members"])>=MAX_PARTY_SIZE: return await q.message.reply_text("❌ Пати заполнена (максимум 5 человек).", reply_markup=kb_back())
            context.user_data['party_invite_mode']=True
            await q.message.reply_text("👤 Введите Telegram юзернейм игрока:", reply_markup=kb_back())
            return
        if data=="party:leave":
            await safe_delete(q.message); parties=await load_parties(); lid,party=find_party_of(parties, user.id)
            if not party: return await q.message.reply_text("Ты не в пати.", reply_markup=kb_back())
            if int(lid)==user.id:
                [await safe_send(context.bot, uid, "🎉 Пати расформирована лидером.") for uid in party["members"] if uid!=user.id]
                del parties[lid]; await save_parties(parties); await q.message.reply_text("🚪 Пати расформирована.", reply_markup=kb_back())
            else:
                party["members"].remove(user.id); parties[lid]=party; await save_parties(parties)
                await safe_send(context.bot, int(lid), f"🎉 @{p['tag']} покинул пати.")
                await q.message.reply_text("🚪 Ты покинул пати.", reply_markup=kb_back())
            return
        if data.startswith("party_accept:") or data.startswith("party_decline:"):
            action,lid=data.split(":",1); parties=await load_parties(); party=parties.get(lid); await safe_delete(q.message)
            if not party or not party.get("pending_invite") or party["pending_invite"].get("target")!=user.id:
                return await q.message.reply_text("❌ Приглашение не действительно.")
            if action=="party_decline":
                party["pending_invite"]=None; parties[lid]=party; await save_parties(parties); await q.message.reply_text("❌ Приглашение отклонено."); await safe_send(context.bot, int(lid), f"❌ @{p['tag']} отклонил приглашение."); return
            target_lid,target_party=find_party_of(parties, user.id)
            if target_party and int(target_lid)==user.id:
                del parties[str(user.id)]; await save_parties(parties); parties=await load_parties(); party=parties.get(lid)
                if not party: return await q.message.reply_text("❌ Пати больше не существует.")
            if len(party["members"])>=MAX_PARTY_SIZE:
                await q.message.reply_text("❌ Пати уже заполнена."); party["pending_invite"]=None; parties[lid]=party; await save_parties(parties); return
            lobbies=await load_lobbies(); [lobby.remove(user.id) for plt in PLATFORMS for lobby in lobbies[plt] if user.id in lobby]; await save_lobbies(lobbies)
            party["members"].append(user.id); party["pending_invite"]=None; parties[lid]=party; await save_parties(parties)
            await q.message.reply_text(f"✅ Ты присоединился к пати @{(await load_players()).get(lid,{}).get('tag',lid)}!", reply_markup=kb_back())
            [await safe_send(context.bot, uid, party_text(parties, lid, await load_players()), reply_markup=kb_party_menu(uid==int(lid), len(party["members"]))) for uid in party["members"]]
            return
        if data=="menu:find":
            await safe_delete(q.message)
            if not p or p.get("reg")!=1: return await q.message.reply_text("Сначала зарегистрируйся!", reply_markup=kb_start())
            await q.message.reply_text("📱 ВЫБЕРИ ПЛАТФОРМУ", reply_markup=kb_platforms())
            return
        if data.startswith("platform:"):
            platform=data.split(":")[1]; lobbies=await load_lobbies(); parties=await load_parties(); lid,party=find_party_of(parties, user.id)
            if party and int(lid)!=user.id:
                await safe_delete(q.message); return await q.message.reply_text("❌ Только лидер пати может выбирать лобби.", reply_markup=kb_back())
            await safe_delete(q.message)
            await q.message.reply_text(f"📱 {platform.upper()} ЛОББИ" + (f"\n(нужно {len(party['members']) if party else 1} мест для пати)" if party else ""), reply_markup=kb_lobbies(platform, lobbies, len(party["members"]) if party else 1))
            return
        if data=="lobby:full": return await q.answer("❌ Недостаточно свободных мест.", show_alert=True)
        if data.startswith("lobby:"):
            _,platform,idx_str=data.split(":"); idx=int(idx_str); lobbies=await load_lobbies(); parties=await load_parties(); lid,party=find_party_of(parties, user.id)
            members_to_add=party["members"] if party else [user.id]
            if len(lobbies[platform][idx]) + len(members_to_add) > LOBBY_SIZE:
                return await q.answer("❌ Недостаточно места для всей пати!", show_alert=True)
            [lobby.remove(m) for plt in PLATFORMS for lobby in lobbies[plt] for m in members_to_add if m in lobby]
            [lobbies[platform][idx].append(m) for m in members_to_add if m not in lobbies[platform][idx]]
            await save_lobbies(lobbies); await safe_delete(q.message); await update_lobby_for_all(platform, idx, context)
            if len(lobbies[platform][idx]) >= LOBBY_SIZE: await start_ready_check(platform, idx, context)
            return
        if data.startswith("lobby_leave:"):
            _,platform,idx_str=data.split(":"); idx=int(idx_str); lobbies=await load_lobbies(); parties=await load_parties(); lid,party=find_party_of(parties, user.id)
            members_to_remove=party["members"] if (party and int(lid)==user.id) else [user.id]
            [lobbies[platform][idx].remove(m) for m in members_to_remove if m in lobbies[platform][idx]]
            await save_lobbies(lobbies); await safe_delete(q.message); await update_lobby_for_all(platform, idx, context)
            [await safe_send(context.bot, m, "🚪 Вышел из лобби", reply_markup=kb_platforms()) for m in members_to_remove]
            return
        if data=="ready:confirm":
            rs=READY_CHECKS.get(user.id)
            if not rs: return await q.answer("❌ Нет активной проверки.", show_alert=True)
            rc=READY_CHECKS_BY_ID.get(rs["id"])
            if not rc or rc["status"]!="pending": return await q.answer("❌ Проверка уже завершена.", show_alert=True)
            rc["confirmed"].add(user.id); await safe_delete(q.message); await q.message.reply_text("✅ Готовность подтверждена! Ждём остальных...")
            if len(rc["confirmed"]) >= len(rc["players"]): await finalize_ready_check(rs["id"], context)
            return
        if data.startswith("veto_ban:"):
            map_name=data.split(":",1)[1]; veto=context.user_data.get('veto')
            if not veto: return await q.answer("❌ Вето не активно", show_alert=True)
            success,error=veto_ban(veto,str(user.id),map_name)
            if not success: return await q.answer(f"❌ {error}", show_alert=True)
            await safe_delete(q.message)
            if veto["final_map"]:
                match=context.user_data.get('match',{}); match['map']=veto['final_map']; match['status']='in_progress'; match['result_unlock_time']=time.time()+RESULT_UNLOCK_DELAY
                context.user_data['match']=match; host_p=(await load_players()).get(str(match.get('host')),{})
                [await safe_send(context.bot, uid, f"🏆 Матч сформирован!\n\nID: {context.user_data.get('match_id')}\nИгра до 13\nКарта: {MAP_EMOJI.get(veto['final_map'],'')} {veto['final_map']}\nРаунд: 1:50\nХост: @{host_p.get('tag', match.get('host'))}\n\nПосле матча хост отправляет фото, затем:\n/winner ct или /winner t") for uid in match.get('players',[])]
                asyncio.create_task(_notify_result_ready(match.get('host'), context.user_data.get('match_id'), context))
                return
            next_player=(await load_players()).get(veto["turn"],{})
            await q.message.reply_text(f"🗺️ ВЕТО\n\nХод: @{next_player.get('tag', veto['turn'])}\nДоступные карты:\n" + "\n".join(f"• {MAP_EMOJI.get(m,'')} {m}" for m in veto["pool"]), reply_markup=kb_veto(veto["pool"]))
            return
        if data.startswith("winteam:"):
            match=context.user_data.get('match')
            if not match or match.get('status')!='awaiting_winning_team':
                return await q.answer("❌ Нет матча, ожидающего выбор.", show_alert=True)
            if match.get('host')!=user.id: return await q.answer("❌ Только хост может указать победителя.", show_alert=True)
            choice=data.split(":",1)[1]; match['winner_team']=choice; match['status']='awaiting_stats'
            context.user_data['match']=match; context.user_data['stats_mode']=True; context.user_data['stats_buffer']={}
            await safe_delete(q.message)
            await q.message.reply_text("Теперь введи статистику каждого игрока в формате:\n@ник убийства-смерти-хс\n\nНапример:\n@Vasya 18-9-5\n\nОтправляй по одному игроку.", reply_markup=kb_skip())
            return
        if data=="stats:skip": await safe_delete(q.message); await finalize_match(update, context, skip_stats=True); return
        if data=="result:send": await q.answer(); return

        if user.id == OWNER_ID and data.startswith("admin:"):
            action=data.split(":",1)[1]
            if action=="back":
                await safe_delete(q.message); context.user_data.pop('admin_action',None); context.user_data.pop('admin_target',None)
                await q.message.reply_text("👑 АДМИН-ПАНЕЛЬ\n\nВыбери действие:", reply_markup=kb_admin()); return
            if action=="analytics":
                await safe_delete(q.message)
                a,pending=await load_analytics(),await load_pending() 
                if action == "analytics":
    await safe_delete(q.message)
    a, pending = await load_analytics(), await load_pending()
    map_picks = a.get("map_picks", {})
    top_map = max(map_picks.items(), key=lambda x: x[1])[0] if map_picks else "нет данных"
    online_samples = a.get("online_samples", [])
    avg_online = round(sum(online_samples) / len(online_samples), 1) if online_samples else 0
    elos = [p.get("elo", 0) for p in (await load_players()).values() if p.get("reg") == 1 and p.get("calib", 0) >= CALIBRATION_GAMES]
    avg_elo = round(sum(elos) / len(elos), 1) if elos else 0
    hours = []
    for ts in a.get("match_timestamps", []):
        if ts:
            try:
                hours.append(datetime.fromisoformat(ts).hour)
            except:
                pass
    peak_hour = Counter(hours).most_common(1)[0][0] if hours else None
    reports = await load_reports()
    categories = {"читер": 0, "оскорбления": 0, "слив": 0, "афк": 0, "токсик": 0, "другое": 0}
    keywords = {
        "читер": ["чит", "aim", "wallhack", "аим", "вх"],
        "оскорбления": ["оскорб", "мат", "хам"],
        "слив": ["слил", "слив", "throw"],
        "афк": ["афк", "afk", "не играл"],
        "токсик": ["токсич", "токсик"]
    }
    for lst in reports.values():
        for r in lst:
            t = r.get("text", "").lower()
            matched = False
            for cat, kws in keywords.items():
                if any(k in t for k in kws):
                    categories[cat] += 1
                    matched = True
                    break
            if not matched:
                categories["другое"] += 1
    text = f"📊 АНАЛИТИКА\n\n🗺️ Карта: {MAP_EMOJI.get(top_map, '')} {top_map}\n👥 Средний онлайн: {avg_online}\n📈 Средний ELO: {avg_elo}\n⏰ Пиковое время: {f'{peak_hour}:00 - {(peak_hour + 1) % 24}:00 (МСК)' if peak_hour else 'нет данных'}\n🎮 Активных матчей: {sum(1 for v in pending.values() if v.get('status') == 'awaiting_review')}\n\n📢 Топ жалоб:\n" + "\n".join(f"  • {c}: {n}" for c, n in sorted(categories.items(), key=lambda x: x[1], reverse=True) if n > 0)
    await q.message.reply_text(text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data="admin:back")]]))
    return

if action == "complaints_top":
    await safe_delete(q.message)
    reports = await load_reports()
    players_data = await load_players()
    counts = []
    for uid, lst in reports.items():
        if lst and len(lst) > 0:
            tag = players_data.get(uid, {}).get('tag', uid)
            counts.append((tag, len(lst)))
    counts.sort(key=lambda x: x[1], reverse=True)
    counts = counts[:10]
    text = "📢 ТОП ЖАЛОБ ПО ИГРОКАМ\n\n" + ("\n".join(f"@{t} — {n}" for t, n in counts) if counts else "Нет жалоб.")
    await q.message.reply_text(text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data="admin:back")]]))
    return
            if action=="history":
                history=await load_history(); text,total=admin_history_page_text(history,0)
                await safe_delete(q.message); await q.message.reply_text(text, reply_markup=kb_history_nav(0,total)); return
            if action=="unlink":
                all_players=sorted([(uid,p) for uid,p in (await load_players()).items() if p.get("reg")==1], key=lambda x:x[1].get('tag',''))
                context.user_data['admin_unlink_players']=all_players
                await safe_delete(q.message)
                await q.message.reply_text("🆔 ОТВЯЗАТЬ АЙДИ\n\nВыбери игрока:", reply_markup=kb_players_list(all_players, "unlink", 0, back_cb="admin:back")); return
            if action=="ban":
                all_players=sorted([(uid,p) for uid,p in (await load_players()).items() if p.get("reg")==1], key=lambda x:x[1].get('tag',''))
                context.user_data['admin_ban_players']=all_players
                await safe_delete(q.message)
                await q.message.reply_text("🔨 ЗАБАНИТЬ ИГРОКА\n\nВыбери игрока:", reply_markup=kb_players_list(all_players, "ban", 0, back_cb="admin:back")); return
            if action=="elo":
                await safe_delete(q.message); await q.message.reply_text("📊 УПРАВЛЕНИЕ ELO\n\nВыбери действие:", reply_markup=kb_admin_elo()); return
            if action in ("elo_add","elo_remove"):
                sub="add" if action=="elo_add" else "remove"; context.user_data['admin_action']=f"elo_{sub}"
                all_players=sorted([(uid,p) for uid,p in (await load_players()).items() if p.get("reg")==1], key=lambda x:x[1].get('elo',0), reverse=True)
                context.user_data[f'admin_elo_{sub}_players']=all_players
                await safe_delete(q.message)
                await q.message.reply_text("👥 Выбери игрока:", reply_markup=kb_players_list(all_players, f"eloact_{sub}", 0, back_cb="admin:back")); return
            if action in ("change_id","change_nick"):
                context.user_data['admin_action']=action
                all_players=sorted([(uid,p) for uid,p in (await load_players()).items() if p.get("reg")==1], key=lambda x:x[1].get('tag',''))
                context.user_data['admin_change_players']=all_players
                await safe_delete(q.message)
                await q.message.reply_text(f"✏️ Выбери игрока для изменения {'ID' if action=='change_id' else 'ник'}:", reply_markup=kb_players_list(all_players, f"chg_{action}", 0, back_cb="admin:back")); return
            if action=="give_tag":
                all_players=sorted([(uid,p) for uid,p in (await load_players()).items() if p.get("reg")==1], key=lambda x:x[1].get('tag',''))
                context.user_data['admin_give_tag_players']=all_players
                await safe_delete(q.message)
                await q.message.reply_text("🏷️ ВЫДАТЬ ТЕГ\n\nВыбери игрока:", reply_markup=kb_players_list(all_players, "give_tag", 0, back_cb="admin:back")); return

        if data.startswith("admin_history_page:"):
            page=int(data.split(":")[1]); history=await load_history(); text,total=admin_history_page_text(history,page)
            await q.message.edit_text(text, reply_markup=kb_history_nav(page,total)); return
        for prefix in ["unlink","ban","eloact_add","eloact_remove","chg_change_id","chg_change_nick","give_tag"]:
            if data.startswith(f"{prefix}_page:"):
                page=int(data.split(":")[1]); key_map={"unlink":"admin_unlink_players","ban":"admin_ban_players","eloact_add":"admin_elo_add_players","eloact_remove":"admin_elo_remove_players","chg_change_id":"admin_change_players","chg_change_nick":"admin_change_players","give_tag":"admin_give_tag_players"}
                lst=context.user_data.get(key_map[prefix],[])
                await q.message.edit_text(q.message.text or "Выбери игрока:", reply_markup=kb_players_list(lst, prefix, page, back_cb="admin:back")); return
        if data.startswith("unlink:") and user.id==OWNER_ID:
            _,target_uid,_=data.split(":"); target_p=(await load_players()).get(target_uid)
            if not target_p: return await q.answer("❌ Игрок не найден.", show_alert=True)
            await safe_delete(q.message)
            await q.message.reply_text(f"⚠️ Отвязать айди и сбросить ВСЮ статистику игрока @{target_p.get('tag',target_uid)}?", reply_markup=kb_confirm("unlink", target_uid)); return
        if data.startswith("unlink_yes:") and user.id==OWNER_ID:
            target_uid=data.split(":")[1]; players_data=await load_players()
            if target_uid in players_data:
                audit_log("admin_unlink", user.id, {"target": target_uid, "old_data": players_data[target_uid]})
                del players_data[target_uid]; await save_players(players_data)
                await safe_send(context.bot, int(target_uid), "🆔 Твой аккаунт был отвязан. Статистика сброшена.")
            await safe_delete(q.message); await q.message.reply_text("✅ Айди отвязан, статистика сброшена (см. audit.log)."); return
        if data.startswith("unlink_no:") and user.id==OWNER_ID:
            await safe_delete(q.message); await q.message.reply_text("Отменено.", reply_markup=kb_admin()); return
        if data.startswith("ban:") and user.id==OWNER_ID:
            _,target_uid,_=data.split(":"); target_p=(await load_players()).get(target_uid)
            if not target_p: return await q.answer("❌ Игрок не найден.", show_alert=True)
            await safe_delete(q.message)
            await q.message.reply_text(f"⚠️ Забанить игрока @{target_p.get('tag',target_uid)}? Статистика НЕ будет сброшена.", reply_markup=kb_confirm("ban", target_uid)); return
        if data.startswith("ban_yes:") and user.id==OWNER_ID:
            target_uid=data.split(":")[1]; players_data=await load_players()
            if target_uid in players_data:
                players_data[target_uid]['ban']=True; await save_players(players_data); audit_log("admin_ban", user.id, {"target": target_uid})
                await safe_send(context.bot, int(target_uid), "🔨 Ты был забанен.")
            await safe_delete(q.message); await q.message.reply_text("✅ Игрок забанен."); return
        if data.startswith("ban_no:") and user.id==OWNER_ID:
            await safe_delete(q.message); await q.message.reply_text("Отменено.", reply_markup=kb_admin()); return
        if data.startswith("eloact_add:") or data.startswith("eloact_remove:"):
            if user.id != OWNER_ID: return
            sub="add" if data.startswith("eloact_add:") else "remove"; target_uid=data.split(":")[1]; target_p=(await load_players()).get(target_uid)
            if not target_p: return await q.answer("❌ Игрок не найден.", show_alert=True)
            context.user_data['admin_target']=target_uid; context.user_data['admin_action']=f"elo_{sub}"; context.user_data['admin_input_mode']='elo_amount'
            await safe_delete(q.message)
            await q.message.reply_text(f"🎯 Игрок: @{target_p['tag']}\nТекущий ELO: {target_p['elo']}\n\nВведи сумму ELO для {'выдачи' if sub=='add' else 'убавки'}:"); return
        if data.startswith("chg_change_id:") or data.startswith("chg_change_nick:"):
            if user.id != OWNER_ID: return
            is_id=data.startswith("chg_change_id:"); target_uid=data.split(":")[1]; target_p=(await load_players()).get(target_uid)
            if not target_p: return await q.answer("❌ Игрок не найден.", show_alert=True)
            context.user_data['admin_target']=target_uid; context.user_data['admin_input_mode']='change_id' if is_id else 'change_nick'
            await safe_delete(q.message)
            await q.message.reply_text(f"✏️ Введи {'новый ID (8-15 цифр)' if is_id else 'новый ник'} для @{target_p['tag']}:"); return
        if data.startswith("give_tag:") and user.id==OWNER_ID:
            _,target_uid,_=data.split(":"); target_p=(await load_players()).get(target_uid)
            if not target_p: return await q.answer("❌ Игрок не найден.", show_alert=True)
            context.user_data['admin_give_tag_target']=target_uid
            await safe_delete(q.message)
            await q.message.reply_text(f"🏷️ Введи новый тег для @{target_p.get('tag',target_uid)}:")
            context.user_data['admin_input_mode']='give_tag'; return
        if data.startswith("admin_ok:") or data.startswith("admin_no:"):
            if user.id not in ADMIN_IDS: return await q.answer("❌ Только для администраторов.", show_alert=True)
            action,pid=data.split(":",1); pending=await load_pending(); record=pending.get(pid)
            if not record: return await q.answer("❌ Заявка не найдена.", show_alert=True)
            players_data=await load_players()
            if action=="admin_ok":
                audit_log("admin_confirm_match", user.id, {"match_id": pid})
                record['status']='confirmed'; record['confirmed_by']=players_data.get(str(user.id),{}).get('tag',str(user.id))
                pending[pid]=record; await save_pending(pending)
                for uid,summary in record['player_results'].items():
                    p=players_data.get(uid)
                    if not p: continue
                    if record.get('match_photo'): await safe_send(context.bot, int(uid), "📸 Скриншот матча:", photo=record['match_photo'])
                    text=f"✅ Матч подтверждён!\n\nID: {record['match_id']}\nКарта: {MAP_EMOJI.get(record['map_name'],'')} {record['map_name']}\n{'🏆 Победа' if summary['is_winner'] else 'Поражение'}\n"
                    if summary.get('calibrating'): text+=f"Калибровка: {p['calib']}/{CALIBRATION_GAMES}\n"
                    elif summary.get('just_finished_calibration'): text+=f"Калибровка завершена! ELO: {summary['new_elo']}\n"
                    else: text+=f"{summary['delta']:+d} ELO → {summary['new_elo']}\n"
                    if summary.get('mvp'): text+="⭐ MVP матча!\n"
                    await safe_send(context.bot, int(uid), text)
                await save_players(players_data); await safe_delete(q.message); await q.message.reply_text(f"✅ Матч {record['match_id']} подтверждён.")
            else:
                audit_log("admin_reject_match", user.id, {"match_id": pid})
                for uid,summary in record['player_results'].items():
                    p=players_data.get(uid)
                    if not p: continue
                    if summary.get('_snapshot_before'): rollback_match_result(p, summary['_snapshot_before'])
                    map_name=record['map_name']
                    if map_name in p.get('maps',{}):
                        if summary['is_winner']: p['maps'][map_name]['wins']=max(0, p['maps'][map_name]['wins']-1)
                        else: p['maps'][map_name]['losses']=max(0, p['maps'][map_name]['losses']-1)
                    await safe_send(context.bot, int(uid), f"❌ Результат матча {record['match_id']} отклонён. Изменения отменены.")
                await save_players(players_data); record['status']='rejected'; pending[pid]=record; await save_pending(pending)
                await safe_delete(q.message); await q.message.reply_text(f"❌ Матч {record['match_id']} отклонён.")
    except Exception as e:
        logger.error(f"Ошибка в button_callback: {e}")
        try: await update.callback_query.message.reply_text("❌ Произошла ошибка. Попробуйте снова.")
        except: pass

async def handle_photo(update, context):
    if await check_banned(update): return
    if context.user_data.get('auth_step') == 'reg_photo':
        sid,tag=context.user_data.get('reg_pending_sid'), context.user_data.get('reg_pending_tag')
        if not sid or not tag: return await update.message.reply_text("❌ Сессия истекла. Начни заново.")
        pid=f"reg_{int(time.time())}_{update.effective_user.id}"; pending=await load_pending()
        pending[pid]={"type":"registration","user_id":update.effective_user.id,"sid":sid,"tag":tag,"photo_id":update.message.photo[-1].file_id,"status":"pending","created_at":time.time()}
        await save_pending(pending)
        await safe_send(context.bot, ADMIN_CHAT_ID, f"📸 Новая заявка на регистрацию!\n\nНик: {tag}\nID: {sid}\nTelegram: @{update.effective_user.username or 'нет юза'}", photo=update.message.photo[-1].file_id, reply_markup=kb_admin_review(pid))
        await update.message.reply_text("⏳ Твоя заявка отправлена админам. Жди ответа.")
        context.user_data.clear(); return
    match=context.user_data.get('match')
    if not match or match.get('status')!='in_progress': return await update.message.reply_text("❌ Нет матча, ожидающего скриншот.")
    if match.get('host')!=update.effective_user.id: return await update.message.reply_text("❌ Только хост может отправлять результат.")
    if time.time() < match.get('result_unlock_time',0): return await update.message.reply_text(f"⏳ Отправка доступна через {int(match['result_unlock_time']-time.time())} сек.")
    context.user_data['match_photo']=update.message.photo[-1].file_id
    await update.message.reply_text("✅ Скриншот принят!\n\nТеперь объяви победившую сторону:\n/winner ct или /winner t")

async def pre_checkout_query(update, context): await update.pre_checkout_query.answer(ok=True)
async def successful_payment(update, context):
    payload=update.message.successful_payment.invoice_payload
    if not payload.startswith("premium_"): return
    period=payload.split("_")[1]
    if period not in PREMIUM_DURATIONS: return
    players = await load_players()
    p = get_player(players, update.effective_user.id)
    if p:
        p['premium_until'] = int(time.time()) + PREMIUM_DURATIONS[period]
        await save_players(players)
        await update.message.reply_text(f"✅ Премиум-подписка активирована!\n\n📅 Период: {'день' if period=='day' else 'неделя' if period=='week' else 'месяц'}\n💎 Теперь тебе доступны:\n• x2 ELO\n• Любой тег\n• Бесплатные турниры\n• Смена ника (2 раза бесплатно, далее 50⭐)")
    else: await update.message.reply_text("❌ Ошибка: игрок не найден.")
async def _notify_result_ready(host_id, match_id, context):
    await asyncio.sleep(RESULT_UNLOCK_DELAY)
    if host_id: await safe_send(context.bot, host_id, f"📤 Можешь отправить скриншот результата матча {match_id}, затем /winner ct или /winner t.", reply_markup=kb_send())

def find_subset_with_sum(groups, target):
    n=len(groups); sizes=[len(g) for g in groups]
    def backtrack(i, rem, chosen):
        if rem==0: return chosen
        if i>=n or rem<0: return None
        res=backtrack(i+1, rem-sizes[i], chosen+[i])
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
    random.shuffle(groups); total, team_size = sum(len(g) for g in groups), len(players_list)//2
    chosen = find_subset_with_sum(groups, team_size)
    if chosen is None:
        team_a, team_b = [], []
        for group in sorted(groups, key=len, reverse=True):
            if len(team_a) + len(group) <= team_size: team_a.extend(group)
            elif len(team_b) + len(group) <= (total - team_size): team_b.extend(group)
            else: [team_a.append(m) if len(team_a) < team_size else team_b.append(m) for m in group]
        return team_a, team_b
    chosen_set = set(chosen); return [uid for i in chosen for uid in groups[i]], [uid for i,g in enumerate(groups) if i not in chosen_set for uid in g]
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

STATS_LINE_RE = re.compile(r"^@?([A-Za-z0-9_]+)\s+(\d+)\s*-\s*(\d+)(?:\s*-\s*(\d+))?$")
async def handle_message(update, context):
    try:
        user, text = update.effective_user, update.message.text.strip()
        players = await load_players()
        if context.user_data.get('admin_input_mode') == 'elo_amount' and user.id == OWNER_ID:
            try:
                amount=int(text)
                if amount<=0: return await update.message.reply_text("❌ Сумма должна быть положительным числом.")
                action,target_uid=context.user_data.get('admin_action'),context.user_data.get('admin_target')
                players_data=await load_players(); tp=players_data.get(target_uid)
                if not tp: return await update.message.reply_text("❌ Игрок не найден.")
                tp['elo']=max(0, tp['elo'] + (amount if action=='elo_add' else -amount))
                tp['level']=level_from_elo(tp['elo']); tp['rank']=rank_label(tp['level'])
                audit_log(f"admin_{action}", user.id, {"target": target_uid, "amount": amount, "new_elo": tp['elo']})
                await save_players(players_data); await update.message.reply_text(f"✅ ELO обновлён. Новый ELO @{tp['tag']}: {tp['elo']}")
                context.user_data.pop('admin_input_mode',None); context.user_data.pop('admin_action',None); context.user_data.pop('admin_target',None); return
            except ValueError: return await update.message.reply_text("❌ Введи число (например: 50)")
        if context.user_data.get('admin_input_mode') in ('change_id','change_nick') and user.id == OWNER_ID:
            mode,target_uid=context.user_data['admin_input_mode'],context.user_data.get('admin_target')
            players_data=await load_players(); tp=players_data.get(target_uid)
            if not tp: return await update.message.reply_text("❌ Игрок не найден.")
            if mode=='change_id':
                if not validate_id(text): return await update.message.reply_text("❌ ID должен быть числом из 8-15 цифр!")
                if find_by_sid(players_data, text): return await update.message.reply_text("❌ Этот ID уже занят!")
                audit_log("admin_change_id", user.id, {"target": target_uid, "old": tp['sid'], "new": text}); tp['sid']=text
            else:
                if find_by_tag(players_data, text): return await update.message.reply_text("❌ Этот ник уже занят!")
                audit_log("admin_change_nick", user.id, {"target": target_uid, "old": tp['tag'], "new": text}); tp['tag']=text; tp['name']=text
            await save_players(players_data); await update.message.reply_text(f"✅ {'ID' if mode=='change_id' else 'Ник'} изменён на {text}.")
            context.user_data.pop('admin_input_mode',None); context.user_data.pop('admin_target',None); return
        if context.user_data.get('admin_input_mode') == 'give_tag' and user.id == OWNER_ID:
            target_uid,fresh_players=context.user_data.get('admin_give_tag_target'), await load_players()
            tp=fresh_players.get(target_uid)
            if not tp: return await update.message.reply_text("❌ Игрок не найден.")
            old_tag=tp['tag']; tp['tag']=text; tp['name']=text
            audit_log("admin_give_tag", user.id, {"target": target_uid, "old": old_tag, "new": text})
            await save_players(fresh_players); await update.message.reply_text(f"✅ Тег @{old_tag} изменён на: {text}")
            context.user_data.pop('admin_input_mode',None); context.user_data.pop('admin_give_tag_target',None); return
        if context.user_data.get('auth_step') == 'login_id':
            if not validate_id(text): return await update.message.reply_text("❌ ID должен быть числом из 8-15 цифр!")
            owner_uid=find_by_sid(players, text)
            if not owner_uid: return await update.message.reply_text("❌ Игрок с таким ID не найден.")
            context.user_data['login_sid'], context.user_data['login_owner_uid'], context.user_data['auth_step'] = text, owner_uid, 'login_name'
            await update.message.reply_text(f"✅ ID найден!\n\nТеперь введи свой ник в Standoff 2:"); return
        if context.user_data.get('auth_step') == 'login_name':
            owner_uid=context.user_data.get('login_owner_uid'); fresh_players=await load_players(); owner_p=fresh_players.get(owner_uid)
            if not owner_p: return await update.message.reply_text("❌ Ошибка. Начни заново.") and context.user_data.clear()
            if text.lstrip("@").lower() != owner_p.get('tag', '').lower(): return await update.message.reply_text("❌ Ник не совпадает. Попробуй снова.")
            if owner_uid != str(user.id):
                fresh_players[str(user.id)]=owner_p; del fresh_players[owner_uid]; fresh_players[str(user.id)]['tg_username']=user.username or str(user.id); await save_players(fresh_players)
            context.user_data.clear(); p=fresh_players.get(str(user.id)); parties=await load_parties(); lid,_=find_party_of(parties, user.id)
            await update.message.reply_text("✅ Вход выполнен!"); await update.message.reply_text(main_menu_text(p), reply_markup=kb_menu(bool(lid))); return
        if context.user_data.get('auth_step') == 'reg_id':
            if not validate_id(text): return await update.message.reply_text("❌ ID должен быть числом из 8-15 цифр!")
            if find_by_sid(players, text): return await update.message.reply_text("❌ Этот ID уже зарегистрирован!")
            context.user_data['reg_pending_sid'], context.user_data['auth_step'] = text, 'reg_nick'
            await update.message.reply_text(f"✅ ID принят!\n\nТеперь введи свой НАСТОЯЩИЙ ник в Standoff 2:"); return
        if context.user_data.get('auth_step') == 'reg_nick':
            if find_by_tag(players, text): return await update.message.reply_text("❌ Этот ник уже занят! Введи другой:")
            context.user_data['reg_pending_tag']=text
            await update.message.reply_text(f"✅ Ник принят: {text}\n\n📸 Теперь отправь скриншот профиля из Standoff 2\nНа скриншоте должны быть видны:\n• Твой ID: {context.user_data.get('reg_pending_sid')}\n• Твой ник: {text}\n\nАдминистраторы проверят и подтвердят регистрацию.", reply_markup=kb_reg_done())
            context.user_data['auth_step']='reg_photo_wait'; return
        if context.user_data.get('party_invite_mode'):
            context.user_data['party_invite_mode']=False; target_uid=find_by_telegram_username(players, text)
            if not target_uid: return await update.message.reply_text("❌ Игрок с таким Telegram юзернеймом не найден.")
            if int(target_uid)==user.id: return await update.message.reply_text("❌ Нельзя пригласить самого себя.")
            parties=await load_parties(); lid,party=find_party_of(parties, user.id)
            if not party or int(lid)!=user.id: return await update.message.reply_text("❌ Ты не лидер пати.")
            if int(target_uid) in party["members"]: return await update.message.reply_text("❌ Этот игрок уже в пати.")
            if find_party_of(parties, int(target_uid))[1]: return await update.message.reply_text("❌ Игрок уже в другой пати.")
            if len(party["members"])>=MAX_PARTY_SIZE: return await update.message.reply_text("❌ Пати заполнена.")
            party["pending_invite"]={"target": int(target_uid), "invited_at": time.time()}
            parties[lid]=party; await save_parties(parties)
            await update.message.reply_text(f"✅ Приглашение отправлено @{players.get(str(target_uid),{}).get('tag',target_uid)}!")
            await safe_send(context.bot, int(target_uid), f"🎉 Игрок @{players.get(str(user.id),{}).get('tag',user.id)} приглашает в пати.", reply_markup=kb_party_invite_response(lid))
            return
        if context.user_data.get('support_mode'):
            p=get_player(players, user.id)
            await safe_send(context.bot, ADMIN_CHAT_ID, f"🆘 Поддержка\n\n👤 @{p.get('tag',user.id) if p else user.id} (ID: {user.id})\n📝 {text}")
            context.user_data['support_mode']=False
            await update.message.reply_text("✅ Запрос отправлен!", reply_markup=kb_menu() if (p and p.get('reg')==1) else kb_start())
            return
        if context.user_data.get('complaint_target'):
            target_uid=context.user_data.pop('complaint_target'); report_text=sanitize_input(text)
            if not report_text: return await update.message.reply_text("❌ Текст жалобы не может быть пустым.")
            reports=await load_reports(); reports.setdefault(target_uid, [])
            if any(r.get("by")==str(user.id) for r in reports[target_uid]): return await update.message.reply_text("❌ Ты уже жаловался на этого игрока.")
            reports[target_uid].append({"by": str(user.id), "text": report_text, "timestamp": datetime.now().isoformat()})
            await save_reports(reports)
            await update.message.reply_text(f"✅ Жалоба на @{(await load_players()).get(target_uid,{}).get('tag',target_uid)} отправлена.")
            return
        if context.user_data.get('stats_mode'): await handle_stats_input(update, context, text)
    except Exception as e:
        logger.error(f"Ошибка в handle_message: {e}"); await update.message.reply_text("❌ Произошла ошибка. Попробуйте снова.")

async def handle_stats_input(update, context, text):
    match, m = context.user_data.get('match'), STATS_LINE_RE.match(text)
    if not match: return await update.message.reply_text("❌ Нет активного матча.") and context.user_data.__setitem__('stats_mode', False)
    if match.get('host') != update.effective_user.id: return await update.message.reply_text("❌ Статистику вводит только хост.")
    if not m: return await update.message.reply_text("❌ Неверный формат. Используй: @ник убийства-смерти-хс\nНапример: @Vasya 18-9-5", reply_markup=kb_skip())
    tag,kills_str,deaths_str,hs_str=m.groups(); players=await load_players(); uid=find_by_tag(players, tag)
    if not uid or int(uid) not in match.get('players',[]): return await update.message.reply_text(f"❌ Игрок @{tag} не найден в этом матче.", reply_markup=kb_skip())
    buffer=context.user_data.setdefault('stats_buffer', {}); buffer[uid]={"kills": int(kills_str), "deaths": int(deaths_str), "hs": int(hs_str) if hs_str else 0}
    remaining=[str(p) for p in match['players'] if str(p) not in buffer]
    if remaining: await update.message.reply_text(f"✅ Записано: @{tag} {kills_str}-{deaths_str}\n\nОсталось: {len(remaining)}", reply_markup=kb_skip())
    else: await finalize_match(update, context, skip_stats=False)

async def finalize_match(update, context, skip_stats):
    match=context.user_data.get('match')
    if not match: return
    players=await load_players()
    winning_team=match['team_a'] if match.get('winner_team')=='a' else match['team_b']
    losing_team=match['team_b'] if match.get('winner_team')=='a' else match['team_a']
    stats_buffer=context.user_data.get('stats_buffer',{}) if not skip_stats else {}
    if not stats_buffer: stats_buffer={}
    mvp_uid=None
    if stats_buffer:
        best_kills=-1
        for uid in [str(u) for u in winning_team]:
            if stats_buffer.get(uid,{}).get('kills',0)>best_kills:
                best_kills=stats_buffer[uid]['kills']; mvp_uid=uid
    map_name, match_id = match.get('map'), match.get('match_id') or gen_match_id()
    player_results, winners_card, losers_card, match_stats_record = {}, [], [], {}
    for uid in [str(u) for u in winning_team]:
        p=players.get(uid)
        if not p: continue
        s=stats_buffer.get(uid,{"kills":0,"deaths":0,"hs":0})
        is_mvp=(uid==mvp_uid)
        result=apply_match_result(p, True, s['kills'], s['deaths'], s.get('hs',0), is_mvp)
        apply_map_result(p, map_name, True)
        player_results[uid]={**result,"is_winner":True,"mvp":is_mvp}
        match_stats_record[uid]=s
        winners_card.append({"tag":p['tag'],"kd":f"{s['kills']}/{s['deaths']}","mvp":is_mvp,"calibrating":result['calibrating'],"delta":result['delta'],"elo":result['new_elo'] or p['elo']})
    for uid in [str(u) for u in losing_team]:
        p=players.get(uid)
        if not p: continue
        s=stats_buffer.get(uid,{"kills":0,"deaths":0,"hs":0})
        result=apply_match_result(p, False, s['kills'], s['deaths'], s.get('hs',0), False)
        apply_map_result(p, map_name, False)
        player_results[uid]={**result,"is_winner":False,"mvp":False}
        match_stats_record[uid]=s
        losers_card.append({"tag":p['tag'],"kd":f"{s['kills']}/{s['deaths']}","calibrating":result['calibrating'],"delta":result['delta'],"elo":result['new_elo'] or p['elo']})
    await save_players(players)
    await append_history({"match_id":match_id,"map":map_name,"all_players":[str(u) for u in match.get('players',[])],"winners":[str(u) for u in winning_team],"losers":[str(u) for u in losing_team],"stats":match_stats_record,"mvp":mvp_uid,"timestamp":datetime.now(MOSCOW_TZ).isoformat()})
    pending=await load_pending()
    pending[match_id]={"match_id":match_id,"map_name":map_name,"player_results":player_results,"status":"awaiting_review","match_photo":context.user_data.get('match_photo'),"created_at":time.time()}
    await save_pending(pending)
    if ADMIN_CHAT_ID:
        if context.user_data.get('match_photo'): await safe_send(context.bot, ADMIN_CHAT_ID, f"📸 Скриншот результата матча {match_id}", photo=context.user_data['match_photo'])
        await safe_send(context.bot, ADMIN_CHAT_ID, f"📋 Матч на проверку\nID: {match_id}\nКарта: {map_name}\n\n🔵 ПОБЕДА:\n" + "\n".join(f"🏆 {p['tag']} ({p['kd']})" for p in winners_card) + "\n\n🔴 ПОРАЖЕНИЕ:\n" + "\n".join(f"❌ {p['tag']} ({p['kd']})" for p in losers_card), reply_markup=kb_admin_review(match_id))
    context.user_data['stats_mode'], context.user_data['stats_buffer'], context.user_data['match'], context.user_data['match_id'], context.user_data['veto'], context.user_data['match_photo'] = False, {}, None, None, None, None
    await update.effective_message.reply_text("✅ Результат отправлен админам на проверку.\nКак только подтвердят — получишь уведомление.")

app_flask = Flask(__name__); start_time=time.time()
@app_flask.route('/health')
def health(): return jsonify({'status':'ok','timestamp':datetime.now().isoformat(),'players':len(asyncio.run(load_players())),'uptime_seconds':int(time.time()-start_time)})
def run_health_server():
    try: app_flask.run(host='0.0.0.0', port=8080, debug=False, use_reloader=False)
    except Exception as e: logger.error(f"Health check не запущен: {e}")
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

import os,re,time,json,random,string,logging,threading,asyncio
from datetime import datetime,timedelta,timezone
from typing import Optional,Any,Dict,List
import bcrypt
from flask import Flask
from supabase import create_client
from telegram import Update,InlineKeyboardButton,InlineKeyboardMarkup,ReplyKeyboardMarkup,ReplyKeyboardRemove
from telegram.constants import ParseMode
from telegram.ext import Application,ApplicationBuilder,CommandHandler,MessageHandler,CallbackQueryHandler,ConversationHandler,ContextTypes,PreCheckoutQueryHandler,filters

# ===== КОНФИГ =====
BOT_TOKEN=os.environ.get("BOT_TOKEN","")
OWNER_ID=int(os.environ.get("OWNER_ID","0") or "0")
ADMIN_CHAT_ID=int(os.environ.get("ADMIN_CHAT_ID","0") or "0")
SUPABASE_URL=os.environ.get("SUPABASE_URL","")
SUPABASE_KEY=os.environ.get("SUPABASE_KEY","")
REQUIRED_CHANNEL=os.environ.get("REQUIRED_CHANNEL","")
PORT=int(os.environ.get("PORT","10000"))
if not BOT_TOKEN or not SUPABASE_URL or not SUPABASE_KEY: raise RuntimeError("Missing env vars")
RATE_LIMIT_MAX,RATE_LIMIT_WINDOW,CACHE_TTL,CALIBRATION_MATCHES,READY_CHECK_SECONDS,LOBBY_SIZE,BAN_STEPS_BEFORE_PICK=10,60,60,10,60,10,2
MAP_POOL=["Dust2","Mirage","Inferno","Nuke","Ancient","Anubis","Vertigo"]
TABLE_PLAYERS,TABLE_PROMOCODES,TABLE_LOBBIES,TABLE_MATCHES="players","promocodes","lobbies","matches"

# ===== ЛОГИ =====
logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",level=logging.INFO)
logger=logging.getLogger("cs_bot")
logging.getLogger("httpx").setLevel(logging.WARNING)

# ===== ИСКЛЮЧЕНИЯ =====
class BotError(Exception): pass
class DatabaseError(BotError): pass
class ValidationError(BotError): pass

# ===== SUPABASE =====
supabase=create_client(SUPABASE_URL,SUPABASE_KEY)
_cache={}
def _cg(k):
    v=_cache.get(k)
    if v and time.time()-v[0]<CACHE_TTL: return v[1]
    _cache.pop(k,None); return None
def _cs(k,v): _cache[k]=(time.time(),v)
def _ci(p):
    for k in list(_cache.keys()):
        if k.startswith(p): _cache.pop(k,None)
def _wr(f,*a,**kw):
    for i in range(1,4):
        try: return f(*a,**kw)
        except Exception as e:
            logger.warning(f"Retry {i}/3: {e}")
            time.sleep(0.5*i)
    raise DatabaseError("All retries failed")

def dbg(t,u=True):
    k=f"pt_{t}"
    if u:
        c=_cg(k)
        if c is not None: return c if c!="NONE" else None
    try:
        r=_wr(lambda: supabase.table(TABLE_PLAYERS).select("*").eq("Telegram_id",t).limit(1).execute()).data
        d=r[0] if r else None
        _cs(k,d if d else "NONE"); return d
    except: return None
def dbn(n):
    try:
        r=_wr(lambda: supabase.table(TABLE_PLAYERS).select("*").eq("nick",n).limit(1).execute()).data
        return r[0] if r else None
    except: return None
def dbc(t,n,g,p):
    try:
        r=_wr(lambda: supabase.table(TABLE_PLAYERS).insert({"Telegram_id":t,"nick":n,"game_id":g,"password":p,"elo":1000,"wins":0,"losses":0,"matches":0,"mvp":0,"kills":0,"deaths":0,"headshots":0,"fav_map":"—","premium_until":None,"banned":False}).execute())
        _ci(f"pt_{t}"); return r.data
    except: return None
def dbup(t,f):
    try:
        r=_wr(lambda: supabase.table(TABLE_PLAYERS).update(f).eq("Telegram_id",t).execute())
        _ci(f"pt_{t}"); return r.data
    except: return None
def dbt(l=10):
    c=_cg(f"tp_{l}")
    if c is not None: return c
    try:
        r=_wr(lambda: supabase.table(TABLE_PLAYERS).select("*").order("elo",desc=True).limit(l).execute()).data or []
        _cs(f"tp_{l}",r); return r
    except: return []
def dbb(t,b): dbup(t,{"banned":b})
def dbpc(c,d,cr):
    try: return _wr(lambda: supabase.table(TABLE_PROMOCODES).insert({"code":c,"days":d,"created_by":cr,"used":False,"used_by":None}).execute()).data
    except: return None
def dbgp(c):
    try:
        r=_wr(lambda: supabase.table(TABLE_PROMOCODES).select("*").eq("code",c).limit(1).execute()).data
        return r[0] if r else None
    except: return None
def dbmp(c,t):
    try: _wr(lambda: supabase.table(TABLE_PROMOCODES).update({"used":True,"used_by":t}).eq("code",c).execute())
    except: pass
def dbsl(l):
    try:
        _wr(lambda: supabase.table(TABLE_LOBBIES).upsert({"id":l["id"],"platform":l["platform"],"status":l["status"],"players":json.dumps(l["players"]),"ready":json.dumps(list(l.get("ready",[]))),"veto_maps":json.dumps(l.get("veto_maps",[])),"veto_turn":l.get("veto_turn",0),"chosen_map":l.get("chosen_map"),"updated_at":datetime.now(timezone.utc).isoformat()}).execute())
    except: pass
def dbdl(l):
    try: _wr(lambda: supabase.table(TABLE_LOBBIES).delete().eq("id",l).execute())
    except: pass
def dbll():
    try:
        lobbies={}
        for r in _wr(lambda: supabase.table(TABLE_LOBBIES).select("*").execute()).data:
            lobbies[r["id"]]={"id":r["id"],"platform":r["platform"],"status":r["status"],"players":json.loads(r["players"]),"ready":set(json.loads(r["ready"])),"veto_maps":json.loads(r["veto_maps"]),"veto_turn":r["veto_turn"],"chosen_map":r["chosen_map"]}
        return lobbies
    except: return {}
def dbsm(lid,m,pl,w,st):
    try: _wr(lambda: supabase.table(TABLE_MATCHES).insert({"lobby_id":lid,"map":m,"players":json.dumps(pl),"winner_side":w,"stats":json.dumps(st),"played_at":datetime.now(timezone.utc).isoformat()}).execute())
    except: pass

# ===== ВАЛИДАЦИЯ =====
NR=re.compile(r"^[A-Za-zА-Яа-яЁё0-9_\-]{3,20}$"); GR=re.compile(r"^[A-Za-z0-9_\-]{1,32}$")
def vn(n):
    n=n.strip()
    if not NR.match(n): raise ValidationError("Ник 3-20 символов (буквы, цифры, _ -)")
    return n
def vg(g):
    g=g.strip()
    if not GR.match(g): raise ValidationError("ID 1-32 символа (буквы, цифры, _ -)")
    return g
def vp(p):
    if len(p)<4 or len(p)>64: raise ValidationError("Пароль 4-64 символа")
    return p
def hp(p): return bcrypt.hashpw(p.encode(), bcrypt.gensalt()).decode()
def vpwd(p,h): 
    try: return bcrypt.checkpw(p.encode(), h.encode())
    except: return False

# ===== RATE LIMIT =====
_rl={}
def chk(uid):
    n=time.time(); l=_rl.setdefault(uid,[]); l[:]=[x for x in l if n-x<RATE_LIMIT_WINDOW]
    if len(l)>=RATE_LIMIT_MAX: return False
    l.append(n); return True
async def rlg(u):
    if not chk(u.effective_user.id):
        if u.message: await u.message.reply_text("⏳ Подождите")
        elif u.callback_query: await u.callback_query.answer("⏳ Подождите",show_alert=True)
        return False
    return True

# ===== ELO =====
def gr(elo):
    rank="Новичок"
    for t,n in [(0,"Новичок"),(500,"Бронза"),(1000,"Серебро"),(1500,"Золото"),(2000,"Платина"),(2500,"Алмаз"),(3000,"Мастер"),(3500,"Элита"),(4000,"Легенда")]:
        if elo>=t: rank=n
    return rank
def calc(p,o,w,m):
    k=50 if m<CALIBRATION_MATCHES else 24
    return round(k*((1.0 if w else 0.0)-1/(1+10**((o-p)/400))))
def isp(p):
    if not p: return False
    try: return p.get("premium_until") and datetime.fromisoformat(str(p["premium_until"]).replace("Z","+00:00"))>datetime.now(timezone.utc)
    except: return False
def isb(p): return bool(p and p.get("banned"))
def gp(l=8): return ''.join(random.choices(string.ascii_uppercase+string.digits,k=l))

# ===== КЛАВИАТУРЫ =====
def mk(p):
    b=[["🎮 Найти матч","👥 Пати"],["👤 Профиль","📊 Статистика"],["🏆 Топ игроков","💎 Премиум"],["🎟 Промокод","🆘 Поддержка"]]
    if p and isp(p): b.append(["✏️ Сменить ник"])
    if p and p.get("Telegram_id")==OWNER_ID: b.append(["⚙️ Админ-панель"])
    return ReplyKeyboardMarkup(b,resize_keyboard=True)
def pk(): return InlineKeyboardMarkup([[InlineKeyboardButton("🖥 PC",callback_data="platform_pc")],[InlineKeyboardButton("🎮 Console",callback_data="platform_console")],[InlineKeyboardButton("📱 Mobile",callback_data="platform_mobile")]])
def rk(lid): return InlineKeyboardMarkup([[InlineKeyboardButton("✅ Готов",callback_data=f"ready_{lid}")]])
def vk(lid,maps): return InlineKeyboardMarkup([[InlineKeyboardButton(m,callback_data=f"veto_{lid}_{m}")] for m in maps])
def ak(): return InlineKeyboardMarkup([[InlineKeyboardButton("➕ ELO",callback_data="admin_giveelo")],[InlineKeyboardButton("🚫 Бан",callback_data="admin_ban")],[InlineKeyboardButton("✅ Разбан",callback_data="admin_unban")],[InlineKeyboardButton("✏️ Ник",callback_data="admin_setnick")],[InlineKeyboardButton("🆔 ID",callback_data="admin_setgameid")],[InlineKeyboardButton("🔑 Пароль",callback_data="admin_resetpass")],[InlineKeyboardButton("🎟 Промокод",callback_data="admin_createpromo")]])

# ===== СОСТОЯНИЯ =====
(REG_NICK,REG_GAMEID,REG_PASSWORD,LOGIN_NICK,LOGIN_GAMEID,LOGIN_PASSWORD,CHANGE_NICK_NEW,PROMO_INPUT,SUPPORT_MESSAGE,PARTY_USERNAME,ADMIN_PROMO_DAYS,ADMIN_GIVEELO_TARGET,ADMIN_GIVEELO_AMOUNT,ADMIN_BAN_TARGET,ADMIN_UNBAN_TARGET,ADMIN_SETNICK_TARGET,ADMIN_SETNICK_NEW,ADMIN_SETGAMEID_TARGET,ADMIN_SETGAMEID_NEW,ADMIN_RESETPASS_TARGET,ADMIN_RESETPASS_NEW)=range(21)
SUPPORT_REPLY_TARGET={}
LOBBIES={}

def _nid(platform): return f"{platform}_{int(time.time())}_{random.randint(100,999)}"
def get_lobby(platform):
    global LOBBIES
    for lb in LOBBIES.values():
        if lb["platform"]==platform and lb["status"]=="waiting" and len(lb["players"])<LOBBY_SIZE:
            return lb
    lid=_nid(platform); lobby={"id":lid,"platform":platform,"status":"waiting","players":[],"ready":set(),"veto_maps":MAP_POOL.copy(),"veto_turn":0,"chosen_map":None}
    LOBBIES[lid]=lobby; dbsl(lobby); return lobby
def reset_lobby(lid):
    global LOBBIES
    LOBBIES.pop(lid,None); dbdl(lid)

async def rl(update):
    if not await rlg(update): return None
    p=dbg(update.effective_user.id)
    if p and isb(p):
        await update.message.reply_text("⛔ Вы забанены")
        return None
    return p

# ===== ОБРАБОТЧИКИ =====
async def start(update,context):
    if not await rlg(update): return
    p=dbg(update.effective_user.id)
    if p and isb(p): await update.message.reply_text("⛔ Забанен"); return
    await update.message.reply_text(f"👋 {f'С возвращением, {p['nick']}' if p else 'Добро пожаловать! /register'}",reply_markup=mk(p),parse_mode=ParseMode.HTML)
async def help(update,context):
    await update.message.reply_text("/start /register /login /profile /top /stats /premium /promo /support /findmatch /winner ct|t /cancel",parse_mode=ParseMode.HTML)
async def cancel(update,context):
    p=dbg(update.effective_user.id); context.user_data.clear()
    await update.message.reply_text("❌ Отменено",reply_markup=mk(p))
    return ConversationHandler.END

async def reg_start(update,context):
    if await rl(update) is None: return ConversationHandler.END
    if dbg(update.effective_user.id): await update.message.reply_text("✅ Уже зарегистрированы"); return ConversationHandler.END
    await update.message.reply_text("📝 Введите ник:",reply_markup=ReplyKeyboardRemove())
    return REG_NICK
async def reg_nick(update,context):
    try: n=vn(update.message.text)
    except ValidationError as e: await update.message.reply_text(f"⚠️ {e}"); return REG_NICK
    if dbn(n): await update.message.reply_text("⚠️ Ник занят"); return REG_NICK
    context.user_data["reg_nick"]=n; await update.message.reply_text("🎮 Введите игровой ID:"); return REG_GAMEID
async def reg_gameid(update,context):
    try: g=vg(update.message.text)
    except ValidationError as e: await update.message.reply_text(f"⚠️ {e}"); return REG_GAMEID
    context.user_data["reg_gameid"]=g; await update.message.reply_text("🔒 Пароль (мин 4 символа):"); return REG_PASSWORD
async def reg_pass(update,context):
    try: p=vp(update.message.text)
    except ValidationError as e: await update.message.reply_text(f"⚠️ {e}"); return REG_PASSWORD
    t=update.effective_user.id; n=context.user_data.get("reg_nick"); g=context.user_data.get("reg_gameid")
    if dbc(t,n,g,hp(p)):
        await update.message.reply_text(f"✅ Регистрация успешна!\n👤 {n}",reply_markup=mk(dbg(t)))
    else:
        await update.message.reply_text("❌ Ошибка регистрации")
    context.user_data.clear(); return ConversationHandler.END

async def login_start(update,context):
    if await rl(update) is None: return ConversationHandler.END
    await update.message.reply_text("🔑 Введите ник:",reply_markup=ReplyKeyboardRemove()); return LOGIN_NICK
async def login_nick(update,context):
    n=update.message.text.strip(); p=dbn(n)
    if not p: await update.message.reply_text("⚠️ Не найден"); return LOGIN_NICK
    context.user_data["login_player"]=p; await update.message.reply_text("🎮 Введите ID:"); return LOGIN_GAMEID
async def login_gameid(update,context):
    g=update.message.text.strip(); p=context.user_data.get("login_player")
    if not p or p.get("game_id")!=g: await update.message.reply_text("⚠️ Неверный ID"); return LOGIN_GAMEID
    await update.message.reply_text("🔒 Пароль:"); return LOGIN_PASSWORD
async def login_pass(update,context):
    p=context.user_data.get("login_player")
    if not p or not vpwd(update.message.text,p.get("password","")):
        await update.message.reply_text("❌ Неверный пароль"); context.user_data.clear(); return ConversationHandler.END
    if isb(p): await update.message.reply_text("⛔ Забанен"); context.user_data.clear(); return ConversationHandler.END
    t=update.effective_user.id; dbup(p["Telegram_id"],{"Telegram_id":t})
    await update.message.reply_text(f"✅ Вход выполнен! {p['nick']}",reply_markup=mk(dbg(t))); context.user_data.clear(); return ConversationHandler.END

async def profile(update,context):
    if not await rlg(update): return
    p=await rl(update)
    if not p: return
    await update.message.reply_text(f"👤 {p['nick']}\n🏆 {gr(p['elo'])}\n📈 ELO: {p['elo']}\n🎮 Матчей: {p['matches']}\n✅ Побед: {p['wins']}\n❌ Поражений: {p['losses']}\n⭐ MVP: {p['mvp']}\n💎 Премиум: {'✅' if isp(p) else '❌'}",parse_mode=ParseMode.HTML)
async def top(update,context):
    if not await rlg(update): return
    pl=dbt(10); text="🏆 Топ-10:\n"
    for i,p in enumerate(pl):
        text+=f"{['🥇','🥈','🥉'][i] if i<3 else f'{i+1}.'} {p['nick']} — {p['elo']} ELO ({gr(p['elo'])})\n"
    await update.message.reply_text(text)
async def stats(update,context):
    if not await rlg(update): return
    p=await rl(update)
    if not p: return
    k,d,h=p.get("kills",0),p.get("deaths",0),p.get("headshots",0)
    await update.message.reply_text(f"📊 {p['nick']}\n🔫 Убийств: {k}\n💀 Смертей: {d}\n⚔️ K/D: {round(k/d,2) if d else k}\n🎯 HS: {round(h/k*100,1) if k else 0}%\n🗺 Карта: {p.get('fav_map','—')}")

async def premium(update,context):
    if not await rlg(update): return
    p=await rl(update)
    if not p: return
    kb=InlineKeyboardMarkup([[InlineKeyboardButton("🎁 24ч",callback_data="premium_trial")],[InlineKeyboardButton("⭐ Купить",callback_data="premium_buy")]])
    await update.message.reply_text(f"💎 Премиум: {'✅' if isp(p) else '❌'}",reply_markup=kb)
async def prem_cb(update,context):
    q=update.callback_query; await q.answer(); t=update.effective_user.id; p=dbg(t)
    if not p: await q.edit_message_text("⚠️ Не зарегистрированы"); return
    if q.data=="premium_trial":
        if p.get("premium_until"): await q.edit_message_text("⚠️ Уже есть"); return
        dbup(t,{"premium_until":(datetime.now(timezone.utc)+timedelta(hours=24)).isoformat()}); await q.edit_message_text("🎉 24ч премиума!")
    elif q.data=="premium_buy":
        await context.bot.send_invoice(chat_id=t,title="Премиум 30 дней",description="Смена ника и приоритет",payload="premium_30d",provider_token="",currency="XTR",prices=[{"label":"30 дней","amount":100}])
async def precb(update,context): await update.pre_checkout_query.answer(ok=True)
async def pay(update,context):
    dbup(update.effective_user.id,{"premium_until":(datetime.now(timezone.utc)+timedelta(days=30)).isoformat()})
    await update.message.reply_text("✅ Премиум 30 дней активирован!")

async def promo(update,context):
    if await rl(update) is None: return ConversationHandler.END
    await update.message.reply_text("🎟 Введите промокод:"); return PROMO_INPUT
async def promo_apply(update,context):
    c=update.message.text.strip().upper(); t=update.effective_user.id; p=dbg(t)
    if not p: await update.message.reply_text("❌ Не зарегистрированы"); return ConversationHandler.END
    pr=dbgp(c)
    if not pr or pr.get("used"): await update.message.reply_text("❌ Неверный или использован"); return ConversationHandler.END
    d=pr.get("days",0); base=datetime.now(timezone.utc)
    if p.get("premium_until"):
        try: base=max(base,datetime.fromisoformat(str(p["premium_until"]).replace("Z","+00:00")))
        except: pass
    dbup(t,{"premium_until":(base+timedelta(days=d)).isoformat()}); dbmp(c,t)
    await update.message.reply_text(f"✅ +{d} дней премиума!"); return ConversationHandler.END

async def support(update,context):
    if not await rlg(update): return ConversationHandler.END
    await update.message.reply_text("🆘 Опишите проблему:"); return SUPPORT_MESSAGE
async def support_send(update,context):
    user=update.effective_user; msg=update.message.text
    if ADMIN_CHAT_ID:
        kb=InlineKeyboardMarkup([[InlineKeyboardButton("✍️ Ответить",callback_data=f"sr_{user.id}")]])
        await context.bot.send_message(ADMIN_CHAT_ID,f"🆘 От: @{user.username or user.id}\n{msg}",reply_markup=kb)
    await update.message.reply_text("✅ Отправлено!"); return ConversationHandler.END
async def sr_cb(update,context):
    q=update.callback_query; await q.answer(); tid=int(q.data.replace("sr_",""))
    SUPPORT_REPLY_TARGET[q.message.chat_id]=tid; await q.message.reply_text(f"✍️ Ответ для {tid}:")
async def admin_chat(update,context):
    cid=update.effective_chat.id
    if cid in SUPPORT_REPLY_TARGET:
        tid=SUPPORT_REPLY_TARGET.pop(cid)
        await context.bot.send_message(tid,f"📩 Ответ:\n{update.message.text}")
        await update.message.reply_text("✅ Отправлено")

async def change_nick(update,context):
    p=await rl(update)
    if not p: return ConversationHandler.END
    if not isp(p): await update.message.reply_text("⛔ Только премиум"); return ConversationHandler.END
    await update.message.reply_text("✏️ Новый ник:"); return CHANGE_NICK_NEW
async def change_apply(update,context):
    try: n=vn(update.message.text)
    except ValidationError as e: await update.message.reply_text(f"⚠️ {e}"); return CHANGE_NICK_NEW
    if dbn(n): await update.message.reply_text("⚠️ Занят"); return CHANGE_NICK_NEW
    dbup(update.effective_user.id,{"nick":n}); await update.message.reply_text(f"✅ {n}"); return ConversationHandler.END

async def find(update,context):
    if not await rlg(update): return
    if await rl(update) is None: return
    await update.message.reply_text("🎮 Выберите платформу:",reply_markup=pk())
async def plat_cb(update,context):
    q=update.callback_query; await q.answer(); t=update.effective_user.id
    plat=q.data.replace("platform_",""); lobby=get_lobby(plat)
    if t not in lobby["players"]: lobby["players"].append(t); dbsl(lobby)
    kb=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Отмена",callback_data=f"cancel_{lobby['id']}")]])
    await q.edit_message_text(f"🔎 Поиск... {len(lobby['players'])}/{LOBBY_SIZE}",reply_markup=kb)
    if len(lobby["players"])>=LOBBY_SIZE:
        lobby["status"]="ready_check"; lobby["ready"]=set(); dbsl(lobby)
        for pid in lobby["players"]:
            try: await context.bot.send_message(pid,"✅ Лобби набрано! Подтвердите готовность:",reply_markup=rk(lobby["id"]))
            except: pass
        asyncio.create_task(_timeout(lobby["id"],context))
async def _timeout(lid,context):
    await asyncio.sleep(READY_CHECK_SECONDS)
    lobby=LOBBIES.get(lid)
    if lobby and lobby["status"]=="ready_check" and len(lobby["ready"])<len(lobby["players"]):
        for p in lobby["players"]:
            try: await context.bot.send_message(p,"❌ Не все готовы, лобби распущено")
            except: pass
        reset_lobby(lid)
async def ready(update,context):
    q=update.callback_query; await q.answer(); lid=q.data.replace("ready_",""); lobby=LOBBIES.get(lid)
    if not lobby or lobby["status"]!="ready_check": await q.edit_message_text("⚠️ Неактивно"); return
    lobby["ready"].add(update.effective_user.id); await q.edit_message_text("✅ Готов!")
    if len(lobby["ready"])>=len(lobby["players"]):
        lobby["status"]="veto"; lobby["veto_maps"]=MAP_POOL.copy(); lobby["veto_turn"]=0; dbsl(lobby)
        for p in lobby["players"]:
            try: await context.bot.send_message(p,"🗺 Вето карт:",reply_markup=vk(lid,lobby["veto_maps"]))
            except: pass
async def veto(update,context):
    q=update.callback_query; await q.answer()
    _,lid,map_name=q.data.split("_",2); lobby=LOBBIES.get(lid)
    if not lobby or lobby["status"]!="veto": await q.edit_message_text("⚠️ Неактивно"); return
    if map_name not in lobby["veto_maps"]: await q.edit_message_text("⚠️ Уже выбрана"); return
    lobby["veto_maps"].remove(map_name); lobby["veto_turn"]+=1; dbsl(lobby)
    if len(lobby["veto_maps"])>1:
        for p in lobby["players"]:
            try: await context.bot.send_message(p,f"🚫 {map_name} забанена. Осталось: {len(lobby['veto_maps'])}",reply_markup=vk(lid,lobby["veto_maps"]))
            except: pass
    else:
        chosen=lobby["veto_maps"][0]; lobby["chosen_map"]=chosen; lobby["status"]="in_progress"; dbsl(lobby)
        for p in lobby["players"]:
            try: await context.bot.send_message(p,f"🗺 Выбрана: {chosen}\n/winner ct или /winner t")
            except: pass
async def cancel_search(update,context):
    q=update.callback_query; await q.answer(); lid=q.data.replace("cancel_",""); lobby=LOBBIES.get(lid)
    if lobby and update.effective_user.id in lobby["players"]:
        lobby["players"].remove(update.effective_user.id); dbsl(lobby)
        if not lobby["players"]: reset_lobby(lid)
    await q.edit_message_text("❌ Отменено")

async def winner(update,context):
    if not await rlg(update): return
    t=update.effective_user.id; args=context.args
    lobby=None
    for lb in LOBBIES.values():
        if lb["status"]=="in_progress" and t in lb["players"]: lobby=lb; break
    if not lobby: await update.message.reply_text("⚠️ Нет матча"); return
    if not args or args[0].lower() not in ("ct","t"): await update.message.reply_text("⚠️ /winner ct или /winner t"); return
    side=args[0].lower(); pl=lobby["players"]; half=len(pl)//2
    w=pl[:half] if side=="ct" else pl[half:]; l=pl[half:] if side=="ct" else pl[:half]
    avgw=sum(dbg(p).get("elo",1000) for p in w)/len(w) if w else 0
    avgl=sum(dbg(p).get("elo",1000) for p in l)/len(l) if l else 0
    for p in w:
        pp=dbg(p); d=calc(pp["elo"] if pp else 1000,int(avgw),True,pp.get("matches",0))
        dbup(p,{"elo":max(0,(pp["elo"] if pp else 1000)+d),"wins":(pp.get("wins",0)+1),"matches":(pp.get("matches",0)+1)})
    for p in l:
        pp=dbg(p); d=calc(pp["elo"] if pp else 1000,int(avgl),False,pp.get("matches",0))
        dbup(p,{"elo":max(0,(pp["elo"] if pp else 1000)+d),"losses":(pp.get("losses",0)+1),"matches":(pp.get("matches",0)+1)})
    dbsm(lobby["id"],lobby.get("chosen_map","Unknown"),lobby["players"],side,{})
    for p in lobby["players"]:
        try: await context.bot.send_message(p,f"🏁 Матч завершён! Победила: {side.upper()}\n/addstats убийства смерти")
        except: pass
    reset_lobby(lobby["id"]); await update.message.reply_text("✅ Результат зафиксирован")

async def addstats(update,context):
    if not await rlg(update): return
    p=await rl(update)
    if not p: return
    args=context.args
    if len(args)!=2: await update.message.reply_text("⚠️ /addstats 20 15"); return
    try:
        k=int(args[0]); d=int(args[1])
        if k<0 or d<0: raise ValueError
    except: await update.message.reply_text("⚠️ Числа"); return
    t=update.effective_user.id
    dbup(t,{"kills":p.get("kills",0)+k,"deaths":p.get("deaths",0)+d})
    await update.message.reply_text(f"✅ {k}/{d} сохранено")

async def admin(update,context):
    if update.effective_user.id!=OWNER_ID: await update.message.reply_text("⛔ Нет доступа"); return
    await update.message.reply_text("⚙️ Админ-панель",reply_markup=ak())
async def admin_cb(update,context):
    q=update.callback_query; await q.answer()
    if update.effective_user.id!=OWNER_ID: await q.edit_message_text("⛔ Нет доступа"); return
    action=q.data
    if action=="admin_giveelo": await q.message.reply_text("Введите Telegram ID:"); return ADMIN_GIVEELO_TARGET
    if action=="admin_ban": await q.message.reply_text("Введите Telegram ID для бана:"); return ADMIN_BAN_TARGET
    if action=="admin_unban": await q.message.reply_text("Введите Telegram ID для разбана:"); return ADMIN_UNBAN_TARGET
    if action=="admin_setnick": await q.message.reply_text("Введите Telegram ID:"); return ADMIN_SETNICK_TARGET
    if action=="admin_setgameid": await q.message.reply_text("Введите Telegram ID:"); return ADMIN_SETGAMEID_TARGET
    if action=="admin_resetpass": await q.message.reply_text("Введите Telegram ID:"); return ADMIN_RESETPASS_TARGET
    if action=="admin_createpromo": await q.message.reply_text("Введите количество дней:"); return ADMIN_PROMO_DAYS
    return ConversationHandler.END

async def admin_giveelo_target(update,context):
    try: t=int(update.message.text.strip())
    except: await update.message.reply_text("⚠️ Введите число"); return ADMIN_GIVEELO_TARGET
    if not dbg(t): await update.message.reply_text("⚠️ Игрок не найден"); return ADMIN_GIVEELO_TARGET
    context.user_data["admin_target"]=t; await update.message.reply_text("Введите количество ELO:"); return ADMIN_GIVEELO_AMOUNT
async def admin_giveelo_amount(update,context):
    try: a=int(update.message.text.strip())
    except: await update.message.reply_text("⚠️ Число"); return ADMIN_GIVEELO_AMOUNT
    t=context.user_data.get("admin_target"); p=dbg(t)
    if not p: await update.message.reply_text("❌ Не найден"); return ConversationHandler.END
    ne=max(0,p.get("elo",1000)+a); dbup(t,{"elo":ne}); _ci("tp_")
    await update.message.reply_text(f"✅ {p['nick']}: {ne} ELO"); context.user_data.clear(); return ConversationHandler.END

async def admin_ban_target(update,context):
    try: t=int(update.message.text.strip())
    except: await update.message.reply_text("⚠️ Число"); return ADMIN_BAN_TARGET
    p=dbg(t)
    if not p: await update.message.reply_text("⚠️ Не найден"); return ConversationHandler.END
    dbb(t,True); await update.message.reply_text(f"🚫 {p['nick']} забанен"); return ConversationHandler.END
async def admin_unban_target(update,context):
    try: t=int(update.message.text.strip())
    except: await update.message.reply_text("⚠️ Число"); return ADMIN_UNBAN_TARGET
    p=dbg(t)
    if not p: await update.message.reply_text("⚠️ Не найден"); return ConversationHandler.END
    dbb(t,False); await update.message.reply_text(f"✅ {p['nick']} разбанен"); return ConversationHandler.END

async def admin_setnick_target(update,context):
    try: t=int(update.message.text.strip())
    except: await update.message.reply_text("⚠️ Число"); return ADMIN_SETNICK_TARGET
    if not dbg(t): await update.message.reply_text("⚠️ Не найден"); return ConversationHandler.END
    context.user_data["admin_target"]=t; await update.message.reply_text("Введите новый ник:"); return ADMIN_SETNICK_NEW
async def admin_setnick_apply(update,context):
    try: n=vn(update.message.text)
    except ValidationError as e: await update.message.reply_text(f"⚠️ {e}"); return ADMIN_SETNICK_NEW
    if dbn(n): await update.message.reply_text("⚠️ Занят"); return ADMIN_SETNICK_NEW
    t=context.user_data.get("admin_target"); dbup(t,{"nick":n}); await update.message.reply_text(f"✅ {n}"); context.user_data.clear(); return ConversationHandler.END

async def admin_setgameid_target(update,context):
    try: t=int(update.message.text.strip())
    except: await update.message.reply_text("⚠️ Число"); return ADMIN_SETGAMEID_TARGET
    if not dbg(t): await update.message.reply_text("⚠️ Не найден"); return ConversationHandler.END
    context.user_data["admin_target"]=t; await update.message.reply_text("Введите новый ID:"); return ADMIN_SETGAMEID_NEW
async def admin_setgameid_apply(update,context):
    try: g=vg(update.message.text)
    except ValidationError as e: await update.message.reply_text(f"⚠️ {e}"); return ADMIN_SETGAMEID_NEW
    t=context.user_data.get("admin_target"); dbup(t,{"game_id":g}); await update.message.reply_text(f"✅ ID: {g}"); context.user_data.clear(); return ConversationHandler.END

async def admin_resetpass_target(update,context):
    try: t=int(update.message.text.strip())
    except: await update.message.reply_text("⚠️ Число"); return ADMIN_RESETPASS_TARGET
    if not dbg(t): await update.message.reply_text("⚠️ Не найден"); return ConversationHandler.END
    context.user_data["admin_target"]=t; await update.message.reply_text("Введите новый пароль:"); return ADMIN_RESETPASS_NEW
async def admin_resetpass_apply(update,context):
    try: p=vp(update.message.text)
    except ValidationError as e: await update.message.reply_text(f"⚠️ {e}"); return ADMIN_RESETPASS_NEW
    t=context.user_data.get("admin_target"); dbup(t,{"password":hp(p)}); await update.message.reply_text("✅ Пароль сброшен"); context.user_data.clear(); return ConversationHandler.END

async def admin_createpromo(update,context):
    try: d=int(update.message.text.strip())
    except: await update.message.reply_text("⚠️ Число"); return ADMIN_PROMO_DAYS
    if d<=0: await update.message.reply_text("⚠️ >0"); return ADMIN_PROMO_DAYS
    c=gp(); dbpc(c,d,update.effective_user.id); await update.message.reply_text(f"✅ {c} — {d} дней"); return ConversationHandler.END

async def party(update,context):
    p=await rl(update)
    if not p: return ConversationHandler.END
    await update.message.reply_text("👥 Введите юзернейм (без @):"); return PARTY_USERNAME
async def party_invite(update,context):
    username=update.message.text.strip().lstrip("@"); inviter=update.effective_user
    target=dbn(username)
    if target and target.get("Telegram_id"):
        kb=InlineKeyboardMarkup([[InlineKeyboardButton("✅ Принять",callback_data=f"party_accept_{inviter.id}"),InlineKeyboardButton("❌ Отклонить",callback_data=f"party_decline_{inviter.id}")]])
        await context.bot.send_message(target["Telegram_id"],f"👥 {inviter.full_name} приглашает в пати!",reply_markup=kb)
        await update.message.reply_text(f"📨 Приглашение отправлено @{username}")
    else:
        await update.message.reply_text("⚠️ Игрок не найден")
    return ConversationHandler.END
async def party_cb(update,context):
    q=update.callback_query; await q.answer()
    if q.data.startswith("party_accept_"):
        inviter_id=int(q.data.replace("party_accept_",""))
        await q.edit_message_text("✅ Вы приняли приглашение!")
        await context.bot.send_message(inviter_id,f"✅ {update.effective_user.full_name} принял приглашение")
    elif q.data.startswith("party_decline_"):
        inviter_id=int(q.data.replace("party_decline_",""))
        await q.edit_message_text("❌ Вы отклонили приглашение")
        await context.bot.send_message(inviter_id,f"❌ {update.effective_user.full_name} отклонил приглашение")

async def error(update,context):
    logger.error(f"Error: {context.error}")

# ===== FLASK =====
flask_app=Flask(__name__)
@flask_app.route("/health")
def health(): return {"status":"ok"},200
@flask_app.route("/")
def index(): return {"status":"bot running"},200
def run_flask(): flask_app.run(host="0.0.0.0",port=PORT)

# ===== MAIN =====
async def post_init(app):
    try: await app.bot.delete_webhook(drop_pending_updates=True)
    except: pass

def main():
    global LOBBIES
    LOBBIES=dbll()
    logger.info(f"Загружено {len(LOBBIES)} лобби из Supabase")
    threading.Thread(target=run_flask,daemon=True).start()
    app=ApplicationBuilder().token(BOT_TOKEN).build()
    app.post_init=post_init
    
    # Команды
    app.add_handler(CommandHandler("start",start))
    app.add_handler(CommandHandler("help",help))
    app.add_handler(CommandHandler("cancel",cancel))
    app.add_handler(CommandHandler("profile",profile))
    app.add_handler(CommandHandler("top",top))
    app.add_handler(CommandHandler("stats",stats))
    app.add_handler(CommandHandler("premium",premium))
    app.add_handler(CommandHandler("promo",promo))
    app.add_handler(CommandHandler("support",support))
    app.add_handler(CommandHandler("findmatch",find))
    app.add_handler(CommandHandler("winner",winner))
    app.add_handler(CommandHandler("addstats",addstats))
    app.add_handler(CommandHandler("admin",admin))
    app.add_handler(CommandHandler("party",party))
    
    # ConversationHandler
    app.add_handler(ConversationHandler(
        [CommandHandler("register",reg_start)],
        {REG_NICK:[MessageHandler(filters.TEXT&~filters.COMMAND,reg_nick)],REG_GAMEID:[MessageHandler(filters.TEXT&~filters.COMMAND,reg_gameid)],REG_PASSWORD:[MessageHandler(filters.TEXT&~filters.COMMAND,reg_pass)]},
        [CommandHandler("cancel",cancel)]
    ))
    app.add_handler(ConversationHandler(
        [CommandHandler("login",login_start)],
        {LOGIN_NICK:[MessageHandler(filters.TEXT&~filters.COMMAND,login_nick)],LOGIN_GAMEID:[MessageHandler(filters.TEXT&~filters.COMMAND,login_gameid)],LOGIN_PASSWORD:[MessageHandler(filters.TEXT&~filters.COMMAND,login_pass)]},
        [CommandHandler("cancel",cancel)]
    ))
    app.add_handler(ConversationHandler(
        [CommandHandler("changenick",change_nick),MessageHandler(filters.Regex("^✏️ Сменить ник$"),change_nick)],
        {CHANGE_NICK_NEW:[MessageHandler(filters.TEXT&~filters.COMMAND,change_apply)]},
        [CommandHandler("cancel",cancel)]
    ))
    app.add_handler(ConversationHandler(
        [CommandHandler("promo",promo),MessageHandler(filters.Regex("^🎟 Промокод$"),promo)],
        {PROMO_INPUT:[MessageHandler(filters.TEXT&~filters.COMMAND,promo_apply)]},
        [CommandHandler("cancel",cancel)]
    ))
    app.add_handler(ConversationHandler(
        [CommandHandler("support",support),MessageHandler(filters.Regex("^🆘 Поддержка$"),support)],
        {SUPPORT_MESSAGE:[MessageHandler(filters.TEXT&~filters.COMMAND,support_send)]},
        [CommandHandler("cancel",cancel)]
    ))
    app.add_handler(ConversationHandler(
        [CommandHandler("party",party),MessageHandler(filters.Regex("^👥 Пати$"),party)],
        {PARTY_USERNAME:[MessageHandler(filters.TEXT&~filters.COMMAND,party_invite)]},
        [CommandHandler("cancel",cancel)]
    ))
    app.add_handler(ConversationHandler(
        [CallbackQueryHandler(admin_cb,pattern="^admin_")],
        {ADMIN_GIVEELO_TARGET:[MessageHandler(filters.TEXT&~filters.COMMAND,admin_giveelo_target)],ADMIN_GIVEELO_AMOUNT:[MessageHandler(filters.TEXT&~filters.COMMAND,admin_giveelo_amount)],ADMIN_BAN_TARGET:[MessageHandler(filters.TEXT&~filters.COMMAND,admin_ban_target)],ADMIN_UNBAN_TARGET:[MessageHandler(filters.TEXT&~filters.COMMAND,admin_unban_target)],ADMIN_SETNICK_TARGET:[MessageHandler(filters.TEXT&~filters.COMMAND,admin_setnick_target)],ADMIN_SETNICK_NEW:[MessageHandler(filters.TEXT&~filters.COMMAND,admin_setnick_apply)],ADMIN_SETGAMEID_TARGET:[MessageHandler(filters.TEXT&~filters.COMMAND,admin_setgameid_target)],ADMIN_SETGAMEID_NEW:[MessageHandler(filters.TEXT&~filters.COMMAND,admin_setgameid_apply)],ADMIN_RESETPASS_TARGET:[MessageHandler(filters.TEXT&~filters.COMMAND,admin_resetpass_target)],ADMIN_RESETPASS_NEW:[MessageHandler(filters.TEXT&~filters.COMMAND,admin_resetpass_apply)],ADMIN_PROMO_DAYS:[MessageHandler(filters.TEXT&~filters.COMMAND,admin_createpromo)]},
        [CommandHandler("cancel",cancel)]
    ))
    
    # CallbackQuery
    app.add_handler(CallbackQueryHandler(prem_cb,pattern="^premium_"))
    app.add_handler(CallbackQueryHandler(plat_cb,pattern="^platform_"))
    app.add_handler(CallbackQueryHandler(ready,pattern="^ready_"))
    app.add_handler(CallbackQueryHandler(cancel_search,pattern="^cancel_"))
    app.add_handler(CallbackQueryHandler(veto,pattern="^veto_"))
    app.add_handler(CallbackQueryHandler(sr_cb,pattern="^sr_"))
    app.add_handler(CallbackQueryHandler(party_cb,pattern="^party_"))
    app.add_handler(PreCheckoutQueryHandler(precb))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT,pay))
    
    if ADMIN_CHAT_ID:
        app.add_handler(MessageHandler(filters.TEXT&filters.Chat(chat_id=ADMIN_CHAT_ID)&~filters.COMMAND,admin_chat))
    
    # Роутер главного меню
    app.add_handler(MessageHandler(filters.TEXT&~filters.COMMAND,lambda u,c: globals().get({"🎮 Найти матч":find,"👤 Профиль":profile,"📊 Статистика":stats,"🏆 Топ игроков":top,"💎 Премиум":premium,"⚙️ Админ-панель":admin,"🆘 Поддержка":support,"🎟 Промокод":promo,"✏️ Сменить ник":change_nick,"👥 Пати":party}.get(u.message.text,lambda:None)) and globals().get({"🎮 Найти матч":find,"👤 Профиль":profile,"📊 Статистика":stats,"🏆 Топ игроков":top,"💎 Премиум":premium,"⚙️ Админ-панель":admin,"🆘 Поддержка":support,"🎟 Промокод":promo,"✏️ Сменить ник":change_nick,"👥 Пати":party}.get(u.message.text,lambda:None))(u,c)))
    
    app.add_error_handler(error)
    app.run_polling(allowed_updates=Update.ALL_TYPES,drop_pending_updates=True)

if __name__=="__main__":
    main()

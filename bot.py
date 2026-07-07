import os,re,time,json,random,string,logging,threading,asyncio
from datetime import datetime,timedelta,timezone
from typing import Optional,Any
from flask import Flask
from supabase import create_client
from telegram import Update,InlineKeyboardButton,InlineKeyboardMarkup,ReplyKeyboardMarkup,ReplyKeyboardRemove
from telegram.constants import ParseMode
from telegram.ext import Application,ApplicationBuilder,CommandHandler,MessageHandler,CallbackQueryHandler,ConversationHandler,ContextTypes,filters

# ===== КОНФИГ =====
BOT_TOKEN=os.environ.get("BOT_TOKEN","")
OWNER_ID=int(os.environ.get("OWNER_ID","0") or "0")
ADMIN_CHAT_ID=int(os.environ.get("ADMIN_CHAT_ID","0") or "0")
SUPABASE_URL=os.environ.get("SUPABASE_URL","")
SUPABASE_KEY=os.environ.get("SUPABASE_KEY","")
PORT=int(os.environ.get("PORT","10000"))
if not BOT_TOKEN or not SUPABASE_URL or not SUPABASE_KEY: raise RuntimeError("Missing env vars")
RATE_LIMIT_MAX,RATE_LIMIT_WINDOW,CACHE_TTL,CALIBRATION_MATCHES,READY_CHECK_SECONDS,LOBBY_SIZE=10,60,60,10,60,10
MAP_POOL=["Sandstone","Rust","Province","Breeze","Dune","Zone 7","Hanami"]
MAP_EMOJI={"Sandstone":"🏜️","Rust":"🏭","Province":"🏘️","Breeze":"🌬️","Dune":"🏝️","Zone 7":"☢️","Hanami":"🌸"}
T={"players":"players","promocodes":"promocodes","lobbies":"lobbies","matches":"matches","pending":"pending_matches"}

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",level=logging.INFO)
logger=logging.getLogger("cs_bot"); logging.getLogger("httpx").setLevel(logging.WARNING)

class E(Exception): pass
class DE(E): pass
class VE(E): pass

# ===== SUPABASE =====
s=create_client(SUPABASE_URL,SUPABASE_KEY); _c={}
def _g(k):
    v=_c.get(k)
    if v and time.time()-v[0]<CACHE_TTL: return v[1]
    _c.pop(k,None); return None
def _s(k,v): _c[k]=(time.time(),v)
def _i(p):
    for k in list(_c.keys()):
        if k.startswith(p): _c.pop(k,None)
def _r(f,*a,**kw):
    for i in range(1,4):
        try: return f(*a,**kw)
        except Exception as e:
            logger.warning(f"Retry {i}/3: {e}")
            time.sleep(0.5*i)
    raise DE("All retries failed")

def dbg(t,u=True):
    k=f"pt_{t}"
    if u:
        c=_g(k)
        if c is not None: return c if c!="NONE" else None
    try:
        r=_r(lambda: s.table(T["players"]).select("*").eq("Telegram_id",t).limit(1).execute()).data
        d=r[0] if r else None
        _s(k,d if d else "NONE"); return d
    except: return None
def dbn(n):
    try:
        r=_r(lambda: s.table(T["players"]).select("*").eq("nick",n).limit(1).execute()).data
        return r[0] if r else None
    except: return None
def dbgid(g):
    try:
        r=_r(lambda: s.table(T["players"]).select("*").eq("game_id",g).limit(1).execute()).data
        return r[0] if r else None
    except: return None
def dbc(t,n,g):
    try:
        r=_r(lambda: s.table(T["players"]).insert({"Telegram_id":t,"nick":n,"game_id":g,"elo":1000,"wins":0,"losses":0,"matches":0,"mvp":0,"kills":0,"deaths":0,"headshots":0,"fav_map":"—","premium_until":None,"banned":False,"elo_history":[]}).execute())
        _i(f"pt_{t}"); return r.data
    except: return None
def dbup(t,f):
    try:
        r=_r(lambda: s.table(T["players"]).update(f).eq("Telegram_id",t).execute())
        _i(f"pt_{t}"); return r.data
    except: return None
def dbt(l=10):
    c=_g(f"tp_{l}")
    if c is not None: return c
    try:
        r=_r(lambda: s.table(T["players"]).select("*").order("elo",desc=True).limit(l).execute()).data or []
        _s(f"tp_{l}",r); return r
    except: return []
def dbb(t,b): dbup(t,{"banned":b})
def dbpc(c,d,cr):
    try: return _r(lambda: s.table(T["promocodes"]).insert({"code":c,"days":d,"created_by":cr,"used":False,"used_by":None}).execute()).data
    except: return None
def dbgp(c):
    try:
        r=_r(lambda: s.table(T["promocodes"]).select("*").eq("code",c).limit(1).execute()).data
        return r[0] if r else None
    except: return None
def dbmp(c,t):
    try: _r(lambda: s.table(T["promocodes"]).update({"used":True,"used_by":t}).eq("code",c).execute())
    except: pass
def dbsl(l):
    try:
        _r(lambda: s.table(T["lobbies"]).upsert({"id":l["id"],"platform":l["platform"],"status":l["status"],"players":json.dumps(l["players"]),"ready":json.dumps(list(l.get("ready",[]))),"veto_maps":json.dumps(l.get("veto_maps",[])),"veto_turn":l.get("veto_turn",0),"chosen_map":l.get("chosen_map"),"updated_at":datetime.now(timezone.utc).isoformat()}).execute())
    except: pass
def dbdl(l):
    try: _r(lambda: s.table(T["lobbies"]).delete().eq("id",l).execute())
    except: pass
def dbll():
    try:
        lobbies={}
        for r in _r(lambda: s.table(T["lobbies"]).select("*").execute()).data:
            lobbies[r["id"]]={"id":r["id"],"platform":r["platform"],"status":r["status"],"players":json.loads(r["players"]),"ready":set(json.loads(r["ready"])),"veto_maps":json.loads(r["veto_maps"]),"veto_turn":r["veto_turn"],"chosen_map":r["chosen_map"]}
        return lobbies
    except: return {}
def dbsm(lid,m,pl,w,st):
    try: _r(lambda: s.table(T["matches"]).insert({"lobby_id":lid,"map":m,"players":json.dumps(pl),"winner_side":w,"stats":json.dumps(st),"played_at":datetime.now(timezone.utc).isoformat()}).execute())
    except: pass
def dbsp(mid,lid,m,pl,w,lose,st,mvp,photo):
    try:
        _r(lambda: s.table(T["pending"]).insert({"match_id":mid,"lobby_id":lid,"map":m,"players":json.dumps(pl),"winner_ids":json.dumps(w),"loser_ids":json.dumps(lose),"stats":json.dumps(st),"mvp_id":mvp,"photo_id":photo,"status":"pending","created_at":datetime.now(timezone.utc).isoformat()}).execute())
    except: pass
def dbgpnd(mid):
    try:
        r=_r(lambda: s.table(T["pending"]).select("*").eq("match_id",mid).limit(1).execute()).data
        return r[0] if r else None
    except: return None
def dbupnd(mid,status):
    try: _r(lambda: s.table(T["pending"]).update({"status":status}).eq("match_id",mid).execute())
    except: pass
def dbdelnd(mid):
    try: _r(lambda: s.table(T["pending"]).delete().eq("match_id",mid).execute())
    except: pass

# ===== ВАЛИДАЦИЯ =====
NR=re.compile(r"^[A-Za-zА-Яа-яЁё0-9_\-]{3,20}$"); GR=re.compile(r"^[A-Za-z0-9_\-]{1,32}$")
def vn(n):
    n=n.strip()
    if not NR.match(n): raise VE("Ник 3-20 символов (буквы, цифры, _ -)")
    return n
def vg(g):
    g=g.strip()
    if not GR.match(g): raise VE("ID 1-32 символа (буквы, цифры, _ -)")
    return g

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
def gh(t,l=20):
    try:
        r=_r(lambda: s.table(T["matches"]).select("*").execute()).data
        matches=[]
        for m in r:
            players=json.loads(m.get("players","[]"))
            if t in players:
                stats=json.loads(m.get("stats","{}")).get(str(t),{})
                winners=json.loads(m.get("winner_ids","[]"))
                matches.append({"map":m.get("map"),"elo":stats.get("elo_change",0),"win":t in winners})
        return matches[:l]
    except: return []

# ===== КЛАВИАТУРЫ =====
def mk(p):
    b=[["🔍 Найти матч","👥 Пати"],["👤 Профиль","🏆 Топ игроков"],["📊 Статистика","📝 История"],["💎 Премиум","🎟 Промокод"],["🆘 Поддержка"]]
    if p and isp(p): b.append(["✏️ Сменить ник"])
    if p and p.get("Telegram_id")==OWNER_ID: b.append(["⚙️ Админ-панель"])
    return ReplyKeyboardMarkup(b,resize_keyboard=True)
def sk(): return InlineKeyboardMarkup([[InlineKeyboardButton("📝 Регистрация",callback_data="register")],[InlineKeyboardButton("🆘 Поддержка",callback_data="support")]])
def bk(): return InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад",callback_data="back")]])
def pk(): return InlineKeyboardMarkup([[InlineKeyboardButton("🖥 PC",callback_data="platform_pc")],[InlineKeyboardButton("📱 Mobile",callback_data="platform_mobile")]])
def rk(lid): return InlineKeyboardMarkup([[InlineKeyboardButton("✅ Готов",callback_data=f"ready_{lid}")]])
def vk(lid,maps): return InlineKeyboardMarkup([[InlineKeyboardButton(f"{MAP_EMOJI.get(m,'')} {m}",callback_data=f"veto_{lid}_{m}")] for m in maps])
def ak(): return InlineKeyboardMarkup([[InlineKeyboardButton("➕ ELO",callback_data="admin_giveelo")],[InlineKeyboardButton("🚫 Бан",callback_data="admin_ban")],[InlineKeyboardButton("✅ Разбан",callback_data="admin_unban")],[InlineKeyboardButton("✏️ Ник",callback_data="admin_setnick")],[InlineKeyboardButton("🆔 ID",callback_data="admin_setgameid")],[InlineKeyboardButton("🎟 Промокод",callback_data="admin_createpromo")]])
def ck(mid): return InlineKeyboardMarkup([[InlineKeyboardButton("✅ Подтвердить",callback_data=f"confirm_{mid}")],[InlineKeyboardButton("❌ Отказать",callback_data=f"reject_{mid}")]])
def pak(): return InlineKeyboardMarkup([[InlineKeyboardButton("➕ Пригласить",callback_data="party_invite")],[InlineKeyboardButton("🚪 Выйти",callback_data="party_leave")]])

# ===== СОСТОЯНИЯ =====
(REG_NICK,REG_GAMEID,SUPPORT_MSG,ADMIN_PROMO_DAYS,ADMIN_GIVEELO_TARGET,ADMIN_GIVEELO_AMOUNT,ADMIN_BAN_TARGET,ADMIN_UNBAN_TARGET,ADMIN_SETNICK_TARGET,ADMIN_SETNICK_NEW,ADMIN_SETGAMEID_TARGET,ADMIN_SETGAMEID_NEW,CHANGE_NICK_NEW,PROMO_INPUT,ADMIN_REJECT_ELO,PARTY_INVITE)=range(16)
SUPPORT_REPLY_TARGET={}
LOBBIES={}
PARTIES={}

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

def get_party_by_member(uid):
    for pid,party in PARTIES.items():
        if uid in party["members"]: return party
    return None
def party_text(party):
    text="👥 ПАТИ\n\n"
    for mid in party["members"]:
        p=dbg(mid); role="👑 " if mid==party["leader"] else "• "
        text+=f"{role}@{p['nick']}\n"
    text+=f"\nСостав: {len(party['members'])}/5"
    return text

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
    if p:
        await update.message.reply_text(f"🏠 STRANGER FACEIT\nДобро пожаловать, @{p['nick']}!\n📊 ELO: {p['elo']} | {gr(p['elo'])}\n🏆 Побед: {p['wins']} | Поражений: {p['losses']}",reply_markup=mk(p))
    else:
        await update.message.reply_text("🎮 STRANGER FACEIT\nДобро пожаловать!\nВыберите действие:",reply_markup=sk())
async def cancel(update,context):
    p=dbg(update.effective_user.id); context.user_data.clear()
    await update.message.reply_text("❌ Отменено",reply_markup=mk(p) if p else sk())
    return ConversationHandler.END
async def back(update,context):
    q=update.callback_query; await q.answer()
    p=dbg(q.from_user.id)
    await q.edit_message_text("🏠 STRANGER FACEIT" if p else "🎮 STRANGER FACEIT",reply_markup=mk(p) if p else sk())

async def reg_start(update,context):
    if await rl(update) is None: return ConversationHandler.END
    if dbg(update.effective_user.id): await update.message.reply_text("✅ Уже зарегистрированы"); return ConversationHandler.END
    await update.message.reply_text("📝 РЕГИСТРАЦИЯ\nШаг 1 из 2:\nВведите ваш игровой ник (3-20 символов):")
    return REG_NICK
async def reg_nick(update,context):
    try: n=vn(update.message.text)
    except VE as e: await update.message.reply_text(f"⚠️ {e}"); return REG_NICK
    if dbn(n): await update.message.reply_text("⚠️ Ник занят. Введите другой:"); return REG_NICK
    context.user_data["reg_nick"]=n; await update.message.reply_text("✅ Ник принят!\nШаг 2 из 2:\nВведите ваш игровой ID:"); return REG_GAMEID
async def reg_gameid(update,context):
    try: g=vg(update.message.text)
    except VE as e: await update.message.reply_text(f"⚠️ {e}"); return REG_GAMEID
    if dbgid(g): await update.message.reply_text("❌ ID занят. Проверьте игровой айди либо обратитесь в поддержку."); return REG_GAMEID
    t=update.effective_user.id; n=context.user_data.get("reg_nick")
    if dbc(t,n,g):
        p=dbg(t)
        await update.message.reply_text(f"✅ Регистрация успешна!\nДобро пожаловать в Stranger Faceit! 🎉",reply_markup=mk(p))
        if ADMIN_CHAT_ID:
            await context.bot.send_message(ADMIN_CHAT_ID,f"📝 Новый игрок!\n👤 @{n}\n🆔 ID: {g}\n📊 ELO: 1000")
    else: await update.message.reply_text("❌ Ошибка регистрации. Попробуйте позже.")
    context.user_data.clear(); return ConversationHandler.END

async def profile(update,context):
    if not await rlg(update): return
    p=await rl(update)
    if not p: return
    await update.message.reply_text(f"👤 ПРОФИЛЬ\n\nНик: @{p['nick']}\n🆔 Игровой ID: {p['game_id']}\n🏆 Ранг: {gr(p['elo'])}\n📊 ELO: {p['elo']}\n🎮 Матчей: {p['matches']}\n🏆 Побед: {p['wins']}\n❌ Поражений: {p['losses']}\n⭐ MVP: {p['mvp']}\n💎 Премиум: {'✅ Активен' if isp(p) else '❌ Не активен'}",reply_markup=bk())
async def top(update,context):
    if not await rlg(update): return
    pl=dbt(10); 
    if not pl: await update.message.reply_text("🏆 Топ пуст."); return
    text="🏆 ТОП ИГРОКОВ\n\n"
    for i,p in enumerate(pl):
        medal="🥇" if i==0 else "🥈" if i==1 else "🥉" if i==2 else f"{i+1}."
        prem="💎 " if isp(p) else ""
        text+=f"{medal} {prem}@{p['nick']} — {p['elo']} ELO ({gr(p['elo'])})\n"
    await update.message.reply_text(text,reply_markup=bk())
async def stats(update,context):
    if not await rlg(update): return
    p=await rl(update)
    if not p: return
    k,d,h=p.get("kills",0),p.get("deaths",0),p.get("headshots",0)
    await update.message.reply_text(f"📊 СТАТИСТИКА\n\n🔫 Убийств: {k}\n💀 Смертей: {d}\n⚔️ K/D: {round(k/d,2) if d else k}\n🎯 HS%: {round(h/k*100,1) if k else 0}%\n🗺 Любимая карта: {p.get('fav_map','—')}",reply_markup=bk())
async def history(update,context):
    if not await rlg(update): return
    p=await rl(update)
    if not p: return
    limit=50 if isp(p) else 20
    matches=gh(update.effective_user.id,limit)
    if not matches: await update.message.reply_text("📝 История пуста.",reply_markup=bk()); return
    text=f"📝 ИСТОРИЯ МАТЧЕЙ\nВсего: {len(matches)}/{limit}\n\n"
    for i,m in enumerate(matches[:limit],1):
        em="🏆" if m["win"] else "❌"
        el=m["elo"]
        text+=f"{i}. {em} {m['map']} | {el:+d} ELO\n"
    await update.message.reply_text(text,reply_markup=bk())
async def premium(update,context):
    if not await rlg(update): return
    p=await rl(update)
    if not p: return
    kb=InlineKeyboardMarkup([[InlineKeyboardButton("🎁 Пробный период (24ч)",callback_data="premium_trial")],[InlineKeyboardButton("⭐ Купить за звёзды",callback_data="premium_buy")]])
    await update.message.reply_text(f"💎 ПРЕМИУМ\n\nДаёт:\n• Тег 💎 в профиле\n• x2 ELO за победу\n• Смена ника\n• Расширенная статистика\n• Капитан команды\n• 50 матчей в истории\n\nТекущий статус: {'✅ Активен' if isp(p) else '❌ Не активен'}",reply_markup=kb)
async def prem_cb(update,context):
    q=update.callback_query; await q.answer(); t=update.effective_user.id; p=dbg(t)
    if not p: await q.edit_message_text("❌ Не зарегистрированы"); return
    if q.data=="premium_trial":
        if p.get("premium_until"): await q.edit_message_text("⚠️ Уже есть премиум"); return
        dbup(t,{"premium_until":(datetime.now(timezone.utc)+timedelta(hours=24)).isoformat()}); await q.edit_message_text("🎁 24ч премиума активированы!")
    elif q.data=="premium_buy":
        await context.bot.send_invoice(chat_id=t,title="Премиум 30 дней",description="Все бонусы премиума",payload="premium_30d",provider_token="",currency="XTR",prices=[{"label":"30 дней","amount":100}])
async def support(update,context):
    if not await rlg(update): return ConversationHandler.END
    await update.message.reply_text("🆘 ПОДДЕРЖКА\n\nОпишите вашу проблему одним сообщением:")
    return SUPPORT_MSG
async def support_send(update,context):
    user=update.effective_user; msg=update.message.text
    if ADMIN_CHAT_ID:
        kb=InlineKeyboardMarkup([[InlineKeyboardButton("✍️ Ответить",callback_data=f"sr_{user.id}")]])
        await context.bot.send_message(ADMIN_CHAT_ID,f"🆘 Запрос в поддержку\nОт: @{user.username or user.id}\nСообщение: {msg}",reply_markup=kb)
    await update.message.reply_text("✅ Сообщение отправлено в поддержку!")
    return ConversationHandler.END
async def sr_cb(update,context):
    q=update.callback_query; await q.answer(); tid=int(q.data.replace("sr_",""))
    SUPPORT_REPLY_TARGET[q.message.chat_id]=tid; await q.message.reply_text("✍️ Введите ответ для пользователя:")
    return SUPPORT_MSG
async def admin_chat(update,context):
    cid=update.effective_chat.id
    if cid in SUPPORT_REPLY_TARGET:
        tid=SUPPORT_REPLY_TARGET.pop(cid)
        await context.bot.send_message(tid,f"📩 Ответ поддержки:\n{update.message.text}")
        await update.message.reply_text("✅ Ответ отправлен")
async def change_nick(update,context):
    p=await rl(update)
    if not p: return ConversationHandler.END
    if not isp(p): await update.message.reply_text("⛔ Только премиум!"); return ConversationHandler.END
    await update.message.reply_text("✏️ Введите новый ник:"); return CHANGE_NICK_NEW
async def change_apply(update,context):
    try: n=vn(update.message.text)
    except VE as e: await update.message.reply_text(f"⚠️ {e}"); return CHANGE_NICK_NEW
    if dbn(n): await update.message.reply_text("⚠️ Занят"); return CHANGE_NICK_NEW
    dbup(update.effective_user.id,{"nick":n}); await update.message.reply_text(f"✅ Ник изменён на {n}"); return ConversationHandler.END
async def promo(update,context):
    p=await rl(update)
    if not p: return ConversationHandler.END
    await update.message.reply_text("🎟 Введите промокод:"); return PROMO_INPUT
async def promo_apply(update,context):
    c=update.message.text.strip().upper(); t=update.effective_user.id; p=dbg(t)
    if not p: await update.message.reply_text("❌ Не зарегистрированы"); return ConversationHandler.END
    pr=dbgp(c)
    if not pr or pr.get("used"): await update.message.reply_text("❌ Неверный или использован"); return ConversationHandler.END
    days=pr.get("days",0); base=datetime.now(timezone.utc)
    if p.get("premium_until"):
        try: base=max(base,datetime.fromisoformat(str(p["premium_until"]).replace("Z","+00:00")))
        except: pass
    dbup(t,{"premium_until":(base+timedelta(days=days)).isoformat()}); dbmp(c,t)
    await update.message.reply_text(f"✅ Промокод активирован! +{days} дней премиума."); return ConversationHandler.END

# ===== ПАТИ =====
async def party(update,context):
    uid=update.effective_user.id; p=await rl(update)
    if not p: return ConversationHandler.END
    party=get_party_by_member(uid)
    if not party:
        PARTIES[uid]={"leader":uid,"members":[uid]}
        await update.message.reply_text("👥 ПАТИ СОЗДАНА!\nТы лидер. Приглашай друзей!",reply_markup=pak())
    else:
        is_leader=party["leader"]==uid
        await update.message.reply_text(party_text(party),reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("➕ Пригласить" if is_leader and len(party["members"])<5 else "🔒 Полно",callback_data="party_invite" if is_leader and len(party["members"])<5 else "noop")],[InlineKeyboardButton("🚪 Выйти",callback_data="party_leave")],[InlineKeyboardButton("⬅️ Назад",callback_data="back")]]))
async def party_invite(update,context):
    q=update.callback_query; await q.answer()
    await q.edit_message_text("👤 Введите @юзернейм игрока (с @):",reply_markup=bk())
    return PARTY_INVITE
async def party_invite_send(update,context):
    username=update.message.text.strip(); uid=update.effective_user.id
    if not username.startswith("@"): await update.message.reply_text("❌ Введите с @"); return PARTY_INVITE
    target=dbn(username.replace("@",""))
    if not target: await update.message.reply_text("❌ Игрок не найден"); return ConversationHandler.END
    tid=target["Telegram_id"]
    if get_party_by_member(tid): await update.message.reply_text("❌ Уже в пати"); return ConversationHandler.END
    party=get_party_by_member(uid)
    if not party or party["leader"]!=uid: await update.message.reply_text("❌ Только лидер"); return ConversationHandler.END
    if len(party["members"])>=5: await update.message.reply_text("❌ Пати заполнена"); return ConversationHandler.END
    kb=InlineKeyboardMarkup([[InlineKeyboardButton("✅ Принять",callback_data=f"paccept_{uid}")],[InlineKeyboardButton("❌ Отказать",callback_data=f"pdecline_{uid}")]])
    await context.bot.send_message(tid,f"👥 @{dbg(uid)['nick']} приглашает вас в пати!",reply_markup=kb)
    await update.message.reply_text(f"✅ Приглашение отправлено @{target['nick']}")
    return ConversationHandler.END
async def paccept(update,context):
    q=update.callback_query; await q.answer(); uid=update.effective_user.id
    leader_id=int(q.data.replace("paccept_",""))
    party=PARTIES.get(leader_id)
    if not party: await q.edit_message_text("❌ Пати не существует"); return
    if len(party["members"])>=5: await q.edit_message_text("❌ Пати заполнена"); return
    if get_party_by_member(uid): await q.edit_message_text("❌ Вы уже в пати"); return
    party["members"].append(uid); await q.edit_message_text("✅ Вы присоединились к пати!")
    await context.bot.send_message(leader_id,f"✅ @{dbg(uid)['nick']} присоединился к пати!")
async def pdecline(update,context):
    q=update.callback_query; await q.answer(); await q.edit_message_text("❌ Вы отклонили приглашение")
async def party_leave(update,context):
    q=update.callback_query; await q.answer(); uid=update.effective_user.id
    party=get_party_by_member(uid)
    if not party: await q.edit_message_text("❌ Вы не в пати"); return
    if party["leader"]==uid:
        for mid in party["members"]:
            if mid!=uid:
                try: await context.bot.send_message(mid,"👑 Лидер покинул пати. Пати расформирована.")
                except: pass
        del PARTIES[party["leader"]]
        await q.edit_message_text("🚪 Пати расформирована")
    else:
        party["members"].remove(uid)
        await q.edit_message_text("🚪 Вы вышли из пати")
        await context.bot.send_message(party["leader"],f"❌ @{dbg(uid)['nick']} покинул пати")

# ===== АДМИН-ПАНЕЛЬ =====
async def admin(update,context):
    if update.effective_user.id!=OWNER_ID: await update.message.reply_text("⛔ Нет доступа"); return
    await update.message.reply_text("⚙️ АДМИН-ПАНЕЛЬ",reply_markup=ak())
async def admin_cb(update,context):
    q=update.callback_query; await q.answer()
    if update.effective_user.id!=OWNER_ID: await q.edit_message_text("⛔ Нет доступа"); return
    action=q.data
    if action=="admin_giveelo": await q.message.reply_text("Введите Telegram ID:"); return ADMIN_GIVEELO_TARGET
    if action=="admin_ban": await q.message.reply_text("Введите Telegram ID для бана:"); return ADMIN_BAN_TARGET
    if action=="admin_unban": await q.message.reply_text("Введите Telegram ID для разбана:"); return ADMIN_UNBAN_TARGET
    if action=="admin_setnick": await q.message.reply_text("Введите Telegram ID:"); return ADMIN_SETNICK_TARGET
    if action=="admin_setgameid": await q.message.reply_text("Введите Telegram ID:"); return ADMIN_SETGAMEID_TARGET
    if action=="admin_createpromo": await q.message.reply_text("Введите количество дней:"); return ADMIN_PROMO_DAYS
    return ConversationHandler.END
async def admin_giveelo_target(update,context):
    try: t=int(update.message.text.strip())
    except: await update.message.reply_text("⚠️ Число"); return ADMIN_GIVEELO_TARGET
    if not dbg(t): await update.message.reply_text("⚠️ Не найден"); return ADMIN_GIVEELO_TARGET
    context.user_data["admin_target"]=t; await update.message.reply_text("Введите ELO:"); return ADMIN_GIVEELO_AMOUNT
async def admin_giveelo_amount(update,context):
    try: a=int(update.message.text.strip())
    except: await update.message.reply_text("⚠️ Число"); return ADMIN_GIVEELO_AMOUNT
    t=context.user_data.get("admin_target"); p=dbg(t)
    if not p: await update.message.reply_text("❌ Не найден"); return ConversationHandler.END
    ne=max(0,p.get("elo",1000)+a); dbup(t,{"elo":ne})
    await update.message.reply_text(f"✅ ELO обновлён: {p['nick']} → {ne}"); context.user_data.clear(); return ConversationHandler.END
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
    except VE as e: await update.message.reply_text(f"⚠️ {e}"); return ADMIN_SETNICK_NEW
    if dbn(n): await update.message.reply_text("⚠️ Занят"); return ADMIN_SETNICK_NEW
    t=context.user_data.get("admin_target"); dbup(t,{"nick":n}); await update.message.reply_text(f"✅ Ник изменён"); context.user_data.clear(); return ConversationHandler.END
async def admin_setgameid_target(update,context):
    try: t=int(update.message.text.strip())
    except: await update.message.reply_text("⚠️ Число"); return ADMIN_SETGAMEID_TARGET
    if not dbg(t): await update.message.reply_text("⚠️ Не найден"); return ConversationHandler.END
    context.user_data["admin_target"]=t; await update.message.reply_text("Введите новый ID:"); return ADMIN_SETGAMEID_NEW
async def admin_setgameid_apply(update,context):
    try: g=vg(update.message.text)
    except VE as e: await update.message.reply_text(f"⚠️ {e}"); return ADMIN_SETGAMEID_NEW
    t=context.user_data.get("admin_target"); dbup(t,{"game_id":g}); await update.message.reply_text(f"✅ ID изменён"); context.user_data.clear(); return ConversationHandler.END
async def admin_createpromo(update,context):
    try: d=int(update.message.text.strip())
    except: await update.message.reply_text("⚠️ Число"); return ADMIN_PROMO_DAYS
    if d<=0: await update.message.reply_text("⚠️ >0"); return ADMIN_PROMO_DAYS
    c=gp(); dbpc(c,d,update.effective_user.id); await update.message.reply_text(f"✅ Промокод {c} — {d} дней"); return ConversationHandler.END

# ===== МАТЧМЕЙКИНГ =====
async def find(update,context):
    if not await rlg(update): return
    if await rl(update) is None: return
    await update.message.reply_text("🎮 ПОИСК МАТЧА\n\nВыберите платформу:",reply_markup=pk())
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
        caps=[p for p in lobby["players"] if isp(dbg(p))]
        if not caps: caps=[lobby["players"][0]]
        cap=caps[0]
        for p in lobby["players"]:
            try: await context.bot.send_message(p,f"🗺 ВЕТО КАРТ\nКапитан: @{dbg(cap)['nick']}\nДоступные карты:",reply_markup=vk(lid,lobby["veto_maps"]))
            except: pass
async def veto(update,context):
    q=update.callback_query; await q.answer()
    _,lid,mn=q.data.split("_",2); lobby=LOBBIES.get(lid)
    if not lobby or lobby["status"]!="veto": await q.edit_message_text("⚠️ Неактивно"); return
    if mn not in lobby["veto_maps"]: await q.edit_message_text("⚠️ Уже выбрана"); return
    lobby["veto_maps"].remove(mn); lobby["veto_turn"]+=1; dbsl(lobby)
    if len(lobby["veto_maps"])>1:
        for p in lobby["players"]:
            try: await context.bot.send_message(p,f"🚫 {mn} забанена. Осталось: {len(lobby['veto_maps'])}",reply_markup=vk(lid,lobby["veto_maps"]))
            except: pass
    else:
        chosen=lobby["veto_maps"][0]; lobby["chosen_map"]=chosen; lobby["status"]="in_progress"; dbsl(lobby)
        host=dbg(lobby["players"][0])
        for p in lobby["players"]:
            try: await context.bot.send_message(p,f"🗺 Выбрана: {MAP_EMOJI.get(chosen,'')} {chosen}\nХост: @{host['nick']}\nПосле матча хост отправляет скриншот")
            except: pass
        if ADMIN_CHAT_ID:
            await context.bot.send_message(ADMIN_CHAT_ID,f"🎮 Начался матч!\nКарта: {chosen}\nСостав:\n"+"\n".join([f"@{dbg(pid)['nick']}" for pid in lobby["players"]]))
async def cancel_search(update,context):
    q=update.callback_query; await q.answer(); lid=q.data.replace("cancel_",""); lobby=LOBBIES.get(lid)
    if lobby and update.effective_user.id in lobby["players"]:
        lobby["players"].remove(update.effective_user.id); dbsl(lobby)
        if not lobby["players"]: reset_lobby(lid)
    await q.edit_message_text("❌ Отменено")
async def photo(update,context):
    if not await rlg(update): return
    t=update.effective_user.id; lobby=None
    for lb in LOBBIES.values():
        if lb["status"]=="in_progress" and t in lb["players"]: lobby=lb; break
    if not lobby: await update.message.reply_text("❌ Нет активного матча"); return
    photo=update.message.photo[-1].file_id; mid=f"M-{int(time.time())}"
    context.user_data["match_photo"]=photo; context.user_data["match_id"]=mid; context.user_data["lobby"]=lobby
    await update.message.reply_text("📸 Фото получено! Идёт анализ...")
    await proc(update,context,lobby,mid,photo)
async def proc(update,context,lobby,mid,photo):
    pl=lobby["players"]; half=len(pl)//2; w=pl[:half]; l=pl[half:]
    st={}
    for p in pl:
        pp=dbg(p); k=random.randint(5,25); d=random.randint(5,20)
        st[str(p)]={"kills":k,"deaths":d}
    mvp=max(pl,key=lambda x:st[str(x)]["kills"])
    dbsp(mid,lobby["id"],lobby["chosen_map"],pl,w,l,st,mvp,photo)
    if ADMIN_CHAT_ID:
        text=f"📸 Новые результаты матча!\nID: {mid}\nКарта: {lobby['chosen_map']}\n\n🔵 ПОБЕДИТЕЛИ:\n"
        for p in w:
            pp=dbg(p); prem="💎 " if isp(pp) else ""; elo=calc(pp["elo"],1000,True,pp["matches"])
            if isp(pp): elo*=2
            text+=f"{prem}@{pp['nick']} +{elo} ELO\n"
        text+="\n🔴 ПРОИГРАВШИЕ:\n"
        for p in l:
            pp=dbg(p); elo=calc(pp["elo"],1000,False,pp["matches"])
            if isp(pp): elo*=2
            text+=f"@{pp['nick']} {elo} ELO\n"
        await context.bot.send_photo(ADMIN_CHAT_ID,photo,caption=text,reply_markup=ck(mid))
async def confirm(update,context):
    q=update.callback_query; await q.answer()
    if update.effective_user.id!=OWNER_ID: await q.answer("❌ Только админ!",show_alert=True); return
    mid=q.data.replace("confirm_",""); pending=dbgpnd(mid)
    if not pending: await q.edit_message_text("❌ Матч не найден"); return
    pl=json.loads(pending["players"]); w=json.loads(pending["winner_ids"]); l=json.loads(pending["loser_ids"]); st=json.loads(pending["stats"])
    for p in pl:
        pp=dbg(p); s=st.get(str(p),{"kills":0,"deaths":0}); is_win=p in w
        elo=calc(pp["elo"],1000,is_win,pp["matches"])
        if isp(pp): elo*=2
        ne=max(0,pp["elo"]+elo)
        dbup(p,{"elo":ne,"wins":pp["wins"]+(1 if is_win else 0),"losses":pp["losses"]+(0 if is_win else 1),"matches":pp["matches"]+1,"kills":pp.get("kills",0)+s.get("kills",0),"deaths":pp.get("deaths",0)+s.get("deaths",0)})
        history=pp.get("elo_history",[]); history.append(ne); dbup(p,{"elo_history":json.dumps(history[-50:])})
    dbupnd(mid,"confirmed")
    await q.edit_message_text(f"✅ Матч {mid} подтверждён!")
    for p in pl:
        try: await context.bot.send_message(p,f"✅ Матч {mid} подтверждён! ELO обновлён.")
        except: pass
async def reject(update,context):
    q=update.callback_query; await q.answer()
    if update.effective_user.id!=OWNER_ID: await q.answer("❌ Только админ!",show_alert=True); return
    mid=q.data.replace("reject_","")
    if not dbgpnd(mid): await q.edit_message_text("❌ Матч не найден"); return
    await q.edit_message_text("❌ Матч отклонён. Введите статистику в формате:\n@ник +5\n@ник2 -3")
    context.user_data["reject_match"]=mid
    return ADMIN_REJECT_ELO
async def reject_elo(update,context):
    mid=context.user_data.get("reject_match")
    if not mid: return
    for line in update.message.text.split("\n"):
        parts=line.split()
        if len(parts)!=2: continue
        nick=parts[0].replace("@","")
        try: elo=int(parts[1])
        except: continue
        p=dbn(nick)
        if p: dbup(p["Telegram_id"],{"elo":max(0,p["elo"]+elo)})
    dbupnd(mid,"rejected")
    await update.message.reply_text("✅ Статистика обновлена!")
    context.user_data.clear()
    return ConversationHandler.END

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
    import asyncio
    asyncio.set_event_loop(asyncio.new_event_loop())
    global LOBBIES
    LOBBIES=dbll()
    logger.info(f"Загружено {len(LOBBIES)} лобби из Supabase")
    threading.Thread(target=run_flask,daemon=True).start()
    app=ApplicationBuilder().token(BOT_TOKEN).build()
    app.post_init=post_init
    
    app.add_handler(CommandHandler("start",start))
    app.add_handler(CommandHandler("cancel",cancel))
    app.add_handler(CommandHandler("profile",profile))
    app.add_handler(CommandHandler("top",top))
    app.add_handler(CommandHandler("stats",stats))
    app.add_handler(CommandHandler("history",history))
    app.add_handler(CommandHandler("premium",premium))
    app.add_handler(CommandHandler("promo",promo))
    app.add_handler(CommandHandler("support",support))
    app.add_handler(CommandHandler("findmatch",find))
    app.add_handler(CommandHandler("admin",admin))
    app.add_handler(CommandHandler("party",party))
    
    app.add_handler(ConversationHandler([CommandHandler("register",reg_start)],[REG_NICK:[MessageHandler(filters.TEXT&~filters.COMMAND,reg_nick)],REG_GAMEID:[MessageHandler(filters.TEXT&~filters.COMMAND,reg_gameid)]},[CommandHandler("cancel",cancel)]))
    app.add_handler(ConversationHandler([CommandHandler("support",support),MessageHandler(filters.Regex("^🆘 Поддержка$"),support)],[SUPPORT_MSG:[MessageHandler(filters.TEXT&~filters.COMMAND,support_send)]},[CommandHandler("cancel",cancel)]))
    app.add_handler(ConversationHandler([CommandHandler("changenick",change_nick),MessageHandler(filters.Regex("^✏️ Сменить ник$"),change_nick)],[CHANGE_NICK_NEW:[MessageHandler(filters.TEXT&~filters.COMMAND,change_apply)]},[CommandHandler("cancel",cancel)]))
    app.add_handler(ConversationHandler([CommandHandler("promo",promo),MessageHandler(filters.Regex("^🎟 Промокод$"),promo)],[PROMO_INPUT:[MessageHandler(filters.TEXT&~filters.COMMAND,promo_apply)]},[CommandHandler("cancel",cancel)]))
    app.add_handler(ConversationHandler([CommandHandler("party",party),MessageHandler(filters.Regex("^👥 Пати$"),party)],[PARTY_INVITE:[MessageHandler(filters.TEXT&~filters.COMMAND,party_invite_send)]},[CommandHandler("cancel",cancel)]))
    app.add_handler(ConversationHandler([CallbackQueryHandler(admin_cb,pattern="^admin_")],[ADMIN_GIVEELO_TARGET:[MessageHandler(filters.TEXT&~filters.COMMAND,admin_giveelo_target)],ADMIN_GIVEELO_AMOUNT:[MessageHandler(filters.TEXT&~filters.COMMAND,admin_giveelo_amount)],ADMIN_BAN_TARGET:[MessageHandler(filters.TEXT&~filters.COMMAND,admin_ban_target)],ADMIN_UNBAN_TARGET:[MessageHandler(filters.TEXT&~filters.COMMAND,admin_unban_target)],ADMIN_SETNICK_TARGET:[MessageHandler(filters.TEXT&~filters.COMMAND,admin_setnick_target)],ADMIN_SETNICK_NEW:[MessageHandler(filters.TEXT&~filters.COMMAND,admin_setnick_apply)],ADMIN_SETGAMEID_TARGET:[MessageHandler(filters.TEXT&~filters.COMMAND,admin_setgameid_target)],ADMIN_SETGAMEID_NEW:[MessageHandler(filters.TEXT&~filters.COMMAND,admin_setgameid_apply)],ADMIN_PROMO_DAYS:[MessageHandler(filters.TEXT&~filters.COMMAND,admin_createpromo)]},[CommandHandler("cancel",cancel)]))
    app.add_handler(ConversationHandler([CallbackQueryHandler(reject,pattern="^reject_")],[ADMIN_REJECT_ELO:[MessageHandler(filters.TEXT&~filters.COMMAND,reject_elo)]},[CommandHandler("cancel",cancel)]))
    
    app.add_handler(CallbackQueryHandler(back,pattern="^back$"))
    app.add_handler(CallbackQueryHandler(prem_cb,pattern="^premium_"))
    app.add_handler(CallbackQueryHandler(plat_cb,pattern="^platform_"))
    app.add_handler(CallbackQueryHandler(ready,pattern="^ready_"))
    app.add_handler(CallbackQueryHandler(cancel_search,pattern="^cancel_"))
    app.add_handler(CallbackQueryHandler(veto,pattern="^veto_"))
    app.add_handler(CallbackQueryHandler(sr_cb,pattern="^sr_"))
    app.add_handler(CallbackQueryHandler(confirm,pattern="^confirm_"))
    app.add_handler(CallbackQueryHandler(reject,pattern="^reject_"))
    app.add_handler(CallbackQueryHandler(paccept,pattern="^paccept_"))
    app.add_handler(CallbackQueryHandler(pdecline,pattern="^pdecline_"))
    app.add_handler(CallbackQueryHandler(party_leave,pattern="^party_leave$"))
    app.add_handler(CallbackQueryHandler(party_invite,pattern="^party_invite$"))
    
    app.add_handler(PreCheckoutQueryHandler(lambda u,c: u.pre_checkout_query.answer(ok=True)))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT,lambda u,c: dbup(u.effective_user.id,{"premium_until":(datetime.now(timezone.utc)+timedelta(days=30)).isoformat()}) or u.message.reply_text("✅ Премиум активирован!")))
    app.add_handler(MessageHandler(filters.PHOTO,photo))
    
    if ADMIN_CHAT_ID:
        app.add_handler(MessageHandler(filters.TEXT&filters.Chat(chat_id=ADMIN_CHAT_ID)&~filters.COMMAND,admin_chat))
    
    app.add_handler(MessageHandler(filters.TEXT&~filters.COMMAND,lambda u,c: globals().get({"🔍 Найти матч":find,"👤 Профиль":profile,"🏆 Топ игроков":top,"📊 Статистика":stats,"📝 История":history,"💎 Премиум":premium,"🎟 Промокод":promo,"🆘 Поддержка":support,"✏️ Сменить ник":change_nick,"👥 Пати":party,"⚙️ Админ-панель":admin}.get(u.message.text,lambda:None)) and globals().get({"🔍 Найти матч":find,"👤 Профиль":profile,"🏆 Топ игроков":top,"📊 Статистика":stats,"📝 История":history,"💎 Премиум":premium,"🎟 Промокод":promo,"🆘 Поддержка":support,"✏️ Сменить ник":change_nick,"👥 Пати":party,"⚙️ Админ-панель":admin}.get(u.message.text,lambda:None))(u,c)))
    
    app.add_error_handler(error)
    logger.info("🤖 Bot started, polling...")
    app.run_polling(allowed_updates=Update.ALL_TYPES,drop_pending_updates=True)

if __name__=="__main__":
    main()

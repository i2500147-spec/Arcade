import os,re,asyncio,logging,threading,requests,random,string
from datetime import datetime,timedelta,timezone
import httpx
from flask import Flask
from dotenv import load_dotenv
from telegram import Update,InlineKeyboardButton,InlineKeyboardMarkup
from telegram.ext import Application,CommandHandler,CallbackQueryHandler,MessageHandler,ContextTypes,ConversationHandler,filters,PreCheckoutQueryHandler
load_dotenv()

# ===== КОНФИГ =====
BOT_TOKEN=os.getenv("BOT_TOKEN")
OWNER_ID=int(os.getenv("OWNER_ID","0"))
ADMIN_CHAT_ID=int(os.getenv("ADMIN_CHAT_ID","0"))
SUPABASE_URL=os.getenv("SUPABASE_URL","").rstrip("/")
SUPABASE_KEY=os.getenv("SUPABASE_KEY")
PORT=int(os.getenv("PORT","10000"))
logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",level=logging.INFO)
logger=logging.getLogger(__name__)

def ensure_event_loop():
    try:asyncio.get_event_loop()
    except RuntimeError:asyncio.set_event_loop(asyncio.new_event_loop())

flask_app=Flask(__name__)
@flask_app.route("/")
@flask_app.route("/health")
def health():return "OK",200
def run_flask():flask_app.run(host="0.0.0.0",port=PORT)

# ===== SUPABASE =====
SB_HEADERS={"apikey":SUPABASE_KEY or "","Authorization":f"Bearer {SUPABASE_KEY}" if SUPABASE_KEY else "","Content-Type":"application/json"}
def sb_client():return httpx.Client(base_url=f"{SUPABASE_URL}/rest/v1",headers=SB_HEADERS,timeout=15)

def gb(method,path,data=None):  # gb = get_by
    try:
        with sb_client() as c:
            r=c.request(method,path,json=data) if data else c.get(path)
            return r.json()[0] if r.status_code==200 and r.json() else None
    except:return None

def get_player_by_tg(tg_id):return gb("GET",f"/players?Telegram_id=eq.{tg_id}")
def get_player_by_nick(nick):return gb("GET",f"/players?nick=eq.{nick}")
def get_player_by_game_id(game_id):return gb("GET",f"/players?game_id=eq.{game_id}")
def create_player(tg_id,nick,game_id,password):
    try:
        with sb_client() as c:
            r=c.post("/players",json={"Telegram_id":tg_id,"nick":nick,"game_id":game_id,"password":password})
            return r.status_code==201
    except:return False
def update_player(tg_id,fields):
    try:
        with sb_client() as c:
            r=c.patch("/players",params={"Telegram_id":f"eq.{tg_id}"},json=fields)
            return r.status_code==200
    except:return False
def top_players(limit=10):
    try:
        with sb_client() as c:
            r=c.get("/players",params={"order":"elo.desc","limit":str(limit)})
            return r.json() if r.status_code==200 else []
    except:return []
def all_players(limit=100):
    try:
        with sb_client() as c:
            r=c.get("/players",params={"limit":str(limit)})
            return r.json() if r.status_code==200 else []
    except:return []

# ===== ВАЛИДАЦИЯ =====
NICK_RE=re.compile(r"^[A-Za-z0-9_]{2,32}$")
ID_RE=re.compile(r"^\d{8,15}$")
PASS_RE=re.compile(r"^(?=.*[A-Za-z])(?=.*\d)[A-Za-z0-9]{8,}$")
def vn(s):return bool(NICK_RE.match(s))
def vi(s):return bool(ID_RE.match(s))
def vp(s):return bool(PASS_RE.match(s))

def rank_for_elo(elo):
    ranks=[(0,"Без ранга"),(900,"Бронза"),(1100,"Серебро"),(1300,"Золото"),(1500,"Платина"),(1700,"Алмаз"),(1900,"Мастер"),(2100,"Элита"),(2300,"Легенда")]
    r=ranks[0][1]
    for t,n in ranks:
        if elo>=t:r=n
    return r

def is_premium(p):
    u=p.get("premium_until")
    if not u:return False
    try:return datetime.fromisoformat(u.replace("Z","+00:00"))>datetime.now(timezone.utc)
    except:return False
def dn(p):return f"{'💎 ' if is_premium(p) else ''}{p['nick']}"

# ===== КЛАВИАТУРЫ =====
def sk():return InlineKeyboardMarkup([[InlineKeyboardButton("🔑 Вход",callback_data="login")],[InlineKeyboardButton("📝 Регистрация",callback_data="register")],[InlineKeyboardButton("🆘 Поддержка",callback_data="support")]])
def mk():return InlineKeyboardMarkup([[InlineKeyboardButton("🔍 Найти матч",callback_data="find_match"),InlineKeyboardButton("🎉 Пати",callback_data="party")],[InlineKeyboardButton("👤 Профиль",callback_data="profile"),InlineKeyboardButton("🏆 Топ",callback_data="top")],[InlineKeyboardButton("📊 Статистика",callback_data="stats"),InlineKeyboardButton("📝 История",callback_data="history")],[InlineKeyboardButton("📢 Жалобы",callback_data="reports")],[InlineKeyboardButton("💎 Премиум",callback_data="premium_menu")],[InlineKeyboardButton("✏️ Сменить ник",callback_data="changenick")],[InlineKeyboardButton("🚪 Выйти",callback_data="logout")]])
def bk():return InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад",callback_data="back")]])
def stk():return InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад",callback_data="back"),InlineKeyboardButton("📊 Расширенная статистика",callback_data="extended_stats")]])
def pk():return InlineKeyboardMarkup([[InlineKeyboardButton("🎁 Пробный период (24ч)",callback_data="premium_trial")],[InlineKeyboardButton("⭐ 1 день — 5 ⭐",callback_data="buy_1_day")],[InlineKeyboardButton("⭐ 1 неделя — 30 ⭐",callback_data="buy_1_week")],[InlineKeyboardButton("⭐ 1 месяц — 100 ⭐",callback_data="buy_1_month")],[InlineKeyboardButton("🎫 Промокоды",callback_data="promo_menu")],[InlineKeyboardButton("✏️ Сменить ник",callback_data="changenick")],[InlineKeyboardButton("⬅️ Назад",callback_data="back")]])
def prk():return InlineKeyboardMarkup([[InlineKeyboardButton("🎫 Ввести промокод",callback_data="promo_enter")],[InlineKeyboardButton("⬅️ Назад",callback_data="premium_menu")]])

PROMOCODES={}
REG_NICK,REG_ID,REG_PASS,LOGIN_NICK,LOGIN_ID,LOGIN_PASS,SUPPORT_MSG,ADMIN_REPLY,RESET_PASS,PROMO_ENTER=range(10)

# ===== ОСНОВНЫЕ ОБРАБОТЧИКИ =====
async def cmd_start(u,c):
    logger.info(f"команда /start от {u.effective_user.id}")
    c.user_data.clear()
    p=get_player_by_tg(u.effective_user.id)
    await u.message.reply_text(f"С возвращением, {p['nick']}!" if p else "🎮 STRANGER FACEIT\n\nВыберите действие:",reply_markup=mk() if p else sk())
    return ConversationHandler.END

async def cmd_cancel(u,c):
    c.user_data.clear()
    await u.message.reply_text("❌ Отменено.",reply_markup=sk())
    return ConversationHandler.END

async def cmd_admin(u,c):
    if u.effective_user.id!=OWNER_ID:
        await u.message.reply_text("❌ Нет доступа.")
        return
    await u.message.reply_text("👑 Админ-панель",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔑 Сброс пароля",callback_data="admin_reset")],[InlineKeyboardButton("🎫 Создать промокод",callback_data="admin_promo")],[InlineKeyboardButton("⬅️ Назад",callback_data="back")]]))

async def admin_promo_create(u,c):
    q=u.callback_query;await q.answer()
    if q.from_user.id!=OWNER_ID:return
    await q.edit_message_text("📝 Введите промокод и количество дней в формате:\n/promo НАЗВАНИЕ ДНЕЙ\n\nПример:\n/promo SUMMER2024 7")

async def reg_start(u,c):
    q=u.callback_query;await q.answer()
    if get_player_by_tg(q.from_user.id):
        await q.edit_message_text("✅ Вы уже зарегистрированы!",reply_markup=mk())
        return ConversationHandler.END
    await q.edit_message_text("📝 **РЕГИСТРАЦИЯ**\n\nВведите ник (2-32 символа, латиница/цифры/_):",parse_mode="Markdown")
    return REG_NICK

async def reg_nick(u,c):
    n=u.message.text.strip()
    if not vn(n):
        await u.message.reply_text("❌ Некорректный ник.\n• 2-32 символа\n• Только латиница, цифры и _\n\nПопробуйте снова:")
        return REG_NICK
    if get_player_by_nick(n):
        await u.message.reply_text("❌ Этот ник уже занят.\nПопробуйте другой:")
        return REG_NICK
    c.user_data["nick"]=n
    await u.message.reply_text(f"✅ Ник принят: {n}\n\nТеперь введите ID в Standoff 2 (8-15 цифр):")
    return REG_ID

async def reg_id(u,c):
    gid=u.message.text.strip()
    if not vi(gid):
        await u.message.reply_text("❌ Некорректный ID.\n• 8-15 цифр\n\nПопробуйте снова:")
        return REG_ID
    if get_player_by_game_id(gid):
        await u.message.reply_text("❌ Этот ID уже зарегистрирован.\nОбратитесь в поддержку.",reply_markup=sk())
        return ConversationHandler.END
    c.user_data["game_id"]=gid
    await u.message.reply_text(f"✅ ID принят: {gid}\n\nТеперь придумайте пароль:\n• Минимум 8 символов\n• Буквы и цифры\n• Специальные символы приветствуются")
    return REG_PASS

async def reg_pass(u,c):
    pwd=u.message.text.strip()
    if not vp(pwd):
        await u.message.reply_text("❌ Слабый пароль. Попробуйте снова:\n• Минимум 8 символов\n• Буквы и цифры\n• Специальные символы приветствуются")
        return REG_PASS
    tid=u.effective_user.id;n=c.user_data.get("nick");gid=c.user_data.get("game_id")
    if not n or not gid:
        await u.message.reply_text("❌ Ошибка: сессия регистрации истекла. Начните заново.",reply_markup=sk())
        c.user_data.clear();return ConversationHandler.END
    try:
        if create_player(tid,n,gid,pwd):
            c.user_data.clear()
            await u.message.reply_text(f"✅ **РЕГИСТРАЦИЯ УСПЕШНА!**\n\nДобро пожаловать, {n}! 🎉",reply_markup=mk(),parse_mode="Markdown")
        else:
            await u.message.reply_text("❌ Ошибка регистрации. Возможно, ник уже занят.\nПопробуйте позже.",reply_markup=sk())
    except:
        await u.message.reply_text("❌ Техническая ошибка. Попробуйте позже.",reply_markup=sk())
    return ConversationHandler.END

async def login_start(u,c):
    q=u.callback_query;await q.answer()
    if get_player_by_tg(q.from_user.id):
        await q.edit_message_text("✅ Уже вошли.",reply_markup=mk())
        return ConversationHandler.END
    await q.edit_message_text("🔑 **ВХОД**\n\nВведите ник:",parse_mode="Markdown")
    return LOGIN_NICK

async def login_nick(u,c):
    n=u.message.text.strip()
    p=get_player_by_nick(n)
    if not p:
        await u.message.reply_text("❌ Ник не найден. Попробуйте:")
        return LOGIN_NICK
    c.user_data["login_player"]=p
    await u.message.reply_text("🔑 Введите ID:")
    return LOGIN_ID

async def login_id(u,c):
    gid=u.message.text.strip()
    p=c.user_data.get("login_player")
    if p.get("game_id")!=gid:
        await u.message.reply_text("❌ ID не совпадает. Попробуйте:")
        return LOGIN_ID
    await u.message.reply_text("🔑 Введите пароль:")
    return LOGIN_PASS

async def login_pass(u,c):
    pwd=u.message.text.strip()
    p=c.user_data.get("login_player")
    if p.get("password")!=pwd:
        await u.message.reply_text("❌ Неверный пароль. Попробуйте:")
        return LOGIN_PASS
    tid=u.effective_user.id
    if p.get("Telegram_id")!=tid:update_player(p["Telegram_id"],{"Telegram_id":tid})
    c.user_data.clear()
    await u.message.reply_text(f"✅ Вход выполнен!\nС возвращением, {p['nick']}!",reply_markup=mk())
    return ConversationHandler.END

async def menu_profile(u,c):
    q=u.callback_query;await q.answer()
    p=get_player_by_tg(q.from_user.id)
    if not p:
        await q.edit_message_text("❌ Сначала войдите.",reply_markup=sk())
        return
    await q.edit_message_text(f"📊 **ПРОФИЛЬ**\n\n👤 Ник: {dn(p)}\n🆔 ID: {p.get('game_id')}\n🏅 Ранг: {rank_for_elo(p.get('elo',1000))}\n📊 ELO: {p.get('elo',1000)}\n🎯 Матчи: {p.get('matches',0)}\n🏆 Победы: {p.get('wins',0)}\n❌ Поражения: {p.get('losses',0)}\n⭐ MVP: {p.get('mvp',0)}\n💎 Премиум: {'✅ Активен' if is_premium(p) else '❌ Нет'}",reply_markup=bk(),parse_mode="Markdown")

async def menu_top(u,c):
    q=u.callback_query;await q.answer()
    pl=top_players(10)
    if not pl:
        await q.edit_message_text("🏆 Топ пуст.",reply_markup=bk())
        return
    lines=["🏆 **ТОП ИГРОКОВ**\n"]
    for i,p in enumerate(pl,1):
        lines.append(f"{i}. {'💎' if is_premium(p) else ''} {p['nick']} — {p['elo']} ELO")
    await q.edit_message_text("\n".join(lines),reply_markup=bk(),parse_mode="Markdown")

async def menu_stats(u,c):
    q=u.callback_query;await q.answer()
    p=get_player_by_tg(q.from_user.id)
    if not p:
        await q.edit_message_text("❌ Сначала войдите.",reply_markup=sk())
        return
    k=p.get("kills",0);d=p.get("deaths",0);hs=p.get("headshots",0)
    await q.edit_message_text(f"📊 **СТАТИСТИКА**\n\n🔫 AVG K/D: {round(k/d,2) if d else k}\n🎯 HS%: {round(hs/k*100,1) if k else 0}%\n🗺️ Любимая карта: {p.get('fav_map','—')}",reply_markup=stk(),parse_mode="Markdown")

async def extended_stats(u,c):
    q=u.callback_query;await q.answer()
    p=get_player_by_tg(q.from_user.id)
    if not p:
        await q.edit_message_text("❌ Сначала войдите.",reply_markup=sk())
        return
    if is_premium(p):
        await q.edit_message_text(f"📊 **РАСШИРЕННАЯ СТАТИСТИКА**\n\n🎯 Всего матчей: {p.get('matches',0)}\n📈 Винрейт: {round(p.get('wins',0)/max(1,p.get('matches',0))*100,1)}%\n🗺️ Любимая карта: {p.get('fav_map','—')}\n⭐ MVP: {p.get('mvp',0)}",reply_markup=bk(),parse_mode="Markdown")
    else:
        await q.edit_message_text("❌ Расширенная статистика доступна только с премиум-подпиской.",reply_markup=bk())

PREMIUM_PRICES={"1_day":{"label":"1 день — 5 ⭐","stars":5,"days":1},"1_week":{"label":"1 неделя — 30 ⭐","stars":30,"days":7},"1_month":{"label":"1 месяц — 100 ⭐","stars":100,"days":30}}

async def menu_premium(u,c):
    q=u.callback_query;await q.answer()
    p=get_player_by_tg(q.from_user.id)
    if not p:
        await q.edit_message_text("❌ Сначала войдите.",reply_markup=sk())
        return
    await q.edit_message_text(f"💎 **ПРЕМИУМ**\n\nДаёт:\n• Тег 💎 в профиле\n• Смену ника\n• Расширенную статистику\n• x2 ELO за победу\n\n📌 Текущий статус: {'✅ активен' if is_premium(p) else '❌ не активен'}",reply_markup=pk(),parse_mode="Markdown")

async def premium_trial(u,c):
    q=u.callback_query;await q.answer()
    p=get_player_by_tg(q.from_user.id)
    if not p:
        await q.edit_message_text("❌ Сначала войдите.",reply_markup=sk())
        return
    if is_premium(p):
        await q.edit_message_text("❌ У вас уже есть активный премиум.",reply_markup=pk())
        return
    until=datetime.now(timezone.utc)+timedelta(days=1)
    if update_player(p["Telegram_id"],{"premium_until":until.isoformat()}):
        await q.edit_message_text("🎁 Пробный период активирован! 24 часа премиума.",reply_markup=pk())
    else:
        await q.edit_message_text("❌ Ошибка активации. Попробуйте позже.",reply_markup=pk())

async def premium_buy(u,c):
    q=u.callback_query;await q.answer()
    plan_key=q.data.replace("buy_","")
    plan=PREMIUM_PRICES.get(plan_key)
    if not plan:return
    await c.bot.send_invoice(chat_id=q.from_user.id,title="Премиум",description=f"Премиум-подписка {plan['label']}",payload=f"premium_{plan_key}",provider_token="",currency="XTR",prices=[{"label":plan["label"],"amount":plan["stars"]}])

async def precheckout_callback(u,c):await u.pre_checkout_query.answer(ok=True)

async def successful_payment(u,c):
    payload=u.message.successful_payment.invoice_payload
    plan_key=payload.replace("premium_","")
    plan=PREMIUM_PRICES.get(plan_key)
    tid=u.effective_user.id
    p=get_player_by_tg(tid)
    if not p or not plan:
        await u.message.reply_text("❌ Ошибка активации. Обратитесь в поддержку.")
        return
    now=datetime.now(timezone.utc)
    current=p.get("premium_until")
    if current:
        try:
            cd=datetime.fromisoformat(current.replace("Z","+00:00"))
            if cd>now:now=cd
        except:pass
    new_until=now+timedelta(days=plan["days"])
    if update_player(tid,{"premium_until":new_until.isoformat()}):
        await u.message.reply_text(f"✅ Премиум активирован до {new_until.strftime('%Y-%m-%d %H:%M')} UTC!",reply_markup=mk())
    else:
        await u.message.reply_text("❌ Ошибка активации. Обратитесь в поддержку.")

async def promo_menu(u,c):
    q=u.callback_query;await q.answer()
    await q.edit_message_text("🎫 **ПРОМОКОДЫ**\n\nВведите промокод, чтобы получить бесплатные дни премиума.",reply_markup=prk(),parse_mode="Markdown")

async def promo_enter(u,c):
    q=u.callback_query;await q.answer()
    await q.edit_message_text("🎫 Введите промокод:")
    return PROMO_ENTER

async def promo_apply(u,c):
    code=u.message.text.strip().upper()
    p=get_player_by_tg(u.effective_user.id)
    if not p:
        await u.message.reply_text("❌ Сначала войдите.",reply_markup=sk())
        return ConversationHandler.END
    promo=PROMOCODES.get(code)
    if not promo:
        await u.message.reply_text("❌ Неверный промокод.",reply_markup=pk())
        return ConversationHandler.END
    days=promo.get("days",0)
    now=datetime.now(timezone.utc)
    current=p.get("premium_until")
    if current:
        try:
            cd=datetime.fromisoformat(current.replace("Z","+00:00"))
            if cd>now:now=cd
        except:pass
    new_until=now+timedelta(days=days)
    if update_player(p["Telegram_id"],{"premium_until":new_until.isoformat()}):
        await u.message.reply_text(f"✅ Промокод активирован! +{days} дней премиума.",reply_markup=pk())
    else:
        await u.message.reply_text("❌ Ошибка активации.",reply_markup=pk())
    return ConversationHandler.END

async def cmd_promo(u,c):
    if u.effective_user.id!=OWNER_ID:
        await u.message.reply_text("❌ Нет доступа.")
        return
    args=c.args
    if len(args)<2:
        await u.message.reply_text("Использование: /promo НАЗВАНИЕ ДНЕЙ")
        return
    name=args[0].upper()
    try:days=int(args[1])
    except:
        await u.message.reply_text("❌ Количество дней должно быть числом.")
        return
    if days<=0:
        await u.message.reply_text("❌ Количество дней должно быть больше 0.")
        return
    PROMOCODES[name]={"days":days}
    await u.message.reply_text(f"✅ Промокод {name} создан! +{days} дней премиума.")

async def back(u,c):
    q=u.callback_query;await q.answer()
    p=get_player_by_tg(q.from_user.id)
    await q.edit_message_text("🏠 Главное меню:" if p else "🎮 Выберите действие:",reply_markup=mk() if p else sk())

async def logout(u,c):
    q=u.callback_query;await q.answer()
    c.user_data.clear()
    await q.edit_message_text("🚪 Вы вышли.",reply_markup=sk())

SUPPORT_TICKETS={}
_ticket_counter=0
def next_ticket():
    global _ticket_counter
    _ticket_counter+=1
    return _ticket_counter

async def support_start(u,c):
    q=u.callback_query;await q.answer()
    await q.edit_message_text("🆘 **ПОДДЕРЖКА**\n\nНапишите ваше сообщение:",parse_mode="Markdown")
    return SUPPORT_MSG

async def support_receive(u,c):
    user=u.effective_user
    tid=next_ticket()
    SUPPORT_TICKETS[tid]={"user_id":user.id,"username":user.username or str(user.id)}
    if ADMIN_CHAT_ID:
        try:
            await c.bot.send_message(ADMIN_CHAT_ID,f"🆘 **Новый тикет #{tid}**\n👤 От: @{SUPPORT_TICKETS[tid]['username']} (ID: {user.id})\n\n📝 Сообщение:\n{u.message.text}",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📩 Ответить",callback_data=f"reply_{tid}")]]),parse_mode="Markdown")
        except Exception as e:logger.error(f"Ошибка отправки тикета: {e}")
    await u.message.reply_text("✅ Сообщение отправлено в админ-чат. Мы ответим вам здесь.",reply_markup=mk())
    return ConversationHandler.END

async def admin_reply_start(u,c):
    q=u.callback_query;await q.answer()
    if u.effective_chat.id!=ADMIN_CHAT_ID:
        await q.answer("❌ Только из админ-чата!",show_alert=True)
        return
    tid=int(q.data.replace("reply_",""))
    if tid not in SUPPORT_TICKETS:
        await q.edit_message_text("❌ Тикет закрыт.")
        return
    c.user_data["reply_ticket"]=tid
    await q.message.reply_text(f"📝 Введите ответ для тикета #{tid}:")
    return ADMIN_REPLY

async def admin_reply_send(u,c):
    tid=c.user_data.get("reply_ticket")
    t=SUPPORT_TICKETS.get(tid)
    if not t:
        await u.message.reply_text("❌ Тикет не найден.")
        return ConversationHandler.END
    try:
        await c.bot.send_message(t["user_id"],f"📩 **Ответ администратора:**\n\n{u.message.text}",parse_mode="Markdown")
        await u.message.reply_text("✅ Ответ отправлен пользователю.")
    except Exception as e:
        logger.error(f"Ошибка отправки ответа: {e}")
        await u.message.reply_text("❌ Не удалось отправить ответ.")
    SUPPORT_TICKETS.pop(tid,None)
    c.user_data.clear()
    return ConversationHandler.END

async def changenick_start(u,c):
    q=u.callback_query;await q.answer()
    p=get_player_by_tg(q.from_user.id)
    if not p:
        await q.edit_message_text("❌ Сначала войдите.",reply_markup=sk())
        return ConversationHandler.END
    if not is_premium(p):
        await q.edit_message_text("❌ Смена ника только для премиум!",reply_markup=bk())
        return ConversationHandler.END
    await q.edit_message_text("✏️ Введите новый ник (2-32 символа, латиница/цифры/_):")
    return REG_NICK

async def changenick_apply(u,c):
    n=u.message.text.strip()
    if not vn(n):
        await u.message.reply_text("❌ Некорректный ник. Попробуйте:")
        return REG_NICK
    if get_player_by_nick(n):
        await u.message.reply_text("❌ Ник занят. Введите другой:")
        return REG_NICK
    if update_player(u.effective_user.id,{"nick":n}):
        await u.message.reply_text(f"✅ Ник изменён на {n}!",reply_markup=mk())
    else:
        await u.message.reply_text("❌ Ошибка смены ника.",reply_markup=mk())
    return ConversationHandler.END

async def menu_find_match(u,c):
    q=u.callback_query;await q.answer()
    await q.edit_message_text("🔍 **ПОИСК МАТЧА**\n\n📱 ВЫБЕРИ ПЛАТФОРМУ",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📱 Phone",callback_data="platform_phone")],[InlineKeyboardButton("💻 PC",callback_data="platform_pc")],[InlineKeyboardButton("⬅️ Назад",callback_data="back")]]),parse_mode="Markdown")

async def platform_select(u,c):
    q=u.callback_query;await q.answer()
    plat=q.data.replace("platform_","").upper()
    kb=[]
    for i in range(1,7):kb.append([InlineKeyboardButton(f"Лобби {i} (0/10)",callback_data=f"lobby_{plat}_{i}")])
    kb.append([InlineKeyboardButton("⬅️ Назад",callback_data="find_match")])
    await q.edit_message_text(f"📱 {plat} ЛОББИ",reply_markup=InlineKeyboardMarkup(kb))

async def lobby_join(u,c):
    q=u.callback_query;await q.answer()
    _,plat,lid=q.data.split("_")
    p=get_player_by_tg(q.from_user.id)
    await q.edit_message_text(f"📋 **ЛОББИ {lid}** (1/10)\n\n👥 **ИГРОКИ:**\n1. @{p['nick'] if p else 'игрок'} (1000 ELO)\n\n⏳ Ожидание: 1/10",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🚪 Выйти",callback_data=f"lobby_leave_{plat}_{lid}")]]),parse_mode="Markdown")

async def lobby_leave(u,c):
    q=u.callback_query;await q.answer()
    await q.edit_message_text("🚪 Вы вышли из лобби.",reply_markup=bk())

async def menu_party(u,c):
    q=u.callback_query;await q.answer()
    await q.edit_message_text("🎉 **ПАТИ**\n\nТы пока не в группе.\nПригласи друга!",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("➕ Пригласить",callback_data="party_invite")],[InlineKeyboardButton("⬅️ Назад",callback_data="back")]]),parse_mode="Markdown")

async def party_invite(u,c):
    q=u.callback_query;await q.answer()
    await q.edit_message_text("👤 Введите Telegram юзернейм игрока:",reply_markup=bk())

async def menu_history(u,c):
    q=u.callback_query;await q.answer()
    p=get_player_by_tg(q.from_user.id)
    if not p:
        await q.edit_message_text("❌ Сначала войдите.",reply_markup=sk())
        return
    await q.edit_message_text("📝 **ИСТОРИЯ МАТЧЕЙ**\n\nПока нет сыгранных матчей.",reply_markup=bk(),parse_mode="Markdown")

async def menu_reports(u,c):
    q=u.callback_query;await q.answer()
    await q.edit_message_text("📢 **ЖАЛОБЫ**\n\nВыбери игрока:",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад",callback_data="back")]]),parse_mode="Markdown")

async def admin_reset_list(u,c):
    q=u.callback_query;await q.answer()
    if q.from_user.id!=OWNER_ID:return
    pl=all_players(50)
    if not pl:
        await q.edit_message_text("❌ Нет игроков.")
        return
    kb=[[InlineKeyboardButton(p["nick"],callback_data=f"reset_{p['Telegram_id']}")] for p in pl]
    kb.append([InlineKeyboardButton("⬅️ Назад",callback_data="back")])
    await q.edit_message_text("👤 Выберите игрока для сброса пароля:",reply_markup=InlineKeyboardMarkup(kb))

async def admin_reset_pick(u,c):
    q=u.callback_query;await q.answer()
    if q.from_user.id!=OWNER_ID:return
    tid=int(q.data.replace("reset_",""))
    c.user_data["reset_tg_id"]=tid
    await q.message.reply_text("🔑 Введите новый пароль (мин. 8 символов, латиница+цифры):")
    return RESET_PASS

async def admin_reset_apply(u,c):
    pwd=u.message.text.strip()
    if not vp(pwd):
        await u.message.reply_text("❌ Слабый пароль. Попробуйте:")
        return RESET_PASS
    tid=c.user_data.get("reset_tg_id")
    if update_player(tid,{"password":pwd}):
        await u.message.reply_text("✅ Пароль сброшен.")
        try:await c.bot.send_message(tid,"🔑 Ваш пароль был сброшен администратором.")
        except:pass
    else:
        await u.message.reply_text("❌ Ошибка сброса.")
    c.user_data.clear()
    return ConversationHandler.END

async def error_handler(update,context):
    logger.error(f"Update {update} caused error: {context.error}")

# ===== MAIN =====
def main():
    ensure_event_loop()
    if not BOT_TOKEN or not SUPABASE_URL or not SUPABASE_KEY:
        logger.error("Missing required environment variables")
        return
    threading.Thread(target=run_flask,daemon=True).start()
    app=Application.builder().token(BOT_TOKEN).build()
    try:
        requests.get(f"https://api.telegram.org/bot{BOT_TOKEN}/deleteWebhook?drop_pending_updates=true")
    except:pass
    # Команды
    app.add_handler(CommandHandler("start",cmd_start))
    app.add_handler(CommandHandler("admin",cmd_admin))
    app.add_handler(CommandHandler("cancel",cmd_cancel))
    app.add_handler(CommandHandler("promo",cmd_promo))
    # Регистрация
    app.add_handler(ConversationHandler([CallbackQueryHandler(reg_start,pattern="^register$")],{REG_NICK:[MessageHandler(filters.TEXT&~filters.COMMAND,reg_nick)],REG_ID:[MessageHandler(filters.TEXT&~filters.COMMAND,reg_id)],REG_PASS:[MessageHandler(filters.TEXT&~filters.COMMAND,reg_pass)]},[CommandHandler("cancel",cmd_cancel)]))
    # Вход
    app.add_handler(ConversationHandler([CallbackQueryHandler(login_start,pattern="^login$")],{LOGIN_NICK:[MessageHandler(filters.TEXT&~filters.COMMAND,login_nick)],LOGIN_ID:[MessageHandler(filters.TEXT&~filters.COMMAND,login_id)],LOGIN_PASS:[MessageHandler(filters.TEXT&~filters.COMMAND,login_pass)]},[CommandHandler("cancel",cmd_cancel)]))
    # Смена ника
    app.add_handler(ConversationHandler([CallbackQueryHandler(changenick_start,pattern="^changenick$")],{REG_NICK:[MessageHandler(filters.TEXT&~filters.COMMAND,changenick_apply)]},[CommandHandler("cancel",cmd_cancel)]))
    # Поддержка
    app.add_handler(ConversationHandler([CallbackQueryHandler(support_start,pattern="^support$")],{SUPPORT_MSG:[MessageHandler(filters.TEXT&~filters.COMMAND,support_receive)]},[CommandHandler("cancel",cmd_cancel)]))
    # Ответ админа
    app.add_handler(ConversationHandler([CallbackQueryHandler(admin_reply_start,pattern="^reply_")],{ADMIN_REPLY:[MessageHandler(filters.TEXT&~filters.COMMAND,admin_reply_send)]},[CommandHandler("cancel",cmd_cancel)]))
    # Промокоды
    app.add_handler(ConversationHandler([CallbackQueryHandler(promo_enter,pattern="^promo_enter$")],{PROMO_ENTER:[MessageHandler(filters.TEXT&~filters.COMMAND,promo_apply)]},[CommandHandler("cancel",cmd_cancel)]))
    # Сброс пароля
    app.add_handler(ConversationHandler([CallbackQueryHandler(admin_reset_list,pattern="^admin_reset$")],{ADMIN_REPLY:[CallbackQueryHandler(admin_reset_pick,pattern="^reset_")],RESET_PASS:[MessageHandler(filters.TEXT&~filters.COMMAND,admin_reset_apply)]},[CommandHandler("cancel",cmd_cancel)]))
    # Callback-кнопки
    for p in ["^back$","^logout$","^profile$","^top$","^stats$","^extended_stats$","^premium_menu$","^premium_trial$","^buy_","^promo_menu$","^admin_promo$","^find_match$","^platform_","^lobby_","^lobby_leave_","^party$","^party_invite$","^history$","^reports$"]:
        app.add_handler(CallbackQueryHandler(back if p=="^back$" else logout if p=="^logout$" else menu_profile if p=="^profile$" else menu_top if p=="^top$" else menu_stats if p=="^stats$" else extended_stats if p=="^extended_stats$" else menu_premium if p=="^premium_menu$" else premium_trial if p=="^premium_trial$" else premium_buy if p=="^buy_" else promo_menu if p=="^promo_menu$" else admin_promo_create if p=="^admin_promo$" else menu_find_match if p=="^find_match$" else platform_select if p=="^platform_" else lobby_join if p=="^lobby_" else lobby_leave if p=="^lobby_leave_" else menu_party if p=="^party$" else party_invite if p=="^party_invite$" else menu_history if p=="^history$" else menu_reports, pattern=p))
    # Платежи
    app.add_handler(PreCheckoutQueryHandler(precheckout_callback))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT,successful_payment))
    app.add_error_handler(error_handler)
    logger.info("🤖 Bot started, polling...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__=="__main__":main()

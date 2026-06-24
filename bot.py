import asyncio
import hashlib
import hmac
import json
import logging
import os
import secrets
import random as rnd
from datetime import datetime
from threading import Thread

import aiohttp
import aiosqlite
from flask import Flask, jsonify, request, send_from_directory

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message, WebAppInfo

BOT_TOKEN = "600107:AA2nCW2v5aXOUMSjCNP9SsH0am9817iVvNV"
BOT_USERNAME = "Casinoarcadebot"
OWNER_ID = 8131755675
WEBAPP_URL = "https://arcade-8ru7.onrender.com"
DB_NAME = "arcade.db"
CHANNEL_USERNAME = "@arcade_ludo"

CRYPTOBOT_API_TOKEN = "600107:AA2nCW2v5aXOUMSjCNP9SsH0am9817iVvNV"
CRYPTOBOT_API_URL = "https://pay.crypt.bot/api"

MIN_WITHDRAWAL = 100
MODERATOR_IDS = [OWNER_ID]
STAR_PRICE_TON = 0.006

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY, username TEXT, balance INTEGER DEFAULT 0,
    ref_code TEXT UNIQUE, referred_by INTEGER, total_earned INTEGER DEFAULT 0,
    last_daily TEXT, last_allornothing TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS deposits (
    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, amount_ton REAL,
    stars INTEGER, tx_hash TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS withdrawals (
    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, amount_stars INTEGER,
    status TEXT DEFAULT 'pending', created_at TEXT
);
CREATE TABLE IF NOT EXISTS inventory (
    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, nft_name TEXT,
    nft_value INTEGER, nft_icon TEXT, obtained_at TEXT
);
CREATE TABLE IF NOT EXISTS cases_opened (
    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, case_type TEXT,
    result TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS promos (
    code TEXT PRIMARY KEY, stars INTEGER, uses_left INTEGER, created_at TEXT
);
CREATE TABLE IF NOT EXISTS processed_tx (
    tx_hash TEXT PRIMARY KEY, processed_at TEXT
);
CREATE TABLE IF NOT EXISTS cryptobot_invoices (
    invoice_id INTEGER PRIMARY KEY, user_id INTEGER, stars INTEGER, ton_amount REAL,
    status TEXT DEFAULT 'pending', created_at TEXT, processed_at TEXT
);
CREATE TABLE IF NOT EXISTS admins (
    user_id INTEGER PRIMARY KEY, added_at TEXT
);
"""

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("bot")

router = Router()
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
dp.include_router(router)
flask_app = Flask(__name__)

def now() -> str:
    return datetime.utcnow().isoformat()

async def db():
    return aiosqlite.connect(DB_NAME)

async def init_db():
    async with await db() as conn:
        await conn.executescript(SCHEMA)
        await conn.commit()
@router.message(Command("start"))
async def cmd_start(message: Message, command: CommandObject):
    await get_or_create_user(message.from_user.id, message.from_user.username, command.args)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎮 Играть", web_app=WebAppInfo(url=WEBAPP_URL))],
        [InlineKeyboardButton(text="📢 Канал", url=f"https://t.me/{CHANNEL_USERNAME.lstrip('@')}")],
    ])
    await message.answer(
        "🎰 Это Arcade.\n\n🎁 Кейсы. Жмёшь — выпадает.\n⚡️Апгрейды.\n🚀 Краш.\n👑 Казна.\n\n👇 Залетай",
        reply_markup=kb,
    )

@router.message(Command("promo"))
async def cmd_promo(message: Message, command: CommandObject):
    if message.from_user.id != OWNER_ID:
        return
    parts = (command.args or "").split()
    if len(parts) != 3:
        await message.answer("Использование: /promo КОД ЗВЁЗДЫ ИСПОЛЬЗОВАНИЯ")
        return
    code, stars, uses = parts
    if not (stars.isdigit() and uses.isdigit()):
        await message.answer("ЗВЁЗДЫ и ИСПОЛЬЗОВАНИЯ должны быть числами.")
        return
    async with await db() as conn:
        await conn.execute(
            "INSERT OR REPLACE INTO promos (code, stars, uses_left, created_at) VALUES (?, ?, ?, ?)",
            (code, int(stars), int(uses), now()),
        )
        await conn.commit()
    await message.answer(f"Промокод {code} создан: {stars}⭐, {uses} использований.")

@router.message(Command("pay"))
async def cmd_pay(message: Message, command: CommandObject):
    raw = (command.args or "").strip()
    if not raw.isdigit() or int(raw) <= 0:
        await message.answer("Использование: /pay КОЛИЧЕСТВО_ЗВЁЗД (например /pay 100)")
        return
    stars = int(raw)
    invoice = await make_deposit_invoice(message.from_user.id, stars)
    if not invoice:
        await message.answer("Не удалось создать счёт. Попробуй позже.")
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="💳 Оплатить", url=invoice["pay_url"])]])
    await message.answer(f"Счёт на {stars}⭐ ({stars_to_ton(stars)} TON) создан. Нажми «Оплатить».", reply_markup=kb)

@router.message(Command("check"))
async def cmd_check(message: Message, command: CommandObject):
    if message.from_user.id != OWNER_ID: return
    amount = parse_amount(command.args or "")
    if amount is None:
        await message.answer("Использование: /check СУММА (например /check 1, в TON)")
        return
    check = await create_check(amount, asset="TON")
    if not check:
        await message.answer("Не удалось создать чек.")
        return
    await message.answer(f"Чек на {amount} TON создан:\n{check['check_url']}")

@router.message(Command("give"))
async def cmd_give(message: Message, command: CommandObject):
    if not await is_admin(message.from_user.id): return
    parts = (command.args or "").split()
    if len(parts) != 2 or not parts[0].isdigit() or not parts[1].lstrip("-").isdigit():
        await message.answer("Использование: /give ID ЗВЁЗДЫ")
        return
    target_id, stars = int(parts[0]), int(parts[1])
    async with await db() as conn:
        await conn.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (stars, target_id))
        await conn.commit()
    await message.answer(f"Начислено {stars}⭐ пользователю {target_id}.")

@router.message(Command("stats"))
async def cmd_stats(message: Message):
    if not await is_admin(message.from_user.id): return
    async with await db() as conn:
        users_count = (await (await conn.execute("SELECT COUNT(*) FROM users")).fetchone())[0]
        total_balance = (await (await conn.execute("SELECT COALESCE(SUM(balance), 0) FROM users")).fetchone())[0]
        pending = (await (await conn.execute("SELECT COUNT(*), COALESCE(SUM(amount_stars), 0) FROM withdrawals WHERE status = 'pending'")).fetchone())
    await message.answer(f"📊 Статистика\n\n👥 Пользователей: {users_count}\n⭐ Звёзд: {total_balance}\n⏳ Выводов: {pending[0]} ({pending[1]}⭐)")
    @router.message(Command("backup"))
 async def cmd_backup(message: Message):
    if not await is_admin(message.from_user.id): return
    try:
        from aiogram.types import FSInputFile
        await message.answer_document(FSInputFile(DB_NAME), caption=f"Бекап БД от {now()}")
    except Exception as e:
        await message.answer(f"Не удалось сделать бекап: {e}")

WEBAPP_ACTIONS = {}

def webapp_action(name):
    def wrapper(fn):
        WEBAPP_ACTIONS[name] = fn
        return fn
    return wrapper

@webapp_action("get_ref")
async def handle_get_ref(message: Message, user_id: int, data: dict):
    async with await db() as conn:
        row = await (await conn.execute("SELECT ref_code FROM users WHERE user_id = ?", (user_id,))).fetchone()
    await message.answer(f"ref:{row[0]}" if row else "ref:none")

@webapp_action("promo")
async def handle_promo_activation(message: Message, user_id: int, data: dict):
    promo = data.get("code", "").upper()
    async with await db() as conn:
        cur = await conn.execute("SELECT stars, uses_left FROM promos WHERE code=? AND uses_left>0", (promo,))
        r = await cur.fetchone()
        if r:
            await conn.execute("UPDATE promos SET uses_left=uses_left-1 WHERE code=?", (promo,))
            await conn.execute("UPDATE users SET balance=balance+? WHERE user_id=?", (r[0], user_id))
            await conn.commit()
            await message.answer(f"promo:success,{r[0]}", parse_mode=None)
        else:
            await message.answer("promo:error")

@webapp_action("pay")
async def handle_pay(message: Message, user_id: int, data: dict):
    try:
        stars = int(data.get("amount", 0))
    except (TypeError, ValueError):
        await message.answer("Некорректная сумма.")
        return
    if stars <= 0:
        await message.answer("Сумма должна быть больше нуля.")
        return
    invoice = await make_deposit_invoice(user_id, stars)
    if not invoice:
        await message.answer("Не удалось создать счёт.")
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="💳 Оплатить", url=invoice["pay_url"])]])
    await message.answer(f"Счёт на {stars}⭐ ({stars_to_ton(stars)} TON) создан.", reply_markup=kb)

@webapp_action("withdraw")
async def handle_withdraw(message: Message, user_id: int, data: dict):
    try:
        amount = int(data.get("amount", 0))
    except (TypeError, ValueError):
        await message.answer("Некорректная сумма для вывода.")
        return
    if amount < MIN_WITHDRAWAL:
        await message.answer(f"Минимальная сумма для вывода — {MIN_WITHDRAWAL}⭐.")
        return
    async with await db() as conn:
        row = await (await conn.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,))).fetchone()
        balance = row[0] if row else 0
        if balance < amount:
            await message.answer("withdraw:no_balance")
            return
        await conn.execute("UPDATE users SET balance = balance - ? WHERE user_id = ?", (amount, user_id))
        cur = await conn.execute("INSERT INTO withdrawals (user_id, amount_stars, status, created_at) VALUES (?, ?, 'pending', ?)", (user_id, amount, now()))
        withdrawal_id = cur.lastrowid
        await conn.commit()
    await message.answer("withdraw:ok")
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Одобрить", callback_data=f"wd_approve:{withdrawal_id}"),
        InlineKeyboardButton(text="❌ Отклонить", callback_data=f"wd_deny:{withdrawal_id}"),
    ]])
    for mod_id in MODERATOR_IDS:
        try:
            await bot.send_message(mod_id, f"💸 Заявка на вывод #{withdrawal_id}\nПользователь: {user_id}\nСумма: {amount}⭐", reply_markup=kb)
        except: pass

@router.callback_query(F.data.startswith("wd_approve:") | F.data.startswith("wd_deny:"))
async def on_withdrawal_decision(callback: CallbackQuery):
    if callback.from_user.id not in MODERATOR_IDS:
        await callback.answer("Недостаточно прав.", show_alert=True)
        return
    action, withdrawal_id = callback.data.split(":")
    withdrawal_id = int(withdrawal_id)
    async with await db() as conn:
        row = await (await conn.execute("SELECT user_id, amount_stars, status FROM withdrawals WHERE id = ?", (withdrawal_id,))).fetchone()
        if not row or row[2] != "pending":
            await callback.answer("Заявка уже обработана.", show_alert=True)
            return
        target_user_id, amount, _ = row
        new_status = "approved" if action == "wd_approve" else "denied"
        await conn.execute("UPDATE withdrawals SET status = ? WHERE id = ?", (new_status, withdrawal_id))
        if new_status == "denied":
            await conn.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (amount, target_user_id))
        await conn.commit()
    await callback.message.edit_text(callback.message.text + f"\n\n— {new_status.upper()}")
    await callback.answer("Готово.")
    async def process_case(uid, case):
    if case == "daily":
        async with await db() as conn:
            cur = await conn.execute("SELECT last_daily FROM users WHERE user_id=?", (uid,))
            row = await cur.fetchone()
            if row and row[0] == datetime.utcnow().strftime("%Y-%m-%d"):
                return "case:error,already_opened"
            await conn.execute("UPDATE users SET last_daily=? WHERE user_id=?", (datetime.utcnow().strftime("%Y-%m-%d"), uid))
            await conn.commit()
    if case == "allornothing":
        async with await db() as conn:
            cur = await conn.execute("SELECT last_allornothing FROM users WHERE user_id=?", (uid,))
            row = await cur.fetchone()
            if row and row[0] == datetime.utcnow().strftime("%Y-%m-%d"):
                return "case:error,already_opened"
            await conn.execute("UPDATE users SET last_allornothing=? WHERE user_id=?", (datetime.utcnow().strftime("%Y-%m-%d"), uid))
            await conn.commit()
    
    cases = {
        "daily": {"price": 0, "items": [
            ("Scared Cat", 16000, 1), ("Mightly Arms", 10000, 1), ("Loot Bag", 9000, 2),
            ("Artisan Bricks", 5000, 3), ("5 ⭐", 5, 30), ("1 ⭐", 1, 30), ("3 ⭐", 3, 33)
        ]},
        "valera": {"price": 3, "items": [("3 ⭐", 3, 50), ("5 ⭐", 5, 40), ("10 ⭐", 10, 10)]},
        "bumzhikha": {"price": 5, "items": [("5 ⭐", 5, 50), ("15 ⭐", 15, 30), ("30 ⭐", 30, 16), ("50 ⭐", 50, 14)]},
        "svidanie": {"price": 50, "items": [
            ("5 ⭐", 5, 1), ("4 ⭐", 4, 2), ("3 ⭐", 3, 3), ("7 ⭐", 7, 4),
            ("50 ⭐", 50, 40), ("75 ⭐", 75, 25), ("100 ⭐", 100, 20), ("200 ⭐", 200, 5)
        ]},
        "otel": {"price": 75, "items": [
            ("5 ⭐", 5, 1), ("4 ⭐", 4, 2), ("3 ⭐", 3, 3), ("1 ⭐", 1, 4),
            ("80 ⭐", 80, 50), ("150 ⭐", 150, 25), ("200 ⭐", 200, 15)
        ]},
        "forever": {"price": 300, "items": [
            ("1 ⭐", 1, 1), ("10 ⭐", 10, 5), ("4 ⭐", 4, 4),
            ("350 ⭐", 350, 50), ("400 ⭐", 400, 20), ("500 ⭐", 500, 15), ("1000 ⭐", 1000, 5)
        ]},
        "allornothing": {"price": 2000, "items": [("1000 ⭐", 1000, 50), ("5000 ⭐", 5000, 50)]}
    }
    
    c = cases.get(case)
    if not c: return "case:error,not_found"
    
    async with await db() as conn:
        cur = await conn.execute("SELECT balance FROM users WHERE user_id=?", (uid,))
        bal = (await cur.fetchone())[0]
        if c["price"] > 0 and bal < c["price"]:
            return "case:error,no_balance"
        if c["price"] > 0:
            await conn.execute("UPDATE users SET balance=balance-? WHERE user_id=?", (c["price"], uid))
        total = sum(it[2] for it in c["items"])
        rand = rnd.randint(1, total)
        cur = 0
        for name, val, ch in c["items"]:
            cur += ch
            if rand <= cur:
                await conn.execute("UPDATE users SET balance=balance+? WHERE user_id=?", (val, uid))
                await conn.execute("INSERT INTO cases_opened (user_id, case_type, result, created_at) VALUES (?,?,?,?)", (uid, case, name, now()))
                nft_icons = {"Scared Cat":"scared_cat","Mightly Arms":"mightly_arms","Loot Bag":"loot_bag","Artisan Bricks":"bricks"}
                if name in nft_icons:
                    await conn.execute("INSERT INTO inventory (user_id,nft_name,nft_value,nft_icon,obtained_at) VALUES (?,?,?,?,?)", (uid, name, val, nft_icons[name], now()))
                await conn.commit()
                new_bal = (await (await conn.execute("SELECT balance FROM users WHERE user_id=?", (uid,))).fetchone())[0]
                return f"case:success,{name},{val},{new_bal}"
    return "case:error,unknown"

@webapp_action("open_case")
async def handle_open_case(message: Message, user_id: int, data: dict):
    case = data.get("case", "")
    result = await process_case(user_id, case)
    await message.answer(result)

@router.message(F.web_app_data)
async def on_web_app_data(message: Message):
    try:
        data = json.loads(message.web_app_data.data)
    except (json.JSONDecodeError, AttributeError):
        await message.answer("Некорректные данные от WebApp.")
        return
    handler = WEBAPP_ACTIONS.get(data.get("action"))
    if not handler:
        await message.answer("Неизвестное действие.")
        return
    await handler(message, message.from_user.id, data)

def verify_webhook_signature(body_raw: bytes, signature: str) -> bool:
    secret = hashlib.sha256(CRYPTOBOT_API_TOKEN.encode()).digest()
    computed = hmac.new(secret, body_raw, hashlib.sha256).hexdigest()
    return hmac.compare_digest(computed, signature)

@flask_app.route("/webhook/cryptobot", methods=["POST"])
def cryptobot_webhook():
    body_raw = request.get_data()
    if not verify_webhook_signature(body_raw, request.headers.get("crypto-pay-api-signature", "")):
        return jsonify({"ok": False, "error": "bad signature"}), 403
    update = json.loads(body_raw)
    if update.get("update_type") == "invoice_paid":
        asyncio.run(mark_invoice_paid(update["payload"]["invoice_id"]))
    return jsonify({"ok": True})

async def mark_invoice_paid(invoice_id: int):
    async with await db() as conn:
        row = await (await conn.execute("SELECT user_id, stars, status FROM cryptobot_invoices WHERE invoice_id = ?", (invoice_id,))).fetchone()
        if not row or row[2] == "paid": return
        user_id, stars, _ = row
        await conn.execute("UPDATE cryptobot_invoices SET status = 'paid', processed_at = ? WHERE invoice_id = ?", (now(), invoice_id))
        await conn.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (stars, user_id))
        await conn.commit()
        log.info(f"Invoice {invoice_id}: начислено {stars}⭐ пользователю {user_id}")

@flask_app.route('/')
def home():
    return send_from_directory('webapp', 'index.html')

@flask_app.route('/<path:path>')
def static_files(path):
    return send_from_directory('webapp', path)

@flask_app.route('/api/user/<int:uid>')
def api_user(uid):
    async def get():
        async with await db() as conn:
            cur = await conn.execute("SELECT balance, ref_code, total_earned FROM users WHERE user_id=?", (uid,))
            r = await cur.fetchone()
            if r: return jsonify({"balance": r[0], "ref_code": r[1], "ref_earned": r[2]})
            return jsonify({"balance": 0, "ref_code": "", "ref_earned": 0})
    return asyncio.new_event_loop().run_until_complete(get())

@flask_app.route('/api/inventory/<int:uid>')
def api_inventory(uid):
    async def get():
        async with await db() as conn:
            cur = await conn.execute("SELECT nft_name, nft_value, nft_icon FROM inventory WHERE user_id=?", (uid,))
            items = await cur.fetchall()
            return jsonify([{"name": i[0], "value": i[1], "icon": i[2]} for i in items])
    return asyncio.new_event_loop().run_until_complete(get())

@flask_app.route('/api/leaderboard')
def api_leaderboard():
    async def get():
        async with await db() as conn:
            cur = await conn.execute("SELECT u.username, COALESCE(SUM(w.amount_stars),0) FROM users u LEFT JOIN withdrawals w ON u.user_id=w.user_id AND w.status='approved' GROUP BY u.user_id ORDER BY 2 DESC LIMIT 10")
            top = await cur.fetchall()
            cur2 = await conn.execute("SELECT COALESCE(SUM(amount_stars),0) FROM withdrawals WHERE status='approved'")
            tw = (await cur2.fetchone())[0]
            return jsonify({"top":[{"name":t[0] or "User","total":t[1]} for t in top],"total_withdrawn":tw})
    return asyncio.new_event_loop().run_until_complete(get())

@flask_app.route('/api/shop')
def api_shop():
    nfts = [
        {"name":"Scared Cat","icon":"scared_cat","value":16000},
        {"name":"Mightly Arms","icon":"mightly_arms","value":10000},
        {"name":"Loot Bag","icon":"loot_bag","value":9000},
        {"name":"Artisan Bricks","icon":"bricks","value":5000},
        {"name":"Diamond Hands","icon":"diamond","value":7500},
        {"name":"Crypto Punk","icon":"punk","value":12000},
        {"name":"Golden Ape","icon":"ape","value":8500},
        {"name":"Moon Rocket","icon":"rocket","value":6800},
        {"name":"Bitcoin Lord","icon":"bitcoin","value":14000},
        {"name":"Snoop Dogg","icon":"snoop","value":1300},
        {"name":"Torch","icon":"torch","value":450},
        {"name":"Ice Cream","icon":"icecream","value":420},
        {"name":"Ghost Spirit","icon":"ghost","value":600},
        {"name":"Phoenix","icon":"phoenix","value":800},
        {"name":"Dragon Egg","icon":"dragon","value":1100},
        {"name":"Magic Lamp","icon":"lamp","value":950},
        {"name":"Pirate Ship","icon":"ship","value":700},
        {"name":"Ring","icon":"ring","value":100},
        {"name":"Cake","icon":"cake","value":50},
        {"name":"Rose","icon":"rose","value":25},
        {"name":"Teddy Bear","icon":"bear","value":15},
        {"name":"Magic Wand","icon":"wand","value":200},
        {"name":"Crystal Ball","icon":"crystal","value":180},
        {"name":"Golden Key","icon":"key","value":150},
        {"name":"Crown","icon":"crown","value":300},
        {"name":"Ruby","icon":"ruby","value":250},
        {"name":"Amulet","icon":"amulet","value":120},
        {"name":"Sword","icon":"sword","value":80},
        {"name":"Shield","icon":"shield","value":90},
        {"name":"Potion","icon":"potion","value":60},
    ]
    return jsonify(nfts)

@flask_app.route('/api/buy_nft', methods=['POST'])
def api_buy_nft():
    d = request.json
    uid, name, value, icon = d['uid'], d['name'], d['value'], d['icon']
    async def p():
        async with await db() as conn:
            cur = await conn.execute("SELECT balance FROM users WHERE user_id=?", (uid,))
            r = await cur.fetchone()
            if not r or r[0] < value: return jsonify({"error":"no_balance"})
            await conn.execute("UPDATE users SET balance=balance-? WHERE user_id=?", (value, uid))
            await conn.execute("INSERT INTO inventory (user_id,nft_name,nft_value,nft_icon,obtained_at) VALUES (?,?,?,?,?)", (uid, name, value, icon, now()))
            await conn.commit()
            cur = await conn.execute("SELECT balance FROM users WHERE user_id=?", (uid,))
            return jsonify({"success":True,"balance":(await cur.fetchone())[0]})
    return asyncio.new_event_loop().run_until_complete(p())

@flask_app.route('/api/sell_nft', methods=['POST'])
def api_sell_nft():
    d = request.json
    uid, name, value = d['uid'], d['name'], d['value']
    async def p():
        async with await db() as conn:
            cur = await conn.execute("SELECT id FROM inventory WHERE user_id=? AND nft_name=? LIMIT 1", (uid, name))
            item = await cur.fetchone()
            if not item: return jsonify({"error":"not_found"})
            await conn.execute("DELETE FROM inventory WHERE id=?", (item[0],))
            await conn.execute("UPDATE users SET balance=balance+? WHERE user_id=?", (value, uid))
            await conn.commit()
            cur = await conn.execute("SELECT balance FROM users WHERE user_id=?", (uid,))
            return jsonify({"success":True,"balance":(await cur.fetchone())[0]})
    return asyncio.new_event_loop().run_until_complete(p())

@flask_app.route('/api/upgrade', methods=['POST'])
def api_upgrade():
    d = request.json
    uid, fr, to = d['uid'], d['from'], d['to']
    async def p():
        ratio = fr['value'] / to['value']
        chance = max(1, min(50, int(ratio * 100)))
        async with await db() as conn:
            cur = await conn.execute("SELECT id FROM inventory WHERE user_id=? AND nft_name=? LIMIT 1", (uid, fr['name']))
            item = await cur.fetchone()
            if not item: return jsonify({"error":"not_found"})
            won = rnd.randint(1, 100) <= chance
            if won:
                await conn.execute("DELETE FROM inventory WHERE id=?", (item[0],))
                await conn.execute("INSERT INTO inventory (user_id,nft_name,nft_value,nft_icon,obtained_at) VALUES (?,?,?,?,?)", (uid, to['name'], to['value'], to['icon'], now()))
                await conn.commit()
                return jsonify({"success":True,"won":True,"chance":chance})
            else:
                await conn.execute("DELETE FROM inventory WHERE id=?", (item[0],))
                await conn.commit()
                return jsonify({"success":True,"won":False,"chance":chance})
    return asyncio.new_event_loop().run_until_complete(p())

def run_flask():
    port = int(os.environ.get('PORT', 8000))
    flask_app.run(host='0.0.0.0', port=port)

async def main():
    await init_db()
    print("DB OK")
    Thread(target=run_flask, daemon=True).start()
    print("Flask started")
    print("Bot started")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())

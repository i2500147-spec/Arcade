import asyncio
import json
import os
import random
import sys
import traceback
import time
import fcntl
lock_file = open('/tmp/bot.lock', 'w')
try:
    fcntl.lockf(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
except IOError:
    print("Another instance running, exiting")
    sys.exit(0)
from datetime import datetime
from threading import Thread
from flask import Flask, send_from_directory, request, jsonify
import aiosqlite
import aiohttp
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, WebAppInfo, MenuButtonWebApp
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties

def log_error(exc_type, exc_value, exc_tb):
    print("ERROR:", exc_type.__name__, exc_value)
    traceback.print_exception(exc_type, exc_value, exc_tb)

sys.excepthook = log_error
print("Starting bot...")

BOT_TOKEN = "8894875970:AAEZ6T_kSN9MJjs7mLapE-SohhJTaRcsZgM"
BOT_USERNAME = "Casinoarcadebot"
OWNER_ID = 8131755675
TON_WALLET = "UQAISFpye-QozqPlK1iX_qHPmYzEphSNalQsFojALxuLXpx6"
STAR_PRICE_TON = 0.006
WEBAPP_URL = "https://arcade-8ru7.onrender.com"
DB_NAME = "arcade.db"

flask_app = Flask(__name__, static_folder='webapp')

@flask_app.route('/')
def home():
    return send_from_directory('webapp', 'index.html')

@flask_app.route('/<path:path>')
def static_files(path):
    return send_from_directory('webapp', path)

@flask_app.route('/api/user/<int:uid>')
def api_user(uid):
    async def get():
        async with aiosqlite.connect(DB_NAME) as db:
            cur = await db.execute("SELECT balance, ref_code, ref_earned FROM users WHERE user_id=?", (uid,))
            r = await cur.fetchone()
            if r: return jsonify({"balance": r[0], "ref_code": r[1], "ref_earned": r[2]})
            return jsonify({"balance": 0, "ref_code": "", "ref_earned": 0})
    return asyncio.new_event_loop().run_until_complete(get())

@flask_app.route('/api/inventory/<int:uid>')
def api_inventory(uid):
    async def get():
        async with aiosqlite.connect(DB_NAME) as db:
            cur = await db.execute("SELECT item_name, item_value, item_icon FROM inventory WHERE user_id=?", (uid,))
            items = await cur.fetchall()
            return jsonify([{"name": i[0], "value": i[1], "icon": i[2]} for i in items])
    return asyncio.new_event_loop().run_until_complete(get())

@flask_app.route('/api/leaderboard')
def api_leaderboard():
    async def get():
        async with aiosqlite.connect(DB_NAME) as db:
            cur = await db.execute("SELECT u.username, u.first_name, COALESCE(SUM(w.amount_stars),0) FROM users u LEFT JOIN withdrawals w ON u.user_id=w.user_id AND w.status='done' GROUP BY u.user_id ORDER BY 3 DESC LIMIT 10")
            top = await cur.fetchall()
            cur2 = await db.execute("SELECT COALESCE(SUM(amount_stars),0) FROM withdrawals WHERE status='done'")
            tw = (await cur2.fetchone())[0]
            return jsonify({"top":[{"name":t[1] or t[0] or "User","total":t[2]} for t in top],"total_withdrawn":tw})
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
        async with aiosqlite.connect(DB_NAME) as db:
            cur = await db.execute("SELECT balance FROM users WHERE user_id=?", (uid,))
            r = await cur.fetchone()
            if not r or r[0] < value: return jsonify({"error":"no_balance"})
            await db.execute("UPDATE users SET balance=balance-? WHERE user_id=?", (value, uid))
            await db.execute("INSERT INTO inventory (user_id,item_name,item_value,item_icon) VALUES (?,?,?,?)", (uid, name, value, icon))
            await db.commit()
            cur = await db.execute("SELECT balance FROM users WHERE user_id=?", (uid,))
            return jsonify({"success":True,"balance":(await cur.fetchone())[0]})
    return asyncio.new_event_loop().run_until_complete(p())

@flask_app.route('/api/sell_nft', methods=['POST'])
def api_sell_nft():
    d = request.json
    uid, name, value = d['uid'], d['name'], d['value']
    async def p():
        async with aiosqlite.connect(DB_NAME) as db:
            cur = await db.execute("SELECT id FROM inventory WHERE user_id=? AND item_name=? LIMIT 1", (uid, name))
            item = await cur.fetchone()
            if not item: return jsonify({"error":"not_found"})
            await db.execute("DELETE FROM inventory WHERE id=?", (item[0],))
            await db.execute("UPDATE users SET balance=balance+? WHERE user_id=?", (value, uid))
            await db.commit()
            cur = await db.execute("SELECT balance FROM users WHERE user_id=?", (uid,))
            return jsonify({"success":True,"balance":(await cur.fetchone())[0]})
    return asyncio.new_event_loop().run_until_complete(p())

@flask_app.route('/api/upgrade', methods=['POST'])
def api_upgrade():
    d = request.json
    uid, fr, to = d['uid'], d['from'], d['to']
    async def p():
        ratio = fr['value'] / to['value']
        chance = max(1, min(50, int(ratio * 100)))
        async with aiosqlite.connect(DB_NAME) as db:
            cur = await db.execute("SELECT id FROM inventory WHERE user_id=? AND item_name=? LIMIT 1", (uid, fr['name']))
            item = await cur.fetchone()
            if not item: return jsonify({"error":"not_found"})
            won = random.randint(1, 100) <= chance
            if won:
                await db.execute("DELETE FROM inventory WHERE id=?", (item[0],))
                await db.execute("INSERT INTO inventory (user_id,item_name,item_value,item_icon) VALUES (?,?,?,?)", (uid, to['name'], to['value'], to['icon']))
                await db.commit()
                return jsonify({"success":True,"won":True,"chance":chance})
            else:
                await db.execute("DELETE FROM inventory WHERE id=?", (item[0],))
                await db.commit()
                return jsonify({"success":True,"won":False,"chance":chance})
    return asyncio.new_event_loop().run_until_complete(p())

async def process_case(uid, case, s, chat_id):
    if case == "daily":
        try:
            async with s.get(f"https://api.telegram.org/bot{BOT_TOKEN}/getChatMember?chat_id=@arcade_ludo&user_id={uid}") as r:
                member = await r.json()
                ok = member.get('ok')
                status = member.get('result', {}).get('status', '')
                if not ok or status in ['left', 'kicked']:
                    return "case:error,not_subscribed"
        except:
            return "case:error,not_subscribed"
        
        async with aiosqlite.connect(DB_NAME) as db:
            cur = await db.execute("SELECT last_daily FROM users WHERE user_id=?", (uid,))
            r = await cur.fetchone()
            if r and r[0] == datetime.now().strftime("%Y-%m-%d"):
                return "case:error,already_opened"
            await db.execute("UPDATE users SET last_daily=? WHERE user_id=?", (datetime.now().strftime("%Y-%m-%d"), uid))
            await db.commit()
    
    if case == "allornothing":
        async with aiosqlite.connect(DB_NAME) as db:
            cur = await db.execute("SELECT last_allornothing FROM users WHERE user_id=?", (uid,))
            r = await cur.fetchone()
            if r and r[0] == datetime.now().strftime("%Y-%m-%d"):
                return "case:error,already_opened"
            await db.execute("UPDATE users SET last_allornothing=? WHERE user_id=?", (datetime.now().strftime("%Y-%m-%d"), uid))
            await db.commit()
    
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
    
    async with aiosqlite.connect(DB_NAME) as db:
        cur = await db.execute("SELECT balance FROM users WHERE user_id=?", (uid,))
        bal = (await cur.fetchone())[0]
        
        if c["price"] > 0 and bal < c["price"]:
            return "case:error,no_balance"
        
        if c["price"] > 0:
            await db.execute("UPDATE users SET balance=balance-? WHERE user_id=?", (c["price"], uid))
        
        total = sum(it[2] for it in c["items"])
        rnd = random.randint(1, total)
        cur = 0
        for name, val, ch in c["items"]:
            cur += ch
            if rnd <= cur:
                await db.execute("UPDATE users SET balance=balance+? WHERE user_id=?", (val, uid))
                await db.execute("INSERT INTO cases_opened (user_id, case_name, reward, reward_value) VALUES (?,?,?,?)", (uid, case, name, val))
                nft_icons = {"Scared Cat":"scared_cat","Mightly Arms":"mightly_arms","Loot Bag":"loot_bag","Artisan Bricks":"bricks"}
                if name in nft_icons:
                    await db.execute("INSERT INTO inventory (user_id, item_name, item_value, item_icon) VALUES (?,?,?,?)", (uid, name, val, nft_icons[name]))
                await db.commit()
                new_bal = (await (await db.execute("SELECT balance FROM users WHERE user_id=?", (uid,))).fetchone())[0]
                return f"case:success,{name},{val},{new_bal}"
    return "case:error,unknown"

async def start_cmd(message, bot):
    uid = message.from_user.id
    uname = message.from_user.username or ''
    fn = message.from_user.first_name or ''
    
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("INSERT OR IGNORE INTO users (user_id, username, first_name) VALUES (?,?,?)", (uid, uname, fn))
        cur = await db.execute("SELECT ref_code FROM users WHERE user_id=?", (uid,))
        r = await cur.fetchone()
        if not r or not r[0]:
            code = ''.join(random.choices('abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789', k=8))
            await db.execute("UPDATE users SET ref_code=? WHERE user_id=?", (code, uid))
        
        args = message.text.split()
        if len(args) > 1 and args[1].startswith("ref_"):
            ref_code = args[1].replace("ref_", "")
            cur2 = await db.execute("SELECT user_id FROM users WHERE ref_code=?", (ref_code,))
            ref_user = await cur2.fetchone()
            if ref_user and ref_user[0] != uid:
                cur3 = await db.execute("SELECT id FROM users WHERE user_id=? AND invited_by IS NOT NULL", (uid,))
                if not await cur3.fetchone():
                    await db.execute("UPDATE users SET invited_by=? WHERE user_id=?", (ref_user[0], uid))
        await db.commit()
    
    await bot.set_chat_menu_button(chat_id=uid, menu_button=MenuButtonWebApp(text="🎮 Играть", web_app=WebAppInfo(url=WEBAPP_URL)))
    
    kb = ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="🎮 Играть", web_app=WebAppInfo(url=WEBAPP_URL))],
        [KeyboardButton(text="📢 Канал", url="https://t.me/arcade_ludo")]
    ], resize_keyboard=True)
    
    await message.answer("🎰 <b>Это Arcade.</b>\n\n🎁 <b>Кейсы.</b> Жмёшь — выпадает.\n⚡️ <b>Апгрейды.</b>\n🚀 <b>Краш.</b>\n👑 <b>Казна.</b>\n\n👇 Залетай", reply_markup=kb, parse_mode=ParseMode.HTML)

async def webapp_handler(message, bot):
    uid = message.from_user.id
    data = json.loads(message.web_app_data.data)
    act = data.get('action')
    
    if act == "get_ref":
        async with aiosqlite.connect(DB_NAME) as db:
            cur = await db.execute("SELECT ref_code FROM users WHERE user_id=?", (uid,))
            r = await cur.fetchone()
            code = r[0] if r and r[0] else ''.join(random.choices('abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789', k=8))
            if not r or not r[0]:
                await db.execute("UPDATE users SET ref_code=? WHERE user_id=?", (code, uid))
                await db.commit()
        await message.answer(f"ref:{code}")
    
    elif act == "promo":
        promo = data.get("code", "").upper()
        async with aiosqlite.connect(DB_NAME) as db:
            cur = await db.execute("SELECT stars, uses_left FROM promos WHERE code=? AND uses_left>0", (promo,))
            r = await cur.fetchone()
            if r:
                await db.execute("UPDATE promos SET uses_left=uses_left-1 WHERE code=?", (promo,))
                await db.execute("UPDATE users SET balance=balance+? WHERE user_id=?", (r[0], uid))
                await db.commit()
                await message.answer(f"promo:success,{r[0]}")
            else:
                await message.answer("promo:error")
    
    elif act == "pay":
        stars = data.get("amount", 0)
        ton = round(stars * STAR_PRICE_TON, 4)
        comm = f"BUY_{uid}_{int(datetime.now().timestamp())}"
        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute("INSERT INTO deposits (user_id, amount_ton, amount_stars, expected_amount, comment) VALUES (?,?,?,?,?)", (uid, ton, stars, ton, comm))
            await db.commit()
        link = f"ton://transfer/{TON_WALLET}?amount={ton}&text={comm}"
        await message.answer(f"💎 <b>{stars} ⭐</b>\n💳 <b>{ton} TON</b>\n\n📤 <code>{TON_WALLET}</code>\n💬 <code>{comm}</code>\n\n🔗 <a href='{link}'>Оплатить</a>", parse_mode=ParseMode.HTML)
    
    elif act == "withdraw":
        stars = data.get("amount", 0)
        async with aiosqlite.connect(DB_NAME) as db:
            cur = await db.execute("SELECT balance FROM users WHERE user_id=?", (uid,))
            bal = (await cur.fetchone())[0]
            if stars < 100:
                await message.answer("withdraw:min")
                return
            if stars > bal:
                await message.answer("withdraw:no_balance")
                return
            await db.execute("UPDATE users SET balance=balance-? WHERE user_id=?", (stars, uid))
            await db.execute("INSERT INTO withdrawals (user_id, amount_stars) VALUES (?,?)", (uid, stars))
            await db.commit()
        user_mention = message.from_user.username or f"ID:{uid}"
        await bot.send_message(OWNER_ID, f"📤 Вывод\n👤 @{user_mention}\n🆔 {uid}\n💎 {stars} ⭐\n📅 {datetime.now().strftime('%d.%m.%Y %H:%M')}")
        await message.answer("withdraw:ok")
    
    elif act == "open_case":
        case = data.get("case", "")
        async with aiohttp.ClientSession() as s:
            result = await process_case(uid, case, s, message.chat.id)
        await message.answer(result)
    
    elif act and act.startswith('/promo') and uid == OWNER_ID:
        parts = message.text.split() if hasattr(message, 'text') else []
        if len(parts) == 4:
            promo_code = parts[1].upper()
            stars = int(parts[2])
            uses = int(parts[3])
            async with aiosqlite.connect(DB_NAME) as db:
                await db.execute("CREATE TABLE IF NOT EXISTS promos (code TEXT PRIMARY KEY, stars INTEGER, uses_left INTEGER)")
                await db.execute("INSERT OR REPLACE INTO promos VALUES (?,?,?)", (promo_code, stars, uses))
                await db.commit()
            await message.answer(f"✅ Промокод <b>{promo_code}</b> на {stars}⭐ ({uses} исп.) создан!", parse_mode=ParseMode.HTML)

async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, username TEXT, first_name TEXT, balance INTEGER DEFAULT 0, ref_code TEXT UNIQUE, invited_by INTEGER, ref_earned INTEGER DEFAULT 0, last_daily TEXT, last_allornothing TEXT)")
        await db.execute("CREATE TABLE IF NOT EXISTS deposits (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, amount_ton REAL, amount_stars INTEGER, expected_amount REAL, comment TEXT UNIQUE, status TEXT DEFAULT 'waiting', tx_hash TEXT, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)")
        await db.execute("CREATE TABLE IF NOT EXISTS withdrawals (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, amount_stars INTEGER, status TEXT DEFAULT 'pending', timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)")
        await db.execute("CREATE TABLE IF NOT EXISTS inventory (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, item_name TEXT, item_value INTEGER, item_icon TEXT, obtained_at DATETIME DEFAULT CURRENT_TIMESTAMP)")
        await db.execute("CREATE TABLE IF NOT EXISTS cases_opened (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, case_name TEXT, reward TEXT, reward_value INTEGER, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)")
        await db.execute("CREATE TABLE IF NOT EXISTS processed_tx (tx_hash TEXT PRIMARY KEY)")
        await db.execute("CREATE TABLE IF NOT EXISTS promos (code TEXT PRIMARY KEY, stars INTEGER, uses_left INTEGER)")
        await db.commit()

def run_flask():
    port = int(os.environ.get('PORT', 8000))
    flask_app.run(host='0.0.0.0', port=port)

async def main():
    await init_db()
    print("DB OK")
    Thread(target=run_flask, daemon=True).start()
    print("Flask started")
    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    dp.message.register(start_cmd, Command("start"))
    dp.message.register(webapp_handler, F.web_app_data)
    print("Bot started - POLLING")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())

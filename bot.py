import asyncio
import logging
import json
import os
from datetime import datetime
from threading import Thread
from flask import Flask, send_from_directory, request, jsonify
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, WebAppInfo, MenuButtonWebApp
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
import aiosqlite
import aiohttp
import random
import string

# ==================== НАСТРОЙКИ ====================
BOT_TOKEN = "8965870385:AAHZ_zppdEcBPIVl2DIdgNwds4z-BqYfv5A"
print(f"Токен загружен: {BOT_TOKEN[:5]}...")
BOT_USERNAME = "arcadecasinobot"
CHANNEL_USERNAME = "@arcade_ludo"
CHANNEL_TAG = "arcade_ludo"
OWNER_ID = 8131755675
TON_WALLET = "UQAISFpye-QozqPlK1iX_qHPmYzEphSNalQsFojALxuLXpx6"
STAR_PRICE_TON = 0.006
WEBAPP_URL = "https://arcade-ycih.onrender.com"
DB_NAME = "arcade.db"

# ==================== FLASK ====================
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
            if r:
                return jsonify({"balance": r[0], "ref_code": r[1], "ref_earned": r[2]})
            return jsonify({"balance": 0, "ref_code": "", "ref_earned": 0})
    return asyncio.new_event_loop().run_until_complete(get())

@flask_app.route('/api/inventory/<int:uid>')
def api_inventory(uid):
    async def get():
        async with aiosqlite.connect(DB_NAME) as db:
            cur = await db.execute("SELECT item_name, item_value, item_emoji FROM inventory WHERE user_id=? ORDER BY obtained_at DESC", (uid,))
            items = await cur.fetchall()
            return jsonify([{"name": i[0], "value": i[1], "emoji": i[2]} for i in items])
    return asyncio.new_event_loop().run_until_complete(get())

@flask_app.route('/api/leaderboard')
def api_leaderboard():
    async def get():
        async with aiosqlite.connect(DB_NAME) as db:
            cur = await db.execute("SELECT u.username, u.first_name, COALESCE(SUM(w.amount_stars), 0) FROM users u LEFT JOIN withdrawals w ON u.user_id=w.user_id AND w.status='done' GROUP BY u.user_id ORDER BY 3 DESC LIMIT 10")
            top = await cur.fetchall()
            cur2 = await db.execute("SELECT COALESCE(SUM(amount_stars), 0) FROM withdrawals WHERE status='done'")
            tw = (await cur2.fetchone())[0]
            return jsonify({"top": [{"name": t[1] or t[0] or "User", "total": t[2]} for t in top], "total_withdrawn": tw})
    return asyncio.new_event_loop().run_until_complete(get())

@flask_app.route('/api/shop')
def api_shop():
    nfts = [
        {"name": "Scared Cat", "emoji": "🐱", "value": 16000},
        {"name": "Mightly Arms", "emoji": "💪", "value": 10000},
        {"name": "Loot Bag", "emoji": "🎒", "value": 9000},
        {"name": "Artisan Bricks", "emoji": "🧱", "value": 5000},
        {"name": "Diamond Hands", "emoji": "💎", "value": 7500},
        {"name": "Crypto Punk", "emoji": "🤖", "value": 12000},
        {"name": "Golden Ape", "emoji": "🦍", "value": 8500},
        {"name": "Moon Rocket", "emoji": "🌙", "value": 6800},
        {"name": "Bitcoin Lord", "emoji": "₿", "value": 14000},
        {"name": "Snoop Dogg", "emoji": "🐕", "value": 1300},
        {"name": "Torch", "emoji": "🔥", "value": 450},
        {"name": "Ice Cream", "emoji": "🍦", "value": 420},
        {"name": "Ghost Spirit", "emoji": "👻", "value": 600},
        {"name": "Phoenix", "emoji": "🦅", "value": 800},
        {"name": "Dragon Egg", "emoji": "🥚", "value": 1100},
        {"name": "Magic Lamp", "emoji": "🪔", "value": 950},
        {"name": "Pirate Ship", "emoji": "🏴‍☠️", "value": 700},
        {"name": "Ring", "emoji": "💍", "value": 100},
        {"name": "Cake", "emoji": "🎂", "value": 50},
        {"name": "Rose", "emoji": "🌹", "value": 25},
        {"name": "Teddy Bear", "emoji": "🧸", "value": 15},
        {"name": "Magic Wand", "emoji": "🪄", "value": 200},
        {"name": "Crystal Ball", "emoji": "🔮", "value": 180},
        {"name": "Golden Key", "emoji": "🗝️", "value": 150},
        {"name": "Crown", "emoji": "👑", "value": 300},
        {"name": "Ruby", "emoji": "💎", "value": 250},
        {"name": "Amulet", "emoji": "📿", "value": 120},
        {"name": "Sword", "emoji": "⚔️", "value": 80},
        {"name": "Shield", "emoji": "🛡️", "value": 90},
        {"name": "Potion", "emoji": "🧪", "value": 60},
    ]
    return jsonify(nfts)

@flask_app.route('/api/buy_nft', methods=['POST'])
def api_buy_nft():
    data = request.json
    uid, name, value, emoji = data['uid'], data['name'], data['value'], data['emoji']
    async def p():
        async with aiosqlite.connect(DB_NAME) as db:
            cur = await db.execute("SELECT balance FROM users WHERE user_id=?", (uid,))
            r = await cur.fetchone()
            if not r or r[0] < value:
                return jsonify({"error": "no_balance"})
            await db.execute("UPDATE users SET balance=balance-? WHERE user_id=?", (value, uid))
            await db.execute("INSERT INTO inventory (user_id, item_name, item_value, item_emoji) VALUES (?,?,?,?)", (uid, name, value, emoji))
            await db.commit()
            cur = await db.execute("SELECT balance FROM users WHERE user_id=?", (uid,))
            return jsonify({"success": True, "balance": (await cur.fetchone())[0]})
    return asyncio.new_event_loop().run_until_complete(p())

@flask_app.route('/api/sell_nft', methods=['POST'])
def api_sell_nft():
    data = request.json
    uid, name, value = data['uid'], data['name'], data['value']
    async def p():
        async with aiosqlite.connect(DB_NAME) as db:
            cur = await db.execute("SELECT id FROM inventory WHERE user_id=? AND item_name=? LIMIT 1", (uid, name))
            item = await cur.fetchone()
            if not item:
                return jsonify({"error": "not_found"})
            await db.execute("DELETE FROM inventory WHERE id=?", (item[0],))
            await db.execute("UPDATE users SET balance=balance+? WHERE user_id=?", (value, uid))
            await db.commit()
            cur = await db.execute("SELECT balance FROM users WHERE user_id=?", (uid,))
            return jsonify({"success": True, "balance": (await cur.fetchone())[0]})
    return asyncio.new_event_loop().run_until_complete(p())

@flask_app.route('/api/upgrade', methods=['POST'])
def api_upgrade():
    data = request.json
    uid, fr, to = data['uid'], data['from'], data['to']
    async def p():
        ratio = fr['value'] / to['value']
        chance = max(1, min(50, int(ratio * 100)))
        async with aiosqlite.connect(DB_NAME) as db:
            cur = await db.execute("SELECT id FROM inventory WHERE user_id=? AND item_name=? LIMIT 1", (uid, fr['name']))
            item = await cur.fetchone()
            if not item:
                return jsonify({"error": "not_found"})
            won = random.randint(1, 100) <= chance
            if won:
                await db.execute("DELETE FROM inventory WHERE id=?", (item[0],))
                await db.execute("INSERT INTO inventory (user_id, item_name, item_value, item_emoji) VALUES (?,?,?,?)", (uid, to['name'], to['value'], to['emoji']))
                await db.commit()
                return jsonify({"success": True, "won": True, "chance": chance})
            else:
                await db.execute("DELETE FROM inventory WHERE id=?", (item[0],))
                await db.commit()
                return jsonify({"success": True, "won": False, "chance": chance})
    return asyncio.new_event_loop().run_until_complete(p())

def run_flask():
    port = int(os.environ.get('PORT', 8000))
    flask_app.run(host='0.0.0.0', port=port)

# ==================== БАЗА ДАННЫХ ====================
async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute('''CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, username TEXT, first_name TEXT, balance INTEGER DEFAULT 0, ref_code TEXT UNIQUE, invited_by INTEGER, ref_earned INTEGER DEFAULT 0, last_daily TEXT, last_allornothing TEXT)''')
        await db.execute('''CREATE TABLE IF NOT EXISTS deposits (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, amount_ton REAL, amount_stars INTEGER, expected_amount REAL, comment TEXT UNIQUE, status TEXT DEFAULT 'waiting', tx_hash TEXT, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)''')
        await db.execute('''CREATE TABLE IF NOT EXISTS withdrawals (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, amount_stars INTEGER, status TEXT DEFAULT 'pending', timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)''')
        await db.execute('''CREATE TABLE IF NOT EXISTS inventory (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, item_name TEXT, item_value INTEGER, item_emoji TEXT, obtained_at DATETIME DEFAULT CURRENT_TIMESTAMP)''')
        await db.execute('''CREATE TABLE IF NOT EXISTS cases_opened (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, case_name TEXT, reward TEXT, reward_value INTEGER, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)''')
        await db.execute('''CREATE TABLE IF NOT EXISTS processed_tx (tx_hash TEXT PRIMARY KEY)''')
        await db.commit()

# ==================== TON ====================
async def check_ton():
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(f"https://toncenter.com/api/v2/getTransactions?address={TON_WALLET}&limit=10", timeout=15) as r:
                data = await r.json()
                return data.get("result", []) if data.get("ok") else []
    except:
        return []

async def process_ton(bot: Bot):
    while True:
        try:
            for tx in await check_ton():
                tx_hash = tx.get("transaction_id", {}).get("hash", "")
                if not tx_hash: continue
                async with aiosqlite.connect(DB_NAME) as db:
                    if await (await db.execute("SELECT tx_hash FROM processed_tx WHERE tx_hash=?", (tx_hash,))).fetchone(): continue
                    msg = tx.get("in_msg", {})
                    if not msg or msg.get("source") == "": continue
                    val = int(msg.get("value", 0)) / 1_000_000_000
                    if val <= 0.001: continue
                    cur = await db.execute("SELECT id, user_id, amount_stars FROM deposits WHERE status='waiting' AND expected_amount<=? ORDER BY expected_amount DESC LIMIT 1", (val,))
                    dep = await cur.fetchone()
                    if dep:
                        await db.execute("UPDATE deposits SET status='paid', tx_hash=? WHERE id=?", (tx_hash, dep[0]))
                        await db.execute("UPDATE users SET balance=balance+? WHERE user_id=?", (dep[2], dep[1]))
                        await db.execute("INSERT OR IGNORE INTO processed_tx VALUES(?)", (tx_hash,))
                        await db.commit()
                        try: await bot.send_message(dep[1], f"✅ +{dep[2]} ⭐ зачислено!", parse_mode=ParseMode.HTML)
                        except: pass
        except: pass
        await asyncio.sleep(30)

# ==================== ФУНКЦИИ ====================
async def generate_ref_code(uid):
    code = ''.join(random.choices(string.ascii_letters + string.digits, k=8))
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE users SET ref_code=? WHERE user_id=?", (code, uid))
        await db.commit()
    return code

async def check_sub(bot, uid):
    try:
        m = await bot.get_chat_member(f"@{CHANNEL_TAG}", uid)
        return m.status not in ['left', 'kicked']
    except:
        return False

def main_kb():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="🎮 Играть", web_app=WebAppInfo(url=WEBAPP_URL))],
        [KeyboardButton(text="📢 Канал", url="https://t.me/arcade_ludo")]
    ], resize_keyboard=True)

# ==================== ХЕНДЛЕРЫ ====================
async def start_cmd(message: types.Message, bot: Bot):
    uid = message.from_user.id
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("INSERT OR IGNORE INTO users (user_id, username, first_name) VALUES (?,?,?)", (uid, message.from_user.username, message.from_user.first_name))
        cur = await db.execute("SELECT ref_code FROM users WHERE user_id=?", (uid,))
        r = await cur.fetchone()
        if not r or not r[0]:
            await generate_ref_code(uid)
        args = message.text.split()
        if len(args) > 1 and args[1].startswith("ref_"):
            ref_code = args[1].replace("ref_", "")
            cur2 = await db.execute("SELECT user_id FROM users WHERE ref_code=?", (ref_code,))
            ref_user = await cur2.fetchone()
            if ref_user and ref_user[0] != uid:
                if not await (await db.execute("SELECT id FROM users WHERE user_id=? AND invited_by IS NOT NULL", (uid,))).fetchone():
                    await db.execute("UPDATE users SET invited_by=? WHERE user_id=?", (ref_user[0], uid))
        await db.commit()
    await bot.set_chat_menu_button(chat_id=uid, menu_button=MenuButtonWebApp(text="🎮 Играть", web_app=WebAppInfo(url=WEBAPP_URL)))
    await message.answer("🎰 <b>Это Arcade.</b>\n\n🎁 <b>Кейсы.</b> Жмёшь — выпадает.\n⚡️ <b>Апгрейды.</b> Меняй мелочь на редкость.\n🚀 <b>Краш.</b> Соскочи вовремя.\n👑 <b>Казна.</b> Банк забирает топ-1.\n\n👇 Залетай", reply_markup=main_kb(), parse_mode=ParseMode.HTML)

async def webapp_handler(message: types.Message, bot: Bot):
    uid = message.from_user.id
    data = json.loads(message.web_app_data.data)
    act = data.get("action")
    
    if act == "pay":
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
            if stars < 100: await message.answer("withdraw:min"); return
            if stars > bal: await message.answer("withdraw:no_balance"); return
            await db.execute("UPDATE users SET balance=balance-? WHERE user_id=?", (stars, uid))
            await db.execute("INSERT INTO withdrawals (user_id, amount_stars) VALUES (?,?)", (uid, stars))
            await db.commit()
        user_mention = f"@{message.from_user.username}" if message.from_user.username else f"ID:{uid}"
        await bot.send_message(OWNER_ID, f"📤 Вывод\n👤 {user_mention}\n🆔 {uid}\n💎 {stars} ⭐\n📅 {datetime.now().strftime('%d.%m.%Y %H:%M')}")
        await message.answer("withdraw:ok")
    
    elif act == "open_case":
        case = data.get("case", "")
        if case == "daily":
            if not await check_sub(bot, uid): await message.answer("case:error,not_subscribed"); return
            async with aiosqlite.connect(DB_NAME) as db:
                cur = await db.execute("SELECT last_daily FROM users WHERE user_id=?", (uid,))
                r = await cur.fetchone()
                if r and r[0] == datetime.now().strftime("%Y-%m-%d"): await message.answer("case:error,already_opened"); return
                await db.execute("UPDATE users SET last_daily=? WHERE user_id=?", (datetime.now().strftime("%Y-%m-%d"), uid))
                await db.commit()
        
        if case == "allornothing":
            async with aiosqlite.connect(DB_NAME) as db:
                cur = await db.execute("SELECT last_allornothing FROM users WHERE user_id=?", (uid,))
                r = await cur.fetchone()
                if r and r[0] == datetime.now().strftime("%Y-%m-%d"): await message.answer("case:error,already_opened"); return
                await db.execute("UPDATE users SET last_allornothing=? WHERE user_id=?", (datetime.now().strftime("%Y-%m-%d"), uid))
                await db.commit()
        
        cases = {
            "daily": {"price": 0, "items": [("Scared Cat", 16000, 1), ("Mightly Arms", 10000, 1), ("Loot Bag", 9000, 2), ("Artisan Bricks", 5000, 3), ("5 ⭐", 5, 30), ("1 ⭐", 1, 30), ("3 ⭐", 3, 33)]},
            "valera": {"price": 3, "items": [("3 ⭐", 3, 50), ("5 ⭐", 5, 40), ("10 ⭐", 10, 10)]},
            "bumzhikha": {"price": 5, "items": [("5 ⭐", 5, 50), ("15 ⭐", 15, 30), ("30 ⭐", 30, 16), ("50 ⭐", 50, 14)]},
            "svidanie": {"price": 50, "items": [("5 ⭐", 5, 1), ("4 ⭐", 4, 2), ("3 ⭐", 3, 3), ("7 ⭐", 7, 4), ("50 ⭐", 50, 40), ("75 ⭐", 75, 25), ("100 ⭐", 100, 20), ("200 ⭐", 200, 5)]},
            "otel": {"price": 75, "items": [("5 ⭐", 5, 1), ("4 ⭐", 4, 2), ("3 ⭐", 3, 3), ("1 ⭐", 1, 4), ("80 ⭐", 80, 50), ("150 ⭐", 150, 25), ("200 ⭐", 200, 15)]},
            "forever": {"price": 300, "items": [("1 ⭐", 1, 1), ("10 ⭐", 10, 5), ("4 ⭐", 4, 4), ("350 ⭐", 350, 50), ("400 ⭐", 400, 20), ("500 ⭐", 500, 15), ("1000 ⭐", 1000, 5)]},
            "allornothing": {"price": 2000, "items": [("1000 ⭐", 1000, 50), ("5000 ⭐", 5000, 50)]}
        }
        
        c = cases.get(case)
        if not c: await message.answer("case:error,not_found"); return
        
        async with aiosqlite.connect(DB_NAME) as db:
            cur = await db.execute("SELECT balance FROM users WHERE user_id=?", (uid,))
            bal = (await cur.fetchone())[0]
            
            if c["price"] > 0 and bal < c["price"]:
                await message.answer("case:error,no_balance")
                return
            
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
                    nft_emojis = {"Scared Cat": "🐱", "Mightly Arms": "💪", "Loot Bag": "🎒", "Artisan Bricks": "🧱"}
                    if name in nft_emojis:
                        await db.execute("INSERT INTO inventory (user_id, item_name, item_value, item_emoji) VALUES (?,?,?,?)", (uid, name, val, nft_emojis[name]))
                    await db.commit()
                    new_bal = (await (await db.execute("SELECT balance FROM users WHERE user_id=?", (uid,))).fetchone())[0]
                    await message.answer(f"case:success,{name},{val},{new_bal}")
                    return
        await message.answer("case:error,unknown")

# ==================== ЗАПУСК ====================
async def main():
    logging.basicConfig(level=logging.INFO)
    await init_db()
    print("✅ База готова")
    Thread(target=run_flask, daemon=True).start()
    print("🌐 Flask запущен")
    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    dp.message.register(start_cmd, Command("start"))
    dp.message.register(webapp_handler, F.web_app_data)

import asyncio
import json
import os
import random
from datetime import datetime
from threading import Thread
from flask import Flask, send_from_directory, request, jsonify
import aiosqlite
import aiohttp

BOT_TOKEN = "8941049801:AAFQHjVBXnx_58ndwkskRTwEam0g5ZaZcb0"
BOT_USERNAME = "arcadecasinobot"
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
            cur = await db.execute("SELECT item_name, item_value, item_emoji FROM inventory WHERE user_id=?", (uid,))
            items = await cur.fetchall()
            return jsonify([{"name": i[0], "value": i[1], "emoji": i[2]} for i in items])
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
        {"name":"Scared Cat","emoji":"🐱","value":16000},
        {"name":"Mightly Arms","emoji":"💪","value":10000},
        {"name":"Loot Bag","emoji":"🎒","value":9000},
        {"name":"Artisan Bricks","emoji":"🧱","value":5000},
        {"name":"Diamond Hands","emoji":"💎","value":7500},
        {"name":"Crypto Punk","emoji":"🤖","value":12000},
        {"name":"Golden Ape","emoji":"🦍","value":8500},
        {"name":"Moon Rocket","emoji":"🌙","value":6800},
        {"name":"Bitcoin Lord","emoji":"₿","value":14000},
        {"name":"Snoop Dogg","emoji":"🐕","value":1300},
        {"name":"Torch","emoji":"🔥","value":450},
        {"name":"Ice Cream","emoji":"🍦","value":420},
        {"name":"Ghost Spirit","emoji":"👻","value":600},
        {"name":"Phoenix","emoji":"🦅","value":800},
        {"name":"Dragon Egg","emoji":"🥚","value":1100},
        {"name":"Magic Lamp","emoji":"🪔","value":950},
        {"name":"Pirate Ship","emoji":"🏴‍☠️","value":700},
        {"name":"Ring","emoji":"💍","value":100},
        {"name":"Cake","emoji":"🎂","value":50},
        {"name":"Rose","emoji":"🌹","value":25},
        {"name":"Teddy Bear","emoji":"🧸","value":15},
        {"name":"Magic Wand","emoji":"🪄","value":200},
        {"name":"Crystal Ball","emoji":"🔮","value":180},
        {"name":"Golden Key","emoji":"🗝️","value":150},
        {"name":"Crown","emoji":"👑","value":300},
        {"name":"Ruby","emoji":"💎","value":250},
        {"name":"Amulet","emoji":"📿","value":120},
        {"name":"Sword","emoji":"⚔️","value":80},
        {"name":"Shield","emoji":"🛡️","value":90},
        {"name":"Potion","emoji":"🧪","value":60},
    ]
    return jsonify(nfts)

@flask_app.route('/api/buy_nft', methods=['POST'])
def api_buy_nft():
    d = request.json
    uid, name, value, emoji = d['uid'], d['name'], d['value'], d['emoji']
    async def p():
        async with aiosqlite.connect(DB_NAME) as db:
            cur = await db.execute("SELECT balance FROM users WHERE user_id=?", (uid,))
            r = await cur.fetchone()
            if not r or r[0] < value: return jsonify({"error":"no_balance"})
            await db.execute("UPDATE users SET balance=balance-? WHERE user_id=?", (value, uid))
            await db.execute("INSERT INTO inventory (user_id,item_name,item_value,item_emoji) VALUES (?,?,?,?)", (uid, name, value, emoji))
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
                await db.execute("INSERT INTO inventory (user_id,item_name,item_value,item_emoji) VALUES (?,?,?,?)", (uid, to['name'], to['value'], to['emoji']))
                await db.commit()
                return jsonify({"success":True,"won":True,"chance":chance})
            else:
                await db.execute("DELETE FROM inventory WHERE id=?", (item[0],))
                await db.commit()
                return jsonify({"success":True,"won":False,"chance":chance})
    return asyncio.new_event_loop().run_until_complete(p())

@flask_app.route('/webhook', methods=['POST'])
def webhook():
    data = request.json
    loop = asyncio.new_event_loop()
    loop.run_until_complete(process_update(data))
    return "OK"

async def process_update(data):
    try:
        async with aiohttp.ClientSession() as s:
            msg = data.get('message', {})
            text = msg.get('text', '')
            chat_id = msg.get('chat', {}).get('id')
            uid = msg.get('from', {}).get('id')
            
            if text == '/start':
                uname = msg.get('from', {}).get('username', '')
                fn = msg.get('from', {}).get('first_name', '')
                
                async with aiosqlite.connect(DB_NAME) as db:
                    await db.execute("INSERT OR IGNORE INTO users (user_id, username, first_name) VALUES (?,?,?)", (uid, uname, fn))
                    cur = await db.execute("SELECT ref_code FROM users WHERE user_id=?", (uid,))
                    r = await cur.fetchone()
                    if not r or not r[0]:
                        code = ''.join(random.choices('abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789', k=8))
                        await db.execute("UPDATE users SET ref_code=? WHERE user_id=?", (code, uid))
                    
                    args = text.split()
                    if len(args) > 1 and args[1].startswith("ref_"):
                        ref_code = args[1].replace("ref_", "")
                        cur2 = await db.execute("SELECT user_id FROM users WHERE ref_code=?", (ref_code,))
                        ref_user = await cur2.fetchone()
                        if ref_user and ref_user[0] != uid:
                            cur3 = await db.execute("SELECT id FROM users WHERE user_id=? AND invited_by IS NOT NULL", (uid,))
                            if not await cur3.fetchone():
                                await db.execute("UPDATE users SET invited_by=? WHERE user_id=?", (ref_user[0], uid))
                    await db.commit()
                
                keyboard = {
                    "keyboard": [[{"text": "🎮 Играть", "web_app": {"url": WEBAPP_URL}}], [{"text": "📢 Канал", "url": "https://t.me/arcade_ludo"}]],
                    "resize_keyboard": True
                }
                
                await s.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", json={
                    "chat_id": chat_id,
                    "text": "🎰 <b>Это Arcade.</b>\n\n🎁 <b>Кейсы.</b> Жмёшь — выпадает.\n⚡️ <b>Апгрейды.</b>\n🚀 <b>Краш.</b>\n👑 <b>Казна.</b>\n\n👇 Залетай",
                    "parse_mode": "HTML",
                    "reply_markup": keyboard
                })
            
            elif msg.get('web_app_data'):
                d = json.loads(msg['web_app_data']['data'])
                act = d.get('action')
                
                if act == "get_ref":
                    async with aiosqlite.connect(DB_NAME) as db:
                        cur = await db.execute("SELECT ref_code FROM users WHERE user_id=?", (uid,))
                        r = await cur.fetchone()
                        code = r[0] if r and r[0] else ''.join(random.choices('abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789', k=8))
                        if not r or not r[0]:
                            await db.execute("UPDATE users SET ref_code=? WHERE user_id=?", (code, uid))
                            await db.commit()
                    await s.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", json={
                        "chat_id": chat_id,
                        "text": f"ref:{code}"
                    })
                
                elif act == "pay":
                    stars = d.get("amount", 0)
                    ton = round(stars * STAR_PRICE_TON, 4)
                    comm = f"BUY_{uid}_{int(datetime.now().timestamp())}"
                    async with aiosqlite.connect(DB_NAME) as db:
                        await db.execute("INSERT INTO deposits (user_id, amount_ton, amount_stars, expected_amount, comment) VALUES (?,?,?,?,?)", (uid, ton, stars, ton, comm))
                        await db.commit()
                    link = f"ton://transfer/{TON_WALLET}?amount={ton}&text={comm}"
                    await s.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", json={
                        "chat_id": chat_id,
                        "text": f"💎 <b>{stars} ⭐</b>\n💳 <b>{ton} TON</b>\n\n📤 <code>{TON_WALLET}</code>\n💬 <code>{comm}</code>\n\n🔗 <a href='{link}'>Оплатить</a>",
                        "parse_mode": "HTML"
                    })
                
                elif act == "withdraw":
                    stars = d.get("amount", 0)
                    async with aiosqlite.connect(DB_NAME) as db:
                        cur = await db.execute("SELECT balance FROM users WHERE user_id=?", (uid,))
                        bal = (await cur.fetchone())[0]
                        if stars < 100:
                            await s.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", json={"chat_id": chat_id, "text": "withdraw:min"})
                            return
                        if stars > bal:
                            await s.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", json={"chat_id": chat_id, "text": "withdraw:no_balance"})
                            return
                        await db.execute("UPDATE users SET balance=balance-? WHERE user_id=?", (stars, uid))
                        await db.execute("INSERT INTO withdrawals (user_id, amount_stars) VALUES (?,?)", (uid, stars))
                        await db.commit()
                    user_mention = msg.get('from', {}).get('username', f"ID:{uid}")
                    await s.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", json={
                        "chat_id": OWNER_ID,
                        "text": f"📤 Вывод\n👤 @{user_mention}\n🆔 {uid}\n💎 {stars} ⭐\n📅 {datetime.now().strftime('%d.%m.%Y %H:%M')}"
                    })
                    await s.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", json={"chat_id": chat_id, "text": "withdraw:ok"})
                
                elif act == "open_case":
                    case = d.get("case", "")
                    
                    if case == "daily":
                        try:
                            async with s.get(f"https://api.telegram.org/bot{BOT_TOKEN}/getChatMember?chat_id=@arcade_ludo&user_id={uid}") as r:
                                member = await r.json()
                                if not member.get('ok') or member['result']['status'] in ['left', 'kicked']:
                                    await s.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", json={"chat_id": chat_id, "text": "case:error,not_subscribed"})
                                    return
                        except:
                            await s.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", json={"chat_id": chat_id, "text": "case:error,not_subscribed"})
                            return
                        
                        async with aiosqlite.connect(DB_NAME) as db:
                            cur = await db.execute("SELECT last_daily FROM users WHERE user_id=?", (uid,))
                            r = await cur.fetchone()
                            if r and r[0] == datetime.now().strftime("%Y-%m-%d"):
                                await s.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", json={"chat_id": chat_id, "text": "case:error,already_opened"})
                                return
                            await db.execute("UPDATE users SET last_daily=? WHERE user_id=?", (datetime.now().strftime("%Y-%m-%d"), uid))
                            await db.commit()
                    
                    if case == "allornothing":
                        async with aiosqlite.connect(DB_NAME) as db:
                            cur = await db.execute("SELECT last_allornothing FROM users WHERE user_id=?", (uid,))
                            r = await cur.fetchone()
                            if r and r[0] == datetime.now().strftime("%Y-%m-%d"):
                                await s.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", json={"chat_id": chat_id, "text": "case:error,already_opened"})
                                return
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
                    if not c:
                        await s.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", json={"chat_id": chat_id, "text": "case:error,not_found"})
                        return
                    
                    async with aiosqlite.connect(DB_NAME) as db:
                        cur = await db.execute("SELECT balance FROM users WHERE user_id=?", (uid,))
                        bal = (await cur.fetchone())[0]
                        
                        if c["price"] > 0 and bal < c["price"]:
                            await s.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", json={"chat_id": chat_id, "text": "case:error,no_balance"})
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
                                nft_emojis = {"Scared Cat":"🐱","Mightly Arms":"💪","Loot Bag":"🎒","Artisan Bricks":"🧱"}
                                if name in nft_emojis:
                                    await db.execute("INSERT INTO inventory (user_id, item_name, item_value, item_emoji) VALUES (?,?,?,?)", (uid, name, val, nft_emojis[name]))
                                await db.commit()
                                new_bal = (await (await db.execute("SELECT balance FROM users WHERE user_id=?", (uid,))).fetchone())[0]
                                await s.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", json={"chat_id": chat_id, "text": f"case:success,{name},{val},{new_bal}"})
                                return
                    await s.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", json={"chat_id": chat_id, "text": "case:error,unknown"})
    except Exception as e:
        print(f"Error in webhook: {e}")

async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute('''CREATE TABLE IF NOT EXISTS inventory ...

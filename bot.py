import asyncio
import logging
import json
from datetime import datetime
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, WebAppInfo, MenuButtonWebApp
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
import aiosqlite
import aiohttp
import random

# ==================== НАСТРОЙКИ ====================
BOT_TOKEN = "8965870385:AAHZ_zppdEcBPIVl2DIdgNwds4z-BqYfv5A"
BOT_USERNAME = "arcadecasinobot"
OWNER_ID = 8131755675
TON_WALLET = "UQAhap1bl6g49QgjYoK2H43k0GeB5xtd9JSCJzDYLy6QgJv4"
STAR_PRICE_TON = 0.006
WEBAPP_URL = "arcade-production-4354.up.railway.app"
DB_NAME = "arcade.db"

# ==================== БАЗА ДАННЫХ ====================
async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute('''CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY, username TEXT, first_name TEXT,
            balance INTEGER DEFAULT 0, last_daily TEXT)''')
        await db.execute('''CREATE TABLE IF NOT EXISTS deposits (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, amount_ton REAL,
            amount_stars INTEGER, expected_amount REAL, comment TEXT UNIQUE,
            status TEXT DEFAULT 'waiting', tx_hash TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)''')
        await db.execute('''CREATE TABLE IF NOT EXISTS withdrawals (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER,
            amount_stars INTEGER, amount_ton REAL, card_number TEXT,
            status TEXT DEFAULT 'pending', timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)''')
        await db.execute('''CREATE TABLE IF NOT EXISTS cases_opened (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER,
            case_name TEXT, reward TEXT, reward_value INTEGER,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)''')
        await db.execute('''CREATE TABLE IF NOT EXISTS processed_tx (
            tx_hash TEXT PRIMARY KEY)''')
        await db.commit()

# ==================== TON ПРОВЕРКА ====================
async def check_ton():
    url = f"https://toncenter.com/api/v2/getTransactions?address={TON_WALLET}&limit=10"
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url, timeout=15) as r:
                data = await r.json()
                return data.get("result", []) if data.get("ok") else []
    except:
        return []

async def process_ton(bot: Bot):
    while True:
        try:
            txs = await check_ton()
            for tx in txs:
                tx_hash = tx.get("transaction_id", {}).get("hash", "")
                if not tx_hash:
                    continue
                    
                async with aiosqlite.connect(DB_NAME) as db:
                    cur = await db.execute("SELECT tx_hash FROM processed_tx WHERE tx_hash=?", (tx_hash,))
                    if await cur.fetchone():
                        continue
                    
                    msg = tx.get("in_msg", {})
                    if not msg or msg.get("source") == "":
                        continue
                    
                    val = int(msg.get("value", 0)) / 1_000_000_000
                    comm = msg.get("message", "")
                    
                    if val <= 0.001:
                        continue
                    
                    cur = await db.execute(
                        "SELECT id, user_id, amount_stars FROM deposits WHERE status='waiting' AND expected_amount<=? ORDER BY expected_amount DESC LIMIT 1",
                        (val,)
                    )
                    dep = await cur.fetchone()
                    
                    if dep:
                        await db.execute("UPDATE deposits SET status='paid', tx_hash=? WHERE id=?", (tx_hash, dep[0]))
                        await db.execute("UPDATE users SET balance=balance+? WHERE user_id=?", (dep[2], dep[1]))
                        await db.execute("INSERT OR IGNORE INTO processed_tx VALUES(?)", (tx_hash,))
                        await db.commit()
                        
                        try:
                            await bot.send_message(
                                dep[1],
                                f"✅ <b>+{dep[2]} ⭐ зачислено!</b>\n💎 Автоматически\n🔗 <a href='https://tonviewer.com/transaction/{tx_hash}'>Транзакция</a>",
                                parse_mode=ParseMode.HTML,
                                disable_web_page_preview=True
                            )
                        except:
                            pass
        except Exception as e:
            print(f"TON error: {e}")
        await asyncio.sleep(30)

# ==================== ФУНКЦИИ ====================
async def get_balance(uid: int) -> int:
    async with aiosqlite.connect(DB_NAME) as db:
        cur = await db.execute("SELECT balance FROM users WHERE user_id=?", (uid,))
        r = await cur.fetchone()
        return r[0] if r else 0

async def add_balance(uid: int, amt: int):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE users SET balance=balance+? WHERE user_id=?", (amt, uid))
        await db.commit()

async def open_case(uid: int, case: str) -> str:
    cases = {
        "daily": {"price": 0, "items": [
            ("⭐ 15", 15, 60), ("⭐ 30", 30, 20), ("⭐ 50", 50, 18), ("⭐ 100", 100, 2)
        ]},
        "bum": {"price": 5, "items": [
            ("⭐ 1", 1, 10), ("⭐ 5", 5, 60), ("⭐ 20", 20, 15), ("⭐ 50", 50, 10), ("⭐ 150", 150, 5)
        ]},
        "medium": {"price": 50, "items": [
            ("🌹 Роза", 25, 25), ("🎂 Торт", 50, 30), ("💍 Кольцо", 100, 44), ("🍦 Нфт Мороженое", 500, 1)
        ]},
        "major": {"price": 350, "items": [
            ("🐕 Нфт Снуп дог", 1300, 10), ("🔥 Нфт Факел", 450, 40), ("🧸 Мишка", 15, 30), ("🍦 Нфт Мороженое", 420, 10)
        ]},
        "allornothing": {"price": 500, "items": [
            ("🧸 Мишка", 15, 30), ("🌹 Роза", 25, 40), ("💎 5000 ⭐", 5000, 25), ("👑 Премиум 3 мес", 900, 5)
        ]}
    }
    
    c = cases.get(case)
    if not c:
        return "❌ Кейс не найден"
    
    if case == "daily":
        async with aiosqlite.connect(DB_NAME) as db:
            cur = await db.execute("SELECT last_daily FROM users WHERE user_id=?", (uid,))
            r = await cur.fetchone()
            if r and r[0] == datetime.now().strftime("%Y-%m-%d"):
                return "❌ Уже открывали сегодня!"
            await db.execute("UPDATE users SET last_daily=? WHERE user_id=?", (datetime.now().strftime("%Y-%m-%d"), uid))
            await db.commit()
    
    if c["price"] > 0:
        bal = await get_balance(uid)
        if bal < c["price"]:
            return f"❌ Недостаточно! Нужно {c['price']} ⭐, у вас {bal} ⭐"
        await add_balance(uid, -c["price"])
    
    total = sum(it[2] for it in c["items"])
    rnd = random.randint(1, total)
    cur = 0
    for name, val, ch in c["items"]:
        cur += ch
        if rnd <= cur:
            await add_balance(uid, val)
            async with aiosqlite.connect(DB_NAME) as db:
                await db.execute("INSERT INTO cases_opened (user_id, case_name, reward, reward_value) VALUES (?,?,?,?)",
                                (uid, case, name, val))
                await db.commit()
            new_bal = await get_balance(uid)
            return f"🎉 <b>{name}</b>\n💰 +{val} ⭐\n💎 Баланс: {new_bal} ⭐"
    return "❌ Ошибка"

# ==================== КЛАВИАТУРА ====================
def main_kb():
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="🎮 Играть", web_app=WebAppInfo(url=WEBAPP_URL))]],
        resize_keyboard=True
    )

# ==================== ХЕНДЛЕРЫ ====================
async def start_cmd(message: types.Message, bot: Bot):
    uid = message.from_user.id
    uname = message.from_user.username
    fn = message.from_user.first_name
    
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("INSERT OR IGNORE INTO users (user_id, username, first_name) VALUES (?,?,?)", (uid, uname, fn))
        await db.commit()
    
    await bot.set_chat_menu_button(
        chat_id=uid,
        menu_button=MenuButtonWebApp(text="🎮 Играть", web_app=WebAppInfo(url=WEBAPP_URL))
    )
    
    await message.answer(
        f"🎰 <b>Arcade Casino</b>\n\n"
        f"💫 Открывай кейсы и выигрывай!\n"
        f"💰 1 ⭐ = {STAR_PRICE_TON} TON\n"
        f"💎 Пополнение через TON\n\n"
        f"👇 Жми кнопку:",
        reply_markup=main_kb(),
        parse_mode=ParseMode.HTML
    )

async def webapp(message: types.Message, bot: Bot):
    uid = message.from_user.id
    data = json.loads(message.web_app_data.data)
    act = data.get("action")
    
    if act == "get_balance":
        bal = await get_balance(uid)
        await message.answer(f"💰 Баланс: {bal} ⭐")
    
    elif act == "pay":
        stars = data.get("amount", 0)
        ton = round(stars * STAR_PRICE_TON, 4)
        comm = f"BUY_{uid}_{int(datetime.now().timestamp())}"
        
        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute(
                "INSERT INTO deposits (user_id, amount_ton, amount_stars, expected_amount, comment) VALUES (?,?,?,?,?)",
                (uid, ton, stars, ton, comm)
            )
            await db.commit()
        
        link = f"ton://transfer/{TON_WALLET}?amount={ton}&text={comm}"
        
        await message.answer(
            f"💎 <b>Покупка {stars} ⭐</b>\n\n"
            f"💳 Сумма: <b>{ton} TON</b>\n"
            f"📊 Курс: 1 ⭐ = {STAR_PRICE_TON} TON\n\n"
            f"📤 Отправьте <b>ровно {ton} TON</b>:\n"
            f"<code>{TON_WALLET}</code>\n\n"
            f"💬 Комментарий: <code>{comm}</code>\n\n"
            f"🔗 <a href='{link}'>Быстрая оплата</a>\n\n"
            f"⚡ Звёзды зачислятся автоматически!",
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True
        )
    
    elif act == "withdraw":
        stars = data.get("amount", 0)
        card = data.get("card", "")
        bal = await get_balance(uid)
        
        if stars < 100:
            await message.answer("❌ Минимум: 100 ⭐")
            return
        if stars > bal:
            await message.answer(f"❌ Баланс: {bal} ⭐")
            return
        
        ton = round(stars * STAR_PRICE_TON * 0.9, 4)
        
        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute("UPDATE users SET balance=balance-? WHERE user_id=?", (stars, uid))
            await db.execute(
                "INSERT INTO withdrawals (user_id, amount_stars, amount_ton, card_number) VALUES (?,?,?,?)",
                (uid, stars, ton, card)
            )
            await db.commit()
        
        await bot.send_message(
            OWNER_ID,
            f"📤 <b>Вывод</b>\n👤 ID: {uid}\n💎 {stars} ⭐\n💎 {ton} TON\n💳 {card}",
            parse_mode=ParseMode.HTML
        )
        await message.answer(f"✅ Заявка создана!\n💎 {ton} TON на карту")
    
    elif act == "open_case":
        case = data.get("case", "")
        result = await open_case(uid, case)
        await message.answer(result, parse_mode=ParseMode.HTML)

# ==================== ЗАПУСК ====================
async def main():
    logging.basicConfig(level=logging.INFO)
    await init_db()
    print("✅ База готова")
    
    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    
    dp.message.register(start_cmd, Command("start"))
    dp.message.register(webapp, F.web_app_data)
    
    asyncio.create_task(process_ton(bot))
    
    print("🤖 Бот запущен!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())

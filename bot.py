import os, asyncio, json, random, hashlib, hmac, time
from datetime import datetime, timedelta
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, types, F
from aiogram.types import WebAppInfo, InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile
from aiogram.filters import Command
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
import uvicorn
from supabase import create_client

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
OWNER_ID = int(os.getenv("OWNER_ID"))
ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID"))
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_ANON_KEY = os.getenv("SUPABASE_ANON_KEY")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
app = FastAPI()
sup = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)

# ---------- КАРТЫ ----------
MAPS = [
    {"name": "SANDSTONE", "emoji": "🏜️"},
    {"name": "RUST", "emoji": "🏗️"},
    {"name": "PROVINCE", "emoji": "🏘️"},
    {"name": "BREEZE", "emoji": "🏖️"},
    {"name": "HANAMI", "emoji": "🌸"},
    {"name": "ZONE 7", "emoji": "🌃"}
]

# ---------- ХЕЛПЕРЫ ----------
def get_user(tg_id):
    try:
        r = sup.table("users").select("*").eq("telegram_id", tg_id).execute()
        return r.data[0] if r.data else None
    except: return None

def create_user(tg_id, username, snick, sid, pwd):
    try:
        sup.table("users").insert({
            "telegram_id": tg_id, "username": username, "standoff_nick": snick,
            "standoff_id": sid, "password_hash": hashlib.sha256(pwd.encode()).hexdigest(),
            "elo": 1000, "matches_played": 0, "wins": 0, "is_calibration": True,
            "created_at": datetime.now().isoformat()
        }).execute()
        return True
    except: return False

def calc_elo(score_w, score_l, cal):
    d = abs(score_w - score_l)
    base = 15 + d * 2
    return min(base * (2 if cal else 1), 50)

def get_level(mmr):
    if mmr < 800: return 1
    elif mmr < 1000: return 2
    elif mmr < 1200: return 3
    elif mmr < 1400: return 4
    elif mmr < 1600: return 5
    elif mmr < 1800: return 6
    elif mmr < 2000: return 7
    elif mmr < 2200: return 8
    elif mmr < 2500: return 9
    else: return 10

def get_level_name(lvl):
    names = ["", "НОВИЧОК", "БРОНЗА", "СЕРЕБРО", "ЗОЛОТО", "ПЛАТИНА", "ДИАМАНТ", "МАСТЕР", "ГРАНДМАСТЕР", "ЛЕГЕНДА", "ПРО"]
    return names[lvl] if lvl <= 10 else "ПРО"

# ---------- ЛОББИ (ВРЕМЕННОЕ ХРАНИЛИЩЕ) ----------
lobbies = {}  # lobby_id: {players: {tg_id: {ready, username}}, platform, host_id, map, bans, ban_count, turn, captains, teams, status}
match_results = {}  # lobby_id: {winner, score_ct, score_t, screenshot}

# ---------- БОТ ----------
@dp.message(Command("start"))
async def start(msg: types.Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚀 ОТКРЫТЬ ПЛАТФОРМУ", web_app=WebAppInfo(url="https://YOUR_RENDER_URL"))]
    ])
    await msg.answer("👋 Добро пожаловать в Stranger Faceit!", reply_markup=kb)

@dp.message(Command("admin"))
async def admin_cmd(msg: types.Message):
    if msg.from_user.id != OWNER_ID:
        await msg.answer("⛔ Нет доступа")
        return
    await msg.answer("⚙️ Админ-панель\n/lobbies - активные лобби\n/stats - статистика")

@dp.message(Command("lobbies"))
async def admin_lobbies(msg: types.Message):
    if msg.from_user.id != OWNER_ID: return
    if not lobbies:
        await msg.answer("Нет активных лобби")
        return
    text = "📋 Активные лобби:\n"
    for lid, lb in lobbies.items():
        text += f"ID: {lid} | {lb['platform']} | {len(lb['players'])}/10 | статус: {lb.get('status', 'waiting')}\n"
    await msg.answer(text)

# ---------- ВЕБ-СЕРВЕР ----------
@app.get("/")
async def web_root():
    with open("web/index.html", "r", encoding="utf-8") as f:
        return HTMLResponse(f.read())

@app.post("/webhook")
async def webhook(req: Request):
    data = await req.json()
    tg_id = data.get("telegram_id")
    action = data.get("action")
    user = get_user(tg_id)
    if not user and action not in ["register", "login"]:
        return {"status": "error", "message": "Сначала зарегистрируйся"}
    
    # ---------- РЕГИСТРАЦИЯ ----------
    if action == "register":
        nick = data.get("nick")
        sid = data.get("standoff_id")
        pwd = data.get("password")
        if len(pwd) < 6:
            return {"status": "error", "message": "Пароль минимум 6 символов"}
        if create_user(tg_id, f"user_{tg_id}", nick, sid, pwd):
            return {"type": "auth", "success": True, "message": "Регистрация успешна!", "user": get_user(tg_id)}
        return {"status": "error", "message": "Ник или ID уже заняты"}
    
    # ---------- ВХОД ----------
    if action == "login":
        nick = data.get("nick")
        pwd = data.get("password")
        u = sup.table("users").select("*").eq("standoff_nick", nick).execute()
        if u.data and u.data[0]["password_hash"] == hashlib.sha256(pwd.encode()).hexdigest():
            return {"type": "auth", "success": True, "message": "Вход выполнен!", "user": u.data[0]}
        return {"status": "error", "message": "Неверный ник или пароль"}
    
    # ---------- ПОИСК МАТЧА ----------
    if action == "find_match":
        platform = data.get("platform")
        # Ищем свободное лобби или создаём новое
        lid = None
        for k, v in lobbies.items():
            if v["platform"] == platform and len(v["players"]) < 10 and v.get("status") == "waiting":
                lid = k
                break
        if lid is None:
            lid = len(lobbies) + 1
            lobbies[lid] = {
                "platform": platform,
                "players": {},
                "host_id": None,
                "map": None,
                "bans": [],
                "ban_count": 0,
                "turn": 0,
                "captains": [],
                "teams": {"ct": [], "t": []},
                "status": "waiting",
                "created_at": datetime.now()
            }
        lobbies[lid]["players"][tg_id] = {"ready": False, "username": user["username"]}
        return {"type": "lobby_update", "lobby_id": lid, "platform": platform, "count": len(lobbies[lid]["players"]), "players": [{"username": p["username"], "ready": p["ready"]} for p in lobbies[lid]["players"].values()]}
    
    # ---------- ГОТОВНОСТЬ ----------
    if action == "set_ready":
        for lid, lb in lobbies.items():
            if tg_id in lb["players"]:
                lb["players"][tg_id]["ready"] = True
                # Проверяем, все ли готовы
                if len(lb["players"]) == 10 and all(p["ready"] for p in lb["players"].values()):
                    # Выбираем капитанов
                    players = list(lb["players"].keys())
                    caps = random.sample(players, 2)
                    lb["captains"] = caps
                    lb["turn"] = 0
                    lb["status"] = "ban_pick"
                    lb["bans"] = []
                    lb["ban_count"] = 0
                    lb["map"] = None
                    # Разбиваем на команды КТ и Т
                    ct_players = random.sample(players, 5)
                    t_players = [p for p in players if p not in ct_players]
                    lb["teams"] = {"ct": ct_players, "t": t_players}
                    # Уведомляем всех в лобби
                    for pid in players:
                        try:
                            await bot.send_message(pid, f"🔔 МАТЧ НАЧАЛСЯ!\nКарта: БАН-ПИК\nТы в команде {'КТ' if pid in ct_players else 'Т'}")
                        except: pass
                    # Возвращаем обновление с бан-пиками
                    return {"type": "ban_update", "maps": MAPS, "bans": [], "ban_count": 0, "current_banner": lb["captains"][0], "finished": False}
                return {"type": "lobby_update", "lobby_id": lid, "platform": lb["platform"], "count": len(lb["players"]), "players": [{"username": p["username"], "ready": p["ready"]} for p in lb["players"].values()]}
        return {"status": "error", "message": "Ты не в лобби"}
    
    # ---------- БАН КАРТЫ ----------
    if action == "ban_map":
        map_name = data.get("map")
        for lid, lb in lobbies.items():
            if tg_id in lb["players"] and lb.get("status") == "ban_pick":
                # Проверяем, что ход капитана
                if lb["captains"][lb["ban_count"] % 2] != tg_id:
                    return {"status": "error", "message": "Сейчас не твой ход"}
                # Баним карту
                if map_name not in [m["name"] for m in MAPS]:
                    return {"status": "error", "message": "Неверная карта"}
                if map_name in lb["bans"]:
                    return {"status": "error", "message": "Карта уже забанена"}
                lb["bans"].append(map_name)
                lb["ban_count"] += 1
                # Проверяем, закончены ли бан-пики
                if lb["ban_count"] >= 6:
                    # Осталась одна карта
                    remaining = [m for m in MAPS if m["name"] not in lb["bans"]]
                    if remaining:
                        lb["map"] = remaining[0]["name"]
                    lb["status"] = "ready"
                    # Назначаем хоста (первый капитан)
                    lb["host_id"] = lb["captains"][0]
                    host_data = get_user(lb["host_id"])
                    host_id_in_game = host_data["standoff_id"] if host_data else "123456"
                    # Уведомляем всех
                    for pid in lb["players"]:
                        try:
                            team = "КТ" if pid in lb["teams"]["ct"] else "Т"
                            await bot.send_message(pid, f"✅ БАН-ПИКИ ЗАВЕРШЕНЫ!\nКарта: {lb['map']}\nТы в команде {team}\nID ХОСТА: {host_id_in_game}\nХост: @{lb['players'][lb['host_id']]['username']}")
                        except: pass
                    return {"type": "ban_update", "finished": True, "selected_map": lb["map"], "maps": MAPS, "bans": lb["bans"]}
                # Следующий ход
                next_cap = lb["captains"][lb["ban_count"] % 2]
                return {"type": "ban_update", "maps": MAPS, "bans": lb["bans"], "ban_count": lb["ban_count"], "current_banner": next_cap, "finished": False}
        return {"status": "error", "message": "Ошибка бан-пиков"}
    
    # ---------- ВЫХОД ИЗ ЛОББИ ----------
    if action == "leave_lobby":
        for lid, lb in lobbies.items():
            if tg_id in lb["players"]:
                del lb["players"][tg_id]
                if len(lb["players"]) == 0:
                    del lobbies[lid]
                return {"status": "ok", "message": "Ты вышел из лобби"}
        return {"status": "error", "message": "Ты не в лобби"}
    
    # ---------- ТОП ----------
    if action == "get_top":
        r = sup.table("users").select("*").order("elo", desc=True).limit(100).execute()
        data = [{"username": u["username"], "elo": u["elo"], "level": get_level(u["elo"])} for u in r.data]
        return {"type": "top_update", "data": data}
    
    # ---------- ИСТОРИЯ ----------
    if action == "get_history":
        r = sup.table("matches").select("*").eq("telegram_id", tg_id).order("created_at", desc=True).limit(20).execute()
        data = []
        for m in r.data:
            data.append({
                "map": m["map_name"],
                "win": m["winner"] == tg_id,
                "score": f"{m['ct_score']}:{m['t_score']}",
                "elo_change": m.get("elo_change", 0)
            })
        return {"type": "history_update", "data": data}
    
    # ---------- ПРОМОКОДЫ ----------
    if action == "activate_promo":
        code = data.get("code")
        # Проверяем промокод
        r = sup.table("promo_codes").select("*").eq("code", code).execute()
        if not r.data:
            return {"type": "promo_result", "success": False, "message": "Промокод не найден"}
        promo = r.data[0]
        if promo["expires_at"] and datetime.fromisoformat(promo["expires_at"]) < datetime.now():
            return {"type": "promo_result", "success": False, "message": "Промокод истёк"}
        # Проверяем, не активировал ли уже
        r2 = sup.table("redeemed_promo").select("*").eq("promo_id", promo["id"]).eq("telegram_id", tg_id).execute()
        if r2.data:
            return {"type": "promo_result", "success": False, "message": "Ты уже активировал этот промокод"}
        # Начисляем
        sup.table("redeemed_promo").insert({"promo_id": promo["id"], "telegram_id": tg_id}).execute()
        new_elo = user["elo"] + promo["reward_elo"]
        sup.table("users").update({"elo": new_elo}).eq("telegram_id", tg_id).execute()
        return {"type": "promo_result", "success": True, "message": f"+{promo['reward_elo']} Эло!", "code": code, "reward": promo["reward_elo"]}
    
    # ---------- ПОДДЕРЖКА ----------
    if action == "support":
        text = data.get("text")
        await bot.send_message(ADMIN_CHAT_ID, f"🆕 ЗАЯВКА В ПОДДЕРЖКУ\nОт: @{user['username']} (ID: {tg_id})\nТекст: {text}")
        return {"status": "ok", "message": "Отправлено!"}
    
    return {"status": "ok"}

# ---------- ЗАПУСК ----------
async def main():
    asyncio.create_task(dp.start_polling(bot))
    uvicorn.run(app, host="0.0.0.0", port=8080)

if __name__ == "__main__":
    asyncio.run(main())

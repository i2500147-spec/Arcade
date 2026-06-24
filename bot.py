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

CRYPTOBOT_API_TOKEN = "ТВОЙ_ТОКЕН_CRYPTOBOT"
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

from datetime import datetime, timedelta
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from telegram import Bot
from telegram.ext import CallbackQueryHandler
from dotenv import load_dotenv
from collections import defaultdict
import asyncio
import requests
import sqlite3
import os
import json
import sys
import psutil
import aiohttp
from telegram.ext import ConversationHandler, MessageHandler, filters
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
import nest_asyncio

nest_asyncio.apply()

EDIT_SELECT, EDIT_UPDATE = range(2)

SIGNAL_CHANNEL_ID = -1002535596294 # replace with your real channel ID



PID_FILE = "bot.pid"

def check_existing_instance():
    if os.path.exists(PID_FILE):
        try:
            with open(PID_FILE, "r") as f:
                old_pid = int(f.read())
            if psutil.pid_exists(old_pid):
                print(f"❌ Bot is already running (PID {old_pid}). Exiting.")
                sys.exit(1)
            else:
                print("⚠️ Stale PID file found. Continuing...")
        except Exception as e:
            print(f"⚠️ Error reading PID file: {e}")

    with open(PID_FILE, "w") as f:
        f.write(str(os.getpid()))

def cleanup_pid():
    if os.path.exists(PID_FILE):
        os.remove(PID_FILE)

def generate_price_chart(symbol, closes):
    chart_url = "https://quickchart.io/chart"
    chart_data = {
        "type": "line",
        "data": {
            "labels": list(range(len(closes))),
            "datasets": [{
                "label": f"{symbol} Price (24h)",
                "data": closes,
                "fill": False,
                "borderColor": "rgb(75, 192, 192)",
                "tension": 0.3
            }]
        }
    }
    return f"{chart_url}?c={requests.utils.quote(json.dumps(chart_data))}"


def generate_rsi_chart(symbol, rsi_values):
    chart = {
        "type": "line",
        "data": {
            "labels": list(range(len(rsi_values))),
            "datasets": [{
                "label": f"{symbol} RSI (14)",
                "data": rsi_values,
                "borderColor": "orange",
                "fill": False,
                "tension": 0.3
            }]
        },
        "options": {
            "scales": {
                "y": {"suggestedMin": 0, "suggestedMax": 100}
            }
        }
    }
    return f"https://quickchart.io/chart?c={requests.utils.quote(json.dumps(chart))}"

def calculate_rsi_series(closes, period=14):
    if len(closes) < period + 1:
        return None
    rsi_values = []
    for i in range(period, len(closes)):
        gains = []
        losses = []
        for j in range(i - period + 1, i + 1):
            delta = closes[j] - closes[j - 1]
            if delta > 0:
                gains.append(delta)
                losses.append(0)
            else:
                gains.append(0)
                losses.append(-delta)
        avg_gain = sum(gains) / period
        avg_loss = sum(losses) / period
        if avg_loss == 0:
            rsi_values.append(100)
        else:
            rs = avg_gain / avg_loss
            rsi_values.append(100 - (100 / (1 + rs)))
    return rsi_values

def generate_macd_chart(symbol, macd_list, signal_list, hist_list):
    labels = list(range(len(macd_list)))
    chart = {
        "type": "bar",
        "data": {
            "labels": labels,
            "datasets": [
                {
                    "type": "line",
                    "label": "MACD",
                    "data": macd_list,
                    "borderColor": "blue",
                    "fill": False,
                    "tension": 0.2
                },
                {
                    "type": "line",
                    "label": "Signal",
                    "data": signal_list,
                    "borderColor": "red",
                    "fill": False,
                    "tension": 0.2
                },
                {
                    "label": "Histogram",
                    "data": hist_list,
                    "backgroundColor": "rgba(75,192,192,0.4)"
                }
            ]
        }
    }
    return f"https://quickchart.io/chart?c={requests.utils.quote(json.dumps(chart))}"


def compute_macd_series(prices):
    if len(prices) < 35:
        return None, None, None
    macd_list = []
    signal_list = []
    hist_list = []

    for i in range(26, len(prices)):
        ema_12 = calculate_ema(prices[i - 12:i], 12)
        ema_26 = calculate_ema(prices[i - 26:i], 26)
        macd = ema_12 - ema_26
        macd_list.append(macd)

    for i in range(9, len(macd_list)):
        signal = sum(macd_list[i - 9:i]) / 9
        hist = macd_list[i] - signal
        signal_list.append(signal)
        hist_list.append(hist)

    return macd_list[-len(signal_list):], signal_list, hist_list


def generate_ema_chart(symbol, closes, ema_values, period):
    chart = {
        "type": "line",
        "data": {
            "labels": list(range(len(closes))),
            "datasets": [
                {
                    "label": "Price",
                    "data": closes,
                    "borderColor": "blue",
                    "fill": False,
                    "tension": 0.2
                },
                {
                    "label": f"{period}-EMA",
                    "data": ema_values,
                    "borderColor": "red",
                    "fill": False,
                    "tension": 0.2
                }
            ]
        }
    }
    return f"https://quickchart.io/chart?c={requests.utils.quote(json.dumps(chart))}"

def generate_portfolio_pie_chart(symbols, values):
    chart = {
        "type": "pie",
        "data": {
            "labels": symbols,
            "datasets": [{
                "data": values,
                "backgroundColor": [
                    "#4dc9f6", "#f67019", "#f53794", "#537bc4",
                    "#acc236", "#166a8f", "#00a950", "#58595b",
                    "#8549ba", "#b24592"
                ]
            }]
        },
        "options": {
            "plugins": {
                "legend": {
                    "position": "bottom"
                },
                "datalabels": {
                    "formatter": """function(value, context) {
                        const data = context.chart.data.datasets[0].data;
                        const sum = data.reduce((a, b) => a + b, 0);
                        const percentage = (value / sum * 100).toFixed(1);
                        return percentage + '%';
                    }""",
                    "color": "white",
                    "font": {
                        "weight": "bold",
                        "size": 14
                    }
                }
            }
        },
        "plugins": ["https://cdn.jsdelivr.net/npm/chartjs-plugin-datalabels"]
    }

    encoded = requests.utils.quote(json.dumps(chart))
    return f"https://quickchart.io/chart?c={encoded}"


# ✅ Run instance check immediately
check_existing_instance()

# Cache for news
cached_news = {
    "timestamp": None,
    "message": None
}

cached_best = {
    "timestamp": None,
    "message": None
}

cached_worst = {
    "timestamp": None,
    "message": None
}

alert_store = {}

price_cache = {}  # Structure: { "BTCUSDT": {"price": 62000.0, "timestamp": 1715000000} }

api_usage_stats = {
    "daily": 0,
    "weekly": 0,
    "last_reset_day": datetime.utcnow().date(),
    "last_reset_week": datetime.utcnow().isocalendar()[1]
}

bot_start_time = datetime.utcnow()


# ✅ Load environment variables
load_dotenv()
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not TELEGRAM_BOT_TOKEN:
    raise ValueError("❌ TELEGRAM_BOT_TOKEN is missing! Check your .env file.")
bot = Bot(token=TELEGRAM_BOT_TOKEN)
CRYPTOCOMPARE_API_KEY = os.getenv("CRYPTOCOMPARE_API_KEY")
ADMIN_ID = int(os.getenv("ADMIN_ID"))
if not ADMIN_ID:
    raise ValueError("❌ ADMIN_ID is missing! Please check your .env file.")

# ✅ Use a single database file for all tables
DB_FILE = os.path.join(os.path.dirname(__file__), "alerts.db")
EXCHANGE_RATE_API_KEY = "7aae50601329a3afe6874c11"


def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    # Create alerts table
    cursor.execute('''CREATE TABLE IF NOT EXISTS alerts (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id INTEGER,
                        symbol TEXT,
                        condition TEXT,
                        target_price REAL,
                        repeat INTEGER DEFAULT 0
                    )''')

    cursor.execute('''CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    alerts_used INTEGER DEFAULT 0,
    last_reset DATE DEFAULT (DATE('now'))
)
''')
    cursor.execute('''
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    plan TEXT DEFAULT 'free',
    alerts_used INTEGER DEFAULT 0,
    last_reset DATE DEFAULT (DATE('now'))
)
''')

    cursor.execute('''CREATE TABLE IF NOT EXISTS percent_alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    symbol TEXT,
    base_price REAL,
    threshold_percent REAL,
    repeat INTEGER DEFAULT 0
)''')


    cursor.execute("PRAGMA table_info(users)")
    columns = [col[1] for col in cursor.fetchall()]
    if "plan" not in columns:
     cursor.execute("ALTER TABLE users ADD COLUMN plan TEXT DEFAULT 'free'")

    cursor.execute('''CREATE TABLE IF NOT EXISTS volume_alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    symbol TEXT,
    multiplier REAL,
    repeat INTEGER DEFAULT 0
)''')
    cursor.execute('''
CREATE TABLE IF NOT EXISTS risk_alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    symbol TEXT,
    stop_price REAL,
    take_price REAL,
    repeat INTEGER DEFAULT 0
)
''')

    cursor.execute('''
CREATE TABLE IF NOT EXISTS custom_alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    symbol TEXT,
    price_condition TEXT,   -- ">" or "<"
    price_value REAL,
    rsi_condition TEXT,     -- ">" or "<"
    rsi_value REAL,
    repeat INTEGER DEFAULT 0
)
''')

    cursor.execute('''
CREATE TABLE IF NOT EXISTS portfolio (
    user_id INTEGER,
    symbol TEXT,
    quantity REAL,
    PRIMARY KEY (user_id, symbol)
)
''')

    cursor.execute('''
CREATE TABLE IF NOT EXISTS portfolio_limits (
    user_id INTEGER PRIMARY KEY,
    loss_limit REAL,
    profit_target REAL
)
''')
    cursor.execute('''
CREATE TABLE IF NOT EXISTS trade_signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    symbol TEXT NOT NULL,
    direction TEXT CHECK(direction IN ('>', '<')) NOT NULL,
    entry_price REAL NOT NULL,
    stop_loss REAL,
    take_profit REAL,
    timestamp TEXT DEFAULT (datetime('now')),
    approved INTEGER DEFAULT 1
)
''')

    conn.commit()
    conn.close()

    print("✅ Database initialized!")



# Call init_db once at startup
init_db()


import time

def get_cached_price(symbol, ttl=60):
    symbol = symbol.upper()
    now = time.time()

    # If cache exists and is fresh, return it
    if symbol in price_cache:
        cached = price_cache[symbol]
        if now - cached["timestamp"] < ttl:
            return cached["price"]

    # Otherwise, fetch fresh from API
    price = get_crypto_price(symbol)
    if price is not None:
        price_cache[symbol] = {"price": price, "timestamp": now}
    return price

def count_api_call():
    today = datetime.utcnow().date()
    week = datetime.utcnow().isocalendar()[1]

    # Reset daily
    if api_usage_stats["last_reset_day"] != today:
        api_usage_stats["daily"] = 0
        api_usage_stats["last_reset_day"] = today

    # Reset weekly
    if api_usage_stats["last_reset_week"] != week:
        api_usage_stats["weekly"] = 0
        api_usage_stats["last_reset_week"] = week

    # Count it
    api_usage_stats["daily"] += 1
    api_usage_stats["weekly"] += 1

def migrate_add_pro_column():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    try:
        cursor.execute("ALTER TABLE users ADD COLUMN pro INTEGER DEFAULT 0")
        conn.commit()
    except sqlite3.OperationalError:
        # Column already exists
        pass
    conn.close()

migrate_add_pro_column()

# ✅ Fetch Prices

def get_crypto_price(symbol="BTC"):
    url = f"https://min-api.cryptocompare.com/data/price?fsym={symbol.upper()}&tsyms=USD"
    headers = {
        "authorization": f"Apikey {CRYPTOCOMPARE_API_KEY}"
    }

    try:
        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()
        data = response.json()
        return data.get("USD", None)
    except Exception as e:
        print(f"❌ CryptoCompare Error: {e} | Symbol tried: {symbol}")
        return None

    if data is valid:
        count_api_call()
        return price



# ✅ Telegram Bot Handlers

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if args:
        command = args[0]
        fake_update = update  # reuse the same update
        fake_context = context

        if command == "set":
            return await set(fake_update, fake_context)
        elif command == "help":
            return await help_command(fake_update, fake_context)
        elif command == "alerts":
            return await alerts(fake_update, fake_context)
        elif command == "clear":
            return await clear_alerts_prompt(fake_update, fake_context)
        elif command == "best":
            return await best(fake_update, fake_context)
        elif command == "worst":
            return await worst(fake_update, fake_context)
        elif command == "news":
            return await news(fake_update, fake_context)
        elif command == "trend":
            return await trend(fake_update, fake_context)
        elif command == "price":
            return await price(fake_update, fake_context)
        elif command == "upgrade":
            return await upgrade(fake_update, fake_context)
        elif command == "edit":
            return await edit_alert_start(fake_update, fake_context)

    args = context.args
    user_id = update.effective_user.id

    if args:
        source = args[0]
        # Log referral source
        referral_stats[source] += 1  # replace with DB increment if needed

        # Optionally, log to database
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS referrals (
                source TEXT PRIMARY KEY,
                clicks INTEGER DEFAULT 0
            )
        ''')
        cursor.execute('''
            INSERT INTO referrals (source, clicks) 
            VALUES (?, 1)
            ON CONFLICT(source) DO UPDATE SET clicks = clicks + 1
        ''', (source,))
        conn.commit()
        conn.close()


    # Default welcome message if no args
    await update.message.reply_text(
        "👋 Welcome to PricePulseBot!\nUse /menu to get started."
    )


async def price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) != 1:
        await update.message.reply_text("❌ Usage: /price <symbol>\nExample: /price BTCUSDT")
        return

    symbol = context.args[0].upper()
    price = get_cached_price(symbol)

    if price is not None:
        await update.message.reply_text(f"💰 *{symbol} Price:* ${price:.2f}", parse_mode="Markdown")

        # Generate and send chart
        url = f"https://min-api.cryptocompare.com/data/v2/histohour?fsym={symbol}&tsym=USD&limit=24"
        headers = {"authorization": f"Apikey {CRYPTOCOMPARE_API_KEY}"}
        try:
            response = requests.get(url, headers=headers)
            closes = [item["close"] for item in response.json()["Data"]["Data"]]
            chart_url = generate_price_chart(symbol, closes)
            await update.message.reply_photo(photo=chart_url, caption=f"{symbol} – 24h Price Trend")
        except Exception as e:
            print(f"Chart generation failed: {e}")
    else:
        await update.message.reply_text("⚠️ Couldn't fetch the price. Please try again later.")


async def set_price_alert(update: Update, context: ContextTypes.DEFAULT_TYPE):
    
    if len(context.args) < 3:
        await update.message.reply_text("❌ Usage: /set BTCUSDT > 70000 [repeat]")
        return

    user_id = update.message.chat_id
    symbol = context.args[0].upper()
    condition = context.args[1]
    today = datetime.now().strftime("%Y-%m-%d")

    try:
        target_price = float(context.args[2])
    except ValueError:
        await update.message.reply_text("❌ Invalid price format. Use numbers only.")
        return

    if condition not in [">", "<"]:
        await update.message.reply_text("❌ Invalid condition. Use '>' or '<'.")
        return

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    # Retrieve user record or create if not exists
    cursor.execute(
        "SELECT plan, alerts_used, last_reset FROM users WHERE user_id = ?", (user_id,))
    user = cursor.fetchone()

    if user is None:
        # New user, default to free plan
        cursor.execute(
            "INSERT INTO users (user_id, plan, alerts_used, last_reset) VALUES (?, 'free', 0, ?)",
            (user_id, today)
        )
        conn.commit()
        plan, alerts_used, last_reset = 'free', 0, today
    else:
        plan, alerts_used, last_reset = user

    # Reset daily count if it's a new day
    if last_reset != today:
        alerts_used = 0
        cursor.execute(
            "UPDATE users SET alerts_used = 0, last_reset = ? WHERE user_id = ?",
            (today, user_id)
        )
        conn.commit()

    # Enforce daily limit for free users
    if plan == 'free' and alerts_used >= 3:
        await update.message.reply_text(
            "🚫 You've reached your *daily alert limit* of 3.\n\n"
            "Use /upgrade to unlock *unlimited alerts* with Pro access.",
            parse_mode="Markdown"
        )
        conn.close()
        return
    
         # ✅ Limit free users to 1 persistent alert
    repeat_flag = 1 if len(context.args) > 3 and context.args[3].lower() == "repeat" else 0

    if plan == 'free' and repeat_flag == 1:
        cursor.execute("SELECT COUNT(*) FROM alerts WHERE user_id = ? AND repeat = 1", (user_id,))
        count = cursor.fetchone()[0]
        if count >= 1:
            await update.message.reply_text(
            "🔒 Free users can only set *1 persistent alert*.\n\n"
            "🚀 Upgrade to Pro for *unlimited persistent alerts*.\nUse /upgrade to learn more.",
            parse_mode="Markdown"
        )
        conn.close()
        return


    # Insert new alert with repeat flag
    cursor.execute(
        "INSERT INTO alerts (user_id, symbol, condition, target_price, repeat) VALUES (?, ?, ?, ?, ?)",
        (user_id, symbol, condition, target_price, repeat_flag)
    )

   
    # Increment alert usage if user is on the free plan
    if plan == 'free':
        cursor.execute(
            "UPDATE users SET alerts_used = alerts_used + 1 WHERE user_id = ?",
            (user_id,)
        )

    conn.commit()
    conn.close()

    await update.message.reply_text(f"✅ Alert set for {symbol} when price is {condition} {target_price}.")

async def edit_alert_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_chat.id

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, symbol, target_price FROM alerts WHERE user_id = ?",
        (user_id,)
    )
    rows = cursor.fetchall()
    conn.close()

    if not rows:
        await update.message.reply_text("📭 You have no active alerts.")
        return ConversationHandler.END

    # Save alert data to user_data for editing
    context.user_data["edit_alerts"] = [{"id": r[0], "symbol": r[1], "target_price": r[2]} for r in rows]

    message = "🛠 *Your Alerts:*\n\n"
    for i, alert in enumerate(context.user_data["edit_alerts"], start=1):
        message += f"{i}. {alert['symbol']} → ${alert['target_price']}\n"
    message += "\nPlease send the *number* of the alert you'd like to edit."

    await update.message.reply_text(message, parse_mode="Markdown")
    return EDIT_SELECT


async def edit_alert_select(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()

    if not text.isdigit():
        await update.message.reply_text("⚠️ Please enter a valid number.")
        return EDIT_SELECT

    index = int(text) - 1
    alerts_list = context.user_data["edit_alerts"]

    if index < 0 or index >= len(alerts_list):
        await update.message.reply_text("⚠️ That number doesn't match any alert.")
        return EDIT_SELECT

    context.user_data["edit_index"] = index
    alert = alerts_list[index]

    await update.message.reply_text(
        f"You selected *{alert['symbol']}* alert (Current: ${alert['target_price']}).\n\n"
        "Send the new target price:",
        parse_mode="Markdown"
    )
    return EDIT_UPDATE

async def edit_alert_update(update: Update, context: ContextTypes.DEFAULT_TYPE):
    new_price_text = update.message.text.strip()

    try:
        new_price = float(new_price_text)
    except ValueError:
        await update.message.reply_text("⚠️ Please enter a valid number (e.g., 29250.75).")
        return EDIT_UPDATE

    selected_alert = context.user_data["edit_alerts"][context.user_data["edit_index"]]
    alert_id = selected_alert["id"]

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE alerts SET target_price = ? WHERE id = ?",
        (new_price, alert_id)
    )
    conn.commit()
    conn.close()

    await update.message.reply_text("✅ Alert updated successfully.")

    return ConversationHandler.END


# ✅ Helper functions for Trend


async def remove(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) == 0:
        await update.message.reply_text("❌ Usage: /remove <ALERT_ID>\nExample: /remove 3")
        return

    try:
        alert_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ Invalid ID format. Use a number.")
        return

    user_id = update.message.chat_id
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    # Attempt to delete only if the alert belongs to the user
    cursor.execute(
        "DELETE FROM alerts WHERE id = ? AND user_id = ?",
        (alert_id, user_id)
    )
    deleted = cursor.rowcount
    conn.commit()
    conn.close()

    if deleted:
        await update.message.reply_text(f"✅ Alert ID {alert_id} has been removed.")
    else:
        await update.message.reply_text("❌ No alert found with that ID, or it does not belong to you.")

async def removepercent(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if len(context.args) != 1:
        await update.message.reply_text("❌ Usage: /removepercent <ID>\nExample: /removepercent 5")
        return

    try:
        alert_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ Invalid ID format. Use a number.")
        return

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM percent_alerts WHERE id = ? AND user_id = ?", (alert_id, user_id))
    deleted = cursor.rowcount
    conn.commit()
    conn.close()

    if deleted:
        await update.message.reply_text(f"✅ Percentage alert #{alert_id} has been removed.")
    else:
        await update.message.reply_text("❌ No alert found with that ID, or it does not belong to you.")

async def removevolume(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if len(context.args) != 1:
        await update.message.reply_text("❌ Usage: /removevolume <ID>\nExample: /removevolume 4")
        return

    try:
        alert_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ Invalid ID format. Use a number.")
        return

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM volume_alerts WHERE id = ? AND user_id = ?", (alert_id, user_id))
    deleted = cursor.rowcount
    conn.commit()
    conn.close()

    if deleted:
        await update.message.reply_text(f"✅ Volume alert #{alert_id} has been removed.")
    else:
        await update.message.reply_text("❌ No alert found with that ID, or it does not belong to you.")

async def removerisk(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if len(context.args) != 1:
        await update.message.reply_text("❌ Usage: /removerisk <ID>\nExample: /removerisk 6")
        return

    try:
        alert_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ Invalid ID format. Use a number.")
        return

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM risk_alerts WHERE id = ? AND user_id = ?", (alert_id, user_id))
    deleted = cursor.rowcount
    conn.commit()
    conn.close()

    if deleted:
        await update.message.reply_text(f"✅ Risk alert #{alert_id} has been removed.")
    else:
        await update.message.reply_text("❌ No alert found with that ID, or it does not belong to you.")

async def removecustom(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if len(context.args) != 1:
        await update.message.reply_text("❌ Usage: /removecustom <ID>\nExample: /removecustom 12")
        return

    try:
        alert_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ Invalid ID format. Use a number.")
        return

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM custom_alerts WHERE id = ? AND user_id = ?", (alert_id, user_id))
    deleted = cursor.rowcount
    conn.commit()
    conn.close()

    if deleted:
        await update.message.reply_text(f"✅ Custom alert #{alert_id} has been removed.")
    else:
        await update.message.reply_text("❌ No alert found with that ID, or it does not belong to you.")


async def clear_alerts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    # Delete alerts across all alert tables
    tables = ["alerts", "percent_alerts", "volume_alerts", "risk_alerts", "custom_alerts"]
    for table in tables:
        cursor.execute(f"DELETE FROM {table} WHERE user_id = ?", (user_id,))

    conn.commit()

    # Reset autoincrement (optional: only if you want IDs to start fresh)
    for table in tables:
        cursor.execute(f"SELECT COUNT(*) FROM {table}")
        if cursor.fetchone()[0] == 0:
            cursor.execute(f"DELETE FROM sqlite_sequence WHERE name='{table}'")

    conn.commit()
    conn.close()

    await update.message.reply_text("🧹 All your alerts (price, percent, volume, risk, and custom) have been cleared.")


async def clear_alerts_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Yes, clear", callback_data="confirm_clear")],
        [InlineKeyboardButton("❌ Cancel", callback_data="cancel_clear")]
    ])
    await update.message.reply_text(
        "⚠️ Are you sure you want to *delete all your alerts*?\nThis action cannot be undone.",
        reply_markup=keyboard,
        parse_mode="Markdown"
    )

async def clear_alerts_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    if query.data == "confirm_clear":
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()

        # List of all alert tables
        alert_tables = [
            "alerts",
            "percent_alerts",
            "volume_alerts",
            "risk_alerts",
            "custom_alerts"
        ]

        # Delete user's alerts from all tables
        for table in alert_tables:
            cursor.execute(f"DELETE FROM {table} WHERE user_id = ?", (user_id,))

        conn.commit()

        # Optionally reset auto-increment if tables are now empty
        for table in alert_tables:
            cursor.execute(f"SELECT COUNT(*) FROM {table}")
            if cursor.fetchone()[0] == 0:
                cursor.execute(f"DELETE FROM sqlite_sequence WHERE name='{table}'")

        conn.commit()
        conn.close()

        await query.edit_message_text("🧹 All your alerts have been cleared.")

    elif query.data == "cancel_clear":
        await query.edit_message_text("❎ Cancelled. No alerts were deleted.")


def get_crypto_trend(symbol, timeframe):
    symbol = symbol.upper()
    url_map = {
        "1H": ("histominute", 60),
        "4H": ("histominute", 240),
        "12H": ("histominute", 720),
        "24H": ("histohour", 24),
        "7D": ("histoday", 7),
    }

    if timeframe not in url_map:
        print(f"❌ Unsupported timeframe: {timeframe}")
        return None

    endpoint, limit = url_map[timeframe]
    url = f"https://min-api.cryptocompare.com/data/{endpoint}?fsym={symbol}&tsym=USD&limit={limit}"
    headers = {
        "authorization": f"Apikey {os.getenv('CRYPTOCOMPARE_API_KEY')}"
    }

    try:
        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()
        data = response.json()

        prices = data.get("Data", [])
        if len(prices) < 2:
            return None

        old_price = prices[0]["close"]
        current_price = prices[-1]["close"]

        if old_price == 0:
            return None

        return ((current_price - old_price) / old_price) * 100
    except Exception as e:
        print(f"❌ CryptoCompare Trend Error for {symbol}: {e}")
        return None

    if data is valid:
        count_api_call()
        return price



async def trend(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.chat_id

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT plan FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()

    plan = row[0] if row else "free"

    if plan == "free":
        await update.message.reply_text(
            "🔒 Trend indicators are available to Pro users only.\nUse /upgrade to unlock.",
            parse_mode="Markdown"
        )
        return

    if len(context.args) < 2:
        await update.message.reply_text(
            "❌ Usage:\n"
            "/trend BTCUSDT rsi\n"
            "/trend BTCUSDT macd\n"
            "/trend BTCUSDT ema 20"
        )
        return

    symbol = context.args[0].upper()
    indicator = context.args[1].lower()

    if indicator == "rsi":
        rsi = get_rsi(symbol)
        if rsi is None:
            await update.message.reply_text("⚠️ Could not fetch RSI.")
            return
        await update.message.reply_text(f"📊 *RSI for {symbol}*: `{rsi:.2f}`", parse_mode="Markdown")

        # Fetch full list of RSI values
        prices = get_candles(symbol, 30)
        rsi_list = calculate_rsi_series(prices)
        if rsi_list:
            chart_url = generate_rsi_chart(symbol, rsi_list)
            await update.message.reply_photo(chart_url, caption=f"{symbol} RSI (14)")

    elif indicator == "macd":
        macd, signal, hist = get_macd(symbol)
        if macd is None:
            await update.message.reply_text("⚠️ Could not fetch MACD.")
            return

        await update.message.reply_text(
            f"📉 *MACD for {symbol}*:\nMACD: `{macd:.4f}`\nSignal: `{signal:.4f}`\nHistogram: `{hist:.4f}`",
            parse_mode="Markdown"
        )

        prices = get_candles(symbol, 50)
        macd_list, signal_list, hist_list = compute_macd_series(prices)
        if macd_list:
            chart_url = generate_macd_chart(symbol, macd_list, signal_list, hist_list)
            await update.message.reply_photo(chart_url, caption=f"{symbol} MACD")


    elif indicator == "ema":
        if len(context.args) < 3:
            await update.message.reply_text("❌ Usage: /trend BTCUSDT ema 20")
            return
        try:
            period = int(context.args[2])
        except:
            await update.message.reply_text("❌ Invalid EMA period.")
            return

        closes = get_candles(symbol, period + 10)
        ema = calculate_ema(closes, period)

        if ema is None:
            await update.message.reply_text("⚠️ Could not compute EMA.")
            return

        ema_values = [None] * (period - 1)
        for i in range(period - 1, len(closes)):
            ema_values.append(calculate_ema(closes[:i+1], period))

        await update.message.reply_text(f"📈 *{period}-EMA for {symbol}* is: `${ema:.2f}`", parse_mode="Markdown")

        chart_url = generate_ema_chart(symbol, closes, ema_values, period)
        await update.message.reply_photo(chart_url, caption=f"{symbol} – Price vs {period}-EMA")

async def get_cached_rsi(symbol, indicator_cache):
    if "rsi" not in indicator_cache[symbol]:
        rsi = get_rsi(symbol)
        indicator_cache[symbol]["rsi"] = rsi
    return indicator_cache[symbol]["rsi"]

async def get_cached_macd(symbol, indicator_cache):
    if "macd" not in indicator_cache[symbol]:
        macd = get_macd(symbol)
        indicator_cache[symbol]["macd"] = macd
    return indicator_cache[symbol]["macd"]

async def get_cached_ema(symbol, period, indicator_cache):
    key = f"ema{period}"
    if key not in indicator_cache[symbol]:
        candles = get_candles(symbol, period + 5)
        ema = calculate_ema(candles, period)
        indicator_cache[symbol][key] = ema
    return indicator_cache[symbol][key]

async def check_alerts(context: ContextTypes.DEFAULT_TYPE):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    indicator_cache = defaultdict(dict)  # indicator_cache[symbol]["rsi"], ["macd"], ["ema20"], etc.

    # 1. Collect unique symbols from all alert tables
    all_symbols = set()
    for table in ["alerts", "percent_alerts", "risk_alerts", "custom_alerts"]:
        cursor.execute(f"SELECT DISTINCT symbol FROM {table}")
        all_symbols.update(row[0] for row in cursor.fetchall())

    # 2. Fetch all prices once
    symbol_prices = {}
    for symbol in all_symbols:
        symbol_prices[symbol] = get_crypto_price(symbol)

    # --- PRICE ALERTS ---
    cursor.execute("SELECT id, user_id, symbol, condition, target_price, repeat FROM alerts")
    for alert_id, user_id, symbol, cond, target, repeat in cursor.fetchall():
        price = symbol_prices.get(symbol)
        if price is None:
            continue
        if (cond == ">" and price > target) or (cond == "<" and price < target):
            try:
                await context.bot.send_message(
                    chat_id=user_id,
                    text=f"🔔 *Price Alert: {symbol}*\nCurrent price: ${price:.2f} {cond} {target}",
                    parse_mode="Markdown"
                )
            except Exception as e:
                print(f"Price alert error: {e}")
            if not repeat:
                cursor.execute("DELETE FROM alerts WHERE id = ?", (alert_id,))
                conn.commit()

    # --- PERCENT ALERTS ---
    cursor.execute("SELECT id, user_id, symbol, base_price, threshold_percent, repeat FROM percent_alerts")
    for alert_id, user_id, symbol, base_price, threshold_percent, repeat in cursor.fetchall():
        price = symbol_prices.get(symbol)
        if price is None:
            continue
        change = abs((price - base_price) / base_price * 100)
        if change >= threshold_percent:
            try:
                await context.bot.send_message(
                    chat_id=user_id,
                    text=f"📉 *% Alert for {symbol}*\nChange: {change:.2f}% from ${base_price:.2f}\nNow: ${price:.2f}",
                    parse_mode="Markdown"
                )
            except Exception as e:
                print(f"Percent alert error: {e}")
            if not repeat:
                cursor.execute("DELETE FROM percent_alerts WHERE id = ?", (alert_id,))
                conn.commit()

    # --- RISK ALERTS ---
    cursor.execute("SELECT id, user_id, symbol, stop_price, take_price, repeat FROM risk_alerts")
    for alert_id, user_id, symbol, stop_price, take_price, repeat in cursor.fetchall():
        price = symbol_prices.get(symbol)
        if price is None:
            continue
        if price <= stop_price or price >= take_price:
            try:
                await context.bot.send_message(
                    chat_id=user_id,
                    text=f"🛑 *Risk Alert for {symbol}*\nPrice hit ${price:.2f}.\nSL: {stop_price}, TP: {take_price}",
                    parse_mode="Markdown"
                )
            except Exception as e:
                print(f"Risk alert error: {e}")
            if not repeat:
                cursor.execute("DELETE FROM risk_alerts WHERE id = ?", (alert_id,))
                conn.commit()

    # --- CUSTOM ALERTS (price + RSI/MACD/EMA) ---
    custom_data_cache = {}  # For RSI, MACD, EMA

    cursor.execute("SELECT id, user_id, symbol, price_condition, price_value, rsi_condition, rsi_value, repeat FROM custom_alerts")
    for row in cursor.fetchall():
        alert_id, user_id, symbol, p_cond, p_val, r_cond, r_val, repeat = row
        price = symbol_prices.get(symbol)
        if price is None:
            continue

        # Price match
        price_match = (p_cond == ">" and price > p_val) or (p_cond == "<" and price < p_val)

        # RSI/MACD/EMA match
        rsi_match = False
        try:
            if r_cond.startswith("rsi"):
                rsi = await get_cached_rsi(symbol, indicator_cache)
                rsi_match = (r_cond == ">" and rsi > r_val) or (r_cond == "<" and rsi < r_val)

            elif r_cond == "macd":
                macd, signal, hist = await get_cached_macd(symbol, indicator_cache)
                rsi_match = hist > 0

            elif r_cond.startswith("ema>"):
                period = int(r_cond.split(">")[1])
                ema = await get_cached_ema(symbol, period, indicator_cache)
                rsi_match = ema and price > ema

        except:
            continue

        if price_match and rsi_match:
            try:
                await context.bot.send_message(
                    chat_id=user_id,
                    text=(
                        f"🧠 *Custom Alert for {symbol}*\n"
                        f"Price: ${price:.2f} ({p_cond}{p_val}) ✅\n"
                        f"Indicator: `{r_cond}` ✅"
                    ),
                    parse_mode="Markdown"
                )
            except Exception as e:
                print(f"Custom alert error: {e}")
            if not repeat:
                cursor.execute("DELETE FROM custom_alerts WHERE id = ?", (alert_id,))
                conn.commit()

        # --- PORTFOLIO VALUE ALERTS ---
    cursor.execute("""
    SELECT p.user_id, p.symbol, p.quantity, l.loss_limit, l.profit_target 
    FROM portfolio p
    LEFT JOIN portfolio_limits l ON p.user_id = l.user_id
""")

    portfolios = defaultdict(list)
    limits = {}

    for user_id, symbol, quantity, loss_limit, profit_target in cursor.fetchall():
        portfolios[user_id].append((symbol, quantity))
        limits[user_id] = {"loss_limit": loss_limit, "profit_target": profit_target}

    for user_id, assets in portfolios.items():
        loss_limit = limits[user_id]["loss_limit"]
        profit_target = limits[user_id]["profit_target"]

        # 🚫 Skip users who have no value alerts
        if loss_limit is None and profit_target is None:
            continue

        total_value = 0
        missing_data = False
        for symbol, amount in assets:
            price = symbol_prices.get(symbol)
            if price is None:
                missing_data = True
                break
            total_value += price * amount

        if missing_data:
            continue  # Skip if any symbol price is missing

        if loss_limit and total_value <= loss_limit:
            try:
                await context.bot.send_message(
                    chat_id=user_id,
                    text=(
                        f"⚠️ *Portfolio Loss Alert*\n"
                        f"Your total value dropped to ${total_value:,.2f}.\n"
                        f"Loss limit was: ${loss_limit:,.2f}"
                    ),
                    parse_mode="Markdown"
                )
            except Exception as e:
                print(f"Portfolio loss alert error: {e}")

            cursor.execute("UPDATE portfolio_limits SET loss_limit = NULL WHERE user_id = ?", (user_id,))
            conn.commit()

        elif profit_target and total_value >= profit_target:
            try:
                await context.bot.send_message(
                    chat_id=user_id,
                    text=(
                        f"🎯 *Portfolio Target Reached*\n"
                        f"Your total value is now ${total_value:,.2f}.\n"
                        f"Target goal was: ${profit_target:,.2f}"
                    ),
                    parse_mode="Markdown"
                )
            except Exception as e:
                print(f"Portfolio profit alert error: {e}")

            cursor.execute("UPDATE portfolio_limits SET profit_target = NULL WHERE user_id = ?", (user_id,))
            conn.commit()
    conn.close()

async def alerts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id =  user_id = update.effective_user.id


    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    # ✅ Fetch alerts
    cursor.execute(
        "SELECT id, symbol, condition, target_price, repeat FROM alerts WHERE user_id = ?",
        (user_id,)
    )
    conn.commit()
    alert_rows = cursor.fetchall()

    # ✅ Fetch plan
    cursor.execute("SELECT plan FROM users WHERE user_id = ?", (user_id,))
    result = cursor.fetchone()
    plan = result[0] if result else "free"

    conn.close()

    if not alert_rows:
        await update.message.reply_text("📭 You have no active alerts.")
        return

    text = "\n".join([
        f"#{alert_id}: {symbol} {condition} {target_price} {'🔁' if repeat else ''}"
        for alert_id, symbol, condition, target_price, repeat in alert_rows
    ])
    await update.message.reply_text(f"📋 Your Alerts:\n{text}")

    if plan == "free":
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT COUNT(*) FROM alerts WHERE user_id = ? AND repeat = 1", (user_id,)
        )
        conn.commit()
        count = cursor.fetchone()[0]
        conn.close()

        if count == 1:
            await update.message.reply_text(
                "🔁 You're using your *1 free persistent alert*.\n"
                "Upgrade to Pro for unlimited.\n"
                "Use /upgrade to learn more.",
                parse_mode="Markdown"
            )

async def percentalerts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT id, symbol, base_price, threshold_percent, repeat FROM percent_alerts WHERE user_id = ?", (user_id,))
    rows = cursor.fetchall()
    conn.close()

    if not rows:
        await update.message.reply_text("📭 You have no active percentage alerts.")
        return

    message = "📊 *Your Percentage Alerts:*\n\n"
    for alert_id, symbol, base, percent, repeat in rows:
        message += f"#{alert_id}: {symbol} ±{percent}% from ${base:.2f} {'🔁' if repeat else ''}\n"

    await update.message.reply_text(message, parse_mode="Markdown")

async def volumealerts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, symbol, multiplier, repeat FROM volume_alerts WHERE user_id = ?",
        (user_id,)
    )
    rows = cursor.fetchall()
    conn.close()

    if not rows:
        await update.message.reply_text("📭 You have no active volume spike alerts.")
        return

    message = "📊 *Your Volume Spike Alerts:*\n\n"
    for alert_id, symbol, multiplier, repeat in rows:
        message += f"#{alert_id}: {symbol} Volume > {multiplier}× avg {'🔁' if repeat else ''}\n"

    await update.message.reply_text(message, parse_mode="Markdown")

async def riskalerts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, symbol, stop_price, take_price, repeat FROM risk_alerts WHERE user_id = ?",
        (user_id,)
    )
    rows = cursor.fetchall()
    conn.close()

    if not rows:
        await update.message.reply_text("📭 You have no active risk alerts.")
        return

    message = "🛡 *Your Risk Alerts (SL/TP):*\n\n"
    for alert_id, symbol, sl, tp, repeat in rows:
        message += f"#{alert_id}: {symbol}\n🛑 SL: ${sl:.2f} | 🎯 TP: ${tp:.2f} {'🔁' if repeat else ''}\n\n"

    await update.message.reply_text(message, parse_mode="Markdown")

async def customalerts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, symbol, price_condition, price_value, rsi_condition, rsi_value, repeat FROM custom_alerts WHERE user_id = ?",
        (user_id,)
    )
    rows = cursor.fetchall()
    conn.close()

    if not rows:
        await update.message.reply_text("📭 You have no custom alerts.")
        return

    message = "🧠 *Your Custom Alerts (Price + RSI):*\n\n"
    for alert_id, symbol, p_cond, p_val, r_cond, r_val, repeat in rows:
        message += (
            f"#{alert_id}: {symbol}\n"
            f"• Price {p_cond} {p_val} & RSI {r_cond} {r_val} {'🔁' if repeat else ''}\n\n"
        )

    await update.message.reply_text(message, parse_mode="Markdown")


async def best(update: Update, context: ContextTypes.DEFAULT_TYPE):
    now = datetime.utcnow()
    cache_duration = timedelta(minutes=5)

    if cached_best["timestamp"] and now - cached_best["timestamp"] < cache_duration:
        await update.message.reply_text(cached_best["message"], parse_mode="Markdown")
        return

    url = "https://min-api.cryptocompare.com/data/top/mktcapfull?limit=50&tsym=USDT"

    api_key = os.getenv("CRYPTOCOMPARE_API_KEY")
    headers = {"Authorization": api_key}

    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers) as response:
            data = await response.json()

    if "Data" not in data:
        await update.message.reply_text("⚠️ Could not fetch market data. Please try again later.")
        return

    coins = data["Data"]
    gainers = []

    for coin in coins:
        try:
            symbol = coin["CoinInfo"]["Name"]
            name = coin["CoinInfo"]["FullName"]
            price = coin["RAW"]["USDT"]["PRICE"]
            change_pct = coin["RAW"]["USDT"]["CHANGEPCT24HOUR"]
            gainers.append((symbol, name, price, change_pct))
        except KeyError:
            continue

    top_gainers = sorted(gainers, key=lambda x: x[3], reverse=True)[:3]

    message = "📈 *Top 3 Gainers (24h)*:\n\n"
    for symbol, name, price, change_pct in top_gainers:
        message += f"*{name}* ({symbol})\n💰 ${price:,.2f}\n📈 {change_pct:+.2f}%\n\n"

    cached_best["timestamp"] = now
    cached_best["message"] = message

    await update.message.reply_text(message, parse_mode="Markdown")

    if data is valid:
        count_api_call()
        return price



async def worst(update: Update, context: ContextTypes.DEFAULT_TYPE):
    now = datetime.utcnow()
    cache_duration = timedelta(minutes=5)

    if cached_worst["timestamp"] and now - cached_worst["timestamp"] < cache_duration:
        await update.message.reply_text(cached_worst["message"], parse_mode="Markdown")
        return

    url = "https://min-api.cryptocompare.com/data/top/mktcapfull?limit=50&tsym=USDT"
    
    api_key = os.getenv("CRYPTOCOMPARE_API_KEY")
    headers = {"Authorization": api_key}


    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers) as response:
            data = await response.json()

    if "Data" not in data:
        await update.message.reply_text("⚠️ Could not fetch market data. Please try again later.")
        return

    coins = data["Data"]
    losers = []

    for coin in coins:
        try:
            symbol = coin["CoinInfo"]["Name"]
            name = coin["CoinInfo"]["FullName"]
            price = coin["RAW"]["USDT"]["PRICE"]
            change_pct = coin["RAW"]["USDT"]["CHANGEPCT24HOUR"]
            losers.append((symbol, name, price, change_pct))
        except KeyError:
            continue

    top_losers = sorted(losers, key=lambda x: x[3])[:3]

    message = "📉 *Top 3 Losers (24h)*:\n\n"
    for symbol, name, price, change_pct in top_losers:
        message += f"*{name}* ({symbol})\n💰 ${price:,.2f}\n📉 {change_pct:+.2f}%\n\n"

    cached_worst["timestamp"] = now
    cached_worst["message"] = message

    await update.message.reply_text(message, parse_mode="Markdown")

    if data is valid:
        count_api_call()
        return price

async def news(update: Update, context: ContextTypes.DEFAULT_TYPE):
    now = datetime.utcnow()
    cache_duration = timedelta(minutes=5)

    # Check if cached and still fresh
    if cached_news["timestamp"] and now - cached_news["timestamp"] < cache_duration:
        await update.message.reply_text(cached_news["message"], parse_mode="Markdown", disable_web_page_preview=True)
        return

    url = "https://min-api.cryptocompare.com/data/v2/news/?lang=EN&categories=BTC,ETH,Crypto"
    headers = {"Authorization": "3b5c9de4e851d129efb5aeec80c0b99ea5d7ba7b4fd3c94d38e919a6a4915da6"}

    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers) as response:
            data = await response.json()

    if "Data" not in data:
        await update.message.reply_text("⚠️ Could not fetch news. Please try again later.")
        return

    articles = data["Data"][:3]

    message = "📰 *Latest Crypto Headlines:*\n\n"
    for article in articles:
        title = article["title"]
        source = article["source"]
        url = article["url"]
        message += f"🔹 [{title}]({url}) – `{source}`\n\n"

    # Update cache
    cached_news["timestamp"] = now
    cached_news["message"] = message

    await update.message.reply_text(message, parse_mode="Markdown", disable_web_page_preview=True)

    if data is valid:
        count_api_call()
        return price

async def percent(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    today = datetime.now().strftime("%Y-%m-%d")

    # Pro-only
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT plan FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    plan = row[0] if row else "free"
    if plan == "free":
        await update.message.reply_text(
            "🔒 Percentage alerts are for *Pro* users.\nUse /upgrade to unlock this feature.",
            parse_mode="Markdown"
        )
        return

    if len(context.args) < 2:
        await update.message.reply_text("❌ Usage: /percent BTCUSDT 5 [repeat]")
        return

    symbol = context.args[0].upper()
    try:
        percent = float(context.args[1])
    except ValueError:
        await update.message.reply_text("❌ Enter a valid percentage value (e.g. 5)")
        return

    repeat_flag = 1 if len(context.args) > 2 and context.args[2].lower() == "repeat" else 0

    base_price = get_crypto_price(symbol)
    if base_price is None:
        await update.message.reply_text("❌ Could not fetch the current price.")
        return

    # Insert into table
    cursor.execute(
        "INSERT INTO percent_alerts (user_id, symbol, base_price, threshold_percent, repeat) VALUES (?, ?, ?, ?, ?)",
        (user_id, symbol, base_price, percent, repeat_flag)
    )
    conn.commit()
    conn.close()

    await update.message.reply_text(
        f"✅ Alert set: Notify when *{symbol}* changes ±{percent}% from ${base_price:.2f}",
        parse_mode="Markdown"
    )

async def volume(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    # Only Pro users
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT plan FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    plan = row[0] if row else "free"

    if plan == "free":
        await update.message.reply_text(
            "🔒 Volume spike alerts are for *Pro users*.\nUse /upgrade to access this feature.",
            parse_mode="Markdown"
        )
        return

    if len(context.args) < 2:
        await update.message.reply_text("❌ Usage: /volume BTCUSDT 2 [repeat]")
        return

    symbol = context.args[0].upper()
    try:
        multiplier = float(context.args[1])
        if multiplier <= 1:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ Multiplier must be a number > 1 (e.g., 2)")
        return

    repeat_flag = 1 if len(context.args) > 2 and context.args[2].lower() == "repeat" else 0

    # Insert
    cursor.execute(
        "INSERT INTO volume_alerts (user_id, symbol, multiplier, repeat) VALUES (?, ?, ?, ?)",
        (user_id, symbol, multiplier, repeat_flag)
    )
    conn.commit()
    conn.close()

    await update.message.reply_text(
        f"✅ Volume alert set for *{symbol}*.\nWill notify if volume spikes {multiplier}x above normal.",
        parse_mode="Markdown"
    )
async def risk(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    # Pro-only
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT plan FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    plan = row[0] if row else "free"

    if plan == "free":
        await update.message.reply_text(
            "🔒 Risk alerts (Stop-Loss / Take-Profit) are for *Pro users*.\nUse /upgrade to unlock.",
            parse_mode="Markdown"
        )
        return

    if len(context.args) < 3:
        await update.message.reply_text("❌ Usage: /risk BTCUSDT 30000 32000 [repeat]")
        return

    symbol = context.args[0].upper()
    try:
        stop_price = float(context.args[1])
        take_price = float(context.args[2])
    except ValueError:
        await update.message.reply_text("❌ Stop-loss and Take-profit must be valid prices.")
        return

    repeat_flag = 1 if len(context.args) > 3 and context.args[3].lower() == "repeat" else 0

    cursor.execute(
        "INSERT INTO risk_alerts (user_id, symbol, stop_price, take_price, repeat) VALUES (?, ?, ?, ?, ?)",
        (user_id, symbol, stop_price, take_price, repeat_flag)
    )
    conn.commit()
    conn.close()

    await update.message.reply_text(
        f"✅ Risk alert set for *{symbol}*\n\n"
        f"• Stop-Loss: ${stop_price:.2f}\n"
        f"• Take-Profit: ${take_price:.2f}\n"
        f"{'🔁 Repeat enabled' if repeat_flag else ''}",
        parse_mode="Markdown"
    )

def get_rsi(symbol, period=14):
    url = f"https://min-api.cryptocompare.com/data/histohour?fsym={symbol}&tsym=USD&limit={period+1}"
    headers = {"authorization": f"Apikey {CRYPTOCOMPARE_API_KEY}"}
    try:
        response = requests.get(url, headers=headers, timeout=10)
        data = response.json()["Data"]
        closes = [item["close"] for item in data if "close" in item]
        if len(closes) < period + 1:
            return None

        gains = []
        losses = []
        for i in range(1, len(closes)):
            delta = closes[i] - closes[i - 1]
            if delta >= 0:
                gains.append(delta)
                losses.append(0)
            else:
                gains.append(0)
                losses.append(-delta)

        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period
        if avg_loss == 0:
            return 100.0

        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))
        return rsi
    except Exception as e:
        print(f"❌ RSI fetch failed: {e}")
        return None

    if data is valid:
        count_api_call()
        return price

async def custom(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT plan FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    plan = row[0] if row else "free"

    if plan == "free":
        await update.message.reply_text(
            "🔒 Custom alerts (Price + RSI) are for *Pro users only*.\nUse /upgrade to unlock.",
            parse_mode="Markdown"
        )
        return

    if len(context.args) < 3:
        await update.message.reply_text(
            "❌ Usage: /custom BTCUSDT >62000 rsi>70 [repeat]"
        )
        return

    symbol = context.args[0].upper()
    price_cond_raw = context.args[1]
    rsi_cond_raw = context.args[2]
    repeat_flag = 1 if len(context.args) > 3 and context.args[3].lower() == "repeat" else 0

    if not (price_cond_raw.startswith(">") or price_cond_raw.startswith("<")):
        await update.message.reply_text("❌ Price condition must start with '>' or '<'.")
        return

    # Extract price condition (e.g., >62000)
    price_condition = price_cond_raw[0]
    try:
        price_value = float(price_cond_raw[1:])
    except ValueError:
        await update.message.reply_text("❌ Invalid price format (e.g. >62000).")
        return

    # Normalize and validate the second argument
    second_arg = rsi_cond_raw.lower().strip()

    if second_arg.startswith("rsi>") or second_arg.startswith("rsi<"):
        if len(second_arg) < 5:
            await update.message.reply_text("❌ Incomplete RSI condition. Use formats like rsi>70.")
            return
        rsi_condition = second_arg[3]
        try:
            rsi_value = float(second_arg[4:])
        except ValueError:
            await update.message.reply_text("❌ Invalid RSI value. Example: rsi>70")
            return

    elif second_arg == "macd>0":
        rsi_condition = "macd"
        rsi_value = 0.0  # Placeholder, not used

    elif second_arg.startswith("ema") and ">price" in second_arg:
        try:
            period_str = second_arg[3:].split(">")[0]
            period = int(period_str)
            rsi_condition = f"ema>{period}"
            rsi_value = 0.0
        except:
            await update.message.reply_text("❌ Invalid EMA format. Example: ema20>price")
            return

    else:
        await update.message.reply_text("❌ Invalid indicator condition. Use:\n- rsi>70\n- macd>0\n- ema20>price")
        return



    cursor.execute(
        "INSERT INTO custom_alerts (user_id, symbol, price_condition, price_value, rsi_condition, rsi_value, repeat) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (user_id, symbol, price_condition, price_value, rsi_condition, rsi_value, repeat_flag)
    )
    conn.commit()
    conn.close()

    await update.message.reply_text(
        f"✅ Custom alert set for *{symbol}*:\n"
        f"• Price {price_condition} {price_value}\n"
        f"• RSI {rsi_condition} {rsi_value}\n"
        f"{'🔁 Repeat enabled' if repeat_flag else ''}",
        parse_mode="Markdown"
    )

async def addasset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if len(context.args) != 2:
        await update.message.reply_text("❌ Usage: /addasset BTC 2.5")
        return

    symbol = context.args[0].upper()
    try:
        quantity = float(context.args[1])
    except ValueError:
        await update.message.reply_text("❌ Quantity must be a number.")
        return

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute(
        "REPLACE INTO portfolio (user_id, symbol, quantity) VALUES (?, ?, ?)",
        (user_id, symbol, quantity)
    )
    conn.commit()
    conn.close()

    await update.message.reply_text(f"✅ Added {quantity} {symbol} to your portfolio.")

async def portfolio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT symbol, quantity FROM portfolio WHERE user_id = ?", (user_id,))
    assets = cursor.fetchall()
    conn.close()

    if not assets:
        await update.message.reply_text("📭 You have not added any assets. Use /addasset to begin.")
        return

    total_value = 0
    breakdown = "📊 *Your Portfolio:*\n\n"

    symbol_labels = []
    value_data = []

    for symbol, quantity in assets:
        symbol = symbol.upper()
        price = get_crypto_price(symbol)

        if price is None:
            price = get_fiat_to_usd(symbol)

        if price is None:
            continue

        value = price * quantity
        total_value += value
        breakdown += f"{symbol}: {quantity} × ${price:.4f} = ${value:,.2f}\n"

        # Collect for pie chart
        symbol_labels.append(symbol)
        value_data.append(round(value, 2))

    breakdown += f"\n💼 *Total Value:* ${total_value:,.2f}"
    await update.message.reply_text(breakdown, parse_mode="Markdown")

    if symbol_labels and value_data:
        chart_url = generate_portfolio_pie_chart(symbol_labels, value_data)
        await update.message.reply_photo(chart_url, caption="📊 Portfolio Distribution")

async def portfoliolimit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if len(context.args) != 1:
        await update.message.reply_text("❌ Usage: /portfoliolimit 15000")
        return
    try:
        limit = float(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ Must be a number.")
        return

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("INSERT OR REPLACE INTO portfolio_limits (user_id, loss_limit, profit_target) VALUES (?, ?, COALESCE((SELECT profit_target FROM portfolio_limits WHERE user_id = ?), NULL))",
                   (user_id, limit, user_id))
    conn.commit()
    conn.close()
    await update.message.reply_text(f"⚠️ Loss alert set: You'll be notified if total value drops below ${limit:,.2f}.")
async def portfoliotarget(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if len(context.args) != 1:
        await update.message.reply_text("❌ Usage: /portfoliotarget 25000")
        return
    try:
        target = float(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ Must be a number.")
        return

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("INSERT OR REPLACE INTO portfolio_limits (user_id, profit_target, loss_limit) VALUES (?, ?, COALESCE((SELECT loss_limit FROM portfolio_limits WHERE user_id = ?), NULL))",
                   (user_id, target, user_id))
    conn.commit()
    conn.close()

    await update.message.reply_text(f"🎯 Profit alert set: You'll be notified if value exceeds ${target:,.2f}.")

async def removeasset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if len(context.args) != 1:
        await update.message.reply_text("❌ Usage: /removeasset BTC")
        return

    symbol = context.args[0].upper()

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM portfolio WHERE user_id = ? AND symbol = ?", (user_id, symbol))
    affected = cursor.rowcount
    conn.commit()
    conn.close()

    if affected:
        await update.message.reply_text(f"🗑 Removed {symbol} from your portfolio.")
    else:
        await update.message.reply_text("⚠️ You don't have that asset in your portfolio.")
async def resetportfolio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM portfolio WHERE user_id = ?", (user_id,))
    cursor.execute("DELETE FROM portfolio_limits WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()

    await update.message.reply_text("🔄 Your entire portfolio has been reset.")

def get_fiat_to_usd(symbol):
    try:
        url = f"https://v6.exchangerate-api.com/v6/{EXCHANGE_RATE_API_KEY}/latest/{symbol.upper()}"
        response = requests.get(url, timeout=10)
        data = response.json()
        rate = data["conversion_rates"].get("USD")
        return 1 / rate if rate else None
    except Exception as e:
        print(f"Fiat conversion error: {e}")
        return None

def get_candles(symbol, limit=100):
    url = f"https://min-api.cryptocompare.com/data/histohour?fsym={symbol.upper()}&tsym=USD&limit={limit}"
    headers = {"authorization": f"Apikey {CRYPTOCOMPARE_API_KEY}"}
    try:
        response = requests.get(url, headers=headers, timeout=10)
        data = response.json()
        return [item["close"] for item in data.get("Data", [])]
    except Exception as e:
        print(f"Error fetching candles: {e}")
        return []

    if data is valid:
        count_api_call()
        return price

def calculate_ema(prices, period):
    if len(prices) < period:
        return None
    ema = prices[:period]
    multiplier = 2 / (period + 1)
    for price in prices[period:]:
        ema_val = (price - ema[-1]) * multiplier + ema[-1]
        ema.append(ema_val)
    return ema[-1]

def get_macd(symbol):
    prices = get_candles(symbol, 50)
    if len(prices) < 26:
        return None, None, None

    ema_12 = calculate_ema(prices, 12)
    ema_26 = calculate_ema(prices, 26)

    if ema_12 is None or ema_26 is None:
        return None, None, None

    macd = ema_12 - ema_26
    macd_line = [calculate_ema(prices[i:], 12) - calculate_ema(prices[i:], 26) for i in range(9)]
    signal_line = sum(macd_line) / len(macd_line)
    hist = macd - signal_line
    return macd, signal_line, hist

async def signal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    username = update.effective_user.username or "anonymous"

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT plan FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    plan = row[0] if row else "free"

    if plan == "free":
        await update.message.reply_text("🔒 Only *Pro users* can submit trade signals. Use /upgrade to unlock.", parse_mode="Markdown")
        return

    if len(context.args) < 2:
        await update.message.reply_text("❌ Usage: /signal BTCUSDT >62000 sl=60000 tp=68000")
        return

    symbol = context.args[0].upper()
    direction_raw = context.args[1]
    if not (direction_raw.startswith(">") or direction_raw.startswith("<")):
        await update.message.reply_text("❌ Entry condition must start with '>' or '<'.")
        return

    try:
        entry_price = float(direction_raw[1:])
    except:
        await update.message.reply_text("❌ Invalid entry price.")
        return

    # Parse optional SL/TP
    stop_loss = None
    take_profit = None
    for arg in context.args[2:]:
        if arg.startswith("sl="):
            try: stop_loss = float(arg[3:])
            except: pass
        elif arg.startswith("tp="):
            try: take_profit = float(arg[3:])
            except: pass
    cursor.execute("""
    INSERT INTO trade_signals (user_id, symbol, direction, entry_price, stop_loss, take_profit, timestamp, approved)
    VALUES (?, ?, ?, ?, ?, ?, ?, 0)
""", (user_id, symbol, direction_raw[0], entry_price, stop_loss, take_profit, datetime.utcnow().isoformat()))
    conn.commit()
    conn.close()

    await update.message.reply_text("✅ Signal submitted. Pro users will see it in /signals.")

async def signals(update: Update, context: ContextTypes.DEFAULT_TYPE):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT symbol, direction, entry_price, stop_loss, take_profit, timestamp, user_id
        FROM trade_signals WHERE approved = 1 ORDER BY id DESC LIMIT 5
    """)
    rows = cursor.fetchall()
    conn.close()

    if not rows:
        await update.message.reply_text("📭 No recent signals available.")
        return

    messages = []
    for row in rows:
        symbol, direction, price, sl, tp, ts, uid = row
        sl_text = f"\n🛑 SL: ${sl:.2f}" if sl else ""
        tp_text = f"\n🎯 TP: ${tp:.2f}" if tp else ""
        timestamp = datetime.fromisoformat(ts).strftime("%Y-%m-%d %H:%M UTC")
        messages.append(
            f"*{symbol}* Signal – {timestamp}\n"
            f"📈 Entry: `{direction} {price:.2f}`{sl_text}{tp_text}\n"
            f"👤 User: `{uid}`"
        )

    await update.message.reply_text("\n\n".join(messages), parse_mode="Markdown")

async def approvesignals(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ You are not authorized.")
        return

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT id, user_id, symbol, direction, entry_price, stop_loss, take_profit FROM trade_signals WHERE approved = 0 LIMIT 5")
    rows = cursor.fetchall()
    conn.close()

    if not rows:
        await update.message.reply_text("📭 No pending signals.")
        return

    for row in rows:
        signal_id, user_id, symbol, direction, price, sl, tp = row
        sl_text = f"🛑 SL: ${sl}" if sl else ""
        tp_text = f"🎯 TP: ${tp}" if tp else ""

        text = (
            f"📝 *Signal #{signal_id}* by `{user_id}`\n\n"
            f"*{symbol}* – {direction} {price}\n"
            f"{sl_text}\n{tp_text}"
        )

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Approve", callback_data=f"approve_{signal_id}"),
                InlineKeyboardButton("❌ Reject", callback_data=f"reject_{signal_id}")
            ]
        ])

        await update.message.reply_text(text, parse_mode="Markdown", reply_markup=keyboard)

async def handle_signal_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    if user_id != ADMIN_ID:
        await query.edit_message_text("❌ You are not authorized.")
        return

    action, sid = query.data.split("_")
    sid = int(sid)

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    if action == "approve":
        cursor.execute("SELECT symbol, direction, entry_price, stop_loss, take_profit FROM trade_signals WHERE id = ?", (sid,))
        signal = cursor.fetchone()
        if not signal:
            await query.edit_message_text("⚠️ Signal not found.")
            return

        symbol, direction, entry, sl, tp = signal

        cursor.execute("UPDATE trade_signals SET approved = 1 WHERE id = ?", (sid,))
        conn.commit()

    # 📡 Send to channel
        sl_text = f"\n🛑 SL: ${sl}" if sl else ""
        tp_text = f"\n🎯 TP: ${tp}" if tp else ""
        message = (
            f"🚨 *New Signal Alert: {symbol}*\n\n"
            f"📈 Entry: `{direction} {entry}`\n"
            f"{sl_text}\n{tp_text}\n\n"
            f"📡 From: @PricePulseBot\n"
            f"🔔 Follow for more signals!"
        )


        try:
            await context.bot.send_message(chat_id=SIGNAL_CHANNEL_ID, text=message, parse_mode="Markdown")
        except Exception as e:
            print(f"Broadcast error: {e}")

        await query.edit_message_text(f"✅ Signal #{sid} approved and posted to channel.")


    conn.close()

async def myalerts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    # Count alerts per type
    cursor.execute("SELECT COUNT(*) FROM alerts WHERE user_id = ?", (user_id,))
    price_count = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM percent_alerts WHERE user_id = ?", (user_id,))
    percent_count = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM volume_alerts WHERE user_id = ?", (user_id,))
    volume_count = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM risk_alerts WHERE user_id = ?", (user_id,))
    risk_count = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM custom_alerts WHERE user_id = ?", (user_id,))
    custom_count = cursor.fetchone()[0]

    conn.close()

    # Build summary message
    summary = (
        "📋 *Your Alert Summary:*\n\n"
        f"• Price Alerts: `{price_count}`\n"
        f"• % Change Alerts: `{percent_count}`\n"
        f"• Volume Spike Alerts: `{volume_count}`\n"
        f"• Risk Alerts (SL/TP): `{risk_count}`\n"
        f"• Custom Alerts (Price + RSI): `{custom_count}`\n"
    )

    # Inline navigation buttons
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔔 View Price Alerts", url="https://t.me/EliteTradeSignalBot?start=alerts")],
        [InlineKeyboardButton("📉 % Alerts", callback_data="show_percent_alerts"),
         InlineKeyboardButton("📊 Volume Alerts", callback_data="show_volume_alerts")],
        [InlineKeyboardButton("🛡 Risk Alerts", callback_data="show_risk_alerts"),
         InlineKeyboardButton("🧠 Custom Alerts", callback_data="show_custom_alerts")]
    ])

    await update.message.reply_text(summary, parse_mode="Markdown", reply_markup=keyboard)

async def forward_alert_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    command_map = {
        "show_percent_alerts": "/percentalerts",
        "show_volume_alerts": "/volumealerts",
        "show_risk_alerts": "/riskalerts",
        "show_custom_alerts": "/customalerts"
    }

    command = command_map.get(query.data)
    if command:
        fake_update = update
        fake_context = context
        await context.bot.delete_message(chat_id=query.message.chat_id, message_id=query.message.message_id)
        await context.bot.send_message(chat_id=query.message.chat_id, text=command)

async def toggle_menu_features(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "show_pro_features":
        text = (
            "💎 *Pro Features:*\n\n"
            "• ♾️ Unlimited alerts (no 3-alert cap)\n"
            "• 🔁 Persistent alerts (auto-resend)\n"
            "• ✏️ Edit individual alerts\n"
            "• 📊 Trend indicators (RSI, MACD, EMA)\n"
            "• 📉 % Change alerts (e.g. BTC -5%)\n"
            "• 📊 Volume spike alerts\n"
            "• 🛑 Risk alerts (SL / TP triggers)\n"
            "• 💼 Portfolio tracking + value alerts\n"
            "• 🧠 Custom alert conditions (price + RSI)\n"
            "• 🤝 Submit & view trading signals\n"
        )
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("⬅️ Back to Free Features", callback_data="show_free_features")],
            [InlineKeyboardButton("🚀 Upgrade to Pro", url="https://t.me/EliteTradeSignalBot?start=upgrade")]
        ])
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=keyboard)

    elif query.data == "show_free_features":
        text = (
            "🟢 *Free Features:*\n\n"
            "• 🔔 Create up to 3 price alerts\n"
            "• 📋 View & clear alerts\n"
            "• 📈 Top Gainers / 📉 Top Losers\n"
            "• 📰 Latest crypto news\n"
            "• 💰 Check current price\n"
            "• 🧠 Group support (manual commands)\n"
        )
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔓 Show Pro Features", callback_data="show_pro_features")],
            [InlineKeyboardButton("🆘 Help Guide", url="https://t.me/EliteTradeSignalBot?start=help")],
            [InlineKeyboardButton("🚀 Upgrade to Pro", url="https://t.me/EliteTradeSignalBot?start=upgrade")]
        ])
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=keyboard)

async def show_user_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()  # Acknowledge the button tap
    user_id = query.from_user.id
    await query.edit_message_text(f"🧾 *Your Telegram User ID is:* `{user_id}`", parse_mode="Markdown")


async def menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    menu_text = (
        "🟢 *Free Features:*\n\n"
        "• 🔔 Create up to 3 price alerts\n"
        "• 📋 View & clear alerts\n"
        "• 📈 Top Gainers / 📉 Top Losers\n"
        "• 📰 Latest crypto news\n"
        "• 💰 Check current price\n"
        "• 🧠 Group support (manual commands)\n"
    )

    keyboard = InlineKeyboardMarkup([ 
    [InlineKeyboardButton("🔓 Show Pro Features", callback_data="show_pro_features")],
    [InlineKeyboardButton("🆘 Help Guide", url="https://t.me/EliteTradeSignalBot?start=help")],   
    [InlineKeyboardButton("🚀 Upgrade to Pro", url="https://t.me/EliteTradeSignalBot?start=upgrade")]
])


    await update.message.reply_text(menu_text, parse_mode="Markdown", reply_markup=keyboard)

async def how_to_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    bot_username = context.bot.username

    message = (
        "🤖 *How to Add PricePulseBot to a Group:*\n\n"
        "1. Open your Telegram group.\n"
        "2. Tap the group name at the top.\n"
        "3. Choose *'Add Members'* or *'Invite to Group'*.\n"
        f"4. Search for `@{bot_username}` and tap to add it.\n"
        "5. After adding, make sure to *give the bot admin rights* if you want it to send alerts automatically.\n\n"
        "🧠 *Tip:* Use the button below to invite the bot directly to any of your groups."
    )

    invite_button = InlineKeyboardMarkup([[
        InlineKeyboardButton("➕ Add to Group", url=f"https://t.me/{bot_username}?startgroup=true")
    ]])

    await update.message.reply_text(message, parse_mode="Markdown", reply_markup=invite_button)

async def upgrade(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("👤 Show My Telegram User ID", callback_data="show_user_id")]
    ])

    await update.message.reply_text(
        "🚀 *Upgrade to PricePulse Pro — Trade Smarter, Win Bigger!*\n\n"
        "💹 Stop trading blind. Unlock intelligent alerts, advanced indicators, and your personal signal assistant — all inside Telegram.\n\n"
        "💎 *Pro Plan Options:*\n"
        "• 🌍 *Global (Crypto)* – **$4.99/month**\n"
        "• 🇳🇬 *Nigeria (Bank Transfer)* – **₦3,000/month**\n\n"
        "*Includes ALL Pro Features:*\n"
        "• ♾️ Unlimited alerts (remove 3-alert cap)\n"
        "• 🔁 Persistent alerts (auto-resend until conditions reset)\n"
        "• ✏️ Edit individual alerts easily\n"
        "• 📊 Indicators: RSI, MACD, EMA-based triggers\n"
        "• 📉 % Change, Volume Spike & Custom alerts\n"
        "• 🛑 Risk Alerts (Stop Loss / Take Profit)\n"
        "• 💼 Portfolio tracking + value triggers\n"
        "• 🤝 Submit signals, broadcast to community\n"
        "• 🥇 Early access to future tools\n\n"
        "🔥 *LIMITED OFFER – First Month Only ₦2,000!* (Nigerian users)\n"
        "Start now. Results begin with action.\n\n"
        "💳 *Pay via Bank Transfer (Nigeria Only):*\n"
        "`Bank:` Opay\n"
        "`Account Name:` MAIMUNAT AL-AMIN YARO\n"
        "`Account Number:` 8068446778\n\n"
        "🪙 *Pay via USDT (TRC20 Network):*\n"
        "`TQHw2F63cC8QoyUR5iCLhWfUzvNmvqdwej`\n"
        "_Send exactly $4.99 USDT (TRC20). Use only this network._\n\n"
        "📩 *After Payment:*\n"
        "Send proof + your Telegram User ID to [@PricePulseDev](https://t.me/PricePulseDev)\n"
        "We’ll activate your Pro access within 5–15 minutes.\n\n"
        "Thank you for supporting *PricePulseBot* — the future of smart trading. 🔥",
        parse_mode="Markdown",
        disable_web_page_preview=True,
        reply_markup=keyboard

    )

    


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = """🆘 *PricePulseBot Help Menu*

Welcome to your all-in-one crypto alert assistant. Here’s how to use the bot effectively:

━━━━━━━━━━━━━━━━━━━
🟢 *FREE FEATURES*
━━━━━━━━━━━━━━━━━━━

🔔 `/set BTCUSDT >70000` — Set a price alert  
📋 `/alerts` — View your active alerts  
🗑 `/remove <ID>` — Remove an alert by ID  
🧹 `/clear` — Clear all your alerts  
✏️ `/edit` — Edit existing alert  

💰 `/price BTCUSDT` — Get live price  
📈 `/best` — Top 3 Gainers (24h)  
📉 `/worst` — Top 3 Losers (24h)  
📰 `/news` — Crypto news headlines  
🧠 `/menu` — View feature overview  

━━━━━━━━━━━━━━━━━━━
💎 *PRO FEATURES* (`₦3,000 or $4.99/month`)
━━━━━━━━━━━━━━━━━━━

📉 `/percent BTCUSDT 5 repeat` — % move alerts (±5%)  
📋 `/percentalerts` — View % alerts  
🗑 `/removepercent <ID>`

📊 `/volume BTCUSDT 2 repeat` — Volume spike alerts (e.g. 2× avg)  
📋 `/volumealerts` — View volume alerts  
🗑 `/removevolume <ID>`

🛑 `/risk BTCUSDT 30000 33000 repeat` — SL/TP risk alerts  
📋 `/riskalerts` — View risk alerts  
🗑 `/removerisk <ID>`

🧠 `/custom BTCUSDT >60000 rsi>70 repeat` — Smart combo alerts  
📋 `/customalerts` — View custom alerts  
🗑 `/removecustom <ID>`

💼 `/addasset BTC 1.2` — Add to portfolio  
📊 `/portfolio` — Portfolio valuation  
⚠️ `/portfoliolimit 15000` — Loss alert  
🎯 `/portfoliotarget 25000` — Profit goal alert  
🗑 `/removeasset BTC` — Remove from portfolio  
🔄 `/resetportfolio` — Clear your portfolio

📢 `/signal BTCUSDT >70000 sl=65000 tp=75000` — Submit signal  
📈 `/signals` — View approved signals  

━━━━━━━━━━━━━━━━━━━
🚀 *UPGRADE TO PRO*
━━━━━━━━━━━━━━━━━━━

Use `/upgrade` for secure payment options.  
• 🌍 Global (Crypto): **$4.99/month**  
• 🇳🇬 Nigeria (Bank): **₦3,000/month**  
_First month ₦2,000 promo available!_

━━━━━━━━━━━━━━━━━━━
🔗 *Quick Access*
━━━━━━━━━━━━━━━━━━━
• `/menu` — Feature overview  
• `/myalerts` — Alert summary  
• `/howtoadd` — Add bot to group  
• `/help` — This guide

ℹ️ For support, contact: [@PricePulseDev](https://t.me/PricePulseDev)
"""

    await update.message.reply_text(help_text, parse_mode="Markdown")


  # your Telegram user ID


async def setplan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.chat_id

    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ You are not authorized.")
        return

    if len(context.args) < 2:
        await update.message.reply_text("❌ Usage: /setplan USER_ID PLAN (e.g., /setplan 123456 pro)")
        return

    target_id = int(context.args[0])
    plan = context.args[1].lower()

    if plan not in ["free", "pro", "vip"]:
        await update.message.reply_text("❌ Plan must be: free, pro, or vip.")
        return

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET plan = ? WHERE user_id = ?",
                   (plan, target_id))
    conn.commit()
    conn.close()

    await update.message.reply_text(f"✅ Plan for {target_id} set to {plan}.")

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return

    chat_id = update.effective_chat.id
    now = datetime.utcnow()
    uptime = now - bot_start_time

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    # Create referrals table if it doesn't exist
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS referrals (
            source TEXT PRIMARY KEY,
            clicks INTEGER DEFAULT 0
        )
    """)

    # --- Referral Data ---
    cursor.execute("SELECT source, clicks FROM referrals ORDER BY clicks DESC LIMIT 5")
    referrals = cursor.fetchall()

    # --- Alerts ---
    cursor.execute("SELECT COUNT(*) FROM alerts")
    price_alerts = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM alerts WHERE repeat = 1")
    persistent_alerts = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM percent_alerts")
    percent_alerts = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM volume_alerts")
    volume_alerts = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM risk_alerts")
    risk_alerts = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM custom_alerts")
    custom_alerts = cursor.fetchone()[0]

    total_alerts = price_alerts + percent_alerts + volume_alerts + risk_alerts + custom_alerts

    # --- Users ---
    cursor.execute("SELECT COUNT(*) FROM users")
    total_users = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM users WHERE pro = 1")
    pro_users = cursor.fetchone()[0]
    free_users = total_users - pro_users

    cursor.execute("SELECT COUNT(*) FROM users WHERE last_reset = DATE('now')")
    new_users_today = cursor.fetchone()[0]

    conn.close()

    # --- Derived Metric ---
    conversion_rate = (pro_users / total_users) * 100 if total_users else 0

    # --- Build Message ---
    text = "📊 *PricePulseBot Stats*\n\n"

    if referrals:
        text += "🔗 *Top Referral Sources:*\n"
        for source, clicks in referrals:
            text += f"• `{source}`: {clicks} clicks\n"
    else:
        text += "🔗 No referral data yet.\n"

    text += (
        f"\n👥 *Users:* `{total_users}` total\n"
        f"  ┗ Free: `{free_users}` | Pro: `{pro_users}`\n"
        f"  ┗ New Today: `{new_users_today}`\n\n"
        f"📡 *Alerts:* `{total_alerts}` total\n"
        f"  ┗ Price: {price_alerts} (🔁 {persistent_alerts})\n"
        f"  ┗ %: {percent_alerts}, Volume: {volume_alerts}, Risk: {risk_alerts}, Custom: {custom_alerts}\n\n"
        f"💹 *Pro Conversion Rate:* `{conversion_rate:.2f}%`\n"
    )

    await context.bot.send_message(chat_id=chat_id, text=text, parse_mode="Markdown")


# ✅ Main Bot Function


async def main():
    print("🚀 Bot is running...")
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("price", price))
    app.add_handler(CommandHandler("set", set_price_alert))
    app.add_handler(CommandHandler("remove", remove))
    app.add_handler(CommandHandler("percentalerts", percentalerts))
    app.add_handler(CommandHandler("removepercent", removepercent))
    app.add_handler(CommandHandler("volumealerts", volumealerts))
    app.add_handler(CommandHandler("removevolume", removevolume))
    app.add_handler(CommandHandler("riskalerts", riskalerts))
    app.add_handler(CommandHandler("removerisk", removerisk))
    app.add_handler(CommandHandler("customalerts", customalerts))
    app.add_handler(CommandHandler("removecustom", removecustom))
    app.add_handler(CommandHandler("myalerts", myalerts))
    app.add_handler(CallbackQueryHandler(forward_alert_command, pattern="^(show_percent_alerts|show_volume_alerts|show_risk_alerts|show_custom_alerts)$"))
    app.add_handler(CommandHandler("trend", trend))
    app.add_handler(CommandHandler("alerts", alerts))
    app.add_handler(CommandHandler("upgrade", upgrade))
    app.add_handler(CommandHandler("setplan", setplan))
    app.add_handler(CommandHandler("best", best))
    app.add_handler(CommandHandler("worst", worst))
    app.add_handler(CommandHandler("news", news))
    app.add_handler(CommandHandler("menu", menu))
    app.add_handler(CommandHandler("clear", clear_alerts))
    app.add_handler(CommandHandler("clear", clear_alerts_prompt))
    app.add_handler(CommandHandler("howtoadd", how_to_add))
    app.add_handler(CommandHandler("percent", percent))
    app.add_handler(CommandHandler("volume", volume))
    app.add_handler(CommandHandler("risk", risk))
    app.add_handler(CommandHandler("custom", custom))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(CommandHandler("addasset", addasset))
    app.add_handler(CommandHandler("portfolio", portfolio))
    app.add_handler(CommandHandler("portfoliolimit", portfoliolimit))
    app.add_handler(CommandHandler("portfoliotarget", portfoliotarget))
    app.add_handler(CommandHandler("removeasset", removeasset))
    app.add_handler(CommandHandler("resetportfolio", resetportfolio))
    app.add_handler(CallbackQueryHandler(toggle_menu_features, pattern="^(show_pro_features|show_free_features)$"))
    app.add_handler(CommandHandler("signal", signal))
    app.add_handler(CommandHandler("signals", signals))
    app.add_handler(CommandHandler("approvesignals", approvesignals))
    app.add_handler(CallbackQueryHandler(handle_signal_action, pattern="^(approve|reject)_\\d+$"))
    app.add_handler(CallbackQueryHandler(clear_alerts_callback, pattern="^(confirm_clear|cancel_clear)$"))
    app.add_handler(
    ConversationHandler(
        entry_points=[CommandHandler("edit", edit_alert_start)],
        states={
            EDIT_SELECT: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_alert_select)],
            EDIT_UPDATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_alert_update)],
        },
        fallbacks=[],
    )
)
    app.add_handler(CallbackQueryHandler(show_user_id, pattern="^show_user_id$"))

    app.job_queue.run_repeating(check_alerts, interval=60, first=10)

    await app.run_polling()


# ✅ Corrected AsyncIO Handling
if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("🛑 Shutdown requested (KeyboardInterrupt)")
    finally:
        cleanup_pid()
        print("✅ PID file removed. Shutdown complete.")



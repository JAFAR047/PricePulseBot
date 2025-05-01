from datetime import datetime, timedelta
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from telegram import Bot
from dotenv import load_dotenv
import asyncio
import requests
import sqlite3
import os
import nest_asyncio

nest_asyncio.apply()

# ✅ Load environment variables
load_dotenv()
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not TELEGRAM_BOT_TOKEN:
    raise ValueError("❌ TELEGRAM_BOT_TOKEN is missing! Check your .env file.")

bot = Bot(token=TELEGRAM_BOT_TOKEN)

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

    conn.commit()
    conn.close()

    print("✅ Database initialized!")


# Call init_db once at startup
init_db()

# ✅ Fetch Prices


def get_crypto_price(symbol="bitcoin"):
    url = f"https://api.coingecko.com/api/v3/simple/price?ids={symbol.lower()}&vs_currencies=usd"
    try:
        response = requests.get(url)
        response.raise_for_status()
        data = response.json()
        return data.get(symbol.lower(), {}).get('usd', None)
    except Exception as e:
        print(f"Error fetching crypto price for {symbol}: {e}")
        return None


def get_forex_price(pair="EURUSD"):
    base_currency = pair[:3]
    quote_currency = pair[3:]
    url = f"https://v6.exchangerate-api.com/v6/{EXCHANGE_RATE_API_KEY}/latest/{base_currency}"
    try:
        response = requests.get(url)
        response.raise_for_status()
        data = response.json()
        price = data.get("conversion_rates", {}).get(quote_currency)
        return price
    except Exception as e:
        print(f"Error fetching forex price for {pair}: {e}")
        return None

# ✅ Telegram Bot Handlers


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.chat_id
    await update.message.reply_text(f"👋 Welcome! Your Telegram User ID is: `{user_id}`")


async def crypto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) == 0:
        await update.message.reply_text("❌ Please specify a crypto pair (e.g., /crypto bitcoin)")
        return
    symbol = context.args[0].lower()
    price = get_crypto_price(symbol)
    if price:
        await update.message.reply_text(f"💰 {symbol.upper()} Price: ${price}")
    else:
        await update.message.reply_text("❌ Error fetching crypto price.")


async def forex(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) == 0:
        await update.message.reply_text("❌ Please specify a forex pair (e.g., /forex EURUSD)")
        return
    pair = context.args[0].upper()
    price = get_forex_price(pair)
    if price:
        await update.message.reply_text(f"💱 {pair} Price: {price}")
    else:
        await update.message.reply_text("❌ Error fetching forex price.")


async def set_alert(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 3:
        await update.message.reply_text("❌ Usage: /setalert BTCUSDT > 70000 OR /setalert EURUSD < 1.20")
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

    # Insert new alert
    cursor.execute(
        "INSERT INTO alerts (user_id, symbol, condition, target_price) VALUES (?, ?, ?, ?)",
        (user_id, symbol, condition, target_price)
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

# ✅ Helper functions for Trend


async def remove_alert(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) == 0:
        await update.message.reply_text("❌ Usage: /removealert <ALERT_ID>\nExample: /removealert 3")
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


def get_crypto_trend(symbol, timeframe):
    url = f"https://api.coingecko.com/api/v3/coins/{symbol.lower()}/market_chart?vs_currency=usd&days=7&interval=daily"
    try:
        response = requests.get(url)
        response.raise_for_status()
        data = response.json()

        prices = data.get("prices", [])
        if not prices:
            return None

        current_price = prices[-1][1]  # Latest price

        # Normalize timeframe to uppercase for mapping consistency
        timeframe = timeframe.upper()

        # Timeframe mapping - adjust based on available data
        timeframe_mapping = {
            "1H": -2,
            "4H": -5,
            "12H": -13,
            "24H": -25,
            "7D": 0,
        }

        if timeframe in timeframe_mapping:
            index = timeframe_mapping[timeframe]
            if abs(index) >= len(prices):
                return None
            old_price = prices[index][1]
        else:
            return None

        # Calculate percentage change
        return ((current_price - old_price) / old_price) * 100
    except Exception as e:
        print(f"Error fetching crypto trend for {symbol}: {e}")
        return None


def get_forex_trend(pair, timeframe):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    # Normalize timeframe input to lower case for mapping consistency
    timeframe = timeframe.lower()

    time_deltas = {
        "1h": "1 hour",
        "4h": "4 hours",
        "12h": "12 hours",
        "1d": "1 day",
        "1w": "7 days"
    }

    if timeframe not in time_deltas:
        conn.close()
        return None

    cursor.execute(
        "SELECT price FROM forex_prices WHERE pair = ? ORDER BY timestamp DESC LIMIT 1", (pair,))
    latest_price = cursor.fetchone()
    if not latest_price:
        conn.close()
        return None

    latest_price = latest_price[0]

    cursor.execute(f'''
        SELECT price FROM forex_prices 
        WHERE pair = ? 
        AND timestamp <= datetime('now', '-{time_deltas[timeframe]}') 
        ORDER BY timestamp DESC 
        LIMIT 1
    ''', (pair,))

    past_price = cursor.fetchone()
    conn.close()

    if not past_price:
        return None

    past_price = past_price[0]

    return ((latest_price - past_price) / past_price) * 100


async def trend(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.chat_id

    # 🔍 Check user plan
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT plan FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()

    plan = row[0] if row else "free"

    if plan == "free":
        await update.message.reply_text(
            "🔒 Trend analysis is only available for Pro/VIP users.\n\n"
            "💡 Use /upgrade to unlock this feature."
        )
        return

    # ✅ Check args
    if len(context.args) < 2:
        await update.message.reply_text(
            "❌ Usage: /trend SYMBOL TIMEFRAME\n"
            "Example: /trend BTCUSDT 1H or /trend EURUSD 1D"
        )
        return

    symbol = context.args[0].upper()
    timeframe = context.args[1].upper()

    # 🧠 Decide crypto or forex based on symbol format
    if "USD" in symbol and len(symbol) == 6:
        change = get_forex_trend(symbol, timeframe)
    else:
        change = get_crypto_trend(symbol, timeframe)

    if change is None:
        await update.message.reply_text(
            f"⚠️ Could not retrieve trend data for `{symbol}` using `{timeframe}` timeframe.\n"
            "Please check symbol/timeframe format.",
            parse_mode="Markdown"
        )
        return

    direction = "📈 Increased" if change > 0 else "📉 Decreased"
    await update.message.reply_text(
        f"📊 *{symbol}* trend over `{timeframe}`:\n"
        f"{direction} by `{abs(change):.2f}%`",
        parse_mode="Markdown"
    )


async def check_alerts(context: ContextTypes.DEFAULT_TYPE):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, user_id, symbol, condition, target_price, repeat FROM alerts")
    alerts = cursor.fetchall()

    for alert_id, user_id, symbol, condition, target_price, repeat in alerts:
        # Determine if it's crypto or forex
        if "USD" in symbol and len(symbol) == 6:
            price = get_forex_price(symbol)
        else:
            price = get_crypto_price(symbol)

        if price is None:
            continue  # Skip if price fetch failed

        if (condition == ">" and price >= target_price) or (condition == "<" and price <= target_price):
            try:
                await context.bot.send_message(
                    chat_id=user_id,
                    text=f"🚨 Alert Triggered!\n\n{symbol} price is {price} (condition: {condition} {target_price})"
                )
            except Exception as e:
                print(f"Error sending alert to {user_id}: {e}")

            if repeat == 0:
                cursor.execute("DELETE FROM alerts WHERE id = ?", (alert_id,))
                conn.commit()

    conn.close()


async def my_alerts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.chat_id
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, symbol, condition, target_price, repeat FROM alerts WHERE user_id = ?", (user_id,))
    alerts = cursor.fetchall()
    conn.close()

    if not alerts:
        await update.message.reply_text("📭 You have no active alerts.")
        return

    text = "\n".join([
        f"#{alert_id}: {symbol} {condition} {target_price} {'🔁' if repeat else ''}"
        for alert_id, symbol, condition, target_price, repeat in alerts
    ])
    await update.message.reply_text(f"📋 Your Alerts:\n{text}")


async def upgrade(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🚀 *Upgrade to Pro/VIP Today!*\n\n"
        "🔹 *Pro Plan* (₦2,000/month):\n"
        "   - Unlimited Alerts\n"
        "   - Trend Analysis\n\n"
        "🌟 *VIP Plan* (₦5,000/month):\n"
        "   - Everything in Pro\n"
        "   - VIP Trade Signals (Daily)\n"
        "   - Priority 1-on-1 Support\n\n"
        "💳 Pay via Bank Transfer:\n"
        "`Bank`: Opay\n"
        "`Name`: MAIMUNAT AL-AMIN YARO\n"
        "`Account`: 8068446778\n\n"
        "📩 After payment, send your screenshot to @uncannyvintage for upgrade.",
        parse_mode="Markdown"
    )


ADMIN_ID = 5633927235  # your Telegram user ID


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

async def post_init(app):
    app.job_queue.run_repeating(check_alerts, interval=60, first=10)
    # Add other background tasks here too

# ✅ Main Bot Function


async def main():
    print("🚀 Bot is running...")
    
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).post_init(post_init).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("crypto", crypto))
    app.add_handler(CommandHandler("forex", forex))
    app.add_handler(CommandHandler("setalert", set_alert))
    app.add_handler(CommandHandler("removealert", remove_alert))
    app.add_handler(CommandHandler("trend", trend))
    app.add_handler(CommandHandler("myalerts", my_alerts))
    app.add_handler(CommandHandler("upgrade", upgrade))

    app.job_queue.run_repeating(check_alerts, interval=60, first=10)

    await app.run_polling()


# ✅ Corrected AsyncIO Handling
if __name__ == "__main__":
    asyncio.run(main())

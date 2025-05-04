from datetime import datetime, timedelta
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from telegram import Bot
from telegram.ext import CallbackQueryHandler
from dotenv import load_dotenv
import asyncio
import requests
import sqlite3
import os
import aiohttp
from telegram.ext import ConversationHandler, MessageHandler, filters
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
import nest_asyncio

nest_asyncio.apply()

EDIT_SELECT, EDIT_UPDATE = range(2)

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


# ✅ Load environment variables
load_dotenv()
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not TELEGRAM_BOT_TOKEN:
    raise ValueError("❌ TELEGRAM_BOT_TOKEN is missing! Check your .env file.")
bot = Bot(token=TELEGRAM_BOT_TOKEN)
CRYPTOCOMPARE_API_KEY = os.getenv("CRYPTOCOMPARE_API_KEY")
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

    cursor.execute("PRAGMA table_info(users)")
    columns = [col[1] for col in cursor.fetchall()]
    if "plan" not in columns:
     cursor.execute("ALTER TABLE users ADD COLUMN plan TEXT DEFAULT 'free'")


    conn.commit()
    conn.close()

    print("✅ Database initialized!")



# Call init_db once at startup
init_db()



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






# ✅ Telegram Bot Handlers


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if args:
        command = args[0]
        fake_update = update  # reuse the same update
        fake_context = context

        if command == "set":
            return await set(fake_update, fake_context)
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

    # Default welcome message if no args
    await update.message.reply_text(
        "👋 Welcome to PricePulseBot!\nUse /menu to get started."
    )


async def price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) == 0:
        await update.message.reply_text("❌ Please specify a crypto pair (e.g., /crypto bitcoin)")
        return
    symbol = context.args[0].lower()
    price = get_crypto_price(symbol)
    if price:
        await update.message.reply_text(f"💰 {symbol.upper()} Price: ${price}")
    else:
        await update.message.reply_text("❌ Error fetching crypto price.")




async def set(update: Update, context: ContextTypes.DEFAULT_TYPE):
    
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



async def clear_alerts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    # Delete all alerts for this user
    cursor.execute("DELETE FROM alerts WHERE user_id = ?", (user_id,))
    conn.commit()

    # Check if there are any alerts left at all
    cursor.execute("SELECT COUNT(*) FROM alerts")
    total_alerts = cursor.fetchone()[0]

    # If database is completely empty, reset the AUTOINCREMENT sequence
    if total_alerts == 0:
        cursor.execute("DELETE FROM sqlite_sequence WHERE name='alerts'")
        conn.commit()

    conn.close()

    await update.message.reply_text("🧹 All your alerts have been cleared.")


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
        cursor.execute("DELETE FROM alerts WHERE user_id = ?", (user_id,))
        conn.commit()

        # Check if any alerts remain in DB
        cursor.execute("SELECT COUNT(*) FROM alerts")
        total_alerts = cursor.fetchone()[0]
        if total_alerts == 0:
            cursor.execute("DELETE FROM sqlite_sequence WHERE name='alerts'")
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


async def show_user_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()  # Acknowledge the button tap
    user_id = query.from_user.id
    await query.edit_message_text(f"🧾 *Your Telegram User ID is:* `{user_id}`", parse_mode="Markdown")


async def menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    menu_text = """🤖 *Welcome to PricePulseBot*

Choose a feature below:
"""

    keyboard = [
        [
            InlineKeyboardButton("➕ Create Alert", url="https://t.me/EliteTradeSignalBot?start=set"),
            InlineKeyboardButton("📋 My Alerts", url="https://t.me/EliteTradeSignalBot?start=alerts")
        ],
        [
            InlineKeyboardButton("✏️ Edit Alert", url="https://t.me/EliteTradeSignalBot?start=edit"),
            InlineKeyboardButton("🧹 Clear Alerts", url="https://t.me/EliteTradeSignalBot?start=clear")
        ],
        [
            InlineKeyboardButton("📈 Top Gainers", url="https://t.me/EliteTradeSignalBot?start=best"),
            InlineKeyboardButton("📉 Top Losers", url="https://t.me/EliteTradeSignalBot?start=worst")
        ],
        [
            InlineKeyboardButton("📊 Price Trend", url="https://t.me/EliteTradeSignalBot?start=trend"),
            InlineKeyboardButton("💰 Check Price", url="https://t.me/EliteTradeSignalBot?start=price")
        ],
        [
            InlineKeyboardButton("📰 Crypto News", url="https://t.me/EliteTradeSignalBot?start=news"),
            InlineKeyboardButton("🔎 User ID", callback_data="show_user_id")
        ],
         [
        InlineKeyboardButton("🚀 Upgrade to Pro", url="https://t.me/EliteTradeSignalBot?start=upgrade")
         ]
    ]

    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(menu_text, parse_mode="Markdown", reply_markup=reply_markup)

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
    await update.message.reply_text(
        "🚀 *Upgrade to Pro Today – Unlock Powerful Features!*\n\n"
        "🆓 *Free Plan Includes:*\n"
        "• Create up to 3 alerts\n"
        "• View and clear your alerts\n"
        "• Top 3 Gainers & Losers (24h)\n"
        "• Latest crypto news\n"
        "• Group support (manual commands)\n\n"
        "💎 *Pro Plan – Only ₦2,000/month:*\n"
        "• 🔓 *Unlimited alerts*\n"
        "• ♻️ *Persistent alerts* (auto-resend)\n"
        "• ✏️ *Edit specific alerts*\n"
        "• 📉 *Trend analysis* for each asset\n"
        "• 🎯 *Suggest & vote on future features*\n"
        "• ⚡️ *Priority feature access*\n\n"
        "🧪 And this is just the beginning... *many more powerful features are coming*, but only *Pro users* will help decide what gets added next!\n\n"
        "💳 *How to Upgrade (Bank Transfer)*:\n"
        "`Bank:` Opay\n"
        "`Name:` MAIMUNAT AL-AMIN YARO\n"
        "`Account:` 8068446778\n\n"
        "📩 After payment, send your screenshot to [@uncannyvintage](https://t.me/uncannyvintage) for a manual upgrade.\n\n"
        "Thank you for supporting the future of *PricePulseBot*! 💼",
        parse_mode="Markdown",
        disable_web_page_preview=True
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


# ✅ Main Bot Function


async def main():
    print("🚀 Bot is running...")
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("price", price))
    app.add_handler(CommandHandler("set", set))
    app.add_handler(CommandHandler("remove", remove))
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
    asyncio.run(main())


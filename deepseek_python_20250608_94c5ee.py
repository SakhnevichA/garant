import os
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
    CallbackQueryHandler
)
import sqlite3
import json
from datetime import datetime
import requests
from contextlib import closing

# Configuration
BOT_TOKEN = '7767877400:AAFGHSbm-qcE4vI0K5Pgw_5x72T56xIJWDg'
ADMIN_PASSWORD = os.getenv('ADMIN_PASSWORD')
CRYPTO_ADDRESSES = {
    'BTC': os.getenv('BTC_ADDRESS'),
    'TRX': os.getenv('TRX_ADDRESS'),
    'SOL': os.getenv('SOL_ADDRESS')
}
API_KEY = os.getenv('COINMARKETCAP_API_KEY')

# Database setup
def init_db():
    with closing(sqlite3.connect('escrow_bot.db')) as conn:
        cursor = conn.cursor()
        
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            last_name TEXT,
            balance REAL DEFAULT 0,
            rating REAL DEFAULT 5.0,
            completed_deals INTEGER DEFAULT 0,
            is_admin INTEGER DEFAULT 0
        )
        ''')
        
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS escrows (
            deal_id INTEGER PRIMARY KEY AUTOINCREMENT,
            creator_id INTEGER,
            participant_id INTEGER,
            amount REAL,
            currency TEXT,
            status TEXT DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            completed_at TIMESTAMP,
            FOREIGN KEY (creator_id) REFERENCES users (user_id),
            FOREIGN KEY (participant_id) REFERENCES users (user_id)
        )
        ''')
        
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS transactions (
            tx_id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            amount REAL,
            currency TEXT,
            tx_type TEXT,
            status TEXT DEFAULT 'pending',
            crypto_address TEXT,
            tx_hash TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            confirmed_at TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (user_id)
            )
        ''')
        
        conn.commit()

init_db()

# Helper functions
def get_crypto_price(currency):
    url = f'https://pro-api.coinmarketcap.com/v1/cryptocurrency/quotes/latest?symbol={currency}'
    headers = {'X-CMC_PRO_API_KEY': API_KEY}
    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        data = response.json()
        return data['data'][currency]['quote']['USD']['price']
    except Exception as e:
        print(f"Error getting crypto price: {e}")
        return None

async def send_admin_notification(context: ContextTypes.DEFAULT_TYPE, message):
    try:
        with closing(sqlite3.connect('escrow_bot.db')) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT user_id FROM users WHERE is_admin = 1")
            admins = cursor.fetchall()
        
        for admin in admins:
            try:
                await context.bot.send_message(chat_id=admin[0], text=message)
            except Exception as e:
                print(f"Error sending admin notification: {e}")
    except Exception as e:
        print(f"Error getting admin list: {e}")

# Bot commands
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    try:
        with closing(sqlite3.connect('escrow_bot.db')) as conn:
            cursor = conn.cursor()
            
            cursor.execute("SELECT 1 FROM users WHERE user_id = ?", (user.id,))
            if not cursor.fetchone():
                cursor.execute(
                    "INSERT INTO users (user_id, username, first_name, last_name) VALUES (?, ?, ?, ?)",
                    (user.id, user.username, user.first_name, user.last_name)
                )
                conn.commit()
            
            keyboard = [
                [InlineKeyboardButton("Open Web App", web_app=WebAppInfo(url="https://sakhnevicha.github.io/garant/"))],
                [InlineKeyboardButton("My Balance", callback_data='balance')]
            ]
            
            await update.message.reply_text(
                f"Welcome {user.first_name} to Escrow Bot!\n\n"
                "Use the web app for full functionality.",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
    except Exception as e:
        print(f"Error in start command: {e}")
        await update.message.reply_text("An error occurred. Please try again.")

async def admin_login(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) != 1:
        await update.message.reply_text("Usage: /admin <password>")
        return
    
    password = context.args[0]
    if password == ADMIN_PASSWORD:
        try:
            with closing(sqlite3.connect('escrow_bot.db')) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "UPDATE users SET is_admin = 1 WHERE user_id = ?",
                    (update.effective_user.id,)
                )
                conn.commit()
            await update.message.reply_text("You are now logged in as admin!")
        except Exception as e:
            print(f"Error in admin login: {e}")
            await update.message.reply_text("An error occurred. Please try again.")
    else:
        await update.message.reply_text("Incorrect password")

# Web App interaction
async def web_app_data(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        data = json.loads(update.effective_message.web_app_data.data)
        user_id = update.effective_user.id
        
        if data['action'] == 'create_escrow':
            amount = float(data['amount'])
            currency = data['currency']
            participant = data['participant']
            
            try:
                with closing(sqlite3.connect('escrow_bot.db')) as conn:
                    cursor = conn.cursor()
                    
                    cursor.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,))
                    user_balance = cursor.fetchone()[0]
                    
                    if user_balance < amount:
                        await update.message.reply_text("Insufficient balance for this escrow")
                        return
                    
                    cursor.execute(
                        "INSERT INTO escrows (creator_id, participant_id, amount, currency) VALUES (?, ?, ?, ?)",
                        (user_id, participant, amount, currency)
                    )
                    
                    cursor.execute(
                        "UPDATE users SET balance = balance - ? WHERE user_id = ?",
                        (amount, user_id)
                    )
                    
                    conn.commit()
                
                await update.message.reply_text(f"Escrow created for {amount} {currency} with user {participant}")
                
            except Exception as e:
                print(f"Error creating escrow: {e}")
                await update.message.reply_text("An error occurred while creating escrow")
            
        elif data['action'] == 'deposit_request':
            amount_usd = float(data['amount'])
            currency = data['currency']
            
            rate = get_crypto_price(currency)
            if not rate:
                await update.message.reply_text("Error getting exchange rate. Please try again later.")
                return
            
            crypto_amount = amount_usd / rate
            
            try:
                with closing(sqlite3.connect('escrow_bot.db')) as conn:
                    cursor = conn.cursor()
                    
                    cursor.execute(
                        "INSERT INTO transactions (user_id, amount, currency, tx_type, crypto_address) VALUES (?, ?, ?, ?, ?)",
                        (user_id, crypto_amount, currency, 'deposit', CRYPTO_ADDRESSES[currency])
                    )
                    tx_id = cursor.lastrowid
                    conn.commit()
                
                await send_admin_notification(
                    context,
                    f"New deposit request!\n\n"
                    f"User: {update.effective_user.username}\n"
                    f"Amount: {amount_usd} USD ({crypto_amount} {currency})\n"
                    f"Address: {CRYPTO_ADDRESSES[currency]}\n\n"
                    f"Approve with: /approve {tx_id}"
                )
                
                await update.message.reply_text(
                    f"Please send {crypto_amount:.8f} {currency} to:\n"
                    f"{CRYPTO_ADDRESSES[currency]}\n\n"
                    "After sending, click the confirmation button in the web app."
                )
                
            except Exception as e:
                print(f"Error processing deposit: {e}")
                await update.message.reply_text("An error occurred. Please try again.")
                
    except Exception as e:
        print(f"Error processing web app data: {e}")
        await update.message.reply_text("An error occurred. Please try again.")

async def approve_transaction(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        with closing(sqlite3.connect('escrow_bot.db')) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT is_admin FROM users WHERE user_id = ?",
                (update.effective_user.id,)
            )
            user = cursor.fetchone()
            
            if not user or not user[0]:
                await update.message.reply_text("You are not authorized to use this command")
                return
            
            if len(context.args) != 1:
                await update.message.reply_text("Usage: /approve <transaction_id>")
                return
            
            tx_id = int(context.args[0])
            
            cursor.execute(
                "SELECT user_id, amount, currency FROM transactions WHERE tx_id = ? AND status = 'pending'",
                (tx_id,)
            )
            tx = cursor.fetchone()
            
            if not tx:
                await update.message.reply_text("Transaction not found or already processed")
                return
            
            user_id, amount, currency = tx
            
            rate = get_crypto_price(currency)
            if not rate:
                await update.message.reply_text("Error getting exchange rate. Please try again later.")
                return
            
            usd_amount = amount * rate
            
            cursor.execute(
                "UPDATE transactions SET status = 'completed', confirmed_at = ? WHERE tx_id = ?",
                (datetime.now(), tx_id)
            )
            
            cursor.execute(
                "UPDATE users SET balance = balance + ? WHERE user_id = ?",
                (usd_amount, user_id)
            )
            
            conn.commit()
        
        await context.bot.send_message(
            chat_id=user_id,
            text=f"Your deposit of {usd_amount:.2f} USD has been approved!"
        )
        
        await update.message.reply_text("Transaction approved successfully")
        
    except Exception as e:
        print(f"Error approving transaction: {e}")
        await update.message.reply_text("An error occurred while approving transaction")

def main():
    application = Application.builder().token(BOT_TOKEN).build()
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("admin", admin_login))
    application.add_handler(CommandHandler("approve", approve_transaction))
    application.add_handler(MessageHandler(filters.StatusUpdate.WEB_APP_DATA, web_app_data))
    
    application.run_polling()

if __name__ == "__main__":
    main()
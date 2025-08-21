import os
import asyncio
import re
import zipfile
import io
import json
from datetime import datetime, timedelta
from telethon import TelegramClient, events
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputFile
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
from telethon.tl.functions.messages import ReportRequest, ReportSpamRequest, ImportChatInviteRequest, GetDialogsRequest
from telethon.tl.functions.channels import JoinChannelRequest
from telethon.tl.types import (
    InputPeerChannel, Channel, ReportResultChooseOption, MessageReportOption
)
from telethon.errors import RPCError, FloodWaitError, UserAlreadyParticipantError, SessionPasswordNeededError
import traceback
import random
import sqlite3
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.image import MIMEImage
import logging

# Set up logging for better debugging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

# --- OWNER DETAILS & BOT CONFIGURATION ---
OWNER_ID = 8167904992  # Replace with your actual Telegram Chat ID
OWNER_USERNAME = "whatsapp_offcial"  # Replace with your actual Telegram Username

API_ID = 94575
API_HASH = 'a3406de8d171bb422bb6ddf3bbd800e9'
BOT_TOKEN = '8324191756:AAF28XJJ9wSO2jZ5iFIqlrdEbjqHFX190Pk'

SESSION_FOLDER = 'sessions'
GRANTED_USERS_FILE = 'granted_users.json'
EMAIL_LIST_FILE = 'email.txt'
DATABASE_FILE = 'channels.db'
# Specify the single session file for detection
DETECTION_SESSION_PHONE = '+923117822922' 

# Mapping for main report types
REPORT_OPTIONS = {
    'Scam or spam': b'8',
    'Violence': b'3',
    'Child abuse': b'2',
    'Illegal goods': b'4',
    'Illegal adult content': b'5',
    'Personal data': b'6',
    'Terrorism': b'7',
    'Copyright': b'9',
    'Other': b'a',
    'I don’t like it': b'1',
    'It’s not illegal, but must be taken down': b'b'
}

# Mapping for specific report subtypes
REPORT_SUBTYPES = {
    'Scam or spam': {
        'Phishing': b'81',
        'Impersonation': b'82',
        'Fraudulent sales': b'83',
        'Spam': b'84'
    },
    'Illegal goods': {
        'Weapons': b'41',
        'Drugs': b'42',
        'Fake documents': b'43',
        'Counterfeit money': b'44',
        'Other goods': b'45'
    },
    'Illegal adult content': {
        'Nudity': b'51',
        'Sexual abuse': b'52',
        'Child sexual abuse material': b'53',
        'Other adult content': b'54'
    },
    'Personal data': {
        'Identity theft': b'61',
        'Leaked phone number': b'62',
        'Leaked address': b'63',
        'Other personal data': b'64'
    }
}

session_locks = {}
user_tasks = {}
task_counter = 0

# --- File/Directory/Database Initialization ---
def init_files():
    if not os.path.exists(SESSION_FOLDER):
        os.makedirs(SESSION_FOLDER)
    if not os.path.exists(GRANTED_USERS_FILE):
        with open(GRANTED_USERS_FILE, 'w') as f:
            json.dump([], f)
    if not os.path.exists(EMAIL_LIST_FILE):
        with open(EMAIL_LIST_FILE, 'w') as f:
            f.write('')
    init_db()

def init_db():
    conn = sqlite3.connect(DATABASE_FILE)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS channels (
            channel_link TEXT PRIMARY KEY,
            report_type TEXT,
            report_message TEXT,
            report_count INTEGER,
            reported_posts_count INTEGER DEFAULT 0,
            successful_reports INTEGER DEFAULT 0,
            failed_reports INTEGER DEFAULT 0,
            last_reported_post_id INTEGER DEFAULT NULL,
            status TEXT DEFAULT 'active'
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS report_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            channel_link TEXT,
            message_id INTEGER,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            response_json TEXT,
            status TEXT
        )
    ''')
    conn.commit()
    conn.close()

# --- Utility Functions ---
def load_granted_users():
    if not os.path.exists(GRANTED_USERS_FILE):
        return []
    with open(GRANTED_USERS_FILE, 'r') as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return []

def save_granted_users(users):
    with open(GRANTED_USERS_FILE, 'w') as f:
        json.dump(users, f, indent=4)

def load_email_accounts():
    accounts = []
    if not os.path.exists(EMAIL_LIST_FILE):
        return accounts
    with open(EMAIL_LIST_FILE, 'r') as f:
        for line in f:
            line = line.strip()
            if not line or ':' in line: # Changed to 'in'
                continue
            email, password = line.split(':', 1)
            accounts.append({'email': email, 'password': password})
    return accounts

def get_granted_user_info(user_id):
    granted_users = load_granted_users()
    for user in granted_users:
        if user['user_id'] == user_id:
            expires_at = datetime.fromisoformat(user['expires_at'])
            if datetime.now() < expires_at:
                return user
    return None

def is_owner(user_id):
    return user_id == OWNER_ID

def is_granted_user(user_id):
    return get_granted_user_info(user_id) is not None

def mask_phone_number(phone_number):
    if len(phone_number) < 8:
        return phone_number
    return phone_number[:5] + '***' + phone_number[-5:]

def get_email_server(email):
    if '@gmail.com' in email:
        return 'smtp.gmail.com', 587
    elif '@yahoo.com' in email:
        return 'smtp.mail.yahoo.com', 587
    elif '@outlook.com' in email or '@hotmail.com' in email:
        return 'smtp-mail.outlook.com', 587
    return None, None

def get_logged_in_accounts(user_id, all_access=False):
    accounts = []
    if all_access:
        for user_folder in os.listdir(SESSION_FOLDER):
            user_path = os.path.join(SESSION_FOLDER, user_folder)
            if os.path.isdir(user_path) and user_folder.isdigit():
                for filename in os.listdir(user_path):
                    if filename.endswith('.session'):
                        phone_number = os.path.splitext(filename)[0]
                        accounts.append((phone_number, int(user_folder)))
    else:
        user_path = os.path.join(SESSION_FOLDER, str(user_id))
        if os.path.exists(user_path):
            for filename in os.listdir(user_path):
                if filename.endswith('.session'):
                    phone_number = os.path.splitext(filename)[0]
                    accounts.append((phone_number, user_id))
    return accounts

# --- Channel Management Functions ---
def add_channel_to_db(link, report_type, message, count):
    conn = sqlite3.connect(DATABASE_FILE)
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO channels (channel_link, report_type, report_message, report_count) VALUES (?, ?, ?, ?)",
              (link, report_type, message, count))
    conn.commit()
    conn.close()

def get_channel_info_from_db(link):
    conn = sqlite3.connect(DATABASE_FILE)
    c = conn.cursor()
    c.execute("SELECT * FROM channels WHERE channel_link = ?", (link,))
    row = c.fetchone()
    conn.close()
    return row

def get_all_channels_from_db():
    conn = sqlite3.connect(DATABASE_FILE)
    c = conn.cursor()
    c.execute("SELECT * FROM channels")
    rows = c.fetchall()
    conn.close()
    return rows

def update_channel_status(link, status):
    conn = sqlite3.connect(DATABASE_FILE)
    c = conn.cursor()
    c.execute("UPDATE channels SET status = ? WHERE channel_link = ?", (status, link))
    conn.commit()
    conn.close()

def delete_channel_from_db(link):
    conn = sqlite3.connect(DATABASE_FILE)
    c = conn.cursor()
    c.execute("DELETE FROM channels WHERE channel_link = ?", (link,))
    conn.commit()
    conn.close()

def update_report_counts(link, is_success, message_id=None):
    conn = sqlite3.connect(DATABASE_FILE)
    c = conn.cursor()
    if is_success:
        c.execute("UPDATE channels SET successful_reports = successful_reports + 1 WHERE channel_link = ?", (link,))
    else:
        c.execute("UPDATE channels SET failed_reports = failed_reports + 1 WHERE channel_link = ?", (link,))
    
    c.execute("UPDATE channels SET reported_posts_count = reported_posts_count + 1 WHERE channel_link = ?", (link,))
    
    if message_id:
        c.execute("UPDATE channels SET last_reported_post_id = ? WHERE channel_link = ?", (message_id, link))

    conn.commit()
    conn.close()

def add_report_record(channel_link, message_id, response, status):
    conn = sqlite3.connect(DATABASE_FILE)
    c = conn.cursor()
    c.execute("INSERT INTO report_records (channel_link, message_id, response_json, status) VALUES (?, ?, ?, ?)",
              (channel_link, message_id, json.dumps(str(response)), status))
    conn.commit()
    conn.close()

def get_last_report_records(channel_link, limit=5):
    conn = sqlite3.connect(DATABASE_FILE)
    c = conn.cursor()
    c.execute("SELECT message_id, timestamp, response_json, status FROM report_records WHERE channel_link = ? ORDER BY timestamp DESC LIMIT ?",
              (channel_link, limit))
    rows = c.fetchall()
    conn.close()
    records = []
    for row in rows:
        records.append({
            'message_id': row[0],
            'timestamp': row[1],
            'response': json.loads(row[2]),
            'status': row[3]
        })
    return records


# --- Telethon Client & Event Handler ---
# Use a single telethon client for event listening
telethon_client = TelegramClient('live_event_listener', API_ID, API_HASH)
live_log_users = {}

@telethon_client.on(events.NewMessage(incoming=True, func=lambda e: e.is_channel))
async def handle_new_channel_message(event):
    if not event.is_channel:
        return

    channels_to_report = get_all_channels_from_db()
    
    for channel in channels_to_report:
        link, report_type_text, message, count, reported_count, successful_reports, failed_reports, last_reported_id, status = channel
        
        if status != 'active':
            continue

        try:
            entity = await telethon_client.get_entity(link)
            if entity.id == event.chat_id:
                if event.message and event.message.id != last_reported_id:
                    logging.info(f"New message detected in channel {link}. Starting report process...")
                    
                    accounts_to_use = get_logged_in_accounts(OWNER_ID, all_access=True)
                    if not accounts_to_use:
                        logging.warning("No accounts logged in to send auto-reports.")
                        await send_owner_error(f"❌ **Auto-Report Error:**\nNo accounts logged in to send auto-reports for channel: `{link}`.")
                        return

                    report_option_byte = None
                    found_subtype = False
                    for main_type, subtypes in REPORT_SUBTYPES.items():
                        if report_type_text in subtypes:
                            report_option_byte = subtypes[report_type_text]
                            found_subtype = True
                            break
                    if not found_subtype:
                        report_option_byte = REPORT_OPTIONS.get(report_type_text)

                    if report_option_byte is None:
                        error_msg = f"❌ **Auto-Report Error:**\nInvalid report type selected for channel `{link}`: `{report_type_text}`. Skipping auto-report."
                        logging.error(error_msg)
                        await send_owner_error(error_msg)
                        return

                    report_tasks = []
                    # Create tasks to report from all logged in accounts
                    for phone_number, account_user_id in accounts_to_use:
                        task = asyncio.create_task(send_single_auto_report(phone_number, account_user_id, entity, event.message.id, report_option_byte, message, link))
                        report_tasks.append(task)
                    
                    await asyncio.gather(*report_tasks)
        except Exception as e:
            error_msg = f"❌ **Auto-Report Error:**\nAn unexpected error occurred while processing channel `{link}`.\n\n**Error Details:**\n`{type(e).__name__}: {str(e)}`"
            logging.error(error_msg)
            await send_owner_error(error_msg)
            await send_owner_error(traceback.format_exc())

async def send_single_auto_report(phone_number, account_user_id, entity, message_id, report_option_byte, report_message, channel_link):
    session_folder = os.path.join(SESSION_FOLDER, str(account_user_id))
    session_path = os.path.join(session_folder, phone_number)
    
    if phone_number not in session_locks:
        session_locks[phone_number] = asyncio.Lock()
    
    async with session_locks[phone_number]:
        if not os.path.exists(session_path + '.session'):
            error_msg = f"❌ **Auto-Report Error:**\nSession file not found for `{mask_phone_number(phone_number)}`. Skipping auto-report for channel `{channel_link}`."
            logging.warning(error_msg)
            await send_owner_error(error_msg)
            update_report_counts(channel_link, False, message_id)
            add_report_record(channel_link, message_id, "Session file not found", "Failed")
            return

        client = TelegramClient(session_path, API_ID, API_HASH)
        
        try:
            await client.connect()
            if not await client.is_user_authorized():
                error_msg = f"❌ **Auto-Report Error:**\nAccount `{mask_phone_number(phone_number)}` is not authorized. Skipping auto-report for channel `{channel_link}`."
                logging.warning(error_msg)
                await send_owner_error(error_msg)
                update_report_counts(channel_link, False, message_id)
                add_report_record(channel_link, message_id, "Account not authorized", "Failed")
                return

            response = await client(ReportRequest(peer=entity, id=[message_id], option=report_option_byte, message=report_message))
            logging.info(f"Report sent successfully from {phone_number} for message {message_id} in {entity.title}")
            update_report_counts(channel_link, True, message_id)
            add_report_record(channel_link, message_id, response, "Success")
            
            # Send live log update to all users who have it enabled
            for user_id, channel_to_log in live_log_users.items():
                if channel_to_log == channel_link:
                    bot_application = Application.builder().token(BOT_TOKEN).build()
                    await bot_application.bot.send_message(chat_id=user_id, text=f"✅ **Live Log:**\nAccount: {mask_phone_number(phone_number)}\nPost ID: {message_id}\nStatus: Success\nResponse: `{str(response)}`", parse_mode='Markdown')
            
        except RPCError as e:
            error_msg = f"❌ **Auto-Report Error:**\nRPCError from `{mask_phone_number(phone_number)}` on auto-report for channel `{channel_link}`.\n\n**Error Details:**\n`{type(e).__name__}: {str(e)}`"
            logging.error(error_msg)
            await send_owner_error(error_msg)
            update_report_counts(channel_link, False, message_id)
            add_report_record(channel_link, message_id, str(e), "Failed")
            # Send live log update
            for user_id, channel_to_log in live_log_users.items():
                if channel_to_log == channel_link:
                    bot_application = Application.builder().token(BOT_TOKEN).build()
                    await bot_application.bot.send_message(chat_id=user_id, text=f"❌ **Live Log:**\nAccount: {mask_phone_number(phone_number)}\nPost ID: {message_id}\nStatus: Failed\nResponse: `{str(e)}`", parse_mode='Markdown')

        except FloodWaitError as e:
            error_msg = f"⚠️ **Auto-Report Warning:**\n`FloodWaitError` from `{mask_phone_number(phone_number)}` on auto-report for channel `{channel_link}`. Waiting for `{e.seconds}` seconds."
            logging.warning(error_msg)
            await send_owner_error(error_msg)
            await asyncio.sleep(e.seconds)
            update_report_counts(channel_link, False, message_id)
            add_report_record(channel_link, message_id, str(e), "Failed")
            # Send live log update
            for user_id, channel_to_log in live_log_users.items():
                if channel_to_log == channel_link:
                    bot_application = Application.builder().token(BOT_TOKEN).build()
                    await bot_application.bot.send_message(chat_id=user_id, text=f"❌ **Live Log:**\nAccount: {mask_phone_number(phone_number)}\nPost ID: {message_id}\nStatus: FloodWaitError\nResponse: `{str(e)}`", parse_mode='Markdown')
        except Exception as e:
            error_msg = f"❌ **Auto-Report Error:**\nGeneral error from `{mask_phone_number(phone_number)}` on auto-report for channel `{channel_link}`.\n\n**Error Details:**\n`{type(e).__name__}: {str(e)}`"
            logging.error(error_msg)
            await send_owner_error(error_msg)
            await send_owner_error(traceback.format_exc())
            update_report_counts(channel_link, False, message_id)
            add_report_record(channel_link, message_id, str(e), "Failed")
            # Send live log update
            for user_id, channel_to_log in live_log_users.items():
                if channel_to_log == channel_link:
                    bot_application = Application.builder().token(BOT_TOKEN).build()
                    await bot_application.bot.send_message(chat_id=user_id, text=f"❌ **Live Log:**\nAccount: {mask_phone_number(phone_number)}\nPost ID: {message_id}\nStatus: Failed\nResponse: `{str(e)}`", parse_mode='Markdown')
        finally:
            if client.is_connected():
                await client.disconnect()

async def send_owner_error(message: str) -> None:
    """Sends a formatted error message to the bot owner."""
    try:
        application = Application.builder().token(BOT_TOKEN).build()
        await application.bot.send_message(chat_id=OWNER_ID, text=message, parse_mode='Markdown')
    except Exception as e:
        logging.error(f"Failed to send error message to owner: {e}")

# --- Bot Handlers (Telegram.ext) ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    text = ''
    keyboard = []

    is_user_granted = is_granted_user(user_id)
    user_info = get_granted_user_info(user_id)
    all_access = user_info.get('all_access') if user_info else False
    
    if is_owner(user_id):
        keyboard = [
            [InlineKeyboardButton("Login 🔐", callback_data='login_start')],
            [InlineKeyboardButton("Join Channel ➕", callback_data='join_channel')],
            [InlineKeyboardButton("Report Illegal Content 🚨", callback_data='report_start')],
            [InlineKeyboardButton("Report via Email 📧", callback_data='report_email_start')],
            [InlineKeyboardButton("Add Channel List ➕", callback_data='add_channel_list')],
            [InlineKeyboardButton("My Accounts 👤", callback_data='my_accounts')],
            [InlineKeyboardButton("Backup 💾", callback_data='backup_sessions')],
            [InlineKeyboardButton("Manage Users 🗂️", callback_data='manage_users')],
            [InlineKeyboardButton("Grant Access ✨", callback_data='grant_access')]
        ]
        text = 'Hello Owner! Please choose an option:'
    elif is_user_granted:
        keyboard = [
            [InlineKeyboardButton("Login 🔐", callback_data='login_start')],
            [InlineKeyboardButton("Join Channel ➕", callback_data='join_channel')],
            [InlineKeyboardButton("Report Illegal Content 🚨", callback_data='report_start')],
            [InlineKeyboardButton("Report via Email 📧", callback_data='report_email_start')],
            [InlineKeyboardButton("Add Channel List ➕", callback_data='add_channel_list')],
        ]
        if all_access:
            keyboard.append([InlineKeyboardButton("My Accounts 👤", callback_data='my_accounts')])
        text = 'Hello! You have limited access. Please choose an option:'
    else:
        keyboard = [
            [InlineKeyboardButton("Login 🔐", callback_data='login_start')],
            [InlineKeyboardButton("Report Illegal Content 🚨", callback_data='report_start')],
            [InlineKeyboardButton("Report via Email 📧", callback_data='report_email_start')],
        ]
        text = 'Welcome! You can log in your accounts and start using the bot.'
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(text, reply_markup=reply_markup)

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    
    if query.data == 'start':
        await start(update, context)
        return

    # Check for user access before proceeding with certain actions
    if not is_granted_user(user_id) and not is_owner(user_id):
        if query.data not in ['login_start', 'report_start', 'report_email_start']:
            await query.edit_message_text("❌ You do not have permission to use this function. Please contact the owner.")
            return

    if query.data == 'login_start':
        await query.edit_message_text(text="Please send your phone number with country code (e.g., +923001234567) to log in.")
        context.user_data['state'] = 'awaiting_phone_number'
    
    elif query.data == 'join_channel':
        await query.edit_message_text(text="Please send the public or private channel invite link to join.")
        context.user_data['state'] = 'awaiting_join_link'

    elif query.data == 'report_start':
        await query.edit_message_text(text="Please send the link of the channel or a post you want to report.")
        context.user_data['state'] = 'awaiting_link'
        context.user_data['report_method'] = 'session'

    elif query.data == 'report_email_start':
        await query.edit_message_text(text="Please send the link of the channel or a post you want to report.")
        context.user_data['state'] = 'awaiting_link'
        context.user_data['report_method'] = 'email'

    elif query.data.startswith('report_type_'):
        report_type_text = query.data.split('_', 2)[-1]
        context.user_data['report_type_text'] = report_type_text
        
        if report_type_text in REPORT_SUBTYPES:
            subtype_options = REPORT_SUBTYPES[report_type_text]
            keyboard_buttons = [[InlineKeyboardButton(text=opt, callback_data=f'report_subtype_{opt}')] for opt in subtype_options.keys()]
            reply_markup = InlineKeyboardMarkup(keyboard_buttons)
            await query.edit_message_text(f"Please choose a specific reason for '{report_type_text}':", reply_markup=reply_markup)
        else:
            if context.user_data.get('report_method') == 'email':
                await query.edit_message_text(f"You selected '{report_type_text}'. Now, please send the screenshot of the content you want to report.\n\nAfter sending the screenshot, provide a brief message and the number of times to report (e.g., 'Violent content 5').")
                context.user_data['state'] = 'awaiting_photo_or_comment'
            else:
                await query.edit_message_text(f"You selected '{report_type_text}'. Now, please provide a brief message and the number of times to report (e.g., 'Violent content 5').")
                context.user_data['state'] = 'awaiting_report_comment_and_count'
            
    elif query.data.startswith('report_subtype_'):
        report_subtype_text = query.data.split('_', 2)[-1]
        context.user_data['report_type_text'] = report_subtype_text
        if context.user_data.get('report_method') == 'email':
            await query.edit_message_text(f"You selected '{report_subtype_text}'. Now, please send the screenshot of the content you want to report.\n\nAfter sending the screenshot, provide a brief message and the number of times to report (e.g., 'Violent content 5').")
            context.user_data['state'] = 'awaiting_photo_or_comment'
        else:
            await query.edit_message_text(f"You selected '{report_subtype_text}'. Now, please provide a brief message and the number of times to report (e.g., 'Violent content 5').")
            context.user_data['state'] = 'awaiting_report_comment_and_count'

    elif query.data == 'my_accounts':
        if not is_owner(user_id) and not get_granted_user_info(user_id).get('all_access'):
            await query.edit_message_text("You do not have permission to view other users' accounts.")
            return
        await manage_accounts(update, context)

    elif query.data.startswith('view_account_'):
        parts = query.data.split('_')
        if len(parts) != 4:
            await query.edit_message_text("❌ An error occurred. Please try again.")
            return
        
        phone_number, account_user_id = parts[2], parts[3]
        
        if is_owner(user_id) or (user_id == int(account_user_id)):
            keyboard = [[
                InlineKeyboardButton("Delete Account 🗑️", callback_data=f'confirm_delete_{phone_number}_{account_user_id}'),
                InlineKeyboardButton("Back ↩️", callback_data='my_accounts')
            ]]
        else:
            keyboard = [[InlineKeyboardButton("Back ↩️", callback_data='my_accounts')]]
            
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(f"Options for account: {mask_phone_number(phone_number)}", reply_markup=reply_markup)
        
    elif query.data.startswith('confirm_delete_'):
        parts = query.data.split('_')
        if len(parts) != 4:
            await query.edit_message_text("❌ An error occurred. Please try again.")
            return
        
        phone_number, account_user_id = parts[2], parts[3]
        
        if not is_owner(user_id) and not (user_id == int(account_user_id)):
            await query.edit_message_text("❌ You do not have permission to delete this account.")
            return

        keyboard = [[
            InlineKeyboardButton("Confirm Delete ⚠️", callback_data=f'delete_account_{phone_number}_{account_user_id}'),
            InlineKeyboardButton("Cancel ❌", callback_data=f'view_account_{phone_number}_{account_user_id}')
        ]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(f"Are you sure you want to delete the session for {mask_phone_number(phone_number)}?", reply_markup=reply_markup)
    
    elif query.data.startswith('delete_account_'):
        parts = query.data.split('_')
        if len(parts) != 4:
            await query.edit_message_text("❌ An error occurred. Please try again.")
            return
        
        phone_number, account_user_id = parts[2], parts[3]

        if not is_owner(user_id) and not (user_id == int(account_user_id)):
            await query.edit_message_text("❌ You do not have permission to delete this account.")
            return

        await delete_account(update, context, phone_number, account_user_id)

    elif query.data == 'my_channels' or query.data == 'add_channel_list':
        channels = get_all_channels_from_db()
        keyboard = [[InlineKeyboardButton("Add New Channel ➕", callback_data='add_new_channel')]]
        
        for link, report_type, message, count, reported_count, successful_reports, failed_reports, last_reported_id, status in channels:
            status_text = "Active ✅" if status == 'active' else "Paused ⏸️"
            keyboard.append([InlineKeyboardButton(f"Channel: {link} ({status_text})", callback_data=f'manage_channel_{link}')])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text("Your reported channel list:", reply_markup=reply_markup)

    elif query.data == 'add_new_channel':
        await query.edit_message_text("Please send the channel link (e.g., t.me/channel_name) to add it to the auto-report list.")
        context.user_data['state'] = 'awaiting_channel_link_for_auto_report'

    elif query.data.startswith('add_channel_type_'):
        report_type_text = query.data.split('_', 3)[-1]
        context.user_data['add_report_type_text'] = report_type_text
        
        if report_type_text in REPORT_SUBTYPES:
            subtype_options = REPORT_SUBTYPES[report_type_text]
            keyboard_buttons = [[InlineKeyboardButton(text=opt, callback_data=f'add_channel_subtype_{opt}')] for opt in subtype_options.keys()]
            reply_markup = InlineKeyboardMarkup(keyboard_buttons)
            await query.edit_message_text(f"Please choose a specific reason for '{report_type_text}':", reply_markup=reply_markup)
        else:
            await query.edit_message_text(f"You selected '{report_type_text}'. Now, please provide a brief message and the number of times to report (e.g., 'Violent content 5').")
            context.user_data['state'] = 'awaiting_add_report_message_and_count'
            
    elif query.data.startswith('add_channel_subtype_'):
        report_subtype_text = query.data.split('_', 3)[-1]
        context.user_data['add_report_type_text'] = report_subtype_text
        await query.edit_message_text(f"You selected '{report_subtype_text}'. Now, please provide a brief message and the number of times to report (e.g., 'Violent content 5').")
        context.user_data['state'] = 'awaiting_add_report_message_and_count'

    elif query.data.startswith('manage_channel_'):
        channel_link = query.data.split('_', 2)[-1]
        channel_info = get_channel_info_from_db(channel_link)
        if not channel_info:
            await query.edit_message_text("Channel not found.")
            return

        link, report_type, message, count, reported_count, successful_reports, failed_reports, last_reported_id, status = channel_info
        
        status_text = "Active ✅" if status == 'active' else "Paused ⏸️"
        
        message_text = f"""
**Channel Management Dashboard**
------------------------------
**Channel:** {link}
**Status:** {status_text}
**Total Posts Reported:** {reported_count}
**Successful Reports:** {successful_reports}
**Failed Reports:** {failed_reports}
**Last Reported Post ID:** {last_reported_id if last_reported_id else 'N/A'}
------------------------------
"""
        
        toggle_status = 'pause' if status == 'active' else 'start'
        
        keyboard = [
            [InlineKeyboardButton(f"{toggle_status.capitalize()} Reporting", callback_data=f'toggle_channel_{channel_link}')],
            [InlineKeyboardButton("Delete Channel 🗑️", callback_data=f'delete_channel_{channel_link}')],
            [InlineKeyboardButton("Check Records ✅", callback_data=f'check_channel_records_{channel_link}')],
        ]
        
        # Add Live Log buttons
        if user_id in live_log_users and live_log_users[user_id] == channel_link:
            keyboard.append([InlineKeyboardButton("Hide Log ⏸️", callback_data=f'toggle_live_log_{channel_link}')])
        else:
            keyboard.append([InlineKeyboardButton("Check Log ▶️", callback_data=f'toggle_live_log_{channel_link}')])

        keyboard.append([InlineKeyboardButton("Back ↩️", callback_data='add_channel_list')])

        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(message_text, reply_markup=reply_markup, parse_mode='Markdown')
    
    elif query.data.startswith('toggle_channel_'):
        channel_link = query.data.split('_', 2)[-1]
        channel_info = get_channel_info_from_db(channel_link)
        if not channel_info:
            await query.edit_message_text("Channel not found.")
            return
            
        current_status = channel_info[8]
        new_status = 'paused' if current_status == 'active' else 'active'
        update_channel_status(channel_link, new_status)
        
        action = "stopped" if new_status == 'paused' else "started"
        await query.edit_message_text(f"✅ Auto-reporting for channel {channel_link} has been {action}.")
        
        await asyncio.sleep(1) # Wait for message to be sent
        await button_handler(update, context) # Reload the menu

    elif query.data.startswith('toggle_live_log_'):
        channel_link = query.data.split('_', 3)[-1]
        user_id = update.effective_user.id
        
        if user_id in live_log_users and live_log_users[user_id] == channel_link:
            del live_log_users[user_id]
            await query.edit_message_text(f"✅ Live logging for channel {channel_link} has been paused.")
        else:
            live_log_users[user_id] = channel_link
            await query.edit_message_text(f"✅ Live logging for channel {channel_link} has been started. You will now receive real-time updates for new reports.")

        await asyncio.sleep(1) # Wait for message to be sent
        await button_handler(update, context) # Reload the menu

    elif query.data.startswith('check_channel_records_'):
        channel_link = query.data.split('_', 3)[-1]
        records = get_last_report_records(channel_link)
        if not records:
            await query.edit_message_text("❌ No report records found for this channel.")
            return
        
        response_text = f"**Last 5 Report Records for {channel_link}:**\n\n"
        for record in records:
            response_text += f"**Timestamp:** {record['timestamp']}\n"
            response_text += f"**Message ID:** {record['message_id']}\n"
            response_text += f"**Status:** {'✅ Success' if record['status'] == 'Success' else '❌ Failed'}\n"
            response_text += f"**API Response:** `{record['response']}`\n"
            response_text += "--------------------------------------\n"
        
        await query.edit_message_text(response_text)

    elif query.data.startswith('delete_channel_'):
        channel_link = query.data.split('_', 2)[-1]
        delete_channel_from_db(channel_link)
        await query.edit_message_text(f"✅ Channel {channel_link} has been deleted from the list.")
    
    elif query.data == 'backup_sessions' and is_owner(user_id):
        await query.edit_message_text("Creating a full project backup. This may take a moment...")
        await create_full_backup(query, context)
        await query.message.reply_text("Backup process completed.")
        
    elif query.data == 'manage_users' and is_owner(user_id):
        await query.edit_message_text("Fetching list of granted users...")
        await list_granted_users(query, context)
        
    elif query.data == 'grant_access' and is_owner(user_id):
        await query.edit_message_text("Please send the user's Chat ID or Username, duration, and optionally 'true' for all-access (e.g., `123456789 1h true`).")
        context.user_data['state'] = 'awaiting_grant_info'

    elif query.data.startswith('delete_access_') and is_owner(user_id):
        user_to_delete = int(query.data.split('_', 2)[-1])
        await delete_access(query, context, user_to_delete)
    
    elif query.data.startswith('reset_access_') and is_owner(user_id):
        user_to_reset = int(query.data.split('_', 2)[-1])
        context.user_data['state'] = 'awaiting_reset_info'
        context.user_data['user_to_reset'] = user_to_reset
        await query.edit_message_text(f"Please send the new duration for user {user_to_reset} (e.g., `1h`, `1d`).")
        

async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    global task_counter
    user_message = update.message.text
    user_state = context.user_data.get('state')
    user_id = update.effective_user.id
    
    if user_state == 'awaiting_grant_info' and is_owner(user_id):
        parts = user_message.split()
        if not (2 <= len(parts) <= 3):
            await update.message.reply_text("Invalid format. Please provide the ID/Username, duration, and optionally 'true' (e.g., `123456789 1h true`).")
            context.user_data['state'] = None
            return
        target_str, duration_str = parts[0], parts[1]
        all_access = parts[2].lower() == 'true' if len(parts) == 3 else False
        
        try:
            if not target_str.isdigit():
                chat_id = (await context.bot.get_chat(target_str)).id
            else:
                chat_id = int(target_str)
        except Exception:
            await update.message.reply_text("Could not find a user with that ID or Username. Please try again.")
            context.user_data['state'] = None
            return
        try:
            unit = duration_str[-1].lower()
            value = int(duration_str[:-1])
            if unit == 'h':
                delta = timedelta(hours=value)
            elif unit == 'd':
                delta = timedelta(days=value)
            else:
                await update.message.reply_text("Invalid duration format. Use 'h' for hours or 'd' for days (e.g., '1h', '2d').")
                context.user_data['state'] = None
                return
            expires_at = (datetime.now() + delta).isoformat()
            granted_users = load_granted_users()
            user_found = False
            for user in granted_users:
                if user['user_id'] == chat_id:
                    user['expires_at'] = expires_at
                    user['all_access'] = all_access
                    user_found = True
                    break
            if not user_found:
                granted_users.append({'user_id': chat_id, 'expires_at': expires_at, 'all_access': all_access})
            save_granted_users(granted_users)
            access_type = "full access" if all_access else "limited access"
            await update.message.reply_text(f"✅ Access granted to user ID {chat_id} with {access_type} until {datetime.fromisoformat(expires_at).strftime('%Y-%m-%d %H:%M')}.")
            context.user_data['state'] = None
        except (ValueError, IndexError):
            await update.message.reply_text("Invalid format. Please provide the ID and duration (e.g., `123456789 1h`).")
            context.user_data['state'] = None
    
    elif user_state == 'awaiting_reset_info' and is_owner(user_id):
        user_to_reset = context.user_data.get('user_to_reset')
        duration_str = user_message.strip()
        try:
            unit = duration_str[-1].lower()
            value = int(duration_str[:-1])
            if unit == 'h':
                delta = timedelta(hours=value)
            elif unit == 'd':
                delta = timedelta(days=value)
            else:
                await update.message.reply_text("Invalid duration format. Use 'h' for hours or 'd' for days (e.g., '1h', '2d').")
                context.user_data['state'] = 'awaiting_reset_info'
                return
            expires_at = (datetime.now() + delta).isoformat()
            granted_users = load_granted_users()
            user_found = False
            for user in granted_users:
                if user['user_id'] == user_to_reset:
                    user['expires_at'] = expires_at
                    user_found = True
                    break
            if user_found:
                save_granted_users(granted_users)
                await update.message.reply_text(f"✅ Access for user {user_to_reset} has been reset until {datetime.fromisoformat(expires_at).strftime('%Y-%m-%d %H:%M')}.")
            else:
                await update.message.reply_text(f"User {user_to_reset} not found in granted list.")
            context.user_data['state'] = None
            context.user_data.pop('user_to_reset', None)
        except (ValueError, IndexError):
            await update.message.reply_text("Invalid duration format. Please provide a duration (e.g., '1h', '2d').")
            context.user_data['state'] = 'awaiting_reset_info'

    elif user_state == 'awaiting_phone_number':
        phone_number = user_message
        try:
            user_session_folder = os.path.join(SESSION_FOLDER, str(user_id))
            if not os.path.exists(user_session_folder):
                os.makedirs(user_session_folder)
            
            session_path = os.path.join(user_session_folder, phone_number)
            
            if os.path.exists(session_path + '.session'):
                await update.message.reply_text("This account is already logged in. If you are having issues, please delete the old session file and try again.")
                context.user_data['state'] = None
                return

            client = TelegramClient(session_path, API_ID, API_HASH)
            await client.connect()
            if not await client.is_user_authorized():
                await client.send_code_request(phone_number)
                context.user_data['client'] = client
                context.user_data['phone_number'] = phone_number
                await update.message.reply_text("OTP has been sent to your number. Please enter the code.")
                context.user_data['state'] = 'awaiting_otp'
            else:
                await update.message.reply_text("This account is already logged in.")
                await client.disconnect()
                context.user_data['state'] = None
        except Exception as e:
            await update.message.reply_text(f"An error occurred: {e}. Please try again.")
            context.user_data['state'] = None

    elif user_state == 'awaiting_otp':
        otp = user_message
        client = context.user_data.get('client')
        phone_number = context.user_data.get('phone_number')

        if not client or not phone_number:
            await update.message.reply_text("Something went wrong. Please start the login process again.")
            context.user_data['state'] = None
            return

        try:
            await client.sign_in(code=otp)
            await update.message.reply_text("Successfully logged in! Your session file has been saved.")
            context.user_data['state'] = None
            context.user_data.pop('client', None)
            context.user_data.pop('phone_number', None)
        except SessionPasswordNeededError:
            await update.message.reply_text("Two-factor authentication is enabled. Please enter your password.")
            context.user_data['state'] = 'awaiting_password'
        except Exception as e:
            await update.message.reply_text(f"Invalid OTP. Please try again. Error: {e}")
    
    elif user_state == 'awaiting_password':
        password = user_message
        client = context.user_data.get('client')
        try:
            await client.sign_in(password=password)
            await update.message.reply_text("Successfully logged in! Your session file has been saved.")
            context.user_data['state'] = None
            context.user_data.pop('client', None)
            context.user_data.pop('phone_number', None)
        except Exception as e:
            await update.message.reply_text(f"Invalid password. Please try again. Error: {e}")
            
    elif user_state == 'awaiting_link':
        context.user_data['target_link'] = user_message
        keyboard_buttons = [[InlineKeyboardButton(text=key, callback_data=f'report_type_{key}')] for key in REPORT_OPTIONS.keys()]
        reply_markup = InlineKeyboardMarkup(keyboard_buttons)
        await update.message.reply_text("Please choose a report type:", reply_markup=reply_markup)
        context.user_data['state'] = 'awaiting_report_type_selection'
    
    elif user_state == 'awaiting_photo_or_comment':
        if update.message.photo:
            photo_file_id = update.message.photo[-1].file_id
            context.user_data['photo_file_id'] = photo_file_id
            await update.message.reply_text("Photo received. Now, please provide a brief message and the number of times to report (e.g., 'Violent content 5').")
            context.user_data['state'] = 'awaiting_report_comment_and_count'
            return # Wait for the next message
        
        await update.message.reply_text("You have to send a screenshot first. If you want to continue without a screenshot, just send your report message and count now.")
        context.user_data['state'] = 'awaiting_report_comment_and_count'
    
    elif user_state == 'awaiting_report_comment_and_count':
        try:
            user_message = update.message.text
            parts = user_message.rsplit(' ', 1)
            report_message = parts[0].strip()
            report_count = int(parts[1].strip())
            
            target_link = context.user_data.get('target_link')
            report_type_text = context.user_data.get('report_type_text')
            report_method = context.user_data.get('report_method')
            attachment = context.user_data.get('photo_file_id')

            task_counter += 1
            task_id = task_counter

            if report_method == 'session':
                user_info = get_granted_user_info(user_id)
                accounts_to_use = get_logged_in_accounts(user_id, is_owner(user_id) or (user_info and user_info.get('all_access')))
                
                if not accounts_to_use:
                    await update.message.reply_text("No accounts logged in to send reports.")
                    context.user_data['state'] = None
                    return
                
                await update.message.reply_text(f"Starting to report '{target_link}' for you. This is task #{task_id}. It will run in the background.")
                report_main_task = asyncio.create_task(start_reporting_process(update, context, accounts_to_use, target_link, report_type_text, report_count, report_message, task_id, user_id))
            
            elif report_method == 'email':
                email_accounts = load_email_accounts()
                if not email_accounts:
                    await update.message.reply_text("No email accounts found in `email_list.txt`. Please add accounts and try again.")
                    context.user_data['state'] = None
                    return
                
                await update.message.reply_text(f"Starting to report '{target_link}' via email. This is task #{task_id}. It will run in the background.")
                report_main_task = asyncio.create_task(start_email_reporting_process(update, context, email_accounts, target_link, report_type_text, report_count, report_message, task_id, user_id, attachment))
            
            if user_id not in user_tasks:
                user_tasks[user_id] = {}
            user_tasks[user_id][task_id] = report_main_task
            
            context.user_data['state'] = None
            context.user_data.pop('photo_file_id', None)
            
        except (ValueError, IndexError):
            await update.message.reply_text("Please provide a comment and a number separated by a space (e.g., 'Violent content 5').")
            context.user_data['state'] = 'awaiting_report_comment_and_count'
    
    elif user_state == 'awaiting_channel_link_for_auto_report':
        context.user_data['channel_link_for_auto_report'] = user_message
        keyboard_buttons = [[InlineKeyboardButton(text=key, callback_data=f'add_channel_type_{key}')] for key in REPORT_OPTIONS.keys()]
        reply_markup = InlineKeyboardMarkup(keyboard_buttons)
        await update.message.reply_text("Please choose a report type for this channel:", reply_markup=reply_markup)
        context.user_data['state'] = 'awaiting_add_report_type'

    elif user_state == 'awaiting_add_report_message_and_count':
        try:
            parts = user_message.rsplit(' ', 1)
            report_message = parts[0].strip()
            report_count = int(parts[1].strip())
            
            link = context.user_data.get('channel_link_for_auto_report')
            report_type = context.user_data.get('add_report_type_text')
            
            if not link or not report_type:
                 await update.message.reply_text("An error occurred. Please start the process again from the menu.")
                 context.user_data['state'] = None
                 return

            add_channel_to_db(link, report_type, report_message, report_count)
            await update.message.reply_text(f"✅ Channel {link} has been added to the auto-report list. It will be reported automatically when new posts are detected.")
            
            context.user_data['state'] = None
            context.user_data.pop('channel_link_for_auto_report', None)
            context.user_data.pop('add_report_type_text', None)
            
        except (ValueError, IndexError):
            await update.message.reply_text("❌ Invalid format. Please provide a comment and a number separated by a space.")
            context.user_data['state'] = 'awaiting_add_report_message_and_count'
            
    elif user_state == 'awaiting_join_link':
        link = user_message
        user_id = update.effective_user.id
        
        accounts = get_logged_in_accounts(user_id)
        if not accounts:
            await update.message.reply_text("❌ No accounts are logged in to join the channel. Please log in first.")
            context.user_data['state'] = None
            return
        
        join_tasks = []
        for phone_number, account_user_id in accounts:
            join_tasks.append(join_channel_for_account(update, context, link, phone_number, account_user_id))
        
        await asyncio.gather(*join_tasks)

async def join_channel_for_account(update: Update, context: ContextTypes.DEFAULT_TYPE, link: str, phone_number: str, account_user_id: int):
    session_folder = os.path.join(SESSION_FOLDER, str(account_user_id))
    session_path = os.path.join(session_folder, phone_number)
    
    if phone_number not in session_locks:
        session_locks[phone_number] = asyncio.Lock()
    
    async with session_locks[phone_number]:
        if not os.path.exists(session_path + '.session'):
            await context.bot.send_message(chat_id=update.effective_chat.id, text=f"❌ **{mask_phone_number(phone_number)}:** Session file not found. Skipping.")
            return

        client = TelegramClient(session_path, API_ID, API_HASH)
        
        try:
            await client.connect()
            if not await client.is_user_authorized():
                await context.bot.send_message(chat_id=update.effective_chat.id, text=f"❌ **{mask_phone_number(phone_number)}:** Account not authorized. Skipping.")
                return

            if link.startswith('https://t.me/+'):
                invite_hash = link.split('+')[1]
                await client(ImportChatInviteRequest(invite_hash))
                await context.bot.send_message(chat_id=update.effective_chat.id, text=f"✅ **{mask_phone_number(phone_number)}:** Joined the private channel successfully!")
            elif link.startswith('https://t.me/'):
                channel_username = link.split('/')[-1]
                await client(JoinChannelRequest(channel=channel_username))
                await context.bot.send_message(chat_id=update.effective_chat.id, text=f"✅ **{mask_phone_number(phone_number)}:** Joined the public channel successfully!")
            else:
                await context.bot.send_message(chat_id=update.effective_chat.id, text=f"⚠️ **{mask_phone_number(phone_number)}:** Invalid link format.")

        except UserAlreadyParticipantError:
            await context.bot.send_message(chat_id=update.effective_chat.id, text=f"⚠️ **{mask_phone_number(phone_number)}:** Already a member of this channel.")
        except RPCError as e:
            await context.bot.send_message(chat_id=update.effective_chat.id, text=f"❌ **{mask_phone_number(phone_number)}:** Failed to join channel. Reason: {e}")
        except Exception as e:
            await context.bot.send_message(chat_id=update.effective_chat.id, text=f"❌ **{mask_phone_number(phone_number)}:** An unexpected error occurred. Reason: {e}")
            logging.error(f"Error joining channel for {phone_number}: {traceback.format_exc()}")
        finally:
            if client.is_connected():
                await client.disconnect()

async def start_reporting_process(update, context, accounts_to_use, target_link, report_type_text, report_count, report_message, task_id, user_id):
    delay_per_report = 5
    if len(accounts_to_use) == 1:
        delay_per_report = 10
    await_tasks = []
    for i in range(report_count):
        for phone_number, account_user_id in accounts_to_use:
            await asyncio.sleep(delay_per_report)
            task = asyncio.create_task(send_single_report(update, context, phone_number, target_link, report_type_text, i + 1, report_count, report_message, task_id, user_id, account_user_id))
            await_tasks.append(task)
    await asyncio.gather(*await_tasks, return_exceptions=True)
    if user_id in user_tasks and task_id in user_tasks[user_id]:
        del user_tasks[user_id][task_id]
    await context.bot.send_message(chat_id=update.effective_chat.id, text=f"✅ Reporting task #{task_id} has been completed.")

async def start_email_reporting_process(update, context, email_accounts, target_link, report_type_text, report_count, report_message, task_id, user_id, attachment):
    delay_per_report = 5
    await_tasks = []
    for i in range(report_count):
        for account in email_accounts:
            await asyncio.sleep(delay_per_report)
            task = asyncio.create_task(send_single_email_report(update, context, account, target_link, report_type_text, i + 1, report_count, report_message, task_id, attachment))
            await_tasks.append(task)
    await asyncio.gather(*await_tasks, return_exceptions=True)
    if user_id in user_tasks and task_id in user_tasks[user_id]:
        del user_tasks[user_id][task_id]
    await context.bot.send_message(chat_id=update.effective_chat.id, text=f"✅ Email reporting task #{task_id} has been completed.")

async def stop_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    try:
        task_id = int(context.args[0])
        if user_id in user_tasks and task_id in user_tasks[user_id]:
            main_task = user_tasks[user_id][task_id]
            main_task.cancel()
            await update.message.reply_text(f"✅ The reporting loop with task #{task_id} has been requested to stop.")
            del user_tasks[user_id][task_id]
        else:
            await update.message.reply_text("❌ Task not found. Please provide a valid task number.")
    except (IndexError, ValueError):
        await update.message.reply_text("❌ Please specify the task number you want to stop. Example: `/stop 1`")
    except Exception as e:
            await update.message.reply_text(f"An error occurred while stopping the task: {e}")

async def send_single_email_report(update: Update, context: ContextTypes.DEFAULT_TYPE, account: dict, target_link, report_type_text, current_report_count, total_report_count, report_message, task_id, attachment):
    sender_email = account['email']
    sender_password = account['password']
    receiver_email = "abuse@telegram.org"
    smtp_server, smtp_port = get_email_server(sender_email)
    
    if not smtp_server:
        await context.bot.send_message(chat_id=update.effective_chat.id, text=f"❌ Failed to send report from {sender_email}. Unknown email provider.")
        return

    subject = f"Urgent Report: Illegal Activity on Telegram - {report_type_text}"
    body = f"""
Dear Telegram Abuse Team,

I am writing to report a serious violation of Telegram's Terms of Service. The channel and/or post linked below is involved in illegal activities.

**Channel/Post Link:**
{target_link}

**Report Category:**
{report_type_text}

**Additional Details:**
{report_message}

I kindly request that you take immediate action to remove this content and ban the channel to prevent further damage.

Thank you for your prompt attention to this matter.

Best regards,

A concerned user
"""

    try:
        msg = MIMEMultipart()
        msg['From'] = sender_email
        msg['To'] = receiver_email
        msg['Subject'] = subject
        msg.attach(MIMEText(body, 'plain'))
        if attachment:
            file = await context.bot.get_file(attachment)
            photo_data = io.BytesIO()
            await file.download_to_memory(photo_data)
            photo_data.seek(0)
            image = MIMEImage(photo_data.read(), name='screenshot.png')
            image.add_header('Content-Disposition', 'attachment', filename='screenshot.png')
            msg.attach(image)
        with smtplib.SMTP(smtp_server, smtp_port) as server:
            server.starttls()
            server.login(sender_email, sender_password)
            server.sendmail(sender_email, receiver_email, msg.as_string())
        await context.bot.send_message(chat_id=update.effective_chat.id, text=f"✅ Email Report Send {current_report_count}/{total_report_count} task #{task_id}.\n\nfrom {sender_email} sent successfully.")
    except smtplib.SMTPAuthenticationError as e:
        await context.bot.send_message(chat_id=update.effective_chat.id, text=f"❌ Email report {current_report_count}/{total_report_count} from {sender_email} failed. **Authentication Error.** Please check your password or App Password and try again. Error: {e}")
    except smtplib.SMTPException as e:
        await context.bot.send_message(chat_id=update.effective_chat.id, text=f"❌ Email report {current_report_count}/{total_report_count} from {sender_email} failed. **SMTP Error.** Please check your internet connection or email provider settings. Error: {e}")
    except Exception as e:
        await context.bot.send_message(chat_id=update.effective_chat.id, text=f"❌ Email report {current_report_count}/{total_report_count} from {sender_email} failed. **Reason:** {e}")
        print(traceback.format_exc())

async def send_single_report(update: Update, context: ContextTypes.DEFAULT_TYPE, phone_number, target_link, report_type_text, current_report_count, total_report_count, report_message, task_id, user_id, account_user_id):
    if phone_number not in session_locks:
        session_locks[phone_number] = asyncio.Lock()
    async with session_locks[phone_number]:
        session_folder = os.path.join(SESSION_FOLDER, str(account_user_id))
        session_path = os.path.join(session_folder, phone_number)
        if not os.path.exists(session_folder):
            await context.bot.send_message(chat_id=update.effective_chat.id, text=f"❌ Account {mask_phone_number(phone_number)}'s session folder not found. Skipping.")
            return
        if not os.path.exists(session_path + '.session'):
            await context.bot.send_message(chat_id=update.effective_chat.id, text=f"❌ Account {mask_phone_number(phone_number)}'s session file not found. Skipping.")
            return
        client = TelegramClient(session_path, API_ID, API_HASH)
        await client.connect()
        if not await client.is_user_authorized():
            await client.disconnect()
            await context.bot.send_message(chat_id=update.effective_chat.id, text=f"Account {mask_phone_number(phone_number)} is not authorized. Skipping reports for task #{task_id}.")
            return
        try:
            match = re.search(r't\.me/([^/]+)/(\d+)', target_link)
            if match:
                channel_name = match.group(1)
                message_id = int(match.group(2))
                entity = await client.get_entity(channel_name)
                report_option_byte = None
                found_subtype = False
                for main_type, subtypes in REPORT_SUBTYPES.items():
                    if report_type_text in subtypes:
                        report_option_byte = subtypes[report_type_text]
                        found_subtype = True
                        break
                if not found_subtype:
                    report_option_byte = REPORT_OPTIONS.get(report_type_text)
                if report_option_byte is None:
                    await context.bot.send_message(chat_id=update.effective_chat.id, text=f"❌ Invalid report type selected: {report_type_text}. Skipping.")
                    return
                result = await client(ReportRequest(peer=entity, id=[message_id], option=report_option_byte, message=report_message))
                response_message = f"✅ Report Send {current_report_count}/{total_report_count} task #{task_id}.\n\n"
                response_message += f"from {mask_phone_number(phone_number)} sent successfully\n\n"
                response_message += f"Original api response: {str(result)}"
                await context.bot.send_message(chat_id=update.effective_chat.id, text=response_message)
                add_report_record(channel_name, message_id, result, 'Success')
            else:
                entity = await client.get_entity(target_link)
                result = await client(ReportSpamRequest(peer=entity))
                response_message = f"✅ Report Send {current_report_count}/{total_report_count} task #{task_id}.\n\n"
                response_message += f"from {mask_phone_number(phone_number)} sent successfully\n\n"
                response_message += f"Original api response: {str(result)}"
                await context.bot.send_message(chat_id=update.effective_chat.id, text=response_message)
                add_report_record(entity.username, None, result, 'Success')
        except asyncio.CancelledError:
            await context.bot.send_message(chat_id=update.effective_chat.id, text=f"Reporting task #{task_id} was cancelled for {mask_phone_number(phone_number)}.")
            raise
        except (RPCError, FloodWaitError) as e:
            await context.bot.send_message(chat_id=update.effective_chat.id, text=f"❌ Report {current_report_count}/{total_report_count} from {mask_phone_number(phone_number)} failed for task #{task_id}. Reason: {e}")
            add_report_record(target_link, message_id if 'message_id' in locals() else None, str(e), 'Failed')
        except Exception as e:
            await context.bot.send_message(chat_id=update.effective_chat.id, text=f"❌ Report {current_report_count}/{total_report_count} from {mask_phone_number(phone_number)} failed for task #{task_id}. Reason: {e}")
            print(traceback.format_exc())
            add_report_record(target_link, message_id if 'message_id' in locals() else None, str(e), 'Failed')
        finally:
            if client.is_connected():
                await client.disconnect()

async def get_user_channels(query: Update.callback_query, context: ContextTypes.DEFAULT_TYPE, phone_number: str, account_user_id: int):
    chat_id = query.message.chat_id
    if phone_number not in session_locks:
        session_locks[phone_number] = asyncio.Lock()
    async with session_locks[phone_number]:
        session_folder = os.path.join(SESSION_FOLDER, str(account_user_id))
        session_path = os.path.join(session_folder, phone_number)
        try:
            if not os.path.exists(session_path + '.session'):
                await context.bot.send_message(chat_id=chat_id, text=f"❌ The session file for account {mask_phone_number(phone_number)} was not found at `{session_path}.session`. Please re-login this account to fix this.")
                return
            client = TelegramClient(session_path, API_ID, API_HASH)
            await client.connect()
            if not await client.is_user_authorized():
                await client.disconnect()
                await context.bot.send_message(chat_id=chat_id, text=f"❌ Account {mask_phone_number(phone_number)} is not authorized. Please re-login.")
                return
            dialogs = await client(GetDialogsRequest(offset_date=None, offset_id=0, offset_peer=InputPeerChannel(channel_id=0, access_hash=0), limit=200, hash=0))
            channels = [d.entity.title for d in dialogs.chats if isinstance(d.entity, Channel)]
            if channels:
                channel_list_text = "\n".join(channels)
                await context.bot.send_message(chat_id=chat_id, text=f"Channels for account {mask_phone_number(phone_number)}:\n\n{channel_list_text}")
            else:
                await context.bot.send_message(chat_id=chat_id, text=f"Account {mask_phone_number(phone_number)} has not joined any channels.")
        except Exception as e:
            error_details = f"❌ An error occurred while fetching channels for account {mask_phone_number(phone_number)}.\n\n**Original Error:**\n```\n{traceback.format_exc()}\n```"
            await context.bot.send_message(chat_id=chat_id, text=error_details)
        finally:
            if 'client' in locals() and client and client.is_connected():
                await client.disconnect()
            await context.bot.send_message(chat_id=chat_id, text=f"✅ Channel fetching for account {mask_phone_number(phone_number)} completed.")

async def create_full_backup(query: Update.callback_query, context: ContextTypes.DEFAULT_TYPE):
    chat_id = query.message.chat_id
    try:
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, 'w', zipfile.Z_DEFLATED) as zipf:
            project_dir = os.getcwd()
            for root, dirs, files in os.walk(project_dir):
                dirs[:] = [d for d in dirs if d not in ['.venv', '__pycache__', '.git', '.idea']]
                for file in files:
                    if file.endswith(('.session-journal')):
                        continue
                    file_path = os.path.join(root, file)
                    arcname = os.path.relpath(file_path, project_dir)
                    zipf.write(file_path, arcname=arcname)
        zip_buffer.seek(0)
        backup_filename = f"full_project_backup_{datetime.now().strftime('%Y-%m-%d')}.zip"
        await context.bot.send_document(chat_id=chat_id, document=zip_buffer, filename=backup_filename)
    except Exception as e:
        await context.bot.send_message(chat_id=chat_id, text=f"An error occurred while creating the backup: {e}")

async def list_granted_users(query: Update.callback_query, context: ContextTypes.DEFAULT_TYPE):
    chat_id = query.message.chat_id
    granted_users = load_granted_users()
    if not granted_users:
        await context.bot.send_message(chat_id=chat_id, text="No users have been granted access yet.")
        return
    keyboard = []
    for user in granted_users:
        user_id = user['user_id']
        expires_at = datetime.fromisoformat(user['expires_at']).strftime('%Y-%m-%d %H:%M')
        access_type = "All Access" if user.get('all_access') else "Limited"
        row = [
            InlineKeyboardButton(text=f"User: {user_id} ({access_type}, Expires: {expires_at})", callback_data='_'),
            InlineKeyboardButton(text="B", callback_data=f'delete_access_{user_id}'),
            InlineKeyboardButton(text="R", callback_data=f'reset_access_{user_id}')
        ]
        keyboard.append(row)
    reply_markup = InlineKeyboardMarkup(keyboard)
    await context.bot.send_message(chat_id=chat_id, text="Granted Users List:", reply_markup=reply_markup)

async def delete_access(query: Update.callback_query, context: ContextTypes.DEFAULT_TYPE, user_to_delete: int):
    chat_id = query.message.chat_id
    granted_users = load_granted_users()
    updated_users = [user for user in granted_users if user['user_id'] != user_to_delete]
    if len(updated_users) < len(granted_users):
        save_granted_users(updated_users)
        await context.bot.send_message(chat_id=chat_id, text=f"✅ Access for user {user_to_delete} has been revoked.")
    else:
        await context.bot.send_message(chat_id=chat_id, text=f"User {user_to_delete} not found in granted list.")
    await list_granted_users(query, context)

async def manage_accounts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = update.effective_user.id
    is_user_granted_access = is_granted_user(user_id)
    user_info = get_granted_user_info(user_id)
    all_access = is_owner(user_id) or (is_user_granted_access and user_info.get('all_access'))
    accounts = get_logged_in_accounts(user_id, all_access)
    if not accounts:
        await query.edit_message_text("No accounts are currently logged in.")
        return
    keyboard = []
    for phone_number, account_user_id in accounts:
        keyboard.append([
            InlineKeyboardButton(
                text=f"{mask_phone_number(phone_number)} (User: {account_user_id})",
                callback_data=f'view_account_{phone_number}_{account_user_id}'
            )
        ])
    keyboard.append([InlineKeyboardButton("Back ↩️", callback_data='start')])
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text("Please select an account to manage:", reply_markup=reply_markup)

async def delete_account(update: Update, context: ContextTypes.DEFAULT_TYPE, phone_number: str, account_user_id: str):
    query = update.callback_query
    session_file_path = os.path.join(SESSION_FOLDER, account_user_id, f'{phone_number}.session')
    try:
        if os.path.exists(session_file_path):
            os.remove(session_file_path)
            journal_file_path = f"{session_file_path}-journal"
            if os.path.exists(journal_file_path):
                os.remove(journal_file_path)
            await query.edit_message_text(f"✅ Session file for {mask_phone_number(phone_number)} has been deleted.")
        else:
            await query.edit_message_text(f"❌ Session file for {mask_phone_number(phone_number)} not found.")
    except Exception as e:
        await query.edit_message_text(f"❌ An error occurred while deleting the session file: {e}")
    await manage_accounts(update, context)

async def main_run() -> None:
    init_files()
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Add handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("stop", stop_command_handler))
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(MessageHandler(filters.TEXT | filters.PHOTO & ~filters.COMMAND, message_handler))

    # Create the Telegram bot polling task
    # یہ کام asyncio.create_task() کے ذریعے ایک الگ ٹاسک کے طور پر چلایا جائے گا
    application_task = asyncio.create_task(application.run_polling())

    # Initialize Telethon client
    detection_session_path = os.path.join(SESSION_FOLDER, str(OWNER_ID), DETECTION_SESSION_PHONE)
    telethon_client = TelegramClient(detection_session_path, API_ID, API_HASH)
    
    try:
        # Check if the detection account is authorized
        if not await telethon_client.is_user_authorized():
            logging.warning(f"Detection session file not found or not authorized for {DETECTION_SESSION_PHONE}. Please login this account via the bot menu.")
            await send_owner_error(f"⚠️ **Warning:**\nDetection session file not found or not authorized for `{DETECTION_SESSION_PHONE}`. Please login this account via the bot menu to enable channel post detection.")
            # If Telethon client is not ready, we will still run the Telegram.ext bot
            await application_task
        else:
            # Start the Telethon client in a separate task
            telethon_task = asyncio.create_task(telethon_client.run_until_disconnected())
            
            # Use asyncio.gather() to run both tasks concurrently
            # دونوں tasks کو ایک ہی event loop میں چلانے کے لیے
            await asyncio.gather(application_task, telethon_task)
            
    except SessionPasswordNeededError:
        logging.error("Two-factor authentication is enabled on the detection account. Please log in again and provide the password.")
        await send_owner_error(f"❌ **Error:**\nTwo-factor authentication is enabled on the detection account `{DETECTION_SESSION_PHONE}`. Please log in again and provide the password.")
        # If Telethon client fails, we only run the Telegram.ext bot
        await application_task
    except Exception as e:
        logging.error(f"Failed to connect Telethon client for detection: {e}")
        await send_owner_error(f"❌ **Critical Error:**\nFailed to connect Telethon client for detection. Post detection will not work.\n\n**Error Details:**\n`{type(e).__name__}: {str(e)}`")
        # If Telethon client fails, we only run the Telegram.ext bot
        await application_task

# Main entry point for the script
if __name__ == '__main__':
    asyncio.run(main_run())

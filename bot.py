import os
import asyncio
import re
import zipfile
import io
import json
from datetime import datetime, timedelta
from telethon import TelegramClient
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
from telethon.tl.functions.messages import ReportRequest, ReportSpamRequest, ImportChatInviteRequest
from telethon.tl.types import (
    InputPeerChannel, Channel
)
from telethon.errors import RPCError, FloodWaitError
import traceback

# --- OWNER DETAILS & BOT CONFIGURATION ---
# Replace with your own Telegram Chat ID and Username
OWNER_ID = 8167904992  # Replace with your actual Telegram Chat ID
OWNER_USERNAME = "whatsapp_offcial"  # Replace with your actual Telegram Username

API_ID = 94575
API_HASH = 'a3406de8d171bb422bb6ddf3bbd800e2'
BOT_TOKEN = '8324191756:AAF28XJJ9wSO2jZ5iFIqlrdEbjqHFX190Pk'

SESSION_FOLDER = 'sessions'
GRANTED_USERS_FILE = 'granted_users.json'

# Map report types to the byte values from the API response
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

session_locks = {}
# نیا گلوبل ڈکشنری جو چلنے والے ٹاسک کو اسٹور کرے گا
# Key: user_id, Value: dict of tasks {task_id: task_object}
user_tasks = {}

# Ensure folders and files exist
if not os.path.exists(SESSION_FOLDER):
    os.makedirs(SESSION_FOLDER)
if not os.path.exists(GRANTED_USERS_FILE):
    with open(GRANTED_USERS_FILE, 'w') as f:
        json.dump([], f)

def load_granted_users():
    with open(GRANTED_USERS_FILE, 'r') as f:
        return json.load(f)

def save_granted_users(users):
    with open(GRANTED_USERS_FILE, 'w') as f:
        json.dump(users, f, indent=4)

def is_owner(user_id):
    return user_id == OWNER_ID

def is_granted_user(user_id):
    granted_users = load_granted_users()
    for user in granted_users:
        if user['user_id'] == user_id:
            expires_at = datetime.fromisoformat(user['expires_at'])
            if datetime.now() < expires_at:
                return True
    return False

# --- Handlers for Telegram Bot ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id

    if is_owner(user_id):
        keyboard = [
            [InlineKeyboardButton("Login 🔐", callback_data='login_start')],
            [InlineKeyboardButton("Join Channel ➕", callback_data='join_channel')],
            [InlineKeyboardButton("Report Illegal Content 🚨", callback_data='report_start')],
            [InlineKeyboardButton("My Accounts 👤", callback_data='my_accounts')],
            [InlineKeyboardButton("My Channels 👥", callback_data='my_channels')],
            [InlineKeyboardButton("Backup 💾", callback_data='backup_sessions')],
            [InlineKeyboardButton("Manage Users 🗂️", callback_data='manage_users')],
            [InlineKeyboardButton("Grant Access ✨", callback_data='grant_access')]
        ]
        text = 'Hello Owner! Please choose an option:'
    elif is_granted_user(user_id):
        keyboard = [
            [InlineKeyboardButton("Login 🔐", callback_data='login_start')],
            [InlineKeyboardButton("Report Illegal Content 🚨", callback_data='report_start')],
        ]
        text = 'Hello! You have limited access. Please choose an option:'
    else:
        keyboard = [[InlineKeyboardButton("Contact Owner 👤", url=f"https://t.me/{OWNER_USERNAME}")]]
        text = 'You cannot access this bot.'

    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(text, reply_markup=reply_markup)

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id

    # Logic for ALL users (owner and granted)
    if is_owner(user_id) or is_granted_user(user_id):
        if query.data == 'login_start':
            await query.edit_message_text(text="Please send your phone number with country code (e.g., +923001234567) to log in.")
            context.user_data['state'] = 'awaiting_phone_number'
        
        elif query.data == 'report_start':
            await query.edit_message_text(text="Please send the link of the channel or a post you want to report.")
            context.user_data['state'] = 'awaiting_link'

        elif query.data.startswith('report_type_'):
            report_type_text = query.data.split('_', 2)[-1]
            context.user_data['report_type_text'] = report_type_text
            await query.edit_message_text(f"You selected '{report_type_text}'. Now, please provide a brief message explaining the violation and then the number of times to report (e.g., 'Violent content, 5').")
            context.user_data['state'] = 'awaiting_report_comment_and_count'
    
    # Logic only for Owner
    if is_owner(user_id):
        if query.data == 'join_channel':
            await query.edit_message_text(text="Please send the invite link of the channel you want to join (e.g., https://t.me/+AbCdeFghIjklMnOp).")
            context.user_data['state'] = 'awaiting_join_link'
        
        elif query.data == 'my_accounts':
            accounts = get_logged_in_accounts()
            if accounts:
                account_list = "\n".join([f"- {acc}" for acc in accounts])
                await query.edit_message_text(f"Logged in accounts:\n{account_list}")
            else:
                await query.edit_message_text("No accounts are currently logged in.")

        elif query.data == 'my_channels':
            accounts = get_logged_in_accounts()
            if not accounts:
                await query.edit_message_text("No accounts are currently logged in.")
                return
            
            keyboard = [[InlineKeyboardButton(text=phone, callback_data=f'show_channels_{phone}')] for phone in accounts]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_text("Please select an account to view its channels:", reply_markup=reply_markup)

        elif query.data.startswith('show_channels_'):
            phone_number = query.data.split('_', 2)[-1]
            await query.edit_message_text(f"Fetching channels for account {phone_number}. This may take a moment...")
            await get_user_channels(query, context, phone_number)
            
        elif query.data == 'backup_sessions':
            await query.edit_message_text("Creating backup of your sessions folder. Please wait...")
            await create_backup(query, context)
            await query.message.reply_text("Backup process completed.")

        elif query.data == 'manage_users':
            await query.edit_message_text("Fetching list of granted users...")
            await list_granted_users(query, context)
            
        elif query.data == 'grant_access':
            await query.edit_message_text("Please send the user's Chat ID or Username and duration (e.g., `123456789 1h`, `username 1d`).")
            context.user_data['state'] = 'awaiting_grant_info'

        elif query.data.startswith('delete_access_'):
            user_to_delete = int(query.data.split('_', 2)[-1])
            await delete_access(query, context, user_to_delete)
        
        elif query.data.startswith('reset_access_'):
            user_to_reset = int(query.data.split('_', 2)[-1])
            context.user_data['state'] = 'awaiting_reset_info'
            context.user_data['user_to_reset'] = user_to_reset
            await query.edit_message_text(f"Please send the new duration for user {user_to_reset} (e.g., `1h`, `1d`).")

async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_message = update.message.text
    user_state = context.user_data.get('state')
    user_id = update.effective_user.id
    
    # Grant Access state for Owner
    if is_owner(user_id) and user_state == 'awaiting_grant_info':
        parts = user_message.split()
        if len(parts) != 2:
            await update.message.reply_text("Invalid format. Please provide the ID/Username and duration (e.g., `123456789 1h`).")
            context.user_data['state'] = None
            return

        target_str, duration_str = parts
        try:
            # Try to get user ID from username if provided
            try:
                if not target_str.isdigit():
                    chat_id = (await context.bot.get_chat(target_str)).id
                else:
                    chat_id = int(target_str)
            except Exception:
                await update.message.reply_text("Could not find a user with that ID or Username. Please try again.")
                context.user_data['state'] = None
                return

            # Parse duration
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
                    user_found = True
                    break
            
            if not user_found:
                granted_users.append({'user_id': chat_id, 'expires_at': expires_at})
            
            save_granted_users(granted_users)
            await update.message.reply_text(f"✅ Access granted to user ID {chat_id} until {datetime.fromisoformat(expires_at).strftime('%Y-%m-%d %H:%M')}.")
            context.user_data['state'] = None

        except (ValueError, IndexError):
            await update.message.reply_text("Invalid format. Please provide the ID and duration (e.g., `123456789 1h`).")
            context.user_data['state'] = None

    elif is_owner(user_id) and user_state == 'awaiting_reset_info':
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

    elif (is_owner(user_id) or is_granted_user(user_id)) and user_state == 'awaiting_phone_number':
        phone_number = user_message
        try:
            client = TelegramClient(os.path.join(SESSION_FOLDER, phone_number), API_ID, API_HASH)
            await client.connect()
            if not await client.is_user_authorized():
                await client.send_code_request(phone_number)
                context.user_data['client'] = client
                await update.message.reply_text("OTP has been sent to your number. Please enter the code.")
                context.user_data['state'] = 'awaiting_otp'
            else:
                await update.message.reply_text("This account is already logged in.")
                await client.disconnect()
                context.user_data['state'] = None
        except Exception as e:
            await update.message.reply_text(f"An error occurred: {e}. Please try again.")
            context.user_data['state'] = None

    elif (is_owner(user_id) or is_granted_user(user_id)) and user_state == 'awaiting_otp':
        otp = user_message
        client = context.user_data.get('client')
        if not client:
            await update.message.reply_text("Something went wrong. Please start the login process again.")
            context.user_data['state'] = None
            return

        try:
            await client.sign_in(code=otp)
            await update.message.reply_text("Successfully logged in! Your session file has been saved.")
            context.user_data['state'] = None
            context.user_data.pop('client', None)
        except Exception as e:
            await update.message.reply_text(f"Invalid OTP. Please try again.")
            
    elif (is_owner(user_id) or is_granted_user(user_id)) and user_state == 'awaiting_link':
        context.user_data['target_link'] = user_message
        # Provide the buttons for report types immediately
        keyboard_buttons = [[InlineKeyboardButton(text=key, callback_data=f'report_type_{key}')] for key in REPORT_OPTIONS.keys()]
        reply_markup = InlineKeyboardMarkup(keyboard_buttons)
        await update.message.reply_text("Please choose a report type:", reply_markup=reply_markup)
        context.user_data['state'] = 'awaiting_report_type_selection'

    elif is_owner(user_id) and user_state == 'awaiting_join_link':
        invite_link = user_message
        accounts = get_logged_in_accounts()
        if not accounts:
            await update.message.reply_text("No accounts logged in to join channels.")
            return

        # یہاں ہم نے JoinChannel کا کام بھی بیک گراؤنڈ ٹاسک میں ڈال دیا ہے
        task = asyncio.create_task(join_channels_in_background(update, context, invite_link, accounts))
        await update.message.reply_text("All join requests have been sent. They are now processing in the background.")
        context.user_data['state'] = None

    elif (is_owner(user_id) or is_granted_user(user_id)) and user_state == 'awaiting_report_comment_and_count':
        try:
            # Split the user message to get the comment and the count
            parts = user_message.rsplit(',', 1)
            report_message = parts[0].strip()
            report_count = int(parts[1].strip())
            
            target_link = context.user_data.get('target_link')
            report_type_text = context.user_data.get('report_type_text')

            # یہاں ہم نے پورا رپورٹنگ کا عمل ایک بیک گراؤنڈ ٹاسک میں ڈال دیا ہے
            # تاکہ بوٹ دوسرے یوزرز کے لیے آزاد ہو
            
            # ٹاسک کے لیے ایک یونیک ID بنائیں
            if user_id not in user_tasks:
                user_tasks[user_id] = {}
            task_id = len(user_tasks[user_id]) + 1
            
            await update.message.reply_text(f"Starting to report '{target_link}' for you. This is task #{task_id}.")
            
            task = asyncio.create_task(run_reports_in_background(update, context, target_link, report_type_text, report_count, report_message, task_id))
            user_tasks[user_id][task_id] = task

            context.user_data['state'] = None
            
        except (ValueError, IndexError):
            await update.message.reply_text("Please provide a comment and a number separated by a comma (e.g., 'Violent content, 5').")
            context.user_data['state'] = 'awaiting_report_comment_and_count'

async def stop_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    try:
        # Get the task ID from the command arguments
        task_id = int(context.args[0])
        
        if user_id in user_tasks and task_id in user_tasks[user_id]:
            task_to_cancel = user_tasks[user_id][task_id]
            task_to_cancel.cancel()
            await update.message.reply_text(f"✅ The reporting loop with task #{task_id} has been requested to stop.")
            # ٹاسک کو لسٹ سے ہٹا دیں
            del user_tasks[user_id][task_id]
        else:
            await update.message.reply_text("❌ Task not found. Please provide a valid task number.")
    except (IndexError, ValueError):
        await update.message.reply_text("❌ Please specify the task number you want to stop. Example: `/stop 1`")
    except Exception as e:
        await update.message.reply_text(f"An error occurred while stopping the task: {e}")

# --- Helper Functions ---

def get_logged_in_accounts():
    accounts = []
    for filename in os.listdir(SESSION_FOLDER):
        if filename.endswith('.session'):
            accounts.append(os.path.splitext(filename)[0])
    return accounts

async def run_reports_in_background(update, context, target_link, report_type_text, count, report_message, task_id):
    logged_in_accounts = get_logged_in_accounts()
    if not logged_in_accounts:
        await context.bot.send_message(chat_id=update.effective_chat.id, text=f"No accounts logged in to send reports for task #{task_id}.")
        return

    for phone in logged_in_accounts:
        # Check if the task was cancelled before starting a new account's reports
        if asyncio.current_task().cancelled():
            return
            
        await context.bot.send_message(chat_id=update.effective_chat.id, text=f"Sending reports with account: {phone} for task #{task_id}...")
        await send_reports(update, context, phone, target_link, report_type_text, count, report_message, task_id)

    await context.bot.send_message(chat_id=update.effective_chat.id, text=f"All reports for task #{task_id} have been processed. Thank you.")
    
    # Task list سے اس ٹاسک کو ختم کر دیں
    user_id = update.effective_user.id
    if user_id in user_tasks and task_id in user_tasks[user_id]:
        del user_tasks[user_id][task_id]


async def send_reports(update: Update, context: ContextTypes.DEFAULT_TYPE, phone_number, target_link, report_type_text, count, report_message, task_id):
    if phone_number not in session_locks:
        session_locks[phone_number] = asyncio.Lock()
    
    async with session_locks[phone_number]:
        client = TelegramClient(os.path.join(SESSION_FOLDER, phone_number), API_ID, API_HASH)
        await client.connect()
        
        if not await client.is_user_authorized():
            await client.disconnect()
            await context.bot.send_message(chat_id=update.effective_chat.id, text=f"Account {phone_number} is not authorized. Skipping reports for task #{task_id}.")
            return
        
        try:
            match = re.search(r't\.me/([^/]+)/(\d+)', target_link)
            
            if match:
                channel_name = match.group(1)
                message_id = int(match.group(2))
                entity = await client.get_entity(channel_name)
                
                # Get the correct byte value from our mapping
                report_option_byte = REPORT_OPTIONS.get(report_type_text)

                for i in range(count):
                    try:
                        # اگر لوپ کو روکنے کی درخواست آئی تو یہ یہاں رک جائے گا
                        if asyncio.current_task().cancelled():
                            await context.bot.send_message(chat_id=update.effective_chat.id, text=f"Reporting task #{task_id} was cancelled.")
                            return

                        result = await client(ReportRequest(peer=entity, id=[message_id], option=report_option_byte, message=report_message))
                        await context.bot.send_message(chat_id=update.effective_chat.id, text=f"✅ Report {i+1}/{count} from {phone_number} sent successfully for task #{task_id}.")
                    except asyncio.CancelledError:
                        raise # ضروری ہے کہ یہ ایرر اوپر بھیجی جائے تاکہ ٹاسک رک سکے
                    except (RPCError, FloodWaitError) as e:
                        await context.bot.send_message(chat_id=update.effective_chat.id, text=f"❌ Report {i+1}/{count} from {phone_number} failed for task #{task_id}. Reason: {e}")
                    except Exception as e:
                        await context.bot.send_message(chat_id=update.effective_chat.id, text=f"❌ Report {i+1}/{count} from {phone_number} failed for task #{task_id}. Reason: {e}")
                    await asyncio.sleep(10) # 10 سیکنڈ انتظار تاکہ فلڈ ایرر نہ آئے
            else:
                entity = await client.get_entity(target_link)
                
                for i in range(count):
                    try:
                        if asyncio.current_task().cancelled():
                            await context.bot.send_message(chat_id=update.effective_chat.id, text=f"Reporting task #{task_id} was cancelled.")
                            return
                        
                        result = await client(ReportSpamRequest(peer=entity))
                        await context.bot.send_message(chat_id=update.effective_chat.id, text=f"✅ Report {i+1}/{count} from {phone_number} sent successfully for task #{task_id}.")
                    except asyncio.CancelledError:
                        raise
                    except (RPCError, FloodWaitError) as e:
                        await context.bot.send_message(chat_id=update.effective_chat.id, text=f"❌ Report {i+1}/{count} from {phone_number} failed for task #{task_id}. Reason: {e}")
                    except Exception as e:
                        await context.bot.send_message(chat_id=update.effective_chat.id, text=f"❌ Report {i+1}/{count} from {phone_number} failed for task #{task_id}. Reason: {e}")
                    await asyncio.sleep(10)

        except asyncio.CancelledError:
            # اگر ٹاسک کینسل ہو جائے تو یہاں پر کنیکٹ ختم کر دیں
            await client.disconnect()
            raise
        except Exception as e:
            await context.bot.send_message(chat_id=update.effective_chat.id, text=f"An error occurred with account {phone_number}: {e}")
            await client.disconnect()
        finally:
            await client.disconnect()

async def join_channels_in_background(update, context, invite_link, accounts):
    for phone_number in accounts:
        # ہر اکاؤنٹ کے لیے جوائن کا کام ایک ایک کرکے کریں
        await join_channel(update, context, phone_number, invite_link)
    
    await update.message.reply_text("All join requests have been processed.")
    
async def join_channel(update: Update, context: ContextTypes.DEFAULT_TYPE, phone_number: str, invite_link: str):
    if phone_number not in session_locks:
        session_locks[phone_number] = asyncio.Lock()

    async with session_locks[phone_number]:
        client = TelegramClient(os.path.join(SESSION_FOLDER, phone_number), API_ID, API_HASH)
        await client.connect()

        if not await client.is_user_authorized():
            await client.disconnect()
            await context.bot.send_message(chat_id=update.effective_chat.id, text=f"Account {phone_number} is not authorized. Skipping join request.")
            return

        try:
            # Check if it's an invite link (t.me/+AbCdeFghIjklMnOp)
            match = re.search(r't\.me/\+([A-Za-z0-9_-]+)', invite_link)
            if match:
                invite_hash = match.group(1)
                await client(ImportChatInviteRequest(hash=invite_hash))
                await context.bot.send_message(chat_id=update.effective_chat.id, text=f"✅ Join request sent from account {phone_number} successfully.")
            else:
                # اگر یہ پبلک چینل لنک ہے
                await client(JoinChannelRequest(channel=invite_link))
                await context.bot.send_message(chat_id=update.effective_chat.id, text=f"✅ Join request sent from account {phone_number} successfully.")

        except Exception as e:
            if "ChatInviteEmptyError" in str(e):
                await context.bot.send_message(chat_id=update.effective_chat.id, text=f"❌ Join request from account {phone_number} failed. The invite link is invalid or expired.")
            elif "UserAlreadyParticipantError" in str(e):
                await context.bot.send_message(chat_id=update.effective_chat.id, text=f"❌ Account {phone_number} is already a member of this channel.")
            else:
                await context.bot.send_message(chat_id=update.effective_chat.id, text=f"❌ Join request from account {phone_number} failed. Reason: {e}")
        finally:
            await client.disconnect()

async def get_user_channels(query: Update.callback_query, context: ContextTypes.DEFAULT_TYPE, phone_number: str):
    chat_id = query.message.chat_id
    if phone_number not in session_locks:
        session_locks[phone_number] = asyncio.Lock()

    async with session_locks[phone_number]:
        client = TelegramClient(os.path.join(SESSION_FOLDER, phone_number), API_ID, API_HASH)
        await client.connect()

        if not await client.is_user_authorized():
            await client.disconnect()
            await context.bot.send_message(chat_id=chat_id, text=f"Account {phone_number} is not authorized. Skipping channel list.")
            return

        try:
            dialogs = await client.get_dialogs()
            channels = [d.entity.title for d in dialogs if isinstance(d.entity, Channel)]
            
            if channels:
                channel_list_text = "\n".join(channels)
                await context.bot.send_message(chat_id=chat_id, text=f"Channels for account {phone_number}:\n\n{channel_list_text}")
            else:
                await context.bot.send_message(chat_id=chat_id, text=f"Account {phone_number} has not joined any channels.")
        except Exception as e:
            await context.bot.send_message(chat_id=chat_id, text=f"❌ Could not fetch channels for account {phone_number}. Reason: {e}")
        finally:
            await client.disconnect()
            await context.bot.send_message(chat_id=chat_id, text=f"✅ Channel fetching for account {phone_number} completed.")

async def create_backup(query: Update.callback_query, context: ContextTypes.DEFAULT_TYPE):
    chat_id = query.message.chat_id
    try:
        if not os.path.exists(SESSION_FOLDER) or not os.listdir(SESSION_FOLDER):
            await context.bot.send_message(chat_id=chat_id, text="There are no sessions to back up.")
            return

        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for filename in os.listdir(SESSION_FOLDER):
                file_path = os.path.join(SESSION_FOLDER, filename)
                if os.path.isfile(file_path):
                    zipf.write(file_path, arcname=filename)
        
        zip_buffer.seek(0)
        
        backup_filename = f"sessions_backup_{datetime.now().strftime('%Y-%m-%d')}.zip"
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
        
        row = [
            InlineKeyboardButton(text=f"User: {user_id} (Expires: {expires_at})", callback_data='_'),
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
    
    # Refresh the user list display
    await list_granted_users(query, context)

def main() -> None:
    application = Application.builder().token(BOT_TOKEN).build()
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("stop", stop_command_handler)) # نیا ہینڈلر شامل کیا
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))
    
    application.run_polling()

if __name__ == '__main__':
    main()

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
    InputPeerChannel, Channel, ReportResultChooseOption, MessageReportOption
)
from telethon.errors import RPCError, FloodWaitError
import traceback
import random
import sqlite3

# --- OWNER DETAILS & BOT CONFIGURATION ---
# Replace with your own Telegram Chat ID and Username
OWNER_ID = 8167904992  # Replace with your actual Telegram Chat ID
OWNER_USERNAME = "whatsapp_offcial"  # Replace with your actual Telegram Username

API_ID = 94575
API_HASH = 'a3406de8d171bb422bb6ddf3bbd800e2'
BOT_TOKEN = '8324191756:AAF28XJJ9wSO2jZ5iFIqlrdEbjqHFX190Pk'

SESSION_FOLDER = 'sessions'
GRANTED_USERS_FILE = 'granted_users.json'

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

# Ensure folders and files exist
if not os.path.exists(SESSION_FOLDER):
    os.makedirs(SESSION_FOLDER)
if not os.path.exists(GRANTED_USERS_FILE):
    with open(GRANTED_USERS_FILE, 'w') as f:
        json.dump([], f)

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
    """
    Masks the middle of a phone number for privacy.
    Example: +923117822922 -> +92311***22922
    """
    if len(phone_number) < 8:
        return phone_number
    return phone_number[:5] + '***' + phone_number[-5:]

# --- Handlers for Telegram Bot ---

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
            [InlineKeyboardButton("My Accounts 👤", callback_data='my_accounts')],
            [InlineKeyboardButton("My Channels 👥", callback_data='my_channels')],
            [InlineKeyboardButton("Backup 💾", callback_data='backup_sessions')],
            [InlineKeyboardButton("Manage Users 🗂️", callback_data='manage_users')],
            [InlineKeyboardButton("Grant Access ✨", callback_data='grant_access')]
        ]
        text = 'Hello Owner! Please choose an option:'
    elif is_user_granted:
        keyboard = [
            [InlineKeyboardButton("Login 🔐", callback_data='login_start')],
            [InlineKeyboardButton("Report Illegal Content 🚨", callback_data='report_start')],
        ]
        if not all_access:
            keyboard.append([InlineKeyboardButton("My Accounts 👤", callback_data='my_accounts')])
            keyboard.append([InlineKeyboardButton("My Channels 👥", callback_data='my_channels')])

        text = 'Hello! You have limited access. Please choose an option:'
    else:
        keyboard = [
            [InlineKeyboardButton("Login 🔐", callback_data='login_start')],
            [InlineKeyboardButton("Report Illegal Content 🚨", callback_data='report_start')]
        ]
        text = 'Welcome! You can log in your accounts and start using the bot.'

    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(text, reply_markup=reply_markup)


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    
    is_user_granted_access = is_granted_user(user_id)
    
    if query.data == 'login_start':
        await query.edit_message_text(text="Please send your phone number with country code (e.g., +923001234567) to log in.")
        context.user_data['state'] = 'awaiting_phone_number'
    
    elif query.data == 'report_start':
        await query.edit_message_text(text="Please send the link of the channel or a post you want to report.")
        context.user_data['state'] = 'awaiting_link'

    elif query.data.startswith('report_type_'):
        report_type_text = query.data.split('_', 2)[-1]
        context.user_data['report_type_text'] = report_type_text
        
        # Check if the main report type has subtypes
        if report_type_text in REPORT_SUBTYPES:
            subtype_options = REPORT_SUBTYPES[report_type_text]
            keyboard_buttons = [[InlineKeyboardButton(text=opt, callback_data=f'report_subtype_{opt}')] for opt in subtype_options.keys()]
            reply_markup = InlineKeyboardMarkup(keyboard_buttons)
            await query.edit_message_text(f"Please choose a specific reason for '{report_type_text}':", reply_markup=reply_markup)
            context.user_data['state'] = 'awaiting_report_type_selection'
        else:
            await query.edit_message_text(f"You selected '{report_type_text}'. Now, please provide a brief message and the number of times to report (e.g., 'Violent content 5').")
            context.user_data['state'] = 'awaiting_report_comment_and_count'
            
    elif query.data.startswith('report_subtype_'):
        report_subtype_text = query.data.split('_', 2)[-1]
        context.user_data['report_type_text'] = report_subtype_text
        await query.edit_message_text(f"You selected '{report_subtype_text}'. Now, please provide a brief message and the number of times to report (e.g., 'Violent content 5').")
        context.user_data['state'] = 'awaiting_report_comment_and_count'

    elif query.data == 'my_accounts':
        user_info = get_granted_user_info(user_id)
        if not is_owner(user_id) and user_info and user_info.get('all_access'):
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

    elif query.data == 'my_channels':
        user_info = get_granted_user_info(user_id)
        if not is_owner(user_id) and user_info and user_info.get('all_access'):
            await query.edit_message_text("You do not have permission to view other users' channels.")
            return

        accounts = get_logged_in_accounts(user_id, is_owner(user_id) or is_user_granted_access)
        if not accounts:
            await query.edit_message_text("No accounts are currently logged in.")
            return
        
        keyboard = [[InlineKeyboardButton(text=f"{acc[0]} (User: {acc[1]})", callback_data=f'show_channels_{acc[0]}_{acc[1]}')] for acc in accounts]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text("Please select an account to view its channels:", reply_markup=reply_markup)

    elif query.data.startswith('show_channels_'):
        try:
            parts = query.data.split('_')
            if len(parts) != 3:
                await query.edit_message_text("❌ There was an error processing the request. Please try again.")
                return
                
            _, phone_number, account_user_id_str = parts
            account_user_id = int(account_user_id_str)
            
        except (ValueError, IndexError):
            await query.edit_message_text("❌ Could not parse account details. Please try again or contact support.")
            return
            
        await query.edit_message_text(f"Fetching channels for account {phone_number}. This may take a moment...")
        await get_user_channels(query, context, phone_number, account_user_id)
        
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
        
    elif query.data == 'start':
        await start(update, context)


async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    global task_counter
    user_message = update.message.text
    user_state = context.user_data.get('state')
    user_id = update.effective_user.id
    
    # --- Owner Only States ---
    if is_owner(user_id):
        if user_state == 'awaiting_grant_info':
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

        elif user_state == 'awaiting_reset_info':
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

        elif user_state == 'awaiting_join_link':
            invite_link = user_message
            accounts = get_logged_in_accounts(user_id, all_access=True)
            if not accounts:
                await update.message.reply_text("No accounts logged in to join channels.")
                return

            task = asyncio.create_task(join_channels_in_background(update, context, invite_link, accounts))
            await update.message.reply_text("All join requests have been sent. They are now processing in the background.")
            context.user_data['state'] = None

    # --- All Users States ---
    if user_state == 'awaiting_phone_number':
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
        except Exception as e:
            await update.message.reply_text(f"Invalid OTP. Please try again.")
            
    elif user_state == 'awaiting_link':
        context.user_data['target_link'] = user_message
        keyboard_buttons = [[InlineKeyboardButton(text=key, callback_data=f'report_type_{key}')] for key in REPORT_OPTIONS.keys()]
        reply_markup = InlineKeyboardMarkup(keyboard_buttons)
        await update.message.reply_text("Please choose a report type:", reply_markup=reply_markup)
        context.user_data['state'] = 'awaiting_report_type_selection'

    elif user_state == 'awaiting_report_comment_and_count':
        try:
            parts = user_message.rsplit(' ', 1)
            report_message = parts[0].strip()
            report_count = int(parts[1].strip())
            
            target_link = context.user_data.get('target_link')
            report_type_text = context.user_data.get('report_type_text')

            user_info = get_granted_user_info(user_id)
            accounts_to_use = get_logged_in_accounts(user_id, is_owner(user_id) or (user_info and user_info.get('all_access')))
            
            if not accounts_to_use:
                await update.message.reply_text("No accounts logged in to send reports.")
                context.user_data['state'] = None
                return

            task_counter += 1
            task_id = task_counter
            
            await update.message.reply_text(f"Starting to report '{target_link}' for you. This is task #{task_id}.")

            if len(accounts_to_use) > 1:
                report_delay = 5
            else:
                report_delay = 10

            tasks = []
            for i in range(report_count):
                for phone_number, account_user_id in accounts_to_use:
                    await asyncio.sleep(report_delay)
                    task = asyncio.create_task(send_single_report(update, context, phone_number, target_link, report_type_text, i + 1, report_count, report_message, task_id, user_id, account_user_id))
                    tasks.append(task)
            
            if user_id not in user_tasks:
                user_tasks[user_id] = {}
            user_tasks[user_id][task_id] = tasks
            
            context.user_data['state'] = None
            
        except (ValueError, IndexError):
            await update.message.reply_text("Please provide a comment and a number separated by a space (e.g., 'Violent content 5').")
            context.user_data['state'] = 'awaiting_report_comment_and_count'

async def stop_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    try:
        task_id = int(context.args[0])
        
        if user_id in user_tasks and task_id in user_tasks[user_id]:
            tasks_to_cancel = user_tasks[user_id][task_id]
            for task in tasks_to_cancel:
                task.cancel()
            await update.message.reply_text(f"✅ The reporting loop with task #{task_id} has been requested to stop.")
            del user_tasks[user_id][task_id]
        else:
            await update.message.reply_text("❌ Task not found. Please provide a valid task number.")
    except (IndexError, ValueError):
        await update.message.reply_text("❌ Please specify the task number you want to stop. Example: `/stop 1`")
    except Exception as e:
            await update.message.reply_text(f"An error occurred while stopping the task: {e}")

# --- Helper Functions ---

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

                # Get the correct report option from the dictionaries
                report_option_byte = None
                
                # Check for subtype options first
                found_subtype = False
                for main_type, subtypes in REPORT_SUBTYPES.items():
                    if report_type_text in subtypes:
                        report_option_byte = subtypes[report_type_text]
                        found_subtype = True
                        break
                
                # If not a subtype, check main options
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
                    
            else:
                entity = await client.get_entity(target_link)
                
                result = await client(ReportSpamRequest(peer=entity))
                
                response_message = f"✅ Report Send {current_report_count}/{total_report_count} task #{task_id}.\n\n"
                response_message += f"from {mask_phone_number(phone_number)} sent successfully\n\n"
                response_message += f"Original api response: {str(result)}"
                await context.bot.send_message(chat_id=update.effective_chat.id, text=response_message)

        except asyncio.CancelledError:
            await context.bot.send_message(chat_id=update.effective_chat.id, text=f"Reporting task #{task_id} was cancelled for {mask_phone_number(phone_number)}.")
            raise
        except (RPCError, FloodWaitError) as e:
            await context.bot.send_message(chat_id=update.effective_chat.id, text=f"❌ Report {current_report_count}/{total_report_count} from {mask_phone_number(phone_number)} failed for task #{task_id}. Reason: {e}")
        except Exception as e:
            await context.bot.send_message(chat_id=update.effective_chat.id, text=f"❌ Report {current_report_count}/{total_report_count} from {mask_phone_number(phone_number)} failed for task #{task_id}. Reason: {e}")
            print(traceback.format_exc())
        finally:
            await client.disconnect()
            
async def join_channels_in_background(update, context, invite_link, accounts):
    tasks = [join_channel(update, context, phone, user_id, invite_link) for phone, user_id in accounts]
    await asyncio.gather(*tasks)
    await update.message.reply_text("All join requests have been processed.")
    
async def join_channel(update: Update, context: ContextTypes.DEFAULT_TYPE, phone_number: str, account_user_id: int, invite_link: str):
    user_id = update.effective_user.id
    if phone_number not in session_locks:
        session_locks[phone_number] = asyncio.Lock()

    async with session_locks[phone_number]:
        session_folder = os.path.join(SESSION_FOLDER, str(account_user_id))
        session_path = os.path.join(session_folder, phone_number)

        if not os.path.exists(session_folder):
            await context.bot.send_message(chat_id=update.effective_chat.id, text=f"❌ Account {mask_phone_number(phone_number)}'s session folder not found. Skipping join request.")
            return

        client = TelegramClient(session_path, API_ID, API_HASH)
        await client.connect()

        if not await client.is_user_authorized():
            await client.disconnect()
            await context.bot.send_message(chat_id=update.effective_chat.id, text=f"Account {mask_phone_number(phone_number)} is not authorized. Skipping join request.")
            return

        try:
            match = re.search(r't\.me/\+([A-Za-z0-9_-]+)', invite_link)
            if match:
                invite_hash = match.group(1)
                await client(ImportChatInviteRequest(hash=invite_hash))
                await context.bot.send_message(chat_id=update.effective_chat.id, text=f"✅ Join request sent from account {mask_phone_number(phone_number)} successfully.")
            else:
                await client(JoinChannelRequest(channel=invite_link))
                await context.bot.send_message(chat_id=update.effective_chat.id, text=f"✅ Join request sent from account {mask_phone_number(phone_number)} successfully.")
        except Exception as e:
            if "ChatInviteEmptyError" in str(e):
                await context.bot.send_message(chat_id=update.effective_chat.id, text=f"❌ Join request from account {mask_phone_number(phone_number)} failed. The invite link is invalid or expired.")
            elif "UserAlreadyParticipantError" in str(e):
                await context.bot.send_message(chat_id=update.effective_chat.id, text=f"❌ Account {mask_phone_number(phone_number)} is already a member of this channel.")
            else:
                await context.bot.send_message(chat_id=update.effective_chat.id, text=f"❌ Join request from account {mask_phone_number(phone_number)} failed. Reason: {e}")
        finally:
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

            dialogs = await client.get_dialogs()
            channels = [d.entity.title for d in dialogs if isinstance(d.entity, Channel)]
            
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
        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zipf:
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

def main() -> None:
    application = Application.builder().token(BOT_TOKEN).build()
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("stop", stop_command_handler))
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))
    
    application.run_polling()

if __name__ == '__main__':
    main()

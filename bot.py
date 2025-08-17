import os
import asyncio
import re
from telethon import TelegramClient
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
from telethon.tl.functions.messages import ReportRequest, ReportSpamRequest
from telethon.tl.types import (
    InputPeerChannel, InputPeerChat, InputPeerUser
)
from telethon.errors import RPCError, FloodWaitError

# --- Your Credentials ---
API_ID = 94575
API_HASH = 'a3406de8d171bb422bb6ddf3bbd800e2'
BOT_TOKEN = '8324191756:AAF28XJJ9wSO2jZ5iFIqlrdEbjqHFX190Pk'

SESSION_FOLDER = 'sessions'

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

if not os.path.exists(SESSION_FOLDER):
    os.makedirs(SESSION_FOLDER)

# --- Handlers for Telegram Bot ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    keyboard = [
        [InlineKeyboardButton("Login 🔐", callback_data='login_start')],
        [InlineKeyboardButton("Report Illegal Content 🚨", callback_data='report_start')],
        [InlineKeyboardButton("My Accounts 👤", callback_data='my_accounts')],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text('Hello! Please choose an option:', reply_markup=reply_markup)

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    if query.data == 'login_start':
        await query.edit_message_text(text="Please send your phone number with country code (e.g., +923001234567) to log in.")
        context.user_data['state'] = 'awaiting_phone_number'
    
    elif query.data == 'report_start':
        await query.edit_message_text(text="Please send the link of the channel or a post you want to report.")
        context.user_data['state'] = 'awaiting_link'

    elif query.data == 'my_accounts':
        accounts = get_logged_in_accounts()
        if accounts:
            account_list = "\n".join([f"- {acc}" for acc in accounts])
            await query.edit_message_text(f"Logged in accounts:\n{account_list}")
        else:
            await query.edit_message_text("No accounts are currently logged in.")

    elif query.data.startswith('report_type_'):
        report_type_text = query.data.split('_', 2)[-1]
        context.user_data['report_type_text'] = report_type_text
        await query.edit_message_text(f"You selected '{report_type_text}'. Now, please provide a brief message explaining the violation and then the number of times to report (e.g., 'Violent content, 5').")
        context.user_data['state'] = 'awaiting_report_comment_and_count'


async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_message = update.message.text
    user_state = context.user_data.get('state')

    if user_state == 'awaiting_phone_number':
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

    elif user_state == 'awaiting_otp':
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
            
    elif user_state == 'awaiting_link':
        context.user_data['target_link'] = user_message
        # Provide the buttons for report types immediately
        keyboard_buttons = [[InlineKeyboardButton(text=key, callback_data=f'report_type_{key}')] for key in REPORT_OPTIONS.keys()]
        reply_markup = InlineKeyboardMarkup(keyboard_buttons)
        await update.message.reply_text("Please choose a report type:", reply_markup=reply_markup)
        context.user_data['state'] = 'awaiting_report_type_selection'

    elif user_state == 'awaiting_report_comment_and_count':
        try:
            # Split the user message to get the comment and the count
            parts = user_message.rsplit(',', 1)
            report_message = parts[0].strip()
            report_count = int(parts[1].strip())
            
            target_link = context.user_data.get('target_link')
            report_type_text = context.user_data.get('report_type_text')

            await update.message.reply_text(f"Starting to report '{target_link}' with '{report_type_text}' {report_count} times per account. Comment: '{report_message}'...")

            logged_in_accounts = get_logged_in_accounts()
            if not logged_in_accounts:
                await update.message.reply_text("No accounts logged in to send reports.")
                context.user_data['state'] = None
                return
            
            for phone in logged_in_accounts:
                await update.message.reply_text(f"Sending reports with account: {phone}...")
                await send_reports(update, context, phone, target_link, report_type_text, report_count, report_message)

            await update.message.reply_text(f"All reports have been processed. Thank you.")
            context.user_data['state'] = None
        except (ValueError, IndexError):
            await update.message.reply_text("Please provide a comment and a number separated by a comma (e.g., 'Violent content, 5').")
            context.user_data['state'] = 'awaiting_report_comment_and_count'
        
# --- Helper Functions ---

def get_logged_in_accounts():
    accounts = []
    for filename in os.listdir(SESSION_FOLDER):
        if filename.endswith('.session'):
            accounts.append(os.path.splitext(filename)[0])
    return accounts

async def send_reports(update: Update, context: ContextTypes.DEFAULT_TYPE, phone_number, target_link, report_type_text, count, report_message):
    if phone_number not in session_locks:
        session_locks[phone_number] = asyncio.Lock()
    
    async with session_locks[phone_number]:
        client = TelegramClient(os.path.join(SESSION_FOLDER, phone_number), API_ID, API_HASH)
        await client.connect()
        
        if not await client.is_user_authorized():
            await client.disconnect()
            await context.bot.send_message(chat_id=update.effective_chat.id, text=f"Account {phone_number} is not authorized. Skipping reports.")
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
                        result = await client(ReportRequest(peer=entity, id=[message_id], option=report_option_byte, message=report_message))
                        await context.bot.send_message(chat_id=update.effective_chat.id, text=f"✅ Report {i+1}/{count} from {phone_number} sent successfully. Response: {str(result)}")
                    except (RPCError, FloodWaitError) as e:
                        await context.bot.send_message(chat_id=update.effective_chat.id, text=f"❌ Report {i+1}/{count} from {phone_number} failed. Reason: {e}")
                    except Exception as e:
                        await context.bot.send_message(chat_id=update.effective_chat.id, text=f"❌ Report {i+1}/{count} from {phone_number} failed. Reason: {e}")
                    await asyncio.sleep(10)
            else:
                entity = await client.get_entity(target_link)
                
                for i in range(count):
                    try:
                        result = await client(ReportSpamRequest(peer=entity))
                        await context.bot.send_message(chat_id=update.effective_chat.id, text=f"✅ Report {i+1}/{count} from {phone_number} sent successfully. Response: {str(result)}")
                    except (RPCError, FloodWaitError) as e:
                        await context.bot.send_message(chat_id=update.effective_chat.id, text=f"❌ Report {i+1}/{count} from {phone_number} failed. Reason: {e}")
                    except Exception as e:
                        await context.bot.send_message(chat_id=update.effective_chat.id, text=f"❌ Report {i+1}/{count} from {phone_number} failed. Reason: {e}")
                    await asyncio.sleep(10)

        except Exception as e:
            await context.bot.send_message(chat_id=update.effective_chat.id, text=f"An error occurred with account {phone_number}: {e}")
        finally:
            await client.disconnect()

def main() -> None:
    application = Application.builder().token(BOT_TOKEN).build()
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))
    
    application.run_polling()

if __name__ == '__main__':
    main()

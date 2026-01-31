import os
import json
import logging
import threading
import time
import asyncio
import csv
import io
import random
import string
from datetime import datetime, timedelta
import requests
import firebase_admin
from firebase_admin import credentials, firestore
from flask import Flask, render_template, request, jsonify

# Telegram Bot Library v20+ (Async Version)
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup,
    WebAppInfo
)
from telegram.ext import (
    ApplicationBuilder, CommandHandler, ContextTypes, CallbackQueryHandler,
    MessageHandler, filters, ConversationHandler
)

# ==========================================
# 1. কনফিগারেশন এবং সেটআপ
# ==========================================

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# ENV ভেরিয়েবল
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
OWNER_ID = os.environ.get("OWNER_ID", "") 
FIREBASE_JSON = os.environ.get("FIREBASE_CREDENTIALS", "firebase_key.json")
IMGBB_API_KEY = os.environ.get('IMGBB_API_KEY', "")
WEB_APP_URL = os.environ.get("WEB_APP_URL", "https://earn-money-bot.onrender.com")
PORT = int(os.environ.get("PORT", 8080))

# Firebase কানেকশন
if not firebase_admin._apps:
    try:
        if FIREBASE_JSON.startswith("{"):
            cred_dict = json.loads(FIREBASE_JSON)
            cred = credentials.Certificate(cred_dict)
        else:
            cred = credentials.Certificate(FIREBASE_JSON)
        firebase_admin.initialize_app(cred)
        print("✅ Firebase Connected Successfully!")
    except Exception as e:
        print(f"❌ Firebase Connection Failed: {e}")

db = firestore.client()

# ==========================================
# 2. গ্লোবাল কনফিগারেশন
# ==========================================

DEFAULT_CONFIG = {
    "task_price": 20.0,
    "referral_bonus": 5.0,
    "min_withdraw": 50.0,
    "monitored_apps": [],
    "log_channel_id": "",
    "work_start_time": "15:30",
    "work_end_time": "23:00",
    "rules_text": "⚠️ কাজের নিয়ম: ভিডিওতে দেখানো হয়েছে ভিডিওটি দেখে নিন।",
    "schedule_text": "⏰ কাজের সময়: বিকেল 03:30 PM To 11:00 PM।",
    "website_url": WEB_APP_URL,
    "buttons": {
        "submit": {"text": "💰 কাজ জমা দিন", "show": True},
        "profile": {"text": "👤 প্রোফাইল", "show": True},
        "withdraw": {"text": "📤 উইথড্র", "show": True},
        "refer": {"text": "📢 রেফার", "show": True},
        "schedule": {"text": "📅 সময়সূচী", "show": True},
        "website": {"text": "🌐 ওয়েবসাইট", "show": True}
    },
    "custom_buttons": [],
    "ad_codes": {
        "monetag_header": "",
        "monetag_popunder": "",
        "monetag_direct": ""
    }
}

# Conversation States
(
    T_APP_SELECT, T_REVIEW_NAME, T_EMAIL, T_DEVICE, T_SS,
    ADD_APP_ID, ADD_APP_NAME, ADD_APP_LIMIT,
    WD_METHOD, WD_NUMBER, WD_AMOUNT,
    REMOVE_APP_SELECT,
    ADMIN_USER_SEARCH, ADMIN_USER_ACTION, ADMIN_USER_AMOUNT,
    ADMIN_EDIT_TEXT_KEY, ADMIN_EDIT_TEXT_VAL,
    ADMIN_EDIT_BTN_KEY, ADMIN_EDIT_BTN_NAME,
    ADMIN_ADD_BTN_NAME, ADMIN_ADD_BTN_LINK,
    ADMIN_SET_LOG_CHANNEL,
    ADMIN_ADD_ADMIN_ID, ADMIN_RMV_ADMIN_ID,
    ADMIN_SET_START_TIME, ADMIN_SET_END_TIME,
    EDIT_APP_SELECT, EDIT_APP_LIMIT_VAL,
    REMOVE_CUS_BTN,
    ADMIN_AD_CODE_TYPE, ADMIN_AD_CODE_VALUE,
    ADMIN_RESET_AUTO_APPROVE
) = range(32)

# ==========================================
# 3. হেল্পার ফাংশন
# ==========================================

def get_config():
    try:
        ref = db.collection('settings').document('main_config')
        doc = ref.get()
        if doc.exists:
            data = doc.to_dict()
            for key, val in DEFAULT_CONFIG.items():
                if key not in data:
                    data[key] = val
            return data
        else:
            ref.set(DEFAULT_CONFIG)
            return DEFAULT_CONFIG
    except Exception as e:
        logger.error(f"Config Error: {e}")
        return DEFAULT_CONFIG

def get_bd_time():
    return datetime.utcnow() + timedelta(hours=6)

def is_working_hour():
    config = get_config()
    start_str = config.get("work_start_time", "15:30")
    end_str = config.get("work_end_time", "23:00")
    try:
        now = get_bd_time().time()
        start = datetime.strptime(start_str, "%H:%M").time()
        end = datetime.strptime(end_str, "%H:%M").time()
        if start < end:
            return start <= now <= end
        else:
            return now >= start or now <= end
    except Exception as e:
        return True

def is_admin(user_id):
    if str(user_id) == str(OWNER_ID): 
        return True
    try:
        user = db.collection('users').document(str(user_id)).get()
        return user.exists and user.to_dict().get('is_admin', False)
    except:
        return False

def get_user(user_id):
    try:
        doc = db.collection('users').document(str(user_id)).get()
        if doc.exists: return doc.to_dict()
    except: pass
    return None

def generate_password():
    chars = string.ascii_letters + string.digits
    return ''.join(random.choice(chars) for _ in range(8))

def create_user(user_id, first_name, referrer_id=None):
    if not get_user(user_id):
        try:
            password = generate_password()
            user_data = {
                "id": str(user_id),
                "name": first_name,
                "full_name": first_name,
                "balance": 0.0,
                "total_tasks": 0,
                "joined_at": datetime.now(),
                "referrer": referrer_id if referrer_id and referrer_id.isdigit() and str(referrer_id) != str(user_id) else None,
                "is_blocked": False,
                "is_admin": str(user_id) == str(OWNER_ID),
                "password": password,
                "email": "",
                "last_login": datetime.now(),
                "web_sessions": 0,
                "telegram_id": str(user_id)
            }
            db.collection('users').document(str(user_id)).set(user_data)
            if referrer_id and referrer_id.isdigit() and str(referrer_id) != str(user_id):
                config = get_config()
                db.collection('users').document(str(referrer_id)).update({
                    "balance": firestore.Increment(config['referral_bonus'])
                })
            return password
        except Exception as e:
            logger.error(f"Create User Error: {e}")
            return None
    return None

def get_app_task_count(app_id):
    try:
        pending = db.collection('tasks').where('app_id', '==', app_id).where('status', '==', 'pending').stream()
        approved = db.collection('tasks').where('app_id', '==', app_id).where('status', '==', 'approved').stream()
        count = len(list(pending)) + len(list(approved))
        return count
    except: return 0

async def send_log_message(context, text, reply_markup=None):
    config = get_config()
    chat_id = config.get('log_channel_id')
    target_id = chat_id if chat_id else OWNER_ID
    if target_id:
        try:
            await context.bot.send_message(chat_id=target_id, text=text, reply_markup=reply_markup, parse_mode="Markdown")
        except: pass
            # ==========================================
# 4. ইউজার ফাংশন - টেলিগ্রাম
# ==========================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    args = context.args
    referrer = args[0] if args and args[0].isdigit() else None
    
    existing_user = get_user(user.id)
    if not existing_user:
        password = create_user(user.id, user.first_name, referrer)
        if password:
            await update.message.reply_text(
                f"🎉 নিবন্ধন সফল হয়েছে!\n🔐 পাসওয়ার্ড: `{password}`\n🌐 ওয়েবসাইট: {WEB_APP_URL}",
                parse_mode="Markdown"
            )
    
    db_user = get_user(user.id)
    if db_user and db_user.get('is_blocked'):
        await update.message.reply_text("⛔ আপনাকে ব্লক করা হয়েছে।")
        return

    config = get_config()
    btns_conf = config.get('buttons', DEFAULT_CONFIG['buttons'])
    
    welcome_msg = (
        f"আসসালামু আলাইকুম, {user.first_name}! 🌙\n\n"
        f"🗒 **কাজের নিয়মাবলী:**\n{config.get('rules_text', '')}\n\n"
        "নিচের মেনু থেকে অপশন সিলেক্ট করুন:"
    )

    keyboard = []
    row1 = []
    if btns_conf['submit']['show']: row1.append(InlineKeyboardButton(btns_conf['submit']['text'], callback_data="submit_task"))
    if btns_conf['profile']['show']: row1.append(InlineKeyboardButton(btns_conf['profile']['text'], callback_data="my_profile"))
    if row1: keyboard.append(row1)
    
    row2 = []
    if btns_conf['withdraw']['show']: row2.append(InlineKeyboardButton(btns_conf['withdraw']['text'], callback_data="start_withdraw"))
    if btns_conf['refer']['show']: row2.append(InlineKeyboardButton(btns_conf['refer']['text'], callback_data="refer_friend"))
    if row2: keyboard.append(row2)
    
    row3 = []
    if btns_conf.get('schedule', {}).get('show', True): 
        row3.append(InlineKeyboardButton("📅 সময়সূচী", callback_data="show_schedule"))
    if btns_conf['website']['show']:
        web_app = WebAppInfo(url=config['website_url'])
        row3.append(InlineKeyboardButton("🌐 ওয়েবসাইট", web_app=web_app))
    if row3: keyboard.append(row3)
    
    if is_admin(user.id):
        keyboard.append([InlineKeyboardButton("⚙️ এডমিন প্যানেল", callback_data="admin_panel")])

    await update.message.reply_text(welcome_msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def common_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == "back_home":
        await start(update, context) # Reusing start function logic
        
    elif query.data == "my_profile":
        user = get_user(query.from_user.id)
        if user:
            msg = (
                f"👤 **প্রোফাইল**\n🆔 ID: `{user['id']}`\n"
                f"🔑 Password: `{user.get('password', 'N/A')}`\n"
                f"💰 ব্যালেন্স: ৳{user['balance']:.2f}\n"
                f"✅ সম্পন্ন টাস্ক: {user['total_tasks']}"
            )
        else: msg = "প্রোফাইল পাওয়া যায়নি।"
        await query.edit_message_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙", callback_data="back_home")]]))
        
    elif query.data == "refer_friend":
        config = get_config()
        link = f"https://t.me/{context.bot.username}?start={query.from_user.id}"
        await query.edit_message_text(f"📢 **রেফার লিংক:**\n`{link}`\nবোনাস: ৳{config['referral_bonus']}", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙", callback_data="back_home")]]))

    elif query.data == "show_schedule":
        config = get_config()
        await query.edit_message_text(f"📅 **সময়সূচী:**\n{config.get('schedule_text', '')}", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙", callback_data="back_home")]]))

# ==========================================
# 5. টাস্ক সাবমিশন সিস্টেম
# ==========================================

async def start_task_submission(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    config = get_config()
    
    if not is_working_hour():
        await query.edit_message_text("⛔ এখন কাজের সময় নয়!", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙", callback_data="back_home")]]))
        return ConversationHandler.END

    apps = config.get('monitored_apps', [])
    if not apps:
        await query.edit_message_text("❌ বর্তমানে কোনো কাজ নেই।", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙", callback_data="back_home")]]))
        return ConversationHandler.END
        
    buttons = []
    for app in apps:
        limit = app.get('limit', 1000)
        count = get_app_task_count(app['id'])
        btn_text = f"📱 {app['name']} ({count}/{limit})" if count < limit else f"⛔ {app['name']} (Full)"
        buttons.append([InlineKeyboardButton(btn_text, callback_data=f"sel_{app['id']}")])
    buttons.append([InlineKeyboardButton("❌ বাতিল", callback_data="cancel")])
    
    await query.edit_message_text("কোন অ্যাপে কাজ করতে চান সিলেক্ট করুন:", reply_markup=InlineKeyboardMarkup(buttons))
    return T_APP_SELECT

async def app_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "cancel": return await cancel_conv(update, context)
    
    app_id = query.data.split("sel_")[1]
    context.user_data['tid'] = app_id
    await query.edit_message_text("✍️ **রিভিউ নাম (Review Name)** দিন:\nপ্লে-স্টোরের নামের সাথে মিল রাখতে হবে।", parse_mode="Markdown")
    return T_REVIEW_NAME

async def get_review_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['rname'] = update.message.text.strip()
    await update.message.reply_text("আপনার ইমেইল এড্রেস দিন:")
    return T_EMAIL

async def get_email(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['email'] = update.message.text
    await update.message.reply_text("মোবাইল মডেল/ডিভাইস নাম:")
    return T_DEVICE

async def get_device(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['dev'] = update.message.text
    await update.message.reply_text("স্ক্রিনশট দিন (ছবি আপলোড করুন):")
    return T_SS

async def save_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not update.message.photo:
        await update.message.reply_text("❌ অনুগ্রহ করে ছবি দিন।")
        return T_SS

    wait_msg = await update.message.reply_text("📤 আপলোড হচ্ছে...")
    try:
        photo_file = await update.message.photo[-1].get_file()
        img_bytes = await photo_file.download_as_bytearray()
        
        if IMGBB_API_KEY:
            response = requests.post("https://api.imgbb.com/1/upload", data={'key': IMGBB_API_KEY}, files={'image': bytes(img_bytes)})
            result = response.json()
            screenshot_link = result['data']['url'] if result.get('success') else "Error"
        else:
            screenshot_link = "No API Key"

        config = get_config()
        task_ref = db.collection('tasks').add({
            "user_id": str(user.id),
            "app_id": context.user_data['tid'],
            "review_name": context.user_data['rname'],
            "email": context.user_data['email'],
            "device": context.user_data['dev'],
            "screenshot": screenshot_link,
            "status": "pending",
            "submitted_at": datetime.now(),
            "price": config['task_price'],
            "platform": "telegram"
        })
        
        task_id = task_ref[1].id
        log_msg = f"📝 **New Task**\nUser: `{user.id}`\nApp ID: {context.user_data['tid']}\nProof: {screenshot_link}"
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅ Approve", callback_data=f"t_apr_{task_id}_{user.id}"), InlineKeyboardButton("❌ Reject", callback_data=f"t_rej_{task_id}_{user.id}")]])
        
        await send_log_message(context, log_msg, kb)
        await wait_msg.edit_text("✅ কাজ জমা হয়েছে! এডমিন চেক করবেন।", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 হোম", callback_data="back_home")]]))
    except Exception as e:
        logger.error(f"Upload Error: {e}")
        await wait_msg.edit_text("❌ সমস্যা হয়েছে।")
    return ConversationHandler.END

async def cancel_conv(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.callback_query:
        await update.callback_query.edit_message_text("❌ বাতিল করা হয়েছে।", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 হোম", callback_data="back_home")]]))
    else:
        await update.message.reply_text("❌ বাতিল করা হয়েছে।")
    return ConversationHandler.END

# ==========================================
# 6. উইথড্র সিস্টেম
# ==========================================

async def withdraw_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = get_user(query.from_user.id)
    config = get_config()
    
    if user['balance'] < config['min_withdraw']:
        await query.edit_message_text(f"❌ সর্বনিম্ন উইথড্র: ৳{config['min_withdraw']:.2f}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙", callback_data="back_home")]]))
        return ConversationHandler.END
        
    await query.edit_message_text("পেমেন্ট মেথড সিলেক্ট করুন:", reply_markup=InlineKeyboardMarkup([
        [InlineKeyboardButton("Bkash", callback_data="m_bkash"), InlineKeyboardButton("Nagad", callback_data="m_nagad")],
        [InlineKeyboardButton("❌ বাতিল", callback_data="cancel")]
    ]))
    return WD_METHOD

async def withdraw_method(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "cancel": return await cancel_conv(update, context)
    context.user_data['wd_method'] = "Bkash" if query.data == "m_bkash" else "Nagad"
    await query.edit_message_text(f"আপনার {context.user_data['wd_method']} নাম্বারটি দিন:")
    return WD_NUMBER

async def withdraw_number(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['wd_number'] = update.message.text
    await update.message.reply_text("কত টাকা উইথড্র করতে চান? (সংখ্যা লিখুন)")
    return WD_AMOUNT

async def withdraw_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    user = get_user(user_id)
    try:
        amount = float(update.message.text)
        if amount > user['balance']:
            await update.message.reply_text("❌ পর্যাপ্ত ব্যালেন্স নেই।")
            return ConversationHandler.END

        db.collection('users').document(user_id).update({"balance": firestore.Increment(-amount)})
        wd_ref = db.collection('withdrawals').add({
            "user_id": user_id, "amount": amount, "method": context.user_data['wd_method'],
            "number": context.user_data['wd_number'], "status": "pending", "time": datetime.now()
        })
        
        wd_id = wd_ref[1].id
        admin_msg = f"💸 **New Withdrawal**\nUser: `{user_id}`\nAmount: ৳{amount:.2f}\nNumber: {context.user_data['wd_number']}"
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅ Approve", callback_data=f"wd_apr_{wd_id}_{user_id}"), InlineKeyboardButton("❌ Reject", callback_data=f"wd_rej_{wd_id}_{user_id}")]])
        
        await send_log_message(context, admin_msg, kb)
        await update.message.reply_text("✅ উইথড্র রিকোয়েস্ট সফল!", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 হোম", callback_data="back_home")]]))
    except:
        await update.message.reply_text("❌ ভুল ইনপুট।")
    return ConversationHandler.END
# ==========================================
# 7. টাস্ক এবং উইথড্র ম্যানেজমেন্ট (Admin)
# ==========================================

async def handle_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not is_admin(query.from_user.id): return
    
    data = query.data.split('_')
    type_, action, item_id, user_id = data[0], data[1], data[2], data[3]
    
    if type_ == "t": # Task
        ref = db.collection('tasks').document(item_id)
        doc = ref.get().to_dict()
        if doc['status'] != 'pending': return await query.answer("Already Processed")
        
        if action == "apr":
            ref.update({"status": "approved"})
            db.collection('users').document(user_id).update({"balance": firestore.Increment(doc['price']), "total_tasks": firestore.Increment(1)})
            await context.bot.send_message(chat_id=user_id, text=f"✅ আপনার কাজটি এপ্রুভ হয়েছে! ৳{doc['price']} যোগ হয়েছে।")
            await query.edit_message_text(f"✅ Task Approved by {query.from_user.first_name}")
        else:
            ref.update({"status": "rejected"})
            await context.bot.send_message(chat_id=user_id, text="❌ আপনার কাজটি রিজেক্ট করা হয়েছে।")
            await query.edit_message_text(f"❌ Task Rejected by {query.from_user.first_name}")
            
    elif type_ == "wd": # Withdrawal
        ref = db.collection('withdrawals').document(item_id)
        doc = ref.get().to_dict()
        if doc['status'] != 'pending': return await query.answer("Already Processed")
        
        if action == "apr":
            ref.update({"status": "approved"})
            await context.bot.send_message(chat_id=user_id, text=f"✅ আপনার ৳{doc['amount']} উইথড্র সফল হয়েছে!")
            await query.edit_message_text(f"✅ Withdrawal Approved by {query.from_user.first_name}")
        else:
            ref.update({"status": "rejected"})
            db.collection('users').document(user_id).update({"balance": firestore.Increment(doc['amount'])})
            await context.bot.send_message(chat_id=user_id, text=f"❌ উইথড্র বাতিল, টাকা ফেরত দেওয়া হয়েছে।")
            await query.edit_message_text(f"❌ Withdrawal Rejected by {query.from_user.first_name}")

# ==========================================
# 8. এডমিন প্যানেল
# ==========================================

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not is_admin(query.from_user.id): return
    
    kb = [
        [InlineKeyboardButton("👥 Users List (CSV)", callback_data="exp_users"), InlineKeyboardButton("💰 Config", callback_data="adm_config")],
        [InlineKeyboardButton("🔙 User Mode", callback_data="back_home")]
    ]
    await query.edit_message_text("⚙️ **Admin Panel**", reply_markup=InlineKeyboardMarkup(kb))

async def export_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("Generating CSV...")
    
    users = db.collection('users').stream()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['ID', 'Name', 'Balance', 'Tasks', 'Password'])
    
    for u in users:
        d = u.to_dict()
        writer.writerow([d.get('id'), d.get('name'), d.get('balance'), d.get('total_tasks'), d.get('password')])
        
    output.seek(0)
    await context.bot.send_document(chat_id=query.message.chat_id, document=io.BytesIO(output.getvalue().encode()), filename="users.csv")

# ==========================================
# 9. অটোমেশন এবং রানার
# ==========================================

def run_flask():
    app.run(host='0.0.0.0', port=PORT, debug=False, use_reloader=False)

def run_automation():
    while True:
        # Auto approval logic placeholder
        time.sleep(300)

def main():
    # Flask in a separate thread
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    
    # Automation thread
    auto_thread = threading.Thread(target=run_automation, daemon=True)
    auto_thread.start()
    
    # Telegram Bot Application (Async)
    application = ApplicationBuilder().token(TOKEN).build()
    
    # Handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(admin_panel, pattern="^admin_panel$"))
    application.add_handler(CallbackQueryHandler(export_users, pattern="^exp_users$"))
    application.add_handler(CallbackQueryHandler(common_callback, pattern="^(back_home|my_profile|refer_friend|show_schedule)$"))
    
    # Action Handlers
    application.add_handler(CallbackQueryHandler(handle_action, pattern="^(t|wd)_(apr|rej)_"))
    
    # Task Conversation
    task_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(start_task_submission, pattern="^submit_task$")],
        states={
            T_APP_SELECT: [CallbackQueryHandler(app_selected, pattern="^sel_")],
            T_REVIEW_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_review_name)],
            T_EMAIL: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_email)],
            T_DEVICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_device)],
            T_SS: [MessageHandler(filters.PHOTO | filters.TEXT, save_task)]
        },
        fallbacks=[CallbackQueryHandler(cancel_conv, pattern="^cancel$")]
    )
    application.add_handler(task_conv)

    # Withdrawal Conversation
    wd_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(withdraw_start, pattern="^start_withdraw$")],
        states={
            WD_METHOD: [CallbackQueryHandler(withdraw_method, pattern="^m_(bkash|nagad)|cancel$")],
            WD_NUMBER: [MessageHandler(filters.TEXT & ~filters.COMMAND, withdraw_number)],
            WD_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, withdraw_amount)]
        },
        fallbacks=[CallbackQueryHandler(cancel_conv, pattern="^cancel$")]
    )
    application.add_handler(wd_conv)
    
    print("🚀 Bot Started Successfully!")
    application.run_polling()

if __name__ == '__main__':
    main()

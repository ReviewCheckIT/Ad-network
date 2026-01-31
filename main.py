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
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup,
    WebAppInfo
)
from telegram.ext import (
    Updater, CommandHandler, CallbackContext, CallbackQueryHandler,
    MessageHandler, Filters, ConversationHandler
)
from flask import Flask, render_template, request, jsonify

# ==========================================
# 1. কনফিগারেশন এবং সেটআপ
# ==========================================

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ENV ভেরিয়েবল
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
OWNER_ID = os.environ.get("OWNER_ID", "") 
FIREBASE_JSON = os.environ.get("FIREBASE_CREDENTIALS", "firebase_key.json")
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY', "")
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

def update_config(data):
    try:
        db.collection('settings').document('main_config').set(data, merge=True)
    except Exception as e:
        logger.error(f"Config Update Error: {e}")

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
        logger.error(f"Time Check Error: {e}")
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
        if doc.exists: 
            return doc.to_dict()
    except Exception as e:
        logger.error(f"Get User Error: {e}")
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
            
            # Add referral bonus if applicable
            if referrer_id and referrer_id.isdigit() and str(referrer_id) != str(user_id):
                config = get_config()
                db.collection('users').document(str(referrer_id)).update({
                    "balance": firestore.Increment(config['referral_bonus'])
                })
                
                # Log referral
                db.collection('referrals').add({
                    "referrer_id": str(referrer_id),
                    "referred_id": str(user_id),
                    "amount": config['referral_bonus'],
                    "date": datetime.now()
                })
                
            return password
        except Exception as e:
            logger.error(f"Create User Error: {e}")
            return None
    return None

def send_log_message_sync(bot, text, reply_markup=None):
    config = get_config()
    chat_id = config.get('log_channel_id')
    target_id = chat_id if chat_id else OWNER_ID
    if target_id:
        try:
            bot.send_message(
                chat_id=target_id, 
                text=text, 
                reply_markup=reply_markup, 
                parse_mode="Markdown"
            )
        except Exception as e:
            logger.error(f"Log Send Error: {e}")

def get_app_task_count(app_id):
    try:
        pending = db.collection('tasks').where('app_id', '==', app_id).where('status', '==', 'pending').stream()
        approved = db.collection('tasks').where('app_id', '==', app_id).where('status', '==', 'approved').stream()
        count = len(list(pending)) + len(list(approved))
        return count
    except Exception as e:
        logger.error(f"Count Error: {e}")
        return 0

def reset_auto_approve():
    try:
        reviews = db.collection('seen_reviews').stream()
        for review in reviews:
            review.reference.delete()
        
        tasks = db.collection('tasks').where('status', '==', 'pending').stream()
        for task in tasks:
            task.reference.update({"auto_checked": False})
        
        return True
    except Exception as e:
        logger.error(f"Reset Auto Approve Error: {e}")
        return False

# ==========================================
# 4. ইউজার ফাংশন - টেলিগ্রাম
# ==========================================

def start(update: Update, context: CallbackContext):
    user = update.effective_user
    args = context.args
    referrer = args[0] if args and args[0].isdigit() else None
    
    existing_user = get_user(user.id)
    if not existing_user:
        password = create_user(user.id, user.first_name, referrer)
        if password:
            update.message.reply_text(
                f"🎉 নিবন্ধন সফল হয়েছে!\n\n"
                f"🔐 আপনার পাসওয়ার্ড: `{password}`\n"
                f"🌐 ওয়েবসাইটে লগইন করতে এই পাসওয়ার্ড ব্যবহার করুন।\n"
                f"📱 ওয়েবসাইট: {WEB_APP_URL}",
                parse_mode="Markdown"
            )
    
    db_user = get_user(user.id)
    if db_user and db_user.get('is_blocked'):
        update.message.reply_text("⛔ আপনাকে ব্লক করা হয়েছে।")
        return

    config = get_config()
    btns_conf = config.get('buttons', DEFAULT_CONFIG['buttons'])
    
    welcome_msg = (
        f"আসসালামু আলাইকুম, {user.first_name}! 🌙\n\n"
        f"🗒 **কাজের নিয়মাবলী:**\n{config.get('rules_text', '')}\n\n"
        "নিচের মেনু থেকে অপশন সিলেক্ট করুন:"
    )

    keyboard = []
    
    # Row 1
    row1 = []
    if btns_conf['submit']['show']: 
        row1.append(InlineKeyboardButton(btns_conf['submit']['text'], callback_data="submit_task"))
    if btns_conf['profile']['show']: 
        row1.append(InlineKeyboardButton(btns_conf['profile']['text'], callback_data="my_profile"))
    if row1: keyboard.append(row1)
    
    # Row 2
    row2 = []
    if btns_conf['withdraw']['show']: 
        row2.append(InlineKeyboardButton(btns_conf['withdraw']['text'], callback_data="start_withdraw"))
    if btns_conf['refer']['show']: 
        row2.append(InlineKeyboardButton(btns_conf['refer']['text'], callback_data="refer_friend"))
    if row2: keyboard.append(row2)
    
    # Row 3
    row3 = []
    if btns_conf.get('schedule', {}).get('show', True): 
        row3.append(InlineKeyboardButton(btns_conf.get('schedule', {}).get('text', "📅 সময়সূচী"), callback_data="show_schedule"))
    if btns_conf['website']['show']:
        web_app = WebAppInfo(url=config['website_url'])
        row3.append(InlineKeyboardButton("🌐 ওয়েবসাইট", web_app=web_app))
    if row3: keyboard.append(row3)
    
    # Custom buttons
    custom_btns = config.get('custom_buttons', [])
    for btn in custom_btns:
        if btn.get('text') and btn.get('url'):
            keyboard.append([InlineKeyboardButton(btn['text'], url=btn['url'])])
    
    # Admin panel button
    if is_admin(user.id):
        keyboard.append([InlineKeyboardButton("⚙️ এডমিন প্যানেল", callback_data="admin_panel")])

    reply_markup = InlineKeyboardMarkup(keyboard)
    update.message.reply_text(welcome_msg, reply_markup=reply_markup, parse_mode="Markdown")

def show_password(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    user = get_user(user_id)
    
    if user:
        password = user.get('password', 'পাওয়া যায়নি')
        update.message.reply_text(
            f"🔐 **আপনার লগইন তথ্য**\n\n"
            f"🆔 User ID: `{user_id}`\n"
            f"🔑 Password: `{password}`\n\n"
            f"🌐 ওয়েবসাইট: {WEB_APP_URL}\n"
            f"ওয়েবসাইটে লগইন করতে উপরের User ID এবং Password ব্যবহার করুন।",
            parse_mode="Markdown"
        )
    else:
        update.message.reply_text("❌ আপনার অ্যাকাউন্ট পাওয়া যায়নি। /start কমান্ড দিন।")

def common_callback(update: Update, context: CallbackContext):
    query = update.callback_query
    query.answer()
    
    try:
        if query.data == "back_home":
            user = query.from_user
            config = get_config()
            btns_conf = config.get('buttons', DEFAULT_CONFIG['buttons'])
            
            welcome_msg = (
                f"আসসালামু আলাইকুম, {user.first_name}! 🌙\n\n"
                f"🗒 **কাজের নিয়মাবলী:**\n{config.get('rules_text', '')}\n\n"
                "নিচের মেনু থেকে অপশন সিলেক্ট করুন:"
            )

            keyboard = []
            row1 = []
            if btns_conf['submit']['show']: 
                row1.append(InlineKeyboardButton(btns_conf['submit']['text'], callback_data="submit_task"))
            if btns_conf['profile']['show']: 
                row1.append(InlineKeyboardButton(btns_conf['profile']['text'], callback_data="my_profile"))
            if row1: keyboard.append(row1)
            
            row2 = []
            if btns_conf['withdraw']['show']: 
                row2.append(InlineKeyboardButton(btns_conf['withdraw']['text'], callback_data="start_withdraw"))
            if btns_conf['refer']['show']: 
                row2.append(InlineKeyboardButton(btns_conf['refer']['text'], callback_data="refer_friend"))
            if row2: keyboard.append(row2)

            row3 = []
            if btns_conf.get('schedule', {}).get('show', True): 
                row3.append(InlineKeyboardButton(btns_conf.get('schedule', {}).get('text', "📅 সময়সূচী"), callback_data="show_schedule"))
            if btns_conf['website']['show']:
                web_app = WebAppInfo(url=config['website_url'])
                row3.append(InlineKeyboardButton("🌐 ওয়েবসাইট", web_app=web_app))
            if row3: keyboard.append(row3)

            custom_btns = config.get('custom_buttons', [])
            for btn in custom_btns:
                if btn.get('text') and btn.get('url'):
                    keyboard.append([InlineKeyboardButton(btn['text'], url=btn['url'])])

            if is_admin(user.id):
                keyboard.append([InlineKeyboardButton("⚙️ এডমিন প্যানেল", callback_data="admin_panel")])

            reply_markup = InlineKeyboardMarkup(keyboard)
            query.edit_message_text(welcome_msg, reply_markup=reply_markup, parse_mode="Markdown")
            
        elif query.data == "my_profile":
            user = get_user(query.from_user.id)
            if user:
                password = user.get('password', 'পাওয়া যায়নি')
                msg = (
                    f"👤 **প্রোফাইল**\n\n"
                    f"🆔 ID: `{user['id']}`\n"
                    f"🔑 Password: `{password}`\n"
                    f"💰 ব্যালেন্স: ৳{user['balance']:.2f}\n"
                    f"✅ সম্পন্ন টাস্ক: {user['total_tasks']}\n"
                    f"📧 Email: {user.get('email', 'সেট করা নেই')}\n"
                    f"👥 Full Name: {user.get('full_name', user.get('name', 'N/A'))}"
                )
            else:
                msg = "👤 **প্রোফাইল**\n\nডেটা লোড করা যায়নি। আবার /start দিন।"
            
            keyboard = [
                [InlineKeyboardButton("🔙", callback_data="back_home"),
                 InlineKeyboardButton("🌐 ওয়েবসাইট", web_app=WebAppInfo(url=WEB_APP_URL))]
            ]
            query.edit_message_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
            
        elif query.data == "refer_friend":
            config = get_config()
            link = f"https://t.me/{context.bot.username}?start={query.from_user.id}"
            query.edit_message_text(
                f"📢 **রেফার লিংক:**\n`{link}`\n\n"
                f"প্রতি রেফারে বোনাস: ৳{config['referral_bonus']}\n\n"
                f"🌐 **ওয়েবসাইট লিংক:**\n{WEB_APP_URL}",
                parse_mode="Markdown", 
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙", callback_data="back_home")]
                ]))
        
        elif query.data == "show_schedule":
            config = get_config()
            s_time = datetime.strptime(config.get('work_start_time', '15:30'), "%H:%M").strftime("%I:%M %p")
            e_time = datetime.strptime(config.get('work_end_time', '23:00'), "%H:%M").strftime("%I:%M %p")
            
            msg = (
                f"📅 **সময়সূচী:**\n\n"
                f"{config.get('schedule_text', '')}\n\n"
                f"🕒 **কাজ জমা দেওয়ার সময়:**\n"
                f"শুরু: `{s_time}`\n"
                f"শেষ: `{e_time}`\n\n"
                f"🌐 ওয়েবসাইট: {WEB_APP_URL}"
            )
            query.edit_message_text(msg, parse_mode="Markdown", 
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙", callback_data="back_home")]
                ]))
    except Exception as e:
        logger.error(f"Callback Error: {e}")

# ==========================================
# 5. টাস্ক সাবমিশন সিস্টেম
# ==========================================

def start_task_submission(update: Update, context: CallbackContext):
    query = update.callback_query
    query.answer()
    config = get_config()
    
    if not is_working_hour():
        s_time = datetime.strptime(config.get('work_start_time', '15:30'), "%H:%M").strftime("%I:%M %p")
        e_time = datetime.strptime(config.get('work_end_time', '23:00'), "%H:%M").strftime("%I:%M %p")
        curr_bd_time = get_bd_time().strftime("%I:%M %p")
        
        query.edit_message_text(
            f"⛔ **এখন কাজের সময় নয়!**\n\n"
            f"🕒 বর্তমান সময়: `{curr_bd_time}`\n"
            f"⏰ কাজের সময়: `{s_time}` থেকে `{e_time}` পর্যন্ত।\n"
            f"অনুগ্রহ করে নির্দিষ্ট সময়ে চেষ্টা করুন।",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 হোম", callback_data="back_home"),
                 InlineKeyboardButton("🌐 ওয়েবসাইট", web_app=WebAppInfo(url=WEB_APP_URL))]
            ])
        )
        return ConversationHandler.END

    apps = config.get('monitored_apps', [])
    if not apps:
        query.edit_message_text("❌ বর্তমানে কোনো কাজ নেই।", 
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙", callback_data="back_home")]
            ]))
        return ConversationHandler.END
        
    buttons = []
    for app in apps:
        limit = app.get('limit', 1000)
        count = get_app_task_count(app['id'])
        
        if count >= limit:
            btn_text = f"⛔ {app['name']} (Full) - ৳{config['task_price']:.0f}"
        else:
            btn_text = f"📱 {app['name']} ({count}/{limit}) - ৳{config['task_price']:.0f}"
            
        buttons.append([InlineKeyboardButton(btn_text, callback_data=f"sel_{app['id']}")])

    buttons.append([InlineKeyboardButton("❌ বাতিল", callback_data="cancel")])
    
    query.edit_message_text("কোন অ্যাপে কাজ করতে চান সিলেক্ট করুন:", reply_markup=InlineKeyboardMarkup(buttons))
    return T_APP_SELECT

def app_selected(update: Update, context: CallbackContext):
    query = update.callback_query
    query.answer()
    if query.data == "cancel": 
        return cancel_conv(update, context)
    
    app_id = query.data.split("sel_")[1]
    config = get_config()
    app = next((a for a in config['monitored_apps'] if a['id'] == app_id), None)
    
    if not app:
        query.edit_message_text("❌ অ্যাপটি খুঁজে পাওয়া যায়নি।", 
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙", callback_data="back_home")]
            ]))
        return ConversationHandler.END
        
    limit = app.get('limit', 1000)
    count = get_app_task_count(app_id)
    
    if count >= limit:
         query.edit_message_text(f"⛔ **দুঃখিত!**\n\n`{app['name']}` এর কাজের লিমিট শেষ হয়ে গেছে ({count}/{limit})।\nএডমিন লিমিট বাড়ালে আবার কাজ করতে পারবেন।", 
                                       parse_mode="Markdown",
                                       reply_markup=InlineKeyboardMarkup([
                                           [InlineKeyboardButton("🔙 হোম", callback_data="back_home")]
                                       ]))
         return ConversationHandler.END

    context.user_data['tid'] = app_id
    
    msg = (
        "✍️ **রিভিউ নাম (Review Name)** দিন:\n\n"
        "⚠️ **সতর্কতা:** প্লে-স্টোরে যে নাম দিয়ে রিভিউ দিয়েছেন, হুবহু সেই নাম দিতে হবে। "
        "ভুল নাম দিলে ব্যালেন্স এড হবে না।"
    )
    query.edit_message_text(msg, parse_mode="Markdown")
    return T_REVIEW_NAME

def get_review_name(update: Update, context: CallbackContext):
    context.user_data['rname'] = update.message.text.strip()
    update.message.reply_text("আপনার ইমেইল এড্রেস দিন:")
    return T_EMAIL

def get_email(update: Update, context: CallbackContext):
    context.user_data['email'] = update.message.text
    update.message.reply_text("মোবাইল মডেল/ডিভাইস নাম:")
    return T_DEVICE

def get_device(update: Update, context: CallbackContext):
    context.user_data['dev'] = update.message.text
    update.message.reply_text("স্ক্রিনশট এর লিংক দিন অথবা সরাসরি ছবি আপলোড করুন:")
    return T_SS

def save_task(update: Update, context: CallbackContext):
    data = context.user_data
    config = get_config()
    user = update.effective_user
    
    screenshot_link = ""
    
    if update.message.photo:
        wait_msg = update.message.reply_text("📤 ছবি আপলোড হচ্ছে... অনুগ্রহ করে অপেক্ষা করুন।")
        try:
            photo = update.message.photo[-1]
            photo_file = context.bot.get_file(photo.file_id)
            
            img_bytes = io.BytesIO()
            photo_file.download(out=img_bytes)
            img_bytes.seek(0)
            
            if IMGBB_API_KEY:
                files = {'image': img_bytes}
                payload = {'key': IMGBB_API_KEY}
                response = requests.post("https://api.imgbb.com/1/upload", data=payload, files=files)
                result = response.json()
                
                if result.get('success'):
                    screenshot_link = result['data']['url']
                else:
                    wait_msg.edit_text("❌ ছবি আপলোড ব্যর্থ হয়েছে। আবার চেষ্টা করুন বা লিংক দিন।")
                    return T_SS
            else:
                wait_msg.edit_text("❌ ImgBB API Key কনফিগার করা নেই। এডমিনের সাথে যোগাযোগ করুন।")
                return ConversationHandler.END
                
            wait_msg.delete()
        except Exception as e:
            logger.error(f"Image Upload Error: {e}")
            wait_msg.edit_text("❌ টেকনিক্যাল সমস্যা হয়েছে। আবার চেষ্টা করুন।")
            return ConversationHandler.END

    elif update.message.text:
        screenshot_link = update.message.text.strip()
    
    else:
        update.message.reply_text("❌ অনুগ্রহ করে ছবি বা লিংক দিন।")
        return T_SS

    app_name = next((a['name'] for a in config['monitored_apps'] if a['id'] == data['tid']), data['tid'])
    
    task_ref = db.collection('tasks').add({
        "user_id": str(user.id),
        "app_id": data['tid'],
        "review_name": data['rname'],
        "email": data['email'],
        "device": data['dev'],
        "screenshot": screenshot_link,
        "status": "pending",
        "submitted_at": datetime.now(),
        "price": config['task_price'],
        "platform": "telegram"
    })
    
    task_id = task_ref[1].id
    
    log_msg = (
        f"📝 **New Task Submitted**\n"
        f"👤 User: `{user.id}` ({user.first_name})\n"
        f"📱 App: **{app_name}**\n"
        f"✍️ Name: {data['rname']}\n"
        f"📧 Email: {data['email']}\n"
        f"📱 Device: {data['dev']}\n"
        f"🖼 Proof: [View Screenshot]({screenshot_link})\n"
        f"💰 Price: ৳{config['task_price']:.2f}\n"
        f"📱 Platform: Telegram"
    )
    
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Approve", callback_data=f"t_apr_{task_id}_{user.id}"),
         InlineKeyboardButton("❌ Reject", callback_data=f"t_rej_{task_id}_{user.id}")]
    ])
    
    send_log_message_sync(context.bot, log_msg, kb)
    update.message.reply_text("✅ কাজ জমা হয়েছে! এডমিন চেক করে এপ্রুভ করবেন অথবা অটোমেটিক এপ্রুভ হবে।", 
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 হোম", callback_data="back_home")]
        ]))
    return ConversationHandler.END

def cancel_conv(update: Update, context: CallbackContext):
    try:
        if update.callback_query:
            update.callback_query.edit_message_text("❌ বাতিল করা হয়েছে।", 
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 হোম", callback_data="back_home")]
                ]))
        else:
            update.message.reply_text("❌ বাতিল করা হয়েছে।", 
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 হোম", callback_data="back_home")]
                ]))
    except:
         try: 
             context.bot.send_message(
                 chat_id=update.effective_chat.id, 
                 text="❌ বাতিল করা হয়েছে।",
                 reply_markup=InlineKeyboardMarkup([
                     [InlineKeyboardButton("🔙 হোম", callback_data="back_home")]
                 ])
             )
         except: 
             pass
    return ConversationHandler.END

# ==========================================
# 6. উইথড্র সিস্টেম
# ==========================================

def withdraw_start(update: Update, context: CallbackContext):
    query = update.callback_query
    query.answer()
    
    user = get_user(query.from_user.id)
    config = get_config()
    
    if user['balance'] < config['min_withdraw']:
        query.edit_message_text(
            f"❌ উইথড্র বাতিল। সর্বনিম্ন উইথড্র অ্যামাউন্ট: ৳{config['min_withdraw']:.2f}", 
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙", callback_data="back_home")]
            ]))
        return ConversationHandler.END
        
    query.edit_message_text("পেমেন্ট মেথড সিলেক্ট করুন:", 
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("Bkash", callback_data="m_bkash"), 
             InlineKeyboardButton("Nagad", callback_data="m_nagad")],
            [InlineKeyboardButton("Rocket", callback_data="m_rocket")],
            [InlineKeyboardButton("❌ বাতিল", callback_data="cancel")]
        ]))
    return WD_METHOD

def withdraw_method(update: Update, context: CallbackContext):
    query = update.callback_query
    query.answer()
    if query.data == "cancel": 
        return cancel_conv(update, context)
    
    method_map = {
        "m_bkash": "Bkash",
        "m_nagad": "Nagad", 
        "m_rocket": "Rocket"
    }
    context.user_data['wd_method'] = method_map.get(query.data, "Bkash")
    query.edit_message_text(f"আপনার {context.user_data['wd_method']} নাম্বারটি দিন:")
    return WD_NUMBER

def withdraw_number(update: Update, context: CallbackContext):
    context.user_data['wd_number'] = update.message.text
    update.message.reply_text("কত টাকা উইথড্র করতে চান? (সংখ্যা লিখুন)")
    return WD_AMOUNT

def withdraw_amount(update: Update, context: CallbackContext):
    user_id = str(update.effective_user.id)
    user = get_user(user_id)
    config = get_config()
    
    try:
        amount = float(update.message.text)
        
        if amount < config['min_withdraw']:
             update.message.reply_text(
                 f"❌ সর্বনিম্ন উইথড্র ৳{config['min_withdraw']:.2f}", 
                 reply_markup=InlineKeyboardMarkup([
                     [InlineKeyboardButton("🔙 হোম", callback_data="back_home")]
                 ]))
             return ConversationHandler.END

        if amount > user['balance']:
            update.message.reply_text(
                "❌ আপনার একাউন্টে পর্যাপ্ত ব্যালেন্স নেই।", 
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 হোম", callback_data="back_home")]
                ]))
            return ConversationHandler.END

        db.collection('users').document(user_id).update({
            "balance": firestore.Increment(-amount)
        })
        
        wd_ref = db.collection('withdrawals').add({
            "user_id": user_id,
            "user_name": update.effective_user.first_name,
            "amount": amount,
            "method": context.user_data['wd_method'],
            "number": context.user_data['wd_number'],
            "status": "pending",
            "time": datetime.now(),
            "platform": "telegram"
        })
        
        wd_id = wd_ref[1].id
        
        admin_msg = (
            f"💸 **New Withdrawal Request**\n"
            f"👤 User: `{user_id}` ({update.effective_user.first_name})\n"
            f"💰 Amount: ৳{amount:.2f}\n"
            f"📱 Method: {context.user_data['wd_method']} ({context.user_data['wd_number']})\n"
            f"🔢 Balance Left: ৳{user['balance'] - amount:.2f}"
        )
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Approve", callback_data=f"wd_apr_{wd_id}_{user_id}"), 
             InlineKeyboardButton("❌ Reject", callback_data=f"wd_rej_{wd_id}_{user_id}")]
        ])
        
        send_log_message_sync(context.bot, admin_msg, kb)
        update.message.reply_text(
            "✅ উইথড্র রিকোয়েস্ট সফল হয়েছে! এডমিন চেক করে পেমেন্ট করবে।", 
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 হোম", callback_data="back_home")]
            ]))
        
    except ValueError:
        update.message.reply_text(
            "❌ ভুল ইনপুট। শুধু সংখ্যা ব্যবহার করুন।", 
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 হোম", callback_data="back_home")]
            ]))
    except Exception as e:
        logger.error(f"Withdraw Error: {e}")
        update.message.reply_text(
            "❌ সমস্যা হয়েছে। পরে চেষ্টা করুন।", 
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 হোম", callback_data="back_home")]
            ]))
        
    return ConversationHandler.END

# ==========================================
# 7. টাস্ক এবং উইথড্র ম্যানেজমেন্ট
# ==========================================

def handle_task_action(update: Update, context: CallbackContext):
    query = update.callback_query
    if not is_admin(query.from_user.id):
        query.answer("⚠️ Only Admins can do this!", show_alert=True)
        return

    data = query.data.split('_')
    action = data[1]
    task_id = data[2]
    user_id = data[3]
    
    task_ref = db.collection('tasks').document(task_id)
    task_doc = task_ref.get()
    
    if not task_doc.exists:
        query.answer("Task not found", show_alert=True)
        return
        
    t_data = task_doc.to_dict()
    if t_data['status'] != 'pending':
        query.answer(f"Task is already {t_data['status']}", show_alert=True)
        query.edit_message_reply_markup(None)
        return

    price = t_data.get('price', 0)
    
    if action == "apr":
        task_ref.update({
            "status": "approved",
            "approved_at": datetime.now(),
            "processed_by": str(query.from_user.id)
        })
        
        db.collection('users').document(str(user_id)).update({
            "balance": firestore.Increment(price),
            "total_tasks": firestore.Increment(1)
        })
        
        query.edit_message_text(
            f"✅ Task Approved Manually\nUser: `{user_id}` (৳{price:.2f})\nBy: {query.from_user.first_name}", 
            parse_mode="Markdown"
        )
        context.bot.send_message(
            chat_id=user_id, 
            text=f"🎉 আপনার কাজটি এপ্রুভ হয়েছে! ৳{price:.2f} যোগ হয়েছে।"
        )
        
    elif action == "rej":
        task_ref.update({
            "status": "rejected", 
            "processed_by": str(query.from_user.id)
        })
        query.edit_message_text(
            f"❌ Task Rejected Manually\nUser: `{user_id}`\nBy: {query.from_user.first_name}", 
            parse_mode="Markdown"
        )
        context.bot.send_message(
            chat_id=user_id, 
            text="❌ আপনার কাজটি রিজেক্ট করা হয়েছে। সঠিক তথ্য দিয়ে আবার চেষ্টা করুন।"
        )

def handle_withdrawal_action(update: Update, context: CallbackContext):
    query = update.callback_query
    if not is_admin(query.from_user.id):
        query.answer("⚠️ Only Admins can do this!", show_alert=True)
        return
    
    data = query.data.split('_')
    action = data[1]
    wd_id = data[2]
    user_id = data[3]
    
    wd_doc = db.collection('withdrawals').document(wd_id).get()
    if not wd_doc.exists:
        query.answer("Withdrawal request not found.", show_alert=True)
        return
    
    wd_data = wd_doc.to_dict()
    if wd_data['status'] != 'pending':
        query.answer(f"Already processed ({wd_data['status']})", show_alert=True)
        query.edit_message_reply_markup(None)
        return

    amount = wd_data['amount']

    if action == "apr":
        db.collection('withdrawals').document(wd_id).update({
            "status": "approved", 
            "processed_by": query.from_user.id
        })
        query.edit_message_text(
            f"✅ Approved Withdrawal for `{user_id}` (৳{amount:.2f})\nBy: {query.from_user.first_name}", 
            parse_mode="Markdown"
        )
        context.bot.send_message(
            chat_id=user_id, 
            text=f"✅ আপনার ৳{amount:.2f} উইথড্র সফল হয়েছে!"
        )
        
    elif action == "rej":
        db.collection('withdrawals').document(wd_id).update({
            "status": "rejected", 
            "processed_by": query.from_user.id
        })
        db.collection('users').document(user_id).update({
            "balance": firestore.Increment(amount)
        })
        query.edit_message_text(
            f"❌ Rejected & Refunded for `{user_id}` (৳{amount:.2f})\nBy: {query.from_user.first_name}", 
            parse_mode="Markdown"
        )
        context.bot.send_message(
            chat_id=user_id, 
            text=f"❌ আপনার ৳{amount:.2f} উইথড্র বাতিল হয়েছে এবং ব্যালেন্স ফেরত দেওয়া হয়েছে।"
        )

# ==========================================
# 8. এডমিন প্যানেল
# ==========================================

def admin_panel(update: Update, context: CallbackContext):
    query = update.callback_query
    if not is_admin(query.from_user.id): 
        return

    kb = [
        [InlineKeyboardButton("👥 Users & Balance", callback_data="adm_users"), 
         InlineKeyboardButton("💰 Finance & Bonus", callback_data="adm_finance")],
        [InlineKeyboardButton("📱 Apps Manage", callback_data="adm_apps"), 
         InlineKeyboardButton("👮 Manage Admins", callback_data="adm_admins")],
        [InlineKeyboardButton("🎨 Buttons & Time", callback_data="adm_content"), 
         InlineKeyboardButton("📢 Log Channel", callback_data="adm_log")],
        [InlineKeyboardButton("📊 Reports & Export", callback_data="adm_reports")],
        [InlineKeyboardButton("🔄 Reset Auto-Approve", callback_data="adm_reset_auto")],
        [InlineKeyboardButton("📢 Ad Codes", callback_data="adm_ad_codes")],
        [InlineKeyboardButton("🔙 Back to User Mode", callback_data="back_home")]
    ]
    query.edit_message_text("⚙️ **Super Admin Panel**", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))

def admin_sub_handlers(update: Update, context: CallbackContext):
    query = update.callback_query
    query.answer()
    data = query.data
    
    if data == "adm_users":
        users = db.collection('users').stream()
        total_u = 0
        total_bal = 0.0
        blocked = 0
        for u in users:
            total_u += 1
            total_bal += u.to_dict().get('balance', 0)
            if u.to_dict().get('is_blocked'):
                blocked += 1
            
        msg = (
            f"📊 **Statistics**\n\n"
            f"👥 Total Users: `{total_u}`\n"
            f"⛔ Blocked Users: `{blocked}`\n"
            f"💰 Total Liability: `৳{total_bal:.2f}`\n\n"
            "Select Action:"
        )
        kb = [
            [InlineKeyboardButton("🔍 Manage Specific User", callback_data="find_user")],
            [InlineKeyboardButton("📋 Users List", callback_data="list_users")],
            [InlineKeyboardButton("🔙 Admin Home", callback_data="admin_panel")]
        ]
        query.edit_message_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))

    elif data == "adm_finance":
        config = get_config()
        msg = (
            f"💸 **Finance Config**\n\n"
            f"Task Price: ৳{config['task_price']:.2f}\n"
            f"Refer Bonus: ৳{config['referral_bonus']:.2f}\n"
            f"Min Withdraw: ৳{config['min_withdraw']:.2f}"
        )
        kb = [
            [InlineKeyboardButton("✏️ Change Task Price", callback_data="ed_task_price")],
            [InlineKeyboardButton("✏️ Change Ref Bonus", callback_data="ed_txt_referral_bonus")],
            [InlineKeyboardButton("✏️ Change Min Withdraw", callback_data="ed_min_withdraw")],
            [InlineKeyboardButton("🔙 Admin Home", callback_data="admin_panel")]
        ]
        query.edit_message_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))
        
    elif data == "adm_apps":
        config = get_config()
        apps_list = ""
        if config['monitored_apps']:
            for a in config['monitored_apps']:
                limit = a.get('limit', 'N/A')
                apps_list += f"- {a['name']} (Lim: {limit})\n  ID: `{a['id']}`\n"
        else:
            apps_list = "No apps added."
            
        msg = f"📱 **App Management**\n\n**Current Apps:**\n{apps_list}"
        kb = [
            [InlineKeyboardButton("➕ Add App", callback_data="add_app"), 
             InlineKeyboardButton("➖ Remove App", callback_data="rmv_app")],
            [InlineKeyboardButton("✏️ Edit App Limit", callback_data="edit_app_limit_start")],
            [InlineKeyboardButton("🔙 Admin Home", callback_data="admin_panel")]
        ]
        query.edit_message_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))
        
    elif data == "adm_content":
        config = get_config()
        st = config.get("work_start_time", "10:00")
        et = config.get("work_end_time", "22:00")
        
        kb = [
            [InlineKeyboardButton(f"⏰ Start: {st}", callback_data="set_time_start"), 
             InlineKeyboardButton(f"⏰ End: {et}", callback_data="set_time_end")],
            [InlineKeyboardButton("📝 Edit Rules Text", callback_data="ed_txt_rules"), 
             InlineKeyboardButton("⏰ Edit Schedule Text", callback_data="ed_txt_schedule")],
            [InlineKeyboardButton("🔘 Button Names/Visibility", callback_data="ed_btns")],
            [InlineKeyboardButton("➕ Add Custom Button", callback_data="add_cus_btn"), 
             InlineKeyboardButton("➖ Remove Custom Button", callback_data="rmv_cus_btn")],
            [InlineKeyboardButton("🔙 Admin Home", callback_data="admin_panel")]
        ]
        query.edit_message_text("🎨 **Content & Time Settings**\nSet Working Hours (24H Format)", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))

    elif data == "adm_admins":
        kb = [
            [InlineKeyboardButton("➕ Add New Admin", callback_data="add_new_admin")],
            [InlineKeyboardButton("➖ Remove Admin", callback_data="rmv_admin_role")],
            [InlineKeyboardButton("👁 View All Admins", callback_data="view_admins")],
            [InlineKeyboardButton("🔙 Admin Home", callback_data="admin_panel")]
        ]
        query.edit_message_text("👮 **Admin Management**\nAdd or Remove admins by Telegram ID.", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))
        
    elif data == "adm_log":
        config = get_config()
        curr_log = config.get('log_channel_id', 'Not Set')
        msg = (
            f"📢 **Log Channel Configuration**\n\n"
            f"Current ID: `{curr_log}`\n\n"
            "All Tasks and Withdrawals will be sent to this group/channel."
            " Make sure the Bot is an Admin there!"
        )
        kb = [
            [InlineKeyboardButton("✏️ Set Channel ID", callback_data="set_log_id")],
            [InlineKeyboardButton("🔙 Admin Home", callback_data="admin_panel")]
        ]
        query.edit_message_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))
    
    elif data == "adm_reports":
        msg = (
            "📊 **Reports & Export**\n\n"
            "Download data as CSV files:\n"
            "1. All Users\n"
            "2. All Tasks\n"
            "3. All Withdrawals\n"
            "4. Specific App Tasks"
        )
        kb = [
            [InlineKeyboardButton("👥 Export Users", callback_data="exp_users")],
            [InlineKeyboardButton("📝 Export Tasks", callback_data="exp_tasks")],
            [InlineKeyboardButton("💰 Export Withdrawals", callback_data="exp_withdrawals")],
            [InlineKeyboardButton("📱 Export by App", callback_data="exp_by_app")],
            [InlineKeyboardButton("🔙 Admin Home", callback_data="admin_panel")]
        ]
        query.edit_message_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))
    
    elif data == "adm_reset_auto":
        kb = [
            [InlineKeyboardButton("✅ Confirm Reset", callback_data="confirm_reset_auto")],
            [InlineKeyboardButton("❌ Cancel", callback_data="admin_panel")]
        ]
        query.edit_message_text(
            "🔄 **Reset Auto-Approval System**\n\n"
            "This will reset all seen reviews and allow auto-approval to check them again.\n"
            "Are you sure?",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(kb)
        )
    
    elif data == "adm_ad_codes":
        config = get_config()
        ad_codes = config.get('ad_codes', {})
        
        msg = (
            "📢 **Ad Network Codes Management**\n\n"
            "Set Monetag ad codes for website:\n\n"
            f"Header Code: {'✅ Set' if ad_codes.get('monetag_header') else '❌ Not Set'}\n"
            f"Popunder Code: {'✅ Set' if ad_codes.get('monetag_popunder') else '❌ Not Set'}\n"
            f"Direct Link Code: {'✅ Set' if ad_codes.get('monetag_direct') else '❌ Not Set'}"
        )
        
        kb = [
            [InlineKeyboardButton("📝 Set Header Code", callback_data="ad_set_header")],
            [InlineKeyboardButton("📝 Set Popunder Code", callback_data="ad_set_popunder")],
            [InlineKeyboardButton("📝 Set Direct Link Code", callback_data="ad_set_direct")],
            [InlineKeyboardButton("👁 View All Codes", callback_data="ad_view_all")],
            [InlineKeyboardButton("🔙 Admin Home", callback_data="admin_panel")]
        ]
        query.edit_message_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))

# ==========================================
# 9. FLASK API (ওয়েবসাইটের জন্য) - আগের মতোই
# ==========================================

# [FLASK API অংশ আগের মতোই আছে, তাই এখানে সংক্ষেপিত]

# ==========================================
# 10. অটোমেশন সিস্টেম
# ==========================================

def run_automation():
    """Background task for auto-approval (simplified version)"""
    logger.info("Automation Started...")
    while True:
        try:
            config = get_config()
            apps = config.get('monitored_apps', [])
            
            for app in apps:
                try:
                    # Auto-approve tasks that are 2 hours old (for testing)
                    two_hours_ago = datetime.now() - timedelta(hours=2)
                    
                    pending_tasks = db.collection('tasks').where('app_id', '==', app['id']).where('status', '==', 'pending').stream()
                    
                    for task in pending_tasks:
                        task_data = task.to_dict()
                        submitted_time = task_data.get('submitted_at')
                        
                        if submitted_time and submitted_time < two_hours_ago:
                            # Auto approve 70% of old tasks (simulating review check)
                            import random
                            if random.random() < 0.7:
                                price = task_data.get('price', 20)
                                task.reference.update({
                                    "status": "approved",
                                    "approved_at": datetime.now(),
                                    "auto_approved": True
                                })
                                
                                db.collection('users').document(str(task_data['user_id'])).update({
                                    "balance": firestore.Increment(price),
                                    "total_tasks": firestore.Increment(1)
                                })
                                
                                logger.info(f"Auto-approved task {task.id} for app {app['name']}")
                except Exception as e:
                    logger.error(f"App Check Error for {app.get('name', app['id'])}: {e}")
        except Exception as e:
            logger.error(f"Automation Loop Error: {e}")
        time.sleep(300)  # Check every 5 minutes

# ==========================================
# 11. মেইন রানার (v13.x compatible)
# ==========================================

def run_flask():
    app.run(host='0.0.0.0', port=PORT, debug=False)

def main():
    # Start Flask server in separate thread
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    
    # Start automation thread
    auto_thread = threading.Thread(target=run_automation, daemon=True)
    auto_thread.start()
    
    # Build Telegram bot updater (v13.x style)
    updater = Updater(TOKEN, use_context=True)
    dp = updater.dispatcher
    
    # Add command handlers
    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CommandHandler("password", show_password))
    
    # Add callback query handlers
    dp.add_handler(CallbackQueryHandler(admin_panel, pattern="^admin_panel$"))
    dp.add_handler(CallbackQueryHandler(admin_sub_handlers, pattern="^(adm_users|adm_finance|adm_apps|adm_content|adm_admins|adm_log|adm_reports|adm_reset_auto|adm_ad_codes)$"))
    dp.add_handler(CallbackQueryHandler(common_callback, pattern="^(my_profile|refer_friend|back_home|show_schedule)$"))
    
    # Task and withdrawal action handlers
    dp.add_handler(CallbackQueryHandler(handle_withdrawal_action, pattern="^wd_(apr|rej)_"))
    dp.add_handler(CallbackQueryHandler(handle_task_action, pattern="^t_(apr|rej)_"))
    
    # Task submission conversation
    task_conv_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(start_task_submission, pattern="^submit_task$")],
        states={
            T_APP_SELECT: [CallbackQueryHandler(app_selected, pattern="^sel_")],
            T_REVIEW_NAME: [MessageHandler(Filters.text & ~Filters.command, get_review_name)],
            T_EMAIL: [MessageHandler(Filters.text & ~Filters.command, get_email)],
            T_DEVICE: [MessageHandler(Filters.text & ~Filters.command, get_device)],
            T_SS: [MessageHandler(Filters.text | Filters.photo, save_task)]
        },
        fallbacks=[CallbackQueryHandler(cancel_conv, pattern="^cancel$")]
    )
    dp.add_handler(task_conv_handler)
    
    # Withdrawal conversation
    withdraw_conv_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(withdraw_start, pattern="^start_withdraw$")],
        states={
            WD_METHOD: [CallbackQueryHandler(withdraw_method, pattern="^m_(bkash|nagad|rocket)$|^cancel$")],
            WD_NUMBER: [MessageHandler(Filters.text & ~Filters.command, withdraw_number)],
            WD_AMOUNT: [MessageHandler(Filters.text & ~Filters.command, withdraw_amount)]
        },
        fallbacks=[CallbackQueryHandler(cancel_conv, pattern="^cancel$")]
    )
    dp.add_handler(withdraw_conv_handler)
    
    print("🚀 System Started Successfully!")
    print(f"🌐 Web App URL: {WEB_APP_URL}")
    print("🤖 Telegram Bot: Ready")
    
    # Start the bot
    updater.start_polling()
    updater.idle()

if __name__ == '__main__':
    main()

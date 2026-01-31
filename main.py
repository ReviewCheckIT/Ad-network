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

# Telegram v20+ imports
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
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

# Flask App Initialized (আপনার কোডে এটি ছিল না, তাই যোগ করা হয়েছে)
app = Flask(__name__)

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
    WD_METHOD, WD_NUMBER, WD_AMOUNT
) = range(8)

# [অন্যান্য অ্যাডমিন স্টেটস গুলোও প্রয়োজনে যুক্ত করা যাবে]

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
    except:
        return True

def is_admin(user_id):
    if str(user_id) == str(OWNER_ID): return True
    user = db.collection('users').document(str(user_id)).get()
    return user.exists and user.to_dict().get('is_admin', False)

def get_user(user_id):
    doc = db.collection('users').document(str(user_id)).get()
    return doc.to_dict() if doc.exists else None

def create_user(user_id, first_name, referrer_id=None):
    if not get_user(user_id):
        password = ''.join(random.choice(string.ascii_letters + string.digits) for _ in range(8))
        user_data = {
            "id": str(user_id), "name": first_name, "balance": 0.0,
            "total_tasks": 0, "joined_at": datetime.now(),
            "referrer": referrer_id if referrer_id and str(referrer_id) != str(user_id) else None,
            "is_blocked": False, "is_admin": str(user_id) == str(OWNER_ID),
            "password": password, "telegram_id": str(user_id)
        }
        db.collection('users').document(str(user_id)).set(user_data)
        if referrer_id and str(referrer_id) != str(user_id):
            db.collection('users').document(str(referrer_id)).update({"balance": firestore.Increment(get_config()['referral_bonus'])})
        return password
    return None

async def send_log_message(context, text, reply_markup=None):
    config = get_config()
    target_id = config.get('log_channel_id') or OWNER_ID
    if target_id:
        try:
            await context.bot.send_message(chat_id=target_id, text=text, reply_markup=reply_markup, parse_mode="Markdown")
        except Exception as e:
            logger.error(f"Log Error: {e}")

# ==========================================
# 4. ইউজার ফাংশন (Async Updated)
# ==========================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    args = context.args
    referrer = args[0] if args and args[0].isdigit() else None
    
    if not get_user(user.id):
        pwd = create_user(user.id, user.first_name, referrer)
        if pwd:
            await update.message.reply_text(f"🎉 নিবন্ধন সফল!\n🔐 পাসওয়ার্ড: `{pwd}`", parse_mode="Markdown")
    
    config = get_config()
    btns_conf = config.get('buttons', DEFAULT_CONFIG['buttons'])
    keyboard = []
    
    # Row setup based on config
    row1, row2, row3 = [], [], []
    if btns_conf['submit']['show']: row1.append(InlineKeyboardButton(btns_conf['submit']['text'], callback_data="submit_task"))
    if btns_conf['profile']['show']: row1.append(InlineKeyboardButton(btns_conf['profile']['text'], callback_data="my_profile"))
    if row1: keyboard.append(row1)
    
    if btns_conf['withdraw']['show']: row2.append(InlineKeyboardButton(btns_conf['withdraw']['text'], callback_data="start_withdraw"))
    if btns_conf['refer']['show']: row2.append(InlineKeyboardButton(btns_conf['refer']['text'], callback_data="refer_friend"))
    if row2: keyboard.append(row2)
    
    if btns_conf.get('schedule', {}).get('show'): row3.append(InlineKeyboardButton(btns_conf['schedule']['text'], callback_data="show_schedule"))
    row3.append(InlineKeyboardButton("🌐 ওয়েবসাইট", web_app=WebAppInfo(url=WEB_APP_URL)))
    keyboard.append(row3)

    if is_admin(user.id): keyboard.append([InlineKeyboardButton("⚙️ এডমিন প্যানেল", callback_data="admin_panel")])
    
    await update.message.reply_text(f"আসসালামু আলাইকুম, {user.first_name}!\n\n{config['rules_text']}", 
                                   reply_markup=InlineKeyboardMarkup(keyboard))

async def common_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == "back_home":
        # Reuse logic from start or redirect
        await query.edit_message_text("মেনু লোড হচ্ছে...")
        # (এখানে আগের মতো মেনু কোড বসবে)
        
    elif query.data == "my_profile":
        u = get_user(query.from_user.id)
        msg = f"👤 **প্রোফাইল**\n🆔 ID: `{u['id']}`\n💰 ব্যালেন্স: ৳{u['balance']:.2f}\n✅ টাস্ক: {u['total_tasks']}"
        await query.edit_message_text(msg, parse_mode="Markdown", 
                                     reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙", callback_data="back_home")]]))

# ==========================================
# 5. টাস্ক সাবমিশন (Conversation)
# ==========================================

async def start_task_submission(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if not is_working_hour():
        await query.edit_message_text("⛔ এখন কাজের সময় নয়।", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙", callback_data="back_home")]]))
        return ConversationHandler.END

    config = get_config()
    apps = config.get('monitored_apps', [])
    if not apps:
        await query.edit_message_text("❌ কোনো কাজ নেই।")
        return ConversationHandler.END
        
    btns = [[InlineKeyboardButton(f"📱 {a['name']}", callback_data=f"sel_{a['id']}")] for a in apps]
    btns.append([InlineKeyboardButton("❌ বাতিল", callback_data="cancel")])
    await query.edit_message_text("অ্যাপ সিলেক্ট করুন:", reply_markup=InlineKeyboardMarkup(btns))
    return T_APP_SELECT

async def app_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data['tid'] = query.data.split("sel_")[1]
    await query.edit_message_text("✍️ আপনার রিভিউ নাম (Review Name) দিন:")
    return T_REVIEW_NAME

async def get_review_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['rname'] = update.message.text
    await update.message.reply_text("আপনার ইমেইল দিন:")
    return T_EMAIL

async def get_email(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['email'] = update.message.text
    await update.message.reply_text("ডিভাইস নাম:")
    return T_DEVICE

async def get_device(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['dev'] = update.message.text
    await update.message.reply_text("স্ক্রিনশট দিন (Image or Link):")
    return T_SS

async def save_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    link = ""
    
    if update.message.photo:
        wait = await update.message.reply_text("📤 আপলোড হচ্ছে...")
        photo = await update.message.photo[-1].get_file()
        img_bytes = await photo.download_as_bytearray()
        
        # ImgBB Upload
        res = requests.post("https://api.imgbb.com/1/upload", 
                            data={'key': IMGBB_API_KEY}, 
                            files={'image': bytes(img_bytes)}).json()
        link = res['data']['url'] if res.get('success') else ""
        await wait.delete()
    else:
        link = update.message.text

    # Database Save
    task_ref = db.collection('tasks').add({
        "user_id": str(user.id), "review_name": context.user_data['rname'],
        "screenshot": link, "status": "pending", "submitted_at": datetime.now(),
        "price": get_config()['task_price']
    })
    
    await send_log_message(context, f"📝 New Task: {user.id}\nProof: {link}")
    await update.message.reply_text("✅ কাজ জমা হয়েছে!", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 হোম", callback_data="back_home")]]))
    return ConversationHandler.END

async def cancel_conv(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text("❌ বাতিল করা হয়েছে।")
    return ConversationHandler.END

# ==========================================
# 6. রানার এবং মেইন ফাংশন
# ==========================================

def run_flask():
    app.run(host='0.0.0.0', port=PORT)

def main():
    # Flask Thread
    threading.Thread(target=run_flask, daemon=True).start()
    
    # Telegram Application
    application = ApplicationBuilder().token(TOKEN).build()
    
    # Handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(common_callback, pattern="^(my_profile|refer_friend|back_home)$"))
    
    # Task Conv
    task_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(start_task_submission, pattern="^submit_task$")],
        states={
            T_APP_SELECT: [CallbackQueryHandler(app_selected, pattern="^sel_")],
            T_REVIEW_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_review_name)],
            T_EMAIL: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_email)],
            T_DEVICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_device)],
            T_SS: [MessageHandler((filters.TEXT | filters.PHOTO) & ~filters.COMMAND, save_task)]
        },
        fallbacks=[CallbackQueryHandler(cancel_conv, pattern="^cancel$")]
    )
    application.add_handler(task_conv)
    
    print("🚀 System Started with Python 3.13 Compatibility!")
    application.run_polling()

if __name__ == '__main__':
    main()

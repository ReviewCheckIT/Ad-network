import telebot
import firebase_admin
from firebase_admin import credentials, firestore
from datetime import datetime, time
import pytz
import requests
import json
import os
import random
import string

# --- Configuration ---
# Render এ Environment Variable এ সেট করবেন
BOT_TOKEN = os.environ.get('BOT_TOKEN', 'YOUR_BOT_TOKEN_HERE') 
FIREBASE_KEY = json.loads(os.environ.get('FIREBASE_JSON', '{}')) # Firebase JSON content
IMGBB_KEY = os.environ.get('IMGBB_KEY', 'YOUR_IMGBB_API_KEY')
ADMIN_ID = 'YOUR_ADMIN_TELEGRAM_ID'

# Firebase Init
if not firebase_admin._apps:
    cred = credentials.Certificate(FIREBASE_KEY)
    firebase_admin.initialize_app(cred)
db = firestore.client()

bot = telebot.TeleBot(BOT_TOKEN)
BD_TIMEZONE = pytz.timezone('Asia/Dhaka')

# --- Helper Functions ---
def get_user(uid):
    doc = db.collection('users').document(str(uid)).get()
    return doc.to_dict() if doc.exists else None

def generate_password(length=8):
    chars = string.ascii_letters + string.digits
    return ''.join(random.choice(chars) for i in range(length))

def is_submission_time():
    now = datetime.now(BD_TIMEZONE).time()
    # 9 PM to 11 PM BD Time
    start = time(21, 0) 
    end = time(23, 0)
    # Admin can override via DB, but hardcoded for safety based on request
    return start <= now <= end

# --- Bot Commands ---

@bot.message_handler(commands=['start'])
def send_welcome(message):
    uid = str(message.from_user.id)
    user = get_user(uid)
    
    if not user:
        # Registration Flow
        msg = bot.reply_to(message, "স্বাগতম! রেজিস্ট্রেশন করতে আপনার ইমেইল দিন:")
        bot.register_next_step_handler(msg, process_email_reg)
    else:
        main_menu(message)

def process_email_reg(message):
    email = message.text
    uid = str(message.from_user.id)
    name = message.from_user.full_name
    password = generate_password()
    
    # Save to Firebase
    data = {
        'uid': uid,
        'name': name,
        'email': email,
        'password': password,
        'balance': 0.0,
        'pending_balance': 0.0,
        'tasks_approved': 0,
        'tasks_rejected': 0,
        'referrals': 0,
        'joined_at': datetime.now(BD_TIMEZONE)
    }
    db.collection('users').document(uid).set(data)
    
    bot.send_message(message.chat.id, f"রেজিস্ট্রেশন সফল!\n\nUser ID: {uid}\nPassword: `{password}`\n(এই পাসওয়ার্ড দিয়ে অ্যাপ/সাইটে লগইন করুন)", parse_mode='Markdown')
    main_menu(message)

def main_menu(message):
    markup = telebot.types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
    btn1 = telebot.types.KeyboardButton('👤 প্রোফাইল')
    btn2 = telebot.types.KeyboardButton('💰 কাজ জমা দিন')
    btn3 = telebot.types.KeyboardButton('📺 Tasks')
    btn4 = telebot.types.KeyboardButton('💸 উইথড্র')
    btn5 = telebot.types.KeyboardButton('🔄 রিফ্রেশ')
    btn6 = telebot.types.KeyboardButton('⭐ রিভিউ টাস্ক')
    markup.add(btn1, btn2, btn3, btn4, btn5, btn6)
    bot.send_message(message.chat.id, "প্রধান মেনু:", reply_markup=markup)

@bot.message_handler(func=lambda message: message.text == '👤 প্রোফাইল')
def profile(message):
    user = get_user(message.from_user.id)
    if user:
        text = f"""
👤 **আমার প্রোফাইল**
নাম: {user.get('name')}
UID: `{user.get('uid')}`
পাসওয়ার্ড: ||{user.get('password')}|| (Click to see)
💰 ব্যালেন্স: {user.get('balance')} BDT
⏳ পেন্ডিং: {user.get('pending_balance')} BDT
✅ এপ্রুভ কাজ: {user.get('tasks_approved')}
❌ রিজেক্ট কাজ: {user.get('tasks_rejected')}
👥 রেফার: {user.get('referrals')}
        """
        bot.send_message(message.chat.id, text, parse_mode='MarkdownV2')

@bot.message_handler(func=lambda message: message.text == '⭐ রিভিউ টাস্ক')
def review_tasks(message):
    # Fetch active tasks from Firebase
    tasks_ref = db.collection('tasks').where('type', '==', 'review').stream()
    
    markup = telebot.types.InlineKeyboardMarkup()
    count = 0
    for task in tasks_ref:
        t_data = task.to_dict()
        btn = telebot.types.InlineKeyboardButton(f"{t_data.get('app_name')} - {t_data.get('rate')} BDT", callback_data=f"task_{task.id}")
        markup.add(btn)
        count += 1
    
    if count == 0:
        bot.send_message(message.chat.id, "বর্তমানে কোনো রিভিউ কাজ নেই।")
    else:
        bot.send_message(message.chat.id, "নিচের অ্যাপগুলো রিভিউ দিন:", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith('task_'))
def task_details(call):
    task_id = call.data.split('_')[1]
    task_doc = db.collection('tasks').document(task_id).get()
    
    if task_doc.exists:
        data = task_doc.to_dict()
        text = f"""
📱 **অ্যাপ:** {data.get('app_name')}
⭐ **রেটিং:** 5 Star
📝 **রিভিউ:** {data.get('instructions')}
💰 **পেমেন্ট:** {data.get('rate')} BDT

⚠️ **নির্দেশনা:** অ্যাপটি ইনস্টল করুন, ব্যবহার করুন এবং প্লে স্টোরে পজিটিভ রিভিউ দিন। স্ক্রিনশট নিয়ে রাখুন। 'কাজ জমা দিন' অপশন থেকে জমা দিন।
        """
        # Image sending logic (if available in data) can be added here
        bot.send_message(call.message.chat.id, text)

@bot.message_handler(func=lambda message: message.text == '💰 কাজ জমা দিন')
def submit_work_start(message):
    if not is_submission_time():
        bot.reply_to(message, "⚠️ দুঃখিত! কাজ জমা দেওয়ার সময়: প্রতিদিন রাত ৯টা থেকে ১১টা পর্যন্ত।")
        return
    
    msg = bot.reply_to(message, "আপনি কোন অ্যাপের রিভিউ জমা দিতে চান? (অ্যাপের নাম লিখুন):")
    bot.register_next_step_handler(msg, process_submission_appname)

def process_submission_appname(message):
    # This stores temp data for the user flow
    bot.reply_to(message, "আপনার প্লে স্টোর রিভিউ প্রোফাইল নাম (Review Name) দিন:")
    bot.register_next_step_handler(message, process_submission_name, {"app": message.text})

def process_submission_name(message, data):
    data['review_name'] = message.text
    bot.reply_to(message, "আপনার ইমেইল এড্রেস দিন:")
    bot.register_next_step_handler(message, process_submission_email, data)

def process_submission_email(message, data):
    data['email'] = message.text
    bot.reply_to(message, "ডিভাইস মডেল নাম দিন:")
    bot.register_next_step_handler(message, process_submission_device, data)

def process_submission_device(message, data):
    data['device'] = message.text
    bot.reply_to(message, "এখন স্ক্রিনশট আপলোড করুন (ছবি পাঠান):")
    bot.register_next_step_handler(message, process_submission_image, data)

def process_submission_image(message, data):
    if message.content_type == 'photo':
        file_info = bot.get_file(message.photo[-1].file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        
        # Upload to ImgBB
        url = "https://api.imgbb.com/1/upload"
        payload = {
            "key": IMGBB_KEY,
            "image": base64.b64encode(downloaded_file),
        }
        # Note: Need `import base64` at top
        import base64
        response = requests.post(url, data=payload)
        img_url = response.json().get('data', {}).get('url')
        
        data['screenshot'] = img_url
        data['uid'] = str(message.from_user.id)
        data['status'] = 'pending'
        data['timestamp'] = datetime.now(BD_TIMEZONE)
        
        # Save submission to Firebase
        db.collection('submissions').add(data)
        
        bot.reply_to(message, "✅ আপনার কাজ জমা নেওয়া হয়েছে! ২৪ ঘন্টার মধ্যে যাচাই করা হবে।")
    else:
        bot.reply_to(message, "দয়া করে ছবি পাঠান।")

# --- Play Store Verification Logic (Automated Placeholder) ---
# Note: Google does not provide a free API to check reviews by username.
# This function simulates the structure requested. Real implementation requires complex scraping.
def check_playstore_reviews():
    # This function would run periodically via a scheduler
    pending_subs = db.collection('submissions').where('status', '==', 'pending').stream()
    
    for sub in pending_subs:
        data = sub.to_dict()
        sub_time = data['timestamp']
        # Check if 24 hours passed
        # If passed and not approved -> Reject
        # Logic to scrape play store using data['app'] and data['review_name']
        # If match found -> db.collection('users').doc(uid).update(balance increment)
        pass 

# --- Admin Export (Simplified) ---
@bot.message_handler(commands=['export'])
def export_data(message):
    if str(message.from_user.id) != ADMIN_ID: return
    # Logic to fetch submissions and create a CSV/Text file
    bot.reply_to(message, "Generating report...")

# --- Polling ---
bot.infinity_polling()

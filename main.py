import os
import sqlite3
import requests
import io
from datetime import datetime, timedelta
import threading
import time
import telebot
from telebot import types
from urllib.parse import quote_plus

TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
ADMIN_ID = int(os.getenv('ADMIN_ID', '7617397626'))
DB_PATH = os.getenv('DATABASE_PATH', 'database.db')
FREE_DAILY_LIMIT = 5

bot = telebot.TeleBot(TELEGRAM_TOKEN, parse_mode=None)

conn = sqlite3.connect(DB_PATH, check_same_thread=False)
conn.row_factory = sqlite3.Row
cur = conn.cursor()

cur.execute('CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, used_today INTEGER DEFAULT 0, last_date TEXT, is_premium INTEGER DEFAULT 0, premium_expiry TEXT)')
cur.execute('CREATE TABLE IF NOT EXISTS channels (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE)')
cur.execute('CREATE TABLE IF NOT EXISTS stats (day TEXT PRIMARY KEY, images_generated INTEGER DEFAULT 0)')
cur.execute('CREATE TABLE IF NOT EXISTS promo (code TEXT PRIMARY KEY, active INTEGER DEFAULT 1)')
conn.commit()

lock = threading.Lock()

def get_today_str():
    return datetime.utcnow().strftime('%Y-%m-%d')

def ensure_user(user_id: int):
    with lock:
        cur.execute('SELECT * FROM users WHERE user_id=?', (user_id,))
        row = cur.fetchone()
        if not row:
            cur.execute('INSERT INTO users(user_id, used_today, last_date, is_premium, premium_expiry) VALUES(?,?,?,?,?)', (user_id, 0, get_today_str(), 0, None))
            conn.commit()
            return {'user_id': user_id, 'used_today': 0, 'last_date': get_today_str(), 'is_premium': 0, 'premium_expiry': None}
        if row['last_date'] != get_today_str():
            cur.execute('UPDATE users SET used_today=?, last_date=? WHERE user_id=?', (0, get_today_str(), user_id))
            conn.commit()
            row = dict(row)
            row['used_today'] = 0
            row['last_date'] = get_today_str()
        return dict(row)

def increment_usage(user_id: int):
    with lock:
        cur.execute('UPDATE users SET used_today = used_today + 1 WHERE user_id=?', (user_id,))
        conn.commit()
        day = get_today_str()
        cur.execute('SELECT * FROM stats WHERE day=?', (day,))
        s = cur.fetchone()
        if not s:
            cur.execute('INSERT INTO stats(day, images_generated) VALUES(?,?)', (day, 1))
        else:
            cur.execute('UPDATE stats SET images_generated = images_generated + 1 WHERE day=?', (day,))
        conn.commit()

def set_premium(user_id: int, months=1):
    expiry = datetime.utcnow() + timedelta(days=30*months)
    with lock:
        ensure_user(user_id)
        cur.execute('UPDATE users SET is_premium=1, premium_expiry=?, last_date=? WHERE user_id=?', (expiry.isoformat(), get_today_str(), user_id))
        conn.commit()

def unset_premium(user_id: int):
    with lock:
        cur.execute('UPDATE users SET is_premium=0, premium_expiry=NULL WHERE user_id=?', (user_id,))
        conn.commit()

def check_premium_active(user):
    if user['is_premium']:
        if user['premium_expiry']:
            exp = datetime.fromisoformat(user['premium_expiry'])
            if exp > datetime.utcnow():
                return True
            else:
                unset_premium(user['user_id'])
                return False
        return False
    return False

def generate_image_bytes(prompt: str) -> bytes:
    url = 'https://image.pollinations.ai/prompt/' + quote_plus(prompt)
    r = requests.get(url, timeout=60)
    r.raise_for_status()
    return r.content

@bot.message_handler(commands=['start'])
def cmd_start(m):
    ensure_user(m.from_user.id)
    with lock:
        cur.execute('SELECT username FROM channels')
        rows = cur.fetchall()
        channels = [r['username'] for r in rows]
    if channels:
        kb = types.InlineKeyboardMarkup()
        for ch in channels:
            kb.add(types.InlineKeyboardButton(ch, url=f'https://t.me/{ch.replace("@", "")}'))
        kb.add(types.InlineKeyboardButton("✅ Tasdiqlash", callback_data="check_sub"))
        bot.reply_to(m, "Iltimos, quyidagi kanallarga obuna bo‘ling:", reply_markup=kb)
    else:
        txt = (f"Salom! Men AI botman.\n"
               "Matn yuboring — men rasm yoki matn yarataman.\n"
               f"Kuniga {FREE_DAILY_LIMIT} ta bepul rasm.\n"
               "Premium uchun promo koddan foydalaning.")
        bot.reply_to(m, txt)

@bot.callback_query_handler(func=lambda call: call.data == "check_sub")
def check_subscription(call):
    with lock:
        cur.execute('SELECT username FROM channels')
        rows = cur.fetchall()
        channels = [r['username'] for r in rows]
    for ch in channels:
        try:
            member = bot.get_chat_member(ch, call.from_user.id)
            if member.status in ('left', 'kicked'):
                bot.answer_callback_query(call.id, "Hali barcha kanallarga obuna bo‘lmadingiz.")
                return
        except:
            bot.answer_callback_query(call.id, "Tekshiruvda xato, keyinroq urinib ko‘ring.")
            return
    bot.send_message(call.from_user.id, "Tasdiqlandi! Endi botdan foydalanishingiz mumkin.")

@bot.message_handler(commands=['admin'])
def cmd_admin(m):
    if m.from_user.id != ADMIN_ID:
        bot.reply_to(m, "Faqat admin uchun.")
        return
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("📈 Statistika", "📢 Reklama")
    kb.add("➕ Kanal qo‘shish", "➖ Kanal o‘chirish", "📜 Kanal ro‘yxati")
    kb.add("🎁 Promo kod yaratish")
    bot.send_message(m.chat.id, "Admin panel:", reply_markup=kb)

@bot.message_handler(func=lambda m: m.text == "📈 Statistika")
def stat_admin(m):
    if m.from_user.id != ADMIN_ID:
        return
    with lock:
        cur.execute('SELECT COUNT(*) as c FROM users')
        users = cur.fetchone()['c']
        cur.execute('SELECT COUNT(*) as c FROM users WHERE is_premium=1')
        premiums = cur.fetchone()['c']
        cur.execute('SELECT images_generated FROM stats WHERE day=?', (get_today_str(),))
        s = cur.fetchone()
        images = s['images_generated'] if s else 0
    bot.send_message(m.chat.id, f"Foydalanuvchilar: {users}\nPremium: {premiums}\nBugun rasmlar: {images}")

@bot.message_handler(func=lambda m: m.text == "➕ Kanal qo‘shish")
def add_ch(m):
    if m.from_user.id != ADMIN_ID:
        return
    msg = bot.send_message(m.chat.id, "Kanal username-ni yuboring (masalan, @kanal)")
    bot.register_next_step_handler(msg, save_channel)

def save_channel(m):
    username = m.text.strip()
    with lock:
        try:
            cur.execute('INSERT INTO channels(username) VALUES(?)', (username,))
            conn.commit()
            bot.send_message(m.chat.id, f"Kanal qo‘shildi: {username}")
        except:
            bot.send_message(m.chat.id, "Bu kanal allaqachon mavjud.")

@bot.message_handler(func=lambda m: m.text == "➖ Kanal o‘chirish")
def del_ch(m):
    if m.from_user.id != ADMIN_ID:
        return
    msg = bot.send_message(m.chat.id, "O‘chiriladigan kanalni yuboring (@kanal)")
    bot.register_next_step_handler(msg, remove_channel)

def remove_channel(m):
    username = m.text.strip()
    with lock:
        cur.execute('DELETE FROM channels WHERE username=?', (username,))
        conn.commit()
        bot.send_message(m.chat.id, f"{username} o‘chirildi.")

@bot.message_handler(func=lambda m: m.text == "📜 Kanal ro‘yxati")
def list_ch(m):
    if m.from_user.id != ADMIN_ID:
        return
    with lock:
        cur.execute('SELECT username FROM channels')
        rows = cur.fetchall()
    if not rows:
        bot.send_message(m.chat.id, "Hech qanday kanal yo‘q.")
    else:
        bot.send_message(m.chat.id, "\n".join([r['username'] for r in rows]))

@bot.message_handler(func=lambda m: m.text == "🎁 Promo kod yaratish")
def promo_create(m):
    if m.from_user.id != ADMIN_ID:
        return
    code = str(int(time.time()))
    with lock:
        cur.execute('INSERT INTO promo(code, active) VALUES(?, ?)', (code, 1))
        conn.commit()
    bot.send_message(m.chat.id, f"Promo kod: {code}")

@bot.message_handler(commands=['premium'])
def premium_cmd(m):
    msg = bot.send_message(m.chat.id, "Promo kodni kiriting:")
    bot.register_next_step_handler(msg, check_promo)

def check_promo(m):
    code = m.text.strip()
    with lock:
        cur.execute('SELECT * FROM promo WHERE code=? AND active=1', (code,))
        row = cur.fetchone()
    if not row:
        bot.send_message(m.chat.id, "Noto‘g‘ri yoki ishlatilgan promo kod.")
        return
    set_premium(m.from_user.id)
    with lock:
        cur.execute('UPDATE promo SET active=0 WHERE code=?', (code,))
        conn.commit()
    bot.send_message(m.chat.id, "Premium faollashtirildi!")

@bot.message_handler(func=lambda m: True)
def handle_message(m):
    user_id = m.from_user.id
    prompt = m.text.strip()
    if not prompt:
        return
    user = ensure_user(user_id)
    premium = check_premium_active(user)
    if m.from_user.id == ADMIN_ID:
        premium = True
    with lock:
        cur.execute('SELECT username FROM channels')
        rows = cur.fetchall()
        channels = [r['username'] for r in rows]
    for ch in channels:
        try:
            member = bot.get_chat_member(ch, user_id)
            if member.status in ('left', 'kicked'):
                bot.reply_to(m, "Iltimos, quyidagi kanallarga obuna bo‘ling:\n" + "\n".join(channels))
                return
        except:
            bot.reply_to(m, "Iltimos, quyidagi kanallarga obuna bo‘ling:\n" + "\n".join(channels))
            return
    if not premium and user['used_today'] >= FREE_DAILY_LIMIT:
        bot.reply_to(m, f"Kunlik {FREE_DAILY_LIMIT} ta limit tugadi. Promo kod orqali premium oling.")
        return
    try:
        bot.send_chat_action(user_id, 'upload_photo')
        img = generate_image_bytes(prompt)
        bio = io.BytesIO(img)
        bio.name = 'ai.jpg'
        bio.seek(0)
        bot.send_photo(user_id, bio, caption=f"AI natija: {prompt}")
        increment_usage(user_id)
    except:
        bot.reply_to(m, "Rasm yaratishda xato yuz berdi.")

def premium_cleaner():
    while True:
        with lock:
            cur.execute('SELECT user_id, premium_expiry FROM users WHERE is_premium=1')
            rows = cur.fetchall()
            for r in rows:
                if r['premium_expiry']:
                    try:
                        exp = datetime.fromisoformat(r['premium_expiry'])
                        if exp <= datetime.utcnow():
                            unset_premium(r['user_id'])
                    except:
                        pass
        time.sleep(3600)

if __name__ == '__main__':
    threading.Thread(target=premium_cleaner, daemon=True).start()
    print('Bot started')
    bot.infinity_polling(skip_pending=True)

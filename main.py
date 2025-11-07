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
FREE_DAILY_LIMIT = int(os.getenv('FREE_DAILY_LIMIT', '3'))

if not TELEGRAM_TOKEN:
    raise RuntimeError('TELEGRAM_TOKEN not set in environment')

bot = telebot.TeleBot(TELEGRAM_TOKEN, parse_mode=None)

conn = sqlite3.connect(DB_PATH, check_same_thread=False)
conn.row_factory = sqlite3.Row
cur = conn.cursor()

cur.execute('''
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    used_today INTEGER DEFAULT 0,
    last_date TEXT,
    is_premium INTEGER DEFAULT 0,
    premium_expiry TEXT
)
''')

cur.execute('''
CREATE TABLE IF NOT EXISTS channels (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE
)
''')

cur.execute('''
CREATE TABLE IF NOT EXISTS stats (
    day TEXT PRIMARY KEY,
    images_generated INTEGER DEFAULT 0
)
''')
conn.commit()

lock = threading.Lock()

def get_today_str():
    return datetime.utcnow().strftime('%Y-%m-%d')

def ensure_user(user_id: int):
    with lock:
        cur.execute('SELECT * FROM users WHERE user_id=?', (user_id,))
        row = cur.fetchone()
        if not row:
            cur.execute('INSERT INTO users(user_id, used_today, last_date, is_premium, premium_expiry) VALUES(?,?,?,?,?)',
                        (user_id, 0, get_today_str(), 0, None))
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
        cur.execute('UPDATE users SET is_premium=1, premium_expiry=?, last_date=? WHERE user_id=?',
                    (expiry.isoformat(), get_today_str(), user_id))
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

POLLINATIONS_BASE = 'https://image.pollinations.ai/prompt/'

def generate_image_bytes(prompt: str) -> bytes:
    url = POLLINATIONS_BASE + quote_plus(prompt)
    r = requests.get(url, timeout=60)
    r.raise_for_status()
    return r.content

@bot.message_handler(commands=['start'])
def cmd_start(m):
    ensure_user(m.from_user.id)
    txt = ("Salom! Men AI rasm botman.\n"
           "Matn yuboring — men sun’iy intellekt yordamida rasm yarataman.\n"
           f"Kuniga {FREE_DAILY_LIMIT} ta bepul rasm.\n"
           "Premium uchun adminga murojaat qiling.")
    bot.reply_to(m, txt)

@bot.message_handler(commands=['stat'])
def cmd_stat(m):
    if m.from_user.id != ADMIN_ID:
        bot.reply_to(m, "Faqat admin uchun.")
        return
    with lock:
        cur.execute('SELECT COUNT(*) as c FROM users')
        users_count = cur.fetchone()['c']
        cur.execute('SELECT COUNT(*) as c FROM users WHERE is_premium=1')
        premium_count = cur.fetchone()['c']
        today = get_today_str()
        cur.execute('SELECT images_generated FROM stats WHERE day=?', (today,))
        row = cur.fetchone()
        today_images = row['images_generated'] if row else 0
    msg = (f"Foydalanuvchilar: {users_count}\n"
           f"Premiumlar: {premium_count}\n"
           f"Bugun yaratilgan rasmlar: {today_images}")
    bot.reply_to(m, msg)

@bot.message_handler(commands=['addchannel'])
def cmd_addchannel(m):
    if m.from_user.id != ADMIN_ID:
        bot.reply_to(m, "Faqat admin uchun.")
        return
    args = m.text.split()
    if len(args) < 2:
        bot.reply_to(m, "Foydalanish: /addchannel @kanalusername")
        return
    username = args[1].strip()
    with lock:
        try:
            cur.execute('INSERT INTO channels(username) VALUES(?)', (username,))
            conn.commit()
            bot.reply_to(m, f"Kanal qo‘shildi: {username}")
        except sqlite3.IntegrityError:
            bot.reply_to(m, "Bu kanal allaqachon ro‘yxatda.")

@bot.message_handler(commands=['delchannel'])
def cmd_delchannel(m):
    if m.from_user.id != ADMIN_ID:
        bot.reply_to(m, "Faqat admin uchun.")
        return
    args = m.text.split()
    if len(args) < 2:
        bot.reply_to(m, "Foydalanish: /delchannel @kanalusername")
        return
    username = args[1].strip()
    with lock:
        cur.execute('DELETE FROM channels WHERE username=?', (username,))
        conn.commit()
        bot.reply_to(m, f"Kanal o‘chirildi: {username}")

@bot.message_handler(commands=['channellist'])
def cmd_channellist(m):
    if m.from_user.id != ADMIN_ID:
        bot.reply_to(m, "Faqat admin uchun.")
        return
    with lock:
        cur.execute('SELECT username FROM channels')
        rows = cur.fetchall()
        channels = [r['username'] for r in rows]
    if not channels:
        bot.reply_to(m, "Hech qanday majburiy kanal yo‘q.")
    else:
        bot.reply_to(m, "Majburiy kanallar:\n" + "\n".join(channels))

@bot.message_handler(commands=['premium'])
def cmd_premium(m):
    if m.from_user.id != ADMIN_ID:
        bot.reply_to(m, "Faqat admin uchun.")
        return
    args = m.text.split()
    if len(args) < 2:
        bot.reply_to(m, "Foydalanish: /premium <user_id>")
        return
    try:
        uid = int(args[1])
    except ValueError:
        bot.reply_to(m, "Noto‘g‘ri user_id.")
        return
    with lock:
        cur.execute('SELECT is_premium FROM users WHERE user_id=?', (uid,))
        r = cur.fetchone()
        if r and r['is_premium']:
            unset_premium(uid)
            bot.reply_to(m, f"{uid} dan premium olib tashlandi.")
        else:
            set_premium(uid, months=1)
            bot.reply_to(m, f"{uid} ga 1 oy premium berildi.")

@bot.message_handler(commands=['reklama'])
def cmd_reklama(m):
    if m.from_user.id != ADMIN_ID:
        bot.reply_to(m, "Faqat admin uchun.")
        return
    text = m.text.partition(' ')[2]
    if not text:
        bot.reply_to(m, "Foydalanish: /reklama <matn>")
        return
    with lock:
        cur.execute('SELECT user_id FROM users')
        rows = cur.fetchall()
        user_ids = [r['user_id'] for r in rows]
    sent = 0
    for uid in user_ids:
        try:
            bot.send_message(uid, text)
            sent += 1
        except Exception:
            pass
    bot.reply_to(m, f"Reklama yuborildi: {sent} foydalanuvchiga.")

@bot.message_handler(func=lambda m: True)
def handle_message(m):
    user_id = m.from_user.id
    prompt = m.text.strip()
    if not prompt:
        bot.reply_to(m, "Iltimos, rasm uchun matn yuboring.")
        return
    user = ensure_user(user_id)
    premium = check_premium_active(user)
    with lock:
        cur.execute('SELECT username FROM channels')
        rows = cur.fetchall()
        channels = [r['username'] for r in rows]
    if channels:
        for ch in channels:
            try:
                member = bot.get_chat_member(ch, user_id)
                if member.status in ('left', 'kicked'):
                    bot.reply_to(m, "Iltimos, quyidagi kanallarga a‘zo bo‘ling:\n" + "\n".join(channels))
                    return
            except Exception:
                bot.reply_to(m, "Iltimos, quyidagi kanallarga a‘zo bo‘ling:\n" + "\n".join(channels))
                return
    if not premium and user['used_today'] >= FREE_DAILY_LIMIT:
        bot.reply_to(m, f"Sizning kunlik {FREE_DAILY_LIMIT} ta rasm limiti tugadi.\nPremium uchun adminga yozing.")
        return
    try:
        bot.send_chat_action(user_id, 'upload_photo')
        img_bytes = generate_image_bytes(prompt)
        bio = io.BytesIO(img_bytes)
        bio.name = 'image.jpg'
        bio.seek(0)
        bot.send_photo(user_id, photo=bio, caption=f'Prompt: {prompt}')
        increment_usage(user_id)
    except Exception:
        bot.reply_to(m, "Rasm yaratishda xato yuz berdi.")

def premium_cleaner():
    while True:
        with lock:
            cur.execute('SELECT user_id, premium_expiry FROM users WHERE is_premium=1')
            rows = cur.fetchall()
            for r in rows:
                try:
                    if r['premium_expiry']:
                        exp = datetime.fromisoformat(r['premium_expiry'])
                        if exp <= datetime.utcnow():
                            unset_premium(r['user_id'])
                except Exception:
                    pass
        time.sleep(3600)

if __name__ == '__main__':
    t = threading.Thread(target=premium_cleaner, daemon=True)
    t.start()
    print('Bot started')
    bot.infinity_polling()

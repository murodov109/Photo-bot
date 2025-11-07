import os
import sqlite3
import requests
import io
import threading
import time
from datetime import datetime, timedelta
from telebot import TeleBot, types
from urllib.parse import quote_plus

TOKEN = os.getenv("TELEGRAM_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "7617397626"))
DB_PATH = os.getenv("DATABASE_PATH", "database.db")
FREE_DAILY_LIMIT = int(os.getenv("FREE_DAILY_LIMIT", "3"))

bot = TeleBot(TOKEN)

conn = sqlite3.connect(DB_PATH, check_same_thread=False)
cur = conn.cursor()

cur.execute("""
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    used_today INTEGER DEFAULT 0,
    last_date TEXT,
    is_premium INTEGER DEFAULT 0,
    premium_expiry TEXT
)
""")

cur.execute("""
CREATE TABLE IF NOT EXISTS channels (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE
)
""")

cur.execute("""
CREATE TABLE IF NOT EXISTS stats (
    day TEXT PRIMARY KEY,
    images_generated INTEGER DEFAULT 0
)
""")
conn.commit()


def get_today():
    return datetime.utcnow().strftime("%Y-%m-%d")


def ensure_user(user_id):
    cur.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
    row = cur.fetchone()
    if not row:
        cur.execute("INSERT INTO users(user_id, used_today, last_date) VALUES(?,?,?)",
                    (user_id, 0, get_today()))
        conn.commit()
        return {"user_id": user_id, "used_today": 0, "last_date": get_today(), "is_premium": 0, "premium_expiry": None}
    last_date = row[2]
    if last_date != get_today():
        cur.execute("UPDATE users SET used_today=0, last_date=? WHERE user_id=?", (get_today(), user_id))
        conn.commit()
    cur.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
    row = cur.fetchone()
    return {
        "user_id": row[0],
        "used_today": row[1],
        "last_date": row[2],
        "is_premium": row[3],
        "premium_expiry": row[4]
    }


def increment_usage(user_id):
    cur.execute("UPDATE users SET used_today = used_today + 1 WHERE user_id=?", (user_id,))
    conn.commit()
    day = get_today()
    cur.execute("SELECT * FROM stats WHERE day=?", (day,))
    s = cur.fetchone()
    if not s:
        cur.execute("INSERT INTO stats(day, images_generated) VALUES(?,?)", (day, 1))
    else:
        cur.execute("UPDATE stats SET images_generated = images_generated + 1 WHERE day=?", (day,))
    conn.commit()


def set_premium(user_id):
    expiry = (datetime.utcnow() + timedelta(days=30)).isoformat()
    cur.execute("UPDATE users SET is_premium=1, premium_expiry=? WHERE user_id=?", (expiry, user_id))
    conn.commit()


def unset_premium(user_id):
    cur.execute("UPDATE users SET is_premium=0, premium_expiry=NULL WHERE user_id=?", (user_id,))
    conn.commit()


def check_premium(user):
    if user["is_premium"] == 1 and user["premium_expiry"]:
        exp = datetime.fromisoformat(user["premium_expiry"])
        if exp > datetime.utcnow():
            return True
        else:
            unset_premium(user["user_id"])
            return False
    return False


def generate_image(prompt):
    url = f"https://image.pollinations.ai/prompt/{quote_plus(prompt)}"
    r = requests.get(url, timeout=60)
    r.raise_for_status()
    return r.content


@bot.message_handler(commands=['start'])
def start(msg):
    ensure_user(msg.from_user.id)
    bot.reply_to(msg, "👋 Salom! Men AI rasm yaratadigan botman.\n"
                      "Matn yuboring — men siz uchun rasm chizaman.\n\n"
                      f"Kunlik limit: {FREE_DAILY_LIMIT} ta.\n"
                      "Premium uchun admin bilan bog‘laning.")


@bot.message_handler(commands=['stat'])
def stat(msg):
    if msg.from_user.id != ADMIN_ID:
        bot.reply_to(msg, "Faqat admin uchun.")
        return
    cur.execute("SELECT COUNT(*) FROM users")
    users = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM users WHERE is_premium=1")
    premiums = cur.fetchone()[0]
    today = get_today()
    cur.execute("SELECT images_generated FROM stats WHERE day=?", (today,))
    r = cur.fetchone()
    images = r[0] if r else 0
    bot.reply_to(msg, f"👥 Foydalanuvchilar: {users}\n💎 Premiumlar: {premiums}\n🖼 Bugun yaratilgan rasm: {images}")


@bot.message_handler(commands=['addchannel'])
def add_channel(msg):
    if msg.from_user.id != ADMIN_ID:
        bot.reply_to(msg, "Faqat admin uchun.")
        return
    parts = msg.text.split()
    if len(parts) < 2:
        bot.reply_to(msg, "Foydalanish: /addchannel @kanalusername")
        return
    ch = parts[1]
    try:
        cur.execute("INSERT INTO channels(username) VALUES(?)", (ch,))
        conn.commit()
        bot.reply_to(msg, f"Kanal qo‘shildi: {ch}")
    except sqlite3.IntegrityError:
        bot.reply_to(msg, "Bu kanal allaqachon mavjud.")


@bot.message_handler(commands=['delchannel'])
def del_channel(msg):
    if msg.from_user.id != ADMIN_ID:
        bot.reply_to(msg, "Faqat admin uchun.")
        return
    parts = msg.text.split()
    if len(parts) < 2:
        bot.reply_to(msg, "Foydalanish: /delchannel @kanalusername")
        return
    ch = parts[1]
    cur.execute("DELETE FROM channels WHERE username=?", (ch,))
    conn.commit()
    bot.reply_to(msg, f"Kanal o‘chirildi: {ch}")


@bot.message_handler(commands=['channellist'])
def channellist(msg):
    if msg.from_user.id != ADMIN_ID:
        bot.reply_to(msg, "Faqat admin uchun.")
        return
    cur.execute("SELECT username FROM channels")
    rows = cur.fetchall()
    if not rows:
        bot.reply_to(msg, "Hech qanday kanal qo‘shilmagan.")
        return
    txt = "\n".join([r[0] for r in rows])
    bot.reply_to(msg, f"📢 Majburiy kanallar:\n{txt}")


@bot.message_handler(commands=['premium'])
def premium(msg):
    if msg.from_user.id != ADMIN_ID:
        bot.reply_to(msg, "Faqat admin uchun.")
        return
    args = msg.text.split()
    if len(args) < 2:
        bot.reply_to(msg, "Foydalanish: /premium <user_id>")
        return
    uid = int(args[1])
    cur.execute("SELECT is_premium FROM users WHERE user_id=?", (uid,))
    r = cur.fetchone()
    if r and r[0] == 1:
        unset_premium(uid)
        bot.reply_to(msg, f"{uid} foydalanuvchidan premium olib tashlandi.")
    else:
        set_premium(uid)
        bot.reply_to(msg, f"{uid} foydalanuvchiga 1 oy premium berildi.")


@bot.message_handler(commands=['reklama'])
def reklama(msg):
    if msg.from_user.id != ADMIN_ID:
        bot.reply_to(msg, "Faqat admin uchun.")
        return
    text = msg.text.partition(" ")[2]
    if not text:
        bot.reply_to(msg, "Foydalanish: /reklama <matn>")
        return
    cur.execute("SELECT user_id FROM users")
    users = [r[0] for r in cur.fetchall()]
    sent = 0
    for u in users:
        try:
            bot.send_message(u, text)
            sent += 1
        except:
            pass
    bot.reply_to(msg, f"Reklama yuborildi: {sent} ta foydalanuvchiga.")


@bot.message_handler(func=lambda msg: True)
def ai_generate(msg):
    user_id = msg.from_user.id
    prompt = msg.text.strip()
    user = ensure_user(user_id)
    premium = check_premium(user)

    cur.execute("SELECT username FROM channels")
    channels = [r[0] for r in cur.fetchall()]
    if channels:
        for ch in channels:
            try:
                member = bot.get_chat_member(ch, user_id)
                if member.status in ("left", "kicked"):
                    bot.reply_to(msg, "Iltimos, quyidagi kanallarga a'zo bo‘ling:\n" + "\n".join(channels))
                    return
            except:
                bot.reply_to(msg, "Iltimos, quyidagi kanallarga a'zo bo‘ling:\n" + "\n".join(channels))
                return

    if not premium and user["used_today"] >= FREE_DAILY_LIMIT:
        bot.reply_to(msg, f"Kunlik {FREE_DAILY_LIMIT} ta limit tugagan. Premium olish uchun admin bilan bog‘laning.")
        return

    bot.send_chat_action(user_id, "upload_photo")

    try:
        img = generate_image(prompt)
        photo = io.BytesIO(img)
        photo.name = "ai_image.jpg"
        photo.seek(0)
        bot.send_photo(user_id, photo=photo, caption=f"🖼 {prompt}")
        increment_usage(user_id)
    except:
        bot.reply_to(msg, "Rasm yaratishda xato yuz berdi.")


def premium_checker():
    while True:
        cur.execute("SELECT user_id, premium_expiry FROM users WHERE is_premium=1")
        rows = cur.fetchall()
        for r in rows:
            if r[1]:
                exp = datetime.fromisoformat(r[1])
                if exp <= datetime.utcnow():
                    unset_premium(r[0])
        time.sleep(3600)


if __name__ == "__main__":
    threading.Thread(target=premium_checker, daemon=True).start()
    print("✅ Bot ishga tushdi!")
    bot.infinity_polling()

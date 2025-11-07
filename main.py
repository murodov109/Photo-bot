import os, sqlite3, requests, io, threading, time
from datetime import datetime, timedelta, UTC
import telebot
from telebot import types
from urllib.parse import quote_plus

TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
ADMIN_ID = int(os.getenv('ADMIN_ID', '7617397626'))
DB_PATH = os.getenv('DATABASE_PATH', 'database.db')
FREE_DAILY_LIMIT = int(os.getenv('FREE_DAILY_LIMIT', '3'))
if not TELEGRAM_TOKEN:
    raise RuntimeError('TELEGRAM_TOKEN not set in environment')

bot = telebot.TeleBot(TELEGRAM_TOKEN)
conn = sqlite3.connect(DB_PATH, check_same_thread=False)
conn.row_factory = sqlite3.Row
cur = conn.cursor()
cur.execute('CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, used_today INTEGER DEFAULT 0, last_date TEXT, is_premium INTEGER DEFAULT 0, premium_expiry TEXT)')
cur.execute('CREATE TABLE IF NOT EXISTS channels (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE)')
cur.execute('CREATE TABLE IF NOT EXISTS stats (day TEXT PRIMARY KEY, images_generated INTEGER DEFAULT 0)')
conn.commit()
lock = threading.Lock()

def today(): return datetime.now(UTC).strftime('%Y-%m-%d')

def ensure_user(uid):
    with lock:
        cur.execute('SELECT * FROM users WHERE user_id=?', (uid,))
        r = cur.fetchone()
        if not r:
            cur.execute('INSERT INTO users VALUES(?,?,?,?,?)', (uid,0,today(),0,None))
            conn.commit()
            return {'user_id':uid,'used_today':0,'last_date':today(),'is_premium':0,'premium_expiry':None}
        if r['last_date']!=today():
            cur.execute('UPDATE users SET used_today=?,last_date=? WHERE user_id=?',(0,today(),uid))
            conn.commit()
            r=dict(r);r['used_today']=0;r['last_date']=today()
        return dict(r)

def inc(uid):
    with lock:
        cur.execute('UPDATE users SET used_today=used_today+1 WHERE user_id=?',(uid,))
        conn.commit()
        d=today()
        cur.execute('SELECT * FROM stats WHERE day=?',(d,))
        s=cur.fetchone()
        if not s:cur.execute('INSERT INTO stats VALUES(?,?)',(d,1))
        else:cur.execute('UPDATE stats SET images_generated=images_generated+1 WHERE day=?',(d,))
        conn.commit()

def set_premium(uid,m=1):
    e=datetime.now(UTC)+timedelta(days=30*m)
    with lock:
        ensure_user(uid)
        cur.execute('UPDATE users SET is_premium=1,premium_expiry=?,last_date=? WHERE user_id=?',(e.isoformat(),today(),uid))
        conn.commit()

def unset_premium(uid):
    with lock:
        cur.execute('UPDATE users SET is_premium=0,premium_expiry=NULL WHERE user_id=?',(uid,))
        conn.commit()

def is_premium(u):
    if u['is_premium'] and u['premium_expiry']:
        e=datetime.fromisoformat(u['premium_expiry'])
        if e>datetime.now(UTC):return True
        unset_premium(u['user_id'])
    return False

def gen_img(prompt):
    url='https://image.pollinations.ai/prompt/'+quote_plus(prompt)
    r=requests.get(url,timeout=60);r.raise_for_status();return r.content

@bot.message_handler(commands=['start'])
def start(m):
    ensure_user(m.from_user.id)
    bot.reply_to(m,f"Salom! Men AI rasm botman.\nMatn yuboring, men rasm yarataman.\nKuniga {FREE_DAILY_LIMIT} ta bepul.\nPremium uchun adminga yozing.")

@bot.message_handler(commands=['admin'])
def admin(m):
    if m.from_user.id!=ADMIN_ID:return
    kb=types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add('📊 Statistika','📢 Reklama','➕ Kanal qo‘shish','➖ Kanal o‘chirish','📜 Kanal ro‘yxati','💎 Premium berish/olish')
    bot.send_message(m.chat.id,"Admin menyusi:",reply_markup=kb)

@bot.message_handler(func=lambda m:m.text in ['📊 Statistika','📢 Reklama','➕ Kanal qo‘shish','➖ Kanal o‘chirish','📜 Kanal ro‘yxati','💎 Premium berish/olish'])
def admin_actions(m):
    if m.from_user.id!=ADMIN_ID:return
    if m.text=='📊 Statistika':
        with lock:
            cur.execute('SELECT COUNT(*) c FROM users');u=cur.fetchone()['c']
            cur.execute('SELECT COUNT(*) c FROM users WHERE is_premium=1');p=cur.fetchone()['c']
            cur.execute('SELECT images_generated FROM stats WHERE day=?',(today(),));r=cur.fetchone();imgs=r['images_generated'] if r else 0
        bot.reply_to(m,f"Foydalanuvchilar: {u}\nPremiumlar: {p}\nBugun yaratilgan: {imgs}")
    elif m.text=='📢 Reklama':
        bot.send_message(m.chat.id,"Reklama matnini yuboring:")
        bot.register_next_step_handler(m,reklama_send)
    elif m.text=='➕ Kanal qo‘shish':
        bot.send_message(m.chat.id,"Kanal username yuboring (@kanal):")
        bot.register_next_step_handler(m,add_channel)
    elif m.text=='➖ Kanal o‘chirish':
        bot.send_message(m.chat.id,"O‘chirish uchun kanal username yuboring:")
        bot.register_next_step_handler(m,del_channel)
    elif m.text=='📜 Kanal ro‘yxati':
        with lock:
            cur.execute('SELECT username FROM channels');rows=cur.fetchall()
        if not rows:bot.reply_to(m,"Kanal yo‘q.")
        else:bot.reply_to(m,"\n".join([r['username'] for r in rows]))
    elif m.text=='💎 Premium berish/olish':
        bot.send_message(m.chat.id,"Foydalanuvchi ID yuboring:")
        bot.register_next_step_handler(m,premium_toggle)

def reklama_send(m):
    if m.from_user.id!=ADMIN_ID:return
    text=m.text
    with lock:
        cur.execute('SELECT user_id FROM users');uids=[r['user_id'] for r in cur.fetchall()]
    s=0
    for uid in uids:
        try:bot.send_message(uid,text);s+=1
        except:pass
    bot.reply_to(m,f"Yuborildi: {s} ta foydalanuvchiga.")

def add_channel(m):
    if m.from_user.id!=ADMIN_ID:return
    u=m.text.strip()
    with lock:
        try:cur.execute('INSERT INTO channels(username) VALUES(?)',(u,));conn.commit();bot.reply_to(m,f"{u} qo‘shildi.")
        except sqlite3.IntegrityError:bot.reply_to(m,"Allaqachon mavjud.")

def del_channel(m):
    if m.from_user.id!=ADMIN_ID:return
    u=m.text.strip()
    with lock:
        cur.execute('DELETE FROM channels WHERE username=?',(u,));conn.commit();bot.reply_to(m,f"{u} o‘chirildi.")

def premium_toggle(m):
    if m.from_user.id!=ADMIN_ID:return
    try:uid=int(m.text)
    except:return bot.reply_to(m,"Noto‘g‘ri ID")
    with lock:
        cur.execute('SELECT is_premium FROM users WHERE user_id=?',(uid,));r=cur.fetchone()
        if r and r['is_premium']:unset_premium(uid);bot.reply_to(m,f"{uid} dan premium olib tashlandi.")
        else:set_premium(uid);bot.reply_to(m,f"{uid} ga premium berildi.")

@bot.message_handler(func=lambda m:True)
def msg(m):
    uid=m.from_user.id;txt=m.text.strip()
    if not txt:return
    if uid==ADMIN_ID:
        user=ensure_user(uid)
        bot.send_chat_action(uid,'upload_photo')
        try:
            b=io.BytesIO(gen_img(txt));b.name='img.jpg';b.seek(0)
            bot.send_photo(uid,b,caption=f'Prompt: {txt}')
            inc(uid)
        except:bot.reply_to(m,"Xato yuz berdi.")
        return
    u=ensure_user(uid);prem=is_premium(u)
    with lock:
        cur.execute('SELECT username FROM channels');chs=[r['username'] for r in cur.fetchall()]
    if chs:
        for c in chs:
            try:
                s=bot.get_chat_member(c,uid)
                if s.status in ('left','kicked'):
                    bot.reply_to(m,"Quyidagi kanallarga a‘zo bo‘ling:\n"+"\n".join(chs));return
            except:bot.reply_to(m,"Quyidagi kanallarga a‘zo bo‘ling:\n"+"\n".join(chs));return
    if not prem and u['used_today']>=FREE_DAILY_LIMIT:
        bot.reply_to(m,f"Kunlik {FREE_DAILY_LIMIT} limit tugadi.\nPremium uchun adminga yozing.");return
    try:
        bot.send_chat_action(uid,'upload_photo')
        b=io.BytesIO(gen_img(txt));b.name='img.jpg';b.seek(0)
        bot.send_photo(uid,b,caption=f'Prompt: {txt}')
        inc(uid)
    except:bot.reply_to(m,"Xato yuz berdi.")

def cleaner():
    while True:
        with lock:
            cur.execute('SELECT user_id,premium_expiry FROM users WHERE is_premium=1')
            for r in cur.fetchall():
                if r['premium_expiry']:
                    e=datetime.fromisoformat(r['premium_expiry'])
                    if e<=datetime.now(UTC):unset_premium(r['user_id'])
        time.sleep(3600)

if __name__=='__main__':
    threading.Thread(target=cleaner,daemon=True).start()
    print('Bot started')
    bot.infinity_polling(skip_pending=True)

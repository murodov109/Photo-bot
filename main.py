import os,sqlite3,requests,io,threading,time,base64,traceback
from datetime import datetime,timedelta,timezone
import telebot
from telebot import types
from urllib.parse import quote_plus

TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
ADMIN_ID = int(os.getenv('ADMIN_ID','7617397626'))
DB_PATH = os.getenv('DATABASE_PATH','database.db')
FREE_DAILY_LIMIT = int(os.getenv('FREE_DAILY_LIMIT','3'))
CAPILOT_API_URL = os.getenv('CAPILOT_API_URL','') 
CAPILOT_API_KEY = os.getenv('CAPILOT_API_KEY','')
POLLINATIONS_BASE = 'https://image.pollinations.ai/prompt/'

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
admin_state = {}

def now_utc(): return datetime.now(timezone.utc)
def today_str(): return now_utc().strftime('%Y-%m-%d')

def ensure_user(uid):
    with lock:
        cur.execute('SELECT * FROM users WHERE user_id=?',(uid,))
        r = cur.fetchone()
        if not r:
            cur.execute('INSERT INTO users(user_id,used_today,last_date,is_premium,premium_expiry) VALUES(?,?,?,?,?)',(uid,0,today_str(),0,None))
            conn.commit()
            cur.execute('SELECT * FROM users WHERE user_id=?',(uid,))
            r = cur.fetchone()
        if r['last_date'] != today_str():
            cur.execute('UPDATE users SET used_today=0,last_date=? WHERE user_id=?',(today_str(),uid))
            conn.commit()
            cur.execute('SELECT * FROM users WHERE user_id=?',(uid,))
            r = cur.fetchone()
        return {'user_id':r['user_id'],'used_today':r['used_today'],'is_premium':r['is_premium'],'premium_expiry':r['premium_expiry']}

def inc_usage(uid):
    if uid == ADMIN_ID: return
    with lock:
        cur.execute('UPDATE users SET used_today=used_today+1 WHERE user_id=?',(uid,))
        conn.commit()
        d = today_str()
        cur.execute('SELECT * FROM stats WHERE day=?',(d,))
        s = cur.fetchone()
        if s:
            cur.execute('UPDATE stats SET images_generated=images_generated+1 WHERE day=?',(d,))
        else:
            cur.execute('INSERT INTO stats(day,images_generated) VALUES(?,?)',(d,1))
        conn.commit()

def set_premium(uid,months=1):
    exp = (now_utc() + timedelta(days=30*months)).isoformat()
    with lock:
        ensure_user(uid)
        cur.execute('UPDATE users SET is_premium=1,premium_expiry=? WHERE user_id=?',(exp,uid))
        conn.commit()

def unset_premium(uid):
    with lock:
        cur.execute('UPDATE users SET is_premium=0,premium_expiry=NULL WHERE user_id=?',(uid,))
        conn.commit()

def premium_active(u):
    if u.get('user_id') == ADMIN_ID: return True
    if u.get('is_premium'):
        pe = u.get('premium_expiry')
        if not pe: return False
        try:
            exp = datetime.fromisoformat(pe)
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=timezone.utc)
        except:
            unset_premium(u['user_id']); return False
        if exp > now_utc(): return True
        unset_premium(u['user_id'])
    return False

def fetch_image_from_pollinations(prompt):
    url = POLLINATIONS_BASE + quote_plus(prompt)
    r = requests.get(url, timeout=60)
    r.raise_for_status()
    return r.content

def fetch_image_from_capilot(prompt):
    if not CAPILOT_API_URL or not CAPILOT_API_KEY:
        raise RuntimeError('Capilot config missing')
    headers = {'Authorization': f'Bearer {CAPILOT_API_KEY}','Accept':'application/json'}
    try:
        r = requests.post(CAPILOT_API_URL, json={'prompt': prompt}, headers=headers, timeout=60)
    except Exception:
        raise
    if r.status_code != 200:
        raise RuntimeError(f'Capilot API error {r.status_code}')
    try:
        j = r.json()
    except Exception:
        return r.content
    if isinstance(j, dict):
        if 'image' in j and isinstance(j['image'], str):
            try:
                return base64.b64decode(j['image'])
            except:
                pass
        if 'url' in j and isinstance(j['url'], str):
            rr = requests.get(j['url'], timeout=60); rr.raise_for_status(); return rr.content
    return r.content

def generate_image_bytes(prompt):
    try:
        if CAPILOT_API_URL and CAPILOT_API_KEY:
            return fetch_image_from_capilot(prompt)
    except Exception:
        pass
    return fetch_image_from_pollinations(prompt)

@bot.message_handler(commands=['start'])
def cmd_start(m):
    ensure_user(m.from_user.id)
    bot.reply_to(m,f"Salom! AI rasm bot.\nMatn yuboring, rasm tayyorlayman.\nKuniga {FREE_DAILY_LIMIT} ta bepul.\nPremium: adminga yozing.")

@bot.message_handler(commands=['admin'])
def cmd_admin(m):
    if m.from_user.id != ADMIN_ID: return
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row('📊 Statistika','📢 Reklama')
    kb.row('➕ Kanal qoʻshish','➖ Kanal oʻchirish')
    kb.row('📜 Kanal roʻyxati','💎 Premium berish/olish')
    kb.row('❌ Bekor')
    bot.send_message(m.chat.id,'Admin panel',reply_markup=kb)

@bot.message_handler(func=lambda m: m.from_user and m.from_user.id==ADMIN_ID and m.text in ['📊 Statistika','📢 Reklama','➕ Kanal qoʻshish','➖ Kanal oʻchirish','📜 Kanal roʻyxati','💎 Premium berish/olish','❌ Bekor'])
def admin_buttons(m):
    t = m.text
    if t == '📊 Statistika':
        with lock:
            cur.execute('SELECT COUNT(*) c FROM users'); u = cur.fetchone()['c']
            cur.execute('SELECT COUNT(*) c FROM users WHERE is_premium=1'); p = cur.fetchone()['c']
            cur.execute('SELECT images_generated FROM stats WHERE day=?',(today_str(),)); r = cur.fetchone(); imgs = r['images_generated'] if r else 0
        bot.reply_to(m,f"Foydalanuvchilar: {u}\nPremiumlar: {p}\nBugun rasm: {imgs}")
        return
    if t == '📢 Reklama':
        admin_state[ADMIN_ID] = 'reklama'; bot.reply_to(m,'Reklama matnini yuboring.'); return
    if t == '➕ Kanal qoʻshish':
        admin_state[ADMIN_ID] = 'addchannel'; bot.reply_to(m,'Kanal username yuboring (masalan @kanal).'); return
    if t == '➖ Kanal oʻchirish':
        admin_state[ADMIN_ID] = 'delchannel'; bot.reply_to(m,'Oʻchirilishi kerak boʻlgan kanalni yuboring.'); return
    if t == '📜 Kanal roʻyxati':
        with lock:
            cur.execute('SELECT username FROM channels'); rows = cur.fetchall()
        if not rows: bot.reply_to(m,'Kanal yoʻq'); return
        bot.reply_to(m,'\n'.join([r['username'] for r in rows])); return
    if t == '💎 Premium berish/olish':
        admin_state[ADMIN_ID] = 'togglepremium'; bot.reply_to(m,'Foydalanuvchi ID yuboring.'); return
    if t == '❌ Bekor':
        admin_state.pop(ADMIN_ID,None); bot.reply_to(m,'Bekor qilindi'); return

@bot.message_handler(func=lambda m: m.from_user and m.from_user.id==ADMIN_ID)
def admin_pending(m):
    state = admin_state.get(ADMIN_ID)
    if not state: return
    txt = (m.text or '').strip()
    try:
        if state == 'reklama':
            with lock:
                cur.execute('SELECT user_id FROM users'); uids = [r['user_id'] for r in cur.fetchall()]
            s = 0
            for uid in uids:
                try: bot.send_message(uid, txt); s += 1
                except: pass
            bot.reply_to(m, f"Yuborildi: {s}")
        elif state == 'addchannel':
            try:
                with lock: cur.execute('INSERT INTO channels(username) VALUES(?)',(txt,)); conn.commit()
                bot.reply_to(m,'Kanal qoʻshildi')
            except: bot.reply_to(m,'Xatolik yoki kanal mavjud')
        elif state == 'delchannel':
            with lock: cur.execute('DELETE FROM channels WHERE username=?',(txt,)); conn.commit(); bot.reply_to(m,'Kanal oʻchirildi')
        elif state == 'togglepremium':
            try:
                uid = int(txt)
                with lock:
                    cur.execute('SELECT is_premium FROM users WHERE user_id=?',(uid,)); r = cur.fetchone()
                    if r and r['is_premium']:
                        unset_premium(uid); bot.reply_to(m,f"Premium olib tashlandi: {uid}")
                    else:
                        set_premium(uid); bot.reply_to(m,f"Premium berildi: {uid}")
            except:
                bot.reply_to(m,'Notoʻgʻri ID')
    except Exception as e:
        traceback.print_exc()
        bot.reply_to(m,'Xatolik yuz berdi')
    finally:
        admin_state.pop(ADMIN_ID,None)

@bot.message_handler(func=lambda m: True)
def handle_user(m):
    try:
        uid = m.from_user.id
        prompt = (m.text or '').strip()
        if not prompt: return
        u = ensure_user(uid)
        prem = premium_active(u)
        if uid == ADMIN_ID: prem = True
        with lock:
            cur.execute('SELECT username FROM channels'); rows = cur.fetchall(); chs = [r['username'] for r in rows]
        if chs:
            for c in chs:
                try:
                    member = bot.get_chat_member(c, uid)
                    if member.status in ('left','kicked'):
                        bot.reply_to(m,'Iltimos quyidagi kanallarga aʻzo boʻling:\n'+ '\n'.join(chs)); return
                except:
                    bot.reply_to(m,'Iltimos quyidagi kanallarga aʻzo boʻling:\n'+ '\n'.join(chs)); return
        if not prem and u['used_today'] >= FREE_DAILY_LIMIT:
            bot.reply_to(m,f"Kunlik {FREE_DAILY_LIMIT} limit tugadi. Premium uchun admin bilan bogʻlaning."); return
        bot.send_chat_action(uid,'upload_photo')
        img = generate_image_bytes(prompt)
        bio = io.BytesIO(img); bio.name='image.jpg'; bio.seek(0)
        bot.send_photo(uid, photo=bio, caption=f'Prompt: {prompt}')
        inc_usage(uid)
    except Exception:
        traceback.print_exc()
        try: bot.reply_to(m,'Rasm yaratishda xato yuz berdi')
        except: pass

def generate_image_bytes(prompt):
    try:
        if CAPILOT_API_URL and CAPILOT_API_KEY:
            return fetch_image_from_capilot(prompt)
    except Exception:
        traceback.print_exc()
    return fetch_image_from_pollinations(prompt)

def fetch_image_from_capilot(prompt):
    headers = {'Authorization': f'Bearer {CAPILOT_API_KEY}','Accept':'application/json'}
    r = requests.post(CAPILOT_API_URL, json={'prompt':prompt}, headers=headers, timeout=60)
    if r.status_code != 200:
        raise RuntimeError('Capilot API error')
    try:
        j = r.json()
    except:
        return r.content
    if isinstance(j, dict):
        if 'image' in j and isinstance(j['image'], str):
            try: return base64.b64decode(j['image'])
            except: pass
        if 'url' in j and isinstance(j['url'], str):
            rr = requests.get(j['url'], timeout=60); rr.raise_for_status(); return rr.content
    return r.content

def fetch_image_from_pollinations(prompt):
    url = POLLINATIONS_BASE + quote_plus(prompt)
    r = requests.get(url, timeout=60); r.raise_for_status(); return r.content

def cleaner():
    while True:
        try:
            with lock:
                cur.execute('SELECT user_id,premium_expiry FROM users WHERE is_premium=1')
                for row in cur.fetchall():
                    pe = row['premium_expiry']
                    if pe:
                        e = datetime.fromisoformat(pe)
                        if e.tzinfo is None: e = e.replace(tzinfo=timezone.utc)
                        if e <= now_utc(): unset_premium(row['user_id'])
        except:
            traceback.print_exc()
        time.sleep(3600)

if __name__=='__main__':
    threading.Thread(target=cleaner,daemon=True).start()
    print('Bot started')
    bot.infinity_polling(skip_pending=True, timeout=20, long_polling_timeout=20)
```0

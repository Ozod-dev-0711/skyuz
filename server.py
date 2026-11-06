"""SkyUz - server.py: aiohttp API + aiogram 3 bot + SQLite (python server.py)."""
import asyncio, hashlib, hmac, html, io, json, os, random, re, secrets, sqlite3, time, uuid
from collections import defaultdict, deque
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qsl
from aiohttp import web
from aiogram import Bot, Dispatcher, F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (BufferedInputFile, CallbackQuery, InlineKeyboardButton, KeyboardButton, MenuButtonWebApp,
                           Message, ReplyKeyboardMarkup, ReplyKeyboardRemove, WebAppInfo, BotCommand)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from dotenv import load_dotenv
import qrcode
from PIL import Image, ImageDraw, ImageFont

# ===================== 1. CONFIG =====================
load_dotenv()
BOT_TOKEN = os.environ["BOT_TOKEN"]
WEB = os.getenv("WEBSITE_URL", "http://localhost:8080").rstrip("/")
ADMIN_CHAT = int(os.getenv("ADMIN_CHAT_ID", "0"))
ADMINS = {int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()}
if not ADMINS and ADMIN_CHAT > 0:
    ADMINS.add(ADMIN_CHAT)
PORT, DB_PATH = int(os.getenv("PORT", "8080")), os.getenv("DB_PATH", "skyuz.db")
BOT_USER, bot, ph = "", None, PasswordHasher()
h = html.escape
PHONE_RE = r"^\+998 \d{2} \d{3} \d{2} \d{2}$"
EMAIL_RE = r"^[^\s@]+@[^\s@]+\.[^\s@]{2,}$"
PAYS = ["Payme", "Click", "Uzcard / Humo"]
now_s = lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S")
money = lambda n: f"{int(round(n, -3)):,}".replace(",", " ") + " so'm"

# ===================== 2. DATABASE =====================
db = sqlite3.connect(DB_PATH, check_same_thread=False)
db.row_factory = sqlite3.Row
db.executescript("""
CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, ism TEXT, fam TEXT, tel TEXT UNIQUE, email TEXT UNIQUE,
  hash TEXT, tg_id INTEGER UNIQUE, created TEXT);
CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY, user_id INTEGER, created REAL);
CREATE TABLE IF NOT EXISTS link_codes(code TEXT PRIMARY KEY, user_id INTEGER, created REAL);
CREATE TABLE IF NOT EXISTS orders(id INTEGER PRIMARY KEY, user_id INTEGER, kind TEXT, title TEXT, route TEXT, date TEXT,
  time TEXT, detail TEXT, total REAL, pay TEXT, status TEXT, reason TEXT, passengers TEXT, token TEXT, created TEXT);
""")

def q(sql, a=(), one=False, w=False):
    cur = db.execute(sql, a)
    if w:
        db.commit()
        return cur.lastrowid
    rows = [dict(r) for r in cur.fetchall()]
    return (rows[0] if rows else None) if one else rows

# ===================== 3. MOCK DATA (real API'larni shu yerga ulang) =====================
C = {'Toshkent': (41.26, 69.28, 'TAS'), 'Samarqand': (39.65, 66.96, 'SKD'), 'Buxoro': (39.77, 64.43, 'BHK'),
     'Xiva / Urganch': (41.55, 60.63, 'UGC'), 'Nukus': (42.46, 59.61, 'NCU'), 'Andijon': (40.78, 72.34, 'AZN'),
     "Farg'ona": (40.38, 71.78, 'FEG'), 'Namangan': (41.0, 71.67, 'NMA'), 'Qarshi': (38.86, 65.79, 'KSQ'),
     'Termiz': (37.22, 67.28, 'TMJ'), 'Navoiy': (40.1, 65.37, 'NVI'), 'Jizzax': (40.12, 67.84, ''),
     'Guliston': (40.49, 68.78, ''), 'Istanbul': (41.01, 28.98, 'IST'), 'Dubay': (25.2, 55.27, 'DXB'),
     'Moskva': (55.75, 37.62, 'SVO'), 'Seul': (37.57, 126.98, 'ICN'), 'Dehli': (28.61, 77.21, 'DEL'),
     'Olmaota': (43.24, 76.89, 'ALA')}
NAMES = list(C)
RAIL, AIR = NAMES[:13], [n for n in NAMES if C[n][2]]
# (nomi, narx koeff., ichki, xalqaro)
AL = [('Uzbekistan Airways', 1.05, 1, 1), ('Qanot Sharq', .9, 1, 1), ('Air Samarkand', .88, 1, 0), ('Centrum Air', .85, 1, 1),
      ('Turkish Airlines', 1.1, 0, 1), ('Aeroflot', 1.15, 0, 1), ('flydubai', .95, 0, 1), ('Air Astana', 1, 0, 1),
      ('Korean Air', 1.3, 0, 1), ('Air India', 1, 0, 1)]
ST = lambda *a: list(a)
TR = [  # nomi, raqam, koeff, vagonlar, jo'nash vaqtlari (daqiqa), bekatlar [(shahar, daqiqa)]
    ('Afrosiyob', 768, 1, ['Economy', 'Business'], [450, 720, 1020], [('Toshkent', 0), ('Guliston', 55), ('Jizzax', 100), ('Samarqand', 135), ('Navoiy', 195), ('Buxoro', 245)]),
    ('Sharq', 10, .9, ['Economy', 'Business'], [540, 1080], [('Toshkent', 0), ('Guliston', 70), ('Jizzax', 140), ('Samarqand', 230), ('Navoiy', 330), ('Buxoro', 430)]),
    ('Registon', 30, .8, ['Plats-kart', 'Kupe', 'SV/Lyuks'], [1320], [('Toshkent', 0), ('Jizzax', 150), ('Samarqand', 260)]),
    ('Nasaf', 44, .8, ['Plats-kart', 'Kupe', 'SV/Lyuks'], [1260], [('Toshkent', 0), ('Jizzax', 170), ('Samarqand', 290), ('Qarshi', 430)]),
    ('Surxon', 52, .8, ['Plats-kart', 'Kupe', 'SV/Lyuks'], [1140], [('Toshkent', 0), ('Samarqand', 300), ('Qarshi', 430), ('Termiz', 720)]),
    ('Shosh', 62, .8, ['Plats-kart', 'Kupe', 'SV/Lyuks'], [480, 1200], [('Toshkent', 0), ('Namangan', 260), ('Andijon', 340), ("Farg'ona", 420)]),
    ('Xorazm', 70, .8, ['Plats-kart', 'Kupe', 'SV/Lyuks'], [1080], [('Toshkent', 0), ('Samarqand', 300), ('Navoiy', 400), ('Buxoro', 520), ('Xiva / Urganch', 900)]),
    ("Qoraqalpog'iston", 80, .8, ['Plats-kart', 'Kupe', 'SV/Lyuks', 'Business'], [1020], [('Toshkent', 0), ('Samarqand', 300), ('Navoiy', 400), ('Buxoro', 520), ('Xiva / Urganch', 900), ('Nukus', 1100)])]
RATE = {'Economy': 800, 'Business': 1200, 'Plats-kart': 330, 'Kupe': 520, 'SV/Lyuks': 950}
WAGS = list(RATE)
OFFERS = {}  # taklif keshi: narx serverda hisoblanadi, klientga ishonilmaydi

def km(a, b):
    import math
    r = math.radians
    (la, lo, _), (lb, ob, _) = C[a], C[b]
    x = math.sin(r(lb - la) / 2) ** 2 + math.cos(r(la)) * math.cos(r(lb)) * math.sin(r(ob - lo) / 2) ** 2
    return 2 * 6371 * math.asin(math.sqrt(x))
tm = lambda m: f"{int(m % 1440) // 60:02d}:{int(m % 1440) % 60:02d}"

def reg(o):
    o["id"] = uuid.uuid4().hex[:10]
    OFFERS[o["id"]] = o
    if len(OFFERS) > 3000:
        for k in list(OFFERS)[:1000]:
            OFFERS.pop(k, None)
    return o

def gen_flights(frm, to, date, cls, ret=False):
    d = km(frm, to)
    intl = NAMES.index(frm) > 12 or NAMES.index(to) > 12
    pool = [a for a in AL if a[3 if intl else 2]]
    out = []
    for k in range(14):
        a, st = pool[k % len(pool)], int(intl and random.random() < .35)
        pr = (160000 + d * (520 if intl else 430)) * a[1] * (1 + (random.random() - .5) * .14) * (.88 if st else 1) * (2.4 if cls == "Biznes" else 1) * (1.9 if ret else 1)
        dep, du = random.randint(5, 23) * 60 + random.choice([0, 15, 30, 45]), int(45 + d / 13 + st * random.randint(90, 170) + random.randint(-5, 10))
        out.append(reg({"kind": "avia", "title": a[0], "dep": tm(dep), "arr": tm(dep + du), "du": du, "stops": st, "price": round(pr, -3),
                        "from": frm, "to": to, "date": date, "cls": cls, "route": f"{frm} → {to}", "time": f"{tm(dep)} → {tm(dep + du)}"}))
    return out

def gen_trains(frm, to, date, wag="Barchasi"):
    out, dist = [], km(frm, to) * 1.25
    for n, no, f, cls, deps, stops in TR:
        tot = stops[-1][1]
        for dr in (0, 1):
            s = [(c, tot - m) for c, m in stops][::-1] if dr else stops
            names = [x[0] for x in s]
            if frm not in names or to not in names or names.index(frm) >= names.index(to):
                continue
            i, j = names.index(frm), names.index(to)
            for k, D in enumerate(deps):
                wg = [{"type": c, "price": round(RATE[c] * dist * f * random.uniform(.97, 1.03), -3), "seats": 0 if random.random() < .1 else random.randint(4, 40)}
                      for c in cls if wag in ("Barchasi", c)]
                if wg:
                    dep, arr = D + s[i][1], D + s[j][1]
                    out.append(reg({"kind": "train", "title": f"{n} №{no + dr + k * 2}", "dep": tm(dep), "arr": tm(arr), "du": arr - dep,
                                    "wagons": wg, "from": frm, "to": to, "date": date, "route": f"{frm} → {to}", "time": f"{tm(dep)} → {tm(arr)}"}))
    return out

# ===================== 4. TICKET (rasm + QR) =====================
def make_ticket(o):
    img = Image.new("RGB", (900, 440), "#0B1224")
    d, F = ImageDraw.Draw(img), (lambda s: ImageFont.load_default(size=s))
    d.rectangle((0, 0, 900, 10), fill="#D6B36A")
    d.text((40, 30), "SkyUz - ELEKTRON CHIPTA", font=F(34), fill="#F2DFA7")
    p = json.loads(o["passengers"])
    rows = [f"Chipta raqami: SU-{o['id']:06d}", f"Yo'nalish: {o['route']}", f"Sana: {o['date']}   {o['time']}", o["title"], o["detail"],
            "Yo'lovchi: " + ", ".join(x["name"] for x in p)[:60], f"Narx: {money(o['total'])}"]
    for i, t in enumerate(rows):
        d.text((40, 100 + i * 46), t, font=F(26), fill="#F4EFE6")
    qr = qrcode.make(f"SKYUZ:{o['id']}:{o['token']}").convert("RGB").resize((240, 240))
    img.paste(qr, (630, 130))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()

# ===================== 5. ORDERS / NOTIFICATIONS =====================
async def safe_send(chat, text, **kw):
    try:
        return await bot.send_message(chat, text, parse_mode="HTML", **kw)
    except (TelegramForbiddenError, TelegramBadRequest) as e:  # bot bloklangan va h.k.
        print("Telegram xato:", chat, e)

async def notify_admin(text, kb=None):
    if ADMIN_CHAT:
        return await safe_send(ADMIN_CHAT, text, reply_markup=kb)

def admin_kb(oid):
    b = InlineKeyboardBuilder()
    b.button(text="Tasdiqlash", callback_data=f"a:ok:{oid}")
    b.button(text="Rad etish", callback_data=f"a:no:{oid}")
    b.button(text="Foydalanuvchiga yozish", callback_data=f"a:msg:{oid}")
    b.adjust(2, 1)
    return b.as_markup()

def order_text(o, admin=False):
    t = f"<b>Buyurtma №{o['id']}</b>\n"
    if admin:
        u = q("SELECT * FROM users WHERE id=?", (o["user_id"],), one=True)
        t += f"Mijoz: {h(u['ism'])} {h(u['fam'] or '')} {h(u['tel'] or '')}\n"
    names = ", ".join(h(x["name"]) for x in json.loads(o["passengers"]))
    t += (f"Yo'nalish: {h(o['route'])}\nSana: {h(o['date'])}, {h(o['time'])}\n{h(o['title'])}\n{h(o['detail'])}\n"
          f"Yo'lovchilar: {names}\nNarx: {money(o['total'])}\nTo'lov: {h(o['pay'])}\nHolat: <b>{h(o['status'])}</b>")
    if o.get("reason"):
        t += f"\nSabab: {h(o['reason'])}"
    return t

async def make_order(u, off, wag, pax, pay):
    if off["kind"] == "avia":
        price, detail = off["price"], off["cls"]
    else:
        price, detail = next(w["price"] for w in off["wagons"] if w["type"] == wag), wag
    oid = q("INSERT INTO orders(user_id,kind,title,route,date,time,detail,total,pay,status,passengers,token,created) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (u["id"], off["kind"], off["title"], off["route"], off["date"], off["time"], detail, price * len(pax), pay, "Kutilmoqda",
             json.dumps(pax), secrets.token_hex(4), now_s()), w=True)
    o = q("SELECT * FROM orders WHERE id=?", (oid,), one=True)
    await notify_admin(order_text(o, True), admin_kb(oid))
    if u["tg_id"]:
        await safe_send(u["tg_id"], "Buyurtmangiz qabul qilindi, tasdiqlangach chipta shu yerga yuboriladi.\n\n" + order_text(o))
    return o

async def confirm_order(o):
    q("UPDATE orders SET status='Tasdiqlangan' WHERE id=?", (o["id"],), w=True)
    o = q("SELECT * FROM orders WHERE id=?", (o["id"],), one=True)
    u = q("SELECT * FROM users WHERE id=?", (o["user_id"],), one=True)
    if u["tg_id"]:
        try:
            await bot.send_photo(u["tg_id"], BufferedInputFile(make_ticket(o), "chipta.png"), caption=order_text(o), parse_mode="HTML")
        except (TelegramForbiddenError, TelegramBadRequest) as e:
            print("Chipta yuborilmadi:", e)

# ===================== 6. WEB API =====================
HITS = defaultdict(deque)

def validate_init_data(init_data, token, max_age=86400):
    """Telegram Mini App initData tekshiruvi (HMAC-SHA256). Muvaffaqiyatda user dict, aks holda None."""
    try:
        pairs = dict(parse_qsl(init_data, keep_blank_values=True))
        got = pairs.pop("hash", "")
        dcs = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
        secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()  # kalit = HMAC("WebAppData", BOT_TOKEN)
        calc = hmac.new(secret, dcs.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(calc, got) or time.time() - int(pairs.get("auth_date", 0)) > max_age:
            return None
        return json.loads(pairs["user"])
    except Exception:
        return None

def public(u):
    return {"id": u["id"], "ism": u["ism"], "fam": u["fam"], "tel": u["tel"], "email": u["email"], "tg": bool(u["tg_id"])}

def err(msg, st=400):
    return web.json_response({"xato": msg}, status=st)

async def body(r):
    try:
        d = await r.json()
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}

cl = lambda s, n=100: re.sub(r"[<>]", "", str(s or "").strip())[:n]

def cur_user(r):
    t = r.cookies.get("sky")
    return q("SELECT u.* FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token=? AND s.created>?", (t, time.time() - 30 * 86400), one=True) if t else None

def login_resp(u):
    tok = secrets.token_urlsafe(32)
    q("INSERT INTO sessions VALUES(?,?,?)", (tok, u["id"], time.time()), w=True)
    resp = web.json_response({"user": public(u)})
    resp.set_cookie("sky", tok, httponly=True, samesite="Lax", secure=WEB.startswith("https"), max_age=30 * 86400)
    return resp

def ensure_tg_user(tg):
    """Telegram ID bo'yicha hisobni topadi yoki parolsiz yaratadi. (user, yangi_mi)"""
    u = q("SELECT * FROM users WHERE tg_id=?", (tg["id"],), one=True)
    if u:
        return u, False
    uid = q("INSERT INTO users(ism,fam,tg_id,created) VALUES(?,?,?,?)", (cl(tg.get("first_name") or "Foydalanuvchi", 40), cl(tg.get("last_name"), 40), tg["id"], now_s()), w=True)
    return q("SELECT * FROM users WHERE id=?", (uid,), one=True), True

@web.middleware
async def mw(r, handler):
    ip = r.headers.get("X-Forwarded-For", r.remote or "").split(",")[0].strip()
    lim = 10 if r.path in ("/api/login", "/api/register", "/api/tg-auth") else 120
    dq, t = HITS[(ip, lim)], time.time()
    while dq and dq[0] < t - 60:
        dq.popleft()
    if len(dq) >= lim:
        return err("Juda ko'p so'rov. Birozdan keyin urinib ko'ring.", 429)
    dq.append(t)
    try:
        resp = web.Response(status=204) if r.method == "OPTIONS" else await handler(r)
    except web.HTTPException as e:
        resp = err(e.reason, e.status)
    if r.headers.get("Origin") == WEB:  # CORS faqat WEBSITE_URL uchun
        resp.headers.update({"Access-Control-Allow-Origin": WEB, "Access-Control-Allow-Credentials": "true",
                             "Access-Control-Allow-Headers": "Content-Type", "Access-Control-Allow-Methods": "GET,POST,OPTIONS"})
    resp.headers["Content-Security-Policy"] = "frame-ancestors 'self' https://web.telegram.org https://*.telegram.org"
    return resp

async def api_register(r):
    d = await body(r)
    ism, fam, tel, em, pw = cl(d.get("ism"), 40), cl(d.get("fam"), 40), cl(d.get("tel"), 20), cl(d.get("email"), 80).lower(), str(d.get("parol") or "")
    if len(ism) < 2 or len(fam) < 2: return err("Ism va familiyani kiriting")
    if not re.match(PHONE_RE, tel): return err("Telefon raqami noto'g'ri")
    if not re.match(EMAIL_RE, em): return err("Email noto'g'ri")
    if len(pw) < 8 or not re.search(r"[A-Za-z]", pw) or not re.search(r"\d", pw): return err("Parol kamida 8 belgi, harf va raqamdan iborat bo'lsin")
    if q("SELECT 1 FROM users WHERE tel=? OR email=?", (tel, em), one=True): return err("Bu telefon yoki email ro'yxatdan o'tgan", 409)
    uid = q("INSERT INTO users(ism,fam,tel,email,hash,created) VALUES(?,?,?,?,?,?)", (ism, fam, tel, em, ph.hash(pw), now_s()), w=True)
    u = q("SELECT * FROM users WHERE id=?", (uid,), one=True)
    asyncio.create_task(notify_admin(f"<b>Yangi foydalanuvchi</b>\nIsm: {h(ism)} {h(fam)}\nTelefon: {h(tel)}\nEmail: {h(em)}\nVaqt: {now_s()}"))
    return login_resp(u)

async def api_login(r):
    d = await body(r)
    ident, pw = cl(d.get("id"), 80).lower(), str(d.get("parol") or "")
    u = q("SELECT * FROM users WHERE email=? OR tel=?", (ident, cl(d.get("id"), 20)), one=True)
    try:
        if not u or not u["hash"]: raise VerifyMismatchError()
        ph.verify(u["hash"], pw)
    except VerifyMismatchError:
        return err("Telefon/email yoki parol noto'g'ri", 401)
    return login_resp(u)

async def api_tg_auth(r):
    tg = validate_init_data(str((await body(r)).get("initData") or ""), BOT_TOKEN)
    if not tg: return err("Telegram ma'lumotlari tasdiqlanmadi", 401)
    u, new = ensure_tg_user(tg)
    if new:
        asyncio.create_task(notify_admin(f"<b>Yangi foydalanuvchi (Mini App orqali)</b>\nIsm: {h(u['ism'])} {h(u['fam'] or '')}\nUsername: @{h(tg.get('username') or '-')}\nVaqt: {now_s()}"))
    return login_resp(u)

async def api_me(r):
    u = cur_user(r)
    return web.json_response({"user": public(u)}) if u else err("Tizimga kiring", 401)

async def api_logout(r):
    q("DELETE FROM sessions WHERE token=?", (r.cookies.get("sky"),), w=True)
    resp = web.json_response({"ok": True})
    resp.del_cookie("sky")
    return resp

def search_args(r, cities):
    p = r.query
    frm, to, date = p.get("from", ""), p.get("to", ""), p.get("date", "")
    if frm not in cities or to not in cities or frm == to or not re.match(r"^\d{4}-\d{2}-\d{2}$", date):
        raise web.HTTPBadRequest(reason="Qidiruv parametrlari noto'g'ri")
    return frm, to, date, p

async def api_flights(r):
    frm, to, date, p = search_args(r, AIR)
    return web.json_response({"items": gen_flights(frm, to, date, "Biznes" if p.get("cls") == "Biznes" else "Ekonom", bool(p.get("ret")))})

async def api_trains(r):
    frm, to, date, p = search_args(r, RAIL)
    return web.json_response({"items": gen_trains(frm, to, date, p.get("wag") if p.get("wag") in WAGS else "Barchasi")})

async def api_order_new(r):
    u = cur_user(r)
    if not u: return err("Tizimga kiring", 401)
    d = await body(r)
    off = OFFERS.get(str(d.get("offer_id")))
    if not off: return err("Taklif eskirgan, qayta qidiring", 410)
    pax = [{"name": cl(x.get("name"), 60), "passport": cl(x.get("passport"), 9).upper()} for x in (d.get("passengers") or [])[:9] if isinstance(x, dict)]
    if not pax or any(len(x["name"]) < 3 or not re.match(r"^[A-Z]{2}\d{7}$", x["passport"]) for x in pax): return err("Yo'lovchi ma'lumotlari noto'g'ri")
    if d.get("pay") not in PAYS: return err("To'lov usulini tanlang")
    wag = d.get("wagon")
    if off["kind"] == "train" and not any(w["type"] == wag and w["seats"] >= len(pax) for w in off["wagons"]): return err("Bu vagonda yetarli joy yo'q")
    o = await make_order(u, off, wag, pax, d["pay"])
    return web.json_response({"order": {"id": o["id"], "status": o["status"]}})

async def api_orders(r):
    u = cur_user(r)
    if not u: return err("Tizimga kiring", 401)
    rows = q("SELECT * FROM orders WHERE user_id=? ORDER BY id DESC", (u["id"],))
    for o in rows:
        o["passengers"], o["token"], o["ticket"] = json.loads(o["passengers"]), None, o["status"] == "Tasdiqlangan"
    return web.json_response({"orders": rows})

async def api_ticket(r):
    u = cur_user(r)
    o = q("SELECT * FROM orders WHERE id=? AND user_id=?", (int(r.match_info["id"]), u["id"]), one=True) if u else None
    if not o or o["status"] != "Tasdiqlangan": return err("Chipta topilmadi", 404)
    return web.Response(body=make_ticket(o), content_type="image/png", headers={"Content-Disposition": f'inline; filename="chipta-{o["id"]}.png"'})

async def api_link_code(r):
    u = cur_user(r)
    if not u: return err("Tizimga kiring", 401)
    code = secrets.token_urlsafe(8)
    q("INSERT INTO link_codes VALUES(?,?,?)", (code, u["id"], time.time()), w=True)
    return web.json_response({"url": f"https://t.me/{BOT_USER}?start=link_{code}"})

async def index(r):
    return web.FileResponse(Path(__file__).parent / "index.html")

# ===================== 7. BOT =====================
router, dp = Router(), Dispatcher(storage=MemoryStorage())
dp.include_router(router)

class Buy(StatesGroup): flow = State()
class Adm(StatesGroup):
    reason = State()
    msg = State()

STEPS = ["frm", "to", "date", "pax", "cls", "pick", "name", "passport", "phone", "pay", "confirm"]
is_admin = lambda uid: uid in ADMINS

def menu_kb():
    b = InlineKeyboardBuilder()
    b.button(text="Avia chipta", callback_data="b:k:avia"); b.button(text="Poyezd chipta", callback_data="b:k:train")
    b.button(text="Mening chiptalarim", callback_data="m:t"); b.button(text="Saytga o'tish", url=WEB)
    b.button(text="Mini ilovani ochish", web_app=WebAppInfo(url=WEB + "/?tgapp=1")); b.button(text="Yordam", callback_data="m:h")
    b.adjust(2, 2, 1, 1)
    return b.as_markup()

HELP = "SkyUz yordam:\n/menu - bosh menyu\n/tickets - mening chiptalarim\n/cancel - bekor qilish\nSavollar uchun: info@skyuz.uz"

async def ans(t, text, kb=None):
    if isinstance(t, CallbackQuery):
        try:
            return await t.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
        except TelegramBadRequest:
            return await t.message.answer(text, reply_markup=kb, parse_mode="HTML")
    return await t.answer(text, reply_markup=kb, parse_mode="HTML")

def nav(b, back=True):
    b.row(*([InlineKeyboardButton(text="Orqaga", callback_data="b:back")] if back else []), InlineKeyboardButton(text="Bekor qilish", callback_data="b:x"))

async def show(t, st, step):
    """Oqimning har bir bosqichini chizadi."""
    await st.update_data(step=step)
    await st.set_state(Buy.flow)
    d = await st.get_data()
    k, b = d["kind"], InlineKeyboardBuilder()
    if step in ("frm", "to"):
        for i, c in enumerate(AIR if k == "avia" else RAIL):
            if step == "to" and c == d["frm"]: continue
            b.button(text=c, callback_data=f"b:{'f' if step == 'frm' else 't'}:{i}")
        b.adjust(3); text = "Qayerdan jo'naysiz?" if step == "frm" else "Qayerga borasiz?"
    elif step == "date":
        for i in range(1, 15):
            dt = datetime.now() + timedelta(days=i)
            b.button(text=dt.strftime("%d.%m"), callback_data="b:d:" + dt.strftime("%Y-%m-%d"))
        b.adjust(4); text = "Jo'nash sanasini tanlang:"
    elif step == "pax":
        for i in range(1, 7): b.button(text=str(i), callback_data=f"b:p:{i}")
        b.adjust(6); text = "Yo'lovchilar sonini tanlang:"
    elif step == "cls":
        opts = ["Ekonom", "Biznes"] if k == "avia" else WAGS
        for i, c in enumerate(opts): b.button(text=c, callback_data=f"b:c:{i}")
        b.adjust(2); text = "Klassni tanlang:" if k == "avia" else "Vagon turini tanlang:"
    elif step == "pick":
        res, pg = d["res"], d.get("page", 0)
        text = f"<b>{h(d['frm'])} → {h(d['to'])}</b>, {d['date']}\nNatijalar: {len(res)} ta"
        for i in range(pg * 5, min(len(res), pg * 5 + 5)):
            x = res[i]
            b.button(text=f"{x['time'][:5]} {x['title']} · {money(x['price'])}", callback_data=f"b:r:{i}")
        b.adjust(1)
        pager = [InlineKeyboardButton(text="◀", callback_data=f"b:pg:{pg - 1}")] if pg else []
        if (pg + 1) * 5 < len(res): pager.append(InlineKeyboardButton(text="▶", callback_data=f"b:pg:{pg + 1}"))
        if pager: b.row(*pager)
        if not res: text += "\nAfsuski, hech narsa topilmadi. Orqaga qaytib boshqa sana tanlang."
    elif step in ("name", "passport"):
        n = len(d.get("pas", [])) + 1
        text = f"{n}-yo'lovchining " + ("ism va familiyasini kiriting (masalan: Ali Valiyev):" if step == "name" else "pasport seriyasi va raqamini kiriting (masalan: AB1234567):")
    elif step == "phone":
        kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="Telefon raqamini yuborish", request_contact=True)], [KeyboardButton(text="Orqaga"), KeyboardButton(text="Bekor qilish")]], resize_keyboard=True)
        return await (t.message if isinstance(t, CallbackQuery) else t).answer("Telefon raqamingizni yuboring (tugma orqali yoki +998 90 123 45 67 ko'rinishida):", reply_markup=kb)
    elif step == "pay":
        for i, p in enumerate(PAYS): b.button(text=p, callback_data=f"b:y:{i}")
        b.adjust(1); text = "To'lov usulini tanlang (demo):"
    else:  # confirm
        x = d["sel"]
        text = (f"<b>Buyurtma xulosasi</b>\n{h(d['frm'])} → {h(d['to'])}, {d['date']}\n{h(x['title'])} · {x['time']}\nYo'lovchilar: {', '.join(h(p['name']) for p in d['pas'])}\n"
                f"To'lov: {h(d['pay'])}\n<b>Jami: {money(x['price'] * d['pax'])}</b>")
        b.button(text="Tasdiqlash", callback_data="b:ok")
    nav(b, step != "frm")
    await ans(t, text, b.as_markup())

async def go_back(t, st):
    d = await st.get_data()
    i = STEPS.index(d.get("step", "frm"))
    if i == 0:
        await st.clear(); return await ans(t, "Bosh menyu:", menu_kb())
    if STEPS[i - 1] in ("name", "pick") or STEPS[i] == "name": await st.update_data(pas=[])
    if isinstance(t, Message): await t.answer("Orqaga qaytdik.", reply_markup=ReplyKeyboardRemove())
    await show(t, st, STEPS[i - 1])

async def cancel(t, st):
    await st.clear()
    if isinstance(t, Message): await t.answer("Bekor qilindi.", reply_markup=ReplyKeyboardRemove())
    await ans(t, "Bosh menyu:", menu_kb())

@router.message(CommandStart())
async def start(m: Message, state: FSMContext, command: CommandObject):
    await state.clear()
    a = command.args or ""
    if a.startswith("link_"):
        r = q("SELECT * FROM link_codes WHERE code=? AND created>?", (a[5:], time.time() - 600), one=True)
        if not r: return await m.answer("Ulash kodi eskirgan. Saytdan yangi kod oling.")
        q("UPDATE users SET tg_id=NULL WHERE tg_id=?", (m.from_user.id,), w=True)
        q("UPDATE users SET tg_id=? WHERE id=?", (m.from_user.id, r["user_id"]), w=True)
        q("DELETE FROM link_codes WHERE code=?", (a[5:],), w=True)
        return await m.answer("Hisobingiz Telegramga ulandi. Endi buyurtmalar va chiptalar shu yerga keladi.", reply_markup=menu_kb())
    await m.answer(f"Assalomu alaykum, {h(m.from_user.first_name)}! SkyUz - avia va poyezd chiptalari.", reply_markup=menu_kb())

@router.message(Command("menu"))
async def cmd_menu(m: Message, state: FSMContext):
    await state.clear(); await m.answer("Bosh menyu:", reply_markup=menu_kb())

@router.message(Command("cancel"))
async def cmd_cancel(m: Message, state: FSMContext): await cancel(m, state)

@router.message(Command("help"))
async def cmd_help(m: Message): await m.answer(HELP)

async def tickets(t, uid):
    u = q("SELECT * FROM users WHERE tg_id=?", (uid,), one=True)
    rows = q("SELECT * FROM orders WHERE user_id=? ORDER BY id DESC LIMIT 8", (u["id"],)) if u else []
    if not rows: return await ans(t, "Hozircha chiptalaringiz yo'q.", menu_kb())
    msg = t.message if isinstance(t, CallbackQuery) else t
    for o in rows:
        b = InlineKeyboardBuilder()
        if o["status"] == "Kutilmoqda": b.button(text="Bekor qilish", callback_data=f"u:cx:{o['id']}")
        await msg.answer(order_text(o), reply_markup=b.as_markup(), parse_mode="HTML")

@router.message(Command("tickets"))
async def cmd_tickets(m: Message): await tickets(m, m.from_user.id)

@router.callback_query(F.data.startswith("m:"))
async def menu_cb(cb: CallbackQuery):
    await cb.answer()
    await (tickets(cb, cb.from_user.id) if cb.data == "m:t" else ans(cb, HELP, menu_kb()))

@router.callback_query(F.data.startswith("u:cx:"))
async def user_cancel(cb: CallbackQuery):
    o = q("SELECT o.* FROM orders o JOIN users u ON u.id=o.user_id WHERE o.id=? AND u.tg_id=?", (int(cb.data[5:]), cb.from_user.id), one=True)
    if not o or o["status"] != "Kutilmoqda": return await cb.answer("Bekor qilib bo'lmaydi", show_alert=True)
    q("UPDATE orders SET status='Bekor qilingan' WHERE id=?", (o["id"],), w=True)
    await cb.answer("Buyurtma bekor qilindi")
    await cb.message.edit_text(order_text({**o, "status": "Bekor qilingan"}), parse_mode="HTML")
    await notify_admin(f"Buyurtma №{o['id']} mijoz tomonidan bekor qilindi.")

@router.callback_query(F.data.startswith("b:"))
async def buy_cb(cb: CallbackQuery, state: FSMContext):
    a = cb.data.split(":")
    act, v = a[1], a[2] if len(a) > 2 else ""
    await cb.answer()
    if act == "k":
        await state.clear(); await state.update_data(kind=v, pas=[]); return await show(cb, state, "frm")
    d = await state.get_data()
    if not d.get("kind"): return await ans(cb, "Seans eskirdi. /menu ni bosing.")
    k = d["kind"]
    if act == "x": return await cancel(cb, state)
    if act == "back": return await go_back(cb, state)
    if act == "f": await state.update_data(frm=(AIR if k == "avia" else RAIL)[int(v)]); return await show(cb, state, "to")
    if act == "t": await state.update_data(to=(AIR if k == "avia" else RAIL)[int(v)]); return await show(cb, state, "date")
    if act == "d": await state.update_data(date=v); return await show(cb, state, "pax")
    if act == "p": await state.update_data(pax=int(v)); return await show(cb, state, "cls")
    if act == "c":
        cls = (["Ekonom", "Biznes"] if k == "avia" else WAGS)[int(v)]
        offs = gen_flights(d["frm"], d["to"], d["date"], cls) if k == "avia" else gen_trains(d["frm"], d["to"], d["date"], cls)
        res = []
        for o in offs:  # poyezd: har bir (poyezd, vagon) alohida qator
            if k == "avia": res.append({"oid": o["id"], "wag": None, "title": o["title"], "time": o["time"], "price": o["price"]})
            else: res += [{"oid": o["id"], "wag": w["type"], "title": f"{o['title']} {w['type']}", "time": o["time"], "price": w["price"]} for w in o["wagons"] if w["seats"] >= d["pax"]]
        res.sort(key=lambda x: x["price"])
        await state.update_data(cls=cls, res=res, page=0)
        return await show(cb, state, "pick")
    if act == "pg": await state.update_data(page=int(v)); return await show(cb, state, "pick")
    if act == "r": await state.update_data(sel=d["res"][int(v)], pas=[]); return await show(cb, state, "name")
    if act == "y": await state.update_data(pay=PAYS[int(v)]); return await show(cb, state, "confirm")
    if act == "ok":
        u, new = ensure_tg_user({"id": cb.from_user.id, "first_name": cb.from_user.first_name, "last_name": cb.from_user.last_name})
        if new: await notify_admin(f"<b>Yangi foydalanuvchi (Bot orqali)</b>\nIsm: {h(u['ism'])}\nVaqt: {now_s()}")
        if d.get("phone") and not u["tel"] and not q("SELECT 1 FROM users WHERE tel=?", (d["phone"],), one=True):
            q("UPDATE users SET tel=? WHERE id=?", (d["phone"], u["id"]), w=True)
        x = d["sel"]
        if x["oid"] not in OFFERS: return await ans(cb, "Taklif eskirgan. Qaytadan qidiring: /menu")
        o = await make_order(u, OFFERS[x["oid"]], x["wag"], d["pas"], d["pay"])
        await state.clear()
        b = InlineKeyboardBuilder(); b.button(text="Ilovada ochish", web_app=WebAppInfo(url=f"{WEB}/?tgapp=1&route={C[d['frm']][2] or d['frm']}-{C[d['to']][2] or d['to']}"))
        await ans(cb, f"Buyurtmangiz administratorga yuborildi (№{o['id']}). Tasdiqlangach chipta shu yerga yuboriladi.", b.as_markup())

@router.message(Buy.flow)
async def flow_msg(m: Message, state: FSMContext):
    d, t = await state.get_data(), (m.text or "").strip()
    s = d.get("step")
    if t == "Bekor qilish": return await cancel(m, state)
    if t == "Orqaga": return await go_back(m, state)
    if s == "name":
        if len(t) < 5 or " " not in t: return await m.answer("Ism va familiyani to'liq kiriting (masalan: Ali Valiyev).")
        await state.update_data(tmp=cl(t, 60)); return await show(m, state, "passport")
    if s == "passport":
        p = t.replace(" ", "").upper()
        if not re.fullmatch(r"[A-Z]{2}\d{7}", p): return await m.answer("Pasport noto'g'ri. Namuna: AB1234567")
        pas = d.get("pas", []) + [{"name": d["tmp"], "passport": p}]
        await state.update_data(pas=pas)
        return await show(m, state, "name" if len(pas) < d["pax"] else "phone")
    if s == "phone":
        dig = re.sub(r"\D", "", m.contact.phone_number if m.contact else t)
        if len(dig) < 9: return await m.answer("Telefon raqami noto'g'ri. Namuna: +998 90 123 45 67")
        dig = dig[-9:]
        await state.update_data(phone=f"+998 {dig[:2]} {dig[2:5]} {dig[5:7]} {dig[7:]}")
        await m.answer("Rahmat!", reply_markup=ReplyKeyboardRemove())
        return await show(m, state, "pay")
    await m.answer("Iltimos, tugmalardan foydalaning.")

# ---- Admin ----
@router.callback_query(F.data.startswith("a:"))
async def admin_cb(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id): return await cb.answer("Sizda ruxsat yo'q", show_alert=True)  # server tomonida tekshiruv
    _, act, oid = cb.data.split(":")
    o = q("SELECT * FROM orders WHERE id=?", (int(oid),), one=True)
    if not o: return await cb.answer("Buyurtma topilmadi")
    if act in ("ok", "no") and o["status"] != "Kutilmoqda": return await cb.answer("Bu buyurtma allaqachon ko'rib chiqilgan", show_alert=True)
    await cb.answer()
    if act == "ok":
        await confirm_order(o)
        o["status"] = "Tasdiqlangan"
        await cb.message.edit_text(order_text(o, True), parse_mode="HTML")
    else:
        await state.set_state(Adm.reason if act == "no" else Adm.msg)
        await state.update_data(oid=o["id"])
        await cb.message.answer("Rad etish sababini yozing:" if act == "no" else "Foydalanuvchiga xabar matnini yozing:")

@router.message(Adm.reason)
async def adm_reason(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id): return
    d = await state.get_data(); await state.clear()
    q("UPDATE orders SET status='Rad etilgan', reason=? WHERE id=?", (cl(m.text, 200), d["oid"]), w=True)
    o = q("SELECT * FROM orders WHERE id=?", (d["oid"],), one=True)
    u = q("SELECT * FROM users WHERE id=?", (o["user_id"],), one=True)
    if u["tg_id"]: await safe_send(u["tg_id"], f"Afsuski, buyurtmangiz (№{o['id']}) rad etildi.\nSabab: {h(o['reason'])}\nUzr so'raymiz, boshqa variantni tanlab ko'ring.")
    await m.answer(f"Buyurtma №{o['id']} rad etildi.")

@router.message(Adm.msg)
async def adm_msg(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id): return
    d = await state.get_data(); await state.clear()
    u = q("SELECT u.* FROM users u JOIN orders o ON o.user_id=u.id WHERE o.id=?", (d["oid"],), one=True)
    if u and u["tg_id"]:
        await safe_send(u["tg_id"], f"<b>SkyUz administratori (buyurtma №{d['oid']}):</b>\n{h(m.text or '')}")
        await m.answer("Xabar yuborildi.")
    else:
        await m.answer("Mijoz Telegramga ulanmagan, xabar yuborib bo'lmadi.")

@router.message(Command("orders"))
async def cmd_orders(m: Message):
    if not is_admin(m.from_user.id): return
    rows = q("SELECT * FROM orders ORDER BY id DESC LIMIT 10")
    await m.answer("\n\n".join(order_text(o, True) for o in rows) or "Buyurtmalar yo'q.", parse_mode="HTML")

@router.message(Command("stats"))
async def cmd_stats(m: Message):
    if not is_admin(m.from_user.id): return
    day = datetime.now().strftime("%Y-%m-%d") + "%"
    nu = q("SELECT COUNT(*) c FROM users WHERE created LIKE ?", (day,), one=True)["c"]
    no = q("SELECT COUNT(*) c FROM orders WHERE created LIKE ?", (day,), one=True)["c"]
    await m.answer(f"Bugun:\nRo'yxatdan o'tganlar: {nu}\nBuyurtmalar: {no}")

# ===================== 8. ISHGA TUSHIRISH =====================
async def main():
    global bot, BOT_USER
    bot = Bot(BOT_TOKEN)
    BOT_USER = (await bot.get_me()).username
    await bot.set_my_commands([BotCommand(command="start", description="Boshlash"), BotCommand(command="menu", description="Bosh menyu"),
                               BotCommand(command="tickets", description="Mening chiptalarim"), BotCommand(command="help", description="Yordam"),
                               BotCommand(command="cancel", description="Bekor qilish")])
    try:
        await bot.set_chat_menu_button(menu_button=MenuButtonWebApp(text="SkyUz", web_app=WebAppInfo(url=WEB + "/?tgapp=1")))
    except Exception as e:
        print("Menyu tugmasi o'rnatilmadi (HTTPS kerak):", e)
    app = web.Application(middlewares=[mw])
    app.add_routes([web.get("/", index), web.post("/api/register", api_register), web.post("/api/login", api_login), web.post("/api/tg-auth", api_tg_auth),
                    web.get("/api/me", api_me), web.post("/api/logout", api_logout), web.get("/api/search/flights", api_flights),
                    web.get("/api/search/trains", api_trains), web.post("/api/orders", api_order_new), web.get("/api/orders", api_orders),
                    web.get("/api/orders/{id}/ticket", api_ticket), web.get("/api/link-code", api_link_code)])
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", PORT).start()
    print(f"Sayt: http://localhost:{PORT}  Bot: @{BOT_USER}")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())

import asyncio
import html
import logging
import os
import sqlite3
import time
from contextlib import closing

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import BaseFilter, Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
try:  # اختیاری: اگه python-dotenv نصب باشه و فایل .env داشتی، ازش می‌خونه
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

# ============================ تنظیمات (همه‌چیز توی همین فایله) ============================
# نصب:   pip install -U "aiogram>=3.27"
# اجرا:  python bot.py
# اینجا رو مستقیم عوض کن، یا بعداً از داخل ربات (/admin ← تنظیمات) تغییر بده.

BOT_TOKEN = os.getenv("BOT_TOKEN", "8857135364:AAE3JN-SJBJ5B0UGAfOy4_JQ1D9n6ztMc6Q")  # توکن تستی؛ آخر کار /revoke کن
SUPER_ADMIN = int(os.getenv("SUPER_ADMIN_ID", "7418212344"))
CARD_NUMBER = os.getenv("CARD_NUMBER", "0000-0000-0000-0000")
CARD_OWNER = os.getenv("CARD_OWNER", "نام صاحب کارت")
SUPPORT = os.getenv("SUPPORT_USERNAME", "@your_support")
STORE_NAME = os.getenv("STORE_NAME", "فروشگاه ایکس‌باکس")
PAY_TIMEOUT = int(os.getenv("PAY_TIMEOUT_MIN", "15"))
DB_PATH = os.getenv("DB_PATH", "store.db")

CHANNEL_LINK = os.getenv("CHANNEL_LINK", "https://t.me/+XKPTuU-rHrNkYzJk")
# آیدی عددی کانال (مثل -1001234567890). اگه پر باشه، کاربر برای خرید باید عضو کانال باشه.
CHANNEL_ID = os.getenv("CHANNEL_ID", "")

DEFAULT_RULES = (
    "1. بعد از پرداخت، رسید رو همین‌جا برای ربات بفرست.\n"
    "2. رسید جعلی رد می‌شه و ممکنه دسترسی‌ت به ربات بسته بشه.\n"
    "3. هر مشکلی داشتی از بخش پشتیبانی پیگیری کن.\n"
    "(ادمین از پنل ← تنظیمات ← قوانین می‌تونه این متن رو عوض کنه)"
)
DEFAULT_WELCOME = "سلام {name} 👋\nبه {store} خوش اومدی.\nلطفاً قبل از خرید، قوانین رو بخون."

CATEGORIES = ["اکانت گیم", "گیم‌پس", "لاگ"]
REF_THRESHOLD = 3  # هر ۳ زیرمجموعه = ۱ اکانت رایگان

# مقدار پیش‌فرض تنظیمات؛ ادمین از پنل می‌تونه همه‌شون رو عوض کنه
DEFAULTS = {
    "store_name": STORE_NAME,
    "support": SUPPORT,
    "card_number": CARD_NUMBER,
    "card_owner": CARD_OWNER,
    "rules": DEFAULT_RULES,
    "welcome": DEFAULT_WELCOME,
    "channel_link": CHANNEL_LINK,
    "channel_id": CHANNEL_ID,
    "ref_reward_product": "",  # آیدی محصولی که جایزه‌ی رفرال ازش داده می‌شه
}

# ----------------------------------------------------------------- database


def _conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def fetch(sql, args=()):
    with closing(_conn()) as c:
        return c.execute(sql, args).fetchall()


def fetch1(sql, args=()):
    rows = fetch(sql, args)
    return rows[0] if rows else None


def run(sql, args=()):
    with closing(_conn()) as c:
        cur = c.execute(sql, args)
        c.commit()
        return cur.rowcount


def insert(sql, args=()):
    with closing(_conn()) as c:
        cur = c.execute(sql, args)
        c.commit()
        return cur.lastrowid


def init_db():
    with closing(_conn()) as c:
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS users(
                id INTEGER PRIMARY KEY, name TEXT, joined_at INTEGER,
                balance INTEGER NOT NULL DEFAULT 0, ref_rewards_given INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS admins(
                id INTEGER PRIMARY KEY, added_by INTEGER);
            CREATE TABLE IF NOT EXISTS products(
                id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
                price INTEGER NOT NULL, active INTEGER NOT NULL DEFAULT 1,
                deleted INTEGER NOT NULL DEFAULT 0, category TEXT NOT NULL DEFAULT 'گیم‌پس');
            CREATE TABLE IF NOT EXISTS referrals(
                referred_id INTEGER PRIMARY KEY, referrer_id INTEGER NOT NULL,
                created_at INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS settings(
                key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS stock(
                id INTEGER PRIMARY KEY AUTOINCREMENT, product_id INTEGER NOT NULL,
                content TEXT NOT NULL, order_id INTEGER,
                sold INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS discounts(
                code TEXT PRIMARY KEY, percent INTEGER NOT NULL,
                max_uses INTEGER NOT NULL DEFAULT 0, used INTEGER NOT NULL DEFAULT 0,
                expires_at INTEGER, active INTEGER NOT NULL DEFAULT 1);
            CREATE TABLE IF NOT EXISTS orders(
                id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
                product_id INTEGER NOT NULL, price INTEGER NOT NULL,
                discount_code TEXT, percent INTEGER NOT NULL DEFAULT 0,
                final_price INTEGER NOT NULL, status TEXT NOT NULL,
                receipt TEXT, created_at INTEGER NOT NULL, delivered_at INTEGER);
            """
        )
        c.execute(
            "INSERT OR IGNORE INTO admins(id, added_by) VALUES (?, ?)",
            (SUPER_ADMIN, SUPER_ADMIN),
        )
        for stmt in (  # دیتابیس‌های قدیمی
            "ALTER TABLE products ADD COLUMN deleted INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE products ADD COLUMN category TEXT NOT NULL DEFAULT 'گیم‌پس'",
            "ALTER TABLE users ADD COLUMN balance INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE users ADD COLUMN ref_rewards_given INTEGER NOT NULL DEFAULT 0",
        ):
            try:
                c.execute(stmt)
            except sqlite3.OperationalError:
                pass
        c.commit()


# ------------------------------------------------------------------ helpers

FA_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")

STATUS_FA = {
    "pending": "⏳ منتظر پرداخت",
    "receipt_sent": "🕐 رسید ارسال شد، منتظر تأیید",
    "approving": "🕐 در حال تأیید",
    "delivered": "✅ تحویل شد",
    "rejected": "❌ رد شد",
    "cancelled": "🚫 لغو شد",
    "expired": "⌛ منقضی شد",
}


def parse_int(text):
    try:
        return int(text.translate(FA_DIGITS).replace(",", "").replace("،", "").strip())
    except (ValueError, AttributeError):
        return None


def cfg(key):
    row = fetch1("SELECT value FROM settings WHERE key=?", (key,))
    return row["value"] if row else DEFAULTS[key]


def set_cfg(key, value):
    run(
        "INSERT INTO settings(key, value) VALUES (?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )


def is_admin(uid):
    return uid == SUPER_ADMIN or fetch1("SELECT 1 FROM admins WHERE id=?", (uid,)) is not None


def admin_ids():
    return {SUPER_ADMIN} | {r["id"] for r in fetch("SELECT id FROM admins")}


def products_with_stock(category=None):
    if category:
        return fetch(
            """SELECT p.*, (SELECT COUNT(*) FROM stock s
                            WHERE s.product_id=p.id AND s.order_id IS NULL AND s.sold=0) AS n
               FROM products p WHERE p.deleted=0 AND p.category=? ORDER BY p.id""",
            (category,),
        )
    return fetch(
        """SELECT p.*, (SELECT COUNT(*) FROM stock s
                        WHERE s.product_id=p.id AND s.order_id IS NULL AND s.sold=0) AS n
           FROM products p WHERE p.deleted=0 ORDER BY p.id"""
    )


def register_user(uid, name):
    """Add the user if new; returns True the first time this uid is seen."""
    return run(
        "INSERT OR IGNORE INTO users(id, name, joined_at) VALUES (?,?,?)",
        (uid, name, int(time.time())),
    ) > 0


def get_user(uid):
    return fetch1("SELECT * FROM users WHERE id=?", (uid,))


def add_balance(uid, delta):
    """Change a user's wallet balance; never lets it go negative. Returns new balance or None if insufficient."""
    u = get_user(uid)
    if not u:
        return None
    new_bal = u["balance"] + delta
    if new_bal < 0:
        return None
    run("UPDATE users SET balance=? WHERE id=?", (new_bal, uid))
    return new_bal


async def grant_referral_rewards(bot: Bot, referrer_id: int):
    """Whenever a referrer crosses another multiple of REF_THRESHOLD, grant one reward item."""
    u = get_user(referrer_id)
    if not u:
        return
    total = fetch1("SELECT COUNT(*) c FROM referrals WHERE referrer_id=?", (referrer_id,))["c"]
    owed = total // REF_THRESHOLD
    if owed <= u["ref_rewards_given"]:
        return
    rounds = owed - u["ref_rewards_given"]
    run("UPDATE users SET ref_rewards_given=? WHERE id=?", (owed, referrer_id))
    pid = cfg("ref_reward_product")
    for _ in range(rounds):
        delivered = False
        if pid:
            oid = insert(
                """INSERT INTO orders(user_id, product_id, price, percent, final_price,
                                      status, discount_code, created_at)
                   VALUES (?,?,0,0,0,'pending','REFERRAL',?)""",
                (referrer_id, int(pid), int(time.time())),
            )
            got = run(
                """UPDATE stock SET order_id=? WHERE id=(
                       SELECT id FROM stock WHERE product_id=? AND order_id IS NULL AND sold=0 LIMIT 1)""",
                (oid, int(pid)),
            )
            if got:
                delivered = await deliver(bot, oid)
            if not delivered:
                run("DELETE FROM orders WHERE id=?", (oid,))
        if delivered:
            try:
                await bot.send_message(referrer_id, "🎁 با دعوت ۳ نفر، یه اکانت رایگان بهت داده شد. از «سفارش‌های من» ببینش!")
            except Exception:
                pass
        else:
            try:
                await bot.send_message(
                    referrer_id,
                    "🎁 با دعوت ۳ نفر جایزه رایگان بهت تعلق گرفت، ولی موجودی الان تموم شده. "
                    "به پشتیبانی پیام بده تا برات فعال کنن.",
                )
            except Exception:
                pass
            for aid in admin_ids():
                try:
                    await bot.send_message(
                        aid, f"⚠️ جایزه‌ی رفرال کاربر {referrer_id} رو دستی فعال کن (موجودی جایزه تموم شده)."
                    )
                except Exception:
                    pass


def get_discount(code):
    now = int(time.time())
    return fetch1(
        """SELECT * FROM discounts WHERE code=? AND active=1
           AND (expires_at IS NULL OR expires_at>?)
           AND (max_uses=0 OR used<max_uses)""",
        (code, now),
    )


def release_order(oid, new_status):
    """Free the reserved stock item and close the order (only if still open)."""
    changed = run(
        "UPDATE orders SET status=? WHERE id=? AND status IN ('pending','receipt_sent')",
        (new_status, oid),
    )
    if changed:
        run("UPDATE stock SET order_id=NULL WHERE order_id=? AND sold=0", (oid,))
    return changed


def expire_old_orders():
    limit = int(time.time()) - PAY_TIMEOUT * 60
    for o in fetch("SELECT id FROM orders WHERE status='pending' AND created_at<?", (limit,)):
        release_order(o["id"], "expired")


async def is_member(bot: Bot, uid: int) -> bool:
    cid = cfg("channel_id")
    if not cid or is_admin(uid):
        return True
    try:
        mem = await bot.get_chat_member(int(cid), uid)
        return mem.status in ("member", "administrator", "creator", "restricted")
    except Exception as e:  # e.g. bot is not admin of the channel
        logging.warning("membership check failed (is the bot admin of the channel?): %s", e)
        return True


async def edit(cb: CallbackQuery, text, kb=None):
    try:
        await cb.message.edit_text(text, reply_markup=kb)
    except TelegramBadRequest:
        await cb.message.answer(text, reply_markup=kb)


async def deliver(bot: Bot, oid):
    o = fetch1("SELECT * FROM orders WHERE id=?", (oid,))
    s = fetch1("SELECT * FROM stock WHERE order_id=? AND sold=0", (oid,))
    if not o or not s:
        return False
    run("UPDATE stock SET sold=1 WHERE id=?", (s["id"],))
    run(
        "UPDATE orders SET status='delivered', delivered_at=? WHERE id=?",
        (int(time.time()), oid),
    )
    if o["discount_code"]:
        run("UPDATE discounts SET used=used+1 WHERE code=?", (o["discount_code"],))
    p = fetch1("SELECT name FROM products WHERE id=?", (o["product_id"],))
    await bot.send_message(
        o["user_id"],
        f"✅ سفارش #{oid} تأیید شد.\n"
        f"محصول: {html.escape(p['name'])}\n\n"
        f"<code>{html.escape(s['content'])}</code>",
        reply_markup=main_menu(),
    )
    return True


# ---------------------------------------------------------------- keyboards


def main_menu():
    kb = InlineKeyboardBuilder()
    kb.button(text="🎮 خرید محصول", callback_data="buy", style="success")
    kb.button(text="📦 سفارش‌های من", callback_data="my", style="primary")
    kb.button(text="💰 کیف پول من", callback_data="wallet", style="primary")
    kb.button(text="🔗 دعوت دوستان", callback_data="ref", style="primary")
    kb.button(text="💬 پشتیبانی", callback_data="support", style="primary")
    kb.button(text="📜 قوانین", callback_data="rules", style="primary")
    rows = [1, 2, 2, 2]
    if cfg("channel_link"):
        kb.button(text="📢 کانال ما", url=cfg("channel_link"), style="primary")
        rows.append(1)
    kb.adjust(*rows)
    return kb.as_markup()


def home_kb():
    kb = InlineKeyboardBuilder()
    kb.button(text="↩️ منوی اصلی", callback_data="home")
    return kb.as_markup()


ADMIN_MENU_STYLE = {
    "a:np": "success",
    "a:st": "success",
    "a:pr": "primary",
    "a:lp": "primary",
    "a:dc": "primary",
    "a:ad": "primary",
    "a:wl": "primary",
    "a:refprod": "primary",
    "a:stat": "primary",
    "a:bc": "danger",
    "a:cfg": "primary",
}


def admin_menu():
    kb = InlineKeyboardBuilder()
    for text, data in [
        ("➕ محصول جدید", "a:np"),
        ("📦 افزودن موجودی", "a:st"),
        ("💲 تغییر قیمت", "a:pr"),
        ("🗂 محصولات", "a:lp"),
        ("🏷 کد تخفیف", "a:dc"),
        ("👤 ادمین‌ها", "a:ad"),
        ("💰 کیف پول کاربران", "a:wl"),
        ("🎁 جایزه رفرال", "a:refprod"),
        ("📊 آمار", "a:stat"),
        ("📢 پیام همگانی", "a:bc"),
        ("⚙️ تنظیمات", "a:cfg"),
    ]:
        kb.button(text=text, callback_data=data, style=ADMIN_MENU_STYLE.get(data))
    kb.adjust(2)
    return kb.as_markup()


def admin_back():
    kb = InlineKeyboardBuilder()
    kb.button(text="↩️ پنل ادمین", callback_data="a:menu")
    return kb.as_markup()


def product_picker(prefix, back_cb="a:menu", back_text="↩️ پنل ادمین"):
    kb = InlineKeyboardBuilder()
    for p in products_with_stock():
        kb.button(text=f"{p['name']} ({p['n']})", callback_data=f"{prefix}{p['id']}", style="primary")
    kb.button(text=back_text, callback_data=back_cb)
    kb.adjust(1)
    return kb.as_markup()


def category_picker(prefix, back_cb="a:menu"):
    kb = InlineKeyboardBuilder()
    for c in CATEGORIES:
        kb.button(text=c, callback_data=f"{prefix}{c}", style="primary")
    kb.button(text="↩️ بازگشت", callback_data=back_cb)
    kb.adjust(1)
    return kb.as_markup()


# ------------------------------------------------------------------- states


class Buy(StatesGroup):
    code = State()
    receipt = State()


class Adm(StatesGroup):
    np_cat = State()
    np_bulk = State()
    set_value = State()
    wallet_uid = State()
    wallet_amount = State()
    st_text = State()
    price_new = State()
    dc_code = State()
    dc_pct = State()
    dc_max = State()
    dc_days = State()
    add_admin = State()
    bc_text = State()


class AdminFilter(BaseFilter):
    async def __call__(self, event: Message | CallbackQuery) -> bool:
        return bool(event.from_user) and is_admin(event.from_user.id)


user = Router()
admin = Router()
admin.message.filter(AdminFilter())
admin.callback_query.filter(AdminFilter())

# ------------------------------------------------------------- user handlers


@user.message(CommandStart())
async def start(m: Message, state: FSMContext):
    await state.clear()
    is_new = register_user(m.from_user.id, m.from_user.full_name)
    payload = m.text.split(maxsplit=1)
    if is_new and len(payload) == 2 and payload[1].startswith("ref_"):
        ref_id = parse_int(payload[1][4:])
        if ref_id and ref_id != m.from_user.id and get_user(ref_id):
            run(
                "INSERT OR IGNORE INTO referrals(referred_id, referrer_id, created_at) VALUES (?,?,?)",
                (m.from_user.id, ref_id, int(time.time())),
            )
            await grant_referral_rewards(m.bot, ref_id)
    text = (
        cfg("welcome")
        .replace("{name}", m.from_user.first_name or "")
        .replace("{store}", cfg("store_name"))
    )
    await m.answer(html.escape(text), reply_markup=main_menu())


@user.callback_query(F.data == "home")
async def home(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await edit(cb, "منوی اصلی:", main_menu())
    await cb.answer()


@user.callback_query(F.data == "support")
async def support(cb: CallbackQuery):
    await edit(cb, f"💬 پشتیبانی: {html.escape(cfg('support'))}", home_kb())
    await cb.answer()


@user.callback_query(F.data == "rules")
async def rules(cb: CallbackQuery):
    await edit(cb, "📜 <b>قوانین فروشگاه</b>\n\n" + html.escape(cfg("rules")), home_kb())
    await cb.answer()


@user.callback_query(F.data == "my")
async def my_orders(cb: CallbackQuery):
    rows = fetch(
        """SELECT o.*, p.name AS pname,
                  (SELECT content FROM stock s WHERE s.order_id=o.id AND s.sold=1) AS content
           FROM orders o JOIN products p ON p.id=o.product_id
           WHERE o.user_id=? ORDER BY o.id DESC LIMIT 5""",
        (cb.from_user.id,),
    )
    if not rows:
        await edit(cb, "هنوز سفارشی نداری.", home_kb())
    else:
        parts = []
        for r in rows:
            line = (
                f"<b>#{r['id']}</b> {html.escape(r['pname'])} — "
                f"{r['final_price']:,} تومان\n{STATUS_FA.get(r['status'], r['status'])}"
            )
            if r["status"] == "delivered" and r["content"]:
                line += f"\n<code>{html.escape(r['content'])}</code>"
            parts.append(line)
        await edit(cb, "📦 آخرین سفارش‌های تو:\n\n" + "\n\n".join(parts), home_kb())
    await cb.answer()


@user.callback_query(F.data == "wallet")
async def wallet(cb: CallbackQuery):
    u = get_user(cb.from_user.id) or {"balance": 0}
    await edit(
        cb,
        f"💰 موجودی کیف پول تو: <b>{u['balance']:,} تومان</b>\n\n"
        f"برای شارژ یا برداشت کیف پول، به پشتیبانی پیام بده: {html.escape(cfg('support'))}",
        home_kb(),
    )
    await cb.answer()


@user.callback_query(F.data == "ref")
async def referral(cb: CallbackQuery):
    me = await cb.bot.get_me()
    u = get_user(cb.from_user.id) or {"ref_rewards_given": 0}
    n = fetch1("SELECT COUNT(*) c FROM referrals WHERE referrer_id=?", (cb.from_user.id,))["c"]
    left = REF_THRESHOLD - (n % REF_THRESHOLD)
    left = REF_THRESHOLD if left == REF_THRESHOLD and n == 0 else left
    await edit(
        cb,
        "🔗 <b>دعوت دوستان</b>\n\n"
        f"لینک اختصاصی تو:\n<code>https://t.me/{me.username}?start=ref_{cb.from_user.id}</code>\n\n"
        f"👥 زیرمجموعه‌های فعلی: {n} نفر\n"
        f"🎁 جایزه‌های دریافتی: {u['ref_rewards_given']}\n"
        f"با دعوت هر {REF_THRESHOLD} نفر، ۱ اکانت رایگان می‌گیری.",
        home_kb(),
    )
    await cb.answer()


@user.callback_query(F.data == "buy")
async def buy(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    expire_old_orders()
    if not await is_member(cb.bot, cb.from_user.id):
        kb = InlineKeyboardBuilder()
        kb.button(text="📢 عضویت در کانال", url=cfg("channel_link") or "https://t.me", style="primary")
        kb.button(text="✅ عضو شدم", callback_data="buy", style="success")
        kb.button(text="↩️ منوی اصلی", callback_data="home")
        kb.adjust(1)
        await edit(cb, "برای خرید، اول باید عضو کانال ما بشی 👇", kb.as_markup())
        await cb.answer()
        return
    cats = [c for c in CATEGORIES if any(p["active"] for p in products_with_stock(c))]
    if not cats:
        await edit(cb, "فعلاً محصولی موجود نیست.", home_kb())
        await cb.answer()
        return
    kb = InlineKeyboardBuilder()
    for c in cats:
        kb.button(text=c, callback_data=f"cat:{c}", style="primary")
    kb.button(text="↩️ منوی اصلی", callback_data="home")
    kb.adjust(1)
    await edit(cb, "🗂 دسته‌بندی مورد نظر رو انتخاب کن:", kb.as_markup())
    await cb.answer()


@user.callback_query(F.data.startswith("cat:"))
async def buy_category(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    expire_old_orders()
    cat = cb.data.split(":", 1)[1]
    prods = [p for p in products_with_stock(cat) if p["active"]]
    if not prods:
        await edit(cb, "فعلاً محصولی توی این دسته نیست.", home_kb())
        await cb.answer()
        return
    kb = InlineKeyboardBuilder()
    for p in prods:
        tag = "" if p["n"] > 0 else " (ناموجود)"
        kb.button(text=f"{p['name']} — {p['price']:,} تومان{tag}", callback_data=f"p:{p['id']}", style="primary")
    kb.button(text="↩️ بازگشت", callback_data="buy")
    kb.adjust(1)
    await edit(cb, f"🎮 محصولات «{cat}»:", kb.as_markup())
    await cb.answer()


@user.callback_query(F.data.startswith("p:"))
async def choose_product(cb: CallbackQuery, state: FSMContext):
    expire_old_orders()
    pid = int(cb.data.split(":")[1])
    p = next((x for x in products_with_stock() if x["id"] == pid and x["active"]), None)
    if not p or p["n"] == 0:
        await cb.answer("این محصول الان ناموجوده.", show_alert=True)
        return
    await state.update_data(pid=pid)
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ دارم", callback_data="dc:yes", style="success")
    kb.button(text="❌ ندارم", callback_data="dc:no", style="danger")
    kb.button(text="↩️ بازگشت", callback_data="buy")
    kb.adjust(2, 1)
    await edit(
        cb,
        f"<b>{html.escape(p['name'])}</b> — {p['price']:,} تومان\n\n🏷 کد تخفیف داری؟",
        kb.as_markup(),
    )
    await cb.answer()


@user.callback_query(F.data == "dc:yes")
async def discount_yes(cb: CallbackQuery, state: FSMContext):
    await state.set_state(Buy.code)
    kb = InlineKeyboardBuilder()
    kb.button(text="ادامه بدون کد", callback_data="dc:no", style="primary")
    await edit(cb, "کد تخفیفت رو بفرست:", kb.as_markup())
    await cb.answer()


@user.callback_query(F.data == "dc:no")
async def discount_no(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    await make_order(cb.message, cb.from_user.id, state, None)


@user.message(Buy.code, F.text)
async def discount_code(m: Message, state: FSMContext):
    code = m.text.strip().upper()
    d = get_discount(code)
    if not d:
        kb = InlineKeyboardBuilder()
        kb.button(text="ادامه بدون کد", callback_data="dc:no", style="primary")
        await m.answer("❌ این کد نامعتبره یا تموم شده. دوباره بفرست:", reply_markup=kb.as_markup())
        return
    await make_order(m, m.from_user.id, state, code)


async def make_order(target: Message, uid: int, state: FSMContext, code):
    data = await state.get_data()
    expire_old_orders()
    p = fetch1("SELECT * FROM products WHERE id=? AND active=1", (data.get("pid", 0),))
    if not p:
        await state.clear()
        await target.answer("محصول پیدا نشد. دوباره از منو انتخاب کن.", reply_markup=main_menu())
        return
    percent = 0
    if code:
        d = get_discount(code)
        if not d:
            code = None
        else:
            percent = d["percent"]
    final = p["price"] * (100 - percent) // 100
    oid = insert(
        """INSERT INTO orders(user_id, product_id, price, discount_code, percent,
                              final_price, status, created_at)
           VALUES (?,?,?,?,?,?,'pending',?)""",
        (uid, p["id"], p["price"], code, percent, final, int(time.time())),
    )
    got = run(
        """UPDATE stock SET order_id=? WHERE id=(
               SELECT id FROM stock WHERE product_id=? AND order_id IS NULL AND sold=0 LIMIT 1)""",
        (oid, p["id"]),
    )
    if got == 0:
        run("DELETE FROM orders WHERE id=?", (oid,))
        await state.clear()
        await target.answer("😕 همین الان تموم شد. یه پلن دیگه رو امتحان کن.", reply_markup=main_menu())
        return
    if final == 0:
        await state.clear()
        await deliver(target.bot, oid)
        return
    await state.set_state(Buy.receipt)
    await state.update_data(oid=oid)
    disc = f"\n🏷 تخفیف: {percent}٪ (کد {html.escape(code)})" if code else ""
    bal = (get_user(uid) or {"balance": 0})["balance"]
    kb = InlineKeyboardBuilder()
    if bal >= final:
        kb.button(text=f"💰 پرداخت از کیف پول ({bal:,})", callback_data=f"pw:{oid}", style="success")
    kb.button(text="❌ انصراف", callback_data=f"cx:{oid}", style="danger")
    kb.adjust(1)
    await target.answer(
        f"🧾 <b>سفارش #{oid}</b>\n"
        f"محصول: {html.escape(p['name'])}\n"
        f"قیمت: {p['price']:,} تومان{disc}\n"
        f"💰 <b>مبلغ قابل پرداخت: {final:,} تومان</b>\n\n"
        f"💳 شماره کارت:\n<code>{html.escape(cfg('card_number'))}</code>\n"
        f"به نام: {html.escape(cfg('card_owner'))}\n\n"
        f"بعد از واریز، <b>عکس رسید</b> رو همین‌جا بفرست، یا از کیف پولت پرداخت کن.\n"
        f"⏱ فرصت ارسال رسید: {PAY_TIMEOUT} دقیقه",
        reply_markup=kb.as_markup(),
    )


@user.callback_query(F.data.startswith("pw:"))
async def pay_wallet(cb: CallbackQuery, state: FSMContext):
    oid = int(cb.data.split(":")[1])
    o = fetch1("SELECT * FROM orders WHERE id=? AND status='pending'", (oid,))
    if not o or o["user_id"] != cb.from_user.id:
        await cb.answer("این سفارش دیگه فعال نیست.", show_alert=True)
        return
    if add_balance(cb.from_user.id, -o["final_price"]) is None:
        await cb.answer("موجودی کیف پولت کافی نیست.", show_alert=True)
        return
    run("UPDATE orders SET status='receipt_sent' WHERE id=?", (oid,))
    await state.clear()
    ok = await deliver(cb.bot, oid)
    if not ok:
        add_balance(cb.from_user.id, o["final_price"])  # برگردوندن پول
        run("UPDATE orders SET status='rejected' WHERE id=?", (oid,))
        await cb.answer("موجودی این محصول تموم شده؛ پولت برگشت به کیف پولت.", show_alert=True)
        return
    await cb.answer("✅ پرداخت شد.")


@user.callback_query(F.data.startswith("cx:"))
async def cancel_order(cb: CallbackQuery, state: FSMContext):
    oid = int(cb.data.split(":")[1])
    o = fetch1("SELECT user_id FROM orders WHERE id=?", (oid,))
    if o and o["user_id"] == cb.from_user.id:
        release_order(oid, "cancelled")
    await state.clear()
    await edit(cb, "سفارش لغو شد.", main_menu())
    await cb.answer()


@user.message(Buy.receipt, F.photo)
async def receipt(m: Message, state: FSMContext):
    data = await state.get_data()
    o = fetch1("SELECT * FROM orders WHERE id=?", (data.get("oid", 0),))
    if not o or o["status"] != "pending":
        await state.clear()
        await m.answer("این سفارش منقضی یا لغو شده. دوباره از منو سفارش بده.", reply_markup=main_menu())
        return
    file_id = m.photo[-1].file_id
    run("UPDATE orders SET status='receipt_sent', receipt=? WHERE id=?", (file_id, o["id"]))
    p = fetch1("SELECT name FROM products WHERE id=?", (o["product_id"],))
    caption = (
        f"🧾 <b>رسید جدید — سفارش #{o['id']}</b>\n"
        f"کاربر: <a href='tg://user?id={o['user_id']}'>{html.escape(m.from_user.full_name)}</a> "
        f"(<code>{o['user_id']}</code>)\n"
        f"محصول: {html.escape(p['name'])}\n"
        f"کد تخفیف: {html.escape(o['discount_code'] or '—')}\n"
        f"💰 مبلغ: {o['final_price']:,} تومان"
    )
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ تأیید و تحویل", callback_data=f"ao:{o['id']}", style="success")
    kb.button(text="❌ رد", callback_data=f"ar:{o['id']}", style="danger")
    for aid in admin_ids():
        try:
            await m.bot.send_photo(aid, file_id, caption=caption, reply_markup=kb.as_markup())
        except Exception as e:  # admin may not have started the bot yet
            logging.warning("cannot notify admin %s: %s", aid, e)
    await state.clear()
    await m.answer("✅ رسید ثبت شد. بعد از تأیید ادمین، سفارشت همین‌جا تحویل داده می‌شه.")


@user.message(Buy.receipt)
async def receipt_wrong(m: Message):
    await m.answer("لطفاً <b>عکس</b> رسید رو بفرست (یا انصراف بزن).")


# ------------------------------------------------------------ admin: orders


async def _mark_caption(cb: CallbackQuery, note: str):
    try:
        await cb.message.edit_caption(caption=(cb.message.caption or "") + f"\n\n{note}", reply_markup=None)
    except TelegramBadRequest:
        pass


@admin.callback_query(F.data.startswith("ao:"))
async def approve(cb: CallbackQuery):
    oid = int(cb.data.split(":")[1])
    claimed = run(
        "UPDATE orders SET status='approving' WHERE id=? AND status='receipt_sent'", (oid,)
    )
    if not claimed:
        await cb.answer("این سفارش قبلاً بررسی شده.", show_alert=True)
        await _mark_caption(cb, "ℹ️ قبلاً بررسی شده")
        return
    ok = await deliver(cb.bot, oid)
    if not ok:
        run("UPDATE orders SET status='receipt_sent' WHERE id=?", (oid,))
        await cb.answer("موجودی این سفارش پیدا نشد!", show_alert=True)
        return
    await _mark_caption(cb, f"✅ تأیید شد توسط {cb.from_user.id}")
    await cb.answer("تحویل داده شد.")


@admin.callback_query(F.data.startswith("ar:"))
async def reject(cb: CallbackQuery):
    oid = int(cb.data.split(":")[1])
    o = fetch1("SELECT user_id FROM orders WHERE id=?", (oid,))
    if not o or not release_order(oid, "rejected"):
        await cb.answer("این سفارش قبلاً بررسی شده.", show_alert=True)
        await _mark_caption(cb, "ℹ️ قبلاً بررسی شده")
        return
    try:
        await cb.bot.send_message(
            o["user_id"],
            f"❌ رسید سفارش #{oid} تأیید نشد.\nاگه فکر می‌کنی اشتباهه، به پشتیبانی پیام بده: "
            f"{html.escape(cfg('support'))}",
        )
    except Exception:
        pass
    await _mark_caption(cb, f"❌ رد شد توسط {cb.from_user.id}")
    await cb.answer("رد شد.")


# ------------------------------------------------------------- admin: panel


@admin.message(Command("admin"))
async def admin_cmd(m: Message, state: FSMContext):
    await state.clear()
    await m.answer("🛠 پنل ادمین", reply_markup=admin_menu())


@admin.callback_query(F.data == "a:menu")
async def admin_home(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await edit(cb, "🛠 پنل ادمین", admin_menu())
    await cb.answer()


# --- products
@admin.callback_query(F.data == "a:np")
async def np_start(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await edit(cb, "محصول‌ها توی کدوم دسته باشن؟", category_picker("a:npc:"))
    await cb.answer()


@admin.callback_query(F.data.startswith("a:npc:"))
async def np_category(cb: CallbackQuery, state: FSMContext):
    cat = cb.data.split(":", 2)[2]
    if cat not in CATEGORIES:
        await cb.answer()
        return
    await state.set_state(Adm.np_bulk)
    await state.update_data(category=cat)
    await edit(
        cb,
        f"دسته: <b>{cat}</b>\n\n"
        "محصول‌ها رو بفرست؛ <b>هر خط = یک محصول</b> به شکل:\n"
        "<code>اسم محصول | قیمت</code>\n\n"
        "مثال:\n"
        "<code>Game Pass Core - ۱ ماهه | 250000\n"
        "Game Pass Ultimate - ۱ ماهه | 700000</code>\n\n"
        "یکی یا چندتا رو با هم بفرست.",
        admin_back(),
    )
    await cb.answer()


@admin.message(Adm.np_bulk, F.text)
async def np_bulk(m: Message, state: FSMContext):
    category = (await state.get_data()).get("category", CATEGORIES[1])
    made, bad = [], []
    for line in m.text.splitlines():
        if not line.strip():
            continue
        name, sep, price_txt = line.rpartition("|")
        price = parse_int(price_txt)
        if not sep or not name.strip() or price is None or price < 0:
            bad.append(line.strip())
            continue
        insert("INSERT INTO products(name, price, category) VALUES (?,?,?)", (name.strip(), price, category))
        made.append(name.strip())
    if not made:
        await m.answer("هیچ خطی درست نبود. فرمت: <code>اسم | قیمت</code>. دوباره بفرست:")
        return
    await state.clear()
    text = f"✅ {len(made)} محصول ساخته شد."
    if bad:
        text += "\n⚠️ این خط‌ها رد شدن:\n" + "\n".join(html.escape(b) for b in bad)
    text += "\nحالا از «افزودن موجودی» کدها رو اضافه کن."
    await m.answer(text, reply_markup=admin_menu())


@admin.callback_query(F.data == "a:st")
async def st_pick(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await edit(cb, "موجودی برای کدوم محصول؟", product_picker("a:sp:"))
    await cb.answer()


@admin.callback_query(F.data.startswith("a:sp:"))
async def st_start(cb: CallbackQuery, state: FSMContext):
    await state.set_state(Adm.st_text)
    await state.update_data(pid=int(cb.data.split(":")[2]))
    await edit(cb, "کدها رو بفرست؛ <b>هر خط = یک آیتم</b> (چند خط با هم هم می‌شه):", admin_back())
    await cb.answer()


@admin.message(Adm.st_text, F.text)
async def st_text(m: Message, state: FSMContext):
    data = await state.get_data()
    lines = [x.strip() for x in m.text.splitlines() if x.strip()]
    for line in lines:
        insert("INSERT INTO stock(product_id, content) VALUES (?,?)", (data["pid"], line))
    await state.clear()
    await m.answer(f"✅ {len(lines)} آیتم اضافه شد.", reply_markup=admin_menu())


@admin.callback_query(F.data == "a:pr")
async def pr_pick(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await edit(cb, "قیمت کدوم محصول رو عوض کنم؟", product_picker("a:pp:"))
    await cb.answer()


@admin.callback_query(F.data.startswith("a:pp:"))
async def pr_start(cb: CallbackQuery, state: FSMContext):
    await state.set_state(Adm.price_new)
    await state.update_data(pid=int(cb.data.split(":")[2]))
    await edit(cb, "قیمت جدید (تومان، فقط عدد):", admin_back())
    await cb.answer()


@admin.message(Adm.price_new, F.text)
async def pr_set(m: Message, state: FSMContext):
    price = parse_int(m.text)
    if price is None or price < 0:
        await m.answer("فقط عدد بفرست.")
        return
    data = await state.get_data()
    run("UPDATE products SET price=? WHERE id=?", (price, data["pid"]))
    await state.clear()
    await m.answer("✅ قیمت عوض شد.", reply_markup=admin_menu())


@admin.callback_query(F.data == "a:lp")
async def list_products(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    prods = products_with_stock()
    if not prods:
        await edit(cb, "هنوز محصولی نساختی.", admin_back())
        await cb.answer()
        return
    kb = InlineKeyboardBuilder()
    lines = []
    for p in prods:
        state_txt = "فعال" if p["active"] else "غیرفعال"
        lines.append(
            f"#{p['id']} [{p['category']}] {html.escape(p['name'])} — {p['price']:,} — "
            f"موجودی {p['n']} — {state_txt}"
        )
        kb.button(text=f"🔁 {p['name']}", callback_data=f"a:tg:{p['id']}", style="primary")
        kb.button(text=f"🗑 {p['name']}", callback_data=f"a:del:{p['id']}", style="danger")
    kb.button(text="↩️ پنل ادمین", callback_data="a:menu")
    kb.adjust(*([2] * len(prods)), 1)
    await edit(cb, "🗂 محصولات (🔁 = فعال/غیرفعال، 🗑 = حذف):\n\n" + "\n".join(lines), kb.as_markup())
    await cb.answer()


@admin.callback_query(F.data.startswith("a:tg:"))
async def toggle_product(cb: CallbackQuery, state: FSMContext):
    run("UPDATE products SET active=1-active WHERE id=?", (int(cb.data.split(":")[2]),))
    await list_products(cb, state)


@admin.callback_query(F.data.startswith("a:del:"))
async def delete_ask(cb: CallbackQuery):
    pid = int(cb.data.split(":")[2])
    p = fetch1("SELECT * FROM products WHERE id=? AND deleted=0", (pid,))
    if not p:
        await cb.answer("این محصول پیدا نشد.", show_alert=True)
        return
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ آره، حذف کن", callback_data=f"a:delok:{pid}", style="danger")
    kb.button(text="↩️ نه، برگرد", callback_data="a:lp")
    kb.adjust(1)
    await edit(
        cb,
        f"مطمئنی می‌خوای <b>{html.escape(p['name'])}</b> حذف بشه؟\n"
        "موجودی‌های فروخته‌نشده‌ش هم پاک می‌شن. (سفارش‌های قبلی سر جاشون می‌مونن)",
        kb.as_markup(),
    )
    await cb.answer()


@admin.callback_query(F.data.startswith("a:delok:"))
async def delete_do(cb: CallbackQuery, state: FSMContext):
    pid = int(cb.data.split(":")[2])
    run("UPDATE products SET deleted=1, active=0 WHERE id=?", (pid,))
    run("DELETE FROM stock WHERE product_id=? AND order_id IS NULL AND sold=0", (pid,))
    await cb.answer("حذف شد.")
    await list_products(cb, state)


# --- wallet (کیف پول کاربران)
@admin.callback_query(F.data == "a:wl")
async def wl_start(cb: CallbackQuery, state: FSMContext):
    await state.set_state(Adm.wallet_uid)
    await edit(cb, "آیدی عددی کاربر رو بفرست:", admin_back())
    await cb.answer()


@admin.message(Adm.wallet_uid, F.text)
async def wl_uid(m: Message, state: FSMContext):
    uid = parse_int(m.text)
    u = get_user(uid) if uid else None
    if not u:
        await m.answer("این کاربر پیدا نشد (باید یه بار ربات رو /start کرده باشه). دوباره بفرست:")
        return
    await state.clear()
    kb = InlineKeyboardBuilder()
    kb.button(text="➕ افزایش موجودی", callback_data=f"wl:add:{uid}", style="success")
    kb.button(text="➖ برداشت", callback_data=f"wl:sub:{uid}", style="danger")
    kb.button(text="↩️ پنل ادمین", callback_data="a:menu")
    kb.adjust(2, 1)
    await m.answer(
        f"👤 {html.escape(u['name'] or '')} (<code>{uid}</code>)\n💰 موجودی: {u['balance']:,} تومان",
        reply_markup=kb.as_markup(),
    )


@admin.callback_query(F.data.startswith("wl:"))
async def wl_op(cb: CallbackQuery, state: FSMContext):
    _, op, uid = cb.data.split(":")
    await state.set_state(Adm.wallet_amount)
    await state.update_data(op=op, uid=int(uid))
    word = "افزایش" if op == "add" else "برداشت"
    await edit(cb, f"مبلغ {word} (تومان، فقط عدد):", admin_back())
    await cb.answer()


@admin.message(Adm.wallet_amount, F.text)
async def wl_amount(m: Message, state: FSMContext):
    amt = parse_int(m.text)
    if amt is None or amt <= 0:
        await m.answer("یه عدد مثبت بفرست.")
        return
    data = await state.get_data()
    delta = amt if data["op"] == "add" else -amt
    new_bal = add_balance(data["uid"], delta)
    if new_bal is None:
        await m.answer("موجودی کاربر برای این برداشت کافی نیست.")
        return
    await state.clear()
    try:
        word = "افزایش" if delta > 0 else "کاهش"
        await m.bot.send_message(
            data["uid"], f"💰 موجودی کیف پولت {amt:,} تومان {word} پیدا کرد.\nموجودی فعلی: {new_bal:,} تومان"
        )
    except Exception:
        pass
    await m.answer(f"✅ انجام شد. موجودی جدید: {new_bal:,} تومان", reply_markup=admin_menu())


# --- referral reward product
@admin.callback_query(F.data == "a:refprod")
async def ref_prod(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    cur = fetch1("SELECT name FROM products WHERE id=? AND deleted=0", (parse_int(cfg("ref_reward_product")) or 0,))
    kb = InlineKeyboardBuilder()
    for p in products_with_stock():
        kb.button(text=f"{p['name']} ({p['n']})", callback_data=f"a:rp:{p['id']}", style="primary")
    kb.button(text="⛔ خاموش کردن جایزه", callback_data="a:rp:0", style="danger")
    kb.button(text="↩️ پنل ادمین", callback_data="a:menu")
    kb.adjust(1)
    await edit(
        cb,
        f"🎁 جایزه‌ی رفرال (هر {REF_THRESHOLD} زیرمجموعه = ۱ آیتم از محصولی که انتخاب کنی)\n"
        f"فعلی: {html.escape(cur['name']) if cur else 'خاموش'}",
        kb.as_markup(),
    )
    await cb.answer()


@admin.callback_query(F.data.startswith("a:rp:"))
async def ref_prod_set(cb: CallbackQuery, state: FSMContext):
    pid = int(cb.data.split(":")[2])
    set_cfg("ref_reward_product", str(pid) if pid else "")
    await cb.answer("ذخیره شد.")
    await ref_prod(cb, state)


# --- discount codes
@admin.callback_query(F.data == "a:dc")
async def dc_menu(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    kb = InlineKeyboardBuilder()
    kb.button(text="➕ ساخت کد تخفیف", callback_data="a:dcn", style="success")
    kb.button(text="📋 لیست / حذف کدها", callback_data="a:dcl", style="primary")
    kb.button(text="↩️ پنل ادمین", callback_data="a:menu")
    kb.adjust(1)
    await edit(cb, "🏷 کد تخفیف:", kb.as_markup())
    await cb.answer()


@admin.callback_query(F.data == "a:dcn")
async def dc_new(cb: CallbackQuery, state: FSMContext):
    await state.set_state(Adm.dc_code)
    await edit(cb, "متن کد رو بفرست (فقط حروف انگلیسی و عدد، مثلاً SUMMER20):", admin_back())
    await cb.answer()


@admin.message(Adm.dc_code, F.text)
async def dc_code(m: Message, state: FSMContext):
    code = m.text.strip().upper()
    if not (code.isascii() and code.isalnum()):
        await m.answer("فقط حروف انگلیسی و عدد، بدون فاصله.")
        return
    if fetch1("SELECT 1 FROM discounts WHERE code=?", (code,)):
        await m.answer("این کد از قبل هست. یه کد دیگه بفرست.")
        return
    await state.update_data(code=code)
    await state.set_state(Adm.dc_pct)
    await m.answer("درصد تخفیف (۱ تا ۱۰۰):")


@admin.message(Adm.dc_pct, F.text)
async def dc_pct(m: Message, state: FSMContext):
    pct = parse_int(m.text)
    if pct is None or not 1 <= pct <= 100:
        await m.answer("یه عدد بین ۱ تا ۱۰۰ بفرست.")
        return
    await state.update_data(pct=pct)
    await state.set_state(Adm.dc_max)
    await m.answer("حداکثر تعداد استفاده؟ (عدد؛ ۰ = نامحدود)")


@admin.message(Adm.dc_max, F.text)
async def dc_max(m: Message, state: FSMContext):
    n = parse_int(m.text)
    if n is None or n < 0:
        await m.answer("یه عدد بفرست (۰ = نامحدود).")
        return
    await state.update_data(max_uses=n)
    await state.set_state(Adm.dc_days)
    await m.answer("چند روز اعتبار داشته باشه؟ (عدد؛ ۰ = بدون انقضا)")


@admin.message(Adm.dc_days, F.text)
async def dc_days(m: Message, state: FSMContext):
    days = parse_int(m.text)
    if days is None or days < 0:
        await m.answer("یه عدد بفرست (۰ = بدون انقضا).")
        return
    data = await state.get_data()
    expires = int(time.time()) + days * 86400 if days else None
    insert(
        "INSERT INTO discounts(code, percent, max_uses, expires_at) VALUES (?,?,?,?)",
        (data["code"], data["pct"], data["max_uses"], expires),
    )
    await state.clear()
    await m.answer(
        f"✅ کد <code>{data['code']}</code> ساخته شد: {data['pct']}٪ تخفیف.",
        reply_markup=admin_menu(),
    )


@admin.callback_query(F.data == "a:dcl")
async def dc_list(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    rows = fetch("SELECT * FROM discounts WHERE active=1 ORDER BY rowid DESC LIMIT 30")
    if not rows:
        await edit(cb, "کد فعالی نداری.", admin_back())
        await cb.answer()
        return
    kb = InlineKeyboardBuilder()
    lines = []
    for d in rows:
        cap = "∞" if d["max_uses"] == 0 else d["max_uses"]
        exp = "بدون انقضا" if not d["expires_at"] else time.strftime("%Y-%m-%d", time.localtime(d["expires_at"]))
        lines.append(f"<code>{d['code']}</code> — {d['percent']}٪ — استفاده {d['used']}/{cap} — {exp}")
        kb.button(text=f"🗑 حذف {d['code']}", callback_data=f"a:dcd:{d['code']}", style="danger")
    kb.button(text="↩️ پنل ادمین", callback_data="a:menu")
    kb.adjust(1)
    await edit(cb, "🏷 کدهای فعال:\n\n" + "\n".join(lines), kb.as_markup())
    await cb.answer()


@admin.callback_query(F.data.startswith("a:dcd:"))
async def dc_delete(cb: CallbackQuery, state: FSMContext):
    run("UPDATE discounts SET active=0 WHERE code=?", (cb.data.split(":")[2],))
    await dc_list(cb, state)


# --- admins
@admin.callback_query(F.data == "a:ad")
async def admins_menu(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    kb = InlineKeyboardBuilder()
    lines = []
    for a in fetch("SELECT id FROM admins ORDER BY rowid"):
        tag = " (ادمین اصلی)" if a["id"] == SUPER_ADMIN else ""
        lines.append(f"• <code>{a['id']}</code>{tag}")
        if cb.from_user.id == SUPER_ADMIN and a["id"] != SUPER_ADMIN:
            kb.button(text=f"🗑 حذف {a['id']}", callback_data=f"a:adr:{a['id']}", style="danger")
    kb.button(text="➕ افزودن ادمین", callback_data="a:adn", style="success")
    kb.button(text="↩️ پنل ادمین", callback_data="a:menu")
    kb.adjust(1)
    await edit(cb, "👤 ادمین‌ها:\n\n" + "\n".join(lines), kb.as_markup())
    await cb.answer()


@admin.callback_query(F.data == "a:adn")
async def admin_add_start(cb: CallbackQuery, state: FSMContext):
    await state.set_state(Adm.add_admin)
    await edit(
        cb,
        "آیدی عددی تلگرام ادمین جدید رو بفرست.\n"
        "(اون شخص باید یه بار ربات رو /start کرده باشه تا رسیدها براش برسه.)",
        admin_back(),
    )
    await cb.answer()


@admin.message(Adm.add_admin, F.text)
async def admin_add(m: Message, state: FSMContext):
    uid = parse_int(m.text)
    if uid is None or uid <= 0:
        await m.answer("فقط آیدی عددی بفرست.")
        return
    run("INSERT OR IGNORE INTO admins(id, added_by) VALUES (?,?)", (uid, m.from_user.id))
    await state.clear()
    await m.answer(f"✅ ادمین <code>{uid}</code> اضافه شد.", reply_markup=admin_menu())


@admin.callback_query(F.data.startswith("a:adr:"))
async def admin_remove(cb: CallbackQuery, state: FSMContext):
    uid = int(cb.data.split(":")[2])
    if cb.from_user.id != SUPER_ADMIN or uid == SUPER_ADMIN:
        await cb.answer("فقط ادمین اصلی می‌تونه حذف کنه.", show_alert=True)
        return
    run("DELETE FROM admins WHERE id=?", (uid,))
    await admins_menu(cb, state)


# --- settings (rules, texts, card, channel ...)
SETTINGS = [
    ("rules", "📜 قوانین"),
    ("welcome", "👋 پیام خوش‌آمد"),
    ("support", "💬 آیدی پشتیبانی"),
    ("card_number", "💳 شماره کارت"),
    ("card_owner", "👤 نام صاحب کارت"),
    ("store_name", "🏪 اسم فروشگاه"),
    ("channel_link", "📢 لینک کانال"),
    ("channel_id", "🔒 آیدی عددی کانال (عضویت اجباری)"),
]
SETTING_LABEL = dict(SETTINGS)


@admin.callback_query(F.data == "a:cfg")
async def cfg_menu(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    kb = InlineKeyboardBuilder()
    for key, label in SETTINGS:
        kb.button(text=label, callback_data=f"a:s:{key}", style="primary")
    kb.button(text="↩️ پنل ادمین", callback_data="a:menu")
    kb.adjust(1)
    await edit(cb, "⚙️ کدوم مورد رو می‌خوای عوض کنی؟", kb.as_markup())
    await cb.answer()


@admin.callback_query(F.data.startswith("a:s:"))
async def cfg_edit(cb: CallbackQuery, state: FSMContext):
    key = cb.data.split(":")[2]
    if key not in SETTING_LABEL:
        await cb.answer()
        return
    await state.set_state(Adm.set_value)
    await state.update_data(key=key)
    hint = ""
    if key == "welcome":
        hint = "\n\n(می‌تونی {name} برای اسم کاربر و {store} برای اسم فروشگاه بذاری)"
    elif key == "channel_link":
        hint = "\n\n(برای پاک کردن دکمه‌ی کانال، فقط - بفرست)"
    elif key == "channel_id":
        hint = (
            "\n\nآیدی عددی کانال (شبیه -1001234567890). ربات باید توی کانال ادمین باشه. "
            "وقتی پر باشه، کاربر برای خرید باید عضو کانال باشه. برای خاموش کردن، - بفرست."
        )
    current = html.escape(cfg(key)) or "—"
    await edit(
        cb,
        f"{SETTING_LABEL[key]}\n\nمقدار فعلی:\n{current}\n\nمقدار جدید رو بفرست:{hint}",
        admin_back(),
    )
    await cb.answer()


@admin.message(Adm.set_value, F.text)
async def cfg_save(m: Message, state: FSMContext):
    key = (await state.get_data()).get("key")
    if key not in SETTING_LABEL:
        await state.clear()
        return
    value = m.text.strip()
    if key in ("channel_link", "channel_id") and value == "-":
        value = ""
    elif key == "channel_link" and not value.startswith("http"):
        await m.answer("لینک باید با https:// شروع بشه.")
        return
    elif key == "channel_id":
        n = parse_int(value)
        if n is None:
            await m.answer("آیدی عددی بفرست (مثل -1001234567890).")
            return
        value = str(n)
    if len(value) > 3500:
        await m.answer("خیلی طولانیه؛ کوتاه‌ترش کن.")
        return
    set_cfg(key, value)
    await state.clear()
    await m.answer("✅ ذخیره شد.", reply_markup=admin_menu())


# --- stats + broadcast
@admin.callback_query(F.data == "a:stat")
async def stats(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    day_ago = int(time.time()) - 86400
    tot = fetch1("SELECT COUNT(*) c, COALESCE(SUM(final_price),0) s FROM orders WHERE status='delivered'")
    day = fetch1(
        "SELECT COUNT(*) c, COALESCE(SUM(final_price),0) s FROM orders "
        "WHERE status='delivered' AND delivered_at>?",
        (day_ago,),
    )
    users = fetch1("SELECT COUNT(*) c FROM users")["c"]
    waiting = fetch1("SELECT COUNT(*) c FROM orders WHERE status='receipt_sent'")["c"]
    await edit(
        cb,
        "📊 <b>آمار</b>\n\n"
        f"کاربران: {users}\n"
        f"رسیدهای منتظر تأیید: {waiting}\n"
        f"فروش ۲۴ ساعت اخیر: {day['c']} سفارش — {day['s']:,} تومان\n"
        f"کل فروش: {tot['c']} سفارش — {tot['s']:,} تومان",
        admin_back(),
    )
    await cb.answer()


@admin.callback_query(F.data == "a:bc")
async def bc_start(cb: CallbackQuery, state: FSMContext):
    await state.set_state(Adm.bc_text)
    await edit(cb, "متن پیام همگانی رو بفرست (بلافاصله برای همه‌ی کاربرها ارسال می‌شه):", admin_back())
    await cb.answer()


@admin.message(Adm.bc_text, F.text)
async def bc_send(m: Message, state: FSMContext):
    await state.clear()
    ok = fail = 0
    for u in fetch("SELECT id FROM users"):
        try:
            await m.bot.send_message(u["id"], m.text, parse_mode=None)
            ok += 1
        except Exception:
            fail += 1
        await asyncio.sleep(0.05)
    await m.answer(f"📢 ارسال شد: {ok} موفق، {fail} ناموفق.", reply_markup=admin_menu())


# --------------------------------------------------------------------- main


async def main():
    logging.basicConfig(level=logging.INFO)
    if not BOT_TOKEN:
        raise SystemExit("BOT_TOKEN در فایل .env تنظیم نشده.")
    init_db()
    bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(admin)
    dp.include_router(user)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())

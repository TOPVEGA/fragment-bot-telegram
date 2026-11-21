import asyncio
import html
import json
import logging
import os
import re
import time
from contextlib import suppress


import aiosqlite
import aiohttp

LOOP = asyncio.new_event_loop()
asyncio.set_event_loop(LOOP)

from pyrogram import Client, filters, idle
from pyrogram.enums import ParseMode
from pyrogram.errors import MessageNotModified, RPCError, FloodWait
from pyrogram.types import (
    CallbackQuery,
    InlineKeyboardButton as Btn,
    InlineKeyboardMarkup as Markup,
    Message,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("tools-bot")


class Config:
    API_ID = int(os.getenv("API_ID", "8186557"))
    API_HASH = os.getenv("API_HASH", "efd77b34c69c164ce158037ff5a0d117")
    OWNER_ID = int(os.getenv("OWNER_ID", "481322119"))
    DB_PATH = os.getenv("DB_PATH", "tools_bot.db")

    vega = os.getenv("vega", os.getenv("BOT_TOKEN", "........"))
    topvega = os.getenv(
        "topvega",
        os.getenv(
            "FRAGMENT_COOKIE",
            "......",
        ),
    )
    FRAGMENT_HASH = os.getenv("FRAGMENT_HASH", "")
    kim = os.getenv(
        "kim",
        os.getenv(
            "TON_API_KEY",
            ".......",
        ),
    )

    TON_WALLET_ADDRESS = os.getenv("OPERATIONAL_WALLET_ADDRESS", "...........")
    TON_NETWORK = os.getenv("TON_NETWORK", "mainnet")


app = Client(
    "tools_bot",
    api_id=Config.API_ID,
    api_hash=Config.API_HASH,
    bot_token=Config.vega,
    parse_mode=ParseMode.HTML,
    workers=4,
)

BOT_ID = 0
DEV_NAME = ""
DEV_USERNAME = ""
BG_TASKS: set[asyncio.Task] = set()

TON_HEADERS = {"Authorization": f"Bearer {Config.kim}"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS states(
    user_id INTEGER PRIMARY KEY,
    action TEXT NOT NULL,
    data TEXT,
    updated INTEGER);
CREATE TABLE IF NOT EXISTS settings(
    key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS watchlist(
    user_id INTEGER,
    address TEXT,
    last_tx TEXT,
    added INTEGER,
    PRIMARY KEY(user_id, address));
CREATE TABLE IF NOT EXISTS history(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    tool TEXT,
    result TEXT,
    created INTEGER);
"""


class DB:
    def __init__(self, path: str):
        self.path = path
        self.conn: aiosqlite.Connection | None = None

    async def start(self):
        self.conn = await aiosqlite.connect(self.path)
        self.conn.row_factory = aiosqlite.Row
        await self.conn.executescript(SCHEMA)
        await self.conn.commit()

    async def close(self):
        if self.conn:
            await self.conn.close()

    async def one(self, q: str, args=()):
        async with self.conn.execute(q, args) as c:
            return await c.fetchone()

    async def all(self, q: str, args=()):
        async with self.conn.execute(q, args) as c:
            return await c.fetchall()

    async def run(self, q: str, args=()):
        async with self.conn.execute(q, args) as c:
            rid = c.lastrowid
            await self.conn.commit()
            return c.rowcount, rid

    async def get_state(self, uid: int) -> dict | None:
        row = await self.one("SELECT action,data FROM states WHERE user_id=?", (uid,))
        if not row:
            return None
        data = json.loads(row["data"]) if row["data"] else {}
        data["action"] = row["action"]
        return data

    async def set_state(self, uid: int, state: dict | None):
        if state is None:
            await self.run("DELETE FROM states WHERE user_id=?", (uid,))
            return
        action = state.get("action", "")
        data = {k: v for k, v in state.items() if k != "action"}
        await self.run(
            "INSERT INTO states(user_id,action,data,updated) VALUES(?,?,?,?) "
            "ON CONFLICT(user_id) DO UPDATE SET action=excluded.action, "
            "data=excluded.data, updated=excluded.updated",
            (uid, action, json.dumps(data) if data else None, int(time.time())),
        )

    async def add_history(self, uid: int, tool: str, result: str):
        await self.run(
            "INSERT INTO history(user_id,tool,result,created) VALUES(?,?,?,?)",
            (uid, tool, result[:4000], int(time.time())),
        )
        await self.run(
            "DELETE FROM history WHERE user_id=? AND id NOT IN "
            "(SELECT id FROM history WHERE user_id=? ORDER BY id DESC LIMIT 10)",
            (uid, uid),
        )

    async def get_history(self, uid: int):
        return await self.all(
            "SELECT tool,result,created FROM history WHERE user_id=? ORDER BY id DESC LIMIT 10",
            (uid,),
        )

    async def watch_add(self, uid: int, address: str):
        await self.run(
            "INSERT OR IGNORE INTO watchlist(user_id,address,last_tx,added) VALUES(?,?,?,?)",
            (uid, address, "", int(time.time())),
        )

    async def watch_remove(self, uid: int, address: str):
        await self.run("DELETE FROM watchlist WHERE user_id=? AND address=?", (uid, address))

    async def watch_list(self, uid: int):
        return await self.all("SELECT address,last_tx FROM watchlist WHERE user_id=?", (uid,))

    async def watch_all(self):
        return await self.all("SELECT user_id,address,last_tx FROM watchlist")

    async def watch_update(self, uid: int, address: str, last_tx: str):
        await self.run(
            "UPDATE watchlist SET last_tx=? WHERE user_id=? AND address=?",
            (last_tx, uid, address),
        )

    async def users_count(self):
        row = await self.one("SELECT COUNT(*) as c FROM states")
        return row["c"] if row else 0


db = DB(Config.DB_PATH)


def bq(s: str) -> str:
    return f"{s}"


def esc(s) -> str:
    return html.escape(str(s or ""))


BACK = Markup([[Btn("رجوع", callback_data="home")]])
CANCEL = Markup([[Btn("إلغاء", callback_data="home")]])


def main_menu() -> Markup:
    dev_link = f"https://t.me/{DEV_USERNAME}" if DEV_USERNAME else "https://t.me/telegram"
    dev_text = f"{DEV_NAME}" if DEV_NAME else "TOPVEGA"
    rows = [
        [Btn("كشف محفظة", callback_data="tool:wallet"), Btn("بروفايل NFT", callback_data="tool:nft_profile")],
        [Btn("فحص يوزر/هدية", callback_data="tool:username_scan"), Btn("فحص Gift", callback_data="tool:fragment")],
        [Btn("بحث شامل", callback_data="tool:search"), Btn("تحويل عملات", callback_data="tool:convert")],
        [Btn("أسعار العملات", callback_data="tool:prices"), Btn("أكواد الكاش", callback_data="tool:cash")],
        [Btn("شراء نجوم", callback_data="tool:buy_stars"), Btn("النجوم و بريميوم", callback_data="tool:stars")],
        [Btn("عمولة الوساطة", callback_data="tool:commission"), Btn("تتبع محفظة", callback_data="tool:track")],
        [Btn("تعزيز القنوات", callback_data="tool:boost"), Btn("مراقبة محفظة", callback_data="tool:watch")],
        [Btn("آخر فحوصاتي", callback_data="tool:history")],
        [Btn(dev_text, url=dev_link)],
        [Btn("رجوع", callback_data="home")]
    ]
    return Markup(rows)


async def send_home(client: Client, chat_id: int, cb: CallbackQuery | None = None):
    caption = (
        "<b>لوحة التحكم والقائمة الرئيسية</b>\n\n"
        ""
        "• <b>بروفايل NFT:</b> فحص مقتنيات وتفاصيل المعرفات والمحافظ\n"
        "• <b>كشف محفظة:</b> فحص الرصيد والشبكات المختلفة\n"
        "• <b>فحص يوزر/هدية:</b> فحص اليوزرات والهدايا والأرقام\n"
        "• <b>فحص Gift:</b> جلب بيانات أي Gift من رابط Fragment\n"
        "• <b>بحث شامل:</b> البحث عن يوزرات، أرقام، وهدايا\n"
        "• <b>تحويل عملات:</b> حاسبة التحويل المباشرة للأسعار الحية\n"
        "• <b>أسعار العملات:</b> متابعة حية لأسعار TON والدولار\n"
        "• <b>أكواد الكاش:</b> أكواد نسخ وتحويل المحافظ السريعة\n"
        "• <b>شراء نجوم:</b> شراء Stars عبر TON/USDT\n"
        "• <b>النجوم وبريميوم:</b> فحص وشراء نجوم تليجرام\n"
        "• <b>عمولة الوساطة:</b> حاسبة عمولة ووساطة الضمان\n"
        "• <b>تتبع محفظة:</b> المراقبة اللحظية لتعاملات TON\n"
        "• <b>مراقبة محفظة:</b> تنبيهات تلقائية عند التحويلات\n"
        "• <b>تعزيز القنوات:</b> حاسبة مستويات Boost المطلوبة\n"
        ""
    )
    markup = main_menu()

    if cb is not None:
        try:
            if cb.message.photo:
                return await cb.message.edit_caption(caption=caption, reply_markup=markup)
            return await cb.message.edit_text(caption, reply_markup=markup)
        except MessageNotModified:
            return
        except FloodWait as e:
            await asyncio.sleep(e.value)
            return
        except RPCError:
            pass

    try:
        await client.send_message(chat_id, caption, reply_markup=markup)
    except RPCError:
        pass


async def screen(client: Client, cb: CallbackQuery, text: str, markup=None):
    try:
        if cb.message.photo:
            return await cb.message.edit_caption(caption=text, reply_markup=markup)
        return await cb.message.edit_text(text, reply_markup=markup)
    except MessageNotModified:
        return
    except FloodWait as e:
        await asyncio.sleep(e.value)
        return
    except RPCError:
        with suppress(RPCError):
            await cb.message.delete()
        return await client.send_message(cb.message.chat.id, text, reply_markup=markup)


ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")

_live_price_cache: dict | None = None
_live_price_cache_expires_at = 0.0
_live_price_cache_failed_until = 0.0
_live_price_cache_lock = asyncio.Lock()

_TON_API_SEM = asyncio.Semaphore(2)
_FRAGMENT_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

TON_ADDR_RE = re.compile(r"^(?:EQ|UQ|0:)[A-Za-z0-9_\-:]{20,64}$")


async def try_sources(sources, label):
    for url, extract in sources:
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=6)) as s:
                async with s.get(url) as r:
                    if r.status != 200:
                        continue
                    data = await r.json()
                    val = extract(data)
                    if val:
                        return float(val)
        except Exception as e:
            log.warning("price source failed %s: %s", label, e)
    raise RuntimeError(f"all {label} sources failed")


async def _fetch_uncached() -> dict:
    ton_sources = [
        ("https://tonapi.io/v2/rates?tokens=ton&currencies=usd",
         lambda d: d["rates"]["TON"]["prices"]["USD"]),
        ("https://api.coingecko.com/api/v3/simple/price?ids=the-open-network&vs_currencies=usd",
         lambda d: d["the-open-network"]["usd"]),
    ]
    fx_sources = [
        ("https://open.er-api.com/v6/latest/USD", lambda d: d["rates"]["EGP"]),
        ("https://api.exchangerate-api.com/v4/latest/USD", lambda d: d["rates"]["EGP"]),
    ]
    ton_usd, usd_egp = await asyncio.gather(
        try_sources(ton_sources, "TON"),
        try_sources(fx_sources, "EGP"),
    )
    return {
        "ton_usd": ton_usd,
        "usd_egp": usd_egp,
        "ton_egp": ton_usd * usd_egp,
        "gram_usd": ton_usd,
    }


async def fetch_live_prices(force: bool = False) -> dict | None:
    global _live_price_cache, _live_price_cache_expires_at, _live_price_cache_failed_until
    now = time.monotonic()
    if not force:
        if _live_price_cache is not None and now < _live_price_cache_expires_at:
            return dict(_live_price_cache)
        if now < _live_price_cache_failed_until:
            return None
    async with _live_price_cache_lock:
        now = time.monotonic()
        if not force and _live_price_cache is not None and now < _live_price_cache_expires_at:
            return dict(_live_price_cache)
        try:
            prices = await asyncio.wait_for(_fetch_uncached(), timeout=8)
        except Exception:
            prices = None
        if prices is not None:
            _live_price_cache = dict(prices)
            _live_price_cache_expires_at = time.monotonic() + 60
            _live_price_cache_failed_until = 0.0
        else:
            _live_price_cache_failed_until = time.monotonic() + 5
        return dict(prices) if prices is not None else None


_ALIAS = {
    "ton": ("ton", "t", "تون", "ت"),
    "usd": ("usd", "usdt", "u", "$", "dollar", "دولار", "دولر"),
    "egp": ("egp", "ج", "جنيه", "جنية", "مصري", "مصرى"),
    "gram": ("gram", "g", "جرام", "غرام"),
}

_LABELS = {"ton": "TON", "usd": "USD", "egp": "EGP", "gram": "GRAM"}


def _currency_of(unit: str) -> str | None:
    u = unit.lower()
    for key, aliases in _ALIAS.items():
        if u in aliases:
            return key
    return None


def parse_amount_query(text: str) -> tuple[float, str] | None:
    if not text:
        return None
    value = text.strip().lower().translate(ARABIC_DIGITS)
    value = value.removeprefix("/").strip()
    value = re.sub(r"^(?:price|سعر|بورصة|بورصه|عملات|عملة)\s+", "", value)
    m = re.fullmatch(r"(\d+(?:[.,]\d+)?)\s*([a-zA-Z$آ-ي]+)", value)
    if m:
        amount = float(m.group(1).replace(",", "."))
        unit = _currency_of(m.group(2))
        if unit:
            return amount, unit
    m = re.fullmatch(r"([a-zA-Zآ-ي]+)\s*(\d+(?:[.,]\d+)?)", value)
    if m:
        unit = _currency_of(m.group(1))
        if unit:
            return float(m.group(2).replace(",", ".")), unit
    m = re.fullmatch(r"(ton|usd|usdt|egp|gram|تون|دولار|جنيه|جرام)", value)
    if m:
        unit = _currency_of(m.group(1))
        if unit:
            return 1.0, unit
    return None


def _fmt(key: str, value: float) -> str:
    decimals = {"usd": 2, "ton": 4, "egp": 2, "gram": 4}.get(key, 2)
    return f"{value:,.{decimals}f}".rstrip("0").rstrip(".")


def build_values(amount: float, base: str, prices: dict) -> dict:
    if base == "ton":
        return {
            "ton": amount,
            "usd": amount * prices["ton_usd"],
            "egp": amount * prices["ton_egp"],
            "gram": amount * (prices["ton_usd"] / prices["gram_usd"]) if prices["gram_usd"] else amount,
        }
    if base == "usd":
        return {
            "ton": amount / prices["ton_usd"] if prices["ton_usd"] else 0,
            "usd": amount,
            "egp": amount * prices["usd_egp"],
            "gram": amount / prices["gram_usd"] if prices["gram_usd"] else 0,
        }
    if base == "egp":
        return {
            "ton": amount / prices["ton_egp"] if prices["ton_egp"] else 0,
            "usd": amount / prices["usd_egp"] if prices["usd_egp"] else 0,
            "egp": amount,
            "gram": (amount / prices["usd_egp"]) / prices["gram_usd"] if prices["gram_usd"] and prices["usd_egp"] else 0,
        }
    if base == "gram":
        return {
            "ton": (amount * prices["gram_usd"]) / prices["ton_usd"] if prices["ton_usd"] else 0,
            "usd": amount * prices["gram_usd"],
            "egp": amount * prices["gram_usd"] * prices["usd_egp"],
            "gram": amount,
        }
    return {}


async def fetch_wallet_full(session: aiohttp.ClientSession, address: str) -> dict:
    address = address.strip().replace(" ", "")
    account_url = f"https://tonapi.io/v2/accounts/{address}"

    async with _TON_API_SEM:
        async with session.get(account_url, headers=TON_HEADERS) as r:
            if r.status != 200:
                if r.status == 404:
                    raise ValueError("العنوان غير موجود أو المحفظة غير مفعلة.")
                raise ValueError(f"خطأ من الخادم (كود: {r.status})")
            acct = await r.json()

    async with _TON_API_SEM:
        async with session.get(
            f"https://tonapi.io/v2/accounts/{address}/nfts?limit=1000",
            headers=TON_HEADERS,
        ) as r:
            nfts_data = await r.json() if r.status == 200 else {"nft_items": []}

    items = nfts_data.get("nft_items", []) or []
    users, numbers, gifts, others, auctions = [], [], [], [], []
    for it in items:
        md = it.get("metadata", {}) or {}
        name = (md.get("name") or "").strip()
        coll = (it.get("collection", {}) or {}).get("address", "")
        desc = (md.get("description") or "").lower()
        is_on_sale = bool(it.get("sale"))
        if name.startswith("@"):
            users.append(name)
        elif name.startswith("+"):
            numbers.append(name)
        elif "gift" in desc or "gift" in coll.lower():
            gifts.append(name or "Gift")
        else:
            others.append(name or "NFT")
        if is_on_sale:
            auctions.append(name or "NFT")

    balance_ton = int(acct.get("balance", 0)) / 1e9
    return {
        "balance_ton": balance_ton,
        "users": users,
        "numbers": numbers,
        "gifts": gifts,
        "others": others,
        "auctions": auctions,
    }


def build_wallet_report(address: str, data: dict, prices: dict) -> str:
    def join(lst, empty):
        return " - ".join(lst) if lst else f"~ {empty}"
    ton = data["balance_ton"]
    usd = ton * prices.get("ton_usd", 0)
    egp = usd * prices.get("usd_egp", 0)
    return (
        f"💰 <b>Wallet</b> : {esc(address[:6])}...{esc(address[-4:])}\n"
        f"🪙 <b>Balance</b> : <b>{ton:.4f} TON</b> = <b>${usd:.2f}</b> = <b>{egp:,.2f} EGP</b>\n"
        f"👤 <b>Users ({len(data['users'])})</b> : {join(data['users'], 'No usernames found')}\n"
        f"⏲ <b>In Auction ({len(data['auctions'])})</b> : {join(data['auctions'], 'No auctions')}\n"
        f"🤙 <b>Numbers ({len(data['numbers'])})</b> : {join(data['numbers'], 'No number found')}\n"
        f"❤️ <b>Upgraded gifts ({len(data['gifts'])})</b> : {join(data['gifts'], 'No upgraded gifts')}\n"
        f"👁 <b>Other NFT ({len(data['others'])})</b> : {join(data['others'], 'No NFT found')}"
    )


async def fragment_public_item(item_type: str, item_id: str) -> dict | None:
    url = "https://fragment.com/api"
    payload = {
        "method": "getItemInfo",
        "type": item_type,
        "id": item_id,
    }
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=8)) as s:
            async with s.post(url, json=payload, headers=_FRAGMENT_HEADERS) as r:
                if r.status == 200:
                    data = await r.json()
                    if data.get("ok"):
                        return data.get("result") or data
    except Exception as e:
        log.warning("fragment public api error: %s", e)
    return None


async def fragment_search_public(query: str, item_type: str = "username") -> list[dict]:
    url = "https://fragment.com/api"
    payload = {
        "method": "search",
        "type": item_type,
        "query": query,
    }
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=8)) as s:
            async with s.post(url, json=payload, headers=_FRAGMENT_HEADERS) as r:
                if r.status == 200:
                    data = await r.json()
                    if data.get("ok"):
                        return data.get("result", {}).get("items", [])
    except Exception as e:
        log.warning("fragment search error: %s", e)
    return []


@app.on_callback_query(filters.regex(r"^home$"))
async def cb_home(client: Client, cb: CallbackQuery):
    with suppress(FloodWait):
        await cb.answer()
    await db.set_state(cb.from_user.id, None)
    await send_home(client, cb.message.chat.id, cb)


@app.on_callback_query(filters.regex(r"^tool:prices$"))
async def cb_prices(client: Client, cb: CallbackQuery):
    with suppress(FloodWait):
        await cb.answer("جاري جلب الأسعار...")
    prices = await fetch_live_prices(force=True)
    if not prices:
        await screen(client, cb, bq("تعذر جلب الأسعار الآن، حاول لاحقاً."), BACK)
        return
    text = (
        "<b>أسعار العملات الحية</b>\n\n"
        f"TON: <b>{prices['ton_usd']:.4f} $</b>\n"
        f"USDT: <b>1.00 $</b>\n"
        f"EGP: <b>{prices['usd_egp']:.2f} EGP</b>\n"
        f"GRAM: <b>{prices['gram_usd']:.4f} $</b>\n"
        f"TON/EGP: <b>{prices['ton_egp']:.2f} EGP</b>\n"
        f"<i>آخر تحديث: {time.strftime('%H:%M:%S')}</i>"
    )
    await screen(client, cb, text, BACK)


@app.on_callback_query(filters.regex(r"^tool:wallet$"))
async def cb_wallet(client: Client, cb: CallbackQuery):
    with suppress(FloodWait):
        await cb.answer()
    await db.set_state(cb.from_user.id, {"action": "tool_wallet"})
    await screen(client, cb, bq("أرسل الآن <b>عنوان المحفظة</b> (TON) لفحص الرصيد والمقتنيات:"), CANCEL)


@app.on_callback_query(filters.regex(r"^tool:commission$"))
async def cb_commission(client: Client, cb: CallbackQuery):
    with suppress(FloodWait):
        await cb.answer()
    await db.set_state(cb.from_user.id, {"action": "tool_commission"})
    await screen(client, cb, bq("أرسل <b>المبلغ</b> لحساب عمولة الوساطة (2%):"), CANCEL)


@app.on_callback_query(filters.regex(r"^tool:convert$"))
async def cb_convert(client: Client, cb: CallbackQuery):
    with suppress(FloodWait):
        await cb.answer()
    await db.set_state(cb.from_user.id, {"action": "tool_convert"})
    await screen(client, cb, bq(
        "أرسل المبلغ مع العملة، مثال:\n"
        "10 ton أو 5$ أو 100 جنيه أو 2 gram"
    ), CANCEL)


@app.on_callback_query(filters.regex(r"^tool:nft_profile$"))
async def cb_nft_profile(client: Client, cb: CallbackQuery):
    with suppress(FloodWait):
        await cb.answer()
    await db.set_state(cb.from_user.id, {"action": "tool_nft_profile"})
    await screen(client, cb, bq("أرسل <b>عنوان محفظة TON</b> لفحص مقتنيات NFT الخاصة بها:"), CANCEL)


@app.on_callback_query(filters.regex(r"^tool:track$"))
async def cb_track(client: Client, cb: CallbackQuery):
    with suppress(FloodWait):
        await cb.answer()
    await db.set_state(cb.from_user.id, {"action": "tool_track"})
    await screen(client, cb, bq("أرسل <b>عنوان محفظة TON</b> لعرض آخر 5 معاملات:"), CANCEL)


@app.on_callback_query(filters.regex(r"^tool:boost$"))
async def cb_boost(client: Client, cb: CallbackQuery):
    with suppress(FloodWait):
        await cb.answer()
    await db.set_state(cb.from_user.id, {"action": "tool_boost"})
    await screen(client, cb, bq("أرسل <b>عدد المشتركين</b> في قناتك لحساب عدد الـ Boosts المطلوبة (المستوى 1-8):"), CANCEL)


@app.on_callback_query(filters.regex(r"^tool:stars$"))
async def cb_stars(client: Client, cb: CallbackQuery):
    with suppress(FloodWait):
        await cb.answer("جاري جلب الأسعار...")
    text = (
        "<b>النجوم و بريميوم (Fragment)</b>\n\n"
        ""
        "شراء النجوم والبريميوم يتم عبر منصة فراجمنت الرسمية.\n\n"
        "<b>طريقة الشراء:</b>\n"
        "1. اضغط على الرابط أدناه.\n"
        "2. سجل الدخول بحسابك.\n"
        "3. اختر الكمية أو مدة البريميوم.\n"
        ""
    )
    markup = Markup([
        [Btn("فتح فراجمنت", url="https://fragment.com/stars")],
        [Btn("رجوع", callback_data="home")]
    ])
    await screen(client, cb, text, markup)


@app.on_callback_query(filters.regex(r"^tool:cash$"))
async def cb_tool_cash(client: Client, cb: CallbackQuery):
    with suppress(FloodWait):
        await cb.answer()
    text = bq(
        "أكواد الكاش\n\n"
        "هذه الأداة مخصصة لتوليد أو استرداد أكواد USDT (Cash Codes) على شبكة TON.\n"
        "يمكنك استخدام بوتات مخصصة لذلك مثل @wallet أو @tonkeeper."
    )
    markup = Markup([
        [Btn("فتح الموقع", url="https://fragment.com")],
        [Btn("رجوع", callback_data="home")]
    ])
    await screen(client, cb, text, markup)


@app.on_callback_query(filters.regex(r"^tool:fragment$"))
async def cb_tool_fragment(client: Client, cb: CallbackQuery):
    with suppress(FloodWait):
        await cb.answer()
    await db.set_state(cb.from_user.id, {"action": "tool_fragment"})
    await screen(client, cb, bq(
        "أرسل <b>رابط Gift</b> من Fragment أو الـ slug فقط:\n"
        "مثال: https://t.me/nft/LunarSnake-192305\n"
        "أو: LunarSnake-192305"
    ), CANCEL)


@app.on_callback_query(filters.regex(r"^tool:username_scan$"))
async def cb_username_scan(client: Client, cb: CallbackQuery):
    with suppress(FloodWait):
        await cb.answer()
    await db.set_state(cb.from_user.id, {"action": "tool_username_scan"})
    await screen(client, cb, bq(
        "أرسل <b>@username</b> أو <b>رابط gift</b> (t.me/nft/...) أو <b>عنوان محفظة TON</b>:"
    ), CANCEL)


@app.on_callback_query(filters.regex(r"^tool:search$"))
async def cb_search(client: Client, cb: CallbackQuery):
    with suppress(FloodWait):
        await cb.answer()
    await db.set_state(cb.from_user.id, {"action": "tool_search"})
    await screen(client, cb, bq(
        "أرسل ما تريد البحث عنه:\n"
        "• اسم مستخدم (مثال: TOPVEGA أو @TOPVEGA)\n"
        "• رقم (+888...)\n"
        "• رابط هدية أو slug"
    ), CANCEL)


@app.on_callback_query(filters.regex(r"^tool:watch$"))
async def cb_watch(client: Client, cb: CallbackQuery):
    with suppress(FloodWait):
        await cb.answer()
    rows = await db.watch_list(cb.from_user.id)
    markup_rows = []
    if rows:
        for r in rows:
            addr = r["address"]
            markup_rows.append([Btn(f"حذف {addr[:8]}...{addr[-4:]}", callback_data=f"watchdel:{addr}")])
        text = "<b>محافظك المراقبة:</b>\n\n"
        for r in rows:
            text += f"• {esc(r['address'])}\n"
        text += ""
    else:
        text = bq("لا توجد محافظ مراقبة حالياً.")
    markup_rows.append([Btn("إضافة محفظة", callback_data="watchadd")])
    markup_rows.append([Btn("رجوع", callback_data="home")])
    await screen(client, cb, text, Markup(markup_rows))


@app.on_callback_query(filters.regex(r"^watchadd$"))
async def cb_watch_add(client: Client, cb: CallbackQuery):
    with suppress(FloodWait):
        await cb.answer()
    await db.set_state(cb.from_user.id, {"action": "tool_watch_add"})
    await screen(client, cb, bq("أرسل <b>عنوان محفظة TON</b> لإضافتها للمراقبة:"), CANCEL)


@app.on_callback_query(filters.regex(r"^watchdel:"))
async def cb_watch_del(client: Client, cb: CallbackQuery):
    with suppress(FloodWait):
        await cb.answer()
    addr = cb.data.split(":", 1)[1]
    await db.watch_remove(cb.from_user.id, addr)
    await cb_watch(client, cb)


@app.on_callback_query(filters.regex(r"^tool:history$"))
async def cb_history(client: Client, cb: CallbackQuery):
    with suppress(FloodWait):
        await cb.answer()
    rows = await db.get_history(cb.from_user.id)
    if not rows:
        await screen(client, cb, bq("لا توجد فحوصات سابقة."), BACK)
        return
    text = "<b>آخر فحوصاتك:</b>\n\n"
    for r in rows:
        ts = time.strftime("%m-%d %H:%M", time.localtime(r["created"]))
        preview = (r["result"] or "").replace("<", "&lt;").replace(">", "&gt;")[:80]
        text += f"[{ts}] {esc(r['tool'])}: {preview}\n"
    text += ""
    await screen(client, cb, text, BACK)


@app.on_callback_query(filters.regex(r"^tool:buy_stars$"))
async def cb_buy_stars(client: Client, cb: CallbackQuery):
    with suppress(FloodWait):
        await cb.answer()
    await db.set_state(cb.from_user.id, {"action": "tool_buy_stars"})
    await screen(client, cb, bq(
        "أرسل بالصيغة: @username 100\n"
        "(المستخدم وعدد Stars، الحد الأدنى 50)"
    ), CANCEL)


async def _state_filter(_, __, m):
    if not m.from_user:
        return False
    return bool(await db.get_state(m.from_user.id))


in_state = filters.create(_state_filter)


@app.on_message(filters.private & in_state)
async def on_state(client: Client, m: Message):
    uid = m.from_user.id
    st = await db.get_state(uid)
    if not st or not m.text:
        return
    act = st["action"]
    text = m.text.strip()

    if act == "tool_wallet":
        await db.set_state(uid, None)
        clean_addr = text.strip().replace(" ", "").replace("\n", "")

        if len(clean_addr) < 20:
            await m.reply_text(bq("عنوان محفظة غير صالح. أرسل عنوان TON صحيح."), reply_markup=BACK)
            return

        msg = await m.reply_text("جاري فحص المحفظة...")
        try:
            prices = await fetch_live_prices() or {"ton_usd": 0, "usd_egp": 0, "ton_egp": 0, "gram_usd": 0}
            async with aiohttp.ClientSession() as session:
                data = await fetch_wallet_full(session, clean_addr)
            res_text = build_wallet_report(clean_addr, data, prices)
            await db.add_history(uid, "wallet", res_text)
            await msg.edit_text(res_text, reply_markup=BACK)
        except Exception as e:
            error_msg = str(e) if str(e) else "تعذر الاتصال بالشبكة."
            await msg.edit_text(bq(f"خطأ: {esc(error_msg)}"), reply_markup=BACK)
        return

    if act == "tool_nft_profile":
        await db.set_state(uid, None)
        msg = await m.reply_text("جاري فحص مقتنيات NFT...")
        try:
            async with aiohttp.ClientSession() as session:
                async with _TON_API_SEM:
                    async with session.get(
                        f"https://tonapi.io/v2/accounts/{text}/nfts?limit=50",
                        headers=TON_HEADERS,
                    ) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            nfts = data.get("nft_items", [])
                            if not nfts:
                                await msg.edit_text(bq("لا توجد مقتنيات NFT في هذه المحفظة."), reply_markup=BACK)
                                return
                            res = "<b>مقتنيات NFT:</b>\n\n"
                            for nft in nfts:
                                name = nft.get("metadata", {}).get("name", "بدون اسم")
                                res += f"• {esc(name)}\n"
                            res += ""
                            await db.add_history(uid, "nft_profile", res)
                            await msg.edit_text(res, reply_markup=BACK)
                        else:
                            await msg.edit_text(bq("تعذر جلب بيانات NFT."), reply_markup=BACK)
        except Exception as e:
            await msg.edit_text(bq(f"خطأ: {esc(e)}"), reply_markup=BACK)
        return

    if act == "tool_track":
        await db.set_state(uid, None)
        msg = await m.reply_text("جاري جلب المعاملات...")
        try:
            async with aiohttp.ClientSession() as session:
                async with _TON_API_SEM:
                    async with session.get(
                        f"https://tonapi.io/v2/accounts/{text}/transactions?limit=5",
                        headers=TON_HEADERS,
                    ) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            txs = data.get("transactions", [])
                            if not txs:
                                await msg.edit_text(bq("لا توجد معاملات حديثة."), reply_markup=BACK)
                                return
                            res = "<b>آخر 5 معاملات:</b>\n\n"
                            for tx in txs:
                                val = int(tx.get("in_msg", {}).get("value", 0)) / 1e9
                                if val > 0:
                                    res += f"• استلام: {val:.4f} TON\n"
                                else:
                                    res += f"• إرسال: {-val:.4f} TON\n"
                            res += ""
                            await db.add_history(uid, "track", res)
                            await msg.edit_text(res, reply_markup=BACK)
                        else:
                            await msg.edit_text(bq("تعذر جلب المعاملات."), reply_markup=BACK)
        except Exception as e:
            await msg.edit_text(bq(f"خطأ: {esc(e)}"), reply_markup=BACK)
        return

    if act == "tool_boost":
        await db.set_state(uid, None)
        try:
            subs = int(text)
            level_reqs = [1, 5, 10, 25, 50, 100, 200, 500]
            res = "<b>حاسبة تعزيز القنوات (Boosts)</b>\n\n"
            for level, req in enumerate(level_reqs, 1):
                res += f"المستوى {level}: <b>{req}</b> Boost\n"
            res += f"\nعدد المشتركين الحالي: <b>{subs}</b>\n"
            res += "المستويات المطلوبة تعتمد على نشاط القناة وعدد المشتركين."
            await db.add_history(uid, "boost", res)
            await m.reply_text(res, reply_markup=BACK)
        except ValueError:
            await m.reply_text(bq("أرسل رقماً صحيحاً لعدد المشتركين."), reply_markup=BACK)
        return

    if act == "tool_commission":
        await db.set_state(uid, None)
        try:
            amt = float(text)
            fee = amt * 0.02
            res = bq(f"المبلغ: {amt}\nالعمولة (2%): <b>{fee}</b>\nالصافي المستلم: {amt - fee}")
            await db.add_history(uid, "commission", res)
            await m.reply_text(res, reply_markup=BACK)
        except ValueError:
            await m.reply_text(bq("أرسل رقماً صحيحاً."), reply_markup=BACK)
        return

    if act == "tool_convert":
        await db.set_state(uid, None)
        parsed = parse_amount_query(text)
        if not parsed:
            await m.reply_text(bq("صيغة غير صحيحة. مثال: 10 ton أو 5$"), reply_markup=BACK)
            return
        amount, base = parsed
        prices = await fetch_live_prices()
        if not prices:
            await m.reply_text(bq("تعذر جلب الأسعار الآن، حاول لاحقاً."), reply_markup=BACK)
            return
        vals = build_values(amount, base, prices)
        lines = [f"<b>{amount:g} {_LABELS[base]}</b> =", ""]
        for k in ("ton", "usd", "egp", "gram"):
            if k == base:
                continue
            lines.append(f"• {_LABELS[k]}: <b>{_fmt(k, vals[k])}</b>")
        lines.append("")
        lines.append(f"<i>تحديث: {time.strftime('%H:%M')}</i>")
        res = "\n".join(lines)
        await db.add_history(uid, "convert", res)
        await m.reply_text(res, reply_markup=BACK)
        return

    if act == "tool_fragment":
        await db.set_state(uid, None)
        msg = await m.reply_text("جاري فحص الـ Gift...")
        slug = text.strip()
        if "/nft/" in slug:
            slug = slug.split("/nft/")[-1].split("?")[0].split("/")[0]
        try:
            info = await fragment_public_item("gift", slug)
            if not info:
                await msg.edit_text(
                    bq(f"تعذر جلب بيانات الـ Gift.\nتأكد من صحة الرابط: https://t.me/nft/{esc(slug)}"),
                    reply_markup=BACK,
                )
                return
            name = info.get("name") or slug
            price = info.get("price") or info.get("sale_price") or "غير متاح"
            owner = info.get("owner") or "غير معروف"
            status = info.get("status") or "غير محدد"
            res = (
                f"🎁 <b>Gift</b> : <b>{esc(name)}</b>\n"
                f""
                f"السعر: <b>{esc(str(price))}</b>\n"
                f"المالك: {esc(str(owner))}\n"
                f"الحالة: {esc(str(status))}\n"
                f"الرابط: https://t.me/nft/{esc(slug)}\n"
                f""
            )
            await db.add_history(uid, "fragment", res)
            await msg.edit_text(res, reply_markup=BACK)
        except Exception as e:
            await msg.edit_text(bq(f"خطأ: {esc(e)}"), reply_markup=BACK)
        return

    if act == "tool_username_scan":
        await db.set_state(uid, None)
        msg = await m.reply_text("جاري الفحص...")
        prices = await fetch_live_prices() or {"ton_usd": 0, "usd_egp": 0, "ton_egp": 0, "gram_usd": 0}
        try:
            if text.startswith("http") and "/nft/" in text:
                slug = text.split("/nft/")[-1].split("?")[0].split("/")[0]
                info = await fragment_public_item("gift", slug)
                if not info:
                    await msg.edit_text(bq("تعذر جلب بيانات الـ Gift."), reply_markup=BACK)
                    return
                res = (
                    f"🎁 <b>Gift</b> : <b>{esc(info.get('name', slug))}</b>\n"
                    f"السعر: <b>{esc(str(info.get('price', 'غير متاح')))}</b>\n"
                    f"الرابط: https://t.me/nft/{esc(slug)}"
                )
                await db.add_history(uid, "username_scan", res)
                await msg.edit_text(res, reply_markup=BACK)
                return
            if text.startswith("@"):
                uname = text.lstrip("@").lower()
                async with aiohttp.ClientSession() as s:
                    async with _TON_API_SEM:
                        async with s.get(
                            f"https://tonapi.io/v2/dns/{uname}.ton",
                            headers=TON_HEADERS,
                        ) as r:
                            if r.status == 200:
                                dns = await r.json()
                                wallet = (dns.get("item", {}).get("owner", {}) or {}).get("address", "")
                                res = bq(f" اليوزر <b>@{esc(uname)}</b> مسجل\nالمالك: {esc(wallet)}")
                            else:
                                res = bq(f"❌ اليوزر <b>@{esc(uname)}</b> غير مسجل على TON DNS.")
                await db.add_history(uid, "username_scan", res)
                await msg.edit_text(res, reply_markup=BACK)
                return
            async with aiohttp.ClientSession() as s:
                data = await fetch_wallet_full(s, text)
            res = build_wallet_report(text, data, prices)
            await db.add_history(uid, "username_scan", res)
            await msg.edit_text(res, reply_markup=BACK)
        except Exception as e:
            await msg.edit_text(bq(f"خطأ: {esc(e)}"), reply_markup=BACK)
        return

    if act == "tool_search":
        await db.set_state(uid, None)
        query = text.strip().lstrip("@")
        msg = await m.reply_text("جاري البحث...")
        try:
            items = await fragment_search_public(query, "username")
            if not items:
                res = bq(f"لم يتم العثور على نتائج لـ <b>{esc(query)}</b> في Fragment.")
            else:
                res = f"🔍 <b>نتائج البحث عن: {esc(query)}</b>\n\n"
                for item in items[:10]:
                    name = item.get("name") or item.get("username") or "?"
                    price = item.get("price") or "غير معروض"
                    status = item.get("status") or "?"
                    res += f"• <b>@{esc(name)}</b> — {esc(str(price))} TON ({esc(str(status))})\n"
                if len(items) > 10:
                    res += f"\n... و {len(items) - 10} نتيجة أخرى"
                res += ""
            await db.add_history(uid, "search", res)
            await msg.edit_text(res, reply_markup=BACK)
        except Exception as e:
            await msg.edit_text(bq(f"خطأ في البحث: {esc(e)}"), reply_markup=BACK)
        return

    if act == "tool_watch_add":
        await db.set_state(uid, None)
        if not TON_ADDR_RE.match(text) and not text.startswith("0:"):
            await m.reply_text(bq("عنوان محفظة غير صالح."), reply_markup=BACK)
            return
        await db.watch_add(uid, text)
        await m.reply_text(bq(" تمت إضافة المحفظة للمراقبة."), reply_markup=BACK)
        return

    if act == "tool_buy_stars":
        await db.set_state(uid, None)
        parts = text.split()
        if len(parts) < 2:
            await m.reply_text(bq("التنسيق غير صحيح. أرسل: @username 100"), reply_markup=BACK)
            return

        username = parts[0].lstrip("@")
        try:
            amount = int(parts[1])
            if amount < 50:
                await m.reply_text(bq("الحد الأدنى للشراء هو 50 Star."), reply_markup=BACK)
                return
        except ValueError:
            await m.reply_text(bq("عدد Stars غير صحيح."), reply_markup=BACK)
            return

        if not Config.FRAGMENT_HASH or not Config.topvega:
            await m.reply_text(
                bq(" خدمة الشراء غير مفعلة.\n"
                   "يجب على مالك البوت تعيين FRAGMENT_HASH و topvega في متغيرات البيئة.\n\n"
                   "يمكنك الحصول عليهما من Fragment.com بعد تسجيل الدخول عبر أدوات TOPVEGA (F12)."),
                reply_markup=BACK,
            )
            return

        msg = await m.reply_text("جاري إنشاء طلب الشراء عبر Fragment...")
        try:
            from afragment import AsyncFragmentClient, nano_to_ton

            async with AsyncFragmentClient(
                fragment_hash=Config.FRAGMENT_HASH,
                fragment_cookie=Config.topvega,
            ) as af_client:
                result = await af_client.buy_stars(
                    username=username,
                    quantity=amount,
                    wallet_address=Config.TON_WALLET_ADDRESS,
                )

            tx = result.get("transaction", {}).get("messages", [{}])[0]
            price_ton = result.get("amount", "?")
            to_address = tx.get("address", "?")
            nano_amount = tx.get("amount", "0")
            ton_amount = nano_to_ton(int(nano_amount)) if nano_amount.isdigit() else "?"
            payload = tx.get("payload", "")

            res = (
                f" <b>تم إنشاء طلب شراء Stars بنجاح</b>\n"
                f""
                f"المستلم: @{esc(username)}\n"
                f"الكمية: <b>{amount}</b> Stars\n"
                f"السعر: <b>{esc(str(price_ton))} TON</b>\n"
                f"<b>أرسل TON إلى:</b>\n"
                f"{esc(to_address)}\n"
                f"المبلغ: <b>{ton_amount} TON</b>\n"
                f"Payload: {esc(payload[:60])}...\n"
                f"\n"
                f" <i>يجب إرسال TON يدوياً من محفظتك لإتمام الشراء.</i>"
            )
            await db.add_history(uid, "buy_stars", res)
            await msg.edit_text(res, reply_markup=BACK)
        except ImportError:
            await msg.edit_text(
                bq("مكتبة afragment غير مثبتة.\nشغّل: pip install afragment"),
                reply_markup=BACK,
            )
        except Exception as e:
            await msg.edit_text(bq(f"فشل إنشاء الطلب: {esc(e)}"), reply_markup=BACK)
        return


@app.on_message(filters.private & filters.command("start"))
async def cmd_start(client: Client, m: Message):
    await db.set_state(m.from_user.id, None)
    await send_home(client, m.chat.id, None)


async def watchlist_worker():
    while True:
        try:
            rows = await db.watch_all()
            if rows:
                async with aiohttp.ClientSession() as s:
                    for r in rows:
                        uid, addr, last = r["user_id"], r["address"], r["last_tx"]
                        try:
                            async with _TON_API_SEM:
                                async with s.get(
                                    f"https://tonapi.io/v2/accounts/{addr}/transactions?limit=1",
                                    headers=TON_HEADERS,
                                ) as resp:
                                    if resp.status != 200:
                                        continue
                                    data = await resp.json()
                            txs = data.get("transactions", [])
                            if not txs:
                                continue
                            tx_hash = txs[0].get("hash", "")
                            if last and tx_hash and tx_hash != last:
                                val = int(txs[0].get("in_msg", {}).get("value", 0)) / 1e9
                                if val > 100:
                                    with suppress(Exception):
                                        await app.send_message(
                                            uid,
                                            f"🔔 <b>تنبيه محفظة</b>\n\n"
                                            f"محفظة: {esc(addr[:8])}...{esc(addr[-4:])}\n"
                                            f"تحويل: <b>{val:.2f} TON</b>",
                                        )
                            await db.watch_update(uid, addr, tx_hash)
                        except Exception as e:
                            log.warning("watch error: %s", e)
        except Exception as e:
            log.warning("watch worker: %s", e)
        await asyncio.sleep(300)


async def cmd_stats(client: Client, m: Message):
    if m.from_user.id != Config.OWNER_ID:
        return
    users = await db.users_count()
    await m.reply_text(f"👥 المستخدمون: <b>{users}</b>")


async def cmd_broadcast(client: Client, m: Message):
    if m.from_user.id != Config.OWNER_ID:
        return
    if len(m.command) < 2:
        await m.reply_text("الاستخدام: /broadcast نص الرسالة")
        return
    msg_text = m.text.split(None, 1)[1]
    rows = await db.all("SELECT user_id FROM states")
    sent = 0
    for r in rows:
        with suppress(Exception):
            await client.send_message(r["user_id"], msg_text)
            sent += 1
            await asyncio.sleep(0.05)
    await m.reply_text(f"تم الإرسال إلى {sent} مستخدم.")


app.add_handler(__import__("pyrogram").handlers.MessageHandler(cmd_stats, filters.command("stats") & filters.private))
app.add_handler(__import__("pyrogram").handlers.MessageHandler(cmd_broadcast, filters.command("broadcast") & filters.private))


async def main():
    global BOT_ID, DEV_NAME, DEV_USERNAME
    await db.start()
    await app.start()
    me = await app.get_me()
    BOT_ID = me.id
    try:
        dev = await app.get_users(Config.OWNER_ID)
        DEV_NAME = dev.first_name or "TOPVEGA"
        DEV_USERNAME = dev.username or ""
    except Exception:
        DEV_NAME = "TOPVEGA"
        DEV_USERNAME = ""
    task = asyncio.create_task(watchlist_worker())
    BG_TASKS.add(task)
    task.add_done_callback(BG_TASKS.discard)
    log.info("Tools Bot @%s is running", me.username)
    await idle()
    for t in list(BG_TASKS):
        t.cancel()
    await app.stop()
    await db.close()


if __name__ == "__main__":
    LOOP.run_until_complete(main())
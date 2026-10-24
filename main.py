import asyncio
import os
import re
import sqlite3
import random
import string
import time
from html import escape as esc
from types import SimpleNamespace
from datetime import datetime, timedelta, timezone
from aiohttp import web

from aiogram import BaseMiddleware, Bot, Dispatcher, F
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.types import Message, CallbackQuery, ChatMemberUpdated, ChatJoinRequest, BufferedInputFile
from aiogram.filters import ChatMemberUpdatedFilter, JOIN_TRANSITION, LEAVE_TRANSITION
from aiogram.utils.keyboard import InlineKeyboardBuilder

TOKEN = os.environ.get("TOKEN")
if not TOKEN:
    raise RuntimeError("TOKEN environment variable is not set")

CHANNEL_ID = -1003279311073
CHANNEL_LINK = "https://t.me/chapitosikboss"

CHAT_LINK = "https://t.me/+hi1WWyULprhlMDgx"

ADMINS = [8008667717]

# Сколько раз бот сам снимает ЧС за отписку при повторной подписке (0 - не снимать никогда)
AUTO_FORGIVE_LIMIT = 1

# Инструкция по установке - показывается в карточке сборки. Пусто = блок не показывается.
# Можно задать переменной окружения INSTALL_GUIDE
INSTALL_GUIDE = os.environ.get("INSTALL_GUIDE", "").strip()

bot = Bot(TOKEN)
dp = Dispatcher()

user_states = {}
user_temp = {}
db_lock = asyncio.Lock()

DB_PATH = os.environ.get("DB_PATH", "bot/bot.db")
os.makedirs(os.path.dirname(os.path.abspath(DB_PATH)), exist_ok=True)
db = sqlite3.connect(DB_PATH)
cursor = db.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS builds (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT,
    link TEXT,
    downloads INTEGER DEFAULT 0
)
""")

db.commit()


try:
    cursor.execute("ALTER TABLE builds ADD COLUMN recommended INTEGER DEFAULT 0")
    db.commit()
except Exception:
    pass

cursor.execute("""
CREATE TABLE IF NOT EXISTS downloads (
    user_id INTEGER,
    build_id INTEGER,
    ts TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
""")

db.commit()

try:
    cursor.execute("ALTER TABLE users ADD COLUMN blacklisted INTEGER DEFAULT 0")
    db.commit()
except Exception:
    pass

try:
    cursor.execute("ALTER TABLE users ADD COLUMN ref_code TEXT")
    db.commit()
except Exception:
    pass

try:
    cursor.execute("ALTER TABLE users ADD COLUMN coins INTEGER DEFAULT 0")
    db.commit()
except Exception:
    pass

cursor.execute("""
CREATE TABLE IF NOT EXISTS referrals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    referrer_id INTEGER,
    referred_id INTEGER UNIQUE,
    confirmed INTEGER DEFAULT 0,
    ts TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
""")

db.commit()

cursor.execute("""
CREATE TABLE IF NOT EXISTS rewards (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT,
    youtube_link TEXT,
    download_link TEXT,
    active INTEGER DEFAULT 0,
    ts TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS reward_issued (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    reward_id INTEGER,
    ts TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS sponsors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    type TEXT NOT NULL,
    target TEXT NOT NULL,
    button_text TEXT NOT NULL,
    name TEXT NOT NULL,
    active INTEGER DEFAULT 1
)
""")

try:
    cursor.execute("ALTER TABLE sponsors ADD COLUMN invite_link TEXT")
    db.commit()
except Exception:
    pass

cursor.execute("""
CREATE TABLE IF NOT EXISTS sponsor_acks (
    user_id INTEGER,
    sponsor_id INTEGER,
    PRIMARY KEY (user_id, sponsor_id)
)
""")

db.commit()

cursor.execute("""
CREATE TABLE IF NOT EXISTS task_reviewers (
    user_id INTEGER PRIMARY KEY,
    added_by INTEGER,
    ts TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    reward INTEGER NOT NULL,
    active INTEGER DEFAULT 1,
    ts TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS task_submissions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    proof_text TEXT,
    proof_file_id TEXT,
    proof_file_type TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    reviewer_id INTEGER,
    reviewer_note TEXT,
    ts TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    reviewed_ts TIMESTAMP
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS shop_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    description TEXT NOT NULL,
    price INTEGER NOT NULL,
    item_type TEXT NOT NULL DEFAULT 'channel',
    target TEXT NOT NULL,
    active INTEGER DEFAULT 1,
    ts TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS purchases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    price INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    delivery_link TEXT,
    ts TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    delivered_ts TIMESTAMP
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS promo_codes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL UNIQUE COLLATE NOCASE,
    reward_coins INTEGER NOT NULL,
    max_uses INTEGER NOT NULL DEFAULT 0,
    uses_count INTEGER NOT NULL DEFAULT 0,
    expires_at TIMESTAMP,
    active INTEGER DEFAULT 1,
    ts TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS promo_redemptions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    promo_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    ts TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (promo_id, user_id)
)
""")

db.commit()


# ---------------------------------------------------------------------
# v2: миграции (все идемпотентны - безопасно запускать при каждом старте)
# ---------------------------------------------------------------------

def ensure_column(table, column, ddl):
    existing = [row[1] for row in cursor.execute(f"PRAGMA table_info({table})").fetchall()]
    if column not in existing:
        cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
        db.commit()


# users: аналитика, мягкий ЧС, уведомления
ensure_column("users", "created_at", "TEXT")
ensure_column("users", "source", "TEXT")
ensure_column("users", "last_seen", "TEXT")
ensure_column("users", "bot_blocked", "INTEGER DEFAULT 0")
ensure_column("users", "ban_reason", "TEXT")
ensure_column("users", "ban_count", "INTEGER DEFAULT 0")
ensure_column("users", "notify_builds", "INTEGER")
ensure_column("users", "notify_asked_at", "TEXT")

# builds: карточка сборки
ensure_column("builds", "description", "TEXT")
ensure_column("builds", "photo_id", "TEXT")
ensure_column("builds", "added_at", "TEXT")
ensure_column("builds", "notified_at", "TEXT")
ensure_column("builds", "posted_at", "TEXT")
ensure_column("builds", "channel_post_id", "INTEGER")
ensure_column("users", "pending_build", "INTEGER")

cursor.execute("""
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
)
""")
db.commit()

cursor.execute("""
CREATE TABLE IF NOT EXISTS build_votes (
    build_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    vote INTEGER NOT NULL,
    ts TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (build_id, user_id)
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS ban_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    action TEXT NOT NULL,
    ts TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
""")

# Бэкфилл старых данных.
# created_at у старых пользователей = дата первого скачивания (точной даты регистрации раньше не хранили)
cursor.execute("""
UPDATE users
SET created_at = (SELECT MIN(d.ts) FROM downloads d WHERE d.user_id = users.user_id)
WHERE created_at IS NULL
""")

# added_at у старых сборок = дата первого скачивания
cursor.execute("""
UPDATE builds
SET added_at = (SELECT MIN(d.ts) FROM downloads d WHERE d.build_id = builds.id)
WHERE added_at IS NULL
""")

# Уже забаненные: те, кто скачивал сборки, считаем автобаном за отписку (первое нарушение),
# остальных - ручными банами
cursor.execute("UPDATE users SET ban_count = 1 WHERE blacklisted = 1 AND (ban_count IS NULL OR ban_count = 0)")
cursor.execute("""
UPDATE users SET ban_reason = 'auto_leave'
WHERE blacklisted = 1 AND ban_reason IS NULL
  AND EXISTS (SELECT 1 FROM downloads d WHERE d.user_id = users.user_id)
""")
cursor.execute("UPDATE users SET ban_reason = 'manual' WHERE blacklisted = 1 AND ban_reason IS NULL")

db.commit()


async def has_reviewer_access(user_id):
    if user_id in ADMINS:
        return True

    async with db_lock:
        lc = db.cursor()
        lc.execute("SELECT 1 FROM task_reviewers WHERE user_id=?", (user_id,))
        return lc.fetchone() is not None


async def get_reviewer_ids():
    async with db_lock:
        lc = db.cursor()
        lc.execute("SELECT user_id FROM task_reviewers")
        reviewer_ids = [row[0] for row in lc.fetchall()]

    return list(dict.fromkeys(ADMINS + reviewer_ids))


def format_user(user):
    username = f"@{user.username}" if user.username else "без username"
    name = " ".join(part for part in [user.first_name, user.last_name] if part)
    return f"{name or 'Без имени'} ({username}, ID: {user.id})"


def main_menu(user_id=None):

    kb = InlineKeyboardBuilder()

    kb.button(text="📦 Скачать сборку", callback_data="builds")
    kb.button(text="🎁 Бонусы", callback_data="bonuses")
    kb.button(text="👤 Профиль", callback_data="profile")
    kb.button(text="📢 Канал", url=CHANNEL_LINK)
    kb.button(text="💬 Чат", url=CHAT_LINK)

    is_admin = bool(user_id and user_id in ADMINS)
    if is_admin:
        kb.button(text="⚙️ Админ-панель", callback_data="admin_panel")

    if is_admin:
        kb.adjust(1, 2, 2, 1)
    else:
        kb.adjust(1, 2, 2)

    return kb.as_markup()


# Админ-меню: 4 группы вместо 16 кнопок в одну колонку
ADMIN_GROUPS = {
    "builds": ("📦 Сборки", [
        ("➕ Добавить сборку", "admin_add"),
        ("🗑 Удалить сборку", "admin_delete"),
        ("⭐ Рекомендуемая", "admin_recommend"),
        ("🖼 Карточка сборки", "admin_card"),
        ("📢 Пост в канал", "admin_pub"),
    ]),
    "audience": ("📣 Аудитория", [
        ("📢 Рассылка", "admin_broadcast"),
        ("📊 Статистика", "admin_stats"),
        ("🔔 Спросить про уведомления", "admin_ask_notify"),
        ("🔓 Амнистия", "admin_amnesty"),
    ]),
    "economy": ("💰 Экономика", [
        ("📋 Задания", "admin_tasks"),
        ("🛒 Магазин", "admin_shop"),
        ("🎟 Промокоды", "admin_promos"),
        ("🎁 Награда за рефералов", "admin_reward"),
    ]),
    "settings": ("🔧 Настройки", [
        ("👥 Спонсоры", "admin_sponsors"),
        ("👮 Проверяющие", "admin_reviewers"),
        ("🖼 Баннер приветствия", "admin_banner"),
    ]),
}


def admin_menu(pending=None):

    kb = InlineKeyboardBuilder()

    label = "🧾 Проверка заданий"
    if pending:
        label += f" ({pending})"
    kb.button(text=label, callback_data="review_tasks")

    for key, (title, _items) in ADMIN_GROUPS.items():
        kb.button(text=title, callback_data=f"admin_g_{key}")

    kb.button(text="🔙 Главное меню", callback_data="main_menu")

    kb.adjust(1, 2, 2, 1)

    return kb.as_markup()


def admin_group_menu(key):

    _title, items = ADMIN_GROUPS[key]

    kb = InlineKeyboardBuilder()

    for text, callback_data in items:
        kb.button(text=text, callback_data=callback_data)

    kb.button(text="🔙 Админ-панель", callback_data="admin_panel")

    kb.adjust(1)

    return kb.as_markup()


def sub_menu():

    kb = InlineKeyboardBuilder()

    kb.button(
        text="📢 Подписаться",
        url=CHANNEL_LINK
    )

    kb.button(
        text="✅ Проверить",
        callback_data="check_sub"
    )

    kb.button(
        text="🔙 Главное меню",
        callback_data="main_menu"
    )

    kb.adjust(1)

    return kb.as_markup()


async def check_sub(user_id):

    try:

        member = await bot.get_chat_member(
            CHANNEL_ID,
            user_id
        )

        return member.status in [
            "member",
            "administrator",
            "creator"
        ]

    except:
        return False


async def get_unmet_sponsors(user_id):
    async with db_lock:
        lc = db.cursor()
        lc.execute("SELECT id, type, target, button_text, name, invite_link FROM sponsors WHERE active=1")
        all_sponsors = lc.fetchall()

        lc2 = db.cursor()
        lc2.execute("SELECT sponsor_id FROM sponsor_acks WHERE user_id=?", (user_id,))
        acked_ids = {row[0] for row in lc2.fetchall()}

    unmet_channels = []
    unmet_links = []

    for sponsor in all_sponsors:
        sid, stype, target, button_text, name, invite_link = sponsor
        if stype == "channel":
            try:
                member = await bot.get_chat_member(int(target), user_id)
                if member.status not in ["member", "administrator", "creator"]:
                    unmet_channels.append(sponsor)
            except Exception:
                unmet_channels.append(sponsor)
        elif stype == "link":
            if sid not in acked_ids:
                unmet_links.append(sponsor)

    return unmet_channels, unmet_links


def build_sponsor_keyboard(unmet_channels, unmet_links, build_id):
    kb = InlineKeyboardBuilder()
    for sponsor in unmet_channels:
        sid, stype, target, button_text, name, invite_link = sponsor
        if invite_link:
            channel_url = invite_link
        elif target.startswith("http") or target.startswith("@"):
            channel_url = target if target.startswith("http") else f"https://t.me/{target.lstrip('@')}"
        else:
            channel_url = f"https://t.me/{target.lstrip('@')}"
        kb.button(text=f"📢 {button_text}", url=channel_url)
    for sponsor in unmet_links:
        sid, stype, target, button_text, name, invite_link = sponsor
        kb.button(text=f"🔗 {button_text}", url=target)
    kb.button(text="✅ Я подписался - проверить", callback_data=f"check_sponsor_sub_{build_id}")
    kb.adjust(1)
    return kb.as_markup()


async def confirm_referral_if_pending(user_id):

    async with db_lock:
        lc = db.cursor()
        lc.execute("SELECT id, referrer_id FROM referrals WHERE referred_id=? AND confirmed=0", (user_id,))
        pending = lc.fetchone()
        if not pending:
            return

        referral_id, referrer_id = pending

        lc.execute("UPDATE referrals SET confirmed=1 WHERE id=? AND confirmed=0", (referral_id,))
        db.commit()

        if lc.rowcount == 0:
            # Другой процесс/инстанс бота уже подтвердил этот реферал - не дублируем уведомление
            return

        lc.execute("SELECT COUNT(*) FROM referrals WHERE referrer_id=? AND confirmed=1", (referrer_id,))
        cnt = lc.fetchone()[0]

        reward_to_send = None
        if cnt >= 5:
            lc.execute("SELECT id, title, youtube_link, download_link FROM rewards WHERE active=1 LIMIT 1")
            reward_row = lc.fetchone()
            if reward_row:
                lc.execute("SELECT 1 FROM reward_issued WHERE user_id=? AND reward_id=?", (referrer_id, reward_row[0]))
                already = lc.fetchone()
                if not already:
                    reward_to_send = reward_row
                    lc.execute("INSERT INTO reward_issued (user_id, reward_id) VALUES (?, ?)", (referrer_id, reward_row[0]))
                    db.commit()

    # Все await - за пределами лока, курсор уже не нужен
    try:
        chat = await bot.get_chat(user_id)
        name = chat.username or chat.first_name or str(user_id)
    except Exception:
        name = str(user_id)

    try:
        await bot.send_message(referrer_id, f"✅ Твой реферал {name} подтвердил подписку. Подтверждено: {cnt}")
    except Exception:
        pass

    if reward_to_send:
        try:
            await bot.send_message(referrer_id, f"🎁 Награда за 5 рефералов: {reward_to_send[1]}\n\nОбзор: {reward_to_send[2]}\nСсылка: {reward_to_send[3]}")
        except Exception:
            pass


# ======================================================================
# v2 · аналитика, мягкий ЧС, карточка сборки, уведомления о сборках
# ======================================================================

def now_utc_str():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def fmt_date(ts):
    try:
        return datetime.strptime(str(ts)[:10], "%Y-%m-%d").strftime("%d.%m.%Y")
    except Exception:
        return ""


def short_title(name, limit=48):
    name = " ".join((name or "").split())
    if len(name) <= limit:
        return name
    return name[: limit - 1].rstrip() + "…"


def is_http_url(value):
    return bool(value) and str(value).strip().lower().startswith(("http://", "https://"))


# ---------- last_seen: пишем не чаще раза в 10 минут на пользователя ----------

_last_touch = {}
TOUCH_INTERVAL = 600


class TouchMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        try:
            user = getattr(event, "from_user", None)
            if user and not user.is_bot:
                now = time.monotonic()
                last = _last_touch.get(user.id)
                if last is None or now - last > TOUCH_INTERVAL:
                    _last_touch[user.id] = now
                    async with db_lock:
                        db.execute(
                            "UPDATE users SET last_seen=?, bot_blocked=0 WHERE user_id=?",
                            (now_utc_str(), user.id)
                        )
                        db.commit()
        except Exception:
            pass
        return await handler(event, data)


dp.message.outer_middleware(TouchMiddleware())
dp.callback_query.outer_middleware(TouchMiddleware())


@dp.my_chat_member()
async def on_my_chat_member(event: ChatMemberUpdated):
    # Пользователь заблокировал бота / разблокировал его
    if event.chat.type != "private":
        return
    blocked = 1 if event.new_chat_member.status in ("kicked", "left") else 0
    async with db_lock:
        db.execute("UPDATE users SET bot_blocked=? WHERE user_id=?", (blocked, event.chat.id))
        db.commit()


# ---------- мягкий ЧС: прощаем один раз при повторной подписке ----------

async def try_auto_forgive(user_id):
    """Снимает ЧС за отписку, если это первое нарушение. True - снят."""
    async with db_lock:
        lc = db.cursor()
        lc.execute("SELECT blacklisted, ban_reason, ban_count FROM users WHERE user_id=?", (user_id,))
        row = lc.fetchone()
        if not row or row[0] != 1 or row[1] != "auto_leave" or (row[2] or 0) > AUTO_FORGIVE_LIMIT:
            return False
        lc.execute("UPDATE users SET blacklisted=0 WHERE user_id=? AND blacklisted=1", (user_id,))
        changed = lc.rowcount > 0
        if changed:
            lc.execute("INSERT INTO ban_log (user_id, action) VALUES (?, 'auto_unban')", (user_id,))
        db.commit()
    return changed


async def blacklist_check(callback):
    """True - пользователь в ЧС, сообщение ему уже отправлено, дальше идти нельзя."""
    user_id = callback.from_user.id

    async with db_lock:
        lc = db.cursor()
        lc.execute("SELECT blacklisted, ban_reason, ban_count FROM users WHERE user_id=?", (user_id,))
        row = lc.fetchone()

    if not row or row[0] != 1:
        return False

    reason, count = row[1], (row[2] or 0)
    can_forgive = reason == "auto_leave" and count <= AUTO_FORGIVE_LIMIT

    # Уже подписался обратно, а событие входа мы не поймали - прощаем сразу
    if can_forgive and await check_sub(user_id):
        if await try_auto_forgive(user_id):
            await callback.message.answer("✅ С возвращением! ЧС снят - сборки снова доступны.")
            return False

    if can_forgive:
        await callback.message.answer(
            "⏸ Доступ к сборкам приостановлен: ты вышел из канала.\n\n"
            "Подпишись снова - и ЧС снимется автоматически (это работает один раз).",
            reply_markup=sub_menu()
        )
    elif reason == "auto_leave":
        await callback.message.answer(
            "❌ Ты повторно вышел из канала, поэтому ЧС не снимается автоматически.\n\n"
            "Снять его можно в 🛒 Магазине за монеты или через администрацию."
        )
    else:
        await callback.message.answer("❌ Ты в ЧС и не можешь скачивать сборки. Напиши администрации.")
    return True


# ---------- карточка сборки ----------

async def get_build_full(build_id):
    async with db_lock:
        lc = db.cursor()
        lc.execute(
            "SELECT id, name, link, downloads, recommended, description, photo_id, added_at "
            "FROM builds WHERE id=?",
            (build_id,)
        )
        row = lc.fetchone()
        if not row:
            return None
        lc.execute(
            "SELECT COALESCE(SUM(vote=1), 0), COALESCE(SUM(vote=-1), 0) FROM build_votes WHERE build_id=?",
            (build_id,)
        )
        likes, dislikes = lc.fetchone()
    return {
        "id": row[0], "name": row[1], "link": row[2], "downloads": row[3] or 0,
        "recommended": row[4], "description": row[5], "photo_id": row[6],
        "added_at": row[7], "likes": likes, "dislikes": dislikes,
    }


def render_card_text(b, link_in_text=False):
    limit = 1024 if b["photo_id"] else 4000
    name = b["name"] or ""
    desc = (b["description"] or "").strip()
    guide = INSTALL_GUIDE
    text = ""

    for _ in range(100):
        lines = [f"🔥 <b>{h(name)}</b>"]
        if desc:
            lines += ["", h(desc)]
        lines.append("")
        if b["added_at"]:
            lines.append(f"📅 Добавлена: {fmt_date(b['added_at'])}")
        lines.append(f"📥 Скачали: {b['downloads']}")
        if guide:
            lines += ["", "🛠 <b>Как установить</b>", h(guide)]
        if link_in_text or not is_http_url(b["link"]):
            # ссылка без https:// или такая, которую Telegram не принял как кнопку - показываем текстом
            lines += ["", "📥 Скачать:", h(b["link"])]
        text = "\n".join(lines)

        if len(text) <= limit:
            return text

        # не влезает в подпись к фото - укорачиваем описание, потом инструкцию, потом название
        if desc:
            desc = desc[:-100].rstrip(" …") + "…" if len(desc) > 100 else ""
        elif guide:
            guide = guide[:-100].rstrip(" …") + "…" if len(guide) > 100 else ""
        else:
            name = name[:-20] if len(name) > 20 else name[:1]

    return text[:limit]


def card_keyboard(b, with_url=True):
    kb = InlineKeyboardBuilder()
    has_link = with_url and is_http_url(b["link"])
    if has_link:
        kb.button(text="📥 Скачать", url=str(b["link"]).strip())
    kb.button(text=f"👍 {b['likes']}" if b["likes"] else "👍", callback_data=f"bvote_{b['id']}_up")
    kb.button(text=f"👎 {b['dislikes']}" if b["dislikes"] else "👎", callback_data=f"bvote_{b['id']}_down")
    kb.button(text="🔙 Главное меню", callback_data="home_new")
    if has_link:
        kb.adjust(1, 2, 1)
    else:
        kb.adjust(2, 1)
    return kb.as_markup()


async def send_build_card(target_message, b):
    text = render_card_text(b)
    kb = card_keyboard(b)
    if b["photo_id"]:
        try:
            await target_message.answer_photo(b["photo_id"], caption=text, reply_markup=kb, parse_mode="HTML")
            return
        except Exception:
            pass  # file_id мог протухнуть - покажем карточку текстом
    try:
        await target_message.answer(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        # Telegram не принял URL-кнопку - отдаём ссылку текстом, чтобы человек точно получил сборку
        await target_message.answer(
            render_card_text(b, link_in_text=True),
            reply_markup=card_keyboard(b, with_url=False),
            parse_mode="HTML"
        )


async def deliver_build(target_message, user_id, build_id):
    await clear_pending_build(user_id)
    b = await get_build_full(build_id)
    if not b:
        await target_message.answer("❌ Сборка не найдена.")
        return
    await send_build_card(target_message, b)
    try:
        await maybe_offer_notify(target_message, user_id)
    except Exception:
        pass


@dp.callback_query(F.data.startswith("bvote_"))
async def build_vote(callback: CallbackQuery):
    try:
        _, raw_id, kind = callback.data.split("_")
        build_id = int(raw_id)
    except Exception:
        await callback.answer()
        return

    vote = 1 if kind == "up" else -1

    async with db_lock:
        lc = db.cursor()
        lc.execute("SELECT 1 FROM builds WHERE id=?", (build_id,))
        exists = lc.fetchone() is not None
        if exists:
            lc.execute(
                "INSERT OR REPLACE INTO build_votes (build_id, user_id, vote) VALUES (?, ?, ?)",
                (build_id, callback.from_user.id, vote)
            )
            db.commit()

    if not exists:
        await callback.answer("Эта сборка уже удалена", show_alert=True)
        return

    await callback.answer("👍 Спасибо за оценку!" if vote == 1 else "👎 Принято, спасибо!")

    b = await get_build_full(build_id)
    if b:
        try:
            await callback.message.edit_reply_markup(reply_markup=card_keyboard(b))
        except Exception:
            pass


# ---------- уведомления о новых сборках (opt-in) ----------

def notify_prompt_kb():
    kb = InlineKeyboardBuilder()
    kb.button(text="🔔 Да, сообщай", callback_data="notify_on")
    kb.button(text="🔕 Не надо", callback_data="notify_off")
    kb.adjust(2)
    return kb.as_markup()


NOTIFY_PROMPT_TEXT = (
    "🔔 Сообщать тебе о новых сборках?\n\n"
    "Пришлю одно короткое сообщение, когда выйдет новая. Выключить можно в любой момент в меню бота."
)


async def maybe_offer_notify(target_message, user_id):
    """Один раз предлагаем подписаться на уведомления после скачивания."""
    async with db_lock:
        lc = db.cursor()
        lc.execute("SELECT notify_builds, notify_asked_at FROM users WHERE user_id=?", (user_id,))
        row = lc.fetchone()
        if row is None or row[0] is not None or row[1] is not None:
            return
        lc.execute("UPDATE users SET notify_asked_at=? WHERE user_id=?", (now_utc_str(), user_id))
        db.commit()
    await target_message.answer(NOTIFY_PROMPT_TEXT, reply_markup=notify_prompt_kb())


@dp.callback_query(F.data.startswith("notify_"))
async def notify_callbacks(callback: CallbackQuery):
    user_id = callback.from_user.id
    action = callback.data

    back_kb = InlineKeyboardBuilder()
    back_kb.button(text="🔙 Главное меню", callback_data="main_menu")
    back_kb.adjust(1)

    if action in ("notify_on", "notify_off"):
        value = 1 if action == "notify_on" else 0
        async with db_lock:
            db.execute(
                "UPDATE users SET notify_builds=?, notify_asked_at=COALESCE(notify_asked_at, ?) WHERE user_id=?",
                (value, now_utc_str(), user_id)
            )
            db.commit()
        text = (
            "🔔 Готово! Напишу, когда выйдет новая сборка."
            if value else
            "🔕 Ок, уведомления выключены. Включить обратно можно в меню бота."
        )
        await callback.answer()
        try:
            await callback.message.edit_text(text, reply_markup=back_kb.as_markup())
        except Exception:
            await callback.message.answer(text, reply_markup=back_kb.as_markup())
        return

    if action == "notify_toggle":
        async with db_lock:
            lc = db.cursor()
            lc.execute("SELECT notify_builds FROM users WHERE user_id=?", (user_id,))
            row = lc.fetchone()
            new_value = 0 if (row and row[0] == 1) else 1
            lc.execute(
                "UPDATE users SET notify_builds=?, notify_asked_at=COALESCE(notify_asked_at, ?) WHERE user_id=?",
                (new_value, now_utc_str(), user_id)
            )
            db.commit()
        await callback.answer("🔔 Включено" if new_value else "🔕 Выключено")
        text, kb = await render_profile(user_id)
        await show(callback, text, kb)
        return

    if action == "notify_menu":
        # старая кнопка из прежнего меню - ведём в профиль
        await callback.answer()
        text, kb = await render_profile(user_id)
        await show(callback, text, kb)
        return

    await callback.answer()


# ---------- рассылки: сегменты и безопасная отправка ----------

SEGMENTS = {
    "all": (
        "👥 Все (кроме заблокировавших бота)",
        "COALESCE(bot_blocked,0)=0"
    ),
    "active": (
        "✅ Все, кто не в ЧС",
        "COALESCE(bot_blocked,0)=0 AND COALESCE(blacklisted,0)=0"
    ),
    "notify": (
        "🔔 Подписанные на новые сборки",
        "COALESCE(bot_blocked,0)=0 AND COALESCE(blacklisted,0)=0 AND notify_builds=1"
    ),
    "banned": (
        "🚫 Только в ЧС",
        "COALESCE(bot_blocked,0)=0 AND blacklisted=1"
    ),
    "idle14": (
        "😴 Скачивали, но не заходили 14+ дней",
        "COALESCE(bot_blocked,0)=0 AND COALESCE(blacklisted,0)=0 "
        "AND user_id IN (SELECT user_id FROM downloads GROUP BY user_id HAVING MAX(ts) < datetime('now','-14 days')) "
        "AND (last_seen IS NULL OR last_seen < datetime('now','-14 days'))"
    ),
}

# кому ещё не предлагали уведомления
ASK_NOTIFY_WHERE = (
    "COALESCE(bot_blocked,0)=0 AND COALESCE(blacklisted,0)=0 "
    "AND notify_builds IS NULL AND notify_asked_at IS NULL"
)

BROADCAST_DELAY = 0.05  # ~20 сообщений/сек, лимит Telegram около 30

background_tasks = set()


def spawn(coro):
    task = asyncio.create_task(coro)
    background_tasks.add(task)
    task.add_done_callback(background_tasks.discard)
    return task


async def get_ids_where(where):
    # where приходит только из констант выше, не от пользователя
    async with db_lock:
        lc = db.cursor()
        lc.execute(f"SELECT user_id FROM users WHERE {where}")
        return [row[0] for row in lc.fetchall()]


async def count_where(where):
    async with db_lock:
        lc = db.cursor()
        lc.execute(f"SELECT COUNT(*) FROM users WHERE {where}")
        return lc.fetchone()[0]


async def safe_send(user_id, text, photo=None, reply_markup=None, parse_mode="HTML"):
    """Возвращает (статус, ошибка): ok / blocked / error."""
    for _ in range(3):
        try:
            if photo:
                await bot.send_photo(
                    user_id, photo=photo, caption=text or None,
                    parse_mode=parse_mode, reply_markup=reply_markup
                )
            else:
                await bot.send_message(
                    user_id, text, parse_mode=parse_mode, reply_markup=reply_markup
                )
            return "ok", None
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after + 1)
        except TelegramForbiddenError:
            return "blocked", None
        except TelegramBadRequest as e:
            if "chat not found" in str(e).lower():
                return "blocked", None
            return "error", str(e)[:200]
        except Exception as e:
            return "error", str(e)[:200]
    return "error", "лимит Telegram, 3 попытки"


async def run_broadcast(admin_id, user_ids, text, photo=None, reply_markup=None, parse_mode="HTML"):
    sent = blocked = failed = 0
    blocked_ids = []
    first_error = None
    started = time.monotonic()

    for uid in user_ids:
        status, err = await safe_send(uid, text, photo, reply_markup, parse_mode)
        if status == "ok":
            sent += 1
        elif status == "blocked":
            blocked += 1
            blocked_ids.append(uid)
        else:
            failed += 1
            if first_error is None:
                first_error = err
        await asyncio.sleep(BROADCAST_DELAY)

    if blocked_ids:
        async with db_lock:
            db.executemany("UPDATE users SET bot_blocked=1 WHERE user_id=?", [(i,) for i in blocked_ids])
            db.commit()

    report = (
        "✅ Рассылка завершена\n\n"
        f"📨 Доставлено: {sent}\n"
        f"📵 Заблокировали бота: {blocked}\n"
        f"⚠️ Ошибки: {failed}\n"
        f"⏱ Время: {int(time.monotonic() - started)} сек"
    )
    if first_error:
        report += f"\n\nПервая ошибка: {first_error}"
    try:
        await bot.send_message(admin_id, report)
    except Exception:
        pass


@dp.callback_query(F.data.startswith("admin_bc_"))
async def admin_broadcast_segment(callback: CallbackQuery):
    if callback.from_user.id not in ADMINS:
        return

    key = callback.data[len("admin_bc_"):]
    if key not in SEGMENTS:
        await callback.answer("Неизвестный сегмент", show_alert=True)
        return

    user_states[callback.from_user.id] = "broadcast"
    user_temp[callback.from_user.id] = {"segment": key}

    await callback.answer()
    await callback.message.answer(
        f"📢 Сегмент: {SEGMENTS[key][0]}\n\n"
        "Отправь текст рассылки.\n"
        "Форматирование (жирный, ссылки) сохранится. Можно отправить фото с подписью."
    )


@dp.callback_query(F.data == "admin_ask_notify")
async def admin_ask_notify(callback: CallbackQuery):
    if callback.from_user.id not in ADMINS:
        return

    n = await count_where(ASK_NOTIFY_WHERE)
    await callback.answer()

    if n == 0:
        await callback.message.answer("Некому отправлять: всем уже предлагали уведомления.")
        return

    kb = InlineKeyboardBuilder()
    kb.button(text=f"✅ Отправить {n} чел.", callback_data="admin_ask_notify_go")
    kb.button(text="Отмена", callback_data="admin_cancel")
    kb.adjust(1)
    await callback.message.answer(
        "🔔 Разовый вопрос «Сообщать о новых сборках?» с кнопками Да / Не надо.\n\n"
        f"Получат: {n} чел. (без ЧС и без заблокировавших бота, кому ещё не предлагали).",
        reply_markup=kb.as_markup()
    )


@dp.callback_query(F.data == "admin_ask_notify_go")
async def admin_ask_notify_go(callback: CallbackQuery):
    if callback.from_user.id not in ADMINS:
        return

    # читаем и помечаем под одним локом, чтобы повторное нажатие ничего не отправило
    async with db_lock:
        lc = db.cursor()
        lc.execute(f"SELECT user_id FROM users WHERE {ASK_NOTIFY_WHERE}")
        ids = [row[0] for row in lc.fetchall()]
        if ids:
            lc.executemany(
                "UPDATE users SET notify_asked_at=? WHERE user_id=?",
                [(now_utc_str(), i) for i in ids]
            )
            db.commit()

    await callback.answer()

    if not ids:
        await callback.message.answer("Некому отправлять.")
        return

    await callback.message.answer(f"🚀 Отправляю вопрос {len(ids)} чел. Отчёт пришлю по завершении.")
    spawn(run_broadcast(
        callback.from_user.id, ids, NOTIFY_PROMPT_TEXT,
        reply_markup=notify_prompt_kb(), parse_mode=None
    ))


# ---------- админ: карточка сборки (скриншот + описание) ----------

@dp.callback_query(F.data == "admin_card")
async def admin_card(callback: CallbackQuery):
    if callback.from_user.id not in ADMINS:
        return

    async with db_lock:
        lc = db.cursor()
        lc.execute("SELECT id, name, photo_id, description FROM builds ORDER BY id DESC")
        rows = lc.fetchall()

    await callback.answer()

    if not rows:
        await callback.message.answer("❌ Сборок пока нет.")
        return

    user_states[callback.from_user.id] = "card_pick"

    text = "🖼 У какой сборки обновить карточку?\n\n"
    for r in rows:
        marks = ("🖼" if r[2] else "▫️") + ("📝" if r[3] else "▫️")
        text += f"{r[0]} | {marks} | {short_title(r[1], 40)}\n"
    text += "\n🖼 = есть скриншот, 📝 = есть описание\n\nОтправь ID сборки"

    await callback.message.answer(text[:4000])


@dp.callback_query(F.data.startswith("admin_card_"))
async def admin_card_for_build(callback: CallbackQuery):
    if callback.from_user.id not in ADMINS:
        return

    try:
        build_id = int(callback.data.rsplit("_", 1)[1])
    except ValueError:
        await callback.answer()
        return

    user_temp[callback.from_user.id] = {"build_id": build_id}
    user_states[callback.from_user.id] = "card_content"

    await callback.answer()
    await callback.message.answer(CARD_CONTENT_PROMPT)


CARD_CONTENT_PROMPT = (
    "Пришли скриншот с подписью - подпись станет описанием.\n"
    "Или просто текст - он станет описанием.\n\n"
    "Чтобы очистить скриншот и описание - отправь «-»."
)


# ---------- админ: метки источников ----------

@dp.message(F.text.startswith("/admin_link"))
async def admin_link(message: Message):
    if message.from_user.id not in ADMINS:
        return

    parts = message.text.split()
    tag = clean_tag(parts[1]) if len(parts) > 1 else ""

    if not tag:
        await message.answer(
            "🔗 Ссылки с меткой источника.\n\n"
            "Просто в бота:\n/admin_link <метка>\n"
            "Сразу на сборку:\n/admin_link <метка> <ID сборки>\n\n"
            "Примеры:\n/admin_link yt_video12\n/admin_link yt_video12 7\n/admin_link chat_rodina 10\n\n"
            "Метка: латиница, цифры, _ и -. Источники смотри в 📊 Статистике."
        )
        return

    build_id = None
    if len(parts) > 2:
        if not parts[2].isdigit():
            await message.answer("❌ ID сборки должен быть числом. Пример: /admin_link yt_video12 7")
            return
        build_id = int(parts[2])
        found = await get_build_full(build_id)
        if not found:
            await message.answer(f"❌ Сборки с ID {build_id} нет.")
            return

    if build_id:
        link = await build_deeplink(build_id, tag)
        await message.answer(
            f"🔗 Ссылка на сборку {build_id} (метка «{tag}»):\n\n{link}\n\n"
            "Открывает карточку сразу, без меню."
        )
    else:
        username = await get_bot_username()
        await message.answer(f"🔗 Ссылка для метки «{tag}»:\n\nhttps://t.me/{username}?start=src_{tag}")


# ======================================================================
# v3 · прямые ссылки на сборки и публикация сборки в канал
# ======================================================================

def clean_tag(raw):
    return re.sub(r"[^A-Za-z0-9_\-]", "", raw or "")[:40]


def clip(text, limit):
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


_bot_username_cache = None


async def get_bot_username():
    global _bot_username_cache
    if not _bot_username_cache:
        me = await bot.get_me()
        _bot_username_cache = me.username
    return _bot_username_cache


async def build_deeplink(build_id, tag=None):
    username = await get_bot_username()
    if tag:
        return f"https://t.me/{username}?start=build_{build_id}_src_{tag}"
    return f"https://t.me/{username}?start=build_{build_id}"


# ---------- «ожидающая» сборка: человек пришёл по ссылке, но ещё не подписан ----------

async def set_pending_build(user_id, build_id):
    async with db_lock:
        db.execute("UPDATE users SET pending_build=? WHERE user_id=?", (build_id, user_id))
        db.commit()


async def pop_pending_build(user_id):
    async with db_lock:
        lc = db.cursor()
        lc.execute("SELECT pending_build FROM users WHERE user_id=?", (user_id,))
        row = lc.fetchone()
        if not row or not row[0]:
            return None
        lc.execute("UPDATE users SET pending_build=NULL WHERE user_id=?", (user_id,))
        db.commit()
        return row[0]


async def clear_pending_build(user_id):
    async with db_lock:
        db.execute(
            "UPDATE users SET pending_build=NULL WHERE user_id=? AND pending_build IS NOT NULL",
            (user_id,)
        )
        db.commit()


# ---------- рассылка о новой сборке (общая для двух кнопок) ----------

async def launch_build_notification(admin_id, build_id):
    """Возвращает (статус, число получателей): ok / missing / done."""
    b = await get_build_full(build_id)
    if not b:
        return "missing", 0

    # атомарно «занимаем» отправку - по одной сборке рассылка уходит только один раз
    async with db_lock:
        lc = db.cursor()
        lc.execute(
            "UPDATE builds SET notified_at=? WHERE id=? AND notified_at IS NULL",
            (now_utc_str(), build_id)
        )
        claimed = lc.rowcount > 0
        db.commit()

    if not claimed:
        return "done", 0

    ids = await get_ids_where(SEGMENTS["notify"][1])

    text = f"🔥 Вышла новая сборка!\n\n{b['name']}"
    if b["description"]:
        text += f"\n\n{b['description'][:300]}"
    text += "\n\nЖми кнопку 👇\n(отключить уведомления можно в меню бота)"

    kb = InlineKeyboardBuilder()
    kb.button(text="📥 Получить сборку", callback_data=f"build_{build_id}")
    kb.adjust(1)

    spawn(run_broadcast(
        admin_id, ids, text,
        photo=b["photo_id"], reply_markup=kb.as_markup(), parse_mode=None
    ))
    return "ok", len(ids)


@dp.callback_query(F.data.startswith("admin_notify_build_"))
async def admin_notify_build(callback: CallbackQuery):
    if callback.from_user.id not in ADMINS:
        return

    try:
        build_id = int(callback.data.rsplit("_", 1)[1])
    except ValueError:
        await callback.answer()
        return

    status, n = await launch_build_notification(callback.from_user.id, build_id)

    if status == "missing":
        await callback.answer("Сборка не найдена", show_alert=True)
        return
    if status == "done":
        await callback.answer("По этой сборке рассылка уже отправлялась", show_alert=True)
        return

    await callback.answer()
    await callback.message.answer(f"🚀 Рассылка о сборке запущена: {n} чел. Отчёт пришлю по завершении.")


# ---------- публикация сборки в канал ----------

CHANNEL_POST_TAG = "ch"  # метка источника для кнопки под постами канала: ch_b<ID сборки>

_publishing = set()


def channel_post_text(b):
    limit = 1024 if b["photo_id"] else 4000
    name = b["name"] or ""
    desc = clip((b["description"] or "").strip(), 600)
    text = ""

    for _ in range(100):
        lines = [f"🔥 <b>{h(name)}</b>"]
        if desc:
            lines += ["", h(desc)]
        lines += ["", "📥 Скачать — кнопка ниже 👇"]
        text = "\n".join(lines)

        if len(text) <= limit:
            return text

        if desc:
            desc = desc[:-100].rstrip(" …") + "…" if len(desc) > 100 else ""
        else:
            name = name[:-20] if len(name) > 20 else name[:1]

    return text[:limit]


async def channel_post_keyboard(build_id):
    link = await build_deeplink(build_id, f"{CHANNEL_POST_TAG}_b{build_id}")
    kb = InlineKeyboardBuilder()
    kb.button(text="📥 Скачать сборку", url=link)
    return kb.as_markup()


async def get_publish_state(build_id):
    async with db_lock:
        lc = db.cursor()
        lc.execute("SELECT posted_at, notified_at FROM builds WHERE id=?", (build_id,))
        row = lc.fetchone()
    return (row[0], row[1]) if row else (None, None)


@dp.callback_query(F.data == "admin_pub")
async def admin_publish_list(callback: CallbackQuery):
    if callback.from_user.id not in ADMINS:
        return

    async with db_lock:
        lc = db.cursor()
        lc.execute("SELECT id, name, posted_at FROM builds ORDER BY id DESC LIMIT 10")
        rows = lc.fetchall()

    await callback.answer()

    if not rows:
        await callback.message.answer("❌ Сборок пока нет.")
        return

    kb = InlineKeyboardBuilder()
    for bid, name, posted_at in rows:
        mark = "✅" if posted_at else "▫️"
        kb.button(text=f"{mark} {short_title(name, 40)}", callback_data=f"admin_pub_{bid}")
    kb.button(text="Отмена", callback_data="admin_cancel")
    kb.adjust(1)

    await callback.message.answer(
        "📢 Какую сборку опубликовать в канале?\n\n✅ - уже публиковалась",
        reply_markup=kb.as_markup()
    )


@dp.callback_query(F.data.startswith("admin_pub_"))
async def admin_publish_preview(callback: CallbackQuery):
    if callback.from_user.id not in ADMINS:
        return

    try:
        build_id = int(callback.data.rsplit("_", 1)[1])
    except ValueError:
        await callback.answer()
        return

    b = await get_build_full(build_id)
    if not b:
        await callback.answer("Сборка не найдена", show_alert=True)
        return

    await callback.answer()

    # точная копия будущего поста
    try:
        post_kb = await channel_post_keyboard(build_id)
        text = channel_post_text(b)
        if b["photo_id"]:
            await callback.message.answer_photo(b["photo_id"], caption=text, reply_markup=post_kb, parse_mode="HTML")
        else:
            await callback.message.answer(text, reply_markup=post_kb, parse_mode="HTML")
    except Exception as e:
        await callback.message.answer(f"❌ Не удалось собрать превью: {str(e)[:200]}")
        return

    posted_at, notified_at = await get_publish_state(build_id)

    warn = ""
    if posted_at:
        warn = f"⚠️ Эта сборка уже публиковалась ({fmt_date(posted_at)}). Новый пост выйдет отдельно.\n\n"

    ctrl = InlineKeyboardBuilder()
    ctrl.button(text="✅ Опубликовать в канале", callback_data=f"admin_pubgo_{build_id}_0")
    if not notified_at:
        n = await count_where(SEGMENTS["notify"][1])
        ctrl.button(text=f"✅ В канал + уведомить ({n})", callback_data=f"admin_pubgo_{build_id}_1")
    ctrl.button(text="Отмена", callback_data="admin_cancel")
    ctrl.adjust(1)

    await callback.message.answer(
        f"{warn}👆 Так выглядит пост в канале. Публикуем?",
        reply_markup=ctrl.as_markup()
    )


@dp.callback_query(F.data.startswith("admin_pubgo_"))
async def admin_publish_go(callback: CallbackQuery):
    if callback.from_user.id not in ADMINS:
        return

    try:
        raw_id, flag = callback.data[len("admin_pubgo_"):].split("_")
        build_id = int(raw_id)
    except ValueError:
        await callback.answer()
        return
    do_notify = flag == "1"

    if build_id in _publishing:
        await callback.answer("Уже публикую, секунду…")
        return
    _publishing.add(build_id)

    async def finish(text):
        try:
            await callback.message.edit_text(text)
        except Exception:
            await callback.message.answer(text)

    try:
        b = await get_build_full(build_id)
        if not b:
            await callback.answer("Сборка не найдена", show_alert=True)
            return

        await callback.answer()
        try:
            await callback.message.edit_text("⏳ Публикую…")
        except Exception:
            pass

        text = channel_post_text(b)
        note = ""

        try:
            kb = await channel_post_keyboard(build_id)
            if b["photo_id"]:
                try:
                    sent = await bot.send_photo(
                        CHANNEL_ID, photo=b["photo_id"], caption=text, reply_markup=kb, parse_mode="HTML"
                    )
                except TelegramBadRequest:
                    # file_id не подошёл - публикуем текстом
                    sent = await bot.send_message(
                        CHANNEL_ID, channel_post_text(dict(b, photo_id=None)), reply_markup=kb, parse_mode="HTML"
                    )
                    note = "\n\n⚠️ Скриншот Telegram не принял, пост вышел без картинки."
            else:
                sent = await bot.send_message(CHANNEL_ID, text, reply_markup=kb, parse_mode="HTML")
        except Exception as e:
            await finish(
                f"❌ Не получилось опубликовать: {str(e)[:200]}\n\n"
                "Проверь, что бот - админ канала с правом «Публикация сообщений»."
            )
            return

        async with db_lock:
            db.execute(
                "UPDATE builds SET posted_at=?, channel_post_id=? WHERE id=?",
                (now_utc_str(), sent.message_id, build_id)
            )
            db.commit()

        report = f"✅ Опубликовано в канале:\n{CHANNEL_LINK}/{sent.message_id}{note}"

        if do_notify:
            status, n = await launch_build_notification(callback.from_user.id, build_id)
            if status == "ok":
                report += f"\n\n🚀 Уведомления подписчикам запущены: {n} чел. Отчёт пришлю по завершении."
            elif status == "done":
                report += "\n\nℹ️ Уведомления по этой сборке уже отправлялись - повторно не шлю."

        await finish(report)

    finally:
        _publishing.discard(build_id)


# ======================================================================
# v4 · интерфейс: компактное меню, экраны на месте, баннер, единое «ты»
# ======================================================================

def h(value):
    """Экранирование текста для HTML-режима Telegram (нужно экранировать только < > &)."""
    return esc("" if value is None else str(value), quote=False)


def is_not_modified(error):
    return "message is not modified" in str(error).lower()


async def show(callback, text, kb=None):
    """Показывает экран НА МЕСТЕ текущего сообщения (редактирует его),
    а не шлёт новое - чат не превращается в ленту старых меню.
    Если редактировать нельзя (фото-баннер, старое/удалённое сообщение) -
    заменяем сообщение новым."""
    msg = callback.message
    if not getattr(msg, "photo", None):
        try:
            await msg.edit_text(text, reply_markup=kb, parse_mode="HTML")
            return
        except TelegramBadRequest as e:
            if is_not_modified(e):
                return
        except Exception:
            pass
    try:
        await msg.delete()
    except Exception:
        pass
    await msg.answer(text, reply_markup=kb, parse_mode="HTML")


# состояния «жду ввод от пользователя», которые сбрасываются при уходе с экрана
USER_INPUT_STATES = ("task_proof", "redeem_promo")


def reset_input_state(user_id):
    if user_states.get(user_id) in USER_INPUT_STATES:
        user_states.pop(user_id, None)
        user_temp.pop(user_id, None)


# ---------- настройки (баннер) ----------

async def get_setting(key):
    async with db_lock:
        lc = db.cursor()
        lc.execute("SELECT value FROM settings WHERE key=?", (key,))
        row = lc.fetchone()
    return row[0] if row else None


async def set_setting(key, value):
    async with db_lock:
        if value is None:
            db.execute("DELETE FROM settings WHERE key=?", (key,))
        else:
            db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))
        db.commit()


# ---------- главный экран ----------

def home_text():
    return (
        "🔥 <b>Сборки Rodina RP от Чапы</b>\n\n"
        "Нажми «Скачать сборку», выбери нужную — бот пришлёт ссылку."
    )


async def send_home(message, user_id):
    """Главный экран новым сообщением (для /start): баннер + меню, либо просто текст + меню."""
    kb = main_menu(user_id)
    banner = await get_setting("banner_file_id")
    if banner:
        try:
            await message.answer_photo(banner, caption=home_text(), reply_markup=kb, parse_mode="HTML")
            return
        except Exception:
            pass  # file_id не подошёл - показываем без баннера
    await message.answer(home_text(), reply_markup=kb, parse_mode="HTML")


async def go_home(callback):
    """Главный экран по кнопке «Главное меню»: заменяет текущий экран."""
    user_id = callback.from_user.id
    reset_input_state(user_id)
    kb = main_menu(user_id)
    banner = await get_setting("banner_file_id")
    msg = callback.message

    if banner:
        if getattr(msg, "photo", None):
            try:
                await msg.edit_caption(caption=home_text(), reply_markup=kb, parse_mode="HTML")
                return
            except TelegramBadRequest as e:
                if is_not_modified(e):
                    return
            except Exception:
                pass
        try:
            await msg.delete()
        except Exception:
            pass
        try:
            await msg.answer_photo(banner, caption=home_text(), reply_markup=kb, parse_mode="HTML")
            return
        except Exception:
            pass

    await show(callback, home_text(), kb)


@dp.callback_query(F.data == "home_new")
async def home_new(callback: CallbackQuery):
    # кнопка под карточкой сборки: меню приходит НОВЫМ сообщением, карточка со ссылкой остаётся
    await callback.answer()
    await send_home(callback.message, callback.from_user.id)


# ---------- профиль ----------

async def render_profile(user_id):
    async with db_lock:
        lc = db.cursor()
        lc.execute("SELECT coins, notify_builds FROM users WHERE user_id=?", (user_id,))
        row = lc.fetchone()
        lc.execute("SELECT COUNT(*) FROM referrals WHERE referrer_id=? AND confirmed=1", (user_id,))
        friends = lc.fetchone()[0]
        lc.execute("SELECT COUNT(DISTINCT build_id) FROM downloads WHERE user_id=?", (user_id,))
        downloaded = lc.fetchone()[0]

    coins = (row[0] if row else 0) or 0
    notify_on = bool(row and row[1] == 1)

    text = (
        "👤 <b>Профиль</b>\n\n"
        f"Баланс: {coins} монет\n"
        f"Скачано сборок: {downloaded}\n"
        f"Приглашено друзей: {friends}\n"
        f"Уведомления о сборках: {'включены' if notify_on else 'выключены'}"
    )

    kb = InlineKeyboardBuilder()
    kb.button(
        text="🔕 Выключить уведомления" if notify_on else "🔔 Включить уведомления",
        callback_data="notify_toggle"
    )
    kb.button(text="🔙 Главное меню", callback_data="main_menu")
    kb.adjust(1)
    return text, kb.as_markup()


# ---------- реферальный экран (общий для кнопки и /ref) ----------

async def build_referral_text(user_id):
    username = await get_bot_username()
    link = f"https://t.me/{username}?start=ref_{user_id}"

    async with db_lock:
        lc = db.cursor()
        lc.execute("SELECT COUNT(*) FROM referrals WHERE referrer_id=? AND confirmed=1", (user_id,))
        cnt = lc.fetchone()[0]
        reward = None
        if cnt >= 5:
            lc.execute("SELECT title, youtube_link, download_link FROM rewards WHERE active=1 LIMIT 1")
            reward = lc.fetchone()

    text = (
        "🤝 <b>Партнёрская программа</b>\n\n"
        "Пригласи 5 друзей — получи уникальную партнёрскую сборку.\n\n"
        f"Твоя ссылка (нажми, чтобы скопировать):\n<code>{h(link)}</code>\n\n"
        "Друг засчитывается после подписки на канал.\n\n"
        f"Подтверждено: {cnt}/5"
    )

    if cnt >= 5:
        if reward:
            text += (
                f"\n\n🎁 <b>Награда:</b> {h(reward[0])}\n"
                f"Обзор (YouTube): {h(reward[1])}\n"
                f"Ссылка: {h(reward[2])}"
            )
        else:
            text += "\n\n🎁 Награда ещё не задана."
    return text


# ---------- админ: баннер приветствия ----------

@dp.callback_query(F.data == "admin_banner")
async def admin_banner(callback: CallbackQuery):
    if callback.from_user.id not in ADMINS:
        return

    current = await get_setting("banner_file_id")
    user_states[callback.from_user.id] = "banner_set"

    await callback.answer()
    await callback.message.answer(
        "🖼 <b>Баннер приветствия</b>\n\n"
        f"Сейчас: {'установлен' if current else 'не задан'}.\n\n"
        "Пришли картинку — она появится над главным меню "
        "(лучше горизонтальную, около 1280×720, отправляй как фото, не файлом).\n"
        "Чтобы убрать баннер, отправь «-».",
        parse_mode="HTML"
    )


# ---------- админ-панель: главное меню и группы ----------

def admin_home_text(pending):
    text = "⚙️ <b>Админ-панель</b>"
    if pending:
        text += f"\n\nЗаявок на проверке: {pending}"
    return text


async def pending_reviews_count():
    async with db_lock:
        lc = db.cursor()
        lc.execute("SELECT COUNT(*) FROM task_submissions WHERE status='pending'")
        return lc.fetchone()[0]


@dp.message(F.text.startswith("/start"))
async def start(message: Message):

    parts = message.text.split(maxsplit=1)
    param = parts[1].strip() if len(parts) > 1 else None

    # Прямая ссылка на сборку: build_<ID> или build_<ID>_src_<метка>
    build_param = None
    if param:
        m = re.fullmatch(r"build_(\d{1,9})(?:_src_([A-Za-z0-9_\-]{1,40}))?", param)
        if m:
            build_param = (int(m.group(1)), m.group(2))

    async with db_lock:
        # Проверяем - новый пользователь или уже был в боте
        cursor.execute("SELECT user_id FROM users WHERE user_id=?", (message.from_user.id,))
        is_new_user = cursor.fetchone() is None

        # Источник: src_<метка> из ссылки, "ref" для рефералок, "direct" без параметра
        source_val = None
        if is_new_user:
            if build_param:
                source_val = build_param[1] or f"build_{build_param[0]}"
            elif param and param.startswith("src_"):
                source_val = re.sub(r"[^A-Za-z0-9_\-]", "", param[4:])[:40] or "direct"
            elif param:
                source_val = "ref"
            else:
                source_val = "direct"

        cursor.execute(
            "INSERT OR IGNORE INTO users (user_id, created_at, source, last_seen) VALUES (?, ?, ?, ?)",
            (message.from_user.id, now_utc_str(), source_val, now_utc_str())
        )
        db.commit()

        cursor.execute("SELECT ref_code FROM users WHERE user_id=?", (message.from_user.id,))
        row = cursor.fetchone()

        if not row or not row[0]:
            newcode = ''.join(random.choices(string.ascii_letters + string.digits, k=6))
            cursor.execute("UPDATE users SET ref_code=? WHERE user_id=?", (newcode, message.from_user.id))
            db.commit()
            ref_code = newcode
        else:
            ref_code = row[0]

        referrer_id = None
        if param and is_new_user:
            if param.startswith("ref_"):
                try:
                    referrer_id = int(param[4:])
                except Exception:
                    referrer_id = None
            elif not param.startswith("src_") and not build_param:
                cursor.execute("SELECT user_id FROM users WHERE ref_code=?", (param,))
                ref = cursor.fetchone()
                if ref:
                    referrer_id = ref[0]

            if referrer_id and referrer_id != message.from_user.id:
                cursor.execute("INSERT OR IGNORE INTO referrals (referrer_id, referred_id) VALUES (?, ?)", (referrer_id, message.from_user.id))
                db.commit()

    # await - за пределами лока
    if param and is_new_user and referrer_id and referrer_id != message.from_user.id:
        try:
            if await check_sub(message.from_user.id):
                await confirm_referral_if_pending(message.from_user.id)
        except Exception:
            pass

    if build_param:
        target_id = build_param[0]
        async with db_lock:
            cursor.execute("SELECT 1 FROM builds WHERE id=?", (target_id,))
            build_exists = cursor.fetchone() is not None

        if build_exists:
            await build_flow(SimpleNamespace(from_user=message.from_user, message=message), target_id)
            return

        await message.answer("⚠️ Эта сборка больше недоступна. Актуальные - в меню 👇")
    else:
        await clear_pending_build(message.from_user.id)

    await send_home(message, message.from_user.id)


@dp.message(F.text == "/admin")
async def admin_panel(message: Message):

    if message.from_user.id not in ADMINS:
        return

    pending = await pending_reviews_count()
    await message.answer(
        admin_home_text(pending),
        reply_markup=admin_menu(pending),
        parse_mode="HTML"
    )


@dp.message(F.text == "/users")
async def show_users(message: Message):
    if message.from_user.id not in ADMINS:
        return

    async with db_lock:
        lc = db.cursor()
        lc.execute("SELECT user_id FROM users")
        users = lc.fetchall()

    if not users:
        await message.answer("👤 Пользователей пока нет.")
        return

    text = f"👥 Всего пользователей: {len(users)}\n\n"
    ids = [str(u[0]) for u in users]
    text += "\n".join(ids)

    if len(text) > 4000:
        text = text[:4000] + "\n..."

    await message.answer(text)


@dp.message(F.text.startswith("/admin_unref"))
async def admin_unref(message: Message):
    if message.from_user.id not in ADMINS:
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("❌ Формат: /admin_unref <user_id>")
        return

    try:
        target_id = int(parts[1].strip())
    except ValueError:
        await message.answer("❌ Неверный user_id - должно быть число")
        return

    no_pending_msg = None
    referrer_id = None
    cnt = 0
    reward_to_send = None

    async with db_lock:
        lc = db.cursor()
        lc.execute("SELECT id, referrer_id FROM referrals WHERE referred_id=? AND confirmed=0", (target_id,))
        pending = lc.fetchone()

        if not pending:
            lc.execute("SELECT 1 FROM referrals WHERE referred_id=?", (target_id,))
            exists = lc.fetchone()
            no_pending_msg = f"ℹ️ Реферал {target_id} уже подтверждён ранее" if exists else f"❌ Реферал {target_id} не найден в базе"
        else:
            referral_id, referrer_id = pending
            lc.execute("UPDATE referrals SET confirmed=1 WHERE id=? AND confirmed=0", (referral_id,))
            db.commit()
            if lc.rowcount == 0:
                no_pending_msg = f"ℹ️ Реферал {target_id} уже подтверждён ранее"
                referrer_id = None
            else:
                lc.execute("SELECT COUNT(*) FROM referrals WHERE referrer_id=? AND confirmed=1", (referrer_id,))
                cnt = lc.fetchone()[0]
            if cnt >= 5:
                lc.execute("SELECT id, title, youtube_link, download_link FROM rewards WHERE active=1 LIMIT 1")
                reward_row = lc.fetchone()
                if reward_row:
                    lc.execute("SELECT 1 FROM reward_issued WHERE user_id=? AND reward_id=?", (referrer_id, reward_row[0]))
                    already = lc.fetchone()
                    if not already:
                        reward_to_send = reward_row
                        lc.execute("INSERT INTO reward_issued (user_id, reward_id) VALUES (?, ?)", (referrer_id, reward_row[0]))
                        db.commit()

    if no_pending_msg:
        await message.answer(no_pending_msg)
        return

    await message.answer(f"✅ Реферал {target_id} вручную подтверждён. Реферер уведомлён.")

    try:
        await bot.send_message(referrer_id, f"✅ Твой реферал был подтверждён администратором. Подтверждено: {cnt}/5")
    except Exception:
        pass

    if reward_to_send:
        try:
            await bot.send_message(referrer_id, f"🎁 Награда за 5 рефералов: {reward_to_send[1]}\n\nОбзор: {reward_to_send[2]}\nСсылка: {reward_to_send[3]}")
        except Exception:
            pass


@dp.message(F.text == "/admin_refs")
async def show_refs(message: Message):
    if message.from_user.id not in ADMINS:
        return

    async with db_lock:
        lc = db.cursor()
        lc.execute("""
            SELECT r.referrer_id, r.referred_id, r.confirmed, r.ts
            FROM referrals r
            ORDER BY r.ts DESC
        """)
        rows = lc.fetchall()
        lc.execute("SELECT COUNT(*) FROM referrals WHERE confirmed=1")
        total_confirmed = lc.fetchone()[0]
        lc.execute("SELECT COUNT(*) FROM referrals WHERE confirmed=0")
        total_pending = lc.fetchone()[0]

    if not rows:
        await message.answer("📭 Рефералов пока нет.")
        return

    text = f"👥 Рефералы: всего {len(rows)} | ✅ подтверждено {total_confirmed} | ⏳ ожидает {total_pending}\n\n"

    for r in rows:
        referrer_id, referred_id, confirmed, ts = r
        status = "✅" if confirmed else "⏳"
        date = ts[:10] if ts else "?"
        text += f"{status} {referrer_id} → {referred_id} | {date}\n"

    chunks = [text[i:i+4000] for i in range(0, len(text), 4000)]
    for chunk in chunks:
        await message.answer(chunk)


@dp.message(F.text == "/admin_banlist")
async def admin_banlist(message: Message):
    if message.from_user.id not in ADMINS:
        return

    async with db_lock:
        lc = db.cursor()
        lc.execute("SELECT user_id FROM users WHERE blacklisted=1")
        rows = lc.fetchall()

    if not rows:
        await message.answer("✅ Чёрный список пуст.")
        return

    text = f"🚫 Чёрный список ({len(rows)} чел.):\n\n"
    for row in rows:
        text += f"• {row[0]}\n"

    if len(text) <= 4000:
        await message.answer(text)
    else:
        data = "\n".join(str(row[0]) for row in rows).encode("utf-8")
        await message.answer_document(
            BufferedInputFile(data, filename="banlist.txt"),
            caption=f"🚫 Чёрный список: {len(rows)} чел."
        )


@dp.message(F.text.startswith("/admin_ban"))
async def admin_ban(message: Message):
    if message.from_user.id not in ADMINS:
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip().isdigit():
        await message.answer("❌ Использование: /admin_ban <user_id>")
        return

    target_id = int(parts[1].strip())

    async with db_lock:
        cursor.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (target_id,))
        cursor.execute("UPDATE users SET blacklisted=1, ban_reason='manual' WHERE user_id=? AND (blacklisted IS NULL OR blacklisted=0)", (target_id,))
        already_banned = cursor.rowcount == 0
        db.commit()

    await message.answer(f"🚫 Пользователь {target_id} добавлен в ЧС.")

    if already_banned:
        return

    try:
        await bot.send_message(target_id, "❌ Тебя добавили в чёрный список бота. Напиши администрации.")
    except Exception:
        pass


@dp.message(F.text.startswith("/admin_unban"))
async def admin_unban(message: Message):
    if message.from_user.id not in ADMINS:
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip().isdigit():
        await message.answer("❌ Использование: /admin_unban <user_id>")
        return

    target_id = int(parts[1].strip())

    async with db_lock:
        lc = db.cursor()
        lc.execute("SELECT blacklisted FROM users WHERE user_id=?", (target_id,))
        row = lc.fetchone()
        not_found = row is None

        lc.execute("UPDATE users SET blacklisted=0 WHERE user_id=? AND blacklisted=1", (target_id,))
        actually_unbanned = lc.rowcount > 0
        db.commit()

    if not_found:
        await message.answer(f"⚠️ Пользователь {target_id} не найден в базе.")
        return
    if not actually_unbanned:
        await message.answer(f"⚠️ Пользователь {target_id} не в ЧС.")
        return

    await message.answer(f"✅ Пользователь {target_id} удалён из ЧС.")

    try:
        await bot.send_message(target_id, "✅ Тебя убрали из чёрного списка бота. С возвращением!")
    except Exception:
        pass


async def render_builds(callback):
    """Список сборок на месте текущего экрана. Ответ на callback отправляет вызывающий."""
    user_id = callback.from_user.id

    # Проверяем ЧС (мягкий: однократное прощение при повторной подписке)
    if await blacklist_check(callback):
        return

    # Проверяем подписку перед показом списка
    if not await check_sub(user_id):
        await show(
            callback,
            "📢 <b>Нужна подписка</b>\n\nПодпишись на канал и нажми «✅ Проверить».",
            sub_menu()
        )
        return

    async with db_lock:
        lc = db.cursor()
        lc.execute("SELECT id, name, recommended FROM builds ORDER BY id DESC")
        builds_list = lc.fetchall()

    if not builds_list:
        back = InlineKeyboardBuilder()
        back.button(text="🔙 Главное меню", callback_data="main_menu")
        await show(callback, "📦 <b>Сборки</b>\n\nСборок пока нет.", back.as_markup())
        return

    kb = InlineKeyboardBuilder()

    for build_id, name, recommended in builds_list:
        kb.button(
            text=f"🔥 {short_title(name)}{' ⭐' if recommended == 1 else ''}",
            callback_data=f"build_{build_id}"
        )

    kb.button(text="🔙 Главное меню", callback_data="main_menu")
    kb.adjust(1)

    await show(
        callback,
        "📦 <b>Сборки</b>\n\nВыбери сборку. ⭐ — рекомендуем.",
        kb.as_markup()
    )


@dp.callback_query(F.data == "builds")
async def builds(callback: CallbackQuery):
    reset_input_state(callback.from_user.id)
    await callback.answer()
    await render_builds(callback)


@dp.callback_query(F.data == "profile")
async def profile(callback: CallbackQuery):
    reset_input_state(callback.from_user.id)
    await callback.answer()
    text, kb = await render_profile(callback.from_user.id)
    await show(callback, text, kb)


@dp.callback_query(F.data == "wallet")
async def wallet(callback: CallbackQuery):
    # старая кнопка «Мой баланс» в уже отправленных сообщениях - ведём в профиль
    await profile(callback)


@dp.callback_query(F.data == "bonuses")
async def bonuses(callback: CallbackQuery):
    user_id = callback.from_user.id
    reset_input_state(user_id)
    await callback.answer()

    async with db_lock:
        lc = db.cursor()
        lc.execute("SELECT coins FROM users WHERE user_id=?", (user_id,))
        row = lc.fetchone()

    coins = (row[0] if row else 0) or 0

    kb = InlineKeyboardBuilder()
    kb.button(text="🤝 Друзья", callback_data="referral")
    kb.button(text="📋 Задания", callback_data="tasks")
    kb.button(text="🛒 Магазин", callback_data="shop")
    kb.button(text="🎟 Промокод", callback_data="promo_enter")
    kb.button(text="🔙 Главное меню", callback_data="main_menu")
    kb.adjust(2, 2, 1)

    await show(
        callback,
        "🎁 <b>Бонусы</b>\n\n"
        f"Баланс: {coins} монет\n\n"
        "Приглашай друзей, выполняй задания и трать монеты в магазине.",
        kb.as_markup()
    )


@dp.callback_query(F.data == "tasks")
async def user_tasks(callback: CallbackQuery):
    reset_input_state(callback.from_user.id)
    await callback.answer()

    async with db_lock:
        lc = db.cursor()
        lc.execute(
            """
            SELECT t.id, t.title, t.reward
            FROM tasks t
            WHERE t.active=1
              AND NOT EXISTS (
                  SELECT 1
                  FROM task_submissions s
                  WHERE s.task_id=t.id
                    AND s.user_id=?
                    AND s.status IN ('pending', 'approved')
              )
            ORDER BY t.id DESC
            """,
            (callback.from_user.id,)
        )
        rows = lc.fetchall()

    kb = InlineKeyboardBuilder()

    if not rows:
        kb.button(text="🔙 Бонусы", callback_data="bonuses")
        await show(
            callback,
            "📋 <b>Задания</b>\n\nАктивных заданий пока нет.",
            kb.as_markup()
        )
        return

    for task_id, title, reward in rows:
        kb.button(
            text=f"📋 {title} — {reward} монет",
            callback_data=f"task_view_{task_id}"
        )
    kb.button(text="🔙 Бонусы", callback_data="bonuses")
    kb.adjust(1)

    await show(
        callback,
        "📋 <b>Задания</b>\n\nВыбери задание, выполни и отправь доказательство на проверку.",
        kb.as_markup()
    )


@dp.callback_query(F.data.startswith("task_view_"))
async def task_view(callback: CallbackQuery):
    try:
        task_id = int(callback.data.rsplit("_", 1)[1])
    except ValueError:
        await callback.answer("Некорректное задание.", show_alert=True)
        return

    user_id = callback.from_user.id
    async with db_lock:
        lc = db.cursor()
        lc.execute(
            "SELECT title, description, reward FROM tasks WHERE id=? AND active=1",
            (task_id,)
        )
        task = lc.fetchone()
        lc.execute(
            """
            SELECT status FROM task_submissions
            WHERE task_id=? AND user_id=?
            ORDER BY id DESC LIMIT 1
            """,
            (task_id, user_id)
        )
        previous = lc.fetchone()

    if not task:
        await callback.answer("Задание больше недоступно.", show_alert=True)
        return

    title, description, reward = task
    text = f"📋 <b>{h(title)}</b>\n\n{h(description)}\n\n🎁 Награда: {reward} монет"
    kb = InlineKeyboardBuilder()
    if previous and previous[0] == "approved":
        text += "\n\n✅ Это задание уже одобрено."
    elif previous and previous[0] == "pending":
        text += "\n\n⏳ Выполнение уже отправлено на проверку."
    else:
        kb.button(
            text="✅ Отправить выполнение",
            callback_data=f"task_submit_{task_id}"
        )
    kb.button(text="🔙 К заданиям", callback_data="tasks")
    kb.adjust(1)

    await callback.answer()
    await show(callback, text, kb.as_markup())


@dp.callback_query(F.data.startswith("task_submit_"))
async def task_submit_start(callback: CallbackQuery):
    try:
        task_id = int(callback.data.rsplit("_", 1)[1])
    except ValueError:
        await callback.answer("Некорректное задание.", show_alert=True)
        return

    user_id = callback.from_user.id
    async with db_lock:
        lc = db.cursor()
        lc.execute("SELECT title FROM tasks WHERE id=? AND active=1", (task_id,))
        task = lc.fetchone()
        lc.execute(
            """
            SELECT status FROM task_submissions
            WHERE task_id=? AND user_id=?
            ORDER BY id DESC LIMIT 1
            """,
            (task_id, user_id)
        )
        previous = lc.fetchone()

    if not task:
        await callback.answer("Задание больше недоступно.", show_alert=True)
        return
    if previous and previous[0] == "approved":
        await callback.answer("Это задание уже одобрено.", show_alert=True)
        return
    if previous and previous[0] == "pending":
        await callback.answer("Выполнение уже находится на проверке.", show_alert=True)
        return

    user_states[user_id] = "task_proof"
    user_temp[user_id] = {"task_id": task_id}

    kb = InlineKeyboardBuilder()
    kb.button(text="🔙 К заданиям", callback_data="tasks")
    kb.adjust(1)

    await callback.answer()
    await show(
        callback,
        "📎 <b>Доказательство</b>\n\n"
        f"Задание: {h(task[0])}\n\n"
        "Отправь текст, фото с подписью или документ — заявка уйдёт на проверку.",
        kb.as_markup()
    )


@dp.callback_query(F.data == "shop")
async def shop(callback: CallbackQuery):
    reset_input_state(callback.from_user.id)
    await callback.answer()

    async with db_lock:
        lc = db.cursor()
        lc.execute(
            "SELECT id, name, price FROM shop_items WHERE active=1 ORDER BY id DESC"
        )
        rows = lc.fetchall()

    kb = InlineKeyboardBuilder()
    for item_id, name, price in rows:
        kb.button(text=f"🛍 {name} — {price} монет", callback_data=f"shop_item_{item_id}")
    kb.button(text="🔙 Бонусы", callback_data="bonuses")
    kb.adjust(1)

    await show(
        callback,
        "🛒 <b>Магазин</b>\n\n" + ("Выбери товар." if rows else "Товаров пока нет."),
        kb.as_markup()
    )


@dp.callback_query(F.data == "promo_enter")
async def promo_enter(callback: CallbackQuery):
    user_states[callback.from_user.id] = "redeem_promo"

    kb = InlineKeyboardBuilder()
    kb.button(text="🔙 Бонусы", callback_data="bonuses")
    kb.adjust(1)

    await callback.answer()
    await show(
        callback,
        "🎟 <b>Промокод</b>\n\nОтправь код одним сообщением. Регистр не важен.",
        kb.as_markup()
    )


@dp.callback_query(F.data.startswith("shop_item_"))
async def shop_item(callback: CallbackQuery):
    try:
        item_id = int(callback.data.rsplit("_", 1)[1])
    except ValueError:
        await callback.answer("Некорректный товар.", show_alert=True)
        return

    async with db_lock:
        lc = db.cursor()
        lc.execute(
            """
            SELECT id, name, description, price
            FROM shop_items WHERE id=? AND active=1
            """,
            (item_id,)
        )
        item = lc.fetchone()

    if not item:
        await callback.answer("Товар больше недоступен.", show_alert=True)
        return

    item_id, name, description, price = item
    kb = InlineKeyboardBuilder()
    kb.button(text=f"✅ Купить за {price} монет", callback_data=f"shop_buy_{item_id}")
    kb.button(text="🔙 В магазин", callback_data="shop")
    kb.adjust(1)

    await callback.answer()
    await show(
        callback,
        f"🛍 <b>{h(name)}</b>\n\n{h(description)}\n\nЦена: {price} монет",
        kb.as_markup()
    )


@dp.callback_query(F.data.startswith("shop_buy_"))
async def shop_buy(callback: CallbackQuery):
    try:
        item_id = int(callback.data.rsplit("_", 1)[1])
    except ValueError:
        await callback.answer("Некорректный товар.", show_alert=True)
        return

    user_id = callback.from_user.id
    purchase_id = None
    item = None

    async with db_lock:
        lc = db.cursor()
        lc.execute(
            """
            SELECT id, name, description, price, item_type, target
            FROM shop_items WHERE id=? AND active=1
            """,
            (item_id,)
        )
        item = lc.fetchone()
        lc.execute("SELECT blacklisted, coins FROM users WHERE user_id=?", (user_id,))
        user_row = lc.fetchone()

        if not item:
            error = "Товар больше недоступен."
        elif not user_row:
            error = "Сначала запусти бота командой /start."
        elif item[4] == "unban" and user_row[0] != 1:
            error = "У тебя нет активного ЧС."
        elif user_row[0] == 1 and item[4] != "unban":
            error = "Покупки недоступны для пользователей из ЧС."
        elif user_row[1] < item[3]:
            error = f"Недостаточно монет. Нужно: {item[3]}."
        else:
            existing = None
            if item[4] != "unban":
                lc.execute(
                    """
                    SELECT id FROM purchases
                    WHERE item_id=? AND user_id=? AND status IN ('pending', 'approved', 'delivered')
                    ORDER BY id DESC LIMIT 1
                    """,
                    (item_id, user_id)
                )
                existing = lc.fetchone()
            if existing:
                error = "Этот товар уже куплен или ожидает выдачи."
            else:
                lc.execute(
                    """
                    INSERT INTO purchases (item_id, user_id, price, status)
                    VALUES (?, ?, ?, 'pending')
                    """,
                    (item_id, user_id, item[3])
                )
                purchase_id = lc.lastrowid
                lc.execute(
                    "UPDATE users SET coins=coins-? WHERE user_id=?",
                    (item[3], user_id)
                )
                db.commit()
                error = None

    if error:
        await callback.answer(error, show_alert=True)
        return

    if item[4] == "unban":
        async with db_lock:
            lc = db.cursor()
            lc.execute(
                """
                UPDATE purchases
                SET status='delivered', delivered_ts=CURRENT_TIMESTAMP
                WHERE id=? AND status='pending'
                """,
                (purchase_id,)
            )
            changed = lc.rowcount
            if changed:
                lc.execute(
                    "UPDATE users SET blacklisted=0 WHERE user_id=?",
                    (user_id,)
                )
            db.commit()

        if not changed:
            await callback.answer("Покупка уже обработана.", show_alert=True)
            return

        await callback.message.answer(
            f"✅ Покупка оформлена: {item[1]}\n\n"
            "Чёрный список снят, теперь снова доступны задания, "
            "магазин и скачивание сборок."
        )
        await callback.answer("✅ ЧС снят")

        for admin_id in ADMINS:
            try:
                await bot.send_message(
                    admin_id,
                    f"🔓 Пользователь купил снятие ЧС\n\n"
                    f"Покупатель: {format_user(callback.from_user)}\n"
                    f"Цена: {item[3]} монет\n"
                    f"Покупка #{purchase_id}"
                )
            except Exception:
                pass
        return

    try:
        if item[4] != "channel":
            raise RuntimeError("Неизвестный тип товара")

        invite = await bot.create_chat_invite_link(
            int(item[5]),
            name=f"purchase-{purchase_id}",
            creates_join_request=True,
            expire_date=datetime.now(timezone.utc) + timedelta(days=7)
        )
        delivery_link = invite.invite_link
    except Exception:
        async with db_lock:
            lc = db.cursor()
            lc.execute(
                "UPDATE purchases SET status='failed' WHERE id=? AND status='pending'",
                (purchase_id,)
            )
            if lc.rowcount:
                lc.execute(
                    "UPDATE users SET coins=coins+? WHERE user_id=?",
                    (item[3], user_id)
                )
            db.commit()

        await callback.answer(
            "Не удалось подготовить доступ. Монеты возвращены. Сообщи администратору.",
            show_alert=True
        )
        return

    async with db_lock:
        lc = db.cursor()
        lc.execute(
            """
            UPDATE purchases
            SET status='approved', delivery_link=?
            WHERE id=? AND status='pending'
            """,
            (delivery_link, purchase_id)
        )
        db.commit()

    await callback.message.answer(
        f"✅ Покупка оформлена: {item[1]}\n\n"
        "Перейди по ссылке ниже и отправь заявку на вступление. "
        "Бот автоматически одобрит её:\n"
        f"{delivery_link}"
    )
    await callback.answer("Покупка подтверждена")

    for admin_id in ADMINS:
        try:
            await bot.send_message(
                admin_id,
                f"🛒 Новая покупка\n\n"
                f"Товар: {item[1]}\n"
                f"Цена: {item[3]} монет\n"
                f"Покупатель: {format_user(callback.from_user)}\n"
                f"Покупка #{purchase_id}\n"
                "Доступ выдается автоматически после заявки на вступление."
            )
        except Exception:
            pass


@dp.callback_query(F.data.startswith("build_"))
async def build(callback: CallbackQuery):

    build_id = int(
        callback.data.split("_")[1]
    )
    await build_flow(callback, build_id)


async def build_flow(callback, build_id):
    """Выдача сборки. callback - любой объект с from_user и message.answer:
    нужен и для нажатия кнопки, и для прямой ссылки из /start."""
    user_id = callback.from_user.id

    # 1. ЧС (мягкий: однократное прощение при повторной подписке)
    if await blacklist_check(callback):
        return

    # 2. Проверяем подписку (await)
    is_subscribed = await check_sub(user_id)

    if is_subscribed:
        await confirm_referral_if_pending(user_id)

    if not is_subscribed:
        # запоминаем сборку: после «✅ Проверить» бот выдаст её сам
        await set_pending_build(user_id, build_id)
        await callback.message.answer(
            "📢 <b>Нужна подписка</b>\n\nПодпишись на канал и нажми «✅ Проверить» — сборку пришлю сразу.",
            reply_markup=sub_menu(),
            parse_mode="HTML"
        )
        return

    # 4. Проверяем спонсоров (await)
    unmet_channels, unmet_links = await get_unmet_sponsors(user_id)
    if unmet_channels or unmet_links:
        await callback.message.answer(
            "📋 Для получения сборки подпишись на всех спонсоров:",
            reply_markup=build_sponsor_keyboard(unmet_channels, unmet_links, build_id)
        )
        return

    # 5. Читаем и обновляем сборку - всё в одном локе
    async with db_lock:
        lc = db.cursor()
        lc.execute("SELECT * FROM builds WHERE id=?", (build_id,))
        build_row = lc.fetchone()
        if build_row:
            lc.execute("UPDATE builds SET downloads = downloads + 1 WHERE id=?", (build_id,))
            try:
                lc.execute("INSERT INTO downloads (user_id, build_id) VALUES (?, ?)", (user_id, build_id))
            except Exception:
                pass
            db.commit()

    if not build_row:
        await callback.message.answer("❌ Сборка не найдена.")
        return

    await deliver_build(callback.message, user_id, build_id)


@dp.callback_query(F.data == "check_sub")
async def check(callback: CallbackQuery):

    is_subscribed = await check_sub(
        callback.from_user.id
    )

    if is_subscribed:

        await confirm_referral_if_pending(callback.from_user.id)

        await callback.answer(
            "✅ Подписка найдена",
            show_alert=True
        )

        pending = await pop_pending_build(callback.from_user.id)
        if pending:
            try:
                await callback.message.delete()
            except Exception:
                pass
            await build_flow(callback, pending)
        else:
            await render_builds(callback)

    else:

        await callback.answer(
            "❌ Ты не подписан",
            show_alert=True
        )


@dp.callback_query(F.data.startswith("check_sponsor_sub_"))
async def check_sponsor_sub(callback: CallbackQuery):
    build_id = int(callback.data.split("_")[-1])
    user_id = callback.from_user.id

    unmet_channels, unmet_links = await get_unmet_sponsors(user_id)

    if unmet_channels:
        names = ", ".join(s[4] for s in unmet_channels)
        await callback.answer(f"❌ Ещё не подписан: {names}", show_alert=True)
        return

    # Записываем acks и читаем сборку под локом
    async with db_lock:
        lc = db.cursor()
        for sponsor in unmet_links:
            sid = sponsor[0]
            lc.execute("INSERT OR IGNORE INTO sponsor_acks (user_id, sponsor_id) VALUES (?, ?)", (user_id, sid))
        lc.execute("SELECT * FROM builds WHERE id=?", (build_id,))
        build_row = lc.fetchone()
        if build_row:
            lc.execute("UPDATE builds SET downloads = downloads + 1 WHERE id=?", (build_id,))
            try:
                lc.execute("INSERT INTO downloads (user_id, build_id) VALUES (?, ?)", (user_id, build_id))
            except Exception:
                pass
        db.commit()

    await callback.message.delete()

    if not build_row:
        await callback.message.answer("❌ Сборка не найдена.")
        return

    await deliver_build(callback.message, user_id, build_id)


@dp.callback_query(F.data == "admin_sponsors")
async def admin_sponsors(callback: CallbackQuery):
    if callback.from_user.id not in ADMINS:
        return

    async with db_lock:
        lc = db.cursor()
        lc.execute("SELECT id, type, name, button_text, active FROM sponsors ORDER BY id")
        rows = lc.fetchall()

    text = "👥 Спонсоры\n\n"
    if rows:
        for row in rows:
            sid, stype, name, button_text, active = row
            icon = "📢" if stype == "channel" else "🔗"
            status = "✅" if active else "❌"
            text += f"{status} [{sid}] {icon} {name} - «{button_text}»\n"
    else:
        text += "Спонсоров пока нет.\n"

    kb = InlineKeyboardBuilder()
    kb.button(text="➕ Добавить канал", callback_data="admin_add_sponsor_channel")
    kb.button(text="➕ Добавить ссылку", callback_data="admin_add_sponsor_link")
    if rows:
        kb.button(text="🗑 Удалить спонсора", callback_data="admin_del_sponsor_pick")
    kb.button(text="🔙 Назад", callback_data="admin_panel")
    kb.adjust(1)

    await callback.message.answer(text, reply_markup=kb.as_markup())


@dp.callback_query(F.data == "admin_add_sponsor_channel")
async def admin_add_sponsor_channel(callback: CallbackQuery):
    if callback.from_user.id not in ADMINS:
        return
    user_states[callback.from_user.id] = "sponsor_channel_id"
    user_temp[callback.from_user.id] = {}
    await callback.message.answer(
        "📢 Введи ID канала (число, например -1001234567890)\n\n"
        "Бот должен быть добавлен в этот канал как участник или админ."
    )


@dp.callback_query(F.data == "admin_add_sponsor_link")
async def admin_add_sponsor_link(callback: CallbackQuery):
    if callback.from_user.id not in ADMINS:
        return
    user_states[callback.from_user.id] = "sponsor_link_url"
    user_temp[callback.from_user.id] = {}
    await callback.message.answer(
        "🔗 Введи ссылку (например https://t.me/SomeBot?start=ref123)"
    )


@dp.callback_query(F.data == "admin_del_sponsor_pick")
async def admin_del_sponsor_pick(callback: CallbackQuery):
    if callback.from_user.id not in ADMINS:
        return

    async with db_lock:
        lc = db.cursor()
        lc.execute("SELECT id, type, name FROM sponsors WHERE active=1 ORDER BY id")
        rows = lc.fetchall()

    if not rows:
        await callback.answer("Нет активных спонсоров.", show_alert=True)
        return

    kb = InlineKeyboardBuilder()
    for row in rows:
        sid, stype, name = row
        icon = "📢" if stype == "channel" else "🔗"
        kb.button(text=f"🗑 {icon} {name}", callback_data=f"admin_del_sponsor_{sid}")
    kb.button(text="🔙 Назад", callback_data="admin_sponsors")
    kb.adjust(1)

    await callback.message.answer("Выбери спонсора для удаления:", reply_markup=kb.as_markup())


@dp.callback_query(F.data.startswith("admin_del_sponsor_"))
async def admin_del_sponsor(callback: CallbackQuery):
    if callback.from_user.id not in ADMINS:
        return

    try:
        sid = int(callback.data.split("_")[-1])
    except ValueError:
        return

    async with db_lock:
        lc = db.cursor()
        lc.execute("DELETE FROM sponsors WHERE id=?", (sid,))
        lc.execute("DELETE FROM sponsor_acks WHERE sponsor_id=?", (sid,))
        db.commit()

    await callback.message.delete()
    await callback.answer("✅ Спонсор удалён", show_alert=True)


@dp.callback_query(F.data == "admin_tasks")
async def admin_tasks(callback: CallbackQuery):
    if callback.from_user.id not in ADMINS:
        return

    async with db_lock:
        lc = db.cursor()
        lc.execute(
            "SELECT id, title, reward, active FROM tasks ORDER BY id DESC"
        )
        rows = lc.fetchall()

    text = "📋 Управление заданиями\n\n"
    if rows:
        for task_id, title, reward, active in rows:
            status = "✅" if active else "❌"
            text += f"{status} [{task_id}] {title} — {reward} монет\n"
    else:
        text += "Заданий пока нет.\n"

    kb = InlineKeyboardBuilder()
    kb.button(text="➕ Добавить задание", callback_data="admin_add_task")
    if rows:
        kb.button(text="🗑 Отключить задание", callback_data="admin_del_task_pick")
    kb.button(text="🧾 Проверить заявки", callback_data="review_tasks")
    kb.button(text="🔙 Назад", callback_data="admin_panel")
    kb.adjust(1)

    await callback.message.answer(text, reply_markup=kb.as_markup())


@dp.callback_query(F.data == "admin_add_task")
async def admin_add_task(callback: CallbackQuery):
    if callback.from_user.id not in ADMINS:
        return

    user_states[callback.from_user.id] = "add_task"
    await callback.message.answer(
        "📋 Отправь задание в формате:\n"
        "Название|Описание|Награда в монетах\n\n"
        "Пример:\n"
        "Подписаться на канал|Подпишись на канал и пришли скриншот подписки|10"
    )


@dp.callback_query(F.data == "admin_del_task_pick")
async def admin_del_task_pick(callback: CallbackQuery):
    if callback.from_user.id not in ADMINS:
        return

    async with db_lock:
        lc = db.cursor()
        lc.execute("SELECT id, title FROM tasks WHERE active=1 ORDER BY id")
        rows = lc.fetchall()

    if not rows:
        await callback.answer("Активных заданий нет.", show_alert=True)
        return

    kb = InlineKeyboardBuilder()
    for task_id, title in rows:
        kb.button(text=f"🗑 {title}", callback_data=f"admin_del_task_{task_id}")
    kb.button(text="🔙 Назад", callback_data="admin_tasks")
    kb.adjust(1)
    await callback.message.answer(
        "Выбери задание для отключения:", reply_markup=kb.as_markup()
    )


@dp.callback_query(F.data.startswith("admin_del_task_"))
async def admin_del_task(callback: CallbackQuery):
    if callback.from_user.id not in ADMINS:
        return

    try:
        task_id = int(callback.data.rsplit("_", 1)[1])
    except ValueError:
        return

    async with db_lock:
        lc = db.cursor()
        lc.execute("UPDATE tasks SET active=0 WHERE id=? AND active=1", (task_id,))
        changed = lc.rowcount
        db.commit()

    await callback.answer(
        "✅ Задание отключено" if changed else "Задание уже отключено",
        show_alert=True
    )


@dp.callback_query(F.data == "admin_shop")
async def admin_shop(callback: CallbackQuery):
    if callback.from_user.id not in ADMINS:
        return

    async with db_lock:
        lc = db.cursor()
        lc.execute(
            """
            SELECT id, name, price, item_type, target, active
            FROM shop_items ORDER BY id DESC
            """
        )
        rows = lc.fetchall()

    text = "🛒 Управление магазином\n\n"
    if rows:
        for item_id, name, price, item_type, target, active in rows:
            status = "✅" if active else "❌"
            item_kind = "снятие ЧС" if item_type == "unban" else f"канал {target}"
            text += f"{status} [{item_id}] {name} — {price} монет | {item_kind}\n"
    else:
        text += "Товаров пока нет.\n"

    kb = InlineKeyboardBuilder()
    kb.button(text="➕ Добавить доступ в канал", callback_data="admin_add_shop")
    kb.button(text="➕ Добавить снятие ЧС", callback_data="admin_add_shop_unban")
    if rows:
        kb.button(text="🗑 Отключить товар", callback_data="admin_del_shop_pick")
    kb.button(text="🔙 Назад", callback_data="admin_panel")
    kb.adjust(1)

    await callback.message.answer(text, reply_markup=kb.as_markup())


@dp.callback_query(F.data == "admin_promos")
async def admin_promos(callback: CallbackQuery):
    if callback.from_user.id not in ADMINS:
        return

    async with db_lock:
        lc = db.cursor()
        lc.execute(
            """
            SELECT id, code, reward_coins, max_uses, uses_count, expires_at, active
            FROM promo_codes
            ORDER BY id DESC
            """
        )
        rows = lc.fetchall()

    text = "🎟 Управление промокодами\n\n"
    if rows:
        for promo_id, code, reward, max_uses, uses_count, expires_at, active in rows:
            status = "✅" if active else "❌"
            limit = "∞" if max_uses == 0 else str(max_uses)
            expiry = f", до {expires_at[:16]}" if expires_at else ""
            text += (
                f"{status} [{promo_id}] {code} — +{reward} монет | "
                f"{uses_count}/{limit}{expiry}\n"
            )
    else:
        text += "Промокодов пока нет.\n"

    kb = InlineKeyboardBuilder()
    kb.button(text="➕ Добавить промокод", callback_data="admin_add_promo")
    if any(row[6] for row in rows):
        kb.button(
            text="🗑 Отключить промокод",
            callback_data="admin_disable_promo_pick"
        )
    kb.button(text="🔙 Назад", callback_data="admin_panel")
    kb.adjust(1)
    await callback.message.answer(text, reply_markup=kb.as_markup())


@dp.callback_query(F.data == "admin_add_promo")
async def admin_add_promo(callback: CallbackQuery):
    if callback.from_user.id not in ADMINS:
        return

    user_states[callback.from_user.id] = "add_promo"
    await callback.message.answer(
        "🎟 Отправь промокод в формате:\n"
        "КОД|Монеты|Максимум использований|Срок в днях\n\n"
        "Пример:\n"
        "WELCOME|100|50|7\n\n"
        "Максимум использований 0 = без ограничений.\n"
        "Срок 0 = без срока действия."
    )


@dp.callback_query(F.data == "admin_disable_promo_pick")
async def admin_disable_promo_pick(callback: CallbackQuery):
    if callback.from_user.id not in ADMINS:
        return

    async with db_lock:
        lc = db.cursor()
        lc.execute(
            "SELECT id, code FROM promo_codes WHERE active=1 ORDER BY id DESC"
        )
        rows = lc.fetchall()

    if not rows:
        await callback.answer("Активных промокодов нет.", show_alert=True)
        return

    kb = InlineKeyboardBuilder()
    for promo_id, code in rows:
        kb.button(
            text=f"🗑 {code}",
            callback_data=f"admin_disable_promo_{promo_id}"
        )
    kb.button(text="🔙 Назад", callback_data="admin_promos")
    kb.adjust(1)
    await callback.message.answer(
        "Выбери промокод для отключения:",
        reply_markup=kb.as_markup()
    )


@dp.callback_query(F.data.startswith("admin_disable_promo_"))
async def admin_disable_promo(callback: CallbackQuery):
    if callback.from_user.id not in ADMINS:
        return

    try:
        promo_id = int(callback.data.rsplit("_", 1)[1])
    except ValueError:
        await callback.answer("Некорректный промокод.", show_alert=True)
        return

    async with db_lock:
        lc = db.cursor()
        lc.execute(
            "UPDATE promo_codes SET active=0 WHERE id=? AND active=1",
            (promo_id,)
        )
        changed = lc.rowcount
        db.commit()

    await callback.answer(
        "✅ Промокод отключён" if changed else "Промокод уже отключён",
        show_alert=True
    )


@dp.callback_query(F.data == "admin_add_shop")
async def admin_add_shop(callback: CallbackQuery):
    if callback.from_user.id not in ADMINS:
        return

    user_states[callback.from_user.id] = "add_shop"
    await callback.message.answer(
        "🛒 Отправь товар в формате:\n"
        "Название|Описание|Цена в монетах|ID приватного канала\n\n"
        "Пример:\n"
        "Доступ в приватный канал|Доступ к закрытому каналу на 30 дней|100|-1001234567890\n\n"
        "Бот должен быть администратором канала с правом приглашать пользователей."
    )


@dp.callback_query(F.data == "admin_add_shop_unban")
async def admin_add_shop_unban(callback: CallbackQuery):
    if callback.from_user.id not in ADMINS:
        return

    user_states[callback.from_user.id] = "add_shop_unban"
    await callback.message.answer(
        "🔓 Отправь товар для снятия ЧС в формате:\n"
        "Название|Описание|Цена в монетах\n\n"
        "Пример:\n"
        "Снятие ЧС|Разблокировка доступа к боту|250"
    )


@dp.callback_query(F.data == "admin_del_shop_pick")
async def admin_del_shop_pick(callback: CallbackQuery):
    if callback.from_user.id not in ADMINS:
        return

    async with db_lock:
        lc = db.cursor()
        lc.execute("SELECT id, name FROM shop_items WHERE active=1 ORDER BY id")
        rows = lc.fetchall()

    if not rows:
        await callback.answer("Активных товаров нет.", show_alert=True)
        return

    kb = InlineKeyboardBuilder()
    for item_id, name in rows:
        kb.button(text=f"🗑 {name}", callback_data=f"admin_del_shop_{item_id}")
    kb.button(text="🔙 Назад", callback_data="admin_shop")
    kb.adjust(1)
    await callback.message.answer(
        "Выбери товар для отключения:", reply_markup=kb.as_markup()
    )


@dp.callback_query(F.data.startswith("admin_del_shop_"))
async def admin_del_shop(callback: CallbackQuery):
    if callback.from_user.id not in ADMINS:
        return

    try:
        item_id = int(callback.data.rsplit("_", 1)[1])
    except ValueError:
        return

    async with db_lock:
        lc = db.cursor()
        lc.execute(
            "UPDATE shop_items SET active=0 WHERE id=? AND active=1",
            (item_id,)
        )
        changed = lc.rowcount
        db.commit()

    await callback.answer(
        "✅ Товар отключён" if changed else "Товар уже отключён",
        show_alert=True
    )


@dp.callback_query(F.data == "admin_reviewers")
async def admin_reviewers(callback: CallbackQuery):
    if callback.from_user.id not in ADMINS:
        return

    async with db_lock:
        lc = db.cursor()
        lc.execute("SELECT user_id, ts FROM task_reviewers ORDER BY user_id")
        rows = lc.fetchall()

    text = "👮 Проверяющие задания\n\n"
    if rows:
        text += "\n".join(f"• {row[0]}" for row in rows)
    else:
        text += "Дополнительных проверяющих нет.\n"
    text += "\n\nОсновной администратор всегда имеет полный доступ."

    kb = InlineKeyboardBuilder()
    kb.button(text="➕ Выдать доступ", callback_data="admin_add_reviewer")
    if rows:
        kb.button(text="➖ Снять доступ", callback_data="admin_del_reviewer_pick")
    kb.button(text="🔙 Назад", callback_data="admin_panel")
    kb.adjust(1)
    await callback.message.answer(text, reply_markup=kb.as_markup())


@dp.callback_query(F.data == "admin_add_reviewer")
async def admin_add_reviewer(callback: CallbackQuery):
    if callback.from_user.id not in ADMINS:
        return

    user_states[callback.from_user.id] = "add_reviewer"
    await callback.message.answer(
        "👮 Отправь Telegram ID пользователя, которому выдать доступ к проверке заданий."
    )


@dp.callback_query(F.data == "admin_del_reviewer_pick")
async def admin_del_reviewer_pick(callback: CallbackQuery):
    if callback.from_user.id not in ADMINS:
        return

    async with db_lock:
        lc = db.cursor()
        lc.execute("SELECT user_id FROM task_reviewers ORDER BY user_id")
        rows = lc.fetchall()

    if not rows:
        await callback.answer("Дополнительных проверяющих нет.", show_alert=True)
        return

    kb = InlineKeyboardBuilder()
    for (reviewer_id,) in rows:
        kb.button(
            text=f"➖ {reviewer_id}",
            callback_data=f"admin_del_reviewer_{reviewer_id}"
        )
    kb.button(text="🔙 Назад", callback_data="admin_reviewers")
    kb.adjust(1)
    await callback.message.answer(
        "Выбери пользователя, у которого снять доступ:",
        reply_markup=kb.as_markup()
    )


@dp.callback_query(F.data.startswith("admin_del_reviewer_"))
async def admin_del_reviewer(callback: CallbackQuery):
    if callback.from_user.id not in ADMINS:
        return

    try:
        reviewer_id = int(callback.data.rsplit("_", 1)[1])
    except ValueError:
        return

    async with db_lock:
        lc = db.cursor()
        lc.execute("DELETE FROM task_reviewers WHERE user_id=?", (reviewer_id,))
        changed = lc.rowcount
        db.commit()

    await callback.answer(
        "✅ Доступ снят" if changed else "Пользователь не найден",
        show_alert=True
    )


async def send_pending_reviews(target):
    async with db_lock:
        lc = db.cursor()
        lc.execute(
            """
            SELECT s.id, t.title, t.reward, s.user_id, s.ts
            FROM task_submissions s
            JOIN tasks t ON t.id=s.task_id
            WHERE s.status='pending'
            ORDER BY s.id ASC
            LIMIT 30
            """
        )
        rows = lc.fetchall()

    if not rows:
        text = "🧾 Заявок на проверку нет."
        kb = InlineKeyboardBuilder()
        kb.button(text="🔙 Назад", callback_data="admin_panel")
        await target.answer(text, reply_markup=kb.as_markup())
        return

    text = "🧾 Заявки на проверку:\n\n"
    kb = InlineKeyboardBuilder()
    for submission_id, title, reward, user_id, ts in rows:
        text += f"#{submission_id} | {title} | {reward} монет | ID {user_id}\n"
        kb.button(
            text=f"✅ #{submission_id}",
            callback_data=f"task_approve_{submission_id}"
        )
        kb.button(
            text=f"❌ #{submission_id}",
            callback_data=f"task_reject_{submission_id}"
        )
    kb.button(text="🔙 Назад", callback_data="admin_panel")
    kb.adjust(2, 1)
    await target.answer(text, reply_markup=kb.as_markup())


@dp.callback_query(F.data == "review_tasks")
async def review_tasks(callback: CallbackQuery):
    if not await has_reviewer_access(callback.from_user.id):
        return
    await send_pending_reviews(callback.message)
    await callback.answer()


@dp.message(F.text == "/review")
async def review_command(message: Message):
    if not await has_reviewer_access(message.from_user.id):
        return
    await send_pending_reviews(message)


@dp.callback_query(F.data.startswith("task_approve_"))
async def approve_task(callback: CallbackQuery):
    if not await has_reviewer_access(callback.from_user.id):
        return

    try:
        submission_id = int(callback.data.rsplit("_", 1)[1])
    except ValueError:
        await callback.answer("Некорректная заявка.", show_alert=True)
        return

    async with db_lock:
        lc = db.cursor()
        lc.execute(
            """
            SELECT s.user_id, t.title, t.reward
            FROM task_submissions s
            JOIN tasks t ON t.id=s.task_id
            WHERE s.id=? AND s.status='pending'
            """,
            (submission_id,)
        )
        row = lc.fetchone()
        if row:
            target_user_id, title, reward = row
            lc.execute(
                """
                UPDATE task_submissions
                SET status='approved', reviewer_id=?, reviewed_ts=CURRENT_TIMESTAMP
                WHERE id=? AND status='pending'
                """,
                (callback.from_user.id, submission_id)
            )
            changed = lc.rowcount
            if changed:
                lc.execute(
                    "UPDATE users SET coins=COALESCE(coins, 0)+? WHERE user_id=?",
                    (reward, target_user_id)
                )
            db.commit()
        else:
            changed = 0

    if not changed:
        await callback.answer("Заявка уже обработана.", show_alert=True)
        return

    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    try:
        await bot.send_message(
            target_user_id,
            f"✅ Задание «{title}» одобрено!\n"
            f"Начислено: {reward} монет."
        )
    except Exception:
        pass
    await callback.answer("✅ Выполнение одобрено")


@dp.callback_query(F.data.startswith("task_reject_"))
async def reject_task(callback: CallbackQuery):
    if not await has_reviewer_access(callback.from_user.id):
        return

    try:
        submission_id = int(callback.data.rsplit("_", 1)[1])
    except ValueError:
        await callback.answer("Некорректная заявка.", show_alert=True)
        return

    async with db_lock:
        lc = db.cursor()
        lc.execute(
            """
            SELECT s.user_id, t.title
            FROM task_submissions s
            JOIN tasks t ON t.id=s.task_id
            WHERE s.id=? AND s.status='pending'
            """,
            (submission_id,)
        )
        row = lc.fetchone()
        if row:
            target_user_id, title = row
            lc.execute(
                """
                UPDATE task_submissions
                SET status='rejected', reviewer_id=?,
                    reviewer_note='Отклонено проверяющим',
                    reviewed_ts=CURRENT_TIMESTAMP
                WHERE id=? AND status='pending'
                """,
                (callback.from_user.id, submission_id)
            )
            changed = lc.rowcount
            db.commit()
        else:
            changed = 0

    if not changed:
        await callback.answer("Заявка уже обработана.", show_alert=True)
        return

    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    try:
        await bot.send_message(
            target_user_id,
            f"❌ Выполнение задания «{title}» отклонено.\n"
            "Проверь условия и отправь выполнение ещё раз."
        )
    except Exception:
        pass
    await callback.answer("Заявка отклонена")


@dp.callback_query(F.data == "main_menu")
async def back_to_main(callback: CallbackQuery):
    await callback.answer()
    await go_home(callback)


@dp.callback_query(F.data == "admin_panel")
async def admin_panel_callback(callback: CallbackQuery):

    if callback.from_user.id not in ADMINS:
        return

    await callback.answer()
    pending = await pending_reviews_count()
    await show(callback, admin_home_text(pending), admin_menu(pending))


@dp.callback_query(F.data.startswith("admin_g_"))
async def admin_group(callback: CallbackQuery):

    if callback.from_user.id not in ADMINS:
        return

    key = callback.data[len("admin_g_"):]
    if key not in ADMIN_GROUPS:
        await callback.answer()
        return

    await callback.answer()
    await show(callback, f"<b>{ADMIN_GROUPS[key][0]}</b>", admin_group_menu(key))


@dp.callback_query(F.data == "admin_add")
async def admin_add(callback: CallbackQuery):

    if callback.from_user.id not in ADMINS:
        return

    user_states[callback.from_user.id] = "add"

    await callback.message.answer(
        "📦 Отправь:\nНазвание|Ссылка|Описание\n\nОписание необязательно. Скриншот можно добавить после - кнопкой «🖼 Карточка сборки».\nСсылка должна начинаться с https://"
    )


@dp.callback_query(F.data == "admin_delete")
async def admin_delete(callback: CallbackQuery):

    if callback.from_user.id not in ADMINS:
        return

    user_states[callback.from_user.id] = "delete"

    async with db_lock:
        lc = db.cursor()
        lc.execute("SELECT * FROM builds")
        builds_list = lc.fetchall()

    text = "🗑 Сборки:\n\n"

    for b in builds_list:
        text += f"{b[0]} | {b[1]}\n"

    text += "\nОтправь ID для удаления"

    await callback.message.answer(text)


@dp.callback_query(F.data == "admin_stats")
async def admin_stats(callback: CallbackQuery):

    if callback.from_user.id not in ADMINS:
        return

    async with db_lock:
        lc = db.cursor()

        def q1(sql):
            lc.execute(sql)
            return lc.fetchone()[0]

        lc.execute("SELECT * FROM builds")
        builds_list = lc.fetchall()
        total_users = q1("SELECT COUNT(*) FROM users")
        new_today = q1("SELECT COUNT(*) FROM users WHERE created_at >= date('now')")
        new_7d = q1("SELECT COUNT(*) FROM users WHERE created_at >= datetime('now','-7 days')")
        new_30d = q1("SELECT COUNT(*) FROM users WHERE created_at >= datetime('now','-30 days')")
        active_7d = q1("SELECT COUNT(*) FROM users WHERE last_seen >= datetime('now','-7 days')")
        banned_total = q1("SELECT COUNT(*) FROM users WHERE blacklisted=1")
        banned_auto = q1("SELECT COUNT(*) FROM users WHERE blacklisted=1 AND ban_reason='auto_leave'")
        forgiven = q1("SELECT COUNT(*) FROM ban_log WHERE action='auto_unban'")
        blocked_bot = q1("SELECT COUNT(*) FROM users WHERE bot_blocked=1")
        notify_on = q1("SELECT COUNT(*) FROM users WHERE notify_builds=1")
        lc.execute(
            "SELECT COALESCE(u.source, 'до трекинга'), COUNT(*), "
            "SUM(EXISTS(SELECT 1 FROM downloads d WHERE d.user_id=u.user_id)) "
            "FROM users u GROUP BY 1 ORDER BY 2 DESC LIMIT 12"
        )
        sources = lc.fetchall()

    text = (
        "📊 Статистика\n\n"
        f"👥 Пользователей: {total_users}\n"
        f"🆕 Новых: сегодня {new_today} · 7 дн. {new_7d} · 30 дн. {new_30d}\n"
        "(у старых пользователей дата = первое скачивание)\n"
        f"🔥 Заходили за 7 дней: {active_7d} (счётчик ведётся с момента обновления)\n"
        f"🚫 В ЧС: {banned_total} (за отписку {banned_auto}, вручную {banned_total - banned_auto})\n"
        f"♻️ Прощено автоматически: {forgiven}\n"
        f"📵 Заблокировали бота: {blocked_bot}\n"
        f"🔔 Хотят уведомления о сборках: {notify_on}\n\n"
        "📍 Источники (пришло / скачали):\n"
    )

    for src_name, total, downloaded in sources:
        text += f"• {src_name} - {total} / {downloaded or 0}\n"

    text += "\n📦 Сборки:\n"

    for b in builds_list:

        text += (
            f"{b[0]} | "
            f"{short_title(b[1], 40)} - "
            f"{b[3]} скач.\n"
        )

    await callback.message.answer(text[:4000])


@dp.callback_query(F.data == "admin_broadcast")
async def admin_broadcast(callback: CallbackQuery):

    if callback.from_user.id not in ADMINS:
        return

    kb = InlineKeyboardBuilder()
    for key, (title, where) in SEGMENTS.items():
        n = await count_where(where)
        kb.button(text=f"{title} - {n}", callback_data=f"admin_bc_{key}")
    kb.button(text="Отмена", callback_data="admin_cancel")
    kb.adjust(1)

    await callback.answer()
    await callback.message.answer("📢 Кому отправить рассылку?", reply_markup=kb.as_markup())


@dp.callback_query(F.data == "admin_reward")
async def admin_reward(callback: CallbackQuery):

    if callback.from_user.id not in ADMINS:
        return

    async with db_lock:
        lc = db.cursor()
        lc.execute("SELECT id, title, youtube_link, download_link FROM rewards WHERE active=1 LIMIT 1")
        row = lc.fetchone()

    if row:
        text = (
            "Текущая награда за 5 рефералов:\n\n"
            f"{row[1]}\n"
            f"YouTube: {row[2]}\n"
            f"Ссылка: {row[3]}\n\n"
            "Чтобы установить новую - отправь сообщение в формате:\nНазвание|YouTube ссылка|Ссылка для скачивания"
        )
    else:
        text = (
            "Награда не задана.\n"
            "Чтобы установить - отправь сообщение в формате:\nНазвание|YouTube ссылка|Ссылка для скачивания"
        )

    user_states[callback.from_user.id] = "set_reward"

    await callback.message.answer(text)


@dp.callback_query(F.data == "admin_amnesty")
async def admin_amnesty(callback: CallbackQuery):

    if callback.from_user.id not in ADMINS:
        return

    user_states[callback.from_user.id] = "amnesty"

    async with db_lock:
        lc = db.cursor()
        lc.execute("SELECT user_id FROM users WHERE blacklisted=1")
        rows = lc.fetchall()

    if not rows:
        await callback.message.answer("ЧС пуст")
        user_states.pop(callback.from_user.id, None)
        return

    text = f"🔒 Чёрный список ({len(rows)} чел.):\n\n"

    for r in rows[:40]:
        text += f"{r[0]}\n"

    if len(rows) > 40:
        text += f"... и ещё {len(rows) - 40} (полный список: /admin_banlist)\n"

    text += "\nОтправь ID для снятия с ЧС"

    kb = InlineKeyboardBuilder()
    kb.button(text="Глобальная амнистия", callback_data="admin_amnesty_global")
    kb.button(text="Отмена", callback_data="admin_cancel")
    kb.adjust(1)

    await callback.message.answer(text, reply_markup=kb.as_markup())


@dp.callback_query(F.data == "admin_amnesty_global")
async def admin_amnesty_global(callback: CallbackQuery):

    if callback.from_user.id not in ADMINS:
        return

    async with db_lock:
        lc = db.cursor()
        lc.execute("UPDATE users SET blacklisted=0 WHERE blacklisted=1")
        db.commit()

    await callback.message.answer("✅ Глобальная амнистия выполнена - все сняты с ЧС")


@dp.callback_query(F.data == "admin_cancel")
async def admin_cancel(callback: CallbackQuery):

    user_states.pop(callback.from_user.id, None)
    await callback.message.delete()
    await callback.answer("Отменено")


@dp.callback_query(F.data == "admin_recommend")
async def admin_recommend(callback: CallbackQuery):

    if callback.from_user.id not in ADMINS:
        return

    user_states[callback.from_user.id] = "recommend"

    async with db_lock:
        lc = db.cursor()
        lc.execute("SELECT * FROM builds")
        builds_list = lc.fetchall()

    text = "⭐ Сборки:\n\n"

    for b in builds_list:
        text += f"{b[0]} | {b[1]}{' ⭐' if len(b) > 4 and b[4] == 1 else ''}\n"

    text += "\nОтправь ID для установки рекомендованной сборки"

    await callback.message.answer(text)


@dp.callback_query(F.data == "referral")
async def referral_panel(callback: CallbackQuery):

    await callback.answer()

    kb = InlineKeyboardBuilder()
    kb.button(text="🔙 Бонусы", callback_data="bonuses")
    kb.adjust(1)

    await show(callback, await build_referral_text(callback.from_user.id), kb.as_markup())


@dp.message(F.text == "/ref")
async def ref_command(message: Message):

    await message.answer(
        await build_referral_text(message.from_user.id),
        parse_mode="HTML"
    )


@dp.message()
async def handle_text(message: Message):

    user_id = message.from_user.id
    state = user_states.get(user_id)

    if not state:
        return

    if state == "task_proof":
        data = user_temp.get(user_id, {})
        task_id = data.get("task_id")
        proof_text = message.text or message.caption or ""
        proof_file_id = None
        proof_file_type = None
        if message.photo:
            proof_file_id = message.photo[-1].file_id
            proof_file_type = "photo"
        elif message.document:
            proof_file_id = message.document.file_id
            proof_file_type = "document"

        if not proof_text and not proof_file_id:
            await message.answer("❌ Отправь текст, фото с подписью или документ.")
            return

        async with db_lock:
            lc = db.cursor()
            lc.execute(
                "SELECT title FROM tasks WHERE id=? AND active=1",
                (task_id,)
            )
            task = lc.fetchone()
            lc.execute(
                """
                SELECT status FROM task_submissions
                WHERE task_id=? AND user_id=?
                ORDER BY id DESC LIMIT 1
                """,
                (task_id, user_id)
            )
            previous = lc.fetchone()
            if task and (not previous or previous[0] == "rejected"):
                lc.execute(
                    """
                    INSERT INTO task_submissions
                    (task_id, user_id, proof_text, proof_file_id, proof_file_type)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (task_id, user_id, proof_text, proof_file_id, proof_file_type)
                )
                submission_id = lc.lastrowid
                db.commit()
            else:
                submission_id = None

        user_states.pop(user_id, None)
        user_temp.pop(user_id, None)

        if not task:
            await message.answer("❌ Задание больше недоступно.")
            return
        if not submission_id:
            status_text = "уже одобрено" if previous and previous[0] == "approved" else "уже находится на проверке"
            await message.answer(f"❌ Выполнение {status_text}.")
            return

        await message.answer(
            "✅ Выполнение отправлено на проверку. "
            "После решения проверяющего ты получишь уведомление."
        )

        reviewer_ids = await get_reviewer_ids()
        proof_header = (
            f"🧾 Новая заявка на проверку #{submission_id}\n\n"
            f"Задание: {task[0]}\n"
            f"Пользователь: {format_user(message.from_user)}\n"
            f"ID пользователя: {user_id}\n\n"
            f"Доказательство:\n{proof_text or 'см. прикреплённый файл'}"
        )
        kb = InlineKeyboardBuilder()
        kb.button(text="✅ Одобрить", callback_data=f"task_approve_{submission_id}")
        kb.button(text="❌ Отклонить", callback_data=f"task_reject_{submission_id}")
        kb.adjust(2)

        for reviewer_id in reviewer_ids:
            try:
                if proof_file_type == "photo":
                    await bot.send_photo(
                        reviewer_id,
                        photo=proof_file_id,
                        caption=proof_header[:1024],
                        reply_markup=kb.as_markup()
                    )
                elif proof_file_type == "document":
                    await bot.send_document(
                        reviewer_id,
                        document=proof_file_id,
                        caption=proof_header[:1024],
                        reply_markup=kb.as_markup()
                    )
                else:
                    await bot.send_message(
                        reviewer_id,
                        proof_header,
                        reply_markup=kb.as_markup()
                    )
            except Exception:
                pass
        return

    if state == "add_task":
        try:
            data = message.text.split("|", 2)
            if len(data) < 3:
                await message.answer("❌ Формат: Название|Описание|Награда")
                return

            title = data[0].strip()
            description = data[1].strip()
            reward = int(data[2].strip())
            if not title or not description or reward <= 0:
                raise ValueError

            async with db_lock:
                lc = db.cursor()
                lc.execute(
                    """
                    INSERT INTO tasks (title, description, reward, active)
                    VALUES (?, ?, ?, 1)
                    """,
                    (title, description, reward)
                )
                db.commit()

            user_states.pop(user_id, None)
            await message.answer(f"✅ Задание добавлено: {title}")
        except (ValueError, TypeError, AttributeError):
            await message.answer(
                "❌ Неверный формат. Нужно: Название|Описание|положительная награда"
            )
        return

    if state == "add_shop":
        try:
            data = message.text.split("|", 3)
            if len(data) < 4:
                await message.answer("❌ Формат: Название|Описание|Цена|ID канала")
                return

            name = data[0].strip()
            description = data[1].strip()
            price = int(data[2].strip())
            channel_id = int(data[3].strip())
            if not name or not description or price <= 0:
                raise ValueError

            async with db_lock:
                lc = db.cursor()
                lc.execute(
                    """
                    INSERT INTO shop_items
                    (name, description, price, item_type, target, active)
                    VALUES (?, ?, ?, 'channel', ?, 1)
                    """,
                    (name, description, price, str(channel_id))
                )
                db.commit()

            user_states.pop(user_id, None)
            await message.answer(
                f"✅ Товар добавлен: {name}\n"
                "Проверь, что бот администратор указанного канала."
            )
        except (ValueError, TypeError, AttributeError):
            await message.answer(
                "❌ Неверный формат. Нужно: Название|Описание|положительная цена|ID канала"
            )
        return

    if state == "add_shop_unban":
        try:
            data = message.text.split("|", 2)
            if len(data) < 3:
                await message.answer("❌ Формат: Название|Описание|Цена")
                return

            name = data[0].strip()
            description = data[1].strip()
            price = int(data[2].strip())
            if not name or not description or price <= 0:
                raise ValueError

            async with db_lock:
                lc = db.cursor()
                lc.execute(
                    """
                    INSERT INTO shop_items
                    (name, description, price, item_type, target, active)
                    VALUES (?, ?, ?, 'unban', 'unban', 1)
                    """,
                    (name, description, price)
                )
                db.commit()

            user_states.pop(user_id, None)
            await message.answer(f"✅ Товар для снятия ЧС добавлен: {name}")
        except (ValueError, TypeError, AttributeError):
            await message.answer(
                "❌ Неверный формат. Нужно: Название|Описание|положительная цена"
            )
        return

    if state == "add_promo":
        try:
            data = message.text.split("|", 3)
            if len(data) < 4:
                await message.answer(
                    "❌ Формат: КОД|Монеты|Максимум использований|Срок в днях"
                )
                return

            code = data[0].strip().upper()
            reward_coins = int(data[1].strip())
            max_uses = int(data[2].strip())
            expires_days = int(data[3].strip())

            if (
                not 3 <= len(code) <= 32
                or not all(char.isalnum() or char in "_-" for char in code)
                or reward_coins <= 0
                or max_uses < 0
                or expires_days < 0
            ):
                raise ValueError

            expires_at = None
            if expires_days:
                expires_at = (
                    datetime.now(timezone.utc) + timedelta(days=expires_days)
                ).strftime("%Y-%m-%d %H:%M:%S")

            try:
                async with db_lock:
                    lc = db.cursor()
                    lc.execute(
                        """
                        INSERT INTO promo_codes
                        (code, reward_coins, max_uses, expires_at, active)
                        VALUES (?, ?, ?, ?, 1)
                        """,
                        (code, reward_coins, max_uses, expires_at)
                    )
                    db.commit()
            except sqlite3.IntegrityError:
                await message.answer("❌ Такой промокод уже существует.")
                return

            user_states.pop(user_id, None)
            limit = "без ограничений" if max_uses == 0 else str(max_uses)
            expiry = "без срока" if not expires_at else f"на {expires_days} дн."
            await message.answer(
                f"✅ Промокод {code} добавлен.\n"
                f"Награда: {reward_coins} монет | Лимит: {limit} | {expiry}"
            )
        except (ValueError, TypeError, AttributeError):
            await message.answer(
                "❌ Неверный формат. Пример: WELCOME|100|50|7"
            )
        return

    if state == "redeem_promo":
        code = (message.text or "").strip().upper()
        if not code:
            await message.answer("❌ Отправь промокод текстом.")
            return

        error = None
        reward_coins = 0
        try:
            async with db_lock:
                lc = db.cursor()
                lc.execute(
                    """
                    SELECT id, reward_coins, max_uses, uses_count, expires_at, active
                    FROM promo_codes
                    WHERE code=? COLLATE NOCASE
                    """,
                    (code,)
                )
                promo = lc.fetchone()
                lc.execute(
                    "SELECT 1 FROM users WHERE user_id=?",
                    (user_id,)
                )
                user_exists = lc.fetchone() is not None

                if not user_exists:
                    error = "Сначала запусти бота командой /start."
                elif not promo:
                    error = "Промокод не найден."
                else:
                    promo_id, reward_coins, max_uses, uses_count, expires_at, active = promo
                    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
                    if not active:
                        error = "Промокод отключён."
                    elif expires_at and expires_at <= now:
                        error = "Срок действия промокода истёк."
                    else:
                        lc.execute(
                            """
                            SELECT 1 FROM promo_redemptions
                            WHERE promo_id=? AND user_id=?
                            """,
                            (promo_id, user_id)
                        )
                        already_used = lc.fetchone() is not None
                        if already_used:
                            error = "Ты уже использовал этот промокод."
                        elif max_uses and uses_count >= max_uses:
                            error = "Лимит использований промокода исчерпан."
                        else:
                            lc.execute(
                                """
                                INSERT INTO promo_redemptions (promo_id, user_id)
                                VALUES (?, ?)
                                """,
                                (promo_id, user_id)
                            )
                            lc.execute(
                                """
                                UPDATE promo_codes
                                SET uses_count=uses_count+1
                                WHERE id=?
                                """,
                                (promo_id,)
                            )
                            lc.execute(
                                """
                                UPDATE users
                                SET coins=COALESCE(coins, 0)+?
                                WHERE user_id=?
                                """,
                                (reward_coins, user_id)
                            )
                            db.commit()
        except sqlite3.IntegrityError:
            db.rollback()
            error = "Ты уже использовал этот промокод."

        if error:
            await message.answer(f"❌ {error}")
            return

        user_states.pop(user_id, None)
        kb = InlineKeyboardBuilder()
        kb.button(text="🛒 Вернуться в магазин", callback_data="shop")
        await message.answer(
            f"✅ Промокод применён!\n\n"
            f"Начислено: {reward_coins} монет.",
            reply_markup=kb.as_markup()
        )
        return

    if state == "add_reviewer":
        try:
            reviewer_id = int(message.text.strip())
            if reviewer_id <= 0:
                raise ValueError
            if reviewer_id in ADMINS:
                user_states.pop(user_id, None)
                await message.answer("Этот пользователь уже является основным администратором.")
                return

            async with db_lock:
                lc = db.cursor()
                lc.execute(
                    """
                    INSERT OR REPLACE INTO task_reviewers (user_id, added_by)
                    VALUES (?, ?)
                    """,
                    (reviewer_id, user_id)
                )
                db.commit()

            user_states.pop(user_id, None)
            await message.answer(
                f"✅ Пользователю {reviewer_id} выдан доступ к проверке заданий.\n"
                "Он может открыть проверку командой /review."
            )
            try:
                await bot.send_message(
                    reviewer_id,
                    "👮 Тебе выдан доступ к проверке заданий.\n"
                    "Открыть список заявок: /review"
                )
            except Exception:
                pass
        except (ValueError, TypeError, AttributeError):
            await message.answer("❌ ID должен быть положительным числом.")
        return

    if state == "banner_set":
        banner_photo = message.photo[-1].file_id if message.photo else None
        banner_text = (message.text or "").strip()

        if banner_text == "-":
            await set_setting("banner_file_id", None)
            user_states.pop(user_id, None)
            await message.answer("✅ Баннер убран.")
            return

        if not banner_photo:
            await message.answer("❌ Пришли картинку как фото (не файлом) или «-», чтобы убрать баннер.")
            return

        await set_setting("banner_file_id", banner_photo)
        user_states.pop(user_id, None)
        await message.answer("✅ Баннер сохранён. Так выглядит главный экран:")
        await send_home(message, user_id)
        return

    if state == "card_pick":
        try:
            build_id = int((message.text or "").strip())
        except ValueError:
            await message.answer("❌ Нужен числовой ID сборки. Или нажми «Отмена» в админке.")
            return

        async with db_lock:
            lc = db.cursor()
            lc.execute("SELECT 1 FROM builds WHERE id=?", (build_id,))
            found = lc.fetchone() is not None

        if not found:
            await message.answer("❌ Сборки с таким ID нет. Отправь другой ID.")
            return

        user_temp[user_id] = {"build_id": build_id}
        user_states[user_id] = "card_content"
        await message.answer(CARD_CONTENT_PROMPT)
        return

    if state == "card_content":
        build_id = (user_temp.get(user_id) or {}).get("build_id")
        photo_id = message.photo[-1].file_id if message.photo else None
        text_in = ((message.caption if message.photo else message.text) or "").strip()

        if not build_id:
            user_states.pop(user_id, None)
            user_temp.pop(user_id, None)
            await message.answer("❌ Сессия потерялась (бот перезапускался). Начни заново: 🖼 Карточка сборки.")
            return

        if not text_in and not photo_id:
            await message.answer("❌ Пришли скриншот, текст описания или «-» для очистки.")
            return

        async with db_lock:
            lc = db.cursor()
            if text_in == "-":
                lc.execute("UPDATE builds SET description=NULL, photo_id=NULL WHERE id=?", (build_id,))
            else:
                if text_in:
                    lc.execute("UPDATE builds SET description=? WHERE id=?", (text_in[:700], build_id))
                if photo_id:
                    lc.execute("UPDATE builds SET photo_id=? WHERE id=?", (photo_id, build_id))
            db.commit()

        user_states.pop(user_id, None)
        user_temp.pop(user_id, None)

        await message.answer("✅ Карточка обновлена. Так её увидят пользователи:")
        b = await get_build_full(build_id)
        if b:
            await send_build_card(message, b)
            next_kb = InlineKeyboardBuilder()
            next_kb.button(text="📢 Опубликовать в канале", callback_data=f"admin_pub_{build_id}")
            next_kb.adjust(1)
            await message.answer("Дальше:", reply_markup=next_kb.as_markup())
        return

    if state == "add":

        try:

            data = message.text.split("|")

            if len(data) < 2:
                await message.answer("❌ Формат: Название|Ссылка|Описание (описание необязательно)")
                return

            name = data[0].strip()
            link = data[1].strip()
            description = "|".join(data[2:]).strip()[:700] if len(data) > 2 else ""

            async with db_lock:
                lc = db.cursor()
                lc.execute(
                    "INSERT INTO builds (name, link, description, added_at) VALUES (?, ?, ?, ?)",
                    (name, link, description or None, now_utc_str())
                )
                new_build_id = lc.lastrowid
                db.commit()

            user_states.pop(user_id, None)

            subscribers = await count_where(SEGMENTS["notify"][1])

            kb = InlineKeyboardBuilder()
            kb.button(text="🖼 Добавить скриншот / описание", callback_data=f"admin_card_{new_build_id}")
            kb.button(text="📢 Опубликовать в канале", callback_data=f"admin_pub_{new_build_id}")
            kb.button(text=f"📣 Только уведомить подписчиков ({subscribers})", callback_data=f"admin_notify_build_{new_build_id}")
            kb.adjust(1)

            try:
                direct = await build_deeplink(new_build_id)
                link_line = f"\n\n🔗 Прямая ссылка на сборку:\n{direct}"
            except Exception:
                link_line = ""

            warn = "" if is_http_url(link) else "\n\n⚠️ Ссылка не начинается с https:// - кнопка «Скачать» работать не будет, покажу ссылку текстом."

            await message.answer(
                f"✅ Сборка добавлена: {name} (ID {new_build_id}){warn}{link_line}\n\n"
                "Дальше: оформи карточку и опубликуй в канале.",
                reply_markup=kb.as_markup()
            )

        except Exception as e:

            user_states.pop(user_id, None)

            await message.answer(
                f"❌ Ошибка: {e}"
            )

        return

    elif state == "delete":

        try:

            build_id = int(message.text)

            async with db_lock:
                lc = db.cursor()
                lc.execute("DELETE FROM builds WHERE id=?", (build_id,))
                db.commit()

            user_states.pop(user_id, None)

            await message.answer(
                f"✅ Сборка {build_id} удалена"
            )

        except Exception:

            user_states.pop(user_id, None)

            await message.answer(
                "❌ Неверный ID"
            )

        return

    elif state == "recommend":

        try:

            build_id = int(message.text)

            async with db_lock:
                lc = db.cursor()
                lc.execute("UPDATE builds SET recommended=0")
                lc.execute("UPDATE builds SET recommended=1 WHERE id=?", (build_id,))
                db.commit()

            user_states.pop(user_id, None)

            await message.answer(
                f"✅ Сборка {build_id} установлена как рекомендуемая"
            )

        except Exception:

            user_states.pop(user_id, None)

            await message.answer(
                "❌ Неверный ID"
            )

        return

    elif state == "broadcast":

        segment = (user_temp.get(user_id) or {}).get("segment", "all")
        if segment not in SEGMENTS:
            segment = "all"

        photo_id = message.photo[-1].file_id if message.photo else None
        html_content = message.html_text or ""

        if not html_content and not photo_id:
            await message.answer("❌ Пустое сообщение - отправь текст или фото с подписью.")
            return

        user_ids = await get_ids_where(SEGMENTS[segment][1])

        user_states.pop(user_id, None)
        user_temp.pop(user_id, None)

        await message.answer(
            f"🚀 Рассылка запущена: {len(user_ids)} чел. ({SEGMENTS[segment][0]}).\n"
            "Отчёт пришлю, когда закончу - бот пока работает как обычно."
        )

        spawn(run_broadcast(user_id, user_ids, html_content, photo_id))

        return

    elif state == "set_reward":

        try:
            data = message.text.split("|")
            if len(data) < 3:
                await message.answer("❌ Формат: Название|YouTube ссылка|Ссылка для скачивания")
                return

            title = data[0].strip()
            yt = data[1].strip()
            link = data[2].strip()

            async with db_lock:
                lc = db.cursor()
                lc.execute("UPDATE rewards SET active=0 WHERE active=1")
                lc.execute("INSERT INTO rewards (title, youtube_link, download_link, active) VALUES (?, ?, ?, 1)", (title, yt, link))
                db.commit()

            user_states.pop(user_id, None)

            await message.answer("✅ Награда за рефералов установлена")

            return

        except Exception as e:
            user_states.pop(user_id, None)
            await message.answer(f"❌ Ошибка: {e}")
            return

    elif state == "amnesty":

        try:

            target_id = int(message.text)

            async with db_lock:
                lc = db.cursor()
                lc.execute("UPDATE users SET blacklisted=0 WHERE user_id=? AND blacklisted=1", (target_id,))
                actually_unbanned = lc.rowcount > 0
                db.commit()

            user_states.pop(user_id, None)

            await message.answer(
                "✅ Пользователь снят с ЧС" if actually_unbanned else "⚠️ Пользователь уже не в ЧС"
            )

            return

        except Exception:

            user_states.pop(user_id, None)

            await message.answer(
                "❌ Неверный ID"
            )

            return

    elif state == "sponsor_channel_id":
        try:
            channel_id = int(message.text.strip())
            user_temp[user_id]["channel_id"] = channel_id
            user_states[user_id] = "sponsor_channel_text"
            await message.answer("✏️ Введи текст кнопки (например: Подписаться на канал)")
        except ValueError:
            await message.answer("❌ ID должен быть числом, например -1001234567890")
        return

    elif state == "sponsor_channel_text":
        user_temp[user_id]["button_text"] = message.text.strip()
        user_states[user_id] = "sponsor_channel_name"
        await message.answer("📝 Введи название для admin-панели (например: МойКанал)")
        return

    elif state == "sponsor_channel_name":
        data = user_temp.get(user_id, {})
        name = message.text.strip()
        channel_id = data.get("channel_id")
        button_text = data.get("button_text")

        invite_link = None
        try:
            chat = await bot.get_chat(channel_id)
            if chat.username:
                invite_link = f"https://t.me/{chat.username}"
        except Exception:
            pass

        if not invite_link:
            try:
                link_obj = await bot.create_chat_invite_link(channel_id)
                invite_link = link_obj.invite_link
            except Exception:
                invite_link = None

        async with db_lock:
            lc = db.cursor()
            lc.execute(
                "INSERT INTO sponsors (type, target, button_text, name, invite_link) VALUES (?, ?, ?, ?, ?)",
                ("channel", str(channel_id), button_text, name, invite_link)
            )
            db.commit()
        user_states.pop(user_id, None)
        user_temp.pop(user_id, None)

        if invite_link:
            await message.answer(f"✅ Спонсор-канал «{name}» добавлен!")
        else:
            await message.answer(
                f"⚠️ Спонсор-канал «{name}» добавлен, но не удалось создать ссылку-приглашение.\n"
                "Проверь, что бот добавлен в этот канал как администратор с правом приглашать пользователей - "
                "иначе кнопка подписки у пользователей не будет работать."
            )
        return

    elif state == "sponsor_link_url":
        url = message.text.strip()
        if not url.startswith("http"):
            await message.answer("❌ Ссылка должна начинаться с http:// или https://")
            return
        user_temp[user_id]["url"] = url
        user_states[user_id] = "sponsor_link_text"
        await message.answer("✏️ Введи текст кнопки (например: Подключить VPN)")
        return

    elif state == "sponsor_link_text":
        user_temp[user_id]["button_text"] = message.text.strip()
        user_states[user_id] = "sponsor_link_name"
        await message.answer("📝 Введи название для admin-панели (например: VPN Реклама)")
        return

    elif state == "sponsor_link_name":
        data = user_temp.get(user_id, {})
        name = message.text.strip()
        url = data.get("url")
        button_text = data.get("button_text")
        async with db_lock:
            lc = db.cursor()
            lc.execute(
                "INSERT INTO sponsors (type, target, button_text, name) VALUES (?, ?, ?, ?)",
                ("link", url, button_text, name)
            )
            db.commit()
        user_states.pop(user_id, None)
        user_temp.pop(user_id, None)
        await message.answer(f"✅ Спонсор-ссылка «{name}» добавлена!")
        return

    else:
        return


async def health(request):
    return web.Response(text="OK")


async def run_web():
    port = int(os.environ.get("PORT", 3000))
    app = web.Application()
    app.router.add_get("/", health)
    app.router.add_get("/health", health)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    print(f"Web server started on port {port}")


async def notify_admins_on_start():
    import datetime, time
    stamp_file = "/tmp/bot_last_start.txt"
    now_ts = time.time()
    try:
        with open(stamp_file) as f:
            last = float(f.read().strip())
        if now_ts - last < 10:
            return  # Другой instance уже отправил уведомление
    except Exception:
        pass
    try:
        with open(stamp_file, "w") as f:
            f.write(str(now_ts))
    except Exception:
        pass
    for admin_id in ADMINS:
        try:
            await bot.send_message(
                admin_id,
                "✅ Бот запущен и работает!\n🕐 " + datetime.datetime.now().strftime("%d.%m.%Y %H:%M:%S")
            )
        except Exception:
            pass


async def daily_backup():
    import datetime
    while True:
        await asyncio.sleep(86400)
        for admin_id in ADMINS:
            try:
                db.commit()
                with open(DB_PATH, "rb") as f:
                    await bot.send_document(
                        admin_id,
                        document=("bot_backup.db", f),
                        caption=f"💾 Резервная копия БД\n🕐 {datetime.datetime.now().strftime('%d.%m.%Y %H:%M:%S')}"
                    )
            except Exception:
                pass


@dp.chat_join_request()
async def on_shop_join_request(request: ChatJoinRequest):
    invite_link = request.invite_link.invite_link if request.invite_link else None
    if not invite_link:
        return

    async with db_lock:
        lc = db.cursor()
        lc.execute(
            """
            SELECT p.id, s.name
            FROM purchases p
            JOIN shop_items s ON s.id=p.item_id
            WHERE p.user_id=? AND p.status='approved'
              AND s.target=? AND p.delivery_link=?
            ORDER BY p.id DESC LIMIT 1
            """,
            (request.from_user.id, str(request.chat.id), invite_link)
        )
        purchase = lc.fetchone()

    if not purchase:
        return

    try:
        await bot.approve_chat_join_request(request.chat.id, request.from_user.id)
    except Exception:
        return

    async with db_lock:
        lc = db.cursor()
        lc.execute(
            """
            UPDATE purchases
            SET status='delivered', delivered_ts=CURRENT_TIMESTAMP
            WHERE id=? AND status='approved'
            """,
            (purchase[0],)
        )
        db.commit()

    try:
        await bot.send_message(
            request.from_user.id,
            f"✅ Заявка одобрена. Доступ к товару «{purchase[1]}» выдан."
        )
    except Exception:
        pass


@dp.chat_member(ChatMemberUpdatedFilter(JOIN_TRANSITION))
async def on_user_join_channel(event: ChatMemberUpdated):
    if event.chat.id != CHANNEL_ID:
        return
    user_id = event.new_chat_member.user.id
    await confirm_referral_if_pending(user_id)

    if await try_auto_forgive(user_id):
        try:
            await bot.send_message(
                user_id,
                "✅ С возвращением! ЧС снят - сборки снова доступны.\n\n"
                "⚠️ Это разовое прощение: если выйти из канала ещё раз, ЧС останется.",
                reply_markup=main_menu(user_id)
            )
        except Exception:
            pass


@dp.chat_member(ChatMemberUpdatedFilter(LEAVE_TRANSITION))
async def on_user_leave_channel(event: ChatMemberUpdated):
    if event.chat.id != CHANNEL_ID:
        return

    user_id = event.new_chat_member.user.id

    referrer_id = None
    cnt = 0
    was_banned = False
    ban_count = 0

    async with db_lock:
        lc = db.cursor()

        # Баним сразу если когда-либо скачивал - обходим Telegram-кэш
        lc.execute("SELECT 1 FROM downloads WHERE user_id=? LIMIT 1", (user_id,))
        had_downloads = lc.fetchone()
        if had_downloads:
            lc.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))
            lc.execute(
                "UPDATE users SET blacklisted=1, ban_reason='auto_leave', ban_count=COALESCE(ban_count,0)+1 "
                "WHERE user_id=? AND (blacklisted IS NULL OR blacklisted=0)",
                (user_id,)
            )
            was_banned = lc.rowcount > 0
            if was_banned:
                lc.execute("INSERT INTO ban_log (user_id, action) VALUES (?, 'auto_ban')", (user_id,))
                lc.execute("SELECT ban_count FROM users WHERE user_id=?", (user_id,))
                ban_count = lc.fetchone()[0] or 0
            db.commit()

        # Аннулируем реферал если был
        lc.execute("SELECT id, referrer_id FROM referrals WHERE referred_id=? AND confirmed=1", (user_id,))
        row = lc.fetchone()
        if row:
            referral_id, referrer_id = row
            lc.execute("UPDATE referrals SET confirmed=0 WHERE id=? AND confirmed=1", (referral_id,))
            actually_cancelled = lc.rowcount > 0
            db.commit()
            if not actually_cancelled:
                referrer_id = None
            else:
                lc.execute("SELECT COUNT(*) FROM referrals WHERE referrer_id=? AND confirmed=1", (referrer_id,))
                cnt = lc.fetchone()[0]

    if was_banned:
        try:
            if ban_count <= AUTO_FORGIVE_LIMIT:
                await bot.send_message(
                    user_id,
                    "⏸ Ты вышел из канала - доступ к сборкам приостановлен.\n\n"
                    "Подпишись снова - и ЧС снимется автоматически (это работает один раз).",
                    reply_markup=sub_menu()
                )
            else:
                await bot.send_message(
                    user_id,
                    "❌ Ты повторно вышел из канала и добавлен в чёрный список бота.\n\n"
                    "Снять ЧС можно в 🛒 Магазине за монеты или через администрацию."
                )
        except Exception:
            pass

    if referrer_id:
        try:
            await bot.send_message(
                referrer_id,
                f"❌ Твой реферал отписался от канала - реферал аннулирован.\nПодтверждено: {cnt}/5"
            )
        except Exception:
            pass


async def main():

    print("BOT STARTED")

    await notify_admins_on_start()

    await asyncio.gather(
        run_web(),
        dp.start_polling(
            bot,
            allowed_updates=[
                "message",
                "callback_query",
                "chat_member",
                "chat_join_request",
                "my_chat_member"
            ]
        ),
        daily_backup()
    )


if __name__ == "__main__":

    asyncio.run(main())

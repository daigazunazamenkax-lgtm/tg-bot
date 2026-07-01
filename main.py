import asyncio
import os
import sqlite3
import random
import string
from aiohttp import web

from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, CallbackQuery, ChatMemberUpdated
from aiogram.filters import ChatMemberUpdatedFilter, JOIN_TRANSITION, LEAVE_TRANSITION
from aiogram.utils.keyboard import InlineKeyboardBuilder

TOKEN = os.environ.get("TOKEN")
if not TOKEN:
    raise RuntimeError("TOKEN environment variable is not set")

CHANNEL_ID = -1003279311073
CHANNEL_LINK = "https://t.me/chapitosikboss"

CHAT_LINK = "https://t.me/+hi1WWyULprhlMDgx"

ADMINS = [8008667717]

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

cursor.execute("""
CREATE TABLE IF NOT EXISTS sponsor_acks (
    user_id INTEGER,
    sponsor_id INTEGER,
    PRIMARY KEY (user_id, sponsor_id)
)
""")

db.commit()


def main_menu(user_id=None):

    kb = InlineKeyboardBuilder()

    kb.button(
        text="📦 Скачать сборку",
        callback_data="builds"
    )

    kb.button(
        text="🤝 Реферальная система",
        callback_data="referral"
    )

    kb.button(
        text="📢 Канал",
        url=CHANNEL_LINK
    )

    kb.button(
        text="💬 Чат",
        url=CHAT_LINK
    )

    try:
        if user_id and user_id in ADMINS:
            kb.button(text="⚙️ ADMIN PANEL", callback_data="admin_panel")
    except Exception:
        pass

    kb.adjust(1)

    return kb.as_markup()


def admin_menu():

    kb = InlineKeyboardBuilder()

    kb.button(
        text="➕ Добавить сборку",
        callback_data="admin_add"
    )

    kb.button(
        text="🗑 Удалить сборку",
        callback_data="admin_delete"
    )

    kb.button(
        text="📊 Статистика",
        callback_data="admin_stats"
    )

    kb.button(
        text="📢 Рассылка",
        callback_data="admin_broadcast"
    )

    kb.button(
        text="⭐ Рекомендуемая",
        callback_data="admin_recommend"
    )

    kb.button(
        text="🎁 Награда за рефералов",
        callback_data="admin_reward"
    )

    kb.button(
        text="🔓 Амнистия",
        callback_data="admin_amnesty"
    )

    kb.button(
        text="👥 Спонсоры",
        callback_data="admin_sponsors"
    )

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
    lc = db.cursor()
    lc.execute("SELECT id, type, target, button_text, name FROM sponsors WHERE active=1")
    all_sponsors = lc.fetchall()

    lc2 = db.cursor()
    lc2.execute("SELECT sponsor_id FROM sponsor_acks WHERE user_id=?", (user_id,))
    acked_ids = {row[0] for row in lc2.fetchall()}

    unmet_channels = []
    unmet_links = []

    for sponsor in all_sponsors:
        sid, stype, target, button_text, name = sponsor
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
        sid, stype, target, button_text, name = sponsor
        kb.button(text=f"📢 {button_text}", url=f"https://t.me/{target.lstrip('@')}" if not target.startswith("http") else target)
    for sponsor in unmet_links:
        sid, stype, target, button_text, name = sponsor
        kb.button(text=f"🔗 {button_text}", url=target)
    kb.button(text="✅ Я подписался — проверить", callback_data=f"check_sponsor_sub_{build_id}")
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

        lc.execute("UPDATE referrals SET confirmed=1 WHERE id=?", (referral_id,))
        db.commit()

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

    # Все await — за пределами лока, курсор уже не нужен
    try:
        chat = await bot.get_chat(user_id)
        name = chat.username or chat.first_name or str(user_id)
    except Exception:
        name = str(user_id)

    try:
        await bot.send_message(referrer_id, f"✅ Ваш реферал {name} подтвердил подписку. Подтверждённых: {cnt}")
    except Exception:
        pass

    if reward_to_send:
        try:
            await bot.send_message(referrer_id, f"🎁 Вы получили награду за 5 рефералов: {reward_to_send[1]}\n\nОбзор: {reward_to_send[2]}\nСсылка: {reward_to_send[3]}")
        except Exception:
            pass


@dp.message(F.text.startswith("/start"))
async def start(message: Message):

    parts = message.text.split(maxsplit=1)
    param = parts[1].strip() if len(parts) > 1 else None

    async with db_lock:
        # Проверяем — новый пользователь или уже был в боте
        cursor.execute("SELECT user_id FROM users WHERE user_id=?", (message.from_user.id,))
        is_new_user = cursor.fetchone() is None

        cursor.execute(
            "INSERT OR IGNORE INTO users (user_id) VALUES (?)",
            (message.from_user.id,)
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
            else:
                cursor.execute("SELECT user_id FROM users WHERE ref_code=?", (param,))
                ref = cursor.fetchone()
                if ref:
                    referrer_id = ref[0]

            if referrer_id and referrer_id != message.from_user.id:
                cursor.execute("INSERT OR IGNORE INTO referrals (referrer_id, referred_id) VALUES (?, ?)", (referrer_id, message.from_user.id))
                db.commit()

    # await — за пределами лока
    if param and is_new_user and referrer_id and referrer_id != message.from_user.id:
        try:
            if await check_sub(message.from_user.id):
                await confirm_referral_if_pending(message.from_user.id)
        except Exception:
            pass

    text = """
🔥 крмп сборки от чапы 

Лучшие сборки Rodina RP
"""

    await message.answer(
        text,
        reply_markup=main_menu(message.from_user.id)
    )


@dp.message(F.text == "/admin")
async def admin_panel(message: Message):

    if message.from_user.id not in ADMINS:
        return

    await message.answer(
        "⚙️ ADMIN PANEL",
        reply_markup=admin_menu()
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
        await message.answer("❌ Неверный user_id — должно быть число")
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
            lc.execute("UPDATE referrals SET confirmed=1 WHERE id=?", (referral_id,))
            db.commit()
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
        await bot.send_message(referrer_id, f"✅ Ваш реферал был подтверждён администратором. Подтверждённых: {cnt}/5")
    except Exception:
        pass

    if reward_to_send:
        try:
            await bot.send_message(referrer_id, f"🎁 Вы получили награду за 5 рефералов: {reward_to_send[1]}\n\nОбзор: {reward_to_send[2]}\nСсылка: {reward_to_send[3]}")
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

    await message.answer(text)


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
        cursor.execute("UPDATE users SET blacklisted=1 WHERE user_id=?", (target_id,))
        db.commit()

    await message.answer(f"🚫 Пользователь {target_id} добавлен в ЧС.")

    try:
        await bot.send_message(target_id, "❌ Вы добавлены в чёрный список бота. Обратитесь к администрации.")
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
        not_banned = row is not None and row[0] != 1
        if not not_found and not not_banned:
            lc.execute("UPDATE users SET blacklisted=0 WHERE user_id=?", (target_id,))
            db.commit()

    if not_found:
        await message.answer(f"⚠️ Пользователь {target_id} не найден в базе.")
        return
    if not_banned:
        await message.answer(f"⚠️ Пользователь {target_id} не в ЧС.")
        return

    await message.answer(f"✅ Пользователь {target_id} удалён из ЧС.")

    try:
        await bot.send_message(target_id, "✅ Вы удалены из чёрного списка бота. Добро пожаловать обратно!")
    except Exception:
        pass


@dp.callback_query(F.data == "builds")
async def builds(callback: CallbackQuery):

    async with db_lock:
        lc = db.cursor()
        lc.execute("SELECT * FROM builds")
        builds_list = lc.fetchall()
        lc.execute("SELECT * FROM builds WHERE recommended=1 LIMIT 1")
        recommended_build = lc.fetchone()

    if not builds_list:

        await callback.message.answer(
            "❌ Сборок пока нет"
        )

        return

    kb = InlineKeyboardBuilder()

    for build in builds_list:

        kb.button(
            text=f"🔥 {build[1]}{' ⭐' if len(build) > 4 and build[4] == 1 else ''}",
            callback_data=f"build_{build[0]}"
        )

    kb.button(text="🔙 Главное меню", callback_data="main_menu")
    kb.adjust(1)

    text = "📦 Список сборок\n\n"

    if recommended_build:
        text += f"⭐ Рекомендуемая сборка:\n\n🔥 {recommended_build[1]}\n\n"

    await callback.message.answer(
        text,
        reply_markup=kb.as_markup()
    )


@dp.callback_query(F.data.startswith("build_"))
async def build(callback: CallbackQuery):

    build_id = int(
        callback.data.split("_")[1]
    )
    user_id = callback.from_user.id

    # 1. Читаем статус ЧС до любых await
    async with db_lock:
        lc = db.cursor()
        lc.execute("SELECT blacklisted FROM users WHERE user_id=?", (user_id,))
        bl_row = lc.fetchone()

    if bl_row and bl_row[0] == 1:
        await callback.message.answer(
            "❌ Вы в ЧС и не можете скачивать сборки. Обратитесь к администрации."
        )
        return

    # 2. Проверяем подписку (await)
    is_subscribed = await check_sub(user_id)

    if is_subscribed:
        await confirm_referral_if_pending(user_id)

    if not is_subscribed:
        # 3. Читаем историю загрузок после await — нужен свежий лок
        async with db_lock:
            lc = db.cursor()
            lc.execute("SELECT 1 FROM downloads WHERE user_id=? LIMIT 1", (user_id,))
            had = lc.fetchone()
            if had:
                lc.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))
                lc.execute("UPDATE users SET blacklisted=1 WHERE user_id=?", (user_id,))
                db.commit()

        if had:
            await callback.message.answer(
                "❌ Вы отписались от канала после получения сборки — вы добавлены в ЧС."
            )
            return

        await callback.message.answer(
            "❌ Подпишись на канал",
            reply_markup=sub_menu()
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

    # 5. Читаем и обновляем сборку — всё в одном локе
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

    text = f"""
🔥 {build_row[1]}

📥 Скачать:
{build_row[2]}
"""

    kb = InlineKeyboardBuilder()
    kb.button(text="🔙 Главное меню", callback_data="main_menu")
    kb.adjust(1)

    await callback.message.answer(text, reply_markup=kb.as_markup())


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

    text = f"🔥 {build_row[1]}\n\n📥 Скачать:\n{build_row[2]}"
    kb = InlineKeyboardBuilder()
    kb.button(text="🔙 Главное меню", callback_data="main_menu")
    kb.adjust(1)
    await callback.message.answer(text, reply_markup=kb.as_markup())


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
            text += f"{status} [{sid}] {icon} {name} — «{button_text}»\n"
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


@dp.callback_query(F.data == "main_menu")
async def back_to_main(callback: CallbackQuery):
    await callback.message.answer(
        "🔥 крмп сборки от чапы\n\nЛучшие сборки Rodina RP",
        reply_markup=main_menu(callback.from_user.id)
    )


@dp.callback_query(F.data == "admin_panel")
async def admin_panel_callback(callback: CallbackQuery):

    if callback.from_user.id not in ADMINS:
        return

    await callback.message.answer(
        "⚙️ ADMIN PANEL",
        reply_markup=admin_menu()
    )


@dp.callback_query(F.data == "admin_add")
async def admin_add(callback: CallbackQuery):

    if callback.from_user.id not in ADMINS:
        return

    user_states[callback.from_user.id] = "add"

    await callback.message.answer(
        "📦 Отправь:\nНазвание|Ссылка"
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
        lc.execute("SELECT * FROM builds")
        builds_list = lc.fetchall()
        lc.execute("SELECT COUNT(*) FROM users")
        total_users = lc.fetchone()[0]

    text = f"📊 Статистика\n\nПользователей: {total_users}\n\n"

    for b in builds_list:

        text += (
            f"{b[0]} | "
            f"{b[1]} — "
            f"{b[3]} скачиваний\n"
        )

    await callback.message.answer(text)


@dp.callback_query(F.data == "admin_broadcast")
async def admin_broadcast(callback: CallbackQuery):

    if callback.from_user.id not in ADMINS:
        return

    user_states[callback.from_user.id] = "broadcast"

    await callback.message.answer(
        "📢 Отправь текст рассылки"
    )


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
            "Чтобы установить новую — отправьте сообщение в формате:\nНазвание|YouTube ссылка|Ссылка для скачивания"
        )
    else:
        text = (
            "Награда не задана.\n"
            "Чтобы установить — отправьте сообщение в формате:\nНазвание|YouTube ссылка|Ссылка для скачивания"
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

    text = "🔒 Чёрный список:\n\n"

    for r in rows:
        text += f"{r[0]}\n"

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

    await callback.message.answer("✅ Глобальная амнистия выполнена — все сняты с ЧС")


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

    user_id = callback.from_user.id

    me = await bot.get_me()
    link = f"https://t.me/{me.username}?start=ref_{user_id}"

    async with db_lock:
        lc = db.cursor()
        lc.execute("SELECT COUNT(*) FROM referrals WHERE referrer_id=? AND confirmed=1", (user_id,))
        cnt = lc.fetchone()[0]
        reward = None
        if cnt >= 5:
            lc.execute("SELECT title, youtube_link, download_link FROM rewards WHERE active=1 LIMIT 1")
            reward = lc.fetchone()

    text = (
        "🤝 Партнёрская программа:\n\n"
        "За приглашение 5 человек в бота вы получите уникальную сборку для партнеров.\n\n"
        f"Ваша ссылка: {link}\n\n"
        "Важно: реферал засчитывается только после подписки на телеграмм канал.\n\n"
    )

    if cnt < 5:
        text += f"Подтверждённых рефералов: {cnt}/5"
    else:
        if reward:
            text += (
                f"Подтверждённых рефералов: {cnt}/5\n\n"
                f"🎁 Награда: {reward[0]}\n"
                f"Обзор (YouTube): {reward[1]}\n"
                f"Ссылка для получения: {reward[2]}\n"
            )
        else:
            text += f"Подтверждённых рефералов: {cnt}/5\n\n🎁 Награда ещё не задана админом."

    kb = InlineKeyboardBuilder()
    kb.button(text="🔙 Главное меню", callback_data="main_menu")
    kb.adjust(1)

    await callback.message.answer(text, reply_markup=kb.as_markup())


@dp.message(F.text == "/ref")
async def ref_command(message: Message):

    user_id = message.from_user.id

    me = await bot.get_me()
    link = f"https://t.me/{me.username}?start=ref_{user_id}"

    async with db_lock:
        lc = db.cursor()
        lc.execute("SELECT COUNT(*) FROM referrals WHERE referrer_id=? AND confirmed=1", (user_id,))
        cnt = lc.fetchone()[0]

    text = (
        "🤝 Партнёрская программа:\n\n"
        "За приглашение 5 человек в бота вы получите уникальную сборку для партнеров.\n\n"
        f"Ваша ссылка: {link}\n\n"
        "Важно: реферал засчитывается только после подписки на телеграмм канал.\n\n"
        f"Подтверждённых рефералов: {cnt}/5"
    )

    await message.answer(text)


@dp.message()
async def handle_text(message: Message):

    user_id = message.from_user.id
    state = user_states.get(user_id)

    if not state:
        return

    if state == "add":

        try:

            data = message.text.split("|")

            if len(data) < 2:
                await message.answer("❌ Формат: Название|Ссылка")
                return

            name = data[0].strip()
            link = data[1].strip()

            async with db_lock:
                lc = db.cursor()
                lc.execute(
                    "INSERT INTO builds (name, link) VALUES (?, ?)",
                    (name, link)
                )
                db.commit()

            user_states.pop(user_id, None)

            await message.answer(
                f"✅ Сборка добавлена: {name}"
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

        async with db_lock:
            lc = db.cursor()
            lc.execute("SELECT user_id FROM users")
            users = lc.fetchall()

        success = 0

        for user in users:

            try:

                await bot.send_message(
                    user[0],
                    message.text
                )

                success += 1

            except:
                pass

        user_states.pop(user_id, None)

        await message.answer(
            f"✅ Рассылка завершена\n"
            f"📨 Отправлено: {success}"
        )

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
                lc.execute("UPDATE users SET blacklisted=0 WHERE user_id=?", (target_id,))
                db.commit()

            user_states.pop(user_id, None)

            await message.answer(
                "✅ Пользователь снят с ЧС"
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
        async with db_lock:
            lc = db.cursor()
            lc.execute(
                "INSERT INTO sponsors (type, target, button_text, name) VALUES (?, ?, ?, ?)",
                ("channel", str(channel_id), button_text, name)
            )
            db.commit()
        user_states.pop(user_id, None)
        user_temp.pop(user_id, None)
        await message.answer(f"✅ Спонсор-канал «{name}» добавлен!")
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
    for admin_id in ADMINS:
        try:
            await bot.send_message(
                admin_id,
                "✅ Бот запущен и работает!\n🕐 " + __import__('datetime').datetime.now().strftime("%d.%m.%Y %H:%M:%S")
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


@dp.chat_member(ChatMemberUpdatedFilter(JOIN_TRANSITION))
async def on_user_join_channel(event: ChatMemberUpdated):
    if event.chat.id != CHANNEL_ID:
        return
    user_id = event.new_chat_member.user.id
    await confirm_referral_if_pending(user_id)


@dp.chat_member(ChatMemberUpdatedFilter(LEAVE_TRANSITION))
async def on_user_leave_channel(event: ChatMemberUpdated):
    if event.chat.id != CHANNEL_ID:
        return

    user_id = event.new_chat_member.user.id

    async with db_lock:
        cursor.execute("SELECT id, referrer_id FROM referrals WHERE referred_id=? AND confirmed=1", (user_id,))
        row = cursor.fetchone()
        if not row:
            return

        referral_id, referrer_id = row
        cursor.execute("UPDATE referrals SET confirmed=0 WHERE id=?", (referral_id,))
        db.commit()

        cursor.execute("SELECT COUNT(*) FROM referrals WHERE referrer_id=? AND confirmed=1", (referrer_id,))
        cnt = cursor.fetchone()[0]

    try:
        await bot.send_message(
            referrer_id,
            f"❌ Ваш реферал отписался от канала — реферал аннулирован.\nПодтверждённых: {cnt}/5"
        )
    except Exception:
        pass


async def main():

    print("BOT STARTED")

    await notify_admins_on_start()

    await asyncio.gather(
        run_web(),
        dp.start_polling(bot, allowed_updates=["message", "callback_query", "chat_member"]),
        daily_backup()
    )


if __name__ == "__main__":

    asyncio.run(main())

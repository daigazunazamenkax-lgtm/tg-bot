import asyncio
import os
import sqlite3
import random
import string
from aiohttp import web

from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, CallbackQuery
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


async def confirm_referral_if_pending(user_id):

    cursor.execute("SELECT id, referrer_id FROM referrals WHERE referred_id=? AND confirmed=0", (user_id,))
    pending = cursor.fetchone()
    if not pending:
        return

    referral_id, referrer_id = pending

    cursor.execute("UPDATE referrals SET confirmed=1 WHERE id=?", (referral_id,))
    db.commit()

    try:
        chat = await bot.get_chat(user_id)
        name = chat.username or chat.first_name or str(user_id)
    except Exception:
        name = str(user_id)

    cnt = 0
    try:
        cursor.execute("SELECT COUNT(*) FROM referrals WHERE referrer_id=? AND confirmed=1", (referrer_id,))
        cnt = cursor.fetchone()[0]
        await bot.send_message(referrer_id, f"✅ Ваш реферал {name} подтвердил подписку. Подтверждённых: {cnt}")
    except Exception:
        pass

    if cnt >= 5:
        try:
            cursor.execute("SELECT id, title, youtube_link, download_link FROM rewards WHERE active=1 LIMIT 1")
            reward = cursor.fetchone()
            if reward:
                reward_id = reward[0]
                cursor.execute("SELECT 1 FROM reward_issued WHERE user_id=? AND reward_id=?", (referrer_id, reward_id))
                already = cursor.fetchone()
                if not already:
                    try:
                        await bot.send_message(referrer_id, f"🎁 Вы получили награду за 5 рефералов: {reward[1]}\n\nОбзор: {reward[2]}\nСсылка: {reward[3]}")
                    except Exception:
                        pass
                    cursor.execute("INSERT INTO reward_issued (user_id, reward_id) VALUES (?, ?)", (referrer_id, reward_id))
                    db.commit()
        except Exception:
            pass


@dp.message(F.text.startswith("/start"))
async def start(message: Message):

    parts = message.text.split(maxsplit=1)
    param = parts[1].strip() if len(parts) > 1 else None

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

    if param:
        try:
            referrer_id = None
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

    cursor.execute("SELECT user_id FROM users")
    users = cursor.fetchall()

    if not users:
        await message.answer("👤 Пользователей пока нет.")
        return

    text = f"👥 Всего пользователей: {len(users)}\n\n"
    ids = [str(u[0]) for u in users]
    text += "\n".join(ids)

    if len(text) > 4000:
        text = text[:4000] + "\n..."

    await message.answer(text)


@dp.callback_query(F.data == "builds")
async def builds(callback: CallbackQuery):

    cursor.execute("SELECT * FROM builds")

    builds_list = cursor.fetchall()

    if not builds_list:

        await callback.message.answer(
            "❌ Сборок пока нет"
        )

        return

    kb = InlineKeyboardBuilder()

    cursor.execute("SELECT * FROM builds WHERE recommended=1 LIMIT 1")
    recommended_build = cursor.fetchone()

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

    cursor.execute("SELECT blacklisted FROM users WHERE user_id=?", (callback.from_user.id,))
    row = cursor.fetchone()
    if row and row[0] == 1:
        await callback.message.answer(
            "❌ Вы в ЧС и не можете скачивать сборки. Обратитесь к администрации."
        )
        return

    is_subscribed = await check_sub(
        callback.from_user.id
    )

    if is_subscribed:
        await confirm_referral_if_pending(callback.from_user.id)

    if not is_subscribed:

        cursor.execute("SELECT 1 FROM downloads WHERE user_id=? LIMIT 1", (callback.from_user.id,))
        had = cursor.fetchone()

        if had:
            cursor.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (callback.from_user.id,))
            cursor.execute("UPDATE users SET blacklisted=1 WHERE user_id=?", (callback.from_user.id,))
            db.commit()

            await callback.message.answer(
                "❌ Вы отписались от канала после получения сборки — вы добавлены в ЧС."
            )

            return

        await callback.message.answer(
            "❌ Подпишись на канал",
            reply_markup=sub_menu()
        )

        return

    cursor.execute(
        "SELECT * FROM builds WHERE id=?",
        (build_id,)
    )

    build_row = cursor.fetchone()

    if not build_row:
        await callback.message.answer("❌ Сборка не найдена.")
        return

    cursor.execute(
        "UPDATE builds SET downloads = downloads + 1 WHERE id=?",
        (build_id,)
    )

    db.commit()

    try:
        cursor.execute("INSERT INTO downloads (user_id, build_id) VALUES (?, ?)", (callback.from_user.id, build_id))
        db.commit()
    except Exception:
        pass

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

    cursor.execute("SELECT * FROM builds")

    builds_list = cursor.fetchall()

    text = "🗑 Сборки:\n\n"

    for b in builds_list:
        text += f"{b[0]} | {b[1]}\n"

    text += "\nОтправь ID для удаления"

    await callback.message.answer(text)


@dp.callback_query(F.data == "admin_stats")
async def admin_stats(callback: CallbackQuery):

    if callback.from_user.id not in ADMINS:
        return

    cursor.execute("SELECT * FROM builds")

    builds_list = cursor.fetchall()

    cursor.execute("SELECT COUNT(*) FROM users")
    total_users = cursor.fetchone()[0]

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

    cursor.execute("SELECT id, title, youtube_link, download_link FROM rewards WHERE active=1 LIMIT 1")
    row = cursor.fetchone()

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

    cursor.execute("SELECT user_id FROM users WHERE blacklisted=1")

    rows = cursor.fetchall()

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

    cursor.execute("UPDATE users SET blacklisted=0 WHERE blacklisted=1")
    db.commit()

    await callback.message.answer("✅ Глобальная амнистия выполнена — все сняты с ЧС")


@dp.callback_query(F.data == "admin_cancel")
async def admin_cancel(callback: CallbackQuery):

    user_states.pop(callback.from_user.id, None)
    await callback.message.answer("Отмена")


@dp.callback_query(F.data == "admin_recommend")
async def admin_recommend(callback: CallbackQuery):

    if callback.from_user.id not in ADMINS:
        return

    user_states[callback.from_user.id] = "recommend"

    cursor.execute("SELECT * FROM builds")

    builds_list = cursor.fetchall()

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

    cursor.execute("SELECT COUNT(*) FROM referrals WHERE referrer_id=? AND confirmed=1", (user_id,))
    cnt = cursor.fetchone()[0]

    text = (
        "🤝 Партнёрская программа:\n\n"
        "За приглашение 5 человек в бота вы получите уникальную сборку для партнеров.\n\n"
        f"Ваша ссылка: {link}\n\n"
        "Важно: реферал засчитывается только после подписки на телеграмм канал.\n\n"
    )

    if cnt < 5:
        text += f"Подтверждённых рефералов: {cnt}/5"
    else:
        cursor.execute("SELECT title, youtube_link, download_link FROM rewards WHERE active=1 LIMIT 1")
        reward = cursor.fetchone()
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

    cursor.execute("SELECT COUNT(*) FROM referrals WHERE referrer_id=? AND confirmed=1", (user_id,))
    cnt = cursor.fetchone()[0]

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

            cursor.execute(
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

            cursor.execute(
                "DELETE FROM builds WHERE id=?",
                (build_id,)
            )

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

            cursor.execute("UPDATE builds SET recommended=0")
            cursor.execute(
                "UPDATE builds SET recommended=1 WHERE id=?",
                (build_id,)
            )

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

        cursor.execute("SELECT user_id FROM users")

        users = cursor.fetchall()

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

            cursor.execute("UPDATE rewards SET active=0 WHERE active=1")
            cursor.execute("INSERT INTO rewards (title, youtube_link, download_link, active) VALUES (?, ?, ?, 1)", (title, yt, link))
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

            cursor.execute(
                "UPDATE users SET blacklisted=0 WHERE user_id=?",
                (target_id,)
            )

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


async def main():

    print("BOT STARTED")

    await notify_admins_on_start()

    await asyncio.gather(
        run_web(),
        dp.start_polling(bot),
        daily_backup()
    )


if __name__ == "__main__":

    asyncio.run(main())

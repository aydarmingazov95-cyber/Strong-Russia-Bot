import asyncio
import html
import logging
import os
import sqlite3
from pathlib import Path

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
OWNER_ID = int(os.getenv("OWNER_ID", "0"))  # главный админ (числовой Telegram ID)
WELCOME_PHOTO = Path(__file__).parent / "welcome.jpg"
DB_PATH = Path(__file__).parent / "bot.db"

logging.basicConfig(level=logging.INFO)
router = Router()


# ───────────────────────── База данных ─────────────────────────
def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with db() as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS admins (user_id INTEGER PRIMARY KEY)"
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS applications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                username TEXT,
                text TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                admin_id INTEGER
            )"""
        )
        if OWNER_ID:
            conn.execute("INSERT OR IGNORE INTO admins VALUES (?)", (OWNER_ID,))


def get_admins() -> list[int]:
    with db() as conn:
        return [r["user_id"] for r in conn.execute("SELECT user_id FROM admins")]


def is_admin(user_id: int) -> bool:
    return user_id == OWNER_ID or user_id in get_admins()


def has_pending(user_id: int) -> bool:
    with db() as conn:
        row = conn.execute(
            "SELECT 1 FROM applications WHERE user_id=? AND status='pending'",
            (user_id,),
        ).fetchone()
    return row is not None


# ───────────────────────── Тексты ─────────────────────────
GREETING = (
    "Приветик! 👋 Это <b>Strong Russia</b> 🇷🇺\n\n"
    "Захотел встать на медиа? 🎬\n"
    "Нажимай на кнопку снизу, там всё объяснят. 👇"
)

FORM = (
    "Заполни данную форму, чтобы стать медиа партнёром: 📝\n\n"
    "<pre>1. Ваш NickName в игре: \n"
    "2. Ссылка на ваш Tiktok Аккаунт: \n"
    "3. Ваш возраст: \n"
    "4. Ваш юз в Telegram: \n"
    "5. Сколько у вас в среднем просмотров: </pre>\n"
    "👆 Нажми на форму, чтобы скопировать, заполни и отправь мне "
    "<b>одним сообщением</b>."
)

SENT = (
    "✅ Заявка отправлена!\n\n"
    "⏳ Рассмотрение займёт до <b>24 часов</b>, ожидайте. 🙏"
)

APPROVED = "<i>Твоя заявка была одобрена! 🎉 Вам отпишет наш пиар менеджер 💬</i>"
DECLINED = "<i>Твоя заявка получила статус отказано ❌</i>"


class Form(StatesGroup):
    waiting_text = State()


join_kb = InlineKeyboardMarkup(
    inline_keyboard=[
        [InlineKeyboardButton(text="🎬 Вступить на медиа", callback_data="join")]
    ]
)
cancel_kb = InlineKeyboardMarkup(
    inline_keyboard=[
        [InlineKeyboardButton(text="↩️ Отмена", callback_data="cancel")]
    ]
)


def decision_kb(app_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Одобрено", callback_data=f"app:ok:{app_id}"),
                InlineKeyboardButton(text="❌ Отказано", callback_data=f"app:no:{app_id}"),
            ]
        ]
    )


# ───────────────────────── Пользователь ─────────────────────────
@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    if WELCOME_PHOTO.exists():
        await message.answer_photo(
            FSInputFile(WELCOME_PHOTO), caption=GREETING, reply_markup=join_kb
        )
    else:
        await message.answer(GREETING, reply_markup=join_kb)


@router.callback_query(F.data == "join")
async def on_join(call: CallbackQuery, state: FSMContext) -> None:
    if has_pending(call.from_user.id):
        await call.answer(
            "⏳ Твоя заявка уже на рассмотрении, ожидай ответа!", show_alert=True
        )
        return
    await state.set_state(Form.waiting_text)
    await call.message.answer(FORM, reply_markup=cancel_kb)
    await call.answer()


@router.callback_query(F.data == "cancel")
async def on_cancel(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await call.message.answer("Окей, отменил. 🙌 Если передумаешь, жми /start")
    await call.answer()


@router.message(Form.waiting_text, F.text)
async def on_form(message: Message, state: FSMContext, bot: Bot) -> None:
    text = message.text.strip()
    if len(text) < 15:
        await message.answer(
            "🤔 Что-то слишком коротко. Заполни форму полностью и отправь одним сообщением."
        )
        return

    user = message.from_user
    username = f"@{user.username}" if user.username else "нет юзернейма"

    with db() as conn:
        cur = conn.execute(
            "INSERT INTO applications (user_id, username, text) VALUES (?, ?, ?)",
            (user.id, username, text),
        )
        app_id = cur.lastrowid

    card = (
        f"📨 <b>Новая заявка #{app_id}</b>\n"
        f"👤 От: {html.escape(user.full_name)} ({html.escape(username)})\n"
        f"🆔 ID: <code>{user.id}</code>\n\n"
        f"{html.escape(text)}"
    )
    for admin_id in get_admins():
        try:
            await bot.send_message(admin_id, card, reply_markup=decision_kb(app_id))
        except Exception as e:  # админ мог не запускать бота
            logging.warning("Не удалось отправить заявку админу %s: %s", admin_id, e)

    await state.clear()
    await message.answer(SENT)


# ───────────────────────── Решение админа ─────────────────────────
@router.callback_query(F.data.startswith("app:"))
async def on_decision(call: CallbackQuery, bot: Bot) -> None:
    if not is_admin(call.from_user.id):
        await call.answer("⛔ Нет доступа", show_alert=True)
        return

    _, action, raw_id = call.data.split(":")
    app_id = int(raw_id)

    with db() as conn:
        app = conn.execute(
            "SELECT * FROM applications WHERE id=?", (app_id,)
        ).fetchone()
        if app is None:
            await call.answer("Заявка не найдена", show_alert=True)
            return
        if app["status"] != "pending":
            await call.answer("⚠️ Эта заявка уже обработана", show_alert=True)
            try:
                await call.message.edit_reply_markup(reply_markup=None)
            except Exception:
                pass
            return
        status = "approved" if action == "ok" else "declined"
        conn.execute(
            "UPDATE applications SET status=?, admin_id=? WHERE id=?",
            (status, call.from_user.id, app_id),
        )

    approved = status == "approved"
    try:
        await bot.send_message(app["user_id"], APPROVED if approved else DECLINED)
    except Exception as e:
        logging.warning("Не удалось уведомить пользователя %s: %s", app["user_id"], e)

    admin_name = (
        f"@{call.from_user.username}" if call.from_user.username else call.from_user.full_name
    )
    mark = "✅ <b>ОДОБРЕНО</b>" if approved else "❌ <b>ОТКАЗАНО</b>"
    await call.message.edit_text(
        f"{call.message.html_text}\n\n{mark} — {html.escape(admin_name)}",
        reply_markup=None,
    )
    await call.answer("Готово! Игрок уведомлён 👌")


# ───────────────────────── Админ-команды ─────────────────────────
@router.message(Command("addadmin"))
async def cmd_addadmin(message: Message) -> None:
    if message.from_user.id != OWNER_ID:
        return
    parts = message.text.split()
    if len(parts) != 2 or not parts[1].lstrip("-").isdigit():
        await message.answer("Использование: <code>/addadmin 123456789</code> 👈")
        return
    uid = int(parts[1])
    with db() as conn:
        conn.execute("INSERT OR IGNORE INTO admins VALUES (?)", (uid,))
    await message.answer(f"✅ Админ <code>{uid}</code> добавлен! 👑")


@router.message(Command("deladmin"))
async def cmd_deladmin(message: Message) -> None:
    if message.from_user.id != OWNER_ID:
        return
    parts = message.text.split()
    if len(parts) != 2 or not parts[1].lstrip("-").isdigit():
        await message.answer("Использование: <code>/deladmin 123456789</code> 👈")
        return
    uid = int(parts[1])
    if uid == OWNER_ID:
        await message.answer("🙅 Главного админа удалить нельзя.")
        return
    with db() as conn:
        conn.execute("DELETE FROM admins WHERE user_id=?", (uid,))
    await message.answer(f"🗑 Админ <code>{uid}</code> удалён.")


@router.message(Command("admins"))
async def cmd_admins(message: Message) -> None:
    if not is_admin(message.from_user.id):
        return
    lines = "\n".join(
        f"{'👑' if a == OWNER_ID else '🛡'} <code>{a}</code>" for a in get_admins()
    )
    await message.answer(f"<b>Список админов:</b>\n{lines}")


@router.message(Command("id"))
async def cmd_id(message: Message) -> None:
    await message.answer(f"🆔 Твой ID: <code>{message.from_user.id}</code>")


async def main() -> None:
    if not BOT_TOKEN:
        raise SystemExit("Укажи BOT_TOKEN в файле .env")
    init_db()
    bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    dp.include_router(router)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())

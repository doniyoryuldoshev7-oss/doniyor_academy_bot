"""Admin paneli: foydalanuvchilar bo'limi (admin.py dan ajratilgan)."""

from ...tg_retry import retry as _tg_retry
import sys
import asyncio
import logging
import json
import re
import html
from pathlib import Path
from uuid import uuid4
from ...models import Subject, Topic, Question, User, TestAttempt, AnswerLog
from ...keyboards import main_menu
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton, WebAppInfo, ReplyKeyboardRemove
from aiogram.fsm.context import FSMContext
from aiogram.types import FSInputFile
from sqlalchemy import select, func
from ...config import settings
from ...db import SessionLocal
from ...models import Subject, Topic, Question, User
from ...states import AdminState
from ...image_cropper import crop_questions
from ...web_crop import create_session, web_app_url, finish_session, get_session
from ...admin_roles import can_add_questions, is_superadmin
from datetime import datetime, timedelta
from aiogram.fsm.state import StatesGroup, State
from ..start import registration_complete
from ...models import QuestionEditorGrant
from ...role_management import RoleOperationRejected, RolePermissionDenied, set_question_editor_role
from ._common import is_admin
from .statistika import users

router = Router()


@router.callback_query(F.data.startswith("adm:approve_user:"))
async def approve_user(c: CallbackQuery):
    if not is_admin(c.from_user.id):
        return

    telegram_id = int(c.data.split(":")[2])

    async with SessionLocal() as s:
        user = await s.scalar(
            select(User).where(User.telegram_id == telegram_id)
        )

        if not user:
            await c.answer("Foydalanuvchi topilmadi.", show_alert=True)
            return

        invalid_values = {
            "",
            "-",
            "—",
            "–",
            "_",
            ".",
            "yo'q",
            "yo‘q",
            "yoq",
            "none",
            "null",
            "n/a",
            "na",
        }

        missing = []

        full_name = (user.full_name or "").strip()
        phone_number = (user.phone_number or "").strip()
        group_name = (user.group_name or "").strip()

        if len(full_name.split()) < 2:
            missing.append("ism-familiya")

        if group_name.lower() in invalid_values:
            missing.append("guruh")

        if missing:
            await c.answer(
                "Ma'lumotlar to'liq emas: "
                + ", ".join(missing),
                show_alert=True,
            )
            return

        user.registration_status = "approved"
        await s.commit()

        full_name = user.full_name or "Foydalanuvchi"

    try:
        await _tg_retry(lambda: c.bot.send_message(
            telegram_id,
            "<b>Ro'yxatdan o'tishingiz tasdiqlandi!</b>\n\n"
            f"Ism-familiya: <b>{full_name}</b>\n\n"
            "Endi Doniyor Academy dan toliq foydalanishingiz mumkin.",
            reply_markup=main_menu(False),
        ), tries=3)
    except Exception:
        pass

    await c.message.edit_text(
        "<b>FOYDALANUVCHI TASDIQLANDI!</b>\n\n"
        f"Ism-familiya: <b>{full_name}</b>\n"
        f"Telegram ID: <code>{telegram_id}</code>\n\n"
        "Foydalanuvchiga tasdiqlanganligi haqida xabar yuborildi.",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="Admin panel", callback_data="admin")]
            ]
        ),
        parse_mode="HTML",
    )

    await c.answer("Foydalanuvchi tasdiqlandi.")


@router.callback_query(F.data.startswith("adm:reject_user:"))
async def reject_user(c: CallbackQuery):
    if not is_admin(c.from_user.id):
        return

    telegram_id = int(c.data.split(":")[2])

    async with SessionLocal() as s:
        user = await s.scalar(
            select(User).where(User.telegram_id == telegram_id)
        )

        if not user:
            await c.answer("Foydalanuvchi topilmadi.", show_alert=True)
            return

        user.registration_status = "rejected"
        await s.commit()

        full_name = user.full_name or "Foydalanuvchi"

    try:
        await _tg_retry(lambda: c.bot.send_message(
            telegram_id,
            "<b>Royxatdan otish arizangiz rad etildi.</b>\n\n"
            "Qaytadan royxatdan otish uchun /start buyrugini yuboring.",
        ), tries=3)
    except Exception:
        pass

    await c.message.edit_text(
        "<b>FOYDALANUVCHI RAD ETILDI!</b>\n\n"
        f"Ism-familiya: <b>{full_name}</b>\n"
        f"Telegram ID: <code>{telegram_id}</code>",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="Admin panel", callback_data="admin")]
            ]
        ),
        parse_mode="HTML",
    )

    await c.answer("Foydalanuvchi rad etildi.")


# Users directory: server-side search and stable pagination.
USERS_PAGE_SIZE = 15
USER_SORTS = {"new": "Yangi", "old": "Eski", "az": "A-Z"}


def _users_compat_map():
    """Map single-character Unicode compatibility glyphs to ASCII for SQL search."""
    import string
    import unicodedata

    allowed = set(string.ascii_letters + string.digits)
    original, normalized = [], []
    for start, stop in (
        (0x2100, 0x218F),
        (0x2460, 0x2500),
        (0x1D400, 0x1D800),
        (0xFF00, 0xFFEF),
    ):
        for codepoint in range(start, stop):
            char = chr(codepoint)
            plain = unicodedata.normalize("NFKC", char)
            if len(plain) == 1 and plain in allowed and char != plain:
                original.append(char)
                normalized.append(plain)
    return "".join(original), "".join(normalized)


_USERS_COMPAT_FROM, _USERS_COMPAT_TO = _users_compat_map()


def _users_query_value(value):
    import unicodedata

    value = unicodedata.normalize("NFKC", str(value or "").strip()).casefold()
    return value[1:] if value.startswith("@") else value


def _users_filter(query):
    """Return a bound SQL expression; never load the entire users table into Python."""
    from sqlalchemy import String, cast

    needle = _users_query_value(query)
    if not needle:
        return None

    # SQLAlchemy '+' compiles to CONCAT on supported DBs and || on PostgreSQL.
    parts = (
        User.full_name,
        User.username,
        User.group_name,
        User.phone_number,
        cast(User.telegram_id, String),
    )
    combined = func.coalesce(parts[0], "")
    for part in parts[1:]:
        combined = combined + " " + func.coalesce(part, "")

    combined = func.translate(combined, _USERS_COMPAT_FROM, _USERS_COMPAT_TO)
    escaped = needle.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return combined.ilike(f"%{escaped}%", escape="\\")


async def _users_view(page, sort, query):
    """Build users view with SQL COUNT/OFFSET/LIMIT, no full-table fetch."""
    sort = sort if sort in USER_SORTS else "new"
    query = (query or "")[:50]
    condition = _users_filter(query)

    statement = select(User)
    count_stmt = select(func.count(User.id))
    if condition is not None:
        statement = statement.where(condition)
        count_stmt = count_stmt.where(condition)

    if sort == "old":
        statement = statement.order_by(User.created_at.asc(), User.id.asc())
    elif sort == "az":
        statement = statement.order_by(func.lower(User.full_name).asc(), User.id.asc())
    else:
        statement = statement.order_by(User.created_at.desc(), User.id.desc())

    async with SessionLocal() as session:
        total = await session.scalar(count_stmt) or 0
        last_page = max(0, (total - 1) // USERS_PAGE_SIZE)
        page = min(max(0, page), last_page)
        users = (
            await session.scalars(
                statement.offset(page * USERS_PAGE_SIZE).limit(USERS_PAGE_SIZE)
            )
        ).all()

    buttons = [[
        InlineKeyboardButton(
            text=("\u2022 " if key == sort else "") + title,
            callback_data=f"adm:users:sort:{key}",
        )
        for key, title in USER_SORTS.items()
    ]]

    actions = [InlineKeyboardButton(text="\U0001f50d Qidirish", callback_data="adm:users:find")]
    if query:
        actions.append(InlineKeyboardButton(text="\u2716 Tozalash", callback_data="adm:users:clear"))
    buttons.append(actions)
    buttons.append([
        InlineKeyboardButton(
            text="\u26a0\ufe0f Profili to'liq emas",
            callback_data="adm:incomplete",
        ),
        InlineKeyboardButton(
            text="\U0001f5d1 Tanlab o'chirish",
            callback_data="adm:udel:open",
        ),
    ])
    buttons.append([
        InlineKeyboardButton(
            text="\U0001f6ab Botni bloklaganlar",
            callback_data="adm:blocked",
        ),
    ])

    for user in users:
        icon = {
            "approved": "\u2705",
            "pending": "\u23f3",
            "rejected": "\u274c",
        }.get(user.registration_status, "\u2753")
        if not registration_complete(user):
            icon = "\u26a0\ufe0f"
        buttons.append([InlineKeyboardButton(
            text=f"{icon} {(user.full_name or 'Nomsiz')[:60]}",
            callback_data=f"adm:user:{user.telegram_id}",
        )])

    navigation = []
    if page:
        navigation.append(InlineKeyboardButton(
            text="\u2b05\ufe0f Oldingi", callback_data=f"adm:users:page:{page - 1}",
        ))
    if page < last_page:
        navigation.append(InlineKeyboardButton(
            text="Keyingi \u27a1\ufe0f", callback_data=f"adm:users:page:{page + 1}",
        ))
    if navigation:
        buttons.append(navigation)
    buttons.append([InlineKeyboardButton(text="Admin panel", callback_data="admin")])

    lines = [
        "<b>\U0001f465 FOYDALANUVCHILAR</b>",
        "",
        f"Jami: {total} | Sahifa: {page + 1}/{last_page + 1}",
        f"Tartib: {USER_SORTS[sort]}",
    ]
    if query:
        lines.append(f"Qidiruv: <code>{html.escape(query)}</code>")
    if not users:
        lines.append("Hech narsa topilmadi.")
    else:
        lines.append("Kerakli foydalanuvchini tanlang:")

    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=buttons)


@router.callback_query(F.data == "adm:users")
@router.callback_query(F.data.startswith("adm:users:page:"))
@router.callback_query(F.data.startswith("adm:users:sort:"))
@router.callback_query(F.data == "adm:users:clear")
async def admin_users(c: CallbackQuery, state: FSMContext = None):
    if not is_admin(c.from_user.id):
        return

    from aiogram.exceptions import TelegramBadRequest

    data = await state.get_data() if state is not None else {}
    sort = data.get("users_sort", "new")
    query = data.get("users_query", "")
    page = 0
    callback = c.data or ""

    if callback in {"adm:users", "adm:users:clear"}:
        query = ""
    elif callback.startswith("adm:users:sort:"):
        sort = callback.removeprefix("adm:users:sort:")
        if sort not in USER_SORTS:
            await c.answer("Tartib noto'g'ri.", show_alert=True)
            return
    elif callback.startswith("adm:users:page:"):
        try:
            page = int(callback.removeprefix("adm:users:page:"))
            if not 0 <= page <= 1000000:
                raise ValueError
        except ValueError:
            await c.answer("Sahifa raqami noto'g'ri.", show_alert=True)
            return

    if state is not None:
        if await state.get_state() == AdminState.user_search.state:
            await state.set_state(None)
        await state.update_data(users_sort=sort, users_query=query)

    text, markup = await _users_view(page, sort, query)
    try:
        await c.message.edit_text(text, reply_markup=markup, parse_mode="HTML")
    except TelegramBadRequest as exc:
        if "message is not modified" not in str(exc).lower():
            raise
    await c.answer()


@router.callback_query(F.data == "adm:users:find")
async def admin_users_find(c: CallbackQuery, state: FSMContext):
    if not is_admin(c.from_user.id):
        return

    await state.set_state(AdminState.user_search)
    await c.message.edit_text(
        "<b>\U0001f50d QIDIRUV</b>\n\n"
        "Ism, username, guruh yoki Telegram ID yozing:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Bekor qilish", callback_data="adm:users")]
        ]),
        parse_mode="HTML",
    )
    await c.answer()


@router.message(AdminState.user_search, F.text)
async def admin_users_find_result(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return

    query = (m.text or "").strip()[:50]
    previous = await state.get_data()
    sort = previous.get("users_sort", "new")
    await state.set_state(None)
    await state.update_data(users_sort=sort, users_query=query)
    text, markup = await _users_view(0, sort, query)
    await m.answer(text, reply_markup=markup, parse_mode="HTML")

@router.callback_query(F.data.startswith("adm:user:"))
async def admin_user_detail(c: CallbackQuery):
    if not is_admin(c.from_user.id):
        return

    telegram_id = int(c.data.split(":")[2])

    async with SessionLocal() as s:
        user = await s.scalar(
            select(User).where(User.telegram_id == telegram_id)
        )

        editor_granted = False
        if user is not None:
            editor_granted = (
                await s.scalar(
                    select(QuestionEditorGrant.id).where(
                        QuestionEditorGrant.user_id == user.id
                    )
                )
            ) is not None

    if not user:
        await c.answer(
            "Foydalanuvchi topilmadi.",
            show_alert=True,
        )
        return

    status = {
        "approved": "\u2705 Tasdiqlangan",
        "pending": "\u23f3 Kutilmoqda",
        "rejected": "\u274c Rad etilgan",
    }.get(
        user.registration_status,
        user.registration_status,
    )

    if not registration_complete(user):
        status = "\u26a0\ufe0f Profil to'liq emas"

    blocked = "\U0001f6ab Bloklangan" if user.is_blocked else "\u2705 Faol"

    # Telegram HTML parse mode: user-submitted fields must be escaped.
    safe_name = html.escape(user.full_name or "Nomsiz")
    safe_username = html.escape(user.username or "-")
    safe_phone = html.escape(user.phone_number or "-")
    safe_group = html.escape(user.group_name or "-")
    try:
        from ..royxat import _get_exam_goal as _rg_goal
        safe_goal = html.escape(await _rg_goal(user.telegram_id) or "-")
    except Exception:
        safe_goal = "-"

    text = (
        "<b>\U0001f464 FOYDALANUVCHI MA'LUMOTLARI</b>\n\n"
        f"\U0001f464 Ism-familiya: <b>{safe_name}</b>\n"
        f"\U0001f4f1 Username: @{safe_username}\n"
        f"\U0001f194 Telegram ID: <code>{user.telegram_id}</code>\n"
        f"\U0001f465 Guruh: {safe_group}\n"
        f"\U0001f3af Maqsad: {safe_goal}\n"
        f"\U0001f4cc Holat: <b>{html.escape(status)}</b>\n"
        f"\U0001f512 Hisob: <b>{blocked}</b>"
    )

    if not registration_complete(user):
        text += "\n\n\u26a0\ufe0f Ariza to'liq emas. /start orqali ro'yxatdan o'tish kerak."

    if editor_granted:
        text += "\n\n\U0001f6e1 Cheklangan admin: faol"

    buttons = []

    if is_superadmin(c.from_user.id):
        if editor_granted:
            buttons.append([
                InlineKeyboardButton(
                    text="\U0001f6ab Admin huquqini bekor qilish",
                    callback_data=f"adm:role:revoke:ask:{telegram_id}",
                )
            ])
        elif (
            not is_admin(user.telegram_id)
            and user.registration_status == "approved"
            and not user.is_blocked
            and registration_complete(user)
        ):
            buttons.append([
                InlineKeyboardButton(
                    text="\U0001f6e1 Cheklangan admin qilish",
                    callback_data=f"adm:role:grant:ask:{telegram_id}",
                )
            ])


    if user.registration_status != "approved" and registration_complete(user):
        buttons.append(
            [
                InlineKeyboardButton(
                    text="\u2705 Tasdiqlash",
                    callback_data=f"adm:approve_user:{user.telegram_id}",
                )
            ]
        )

    if user.registration_status != "rejected":
        # Guruhga biriktirish va rad etish shablonlari (ro'yxatdan o'tish arizasidagi kabi)
        from ..royxat import card_buttons as _rg_card
        buttons.extend(_rg_card(user.telegram_id, is_admin(user.telegram_id)))

    if user.is_blocked:
        buttons.append(
            [
                InlineKeyboardButton(
                    text="\U0001f513 Blokdan chiqarish",
                    callback_data=f"adm:unblock_user:{user.telegram_id}",
                )
            ]
        )
    else:
        buttons.append(
            [
                InlineKeyboardButton(
                    text="\U0001f6ab Bloklash",
                    callback_data=f"adm:block_user:{user.telegram_id}",
                )
            ]
        )

    buttons.append(
        [
            InlineKeyboardButton(
                text="\U0001f465 Foydalanuvchilar",
                callback_data="adm:users",
            )
        ]
    )

    buttons.append(
        [
            InlineKeyboardButton(
                text="Admin panel",
                callback_data="admin",
            )
        ]
    )

    await c.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=buttons
        ),
        parse_mode="HTML",
    )

    await c.answer()

@router.callback_query(F.data.startswith("adm:block_user:"))
async def block_user(c: CallbackQuery):
    if not is_admin(c.from_user.id):
        return

    telegram_id = int(c.data.split(":")[2])

    async with SessionLocal() as s:
        user = await s.scalar(
            select(User).where(User.telegram_id == telegram_id)
        )

        if not user:
            await c.answer(
                "Foydalanuvchi topilmadi.",
                show_alert=True,
            )
            return

        if user.is_blocked:
            await c.answer(
                "Bu foydalanuvchi allaqachon bloklangan.",
                show_alert=True,
            )
            return

        user.is_blocked = True
        await s.commit()

    await c.answer("\U0001f6ab Foydalanuvchi bloklandi.")

    await admin_user_detail(c)


@router.callback_query(F.data.startswith("adm:unblock_user:"))
async def unblock_user(c: CallbackQuery):
    if not is_admin(c.from_user.id):
        return

    telegram_id = int(c.data.split(":")[2])

    async with SessionLocal() as s:
        user = await s.scalar(
            select(User).where(User.telegram_id == telegram_id)
        )

        if not user:
            await c.answer(
                "Foydalanuvchi topilmadi.",
                show_alert=True,
            )
            return

        if not user.is_blocked:
            await c.answer(
                "Bu foydalanuvchi bloklanmagan.",
                show_alert=True,
            )
            return

        user.is_blocked = False
        await s.commit()

    await c.answer("\U0001f513 Foydalanuvchi blokdan chiqarildi.")

    await admin_user_detail(c)

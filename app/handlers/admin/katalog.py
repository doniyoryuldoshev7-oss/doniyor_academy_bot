"""Admin paneli: katalog bo'limi (admin.py dan ajratilgan)."""

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
from ._common import admin_menu, cancel_kb, is_admin, natural_topic_key, photo_bytes, save_question_image

router = Router()


# -------------------- CATALOG / STATS --------------------
# -------------------- CATALOG / STATS --------------------
# -------------------- CATALOG / STATS --------------------
@router.callback_query(F.data == "adm:catalog")
async def catalog(c: CallbackQuery):
    if not is_admin(c.from_user.id):
        return

    async with SessionLocal() as s:
        subjects = (await s.scalars(select(Subject).order_by(Subject.name))).all()

    if not subjects:
        await c.answer("Hali fanlar yo'q.", show_alert=True)
        return

    buttons = [
        [
            InlineKeyboardButton(
                text=f"📚 {sub.name}", callback_data=f"adm:subject:{sub.id}"
            )
        ]
        for sub in subjects
    ]

    buttons.append([InlineKeyboardButton(text="⬅️ Orqaga", callback_data="adm:g:fan")])

    await c.message.edit_text(
        "📚 <b>Fanlar</b>\n\nKerakli fanni tanlang:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )
    await c.answer()


@router.callback_query(F.data.startswith("adm:subject:"))
async def catalog_subject(c: CallbackQuery):
    if not is_admin(c.from_user.id):
        return

    sid = int(c.data.split(":")[2])

    async with SessionLocal() as s:
        subject = await s.get(Subject, sid)

        if not subject:
            await c.answer("Fan topilmadi.", show_alert=True)
            return

        topics = (
            await s.scalars(
                select(Topic).where(Topic.subject_id == sid).order_by(Topic.name)
            )
        ).all()
        topics = sorted(topics, key=natural_topic_key)

        topics = sorted(topics, key=natural_topic_key)

        topic_count = len(topics)

    buttons = [
        [
            InlineKeyboardButton(
                text=f"📖 {topic.name}", callback_data=f"adm:topic:{topic.id}"
            )
        ]
        for topic in topics
    ]

    if not topics:
        buttons.append(
            [
                InlineKeyboardButton(
                    text="➕ Mavzu qo'shish", callback_data="adm:add_topic"
                )
            ]
        )

    buttons.append(
        [
            InlineKeyboardButton(
                text="🗑 Fanni o'chirish",
                callback_data=f"adm:delete_subject:{subject.id}",
            )
        ]
    )

    buttons.append(
        [InlineKeyboardButton(text="⬅️ Fanlar", callback_data="adm:catalog")]
    )

    await c.message.edit_text(
        f"📚 <b>{subject.name}</b>\n\n"
        f"📖 Mavzular: <b>{topic_count} ta</b>\n\n"
        "Kerakli amalni tanlang:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        parse_mode="HTML",
    )

    await c.answer()


@router.callback_query(F.data.startswith("adm:topic:"))
async def catalog_topic(c: CallbackQuery):
    if not is_admin(c.from_user.id):
        return

    tid = int(c.data.split(":")[2])

    async with SessionLocal() as s:
        topic = await s.get(Topic, tid)

        if not topic:
            await c.answer("Mavzu topilmadi.", show_alert=True)
            return

        count = await s.scalar(
            select(func.count(Question.id)).where(Question.is_active.is_(True), Question.topic_id == tid)
        )

    buttons = [
        [
            InlineKeyboardButton(
                text="📋 Savollarni ko'rish", callback_data=f"adm:questions:{tid}"
            )
        ],
        [
            InlineKeyboardButton(
                text="✏️ Mavzuni tahrirlash", callback_data=f"adm:edit_topic:{tid}"
            )
        ],
        [
            InlineKeyboardButton(
                text="🗑 Mavzuni o'chirish", callback_data=f"adm:delete_topic:{tid}"
            )
        ],
        [
            InlineKeyboardButton(
                text="⬅️ Mavzular", callback_data=f"adm:subject:{topic.subject_id}"
            )
        ],
    ]

    await c.message.edit_text(
        f"📖 <b>{topic.name}</b>\n\n" f"📋 Savollar: <b>{count}</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )
    await c.answer()


@router.callback_query(F.data.startswith("adm:edit_topic:"))
async def edit_topic_start(c: CallbackQuery, state: FSMContext):
    if not is_admin(c.from_user.id):
        return

    tid = int(c.data.split(":")[2])

    async with SessionLocal() as s:
        topic = await s.get(Topic, tid)

    if not topic:
        await c.answer("Mavzu topilmadi.", show_alert=True)
        return

    await state.update_data(topic_id=tid)
    await state.set_state(AdminState.edit_topic_name)

    await c.message.edit_text(
        "✏️ <b>MAVZUNI TAHRIRLASH</b>\n\n"
        f"Eski nom: <b>{topic.name}</b>\n\n"
        "Yangi mavzu nomini yuboring:",
        reply_markup=cancel_kb(),
    )
    await c.answer()


@router.message(AdminState.edit_topic_name)
async def edit_topic_finish(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return

    name = (m.text or "").strip()

    if not name:
        await m.answer("❗ Mavzu nomi bo'sh bo'lishi mumkin emas.")
        return

    data = await state.get_data()
    tid = data["topic_id"]

    async with SessionLocal() as s:
        topic = await s.get(Topic, tid)

        if not topic:
            await state.clear()
            await m.answer("❌ Mavzu topilmadi.", reply_markup=admin_menu())
            return

        topic.name = name
        subject_id = topic.subject_id

        await s.commit()

    await state.clear()

    await m.answer(
        "✅ <b>MAVZU MUVAFFAQIYATLI TAHRIRLANDI!</b>\n\n" f"Yangi nom: <b>{name}</b>",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="📖 Mavzuni ko'rish", callback_data=f"adm:topic:{tid}"
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="⬅️ Mavzular", callback_data=f"adm:subject:{subject_id}"
                    )
                ],
                [InlineKeyboardButton(text="⚙️ Admin panel", callback_data="admin")],
            ]
        ),
    )


@router.callback_query(F.data.startswith("adm:delete_topic:"))
async def delete_topic(c: CallbackQuery):
    if not is_admin(c.from_user.id):
        return

    tid = int(c.data.split(":")[2])

    async with SessionLocal() as s:
        topic = await s.get(Topic, tid)

        if not topic:
            await c.answer("Mavzu topilmadi.", show_alert=True)
            return

        subject_id = topic.subject_id

        await s.delete(topic)
        await s.commit()

    await c.message.edit_text(
        "🗑 <b>MAVZU O'CHIRILDI!</b>",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="⬅️ Mavzular", callback_data=f"adm:subject:{subject_id}"
                    )
                ],
                [InlineKeyboardButton(text="⚙️ Admin panel", callback_data="admin")],
            ]
        ),
    )

    await c.answer("Mavzu o'chirildi.")


@router.callback_query(F.data.startswith("adm:questions:"))
async def catalog_questions(c: CallbackQuery):
    if not is_admin(c.from_user.id):
        return

    tid = int(c.data.split(":")[2])

    async with SessionLocal() as s:
        topic = await s.get(Topic, tid)

        if not topic:
            await c.answer("Mavzu topilmadi.", show_alert=True)
            return

        questions = (
            await s.scalars(
                select(Question).where(Question.is_active.is_(True), Question.topic_id == tid).order_by(Question.id)
            )
        ).all()

    if not questions:
        await c.message.edit_text(
            f"📖 <b>{topic.name}</b>\n\n" "📋 Bu mavzuda hozircha savollar yo'q.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="⬅️ Mavzu", callback_data=f"adm:topic:{tid}"
                        )
                    ]
                ]
            ),
        )
        await c.answer()
        return

    lines = [f"📖 <b>{topic.name}</b>", f"📋 Savollar: <b>{len(questions)}</b>", ""]

    buttons = []

    for i, q in enumerate(questions, 1):
        short_text = q.text.replace("\n", " ").strip()
        if len(short_text) > 55:
            short_text = short_text[:55] + "..."

        buttons.append(
            [
                InlineKeyboardButton(
                    text=f"{i}. {short_text}", callback_data=f"adm:question:{q.id}"
                )
            ]
        )

    buttons.append(
        [InlineKeyboardButton(text="⬅️ Mavzu", callback_data=f"adm:topic:{tid}")]
    )

    await c.message.edit_text(
        "\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
    )
    await c.answer()


@router.callback_query(F.data.startswith("adm:question:"))
async def catalog_question(c: CallbackQuery):
    if not is_admin(c.from_user.id):
        return

    qid = int(c.data.split(":")[2])

    async with SessionLocal() as s:
        q = await s.get(Question, qid)

        if not q:
            await c.answer("Savol topilmadi.", show_alert=True)
            return

        topic = await s.get(Topic, q.topic_id)

    text = (
        "📋 <b>SAVOL</b>\n"
        "──────────────\n\n"
        f"❓ <b>{q.text}</b>\n\n"
        f"A) {q.option_a}\n"
        f"B) {q.option_b}\n"
        f"C) {q.option_c}\n"
        f"D) {q.option_d}\n\n"
        f"✅ To'g'ri javob: <b>{q.correct_option}</b>"
    )

    if q.explanation:
        text += f"\n\n💡 <b>Izoh:</b> {q.explanation}"

    await c.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="✏️ Tahrirlash", callback_data=f"adm:edit_question:{q.id}"
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="🗑 O'chirish", callback_data=f"adm:delete_question:{q.id}"
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="⬅️ Savollar", callback_data=f"adm:questions:{q.topic_id}"
                    )
                ],
                [InlineKeyboardButton(text="⚙️ Admin panel", callback_data="admin")],
            ]
        ),
    )
    await c.answer()


@router.callback_query(F.data.startswith("adm:edit_question:"))
async def edit_question_start(c: CallbackQuery, state: FSMContext):
    if not is_admin(c.from_user.id):
        return

    qid = int(c.data.split(":")[2])

    async with SessionLocal() as s:
        q = await s.get(Question, qid)

    if not q:
        await c.answer("Savol topilmadi.", show_alert=True)
        return

    await state.update_data(
        question_id=q.id,
        old_image_path=q.image_path,
        old_question_mode=q.question_mode,
    )
    await state.set_state(AdminState.edit_question_text)

    if q.question_mode == "image" and q.image_path:
        await c.message.answer_photo(
            FSInputFile(q.image_path),
            caption="🖼️ <b>Hozirgi savol rasmi</b>\n\n"
                    "Yangi rasm yuboring yoki matn yuboring.",
            reply_markup=cancel_kb(),
        )
    else:
        await c.message.answer(
            "✏️ <b>SAVOLNI TAHRIRLASH</b>\n\n"
            f"Eski savol:\n<b>{q.text or '?'}</b>\n\n"
            "📷 Rasm yuborsangiz → rasmli savol bo'ladi.\n"
            "📝 Matn yuborsangiz → matnli savol bo'ladi.",
            reply_markup=cancel_kb(),
        )

    await c.answer()


@router.message(AdminState.edit_question_text)
async def edit_question_text_handler(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return

    # RASM yuborildi
    image_data = await photo_bytes(m)

    if image_data:
        await state.update_data(
            text="",
            image_data=image_data,
            question_mode="image",
        )
        await state.set_state(AdminState.edit_question_a)

        await m.answer(
            "🖼️ <b>Rasm qabul qilindi.</b>\n\n"
            "2️⃣ <b>A variant</b>ni yuboring:",
            reply_markup=cancel_kb(),
        )
        return

    # MATN yuborildi
    value = (m.text or "").strip()

    if not value:
        await m.answer(
            "❗ Rasm yoki matn yuboring."
        )
        return

    await state.update_data(
        text=value,
        image_data=None,
        question_mode="closed",
    )
    await state.set_state(AdminState.edit_question_a)

    await m.answer(
        "📝 <b>Matnli savol qabul qilindi.</b>\n\n"
        "2️⃣ <b>A variant</b>ni yuboring:",
        reply_markup=cancel_kb(),
    )


@router.message(AdminState.edit_question_a)
async def edit_question_a_handler(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return

    value = (m.text or "").strip()

    if not value:
        await m.answer("❗ A varianti bo'sh bo'lishi mumkin emas.")
        return

    await state.update_data(option_a=value)
    await state.set_state(AdminState.edit_question_b)

    await m.answer("3️⃣ <b>B variant</b>ni yuboring:", reply_markup=cancel_kb())


@router.message(AdminState.edit_question_b)
async def edit_question_b_handler(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return

    value = (m.text or "").strip()

    if not value:
        await m.answer("❗ B varianti bo'sh bo'lishi mumkin emas.")
        return

    await state.update_data(option_b=value)
    await state.set_state(AdminState.edit_question_c)

    await m.answer("4️⃣ <b>C variant</b>ni yuboring:", reply_markup=cancel_kb())


@router.message(AdminState.edit_question_c)
async def edit_question_c_handler(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return

    value = (m.text or "").strip()

    if not value:
        await m.answer("❗ C varianti bo'sh bo'lishi mumkin emas.")
        return

    await state.update_data(option_c=value)
    await state.set_state(AdminState.edit_question_d)

    await m.answer("5️⃣ <b>D variant</b>ni yuboring:", reply_markup=cancel_kb())


@router.message(AdminState.edit_question_d)
async def edit_question_d_handler(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return

    value = (m.text or "").strip()

    if not value:
        await m.answer("❗ D varianti bo'sh bo'lishi mumkin emas.")
        return

    await state.update_data(option_d=value)

    data = await state.get_data()
    qid = data.get("question_id")

    async with SessionLocal() as s:
        q = await s.get(Question, qid)

        if not q:
            await state.clear()
            await m.answer("❗ Savol topilmadi.")
            return

        # Variantlarni yangilaymiz
        q.option_a = data.get("option_a", q.option_a)
        q.option_b = data.get("option_b", q.option_b)
        q.option_c = data.get("option_c", q.option_c)
        q.option_d = value

        # To'g'ri javob va izoh eski holatda qoladi
        # Faqat savol turi: RASM yoki MATN
        image_data = data.get("image_data")

        if image_data:
            q.text = ""
            q.image_path = await save_question_image(image_data, q.id)
            q.question_mode = "image"
        else:
            q.text = data.get("text", "").strip()
            q.image_path = None
            q.question_mode = "closed"

        await s.commit()

    await state.clear()

    await m.answer(
        "✅ <b>Savol muvaffaqiyatli tahrirlandi!</b>",
        reply_markup=cancel_kb(),
    )


@router.callback_query(F.data.startswith("adm:delete_question:"))
async def delete_question(c: CallbackQuery):
    if not is_admin(c.from_user.id):
        return

    qid = int(c.data.split(":")[2])

    async with SessionLocal() as s:
        q = await s.get(Question, qid)

        if not q:
            await c.answer("Savol topilmadi.", show_alert=True)
            return

        topic_id = q.topic_id

        q.is_active = False
        await s.commit()

    await c.message.edit_text(
        "🗑 <b>SAVOL O'CHIRILDI!</b>",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="⬅️ Savollar", callback_data=f"adm:questions:{topic_id}"
                    )
                ],
                [InlineKeyboardButton(text="⚙️ Admin panel", callback_data="admin")],
            ]
        ),
    )

    await c.answer("Savol o'chirildi.")

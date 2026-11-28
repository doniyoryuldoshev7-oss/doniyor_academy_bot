"""Admin paneli: savol_qoshish bo'limi (admin.py dan ajratilgan)."""

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
from ._common import admin_menu, cancel_kb, is_admin, natural_topic_key, photo_bytes, save_question_image, subject_kb, topic_kb

router = Router()


# -------------------- QUESTION --------------------
@router.callback_query(F.data == "adm:add_question")
async def add_question_start(c: CallbackQuery, state: FSMContext):
    if not await can_add_questions(c.from_user.id):
        return
    async with SessionLocal() as s:
        subjects = (await s.scalars(select(Subject).order_by(Subject.name))).all()
    if not subjects:
        await c.answer("Avval fan qo'shing.", show_alert=True)
        return
    await state.set_state(AdminState.add_question_subject)
    await c.message.edit_text(
        "📋 <b>Yangi test savoli</b>\n\nFanni tanlang:",
        reply_markup=subject_kb(subjects),
    )
    await c.answer()


@router.callback_query(F.data.startswith("adm:qsub:"))
async def add_question_subject(c: CallbackQuery, state: FSMContext):
    if not await can_add_questions(c.from_user.id):
        return
    sid = int(c.data.split(":")[2])
    async with SessionLocal() as s:
        topics = (
            await s.scalars(
                select(Topic).where(Topic.subject_id == sid).order_by(Topic.name)
            )
        ).all()
        topics = sorted(topics, key=natural_topic_key)
        subject = await s.get(Subject, sid)
    if not topics:
        await c.answer(
            "Bu fanda hali mavzu yo'q. Avval mavzu qo'shing.", show_alert=True
        )
        return
    await state.update_data(subject_id=sid)
    await state.set_state(AdminState.add_question_topic)
    await c.message.edit_text(
        f"📚 <b>{subject.name}</b>\n\nMavzuni tanlang:", reply_markup=topic_kb(topics)
    )
    await c.answer()


@router.callback_query(F.data.startswith("adm:qtopic:"))
async def add_question_topic(c: CallbackQuery, state: FSMContext):
    if not await can_add_questions(c.from_user.id):
        return
    tid = int(c.data.split(":")[2])
    async with SessionLocal() as s:
        topic = await s.get(Topic, tid)
    await state.update_data(topic_id=tid)
    await state.set_state(AdminState.add_question_text)
    await c.message.edit_text(
        f"📖 <b>{topic.name}</b>\n\n1️⃣ Savol matnini yuboring:",
        reply_markup=cancel_kb(),
    )
    await c.answer()


@router.message(AdminState.add_question_text)
async def q_text(m: Message, state: FSMContext):
    if not await can_add_questions(m.from_user.id):
        return

    image_data = await photo_bytes(m)

    if image_data:
        session_id = uuid4().hex
        temp_dir = Path("app/question_images/_crop_sessions") / session_id
        temp_dir.mkdir(parents=True, exist_ok=True)

        image_path = temp_dir / "source.jpg"
        image_path.write_bytes(image_data)

        await state.update_data(
            text="",
            image_data=None,
            question_mode="image_multi",
            image_source_path=str(image_path),
            crop_dir=str(temp_dir),
        )

        await state.set_state(AdminState.add_question_image_count)

        await m.answer(
            "🖼️ <b>Rasm qabul qilindi.</b>\n\n"
            "Bu rasmda nechta savol bor?\n"
            "Masalan: <b>3</b>",
            reply_markup=cancel_kb(),
        )
        return

    value = (m.text or "").strip()

    if not value:
        await m.answer(
            "❗ Savol sifatida matn yoki surat yuboring."
        )
        return

    await state.update_data(
        text=value,
        image_data=None,
        question_mode="closed"
    )

    await state.set_state(AdminState.add_question_a)

    await m.answer(
        "2️⃣ <b>A variant</b>ni yuboring:",
        reply_markup=cancel_kb()
    )


@router.message(AdminState.add_question_image_count)
async def q_image_count(m: Message, state: FSMContext):
    if not await can_add_questions(m.from_user.id):
        return

    value = (m.text or "").strip()

    try:
        count = int(value)
    except ValueError:
        await m.answer(
            "❗ Faqat savollar sonini raqam bilan yuboring.\n"
            "Masalan: <b>3</b>"
        )
        return

    if count < 1 or count > 50:
        await m.answer(
            "❗ Savollar soni 1 dan 50 gacha bo'lishi kerak."
        )
        return

    data = await state.get_data()
    image_path = data.get("image_source_path")
    crop_dir = data.get("crop_dir")

    if not image_path or not crop_dir:
        await m.answer(
            "❗ Rasm sessiyasi topilmadi. Iltimos, qaytadan boshlang."
        )
        await state.clear()
        return

    session_id = create_session(
        admin_id=m.from_user.id,
        image_path=image_path,
        crop_dir=crop_dir,
        count=count,
    )

    app_url = web_app_url(session_id)

    if not app_url:
        await m.answer(
            "❗ Web App manzili sozlanmagan.\n\n"
            "Railway'da Public Domain yarating yoki "
            "<b>WEB_APP_URL</b> environment variable o'rnating."
        )
        finish_session(session_id)
        await state.clear()
        return

    await state.update_data(
        image_count=count,
        current_image_index=0,
        cropped_images=[],
        crop_session_id=session_id,
    )

    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(
                    text="✂️ Savollarni belgilash",
                    web_app=WebAppInfo(url=app_url),
                )
            ]
        ],
        resize_keyboard=True,
        one_time_keyboard=False,
    )

    await state.set_state(AdminState.add_question_crop)

    await m.answer(
        "✅ <b>Rasm tayyor.</b>\n\n"
        f"Jami: <b>{count}</b> ta savol.\n\n"
        "Quyidagi tugmani bosing va har bir savolni "
        "to'rtburchak qilib belgilang.",
        reply_markup=keyboard,
    )


@router.message(AdminState.add_question_crop, F.web_app_data)
async def q_image_crop_done(m: Message, state: FSMContext):
    if not await can_add_questions(m.from_user.id):
        return

    try:
        payload = json.loads(m.web_app_data.data)
    except Exception:
        await m.answer(
            "❗ Web App ma'lumoti noto'g'ri."
        )
        return

    if payload.get("action") != "done":
        return

    session_id = payload.get("session_id")

    data = await state.get_data()

    if session_id != data.get("crop_session_id"):
        await m.answer(
            "❗ Crop sessiyasi mos kelmadi."
        )
        return

    session = get_session(session_id)

    if not session:
        await m.answer(
            "❗ Crop sessiyasi topilmadi yoki muddati tugagan."
        )
        await state.clear()
        return

    if int(session["admin_id"]) != int(m.from_user.id):
        await m.answer(
            "❗ Bu crop sessiyasi sizga tegishli emas."
        )
        return

    crop_paths = []

    for i in range(1, int(session["count"]) + 1):
        crop_path = Path(session["crop_dir"]) / f"crop_{i}.jpg"

        if crop_path.exists():
            crop_paths.append(str(crop_path))

    if len(crop_paths) != int(session["count"]):
        await m.answer(
            "❗ Belgilangan savollar soni kutilgan songa teng emas.\n"
            f"Kutilgan: {session['count']}\n"
            f"Topilgan: {len(crop_paths)}"
        )
        return

    finish_session(session_id)

    await state.update_data(
        cropped_images=crop_paths,
        current_image_index=0,
    )

    await state.set_state(AdminState.add_question_correct)

    await m.answer(
        f"✅ <b>{len(crop_paths)} ta savol rasmi tayyor.</b>\n\n"
        "1-savol uchun to'g'ri javob harfini yuboring: "
        "<b>A</b>, <b>B</b>, <b>C</b> yoki <b>D</b>",
        reply_markup=ReplyKeyboardRemove(),
    )



async def save_option(
    m: Message,
    state: FSMContext,
    key: str,
    next_state,
    label: str
):
    if not await can_add_questions(m.from_user.id):
        return

    value = (m.text or "").strip()

    if not value:
        await m.answer("❗ Variant bo'sh bo'lishi mumkin emas.")
        return

    await state.update_data(**{key: value})
    await state.set_state(next_state)
    await m.answer(label, reply_markup=cancel_kb())


@router.message(AdminState.add_question_a)
async def q_a(m, state):
    if not await can_add_questions(m.from_user.id):
        return

    await save_option(
        m,
        state,
        "option_a",
        AdminState.add_question_b,
        "3️⃣ <b>B variant</b>ni yuboring:",
    )


@router.message(AdminState.add_question_b)
async def q_b(m, state):
    if not await can_add_questions(m.from_user.id):
        return

    await save_option(
        m,
        state,
        "option_b",
        AdminState.add_question_c,
        "4️⃣ <b>C variant</b>ni yuboring:",
    )


@router.message(AdminState.add_question_c)
async def q_c(m, state):
    if not await can_add_questions(m.from_user.id):
        return

    await save_option(
        m,
        state,
        "option_c",
        AdminState.add_question_d,
        "5️⃣ <b>D variant</b>ni yuboring:",
    )


@router.message(AdminState.add_question_d)
async def q_d(m, state):
    if not await can_add_questions(m.from_user.id):
        return

    await save_option(
        m,
        state,
        "option_d",
        AdminState.add_question_correct,
        "6️⃣ To'g'ri javob harfini yuboring: "
        "<b>A</b>, <b>B</b>, <b>C</b> yoki <b>D</b>",
    )


@router.message(AdminState.add_question_correct)
async def q_correct(m: Message, state: FSMContext):
    if not await can_add_questions(m.from_user.id):
        return

    correct = (m.text or "").strip().upper()

    if correct not in {"A", "B", "C", "D"}:
        await m.answer(
            "❗ Faqat A, B, C yoki D yuboring."
        )
        return

    data = await state.get_data()

    if data.get("question_mode") == "image_multi":
        current_index = data.get("current_image_index", 0)
        cropped_images = data.get("cropped_images", [])

        if current_index >= len(cropped_images):
            await m.answer(
                "❗ Savol rasmi topilmadi. Jarayonni qaytadan boshlang."
            )
            await state.clear()
            return

        answers = data.get("image_correct_answers", [])
        answers.append(correct)

        await state.update_data(
            image_correct_answers=answers,
            current_correct_option=correct,
        )

        await state.set_state(AdminState.add_question_explanation)

        await m.answer(
            f"📝 <b>{current_index + 1}-savol</b> uchun izohni yuboring.\n"
            "Manbani ham qo'shish mumkin — yangi qatorda: <code>Manba: 8-sinf O'zbekiston tarixi, 52-bet</code>\n"
            "Izoh kerak bo'lmasa <code>-</code> yuboring:",
            reply_markup=cancel_kb(),
        )
        return

    await state.update_data(correct_option=correct)
    await state.set_state(AdminState.add_question_explanation)

    await m.answer(
        "7️⃣ <b>Izoh</b>ni yuboring (nega bu javob to'g'ri). "
        "Manbani ham qo'shish mumkin — yangi qatorda: <code>Manba: 8-sinf O'zbekiston tarixi, 52-bet</code>\n"
        "Agar izoh kerak bo'lmasa, <code>-</code> yuboring:",
        reply_markup=cancel_kb(),
    )


@router.message(AdminState.add_question_explanation)
async def q_explanation(m: Message, state: FSMContext):
    if not await can_add_questions(m.from_user.id):
        return

    explanation = (m.text or "").strip()
    explanation = None if explanation == "-" else explanation
    if explanation:
        # «Manba: …» qatori yozilgan bo'lsa — fayldan yuklashdagi kabi izohdan ajratib saqlanadi
        from ...services.savol_import import SRC_RE, with_source

        exp_lines, src_lines = [], []
        for line in explanation.splitlines():
            sm = SRC_RE.match(line.strip())
            (src_lines.append(sm.group(1).strip()) if sm else exp_lines.append(line))
        if src_lines:
            explanation = with_source("\n".join(exp_lines).strip(), " ".join(src_lines))

    data = await state.get_data()

    if data.get("question_mode") == "image_multi":
        current_index = data.get("current_image_index", 0)
        cropped_images = data.get("cropped_images", [])
        correct_answers = data.get("image_correct_answers", [])
        topic_id = data["topic_id"]

        if current_index >= len(cropped_images):
            await m.answer(
                "❗ Savol rasmi topilmadi. Jarayonni qaytadan boshlang."
            )
            await state.clear()
            return

        correct = correct_answers[current_index]
        crop_path = Path(cropped_images[current_index])

        if not crop_path.exists():
            await m.answer(
                "❗ Savol rasmi fayli topilmadi. Jarayon bekor qilindi."
            )
            await state.clear()
            return

        async with SessionLocal() as s:
            topic = await s.get(Topic, topic_id)

            if not topic:
                await m.answer(
                    "❗ Mavzu topilmadi."
                )
                await state.clear()
                return

            q = Question(
                topic_id=topic.id,
                text="",
                option_a="",
                option_b="",
                option_c="",
                option_d="",
                correct_option=correct,
                explanation=explanation,
                question_mode="image",
            )

            s.add(q)
            await s.commit()
            qid = q.id

            q.image_path = await save_question_image(
                crop_path.read_bytes(),
                qid,
            )

            await s.commit()

        next_index = current_index + 1

        if next_index < len(cropped_images):
            await state.update_data(
                current_image_index=next_index,
            )

            await state.set_state(
                AdminState.add_question_correct
            )

            await m.answer(
                f"✅ <b>{current_index + 1}-savol saqlandi.</b>\n\n"
                f"🎯 <b>{next_index + 1}-savol</b> uchun "
                "to'g'ri javobni yuboring: "
                "<b>A</b>, <b>B</b>, <b>C</b> yoki <b>D</b>",
                reply_markup=cancel_kb(),
            )
            return

        total = len(cropped_images)
        topic_name = topic.name

        await state.clear()

        await m.answer(
            f"✅ <b>{total} ta savol muvaffaqiyatli qo'shildi!</b>\n\n"
            f"📖 Mavzu: <b>{topic_name}</b>",
            reply_markup=admin_menu(limited=not is_admin(m.from_user.id)),
        )
        return

    async with SessionLocal() as s:
        topic = await s.get(Topic, data["topic_id"])

        q = Question(
            topic_id=topic.id,
            text=data["text"],
            option_a=data["option_a"],
            option_b=data["option_b"],
            option_c=data["option_c"],
            option_d=data["option_d"],
            correct_option=data["correct_option"],
            explanation=explanation,
        )

        s.add(q)
        await s.commit()
        qid = q.id

        if data.get("image_data"):
            q.image_path = await save_question_image(
                data["image_data"],
                qid,
            )
            q.question_mode = "image"
            await s.commit()

    await state.clear()

    await m.answer(
        f"✅ <b>Savol muvaffaqiyatli qo'shildi!</b>\n\n"
        f"🆔 ID: <code>{qid}</code>\n"
        f"📖 Mavzu: <b>{topic.name}</b>\n"
        f"✅ To'g'ri javob: <b>{data['correct_option']}</b>",
        reply_markup=admin_menu(limited=not is_admin(m.from_user.id)),
    )

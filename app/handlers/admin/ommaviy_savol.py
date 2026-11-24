"""Admin paneli: ommaviy_savol bo'limi (admin.py dan ajratilgan)."""

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
from ._common import cancel_kb, subject_kb

router = Router()


def bulk_topic_kb(topics):
    rows = [
        [
            InlineKeyboardButton(
                text=f"📖 {t.name}",
                callback_data=f"adm:bulk_qtopic:{t.id}",
            )
        ]
        for t in topics
    ]

    rows.append(
        [
            InlineKeyboardButton(
                text="⬅️ Fan tanlash",
                callback_data="adm:add_questions_bulk",
            )
        ]
    )

    rows.append(
        [
            InlineKeyboardButton(
                text="❌ Bekor qilish",
                callback_data="adm:cancel",
            )
        ]
    )

    return InlineKeyboardMarkup(inline_keyboard=rows)


# -------------------- BULK QUESTIONS --------------------


_BULK_QUESTION_RE = re.compile(
    r"^\s*(\d{1,4})\s*(?:\.+|\)|:|;|-|\u2011|\u2013|\u2014|\u2212)\s*(.+?)\s*$"
)

_BULK_OPTION_RE = re.compile(
    r"^\s*([A-Da-d])"
    r"(?:\s*(?:\)|\]|\.+|:|;|-|\u2011|\u2013|\u2014|\u2212)\s*|\s+)"
    r"(.+?)\s*$"
)

_BULK_BRACKET_OPTION_RE = re.compile(
    r"^\s*[\(\[]\s*([A-Da-d])\s*[\)\]]\s*(.+?)\s*$"
)


def _bulk_extract_correct_marker(value):
    value = (value or "").rstrip()

    # ++, +++, ++++ ...
    # Plus marker answerga yopishib yozilsa ham qabul qilinadi.
    match = re.search(r"\+{2,}\s*$", value)

    if match:
        clean = value[:match.start()].rstrip()
        return clean, True

    # .., ..., ....
    # Oddiy gap... tasodifan correct bo'lmasligi uchun
    # nuqtalardan oldin kamida bitta bo'sh joy talab qilinadi.
    match = re.search(r"\s+\.{2,}\s*$", value)

    if match:
        clean = value[:match.start()].rstrip()
        return clean, True

    return value, False


def _bulk_normalize_text(value):
    return "\n".join(" ".join(line.split()) for line in (value or "").splitlines()).strip()


def _bulk_parse_questions(raw_text):
    from app.bulk_format_normalizer import normalize_bulk_questions

    canonical, format_errors = normalize_bulk_questions(raw_text)
    if format_errors:
        return [], format_errors

    raw_text = canonical
    raw_text = (
        (raw_text or "")
        .replace("\r\n", "\n")
        .replace("\r", "\n")
    )

    questions = []
    errors = []

    current = None
    last_option = None

    for line_no, raw_line in enumerate(
        raw_text.split("\n"),
        start=1,
    ):
        line = raw_line.strip()

        if not line:
            continue

        # -------------------------
        # YANGI SAVOL
        # -------------------------

        question_match = _BULK_QUESTION_RE.match(line)

        if question_match:
            if current is not None:
                questions.append(current)

            current = {
                "number": int(question_match.group(1)),
                "text": question_match.group(2).strip(),
                "options": {},
                "option_lines": {},
            }

            last_option = None
            continue

        # -------------------------
        # A / B / C / D
        # -------------------------

        option_match = (
            _BULK_BRACKET_OPTION_RE.match(line)
            or _BULK_OPTION_RE.match(line)
        )

        if option_match and current is not None:
            letter = option_match.group(1).upper()
            value = option_match.group(2).strip()

            if letter in current["options"]:
                errors.append(
                    f"{current['number']}-savol: "
                    f"{letter} varianti ikki marta yozilgan "
                    f"({line_no}-qator)."
                )
                continue

            current["options"][letter] = value
            current["option_lines"][letter] = line_no
            last_option = letter
            continue

        # -------------------------
        # DAVOM ETUVCHI QATOR
        # -------------------------

        if current is None:
            errors.append(
                f"{line_no}-qator: savol raqami topilmadi. "
                "Masalan: 1. Savol matni"
            )
            continue

        if last_option is None:
            current["text"] += " " + line
        else:
            current["options"][last_option] += " " + line

    if current is not None:
        questions.append(current)

    if not questions:
        errors.append("Hech qanday savol aniqlanmadi.")
        return [], errors

    # =====================================================
    # VALIDATION
    # =====================================================

    seen_numbers = set()

    for item in questions:
        number = item["number"]

        if number in seen_numbers:
            errors.append(
                f"{number}-savol: savol raqami takrorlangan."
            )

        seen_numbers.add(number)

        item["text"] = _bulk_normalize_text(
            item["text"]
        ).replace("\ue000", "\n")

        if not item["text"]:
            errors.append(
                f"{number}-savol: savol matni bo'sh."
            )

        missing = [
            letter
            for letter in "ABCD"
            if letter not in item["options"]
        ]

        if missing:
            errors.append(
                f"{number}-savol: variant yetishmaydi: "
                + ", ".join(missing)
            )

        correct_letters = []

        for letter in "ABCD":
            if letter not in item["options"]:
                continue

            clean_value, is_correct = (
                _bulk_extract_correct_marker(
                    item["options"][letter]
                )
            )

            clean_value = _bulk_normalize_text(
                clean_value
            )

            item["options"][letter] = clean_value

            if not clean_value:
                errors.append(
                    f"{number}-savol: "
                    f"{letter} varianti bo'sh."
                )

            if is_correct:
                correct_letters.append(letter)

        if len(correct_letters) == 0:
            errors.append(
                f"{number}-savol: to'g'ri javob topilmadi. "
                "To'g'ri variant oxiriga ++ yoki ... qo'ying."
            )

        elif len(correct_letters) > 1:
            errors.append(
                f"{number}-savol: bir nechta to'g'ri javob "
                "belgilangan: "
                + ", ".join(correct_letters)
            )

        else:
            item["correct"] = correct_letters[0]

    if errors:
        return questions, errors

    return questions, []


def _bulk_preview_text(items):
    parts = [
        "\u2705 <b>"
        + str(len(items))
        + " ta savol aniqlandi.</b>",
        "",
        "To'g'ri javoblar:",
    ]

    preview_items = items[:10]

    for item in preview_items:
        question = html.escape(
            _bulk_normalize_text(item["text"])
        )

        if len(question) > 90:
            question = question[:87] + "..."

        correct = item["correct"]

        answer = html.escape(
            item["options"][correct]
        )

        if len(answer) > 60:
            answer = answer[:57] + "..."

        parts.append(
            "\n<b>"
            + str(item["number"])
            + ".</b> "
            + question
            + "\n   \u2705 "
            + correct
            + ") "
            + answer
        )

    if len(items) > 10:
        parts.append(
            "\n... va yana "
            + str(len(items) - 10)
            + " ta savol."
        )

    parts.append(
        "\n\u26A0\uFE0F Hali bazaga yozilmadi."
    )

    return "\n".join(parts)


@router.callback_query(F.data == "adm:add_questions_bulk")
async def bulk_question_start(c: CallbackQuery, state: FSMContext):
    if not await can_add_questions(c.from_user.id):
        return

    async with SessionLocal() as db:
        subjects = (
            await db.scalars(
                select(Subject).order_by(Subject.name)
            )
        ).all()

    if not subjects:
        await c.answer(
            "Avval fan qo'shing.",
            show_alert=True,
        )
        return

    await state.set_state(
        AdminState.bulk_question_subject
    )

    await c.message.edit_text(
        "⚡ <b>Bir nechta savol qo'shish</b>\n\n"
        "Fanni tanlang:",
        reply_markup=subject_kb(
            subjects,
            prefix="adm:bulk_qsub",
        ),
    )

    await c.answer()


@router.callback_query(
    F.data.startswith("adm:bulk_qsub:")
)
async def bulk_question_subject(
    c: CallbackQuery,
    state: FSMContext,
):
    if not await can_add_questions(c.from_user.id):
        return

    sid = int(c.data.split(":")[2])

    async with SessionLocal() as db:
        subject = await db.get(Subject, sid)

        topics = (
            await db.scalars(
                select(Topic)
                .where(Topic.subject_id == sid)
                .order_by(Topic.name)
            )
        ).all()

    if subject is None:
        await c.answer(
            "Fan topilmadi.",
            show_alert=True,
        )
        return

    if not topics:
        await c.answer(
            "Bu fanda hali mavzu yo'q. "
            "Avval mavzu qo'shing.",
            show_alert=True,
        )
        return

    await state.update_data(
        subject_id=sid
    )

    await state.set_state(
        AdminState.bulk_question_topic
    )

    await c.message.edit_text(
        f"📚 <b>{subject.name}</b>\n\n"
        "Mavzuni tanlang:",
        reply_markup=bulk_topic_kb(topics),
    )

    await c.answer()


@router.callback_query(
    F.data.startswith("adm:bulk_qtopic:")
)
async def bulk_question_topic(
    c: CallbackQuery,
    state: FSMContext,
):
    if not await can_add_questions(c.from_user.id):
        return

    tid = int(c.data.split(":")[2])

    async with SessionLocal() as db:
        topic = await db.get(Topic, tid)

    if topic is None:
        await c.answer(
            "Mavzu topilmadi.",
            show_alert=True,
        )
        return

    await state.update_data(
        topic_id=tid
    )

    await state.set_state(
        AdminState.bulk_question_text
    )

    await c.message.edit_text(
        f"⚡ <b>{topic.name}</b>\n\n"
        "<b>Savollarni bitta xabarda yuboring.</b>\n\n"
        "Masalan:\n"
        "<code>"
        "1. O'zbekiston poytaxti qaysi?\n"
        "a) Samarqand\n"
        "b) Toshkent ++\n"
        "c) Buxoro\n"
        "d) Xiva\n\n"
        "2) Amir Temur qachon tug'ilgan?\n"
        "A. 1336 ...\n"
        "B. 1340\n"
        "C. 1326\n"
        "D. 1405"
        "</code>\n\n"
        "To'g'ri javob oxiriga "
        "<b>++</b>, <b>+++</b>, "
        "<b>..</b> yoki <b>...</b> qo'ying.",
        reply_markup=cancel_kb(),
    )

    await c.answer()


@router.message(AdminState.bulk_question_text)
async def bulk_question_text(
    m: Message,
    state: FSMContext,
):
    if not await can_add_questions(m.from_user.id):
        return

    raw_text = (m.text or "").strip()

    if not raw_text:
        await m.answer(
            "\u274C Savollarni matn ko'rinishida yuboring.",
            reply_markup=cancel_kb(),
        )
        return

    items, errors = _bulk_parse_questions(raw_text)

    if errors:
        shown = errors[:15]

        error_text = (
            "\u274C <b>Savollar saqlanmadi.</b>\n\n"
            "<b>Topilgan xatolar:</b>\n"
        )

        for i, error in enumerate(shown, 1):
            error_text += (
                "\n"
                + str(i)
                + ". "
                + html.escape(error)
            )

        if len(errors) > 15:
            error_text += (
                "\n\n... yana "
                + str(len(errors) - 15)
                + " ta xato."
            )

        error_text += (
            "\n\nMatnni tuzatib, qayta yuboring."
        )

        await m.answer(
            error_text,
            reply_markup=cancel_kb(),
        )
        return

    await state.update_data(
        bulk_raw_text=raw_text,
        bulk_count=len(items),
    )

    await state.set_state(
        AdminState.bulk_question_confirm
    )

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=(
                        "\u2705 "
                        + str(len(items))
                        + " ta savolni saqlash"
                    ),
                    callback_data="adm:bulk_save",
                )
            ],
            [
                InlineKeyboardButton(
                    text="\u270F\uFE0F Matnni qayta yuborish",
                    callback_data="adm:bulk_retry",
                )
            ],
            [
                InlineKeyboardButton(
                    text="\u274C Bekor qilish",
                    callback_data="adm:cancel",
                )
            ],
        ]
    )

    await m.answer(
        _bulk_preview_text(items),
        reply_markup=keyboard,
    )


@router.callback_query(
    F.data == "adm:bulk_retry"
)
async def bulk_question_retry(
    c: CallbackQuery,
    state: FSMContext,
):
    if not await can_add_questions(c.from_user.id):
        return

    await state.set_state(
        AdminState.bulk_question_text
    )

    await state.update_data(
        bulk_raw_text=None,
        bulk_count=None,
    )

    await c.message.edit_text(
        "\u270F\uFE0F <b>Savollarni qayta yuboring.</b>\n\n"
        "Har bir savol raqam bilan boshlansin.\n"
        "Variantlar A/B/C/D ko'rinishida bo'lsin.\n"
        "To'g'ri javob oxiriga ++ yoki ... qo'ying.",
        reply_markup=cancel_kb(),
    )

    await c.answer()


@router.callback_query(
    F.data == "adm:bulk_save"
)
async def bulk_question_save(
    c: CallbackQuery,
    state: FSMContext,
):
    if not await can_add_questions(c.from_user.id):
        return

    data = await state.get_data()

    topic_id = data.get("topic_id")
    raw_text = data.get("bulk_raw_text")

    if not topic_id or not raw_text:
        await c.answer(
            "Bulk savol ma'lumotlari topilmadi. "
            "Jarayonni qaytadan boshlang.",
            show_alert=True,
        )
        return

    items, errors = _bulk_parse_questions(
        raw_text
    )

    if errors:
        await state.set_state(
            AdminState.bulk_question_text
        )

        await c.answer(
            "Matnda xato aniqlandi. "
            "Savollarni qayta yuboring.",
            show_alert=True,
        )
        return

    async with SessionLocal() as db:
        topic = await db.get(
            Topic,
            int(topic_id),
        )

        if topic is None:
            await c.answer(
                "Mavzu topilmadi.",
                show_alert=True,
            )
            return

        questions = []

        for item in items:
            options = item["options"]
            correct = item["correct"]

            questions.append(
                Question(
                    topic_id=topic.id,
                    text=item["text"],
                    image_path=None,
                    question_mode="closed",
                    option_a=options["A"],
                    option_b=options["B"],
                    option_c=options["C"],
                    option_d=options["D"],
                    correct_option=correct,
                    correct_answer=options[correct],
                    explanation=None,
                )
            )

        db.add_all(questions)

        try:
            await db.commit()
        except Exception:
            await db.rollback()

            await c.answer(
                "Savollarni bazaga saqlashda xato yuz berdi.",
                show_alert=True,
            )
            return

        topic_name = topic.name

        subject_obj = await db.get(Subject, topic.subject_id)
        subject_name = subject_obj.name if subject_obj else "—"

    saved_count = len(items)

    # --- Word avto-eksport (har bir yuklangan test uchun) ---
    try:
        from ...services.word_export import (
            build_questions_docx,
            export_filename,
            items_from_bulk,
        )
        from ...services.test_archive import deliver_test_export

        export_items = items_from_bulk(items)

        docx_bytes = await asyncio.to_thread(
            build_questions_docx,
            subject_name=subject_name,
            topic_name=topic_name,
            items=export_items,
        )

        await deliver_test_export(
            bot=c.bot,
            admin_chat_id=c.from_user.id,
            data=docx_bytes,
            filename=export_filename(subject_name, topic_name),
            subject_name=subject_name,
            topic_name=topic_name,
            count=saved_count,
            author=c.from_user.full_name,
            note="Yangi yuklangan test — avtomatik eksport.",
        )
    except Exception as exc:
        logging.getLogger(__name__).exception(
            "Word avto-eksport xatosi: %s", exc
        )
    # --- /Word avto-eksport ---

    await state.clear()

    done_keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="\u26A1 Shu mavzuga yana savol qo'shish",
                    callback_data=f"adm:bulk_qtopic:{int(topic_id)}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="\u2699\uFE0F Admin panel",
                    callback_data="admin",
                )
            ],
        ]
    )

    await c.message.edit_text(
        "\u2705 <b>"
        + str(saved_count)
        + " ta savol muvaffaqiyatli qo'shildi!</b>\n\n"
        "\U0001F4D6 Mavzu: <b>"
        + html.escape(topic_name)
        + "</b>",
        reply_markup=done_keyboard,
    )

    await c.answer()

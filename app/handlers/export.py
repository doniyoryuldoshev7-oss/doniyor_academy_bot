"""Word eksport paneli — Doniyor Academy bot.

Joylashuvi: app/handlers/export.py

Admin panelda "\U0001F4C4 Word eksport" tugmasi:
  fan -> mavzu (yoki butun fan) -> javobli / javobsiz -> .docx fayl.
"""

from __future__ import annotations

import asyncio
import html
import logging
import re

from aiogram import F, Router
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from sqlalchemy import select

from ..admin_roles import can_add_questions
from ..db import SessionLocal
from ..models import Question, Subject, Topic
from ..services.test_archive import deliver_test_export
from ..services.word_export import (
    build_multi_topic_docx,
    build_questions_docx,
    export_filename,
    items_from_questions,
)

log = logging.getLogger(__name__)

router = Router()


def _natural_key(name: str):
    parts = re.split(r"(\d+)", (name or "").casefold())
    return tuple(
        (0, int(part)) if part.isdigit() else (1, part)
        for part in parts
    )


def _back_row():
    return [
        InlineKeyboardButton(
            text="⬅️ Orqaga",
            callback_data="adm:export",
        ),
        InlineKeyboardButton(
            text="⚙️ Admin panel",
            callback_data="admin",
        ),
    ]


async def _active_questions(db, topic_id: int):
    rows = await db.scalars(
        select(Question)
        .where(
            Question.topic_id == topic_id,
            Question.is_active.is_(True),
        )
        .order_by(Question.id)
    )
    return list(rows)


# ---------------------------------------------------------------
#  1-qadam: fanlar
# ---------------------------------------------------------------

@router.callback_query(F.data == "adm:export")
async def export_start(c: CallbackQuery):
    if not await can_add_questions(c.from_user.id):
        await c.answer("Ruxsat yo'q.", show_alert=True)
        return

    async with SessionLocal() as db:
        subjects = list(
            await db.scalars(
                select(Subject).where(Subject.is_active.is_(True))
            )
        )

    if not subjects:
        await c.answer("Fanlar topilmadi.", show_alert=True)
        return

    subjects.sort(key=lambda s: _natural_key(s.name))

    rows = [
        [
            InlineKeyboardButton(
                text="\U0001F4DA " + s.name,
                callback_data=f"adm:exp_sub:{s.id}",
            )
        ]
        for s in subjects
    ]

    rows.append(
        [
            InlineKeyboardButton(
                text="⚙️ Admin panel",
                callback_data="admin",
            )
        ]
    )

    await c.message.edit_text(
        "\U0001F4C4 <b>Word eksport</b>\n\nFanni tanlang:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await c.answer()


# ---------------------------------------------------------------
#  2-qadam: mavzular
# ---------------------------------------------------------------

@router.callback_query(F.data.startswith("adm:exp_sub:"))
async def export_subject(c: CallbackQuery):
    if not await can_add_questions(c.from_user.id):
        await c.answer("Ruxsat yo'q.", show_alert=True)
        return

    subject_id = int(c.data.split(":")[-1])

    async with SessionLocal() as db:
        subject = await db.get(Subject, subject_id)

        if subject is None:
            await c.answer("Fan topilmadi.", show_alert=True)
            return

        topics = list(
            await db.scalars(
                select(Topic).where(Topic.subject_id == subject_id)
            )
        )

        counts = {}

        for topic in topics:
            counts[topic.id] = len(await _active_questions(db, topic.id))

        subject_name = subject.name

    if not topics:
        await c.answer("Bu fanda mavzu yo'q.", show_alert=True)
        return

    topics.sort(key=lambda t: _natural_key(t.name))

    rows = []

    for topic in topics:
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"\U0001F4D6 {topic.name} ({counts.get(topic.id, 0)})",
                    callback_data=f"adm:exp_t:{topic.id}",
                )
            ]
        )

    total = sum(counts.values())

    rows.append(
        [
            InlineKeyboardButton(
                text=f"\U0001F4E6 Butun fan ({total} savol)",
                callback_data=f"adm:exp_s:{subject_id}",
            )
        ]
    )

    rows.append(_back_row())

    await c.message.edit_text(
        "\U0001F4C4 <b>Word eksport</b>\n\n"
        "\U0001F4DA Fan: <b>" + html.escape(subject_name) + "</b>\n\n"
        "Mavzuni tanlang:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await c.answer()


# ---------------------------------------------------------------
#  3-qadam: javobli / javobsiz
# ---------------------------------------------------------------

def _mode_keyboard(kind: str, target_id: int, subject_id: int | None):
    rows = [
        [
            InlineKeyboardButton(
                text="✅ Javoblar belgilangan",
                callback_data=f"adm:exp_go:{kind}:{target_id}:1",
            )
        ],
        [
            InlineKeyboardButton(
                text="\U0001F4DD Javoblarsiz (o'quvchi uchun)",
                callback_data=f"adm:exp_go:{kind}:{target_id}:0",
            )
        ],
    ]

    if subject_id:
        rows.append(
            [
                InlineKeyboardButton(
                    text="⬅️ Mavzular",
                    callback_data=f"adm:exp_sub:{subject_id}",
                )
            ]
        )

    rows.append(_back_row())

    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data.startswith("adm:exp_t:"))
async def export_topic_choose(c: CallbackQuery):
    if not await can_add_questions(c.from_user.id):
        await c.answer("Ruxsat yo'q.", show_alert=True)
        return

    topic_id = int(c.data.split(":")[-1])

    async with SessionLocal() as db:
        topic = await db.get(Topic, topic_id)

        if topic is None:
            await c.answer("Mavzu topilmadi.", show_alert=True)
            return

        count = len(await _active_questions(db, topic_id))
        topic_name = topic.name
        subject_id = topic.subject_id

    if not count:
        await c.answer("Bu mavzuda savol yo'q.", show_alert=True)
        return

    await c.message.edit_text(
        "\U0001F4C4 <b>Word eksport</b>\n\n"
        "\U0001F4D6 Mavzu: <b>" + html.escape(topic_name) + "</b>\n"
        "\U0001F522 Savollar: <b>" + str(count) + "</b>\n\n"
        "Qanday fayl kerak?",
        reply_markup=_mode_keyboard("t", topic_id, subject_id),
    )
    await c.answer()


@router.callback_query(F.data.startswith("adm:exp_s:"))
async def export_subject_choose(c: CallbackQuery):
    if not await can_add_questions(c.from_user.id):
        await c.answer("Ruxsat yo'q.", show_alert=True)
        return

    subject_id = int(c.data.split(":")[-1])

    async with SessionLocal() as db:
        subject = await db.get(Subject, subject_id)

        if subject is None:
            await c.answer("Fan topilmadi.", show_alert=True)
            return

        subject_name = subject.name

    await c.message.edit_text(
        "\U0001F4C4 <b>Word eksport — butun fan</b>\n\n"
        "\U0001F4DA Fan: <b>" + html.escape(subject_name) + "</b>\n\n"
        "Qanday fayl kerak?",
        reply_markup=_mode_keyboard("s", subject_id, subject_id),
    )
    await c.answer()


# ---------------------------------------------------------------
#  4-qadam: fayl yasash va yuborish
# ---------------------------------------------------------------

@router.callback_query(F.data.startswith("adm:exp_go:"))
async def export_run(c: CallbackQuery):
    if not await can_add_questions(c.from_user.id):
        await c.answer("Ruxsat yo'q.", show_alert=True)
        return

    try:
        _, _, kind, raw_id, raw_flag = c.data.split(":")
        target_id = int(raw_id)
        with_answers = raw_flag == "1"
    except ValueError:
        await c.answer("Eksport parametri xato.", show_alert=True)
        return

    await c.answer("Fayl tayyorlanmoqda...")

    try:
        async with SessionLocal() as db:
            if kind == "t":
                topic = await db.get(Topic, target_id)

                if topic is None:
                    await c.answer("Mavzu topilmadi.", show_alert=True)
                    return

                subject = await db.get(Subject, topic.subject_id)
                subject_name = subject.name if subject else "—"
                topic_name = topic.name

                questions = await _active_questions(db, topic.id)

                if not questions:
                    await c.answer("Savol yo'q.", show_alert=True)
                    return

                items = items_from_questions(questions)
                count = len(items)

                data = await asyncio.to_thread(
                    build_questions_docx,
                    subject_name=subject_name,
                    topic_name=topic_name,
                    items=items,
                    with_answers=with_answers,
                )

            else:
                subject = await db.get(Subject, target_id)

                if subject is None:
                    await c.answer("Fan topilmadi.", show_alert=True)
                    return

                subject_name = subject.name
                topic_name = "Butun fan"

                topics = list(
                    await db.scalars(
                        select(Topic).where(
                            Topic.subject_id == subject.id
                        )
                    )
                )
                topics.sort(key=lambda t: _natural_key(t.name))

                sections = []

                for topic in topics:
                    questions = await _active_questions(db, topic.id)

                    if questions:
                        sections.append(
                            (topic.name, items_from_questions(questions))
                        )

                if not sections:
                    await c.answer("Savol yo'q.", show_alert=True)
                    return

                count = sum(len(items) for _, items in sections)

                data = await asyncio.to_thread(
                    build_multi_topic_docx,
                    subject_name=subject_name,
                    sections=sections,
                    with_answers=with_answers,
                )

        filename = export_filename(
            subject_name,
            topic_name,
            with_answers=with_answers,
        )

        await deliver_test_export(
            bot=c.bot,
            admin_chat_id=c.from_user.id,
            data=data,
            filename=filename,
            subject_name=subject_name,
            topic_name=topic_name,
            count=count,
            author=c.from_user.full_name,
            note="Qo'lda eksport qilindi.",
            to_archive=False,
        )

    except Exception as exc:
        log.exception("Word eksport xatosi: %s", exc)

        await c.message.answer(
            "⚠️ Eksport vaqtida xato yuz berdi. "
            "Loglarni tekshiring."
        )

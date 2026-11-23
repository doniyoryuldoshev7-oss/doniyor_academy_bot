"""Topshiriq muddati eslatmalari.

Joylashuvi: app/services/assignment_reminder.py

Kuniga bir marta (takrorlash eslatmalari bilan bir vaqtda) ishlaydi:

    1) Muddati bugun tugaydigan topshiriqni hali bajarmagan
       o'quvchilarga eslatma yuboradi.
    2) Muddati endigina tugagan topshiriq bo'yicha ustozga
       qisqa hisobot yuboradi.

Sikl kuniga bir marta ishlagani uchun oyna 24 soat qilib olingan —
shunda har bir topshiriq aynan bir marta eslatiladi.
"""

from __future__ import annotations

import asyncio
import html
import logging
import os
from datetime import datetime

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from ..db import SessionLocal
from .assignments import (
    assignment_progress,
    assignments_due_soon,
    assignments_just_ended,
    fmt,
    pending_students,
    progress_percent,
    student_progress,
)

log = logging.getLogger(__name__)

SEND_DELAY = 0.06


def enabled() -> bool:
    return os.getenv("ASSIGNMENT_REMINDER_ENABLED", "1") == "1"


def student_text(topic_name: str, assignment, answered: int = 0) -> str:
    required = assignment.required_questions
    percent = progress_percent(answered, required)

    if answered:
        status = (
            f"\U0001F4CA Eng yaxshi urinishingiz: "
            f"<b>{answered}/{required}</b> savol ({percent}%)\n\n"
            f"Topshiriq bitta testda to'liq bajarilishi kerak: "
            f"<b>{required}</b> ta savolga javob berib, testni "
            "yakunlang."
        )
    else:
        status = (
            "\U0001F4CA Hozir bajarilgan: <b>0%</b>\n\n"
            f"Topshiriq bitta testda to'liq bajarilishi kerak: "
            f"<b>{required}</b> ta savolga javob berib, testni "
            "yakunlang. Bir necha daqiqa yetarli."
        )

    return (
        "⏰ <b>Topshiriq muddati bugun tugaydi</b>\n\n"
        f"\U0001F4D6 <b>{html.escape(topic_name)}</b>\n"
        f"\U0001F553 Muddat: <b>{fmt(assignment.deadline)}</b>\n\n"
        + status
    )


def student_keyboard(topic_id: int, required: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="▶️ Testni boshlash",
                    callback_data=f"quizsize:{topic_id}:{required}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="\U0001F4CB Mening topshiriqlarim",
                    callback_data="asg:my",
                )
            ],
        ]
    )


async def send_deadline_reminders(bot, now: datetime | None = None) -> dict:
    """Muddati bugun tugaydigan topshiriqlar bo'yicha eslatma."""
    sent = 0
    failed = 0
    assignments = 0

    async with SessionLocal() as session:
        rows = await assignments_due_soon(session, now)

        for assignment, topic_name, _subject_name in rows:
            students = await pending_students(session, assignment)

            if not students:
                continue

            assignments += 1

            for student in students:
                answered, _required, _done = await student_progress(
                    session, student, assignment
                )

                try:
                    await bot.send_message(
                        student.telegram_id,
                        student_text(topic_name, assignment, answered),
                        reply_markup=student_keyboard(
                            assignment.topic_id,
                            assignment.required_questions,
                        ),
                    )
                    sent += 1
                except Exception as exc:
                    failed += 1
                    log.warning(
                        "Topshiriq eslatmasi yuborilmadi (%s): %s",
                        student.telegram_id,
                        exc,
                    )

                await asyncio.sleep(SEND_DELAY)

    return {"assignments": assignments, "sent": sent, "failed": failed}


async def send_teacher_summaries(bot, now: datetime | None = None) -> dict:
    """Muddati tugagan topshiriq bo'yicha ustozga hisobot."""
    sent = 0

    async with SessionLocal() as session:
        rows = await assignments_just_ended(session, now)

        for assignment, topic_name, subject_name in rows:
            progress = await assignment_progress(session, assignment)

            if not progress:
                continue

            done = [row for row in progress if row.done]
            pending = [row for row in progress if not row.done]

            lines = [
                "\U0001F4CA <b>Topshiriq muddati tugadi</b>",
                "",
                f"\U0001F4DA {html.escape(subject_name)}",
                f"\U0001F4D6 <b>{html.escape(topic_name)}</b>",
                f"\U0001F553 {fmt(assignment.deadline)}",
                "",
                f"✅ Bajardi: <b>{len(done)}</b> / {len(progress)}",
                f"❌ Bajarmadi: <b>{len(pending)}</b>",
            ]

            if pending:
                lines.append("")
                lines.append("<b>Bajarmaganlar:</b>")

                for row in pending[:15]:
                    lines.append(
                        "  • " + html.escape(row.full_name)
                    )

                if len(pending) > 15:
                    lines.append(
                        f"  ... va yana {len(pending) - 15} ta"
                    )

            keyboard = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="\U0001F4CA Batafsil",
                            callback_data=f"asg:p:{assignment.id}",
                        )
                    ]
                ]
            )

            try:
                await bot.send_message(
                    assignment.created_by,
                    "\n".join(lines),
                    reply_markup=keyboard,
                )
                sent += 1
            except Exception as exc:
                log.warning(
                    "Ustozga hisobot yuborilmadi (%s): %s",
                    assignment.created_by,
                    exc,
                )

            await asyncio.sleep(SEND_DELAY)

    return {"summaries": sent}


async def run_daily(bot, now: datetime | None = None) -> dict:
    """Kunlik sikl chaqiradigan asosiy funksiya."""
    if not enabled():
        return {"skipped": True}

    reminders = await send_deadline_reminders(bot, now)
    summaries = await send_teacher_summaries(bot, now)

    return {**reminders, **summaries, "skipped": False}

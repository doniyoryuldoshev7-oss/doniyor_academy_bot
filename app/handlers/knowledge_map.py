"""Bilim xaritasi — Doniyor Academy bot.

Joylashuvi: app/handlers/knowledge_map.py

O'quvchi har bir mavzu bo'yicha qayerda kuchli, qayerda zaif
ekanini bir qarashda ko'radi. Yangi jadval kerak emas: hammasi
mavjud javoblar tarixidan hisoblanadi.
"""

from __future__ import annotations

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

from ..db import SessionLocal
from ..models import Subject, User
from ..services.mastery import (
    LEVEL_EMPTY,
    LEVEL_STRONG,
    LEVEL_UNKNOWN,
    LEVEL_WEAK,
    bar,
    overall_accuracy,
    percent,
    subject_stat,
    weakest_topic,
)

log = logging.getLogger(__name__)

router = Router()

# Bitta xabarga sig'adigan mavzular soni
TOPIC_LIMIT = 25


def _natural_key(name: str):
    parts = re.split(r"(\d+)", (name or "").casefold())
    return tuple(
        (0, int(part)) if part.isdigit() else (1, part)
        for part in parts
    )


def _short(name: str, limit: int = 28) -> str:
    name = (name or "").strip()

    if len(name) <= limit:
        return name

    return name[: limit - 1].rstrip() + "…"


# ---------------------------------------------------------------
#  Matn yig'ish
# ---------------------------------------------------------------

def topic_line(stat) -> str:
    """Bitta mavzu uchun qator."""
    icon = stat.level_label.split()[0]
    name = html.escape(_short(stat.name))

    if stat.level == LEVEL_EMPTY:
        return f"{icon} {name} — <i>boshlanmagan</i>"

    body = f"{icon} {name}\n     {bar(stat.accuracy)} {percent(stat.accuracy)}%"

    extras = [f"{stat.distinct_answered}/{stat.total_questions} savol"]

    if stat.level == LEVEL_UNKNOWN:
        extras.append("ma'lumot kam")

    if stat.review_pending:
        extras.append(f"\U0001F9E0 {stat.review_pending}")

    return body + "  ·  " + "  ·  ".join(extras)


def subject_text(stat) -> str:
    lines = [
        "\U0001F4C8 <b>BILIM XARITASI</b>",
        "",
        "\U0001F4DA <b>" + html.escape(stat.name) + "</b>",
    ]

    if stat.answered:
        lines.append(
            f"{bar(stat.accuracy)} {percent(stat.accuracy)}%  ·  "
            f"{stat.correct}/{stat.answered} to'g'ri javob"
        )
    else:
        lines.append("<i>Bu fandan hali test ishlamagansiz.</i>")

    lines.append("")

    topics = sorted(stat.topics, key=lambda t: _natural_key(t.name))

    for topic in topics[:TOPIC_LIMIT]:
        lines.append(topic_line(topic))

    if len(topics) > TOPIC_LIMIT:
        lines.append("")
        lines.append(
            f"<i>... va yana {len(topics) - TOPIC_LIMIT} ta mavzu</i>"
        )

    weakest = weakest_topic(stat)

    if weakest is not None:
        lines.append("")

        if weakest.level in (LEVEL_WEAK, LEVEL_UNKNOWN):
            lines.append(
                "\U0001F4A1 <b>Tavsiya:</b> "
                + html.escape(_short(weakest.name))
                + " — eng zaif joyingiz, shundan boshlang."
            )
        elif weakest.level == LEVEL_EMPTY:
            lines.append(
                "\U0001F4A1 <b>Tavsiya:</b> "
                + html.escape(_short(weakest.name))
                + " — hali boshlanmagan mavzu."
            )
        else:
            lines.append(
                "\U0001F4A1 <b>Tavsiya:</b> "
                + html.escape(_short(weakest.name))
                + " — biroz mashq qilsa, mustahkamlanadi."
            )

    lines.append("")
    lines.append(
        "<i>\U0001F7E2 mustahkam  ·  \U0001F7E1 o'rtacha  ·  "
        "\U0001F534 zaif  ·  ⚪ ma'lumot kam</i>"
    )

    return "\n".join(lines)


def subject_keyboard(stat, weakest) -> InlineKeyboardMarkup:
    rows = []

    if weakest is not None and weakest.total_questions:
        rows.append(
            [
                InlineKeyboardButton(
                    text="▶️ " + _short(weakest.name, 24)
                    + " dan test",
                    callback_data=f"topic:{weakest.topic_id}",
                )
            ]
        )

    rows.append(
        [
            InlineKeyboardButton(
                text="⬅️ Orqaga",
                callback_data="km:menu",
            )
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(
                text="\U0001F3E0 Bosh menyu",
                callback_data="home",
            )
        ]
    )

    return InlineKeyboardMarkup(inline_keyboard=rows)


# ---------------------------------------------------------------
#  1-ekran: fanlar
# ---------------------------------------------------------------

@router.callback_query(F.data == "km:menu")
async def knowledge_map_menu(c: CallbackQuery):
    async with SessionLocal() as session:
        user = await session.scalar(
            select(User).where(User.telegram_id == c.from_user.id)
        )

        if user is None:
            await c.answer("Foydalanuvchi topilmadi.", show_alert=True)
            return

        subjects = list(
            await session.scalars(
                select(Subject).where(Subject.is_active.is_(True))
            )
        )

        summary = {}

        for subject in subjects:
            answered, correct = await overall_accuracy(
                session, user.id, subject.id
            )
            summary[subject.id] = (answered, correct)

    if not subjects:
        await c.answer("Fanlar topilmadi.", show_alert=True)
        return

    subjects.sort(key=lambda s: _natural_key(s.name))

    rows = []

    for subject in subjects:
        answered, correct = summary.get(subject.id, (0, 0))

        if answered:
            label = (
                f"\U0001F4DA {_short(subject.name, 22)} — "
                f"{percent(correct / answered)}%"
            )
        else:
            label = f"\U0001F4DA {_short(subject.name, 22)} — —"

        rows.append(
            [
                InlineKeyboardButton(
                    text=label,
                    callback_data=f"km:sub:{subject.id}",
                )
            ]
        )

    rows.append([InlineKeyboardButton(text="⬅️ Orqaga", callback_data="mn:prof")])
    rows.append(
        [
            InlineKeyboardButton(
                text="\U0001F3E0 Bosh menyu",
                callback_data="home",
            )
        ]
    )

    text = (
        "\U0001F4C8 <b>BILIM XARITASI</b>\n\n"
        "Har bir fan bo'yicha qayerda kuchli, qayerda zaif "
        "ekaningizni ko'rsatadi.\n\n"
        "Fanni tanlang:"
    )

    try:
        await c.message.edit_text(
            text,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        )
    except Exception as exc:
        if "message is not modified" not in str(exc):
            await c.message.answer(
                text,
                reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
            )

    await c.answer()


# ---------------------------------------------------------------
#  2-ekran: mavzular
# ---------------------------------------------------------------

@router.callback_query(F.data.startswith("km:sub:"))
async def knowledge_map_subject(c: CallbackQuery):
    try:
        subject_id = int(c.data.split(":")[-1])
    except ValueError:
        await c.answer("Noto'g'ri so'rov.", show_alert=True)
        return

    await c.answer("Hisoblanmoqda...")

    async with SessionLocal() as session:
        user = await session.scalar(
            select(User).where(User.telegram_id == c.from_user.id)
        )

        if user is None:
            await c.answer("Foydalanuvchi topilmadi.", show_alert=True)
            return

        stat = await subject_stat(session, user.id, subject_id)

    if stat is None:
        await c.answer("Fan topilmadi.", show_alert=True)
        return

    if not stat.topics:
        await c.message.edit_text(
            "\U0001F4C8 <b>BILIM XARITASI</b>\n\n"
            "\U0001F4DA <b>" + html.escape(stat.name) + "</b>\n\n"
            "Bu fanda hali mavzu yo'q.",
            reply_markup=subject_keyboard(stat, None),
        )
        return

    text = subject_text(stat)
    keyboard = subject_keyboard(stat, weakest_topic(stat))

    try:
        await c.message.edit_text(text, reply_markup=keyboard)
    except Exception as exc:
        if "message is not modified" in str(exc):
            return

        await c.message.answer(text, reply_markup=keyboard)

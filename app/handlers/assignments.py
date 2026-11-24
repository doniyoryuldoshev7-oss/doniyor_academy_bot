"""Ustoz topshiriqlari — ekranlar.

Joylashuvi: app/handlers/assignments.py

Admin: topshiriq yaratadi, kim bajargan-bajarmaganini ko'radi.
O'quvchi: bosh menyuda "Topshiriqlar" bo'limidan ko'radi.

Topshiriq yaratish faqat tugmalar orqali ketadi — matn kiritish
yo'q, shuning uchun FSM holatlari ham kerak emas.
"""

from __future__ import annotations

import asyncio
import html
import logging
import re

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from sqlalchemy import func, select

from ..admin_roles import is_full_admin
from ..db import SessionLocal
from ..models import Question, Subject, Topic, User
from ..services.assignments import (
    active_assignments,
    deadline_after,
    weak_topics_for_user,
    all_assignments,
    assignment_progress,
    assignment_with_names,
    create_assignment,
    deadline_label,
    fmt,
    group_names,
    progress_percent,
    remove_assignment,
    student_progress,
    target_users,
)

log = logging.getLogger(__name__)

router = Router()

COUNT_CHOICES = (10, 20)
DAY_CHOICES = (1, 2, 3, 7)
SEND_DELAY = 0.06


def natural_key(name: str):
    parts = re.split(r"(\d+)", (name or "").casefold())
    return tuple(
        (0, int(part)) if part.isdigit() else (1, part)
        for part in parts
    )


def short(name: str, limit: int = 26) -> str:
    name = (name or "").strip()

    if len(name) <= limit:
        return name

    return name[: limit - 1].rstrip() + "…"


def back_row(callback: str, text: str = "⬅️ Orqaga"):
    return [InlineKeyboardButton(text=text, callback_data=callback)]


def target_label(assignment, rows=None) -> str:
    """Topshiriq kimga berilgani: ism, guruh yoki "hamma"."""
    if assignment.user_id:
        if rows:
            return rows[0].full_name

        return "shaxsiy"

    return assignment.group_name or "hamma"


def admin_row():
    return [
        InlineKeyboardButton(
            text="⚙️ Admin panel",
            callback_data="admin",
        )
    ]


# ---------------------------------------------------------------
#  Admin: bo'lim menyusi
# ---------------------------------------------------------------

@router.callback_query(F.data == "adm:assign")
async def assign_menu(c: CallbackQuery):
    if not is_full_admin(c.from_user.id):
        await c.answer("Ruxsat yo'q.", show_alert=True)
        return

    await c.answer()

    async with SessionLocal() as session:
        rows = await all_assignments(session, limit=10)

    lines = ["\U0001F4CB <b>USTOZ TOPSHIRIQLARI</b>", ""]

    buttons = [
        [
            InlineKeyboardButton(
                text="➕ Yangi topshiriq",
                callback_data="asg:n",
            )
        ]
    ]

    if not rows:
        lines.append(
            "<i>Hali topshiriq berilmagan.</i>\n\n"
            "Yangi topshiriq bering: o'quvchilarga xabar boradi va "
            "kim bajarganini shu yerda kuzatasiz."
        )
    else:
        lines.append("Oxirgi topshiriqlar:")
        lines.append("")

        for assignment, topic_name, subject_name in rows:
            if assignment.user_id:
                target = "shaxsiy"
            else:
                target = assignment.group_name or "hamma"

            lines.append(
                f"• <b>{html.escape(short(topic_name, 24))}</b> "
                f"· {assignment.required_questions} savol\n"
                f"   \U0001F465 {html.escape(target)}  ·  "
                f"\U0001F553 {fmt(assignment.deadline)}"
            )

            buttons.append(
                [
                    InlineKeyboardButton(
                        text=f"\U0001F4CA {short(topic_name, 20)} "
                        f"· {fmt(assignment.deadline)[:5]}",
                        callback_data=f"asg:p:{assignment.id}",
                    )
                ]
            )

    buttons.append(admin_row())

    try:
        await c.message.edit_text(
            "\n".join(lines),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        )
    except Exception as exc:
        if "message is not modified" not in str(exc):
            raise


# ---------------------------------------------------------------
#  Admin: yangi topshiriq — fan
# ---------------------------------------------------------------

@router.callback_query(F.data == "asg:n")
async def assign_subject(c: CallbackQuery):
    if not is_full_admin(c.from_user.id):
        await c.answer("Ruxsat yo'q.", show_alert=True)
        return

    await c.answer()

    async with SessionLocal() as session:
        subjects = list(
            await session.scalars(
                select(Subject).where(Subject.is_active.is_(True))
            )
        )

    if not subjects:
        await c.answer("Fanlar yo'q.", show_alert=True)
        return

    subjects.sort(key=lambda s: natural_key(s.name))

    buttons = [
        [
            InlineKeyboardButton(
                text="\U0001F4DA " + short(s.name),
                callback_data=f"asg:s:{s.id}",
            )
        ]
        for s in subjects
    ]

    buttons.append(back_row("adm:assign"))

    await c.message.edit_text(
        "\U0001F4CB <b>Yangi topshiriq</b>\n\n"
        "1-qadam: fanni tanlang.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )


# ---------------------------------------------------------------
#  Admin: mavzu
# ---------------------------------------------------------------

@router.callback_query(F.data.startswith("asg:s:"))
async def assign_topic(c: CallbackQuery):
    if not is_full_admin(c.from_user.id):
        await c.answer("Ruxsat yo'q.", show_alert=True)
        return

    subject_id = int(c.data.split(":")[-1])

    await c.answer()

    async with SessionLocal() as session:
        topics = list(
            await session.scalars(
                select(Topic).where(Topic.subject_id == subject_id)
            )
        )

        counts = dict(
            (
                await session.execute(
                    select(Question.topic_id, func.count(Question.id))
                    .where(
                        Question.topic_id.in_([t.id for t in topics]),
                        Question.is_active.is_(True),
                    )
                    .group_by(Question.topic_id)
                )
            ).all()
        )  # tezlik2: bitta so'rov

    topics = [t for t in topics if counts.get(t.id)]

    if not topics:
        await c.answer("Bu fanda savolli mavzu yo'q.", show_alert=True)
        return

    topics.sort(key=lambda t: natural_key(t.name))

    buttons = [
        [
            InlineKeyboardButton(
                text=f"\U0001F4D6 {short(t.name, 22)} ({counts[t.id]})",
                callback_data=f"asg:t:{t.id}",
            )
        ]
        for t in topics
    ]

    buttons.append(back_row("asg:n"))

    await c.message.edit_text(
        "\U0001F4CB <b>Yangi topshiriq</b>\n\n"
        "2-qadam: mavzuni tanlang.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )


# ---------------------------------------------------------------
#  Admin: savollar soni
# ---------------------------------------------------------------

@router.callback_query(F.data.startswith("asg:t:"))
async def assign_count(c: CallbackQuery):
    if not is_full_admin(c.from_user.id):
        await c.answer("Ruxsat yo'q.", show_alert=True)
        return

    topic_id = int(c.data.split(":")[-1])

    await c.answer()

    async with SessionLocal() as session:
        topic = await session.get(Topic, topic_id)

        if topic is None:
            await c.answer("Mavzu topilmadi.", show_alert=True)
            return

        total = await session.scalar(
            select(func.count(Question.id)).where(
                Question.topic_id == topic_id,
                Question.is_active.is_(True),
            )
        )

        subject_id = topic.subject_id
        topic_name = topic.name

    options = [n for n in COUNT_CHOICES if n <= (total or 0)]

    if total and total not in options:
        options.append(int(total))

    if not options:
        options = [int(total or 1)]

    buttons = [
        [
            InlineKeyboardButton(
                text=f"{n} ta savol",
                callback_data=f"asg:c:{topic_id}:{n}",
            )
        ]
        for n in options
    ]

    buttons.append(back_row(f"asg:s:{subject_id}"))

    await c.message.edit_text(
        "\U0001F4CB <b>Yangi topshiriq</b>\n\n"
        f"\U0001F4D6 Mavzu: <b>{html.escape(topic_name)}</b>\n"
        f"\U0001F522 Mavjud savollar: <b>{total}</b>\n\n"
        "3-qadam: nechta savol ishlashi kerak?",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )


# ---------------------------------------------------------------
#  Admin: muddat
# ---------------------------------------------------------------

@router.callback_query(F.data.startswith("asg:c:"))
async def assign_deadline(c: CallbackQuery):
    if not is_full_admin(c.from_user.id):
        await c.answer("Ruxsat yo'q.", show_alert=True)
        return

    _, _, topic_id, count = c.data.split(":")

    await c.answer()

    buttons = [
        [
            InlineKeyboardButton(
                text=deadline_label(days),
                callback_data=f"asg:d:{topic_id}:{count}:{days}",
            )
        ]
        for days in DAY_CHOICES
    ]

    buttons.append(back_row(f"asg:t:{topic_id}"))

    await c.message.edit_text(
        "\U0001F4CB <b>Yangi topshiriq</b>\n\n"
        "4-qadam: muddat qachon tugaydi?",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )


# ---------------------------------------------------------------
#  Admin: guruh
# ---------------------------------------------------------------

@router.callback_query(F.data.startswith("asg:d:"))
async def assign_group(c: CallbackQuery):
    if not is_full_admin(c.from_user.id):
        await c.answer("Ruxsat yo'q.", show_alert=True)
        return

    _, _, topic_id, count, days = c.data.split(":")

    await c.answer()

    async with SessionLocal() as session:
        groups = await group_names(session)

    buttons = [
        [
            InlineKeyboardButton(
                text="\U0001F465 Hamma o'quvchilar",
                callback_data=f"asg:ok:{topic_id}:{count}:{days}:all",
            )
        ]
    ]

    for index, name in enumerate(groups):
        buttons.append(
            [
                InlineKeyboardButton(
                    text="\U0001F3EB " + short(name, 24),
                    callback_data=(
                        f"asg:ok:{topic_id}:{count}:{days}:{index}"
                    ),
                )
            ]
        )

    buttons.append(back_row(f"asg:c:{topic_id}:{count}"))

    await c.message.edit_text(
        "\U0001F4CB <b>Yangi topshiriq</b>\n\n"
        "5-qadam: kimga beriladi?",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )


# ---------------------------------------------------------------
#  Admin: yaratish va xabar yuborish
# ---------------------------------------------------------------

@router.callback_query(F.data.startswith("asg:ok:"))
async def assign_create(c: CallbackQuery):
    if not is_full_admin(c.from_user.id):
        await c.answer("Ruxsat yo'q.", show_alert=True)
        return

    try:
        _, _, topic_text, count_text, days_text, group_text = c.data.split(":")
        topic_id = int(topic_text)
        count = int(count_text)
        days = int(days_text)
    except ValueError:
        await c.answer("Noto'g'ri so'rov.", show_alert=True)
        return

    await c.answer("Yaratilmoqda...")

    async with SessionLocal() as session:
        group_name = None

        if group_text != "all":
            groups = await group_names(session)

            try:
                group_name = groups[int(group_text)]
            except (ValueError, IndexError):
                group_name = None

        assignment = await create_assignment(
            session,
            topic_id=topic_id,
            created_by=c.from_user.id,
            required_questions=count,
            days=days,
            group_name=group_name,
        )

        assignment, topic_name, subject_name = await assignment_with_names(
            session, assignment.id
        )

        students = await target_users(session, assignment)

    text = (
        "\U0001F4CB <b>YANGI TOPSHIRIQ</b>\n\n"
        f"\U0001F4DA {html.escape(subject_name)}\n"
        f"\U0001F4D6 <b>{html.escape(topic_name)}</b>\n"
        f"\U0001F522 Kamida <b>{assignment.required_questions}</b> ta savol\n"
        f"⏰ Muddat: <b>{fmt(assignment.deadline)}</b>\n\n"
        "Testni boshlash uchun quyidagi tugmani bosing."
    )

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="▶️ Testni boshlash",
                    callback_data=(
                        f"quizsize:{assignment.topic_id}:"
                        f"{assignment.required_questions}"
                    ),
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

    sent = 0

    for student in students:
        try:
            await c.bot.send_message(
                student.telegram_id,
                text,
                reply_markup=keyboard,
            )
            sent += 1
        except Exception as exc:
            log.warning(
                "Topshiriq xabari yuborilmadi (%s): %s",
                student.telegram_id,
                exc,
            )

        await asyncio.sleep(SEND_DELAY)

    await c.message.edit_text(
        "✅ <b>Topshiriq berildi</b>\n\n"
        f"\U0001F4D6 {html.escape(topic_name)}\n"
        f"\U0001F522 {assignment.required_questions} ta savol\n"
        f"⏰ {fmt(assignment.deadline)}\n"
        f"\U0001F465 {html.escape(assignment.group_name or 'hamma')}\n\n"
        f"\U0001F4E8 Xabar yuborildi: <b>{sent}</b>/"
        f"{len(students)} o'quvchiga",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="\U0001F4CA Bajarilishini ko'rish",
                        callback_data=f"asg:p:{assignment.id}",
                    )
                ],
                back_row("adm:assign", "\U0001F4CB Topshiriqlar"),
            ]
        ),
    )


# ---------------------------------------------------------------
#  Admin: bajarilish
# ---------------------------------------------------------------

@router.callback_query(F.data.startswith("asg:p:"))
async def assign_progress(c: CallbackQuery):
    if not is_full_admin(c.from_user.id):
        await c.answer("Ruxsat yo'q.", show_alert=True)
        return

    assignment_id = int(c.data.split(":")[-1])

    await c.answer("Hisoblanmoqda...")

    async with SessionLocal() as session:
        assignment, topic_name, subject_name = await assignment_with_names(
            session, assignment_id
        )

        if assignment is None:
            await c.answer("Topshiriq topilmadi.", show_alert=True)
            return

        rows = await assignment_progress(session, assignment)

    done = [row for row in rows if row.done]
    pending = [row for row in rows if not row.done]

    lines = [
        "\U0001F4CA <b>Topshiriq bajarilishi</b>",
        "",
        f"\U0001F4D6 <b>{html.escape(topic_name)}</b>",
        f"\U0001F522 Kamida {assignment.required_questions} ta savol",
        f"⏰ Muddat: {fmt(assignment.deadline)}",
        f"\U0001F465 {html.escape(target_label(assignment, rows))}",
        "",
        f"✅ Bajardi: <b>{len(done)}</b> / {len(rows)}",
        f"❌ Bajarmadi: <b>{len(pending)}</b>",
        "",
    ]

    if done:
        lines.append("<b>Bajarganlar:</b>")

        for row in done[:20]:
            lines.append(
                f"  ✅ {html.escape(short(row.full_name, 22))} — "
                f"{row.best_correct}/{row.best_total} ({row.accuracy}%)"
            )

        if len(done) > 20:
            lines.append(f"  ... va yana {len(done) - 20} ta")

        lines.append("")

    if pending:
        lines.append("<b>Bajarmaganlar:</b>")

        for row in pending[:20]:
            if row.answered_best:
                detail = (
                    f" — {row.progress_percent}% "
                    f"({row.answered_best}/{row.required})"
                )
            else:
                detail = " — boshlamagan"

            lines.append(
                f"  ❌ {html.escape(short(row.full_name, 22))}"
                + detail
            )

        if len(pending) > 20:
            lines.append(f"  ... va yana {len(pending) - 20} ta")

    buttons = [
        [
            InlineKeyboardButton(
                text="\U0001F504 Yangilash",
                callback_data=f"asg:p:{assignment_id}",
            )
        ],
        [
            InlineKeyboardButton(
                text="\U0001F5D1 Topshiriqni olib tashlash",
                callback_data=f"asg:del:{assignment_id}",
            )
        ],
        back_row("adm:assign", "\U0001F4CB Topshiriqlar"),
        admin_row(),
    ]

    try:
        await c.message.edit_text(
            "\n".join(lines),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        )
    except Exception as exc:
        if "message is not modified" not in str(exc):
            raise


# ---------------------------------------------------------------
#  O'quvchi: mening topshiriqlarim
# ---------------------------------------------------------------

@router.callback_query(F.data == "asg:my")
async def my_assignments(c: CallbackQuery):
    async with SessionLocal() as session:
        user = await session.scalar(
            select(User).where(User.telegram_id == c.from_user.id)
        )

        if user is None:
            await c.answer("Foydalanuvchi topilmadi.", show_alert=True)
            return

        rows = await active_assignments(session, user=user)

        statuses = {}

        for assignment, _topic, _subject in rows:
            statuses[assignment.id] = await student_progress(
                session, user, assignment
            )

    lines = [
        "\U0001F4CB <b>MENING TOPSHIRIQLARIM</b>",
        "",
        "<i>Topshiriq bitta testda to'liq bajarilishi kerak: barcha savollarga javob berib, testni yakunlang.</i>",
        "",
    ]

    buttons = []

    if not rows:
        lines.append(
            "<i>Hozircha faol topshiriq yo'q.</i>\n\n"
            "Ustoz topshiriq berganda, bu yerda ko'rinadi."
        )
    else:
        for assignment, topic_name, subject_name in rows:
            answered, required, done = statuses.get(
                assignment.id,
                (0, assignment.required_questions, False),
            )

            percent = progress_percent(answered, required)

            if done:
                mark = "✅ <b>bajarildi</b>"
            elif answered:
                mark = (
                    f"⏳ Eng yaxshi urinish: <b>{answered}/{required}</b> "
                    f"({percent}%) — testni to'liq ishlab chiqing"
                )
            else:
                mark = "⏳ <b>0%</b> — hali boshlanmagan"

            lines.append(
                f"\U0001F4D6 <b>{html.escape(short(topic_name, 24))}</b>\n"
                f"   \U0001F522 {required} ta savol  "
                f"·  ⏰ {fmt(assignment.deadline)}\n"
                f"   {mark}"
            )
            lines.append("")

            if not done:
                buttons.append(
                    [
                        InlineKeyboardButton(
                            text=f"▶️ {short(topic_name, 22)}",
                            callback_data=(
                                f"quizsize:{assignment.topic_id}:"
                                f"{assignment.required_questions}"
                            ),
                        )
                    ]
                )

    buttons.append(
        [
            InlineKeyboardButton(
                text="\U0001F3E0 Bosh menyu",
                callback_data="home",
            )
        ]
    )

    try:
        await c.message.edit_text(
            "\n".join(lines),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        )
    except Exception as exc:
        if "message is not modified" not in str(exc):
            await c.message.answer(
                "\n".join(lines),
                reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
            )

    await c.answer()


@router.message(Command("topshiriqlar"))
async def assignments_command(m: Message):
    if is_full_admin(m.from_user.id):
        async with SessionLocal() as session:
            rows = await all_assignments(session, limit=10)

        if not rows:
            await m.answer(
                "\U0001F4CB Hali topshiriq berilmagan.\n\n"
                "Admin panel → \U0001F4CB Ustoz topshirig'i"
            )
            return

        buttons = [
            [
                InlineKeyboardButton(
                    text=f"\U0001F4CA {short(topic_name, 20)}",
                    callback_data=f"asg:p:{assignment.id}",
                )
            ]
            for assignment, topic_name, _subject in rows
        ]

        await m.answer(
            "\U0001F4CB <b>Topshiriqlar</b>\n\nBirini tanlang:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        )
        return

    await m.answer(
        "\U0001F4CB Topshiriqlaringizni bosh menyudagi "
        "<b>Topshiriqlar</b> tugmasidan ko'ring."
    )


# ---------------------------------------------------------------
#  Shaxsiy topshiriq: o'quvchi kartasidan
# ---------------------------------------------------------------

def encouraging_note() -> str:
    return (
        "Bu — siz uchun tanlangan <b>shaxsiy mashq</b>. "
        "Maqsad baho qo'yish emas, shu mavzuni mustahkamlash."
    )


@router.callback_query(F.data.startswith("asg:u:"))
async def assign_personal_topics(c: CallbackQuery):
    if not is_full_admin(c.from_user.id):
        await c.answer("Ruxsat yo'q.", show_alert=True)
        return

    try:
        telegram_id = int(c.data.split(":")[-1])
    except ValueError:
        await c.answer("Noto'g'ri so'rov.", show_alert=True)
        return

    await c.answer("Zaif mavzular qidirilmoqda...")

    async with SessionLocal() as session:
        user = await session.scalar(
            select(User).where(User.telegram_id == telegram_id)
        )

        if user is None:
            await c.answer("O'quvchi topilmadi.", show_alert=True)
            return

        weak = await weak_topics_for_user(session, user.id, limit=4)
        name = user.full_name or "O'quvchi"

    lines = [
        "\U0001F4CB <b>Shaxsiy topshiriq</b>",
        "\U0001F464 " + html.escape(name),
        "",
    ]

    buttons = []

    if not weak:
        lines.append(
            "<i>Bu o'quvchi hali yetarli test ishlamagan, shuning "
            "uchun zaif mavzusini aniqlab bo'lmaydi.</i>\n\n"
            "Istasangiz, mavzuni o'zingiz tanlab, guruh topshirig'i "
            "sifatida bering."
        )
    else:
        lines.append(
            "Tizim eng zaif mavzularni topdi. Birini tanlang:"
        )
        lines.append("")

        for item in weak:
            lines.append(
                f"\U0001F4D6 <b>{html.escape(short(item.topic_name, 24))}</b>"
                f"  —  <b>{item.accuracy}%</b>\n"
                f"   {html.escape(short(item.subject_name, 20))}  ·  "
                f"{item.distinct_questions} savol ishlangan  ·  "
                f"mavzuda {item.available_questions} ta savol"
            )
            lines.append("")

            buttons.append(
                [
                    InlineKeyboardButton(
                        text=f"\U0001F4D6 {short(item.topic_name, 20)} "
                        f"({item.accuracy}%)",
                        callback_data=(
                            f"asg:ud:{telegram_id}:{item.topic_id}"
                        ),
                    )
                ]
            )

    buttons.append(
        [
            InlineKeyboardButton(
                text="\u2B05\uFE0F O'quvchi kartasi",
                callback_data=f"rep:u:{telegram_id}",
            )
        ]
    )

    try:
        await c.message.edit_text(
            "\n".join(lines),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        )
    except Exception as exc:
        if "message is not modified" not in str(exc):
            raise


@router.callback_query(F.data.startswith("asg:ud:"))
async def assign_personal_deadline(c: CallbackQuery):
    if not is_full_admin(c.from_user.id):
        await c.answer("Ruxsat yo'q.", show_alert=True)
        return

    try:
        _, _, telegram_text, topic_text = c.data.split(":")
        telegram_id = int(telegram_text)
        topic_id = int(topic_text)
    except ValueError:
        await c.answer("Noto'g'ri so'rov.", show_alert=True)
        return

    await c.answer()

    async with SessionLocal() as session:
        topic = await session.get(Topic, topic_id)

        total = await session.scalar(
            select(func.count(Question.id)).where(
                Question.topic_id == topic_id,
                Question.is_active.is_(True),
            )
        )

    if topic is None:
        await c.answer("Mavzu topilmadi.", show_alert=True)
        return

    count = min(20, int(total or 0)) or 1

    buttons = [
        [
            InlineKeyboardButton(
                text=deadline_label(days),
                callback_data=(
                    f"asg:uok:{telegram_id}:{topic_id}:{days}:{count}"
                ),
            )
        ]
        for days in DAY_CHOICES
    ]

    buttons.append(
        [
            InlineKeyboardButton(
                text="\u2B05\uFE0F Mavzular",
                callback_data=f"asg:u:{telegram_id}",
            )
        ]
    )

    await c.message.edit_text(
        "\U0001F4CB <b>Shaxsiy topshiriq</b>\n\n"
        f"\U0001F4D6 <b>{html.escape(topic.name)}</b>\n"
        f"\U0001F522 <b>{count}</b> ta savol "
        f"(mavzuda {total} ta bor)\n\n"
        "Muddat qachon tugaydi?",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )


@router.callback_query(F.data.startswith("asg:uok:"))
async def assign_personal_create(c: CallbackQuery):
    if not is_full_admin(c.from_user.id):
        await c.answer("Ruxsat yo'q.", show_alert=True)
        return

    try:
        parts = c.data.split(":")
        telegram_id = int(parts[2])
        topic_id = int(parts[3])
        days = int(parts[4])
        count = int(parts[5])
    except (IndexError, ValueError):
        await c.answer("Noto'g'ri so'rov.", show_alert=True)
        return

    await c.answer("Yaratilmoqda...")

    async with SessionLocal() as session:
        user = await session.scalar(
            select(User).where(User.telegram_id == telegram_id)
        )

        if user is None:
            await c.answer("O'quvchi topilmadi.", show_alert=True)
            return

        assignment = await create_assignment(
            session,
            topic_id=topic_id,
            created_by=c.from_user.id,
            required_questions=count,
            days=days,
            user_id=user.id,
        )

        assignment, topic_name, subject_name = await assignment_with_names(
            session, assignment.id
        )

        student_name = user.full_name or "O'quvchi"

    text = (
        "\U0001F4CB <b>SHAXSIY TOPSHIRIQ</b>\n\n"
        f"\U0001F4DA {html.escape(subject_name)}\n"
        f"\U0001F4D6 <b>{html.escape(topic_name)}</b>\n"
        f"\U0001F522 Kamida <b>{assignment.required_questions}</b> ta savol\n"
        f"⏰ Muddat: <b>{fmt(assignment.deadline)}</b>\n\n"
        + encouraging_note()
    )

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="▶️ Testni boshlash",
                    callback_data=(
                                f"quizsize:{assignment.topic_id}:"
                                f"{assignment.required_questions}"
                            ),
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

    delivered = True

    try:
        await c.bot.send_message(telegram_id, text, reply_markup=keyboard)
    except Exception as exc:
        delivered = False
        log.warning(
            "Shaxsiy topshiriq yuborilmadi (%s): %s", telegram_id, exc
        )

    status = (
        "\U0001F4E8 Xabar yuborildi."
        if delivered
        else "⚠️ Xabar yuborilmadi (o'quvchi botni bloklagan bo'lishi mumkin)."
    )

    await c.message.edit_text(
        "✅ <b>Shaxsiy topshiriq berildi</b>\n\n"
        f"\U0001F464 {html.escape(student_name)}\n"
        f"\U0001F4D6 {html.escape(topic_name)}\n"
        f"\U0001F522 {assignment.required_questions} ta savol\n"
        f"⏰ {fmt(assignment.deadline)}\n\n"
        + status,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="\U0001F4CA Bajarilishini ko'rish",
                        callback_data=f"asg:p:{assignment.id}",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="\U0001F464 O'quvchi kartasi",
                        callback_data=f"rep:u:{telegram_id}",
                    )
                ],
            ]
        ),
    )


# ---------------------------------------------------------------
#  Topshiriqni olib tashlash
# ---------------------------------------------------------------

@router.callback_query(F.data.startswith("asg:del:"))
async def assign_remove_ask(c: CallbackQuery):
    if not is_full_admin(c.from_user.id):
        await c.answer("Ruxsat yo'q.", show_alert=True)
        return

    try:
        assignment_id = int(c.data.split(":")[-1])
    except ValueError:
        await c.answer("Noto'g'ri so'rov.", show_alert=True)
        return

    await c.answer()

    async with SessionLocal() as session:
        assignment, topic_name, subject_name = await assignment_with_names(
            session, assignment_id
        )

        if assignment is None:
            await c.answer("Topshiriq topilmadi.", show_alert=True)
            return

        rows = await assignment_progress(session, assignment)

    done = sum(1 for row in rows if row.done)

    await c.message.edit_text(
        "\U0001F5D1 <b>Topshiriqni olib tashlash</b>\n\n"
        f"\U0001F4D6 <b>{html.escape(topic_name)}</b>\n"
        f"\U0001F522 {assignment.required_questions} ta savol\n"
        f"\u23F0 {fmt(assignment.deadline)}\n"
        f"\U0001F465 {html.escape(target_label(assignment, rows))}\n"
        f"\u2705 Bajargan: {done} / {len(rows)}\n\n"
        "Topshiriq o'quvchilar ro'yxatidan va eslatmalardan "
        "chiqib ketadi. <b>Test natijalari saqlanib qoladi.</b>\n\n"
        "Rostdan olib tashlansinmi?",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="\U0001F5D1 Ha, olib tashlansin",
                        callback_data=f"asg:delok:{assignment_id}",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="\u274C Yo'q, qolsin",
                        callback_data=f"asg:p:{assignment_id}",
                    )
                ],
            ]
        ),
    )


@router.callback_query(F.data.startswith("asg:delok:"))
async def assign_remove_do(c: CallbackQuery):
    if not is_full_admin(c.from_user.id):
        await c.answer("Ruxsat yo'q.", show_alert=True)
        return

    try:
        assignment_id = int(c.data.split(":")[-1])
    except ValueError:
        await c.answer("Noto'g'ri so'rov.", show_alert=True)
        return

    async with SessionLocal() as session:
        assignment, topic_name, _subject = await assignment_with_names(
            session, assignment_id
        )

        if assignment is None:
            await c.answer("Topshiriq topilmadi.", show_alert=True)
            return

        removed = await remove_assignment(session, assignment_id)

    if not removed:
        await c.answer("Olib tashlab bo'lmadi.", show_alert=True)
        return

    await c.answer("Olib tashlandi")

    await c.message.edit_text(
        "\u2705 <b>Topshiriq olib tashlandi</b>\n\n"
        f"\U0001F4D6 {html.escape(topic_name)}\n\n"
        "<i>Endi o'quvchilarning ro'yxatida ko'rinmaydi va "
        "muddat eslatmasi yuborilmaydi.</i>",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                back_row("adm:assign", "\U0001F4CB Topshiriqlar"),
                admin_row(),
            ]
        ),
    )

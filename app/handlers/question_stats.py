"""Savollar sifati statistikasi — admin ekranlari.

Joylashuvi: app/handlers/question_stats.py

Admin panelda "\U0001F52C Savollar sifati" tugmasi:
    xulosa -> muammoli savollar ro'yxati -> savol tahlili
"""

from __future__ import annotations

import html
import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from ..admin_roles import is_full_admin
from ..db import SessionLocal
from ..services.question_stats import (
    KIND_DEAD,
    KIND_EASY,
    KIND_HARD,
    KIND_SUSPECT,
    KIND_TITLES,
    OPTIONS,
    by_kind,
    question_stats,
    summarize,
)

log = logging.getLogger(__name__)

router = Router()

PAGE_SIZE = 6


def short(text: str | None, limit: int = 48) -> str:
    body = (text or "").strip()

    if not body:
        return ""

    if len(body) <= limit:
        return body

    return body[: limit - 1].rstrip() + "…"


def question_label(stat, limit: int = 48) -> str:
    body = short(stat.text, limit)

    if body:
        return html.escape(body)

    if stat.has_image:
        return "\U0001F5BC <i>rasmli savol</i>"

    return "<i>matnsiz savol</i>"


# ---------------------------------------------------------------
#  1-ekran: xulosa
# ---------------------------------------------------------------

async def render_summary(target, edit: bool = True):
    async with SessionLocal() as session:
        stats = await question_stats(session)

    summary = summarize(stats)

    lines = [
        "\U0001F52C <b>SAVOLLAR SIFATI</b>",
        "",
        f"\U0001F4CA Tahlil qilingan: <b>{summary.analysed}</b> ta savol",
        f"<i>(kamida {summary.threshold} marta javob berilgan savollar)</i>",
    ]

    if summary.analysed:
        lines.append(
            f"\U0001F3AF O'rtacha qiyinlik: "
            f"<b>{summary.average_difficulty}%</b> to'g'ri javob"
        )
        lines.append("")

        lines.append(
            f"{KIND_TITLES[KIND_SUSPECT]}: <b>{len(summary.suspect)}</b> ta"
        )
        lines.append(
            f"{KIND_TITLES[KIND_HARD]}: <b>{len(summary.hard)}</b> ta"
        )
        lines.append(
            f"{KIND_TITLES[KIND_EASY]}: <b>{len(summary.easy)}</b> ta"
        )
        lines.append(
            f"{KIND_TITLES[KIND_DEAD]}: <b>{len(summary.dead)}</b> ta"
        )

        if summary.suspect:
            lines.append("")
            lines.append(
                "<i>Eng muhimi — \"kaliti shubhali\". Bunday "
                "savollarda ko'pchilik bitta xato variantni tanlaydi, "
                "ya'ni to'g'ri javob noto'g'ri belgilangan bo'lishi "
                "mumkin.</i>"
            )
        elif summary.problems == 0:
            lines.append("")
            lines.append("<i>Muammoli savol topilmadi.</i>")
    else:
        lines.append("")
        lines.append(
            "<i>Hali yetarli javob yig'ilmagan. O'quvchilar test "
            "ishlagan sayin statistika to'ldirib boriladi.</i>"
        )

    buttons = []

    for kind, items in (
        (KIND_SUSPECT, summary.suspect),
        (KIND_HARD, summary.hard),
        (KIND_EASY, summary.easy),
        (KIND_DEAD, summary.dead),
    ):
        if items:
            buttons.append(
                [
                    InlineKeyboardButton(
                        text=f"{KIND_TITLES[kind]} ({len(items)})",
                        callback_data=f"qst:k:{kind}:0",
                    )
                ]
            )

    buttons.append(
        [
            InlineKeyboardButton(
                text="⚙️ Admin panel",
                callback_data="admin",
            )
        ]
    )

    text = "\n".join(lines)
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)

    if edit:
        try:
            await target.edit_text(text, reply_markup=keyboard)
            return
        except Exception as exc:
            if "message is not modified" in str(exc):
                return

    await target.answer(text, reply_markup=keyboard)


@router.callback_query(F.data == "adm:qstat")
async def stats_entry(c: CallbackQuery):
    if not is_full_admin(c.from_user.id):
        await c.answer("Ruxsat yo'q.", show_alert=True)
        return

    await c.answer("Hisoblanmoqda...")
    await render_summary(c.message)


@router.message(Command("savol_sifati"))
async def stats_command(m: Message):
    if not is_full_admin(m.from_user.id):
        return

    await render_summary(m, edit=False)


# ---------------------------------------------------------------
#  2-ekran: toifa bo'yicha ro'yxat
# ---------------------------------------------------------------

@router.callback_query(F.data.startswith("qst:k:"))
async def stats_kind(c: CallbackQuery):
    if not is_full_admin(c.from_user.id):
        await c.answer("Ruxsat yo'q.", show_alert=True)
        return

    try:
        _, _, kind, page_text = c.data.split(":")
        page = int(page_text)
    except ValueError:
        await c.answer("Noto'g'ri so'rov.", show_alert=True)
        return

    await c.answer()

    async with SessionLocal() as session:
        stats = await question_stats(session)

    summary = summarize(stats)
    items = by_kind(summary, kind)

    if not items:
        await c.answer("Bu toifada savol yo'q.", show_alert=True)
        return

    pages = max(1, (len(items) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(0, min(page, pages - 1))
    chunk = items[page * PAGE_SIZE : (page + 1) * PAGE_SIZE]

    lines = [
        KIND_TITLES.get(kind, kind) + f" — <b>{len(items)}</b> ta",
        "",
    ]

    buttons = []

    for number, stat in enumerate(chunk, start=page * PAGE_SIZE + 1):
        lines.append(
            f"<b>{number}.</b> " + question_label(stat)
        )
        lines.append(
            f"     \U0001F4DA {html.escape(short(stat.topic_name, 22))}  "
            f"·  {stat.answers} javob  ·  "
            f"to'g'ri {stat.difficulty_percent}%"
        )

        top = stat.top_wrong

        if kind == KIND_SUSPECT and top:
            lines.append(
                f"     ⚠️ ko'pchilik <b>{top[0]}</b> ni "
                f"tanlagan, kalit <b>{stat.correct_option or '—'}</b>"
            )

        lines.append("")

        buttons.append(
            InlineKeyboardButton(
                text=str(number),
                callback_data=f"qst:q:{stat.question_id}:{kind}:{page}",
            )
        )

    lines.append(
        "<i>Savolni ochish uchun pastdagi raqamni bosing.</i>"
    )

    if pages > 1:
        lines.append(f"<i>Sahifa {page + 1}/{pages}</i>")

    rows = []

    for start in range(0, len(buttons), 6):
        rows.append(buttons[start : start + 6])

    nav = []

    if page > 0:
        nav.append(
            InlineKeyboardButton(
                text="◀️",
                callback_data=f"qst:k:{kind}:{page - 1}",
            )
        )

    if page + 1 < pages:
        nav.append(
            InlineKeyboardButton(
                text="▶️",
                callback_data=f"qst:k:{kind}:{page + 1}",
            )
        )

    if nav:
        rows.append(nav)

    rows.append(
        [
            InlineKeyboardButton(
                text="⬅️ Xulosa",
                callback_data="adm:qstat",
            )
        ]
    )

    try:
        await c.message.edit_text(
            "\n".join(lines),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        )
    except Exception as exc:
        if "message is not modified" not in str(exc):
            raise


# ---------------------------------------------------------------
#  3-ekran: bitta savol tahlili
# ---------------------------------------------------------------

@router.callback_query(F.data.startswith("qst:q:"))
async def stats_question(c: CallbackQuery):
    if not is_full_admin(c.from_user.id):
        await c.answer("Ruxsat yo'q.", show_alert=True)
        return

    parts = c.data.split(":")

    try:
        question_id = int(parts[2])
    except (IndexError, ValueError):
        await c.answer("Noto'g'ri so'rov.", show_alert=True)
        return

    kind = parts[3] if len(parts) > 3 else KIND_SUSPECT
    page = parts[4] if len(parts) > 4 else "0"

    await c.answer()

    async with SessionLocal() as session:
        stats = await question_stats(session)

    stat = next(
        (item for item in stats if item.question_id == question_id),
        None,
    )

    if stat is None:
        await c.answer("Savol statistikasi topilmadi.", show_alert=True)
        return

    lines = [
        f"\U0001F52C <b>Savol #{stat.question_id}</b>",
        "\U0001F4DA "
        + html.escape(short(stat.subject_name, 20))
        + " · "
        + html.escape(short(stat.topic_name, 22)),
        "",
    ]

    body = (stat.text or "").strip()

    if body:
        lines.append(html.escape(short(body, 300)))
    elif stat.has_image:
        lines.append("<i>Savol rasmda — pastdagi tugmadan ko'ring.</i>")

    lines.append("")
    lines.append(
        f"\U0001F4CA Javoblar: <b>{stat.answers}</b> ta  ·  "
        f"to'g'ri: <b>{stat.correct}</b> "
        f"({stat.difficulty_percent}%)"
    )
    lines.append("")

    for key in OPTIONS:
        value = stat.options.get(key)
        label = html.escape(short(value, 30)) if value else ""

        marker = " ← <b>kalit</b>" if key == stat.correct_option else ""

        count = stat.picks.get(key, 0)
        share = stat.pick_percent(key)

        line = f"<b>{key})</b>"

        if label:
            line += " " + label

        line += f" · {count} ta ({share}%)" + marker

        lines.append(line)

    lines.append("")
    lines.append("\U0001F4A1 " + stat.verdict)

    buttons = []

    if stat.has_image:
        buttons.append(
            [
                InlineKeyboardButton(
                    text="\U0001F5BC Rasmni ko'rish",
                    callback_data=f"rep:img:{stat.question_id}",
                )
            ]
        )

    buttons.append(
        [
            InlineKeyboardButton(
                text="\U0001F527 Savolni ochish (tahrirlash)",
                callback_data=f"adm:question:{stat.question_id}",
            )
        ]
    )
    buttons.append(
        [
            InlineKeyboardButton(
                text="⬅️ Ro'yxat",
                callback_data=f"qst:k:{kind}:{page}",
            )
        ]
    )
    buttons.append(
        [
            InlineKeyboardButton(
                text="⚙️ Admin panel",
                callback_data="admin",
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

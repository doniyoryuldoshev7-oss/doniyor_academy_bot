"""O'quvchilar natijalari — admin uchun ekranlar.

Joylashuvi: app/handlers/student_reports.py

Admin panelda "\U0001F4CA O'quvchilar natijasi" tugmasi:
    ro'yxat -> o'quvchi kartasi -> to'liq tarix
"""

from __future__ import annotations

import html
import logging
from pathlib import Path

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from ..admin_roles import is_full_admin
from ..db import SessionLocal
from ..services.student_report import (
    SORT_ACTIVITY,
    attempt_detail,
    question_image,
    wrong_questions,
    SORT_NAME,
    SORT_RESULT,
    compute_trend,
    days_since,
    fmt_date,
    fmt_datetime,
    student_report,
    students_list,
)

log = logging.getLogger(__name__)

router = Router()

PAGE_SIZE = 8
HISTORY_PAGE_SIZE = 10
RECENT_ON_CARD = 5

BAR_FULL = "█"
BAR_EMPTY = "░"


def bar(value: int, width: int = 8) -> str:
    value = max(0, min(100, value))
    filled = int(round(value / 100 * width))

    return BAR_FULL * filled + BAR_EMPTY * (width - filled)


def short(name: str, limit: int = 20) -> str:
    name = (name or "").strip()

    if len(name) <= limit:
        return name

    return name[: limit - 1].rstrip() + "…"


def when_label(value, now=None) -> str:
    days = days_since(value, now)

    if days is None:
        return "hech qachon"

    if days == 0:
        return "bugun"

    if days == 1:
        return "kecha"

    if days < 7:
        return f"{days} kun oldin"

    if days < 30:
        return f"{days // 7} hafta oldin"

    return f"{days // 30} oy oldin"


# ---------------------------------------------------------------
#  1-ekran: ro'yxat
# ---------------------------------------------------------------

def list_keyboard(rows, sort: str, page: int, pages: int):
    buttons = []

    for row in rows:
        if row.attempts:
            label = (
                f"{short(row.full_name)} · {row.accuracy}% "
                f"· {row.attempts} test"
            )
        else:
            label = f"{short(row.full_name)} · test ishlamagan"

        buttons.append(
            [
                InlineKeyboardButton(
                    text=label,
                    callback_data=f"rep:u:{row.telegram_id}",
                )
            ]
        )

    nav = []

    if page > 0:
        nav.append(
            InlineKeyboardButton(
                text="◀️",
                callback_data=f"rep:p:{sort}:{page - 1}",
            )
        )

    if pages > 1:
        nav.append(
            InlineKeyboardButton(
                text=f"{page + 1}/{pages}",
                callback_data="rep:noop",
            )
        )

    if page + 1 < pages:
        nav.append(
            InlineKeyboardButton(
                text="▶️",
                callback_data=f"rep:p:{sort}:{page + 1}",
            )
        )

    if nav:
        buttons.append(nav)

    sort_row = []

    if sort != SORT_ACTIVITY:
        sort_row.append(
            InlineKeyboardButton(
                text="\U0001F553 Faollik",
                callback_data=f"rep:p:{SORT_ACTIVITY}:0",
            )
        )

    if sort != SORT_RESULT:
        sort_row.append(
            InlineKeyboardButton(
                text="\U0001F4CA Natija",
                callback_data=f"rep:p:{SORT_RESULT}:0",
            )
        )

    if sort != SORT_NAME:
        sort_row.append(
            InlineKeyboardButton(
                text="\U0001F524 Ism",
                callback_data=f"rep:p:{SORT_NAME}:0",
            )
        )

    buttons.append(sort_row)

    buttons.append(
        [
            InlineKeyboardButton(
                text="⚙️ Admin panel",
                callback_data="admin",
            )
        ]
    )

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def list_text(all_rows, sort: str) -> str:
    total = len(all_rows)
    never = sum(1 for row in all_rows if not row.attempts)

    active_week = sum(
        1
        for row in all_rows
        if row.last_attempt is not None
        and (days_since(row.last_attempt) or 0) < 7
    )

    sort_label = {
        SORT_ACTIVITY: "\U0001F553 faollik bo'yicha",
        SORT_RESULT: "\U0001F4CA natija bo'yicha",
        SORT_NAME: "\U0001F524 ism bo'yicha",
    }.get(sort, sort)

    return (
        "\U0001F4CA <b>O'QUVCHILAR NATIJASI</b>\n\n"
        f"\U0001F465 Jami: <b>{total}</b> ta o'quvchi\n"
        f"\U0001F525 Shu hafta faol: <b>{active_week}</b> ta\n"
        f"\U0001F634 Hech qachon test ishlamagan: <b>{never}</b> ta\n\n"
        f"Saralash: {sort_label}\n\n"
        "O'quvchini tanlang:"
    )


async def render_list(target, sort: str, page: int, edit: bool = True):
    async with SessionLocal() as session:
        rows = await students_list(session, sort=sort)

    if not rows:
        text = (
            "\U0001F4CA <b>O'QUVCHILAR NATIJASI</b>\n\n"
            "Tasdiqlangan o'quvchilar yo'q."
        )
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[[
                InlineKeyboardButton(
                    text="⚙️ Admin panel",
                    callback_data="admin",
                )
            ]]
        )
    else:
        pages = max(1, (len(rows) + PAGE_SIZE - 1) // PAGE_SIZE)
        page = max(0, min(page, pages - 1))
        chunk = rows[page * PAGE_SIZE : (page + 1) * PAGE_SIZE]

        text = list_text(rows, sort)
        keyboard = list_keyboard(chunk, sort, page, pages)

    if edit:
        try:
            await target.edit_text(text, reply_markup=keyboard)
            return
        except Exception as exc:
            if "message is not modified" in str(exc):
                return

    await target.answer(text, reply_markup=keyboard)


@router.callback_query(F.data == "adm:reports")
async def reports_entry(c: CallbackQuery):
    if not is_full_admin(c.from_user.id):
        await c.answer("Ruxsat yo'q.", show_alert=True)
        return

    await c.answer()
    await render_list(c.message, SORT_ACTIVITY, 0)


@router.callback_query(F.data.startswith("rep:p:"))
async def reports_page(c: CallbackQuery):
    if not is_full_admin(c.from_user.id):
        await c.answer("Ruxsat yo'q.", show_alert=True)
        return

    try:
        _, _, sort, page_text = c.data.split(":")
        page = int(page_text)
    except ValueError:
        await c.answer("Noto'g'ri so'rov.", show_alert=True)
        return

    await c.answer()
    await render_list(c.message, sort, page)


@router.callback_query(F.data == "rep:noop")
async def reports_noop(c: CallbackQuery):
    await c.answer()


# ---------------------------------------------------------------
#  2-ekran: o'quvchi kartasi
# ---------------------------------------------------------------

def trend_line(trend) -> str:
    if trend is None:
        return (
            "\U0001F4C8 O'sish: <i>hali aniqlab bo'lmaydi "
            "(test kam ishlangan)</i>"
        )

    delta = trend.delta

    if delta > 2:
        icon = "\U0001F4C8"
        word = "o'sish"
        sign = f"+{delta}"
    elif delta < -2:
        icon = "\U0001F4C9"
        word = "pasayish"
        sign = str(delta)
    else:
        icon = "➡️"
        word = "o'zgarishsiz"
        sign = f"{delta:+d}"

    if trend.mode == "oxirgi":
        detail = (
            f"oxirgi {trend.window} ta test {trend.recent}%, "
            f"undan oldingi {trend.window} tasi {trend.earlier}%"
        )
    else:
        detail = (
            f"oxirgi yarmi {trend.recent}%, "
            f"birinchi yarmi {trend.earlier}%"
        )

    return f"{icon} <b>{word.capitalize()}:</b> {detail} → <b>{sign}</b>"


def card_text(report) -> str:
    lines = [
        "\U0001F464 <b>" + html.escape(report.full_name) + "</b>",
    ]

    if report.group_name:
        lines.append(
            "\U0001F465 Guruh: <b>"
            + html.escape(report.group_name)
            + "</b>"
        )

    lines.append("")
    lines.append(
        "\U0001F4C5 Ro'yxatdan o'tgan: "
        + fmt_date(report.registered_at)
    )

    if report.attempt_count:
        lines.append(
            "\U0001F553 Oxirgi test: "
            + fmt_datetime(report.last_attempt)
            + " (" + when_label(report.last_attempt) + ")"
        )
        lines.append(
            "\U0001F195 Birinchi test: "
            + fmt_date(report.first_attempt)
        )
        lines.append("")
        lines.append(
            f"\U0001F4DD Jami: <b>{report.attempt_count}</b> ta test "
            f"· <b>{report.total}</b> savol"
        )
        lines.append(
            f"\U0001F3AF Umumiy natija: {bar(report.accuracy)} "
            f"<b>{report.accuracy}%</b> "
            f"({report.correct}/{report.total})"
        )
        lines.append("")
        lines.append(trend_line(compute_trend(report.attempts)))

        if report.subjects:
            lines.append("")
            lines.append("\U0001F4DA <b>Fanlar bo'yicha:</b>")

            for subject in report.subjects:
                lines.append(
                    f"  • {html.escape(short(subject.name, 26))} — "
                    f"<b>{subject.accuracy}%</b> "
                    f"({subject.attempts} test)"
                )

        lines.append("")
        lines.append("\U0001F5D3 <b>Oxirgi testlar:</b>")

        for attempt in reversed(report.attempts[-RECENT_ON_CARD:]):
            lines.append(
                "  • "
                + fmt_datetime(attempt.created_at)
                + " — "
                + html.escape(short(attempt.topic_name, 22))
                + f" — {attempt.correct}/{attempt.total} "
                f"({attempt.accuracy}%)"
            )
    else:
        lines.append("")
        lines.append(
            "<i>Bu o'quvchi hali birorta test ishlamagan.</i>"
        )

    return "\n".join(lines)


def card_keyboard(report, sort: str = SORT_ACTIVITY):
    buttons = []

    if report.attempt_count:
        buttons.append(
            [
                InlineKeyboardButton(
                    text="\U0001F4CB Topshiriq berish",
                    callback_data=f"asg:u:{report.telegram_id}",
                )
            ]
        )

        buttons.append(
            [
                InlineKeyboardButton(
                    text="\U0001F4C4 To'liq tarix",
                    callback_data=f"rep:h:{report.telegram_id}:0",
                )
            ]
        )

    buttons.append(
        [
            InlineKeyboardButton(
                text="⬅️ Ro'yxat",
                callback_data=f"rep:p:{sort}:0",
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

    return InlineKeyboardMarkup(inline_keyboard=buttons)


@router.callback_query(F.data.startswith("rep:u:"))
async def reports_user(c: CallbackQuery):
    if not is_full_admin(c.from_user.id):
        await c.answer("Ruxsat yo'q.", show_alert=True)
        return

    try:
        telegram_id = int(c.data.split(":")[-1])
    except ValueError:
        await c.answer("Noto'g'ri so'rov.", show_alert=True)
        return

    await c.answer("Hisoblanmoqda...")

    async with SessionLocal() as session:
        report = await student_report(session, telegram_id)

    if report is None:
        await c.answer("O'quvchi topilmadi.", show_alert=True)
        return

    text = card_text(report)
    keyboard = card_keyboard(report)

    try:
        await c.message.edit_text(text, reply_markup=keyboard)
    except Exception as exc:
        if "message is not modified" in str(exc):
            return

        await c.message.answer(text, reply_markup=keyboard)


# ---------------------------------------------------------------
#  3-ekran: to'liq tarix
# ---------------------------------------------------------------

@router.callback_query(F.data.startswith("rep:h:"))
async def reports_history(c: CallbackQuery):
    if not is_full_admin(c.from_user.id):
        await c.answer("Ruxsat yo'q.", show_alert=True)
        return

    try:
        _, _, id_text, page_text = c.data.split(":")
        telegram_id = int(id_text)
        page = int(page_text)
    except ValueError:
        await c.answer("Noto'g'ri so'rov.", show_alert=True)
        return

    await c.answer()

    async with SessionLocal() as session:
        report = await student_report(session, telegram_id)

    if report is None:
        await c.answer("O'quvchi topilmadi.", show_alert=True)
        return

    attempts = list(reversed(report.attempts))
    pages = max(1, (len(attempts) + HISTORY_PAGE_SIZE - 1) // HISTORY_PAGE_SIZE)
    page = max(0, min(page, pages - 1))

    chunk = attempts[
        page * HISTORY_PAGE_SIZE : (page + 1) * HISTORY_PAGE_SIZE
    ]

    lines = [
        "\U0001F4C4 <b>To'liq tarix</b>",
        "\U0001F464 " + html.escape(report.full_name),
        "",
    ]

    attempt_buttons = []

    for number, attempt in enumerate(
        chunk, start=page * HISTORY_PAGE_SIZE + 1
    ):
        lines.append(
            f"<b>{number}.</b> "
            + fmt_datetime(attempt.created_at)
            + "\n     "
            + html.escape(short(attempt.subject_name, 18))
            + " · "
            + html.escape(short(attempt.topic_name, 22))
            + f"\n     {bar(attempt.accuracy)} "
            f"{attempt.correct}/{attempt.total} ({attempt.accuracy}%)"
        )

        attempt_buttons.append(
            InlineKeyboardButton(
                text=str(number),
                callback_data=f"rep:a:{attempt.attempt_id}:0",
            )
        )

    lines.append("")
    lines.append(
        "<i>Savollarni ko'rish uchun pastdagi raqamni bosing.</i>"
    )
    lines.append(f"<i>Sahifa {page + 1}/{pages}</i>")

    nav = []

    if page > 0:
        nav.append(
            InlineKeyboardButton(
                text="◀️",
                callback_data=f"rep:h:{telegram_id}:{page - 1}",
            )
        )

    if page + 1 < pages:
        nav.append(
            InlineKeyboardButton(
                text="▶️",
                callback_data=f"rep:h:{telegram_id}:{page + 1}",
            )
        )

    buttons = []

    # Raqamli tugmalar: har qatorda 5 tadan
    for start in range(0, len(attempt_buttons), 5):
        buttons.append(attempt_buttons[start : start + 5])

    if nav:
        buttons.append(nav)

    buttons.append(
        [
            InlineKeyboardButton(
                text="⬅️ O'quvchi kartasi",
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


@router.message(Command("natijalar"))
async def reports_command(m: Message):
    if not is_full_admin(m.from_user.id):
        return

    await render_list(m, SORT_ACTIVITY, 0, edit=False)


# ---------------------------------------------------------------
#  4-ekran: bitta testning javoblari
# ---------------------------------------------------------------

ANSWERS_PAGE_SIZE = 8


def question_label(text, has_image, limit=55) -> str:
    body = (text or "").strip()

    if not body:
        return "\U0001F5BC <i>rasmli savol</i>" if has_image else "<i>savol matni yo'q</i>"

    return html.escape(short(body, limit))


def answer_line(number: int, answer) -> str:
    icon = "\u2705" if answer.is_correct else "\u274C"

    lines = [
        f"{number}. {icon} " + question_label(answer.text, answer.has_image)
    ]

    chosen = (answer.selected or "").upper()
    chosen_text = answer.option_text(chosen)

    if chosen_text:
        lines.append(
            f"     javob: <b>{chosen}</b>) "
            + html.escape(short(chosen_text, 28))
        )
    else:
        lines.append(f"     javob: <b>{chosen or '—'}</b>")

    if not answer.is_correct:
        right = (answer.correct_option or "").upper()
        right_text = answer.option_text(right)

        if right_text:
            lines.append(
                f"     to'g'risi: <b>{right}</b>) "
                + html.escape(short(right_text, 28))
            )
        else:
            lines.append(f"     to'g'risi: <b>{right or '—'}</b>")

    return "\n".join(lines)


@router.callback_query(F.data.startswith("rep:a:"))
async def reports_attempt(c: CallbackQuery):
    if not is_full_admin(c.from_user.id):
        await c.answer("Ruxsat yo'q.", show_alert=True)
        return

    try:
        _, _, id_text, page_text = c.data.split(":")
        attempt_id = int(id_text)
        page = int(page_text)
    except ValueError:
        await c.answer("Noto'g'ri so'rov.", show_alert=True)
        return

    await c.answer()

    async with SessionLocal() as session:
        detail = await attempt_detail(session, attempt_id)

    if detail is None:
        await c.answer("Test topilmadi.", show_alert=True)
        return

    answers = detail.answers
    pages = max(
        1,
        (len(answers) + ANSWERS_PAGE_SIZE - 1) // ANSWERS_PAGE_SIZE,
    )
    page = max(0, min(page, pages - 1))

    chunk = answers[
        page * ANSWERS_PAGE_SIZE : (page + 1) * ANSWERS_PAGE_SIZE
    ]

    lines = [
        "\U0001F4C4 <b>Test tafsiloti</b>",
        "\U0001F464 " + html.escape(detail.student_name),
        "\U0001F4D6 "
        + html.escape(short(detail.subject_name, 18))
        + " \u00B7 "
        + html.escape(short(detail.topic_name, 22)),
        "\U0001F553 " + fmt_datetime(detail.created_at),
        f"\U0001F3AF Natija: <b>{detail.correct}/{detail.total}</b> "
        f"({detail.accuracy}%)",
        "",
    ]

    if not answers:
        lines.append(
            "<i>Bu test uchun javoblar saqlanmagan.</i>"
        )
    else:
        start = page * ANSWERS_PAGE_SIZE

        for offset, answer in enumerate(chunk, start=start + 1):
            lines.append(answer_line(offset, answer))
            lines.append("")

        if any(answer.has_image for answer in chunk):
            lines.append(
                "<i>\U0001F5BC belgili tugmani bossangiz, "
                "savol rasmi yuboriladi.</i>"
            )

        if pages > 1:
            lines.append(f"<i>Sahifa {page + 1}/{pages}</i>")

    nav = []

    if page > 0:
        nav.append(
            InlineKeyboardButton(
                text="\u25C0\uFE0F",
                callback_data=f"rep:a:{attempt_id}:{page - 1}",
            )
        )

    if page + 1 < pages:
        nav.append(
            InlineKeyboardButton(
                text="\u25B6\uFE0F",
                callback_data=f"rep:a:{attempt_id}:{page + 1}",
            )
        )

    buttons = []

    image_buttons = []
    start_number = page * ANSWERS_PAGE_SIZE + 1

    for offset, answer in enumerate(chunk, start=start_number):
        if answer.has_image:
            image_buttons.append(
                InlineKeyboardButton(
                    text=f"\U0001F5BC {offset}",
                    callback_data=f"rep:img:{answer.question_id}",
                )
            )

    for start in range(0, len(image_buttons), 5):
        buttons.append(image_buttons[start : start + 5])

    if nav:
        buttons.append(nav)

    buttons.append(
        [
            InlineKeyboardButton(
                text="\u2B05\uFE0F Tarix",
                callback_data=f"rep:h:{detail.student_telegram_id}:0",
            )
        ]
    )
    buttons.append(
        [
            InlineKeyboardButton(
                text="\U0001F464 O'quvchi kartasi",
                callback_data=f"rep:u:{detail.student_telegram_id}",
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


# ---------------------------------------------------------------
#  5-ekran: ko'p xato qilgan savollar
# ---------------------------------------------------------------

@router.callback_query(F.data.startswith("rep:w:"))
async def reports_wrong(c: CallbackQuery):
    if not is_full_admin(c.from_user.id):
        await c.answer("Ruxsat yo'q.", show_alert=True)
        return

    try:
        telegram_id = int(c.data.split(":")[-1])
    except ValueError:
        await c.answer("Noto'g'ri so'rov.", show_alert=True)
        return

    await c.answer("Hisoblanmoqda...")

    async with SessionLocal() as session:
        items = await wrong_questions(session, telegram_id, limit=10)
        report = await student_report(session, telegram_id)

    name = report.full_name if report else "O'quvchi"

    lines = [
        "\u274C <b>Ko'p xato qilgan savollar</b>",
        "\U0001F464 " + html.escape(name),
        "",
    ]

    if not items:
        lines.append(
            "<i>Xato javoblar yo'q \u2014 yoki hali test ishlamagan.</i>"
        )
    else:
        for number, item in enumerate(items, start=1):
            lines.append(
                f"{number}. <b>{item.wrong} marta xato</b> "
                f"({item.total} tadan) \u00B7 "
                + html.escape(short(item.topic_name, 20))
            )
            lines.append(
                "     " + question_label(item.text, item.has_image, 60)
            )
            lines.append("")

        lines.append(
            "<i>Bu savollar Smart Takrorlash navbatida ham turadi.</i>"
        )

    buttons = []

    image_buttons = [
        InlineKeyboardButton(
            text=f"\U0001F5BC {number}",
            callback_data=f"rep:img:{item.question_id}",
        )
        for number, item in enumerate(items, start=1)
        if item.has_image
    ]

    for start in range(0, len(image_buttons), 5):
        buttons.append(image_buttons[start : start + 5])

    buttons += [
        [
            InlineKeyboardButton(
                text="\U0001F464 O'quvchi kartasi",
                callback_data=f"rep:u:{telegram_id}",
            )
        ],
        [
            InlineKeyboardButton(
                text="\u2699\uFE0F Admin panel",
                callback_data="admin",
            )
        ],
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
#  Savol rasmini yuborish
# ---------------------------------------------------------------

@router.callback_query(F.data.startswith("rep:img:"))
async def reports_question_image(c: CallbackQuery):
    if not is_full_admin(c.from_user.id):
        await c.answer("Ruxsat yo'q.", show_alert=True)
        return

    try:
        question_id = int(c.data.split(":")[-1])
    except ValueError:
        await c.answer("Noto'g'ri so'rov.", show_alert=True)
        return

    async with SessionLocal() as session:
        item = await question_image(session, question_id)

    if item is None:
        await c.answer("Savol topilmadi.", show_alert=True)
        return

    if not item.image_path:
        await c.answer("Bu savolda rasm yo'q.", show_alert=True)
        return

    path = Path(item.image_path)

    if not path.is_file():
        await c.answer(
            "Rasm fayli topilmadi. Ehtimol u eski deploydan oldin "
            "yuklangan va saqlanmagan.",
            show_alert=True,
        )
        return

    await c.answer()

    caption_lines = [
        f"\U0001F5BC <b>Savol #{item.question_id}</b>",
        "\U0001F4D6 " + html.escape(short(item.topic_name, 24)),
    ]

    body = (item.text or "").strip()

    if body:
        caption_lines.append("")
        caption_lines.append(html.escape(short(body, 300)))

    if item.correct_option:
        caption_lines.append("")
        caption_lines.append(
            "\u2705 To'g'ri javob: <b>"
            + html.escape(item.correct_option)
            + "</b>"
        )

    try:
        await c.message.answer_photo(
            photo=FSInputFile(path),
            caption="\n".join(caption_lines),
        )
    except Exception as exc:
        log.warning("Savol rasmi yuborilmadi (#%s): %s", question_id, exc)

        await c.message.answer(
            "\u26A0\uFE0F Rasmni yuborib bo'lmadi."
        )

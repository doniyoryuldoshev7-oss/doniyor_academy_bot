"""Diagnostik test — ekranlar.

Joylashuvi: app/handlers/diagnostic.py

Foydalanuvchi qaysi fanlardan va nechta savol olishni o'zi
belgilaydi. Savollarni mavzularga taqsimlashni algoritm bajaradi
(app/services/diagnostic.py).
"""

from __future__ import annotations

import html
import logging
import os
import re
from pathlib import Path

from aiogram import F, Router
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from sqlalchemy import select

from ..db import SessionLocal
from ..models import Question, Subject, Topic, User
from ..services.diagnostic import (
    MIN_FOR_VERDICT,
    build_plan,
    save_diagnostic,
    subject_capacity,
    topic_capacity,
)

log = logging.getLogger(__name__)

router = Router()

CAPTION_LIMIT = 900
STEP = 5
GROUP_LIMIT = 10


def natural_key(name: str):
    parts = re.split(r"(\d+)", (name or "").casefold())
    return tuple(
        (0, int(part)) if part.isdigit() else (1, part)
        for part in parts
    )


def short(name: str, limit: int = 24) -> str:
    name = (name or "").strip()

    if len(name) <= limit:
        return name

    return name[: limit - 1].rstrip() + "…"


def home_button():
    return [
        InlineKeyboardButton(
            text="\U0001F3E0 Bosh menyu",
            callback_data="home",
        )
    ]


async def replace_text(c, text, keyboard=None):
    message = c.message

    is_caption = bool(getattr(message, "photo", None)) or (
        getattr(message, "caption", None) is not None
    )

    try:
        if is_caption:
            await message.edit_caption(caption=text, reply_markup=keyboard)
        else:
            await message.edit_text(text, reply_markup=keyboard)

        return
    except Exception as exc:
        if "message is not modified" in str(exc):
            return

    await message.answer(text, reply_markup=keyboard)


async def show_expired(c):
    await replace_text(
        c,
        "\U0001F3AF <b>DIAGNOSTIKA</b>\n\n"
        "<i>Bu seans endi faol emas — ehtimol bot qayta ishga "
        "tushgan. Qaytadan boshlang.</i>",
        InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="\U0001F504 Qaytadan boshlash",
                        callback_data="diag:menu",
                    )
                ],
                home_button(),
            ]
        ),
    )


# ---------------------------------------------------------------
#  1-ekran: fanlar va savollar soni
# ---------------------------------------------------------------

async def render_setup(c, state):
    data = await state.get_data()
    budgets = dict(data.get("diag_budgets") or {})
    chosen_topics = list(data.get("diag_filter") or [])

    async with SessionLocal() as session:
        capacity = await subject_capacity(session)

    if not capacity:
        await replace_text(
            c,
            "\U0001F3AF <b>DIAGNOSTIKA</b>\n\n"
            "<i>Hozircha diagnostika uchun savol yo'q.</i>",
            InlineKeyboardMarkup(inline_keyboard=[home_button()]),
        )
        return

    items = sorted(
        capacity.items(),
        key=lambda kv: natural_key(kv[1]["name"]),
    )

    rows = []
    total = 0
    active_subjects = []

    for subject_id, info in items:
        count = int(budgets.get(str(subject_id), 0))
        count = max(0, min(count, info["count"]))
        total += count

        if count:
            active_subjects.append(subject_id)

        label = f"\U0001F4DA {short(info['name'], 18)} · {count} ta"

        rows.append(
            [
                InlineKeyboardButton(
                    text="➖",
                    callback_data=f"diag:dec:{subject_id}",
                ),
                InlineKeyboardButton(
                    text=label,
                    callback_data=f"diag:zero:{subject_id}",
                ),
                InlineKeyboardButton(
                    text="➕",
                    callback_data=f"diag:inc:{subject_id}",
                ),
            ]
        )

    if len(active_subjects) == 1:
        rows.append(
            [
                InlineKeyboardButton(
                    text="\U0001F4D6 Mavzularni tanlash"
                    + (f" ({len(chosen_topics)})" if chosen_topics else ""),
                    callback_data=f"diag:tp:{active_subjects[0]}",
                )
            ]
        )

    if total:
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"▶️ Boshlash — {total} ta savol",
                    callback_data="diag:go",
                )
            ]
        )

    rows.append([InlineKeyboardButton(text="⬅️ Orqaga", callback_data="mn:test")])
    rows.append(home_button())

    lines = [
        "\U0001F3AF <b>DIAGNOSTIKA</b>",
        "",
        "Qaysi fandan nechta savol olishni o'zingiz belgilang. "
        "Bir nechta fanni birga tanlash mumkin.",
        "",
    ]

    for subject_id, info in items:
        count = int(budgets.get(str(subject_id), 0))

        lines.append(
            f"\U0001F4DA {html.escape(info['name'])} — "
            f"<b>{count}</b> ta "
            f"<i>(mavjud: {info['count']})</i>"
        )

    lines.append("")

    if total:
        lines.append(f"\U0001F522 Jami: <b>{total}</b> ta savol")
    else:
        lines.append(
            "<i>➕ tugmasi bilan savol sonini belgilang.</i>"
        )

    lines.append("")
    lines.append(
        "<i>Savollar mavzularga o'zi taqsimlanadi: zaif va hali "
        "ishlanmagan mavzulardan ko'proq, mustahkamlaridan "
        "kamroq.</i>"
    )

    await replace_text(
        c,
        "\n".join(lines),
        InlineKeyboardMarkup(inline_keyboard=rows),
    )


@router.callback_query(F.data == "diag:menu")
async def diagnostic_menu(c: CallbackQuery, state):
    await c.answer()
    await render_setup(c, state)


@router.callback_query(F.data.startswith("diag:inc:"))
async def diagnostic_inc(c: CallbackQuery, state):
    await adjust(c, state, c.data.split(":")[-1], STEP)


@router.callback_query(F.data.startswith("diag:dec:"))
async def diagnostic_dec(c: CallbackQuery, state):
    await adjust(c, state, c.data.split(":")[-1], -STEP)


@router.callback_query(F.data.startswith("diag:zero:"))
async def diagnostic_zero(c: CallbackQuery, state):
    await adjust(c, state, c.data.split(":")[-1], None)


async def adjust(c, state, raw_id: str, delta):
    try:
        subject_id = int(raw_id)
    except ValueError:
        await c.answer("Noto'g'ri so'rov.", show_alert=True)
        return

    async with SessionLocal() as session:
        capacity = await subject_capacity(session)

    info = capacity.get(subject_id)

    if info is None:
        await c.answer("Fan topilmadi.", show_alert=True)
        return

    data = await state.get_data()
    budgets = dict(data.get("diag_budgets") or {})
    key = str(subject_id)

    current = int(budgets.get(key, 0))

    if delta is None:
        new_value = 0
    else:
        new_value = current + delta

        if current == 0 and delta > 0:
            new_value = min(STEP, info["count"])

    new_value = max(0, min(new_value, info["count"]))
    budgets[key] = new_value

    update = {"diag_budgets": budgets}

    # Fan o'chirilsa yoki ikkinchi fan qo'shilsa, mavzu filtri
    # ma'nosini yo'qotadi
    active = [k for k, v in budgets.items() if v]

    if len(active) != 1:
        update["diag_filter"] = []

    await state.update_data(**update)

    if delta is not None and new_value == current:
        await c.answer(
            "Chegaraga yetdi."
            if new_value
            else "Bu fanda savol yo'q.",
        )
    else:
        await c.answer()

    await render_setup(c, state)


# ---------------------------------------------------------------
#  Mavzularni tanlash (ixtiyoriy, bitta fan tanlanganda)
# ---------------------------------------------------------------

async def render_topics(c, state, subject_id: int):
    data = await state.get_data()
    selected = set(data.get("diag_filter") or [])

    async with SessionLocal() as session:
        subject = await session.get(Subject, subject_id)

        topics = list(
            await session.scalars(
                select(Topic).where(Topic.subject_id == subject_id)
            )
        )

        counts = await topic_capacity(session, subject_id)

    topics = [t for t in topics if counts.get(t.id)]

    if not topics:
        await c.answer("Bu fanda savolli mavzu yo'q.", show_alert=True)
        return

    topics.sort(key=lambda t: natural_key(t.name))

    rows = []

    for topic in topics:
        mark = "✅" if topic.id in selected else "⬜"

        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{mark} {short(topic.name, 20)} "
                    f"({counts[topic.id]})",
                    callback_data=f"diag:tx:{topic.id}",
                )
            ]
        )

    rows.append(
        [
            InlineKeyboardButton(
                text="✔️ Tayyor",
                callback_data="diag:menu",
            )
        ]
    )

    await replace_text(
        c,
        "\U0001F3AF <b>DIAGNOSTIKA</b>\n\n"
        f"\U0001F4DA {html.escape(subject.name if subject else '')}\n\n"
        "Kerakli mavzularni belgilang. Hech biri belgilanmasa, "
        "barcha mavzular hisobga olinadi.",
        InlineKeyboardMarkup(inline_keyboard=rows),
    )


@router.callback_query(F.data.startswith("diag:tp:"))
async def diagnostic_topics(c: CallbackQuery, state):
    try:
        subject_id = int(c.data.split(":")[-1])
    except ValueError:
        await c.answer("Noto'g'ri so'rov.", show_alert=True)
        return

    await c.answer()
    await render_topics(c, state, subject_id)


@router.callback_query(F.data.startswith("diag:tx:"))
async def diagnostic_toggle_topic(c: CallbackQuery, state):
    try:
        topic_id = int(c.data.split(":")[-1])
    except ValueError:
        await c.answer("Noto'g'ri so'rov.", show_alert=True)
        return

    async with SessionLocal() as session:
        topic = await session.get(Topic, topic_id)

    if topic is None:
        await c.answer("Mavzu topilmadi.", show_alert=True)
        return

    data = await state.get_data()
    selected = list(data.get("diag_filter") or [])

    if topic_id in selected:
        selected.remove(topic_id)
    else:
        selected.append(topic_id)

    await state.update_data(diag_filter=selected)
    await c.answer()
    await render_topics(c, state, topic.subject_id)


# ---------------------------------------------------------------
#  Savolni ko'rsatish
# ---------------------------------------------------------------

def question_text(question, index: int, total: int) -> str:
    body = html.escape(question.text or "").strip()

    options = []

    for key in ("a", "b", "c", "d"):
        value = html.escape(
            getattr(question, f"option_{key}", None) or ""
        ).strip()

        if value:
            options.append(f"<b>{key.upper()})</b> {value}")

    if not body:
        body = (
            "<i>Savol rasmda ko'rsatilgan.</i>"
            if question.image_path
            else "<i>Savol matni yo'q.</i>"
        )

    parts = [
        "\U0001F3AF <b>DIAGNOSTIKA</b>",
        f"❓ <b>Savol {index + 1}/{total}</b>",
        body,
    ]

    if options:
        parts.append("\n".join(options))
    else:
        parts.append(
            "<i>Javob variantlari rasmda — "
            "A, B, C yoki D ni tanlang.</i>"
        )

    return "\n\n".join(parts)


def answer_keyboard(question_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="A", callback_data=f"diag:a:{question_id}:A"
                ),
                InlineKeyboardButton(
                    text="B", callback_data=f"diag:a:{question_id}:B"
                ),
            ],
            [
                InlineKeyboardButton(
                    text="C", callback_data=f"diag:a:{question_id}:C"
                ),
                InlineKeyboardButton(
                    text="D", callback_data=f"diag:a:{question_id}:D"
                ),
            ],
            [
                InlineKeyboardButton(
                    text="❌ To'xtatish",
                    callback_data="diag:stop",
                )
            ],
        ]
    )


async def show_question(c, question, index: int, total: int):
    text = question_text(question, index, total)
    keyboard = answer_keyboard(question.id)

    raw = question.image_path
    path = Path(raw) if raw else None

    if path is None or not path.is_file():
        if raw:
            text += "\n\n<i>⚠️ Rasm topilmadi.</i>"

        await replace_text(c, text, keyboard)
        return

    try:
        await c.message.delete()
    except Exception:
        try:
            await c.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass

    if len(text) <= CAPTION_LIMIT:
        try:
            await c.message.answer_photo(
                photo=FSInputFile(path),
                caption=text,
                reply_markup=keyboard,
            )
            return
        except Exception:
            pass

    try:
        await c.message.answer_photo(photo=FSInputFile(path))
    except Exception:
        text += "\n\n<i>⚠️ Rasmni yuborib bo'lmadi.</i>"

    await c.message.answer(text, reply_markup=keyboard)


# ---------------------------------------------------------------
#  Boshlash
# ---------------------------------------------------------------

@router.callback_query(F.data == "diag:go")
async def diagnostic_start(c: CallbackQuery, state):
    data = await state.get_data()
    budgets = dict(data.get("diag_budgets") or {})
    topic_filter = list(data.get("diag_filter") or [])

    budgets = {int(k): int(v) for k, v in budgets.items() if int(v) > 0}

    if not budgets:
        await c.answer("Avval savol sonini belgilang.", show_alert=True)
        return

    await c.answer("Savollar tanlanmoqda...")

    async with SessionLocal() as session:
        user = await session.scalar(
            select(User).where(User.telegram_id == c.from_user.id)
        )

        if user is None:
            await c.answer(
                "Avval ro'yxatdan o'ting: /start",
                show_alert=True,
            )
            return

        plan = await build_plan(
            session,
            user_id=user.id,
            budgets=budgets,
            topic_ids=topic_filter or None,
        )

    if not plan.questions:
        await replace_text(
            c,
            "\U0001F3AF <b>DIAGNOSTIKA</b>\n\n"
            "<i>Tanlovingiz bo'yicha savol topilmadi. Boshqa fan "
            "yoki mavzu tanlab ko'ring.</i>",
            InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="⬅️ Sozlash",
                            callback_data="diag:menu",
                        )
                    ],
                    home_button(),
                ]
            ),
        )
        return

    info = {}

    for topic_plan in plan.topics:
        info[str(topic_plan.topic_id)] = [
            topic_plan.topic_name,
            topic_plan.subject_name,
        ]

    skipped = [
        [topic_plan.topic_name, topic_plan.subject_name]
        for topic_plan in plan.skipped
    ]

    await state.clear()
    await state.update_data(
        diag_qids=[q.id for q in plan.questions],
        diag_topic_ids=[q.topic_id for q in plan.questions],
        diag_info=info,
        diag_skipped=skipped,
        diag_index=0,
        diag_answers=[],
        diag_wait=True,
    )

    await show_question(c, plan.questions[0], 0, len(plan.questions))


# ---------------------------------------------------------------
#  Javob
# ---------------------------------------------------------------

async def next_valid_question(session, qids: list[int], index: int):
    from ..services.diagnostic import usable

    while index < len(qids):
        question = await session.get(Question, qids[index])

        if question is not None and question.is_active and usable(question):
            return question, index

        index += 1

    return None, index


@router.callback_query(F.data.startswith("diag:a:"))
async def diagnostic_answer(c: CallbackQuery, state):
    try:
        _, _, qid_text, option = c.data.split(":")
        qid = int(qid_text)
    except ValueError:
        await c.answer("Noto'g'ri so'rov.", show_alert=True)
        return

    if option not in ("A", "B", "C", "D"):
        await c.answer("Noto'g'ri variant.", show_alert=True)
        return

    data = await state.get_data()

    qids = data.get("diag_qids") or []
    topic_ids = data.get("diag_topic_ids") or []
    info = data.get("diag_info") or {}
    index = data.get("diag_index", 0)

    if not qids or not data.get("diag_wait"):
        await c.answer()
        await show_expired(c)
        return

    if index >= len(qids) or qids[index] != qid:
        await c.answer("Bu savol allaqachon javoblangan.", show_alert=True)
        return

    answers = list(data.get("diag_answers") or [])

    # Savolni darhol "band" qilamiz: ikki marta tez bosilsa ham
    # javob bir marta hisoblanadi (natija ikki marta saqlanmaydi).
    await state.update_data(diag_index=index + 1)

    async with SessionLocal() as session:
        question = await session.get(Question, qid)

        correct_option = (
            (question.correct_option or "").strip().upper()
            if question is not None
            else ""
        )

        if question is not None and correct_option in ("A", "B", "C", "D"):

            topic_id = (
                topic_ids[index]
                if index < len(topic_ids)
                else question.topic_id
            )

            names = info.get(str(topic_id)) or ["—", "—"]

            answers.append(
                {
                    "qid": qid,
                    "topic_id": topic_id,
                    "topic_name": names[0],
                    "subject_name": names[1],
                    "selected": option,
                    "correct": option == correct_option,
                }
            )
        else:
            log.info("Diagnostika: savol topilmadi (#%s)", qid)

    index += 1

    if index < len(qids):
        async with SessionLocal() as session:
            next_question, index = await next_valid_question(
                session, qids, index
            )

        if next_question is not None:
            await state.update_data(
                diag_index=index,
                diag_answers=answers,
            )

            await show_question(c, next_question, index, len(qids))
            await c.answer()
            return

    await state.update_data(diag_wait=False)
    await c.answer("Natija hisoblanmoqda...")

    if not answers:
        await state.clear()
        await show_expired(c)
        return

    skipped = data.get("diag_skipped") or []

    async with SessionLocal() as session:
        user = await session.scalar(
            select(User).where(User.telegram_id == c.from_user.id)
        )

        if user is None:
            await state.clear()

            await replace_text(
                c,
                "\U0001F3AF <b>DIAGNOSTIKA</b>\n\n"
                "<i>Natijani saqlab bo'lmadi: hisobingiz topilmadi. "
                "/start orqali ro'yxatdan o'ting.</i>",
                InlineKeyboardMarkup(inline_keyboard=[home_button()]),
            )
            return

        try:
            result = await save_diagnostic(
                session, user, answers, skipped=skipped
            )
        except Exception as exc:
            log.exception("Diagnostika saqlanmadi: %s", exc)

            await session.rollback()
            await state.clear()

            await replace_text(
                c,
                "\U0001F3AF <b>DIAGNOSTIKA</b>\n\n"
                "⚠️ <i>Natijani saqlashda xato yuz berdi.</i>",
                InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="\U0001F504 Qaytadan",
                                callback_data="diag:menu",
                            )
                        ],
                        home_button(),
                    ]
                ),
            )
            return

    await state.clear()
    await show_result(c, result)


# ---------------------------------------------------------------
#  Natija
# ---------------------------------------------------------------

def group_lines(topics, icon: str, title: str) -> list:
    if not topics:
        return []

    lines = [f"{icon} <b>{title}</b>"]

    # Telegram xabari 4096 belgidan oshmasligi uchun ro'yxat qisqartiriladi
    hidden = max(0, len(topics) - GROUP_LIMIT)

    for topic in topics[:GROUP_LIMIT]:
        lines.append(
            f"  • {html.escape(short(topic.topic_name))} — "
            f"<b>{topic.accuracy}%</b> "
            f"<i>({topic.correct}/{topic.total})</i>"
        )

    if hidden:
        lines.append(f"  <i>... va yana {hidden} ta mavzu</i>")

    lines.append("")

    return lines


async def show_result(c, result):
    lines = [
        "\U0001F3AF <b>DIAGNOSTIKA NATIJASI</b>",
        "",
        f"\U0001F4DA {html.escape(result.title)}",
        f"\U0001F3AF Umumiy: <b>{result.correct}/{result.total}</b> "
        f"({result.accuracy}%)",
        "",
    ]

    lines += group_lines(result.strong, "\U0001F7E2", "Kuchli mavzular")
    lines += group_lines(result.medium, "\U0001F7E1", "O'rtacha")
    lines += group_lines(result.weak, "\U0001F534", "Zaif mavzular")

    if result.unclear:
        lines.append("⚪ <b>Aniq baho uchun savol kam edi</b>")

        for topic in result.unclear[:GROUP_LIMIT]:
            lines.append(
                f"  • {html.escape(short(topic.topic_name))} — "
                f"{topic.correct}/{topic.total}"
            )

        lines.append(
            f"<i>Baho chiqishi uchun mavzudan kamida "
            f"{MIN_FOR_VERDICT} ta savol kerak.</i>"
        )
        lines.append("")

    if result.skipped:
        names = [
            html.escape(short(item[0]))
            for item in result.skipped[:8]
        ]

        lines.append("\U0001F4CB <b>Bu safar tekshirilmadi</b>")
        lines.append("  " + ", ".join(names))

        if len(result.skipped) > 8:
            lines.append(
                f"  <i>... va yana {len(result.skipped) - 8} ta mavzu</i>"
            )

        lines.append(
            "<i>Savol sonini oshirsangiz, ular ham qamrab olinadi.</i>"
        )
        lines.append("")

    buttons = []

    nxt = result.next_topic

    if nxt is not None:
        if nxt.level == "unclear":
            lines.append(
                "\U0001F4A1 <b>Tavsiya:</b> "
                + html.escape(short(nxt.topic_name))
                + " bo'yicha savol kam bo'ldi. Aniq baho uchun shu "
                "mavzuni ishlab ko'ring."
            )
        else:
            lines.append(
                "\U0001F4A1 <b>Tavsiya:</b> "
                + html.escape(short(nxt.topic_name))
                + " dan boshlang."
            )

        buttons.append(
            [
                InlineKeyboardButton(
                    text=f"▶️ {short(nxt.topic_name, 20)} testi",
                    callback_data=f"topic:{nxt.topic_id}",
                )
            ]
        )
    elif result.topics:
        lines.append(
            "\U0001F389 <b>Tekshirilgan mavzular yaxshi "
            "o'zlashtirilgan.</b>"
        )

    lines.append("")
    if result.correct < result.total and os.getenv(
        "SMART_REVIEW_ENABLED", "0"
    ) == "1":
        lines.append(
            "<i>Xato javoblar Smart Takrorlash navbatiga qo'shildi.</i>"
        )

    buttons.append(
        [
            InlineKeyboardButton(
                text="\U0001F504 Yana diagnostika",
                callback_data="diag:menu",
            )
        ]
    )
    buttons.append(
        [
            InlineKeyboardButton(
                text="\U0001F4C8 Bilim xaritasi",
                callback_data="km:menu",
            )
        ]
    )
    buttons.append(home_button())

    await replace_text(
        c,
        "\n".join(lines),
        InlineKeyboardMarkup(inline_keyboard=buttons),
    )


@router.callback_query(F.data == "diag:stop")
async def diagnostic_stop(c: CallbackQuery, state):
    await state.clear()

    await replace_text(
        c,
        "\U0001F3AF <b>DIAGNOSTIKA</b>\n\n"
        "<i>To'xtatildi. Javoblar saqlanmadi — diagnostika "
        "oxirigacha ishlanishi kerak.</i>",
        InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="\U0001F504 Qaytadan",
                        callback_data="diag:menu",
                    )
                ],
                home_button(),
            ]
        ),
    )
    await c.answer()

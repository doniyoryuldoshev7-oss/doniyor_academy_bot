"""Diagnostik test — o'quvchi ekranlari.

Joylashuvi: app/handlers/diagnostic.py

Bosh menyudagi "\U0001F3AF Diagnostika" tugmasi:
    fan -> bir necha mavzudan qisqa test -> mavzular kesimida natija.
"""

from __future__ import annotations

import html
import logging
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
    MIN_QUESTIONS,
    build_diagnostic,
    save_diagnostic,
)

log = logging.getLogger(__name__)

router = Router()

CAPTION_LIMIT = 900


def natural_key(name: str):
    import re

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


def home_button():
    return [
        InlineKeyboardButton(
            text="\U0001F3E0 Bosh menyu",
            callback_data="home",
        )
    ]


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


async def next_valid_question(session, qids: list[int], index: int):
    """Keyingi mavjud savolni qaytaradi.

    Savol o'chirilgan yoki yashirilgan bo'lsa, o'tkazib yuboriladi —
    aks holda test o'sha joyda qotib qolardi.
    """
    from ..services.diagnostic import reviewable

    while index < len(qids):
        question = await session.get(Question, qids[index])

        if question is not None and question.is_active and reviewable(question):
            return question, index

        index += 1

    return None, index


async def show_expired(c):
    """Seans eskirgan (bot qayta ishga tushgan) holat."""
    await replace_text(
        c,
        "\U0001F3AF <b>DIAGNOSTIKA</b>\n\n"
        "<i>Bu seans endi faol emas — ehtimol bot qayta ishga "
        "tushgan. Diagnostikani qaytadan boshlang.</i>",
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
#  1-ekran: fan tanlash
# ---------------------------------------------------------------

@router.callback_query(F.data == "diag:menu")
async def diagnostic_menu(c: CallbackQuery, state):
    async with SessionLocal() as session:
        subjects = list(
            await session.scalars(
                select(Subject).where(Subject.is_active.is_(True))
            )
        )

    if not subjects:
        await c.answer("Fanlar topilmadi.", show_alert=True)
        return

    buttons = [
        [
            InlineKeyboardButton(
                text="\U0001F4DA " + short(s.name),
                callback_data=f"diag:s:{s.id}",
            )
        ]
        for s in subjects
    ]

    buttons.append(home_button())

    await replace_text(
        c,
        "\U0001F3AF <b>DIAGNOSTIKA</b>\n\n"
        "Bir necha mavzudan qisqa test. Yakunida qaysi mavzu "
        "kuchli, qaysi biri zaif ekani ko'rinadi.\n\n"
        "Fanni tanlang:",
        InlineKeyboardMarkup(inline_keyboard=buttons),
    )
    await c.answer()


# ---------------------------------------------------------------
#  2-ekran: testni boshlash
# ---------------------------------------------------------------

@router.callback_query(F.data.startswith("diag:s:"))
async def diagnostic_subject(c: CallbackQuery, state):
    try:
        subject_id = int(c.data.split(":")[-1])
    except ValueError:
        await c.answer("Noto'g'ri so'rov.", show_alert=True)
        return

    await c.answer()
    await state.update_data(diag_pick=[], diag_pick_subject=subject_id)

    async with SessionLocal() as session:
        subject = await session.get(Subject, subject_id)

    if subject is None:
        await c.answer("Fan topilmadi.", show_alert=True)
        return

    await replace_text(
        c,
        "\U0001F3AF <b>DIAGNOSTIKA</b>\n\n"
        f"\U0001F4DA {html.escape(subject.name)}\n\n"
        "Butun fan bo'yicha tekshiramizmi yoki aniq mavzularni "
        "tanlaysizmi?\n\n"
        "<i>Savollar zaif o'zlashtirilgan mavzulardan ko'proq, "
        "mustahkamlaridan kamroq olinadi. Hali ishlanmagan "
        "savollarga ustunlik beriladi.</i>",
        InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="\u25B6\uFE0F Hamma mavzudan",
                        callback_data=f"diag:go:{subject_id}",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="\U0001F4D6 Mavzularni tanlash",
                        callback_data=f"diag:p:{subject_id}",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="\u2B05\uFE0F Fanlar",
                        callback_data="diag:menu",
                    )
                ],
                home_button(),
            ]
        ),
    )


async def render_picker(c, state, subject_id: int):
    data = await state.get_data()
    selected = set(data.get("diag_pick") or [])

    async with SessionLocal() as session:
        subject = await session.get(Subject, subject_id)

        topics = list(
            await session.scalars(
                select(Topic).where(Topic.subject_id == subject_id)
            )
        )

    if not topics:
        await c.answer("Bu fanda mavzu yo'q.", show_alert=True)
        return

    topics.sort(key=lambda t: natural_key(t.name))

    buttons = []

    for topic in topics:
        mark = "\u2705" if topic.id in selected else "\u2B1C"

        buttons.append(
            [
                InlineKeyboardButton(
                    text=f"{mark} {short(topic.name, 24)}",
                    callback_data=f"diag:x:{topic.id}",
                )
            ]
        )

    buttons.append(
        [
            InlineKeyboardButton(
                text="\u25B6\uFE0F Boshlash"
                + (f" ({len(selected)} mavzu)" if selected else " (hammasi)"),
                callback_data=f"diag:go:{subject_id}",
            )
        ]
    )
    buttons.append(
        [
            InlineKeyboardButton(
                text="\u2B05\uFE0F Orqaga",
                callback_data=f"diag:s:{subject_id}",
            )
        ]
    )

    await replace_text(
        c,
        "\U0001F3AF <b>DIAGNOSTIKA</b>\n\n"
        f"\U0001F4DA {html.escape(subject.name if subject else '')}\n\n"
        "Mavzularni belgilang. Hech biri belgilanmasa, "
        "barcha mavzular olinadi.",
        InlineKeyboardMarkup(inline_keyboard=buttons),
    )


@router.callback_query(F.data.startswith("diag:p:"))
async def diagnostic_picker(c: CallbackQuery, state):
    try:
        subject_id = int(c.data.split(":")[-1])
    except ValueError:
        await c.answer("Noto'g'ri so'rov.", show_alert=True)
        return

    await c.answer()
    await render_picker(c, state, subject_id)


@router.callback_query(F.data.startswith("diag:x:"))
async def diagnostic_toggle(c: CallbackQuery, state):
    try:
        topic_id = int(c.data.split(":")[-1])
    except ValueError:
        await c.answer("Noto'g'ri so'rov.", show_alert=True)
        return

    data = await state.get_data()
    subject_id = data.get("diag_pick_subject")

    if not subject_id:
        await c.answer()
        await show_expired(c)
        return

    selected = list(data.get("diag_pick") or [])

    if topic_id in selected:
        selected.remove(topic_id)
    else:
        selected.append(topic_id)

    await state.update_data(diag_pick=selected)
    await c.answer()
    await render_picker(c, state, subject_id)


@router.callback_query(F.data.startswith("diag:go:"))
async def diagnostic_start(c: CallbackQuery, state):
    try:
        subject_id = int(c.data.split(":")[-1])
    except ValueError:
        await c.answer("Noto'g'ri so'rov.", show_alert=True)
        return

    data = await state.get_data()
    picked = list(data.get("diag_pick") or [])

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

        questions = await build_diagnostic(
            session,
            subject_id,
            user_id=user.id,
            topic_ids=picked or None,
        )

    if len(questions) < MIN_QUESTIONS:
        await replace_text(
            c,
            "\U0001F3AF <b>DIAGNOSTIKA</b>\n\n"
            "<i>Tanlangan mavzularda diagnostika uchun savol "
            "yetarli emas. Boshqa mavzu qo'shing yoki butun fan "
            "bo'yicha urinib ko'ring.</i>",
            InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="\U0001F4D6 Mavzularni tanlash",
                            callback_data=f"diag:p:{subject_id}",
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            text="\u2B05\uFE0F Fanlar",
                            callback_data="diag:menu",
                        )
                    ],
                    home_button(),
                ]
            ),
        )
        return

    await state.clear()
    await state.update_data(
        diag_subject=subject_id,
        diag_qids=[q.id for q in questions],
        diag_topics=[q.topic_id for q in questions],
        diag_index=0,
        diag_answers=[],
        diag_wait=True,
    )

    await show_question(c, questions[0], 0, len(questions))


# ---------------------------------------------------------------
#  3-ekran: javob
# ---------------------------------------------------------------

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
    topics = data.get("diag_topics") or []
    index = data.get("diag_index", 0)

    if not qids or not data.get("diag_wait"):
        await c.answer()
        await show_expired(c)
        return

    if index >= len(qids) or qids[index] != qid:
        await c.answer("Bu savol allaqachon javoblangan.", show_alert=True)
        return

    answers = list(data.get("diag_answers") or [])

    async with SessionLocal() as session:
        question = await session.get(Question, qid)

        if question is not None:
            correct_option = (question.correct_option or "").strip().upper()

            answers.append(
                {
                    "qid": qid,
                    "topic_id": (
                        topics[index]
                        if index < len(topics)
                        else question.topic_id
                    ),
                    "selected": option,
                    "correct": option == correct_option,
                }
            )
        else:
            # Savol javob berilgunga qadar o'chirilgan — uni
            # hisobga olmaymiz, lekin test to'xtab qolmasin.
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

    # Tugadi
    await state.update_data(diag_wait=False)
    await c.answer("Natija hisoblanmoqda...")

    if not answers:
        await state.clear()
        await show_expired(c)
        return

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
                "/start orqali ro'yxatdan o'ting va qaytadan "
                "urinib ko'ring.</i>",
                InlineKeyboardMarkup(inline_keyboard=[home_button()]),
            )
            return

        try:
            result = await save_diagnostic(
                session,
                user,
                data.get("diag_subject"),
                answers,
            )
        except Exception as exc:
            log.exception("Diagnostika saqlanmadi: %s", exc)

            await session.rollback()
            await state.clear()

            await replace_text(
                c,
                "\U0001F3AF <b>DIAGNOSTIKA</b>\n\n"
                "\u26A0\uFE0F <i>Natijani saqlashda xato yuz berdi. "
                "Qaytadan urinib ko'ring.</i>",
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
#  4-ekran: natija
# ---------------------------------------------------------------

async def show_result(c, result):
    lines = [
        "\U0001F3AF <b>DIAGNOSTIKA NATIJASI</b>",
        "",
        f"\U0001F4DA {html.escape(result.subject_name)}",
        f"\U0001F3AF Umumiy: <b>{result.correct}/{result.total}</b> "
        f"({result.accuracy}%)",
        "",
    ]

    if result.strong:
        lines.append("\U0001F7E2 <b>Kuchli mavzular:</b>")

        for topic in result.strong:
            lines.append(
                f"  • {html.escape(short(topic.topic_name, 24))} "
                f"— {topic.accuracy}%"
            )

        lines.append("")

    if result.medium:
        lines.append("\U0001F7E1 <b>O'rtacha:</b>")

        for topic in result.medium:
            lines.append(
                f"  • {html.escape(short(topic.topic_name, 24))} "
                f"— {topic.accuracy}%"
            )

        lines.append("")

    if result.weak:
        lines.append("\U0001F534 <b>Zaif mavzular:</b>")

        for topic in result.weak:
            lines.append(
                f"  • {html.escape(short(topic.topic_name, 24))} "
                f"— {topic.accuracy}%"
            )

        lines.append("")

    buttons = []

    nxt = result.next_topic

    if nxt is not None:
        lines.append(
            "\U0001F4A1 <b>Tavsiya:</b> "
            + html.escape(short(nxt.topic_name, 24))
            + " dan boshlang — shu yerda eng ko'p xato bo'ldi."
        )

        buttons.append(
            [
                InlineKeyboardButton(
                    text=f"▶️ {short(nxt.topic_name, 22)} testi",
                    callback_data=f"topic:{nxt.topic_id}",
                )
            ]
        )
    else:
        lines.append(
            "\U0001F389 <b>Barcha mavzular yaxshi o'zlashtirilgan.</b>"
        )

    lines.append("")
    lines.append(
        "<i>Xato javoblar Smart Takrorlash navbatiga qo'shildi.</i>"
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
        "<i>To'xtatildi. Javoblaringiz saqlanmadi — "
        "diagnostika oxirigacha ishlanishi kerak.</i>",
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

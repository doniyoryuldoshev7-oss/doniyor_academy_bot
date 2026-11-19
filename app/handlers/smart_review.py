import os
from html import escape

from aiogram import Router, F
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.fsm.context import FSMContext
from sqlalchemy import select

from ..db import SessionLocal
from ..models import User
from ..services.smart_review_queue import get_due_reviews

router = Router()


def smart_review_enabled():
    return os.getenv("SMART_REVIEW_ENABLED", "0") == "1"


def review_question_keyboard(question_id):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="A",
                    callback_data=f"sr:answer:{question_id}:A"
                ),
                InlineKeyboardButton(
                    text="B",
                    callback_data=f"sr:answer:{question_id}:B"
                ),
            ],
            [
                InlineKeyboardButton(
                    text="C",
                    callback_data=f"sr:answer:{question_id}:C"
                ),
                InlineKeyboardButton(
                    text="D",
                    callback_data=f"sr:answer:{question_id}:D"
                ),
            ],
            [
                InlineKeyboardButton(
                    text="\U0001F9E0 Smart Takrorlash",
                    callback_data="smart_review"
                ),
            ],
        ]
    )


def review_question_text(question, index, total):
    return (
        f"\U0001F9E0 <b>SMART TAKRORLASH</b>\n\n"
        f"? <b>Savol {index + 1}/{total}</b>\n\n"
        f"{escape(question.text or '')}\n\n"
        f"<b>A)</b> {escape(question.option_a or '')}\n"
        f"<b>B)</b> {escape(question.option_b or '')}\n"
        f"<b>C)</b> {escape(question.option_c or '')}\n"
        f"<b>D)</b> {escape(question.option_d or '')}"
    )


@router.callback_query(F.data == "sr:start")
async def smart_review_start(c, state):
    if not smart_review_enabled():
        await c.answer(
            "Smart Takrorlash hali sinov rejimida.",
            show_alert=True
        )
        return

    async with SessionLocal() as session:
        user = await session.scalar(
            select(User).where(
                User.telegram_id == c.from_user.id
            )
        )

        if user is None:
            await c.answer(
                "Foydalanuvchi topilmadi.",
                show_alert=True
            )
            return

        rows = await get_due_reviews(
            session,
            user_id=user.id,
            limit=20
        )

        # Dastlab faqat matnli savollar.
        rows = [
            (review, question)
            for review, question in rows
            if not question.image_path
        ]

        if not rows:
            await state.clear()
            await c.message.edit_text(
                "\U0001F9E0 <b>SMART TAKRORLASH</b>\n\n"
                "Hozircha takrorlash uchun matnli savol yo'q.",
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[[
                        InlineKeyboardButton(
                            text="\U0001F3E0 Bosh menyu",
                            callback_data="home"
                        )
                    ]]
                )
            )
            await c.answer()
            return

        question_ids = [question.id for _, question in rows]

        await state.clear()
        await state.update_data(
            smart_review_qids=question_ids,
            smart_review_index=0,
            smart_review_waiting=True
        )

        question = rows[0][1]

    await c.message.edit_text(
        review_question_text(
            question,
            index=0,
            total=len(question_ids)
        ),
        reply_markup=review_question_keyboard(question.id)
    )

    await c.answer()


@router.callback_query(F.data.startswith("sr:answer:"))
async def smart_review_answer(c, state):
    from datetime import datetime, timezone
    from sqlalchemy import select
    from ..models import SmartReview, Question
    from ..services.smart_review_service import (
        register_wrong_answer,
        register_correct_review,
    )

    if not smart_review_enabled():
        await c.answer("Funksiya hozircha o'chirilgan.", show_alert=True)
        return

    try:
        _, _, qid_text, option = c.data.split(":")
        qid = int(qid_text)
    except (ValueError, AttributeError):
        await c.answer("Noto'g'ri so'rov.", show_alert=True)
        return

    if option not in ("A", "B", "C", "D"):
        await c.answer("Noto'g'ri javob varianti.", show_alert=True)
        return

    data = await state.get_data()
    qids = data.get("smart_review_qids", [])
    index = data.get("smart_review_index", 0)

    if (
        not data.get("smart_review_waiting")
        or index >= len(qids)
        or qids[index] != qid
    ):
        await c.answer("Bu savol faol emas.", show_alert=True)
        return

    async with SessionLocal() as session:
        user = await session.scalar(
            select(User).where(User.telegram_id == c.from_user.id)
        )

        if user is None:
            await c.answer("O'quvchi topilmadi.", show_alert=True)
            return

        review = await session.scalar(
            select(SmartReview)
            .where(
                SmartReview.user_id == user.id,
                SmartReview.question_id == qid,
            )
            .with_for_update()
        )

        question = await session.get(Question, qid)

        # PostgreSQL row lock olingandan keyingi vaqt.
        now = datetime.now(timezone.utc)

        if (
            review is None
            or question is None
            or review.status != "active"
            or review.next_review_at is None
            or review.next_review_at > now
        ):
            await c.answer(
                "Savolning takrorlash muddati faol emas.",
                show_alert=True,
            )
            return

        correct_option = (question.correct_option or "").upper()
        is_correct = option == correct_option

        if is_correct:
            result = await register_correct_review(
                session, user.id, qid, now=now
            )
        else:
            result = await register_wrong_answer(
                session, user.id, qid, now=now
            )

        if result is None:
            await c.answer("Natijani saqlab bo'lmadi.", show_alert=True)
            return

        await session.commit()

    await state.update_data(smart_review_waiting=False)

    if is_correct:
        feedback = "? <b>To'g'ri javob!</b>"
        if result.status == "mastered":
            feedback += "\n\u2705 Savol mustahkamlandi!"
        else:
            feedback += "\n\U0001F552 Keyingi takrorlash rejalashtirildi."
    else:
        feedback = (
            "? <b>Noto'g'ri javob.</b>\n"
            f"\U0001F4A1 To'g'ri variant: <b>{correct_option}</b>\n"
            "?? Savol 1 kundan keyin qayta beriladi."
        )

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="\u27A1\uFE0F Keyingi savol",
                    callback_data="sr:next",
                )
            ],
            [
                InlineKeyboardButton(
                    text="\U0001F9E0 Smart Takrorlash",
                    callback_data="smart_review",
                )
            ],
        ]
    )

    await c.message.edit_text(
        f"\U0001F9E0 <b>SMART TAKRORLASH</b>\n\n{feedback}",
        reply_markup=keyboard,
    )
    await c.answer()


@router.callback_query(F.data == "sr:next")
async def smart_review_next(c, state):
    from datetime import datetime, timezone
    from sqlalchemy import select
    from ..models import SmartReview, Question

    if not smart_review_enabled():
        await c.answer("Funksiya hozircha o'chirilgan.", show_alert=True)
        return

    data = await state.get_data()

    if not data.get("smart_review_qids") or data.get(
        "smart_review_waiting"
    ):
        await c.answer("Avval savolga javob bering.", show_alert=True)
        return

    qids = data["smart_review_qids"]
    index = data.get("smart_review_index", 0) + 1
    now = datetime.now(timezone.utc)

    async with SessionLocal() as session:
        user = await session.scalar(
            select(User).where(User.telegram_id == c.from_user.id)
        )

        if user is None:
            await c.answer("O'quvchi topilmadi.", show_alert=True)
            return

        question = None

        while index < len(qids):
            qid = qids[index]

            review = await session.scalar(
                select(SmartReview).where(
                    SmartReview.user_id == user.id,
                    SmartReview.question_id == qid,
                    SmartReview.status == "active",
                    SmartReview.next_review_at <= now,
                )
            )

            q = await session.get(Question, qid)

            if (
                review is not None
                and q is not None
                and q.question_mode == "closed"
                and q.correct_option in ("A", "B", "C", "D")
                and not q.image_path
                and all((
                    q.option_a, q.option_b,
                    q.option_c, q.option_d
                ))
            ):
                question = q
                break

            index += 1

        if question is not None:
            text = review_question_text(question, index, len(qids))
            keyboard = review_question_keyboard(question.id)

    if question is None:
        await state.clear()
        await c.message.edit_text(
            "\U0001F389 <b>Smart Takrorlash yakunlandi!</b>\n\n"
            "Bugungi mashg'ulot tugadi.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[
                    InlineKeyboardButton(
                        text="\U0001F4CA Natijalarim",
                        callback_data="smart_review",
                    )
                ]]
            ),
        )
    else:
        await state.update_data(
            smart_review_index=index,
            smart_review_waiting=True,
        )
        await c.message.edit_text(text, reply_markup=keyboard)

    await c.answer()

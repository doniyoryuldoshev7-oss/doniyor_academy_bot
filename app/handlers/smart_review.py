import os
from html import escape
from pathlib import Path

from aiogram import Router, F
from aiogram.types import (
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from aiogram.fsm.context import FSMContext
from sqlalchemy import select

from ..db import SessionLocal
from ..models import User
from ..services.smart_review_queue import (
    get_due_reviews,
    is_reviewable,
)

router = Router()

# Telegram rasm izohi uchun chegara 1024 belgi.
# Zaxira bilan olamiz: uzunroq bo'lsa, matn alohida xabar bo'ladi.
CAPTION_LIMIT = 900


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
    """Savol matni.

    Rasmli savollarda savol ham, variantlar ham rasm ichida bo'ladi
    (bazada ular bo'sh satr), shuning uchun bo'sh qatorlar chiqmaydi.
    """
    body = escape(question.text or "").strip()

    options = []

    for key in ("a", "b", "c", "d"):
        value = escape(
            getattr(question, f"option_{key}", None) or ""
        ).strip()

        if value:
            options.append(f"<b>{key.upper()})</b> {value}")

    has_image = bool(getattr(question, "image_path", None))

    if not body:
        body = (
            "<i>Savol rasmda ko'rsatilgan.</i>"
            if has_image
            else "<i>Savol matni yo'q.</i>"
        )

    parts = [
        "\U0001F9E0 <b>SMART TAKRORLASH</b>",
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


def question_image_path(question):
    """Savol rasmi mavjud bo'lsa, fayl yo'lini qaytaradi."""
    raw = getattr(question, "image_path", None)

    if not raw:
        return None

    path = Path(raw)

    if not path.is_file():
        return None

    return path


async def replace_text(c, text, keyboard=None):
    """Joriy xabarni yangilaydi.

    Rasmli xabarda matn izoh (caption) bo'ladi, oddiy xabarda matn.
    Telegram bu ikkisini turlicha tahrirlaydi, shuning uchun
    tegishlisini tanlaymiz; iloji bo'lmasa, yangi xabar yuboramiz.
    """
    message = c.message

    is_caption = bool(getattr(message, "photo", None)) or (
        getattr(message, "caption", None) is not None
    )

    try:
        if is_caption:
            await message.edit_caption(
                caption=text,
                reply_markup=keyboard,
            )
        else:
            await message.edit_text(text, reply_markup=keyboard)

        return
    except Exception as exc:
        if "message is not modified" in str(exc):
            return

    await message.answer(text, reply_markup=keyboard)


async def show_question(c, question, index, total):
    """Savolni ko'rsatadi.

    Matnli savol — mavjud xabarni tahrirlaydi.
    Rasmli savol — eski xabarni o'chirib, rasmni izohi bilan
    yangi xabar qilib yuboradi (o'quvchi testidagi kabi).
    """
    text = review_question_text(question, index, total)
    keyboard = review_question_keyboard(question.id)

    image = question_image_path(question)

    if image is None:
        if getattr(question, "image_path", None):
            text += "\n\n<i>⚠️ Rasm topilmadi.</i>"

        await replace_text(c, text, keyboard)
        return

    # Rasmli savol: eski xabarni olib tashlaymiz
    try:
        await c.message.delete()
    except Exception:
        try:
            await c.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass

    photo = FSInputFile(image)

    if len(text) <= CAPTION_LIMIT:
        try:
            await c.message.answer_photo(
                photo=photo,
                caption=text,
                reply_markup=keyboard,
            )
            return
        except Exception:
            pass

    # Izoh sig'masa yoki yuborilmasa — rasm va matn alohida
    try:
        await c.message.answer_photo(photo=FSInputFile(image))
    except Exception:
        text += "\n\n<i>⚠️ Rasmni yuborib bo'lmadi.</i>"

    await c.message.answer(text, reply_markup=keyboard)


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

        if not rows:
            await state.clear()
            await replace_text(
                c,
                "\U0001F9E0 <b>SMART TAKRORLASH</b>\n\n"
                "Hozircha takrorlash uchun savol yo'q.",
                InlineKeyboardMarkup(
                    inline_keyboard=[[
                        InlineKeyboardButton(
                            text="\U0001F3E0 Bosh menyu",
                            callback_data="home"
                        )
                    ]]
                ),
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

    await show_question(c, question, 0, len(question_ids))

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
        feedback = "✅ <b>To'g'ri javob!</b>"
        if result.status == "mastered":
            feedback += "\n\U0001F3C6 Savol mustahkamlandi!"
        else:
            feedback += "\n\U0001F552 Keyingi takrorlash rejalashtirildi."
    else:
        feedback = (
            "❌ <b>Noto'g'ri javob.</b>\n"
            f"\U0001F4A1 To'g'ri variant: <b>{correct_option}</b>\n"
            "\U0001F552 Savol 1 kundan keyin qayta beriladi."
        )

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="➡️ Keyingi savol",
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

    await replace_text(
        c,
        f"\U0001F9E0 <b>SMART TAKRORLASH</b>\n\n{feedback}",
        keyboard,
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
    qids = data.get("smart_review_qids", [])
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

            if review is not None and is_reviewable(q):
                question = q
                break

            index += 1

    if question is None:
        await state.clear()
        await replace_text(
            c,
            "\U0001F389 <b>Smart Takrorlash yakunlandi!</b>\n\n"
            "Bugungi mashg'ulot tugadi.",
            InlineKeyboardMarkup(
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
        await show_question(c, question, index, len(qids))

    await c.answer()

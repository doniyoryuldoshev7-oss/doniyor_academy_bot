from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import and_, or_, select

from ..models import SmartReview, Question

# Matnli yopiq savol: variantlar matn ko'rinishida
TEXT_MODE = "closed"

# Rasmli savollar: savol ham, variantlar ham rasm ichida.
# Bunday savollarda option_a..option_d bo'sh satr bo'ladi,
# shuning uchun ularga variant to'ldirilgan degan shart qo'yilmaydi.
IMAGE_MODES = ("image", "image_multi")


def reviewable_condition():
    """Takrorlashga yaroqli savol sharti.

    Matnli savol  -> to'rtala variant to'ldirilgan bo'lishi kerak.
    Rasmli savol  -> rasm yo'li mavjud bo'lishi kerak.
    Ikkalasida ham to'g'ri javob A/B/C/D bo'lishi shart.
    """
    return and_(
        Question.is_active.is_(True),
        Question.correct_option.in_(("A", "B", "C", "D")),
        or_(
            and_(
                Question.question_mode == TEXT_MODE,
                Question.option_a.is_not(None),
                Question.option_b.is_not(None),
                Question.option_c.is_not(None),
                Question.option_d.is_not(None),
            ),
            and_(
                Question.question_mode.in_(IMAGE_MODES),
                Question.image_path.is_not(None),
            ),
        ),
    )


def due_reviews_query(user_id, now, limit=20):
    """Muddati kelgan savollar uchun SQL so'rov."""

    if not 1 <= limit <= 100:
        raise ValueError("limit 1 dan 100 gacha bo'lishi kerak")

    return (
        select(SmartReview, Question)
        .join(Question, SmartReview.question_id == Question.id)
        .where(
            SmartReview.user_id == user_id,
            SmartReview.status == "active",
            SmartReview.next_review_at.is_not(None),
            SmartReview.next_review_at <= now,
            reviewable_condition(),
        )
        .order_by(
            SmartReview.next_review_at.asc(),
            SmartReview.id.asc(),
        )
        .limit(limit)
    )


async def get_due_reviews(session, user_id, limit=20, now=None):
    if now is None:
        now = datetime.now(timezone.utc)

    if now.tzinfo is None:
        raise ValueError("UTC vaqt zonasi ko'rsatilishi kerak")

    result = await session.execute(
        due_reviews_query(user_id, now, limit)
    )

    return result.all()


def is_reviewable(question) -> bool:
    """Python tomonda xuddi shu shart (navbatni aylanib chiqishda)."""
    if question is None:
        return False

    if not getattr(question, "is_active", True):
        return False

    if (question.correct_option or "").strip().upper() not in ("A", "B", "C", "D"):
        return False

    mode = question.question_mode or TEXT_MODE

    if mode in IMAGE_MODES:
        return bool(question.image_path)

    if mode == TEXT_MODE:
        return all(
            value is not None
            for value in (
                question.option_a,
                question.option_b,
                question.option_c,
                question.option_d,
            )
        )

    return False


def unreviewable_reason(question):
    """Savol nega takrorlashga chiqmayapti? None bo'lsa — chiqadi.

    Tekshiruv uchun: is_reviewable() bilan bir xil mantiq, lekin
    sababini matn qilib qaytaradi.
    """
    if question is None:
        return "savol bazadan o'chirilgan"

    if not getattr(question, "is_active", True):
        return "savol yashirilgan (is_active = false)"

    correct = (question.correct_option or "").strip().upper()

    if correct not in ("A", "B", "C", "D"):
        return "to'g'ri javob belgilanmagan"

    mode = question.question_mode or TEXT_MODE

    if mode in IMAGE_MODES:
        if not question.image_path:
            return "rasm yo'li saqlanmagan"

        return None

    if mode == TEXT_MODE:
        missing = [
            key.upper()
            for key in ("a", "b", "c", "d")
            if getattr(question, f"option_{key}", None) is None
        ]

        if missing:
            return "variant yo'q: " + ", ".join(missing)

        return None

    return "savol turi qo'llab-quvvatlanmaydi: " + str(mode)


def image_missing(question) -> bool:
    """Rasm yo'li bor, lekin fayl diskda yo'q."""
    raw = getattr(question, "image_path", None)

    if not raw:
        return False

    return not Path(raw).is_file()

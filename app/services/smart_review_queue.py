from datetime import datetime, timezone

from sqlalchemy import select

from ..models import SmartReview, Question


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
            Question.question_mode == "closed",
            Question.is_active.is_(True),
            Question.correct_option.in_(("A", "B", "C", "D")),
            Question.option_a.is_not(None),
            Question.option_b.is_not(None),
            Question.option_c.is_not(None),
            Question.option_d.is_not(None),
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

from datetime import datetime, timezone

from sqlalchemy import select

from ..models import SmartReview
from .smart_review_schedule import schedule_review


async def register_wrong_answer(
    session,
    user_id: int,
    question_id: int,
    now=None,
):
    """Xato javobni Smart Takrorlashga qo'shadi.

    Tranzaksiyani commit qilish chaqiruvchi kod zimmasida.
    """

    if now is None:
        now = datetime.now(timezone.utc)

    result = schedule_review(
        stage=0,
        is_correct=False,
        now=now,
    )

    review = await session.scalar(
        select(SmartReview).where(
            SmartReview.user_id == user_id,
            SmartReview.question_id == question_id,
        )
    )

    if review is None:
        review = SmartReview(
            user_id=user_id,
            question_id=question_id,
            error_count=0,
            correct_streak=0,
        )
        session.add(review)

    review.stage = result["stage"]
    review.status = result["status"]
    review.next_review_at = result["next_review_at"]
    review.last_reviewed_at = now
    review.error_count = (review.error_count or 0) + 1
    review.correct_streak = 0

    return review


async def register_correct_review(
    session,
    user_id: int,
    question_id: int,
    now=None,
):
    """Smart Takrorlashdagi to'g'ri javobni qayd etadi.

    Commit qilish chaqiruvchi kod zimmasida.
    """
    if now is None:
        now = datetime.now(timezone.utc)

    review = await session.scalar(
        select(SmartReview).where(
            SmartReview.user_id == user_id,
            SmartReview.question_id == question_id,
        )
    )

    if review is None:
        return None

    if review.status == "mastered":
        return review

    if review.status != "active":
        return None

    if review.next_review_at is None or (
        review.next_review_at > now
    ):
        return None

    result = schedule_review(
        stage=review.stage,
        is_correct=True,
        now=now,
    )

    review.stage = result["stage"]
    review.status = result["status"]
    review.next_review_at = result["next_review_at"]
    review.last_reviewed_at = now
    review.correct_streak = (review.correct_streak or 0) + 1

    return review

from sqlalchemy import select
from ..models import Question, TestAttempt, AnswerLog


async def get_quiz(session, topic_id: int, limit: int = 10):
    questions = (await session.scalars(
        select(Question).where(Question.topic_id == topic_id).limit(limit)
    )).all()
    return questions


async def save_attempt(session, user_id: int, topic_id: int, total: int, correct: int):
    attempt = TestAttempt(
        user_id=user_id,
        topic_id=topic_id,
        total=total,
        correct=correct,
        score=correct,
    )
    session.add(attempt)
    await session.flush()
    return attempt

"""Guruh testi natijalarini umumiy test tarixiga yozish.

Joylashuvi: app/services/group_results.py

Oldin guruh testi natijasi faqat guruh jadvallarida qolardi, shuning
uchun u o'quvchi hisobotida, o'sish/pasayishda, Bilim xaritasida va
Smart Takrorlashda ko'rinmasdi. Endi har ishtirokchi uchun bitta
test yozuvi (source = "group") va har javobi uchun javob yozuvi
yaratiladi.

Guruhda variantlar tasodifiy aralashtirib berilgani uchun o'quvchi
aynan qaysi harfni tanlagani saqlanmagan. To'g'ri javobda to'g'ri
harf, noto'g'ri javobda "?" yoziladi.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime

from sqlalchemy import select

from ..models import (
    AnswerLog,
    GroupQuiz,
    GroupQuizAnswer,
    GroupQuizParticipant,
    Question,
    TestAttempt,
)

log = logging.getLogger(__name__)

UNKNOWN_OPTION = "?"


async def record_group_attempts(
    session,
    group_quiz: GroupQuiz,
    participants: list,
    *,
    register_wrong: bool = True,
    finished_at: datetime | None = None,
) -> int:
    """Har ishtirokchi uchun test yozuvi yaratadi. Qaytaradi: nechta.

    Allaqachon yozilgan ishtirokchi (attempt_id bor) qayta yozilmaydi.
    Commit chaqiruvchi zimmasida.
    """
    pending = [
        participant
        for participant in participants
        if getattr(participant, "attempt_id", None) is None
        and int(participant.answered or 0) > 0
    ]

    if not pending:
        return 0

    answers = (
        await session.scalars(
            select(GroupQuizAnswer)
            .where(GroupQuizAnswer.group_quiz_id == group_quiz.id)
            .order_by(GroupQuizAnswer.id)
        )
    ).all()

    by_participant: dict[int, list] = {}

    for answer in answers:
        by_participant.setdefault(answer.participant_id, []).append(answer)

    question_ids = {answer.question_id for answer in answers}

    questions = {}

    if question_ids:
        for question in await session.scalars(
            select(Question).where(Question.id.in_(question_ids))
        ):
            questions[question.id] = question

    smart_on = register_wrong and os.getenv("SMART_REVIEW_ENABLED", "0") == "1"

    register = None

    if smart_on:
        from .smart_review_service import register_wrong_answer as register

    created = 0

    for participant in pending:
        items = [
            answer
            for answer in by_participant.get(participant.id, [])
            if answer.question_id in questions
        ]

        if not items:
            # Yozadigan narsa yo'q: qayta-qayta urinmaslik uchun belgilanadi
            participant.attempt_id = 0
            continue

        moment = finished_at or max(
            (answer.answered_at for answer in items if answer.answered_at),
            default=group_quiz.created_at,
        )

        correct = sum(1 for answer in items if answer.is_correct)

        attempt = TestAttempt(
            user_id=participant.user_id,
            topic_id=group_quiz.topic_id,
            total=max(int(group_quiz.total_questions or 0), len(items)),
            correct=correct,
            score=correct,
            created_at=moment,
        )

        if hasattr(attempt, "source"):
            attempt.source = "group"

        session.add(attempt)
        await session.flush()

        for answer in items:
            question = questions[answer.question_id]

            if answer.is_correct:
                selected = (question.correct_option or "").strip().upper()[:1]
            else:
                selected = UNKNOWN_OPTION

            session.add(
                AnswerLog(
                    attempt_id=attempt.id,
                    question_id=answer.question_id,
                    selected_option=selected or UNKNOWN_OPTION,
                    is_correct=bool(answer.is_correct),
                )
            )

            if register is not None and not answer.is_correct:
                try:
                    async with session.begin_nested():
                        await register(
                            session,
                            user_id=participant.user_id,
                            question_id=answer.question_id,
                        )
                except Exception:
                    log.exception(
                        "Guruh testi: Smart Takrorlashga qo'shilmadi (#%s)",
                        answer.question_id,
                    )

        participant.attempt_id = attempt.id
        created += 1

    return created


async def backfill_group_attempts(session_factory, limit: int = 500) -> int:
    """Oldin tugagan guruh testlarini ham test tarixiga qo'shadi.

    Bir marta ishlaydi: yozilgan ishtirokchilarda attempt_id bo'ladi.
    Smart Takrorlashga qo'shilmaydi (eski xatolar navbatni to'ldirib
    yubormasligi uchun) va foydalanuvchi umumiy sonlari o'zgarmaydi
    (ular guruh tugaganda allaqachon hisoblangan).
    """
    total = 0

    try:
        async with session_factory() as session:
            quiz_ids = (
                await session.scalars(
                    select(GroupQuizParticipant.group_quiz_id)
                    .join(
                        GroupQuiz,
                        GroupQuiz.id == GroupQuizParticipant.group_quiz_id,
                    )
                    .where(
                        GroupQuiz.status == "finished",
                        GroupQuizParticipant.attempt_id.is_(None),
                        GroupQuizParticipant.answered > 0,
                    )
                    .distinct()
                    .limit(limit)
                )
            ).all()
    except Exception:
        log.exception("Eski guruh testlarini qidirib bo'lmadi")
        return 0

    for quiz_id in quiz_ids:
        # Bitta testdagi xato qolganlariga xalaqit bermasligi uchun
        # har biri alohida urinishda bajariladi
        try:
            async with session_factory() as session:
                quiz = await session.get(GroupQuiz, quiz_id)

                if quiz is None:
                    continue

                participants = (
                    await session.scalars(
                        select(GroupQuizParticipant).where(
                            GroupQuizParticipant.group_quiz_id == quiz_id
                        )
                    )
                ).all()

                total += await record_group_attempts(
                    session,
                    quiz,
                    participants,
                    register_wrong=False,
                )

                await session.commit()
        except Exception:
            log.exception("Guruh testi #%s tarixga qo'shilmadi", quiz_id)

    return total

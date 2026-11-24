"""Diagnostik test — hisob-kitob qismi.

Joylashuvi: app/services/diagnostic.py

Bir fandagi bir necha mavzudan qisqa test yig'adi va yakunida
mavzular kesimida natija chiqaradi.

Natijalar mavjud jadvallarga yoziladi: har bir mavzu uchun alohida
TestAttempt va uning AnswerLog yozuvlari. Shuning uchun diagnostika
Bilim xaritasiga, savollar sifatiga va Smart Takrorlashga
odatdagi test kabi qo'shilib ketadi.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from sqlalchemy import case, func, select

from ..models import (
    AnswerLog,
    Question,
    Subject,
    TestAttempt,
    Topic,
    User,
)

# Har mavzudan nechta savol
PER_TOPIC = 2

# Jami savollar chegarasi
MAX_QUESTIONS = 20

# Shundan kam savol yig'ilsa, diagnostika ma'nosiz
MIN_QUESTIONS = 4


def reviewable(question) -> bool:
    """Diagnostikaga yaroqli savol (A/B/C/D)."""
    if (question.correct_option or "").strip().upper() not in ("A", "B", "C", "D"):
        return False

    mode = question.question_mode or "closed"

    if mode in ("image", "image_multi"):
        return bool(question.image_path)

    if mode == "closed":
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


async def question_history(session, user_id: int, topic_ids: list[int]):
    """O'quvchining shu mavzulardagi javoblari.

    Qaytaradi: (savol bo'yicha {qid: (jami, to'g'ri)},
                mavzu bo'yicha {topic_id: (jami, to'g'ri)})
    """
    rows = await session.execute(
        select(
            AnswerLog.question_id,
            Question.topic_id,
            func.count(AnswerLog.id),
            func.sum(
                case((AnswerLog.is_correct.is_(True), 1), else_=0)
            ),
        )
        .join(TestAttempt, TestAttempt.id == AnswerLog.attempt_id)
        .join(Question, Question.id == AnswerLog.question_id)
        .where(
            TestAttempt.user_id == user_id,
            Question.topic_id.in_(topic_ids),
        )
        .group_by(AnswerLog.question_id, Question.topic_id)
    )

    by_question: dict[int, tuple[int, int]] = {}
    by_topic: dict[int, list[int]] = {}

    for question_id, topic_id, total, correct in rows:
        total = int(total or 0)
        correct = int(correct or 0)

        by_question[int(question_id)] = (total, correct)

        bucket = by_topic.setdefault(int(topic_id), [0, 0])
        bucket[0] += total
        bucket[1] += correct

    return by_question, {k: tuple(v) for k, v in by_topic.items()}


def topic_quota(stats: tuple[int, int] | None) -> int:
    """Mavzuga nechta savol ajratiladi.

    Zaif mavzuga ko'proq, mustahkamiga kamroq — lekin noldan
    emas, aks holda diagnostika o'lchash qobiliyatini yo'qotadi.
    """
    if not stats or not stats[0]:
        # Hali ishlanmagan mavzu: tekshirish kerak
        return 2

    total, correct = stats
    accuracy = correct / total

    if accuracy < 0.5:
        return 3

    if accuracy < 0.8:
        return 2

    return 1


def question_rank(question, history) -> tuple:
    """Savol tanlash tartibi.

    1) hali ko'rilmagan savollar
    2) xato qilingan savollar
    3) to'g'ri ishlangan savollar
    """
    stats = history.get(question.id)

    if stats is None:
        return (0, 0)

    total, correct = stats

    if correct < total:
        return (1, correct)

    return (2, correct)


async def build_diagnostic(
    session,
    subject_id: int,
    user_id: int | None = None,
    topic_ids: list[int] | None = None,
    limit: int = MAX_QUESTIONS,
) -> list[Question]:
    """Diagnostika uchun savollar to'plami.

    O'quvchi tarixi ma'lum bo'lsa, zaif mavzulardan ko'proq savol
    olinadi va hali ko'rilmagan savollarga ustunlik beriladi.
    """
    stmt = select(Topic).where(Topic.subject_id == subject_id)

    if topic_ids:
        stmt = stmt.where(Topic.id.in_(topic_ids))

    topics = list(await session.scalars(stmt))

    if not topics:
        return []

    pool: dict[int, list[Question]] = {}

    for topic in topics:
        questions = [
            question
            for question in await session.scalars(
                select(Question).where(
                    Question.topic_id == topic.id,
                    Question.is_active.is_(True),
                )
            )
            if reviewable(question)
        ]

        if questions:
            random.shuffle(questions)
            pool[topic.id] = questions

    if not pool:
        return []

    history: dict[int, tuple[int, int]] = {}
    topic_stats: dict[int, tuple[int, int]] = {}

    if user_id:
        history, topic_stats = await question_history(
            session, user_id, list(pool.keys())
        )

        for questions in pool.values():
            questions.sort(key=lambda q: question_rank(q, history))

    quotas = {
        topic_id: topic_quota(topic_stats.get(topic_id))
        for topic_id in pool
    }

    chosen: list[Question] = []
    taken: dict[int, int] = {topic_id: 0 for topic_id in pool}

    def collect(extra: int = 0) -> None:
        for topic_id, questions in pool.items():
            want = min(quotas[topic_id] + extra, len(questions))
            start = taken[topic_id]

            if want > start:
                chosen.extend(questions[start:want])
                taken[topic_id] = want

    collect()

    # Savol kam bo'lsa (masalan bitta mavzu tanlangan), to'ldiramiz
    extra = 0

    while len(chosen) < MIN_QUESTIONS and len(chosen) < limit:
        extra += 1

        before = len(chosen)
        collect(extra)

        if len(chosen) == before:
            break

    random.shuffle(chosen)

    return chosen[:limit]


# ---------------------------------------------------------------
#  Natija
# ---------------------------------------------------------------

@dataclass
class TopicResult:
    topic_id: int
    topic_name: str
    total: int = 0
    correct: int = 0

    @property
    def accuracy(self) -> int:
        if not self.total:
            return 0

        return int(round(self.correct / self.total * 100))

    @property
    def level(self) -> str:
        if self.accuracy >= 80:
            return "strong"

        if self.accuracy >= 50:
            return "medium"

        return "weak"


@dataclass
class DiagnosticResult:
    subject_name: str
    total: int = 0
    correct: int = 0
    topics: list[TopicResult] = field(default_factory=list)

    @property
    def accuracy(self) -> int:
        if not self.total:
            return 0

        return int(round(self.correct / self.total * 100))

    @property
    def strong(self) -> list[TopicResult]:
        return [t for t in self.topics if t.level == "strong"]

    @property
    def medium(self) -> list[TopicResult]:
        return [t for t in self.topics if t.level == "medium"]

    @property
    def weak(self) -> list[TopicResult]:
        return [t for t in self.topics if t.level == "weak"]

    @property
    def next_topic(self) -> TopicResult | None:
        """O'rganishni qayerdan boshlash kerak."""
        pool = self.weak or self.medium

        if not pool:
            return None

        return min(pool, key=lambda t: t.accuracy)


async def save_diagnostic(
    session,
    user: User,
    subject_id: int,
    answers: list[dict],
) -> DiagnosticResult:
    """Javoblarni bazaga yozadi va natijani hisoblaydi.

    answers: [{"qid", "topic_id", "selected", "correct"}]
    Har mavzu uchun alohida TestAttempt yaratiladi — shunda
    mavjud statistika mexanizmlari uni odatdagi test kabi ko'radi.
    """
    from ..services.smart_review_service import register_wrong_answer
    import os

    subject = (
        await session.get(Subject, subject_id)
        if subject_id
        else None
    )

    by_topic: dict[int, list[dict]] = {}

    for item in answers:
        by_topic.setdefault(item["topic_id"], []).append(item)

    result = DiagnosticResult(
        subject_name=subject.name if subject else "—"
    )

    smart_on = os.getenv("SMART_REVIEW_ENABLED", "0") == "1"

    for topic_id, items in by_topic.items():
        topic = await session.get(Topic, topic_id)

        total = len(items)
        correct = sum(1 for item in items if item["correct"])

        attempt = TestAttempt(
            user_id=user.id,
            topic_id=topic_id,
            total=total,
            correct=correct,
            score=correct,
        )

        session.add(attempt)
        await session.flush()

        for item in items:
            session.add(
                AnswerLog(
                    attempt_id=attempt.id,
                    question_id=item["qid"],
                    selected_option=item["selected"],
                    is_correct=item["correct"],
                )
            )

            if smart_on and not item["correct"]:
                await register_wrong_answer(
                    session,
                    user_id=user.id,
                    question_id=item["qid"],
                )

        result.topics.append(
            TopicResult(
                topic_id=topic_id,
                topic_name=topic.name if topic else "—",
                total=total,
                correct=correct,
            )
        )

        result.total += total
        result.correct += correct

    user.total_tests += 1
    user.total_questions += result.total
    user.correct_answers += result.correct
    user.total_score += result.correct

    await session.commit()

    result.topics.sort(key=lambda t: t.accuracy, reverse=True)

    return result

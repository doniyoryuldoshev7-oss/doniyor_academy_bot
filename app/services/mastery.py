"""Bilim xaritasi hisoblari — Doniyor Academy bot.

Joylashuvi: app/services/mastery.py

Mavjud ma'lumotdan foydalanadi, yangi jadval talab qilmaydi:
    AnswerLog + TestAttempt  -- to'g'ri javoblar foizi va qamrov
    SmartReview              -- takrorlash kutayotgan savollar
    Question                 -- mavzudagi jami faol savollar

Muhim tamoyil: foiz ikkita alohida sondan tashkil topadi —
aniqlik (to'g'ri javoblar ulushi) va qamrov (nechta savol ishlangani).
Ular ko'paytirilmaydi, chunki 3 ta savolga to'g'ri javob bergan
o'quvchi "zaif" emas, shunchaki hali kam ishlagan. Shuning uchun
baho faqat yetarli ma'lumot bo'lganda beriladi.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import case, distinct, func, select

from ..models import AnswerLog, Question, SmartReview, TestAttempt, Topic

BAR_WIDTH = 8
BAR_FULL = "█"
BAR_EMPTY = "░"

# Baho berish uchun kamida shuncha xil savol ishlangan bo'lishi kerak
MIN_DISTINCT_FOR_LEVEL = 3

# To'liq qamrov uchun mo'ljal (yoki mavzudagi savollar soni, qaysi kichik bo'lsa)
COVERAGE_TARGET = 10

LEVEL_STRONG = "strong"
LEVEL_MEDIUM = "medium"
LEVEL_WEAK = "weak"
LEVEL_UNKNOWN = "unknown"
LEVEL_EMPTY = "empty"

LEVEL_LABELS = {
    LEVEL_STRONG: "\U0001F7E2 mustahkam",
    LEVEL_MEDIUM: "\U0001F7E1 o'rtacha",
    LEVEL_WEAK: "\U0001F534 zaif",
    LEVEL_UNKNOWN: "⚪ ma'lumot kam",
    LEVEL_EMPTY: "⚪ boshlanmagan",
}


@dataclass
class TopicStat:
    topic_id: int
    name: str
    total_questions: int = 0
    answered: int = 0
    correct: int = 0
    distinct_answered: int = 0
    review_pending: int = 0
    review_mastered: int = 0

    @property
    def accuracy(self) -> float:
        if not self.answered:
            return 0.0

        return self.correct / self.answered

    @property
    def coverage_target(self) -> int:
        if not self.total_questions:
            return 0

        return min(self.total_questions, COVERAGE_TARGET)

    @property
    def coverage(self) -> float:
        target = self.coverage_target

        if not target:
            return 0.0

        return min(1.0, self.distinct_answered / target)

    @property
    def level(self) -> str:
        if not self.answered:
            return LEVEL_EMPTY

        needed = min(MIN_DISTINCT_FOR_LEVEL, self.total_questions or 1)

        if self.distinct_answered < needed:
            return LEVEL_UNKNOWN

        if self.accuracy >= 0.85:
            return LEVEL_STRONG

        if self.accuracy >= 0.60:
            return LEVEL_MEDIUM

        return LEVEL_WEAK

    @property
    def level_label(self) -> str:
        return LEVEL_LABELS[self.level]


@dataclass
class SubjectStat:
    subject_id: int
    name: str
    topics: list[TopicStat] = field(default_factory=list)

    @property
    def answered(self) -> int:
        return sum(t.answered for t in self.topics)

    @property
    def correct(self) -> int:
        return sum(t.correct for t in self.topics)

    @property
    def accuracy(self) -> float:
        if not self.answered:
            return 0.0

        return self.correct / self.answered

    @property
    def review_pending(self) -> int:
        return sum(t.review_pending for t in self.topics)

    @property
    def started_topics(self) -> int:
        return sum(1 for t in self.topics if t.answered)


def bar(value: float, width: int = BAR_WIDTH) -> str:
    """0.75 -> ██████░░"""
    value = max(0.0, min(1.0, value))
    filled = int(round(value * width))

    return BAR_FULL * filled + BAR_EMPTY * (width - filled)


def percent(value: float) -> int:
    return int(round(max(0.0, min(1.0, value)) * 100))


# ---------------------------------------------------------------
#  Hisoblash
# ---------------------------------------------------------------

async def subject_stat(session, user_id: int, subject_id: int) -> SubjectStat | None:
    """Bitta fan bo'yicha barcha mavzular kesimidagi ko'rsatkichlar."""
    from ..models import Subject

    subject = await session.get(Subject, subject_id)

    if subject is None:
        return None

    topics = list(
        await session.scalars(
            select(Topic).where(Topic.subject_id == subject_id)
        )
    )

    if not topics:
        return SubjectStat(subject_id=subject_id, name=subject.name, topics=[])

    stats = {
        topic.id: TopicStat(topic_id=topic.id, name=topic.name)
        for topic in topics
    }

    topic_ids = list(stats.keys())

    # 1) Mavzudagi jami faol savollar
    rows = await session.execute(
        select(Question.topic_id, func.count(Question.id))
        .where(
            Question.topic_id.in_(topic_ids),
            Question.is_active.is_(True),
        )
        .group_by(Question.topic_id)
    )

    for topic_id, total in rows:
        stats[topic_id].total_questions = int(total or 0)

    # 2) Javoblar tarixi
    rows = await session.execute(
        select(
            Question.topic_id,
            func.count(AnswerLog.id),
            func.sum(
                case((AnswerLog.is_correct.is_(True), 1), else_=0)
            ),
            func.count(distinct(AnswerLog.question_id)),
        )
        .join(TestAttempt, TestAttempt.id == AnswerLog.attempt_id)
        .join(Question, Question.id == AnswerLog.question_id)
        .where(
            TestAttempt.user_id == user_id,
            Question.topic_id.in_(topic_ids),
            Question.is_active.is_(True),
        )
        .group_by(Question.topic_id)
    )

    for topic_id, answered, correct, distinct_answered in rows:
        stat = stats[topic_id]
        stat.answered = int(answered or 0)
        stat.correct = int(correct or 0)
        stat.distinct_answered = int(distinct_answered or 0)

    # 3) Smart Takrorlash holati
    rows = await session.execute(
        select(
            Question.topic_id,
            func.sum(
                case((SmartReview.status == "active", 1), else_=0)
            ),
            func.sum(
                case((SmartReview.status == "mastered", 1), else_=0)
            ),
        )
        .join(Question, Question.id == SmartReview.question_id)
        .where(
            SmartReview.user_id == user_id,
            Question.topic_id.in_(topic_ids),
            Question.is_active.is_(True),
        )
        .group_by(Question.topic_id)
    )

    for topic_id, pending, mastered in rows:
        stat = stats[topic_id]
        stat.review_pending = int(pending or 0)
        stat.review_mastered = int(mastered or 0)

    ordered = [stats[topic.id] for topic in topics]

    return SubjectStat(
        subject_id=subject_id,
        name=subject.name,
        topics=ordered,
    )


async def overall_accuracy(session, user_id: int, subject_id: int) -> tuple[int, int]:
    """(javoblar soni, to'g'ri javoblar soni) — bitta fan bo'yicha."""
    row = await session.execute(
        select(
            func.count(AnswerLog.id),
            func.sum(
                case((AnswerLog.is_correct.is_(True), 1), else_=0)
            ),
        )
        .join(TestAttempt, TestAttempt.id == AnswerLog.attempt_id)
        .join(Question, Question.id == AnswerLog.question_id)
        .join(Topic, Topic.id == Question.topic_id)
        .where(
            TestAttempt.user_id == user_id,
            Topic.subject_id == subject_id,
            Question.is_active.is_(True),
        )
    )

    answered, correct = row.one()

    return int(answered or 0), int(correct or 0)


def weakest_topic(stat: SubjectStat) -> TopicStat | None:
    """Tavsiya uchun: baho berish mumkin bo'lgan eng zaif mavzu."""
    rated = [
        topic
        for topic in stat.topics
        if topic.level in (LEVEL_WEAK, LEVEL_MEDIUM)
    ]

    if rated:
        return min(rated, key=lambda t: t.accuracy)

    # Baholanadigan mavzu yo'q bo'lsa — hali boshlanmaganini taklif qilamiz
    untouched = [
        topic
        for topic in stat.topics
        if not topic.answered and topic.total_questions
    ]

    if untouched:
        return untouched[0]

    return None

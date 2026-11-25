"""Diagnostik test — tanlash algoritmi va natija hisobi.

Joylashuvi: app/services/diagnostic.py

Diagnostika bir necha fan va mavzu bo'yicha o'quvchining holatini
o'lchaydi. Asosiy qiyinchilik: savollar soni cheklangan, mavzular
esa ko'p. Shuning uchun savollar tasodifan emas, ustuvorlik bo'yicha
bosqichma-bosqich tarqatiladi.

ALGORITM

1-qadam. Har mavzu uchun o'quvchi tarixidan ustuvorlik aniqlanadi:

      0  hali ishlanmagan   -> eng qimmatli, chunki hech narsa ma'lum emas
      1  zaif (50% dan past)
      2  o'rtacha (50-79%)
      3  mustahkam (80% dan yuqori)

   Uzoq vaqt tekshirilmagan mavzuga qo'shimcha ustunlik beriladi
   (bilim vaqt o'tishi bilan unutiladi).

2-qadam. Savollar bosqichma-bosqich tarqatiladi:

      1-bosqich: har bir mavzuga bittadan (ustuvorlik tartibida)
      2-bosqich: zaif va ishlanmagan mavzularga ikkinchi savol
      3-bosqich: ularga uchinchi savol
      ...

   Shunday qilib byudjet yetgancha barcha mavzu qamrab olinadi,
   ortgan savollar esa eng zaif joylarga ketadi. Byudjet yetmasa,
   qolgan mavzular "tekshirilmadi" deb ochiq aytiladi — jimgina
   tushirib qoldirilmaydi.

3-qadam. Mavzu ichida savol tanlash tartibi: avval hali ko'rilmagan
   savollar, keyin xato qilinganlari, oxirida o'zlashtirilganlari.

HUKM CHIQARISH

Mavzu bo'yicha baho kamida MIN_FOR_VERDICT ta savol berilgan bo'lsa
chiqariladi. 1-2 savol tasodifga juda yaqin, shuning uchun u yerda
foiz ko'rsatiladi, lekin "zaif" yoki "kuchli" deyilmaydi.
"""

from __future__ import annotations
from ..numfmt import pct_int

import logging
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import case, func, select

from ..models import (
    AnswerLog,
    Question,
    Subject,
    TestAttempt,
    Topic,
    User,
)

# Hukm chiqarish uchun mavzuda kamida shuncha savol bo'lishi kerak
MIN_FOR_VERDICT = 1

# Mavzuga berilishi mumkin bo'lgan eng ko'p savol (ustuvorlikka qarab)
CAP_BY_TIER = {0: 4, 1: 4, 2: 3, 3: 2}

# Uzoq tekshirilmagan deb hisoblash muddati
STALE_DAYS = 21

# Avtomatik byudjet chegaralari
AUTO_MIN = 10
AUTO_MAX = 40

TIER_UNSEEN = 0
TIER_WEAK = 1
TIER_MEDIUM = 2
TIER_STRONG = 3

TIER_LABELS = {
    TIER_UNSEEN: "ishlanmagan",
    TIER_WEAK: "zaif",
    TIER_MEDIUM: "o'rtacha",
    TIER_STRONG: "mustahkam",
}


def usable(question) -> bool:
    """Diagnostikaga yaroqli savol (A/B/C/D bilan javob beriladigan)."""
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


# ---------------------------------------------------------------
#  Tarix
# ---------------------------------------------------------------

@dataclass
class TopicHistory:
    answered: int = 0
    correct: int = 0
    last_seen: datetime | None = None

    @property
    def accuracy(self) -> float:
        if not self.answered:
            return 0.0

        return self.correct / self.answered


async def load_history(session, user_id: int, topic_ids: list[int]):
    """O'quvchining shu mavzulardagi javoblari.

    Qaytaradi: ({qid: (jami, to'g'ri)}, {topic_id: TopicHistory})
    """
    by_question: dict[int, tuple[int, int]] = {}
    by_topic: dict[int, TopicHistory] = {}

    if not topic_ids:
        return by_question, by_topic

    rows = await session.execute(
        select(
            AnswerLog.question_id,
            Question.topic_id,
            func.count(AnswerLog.id),
            func.sum(
                case((AnswerLog.is_correct.is_(True), 1), else_=0)
            ),
            func.max(TestAttempt.created_at),
        )
        .join(TestAttempt, TestAttempt.id == AnswerLog.attempt_id)
        .join(Question, Question.id == AnswerLog.question_id)
        .where(
            TestAttempt.user_id == user_id,
            Question.topic_id.in_(topic_ids),
        )
        .group_by(AnswerLog.question_id, Question.topic_id)
    )

    for question_id, topic_id, total, correct, last_seen in rows:
        total = int(total or 0)
        correct = int(correct or 0)

        by_question[int(question_id)] = (total, correct)

        entry = by_topic.setdefault(int(topic_id), TopicHistory())
        entry.answered += total
        entry.correct += correct

        if last_seen is not None:
            if entry.last_seen is None or last_seen > entry.last_seen:
                entry.last_seen = last_seen

    return by_question, by_topic


def topic_tier(history: TopicHistory | None) -> int:
    if history is None or not history.answered:
        return TIER_UNSEEN

    accuracy = history.accuracy

    if accuracy < 0.5:
        return TIER_WEAK

    if accuracy < 0.8:
        return TIER_MEDIUM

    return TIER_STRONG


def topic_priority(
    history: TopicHistory | None,
    now: datetime | None = None,
) -> float:
    """Kichik qiymat = yuqori ustuvorlik."""
    tier = topic_tier(history)
    score = float(tier)

    if history is not None and history.last_seen is not None:
        moment = now or datetime.utcnow()
        days = (moment - history.last_seen).days

        if days >= STALE_DAYS:
            # Uzoq tekshirilmagan mavzu muhimroq bo'lib qoladi
            score -= 0.5

    return score


def question_rank(question, by_question: dict) -> tuple:
    """Mavzu ichida savol tanlash tartibi."""
    stats = by_question.get(question.id)

    if stats is None:
        return (0, 0)

    total, correct = stats

    if correct < total:
        return (1, correct)

    return (2, correct)


# ---------------------------------------------------------------
#  Tanlash
# ---------------------------------------------------------------

@dataclass
class TopicPlan:
    topic_id: int
    topic_name: str
    subject_id: int
    subject_name: str
    tier: int
    priority: float
    questions: list = field(default_factory=list)
    asked: int = 0

    @property
    def available(self) -> int:
        return len(self.questions)

    @property
    def cap(self) -> int:
        return min(CAP_BY_TIER.get(self.tier, 2), self.available)


@dataclass
class DiagnosticPlan:
    questions: list = field(default_factory=list)
    topics: list = field(default_factory=list)

    @property
    def covered(self) -> list:
        return [plan for plan in self.topics if plan.asked]

    @property
    def skipped(self) -> list:
        return [plan for plan in self.topics if not plan.asked]

    @property
    def subjects(self) -> list:
        seen = []

        for plan in self.topics:
            if plan.subject_name not in seen:
                seen.append(plan.subject_name)

        return seen


def auto_budget(topic_count: int) -> int:
    """Mavzular soniga qarab oqilona test uzunligi."""
    if topic_count <= 0:
        return AUTO_MIN

    return max(AUTO_MIN, min(AUTO_MAX, topic_count * 2))


def allocate(plans: list[TopicPlan], budget: int) -> list:
    """Savollarni bosqichma-bosqich tarqatadi.

    1-faza (shakllantirish): ustuvorlik tartibida har mavzuga
    bittadan beriladi, keyin zaif mavzularga ikkinchi, uchinchi...
    Har mavzuning yuqori chegarasi bor, shunda bitta mavzu butun
    testni egallab olmaydi.

    2-faza (to'ldirish): agar so'ralgan savol soni hali yig'ilmagan
    bo'lsa, chegaralar olib tashlanadi va savollar yana ustuvorlik
    tartibida tarqatilaveradi. Chunki foydalanuvchi "20 ta savol"
    desa, 12 ta berib qo'yish noto'g'ri bo'lardi.
    """
    chosen = []

    if budget <= 0 or not plans:
        return chosen

    ordered = sorted(plans, key=lambda p: (p.priority, -p.available))

    # 1-faza: chegaralar bilan
    max_rounds = max((plan.cap for plan in ordered), default=0)

    for round_index in range(max_rounds):
        progressed = False

        for plan in ordered:
            if len(chosen) >= budget:
                return chosen

            if round_index >= plan.cap:
                continue

            chosen.append(plan.questions[plan.asked])
            plan.asked += 1
            progressed = True

        if not progressed:
            break

    # 2-faza: chegarasiz to'ldirish
    while len(chosen) < budget:
        progressed = False

        for plan in ordered:
            if len(chosen) >= budget:
                break

            if plan.asked >= plan.available:
                continue

            chosen.append(plan.questions[plan.asked])
            plan.asked += 1
            progressed = True

        if not progressed:
            # Savollar tugadi
            break

    return chosen


def usable_condition():
    """usable() ning SQL ko'rinishi — sanashni bazaga topshirish uchun."""
    from sqlalchemy import and_, or_

    return and_(
        Question.is_active.is_(True),
        Question.correct_option.in_(("A", "B", "C", "D")),
        or_(
            and_(
                Question.question_mode == "closed",
                Question.option_a.is_not(None),
                Question.option_b.is_not(None),
                Question.option_c.is_not(None),
                Question.option_d.is_not(None),
            ),
            and_(
                Question.question_mode.in_(("image", "image_multi")),
                Question.image_path.is_not(None),
            ),
        ),
    )


async def subject_capacity(session) -> dict:
    """Har fanda diagnostikaga yaroqli nechta savol bor."""
    rows = await session.execute(
        select(Subject.id, Subject.name, func.count(Question.id))
        .join(Topic, Topic.subject_id == Subject.id)
        .join(Question, Question.topic_id == Topic.id)
        .where(Subject.is_active.is_(True), usable_condition())
        .group_by(Subject.id, Subject.name)
    )

    return {
        int(row[0]): {"name": row[1], "count": int(row[2] or 0)}
        for row in rows
        if int(row[2] or 0) > 0
    }


async def topic_capacity(session, subject_id: int) -> dict:
    """Fandagi har mavzuda nechta yaroqli savol bor."""
    rows = await session.execute(
        select(Topic.id, func.count(Question.id))
        .join(Question, Question.topic_id == Topic.id)
        .where(Topic.subject_id == subject_id, usable_condition())
        .group_by(Topic.id)
    )

    return {int(row[0]): int(row[1] or 0) for row in rows}


async def build_plan(
    session,
    *,
    user_id: int | None = None,
    budgets: dict | None = None,
    subject_ids: list[int] | None = None,
    topic_ids: list[int] | None = None,
    budget: int | None = None,
    now: datetime | None = None,
) -> DiagnosticPlan:
    """Diagnostika rejasini tuzadi.

    budgets berilsa — har fan uchun alohida savol soni
    ({fan_id: soni}). Aks holda umumiy byudjet ishlatiladi.
    """
    stmt = (
        select(Topic, Subject)
        .join(Subject, Subject.id == Topic.subject_id)
        .where(Subject.is_active.is_(True))
    )

    wanted_subjects = None

    if budgets:
        wanted_subjects = [
            subject_id
            for subject_id, count in budgets.items()
            if count > 0
        ]

        if not wanted_subjects:
            return DiagnosticPlan()

        stmt = stmt.where(Subject.id.in_(wanted_subjects))
    elif subject_ids:
        stmt = stmt.where(Subject.id.in_(subject_ids))

    if topic_ids:
        stmt = stmt.where(Topic.id.in_(topic_ids))

    rows = list(await session.execute(stmt))

    if not rows:
        return DiagnosticPlan()

    all_topic_ids = [topic.id for topic, _subject in rows]

    by_question: dict[int, tuple[int, int]] = {}
    by_topic: dict[int, TopicHistory] = {}

    if user_id:
        by_question, by_topic = await load_history(
            session, user_id, all_topic_ids
        )

    plans: list[TopicPlan] = []

    # Barcha mavzuning savollari bitta so'rovda olinadi (har mavzuga
    # alohida so'rov yuborish fanlar ko'p bo'lganda sekin edi)
    questions_by_topic: dict[int, list] = {}

    for question in await session.scalars(
        select(Question).where(
            Question.topic_id.in_(all_topic_ids),
            Question.is_active.is_(True),
        )
    ):
        if usable(question):
            questions_by_topic.setdefault(question.topic_id, []).append(
                question
            )

    for topic, subject in rows:
        questions = questions_by_topic.get(topic.id, [])

        if not questions:
            continue

        random.shuffle(questions)
        questions.sort(key=lambda q: question_rank(q, by_question))

        history = by_topic.get(topic.id)

        plans.append(
            TopicPlan(
                topic_id=topic.id,
                topic_name=topic.name,
                subject_id=subject.id,
                subject_name=subject.name,
                tier=topic_tier(history),
                priority=topic_priority(history, now),
                questions=questions,
            )
        )

    if not plans:
        return DiagnosticPlan()

    chosen: list = []

    if budgets:
        # Har fan o'z byudjeti bo'yicha, mavzularga taqsimlanadi
        for subject_id in wanted_subjects:
            subject_plans = [
                plan for plan in plans if plan.subject_id == subject_id
            ]

            if not subject_plans:
                continue

            chosen.extend(
                allocate(subject_plans, int(budgets[subject_id]))
            )
    else:
        chosen = allocate(plans, budget or auto_budget(len(plans)))

    random.shuffle(chosen)

    return DiagnosticPlan(questions=chosen, topics=plans)


# ---------------------------------------------------------------
#  Natija
# ---------------------------------------------------------------

@dataclass
class TopicResult:
    topic_id: int
    topic_name: str
    subject_name: str
    total: int = 0
    correct: int = 0

    @property
    def accuracy(self) -> int:
        if not self.total:
            return 0

        return pct_int(self.correct, self.total)

    @property
    def level(self) -> str:
        """Baho faqat yetarli savol bo'lsa chiqariladi."""
        if self.total < MIN_FOR_VERDICT:
            return "unclear"

        if self.accuracy >= 80:
            return "strong"

        if self.accuracy >= 50:
            return "medium"

        return "weak"


@dataclass
class DiagnosticResult:
    total: int = 0
    correct: int = 0
    topics: list = field(default_factory=list)
    skipped: list = field(default_factory=list)
    subject_names: list = field(default_factory=list)

    @property
    def accuracy(self) -> int:
        if not self.total:
            return 0

        return pct_int(self.correct, self.total)

    def by_level(self, level: str) -> list:
        return [topic for topic in self.topics if topic.level == level]

    @property
    def strong(self) -> list:
        return self.by_level("strong")

    @property
    def medium(self) -> list:
        return self.by_level("medium")

    @property
    def weak(self) -> list:
        return self.by_level("weak")

    @property
    def unclear(self) -> list:
        return self.by_level("unclear")

    @property
    def next_topic(self):
        """Qaysi mavzudan boshlash kerak."""
        pool = self.weak or self.medium

        if pool:
            return min(pool, key=lambda t: t.accuracy)

        # Baho chiqmagan mavzular orasidan eng pastini olamiz
        if self.unclear:
            return min(self.unclear, key=lambda t: t.accuracy)

        return None

    @property
    def title(self) -> str:
        if not self.subject_names:
            return "Diagnostika"

        if len(self.subject_names) == 1:
            return self.subject_names[0]

        return f"{len(self.subject_names)} ta fan"


async def save_diagnostic(
    session,
    user: User,
    answers: list[dict],
    skipped: list | None = None,
) -> DiagnosticResult:
    """Javoblarni bitta test yozuvi sifatida saqlaydi.

    Mavzular kesimidagi tahlil javoblardan (AnswerLog -> Question ->
    Topic) kelib chiqadi, shuning uchun bitta yozuv yetarli. Bu
    hisobotda bitta diagnostika o'nta alohida test bo'lib
    ko'rinmasligi uchun muhim.
    """
    import os

    if not answers:
        return DiagnosticResult()

    total = len(answers)
    correct = sum(1 for item in answers if item["correct"])

    attempt = TestAttempt(
        user_id=user.id,
        topic_id=answers[0]["topic_id"],
        total=total,
        correct=correct,
        score=correct,
    )

    if hasattr(attempt, "source"):
        attempt.source = "diagnostic"

    session.add(attempt)
    await session.flush()

    smart_on = os.getenv("SMART_REVIEW_ENABLED", "0") == "1"

    if smart_on:
        from ..services.smart_review_service import register_wrong_answer

    for item in answers:
        session.add(
            AnswerLog(
                attempt_id=attempt.id,
                question_id=item["qid"],
                selected_option=item["selected"],
                is_correct=item["correct"],
            )
        )

        if smart_on and not item["correct"]:
            # Smart Takrorlashdagi xato butun natijani yo'qotmasligi uchun
            # alohida "saqlash nuqtasi" ichida bajariladi
            try:
                async with session.begin_nested():
                    await register_wrong_answer(
                        session,
                        user_id=user.id,
                        question_id=item["qid"],
                    )
            except Exception:
                logging.getLogger(__name__).exception(
                    "Smart Takrorlashga qo'shilmadi (savol #%s)",
                    item["qid"],
                )

    user.total_tests += 1
    user.total_questions += total
    user.correct_answers += correct
    user.total_score += correct

    await session.commit()

    # Mavzular kesimida natija
    buckets: dict[int, TopicResult] = {}

    for item in answers:
        bucket = buckets.get(item["topic_id"])

        if bucket is None:
            bucket = TopicResult(
                topic_id=item["topic_id"],
                topic_name=item.get("topic_name") or "—",
                subject_name=item.get("subject_name") or "—",
            )
            buckets[item["topic_id"]] = bucket

        bucket.total += 1

        if item["correct"]:
            bucket.correct += 1

    topics = sorted(
        buckets.values(),
        key=lambda t: (t.accuracy, -t.total),
    )

    subject_names = []

    for topic in topics:
        if topic.subject_name not in subject_names:
            subject_names.append(topic.subject_name)

    return DiagnosticResult(
        total=total,
        correct=correct,
        topics=topics,
        skipped=list(skipped or []),
        subject_names=subject_names,
    )

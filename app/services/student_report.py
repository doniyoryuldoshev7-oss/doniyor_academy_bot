"""O'quvchilar natijalari — hisob-kitob qismi.

Joylashuvi: app/services/student_report.py

Mavjud ma'lumotdan ishlaydi, yangi jadval kerak emas:
    User         -- ro'yxatdan o'tgan sana, guruh
    TestAttempt  -- har bir test urinishi: sana, mavzu, natija
    Topic/Subject-- mavzu va fan nomlari

O'sish/pasayish: urinishlar vaqt bo'yicha ikkiga bo'linadi va
ikki yarmining foizi solishtiriladi. Urinish ko'p bo'lsa,
oxirgi 5 tasi undan oldingi 5 tasi bilan solishtiriladi —
bu yaqin kunlardagi o'zgarishni aniqroq ko'rsatadi.
"""

from __future__ import annotations

import os
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

# Trend uchun eng kam urinish soni
MIN_ATTEMPTS_FOR_TREND = 4

# Urinish ko'p bo'lganda solishtiriladigan oyna
RECENT_WINDOW = 5


def local_offset() -> timedelta:
    raw = (os.getenv("REVIEW_REMINDER_UTC_OFFSET") or "5").strip()

    try:
        hours = int(raw)
    except ValueError:
        hours = 5

    if not -12 <= hours <= 14:
        hours = 5

    return timedelta(hours=hours)


def to_local(value: datetime | None) -> datetime | None:
    if value is None:
        return None

    return value + local_offset()


def fmt_date(value: datetime | None) -> str:
    local = to_local(value)

    if local is None:
        return "—"

    return local.strftime("%d.%m.%Y")


def fmt_datetime(value: datetime | None) -> str:
    local = to_local(value)

    if local is None:
        return "—"

    return local.strftime("%d.%m.%Y %H:%M")


def days_since(value: datetime | None, now: datetime | None = None) -> int | None:
    if value is None:
        return None

    moment = now or datetime.utcnow()

    return max(0, (moment - value).days)


def percent(correct: int, total: int) -> int:
    if not total:
        return 0

    return int(round(correct / total * 100))


# ---------------------------------------------------------------
#  Ro'yxat
# ---------------------------------------------------------------

@dataclass
class StudentRow:
    telegram_id: int
    full_name: str
    group_name: str | None
    attempts: int
    total: int
    correct: int
    last_attempt: datetime | None

    @property
    def accuracy(self) -> int:
        return percent(self.correct, self.total)


SORT_ACTIVITY = "faollik"
SORT_RESULT = "natija"
SORT_NAME = "ism"


async def students_list(
    session,
    *,
    sort: str = SORT_ACTIVITY,
    group: str | None = None,
) -> list[StudentRow]:
    """Barcha tasdiqlangan o'quvchilar va ularning natijalari."""
    stmt = (
        select(
            User.telegram_id,
            User.full_name,
            User.group_name,
            func.count(TestAttempt.id),
            func.coalesce(func.sum(TestAttempt.total), 0),
            func.coalesce(func.sum(TestAttempt.correct), 0),
            func.max(TestAttempt.created_at),
        )
        .outerjoin(TestAttempt, TestAttempt.user_id == User.id)
        .where(User.registration_status == "approved")
        .group_by(
            User.id,
            User.telegram_id,
            User.full_name,
            User.group_name,
        )
    )

    if group:
        stmt = stmt.where(User.group_name == group)

    rows = [
        StudentRow(
            telegram_id=row[0],
            full_name=row[1] or "Nomsiz",
            group_name=row[2],
            attempts=int(row[3] or 0),
            total=int(row[4] or 0),
            correct=int(row[5] or 0),
            last_attempt=row[6],
        )
        for row in await session.execute(stmt)
    ]

    if sort == SORT_RESULT:
        rows.sort(
            key=lambda r: (r.attempts > 0, r.accuracy, r.attempts),
            reverse=True,
        )
    elif sort == SORT_NAME:
        rows.sort(key=lambda r: r.full_name.casefold())
    else:
        rows.sort(
            key=lambda r: (
                r.last_attempt is not None,
                r.last_attempt or datetime.min,
            ),
            reverse=True,
        )

    return rows


async def group_names(session) -> list[str]:
    rows = await session.scalars(
        select(User.group_name)
        .where(
            User.registration_status == "approved",
            User.group_name.is_not(None),
            User.group_name != "",
        )
        .distinct()
    )

    return sorted({row for row in rows if row}, key=str.casefold)


# ---------------------------------------------------------------
#  Bitta o'quvchi
# ---------------------------------------------------------------

@dataclass
class AttemptRow:
    attempt_id: int
    created_at: datetime
    topic_name: str
    subject_name: str
    total: int
    correct: int
    score: int
    source: str = "quiz"

    @property
    def is_diagnostic(self) -> bool:
        return self.source == "diagnostic"

    @property
    def label(self) -> str:
        if self.is_diagnostic:
            return "\U0001F3AF Diagnostika"

        if self.source == "group":
            return "\U0001F465 Guruh: " + self.topic_name

        return self.topic_name

    @property
    def accuracy(self) -> int:
        return percent(self.correct, self.total)


@dataclass
class SubjectRow:
    name: str
    attempts: int
    total: int
    correct: int

    @property
    def accuracy(self) -> int:
        return percent(self.correct, self.total)


@dataclass
class Trend:
    earlier: int
    recent: int
    window: int
    mode: str

    @property
    def delta(self) -> int:
        return self.recent - self.earlier


@dataclass
class StudentReport:
    telegram_id: int
    full_name: str
    group_name: str | None
    registered_at: datetime | None
    attempts: list[AttemptRow] = field(default_factory=list)
    subjects: list[SubjectRow] = field(default_factory=list)

    @property
    def attempt_count(self) -> int:
        return len(self.attempts)

    @property
    def total(self) -> int:
        return sum(a.total for a in self.attempts)

    @property
    def correct(self) -> int:
        return sum(a.correct for a in self.attempts)

    @property
    def accuracy(self) -> int:
        return percent(self.correct, self.total)

    @property
    def first_attempt(self) -> datetime | None:
        return self.attempts[0].created_at if self.attempts else None

    @property
    def last_attempt(self) -> datetime | None:
        return self.attempts[-1].created_at if self.attempts else None


def compute_trend(attempts: list[AttemptRow]) -> Trend | None:
    """Urinishlar vaqt bo'yicha o'sgan yoki pasayganini aniqlaydi.

    Diagnostika hisobga olinmaydi: u ataylab zaif mavzulardan
    ko'proq savol beradi, shuning uchun natijasi tabiiy ravishda
    past chiqadi va o'sish ko'rsatkichini buzgan bo'lardi.
    """
    attempts = [a for a in attempts if not a.is_diagnostic]

    count = len(attempts)

    if count < MIN_ATTEMPTS_FOR_TREND:
        return None

    if count > RECENT_WINDOW * 2:
        earlier_part = attempts[-RECENT_WINDOW * 2 : -RECENT_WINDOW]
        recent_part = attempts[-RECENT_WINDOW:]
        mode = "oxirgi"
        window = RECENT_WINDOW
    else:
        half = count // 2
        earlier_part = attempts[:half]
        recent_part = attempts[-half:]
        mode = "yarim"
        window = half

    def acc(part):
        return percent(
            sum(a.correct for a in part),
            sum(a.total for a in part),
        )

    return Trend(
        earlier=acc(earlier_part),
        recent=acc(recent_part),
        window=window,
        mode=mode,
    )


async def student_report(session, telegram_id: int) -> StudentReport | None:
    user = await session.scalar(
        select(User).where(User.telegram_id == telegram_id)
    )

    if user is None:
        return None

    rows = await session.execute(
        select(
            TestAttempt.id,
            TestAttempt.created_at,
            TestAttempt.total,
            TestAttempt.correct,
            TestAttempt.score,
            Topic.name,
            Subject.name,
            TestAttempt.source,
        )
        .join(Topic, Topic.id == TestAttempt.topic_id)
        .join(Subject, Subject.id == Topic.subject_id)
        .where(TestAttempt.user_id == user.id)
        .order_by(TestAttempt.created_at.asc())
    )

    attempts = [
        AttemptRow(
            attempt_id=int(row[0]),
            created_at=row[1],
            total=int(row[2] or 0),
            correct=int(row[3] or 0),
            score=int(row[4] or 0),
            topic_name=row[5] or "—",
            subject_name=row[6] or "—",
            source=row[7] or "quiz",
        )
        for row in rows
    ]

    subject_map: dict[str, SubjectRow] = {}

    for attempt in attempts:
        row = subject_map.get(attempt.subject_name)

        if row is None:
            row = SubjectRow(
                name=attempt.subject_name,
                attempts=0,
                total=0,
                correct=0,
            )
            subject_map[attempt.subject_name] = row

        row.attempts += 1
        row.total += attempt.total
        row.correct += attempt.correct

    subjects = sorted(
        subject_map.values(),
        key=lambda r: r.accuracy,
        reverse=True,
    )

    return StudentReport(
        telegram_id=user.telegram_id,
        full_name=user.full_name or "Nomsiz",
        group_name=user.group_name,
        registered_at=user.created_at,
        attempts=attempts,
        subjects=subjects,
    )


# ---------------------------------------------------------------
#  Bitta testning javoblari
# ---------------------------------------------------------------

@dataclass
class AnswerRow:
    question_id: int
    text: str | None
    selected: str | None
    correct_option: str | None
    is_correct: bool
    has_image: bool
    option_a: str | None = None
    option_b: str | None = None
    option_c: str | None = None
    option_d: str | None = None

    def option_text(self, key: str | None) -> str | None:
        if not key:
            return None

        return getattr(self, "option_" + key.lower(), None)


@dataclass
class AttemptDetail:
    attempt_id: int
    created_at: datetime
    topic_name: str
    subject_name: str
    total: int
    correct: int
    score: int
    student_name: str
    student_telegram_id: int
    answers: list = field(default_factory=list)

    @property
    def accuracy(self) -> int:
        return percent(self.correct, self.total)


async def attempt_detail(session, attempt_id: int) -> AttemptDetail | None:
    """Bitta test urinishining har bir savoli va javobi."""
    head = (
        await session.execute(
            select(
                TestAttempt.id,
                TestAttempt.created_at,
                TestAttempt.total,
                TestAttempt.correct,
                TestAttempt.score,
                Topic.name,
                Subject.name,
                User.full_name,
                User.telegram_id,
                TestAttempt.source,
            )
            .join(Topic, Topic.id == TestAttempt.topic_id)
            .join(Subject, Subject.id == Topic.subject_id)
            .join(User, User.id == TestAttempt.user_id)
            .where(TestAttempt.id == attempt_id)
        )
    ).first()

    if head is None:
        return None

    rows = await session.execute(
        select(
            AnswerLog.question_id,
            AnswerLog.selected_option,
            AnswerLog.is_correct,
            Question.text,
            Question.correct_option,
            Question.image_path,
            Question.option_a,
            Question.option_b,
            Question.option_c,
            Question.option_d,
        )
        .outerjoin(Question, Question.id == AnswerLog.question_id)
        .where(AnswerLog.attempt_id == attempt_id)
        .order_by(AnswerLog.id)
    )

    answers = [
        AnswerRow(
            question_id=row[0],
            selected=row[1],
            is_correct=bool(row[2]),
            text=row[3],
            correct_option=row[4],
            has_image=bool(row[5]),
            option_a=row[6],
            option_b=row[7],
            option_c=row[8],
            option_d=row[9],
        )
        for row in rows
    ]

    topic_name = head[5] or "—"
    subject_name = head[6] or "—"

    if head[9] == "group":
        topic_name = "\U0001F465 Guruh: " + topic_name

    if head[9] == "diagnostic":
        # Diagnostika bitta mavzuga tegishli emas: fanlar javoblardan olinadi
        names = await session.scalars(
            select(Subject.name)
            .distinct()
            .select_from(AnswerLog)
            .join(Question, Question.id == AnswerLog.question_id)
            .join(Topic, Topic.id == Question.topic_id)
            .join(Subject, Subject.id == Topic.subject_id)
            .where(AnswerLog.attempt_id == attempt_id)
        )
        topic_name = "\U0001F3AF Diagnostika"
        subject_name = ", ".join(sorted(names)) or "—"

    return AttemptDetail(
        attempt_id=head[0],
        created_at=head[1],
        total=int(head[2] or 0),
        correct=int(head[3] or 0),
        score=int(head[4] or 0),
        topic_name=topic_name,
        subject_name=subject_name,
        student_name=head[7] or "Nomsiz",
        student_telegram_id=head[8],
        answers=answers,
    )


# ---------------------------------------------------------------
#  Eng ko'p xato qilingan savollar
# ---------------------------------------------------------------

@dataclass
class WrongQuestion:
    question_id: int
    text: str | None
    has_image: bool
    topic_name: str
    wrong: int
    total: int

    @property
    def wrong_percent(self) -> int:
        return percent(self.wrong, self.total)


async def wrong_questions(
    session,
    telegram_id: int,
    limit: int = 10,
) -> list[WrongQuestion]:
    """O'quvchi eng ko'p xato qilgan savollar."""
    user = await session.scalar(
        select(User).where(User.telegram_id == telegram_id)
    )

    if user is None:
        return []

    rows = await session.execute(
        select(
            AnswerLog.question_id,
            Question.text,
            Question.image_path,
            Topic.name,
            func.count(AnswerLog.id),
            func.sum(
                case((AnswerLog.is_correct.is_(False), 1), else_=0)
            ),
        )
        .join(TestAttempt, TestAttempt.id == AnswerLog.attempt_id)
        .outerjoin(Question, Question.id == AnswerLog.question_id)
        .outerjoin(Topic, Topic.id == Question.topic_id)
        .where(TestAttempt.user_id == user.id)
        .group_by(
            AnswerLog.question_id,
            Question.text,
            Question.image_path,
            Topic.name,
        )
    )

    items = [
        WrongQuestion(
            question_id=row[0],
            text=row[1],
            has_image=bool(row[2]),
            topic_name=row[3] or "—",
            total=int(row[4] or 0),
            wrong=int(row[5] or 0),
        )
        for row in rows
    ]

    items = [item for item in items if item.wrong]
    items.sort(key=lambda i: (i.wrong, i.wrong_percent), reverse=True)

    return items[:limit]


@dataclass
class QuestionImage:
    question_id: int
    text: str | None
    image_path: str | None
    correct_option: str | None
    topic_name: str


async def question_image(session, question_id: int) -> QuestionImage | None:
    """Savol rasmi va to'g'ri javobi (hisobotda ko'rsatish uchun)."""
    row = (
        await session.execute(
            select(
                Question.id,
                Question.text,
                Question.image_path,
                Question.correct_option,
                Topic.name,
            )
            .outerjoin(Topic, Topic.id == Question.topic_id)
            .where(Question.id == question_id)
        )
    ).first()

    if row is None:
        return None

    return QuestionImage(
        question_id=row[0],
        text=row[1],
        image_path=row[2],
        correct_option=row[3],
        topic_name=row[4] or "—",
    )

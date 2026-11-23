"""Ustoz topshiriqlari — hisob-kitob qismi.

Joylashuvi: app/services/assignments.py

Topshiriq: "shu mavzudan N ta savollik test ishlang, muddat —
falon sana". Bajarilganini aniqlash uchun test oqimiga tegilmaydi:
topshiriq yaratilgandan keyin o'sha mavzudan yetarli savolli test
ishlangan bo'lsa, bajarilgan hisoblanadi. Shuning uchun mavjud
test kodiga hech qanday o'zgartirish kiritilmaydi.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, time, timedelta

from sqlalchemy import and_, case, distinct, func, or_, select

from ..models import (
    AnswerLog,
    Assignment,
    Question,
    Subject,
    TestAttempt,
    Topic,
    User,
)

# Muddat kuni soat nechada tugaydi (mahalliy vaqt)
DEADLINE_HOUR = 20


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


def fmt(value: datetime | None) -> str:
    local = to_local(value)

    if local is None:
        return "—"

    return local.strftime("%d.%m.%Y %H:%M")


def deadline_after(days: int, now: datetime | None = None) -> datetime:
    """N kundan keyingi mahalliy 20:00 ni UTC ga o'girib qaytaradi."""
    moment = now or datetime.utcnow()
    local_now = moment + local_offset()

    target_local = datetime.combine(
        (local_now + timedelta(days=days)).date(),
        time(hour=DEADLINE_HOUR),
    )

    return target_local - local_offset()


def deadline_label(days: int) -> str:
    if days == 1:
        return f"Ertaga {DEADLINE_HOUR}:00"

    if days == 7:
        return f"1 hafta ({DEADLINE_HOUR}:00)"

    return f"{days} kundan keyin ({DEADLINE_HOUR}:00)"


# ---------------------------------------------------------------
#  Yaratish
# ---------------------------------------------------------------

async def create_assignment(
    session,
    *,
    topic_id: int,
    created_by: int,
    required_questions: int,
    days: int,
    group_name: str | None = None,
    user_id: int | None = None,
) -> Assignment:
    assignment = Assignment(
        topic_id=topic_id,
        created_by=created_by,
        required_questions=required_questions,
        group_name=group_name,
        user_id=user_id,
        deadline=deadline_after(days),
        is_active=True,
    )

    session.add(assignment)
    await session.commit()

    return assignment


async def assignment_with_names(session, assignment_id: int):
    """(Assignment, mavzu nomi, fan nomi)"""
    row = (
        await session.execute(
            select(Assignment, Topic.name, Subject.name)
            .join(Topic, Topic.id == Assignment.topic_id)
            .join(Subject, Subject.id == Topic.subject_id)
            .where(Assignment.id == assignment_id)
        )
    ).first()

    if row is None:
        return None, None, None

    return row[0], row[1], row[2]


async def active_assignments(session, *, user: User | None = None):
    """Muddati o'tmagan topshiriqlar.

    user berilsa — faqat o'shanga tegishlilari: shaxsiy topshiriq,
    uning guruhiga berilgani yoki hammaga berilgani.
    """
    now = datetime.utcnow()

    stmt = (
        select(Assignment, Topic.name, Subject.name)
        .join(Topic, Topic.id == Assignment.topic_id)
        .join(Subject, Subject.id == Topic.subject_id)
        .where(
            Assignment.is_active.is_(True),
            Assignment.deadline > now,
        )
        .order_by(Assignment.deadline.asc())
    )

    if user is not None:
        stmt = stmt.where(
            or_(
                Assignment.user_id == user.id,
                and_(
                    Assignment.user_id.is_(None),
                    or_(
                        Assignment.group_name.is_(None),
                        Assignment.group_name == user.group_name,
                    ),
                ),
            )
        )

    return list(await session.execute(stmt))


async def all_assignments(
    session,
    limit: int = 30,
    include_removed: bool = False,
):
    stmt = (
        select(Assignment, Topic.name, Subject.name)
        .join(Topic, Topic.id == Assignment.topic_id)
        .join(Subject, Subject.id == Topic.subject_id)
        .order_by(Assignment.id.desc())
        .limit(limit)
    )

    if not include_removed:
        stmt = stmt.where(Assignment.is_active.is_(True))

    return list(await session.execute(stmt))


async def remove_assignment(session, assignment_id: int) -> bool:
    """Topshiriqni olib tashlaydi (yumshoq o'chirish).

    Yozuv bazada qoladi, lekin hech qayerda ko'rinmaydi:
    o'quvchilar ro'yxatidan ham, eslatmalardan ham chiqadi.
    Test natijalari esa o'z joyida qolaveradi.
    """
    assignment = await session.get(Assignment, assignment_id)

    if assignment is None:
        return False

    assignment.is_active = False
    await session.commit()

    return True


# ---------------------------------------------------------------
#  Bajarilganini aniqlash
# ---------------------------------------------------------------

@dataclass
class ProgressRow:
    telegram_id: int
    full_name: str
    group_name: str | None
    done: bool
    best_correct: int
    best_total: int
    answered_best: int
    required: int
    done_at: datetime | None

    @property
    def accuracy(self) -> int:
        """To'g'ri javoblar foizi (bajarilgan urinishda)."""
        if not self.best_total:
            return 0

        return int(round(self.best_correct / self.best_total * 100))

    @property
    def progress_percent(self) -> int:
        """Topshiriq necha foizga bajarilgan."""
        if not self.required:
            return 100 if self.done else 0

        value = int(round(self.answered_best / self.required * 100))

        return min(100, max(0, value))


async def target_users(session, assignment: Assignment) -> list[User]:
    """Topshiriq kimlarga tegishli."""
    if assignment.user_id:
        user = await session.get(User, assignment.user_id)

        return [user] if user is not None else []

    stmt = select(User).where(
        User.registration_status == "approved",
        User.is_blocked.is_(False),
    )

    if assignment.group_name:
        stmt = stmt.where(User.group_name == assignment.group_name)

    return list(await session.scalars(stmt))


@dataclass
class WeakTopic:
    topic_id: int
    topic_name: str
    subject_name: str
    answered: int
    correct: int
    distinct_questions: int
    available_questions: int

    @property
    def accuracy(self) -> int:
        if not self.answered:
            return 0

        return int(round(self.correct / self.answered * 100))


async def weak_topics_for_user(
    session,
    user_id: int,
    limit: int = 4,
    min_distinct: int = 3,
) -> list[WeakTopic]:
    """O'quvchining eng zaif mavzulari (shaxsiy topshiriq uchun)."""
    rows = await session.execute(
        select(
            Topic.id,
            Topic.name,
            Subject.name,
            func.count(AnswerLog.id),
            func.sum(
                case((AnswerLog.is_correct.is_(True), 1), else_=0)
            ),
            func.count(distinct(AnswerLog.question_id)),
        )
        .join(TestAttempt, TestAttempt.id == AnswerLog.attempt_id)
        .join(Question, Question.id == AnswerLog.question_id)
        .join(Topic, Topic.id == Question.topic_id)
        .join(Subject, Subject.id == Topic.subject_id)
        .where(
            TestAttempt.user_id == user_id,
            Question.is_active.is_(True),
        )
        .group_by(Topic.id, Topic.name, Subject.name)
        .having(func.count(distinct(AnswerLog.question_id)) >= min_distinct)
    )

    items = []

    for row in rows:
        items.append(
            WeakTopic(
                topic_id=int(row[0]),
                topic_name=row[1] or "—",
                subject_name=row[2] or "—",
                answered=int(row[3] or 0),
                correct=int(row[4] or 0),
                distinct_questions=int(row[5] or 0),
                available_questions=0,
            )
        )

    items.sort(key=lambda item: item.accuracy)
    items = items[:limit]

    for item in items:
        item.available_questions = int(
            await session.scalar(
                select(func.count(Question.id)).where(
                    Question.topic_id == item.topic_id,
                    Question.is_active.is_(True),
                )
            )
            or 0
        )

    return items


async def assignment_progress(
    session,
    assignment: Assignment,
) -> list[ProgressRow]:
    """Kim bajardi, kim bajarmadi."""
    users = await target_users(session, assignment)

    if not users:
        return []

    user_ids = [user.id for user in users]

    rows = await attempts_in_window(session, assignment, user_ids)

    required = assignment.required_questions

    done_map = {}
    partial_map = {}

    for user_id, _attempt_id, correct, answered, created_at in rows:
        # Qisman bajarish: eng ko'p javob berilgan urinish
        partial_map[user_id] = max(
            partial_map.get(user_id, 0), answered
        )

        if answered < required:
            continue

        current = done_map.get(user_id)

        if current is None:
            done_map[user_id] = (correct, answered, created_at)
            continue

        best_correct, best_answered, first_at = current

        if correct > best_correct:
            best_correct, best_answered = correct, answered

        if created_at < first_at:
            first_at = created_at

        done_map[user_id] = (best_correct, best_answered, first_at)

    result = []

    for user in users:
        entry = done_map.get(user.id)

        result.append(
            ProgressRow(
                telegram_id=user.telegram_id,
                full_name=user.full_name or "Nomsiz",
                group_name=user.group_name,
                done=entry is not None,
                best_correct=entry[0] if entry else 0,
                best_total=entry[1] if entry else 0,
                answered_best=partial_map.get(user.id, 0),
                required=required,
                done_at=entry[2] if entry else None,
            )
        )

    result.sort(key=lambda r: (not r.done, r.full_name.casefold()))

    return result


async def attempts_in_window(
    session,
    assignment: Assignment,
    user_ids: list[int] | None = None,
):
    """Topshiriq oynasidagi barcha urinishlar (qisman ham).

    MUHIM: testdagi savollar soni emas, HAQIQATDA JAVOB BERILGAN
    savollar sanaladi. Bot test natijasiga testdagi savollar sonini
    yozadi, shuning uchun 20 talik testni boshlab ikkitasiga javob
    berib chiqqan o'quvchi ham "bajardi" bo'lib qolardi. Javoblar
    esa answer_logs da alohida yotadi — shuni sanaymiz.

    Qaytaradi: (user_id, attempt_id, correct, answered, created_at)
    """
    started = assignment.created_at or datetime.min

    stmt = (
        select(
            TestAttempt.user_id,
            TestAttempt.id,
            TestAttempt.correct,
            func.count(AnswerLog.id),
            TestAttempt.created_at,
        )
        .join(AnswerLog, AnswerLog.attempt_id == TestAttempt.id)
        .where(
            TestAttempt.topic_id == assignment.topic_id,
            TestAttempt.created_at >= started,
            TestAttempt.created_at <= assignment.deadline,
        )
        .group_by(
            TestAttempt.id,
            TestAttempt.user_id,
            TestAttempt.correct,
            TestAttempt.created_at,
        )
    )

    if user_ids:
        stmt = stmt.where(TestAttempt.user_id.in_(user_ids))

    return [
        (
            int(row[0]),
            int(row[1]),
            int(row[2] or 0),
            int(row[3] or 0),
            row[4],
        )
        for row in await session.execute(stmt)
    ]


async def qualifying_attempts(
    session,
    assignment: Assignment,
    user_ids: list[int] | None = None,
):
    """Topshiriqni to'liq bajargan urinishlar."""
    rows = await attempts_in_window(session, assignment, user_ids)

    return [
        row for row in rows
        if row[3] >= assignment.required_questions
    ]


async def student_status(
    session,
    user: User,
    assignment: Assignment,
) -> bool:
    """Bitta o'quvchi topshiriqni bajarganmi."""
    rows = await qualifying_attempts(session, assignment, [user.id])

    return bool(rows)


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
#  Muddat eslatmalari uchun
# ---------------------------------------------------------------

async def assignments_due_soon(
    session,
    now: datetime | None = None,
    within_hours: int = 24,
):
    """Muddati yaqin topshiriqlar (hali tugamagan)."""
    moment = now or datetime.utcnow()
    edge = moment + timedelta(hours=within_hours)

    stmt = (
        select(Assignment, Topic.name, Subject.name)
        .join(Topic, Topic.id == Assignment.topic_id)
        .join(Subject, Subject.id == Topic.subject_id)
        .where(
            Assignment.is_active.is_(True),
            Assignment.deadline > moment,
            Assignment.deadline <= edge,
        )
        .order_by(Assignment.deadline.asc())
    )

    return list(await session.execute(stmt))


async def assignments_just_ended(
    session,
    now: datetime | None = None,
    within_hours: int = 24,
):
    """Muddati yaqinda tugagan topshiriqlar (ustozga hisobot uchun)."""
    moment = now or datetime.utcnow()
    edge = moment - timedelta(hours=within_hours)

    stmt = (
        select(Assignment, Topic.name, Subject.name)
        .join(Topic, Topic.id == Assignment.topic_id)
        .join(Subject, Subject.id == Topic.subject_id)
        .where(
            Assignment.is_active.is_(True),
            Assignment.deadline <= moment,
            Assignment.deadline > edge,
        )
        .order_by(Assignment.deadline.asc())
    )

    return list(await session.execute(stmt))


async def pending_students(session, assignment: Assignment) -> list[User]:
    """Topshiriqni hali bajarmaganlar."""
    users = await target_users(session, assignment)

    result = []

    for user in users:
        if user is None:
            continue

        done = await student_status(session, user, assignment)

        if not done:
            result.append(user)

    return result


async def student_progress(
    session,
    user: User,
    assignment: Assignment,
) -> tuple[int, int, bool]:
    """(javob berilgan, kerak, bajarildimi) — bitta o'quvchi uchun."""
    rows = await attempts_in_window(session, assignment, [user.id])

    best = max((row[3] for row in rows), default=0)
    required = assignment.required_questions

    return best, required, best >= required


def progress_percent(answered: int, required: int) -> int:
    if not required:
        return 100 if answered else 0

    return min(100, max(0, int(round(answered / required * 100))))

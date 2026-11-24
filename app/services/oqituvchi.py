"""O'qituvchi (ustoz) tizimi: ma'lumot olish yordamchilari.

Joylashuvi: app/services/oqituvchi.py

Ustoz faqat o'ziga biriktirilgan guruhlarni ko'radi. Guruh nomlari
o'quvchilar tomonidan qo'lda yoziladi, shuning uchun solishtirishda
bo'shliqlar va harf kattaligi hisobga olinmaydi.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select

from ..models import Teacher, TeacherGroup, TestAttempt, User

INACTIVE_DAYS = 7


def norm(name: str | None) -> str:
    """Guruh nomini solishtirish uchun qulay ko'rinishga keltiradi."""
    return " ".join((name or "").split()).casefold()


def percent(correct: int, total: int) -> int:
    if not total:
        return 0
    return int(round(correct / total * 100))


@dataclass
class Student:
    user_id: int
    telegram_id: int
    full_name: str
    username: str | None
    group_name: str | None
    is_blocked: bool
    attempts: int
    total: int
    correct: int
    last_attempt: datetime | None

    @property
    def accuracy(self) -> int:
        return percent(self.correct, self.total)

    def days_idle(self, now: datetime | None = None) -> int | None:
        if self.last_attempt is None:
            return None
        moment = now or datetime.utcnow()
        return max(0, (moment - self.last_attempt).days)

    def is_inactive(self, now: datetime | None = None) -> bool:
        idle = self.days_idle(now)
        return idle is None or idle >= INACTIVE_DAYS


async def get_teacher(session, telegram_id: int):
    """(Teacher, User) yoki None. Faqat tasdiqlangan, bloklanmagan ustoz."""
    row = (
        await session.execute(
            select(Teacher, User)
            .join(User, User.id == Teacher.user_id)
            .where(
                User.telegram_id == telegram_id,
                User.registration_status == "approved",
                User.is_blocked.is_(False),
            )
        )
    ).first()

    if row is None:
        return None

    return row[0], row[1]


async def teacher_groups(session, teacher_id: int) -> list[TeacherGroup]:
    rows = (
        await session.scalars(
            select(TeacherGroup).where(TeacherGroup.teacher_id == teacher_id)
        )
    ).all()
    return sorted(rows, key=lambda g: norm(g.group_name))


async def group_students(session, group_name: str) -> list[Student]:
    """Guruhdagi tasdiqlangan o'quvchilar (natijalari bilan)."""
    key = norm(group_name)

    stmt = (
        select(
            User.id,
            User.telegram_id,
            User.full_name,
            User.username,
            User.group_name,
            User.is_blocked,
            func.count(TestAttempt.id),
            func.coalesce(func.sum(TestAttempt.total), 0),
            func.coalesce(func.sum(TestAttempt.correct), 0),
            func.max(TestAttempt.created_at),
        )
        .outerjoin(TestAttempt, TestAttempt.user_id == User.id)
        .where(
            User.registration_status == "approved",
            User.group_name.is_not(None),
        )
        .group_by(
            User.id,
            User.telegram_id,
            User.full_name,
            User.username,
            User.group_name,
            User.is_blocked,
        )
    )

    students = []

    for row in await session.execute(stmt):
        if norm(row[4]) != key:
            continue

        students.append(
            Student(
                user_id=row[0],
                telegram_id=row[1],
                full_name=row[2] or "Nomsiz",
                username=row[3],
                group_name=row[4],
                is_blocked=bool(row[5]),
                attempts=int(row[6] or 0),
                total=int(row[7] or 0),
                correct=int(row[8] or 0),
                last_attempt=row[9],
            )
        )

    students.sort(key=lambda s: s.full_name.casefold())
    return students


async def all_group_names(session) -> list[tuple[str, int]]:
    """Mavjud guruhlar: (ko'rinadigan nom, o'quvchilar soni).

    Faqat harf kattaligi/bo'shliq bilan farq qiladigan yozuvlar
    bitta guruh sanaladi; nom sifatida eng ko'p uchraganı olinadi.
    """
    rows = await session.execute(
        select(User.group_name).where(
            User.registration_status == "approved",
            User.group_name.is_not(None),
        )
    )

    spellings: dict[str, Counter] = {}

    for (raw,) in rows:
        clean = " ".join((raw or "").split())
        if not clean:
            continue
        spellings.setdefault(norm(clean), Counter())[clean] += 1

    result = [
        (counter.most_common(1)[0][0], sum(counter.values()))
        for counter in spellings.values()
    ]
    result.sort(key=lambda item: norm(item[0]))
    return result

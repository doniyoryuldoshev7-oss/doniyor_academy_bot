"""Savollar sifati statistikasi — hisob-kitob qismi.

Joylashuvi: app/services/question_stats.py

Mavjud javoblar tarixidan (answer_logs) har bir savol uchun
ko'rsatkich hisoblanadi:

    qiyinlik      -- to'g'ri javoblar ulushi
    taqsimot      -- A/B/C/D variantlari necha marta tanlangan

Eng muhim signal: agar o'quvchilarning ko'pchiligi bitta xato
variantni tanlayotgan bo'lsa, odatda savol emas, kalit xato
belgilangan bo'ladi. AI bilan yasalgan savollarda bu eng ko'p
uchraydigan nuqson.
"""

from __future__ import annotations
from ..numfmt import pct_int, round_half_up

import os
from dataclasses import dataclass

from sqlalchemy import case, func, select

from ..models import AnswerLog, Question, Subject, Topic

OPTIONS = ("A", "B", "C", "D")

# Shuncha javobdan kam bo'lsa, statistika ishonchsiz
DEFAULT_MIN_ANSWERS = 5

KIND_SUSPECT = "kalit"
KIND_HARD = "qiyin"
KIND_EASY = "oson"
KIND_DEAD = "olik"

KIND_TITLES = {
    KIND_SUSPECT: "⚠️ Kaliti shubhali",
    KIND_HARD: "\U0001F534 Juda qiyin",
    KIND_EASY: "\U0001F7E2 Juda oson",
    KIND_DEAD: "\U0001F4A4 Ishlatilmagan variant",
}


def min_answers() -> int:
    raw = (os.getenv("QUESTION_STATS_MIN_ANSWERS") or "").strip()

    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_MIN_ANSWERS

    if not 1 <= value <= 100:
        return DEFAULT_MIN_ANSWERS

    return value


@dataclass
class QuestionStat:
    question_id: int
    text: str | None
    has_image: bool
    question_mode: str
    topic_name: str
    subject_name: str
    correct_option: str | None
    answers: int
    correct: int
    picks: dict
    options: dict

    @property
    def difficulty(self) -> float:
        """To'g'ri javoblar ulushi: 0.0 (hech kim) ... 1.0 (hamma)."""
        if not self.answers:
            return 0.0

        return self.correct / self.answers

    @property
    def difficulty_percent(self) -> int:
        return pct_int(self.correct, self.answers)

    def pick_percent(self, key: str) -> int:
        if not self.answers:
            return 0

        return pct_int(self.picks.get(key, 0), self.answers)

    @property
    def top_wrong(self) -> tuple[str, int] | None:
        """Eng ko'p tanlangan xato variant."""
        correct = (self.correct_option or "").upper()

        wrong = [
            (key, self.picks.get(key, 0))
            for key in OPTIONS
            if key != correct
        ]

        if not wrong:
            return None

        key, count = max(wrong, key=lambda item: item[1])

        if not count:
            return None

        return key, count

    @property
    def dead_options(self) -> list[str]:
        """Hech kim tanlamagan variantlar (faqat matnli savollarda)."""
        if self.has_image:
            return []

        result = []

        for key in OPTIONS:
            value = self.options.get(key)

            if value is None or str(value).strip() == "":
                continue

            if not self.picks.get(key):
                result.append(key)

        return result

    @property
    def kinds(self) -> list[str]:
        """Savol qaysi muammoli toifalarga tushadi."""
        found = []

        top = self.top_wrong

        if top is not None:
            key, count = top
            share = count / self.answers

            if share >= 0.5 and count > self.correct and self.difficulty < 0.4:
                found.append(KIND_SUSPECT)

        if KIND_SUSPECT not in found and self.difficulty < 0.25:
            found.append(KIND_HARD)

        if self.difficulty > 0.95:
            found.append(KIND_EASY)

        if self.answers >= 10 and self.dead_options:
            found.append(KIND_DEAD)

        return found

    @property
    def verdict(self) -> str:
        """Qisqa xulosa — nima qilish kerakligi."""
        kinds = self.kinds

        if KIND_SUSPECT in kinds:
            top = self.top_wrong
            key = top[0] if top else "—"

            return (
                "Ko'pchilik <b>" + key + "</b> ni tanlagan, kalit esa <b>"
                + (self.correct_option or "—")
                + "</b>. Kalitni tekshiring."
            )

        if KIND_HARD in kinds:
            return (
                "Deyarli hamma xato qilyapti. Savol matni tushunarsiz "
                "yoki mavzu o'tilmagan bo'lishi mumkin."
            )

        if KIND_EASY in kinds:
            return (
                "Deyarli hamma to'g'ri javob beryapti — savol "
                "bilimni ajratmayapti."
            )

        if KIND_DEAD in kinds:
            return (
                "Hech kim tanlamagan variant bor: "
                + ", ".join(self.dead_options)
                + ". Ularni jonliroq qilish mumkin."
            )

        return "Ko'rsatkichlar me'yorda."


async def question_stats(
    session,
    *,
    subject_id: int | None = None,
    limit_answers: int | None = None,
) -> list[QuestionStat]:
    """Barcha faol savollar bo'yicha statistika."""
    threshold = limit_answers or min_answers()

    def picks(option: str):
        return func.sum(
            case((AnswerLog.selected_option == option, 1), else_=0)
        )

    stmt = (
        select(
            Question.id,
            Question.text,
            Question.image_path,
            Question.question_mode,
            Question.correct_option,
            Question.option_a,
            Question.option_b,
            Question.option_c,
            Question.option_d,
            Topic.name,
            Subject.name,
            func.count(AnswerLog.id),
            func.sum(
                case((AnswerLog.is_correct.is_(True), 1), else_=0)
            ),
            picks("A"),
            picks("B"),
            picks("C"),
            picks("D"),
        )
        .join(AnswerLog, AnswerLog.question_id == Question.id)
        .join(Topic, Topic.id == Question.topic_id)
        .join(Subject, Subject.id == Topic.subject_id)
        .where(Question.is_active.is_(True))
        .group_by(
            Question.id,
            Question.text,
            Question.image_path,
            Question.question_mode,
            Question.correct_option,
            Question.option_a,
            Question.option_b,
            Question.option_c,
            Question.option_d,
            Topic.name,
            Subject.name,
        )
        .having(func.count(AnswerLog.id) >= threshold)
    )

    if subject_id is not None:
        stmt = stmt.where(Subject.id == subject_id)

    rows = await session.execute(stmt)

    result = []

    for row in rows:
        result.append(
            QuestionStat(
                question_id=row[0],
                text=row[1],
                has_image=bool(row[2]),
                question_mode=row[3] or "closed",
                correct_option=(row[4] or "").upper() or None,
                options={
                    "A": row[5],
                    "B": row[6],
                    "C": row[7],
                    "D": row[8],
                },
                topic_name=row[9] or "—",
                subject_name=row[10] or "—",
                answers=int(row[11] or 0),
                correct=int(row[12] or 0),
                picks={
                    "A": int(row[13] or 0),
                    "B": int(row[14] or 0),
                    "C": int(row[15] or 0),
                    "D": int(row[16] or 0),
                },
            )
        )

    return result


@dataclass
class StatsSummary:
    analysed: int
    suspect: list
    hard: list
    easy: list
    dead: list
    average_difficulty: int
    threshold: int

    @property
    def problems(self) -> int:
        return len(self.suspect) + len(self.hard) + len(self.easy) + len(self.dead)


def summarize(stats: list[QuestionStat]) -> StatsSummary:
    suspect = []
    hard = []
    easy = []
    dead = []

    for stat in stats:
        kinds = stat.kinds

        if KIND_SUSPECT in kinds:
            suspect.append(stat)

        if KIND_HARD in kinds:
            hard.append(stat)

        if KIND_EASY in kinds:
            easy.append(stat)

        if KIND_DEAD in kinds:
            dead.append(stat)

    suspect.sort(key=lambda s: s.difficulty)
    hard.sort(key=lambda s: s.difficulty)
    easy.sort(key=lambda s: -s.answers)
    dead.sort(key=lambda s: -s.answers)

    if stats:
        average = int(
            round_half_up(sum(s.difficulty for s in stats) / len(stats) * 100)
        )
    else:
        average = 0

    return StatsSummary(
        analysed=len(stats),
        suspect=suspect,
        hard=hard,
        easy=easy,
        dead=dead,
        average_difficulty=average,
        threshold=min_answers(),
    )


def by_kind(summary: StatsSummary, kind: str) -> list:
    return {
        KIND_SUSPECT: summary.suspect,
        KIND_HARD: summary.hard,
        KIND_EASY: summary.easy,
        KIND_DEAD: summary.dead,
    }.get(kind, [])

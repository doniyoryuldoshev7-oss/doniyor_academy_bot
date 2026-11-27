"""Imtihon rejimi: hisob-kitob qismi (Milliy sertifikat shabloni bilan).

Joylashuvi: app/services/imtihon.py

Imtihon yaratilganda savollar bazadan tanlanib, imtihonning o'ziga
"surat" (snapshot) sifatida saqlanadi. Shuning uchun keyin savol
tahrirlansa yoki o'chirilsa ham, o'tgan imtihon o'zgarmaydi.

Natija hisobi ("savol og'irligi bilan"):
  * yopiq savol — 1 birlik, ochiq savolning har bir qismi (a, b) — 1.5 birlik;
  * ishtirokchilar yetarli bo'lsa (MIN_STATS), savolning og'irligi uni
    to'g'ri yechganlar ulushiga qarab 0.5 dan 1.5 gacha o'zgaradi:
    qiyin savol ko'proq ball beradi;
  * ball = olingan og'irlik / umumiy og'irlik * 75.
Bu rasmiy Rasch modeli emas, mashq uchun taxminiy hisob.
"""

from __future__ import annotations
from ..tg_retry import retry as _tg_retry
from ..numfmt import pct_int, round_half_up

import asyncio
import itertools
import json
import logging
import random
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import func, select

from ..models import (
    Exam,
    ExamAttempt,
    ExamItem,
    OpenQuestion,
    Question,
    Topic,
    User,
)

log = logging.getLogger(__name__)

CLOSED_WEIGHT = 1.0
OPEN_PART_WEIGHT = 1.5
MIN_STATS = 5
SCALE_MAX = 75.0

# (eng kam ball, daraja)
LEVELS = [(70, "A+"), (65, "A"), (60, "B+"), (55, "B"), (50, "C+"), (46, "C")]

MILLIY_CLOSED = 35
MILLIY_OPEN = 10
MAX_OPEN_PARTS = 4
MAX_ANSWER_LEN = 200

PUNCT = ".,;:!?\"«»()[]{}—–-"
APOS = str.maketrans({
    "ʻ": "'", "ʼ": "'", "‘": "'", "’": "'", "`": "'", "´": "'", "′": "'",
})


def level_for(score: float) -> str:
    for minimum, name in LEVELS:
        if score >= minimum:
            return name
    return "Daraja yo'q"


# ---------------------------------------------------------------
#  Ochiq javobni tekshirish
# ---------------------------------------------------------------
def normalize(text: str | None) -> str:
    text = (text or "").translate(APOS).lower()
    text = text.strip(PUNCT + " ")
    return " ".join(text.split())


def compact(text: str | None) -> str:
    """Faqat harf-raqamlar (apostrof, bo'shliq, tire tashlab yuboriladi)."""
    return re.sub(r"[^0-9a-zа-яёўқғҳ]", "", normalize(text))


def _tokens(text: str) -> list[str]:
    parts = re.split(r"[,;/\n]+|\s+va\s+|\s+and\s+|\s*&\s*", (text or "").lower())
    return [c for c in (compact(p) for p in parts) if c]


def is_match(answer: str | None, accepted: list[str]) -> bool:
    """Javob qabul qilinadigan variantlardan biriga mos keladimi.

    "a + b" ko'rinishidagi variant — ikkalasi ham yozilishi kerak
    (tartibi muhim emas).
    """
    typed = compact(answer)

    if not typed:
        return False

    for variant in accepted:
        if "+" in variant:
            need = [compact(x) for x in variant.split("+")]
            need = [x for x in need if x]

            if not need:
                continue

            if set(_tokens(answer or "")) == set(need) and len(_tokens(answer or "")) == len(need):
                return True

            if len(need) <= 4:
                for order in itertools.permutations(need):
                    if typed == "".join(order):
                        return True
        else:
            if typed == compact(variant):
                return True

    return False


# ---------------------------------------------------------------
#  Ochiq savollarni matndan o'qish
# ---------------------------------------------------------------
PART_RE = re.compile(r"^\s*([a-dA-D])\s*[\)\.]\s*(.*)$")


def parse_open_text(text: str):
    """Matnni ochiq savollarga ajratadi.

    Format (savollar `---` qatori bilan ajratiladi):

        Savol matni (bir necha qator bo'lishi mumkin)
        a) birinchi qism savoli => javob1 | javob2
        b) ikkinchi qism savoli => javob1 + javob2

    `|` — muqobil to'g'ri javoblar; `+` — ikkalasi ham yozilishi kerak.
    Qaytaradi: (savollar, xatolar).
    """
    blocks = re.split(r"^\s*(?:---+|\*\*\*+)\s*$", text or "", flags=re.M)
    questions, errors = [], []

    for number, block in enumerate(blocks, start=1):
        lines = [ln.rstrip() for ln in block.strip().splitlines() if ln.strip()]

        if not lines:
            continue

        stem_lines, parts, bad = [], [], None

        for line in lines:
            match = PART_RE.match(line)

            if match and "=>" in match.group(2):
                prompt, _, answers = match.group(2).partition("=>")
                variants = [a.strip() for a in answers.split("|") if a.strip()]

                if not prompt.strip() or not variants:
                    bad = f"{match.group(1)}) qismida savol yoki javob bo'sh"
                    break

                parts.append({"q": prompt.strip(), "a": variants})
            elif parts:
                bad = f"tushunarsiz qator: «{line[:40]}»"
                break
            else:
                stem_lines.append(line.strip())

        if bad is None and not parts:
            bad = "birorta ham «a) savol => javob» qismi yo'q"

        if bad is None and len(parts) > MAX_OPEN_PARTS:
            bad = f"qismlar {MAX_OPEN_PARTS} tadan oshmasin"

        if bad is None and not stem_lines:
            bad = "savol matni yo'q"

        if bad:
            errors.append(f"{number}-savol: {bad}")
            continue

        questions.append({"stem": " ".join(stem_lines)[:3000], "parts": parts})

    return questions, errors


# ---------------------------------------------------------------
#  Imtihon elementlari
# ---------------------------------------------------------------
@dataclass
class Item:
    pos: int
    kind: str          # "c" yopiq, "o" ochiq
    data: dict


@dataclass
class Unit:
    key: str
    pos: int
    part: int | None
    base: float


def load_items_from_rows(rows) -> list[Item]:
    items = []
    for row in sorted(rows, key=lambda r: r.position):
        try:
            data = json.loads(row.payload)
        except ValueError:
            continue
        items.append(Item(pos=row.position, kind=data.get("k", "c"), data=data))
    return items


async def load_items(session, exam_id: int) -> list[Item]:
    rows = (
        await session.scalars(select(ExamItem).where(ExamItem.exam_id == exam_id))
    ).all()
    return load_items_from_rows(rows)


def build_units(items: list[Item]) -> list[Unit]:
    units = []
    for it in items:
        if it.kind == "c":
            units.append(Unit(str(it.pos), it.pos, None, CLOSED_WEIGHT))
        else:
            for i, _ in enumerate(it.data.get("p", [])):
                units.append(Unit(f"{it.pos}.{i}", it.pos, i, OPEN_PART_WEIGHT))
    return units


def unit_correct(item: Item, unit: Unit, answers: dict) -> bool:
    if item.kind == "c":
        return answers.get(str(item.pos)) == item.data.get("c")

    if answers.get("!" + unit.key):  # ustoz qo'lda qabul qilgan
        return True

    part = item.data["p"][unit.part]
    return is_match(answers.get(unit.key), part["a"])


def unit_answered(unit: Unit, answers: dict) -> bool:
    return bool((answers.get(unit.key) or "").strip())


def item_answered(item: Item, answers: dict) -> bool:
    if item.kind == "c":
        return bool(answers.get(str(item.pos)))
    return any(
        (answers.get(f"{item.pos}.{i}") or "").strip()
        for i, _ in enumerate(item.data.get("p", []))
    )


# ---------------------------------------------------------------
#  Ball hisobi
# ---------------------------------------------------------------
def difficulty_factors(items: list[Item], answers_list: list[dict]) -> dict | None:
    """Har bir birlik uchun og'irlik koeffitsienti (yoki None)."""
    if len(answers_list) < MIN_STATS:
        return None

    by_pos = {it.pos: it for it in items}
    factors = {}

    for unit in build_units(items):
        item = by_pos[unit.pos]
        ok = sum(1 for a in answers_list if unit_correct(item, unit, a))
        share = ok / len(answers_list)
        factors[unit.key] = 0.5 + (1.0 - share)

    return factors


@dataclass
class Score:
    value: float = 0.0        # milliy: 75 lik ball; oddiy: foiz
    percent: int = 0
    c_ok: int = 0
    c_total: int = 0
    o_ok: int = 0
    o_total: int = 0
    weighted: bool = False
    level: str = ""
    wrong: list = field(default_factory=list)   # noto'g'ri/javobsiz birliklar


def score_attempt(items: list[Item], answers: dict, factors: dict | None, mode: str) -> Score:
    by_pos = {it.pos: it for it in items}
    sc = Score(weighted=factors is not None)
    earned = possible = 0.0
    raw_ok = raw_total = 0

    for unit in build_units(items):
        item = by_pos[unit.pos]
        weight = unit.base * (factors[unit.key] if factors else 1.0)
        ok = unit_correct(item, unit, answers)

        possible += weight
        raw_total += 1

        if item.kind == "c":
            sc.c_total += 1
            sc.c_ok += int(ok)
        else:
            sc.o_total += 1
            sc.o_ok += int(ok)

        if ok:
            earned += weight
            raw_ok += 1
        else:
            sc.wrong.append(unit)

    share = earned / possible if possible else 0.0
    sc.percent = pct_int(raw_ok, raw_total) if raw_total else 0

    if mode == "milliy":
        sc.value = round_half_up(share * SCALE_MAX, 1)
        sc.level = level_for(sc.value)
    else:
        sc.value = round_half_up(share * 100, 1)

    return sc


@dataclass
class Result:
    attempt: ExamAttempt
    user: User
    score: Score
    minutes: int


async def exam_results(session, exam: Exam) -> tuple[list[Item], list[Result]]:
    items = await load_items(session, exam.id)

    rows = (
        await session.execute(
            select(ExamAttempt, User)
            .join(User, User.id == ExamAttempt.user_id)
            .where(
                ExamAttempt.exam_id == exam.id,
                ExamAttempt.status == "finished",
            )
        )
    ).all()

    answers_list = [_loads(a.answers) for a, _ in rows]
    factors = difficulty_factors(items, answers_list)

    results = []
    for (attempt, user), answers in zip(rows, answers_list):
        end = attempt.finished_at or attempt.deadline_at
        minutes = max(0, int((end - attempt.started_at).total_seconds() // 60))
        results.append(
            Result(attempt, user, score_attempt(items, answers, factors, exam.mode), minutes)
        )

    results.sort(key=lambda r: (-r.score.value, r.minutes, r.user.full_name or ""))
    return items, results


def _loads(raw: str | None) -> dict:
    try:
        data = json.loads(raw or "{}")
        return data if isinstance(data, dict) else {}
    except ValueError:
        return {}


def attempt_answers(attempt: ExamAttempt) -> dict:
    return _loads(attempt.answers)


def hardest_units(items: list[Item], answers_list: list[dict], limit: int = 8):
    """Eng kam to'g'ri yechilgan birliklar: [(unit, ulush)]"""
    if not answers_list:
        return []

    by_pos = {it.pos: it for it in items}
    rated = []
    for unit in build_units(items):
        ok = sum(1 for a in answers_list if unit_correct(by_pos[unit.pos], unit, a))
        rated.append((unit, ok / len(answers_list)))

    rated.sort(key=lambda pair: (pair[1], pair[0].pos))
    return rated[:limit]


# ---------------------------------------------------------------
#  Imtihon yaratish
# ---------------------------------------------------------------
def _topic_filter(subject_id, topic_id: int | None = None):
    if isinstance(subject_id, (list, tuple, set)):  # bir nechta fan (fanlar2)
        return [Topic.subject_id.in_(list(subject_id))]
    cond = [Topic.subject_id == subject_id]
    if topic_id:
        cond.append(Topic.id == topic_id)
    return cond


async def bank_counts(session, subject_id: int, topic_id: int | None = None) -> tuple[int, int]:
    closed = await session.scalar(
        select(func.count(Question.id))
        .join(Topic, Topic.id == Question.topic_id)
        .where(*_topic_filter(subject_id, topic_id), *_closed_conditions())
    )
    opened = await session.scalar(
        select(func.count(OpenQuestion.id))
        .join(Topic, Topic.id == OpenQuestion.topic_id)
        .where(*_topic_filter(subject_id, topic_id))
    )
    return int(closed or 0), int(opened or 0)


def _closed_conditions():
    return [
        Question.is_active.is_(True),
        Question.correct_option.in_(["A", "B", "C", "D"]),
        Question.option_a.is_not(None),
        Question.option_b.is_not(None),
    ]


def _closed_payload(q: Question) -> dict | None:
    options = {
        "A": q.option_a, "B": q.option_b, "C": q.option_c, "D": q.option_d,
    }
    options = {k: v for k, v in options.items() if v and str(v).strip()}
    correct = (q.correct_option or "").upper()

    if correct not in options or len(options) < 2:
        return None

    if not (q.text or "").strip() and not q.image_path:
        return None

    return {
        "k": "c",
        "t": q.text or "",
        "o": options,
        "c": correct,
        "img": q.image_path,
        "e": q.explanation,
        "qid": q.id,
    }


async def pick_questions(
    session,
    subject_id: int,
    topic_id: int | None,
    closed_n: int,
    open_n: int,
) -> tuple[list[dict], int, int]:
    """Tasodifiy tanlaydi. Qaytaradi: (elementlar, yopiq soni, ochiq soni)."""
    payloads: list[dict] = []

    if closed_n > 0:
        ids = list(
            await session.scalars(
                select(Question.id)
                .join(Topic, Topic.id == Question.topic_id)
                .where(*_topic_filter(subject_id, topic_id), *_closed_conditions())
            )
        )
        random.shuffle(ids)
        # nosoz savollar (variantsiz/rasmsiz) chetga chiqarilishi mumkin,
        # shuning uchun zaxira bilan olamiz
        chosen = ids[: closed_n + 15]

        if chosen:
            rows = {
                q.id: q
                for q in await session.scalars(select(Question).where(Question.id.in_(chosen)))
            }
            for qid in chosen:
                payload = _closed_payload(rows[qid]) if qid in rows else None
                if payload:
                    payloads.append(payload)
                if len(payloads) >= closed_n:
                    break

    closed_count = len(payloads)

    if open_n > 0:
        ids = list(
            await session.scalars(
                select(OpenQuestion.id)
                .join(Topic, Topic.id == OpenQuestion.topic_id)
                .where(*_topic_filter(subject_id, topic_id))
            )
        )
        random.shuffle(ids)
        chosen = ids[:open_n]

        if chosen:
            rows = {
                q.id: q
                for q in await session.scalars(
                    select(OpenQuestion).where(OpenQuestion.id.in_(chosen))
                )
            }
            for oid in chosen:
                row = rows.get(oid)
                if row is None:
                    continue
                try:
                    parts = json.loads(row.parts)
                except ValueError:
                    continue
                _img = None
                if parts and isinstance(parts[0], dict):
                    _img = parts[0].pop("_img", None)
                payloads.append({"k": "o", "s": row.stem, "p": parts, "oid": row.id, "img": _img})

    open_count = len(payloads) - closed_count
    return payloads, closed_count, open_count


async def pick_questions_multi(session, subject_ids, closed_n: int, open_n: int):
    """Bir nechta fan: savollar fanlar orasida teng olinadi, yetmasa boshqa fanlardan to'ldiriladi."""
    ids = list(subject_ids)

    def quotas(total):
        base, extra = divmod(total, len(ids))
        return [base + (1 if i < extra else 0) for i in range(len(ids))]

    closed, opened = [], []
    seen_c, seen_o = set(), set()

    def add(target, seen, key, items):
        for item in items:
            marker = item.get(key)
            if marker in seen:
                continue
            seen.add(marker)
            target.append(item)

    for sid, c_n, o_n in zip(ids, quotas(closed_n), quotas(open_n)):
        payloads, _, _ = await pick_questions(session, sid, None, c_n, o_n)
        add(closed, seen_c, "qid", [p for p in payloads if p["k"] == "c"])
        add(opened, seen_o, "oid", [p for p in payloads if p["k"] == "o"])

    for sid in ids:
        need_c, need_o = closed_n - len(closed), open_n - len(opened)
        if need_c <= 0 and need_o <= 0:
            break
        payloads, _, _ = await pick_questions(
            session, sid, None, closed_n if need_c > 0 else 0, open_n if need_o > 0 else 0
        )
        add(closed, seen_c, "qid", [p for p in payloads if p["k"] == "c"])
        add(opened, seen_o, "oid", [p for p in payloads if p["k"] == "o"])

    closed, opened = closed[:closed_n], opened[:open_n]
    return closed + opened, len(closed), len(opened)


async def create_exam(
    session,
    *,
    title: str,
    subject_name: str,
    mode: str,
    created_by: int,
    group_name: str | None,
    duration_min: int,
    days_open: int,
    payloads: list[dict],
) -> Exam:
    exam = Exam(
        title=title[:200],
        subject_name=subject_name[:255],
        mode=mode,
        created_by=created_by,
        group_name=group_name,
        duration_min=duration_min,
        closes_at=datetime.utcnow() + timedelta(days=days_open),
        is_open=True,
        is_removed=False,
    )
    session.add(exam)
    await session.flush()

    for position, payload in enumerate(payloads, start=1):
        session.add(
            ExamItem(
                exam_id=exam.id,
                position=position,
                payload=json.dumps(payload, ensure_ascii=False),
            )
        )

    await session.commit()
    return exam


# ---------------------------------------------------------------
#  Holat: ochiq/yopiq, maqsadli o'quvchilar
# ---------------------------------------------------------------
def exam_is_open(exam: Exam, now: datetime | None = None) -> bool:
    now = now or datetime.utcnow()
    if exam.is_removed or not exam.is_open:
        return False
    return exam.closes_at is None or exam.closes_at > now


def norm_group(name: str | None) -> str:
    return " ".join((name or "").split()).casefold()


def exam_targets_user(exam: Exam, user: User) -> bool:
    if not exam.group_name:
        return True
    return norm_group(exam.group_name) == norm_group(user.group_name)


async def target_students(session, exam: Exam) -> list[User]:
    users = (
        await session.scalars(
            select(User).where(
                User.registration_status == "approved",
                User.is_blocked.is_(False),
            )
        )
    ).all()
    return [u for u in users if exam_targets_user(exam, u)]


# ---------------------------------------------------------------
#  Vaqt tugaganlarni yopish
# ---------------------------------------------------------------
def finish_attempt(attempt: ExamAttempt, now: datetime | None = None) -> None:
    now = now or datetime.utcnow()
    attempt.status = "finished"
    attempt.finished_at = min(now, attempt.deadline_at)


async def finish_expired(session, bot=None) -> int:
    now = datetime.utcnow()
    rows = (
        await session.execute(
            select(ExamAttempt, User, Exam)
            .join(User, User.id == ExamAttempt.user_id)
            .join(Exam, Exam.id == ExamAttempt.exam_id)
            .where(ExamAttempt.status == "active", ExamAttempt.deadline_at <= now)
            .limit(200)
        )
    ).all()

    for attempt, _user, _exam in rows:
        finish_attempt(attempt, now)

    if rows:
        await session.commit()

    if bot is not None:
        from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

        for attempt, user, exam in rows:
            try:
                await _tg_retry(lambda: bot.send_message(
                    user.telegram_id,
                    "⏰ <b>Imtihon vaqti tugadi</b>\n\n"
                    f"«{_esc(exam.title)}» imtihoningiz avtomatik yakunlandi.",
                    reply_markup=InlineKeyboardMarkup(
                        inline_keyboard=[[
                            InlineKeyboardButton(
                                text="📊 Natijani ko'rish",
                                callback_data=f"exm:r:{attempt.id}",
                            )
                        ]]
                    ),
                ), tries=3)
            except Exception:
                pass
            await asyncio.sleep(0.05)

    return len(rows)


def _esc(value) -> str:
    import html

    return html.escape(str(value or ""))


async def expiry_loop(bot, interval: int = 30) -> None:
    from ..db import SessionLocal

    log.info("Imtihon vaqtini kuzatish sikli ishga tushdi.")

    while True:
        try:
            async with SessionLocal() as session:
                done = await finish_expired(session, bot)
            if done:
                log.info("Vaqti tugagan imtihonlar yopildi: %s", done)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Imtihon kuzatuvida xato")

        await asyncio.sleep(interval)

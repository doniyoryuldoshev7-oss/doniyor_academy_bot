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
import copy
import itertools
import json
import logging
import random
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import func, select

from ..models import (
    AnswerLog,
    Exam,
    ExamAttempt,
    ExamItem,
    OpenQuestion,
    Question,
    TestAttempt,
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
# 55 talik fayl: 36–55 — 20 ta bitta javobli ochiq savol (testdan aylantirilgan)
MILLIY_OPEN_SINGLE = 20
OPEN_SINGLE_PROMPT = "Javobingizni yozing"


def milliy_open_ok(opened) -> bool:
    """Milliy sertifikat ochiq qismi: 10 ta (a/b qismli) yoki 20 ta bitta javobli savol."""
    opened = list(opened or [])
    if len(opened) == MILLIY_OPEN:
        return True
    return len(opened) == MILLIY_OPEN_SINGLE and all(len(x.get("p") or []) == 1 for x in opened)


def open_range_text(n_open: int) -> str:
    return f"{MILLIY_CLOSED + 1}–{MILLIY_CLOSED + (n_open or MILLIY_OPEN)}"
MAX_OPEN_PARTS = 4
MAX_ANSWER_LEN = 200
# Vaqt tugashidan oldin yuborilgan, lekin tarmoq sababli kech yetib kelgan
# javoblar uchun qisqa imtiyoz (soniya). Imtihon baribir o'z vaqtida yakunlanadi.
SAVE_GRACE_SEC = 10
MAX_EXTRA_ACCEPT = 30

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
    return is_match(answers.get(unit.key), accepted_variants(part))


def accepted_variants(part: dict) -> list[str]:
    """Asl to'g'ri javoblar + ustoz shu imtihon uchun keyin qabul qilganlari."""
    return list(part.get("a") or []) + list(part.get("ax") or [])


def can_save_late(attempt, now: datetime | None = None) -> bool:
    """Faol urinish uchun javobni saqlash mumkinmi (vaqt + qisqa imtiyoz ichida)."""
    now = now or datetime.utcnow()
    return attempt.status == "active" and now < attempt.deadline_at + timedelta(seconds=SAVE_GRACE_SEC)


def open_answer_groups(items: list[Item], answers_list: list[dict]) -> list[dict]:
    """Yozma qismlar bo'yicha avtomatik qabul qilinmagan (lekin yozilgan) javoblar.

    Bir xil yozilgan javoblar bitta guruhga yig'iladi (katta-kichik harf,
    bo'shliq, apostrof farqi hisobga olinmaydi), shunda ustoz har bir yozilishni
    bir marta ko'rib chiqadi. Qaytaradi: qismlar ro'yxati.
    """
    out = []
    for it in items:
        if it.kind != "o":
            continue
        for i, part in enumerate(it.data.get("p", [])):
            key = f"{it.pos}.{i}"
            groups: dict[str, dict] = {}
            ok_n = 0
            for answers in answers_list:
                typed = (answers.get(key) or "").strip()
                if not typed:
                    continue
                if answers.get("!" + key) or is_match(typed, accepted_variants(part)):
                    ok_n += 1
                    continue
                g = groups.setdefault(compact(typed), {"text": typed, "count": 0})
                g["count"] += 1
            out.append({
                "key": key, "pos": it.pos, "part": i, "q": part.get("q", ""),
                "a": list(part.get("a") or []), "ax": list(part.get("ax") or []),
                "ok": ok_n,
                "wrong": sorted(groups.values(), key=lambda g: (-g["count"], g["text"].casefold())),
            })
    return out


def toggle_extra_variant(payload: dict, part_index: int, text: str, accept: bool) -> tuple[bool, str]:
    """Imtihon elementining (surat) qismiga qo'shimcha to'g'ri javob qo'shadi yoki olib tashlaydi.

    Faqat shu imtihonga ta'sir qiladi, savollar bazasi o'zgarmaydi.
    Qaytaradi: (o'zgardimi, izoh).
    """
    parts = payload.get("p") or []
    if payload.get("k") != "o" or not (0 <= part_index < len(parts)):
        return False, "Qism topilmadi."
    text = " ".join((text or "").split())[:MAX_ANSWER_LEN]
    if not compact(text):
        return False, "Javob bo'sh."
    part = parts[part_index]
    extra = list(part.get("ax") or [])
    same = [x for x in extra if compact(x) == compact(text)]
    if accept:
        if is_match(text, accepted_variants(part)):
            return False, "Bu javob allaqachon qabul qilingan."
        if len(extra) >= MAX_EXTRA_ACCEPT:
            return False, f"Bitta qismga {MAX_EXTRA_ACCEPT} tadan ortiq qo'shimcha javob qo'shib bo'lmaydi."
        extra.append(text)
        note = "Javob qabul qilindi. Natijalar qayta hisoblandi."
    else:
        if not same:
            return False, "Bu javob qo'shimcha qabul qilinganlar orasida yo'q."
        extra = [x for x in extra if compact(x) != compact(text)]
        note = "Qabul bekor qilindi. Natijalar qayta hisoblandi."
    if extra:
        part["ax"] = extra
    else:
        part.pop("ax", None)
    return True, note


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
#  O'quvchi ko'radigan tartib (aralashtirish)
# ---------------------------------------------------------------
# Javoblar va ball har doim imtihonning ASL tartibida (ustoz ko'radigan
# raqamlar va harflar) saqlanadi. O'quvchiga esa "ko'rinish" beriladi:
# savollar (yopiqlar o'z ichida, ochiqlar o'z ichida) va A–D variantlar
# har urinish uchun o'z tartibida. Tartib urinish raqamidan hosil
# qilinadi, shuning uchun bot va veb-ilovada har doim bir xil chiqadi.
LETTERS = "ABCD"
_SELF_REF = re.compile(
    r"\b[A-D]\b|\b(?:barcha|hamma|hammasi|yuqoridagi|ikkalasi|hech biri|hech qaysi)",
    re.IGNORECASE,
)


def _can_shuffle_options(data: dict) -> bool:
    """Rasmli savol (variantlar rasmda bo'lishi mumkin) va «barchasi to'g'ri», «A va B»
    kabi bir-biriga bog'liq variantlar aralashtirilmaydi."""
    if data.get("img"):
        return False
    return not any(_SELF_REF.search(str(v or "")) for v in (data.get("o") or {}).values())


def attempt_view(items: list[Item], exam, attempt_id: int) -> list[Item]:
    """O'quvchiga ko'rsatiladigan elementlar: pos = ko'rinishdagi raqam (1, 2, ...).
    data["_cp"] — asl raqam, data["_om"] — {ko'rinish harfi: asl harf}."""
    mix = bool(getattr(exam, "shuffle", False))
    order = list(items)
    rng = None
    if mix:
        rng = random.Random(f"exm:{getattr(exam, 'id', 0)}:{attempt_id}")
        closed = [it for it in items if it.kind == "c"]
        opened = [it for it in items if it.kind != "c"]
        rng.shuffle(closed)
        rng.shuffle(opened)
        order = closed + opened
    view = []
    for number, it in enumerate(order, start=1):
        data = copy.deepcopy(it.data)
        data["_cp"] = it.pos
        if it.kind == "c" and rng is not None:
            canon = list((it.data.get("o") or {}).keys())
            perm = canon[:]
            rng.shuffle(perm)       # tartib barqaror bo'lishi uchun har doim chaqiriladi
            if _can_shuffle_options(it.data) and len(perm) <= len(LETTERS) and perm != canon:
                shown = list(LETTERS[: len(perm)])
                data["o"] = {d: it.data["o"][c] for d, c in zip(shown, perm)}
                data["_om"] = dict(zip(shown, perm))
                back = {c: d for d, c in data["_om"].items()}
                data["c"] = back.get(it.data.get("c"), it.data.get("c"))
        view.append(Item(pos=number, kind=it.kind, data=data))
    return view


def _view_maps(view: list[Item]):
    to_view = {it.data.get("_cp", it.pos): it for it in view}
    return to_view, {it.pos: it for it in view}


def answers_to_view(answers: dict, view: list[Item]) -> dict:
    """Asl tartibdagi javoblar -> o'quvchi ko'rinishi (raqam va harf)."""
    to_view, _ = _view_maps(view)
    out = {}
    for key, value in (answers or {}).items():
        bang = key.startswith("!")
        core = key[1:] if bang else key
        head, dot, tail = core.partition(".")
        if not head.isdigit() or int(head) not in to_view:
            continue
        it = to_view[int(head)]
        if it.kind == "c" and not dot and not bang and it.data.get("_om"):
            back = {c: d for d, c in it.data["_om"].items()}
            value = back.get(value, value)
        out[("!" if bang else "") + str(it.pos) + (dot + tail if dot else "")] = value
    return out


def answers_from_view(view_answers: dict, view: list[Item], original: dict | None = None) -> dict:
    """O'quvchi ko'rinishidagi javoblar -> asl tartib. original dagi xizmat kalitlari ("_...") saqlanadi."""
    _, by_view = _view_maps(view)
    out = {k: v for k, v in (original or {}).items() if k.startswith("_")}
    for key, value in (view_answers or {}).items():
        bang = key.startswith("!")
        core = key[1:] if bang else key
        head, dot, tail = core.partition(".")
        if not head.isdigit() or int(head) not in by_view:
            continue
        it = by_view[int(head)]
        if it.kind == "c" and not dot and not bang and it.data.get("_om"):
            value = it.data["_om"].get(value, value)
        out[("!" if bang else "") + str(it.data.get("_cp", it.pos)) + (dot + tail if dot else "")] = value
    return out


def view_key_to_canon(key: str, value: str, view: list[Item]):
    """Bitta javob kaliti/qiymatini asl tartibga o'giradi. Topilmasa (None, None)."""
    _, by_view = _view_maps(view)
    head, dot, tail = key.partition(".")
    if not head.isdigit() or int(head) not in by_view:
        return None, None
    it = by_view[int(head)]
    if it.kind == "c" and not dot and value and it.data.get("_om"):
        if value not in it.data["_om"]:
            return None, None
        value = it.data["_om"][value]
    return str(it.data.get("_cp", it.pos)) + (dot + tail if dot else ""), value


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
        **_exp_src(q.explanation),
        "qid": q.id,
    }


def _exp_src(text) -> dict:
    """Bankdagi izoh («izoh\n📚 Manba: …») → imtihon elementi uchun {"e", "m"}."""
    from .savol_import import split_source

    e, m = split_source(text)
    return {"e": e, "m": m}


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
                _img = _e = _m = None
                if parts and isinstance(parts[0], dict):
                    _img = parts[0].pop("_img", None)
                    _e = parts[0].pop("_e", None)       # izoh va manba (yuklashda «Izoh:»/«Manba:»)
                    _m = parts[0].pop("_m", None)
                payloads.append({"k": "o", "s": row.stem, "p": parts, "oid": row.id, "img": _img, "e": _e, "m": _m})

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
    shuffle: bool = True,
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
        shuffle=bool(shuffle),
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
            .where(
                ExamAttempt.status == "active",
                ExamAttempt.deadline_at <= now - timedelta(seconds=SAVE_GRACE_SEC),
            )
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


# ---------------------------------------------------------------
#  Yopilgan imtihon natijalarini umumiy test tarixiga yozish
# ---------------------------------------------------------------
# Imtihon yopilgach (tahlil ochilgach) har bir topshirgan o'quvchi uchun
# guruh testi kabi bitta test yozuvi (source="exam") va har bir yopiq
# savol bo'yicha javob yozuvi yaratiladi. Shunda xatolar «Mening
# xatolarim», Smart Takrorlash, statistika va Bilimlar xaritasida ko'rinadi.
# Yozma (ochiq) savollar savollar bazasida test savoli emas, ular kirmaydi.
# Imtihon ochiq turganda yozilmaydi: aks holda to'g'ri javoblar boshqa
# o'quvchilarga oshkor bo'lishi mumkin edi.
SYNC_KEY = "_s"
SYNC_BATCH = 50
SMART_FRESH_DAYS = 14
UNKNOWN_OPTION = "?"


def closed_log_rows(items: list[Item], answers: dict, existing_qids: set) -> list[tuple]:
    """[(question_id, tanlangan harf, to'g'rimi)] — faqat bazada mavjud yopiq savollar.
    Javob berilmagan savol xato hisoblanadi ("?")."""
    rows = []
    seen = set()
    for it in items:
        if it.kind != "c":
            continue
        qid = it.data.get("qid")
        if not qid or qid not in existing_qids or qid in seen:
            continue
        seen.add(qid)
        mine = (answers.get(str(it.pos)) or "").strip().upper()[:1]
        ok = bool(mine) and mine == (it.data.get("c") or "")
        rows.append((qid, mine or UNKNOWN_OPTION, ok))
    return rows


async def _sync_one(session, attempt, items, answers, register, now) -> int:
    """Bitta urinishni test tarixiga yozadi. Qaytaradi: TestAttempt id (yozadigan narsa bo'lmasa 0)."""
    qids = {it.data.get("qid") for it in items if it.kind == "c" and it.data.get("qid")}
    existing = set()
    if qids:
        existing = set(await session.scalars(select(Question.id).where(Question.id.in_(list(qids)))))
    logs = closed_log_rows(items, answers, existing)
    marker = 0
    if logs:
        topic_id = await session.scalar(
            select(Question.topic_id).where(Question.id == logs[0][0])
        )
        total = len(logs)
        correct = sum(1 for _q, _s, ok in logs if ok)
        row = TestAttempt(
            user_id=attempt.user_id,
            topic_id=topic_id,
            total=total,
            correct=correct,
            score=correct,
            created_at=attempt.finished_at or attempt.deadline_at,
        )
        row.source = "exam"
        session.add(row)
        await session.flush()
        for qid, selected, ok in logs:
            session.add(AnswerLog(attempt_id=row.id, question_id=qid, selected_option=selected, is_correct=ok))
        user = await session.get(User, attempt.user_id)
        if user is not None:
            user.total_tests = (user.total_tests or 0) + 1
            user.total_questions = (user.total_questions or 0) + total
            user.correct_answers = (user.correct_answers or 0) + correct
            user.total_score = (user.total_score or 0) + correct
        fresh = (now - (attempt.finished_at or now)) <= timedelta(days=SMART_FRESH_DAYS)
        if register is not None and fresh:
            for qid, _sel, ok in logs:
                if ok:
                    continue
                try:
                    async with session.begin_nested():
                        await register(session, user_id=attempt.user_id, question_id=qid)
                except Exception:
                    log.exception("Imtihon: Smart Takrorlashga qo'shilmadi (#%s)", qid)
        marker = row.id
    return marker


async def sync_closed_exams(session, now: datetime | None = None) -> int:
    """Yopilgan imtihonlarning hali yozilmagan urinishlarini test tarixiga yozadi. Qaytaradi: nechta."""
    import os

    now = now or datetime.utcnow()
    rows = (
        await session.execute(
            select(ExamAttempt, Exam)
            .join(Exam, Exam.id == ExamAttempt.exam_id)
            .where(
                ExamAttempt.status == "finished",
                Exam.is_removed.is_(False),
                (Exam.is_open.is_(False)) | (Exam.closes_at <= now),
                ExamAttempt.answers.not_like('%"' + SYNC_KEY + '":%'),
            )
            .order_by(ExamAttempt.id)
            .limit(SYNC_BATCH)
        )
    ).all()
    if not rows:
        return 0

    smart_on = os.getenv("SMART_REVIEW_ENABLED", "0") == "1"
    register = None
    if smart_on:
        from .smart_review_service import register_wrong_answer as register

    cache: dict[int, list[Item]] = {}
    done = 0
    for attempt, exam in rows:
        if exam_is_open(exam, now):
            continue
        items = cache.get(exam.id)
        if items is None:
            items = cache[exam.id] = await load_items(session, exam.id)
        answers = attempt_answers(attempt)
        try:
            # har bir urinish alohida "saqlash nuqtasi"da: bittasidagi xato qolganlarini to'xtatmaydi
            async with session.begin_nested():
                marker = await _sync_one(session, attempt, items, answers, register, now)
        except Exception:
            log.exception("Imtihon: urinish #%s test tarixiga yozilmadi", attempt.id)
            marker = -1                     # qayta-qayta urinmaslik uchun belgilanadi
        answers[SYNC_KEY] = marker
        attempt.answers = json.dumps(answers, ensure_ascii=False)
        done += 1
    await session.commit()
    return done


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
            try:
                async with SessionLocal() as session:
                    synced = await sync_closed_exams(session)
                if synced:
                    log.info("Imtihon natijalari test tarixiga yozildi: %s", synced)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Imtihon natijalarini test tarixiga yozishda xato")
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Imtihon kuzatuvida xato")

        await asyncio.sleep(interval)

"""Darsliklar katalogi: sinflar bo'yicha fanlar va mavzular.

Joylashuvi: app/services/darsliklar.py, ma'lumot: app/assets/darsliklar.json

1) Bot ishga tushganda katalogdagi fanlar va mavzular bazaga qo'shiladi
   (yo'qlari qo'shiladi; mavjudlari o'zgartirilmaydi va o'chirilmaydi).
2) Savol yuklashda fayl qaysi darslikka tegishli ekani avtomatik aniqlanadi:
   «1-mavzu. …», «19–20-§. …», «3-dars. …» kabi sarlavhalar katalog bilan
   solishtiriladi va har bir savol o'z mavzusiga joylanadi.
"""
from __future__ import annotations

import json
import logging
import re
from functools import lru_cache
from pathlib import Path

log = logging.getLogger(__name__)

CATALOG = Path(__file__).resolve().parent.parent / "assets" / "darsliklar.json"

_APOS = str.maketrans({c: "'" for c in "‘’ʻʼ`´′"})
_DASH = str.maketrans({c: "-" for c in "–—−‑"})

# «12-mavzu.», «41–42-mavzular:», «19–20-§.», «§ 12.», «3-dars», «12 - MAVZU»
HEAD_RE = re.compile(
    r"^\s*(?:(\d{1,2})(?:\s*-+\s*(\d{1,2}))?\s*-*\s*(mavzu(?:lar)?\b|§|&|dars\b)|[§&]\s*(\d{1,2})(?:\s*-+\s*(\d{1,2}))?)"
    r"[\s\.:,;•·|)\-]*(.*)$",
    re.I,
)
# «22–23. Qarluqlar…» (ikki raqam — savol raqami bo'la olmaydi)
RANGE_RE = re.compile(r"^\s*(\d{1,2})\s*-\s*(\d{1,2})\s*[\.\)]\s+(\S.{2,200})$")
# «38. Etnik jarayonlar…» — savolga o'xshaydi, shuning uchun faqat katalogdagi nomga juda mos bo'lsa sarlavha
WEAK_RE = re.compile(r"^\s*(\d{1,2})\s*\.\s+([^?]{4,160})$")
BOB_RE = re.compile(r"^\s*(?:[IVXL]+|\d{1,2})\s*-?\s*(?:bob|bo'lim)\b", re.I)
QSTART_RE = re.compile(r"^\s*\d{1,4}\s*[\.\)]\s*\S")
GRADE_RE = re.compile(r"(\d{1,2})\s*-?\s*sinf", re.I)


def norm(text: str | None) -> str:
    t = (text or "").translate(_APOS).translate(_DASH).casefold()
    return " ".join(t.split())


def _tokens(text: str) -> set[str]:
    return {w[:5] for w in re.findall(r"[a-zа-яёўқғҳ0-9']+", norm(text).replace("'", "")) if len(w) >= 3}


def _sim(a: str, b: str) -> float:
    x, y = _tokens(a), _tokens(b)
    if not x or not y:
        return 0.0
    return len(x & y) / len(x | y)


@lru_cache(maxsize=1)
def books() -> list[dict]:
    try:
        data = json.loads(CATALOG.read_text(encoding="utf-8"))
        return list(data.get("books") or [])
    except Exception:  # noqa: BLE001
        log.exception("Darsliklar katalogi o'qilmadi")
        return []


def topic_title(name: str) -> str:
    """«12-mavzu. Salib yurishlari» -> «Salib yurishlari»."""
    name = name or ""
    m = HEAD_RE.match(name.translate(_DASH))     # tire almashtirish 1:1, joylashuv o'zgarmaydi
    return (name[m.start(6):] if m else name).strip()


def topic_range(name: str):
    """Nom boshidagi raqam(lar): (from, to) yoki None."""
    m = HEAD_RE.match((name or "").translate(_DASH))
    if not m:
        return None
    a = m.group(1) or m.group(4)
    b = m.group(2) or m.group(5) or a
    return int(a), int(b)


# ---------------------------------------------------------------
#  1) Katalogni bazaga yozish
# ---------------------------------------------------------------
async def sync_catalog(session) -> dict:
    """Yo'q fan va mavzularni qo'shadi. Qaytaradi: {"subjects": n, "topics": n}.
    Mavjud fan/mavzu (nomi bir xil yoki raqami bir xil) qayta qo'shilmaydi, hech narsa o'chirilmaydi."""
    from sqlalchemy import select

    from ..models import Subject, Topic

    added_s = added_t = 0
    subjects = {norm(s.name): s for s in (await session.scalars(select(Subject))).all()}
    for book in books():
        subj = subjects.get(norm(book["subject"]))
        if subj is None:
            subj = Subject(name=book["subject"][:255], description=f"{book.get('book', '')} ({book.get('year', '')})"[:1000], is_active=True)
            session.add(subj)
            await session.flush()
            subjects[norm(subj.name)] = subj
            added_s += 1
        have = list(await session.scalars(select(Topic).where(Topic.subject_id == subj.id)))
        names = {norm(t.name) for t in have}
        ranges = {topic_range(t.name) for t in have}
        for t in book["topics"]:
            if norm(t["name"]) in names or (t["from"], t["to"]) in ranges:
                continue
            session.add(Topic(subject_id=subj.id, name=t["name"][:255]))
            added_t += 1
    await session.commit()
    return {"subjects": added_s, "topics": added_t}


async def sync_on_start() -> None:
    from ..db import SessionLocal

    try:
        async with SessionLocal() as session:
            res = await sync_catalog(session)
        print(f"DARSLIKLAR: yangi fan={res['subjects']}, yangi mavzu={res['topics']}", flush=True)
    except Exception:  # noqa: BLE001
        log.exception("Darsliklar katalogini bazaga yozishda xato")


# ---------------------------------------------------------------
#  2) Yuklangan fayl qaysi darslikka tegishli
# ---------------------------------------------------------------
def _headings(lines: list[str]):
    """[(qator, from, to, nom, kuchsizmi)]"""
    out = []
    for i, raw in enumerate(lines):
        line = raw.translate(_DASH)
        m = HEAD_RE.match(line)
        if m:
            a = m.group(1) or m.group(4)
            b = m.group(2) or m.group(5) or a
            title = (m.group(6) or "").strip()
            if len(title) <= 220:
                out.append((i, int(a), int(b), title, False))
            continue
        m = RANGE_RE.match(line)
        if m and int(m.group(1)) < int(m.group(2)):
            out.append((i, int(m.group(1)), int(m.group(2)), m.group(3).strip(), False))
            continue
        m = WEAK_RE.match(line)
        if m:
            out.append((i, int(m.group(1)), int(m.group(1)), m.group(2).strip(), True))
    return out


def _pick(book: dict, a: int, b: int, title: str):
    """Kitob ichida sarlavhaga mos mavzu: (mavzu | None, nom o'xshashligi 0..1).
    Raqam aniq mos kelsa — shu mavzu (muallif nomni qisqa yozgan bo'lishi mumkin);
    raqam oralig'iga tushsa — shu; raqam mos kelmasa — nomi eng o'xshashi."""
    exact = [t for t in book["topics"] if t["from"] == a and t["to"] == b]
    inside = [t for t in book["topics"] if t["from"] <= a <= t["to"]]
    sims = [(_sim(title, topic_title(t["name"])) if title else 0.0, t) for t in book["topics"]]
    best_sim, best_t = max(sims, key=lambda x: x[0]) if sims else (0.0, None)
    for group in (exact, inside):
        if group:
            t = group[0]
            return t, (_sim(title, topic_title(t["name"])) if title else 0.0)
    if best_t is not None and best_sim >= 0.35:
        return best_t, best_sim
    return None, 0.0


def _book_score(book: dict, heads) -> float:
    """Kitob mosligi: sarlavha nomlarining shu kitob mavzulariga o'xshashligi (+ raqam mosligi)."""
    total = 0.0
    for _i, a, b, title, weak in heads:
        if weak:
            continue
        best = 0.0
        for t in book["topics"]:
            sc = (_sim(title, topic_title(t["name"])) if title else 0.0)
            if t["from"] <= a <= t["to"]:
                sc += 0.3
            best = max(best, sc)
        total += best
    n = sum(1 for h in heads if not h[4])
    return total / n if n else 0.0


def _hints(text: str):
    head = norm(text[:1500])
    grades = {int(g) for g in GRADE_RE.findall(head)}
    fans = set()
    if "jahon" in head:
        fans.add("jahon")
    if "o'zbekiston" in head or "ozbekiston" in head:
        fans.add("ozb")
    if "qadimgi dunyo" in head:
        fans.add("jahon")
    if "hikoya" in head:
        fans.add("tarix")
    return grades, fans


def detect(text: str):
    """Qaytaradi: (darslik, [(qator raqami, mavzu nomi | None, sarlavha)]) yoki (None, sabab)."""
    lines = text.split("\n")
    heads = _headings(lines)
    if not any(not h[4] for h in heads):
        return None, None
    grades, fans = _hints(text)
    scored = []
    for book in books():
        sc = _book_score(book, heads)
        if grades and book["grade"] in grades:
            sc += 0.5
        if fans and book["fan"] in fans:
            sc += 0.3
        scored.append((sc, book))
    scored.sort(key=lambda x: -x[0])
    best_sc, best = scored[0]
    second = scored[1][0] if len(scored) > 1 else 0.0
    if best_sc < 0.6 or best_sc - second < 0.2:
        return None, ("Fayl qaysi darslikka tegishli ekani aniq bo'lmadi. Faylning boshiga "
                      "«Fan: 7-sinf Jahon tarixi» kabi qator yozing.")
    mapped = []
    for i, a, b, title, weak in heads:
        t, sim = _pick(best, a, b, title)
        if weak:
            # «38. Etnik jarayonlar…» — faqat raqami va nomi katalogdagiga mos bo'lsa sarlavha
            if t is not None and t["from"] == a and sim >= 0.5:
                mapped.append((i, t["name"], lines[i].strip()))
            continue
        mapped.append((i, t["name"] if t is not None else None, lines[i].strip()))
    return best, mapped


def annotate(text: str):
    """Sarlavhalarni «Fan:» / «Mavzu:» qatorlariga aylantiradi.
    Qaytaradi: (matn, izohlar, xatolar). Fan/Mavzu allaqachon yozilgan bo'lsa yoki sarlavha yo'q bo'lsa — o'zgarmaydi."""
    if re.search(r"^\s*(fan|mavzu)\s*[:=]", text or "", re.I | re.M):
        return text, [], []
    book, mapped = detect(text or "")
    if book is None:
        return text, [], ([mapped] if mapped else [])
    lines = text.split("\n")
    # Ostida birorta savol bo'lmagan sarlavha (masalan, «41–48-MAVZULAR • TEST» kabi umumiy sarlavha) tashlanadi
    starts = sorted(i for i, _n, _r in mapped)
    keep = set()
    for k, i in enumerate(starts):
        end = starts[k + 1] if k + 1 < len(starts) else len(lines)
        if any(QSTART_RE.match(lines[j]) for j in range(i + 1, end)):
            keep.add(i)
    mapped = [m for m in mapped if m[0] in keep]
    drop = set(starts) - keep
    by_line = {i: (name, raw) for i, name, raw in mapped}
    first = min(by_line) if by_line else 0
    out = [f"Fan: {book['subject']}"]
    unknown, used = [], set()
    for i, raw in enumerate(lines):
        if i < first:
            # sarlavhadan oldingi kirish qatorlari (fayl nomi, manba, izoh) — savol bo'lmasa tashlanadi
            if QSTART_RE.match(raw) or re.match(r"^\s*[A-Da-d]\s*[\)\.]", raw):
                out.append(raw)
            continue
        if i in drop:
            continue
        if i in by_line:
            name, rawh = by_line[i]
            if name is None:
                unknown.append(rawh[:80])
                out.append(f"Mavzu: {rawh}")
            else:
                used.add(name)
                out.append(f"Mavzu: {name}")
            continue
        if BOB_RE.match(raw.translate(_APOS)):
            continue
        out.append(raw)
    notes = [f"📚 Darslik aniqlandi: {book['subject']} — {len(used)} ta mavzu ({book.get('book', '')}, {book.get('year', '')})."]
    errors = [f"«{u}» sarlavhasi {book['subject']} mavzulariga mos kelmadi" for u in unknown[:5]]
    return "\n".join(out), notes, errors

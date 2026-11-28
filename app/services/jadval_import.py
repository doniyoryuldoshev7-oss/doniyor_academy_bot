"""Excel/CSV jadval importi (ustunli format) — bot («📥 Excel/CSV import») va veb-ilova uchun BITTA servis.

Ustunlar (sarlavha qatori bo'yicha, tartib ixtiyoriy):
    Fan | Mavzu | Savol | Savol rasmi | A | B | C | D | Savol turi | To'g'ri javob | To'g'ri javob matni | Izoh

• Savol turi: closed (yopiq, A–D) yoki open (ochiq: «To'g'ri javob matni» majburiy).
• Savol rasmi: serverdagi app/question_images ichidagi fayl nomi.
• Fan va mavzu bo'lmasa — yaratiladi.
"""
from __future__ import annotations

import csv
import io
from pathlib import Path

MAX_ROWS = 5000


def _norm_head(value) -> str:
    if value is None:
        return ""
    value = str(value).strip().lower()
    for code in (0x2018, 0x2019, 0x02BB, 0x02BC, 0x0060):
        value = value.replace(chr(code), "'")
    return " ".join(value.replace(chr(0xFEFF), "").split())


ALIASES = {
    "fan": {"fan", "subject"},
    "mavzu": {"mavzu", "topic"},
    "savol": {"savol", "question", "text"},
    "image": {"savol rasmi", "savol rasmi yo'li", "savol rasmi yoli", "image", "image path", "image_path",
              "rasm", "rasm yo'li", "rasm yoli"},
    "a": {"a", "variant a"},
    "b": {"b", "variant b"},
    "c": {"c", "variant c"},
    "d": {"d", "variant d"},
    "mode": {"savol turi", "question type", "question_type", "type", "mode"},
    "correct": {"to'g'ri javob", "tog'ri javob", "togri javob", "correct", "correct option", "correct answer",
                "javob", "to'g'ri", "togri"},
    "correct_answer": {"to'g'ri javob matni", "tog'ri javob matni", "togri javob matni", "correct answer text",
                       "correct_answer", "answer text", "javob matni"},
    "izoh": {"izoh", "explanation", "comment"},
}


def read_rows(filename: str, data: bytes) -> list:
    name = (filename or "").lower()
    if name.endswith(".xlsx"):
        from openpyxl import load_workbook

        try:
            wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
            rows = list(wb.active.iter_rows(values_only=True))
            wb.close()
        except Exception:
            raise ValueError("Excel faylni o'qib bo'lmadi (.xlsx bo'lishi kerak).")
        return rows
    if name.endswith(".csv"):
        for enc in ("utf-8-sig", "utf-8", "cp1251"):
            try:
                text = data.decode(enc)
                break
            except UnicodeDecodeError:
                text = None
        if text is None:
            raise ValueError("CSV kodlashini o'qib bo'lmadi.")
        return list(csv.reader(io.StringIO(text)))
    raise ValueError("Faqat .xlsx yoki .csv fayl yuboring.")


def is_table_format(rows) -> bool:
    """Ustunli jadval (Fan va Mavzu ustunlari bor) ekanini aniqlaydi."""
    if not rows:
        return False
    heads = {_norm_head(x) for x in rows[0]}
    return bool(heads & ALIASES["fan"]) and bool(heads & ALIASES["mavzu"])


def parse_rows(rows) -> tuple[list[dict], list[str]]:
    """Qaytaradi: (yaroqli qatorlar, xatolar). Xato bo'lsa — hech narsa saqlanmasligi kerak."""
    if len(rows) < 2:
        raise ValueError("Faylda savollar mavjud emas.")
    if len(rows) > MAX_ROWS + 1:
        raise ValueError(f"Bitta faylda {MAX_ROWS} tadan ortiq qator bo'lmasin.")
    headers = [_norm_head(x) for x in rows[0]]
    idx = {}
    for key, names in ALIASES.items():
        names = {_norm_head(x) for x in names}
        for i, h in enumerate(headers):
            if h in names:
                idx[key] = i
                break
    missing = [x for x in ("fan", "mavzu") if x not in idx]
    if missing:
        raise ValueError("Majburiy ustunlar topilmadi: " + ", ".join(missing))

    def cell(row, key):
        i = idx.get(key)
        if i is None or i >= len(row) or row[i] is None:
            return ""
        return str(row[i]).strip()

    valid, errors = [], []
    for row_no, row in enumerate(rows[1:], start=2):
        if not any(x is not None and str(x).strip() for x in row):
            continue
        subject_name, topic_name = cell(row, "fan"), cell(row, "mavzu")
        question, image_value = cell(row, "savol"), cell(row, "image")
        a, b, c, d = (cell(row, k) for k in "abcd")
        mode = cell(row, "mode").lower() or "closed"
        correct = cell(row, "correct").upper()
        correct_answer = cell(row, "correct_answer")
        explanation = cell(row, "izoh") or None
        if mode in {"yopiq", "variantli", "test"}:
            mode = "closed"
        if mode in {"ochiq", "free", "free text"}:
            mode = "open"
        if mode not in {"closed", "open"}:
            errors.append(f"{row_no}-qator: Savol turi closed yoki open bo'lishi kerak.")
            continue
        if not subject_name:
            errors.append(f"{row_no}-qator: Fan bo'sh.")
            continue
        if not topic_name:
            errors.append(f"{row_no}-qator: Mavzu bo'sh.")
            continue
        if not question and not image_value:
            errors.append(f"{row_no}-qator: Savol yoki Savol rasmi bo'lishi kerak.")
            continue
        if mode == "closed":
            if not all([a, b, c, d]):
                errors.append(f"{row_no}-qator: closed savolda A/B/C/D to'liq bo'lishi kerak.")
                continue
            if correct not in {"A", "B", "C", "D"}:
                errors.append(f"{row_no}-qator: To'g'ri javob A/B/C/D bo'lishi kerak.")
                continue
            correct_answer = None
        else:
            if not correct_answer:
                errors.append(f"{row_no}-qator: open savolda To'g'ri javob matni bo'lishi kerak.")
                continue
            correct = None
        valid.append({
            "subject": subject_name, "topic": topic_name, "question": question or None, "image": image_value or None,
            "mode": mode, "a": a or None, "b": b or None, "c": c or None, "d": d or None,
            "correct": correct, "correct_answer": correct_answer, "explanation": explanation,
        })
    return valid, errors


async def save_rows(valid: list[dict]) -> int:
    """Qatorlarni bazaga yozadi (fan/mavzu bo'lmasa yaratiladi). Qaytaradi: qo'shilgan savollar soni."""
    from sqlalchemy import func, select

    from ..db import SessionLocal
    from ..models import Question, Subject, Topic

    async with SessionLocal() as s:
        subject_cache, topic_cache = {}, {}
        for item in valid:
            sname, tname = item["subject"], item["topic"]
            subject = subject_cache.get(sname.casefold())
            if not subject:
                subject = await s.scalar(select(Subject).where(func.lower(Subject.name) == sname.lower()))
                if not subject:
                    subject = Subject(name=sname)
                    s.add(subject)
                    await s.flush()
                subject_cache[sname.casefold()] = subject
            tkey = (subject.id, tname.casefold())
            topic = topic_cache.get(tkey)
            if not topic:
                topic = await s.scalar(select(Topic).where(Topic.subject_id == subject.id, func.lower(Topic.name) == tname.lower()))
                if not topic:
                    topic = Topic(subject_id=subject.id, name=tname)
                    s.add(topic)
                    await s.flush()
                topic_cache[tkey] = topic
            image_path = (Path("app/question_images") / Path(item["image"]).name).as_posix() if item["image"] else None
            s.add(Question(
                topic_id=topic.id, text=item["question"], image_path=image_path, question_mode=item["mode"],
                option_a=item["a"], option_b=item["b"], option_c=item["c"], option_d=item["d"],
                correct_option=item["correct"], correct_answer=item["correct_answer"], explanation=item["explanation"],
            ))
        await s.commit()
    return len(valid)

"""Word (.docx) eksport xizmati — Doniyor Academy bot.

Joylashuvi: app/services/word_export.py

Vazifasi: bazadagi yoki yangi yuklangan savollarni chiroyli
Word hujjatiga yig'ish (xotirada, baytlar ko'rinishida).

Formati "master prompt" bilan bir xil:
  savol matni -> A/B/C/D variantlar -> to'g'ri javob "++" bilan.
"""

from __future__ import annotations

import re
from datetime import datetime
from io import BytesIO
from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Cm, Pt, RGBColor

OPTION_KEYS = ("A", "B", "C", "D")
CORRECT_MARK = "++"
MAX_IMAGE_WIDTH_CM = 11.0

_TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e",
    "ё": "yo", "ж": "j", "з": "z", "и": "i", "й": "y", "к": "k",
    "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r",
    "с": "s", "т": "t", "у": "u", "ф": "f", "х": "h", "ц": "ts",
    "ч": "ch", "ш": "sh", "щ": "sh", "ъ": "", "ы": "y", "ь": "",
    "э": "e", "ю": "yu", "я": "ya",
    "ʻ": "", "ʼ": "", "'": "", "`": "",
}


# ---------------------------------------------------------------
#  Ma'lumotni normallashtirish
# ---------------------------------------------------------------

def items_from_bulk(bulk_items: list[dict]) -> list[dict]:
    """Bulk parserdan kelgan savollarni eksport formatiga o'giradi."""
    result = []

    for item in bulk_items:
        options = item.get("options") or {}

        result.append(
            {
                "text": item.get("text") or "",
                "options": {
                    key: options.get(key) for key in OPTION_KEYS
                },
                "correct": item.get("correct"),
                "correct_answer": item.get("correct_answer"),
                "mode": item.get("question_mode") or "closed",
                "image_path": item.get("image_path"),
                "explanation": item.get("explanation"),
            }
        )

    return result


def item_from_question(question) -> dict:
    """ORM Question obyektini eksport formatiga o'giradi."""
    return {
        "text": question.text or "",
        "options": {
            "A": question.option_a,
            "B": question.option_b,
            "C": question.option_c,
            "D": question.option_d,
        },
        "correct": (question.correct_option or "").strip().upper() or None,
        "correct_answer": question.correct_answer,
        "mode": question.question_mode or "closed",
        "image_path": question.image_path,
        "explanation": question.explanation,
    }


def items_from_questions(questions) -> list[dict]:
    return [item_from_question(q) for q in questions]


# ---------------------------------------------------------------
#  Fayl nomi
# ---------------------------------------------------------------

def _slug(value: str, limit: int = 40) -> str:
    text = (value or "").strip().lower()
    text = "".join(_TRANSLIT.get(ch, ch) for ch in text)
    text = re.sub(r"[^a-z0-9]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return text[:limit] or "test"


def export_filename(
    subject_name: str,
    topic_name: str,
    *,
    with_answers: bool = True,
    stamp: datetime | None = None,
) -> str:
    """Masalan: DA_tarix_1_mavzu_javobli_2026-11-23.docx"""
    moment = stamp or datetime.now()
    suffix = "javobli" if with_answers else "javobsiz"

    return (
        "DA_"
        + _slug(subject_name, 24)
        + "_"
        + _slug(topic_name, 32)
        + "_"
        + suffix
        + "_"
        + moment.strftime("%Y-%m-%d_%H%M")
        + ".docx"
    )


# ---------------------------------------------------------------
#  Hujjatni qurish
# ---------------------------------------------------------------

def _setup_styles(document: Document) -> None:
    normal = document.styles["Normal"]
    normal.font.name = "Times New Roman"
    normal.font.size = Pt(12)

    paragraph_format = normal.paragraph_format
    paragraph_format.space_after = Pt(4)
    paragraph_format.line_spacing = 1.15

    for section in document.sections:
        section.top_margin = Cm(2.0)
        section.bottom_margin = Cm(2.0)
        section.left_margin = Cm(2.5)
        section.right_margin = Cm(1.5)


def _add_title(
    document: Document,
    subject_name: str,
    topic_name: str,
    total: int,
    with_answers: bool,
    stamp: datetime,
) -> None:
    title = document.add_paragraph()
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER

    run = title.add_run("DONIYOR ACADEMY")
    run.bold = True
    run.font.size = Pt(16)

    subtitle = document.add_paragraph()
    subtitle.alignment = WD_ALIGN_PARAGRAPH.CENTER

    run = subtitle.add_run(f"{subject_name} — {topic_name}")
    run.bold = True
    run.font.size = Pt(14)

    info = document.add_paragraph()
    info.alignment = WD_ALIGN_PARAGRAPH.CENTER

    run = info.add_run(
        f"Savollar soni: {total}   |   "
        f"{'Javoblar belgilangan' if with_answers else 'Javoblarsiz nusxa'}   |   "
        + stamp.strftime("%d.%m.%Y %H:%M")
    )
    run.font.size = Pt(10)
    run.font.color.rgb = RGBColor(0x60, 0x60, 0x60)

    document.add_paragraph()


def _add_image(document: Document, image_path: str | None) -> None:
    if not image_path:
        return

    path = Path(image_path)

    if not path.is_file():
        return

    try:
        document.add_picture(str(path), width=Cm(MAX_IMAGE_WIDTH_CM))
    except Exception:
        return

    document.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER


def _add_question(
    document: Document,
    index: int,
    item: dict,
    with_answers: bool,
) -> None:
    text = (item.get("text") or "").strip()

    paragraph = document.add_paragraph()
    run = paragraph.add_run(f"{index}. ")
    run.bold = True

    paragraph.add_run(text or "(savol matni rasmda)")

    _add_image(document, item.get("image_path"))

    correct = (item.get("correct") or "").strip().upper()
    mode = item.get("mode") or "closed"

    if mode == "open":
        answer = (item.get("correct_answer") or "").strip()

        if with_answers and answer:
            line = document.add_paragraph()
            line.paragraph_format.left_indent = Cm(0.75)

            run = line.add_run("Javob: ")
            run.bold = True

            run_answer = line.add_run(answer)
            run_answer.bold = True
        else:
            line = document.add_paragraph()
            line.paragraph_format.left_indent = Cm(0.75)
            line.add_run("Javob: " + "_" * 40)

    else:
        options = item.get("options") or {}

        for key in OPTION_KEYS:
            value = options.get(key)

            if value is None or str(value).strip() == "":
                continue

            line = document.add_paragraph()
            line.paragraph_format.left_indent = Cm(0.75)
            line.paragraph_format.space_after = Pt(2)

            is_correct = with_answers and key == correct

            run_key = line.add_run(f"{key}) ")
            run_key.bold = True

            run_value = line.add_run(str(value).strip())

            if is_correct:
                run_key.bold = True
                run_value.bold = True
                line.add_run(" " + CORRECT_MARK).bold = True

    explanation = (item.get("explanation") or "").strip()

    if with_answers and explanation:
        note = document.add_paragraph()
        note.paragraph_format.left_indent = Cm(0.75)

        run = note.add_run("Izoh: " + explanation)
        run.italic = True
        run.font.size = Pt(10)
        run.font.color.rgb = RGBColor(0x50, 0x50, 0x50)

    document.add_paragraph()


def _add_answer_key(document: Document, items: list[dict]) -> None:
    pairs = []

    for index, item in enumerate(items, start=1):
        mode = item.get("mode") or "closed"

        if mode == "open":
            value = (item.get("correct_answer") or "—").strip()
        else:
            value = (item.get("correct") or "—").strip().upper()

        pairs.append(f"{index}-{value}")

    if not pairs:
        return

    document.add_page_break()

    heading = document.add_paragraph()
    run = heading.add_run("JAVOBLAR KALITI")
    run.bold = True
    run.font.size = Pt(14)

    body = document.add_paragraph()
    body.add_run(";  ".join(pairs))


def build_questions_docx(
    subject_name: str,
    topic_name: str,
    items: list[dict],
    *,
    with_answers: bool = True,
    answer_key: bool = True,
    stamp: datetime | None = None,
) -> bytes:
    """Savollar ro'yxatidan .docx fayl yasaydi va baytlarni qaytaradi."""
    moment = stamp or datetime.now()

    document = Document()
    _setup_styles(document)

    _add_title(
        document,
        subject_name or "—",
        topic_name or "—",
        len(items),
        with_answers,
        moment,
    )

    for index, item in enumerate(items, start=1):
        _add_question(document, index, item, with_answers)

    if with_answers and answer_key:
        _add_answer_key(document, items)

    buffer = BytesIO()
    document.save(buffer)

    return buffer.getvalue()


def build_multi_topic_docx(
    subject_name: str,
    sections: list[tuple[str, list[dict]]],
    *,
    with_answers: bool = True,
    answer_key: bool = True,
    stamp: datetime | None = None,
) -> bytes:
    """Butun fan bo'yicha: har mavzu alohida bo'lim bo'lib chiqadi.

    sections = [(mavzu nomi, savollar ro'yxati), ...]
    """
    moment = stamp or datetime.now()
    total = sum(len(items) for _, items in sections)

    document = Document()
    _setup_styles(document)

    _add_title(
        document,
        subject_name or "—",
        f"{len(sections)} mavzu",
        total,
        with_answers,
        moment,
    )

    for position, (topic_name, items) in enumerate(sections):
        if position:
            document.add_page_break()

        heading = document.add_paragraph()
        run = heading.add_run(topic_name or "—")
        run.bold = True
        run.font.size = Pt(13)

        document.add_paragraph()

        for index, item in enumerate(items, start=1):
            _add_question(document, index, item, with_answers)

    if with_answers and answer_key:
        document.add_page_break()

        heading = document.add_paragraph()
        run = heading.add_run("JAVOBLAR KALITI")
        run.bold = True
        run.font.size = Pt(14)

        for topic_name, items in sections:
            if not items:
                continue

            line = document.add_paragraph()
            run = line.add_run((topic_name or "—") + ": ")
            run.bold = True

            pairs = []

            for index, item in enumerate(items, start=1):
                mode = item.get("mode") or "closed"

                if mode == "open":
                    value = (item.get("correct_answer") or "—").strip()
                else:
                    value = (item.get("correct") or "—").strip().upper()

                pairs.append(f"{index}-{value}")

            line.add_run(";  ".join(pairs))

    buffer = BytesIO()
    document.save(buffer)

    return buffer.getvalue()

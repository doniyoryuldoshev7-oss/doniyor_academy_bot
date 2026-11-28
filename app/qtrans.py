"""Ko'p tilli savollar: tarjimani o'qish/yozish va o'quvchi tiliga moslab berish.

Tarjimasi bo'lmagan savol yoki variant o'zbekcha qoladi. To'g'ri javob harfi va variantlar tartibi
asl savolniki bilan bir xil, shuning uchun tekshirish va statistika tilga bog'liq emas.
"""
from __future__ import annotations

from sqlalchemy import select

from .db import SessionLocal
from .models import QuestionTranslation

LANGS = ("ru", "en")
LETTERS = "ABCD"


def lang_of(request) -> str:
    h = getattr(request, "headers", None) or {}
    v = (h.get("X-Lang") or "").strip().lower()[:2]
    return v if v in LANGS else "uz"


async def load(session, ids, lang):
    """{question_id: QuestionTranslation} (faqat shu til)."""
    ids = {int(i) for i in ids if i}
    if not ids or lang not in LANGS:
        return {}
    rows = (await session.scalars(select(QuestionTranslation).where(
        QuestionTranslation.question_id.in_(ids), QuestionTranslation.lang == lang))).all()
    return {r.question_id: r for r in rows}


def _opts(tr):
    return {"A": tr.option_a, "B": tr.option_b, "C": tr.option_c, "D": tr.option_d}


def _apply(view, tr):
    """view: {"t", "o", ["e"]} — joyida o'zgartiriladi. Bo'sh maydonlar o'zbekcha qoladi."""
    if tr.text and tr.text.strip():
        view["t"] = tr.text
    o = view.get("o")
    if isinstance(o, dict):
        for k, v in _opts(tr).items():
            if v and str(v).strip() and k in o:
                o[k] = v
    if "e" in view and tr.explanation and tr.explanation.strip():
        from .services.savol_import import keep_source

        view["e"] = keep_source(tr.explanation, view.get("e"))
    return view


async def localize(views, lang, key="id"):
    """views: savol ko'rinishlari ro'yxati (har birida key — savol id si). Joyida o'zgartiradi va qaytaradi."""
    if lang not in LANGS or not views:
        return views
    try:
        async with SessionLocal() as s:
            trs = await load(s, [v.get(key) for v in views], lang)
        for v in views:
            tr = trs.get(v.get(key))
            if tr is not None:
                _apply(v, tr)
    except Exception:  # noqa: BLE001  — tarjima o'qilmasa, savol o'zbekcha ko'rinaveradi
        pass
    return views


async def localize_exam_items(out_items, items, lang):
    """Imtihon elementlari (pos bo'yicha): payload ichidagi qid orqali."""
    if lang not in LANGS:
        return out_items
    by_pos = {}
    for it in items:
        if it.kind == "c":
            q = (it.data or {}).get("qid")
            if q:
                by_pos[it.pos] = q
    views = []
    for o in out_items:
        if o.get("k") == "c" and o.get("pos") in by_pos:
            views.append(o)
            o["_q"] = by_pos[o["pos"]]
    await localize(views, lang, key="_q")
    for o in views:
        o.pop("_q", None)
    return out_items


async def get_all(session, qid):
    rows = (await session.scalars(select(QuestionTranslation).where(QuestionTranslation.question_id == qid))).all()
    return {r.lang: {"text": r.text or "", "a": r.option_a or "", "b": r.option_b or "", "c": r.option_c or "",
                     "d": r.option_d or "", "explanation": r.explanation or ""} for r in rows}


async def upsert(session, qid, lang, text, opts, explanation=None):
    """opts: {"A":..,"B":..,"C":..,"D":..}. Sessiyani commit QILMAYDI."""
    row = await session.scalar(select(QuestionTranslation).where(
        QuestionTranslation.question_id == qid, QuestionTranslation.lang == lang))
    if row is None:
        row = QuestionTranslation(question_id=qid, lang=lang)
        session.add(row)
    row.text = text
    row.option_a, row.option_b, row.option_c, row.option_d = (opts.get(l) for l in LETTERS)
    row.explanation = explanation or None
    return row


async def remove(session, qid, lang):
    row = await session.scalar(select(QuestionTranslation).where(
        QuestionTranslation.question_id == qid, QuestionTranslation.lang == lang))
    if row is not None:
        await session.delete(row)


async def explanation(qid, lang, default):
    """Bitta savol izohi (javob tekshirilgandan keyin)."""
    if lang not in LANGS:
        return default
    try:
        async with SessionLocal() as s:
            tr = (await load(s, [qid], lang)).get(qid)
        if tr is not None and tr.explanation and tr.explanation.strip():
            from .services.savol_import import keep_source

            return keep_source(tr.explanation, default)
    except Exception:  # noqa: BLE001
        pass
    return default

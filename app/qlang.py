"""Foydalanuvchining tili (bot va veb-ilova uchun umumiy) va savollarni shu tilga moslash (bot uchun).

Til `users.lang` da saqlanadi: uz (standart), ru, en. Savolning tarjimasi bo'lmasa, o'zbekcha ko'rinadi.
Tarjima ko'rinishi bazadagi savolni O'ZGARTIRMAYDI: asl obyekt ustiga yopishtirilgan yengil proksi ishlatiladi.
"""
from __future__ import annotations

import time

from sqlalchemy import select, update

from . import qtrans
from .db import SessionLocal
from .models import User

LANGS = ("uz", "ru", "en")
_TTL = 60.0
_CACHE: dict[int, tuple[str, float]] = {}


def norm(v) -> str:
    v = str(v or "").strip().lower()[:2]
    return v if v in LANGS else "uz"


async def user_lang(tg_id) -> str:
    if not tg_id:
        return "uz"
    now = time.monotonic()
    hit = _CACHE.get(tg_id)
    if hit and now - hit[1] < _TTL:
        return hit[0]
    try:
        async with SessionLocal() as s:
            v = await s.scalar(select(User.lang).where(User.telegram_id == tg_id))
    except Exception:  # noqa: BLE001
        return "uz"
    lang = norm(v)
    _CACHE[tg_id] = (lang, now)
    return lang


async def stored_lang(tg_id):
    """Bazadagi qiymat: 'uz' | 'ru' | 'en' yoki None (hali tanlanmagan)."""
    async with SessionLocal() as s:
        v = await s.scalar(select(User.lang).where(User.telegram_id == tg_id))
    return norm(v) if v else None


async def set_lang(tg_id, lang) -> str:
    lang = norm(lang)
    async with SessionLocal() as s:
        await s.execute(update(User).where(User.telegram_id == tg_id).values(lang=lang))
        await s.commit()
    _CACHE[tg_id] = (lang, time.monotonic())
    return lang


class TQ:
    """Savol ko'rinishi: tarjima maydonlari ustiga qo'yilgan, qolganlari asl savoldan olinadi (faqat o'qish uchun)."""

    __slots__ = ("_q", "_o")

    def __init__(self, q, over):
        object.__setattr__(self, "_q", q)
        object.__setattr__(self, "_o", over)

    def __getattr__(self, name):
        over = object.__getattribute__(self, "_o")
        if name in over:
            return over[name]
        return getattr(object.__getattribute__(self, "_q"), name)

    def __setattr__(self, name, value):
        raise AttributeError("tarjima ko'rinishini o'zgartirib bo'lmaydi")


def _over(tr, q=None):
    over = {}
    for field, val in (("text", tr.text), ("option_a", tr.option_a), ("option_b", tr.option_b),
                       ("option_c", tr.option_c), ("option_d", tr.option_d), ("explanation", tr.explanation)):
        if val and str(val).strip():
            over[field] = val
    if "explanation" in over and q is not None:
        from .services.savol_import import keep_source

        over["explanation"] = keep_source(over["explanation"], getattr(q, "explanation", None))
    return over


async def tq(q, tg_id):
    """Bitta savol: foydalanuvchi tiliga moslangan ko'rinish (tarjima bo'lmasa, asl savol)."""
    if q is None:
        return q
    lang = await user_lang(tg_id)
    if lang == "uz":
        return q
    try:
        async with SessionLocal() as s:
            tr = (await qtrans.load(s, [q.id], lang)).get(q.id)
    except Exception:  # noqa: BLE001
        return q
    return TQ(q, _over(tr, q)) if tr is not None else q


async def tq_map(qmap, tg_id):
    """{id: savol} -> {id: moslangan savol}; bitta so'rov bilan."""
    if not qmap:
        return qmap
    lang = await user_lang(tg_id)
    if lang == "uz":
        return qmap
    try:
        async with SessionLocal() as s:
            trs = await qtrans.load(s, list(qmap), lang)
    except Exception:  # noqa: BLE001
        return qmap
    return {k: (TQ(v, _over(trs[k], v)) if k in trs else v) for k, v in qmap.items()}


async def localize_items(items, tg_id):
    """Imtihon elementlari (payload nusxalari): matn, variantlar va izoh tilga moslanadi. Joyida o'zgaradi."""
    lang = await user_lang(tg_id)
    if lang == "uz" or not items:
        return items
    try:
        ids = {(it.data or {}).get("qid") for it in items if it.kind == "c"}
        async with SessionLocal() as s:
            trs = await qtrans.load(s, ids, lang)
        from .services.imtihon import apply_item_tr

        for it in items:
            if (it.data or {}).get("tr"):
                apply_item_tr(it.data, it.kind, lang)     # Milliy variant: tarjima savolning o'zida
                continue
            if it.kind != "c":
                continue
            tr = trs.get((it.data or {}).get("qid"))
            if tr is not None:
                qtrans._apply(it.data, tr)
    except Exception:  # noqa: BLE001
        pass
    return items

"""Veb-ilova: admin paneli, 1-bosqich (Fanlar, Savol qo'shish). Botdagi admin amallari bilan bir xil qoidalar:
fan, mavzu va fanni o'chirish faqat adminlarga; savol qo'shish adminlarga va savol qo'shuvchilarga.
Savolni o'chirish botdagidek «yumshoq»: savol faol emas deb belgilanadi."""
from __future__ import annotations

import json
import logging

from aiohttp import web
from sqlalchemy import func, select

from .db import SessionLocal
from .models import Question, Subject, Topic

log = logging.getLogger(__name__)

NAME_MAX = 255
TEXT_MAX = 4000
OPT_MAX = 1000
EXPL_MAX = 2000
LIST_MAX = 500


def _helpers():
    from .webapp import _err, _json

    return _err, _json


def _natural(name):
    from .webapp_oquvchi import natural_key

    return natural_key(name)


def _deny(msg):
    return web.HTTPForbidden(text=json.dumps({"error": msg}), content_type="application/json")


async def has_panel(tg_id) -> bool:
    from .admin_roles import can_add_questions, is_teacher

    return bool(await can_add_questions(tg_id) or await is_teacher(tg_id))


async def _admin(request):
    from .admin_roles import is_full_admin

    if not is_full_admin(request["tg_id"]):
        raise _deny("Bu amal faqat adminlar uchun.")


async def _editor(request):
    from .admin_roles import can_add_questions

    if not await can_add_questions(request["tg_id"]):
        raise _deny("Savol qo'shish huquqingiz yo'q.")


# ---------------------------------------------------------------
#  Sof tekshiruvlar (bazasiz sinash mumkin)
# ---------------------------------------------------------------
def clean_name(value):
    v = " ".join(str(value or "").split())
    return v if 0 < len(v) <= NAME_MAX else None


def clean_question(body, require_all=True):
    """Yopiq savol maydonlarini tekshiradi. Qaytaradi: (maydonlar, xato matni)."""
    text = str(body.get("text") or "").strip()
    opts = {k: str(body.get(k) or "").strip() for k in "abcd"}
    correct = str(body.get("correct") or "").strip().upper()
    expl = str(body.get("explanation") or "").strip()
    if expl == "-":
        expl = ""
    if require_all:
        if not text:
            return None, "Savol matnini kiriting."
        for k in "abcd":
            if not opts[k]:
                return None, f"{k.upper()} variant bo'sh bo'lishi mumkin emas."
    if len(text) > TEXT_MAX or any(len(v) > OPT_MAX for v in opts.values()) or len(expl) > EXPL_MAX:
        return None, "Matn juda uzun."
    if correct not in ("A", "B", "C", "D"):
        return None, "To'g'ri javob harfini tanlang."
    return {"text": text, **opts, "correct": correct, "explanation": expl or None}, None


def snippet(text, n=70):
    t = " ".join((text or "").split())
    return t if len(t) <= n else t[:n] + "…"


# ---------------------------------------------------------------
async def api_g_me(request):
    from .admin_roles import can_add_questions, is_full_admin, is_teacher

    tg = request["tg_id"]
    return web.json_response({"admin": bool(is_full_admin(tg)), "editor": bool(await can_add_questions(tg)),
                              "teacher": bool(await is_teacher(tg))})


async def api_g_tree(request):
    await _editor(request)
    async with SessionLocal() as s:
        subs = list((await s.scalars(select(Subject))).all())
        tops = list((await s.scalars(select(Topic))).all())
    subs.sort(key=lambda x: _natural(x.name))
    out = []
    for sub in subs:
        ts = sorted((t for t in tops if t.subject_id == sub.id), key=lambda t: _natural(t.name))
        out.append({"id": sub.id, "name": sub.name, "topics": [{"id": t.id, "name": t.name} for t in ts]})
    return web.json_response({"subjects": out})


async def api_g_subjects(request):
    await _admin(request)
    async with SessionLocal() as s:
        subs = list((await s.scalars(select(Subject))).all())
        tcount = {r[0]: int(r[1]) for r in await s.execute(select(Topic.subject_id, func.count(Topic.id)).group_by(Topic.subject_id))}
        qcount = {r[0]: int(r[1]) for r in await s.execute(
            select(Topic.subject_id, func.count(Question.id)).join(Question, Question.topic_id == Topic.id)
            .where(Question.is_active.is_(True)).group_by(Topic.subject_id))}
    subs.sort(key=lambda x: _natural(x.name))
    return web.json_response({"subjects": [
        {"id": x.id, "name": x.name, "topics": tcount.get(x.id, 0), "questions": qcount.get(x.id, 0)} for x in subs]})


async def api_g_subject(request):
    _err, _ = _helpers()
    await _admin(request)
    sid = int(request.match_info["id"])
    async with SessionLocal() as s:
        sub = await s.get(Subject, sid)
        if sub is None:
            return _err("Fan topilmadi.", 404)
        tops = list((await s.scalars(select(Topic).where(Topic.subject_id == sid))).all())
        qcount = {r[0]: int(r[1]) for r in await s.execute(
            select(Question.topic_id, func.count(Question.id)).where(Question.is_active.is_(True)).group_by(Question.topic_id))}
    tops.sort(key=lambda t: _natural(t.name))
    return web.json_response({"id": sub.id, "name": sub.name,
                              "topics": [{"id": t.id, "name": t.name, "questions": qcount.get(t.id, 0)} for t in tops]})


async def api_g_subject_add(request):
    _err, _json = _helpers()
    await _admin(request)
    name = clean_name((await _json(request)).get("name"))
    if not name:
        return _err("Fan nomini kiriting (255 belgigacha).")
    async with SessionLocal() as s:
        if await s.scalar(select(Subject).where(func.lower(Subject.name) == name.lower())):
            return _err("Bu fan allaqachon mavjud.")
        s.add(Subject(name=name))
        await s.commit()
    return web.json_response({"ok": True, "name": name})


async def api_g_subject_delete(request):
    _err, _json = _helpers()
    await _admin(request)
    try:
        sid = int((await _json(request)).get("id"))
    except (TypeError, ValueError):
        return _err("Noto'g'ri so'rov.")
    async with SessionLocal() as s:
        sub = await s.get(Subject, sid)
        if sub is None:
            return _err("Fan topilmadi.", 404)
        name = sub.name
        await s.delete(sub)
        await s.commit()
    return web.json_response({"ok": True, "name": name})


async def api_g_topic_add(request):
    _err, _json = _helpers()
    await _admin(request)
    body = await _json(request)
    name = clean_name(body.get("name"))
    try:
        sid = int(body.get("subject_id"))
    except (TypeError, ValueError):
        return _err("Fan tanlanmadi.")
    if not name:
        return _err("Mavzu nomini kiriting (255 belgigacha).")
    async with SessionLocal() as s:
        sub = await s.get(Subject, sid)
        if sub is None:
            return _err("Fan topilmadi.", 404)
        if await s.scalar(select(Topic).where(Topic.subject_id == sid, func.lower(Topic.name) == name.lower())):
            return _err("Bu mavzu allaqachon mavjud.")
        sub_name = sub.name
        s.add(Topic(subject_id=sid, name=name))
        await s.commit()
    return web.json_response({"ok": True, "name": name, "subject": sub_name})


async def api_g_topic_rename(request):
    _err, _json = _helpers()
    await _admin(request)
    body = await _json(request)
    name = clean_name(body.get("name"))
    try:
        tid = int(body.get("id"))
    except (TypeError, ValueError):
        return _err("Noto'g'ri so'rov.")
    if not name:
        return _err("Mavzu nomini kiriting (255 belgigacha).")
    async with SessionLocal() as s:
        t = await s.get(Topic, tid)
        if t is None:
            return _err("Mavzu topilmadi.", 404)
        if await s.scalar(select(Topic).where(Topic.subject_id == t.subject_id, func.lower(Topic.name) == name.lower(), Topic.id != tid)):
            return _err("Bu fanda shunday mavzu allaqachon bor.")
        t.name = name
        await s.commit()
    return web.json_response({"ok": True, "name": name})


async def api_g_topic_delete(request):
    _err, _json = _helpers()
    await _admin(request)
    try:
        tid = int((await _json(request)).get("id"))
    except (TypeError, ValueError):
        return _err("Noto'g'ri so'rov.")
    async with SessionLocal() as s:
        t = await s.get(Topic, tid)
        if t is None:
            return _err("Mavzu topilmadi.", 404)
        sid = t.subject_id
        await s.delete(t)
        await s.commit()
    return web.json_response({"ok": True, "subject_id": sid})


async def api_g_questions(request):
    _err, _ = _helpers()
    await _admin(request)
    tid = int(request.match_info["id"])
    async with SessionLocal() as s:
        t = await s.get(Topic, tid)
        if t is None:
            return _err("Mavzu topilmadi.", 404)
        qs = list((await s.scalars(select(Question).where(Question.is_active.is_(True), Question.topic_id == tid)
                                   .order_by(Question.id))).all())
    return web.json_response({"id": t.id, "name": t.name, "subject_id": t.subject_id, "total": len(qs), "questions": [
        {"id": q.id, "t": snippet(q.text) or ("🖼 Rasmli savol" if q.image_path else "—"), "img": bool(q.image_path)}
        for q in qs[:LIST_MAX]]})


async def api_g_question(request):
    _err, _ = _helpers()
    await _admin(request)
    qid = int(request.match_info["id"])
    async with SessionLocal() as s:
        q = await s.get(Question, qid)
        if q is None or not q.is_active:
            return _err("Savol topilmadi.", 404)
        from . import qtrans

        tr = await qtrans.get_all(s, qid)
    return web.json_response({
        "id": q.id, "topic_id": q.topic_id, "mode": q.question_mode or "closed", "img": bool(q.image_path),
        "text": q.text or "", "a": q.option_a or "", "b": q.option_b or "", "c": q.option_c or "", "d": q.option_d or "",
        "correct": (q.correct_option or "").strip().upper(), "explanation": q.explanation or "", "tr": tr})


async def api_g_question_add(request):
    _err, _json = _helpers()
    await _editor(request)
    body = await _json(request)
    try:
        tid = int(body.get("topic_id"))
    except (TypeError, ValueError):
        return _err("Mavzu tanlanmadi.")
    f, e = clean_question(body)
    if e:
        return _err(e)
    async with SessionLocal() as s:
        t = await s.get(Topic, tid)
        if t is None:
            return _err("Mavzu topilmadi.", 404)
        s.add(Question(topic_id=tid, text=f["text"], option_a=f["a"], option_b=f["b"], option_c=f["c"], option_d=f["d"],
                       correct_option=f["correct"], explanation=f["explanation"], question_mode="closed"))
        await s.commit()
    return web.json_response({"ok": True})


async def api_g_question_tr(request):
    """Savol tarjimasini saqlash (ru/en). Hamma maydon bo'sh bo'lsa, tarjima o'chiriladi."""
    _err, _json = _helpers()
    await _editor(request)
    body = await _json(request)
    from . import qtrans

    lang = str(body.get("lang") or "").strip().lower()
    if lang not in qtrans.LANGS:
        return _err("Til noto'g'ri.")
    try:
        qid = int(body.get("id"))
    except (TypeError, ValueError):
        return _err("Noto'g'ri so'rov.")
    text = str(body.get("text") or "").strip()
    opts = {k.upper(): str(body.get(k) or "").strip() for k in "abcd"}
    expl = str(body.get("explanation") or "").strip()
    if expl == "-":
        expl = ""
    empty = not text and not any(opts.values()) and not expl
    if not empty:
        if not text:
            return _err("Tarjimada savol matnini kiriting.")
        for k in "ABCD":
            if not opts[k]:
                return _err(f"Tarjimada {k} variant bo'sh bo'lishi mumkin emas.")
        if len(text) > TEXT_MAX or any(len(v) > OPT_MAX for v in opts.values()) or len(expl) > EXPL_MAX:
            return _err("Matn juda uzun.")
    async with SessionLocal() as s:
        q = await s.get(Question, qid)
        if q is None or not q.is_active:
            return _err("Savol topilmadi.", 404)
        if empty:
            await qtrans.remove(s, qid, lang)
        else:
            await qtrans.upsert(s, qid, lang, text, opts, expl or None)
        await s.commit()
    return web.json_response({"ok": True, "removed": empty})


async def api_g_question_edit(request):
    _err, _json = _helpers()
    await _admin(request)
    body = await _json(request)
    try:
        qid = int(body.get("id"))
    except (TypeError, ValueError):
        return _err("Noto'g'ri so'rov.")
    async with SessionLocal() as s:
        q = await s.get(Question, qid)
        if q is None or not q.is_active:
            return _err("Savol topilmadi.", 404)
        image_only = (q.question_mode or "closed") != "closed"
        f, e = clean_question(body, require_all=not image_only)
        if e:
            return _err(e)
        if not image_only:
            q.text, q.option_a, q.option_b, q.option_c, q.option_d = f["text"], f["a"], f["b"], f["c"], f["d"]
        q.correct_option = f["correct"]
        q.explanation = f["explanation"]
        tid = q.topic_id
        await s.commit()
    return web.json_response({"ok": True, "topic_id": tid})


async def api_g_question_delete(request):
    _err, _json = _helpers()
    await _admin(request)
    try:
        qid = int((await _json(request)).get("id"))
    except (TypeError, ValueError):
        return _err("Noto'g'ri so'rov.")
    async with SessionLocal() as s:
        q = await s.get(Question, qid)
        if q is None or not q.is_active:
            return _err("Savol topilmadi.", 404)
        tid = q.topic_id
        q.is_active = False
        await s.commit()
    return web.json_response({"ok": True, "topic_id": tid})


def setup(app: web.Application) -> None:
    r = app.router
    r.add_get("/api/g/me", api_g_me)
    r.add_get("/api/g/tree", api_g_tree)
    r.add_get("/api/g/subjects", api_g_subjects)
    r.add_get("/api/g/subject/{id:\\d+}", api_g_subject)
    r.add_post("/api/g/subject_add", api_g_subject_add)
    r.add_post("/api/g/subject_delete", api_g_subject_delete)
    r.add_post("/api/g/topic_add", api_g_topic_add)
    r.add_post("/api/g/topic_rename", api_g_topic_rename)
    r.add_post("/api/g/topic_delete", api_g_topic_delete)
    r.add_get("/api/g/questions/{id:\\d+}", api_g_questions)
    r.add_get("/api/g/question/{id:\\d+}", api_g_question)
    r.add_post("/api/g/question_add", api_g_question_add)
    r.add_post("/api/g/question_edit", api_g_question_edit)
    r.add_post("/api/g/question_tr", api_g_question_tr)
    r.add_post("/api/g/question_delete", api_g_question_delete)

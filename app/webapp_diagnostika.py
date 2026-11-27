"""Veb-ilova: Diagnostika (botdagi bilan bir xil servis: app/services/diagnostic.py).

Savollar serverda tanlanadi va seans (token) serverda saqlanadi. To'g'ri javoblar mijozga
YUBORILMAYDI, natija serverda qayta hisoblanadi. Botdagidek, diagnostika oxirigacha ishlanadi
va bitta test yozuvi sifatida saqlanadi. Seans bitta marta ishlatiladi (qayta yuborilsa, ikki marta saqlanmaydi).
"""
from __future__ import annotations

import logging
import secrets
import time

from aiohttp import web
from sqlalchemy import select

from .db import SessionLocal
from .models import Question, Topic, User

log = logging.getLogger(__name__)

STEP = 5
MAX_PER_SUBJECT = 200
SESSION_TTL = 3 * 3600
MAX_SESSIONS = 500
_SESSIONS: dict = {}


def _helpers():
    from .webapp import _err, _json

    return _err, _json


def _natural(name):
    from .webapp_oquvchi import natural_key

    return natural_key(name)


def _purge(now: float | None = None) -> None:
    now = now or time.time()
    for k in [k for k, v in _SESSIONS.items() if now - v["ts"] > SESSION_TTL]:
        _SESSIONS.pop(k, None)
    while len(_SESSIONS) > MAX_SESSIONS:
        _SESSIONS.pop(min(_SESSIONS, key=lambda k: _SESSIONS[k]["ts"]), None)


def clean_budgets(raw, capacity: dict) -> dict:
    """{fan_id: soni} ni tozalaydi: faqat mavjud fanlar, 1..mavjud savol soni."""
    out = {}
    if not isinstance(raw, dict):
        return out
    for k, v in raw.items():
        try:
            sid, n = int(k), int(v)
        except (TypeError, ValueError):
            continue
        info = capacity.get(sid)
        if info is None or n <= 0:
            continue
        out[sid] = min(n, info["count"], MAX_PER_SUBJECT)
    return out


def clean_ids(raw) -> list:
    out = []
    for x in raw if isinstance(raw, list) else []:
        try:
            out.append(int(x))
        except (TypeError, ValueError):
            pass
    return out[:500]


def level_groups(result) -> dict:
    def pack(items):
        return [{"id": t.topic_id, "name": t.topic_name, "pct": t.accuracy, "correct": t.correct, "total": t.total}
                for t in items]

    return {"strong": pack(result.strong), "medium": pack(result.medium),
            "weak": pack(result.weak), "unclear": pack(result.unclear)}


# ---------------------------------------------------------------
async def api_d_setup(request):
    from .services.diagnostic import subject_capacity

    async with SessionLocal() as s:
        cap = await subject_capacity(s)
    subs = [{"id": k, "name": v["name"], "count": v["count"]} for k, v in cap.items()]
    subs.sort(key=lambda x: _natural(x["name"]))
    return web.json_response({"subjects": subs, "step": STEP})


async def api_d_topics(request):
    from .services.diagnostic import topic_capacity

    _err, _ = _helpers()
    sid = int(request.match_info["id"])
    async with SessionLocal() as s:
        counts = await topic_capacity(s, sid)
        topics = list((await s.scalars(select(Topic).where(Topic.subject_id == sid))).all())
    topics = [t for t in topics if counts.get(t.id)]
    if not topics:
        return _err("Bu fanda savolli mavzu yo'q.")
    topics.sort(key=lambda t: _natural(t.name))
    return web.json_response({"topics": [{"id": t.id, "name": t.name, "count": counts[t.id]} for t in topics]})


async def api_d_start(request):
    from .services.diagnostic import build_plan, subject_capacity
    from .webapp_oquvchi import _qview

    _err, _json = _helpers()
    body = await _json(request)
    async with SessionLocal() as s:
        cap = await subject_capacity(s)
        budgets = clean_budgets(body.get("budgets"), cap)
        if not budgets:
            return _err("Avval savol sonini belgilang.")
        topic_ids = clean_ids(body.get("topic_ids")) if len(budgets) == 1 else []
        plan = await build_plan(s, user_id=request["user_id"], budgets=budgets, topic_ids=topic_ids or None)
    if not plan.questions:
        return _err("Tanlovingiz bo'yicha savol topilmadi. Boshqa fan yoki mavzu tanlab ko'ring.")
    info = {str(p.topic_id): [p.topic_name, p.subject_name] for p in plan.topics}
    skipped = [[p.topic_name, p.subject_name] for p in plan.skipped]
    _purge()
    token = secrets.token_urlsafe(16)
    _SESSIONS[token] = {"uid": request["user_id"], "ts": time.time(),
                        "qids": [q.id for q in plan.questions], "info": info, "skipped": skipped}
    from . import qtrans as _qt

    views = await _qt.localize([_qview(q) for q in plan.questions], _qt.lang_of(request))
    return web.json_response({"token": token, "questions": views})


async def api_d_finish(request):
    from .services.diagnostic import save_diagnostic, usable
    from .webapp_oquvchi import clean_letter

    _err, _json = _helpers()
    body = await _json(request)
    token = str(body.get("token") or "")
    sess = _SESSIONS.get(token)
    if sess is None or sess["uid"] != request["user_id"] or time.time() - sess["ts"] > SESSION_TTL:
        _SESSIONS.pop(token, None)
        return _err("Bu seans endi faol emas (ehtimol ilova qayta ishga tushgan). Qaytadan boshlang.", 410)
    sel = {}
    for a in body.get("answers") if isinstance(body.get("answers"), list) else []:
        try:
            qid = int(a.get("qid"))
        except (TypeError, ValueError, AttributeError):
            continue
        letter = clean_letter(a.get("sel"))
        if letter and qid in sess["qids"]:
            sel[qid] = letter
    if not sel:
        return _err("Hali birorta savolga javob berilmadi.")
    # seans darhol yopiladi: qayta yuborilsa, natija ikki marta saqlanmaydi
    _SESSIONS.pop(token, None)
    async with SessionLocal() as s:
        qmap = {q.id: q for q in (await s.scalars(select(Question).where(Question.id.in_(list(sel))))).all()}
        answers = []
        for qid in sess["qids"]:
            q = qmap.get(qid)
            if qid not in sel or q is None or not q.is_active or not usable(q):
                continue
            right = (q.correct_option or "").strip().upper()
            names = sess["info"].get(str(q.topic_id)) or ["—", "—"]
            answers.append({"qid": qid, "topic_id": q.topic_id, "topic_name": names[0], "subject_name": names[1],
                            "selected": sel[qid], "correct": sel[qid] == right})
        if not answers:
            return _err("Natijani hisoblab bo'lmadi. Qaytadan boshlang.")
        user = await s.get(User, request["user_id"])
        if user is None:
            return _err("Hisobingiz topilmadi.", 403)
        try:
            res = await save_diagnostic(s, user, answers, skipped=sess["skipped"])
        except Exception:
            log.exception("veb-diagnostika saqlanmadi")
            await s.rollback()
            return _err("Natijani saqlashda xato yuz berdi.", 500)
        nxt = res.next_topic
        nxt_out = None
        if nxt is not None:
            t = await s.get(Topic, nxt.topic_id)
            nxt_out = {"id": nxt.topic_id, "name": nxt.topic_name, "level": nxt.level,
                       "subject_id": t.subject_id if t else None}
    import os

    return web.json_response({
        "title": res.title, "total": res.total, "correct": res.correct, "pct": res.accuracy,
        "groups": level_groups(res), "skipped": [x[0] for x in res.skipped],
        "next": nxt_out, "smart": res.correct < res.total and os.getenv("SMART_REVIEW_ENABLED", "0") == "1",
    })


def setup(app: web.Application) -> None:
    r = app.router
    r.add_get("/api/d/setup", api_d_setup)
    r.add_get("/api/d/topics/{id:\\d+}", api_d_topics)
    r.add_post("/api/d/start", api_d_start)
    r.add_post("/api/d/finish", api_d_finish)

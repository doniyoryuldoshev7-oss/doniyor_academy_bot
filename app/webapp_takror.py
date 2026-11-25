"""Veb-ilova: Bilimlar xaritasi va Smart Takrorlash (botdagi bilan bir xil servislar va qoidalar)."""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

from aiohttp import web
from sqlalchemy import func, select

from .db import SessionLocal
from .models import Question, SmartReview, Subject, User

log = logging.getLogger(__name__)

LEVEL_ICON = {"strong": "🟢", "medium": "🟡", "weak": "🔴", "unknown": "⚪", "empty": "⚪"}
LEVEL_TEXT = {
    "strong": "mustahkam", "medium": "o'rtacha", "weak": "zaif",
    "unknown": "ma'lumot kam", "empty": "boshlanmagan",
}
TIP_TEXT = {
    "weak": "eng zaif joyingiz, shundan boshlang.",
    "unknown": "eng zaif joyingiz, shundan boshlang.",
    "empty": "hali boshlanmagan mavzu.",
    "medium": "biroz mashq qilsa, mustahkamlanadi.",
    "strong": "biroz mashq qilsa, mustahkamlanadi.",
}


def _helpers():
    from .webapp import _err, _json

    return _err, _json


def _natural(name):
    from .webapp_oquvchi import natural_key

    return natural_key(name)


def review_enabled() -> bool:
    return os.getenv("SMART_REVIEW_ENABLED", "0") == "1"


# ---------------------------------------------------------------
#  Bilimlar xaritasi
# ---------------------------------------------------------------
async def api_k_subjects(request):
    from .services.mastery import overall_accuracy, percent

    async with SessionLocal() as s:
        subs = list((await s.scalars(select(Subject).where(Subject.is_active.is_(True)))).all())
        out = []
        for sub in subs:
            answered, correct = await overall_accuracy(s, request["user_id"], sub.id)
            out.append({"id": sub.id, "name": sub.name, "answered": answered,
                        "pct": percent(correct / answered) if answered else None})
    out.sort(key=lambda x: _natural(x["name"]))
    return web.json_response({"subjects": out})


async def api_k_subject(request):
    from .services.mastery import percent, subject_stat, weakest_topic

    _err, _ = _helpers()
    async with SessionLocal() as s:
        st = await subject_stat(s, request["user_id"], int(request.match_info["id"]))
    if st is None:
        return _err("Fan topilmadi.", 404)
    topics = sorted(st.topics, key=lambda t: _natural(t.name))
    weak = weakest_topic(st)
    return web.json_response({
        "name": st.name,
        "answered": st.answered, "correct": st.correct,
        "pct": percent(st.accuracy) if st.answered else None,
        "topics": [{
            "id": t.topic_id, "name": t.name, "level": t.level,
            "icon": LEVEL_ICON[t.level], "label": LEVEL_TEXT[t.level],
            "pct": percent(t.accuracy) if t.answered else None,
            "done": t.distinct_answered, "total": t.total_questions, "review": t.review_pending,
        } for t in topics],
        "tip": ({"name": weak.name, "text": TIP_TEXT.get(weak.level, "")} if weak else None),
    })


# ---------------------------------------------------------------
#  Smart Takrorlash
# ---------------------------------------------------------------
async def api_r_dashboard(request):
    if not review_enabled():
        return web.json_response({"enabled": False})
    from .services.smart_review_queue import reviewable_condition

    now = datetime.now(timezone.utc)
    uid = request["user_id"]

    async def count(*conds):
        return int(await s.scalar(
            select(func.count(SmartReview.id)).join(Question, Question.id == SmartReview.question_id)
            .where(SmartReview.user_id == uid, *conds)) or 0)

    async with SessionLocal() as s:
        due = await count(SmartReview.status == "active", SmartReview.next_review_at <= now, reviewable_condition())
        upcoming = await count(SmartReview.status == "active", SmartReview.next_review_at > now, reviewable_condition())
        mastered = int(await s.scalar(
            select(func.count(SmartReview.id)).where(SmartReview.user_id == uid, SmartReview.status == "mastered")) or 0)
    return web.json_response({"enabled": True, "due": due, "upcoming": upcoming, "mastered": mastered})


async def api_r_due(request):
    _err, _ = _helpers()
    if not review_enabled():
        return _err("Smart Takrorlash hali sinov rejimida.")
    from .services.smart_review_queue import get_due_reviews
    from .webapp_oquvchi import _qview

    async with SessionLocal() as s:
        rows = await get_due_reviews(s, user_id=request["user_id"], limit=20)
    return web.json_response({"questions": [_qview(q) for _, q in rows]})


async def api_r_answer(request):
    _err, _json = _helpers()
    if not review_enabled():
        return _err("Funksiya hozircha o'chirilgan.")
    from .services.smart_review_service import register_correct_review, register_wrong_answer
    from .webapp_oquvchi import clean_letter

    body = await _json(request)
    sel = clean_letter(body.get("sel"))
    try:
        qid = int(body.get("qid"))
    except (TypeError, ValueError):
        return _err("Noto'g'ri so'rov.")
    if not sel:
        return _err("Noto'g'ri javob varianti.")
    now = datetime.now(timezone.utc)
    uid = request["user_id"]
    async with SessionLocal() as s:
        review = await s.scalar(
            select(SmartReview).where(SmartReview.user_id == uid, SmartReview.question_id == qid).with_for_update())
        q = await s.get(Question, qid)
        if (review is None or q is None or review.status != "active"
                or review.next_review_at is None or review.next_review_at > now):
            return _err("Savolning takrorlash muddati faol emas.")
        right = (q.correct_option or "").strip().upper()
        ok = sel == right
        if ok:
            res = await register_correct_review(s, uid, qid, now=now)
        else:
            res = await register_wrong_answer(s, uid, qid, now=now)
        if res is None:
            return _err("Natijani saqlab bo'lmadi.")
        await s.commit()
        mastered = res.status == "mastered"
    return web.json_response({"ok": ok, "correct": right, "mastered": mastered, "e": q.explanation})


def setup(app: web.Application) -> None:
    r = app.router
    r.add_get("/api/k/subjects", api_k_subjects)
    r.add_get("/api/k/subject/{id:\\d+}", api_k_subject)
    r.add_get("/api/r/dashboard", api_r_dashboard)
    r.add_get("/api/r/due", api_r_due)
    r.add_post("/api/r/answer", api_r_answer)

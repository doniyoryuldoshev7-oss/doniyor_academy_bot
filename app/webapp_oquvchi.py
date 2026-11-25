"""Veb-ilova: o'quvchi bo'limi (botdagi testlar, xatolar, tarix, statistika, reyting, profil).

Bot bilan bir xil ma'lumotlar bazasi va bir xil qoidalar ishlatiladi:
test natijasi TestAttempt/AnswerLog ga yoziladi, foydalanuvchi hisobi yangilanadi.
To'g'ri javoblar testni boshlashda mijozga YUBORILMAYDI: har javob serverda tekshiriladi,
yakuniy natija ham serverda qayta hisoblanadi.
"""
from __future__ import annotations

import logging
import os
import random
import re
from pathlib import Path

from aiohttp import web
from sqlalchemy import func, select

from .db import SessionLocal
from .models import AnswerLog, Question, Subject, TestAttempt, Topic, User

log = logging.getLogger(__name__)

LETTERS = "ABCD"
MAX_TEST = 200
HIST_LIMIT = 20
UI_DIR = Path(__file__).resolve().parent / "webui"


# ---------------------------------------------------------------
#  Sof mantiq (bazasiz sinash mumkin)
# ---------------------------------------------------------------
def natural_key(name: str):
    parts = re.split(r"(\d+)", (name or "").casefold())
    return tuple((0, int(p)) if p.isdigit() else (1, p) for p in parts)


def grade_of(pct: float) -> str:
    if pct >= 90:
        return "🏆 A'lo"
    if pct >= 70:
        return "🥇 Yaxshi"
    if pct >= 50:
        return "📖 Qoniqarli"
    return "💪 Ko'proq mashq kerak"


def clean_letter(value) -> str | None:
    v = str(value or "").strip().upper()
    return v if v in LETTERS and len(v) == 1 else None


def score_answers(qids, answers, correct_map):
    """qids: testdagi savollar; answers: [{qid, sel}]; correct_map: {qid: to'g'ri harf}.
    Qaytaradi: (total, correct, logs[{qid, sel, ok}]). Mijoz yuborgan to'g'rilik e'tiborga olinmaydi."""
    seen = []
    for q in qids if isinstance(qids, list) else []:
        try:
            q = int(q)
        except (TypeError, ValueError):
            continue
        if q in correct_map and q not in seen:
            seen.append(q)
    mine = {}
    for a in answers if isinstance(answers, list) else []:
        if not isinstance(a, dict):
            continue
        try:
            q = int(a.get("qid"))
        except (TypeError, ValueError):
            continue
        sel = clean_letter(a.get("sel"))
        if q in seen and sel:
            mine[q] = sel
    logs = [{"qid": q, "sel": mine[q], "ok": mine[q] == correct_map[q]} for q in seen if q in mine]
    return len(seen), sum(1 for x in logs if x["ok"]), logs


def _closed():
    """Faqat A-D javobli, faol savollar (ochiq savollar botdagidek chiqmaydi)."""
    return (Question.is_active.is_(True), Question.correct_option.in_(list(LETTERS)))


def _qview(q, with_answer=False):
    out = {
        "id": q.id, "t": q.text or "",
        "o": {"A": q.option_a or "", "B": q.option_b or "", "C": q.option_c or "", "D": q.option_d or ""},
        "img": bool(q.image_path),
    }
    if with_answer:
        out["c"] = (q.correct_option or "").strip().upper()
        out["e"] = q.explanation
    return out


def _fmt(dt):
    return dt.strftime("%d.%m.%Y %H:%M") if dt else ""


def _helpers():
    from .webapp import _err, _json  # kech import: webapp.py bu modulni ulaydi

    return _err, _json


# ---------------------------------------------------------------
#  Fan va mavzular
# ---------------------------------------------------------------
async def api_s_subjects(request):
    async with SessionLocal() as s:
        subs = (await s.scalars(select(Subject).where(Subject.is_active.is_(True)).order_by(Subject.name))).all()
        rows = (
            await s.execute(
                select(Topic.subject_id, func.count(Question.id))
                .join(Question, Question.topic_id == Topic.id)
                .where(*_closed())
                .group_by(Topic.subject_id)
            )
        ).all()
    cnt = {sid: n for sid, n in rows}
    return web.json_response({"subjects": [{"id": x.id, "name": x.name, "count": int(cnt.get(x.id, 0))} for x in subs]})


async def api_s_topics(request):
    _err, _ = _helpers()
    sid = int(request.match_info["id"])
    async with SessionLocal() as s:
        sub = await s.get(Subject, sid)
        if sub is None or not sub.is_active:
            return _err("Fan topilmadi.", 404)
        topics = (await s.scalars(select(Topic).where(Topic.subject_id == sid))).all()
        rows = (
            await s.execute(
                select(Question.topic_id, func.count(Question.id))
                .where(Question.topic_id.in_([t.id for t in topics] or [0]), *_closed())
                .group_by(Question.topic_id)
            )
        ).all()
    cnt = {tid: n for tid, n in rows}
    topics = sorted(topics, key=lambda t: natural_key(t.name))
    return web.json_response({
        "subject": {"id": sub.id, "name": sub.name},
        "topics": [{"id": t.id, "name": t.name, "count": int(cnt.get(t.id, 0))} for t in topics],
    })


# ---------------------------------------------------------------
#  Test
# ---------------------------------------------------------------
async def api_s_start(request):
    _err, _json = _helpers()
    body = await _json(request)
    try:
        tid = int(body.get("topic_id"))
    except (TypeError, ValueError):
        return _err("Mavzu tanlanmadi.")
    async with SessionLocal() as s:
        topic = await s.get(Topic, tid)
        if topic is None:
            return _err("Mavzu topilmadi.", 404)
        qs = (await s.scalars(select(Question).where(Question.topic_id == tid, *_closed()))).all()
    if not qs:
        return _err("Bu mavzuda hozircha test yo'q.")
    try:
        size = int(body.get("size") or len(qs))
    except (TypeError, ValueError):
        size = len(qs)
    size = max(1, min(size, len(qs), MAX_TEST))
    picked = random.sample(list(qs), size)
    return web.json_response({"topic": {"id": topic.id, "name": topic.name}, "questions": [_qview(q) for q in picked]})


async def api_s_check(request):
    _err, _json = _helpers()
    body = await _json(request)
    sel = clean_letter(body.get("sel"))
    try:
        qid = int(body.get("qid"))
    except (TypeError, ValueError):
        return _err("Savol topilmadi.")
    async with SessionLocal() as s:
        q = await s.get(Question, qid)
    if q is None or not q.is_active or not clean_letter(q.correct_option):
        return _err("Savol topilmadi.", 404)
    if not sel:
        return _err("Variant noto'g'ri.")
    right = clean_letter(q.correct_option)
    return web.json_response({"ok": sel == right, "correct": right, "e": q.explanation})


async def api_s_finish(request):
    _err, _json = _helpers()
    body = await _json(request)
    try:
        tid = int(body.get("topic_id"))
    except (TypeError, ValueError):
        return _err("Mavzu topilmadi.")
    qids = body.get("qids")
    if not isinstance(qids, list) or not qids or len(qids) > MAX_TEST:
        return _err("Test ma'lumoti noto'g'ri.")
    async with SessionLocal() as s:
        try:
            ids = [int(x) for x in qids]
        except (TypeError, ValueError):
            return _err("Test ma'lumoti noto'g'ri.")
        qs = (await s.scalars(select(Question).where(Question.id.in_(ids), Question.topic_id == tid, *_closed()))).all()
        cmap = {q.id: clean_letter(q.correct_option) for q in qs}
        total, correct, logs = score_answers(ids, body.get("answers"), cmap)
        if not total:
            return _err("Test ma'lumoti noto'g'ri.")
        user = await s.get(User, request["user_id"])
        attempt = TestAttempt(user_id=user.id, topic_id=tid, total=total, correct=correct, score=correct)
        s.add(attempt)
        await s.flush()
        for x in logs:
            s.add(AnswerLog(attempt_id=attempt.id, question_id=x["qid"], selected_option=x["sel"], is_correct=x["ok"]))
            if os.getenv("SMART_REVIEW_ENABLED", "0") == "1" and not x["ok"]:
                try:
                    from .services.smart_review_service import register_wrong_answer

                    await register_wrong_answer(s, user_id=user.id, question_id=x["qid"])
                except Exception:
                    log.exception("web: smart review")
        user.total_tests += 1
        user.total_questions += total
        user.correct_answers += correct
        user.total_score += correct
        await s.commit()
        aid = attempt.id
    pct = correct / total * 100 if total else 0
    return web.json_response({
        "attempt_id": aid, "total": total, "correct": correct,
        "pct": round(pct, 1), "score": correct, "grade": grade_of(pct),
    })


# ---------------------------------------------------------------
#  Tarix, tafsilot
# ---------------------------------------------------------------
async def api_s_history(request):
    async with SessionLocal() as s:
        atts = (
            await s.scalars(
                select(TestAttempt).where(TestAttempt.user_id == request["user_id"])
                .order_by(TestAttempt.created_at.desc()).limit(HIST_LIMIT)
            )
        ).all()
        tmap = {}
        if atts:
            tmap = {t.id: t for t in (await s.scalars(select(Topic).where(Topic.id.in_({a.topic_id for a in atts})))).all()}
    out = []
    for a in atts:
        t = tmap.get(a.topic_id)
        out.append({
            "id": a.id, "topic": t.name if t else "Mavzu o'chirilgan",
            "total": a.total, "correct": a.correct,
            "pct": round(a.correct / a.total * 100, 1) if a.total else 0,
            "score": a.score, "date": _fmt(a.created_at),
        })
    return web.json_response({"attempts": out})


async def api_s_attempt(request):
    _err, _ = _helpers()
    aid = int(request.match_info["id"])
    async with SessionLocal() as s:
        a = await s.get(TestAttempt, aid)
        if a is None or a.user_id != request["user_id"]:
            return _err("Bu testni ko'rishga ruxsat yo'q.", 403)
        topic = await s.get(Topic, a.topic_id)
        logs = (await s.scalars(select(AnswerLog).where(AnswerLog.attempt_id == a.id).order_by(AnswerLog.id))).all()
        qmap = {}
        if logs:
            qmap = {q.id: q for q in (await s.scalars(select(Question).where(Question.id.in_({x.question_id for x in logs})))).all()}
    pct = a.correct / a.total * 100 if a.total else 0
    items = []
    for x in logs:
        q = qmap.get(x.question_id)
        if q is None:
            continue
        v = _qview(q, with_answer=True)
        v.update({"yours": (x.selected_option or "").strip().upper(), "ok": bool(x.is_correct)})
        items.append(v)
    return web.json_response({
        "topic": topic.name if topic else "Mavzu o'chirilgan",
        "total": a.total, "correct": a.correct, "pct": round(pct, 1), "score": a.score,
        "unanswered": max(0, a.total - len(logs)), "grade": grade_of(pct), "date": _fmt(a.created_at),
        "items": items,
    })


# ---------------------------------------------------------------
#  Profil, statistika, reyting
# ---------------------------------------------------------------
async def api_s_profile(request):
    async with SessionLocal() as s:
        u = await s.get(User, request["user_id"])
    pct = u.correct_answers / u.total_questions * 100 if u.total_questions else 0
    return web.json_response({
        "name": u.full_name, "username": u.username, "group": u.group_name,
        "tests": u.total_tests, "questions": u.total_questions, "correct": u.correct_answers,
        "wrong": max(0, u.total_questions - u.correct_answers), "pct": round(pct, 1), "score": u.total_score,
    })


async def api_s_leaderboard(request):
    async with SessionLocal() as s:
        top = (await s.scalars(select(User).order_by(User.total_score.desc(), User.id).limit(10))).all()
        me = await s.get(User, request["user_id"])
        ahead = int(await s.scalar(select(func.count(User.id)).where(User.total_score > me.total_score)) or 0)
    return web.json_response({
        "top": [{"name": u.full_name, "score": u.total_score, "me": u.id == me.id} for u in top],
        "me": {"rank": ahead + 1, "score": me.total_score},
    })


# ---------------------------------------------------------------
#  Mening xatolarim
# ---------------------------------------------------------------
async def api_s_errors(request):
    async with SessionLocal() as s:
        rows = (
            await s.execute(
                select(AnswerLog.question_id, func.max(AnswerLog.id).label("last_id"))
                .join(TestAttempt, AnswerLog.attempt_id == TestAttempt.id)
                .where(TestAttempt.user_id == request["user_id"], AnswerLog.is_correct.is_(False), AnswerLog.resolved.is_(False))
                .group_by(AnswerLog.question_id)
                .order_by(func.max(AnswerLog.id).desc())
            )
        ).all()
        ids = [r.question_id for r in rows]
        qmap = {}
        if ids:
            qmap = {q.id: q for q in (await s.scalars(select(Question).where(Question.id.in_(ids)))).all()}
    return web.json_response({"errors": [_qview(qmap[i]) for i in ids if i in qmap]})


async def api_s_error_solve(request):
    """Xato savolga to'g'ri javob berilsa, u «o'zlashtirildi» deb belgilanadi (javob tarixi o'chmaydi)."""
    _err, _json = _helpers()
    body = await _json(request)
    sel = clean_letter(body.get("sel"))
    try:
        qid = int(body.get("qid"))
    except (TypeError, ValueError):
        return _err("Savol topilmadi.")
    async with SessionLocal() as s:
        q = await s.get(Question, qid)
        if q is None or not clean_letter(q.correct_option):
            return _err("Savol topilmadi.", 404)
        right = clean_letter(q.correct_option)
        if not sel or sel != right:
            return web.json_response({"ok": False})
        logs = (
            await s.scalars(
                select(AnswerLog).join(TestAttempt, AnswerLog.attempt_id == TestAttempt.id)
                .where(TestAttempt.user_id == request["user_id"], AnswerLog.question_id == qid, AnswerLog.is_correct.is_(False))
            )
        ).all()
        for x in logs:
            x.resolved = True
        await s.commit()
    return web.json_response({"ok": True, "correct": right, "e": q.explanation})


# ---------------------------------------------------------------
#  Savol rasmi va sahifa fayllari
# ---------------------------------------------------------------
async def api_s_qimg(request):
    _err, _ = _helpers()
    async with SessionLocal() as s:
        q = await s.get(Question, int(request.match_info["id"]))
    path = q.image_path if q else None
    if not path or not Path(path).is_file():
        return _err("Rasm topilmadi.", 404)
    return web.FileResponse(path, headers={"Cache-Control": "private, max-age=3600"})


# Qo'shimcha bo'limlar: har biri alohida modul (server: webapp_<nom>.py, brauzer: webui/<nom>.js).
# Bittasi xato bersa, qolganlari ishlayveradi.
MODULES = ["takror", "diagnostika", "topshiriq", "admin", "eksport"]
JS_FILES = {"oquvchi"} | set(MODULES)


async def page_js(request):
    name = request.match_info["name"]
    if name not in JS_FILES:
        return web.Response(status=404)
    return web.FileResponse(
        UI_DIR / f"{name}.js",
        headers={"Cache-Control": "no-cache", "X-Content-Type-Options": "nosniff"},
    )


def setup_oquvchi(app: web.Application) -> None:
    r = app.router
    r.add_get("/app/{name:[a-z_]+}.js", page_js)
    r.add_get("/api/s/subjects", api_s_subjects)
    r.add_get("/api/s/topics/{id:\\d+}", api_s_topics)
    r.add_post("/api/s/start", api_s_start)
    r.add_post("/api/s/check", api_s_check)
    r.add_post("/api/s/finish", api_s_finish)
    r.add_get("/api/s/history", api_s_history)
    r.add_get("/api/s/attempt/{id:\\d+}", api_s_attempt)
    r.add_get("/api/s/profile", api_s_profile)
    r.add_get("/api/s/leaderboard", api_s_leaderboard)
    r.add_get("/api/s/errors", api_s_errors)
    r.add_post("/api/s/error_solve", api_s_error_solve)
    r.add_get("/api/s/qimg/{id:\\d+}", api_s_qimg)

    import importlib

    for name in MODULES:
        try:
            importlib.import_module(f".webapp_{name}", __package__).setup(app)
        except Exception:
            log.exception("veb-ilova: %s moduli ulanmadi", name)

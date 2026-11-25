"""Veb-ilova: admin paneli — "O'quvchilar natijasi". Botdagi (handlers/student_reports.py) bilan BIR XIL
hisob-kitob: raqamlar o'sha services/student_report.py dan olinadi. Faqat adminlar."""
from __future__ import annotations

import json
import logging
from pathlib import Path

from aiohttp import web

from .db import SessionLocal

log = logging.getLogger(__name__)

PAGE = 10
ANS_PAGE = 8
SORTS = ("faollik", "natija", "ism")


def _helpers():
    from .webapp import _err, _json

    return _err, _json


async def _admin(request):
    from .admin_roles import is_full_admin

    if not is_full_admin(request["tg_id"]):
        raise web.HTTPForbidden(
            text=json.dumps({"error": "Bu bo'lim faqat adminlar uchun."}), content_type="application/json"
        )


def when_label(days) -> str:
    """Botdagi when_label bilan bir xil matnlar."""
    if days is None:
        return "hech qachon"
    if days == 0:
        return "bugun"
    if days == 1:
        return "kecha"
    if days < 7:
        return f"{days} kun oldin"
    if days < 30:
        return f"{days // 7} hafta oldin"
    return f"{days // 30} oy oldin"


def clean_sort(v) -> str:
    return v if v in SORTS else "faollik"


def clean_page(v, pages) -> int:
    try:
        p = int(v)
    except (TypeError, ValueError):
        p = 0
    return max(0, min(p, max(0, pages - 1)))


def filter_rows(rows, q: str):
    q = (q or "").strip().casefold()
    if not q:
        return rows
    return [r for r in rows if q in (r.full_name or "").casefold() or q in (r.group_name or "").casefold()
            or q == str(r.telegram_id)]


def row_json(r, days_since, fmt_datetime):
    return {
        "tg": r.telegram_id, "name": r.full_name, "group": r.group_name or "",
        "attempts": r.attempts, "total": r.total, "correct": r.correct,
        "pct": r.accuracy if r.attempts else None,
        "last": fmt_datetime(r.last_attempt) if r.last_attempt else "",
        "when": when_label(days_since(r.last_attempt)) if r.last_attempt else "hech qachon",
    }


def attempt_json(a, fmt_datetime):
    return {
        "id": a.attempt_id, "at": fmt_datetime(a.created_at), "label": a.label, "subject": a.subject_name,
        "source": a.source, "total": a.total, "correct": a.correct, "pct": a.accuracy,
    }


def trend_json(t):
    if t is None:
        return None
    return {"earlier": t.earlier, "recent": t.recent, "window": t.window, "mode": t.mode, "delta": t.delta}


# ---------------------------------------------------------------
async def api_r_list(request):
    await _admin(request)
    from .services.student_report import days_since, fmt_datetime, students_list

    q = request.query
    sort = clean_sort(q.get("sort"))
    async with SessionLocal() as s:
        rows = await students_list(s, sort=sort)
    total = len(rows)
    never = sum(1 for r in rows if not r.attempts)
    week = sum(1 for r in rows if r.last_attempt is not None and (days_since(r.last_attempt) or 0) < 7)
    rows = filter_rows(rows, q.get("q", ""))
    pages = max(1, (len(rows) + PAGE - 1) // PAGE)
    page = clean_page(q.get("page"), pages)
    chunk = rows[page * PAGE:(page + 1) * PAGE]
    return web.json_response({
        "sort": sort, "page": page, "pages": pages, "found": len(rows), "total": total,
        "week": week, "never": never,
        "items": [row_json(r, days_since, fmt_datetime) for r in chunk],
    })


def student_json(rep) -> dict:
    """O'quvchi kartasi (admin va ustoz paneli uchun umumiy; botdagi card_text bilan bir xil raqamlar)."""
    from .services.student_report import compute_trend, days_since, fmt_date, fmt_datetime

    recent = [attempt_json(a, fmt_datetime) for a in reversed(rep.attempts[-5:])]
    return {
        "tg": rep.telegram_id, "name": rep.full_name, "group": rep.group_name or "",
        "registered": fmt_date(rep.registered_at),
        "count": rep.attempt_count, "total": rep.total, "correct": rep.correct,
        "pct": rep.accuracy if rep.attempt_count else None,
        "first": fmt_date(rep.first_attempt) if rep.attempts else "",
        "last": fmt_datetime(rep.last_attempt) if rep.attempts else "",
        "when": when_label(days_since(rep.last_attempt)) if rep.attempts else "hech qachon",
        "trend": trend_json(compute_trend(rep.attempts)),
        "subjects": [{"name": x.name, "pct": x.accuracy, "attempts": x.attempts} for x in rep.subjects],
        "recent": recent,
    }


async def api_r_student(request):
    await _admin(request)
    from .services.student_report import student_report

    tg = int(request.match_info["tg"])
    async with SessionLocal() as s:
        rep = await student_report(s, tg)
    _err, _ = _helpers()
    if rep is None:
        return _err("O'quvchi topilmadi.", 404)
    return web.json_response(student_json(rep))


async def api_r_history(request):
    await _admin(request)
    from .services.student_report import fmt_datetime, student_report

    tg = int(request.match_info["tg"])
    async with SessionLocal() as s:
        rep = await student_report(s, tg)
    _err, _ = _helpers()
    if rep is None:
        return _err("O'quvchi topilmadi.", 404)
    items = [attempt_json(a, fmt_datetime) for a in reversed(rep.attempts)]
    pages = max(1, (len(items) + PAGE - 1) // PAGE)
    page = clean_page(request.query.get("page"), pages)
    for i, it in enumerate(items):
        it["n"] = i + 1
    return web.json_response({"name": rep.full_name, "tg": tg, "page": page, "pages": pages, "total": len(items),
                  "items": items[page * PAGE:(page + 1) * PAGE]})


async def api_r_attempt(request):
    await _admin(request)
    from .services.student_report import attempt_detail, fmt_datetime

    aid = int(request.match_info["id"])
    async with SessionLocal() as s:
        d = await attempt_detail(s, aid)
    _err, _ = _helpers()
    if d is None:
        return _err("Test topilmadi.", 404)
    ans = []
    for i, a in enumerate(d.answers):
        ans.append({
            "n": i + 1, "qid": a.question_id, "text": a.text or "", "img": a.has_image, "ok": a.is_correct,
            "sel": (a.selected or "").upper(), "right": (a.correct_option or "").upper(),
            "opts": {k: (a.option_text(k) or "") for k in "ABCD"},
        })
    return web.json_response({
        "id": d.attempt_id, "at": fmt_datetime(d.created_at), "topic": d.topic_name, "subject": d.subject_name,
        "total": d.total, "correct": d.correct, "pct": d.accuracy, "student": d.student_name,
        "tg": d.student_telegram_id, "answers": ans,
    })


async def api_r_wrong(request):
    await _admin(request)
    from .services.student_report import student_report, wrong_questions

    tg = int(request.match_info["tg"])
    async with SessionLocal() as s:
        rep = await student_report(s, tg)
        items = await wrong_questions(s, tg, limit=10) if rep else []
    _err, _ = _helpers()
    if rep is None:
        return _err("O'quvchi topilmadi.", 404)
    return web.json_response({"name": rep.full_name, "tg": tg, "items": [
        {"qid": w.question_id, "text": w.text or "", "img": w.has_image, "topic": w.topic_name,
         "wrong": w.wrong, "total": w.total, "pct": w.wrong_percent} for w in items]})


async def api_r_qimg(request):
    await _admin(request)
    from .services.student_report import question_image

    async with SessionLocal() as s:
        it = await question_image(s, int(request.match_info["id"]))
    _err, _ = _helpers()
    if it is None or not it.image_path or not Path(it.image_path).is_file():
        return _err("Rasm topilmadi.", 404)
    return web.FileResponse(it.image_path, headers={"Cache-Control": "private, max-age=3600"})


def setup(app: web.Application) -> None:
    r = app.router
    r.add_get("/api/r/list", api_r_list)
    r.add_get("/api/r/student/{tg:\\d+}", api_r_student)
    r.add_get("/api/r/history/{tg:\\d+}", api_r_history)
    r.add_get("/api/r/attempt/{id:\\d+}", api_r_attempt)
    r.add_get("/api/r/wrong/{tg:\\d+}", api_r_wrong)
    r.add_get("/api/r/qimg/{id:\\d+}", api_r_qimg)

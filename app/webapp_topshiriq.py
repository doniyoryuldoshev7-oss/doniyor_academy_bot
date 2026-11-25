"""Veb-ilova: o'quvchining topshiriqlari (botdagi «Mening topshiriqlarim» bilan bir xil servis).

Topshiriq bitta testda to'liq bajarilishi kerak. Veb-testlar ham botdagidek TestAttempt/AnswerLog
ga yoziladi, shuning uchun topshiriq hisobiga avtomatik kiradi.
"""
from __future__ import annotations

from aiohttp import web

from .db import SessionLocal
from .models import User


def _helpers():
    from .webapp import _err

    return _err


async def api_a_list(request):
    from .services.assignments import active_assignments, fmt, progress_percent, student_progress

    _err = _helpers()
    async with SessionLocal() as s:
        user = await s.get(User, request["user_id"])
        if user is None:
            return _err("Foydalanuvchi topilmadi.", 403)
        rows = await active_assignments(s, user=user)
        out = []
        for a, topic_name, subject_name in rows:
            answered, required, done = await student_progress(s, user, a)
            out.append({
                "id": a.id, "topic_id": a.topic_id, "topic": topic_name, "subject": subject_name,
                "required": required, "answered": answered, "pct": progress_percent(answered, required),
                "done": bool(done), "deadline": fmt(a.deadline), "personal": a.user_id is not None,
                "note": a.note or "",
            })
    return web.json_response({"assignments": out})


def setup(app: web.Application) -> None:
    app.router.add_get("/api/a/list", api_a_list)

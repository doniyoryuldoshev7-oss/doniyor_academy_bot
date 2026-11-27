"""Veb-ilova: "Eslatmalar" (faqat to'liq adminlar). Botdagi handlers/reminders.py bilan BIR XIL xizmatlar va qoidalar:
holat, hozir yuborish (takrorlash + topshiriq eslatmalari), sinov (o'z navbatini hozirga surish), mening navbatim."""
from __future__ import annotations

import asyncio
import html
import json
import logging
import re
import time
from datetime import datetime, timezone

from aiohttp import web

from .db import SessionLocal

log = logging.getLogger(__name__)

_TASKS: set = set()
_JOB: dict = {}


def _helpers():
    from .webapp import _err, _json

    return _err, _json


def _forbid(msg):
    return web.HTTPForbidden(text=json.dumps({"error": msg}), content_type="application/json")


async def _admin(request):
    from .admin_roles import is_full_admin

    if not is_full_admin(request["tg_id"]):
        raise _forbid("Bu bo'lim faqat adminlar uchun.")


class _Bot:
    async def __aenter__(self):
        from aiogram import Bot
        from aiogram.client.default import DefaultBotProperties
        from aiogram.enums import ParseMode

        from .config import settings

        self.bot = Bot(token=settings.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
        return self.bot

    async def __aexit__(self, *a):
        try:
            await self.bot.session.close()
        except Exception:
            pass


def plain(text: str) -> str:
    """Botning HTML matnini oddiy matnga aylantiradi (teglar olib tashlanadi)."""
    return html.unescape(re.sub(r"<[^>]+>", "", text or ""))


def top_rows(rows, limit=10):
    """rows: [(telegram_id, full_name, due_count)] -> [{name, count}] eng ko'pi bilan limit ta (botdagi kabi)."""
    return [{"name": (n or str(t)), "count": int(c)} for t, n, c in rows[:limit]]


async def api_rm_status(request):
    await _admin(request)
    from .services.review_reminder import (
        due_counts,
        local_offset,
        reminder_hour,
        reminders_enabled,
        smart_review_enabled,
    )

    now = datetime.now(timezone.utc)
    async with SessionLocal() as s:
        rows = await due_counts(s, now)
    off = local_offset()
    return web.json_response({
        "smart": bool(smart_review_enabled()),
        "enabled": bool(reminders_enabled()),
        "hour": int(reminder_hour()),
        "tz": int(off.total_seconds() // 3600),
        "students": len(rows),
        "due": sum(int(r[2]) for r in rows),
        "top": top_rows(rows),
        "more": max(0, len(rows) - 10),
        "checked": (now + off).strftime("%d.%m.%Y %H:%M"),
        "job": public_job(),
    })


def public_job():
    j = _JOB
    if not j:
        return None
    return {"running": bool(j.get("running")), "result": j.get("result"), "error": j.get("error"),
            "started": j.get("started"), "finished": j.get("finished")}


async def _run_job():
    from .services.review_reminder import send_reminders

    try:
        async with _Bot() as bot:
            res = await send_reminders(bot)
            tasks = None
            try:
                from .services.assignment_reminder import run_daily

                tr = await run_daily(bot)
                if not tr.get("skipped"):
                    tasks = {"sent": int(tr.get("sent", 0)), "summaries": int(tr.get("summaries", 0))}
            except Exception:
                log.exception("veb: topshiriq eslatmasi xatosi")
        _JOB["result"] = {"skipped": bool(res.get("skipped")), "users": int(res.get("users", 0)),
                          "sent": int(res.get("sent", 0)), "failed": int(res.get("failed", 0)), "tasks": tasks}
    except Exception:
        log.exception("veb: eslatma yuborishda xato")
        _JOB["error"] = "Xato yuz berdi. Loglarni tekshiring."
    finally:
        _JOB["running"] = False
        _JOB["finished"] = int(time.time())


async def api_rm_send(request):
    await _admin(request)
    _err, _rj = _helpers()
    if _JOB.get("running"):
        return _err("Oldingi yuborish hali davom etmoqda. Kuting.", 409)
    _JOB.clear()
    _JOB.update(running=True, result=None, error=None, started=int(time.time()), finished=None)
    t = asyncio.create_task(_run_job())
    _TASKS.add(t)
    t.add_done_callback(_TASKS.discard)
    return web.json_response({"sending": True})


async def api_rm_job(request):
    await _admin(request)
    return web.json_response({"job": public_job()})


async def api_rm_test(request):
    await _admin(request)
    from .handlers.reminders import _pull_reviews_forward

    n = await _pull_reviews_forward(request["tg_id"])
    return web.json_response({"count": int(n)})


async def api_rm_queue(request):
    await _admin(request)
    from .handlers.reminders import queue_report

    text = await queue_report(request["tg_id"])
    return web.json_response({"text": plain(text)})


def setup(app: web.Application) -> None:
    r = app.router
    r.add_get("/api/rm/status", api_rm_status)
    r.add_post("/api/rm/send", api_rm_send)
    r.add_get("/api/rm/job", api_rm_job)
    r.add_post("/api/rm/test", api_rm_test)
    r.add_get("/api/rm/queue", api_rm_queue)

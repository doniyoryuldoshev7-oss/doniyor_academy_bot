"""Veb-ilova: "E'lonlar" (faqat adminlar). Botdagi «📢 E'lonlar» bilan bir xil qoida: xabar BARCHA foydalanuvchilarga
(is_blocked ga qaramay, botdagi kabi) yuboriladi. Veb faqat matn yuboradi. Yuborish fonda, holat so'rab turiladi."""
from __future__ import annotations

import asyncio
import html
import json
import logging
import time

from aiohttp import web
from sqlalchemy import func, select

from .db import SessionLocal

log = logging.getLogger(__name__)

MAX_AD_LEN = 3500
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


def clean_ad(v) -> str:
    return (v if isinstance(v, str) else "").strip()


def ad_problem(text: str):
    if not text:
        return "E'lon matnini yozing."
    if len(text) > MAX_AD_LEN:
        return f"E'lon juda uzun (eng ko'pi {MAX_AD_LEN} belgi)."
    return None


def public_job():
    j = _JOB
    if not j:
        return None
    return {"running": bool(j.get("running")), "total": j.get("total", 0), "ok": j.get("ok", 0),
            "fail": j.get("fail", 0), "started": j.get("started"), "finished": j.get("finished"),
            "preview": j.get("preview", "")}


async def _counts():
    from .models import User

    async with SessionLocal() as s:
        total = await s.scalar(select(func.count(User.id))) or 0
        blocked = await s.scalar(select(func.count(User.id)).where(User.is_blocked.is_(True))) or 0
    return int(total), int(blocked)


async def api_ad_info(request):
    await _admin(request)
    total, blocked = await _counts()
    return web.json_response({"total": total, "blocked": blocked, "job": public_job(), "max": MAX_AD_LEN})


async def api_ad_status(request):
    await _admin(request)
    return web.json_response({"job": public_job()})


async def api_ad_preview(request):
    await _admin(request)
    _err, rj = _helpers()
    body = await rj(request)
    text = clean_ad(body.get("text"))
    bad = ad_problem(text)
    if bad:
        return _err(bad)
    total, _b = await _counts()
    if not total:
        return _err("Foydalanuvchi yo'q.")
    return web.json_response({"count": total, "text": text})


async def _job(tg, text, ids):
    from .tg_retry import send_message as _tg_send

    text = html.escape(text, quote=False)
    try:
        async with _Bot() as bot:
            for i, uid in enumerate(ids, 1):
                try:
                    await _tg_send(bot, uid, text)
                    _JOB["ok"] += 1
                except Exception:
                    _JOB["fail"] += 1
                if i % 10 == 0:
                    await asyncio.sleep(0.3)
    except Exception:
        log.exception("veb: e'lon yuborishda xato")
    finally:
        _JOB["running"] = False
        _JOB["finished"] = int(time.time())


async def api_ad_send(request):
    await _admin(request)
    _err, rj = _helpers()
    body = await rj(request)
    text = clean_ad(body.get("text"))
    bad = ad_problem(text)
    if bad:
        return _err(bad)
    if _JOB.get("running"):
        return _err("Oldingi e'lon hali yuborilmoqda. Kuting.", 409)
    from .models import User

    async with SessionLocal() as s:
        ids = list((await s.scalars(select(User.telegram_id))).all())
    if not ids:
        return _err("Foydalanuvchi yo'q.")
    _JOB.clear()
    _JOB.update(running=True, total=len(ids), ok=0, fail=0, started=int(time.time()), finished=None,
                preview=text[:80])
    t = asyncio.create_task(_job(request["tg_id"], text, ids))
    _TASKS.add(t)
    t.add_done_callback(_TASKS.discard)
    return web.json_response({"sending": True, "count": len(ids)})


def setup(app: web.Application) -> None:
    r = app.router
    r.add_get("/api/ad/info", api_ad_info)
    r.add_get("/api/ad/status", api_ad_status)
    r.add_post("/api/ad/preview", api_ad_preview)
    r.add_post("/api/ad/send", api_ad_send)

"""Veb-ilova: "E'lonlar" (faqat adminlar). Botdagi «📢 E'lonlar» bilan bir xil qoida: xabar BARCHA foydalanuvchilarga
(is_blocked ga qaramay, botdagi kabi) yuboriladi. Veb faqat matn yuboradi. Yuborish fonda, holat so'rab turiladi."""
from __future__ import annotations

import asyncio
import html
import json
import logging
import re
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


_TAGS = ("b", "i", "u", "s", "code", "pre", "blockquote")
_HREF = re.compile(r'^(https?://|tg://)[^\s"<>]+$')


def render_ad(text: str):
    """Oddiy matnni Telegram HTML ga aylantiradi: faqat <b> <i> <u> <s> <code> <pre> <blockquote> va <a href> ruxsat.
    Qolgan hamma narsa oddiy belgi sifatida yuboriladi. Qaytaradi: (html, muammo|None)."""
    out = html.escape(text, quote=False)
    for t in _TAGS:
        out = re.sub(rf"&lt;(/?){t}&gt;", rf"<\1{t}>", out, flags=re.I)

    bad_link = []

    def link(m):
        url = html.unescape(m.group(1)).strip()
        if _HREF.match(url):
            return f'<a href="{html.escape(url)}">'
        bad_link.append(1)
        return m.group(0)

    out = re.sub(r'&lt;a href="([^"<>]*)"&gt;', link, out, flags=re.I)
    if bad_link:
        return out, "Havola https:// yoki tg:// bilan boshlanib, to'liq manzil bo'lishi kerak."
    out = re.sub(r"&lt;/a&gt;", "</a>", out, flags=re.I)
    stack = []
    for m in re.finditer(r"<(/?)(b|i|u|s|code|pre|blockquote|a)\b[^>]*>", out, flags=re.I):
        close, name = m.group(1), m.group(2).lower()
        if close:
            if not stack or stack.pop() != name:
                return out, "Teglar noto'g'ri yopilgan. Har bir ochilgan belgi (qalin, kursiv, havola…) yopilishi kerak."
        else:
            stack.append(name)
    if stack:
        return out, "Teglar noto'g'ri yopilgan. Har bir ochilgan belgi (qalin, kursiv, havola…) yopilishi kerak."
    return out, None


def ad_problem(text: str):
    if not text:
        return "E'lon matnini yozing."
    if len(text) > MAX_AD_LEN:
        return f"E'lon juda uzun (eng ko'pi {MAX_AD_LEN} belgi)."
    return render_ad(text)[1]


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
    return web.json_response({"count": total, "text": text, "html": render_ad(text)[0]})


async def _job(tg, text, ids):
    from .tg_retry import send_message as _tg_send

    text = render_ad(text)[0]
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


async def api_ad_bot(request):
    """Rasm/video/fayl/ovoz va boshqa formatlar uchun: adminning o'ziga botdan «E'lon yuborish» tugmali xabar yuboradi.
    Tugma botning o'z E'lonlar oqimini (adm:send_ad) ochadi — u xabarni aynan o'zgartirmasdan hammaga nusxalaydi."""
    await _admin(request)
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    from .tg_retry import send_message as _tg_send

    markup = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="📢 E'lon yuborishni boshlash", callback_data="adm:send_ad")]])
    try:
        async with _Bot() as bot:
            await _tg_send(bot, request["tg_id"],
                           "📢 <b>E'lon (istalgan format)</b>\n\nPastdagi tugmani bosing, keyin e'lonni yuboring — rasm, video, fayl, ovozli xabar, "
                           "so'rovnoma yoki formatlangan matn. U barcha foydalanuvchilarga aynan shu ko'rinishda boradi.",
                           reply_markup=markup)
    except Exception:
        log.exception("veb: e'lon xabarini adminga yuborib bo'lmadi")
        _err, _rj = _helpers()
        return _err("Botdan xabar yuborib bo'lmadi. Botga /start yozib, qayta urinib ko'ring.", 502)
    return web.json_response({"ok": True})


def setup(app: web.Application) -> None:
    r = app.router
    r.add_get("/api/ad/info", api_ad_info)
    r.add_get("/api/ad/status", api_ad_status)
    r.add_post("/api/ad/preview", api_ad_preview)
    r.add_post("/api/ad/send", api_ad_send)
    r.add_post("/api/ad/bot", api_ad_bot)

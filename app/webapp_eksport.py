"""Veb-ilova: admin paneli, 2-bosqich (Word eksport va Savollar sifati). Botdagi bilan bir xil qoidalar:
Word eksport adminlarga va savol qo'shuvchilarga; Savollar sifati faqat adminlarga.
Word fayl serverda yasaladi va botdagi kabi foydalanuvchining Telegram chatiga yuboriladi."""
from __future__ import annotations

import asyncio
import json
import logging
import re

from aiohttp import web
from sqlalchemy import func, select

from .db import SessionLocal
from .models import Question, Subject, Topic

log = logging.getLogger(__name__)

PAGE = 10
KINDS = ("kalit", "qiyin", "oson", "olik")
_TAG = re.compile(r"<[^>]+>")


def _helpers():
    from .webapp import _err, _json

    return _err, _json


def _natural(name):
    from .webapp_oquvchi import natural_key

    return natural_key(name)


def _deny(msg):
    return web.HTTPForbidden(text=json.dumps({"error": msg}), content_type="application/json")


async def _editor(request):
    from .admin_roles import can_add_questions

    if not await can_add_questions(request["tg_id"]):
        raise _deny("Eksport huquqingiz yo'q.")


async def _admin(request):
    from .admin_roles import is_full_admin

    if not is_full_admin(request["tg_id"]):
        raise _deny("Bu bo'lim faqat adminlar uchun.")


# ---------------------------------------------------------------
#  Sof yordamchilar (bazasiz sinash mumkin)
# ---------------------------------------------------------------
def plain(text) -> str:
    """Bot matnidagi <b> kabi belgilarni olib tashlaydi."""
    return _TAG.sub("", str(text or "")).replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")


def short(text, limit=70) -> str:
    t = " ".join(str(text or "").split())
    return t if len(t) <= limit else t[: limit - 1] + "…"


def page_slice(items, page):
    pages = max(1, (len(items) + PAGE - 1) // PAGE)
    page = max(0, min(int(page or 0), pages - 1))
    return items[page * PAGE:(page + 1) * PAGE], page, pages


def stat_row(s) -> dict:
    top = s.top_wrong
    return {
        "id": s.question_id,
        "t": short(s.text) if (s.text or "").strip() else ("🖼 Rasmli savol" if s.has_image else "Matnsiz savol"),
        "topic": s.topic_name, "subject": s.subject_name,
        "answers": s.answers, "pct": s.difficulty_percent,
        "wrong": top[0] if top else None, "key": s.correct_option,
    }


# ---------------------------------------------------------------
#  Word eksport
# ---------------------------------------------------------------
async def api_x_subjects(request):
    await _editor(request)
    async with SessionLocal() as s:
        subs = list((await s.scalars(select(Subject).where(Subject.is_active.is_(True)))).all())
        qcount = {r[0]: int(r[1]) for r in await s.execute(
            select(Topic.subject_id, func.count(Question.id)).join(Question, Question.topic_id == Topic.id)
            .where(Question.is_active.is_(True)).group_by(Topic.subject_id))}
    subs.sort(key=lambda x: _natural(x.name))
    return web.json_response({"subjects": [{"id": x.id, "name": x.name, "questions": qcount.get(x.id, 0)} for x in subs]})


async def api_x_subject(request):
    _err, _ = _helpers()
    await _editor(request)
    sid = int(request.match_info["id"])
    async with SessionLocal() as s:
        sub = await s.get(Subject, sid)
        if sub is None:
            return _err("Fan topilmadi.", 404)
        tops = list((await s.scalars(select(Topic).where(Topic.subject_id == sid))).all())
        qcount = {r[0]: int(r[1]) for r in await s.execute(
            select(Question.topic_id, func.count(Question.id)).where(Question.is_active.is_(True)).group_by(Question.topic_id))}
    tops.sort(key=lambda t: _natural(t.name))
    rows = [{"id": t.id, "name": t.name, "questions": qcount.get(t.id, 0)} for t in tops]
    return web.json_response({"id": sub.id, "name": sub.name, "topics": rows, "total": sum(r["questions"] for r in rows)})


async def _active(s, topic_id):
    return list((await s.scalars(
        select(Question).where(Question.topic_id == topic_id, Question.is_active.is_(True)).order_by(Question.id))).all())


async def api_x_word(request):
    """Word faylni yasab, foydalanuvchining Telegram chatiga yuboradi."""
    _err, _json = _helpers()
    await _editor(request)
    body = await _json(request)
    kind = body.get("kind")
    with_answers = bool(body.get("answers"))
    try:
        target = int(body.get("id"))
    except (TypeError, ValueError):
        return _err("Noto'g'ri so'rov.")
    if kind not in ("t", "s"):
        return _err("Noto'g'ri so'rov.")

    from .services.word_export import (build_multi_topic_docx, build_questions_docx, export_filename,
                                       items_from_questions)

    async with SessionLocal() as s:
        if kind == "t":
            topic = await s.get(Topic, target)
            if topic is None:
                return _err("Mavzu topilmadi.", 404)
            sub = await s.get(Subject, topic.subject_id)
            subject_name, topic_name = (sub.name if sub else "—"), topic.name
            qs = await _active(s, topic.id)
            if not qs:
                return _err("Bu mavzuda savol yo'q.")
            items = items_from_questions(qs)
            count = len(items)
            build = lambda: build_questions_docx(subject_name=subject_name, topic_name=topic_name,
                                                 items=items, with_answers=with_answers)
        else:
            sub = await s.get(Subject, target)
            if sub is None:
                return _err("Fan topilmadi.", 404)
            subject_name, topic_name = sub.name, "Butun fan"
            tops = list((await s.scalars(select(Topic).where(Topic.subject_id == sub.id))).all())
            tops.sort(key=lambda t: _natural(t.name))
            sections = []
            for t in tops:
                qs = await _active(s, t.id)
                if qs:
                    sections.append((t.name, items_from_questions(qs)))
            if not sections:
                return _err("Bu fanda savol yo'q.")
            count = sum(len(i) for _, i in sections)
            build = lambda: build_multi_topic_docx(subject_name=subject_name, sections=sections, with_answers=with_answers)

    try:
        data = await asyncio.to_thread(build)
    except Exception:
        log.exception("veb Word eksport yasash xatosi")
        return _err("Fayl yasashda xato bo'ldi.", 500)

    filename = export_filename(subject_name, topic_name, with_answers=with_answers)
    sent = await _send(request["tg_id"], data, filename, subject_name, topic_name, count)
    if not sent:
        return _err("Fayl tayyor bo'ldi, lekin Telegram'ga yuborilmadi. Avval botga /start yozib, qayta urinib ko'ring.", 502)
    return web.json_response({"ok": True, "count": count, "filename": filename})


async def _send(chat_id, data, filename, subject_name, topic_name, count) -> bool:
    from aiogram import Bot
    from aiogram.client.default import DefaultBotProperties
    from aiogram.enums import ParseMode

    from .config import settings
    from .services.test_archive import build_caption, send_document

    bot = Bot(token=settings.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    try:
        caption = build_caption(subject_name, topic_name, count, author=None, note="Veb-ilovadan eksport qilindi.")
        return await send_document(bot, chat_id, data, filename, caption)
    except Exception:
        log.exception("veb Word eksport yuborish xatosi")
        return False
    finally:
        try:
            await bot.session.close()
        except Exception:
            pass


# ---------------------------------------------------------------
#  Savollar sifati (faqat adminlar)
# ---------------------------------------------------------------
async def _summary():
    from .services.question_stats import question_stats, summarize

    async with SessionLocal() as s:
        stats = await question_stats(s)
    return stats, summarize(stats)


async def api_x_quality(request):
    await _admin(request)
    _, sm = await _summary()
    return web.json_response({
        "analysed": sm.analysed, "threshold": sm.threshold, "average": sm.average_difficulty,
        "counts": {"kalit": len(sm.suspect), "qiyin": len(sm.hard), "oson": len(sm.easy), "olik": len(sm.dead)},
    })


async def api_x_quality_list(request):
    _err, _ = _helpers()
    await _admin(request)
    from .services.question_stats import by_kind

    kind = request.match_info["kind"]
    if kind not in KINDS:
        return _err("Noto'g'ri toifa.", 404)
    try:
        page = int(request.query.get("page", 0))
    except ValueError:
        page = 0
    _, sm = await _summary()
    items = by_kind(sm, kind)
    chunk, page, pages = page_slice(items, page)
    return web.json_response({"kind": kind, "total": len(items), "page": page, "pages": pages,
                              "items": [stat_row(x) for x in chunk]})


async def api_x_qstat(request):
    _err, _ = _helpers()
    await _admin(request)
    qid = int(request.match_info["id"])
    stats, _sm = await _summary()
    st = next((x for x in stats if x.question_id == qid), None)
    if st is None:
        return _err("Savol statistikasi topilmadi.", 404)
    return web.json_response({
        "id": st.question_id, "subject": st.subject_name, "topic": st.topic_name,
        "text": (st.text or "").strip(), "img": st.has_image,
        "answers": st.answers, "correct": st.correct, "pct": st.difficulty_percent, "key": st.correct_option,
        "options": [{"l": k, "t": short(st.options.get(k), 120) if st.options.get(k) else "",
                     "n": st.picks.get(k, 0), "p": st.pick_percent(k), "key": k == st.correct_option} for k in "ABCD"],
        "verdict": plain(st.verdict),
    })


def setup(app: web.Application) -> None:
    r = app.router
    r.add_get("/api/x/subjects", api_x_subjects)
    r.add_get("/api/x/subject/{id:\\d+}", api_x_subject)
    r.add_post("/api/x/word", api_x_word)
    r.add_get("/api/x/quality", api_x_quality)
    r.add_get("/api/x/quality/{kind}", api_x_quality_list)
    r.add_get("/api/x/qstat/{id:\\d+}", api_x_qstat)

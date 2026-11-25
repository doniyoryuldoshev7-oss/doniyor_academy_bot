"""Veb-ilova: "Ustoz topshirig'i" (admin va ustoz). Botdagi handlers/assignments.py bilan BIR XIL qoidalar:
ustoz faqat o'z guruhlariga topshiriq beradi va faqat o'zi bergan topshiriqlarni ko'radi; admin hamma narsani ko'radi,
"hamma o'quvchilar"ga va shaxsiy (alohida o'quvchiga) topshiriq bera oladi. Topshiriq berilganda o'quvchilarga botdan
xabar boradi. Hisob-kitob services/assignments.py dan olinadi (botdagi bilan bir xil)."""
from __future__ import annotations

import asyncio
import html
import json
import logging
import re

from aiohttp import web
from sqlalchemy import func, select

from .db import SessionLocal

log = logging.getLogger(__name__)

COUNT_CHOICES = (10, 20)
DAY_CHOICES = (1, 2, 3, 7)
SEND_DELAY = 0.06
LIST_LIMIT = 30
_TASKS: set = set()
_SEND: dict = {}          # topshiriq id -> {"sent","total","done"}


def _helpers():
    from .webapp import _err, _json

    return _err, _json


def _forbid(msg):
    return web.HTTPForbidden(text=json.dumps({"error": msg}), content_type="application/json")


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


async def _manager(request):
    """Admin yoki ustoz; aks holda 403."""
    from .admin_roles import is_full_admin, is_teacher

    uid = request["tg_id"]
    if not (is_full_admin(uid) or await is_teacher(uid)):
        raise _forbid("Ruxsat yo'q.")
    return is_full_admin(uid)


async def _admin(request):
    from .admin_roles import is_full_admin

    if not is_full_admin(request["tg_id"]):
        raise _forbid("Bu amal faqat adminlar uchun.")


# ---------------- sof yordamchilar ----------------
def natural_key(name):
    parts = re.split(r"(\d+)", (name or "").casefold())
    return tuple((0, int(p)) if p.isdigit() else (1, p) for p in parts)


def count_options(total):
    """Savollar soni variantlari (botdagi kabi): 10, 20 (mavjudidan oshmaydi) va mavjud savollar soni."""
    total = int(total or 0)
    opts = [n for n in COUNT_CHOICES if n <= total]
    if total and total not in opts:
        opts.append(total)
    if not opts:
        opts = [total or 1]
    return opts


def target_label(a, rows=None):
    if a.user_id:
        return rows[0].full_name if rows else "shaxsiy"
    return a.group_name or "hamma"


def owns(uid, a, is_admin):
    return bool(is_admin or a.created_by == uid)


def row_json(a, topic, subject, fmt):
    return {"id": a.id, "topic": topic, "subject": subject, "required": a.required_questions,
            "deadline": fmt(a.deadline), "target": "shaxsiy" if a.user_id else (a.group_name or "hamma")}


def valid_days(v):
    try:
        d = int(v)
    except (TypeError, ValueError):
        return None
    return d if d in DAY_CHOICES else None


def assignment_text(subject, topic, required, deadline, personal=False):
    head = "📋 <b>SHAXSIY TOPSHIRIQ</b>" if personal else "📋 <b>YANGI TOPSHIRIQ</b>"
    tail = ("Bu — siz uchun tanlangan <b>shaxsiy mashq</b>. Maqsad baho qo'yish emas, shu mavzuni mustahkamlash."
            if personal else "Testni boshlash uchun quyidagi tugmani bosing.")
    return (f"{head}\n\n📚 {html.escape(subject)}\n📖 <b>{html.escape(topic)}</b>\n"
            f"🔢 Kamida <b>{required}</b> ta savol\n⏰ Muddat: <b>{deadline}</b>\n\n{tail}")


def keyboard(topic_id, required):
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="▶️ Testni boshlash", callback_data=f"quizsize:{topic_id}:{required}")],
        [InlineKeyboardButton(text="📋 Mening topshiriqlarim", callback_data="asg:my")]])


async def _groups_for(s, uid, is_admin):
    from .services.assignments import group_names
    from .services.oqituvchi import get_teacher, teacher_groups

    if is_admin:
        return await group_names(s)
    found = await get_teacher(s, uid)
    if found is None:
        return []
    return sorted({g.group_name for g in await teacher_groups(s, found[0].id)}, key=str.casefold)


# ---------------- ro'yxat ----------------
async def api_at_list(request):
    is_admin = await _manager(request)
    from .services.assignments import all_assignments, fmt

    uid = request["tg_id"]
    async with SessionLocal() as s:
        if is_admin:
            rows = await all_assignments(s, limit=LIST_LIMIT)
        else:
            rows = [r for r in await all_assignments(s, limit=500) if r[0].created_by == uid][:LIST_LIMIT]
    return web.json_response({"admin": is_admin, "items": [row_json(a, t, sub, fmt) for a, t, sub in rows]})


# ---------------- yangi topshiriq (qadamlar) ----------------
async def api_at_subjects(request):
    await _manager(request)
    from .models import Subject

    async with SessionLocal() as s:
        subs = list(await s.scalars(select(Subject).where(Subject.is_active.is_(True))))
    subs.sort(key=lambda x: natural_key(x.name))
    return web.json_response({"items": [{"id": x.id, "name": x.name} for x in subs]})


async def api_at_topics(request):
    await _manager(request)
    from .models import Question, Topic

    sid = int(request.match_info["id"])
    async with SessionLocal() as s:
        topics = list(await s.scalars(select(Topic).where(Topic.subject_id == sid)))
        counts = dict((await s.execute(
            select(Question.topic_id, func.count(Question.id))
            .where(Question.topic_id.in_([t.id for t in topics]), Question.is_active.is_(True))
            .group_by(Question.topic_id))).all())
    topics = sorted((t for t in topics if counts.get(t.id)), key=lambda t: natural_key(t.name))
    return web.json_response({"items": [{"id": t.id, "name": t.name, "count": int(counts[t.id])} for t in topics]})


async def api_at_options(request):
    await _manager(request)
    from .models import Question, Topic
    from .services.assignments import deadline_label

    _err, _ = _helpers()
    tid = int(request.match_info["id"])
    async with SessionLocal() as s:
        topic = await s.get(Topic, tid)
        if topic is None:
            return _err("Mavzu topilmadi.", 404)
        total = await s.scalar(select(func.count(Question.id)).where(Question.topic_id == tid, Question.is_active.is_(True)))
    return web.json_response({"id": topic.id, "name": topic.name, "subject_id": topic.subject_id, "total": int(total or 0),
                              "counts": count_options(total),
                              "days": [{"days": d, "label": deadline_label(d)} for d in DAY_CHOICES]})


async def api_at_groups(request):
    is_admin = await _manager(request)
    async with SessionLocal() as s:
        groups = await _groups_for(s, request["tg_id"], is_admin)
    return web.json_response({"all_ok": is_admin, "items": groups})


async def _send_job(aid, students, subject, topic, required, deadline, topic_id, personal=False):
    st = _SEND[aid]
    text = assignment_text(subject, topic, required, deadline, personal)
    kb = keyboard(topic_id, required)
    try:
        async with _Bot() as bot:
            for tg in students:
                try:
                    await bot.send_message(tg, text, reply_markup=kb)
                    st["sent"] += 1
                except Exception as e:  # noqa: BLE001
                    log.warning("veb: topshiriq xabari yuborilmadi (%s): %s", tg, e)
                await asyncio.sleep(SEND_DELAY)
    except Exception:
        log.exception("veb: topshiriq xabarlarini yuborishda xato")
    finally:
        st["done"] = True
        while len(_SEND) > 50:
            _SEND.pop(next(iter(_SEND)))


async def api_at_create(request):
    is_admin = await _manager(request)
    from .models import Question, Topic
    from .services.assignments import (assignment_with_names, create_assignment, fmt, target_users)

    _err, rj = _helpers()
    body = await rj(request)
    try:
        topic_id, count = int(body.get("topic_id")), int(body.get("count"))
    except (TypeError, ValueError):
        return _err("Noto'g'ri so'rov.")
    days = valid_days(body.get("days"))
    if days is None:
        return _err("Muddat noto'g'ri.")
    group = body.get("group")
    uid = request["tg_id"]
    async with SessionLocal() as s:
        topic = await s.get(Topic, topic_id)
        if topic is None:
            return _err("Mavzu topilmadi.", 404)
        total = int(await s.scalar(select(func.count(Question.id)).where(Question.topic_id == topic_id, Question.is_active.is_(True))) or 0)
        if count < 1 or count > total:
            return _err(f"Savollar soni 1 dan {total} gacha bo'lishi kerak.")
        group_name = None
        if group in (None, "", "all"):
            if not is_admin:
                return _err("Guruhni tanlang.")
        else:
            groups = await _groups_for(s, uid, is_admin)
            group_name = next((g for g in groups if g == group), None)
            if group_name is None:
                return _err("Guruh topilmadi (ro'yxat o'zgargan bo'lishi mumkin). Topshiriq yaratilmadi, qaytadan urinib ko'ring.", 409)
        a = await create_assignment(s, topic_id=topic_id, created_by=uid, required_questions=count, days=days, group_name=group_name)
        a, tname, sname = await assignment_with_names(s, a.id)
        students = [u.telegram_id for u in await target_users(s, a)]
        aid, required, deadline = a.id, a.required_questions, fmt(a.deadline)
    _SEND[aid] = {"sent": 0, "total": len(students), "done": not students}
    if students:
        t = asyncio.create_task(_send_job(aid, students, sname, tname, required, deadline, topic_id))
        _TASKS.add(t)
        t.add_done_callback(_TASKS.discard)
    return web.json_response({"id": aid, "topic": tname, "subject": sname, "required": required, "deadline": deadline,
                              "target": group_name or "hamma", "students": len(students)})


async def api_at_sendstatus(request):
    await _manager(request)
    st = _SEND.get(int(request.match_info["id"]))
    return web.json_response(st or {"sent": 0, "total": 0, "done": True, "unknown": True})


# ---------------- bajarilish / olib tashlash ----------------
async def _load(request, s, aid):
    from .services.assignments import assignment_with_names

    is_admin = await _manager(request)
    a, topic, subject = await assignment_with_names(s, aid)
    _err, _ = _helpers()
    if a is None or not a.is_active:
        return None, None, None, _err("Topshiriq topilmadi.", 404)
    if not owns(request["tg_id"], a, is_admin):
        return None, None, None, _err("Bu topshiriq sizniki emas.", 403)
    return a, topic, subject, None


async def api_at_progress(request):
    from .services.assignments import assignment_progress, fmt

    async with SessionLocal() as s:
        a, topic, subject, err = await _load(request, s, int(request.match_info["id"]))
        if err:
            return err
        rows = await assignment_progress(s, a)
        target = target_label(a, rows)
        head = {"id": a.id, "topic": topic, "subject": subject, "required": a.required_questions,
                "deadline": fmt(a.deadline), "target": target}
    done = [r for r in rows if r.done]
    pend = [r for r in rows if not r.done]
    return web.json_response(dict(head, total=len(rows),
        done=[{"name": r.full_name, "correct": r.best_correct, "total": r.best_total, "pct": r.accuracy} for r in done],
        pending=[{"name": r.full_name, "answered": r.answered_best, "required": r.required, "pct": r.progress_percent,
                  "started": bool(r.answered_best)} for r in pend]))


async def api_at_remove(request):
    from .services.assignments import remove_assignment

    _err, rj = _helpers()
    try:
        aid = int((await rj(request)).get("id"))
    except (TypeError, ValueError):
        return _err("Noto'g'ri so'rov.")
    async with SessionLocal() as s:
        a, _t, _s, err = await _load(request, s, aid)
        if err:
            return err
        ok = await remove_assignment(s, aid)
    if not ok:
        return _err("Olib tashlab bo'lmadi.", 409)
    return web.json_response({"ok": True})


# ---------------- shaxsiy topshiriq (faqat admin) ----------------
async def api_at_weak(request):
    await _admin(request)
    from .models import User
    from .services.assignments import weak_topics_for_user

    _err, _ = _helpers()
    tg = int(request.match_info["tg"])
    async with SessionLocal() as s:
        u = await s.scalar(select(User).where(User.telegram_id == tg))
        if u is None:
            return _err("O'quvchi topilmadi.", 404)
        weak = await weak_topics_for_user(s, u.id, limit=4)
        name = u.full_name or "O'quvchi"
    return web.json_response({"name": name, "tg": tg, "items": [
        {"topic_id": w.topic_id, "topic": w.topic_name, "subject": w.subject_name, "pct": w.accuracy,
         "distinct": w.distinct_questions, "available": w.available_questions} for w in weak]})


async def api_at_personal(request):
    await _admin(request)
    from .models import Question, Topic, User
    from .services.assignments import assignment_with_names, create_assignment, deadline_label, fmt

    _err, rj = _helpers()
    body = await rj(request)
    try:
        tg, topic_id = int(body.get("tg")), int(body.get("topic_id"))
    except (TypeError, ValueError):
        return _err("Noto'g'ri so'rov.")
    days = valid_days(body.get("days"))
    if days is None:
        return _err("Muddat noto'g'ri.")
    async with SessionLocal() as s:
        u = await s.scalar(select(User).where(User.telegram_id == tg))
        if u is None:
            return _err("O'quvchi topilmadi.", 404)
        topic = await s.get(Topic, topic_id)
        if topic is None:
            return _err("Mavzu topilmadi.", 404)
        total = int(await s.scalar(select(func.count(Question.id)).where(Question.topic_id == topic_id, Question.is_active.is_(True))) or 0)
        if not total:
            return _err("Bu mavzuda savol yo'q.")
        count = min(20, total)
        a = await create_assignment(s, topic_id=topic_id, created_by=request["tg_id"], required_questions=count, days=days, user_id=u.id)
        a, tname, sname = await assignment_with_names(s, a.id)
        name, aid, deadline = u.full_name or "O'quvchi", a.id, fmt(a.deadline)
    delivered = True
    try:
        async with _Bot() as bot:
            await bot.send_message(tg, assignment_text(sname, tname, count, deadline, personal=True), reply_markup=keyboard(topic_id, count))
    except Exception as e:  # noqa: BLE001
        delivered = False
        log.warning("veb: shaxsiy topshiriq yuborilmadi (%s): %s", tg, e)
    return web.json_response({"id": aid, "name": name, "topic": tname, "required": count, "deadline": deadline, "delivered": delivered})


async def api_at_days(request):
    await _manager(request)
    from .services.assignments import deadline_label

    return web.json_response({"days": [{"days": d, "label": deadline_label(d)} for d in DAY_CHOICES]})


def setup(app: web.Application) -> None:
    r = app.router
    r.add_get("/api/at/list", api_at_list)
    r.add_get("/api/at/subjects", api_at_subjects)
    r.add_get("/api/at/topics/{id:\\d+}", api_at_topics)
    r.add_get("/api/at/options/{id:\\d+}", api_at_options)
    r.add_get("/api/at/groups", api_at_groups)
    r.add_post("/api/at/create", api_at_create)
    r.add_get("/api/at/send/{id:\\d+}", api_at_sendstatus)
    r.add_get("/api/at/progress/{id:\\d+}", api_at_progress)
    r.add_post("/api/at/remove", api_at_remove)
    r.add_get("/api/at/weak/{tg:\\d+}", api_at_weak)
    r.add_post("/api/at/personal", api_at_personal)
    r.add_get("/api/at/days", api_at_days)

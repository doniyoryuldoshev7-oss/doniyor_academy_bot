"""Veb-ilova: Milliy sertifikat / oddiy imtihon (ustoz va admin tomoni). Botdagi handlers/imtihon_ustoz.py bilan BIR XIL qoidalar:
admin hamma imtihonni ko'radi va "hamma o'quvchilar"ga bera oladi; ustoz faqat o'zi yaratgan imtihonlarni ko'radi va faqat o'z guruhlariga beradi.
Savol tanlash, ball hisoblash va natijalar services/imtihon.py dan olinadi (botdagi bilan bir xil)."""
from __future__ import annotations

import asyncio
import html
import json
import logging
import re
from datetime import datetime, timedelta

from aiohttp import web
from sqlalchemy import select

from .db import SessionLocal
from .numfmt import round_half_up
from .tg_retry import send_message as _tg_send

log = logging.getLogger(__name__)

CLOSED_SIZES = (10, 20, 30, 35, 45)
OPEN_SIZES = (0, 2, 5, 10)
DURATIONS = (30, 45, 60, 90, 120, 150, 180)
DAY_CHOICES = (1, 3, 7, 14)
MILLIY_C, MILLIY_O = 35, 10
SEND_DELAY = 0.05
_TASKS: set = set()
_ANN: dict = {}           # imtihon id -> {"sent","failed","total","done"}
_BUSY: set = set()        # (tg, "create") ikki marta bosishdan himoya

FORMAT_HELP = (
    "Savollarni quyidagi ko'rinishda yuboring. Savollar orasiga --- qatori qo'ying.\n\n"
    "XIII asr boshida mo'g'ul qabilalari birlashtirildi.\n"
    "a) Davlat asoschisi nomini yozing => Chingizxon | Temujin\n"
    "b) U qaysi qal'ani poytaxt qilgan? => Qorakorum\n"
    "---\n"
    "Qo'qon xonligi g'arbda kimlar bilan chegaradosh?\n"
    "a) Ikkala davlatni yozing => Buxoro + Xiva\n\n"
    "• | — bir nechta to'g'ri yozilish (istalgani qabul)\n"
    "• + — ikkalasi ham yozilishi kerak (tartibi muhim emas)\n"
    "• Katta-kichik harf, bo'shliq va apostrof farqi hisobga olinmaydi\n"
    "• Har savolda 1 dan 4 tagacha qism (a, b, c, d)"
)


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
    """Admin yoki ustoz; aks holda 403. Qaytaradi: admin ekanligi."""
    from .admin_roles import is_full_admin, is_teacher

    uid = request["tg_id"]
    if not (is_full_admin(uid) or await is_teacher(uid)):
        raise _forbid("Ruxsat yo'q.")
    return bool(is_full_admin(uid))


# ---------------- sof yordamchilar ----------------
def natural_key(name):
    parts = re.split(r"(\d+)", (name or "").casefold())
    return tuple((0, int(p)) if p.isdigit() else (1, p) for p in parts)


def can_see(uid, exam, is_admin):
    return bool(is_admin or exam.created_by == uid)


def avg_text(values, mode):
    """O'rtacha: aniq (Fraction, yarimdan yuqoriga), 1 xona. Milliy: 75 lik ball; oddiy: foiz."""
    if not values:
        return None
    return round_half_up(sum(values) / len(values), 1)


def score_text(score, mode):
    return f"{score.value} ({score.level})" if mode == "milliy" else f"{score.value}%"


def exam_row(e, open_now, fmt):
    return {"id": e.id, "title": e.title, "open": bool(open_now), "mode": e.mode, "group": e.group_name or "",
            "subject": e.subject_name, "closes": fmt(e.closes_at) if e.closes_at else ""}


def validate_make(body, is_admin, groups):
    """Yaratish so'rovini tekshiradi. Qaytaradi: (ma'lumot, xato matni)."""
    mode = body.get("mode")
    if mode not in ("milliy", "oddiy"):
        return None, "Shablon noto'g'ri."
    try:
        sids = [int(x) for x in (body.get("sids") or [])]
        dur = int(body.get("dur"))
        days = int(body.get("days"))
        tid = int(body["tid"]) if body.get("tid") else None
        c = MILLIY_C if mode == "milliy" else int(body.get("c"))
        o = MILLIY_O if mode == "milliy" else int(body.get("o"))
    except (TypeError, ValueError):
        return None, "Noto'g'ri so'rov."
    if not sids or len(set(sids)) != len(sids):
        return None, "Fanni tanlang."
    if tid and len(sids) != 1:
        return None, "Mavzu faqat bitta fan uchun tanlanadi."
    if mode == "oddiy" and (c not in CLOSED_SIZES or o not in OPEN_SIZES):
        return None, "Savollar soni noto'g'ri."
    if dur not in DURATIONS:
        return None, "Davomiylik noto'g'ri."
    if days not in DAY_CHOICES:
        return None, "Muddat noto'g'ri."
    group = body.get("group")
    if group in (None, "", "all"):
        if not is_admin:
            return None, "Guruhni tanlang."
        group = None
    elif group not in groups:
        return None, "Guruh topilmadi (ro'yxat o'zgargan bo'lishi mumkin). Imtihon yaratilmadi, qaytadan urinib ko'ring."
    title = str(body.get("title") or "").strip()[:200]
    return {"mode": mode, "sids": sids, "tid": tid, "c": c, "o": o, "dur": dur, "days": days, "group": group, "title": title}, None


def announce_text(title, real_c, real_o, duration, deadline_text):
    e = html.escape
    return ("📝 <b>Yangi imtihon!</b>\n\n" f"<b>{e(title)}</b>\n"
            f"Savollar: {real_c} yopiq" + (f" + {real_o} ochiq" if real_o else "") + "\n"
            f"⏱ Vaqt: {duration} daqiqa\n" f"📅 Boshlash muddati: {deadline_text} gacha")


async def _groups_for(s, uid, is_admin):
    from .services.assignments import group_names
    from .services.oqituvchi import get_teacher, teacher_groups

    if is_admin:
        return await group_names(s)
    found = await get_teacher(s, uid)
    if found is None:
        return []
    return sorted({g.group_name for g in await teacher_groups(s, found[0].id)}, key=str.casefold)


# ---------------- yangi imtihon: qadamlar ----------------
async def api_ex_subjects(request):
    await _manager(request)
    from .models import Subject

    async with SessionLocal() as s:
        subs = list(await s.scalars(select(Subject).where(Subject.is_active.is_(True))))
    subs.sort(key=lambda x: natural_key(x.name))
    return web.json_response({"items": [{"id": x.id, "name": x.name} for x in subs[:40]]})


async def api_ex_topics(request):
    """Fan mavzulari va butun fan bo'yicha yopiq/ochiq savollar soni (botdagi «Butun fan» tugmasi)."""
    await _manager(request)
    from .models import Subject, Topic
    from .services.imtihon import bank_counts

    _err, _ = _helpers()
    sid = int(request.match_info["id"])
    async with SessionLocal() as s:
        sub = await s.get(Subject, sid)
        if sub is None:
            return _err("Fan topilmadi.", 404)
        topics = list(await s.scalars(select(Topic).where(Topic.subject_id == sid)))
        closed, opened = await bank_counts(s, sid, None)
    topics.sort(key=lambda t: natural_key(t.name))
    return web.json_response({"subject": sub.name, "closed": closed, "open": opened,
                              "items": [{"id": t.id, "name": t.name} for t in topics]})


async def api_ex_avail(request):
    """Tanlangan fan(lar)/mavzudagi bazadagi savollar soni."""
    await _manager(request)
    from .models import Subject, Topic
    from .services.imtihon import bank_counts

    _err, rj = _helpers()
    body = await rj(request)
    try:
        sids = [int(x) for x in (body.get("sids") or [])]
        tid = int(body["tid"]) if body.get("tid") else None
    except (TypeError, ValueError):
        return _err("Noto'g'ri so'rov.")
    if not sids or (tid and len(sids) != 1):
        return _err("Fanni tanlang.")
    async with SessionLocal() as s:
        subs = [await s.get(Subject, i) for i in sids]
        if any(x is None for x in subs):
            return _err("Fan topilmadi.", 404)
        tname = None
        if tid:
            t = await s.get(Topic, tid)
            if t is None or t.subject_id != sids[0]:
                return _err("Mavzu topilmadi.", 404)
            tname = t.name
        closed, opened = await bank_counts(s, sids if len(sids) > 1 else sids[0], tid)
    return web.json_response({"closed": closed, "open": opened, "name": " + ".join(x.name for x in subs)[:200], "topic": tname})


async def api_ex_options(request):
    await _manager(request)
    return web.json_response({"closed": list(CLOSED_SIZES), "open": list(OPEN_SIZES), "durations": list(DURATIONS),
                              "days": list(DAY_CHOICES), "milliy": [MILLIY_C, MILLIY_O]})


async def api_ex_groups(request):
    is_admin = await _manager(request)
    async with SessionLocal() as s:
        groups = await _groups_for(s, request["tg_id"], is_admin)
    return web.json_response({"all_ok": is_admin, "items": groups[:40]})


async def _announce_job(eid, owner_text, students, text, markup_data):
    st = _ANN[eid]
    try:
        from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

        markup = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="📝 Ko'rish", callback_data=markup_data)]])
        async with _Bot() as bot:
            for tg in students:
                try:
                    await _tg_send(bot, tg, text, reply_markup=markup)
                    st["sent"] += 1
                except Exception as e:  # noqa: BLE001
                    st["failed"] += 1
                    log.warning("veb: imtihon xabari yuborilmadi (%s): %s", tg, e)
                await asyncio.sleep(SEND_DELAY)
    except Exception:
        log.exception("veb: imtihon xabarlarini yuborishda xato")
    finally:
        st["done"] = True
        while len(_ANN) > 50:
            _ANN.pop(next(iter(_ANN)))


async def api_ex_create(request):
    is_admin = await _manager(request)
    from .models import Subject
    from .services.imtihon import create_exam, pick_questions, pick_questions_multi, target_students
    from .services.student_report import fmt_datetime, to_local

    _err, rj = _helpers()
    body = await rj(request)
    uid = request["tg_id"]
    key = (uid, "create")
    if key in _BUSY:
        return _err("Imtihon yaratilmoqda, kuting.", 409)
    _BUSY.add(key)
    try:
        async with SessionLocal() as s:
            groups = await _groups_for(s, uid, is_admin)
            d, bad = validate_make(body, is_admin, groups)
            if bad:
                return _err(bad)
            subs = [await s.get(Subject, i) for i in d["sids"]]
            if any(x is None for x in subs):
                return _err("Fan topilmadi.", 404)
            sname = " + ".join(x.name for x in subs)[:200]
            title = d["title"] or f"{sname} — {to_local(datetime.utcnow()).strftime('%d.%m.%Y')}"[:200]
            if len(d["sids"]) > 1:
                payloads, real_c, real_o = await pick_questions_multi(s, d["sids"], d["c"], d["o"])
            else:
                payloads, real_c, real_o = await pick_questions(s, d["sids"][0], d["tid"], d["c"], d["o"])
            if not payloads:
                return _err("Savol topilmadi. Imtihon yaratilmadi.")
            exam = await create_exam(s, title=title, subject_name=sname, mode=d["mode"], created_by=uid, group_name=d["group"],
                                     duration_min=d["dur"], days_open=d["days"], payloads=payloads)
            students = await target_students(s, exam)
            eid, etitle, dur, closes = exam.id, exam.title, exam.duration_min, exam.closes_at
            chat_ids = [u.telegram_id for u in students if u.telegram_id != uid]
    finally:
        _BUSY.discard(key)
    deadline = fmt_datetime(closes)
    _ANN[eid] = {"sent": 0, "failed": 0, "total": len(chat_ids), "done": not chat_ids}
    if chat_ids:
        t = asyncio.create_task(_announce_job(eid, None, chat_ids, announce_text(etitle, real_c, real_o, dur, deadline), f"exm:i:{eid}"))
        _TASKS.add(t)
        t.add_done_callback(_TASKS.discard)
    return web.json_response({"id": eid, "title": etitle, "closed": real_c, "open": real_o, "duration": dur,
                              "students": len(chat_ids), "deadline": deadline})


async def api_ex_sendstatus(request):
    await _manager(request)
    st = _ANN.get(int(request.match_info["id"]))
    return web.json_response(st or {"sent": 0, "failed": 0, "total": 0, "done": True, "unknown": True})


# ---------------- imtihonlarim va boshqarish ----------------
async def api_ex_list(request):
    is_admin = await _manager(request)
    from .models import Exam
    from .services.imtihon import exam_is_open
    from .services.student_report import fmt_datetime

    uid = request["tg_id"]
    async with SessionLocal() as s:
        stmt = select(Exam).where(Exam.is_removed.is_(False)).order_by(Exam.id.desc())
        if not is_admin:
            stmt = stmt.where(Exam.created_by == uid)
        exams = list(await s.scalars(stmt.limit(200)))
    return web.json_response({"items": [exam_row(e, exam_is_open(e), fmt_datetime) for e in exams]})


async def _load(request, s, eid):
    from .models import Exam

    is_admin = await _manager(request)
    _err, _ = _helpers()
    exam = await s.get(Exam, eid)
    if exam is None or exam.is_removed or not can_see(request["tg_id"], exam, is_admin):
        return None, _err("Imtihon topilmadi.", 404)
    return exam, None


async def api_ex_exam(request):
    from .models import ExamAttempt
    from .services.imtihon import SCALE_MAX, exam_is_open, exam_results, target_students
    from .services.student_report import fmt_datetime

    eid = int(request.match_info["id"])
    async with SessionLocal() as s:
        exam, err = await _load(request, s, eid)
        if err:
            return err
        targets = await target_students(s, exam)
        attempts = list(await s.scalars(select(ExamAttempt).where(ExamAttempt.exam_id == eid)))
        _items, results = await exam_results(s, exam)
        started = {a.user_id for a in attempts}
        missing = [u.full_name or "—" for u in targets if u.id not in started]
        open_now = exam_is_open(exam)
        data = {"id": exam.id, "title": exam.title, "subject": exam.subject_name, "mode": exam.mode,
                "group": exam.group_name or "", "duration": exam.duration_min, "open": bool(open_now),
                "closes": fmt_datetime(exam.closes_at) if exam.closes_at else "",
                "finished": sum(1 for a in attempts if a.status == "finished"),
                "active": sum(1 for a in attempts if a.status == "active"),
                "missing_count": len(missing), "missing": missing[:50],
                "avg": avg_text([r.score.value for r in results], exam.mode), "scale": int(SCALE_MAX) if exam.mode == "milliy" else 100}
    return web.json_response(data)


async def api_ex_results(request):
    from .services.imtihon import exam_results

    eid = int(request.match_info["id"])
    async with SessionLocal() as s:
        exam, err = await _load(request, s, eid)
        if err:
            return err
        _items, results = await exam_results(s, exam)
        rows = []
        for r in results:
            sc = r.score
            rows.append({"name": r.user.full_name or "—", "value": score_text(sc, exam.mode), "c_ok": sc.c_ok, "c_total": sc.c_total,
                         "o_ok": sc.o_ok, "o_total": sc.o_total, "minutes": r.minutes})
        title = exam.title
    return web.json_response({"title": title, "items": rows})


async def api_ex_hard(request):
    from .services.imtihon import attempt_answers, exam_results, hardest_units

    eid = int(request.match_info["id"])
    async with SessionLocal() as s:
        exam, err = await _load(request, s, eid)
        if err:
            return err
        items, results = await exam_results(s, exam)
        title = exam.title
    out = []
    if results:
        by_pos = {it.pos: it for it in items}
        answers_list = [attempt_answers(r.attempt) for r in results]
        for unit, share in hardest_units(items, answers_list):
            item = by_pos[unit.pos]
            if item.kind == "c":
                label = (item.data.get("t") or "(rasmli savol)").strip()
                tag = f"{unit.pos}-savol"
            else:
                label = item.data["p"][unit.part]["q"].strip()
                tag = f"{unit.pos}-savol, {'abcd'[unit.part]})"
            out.append({"tag": tag, "pct": int(round_half_up(share * 100)), "label": label[:160]})
    return web.json_response({"title": title, "count": len(results), "items": out})


async def api_ex_toggle(request):
    from .services.imtihon import exam_is_open

    eid = int(request.match_info["id"])
    async with SessionLocal() as s:
        exam, err = await _load(request, s, eid)
        if err:
            return err
        if exam_is_open(exam):
            exam.is_open = False
            note = "Imtihon yopildi. Tahlil ochildi."
        else:
            exam.is_open = True
            if exam.closes_at is None or exam.closes_at <= datetime.utcnow():
                exam.closes_at = datetime.utcnow() + timedelta(days=1)
            note = "Imtihon qayta ochildi (1 kun)."
        await s.commit()
    return web.json_response({"ok": True, "note": note})


async def api_ex_delete(request):
    eid = int(request.match_info["id"])
    async with SessionLocal() as s:
        exam, err = await _load(request, s, eid)
        if err:
            return err
        exam.is_removed = True
        await s.commit()
    return web.json_response({"ok": True})


# ---------------- ochiq savollar qo'shish ----------------
async def api_ex_open_help(request):
    await _manager(request)
    return web.json_response({"help": FORMAT_HELP})


async def api_ex_open_add(request):
    await _manager(request)
    from .models import OpenQuestion, Topic
    from .services.imtihon import parse_open_text

    _err, rj = _helpers()
    body = await rj(request)
    try:
        tid = int(body.get("topic_id"))
    except (TypeError, ValueError):
        return _err("Mavzuni tanlang.")
    text = str(body.get("text") or "")
    if not text.strip():
        return _err("Savollarni yozing.")
    if len(text) > 60000:
        return _err("Matn juda uzun.")
    questions, errors = parse_open_text(text)
    errs = [str(e) for e in errors[:10]]
    if body.get("dry"):
        return web.json_response({"count": len(questions), "errors": errs, "saved": False})
    async with SessionLocal() as s:
        topic = await s.get(Topic, tid)
        if topic is None:
            return _err("Mavzu topilmadi.", 404)
        if not questions:
            return _err("Birorta ham savol o'qilmadi." + ((" " + "; ".join(errs)) if errs else ""))
        for q in questions:
            s.add(OpenQuestion(topic_id=tid, stem=q["stem"], parts=json.dumps(q["parts"], ensure_ascii=False), created_by=request["tg_id"]))
        await s.commit()
    return web.json_response({"count": len(questions), "errors": errs, "saved": True})


def setup(app: web.Application) -> None:
    r = app.router
    r.add_get("/api/ex/subjects", api_ex_subjects)
    r.add_get("/api/ex/topics/{id:\\d+}", api_ex_topics)
    r.add_post("/api/ex/avail", api_ex_avail)
    r.add_get("/api/ex/options", api_ex_options)
    r.add_get("/api/ex/groups", api_ex_groups)
    r.add_post("/api/ex/create", api_ex_create)
    r.add_get("/api/ex/send/{id:\\d+}", api_ex_sendstatus)
    r.add_get("/api/ex/list", api_ex_list)
    r.add_get("/api/ex/exam/{id:\\d+}", api_ex_exam)
    r.add_get("/api/ex/results/{id:\\d+}", api_ex_results)
    r.add_get("/api/ex/hard/{id:\\d+}", api_ex_hard)
    r.add_post("/api/ex/toggle/{id:\\d+}", api_ex_toggle)
    r.add_post("/api/ex/delete/{id:\\d+}", api_ex_delete)
    r.add_get("/api/ex/open_help", api_ex_open_help)
    r.add_post("/api/ex/open_add", api_ex_open_add)

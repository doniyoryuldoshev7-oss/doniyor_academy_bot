"""Veb-ilova: "Ustozlar" (admin) va "Ustoz paneli" (ustoz). Botdagi handlers/admin/oqituvchilar.py va
handlers/oqituvchi.py bilan BIR XIL qoidalar va hisob-kitob (xizmatlar qayta ishlatiladi).
Ustozlar bo'limi faqat adminlar uchun. Ustoz paneli har so'rovda ustozlik huquqini qayta tekshiradi va faqat
ustozning o'z guruhlarini ko'rsatadi."""
from __future__ import annotations

import asyncio
import json
import logging

from aiohttp import web
from sqlalchemy import or_, select

from .db import SessionLocal

log = logging.getLogger(__name__)

PICK_PAGE = 10
STUDENT_PAGE = 10
MAX_MESSAGE_LEN = 1000
_TASKS: set = set()
_BUSY: set = set()


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


# ---------------- sof yordamchilar ----------------
def clean_page(v, pages) -> int:
    try:
        p = int(v)
    except (TypeError, ValueError):
        p = 0
    return max(0, min(p, max(0, pages - 1)))


def page_slice(items, page_raw, size):
    pages = max(1, (len(items) + size - 1) // size)
    page = clean_page(page_raw, pages)
    return items[page * size:(page + 1) * size], page, pages


def clean_text(v):
    return (v if isinstance(v, str) else "").strip()


def group_choices(names, mine, norm):
    """Guruhlar ro'yxati: mavjudlari + biriktirilgan lekin hozir o'quvchisiz qolganlari (botdagi kabi)."""
    names = list(names)
    known = {norm(n) for n, _ in names}
    for g in mine:
        if norm(g) not in known:
            names.append((g, 0))
    names.sort(key=lambda it: norm(it[0]))
    return names


# ======================= ADMIN: USTOZLAR =======================
async def api_t_list(request):
    await _admin(request)
    from .models import Teacher, User
    from .services.oqituvchi import teacher_groups

    async with SessionLocal() as s:
        rows = (await s.execute(select(Teacher, User).join(User, User.id == Teacher.user_id).order_by(User.full_name))).all()
        items = [{"id": t.id, "name": u.full_name or "Nomsiz", "username": u.username or "",
                  "groups": len(await teacher_groups(s, t.id))} for t, u in rows]
    return web.json_response({"items": items})


async def _teacher_card(s, tid):
    from .models import Teacher, User
    from .services.oqituvchi import all_group_names, norm, teacher_groups

    t = await s.get(Teacher, tid)
    if t is None:
        return None
    u = await s.get(User, t.user_id)
    groups = await teacher_groups(s, t.id)
    counts = {norm(n): c for n, c in await all_group_names(s)}
    return {"id": t.id, "name": u.full_name or "Nomsiz", "username": u.username or "", "tg": u.telegram_id,
            "groups": [{"name": g.group_name, "count": counts.get(norm(g.group_name), 0)} for g in groups]}


async def api_t_teacher(request):
    await _admin(request)
    _err, _ = _helpers()
    async with SessionLocal() as s:
        d = await _teacher_card(s, int(request.match_info["id"]))
    return web.json_response(d) if d else _err("Ustoz topilmadi.", 404)


async def api_t_pick(request):
    await _admin(request)
    from .models import Teacher, User

    q = clean_text(request.query.get("q")).lstrip("@")[:50]
    stmt = (select(User).outerjoin(Teacher, Teacher.user_id == User.id)
            .where(Teacher.id.is_(None), User.registration_status == "approved", User.is_blocked.is_(False))
            .order_by(User.full_name))
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(User.full_name.ilike(like), User.username.ilike(like)))
    async with SessionLocal() as s:
        users = (await s.scalars(stmt)).all()
    chunk, page, pages = page_slice(list(users), request.query.get("page"), PICK_PAGE)
    return web.json_response({"total": len(users), "page": page, "pages": pages, "q": q,
                              "items": [{"tg": u.telegram_id, "name": u.full_name or "Nomsiz",
                                         "group": u.group_name or "", "username": u.username or ""} for u in chunk]})


async def api_t_new(request):
    await _admin(request)
    from .models import Teacher, User

    _err, rj = _helpers()
    try:
        tg = int((await rj(request)).get("tg"))
    except (TypeError, ValueError):
        return _err("Noto'g'ri so'rov.")
    async with SessionLocal() as s:
        u = await s.scalar(select(User).where(User.telegram_id == tg))
        if u is None:
            return _err("Foydalanuvchi topilmadi.", 404)
        t = await s.scalar(select(Teacher).where(Teacher.user_id == u.id))
        created = False
        if t is None:
            if u.registration_status != "approved" or u.is_blocked:
                return _err("Bu foydalanuvchini ustoz qilib bo'lmaydi (tasdiqlanmagan yoki bloklangan).")
            t = Teacher(user_id=u.id)
            s.add(t)
            await s.commit()
            created = True
        tid = t.id
    notified = None
    if created:
        notified = False
        try:
            async with _Bot() as bot:
                await bot.send_message(tg, "🧑‍🏫 <b>Siz ustoz etib tayinlandingiz!</b>\n\n"
                                       "Admin sizga guruh biriktirgach, bosh menyudagi «Ustoz paneli» tugmasidan "
                                       "o'quvchilaringizni ko'rishingiz mumkin.\nBosh menyuga qaytish uchun /start ni yuboring.")
            notified = True
        except Exception:
            log.warning("veb: yangi ustozga xabar yuborilmadi (%s)", tg)
    return web.json_response({"ok": True, "created": created, "id": tid, "notified": notified})


async def _group_list(s, tid):
    from .models import Teacher
    from .services.oqituvchi import all_group_names, norm, teacher_groups

    t = await s.get(Teacher, tid)
    if t is None:
        return None, None, None
    mine = await teacher_groups(s, tid)
    names = group_choices(await all_group_names(s), [g.group_name for g in mine], norm)
    chosen = {norm(g.group_name) for g in mine}
    return t, mine, [{"name": n, "count": c, "chosen": norm(n) in chosen} for n, c in names]


async def api_t_groups(request):
    await _admin(request)
    _err, _ = _helpers()
    from .models import User

    async with SessionLocal() as s:
        t, _mine, items = await _group_list(s, int(request.match_info["id"]))
        if t is None:
            return _err("Ustoz topilmadi.", 404)
        u = await s.get(User, t.user_id)
    return web.json_response({"id": t.id, "name": u.full_name or "Nomsiz", "items": items})


async def api_t_toggle(request):
    await _admin(request)
    from .models import TeacherGroup
    from .services.oqituvchi import norm

    _err, rj = _helpers()
    body = await rj(request)
    try:
        tid = int(body.get("id"))
    except (TypeError, ValueError):
        return _err("Noto'g'ri so'rov.")
    name = " ".join(clean_text(body.get("name")).split())
    async with SessionLocal() as s:
        t, mine, items = await _group_list(s, tid)
        if t is None:
            return _err("Ustoz topilmadi.", 404)
        hit = next((it for it in items if norm(it["name"]) == norm(name)), None)
        if hit is None:
            return _err("Ro'yxat o'zgargan, qaytadan oching.", 409)
        existing = [g for g in mine if norm(g.group_name) == norm(name)]
        if existing:
            for g in existing:
                await s.delete(g)
            chosen = False
        else:
            s.add(TeacherGroup(teacher_id=tid, group_name=hit["name"][:100]))
            chosen = True
        await s.commit()
    return web.json_response({"ok": True, "chosen": chosen, "name": hit["name"]})


async def api_t_remove(request):
    await _admin(request)
    from sqlalchemy import delete

    from .models import Teacher, TeacherGroup

    _err, rj = _helpers()
    try:
        tid = int((await rj(request)).get("id"))
    except (TypeError, ValueError):
        return _err("Noto'g'ri so'rov.")
    async with SessionLocal() as s:
        await s.execute(delete(TeacherGroup).where(TeacherGroup.teacher_id == tid))
        await s.execute(delete(Teacher).where(Teacher.id == tid))
        await s.commit()
    return web.json_response({"ok": True})


# ======================= USTOZ PANELI =======================
async def _ctx(request, s, gid=None):
    """(teacher, user, group) — har so'rovda huquq qayta tekshiriladi. Huquq yo'q bo'lsa 403."""
    from .models import TeacherGroup
    from .services.oqituvchi import get_teacher

    found = await get_teacher(s, request["tg_id"])
    if found is None:
        raise _forbid("Sizda ustoz huquqi yo'q.")
    teacher, user = found
    group = None
    if gid is not None:
        group = await s.get(TeacherGroup, gid)
        if group is None or group.teacher_id != teacher.id:
            raise _forbid("Bu guruhga huquqingiz yo'q.")
    return teacher, user, group


async def api_t_home(request):
    from .services.oqituvchi import group_students, teacher_groups

    async with SessionLocal() as s:
        teacher, _user, _ = await _ctx(request, s)
        items = []
        for g in await teacher_groups(s, teacher.id):
            st = await group_students(s, g.group_name)
            items.append({"id": g.id, "name": g.group_name, "students": len(st), "idle": sum(1 for x in st if x.is_inactive())})
    return web.json_response({"items": items})


async def api_t_group(request):
    from .services.oqituvchi import INACTIVE_DAYS, group_students, percent

    async with SessionLocal() as s:
        _t, _u, g = await _ctx(request, s, int(request.match_info["gid"]))
        st = await group_students(s, g.group_name)
    idle = [x for x in st if x.is_inactive()]
    total = sum(x.total for x in st)
    correct = sum(x.correct for x in st)
    return web.json_response({
        "id": g.id, "name": g.group_name, "students": len(st), "inactive_days": INACTIVE_DAYS,
        "active": len(st) - len(idle), "idle": len(idle), "blocked": sum(1 for x in st if x.is_blocked),
        "total": total, "correct": correct, "pct": percent(correct, total) if total else None,
        "idle_list": [{"name": x.full_name, "days": x.days_idle()} for x in idle[:10]],
        "idle_more": max(0, len(idle) - 10),
    })


async def api_t_students(request):
    from .services.oqituvchi import group_students
    from .services.student_report import fmt_datetime

    async with SessionLocal() as s:
        _t, _u, g = await _ctx(request, s, int(request.match_info["gid"]))
        st = await group_students(s, g.group_name)
    chunk, page, pages = page_slice(st, request.query.get("page"), STUDENT_PAGE)
    return web.json_response({
        "name": g.group_name, "total": len(st), "page": page, "pages": pages,
        "items": [{"tg": x.telegram_id, "name": x.full_name, "n": page * STUDENT_PAGE + i + 1,
                   "state": "blocked" if x.is_blocked else ("idle" if x.is_inactive() else "ok"),
                   "attempts": x.attempts, "pct": x.accuracy if x.attempts else None,
                   "last": fmt_datetime(x.last_attempt) if x.last_attempt else ""} for i, x in enumerate(chunk)],
    })


async def api_t_rating(request):
    from .services.oqituvchi import group_students

    async with SessionLocal() as s:
        _t, _u, g = await _ctx(request, s, int(request.match_info["gid"]))
        st = await group_students(s, g.group_name)
    ranked = sorted((x for x in st if x.attempts), key=lambda x: (x.accuracy, x.total), reverse=True)
    return web.json_response({"name": g.group_name, "items": [
        {"rank": i + 1, "name": x.full_name, "pct": x.accuracy, "correct": x.correct, "total": x.total, "attempts": x.attempts}
        for i, x in enumerate(ranked[:10])]})


async def api_t_student(request):
    from .services.oqituvchi import group_students
    from .services.student_report import student_report
    from .webapp_hisobot import student_json

    _err, _ = _helpers()
    tg = int(request.match_info["tg"])
    async with SessionLocal() as s:
        _t, _u, g = await _ctx(request, s, int(request.match_info["gid"]))
        st = await group_students(s, g.group_name)
        member = next((x for x in st if x.telegram_id == tg), None)
        if member is None:
            raise _forbid("Bu o'quvchi sizning guruhingizda emas.")
        rep = await student_report(s, tg)
    if rep is None:
        return _err("Ma'lumot topilmadi.", 404)
    d = student_json(rep)
    d.update(username=member.username or "", blocked=bool(member.is_blocked))
    return web.json_response(d)


async def _recipients(request, gid, text):
    """(group_name, teacher_name, [telegram_id]) yoki xato javobi."""
    from .services.oqituvchi import group_students

    async with SessionLocal() as s:
        _t, user, g = await _ctx(request, s, gid)
        st = await group_students(s, g.group_name)
        name, gname = user.full_name or "Ustoz", g.group_name
    rec = [x.telegram_id for x in st if not x.is_blocked and x.telegram_id != request["tg_id"]]
    return gname, name, rec


def _msg_problem(text):
    if not text:
        return "Faqat matn yuboring."
    if len(text) > MAX_MESSAGE_LEN:
        return f"Matn juda uzun ({len(text)} belgi). {MAX_MESSAGE_LEN} belgigacha qisqartiring."
    return None


async def api_t_msg_preview(request):
    _err, rj = _helpers()
    body = await rj(request)
    text = clean_text(body.get("text"))
    bad = _msg_problem(text)
    if bad:
        return _err(bad)
    try:
        gid = int(body.get("gid"))
    except (TypeError, ValueError):
        return _err("Noto'g'ri so'rov.")
    gname, _tn, rec = await _recipients(request, gid, text)
    if not rec:
        return _err("Guruhda xabar yuboriladigan o'quvchi yo'q.")
    return web.json_response({"group": gname, "count": len(rec), "text": text})


async def _send_job(tg, tname, gname, rec, text):
    from .handlers.oqituvchi import _broadcast

    try:
        async with _Bot() as bot:
            await _broadcast(bot, tg, tname, gname, rec, text)
    except Exception:
        log.exception("veb: guruhga xabar yuborishda xato")
    finally:
        _BUSY.discard(tg)


async def api_t_msg_send(request):
    _err, rj = _helpers()
    body = await rj(request)
    text = clean_text(body.get("text"))
    bad = _msg_problem(text)
    if bad:
        return _err(bad)
    try:
        gid = int(body.get("gid"))
    except (TypeError, ValueError):
        return _err("Noto'g'ri so'rov.")
    tg = request["tg_id"]
    gname, tname, rec = await _recipients(request, gid, text)
    if not rec:
        return _err("Guruhda xabar yuboriladigan o'quvchi yo'q.")
    if tg in _BUSY:
        return _err("Oldingi xabar hali yuborilmoqda. Kuting.", 409)
    _BUSY.add(tg)
    t = asyncio.create_task(_send_job(tg, tname, gname, rec, text))
    _TASKS.add(t)
    t.add_done_callback(_TASKS.discard)
    return web.json_response({"sending": True, "count": len(rec), "group": gname})


def setup(app: web.Application) -> None:
    r = app.router
    r.add_get("/api/t/list", api_t_list)
    r.add_get("/api/t/teacher/{id:\\d+}", api_t_teacher)
    r.add_get("/api/t/pick", api_t_pick)
    r.add_post("/api/t/new", api_t_new)
    r.add_get("/api/t/groups/{id:\\d+}", api_t_groups)
    r.add_post("/api/t/toggle", api_t_toggle)
    r.add_post("/api/t/remove", api_t_remove)
    r.add_get("/api/t/home", api_t_home)
    r.add_get("/api/t/group/{gid:\\d+}", api_t_group)
    r.add_get("/api/t/students/{gid:\\d+}", api_t_students)
    r.add_get("/api/t/rating/{gid:\\d+}", api_t_rating)
    r.add_get("/api/t/student/{gid:\\d+}/{tg:\\d+}", api_t_student)
    r.add_post("/api/t/msg_preview", api_t_msg_preview)
    r.add_post("/api/t/msg_send", api_t_msg_send)

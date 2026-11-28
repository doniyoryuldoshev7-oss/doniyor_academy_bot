"""Veb-ilova: admin paneli, 3-bosqich (Foydalanuvchilar, Statistika). Botdagi bilan bir xil qoidalar:
faqat adminlar; tasdiqlash uchun ism-familiya, telefon va guruh to'liq bo'lishi shart; tasdiqlash/rad etishda
foydalanuvchiga botdan xabar boradi. Qo'shimcha himoya: adminni bloklash yoki rad etish mumkin emas."""
from __future__ import annotations

from .tg_retry import retry as _tg_retry
import html
import json
import logging
from datetime import datetime, timedelta

from aiohttp import web
from sqlalchemy import case, func, select

from .db import SessionLocal
from .numfmt import pct_1
from .models import AnswerLog, TestAttempt, User

log = logging.getLogger(__name__)

INVALID = {"", "-", "—", "–", "_", ".", "yo'q", "yo‘q", "yoq", "none", "null", "n/a", "na"}
STATUS = {"approved": "✅ Tasdiqlangan", "pending": "⏳ Kutilmoqda", "rejected": "❌ Rad etilgan"}
ICON = {"approved": "✅", "pending": "⏳", "rejected": "❌"}
SORTS = ("new", "old", "az")
UZ_SHIFT = timedelta(hours=5)


def _helpers():
    from .webapp import _err, _json

    return _err, _json


def _deny(msg):
    return web.HTTPForbidden(text=json.dumps({"error": msg}), content_type="application/json")


async def _admin(request):
    from .admin_roles import is_full_admin

    if not is_full_admin(request["tg_id"]):
        raise _deny("Bu bo'lim faqat adminlar uchun.")


# ---------------------------------------------------------------
#  Sof yordamchilar (bazasiz sinash mumkin)
# ---------------------------------------------------------------
def missing_fields(user) -> list:
    """Tasdiqlash uchun yetishmayotgan maydonlar (botdagi qoida)."""
    out = []
    if len((getattr(user, "full_name", None) or "").strip().split()) < 2:
        out.append("ism-familiya")
    if (getattr(user, "phone_number", None) or "").strip().lower() in INVALID:
        out.append("telefon")
    if (getattr(user, "group_name", None) or "").strip().lower() in INVALID:
        out.append("guruh")
    return out


def status_text(user) -> str:
    if missing_fields(user):
        return "⚠️ Profil to'liq emas"
    return STATUS.get(user.registration_status, str(user.registration_status or "—"))


def row_of(user) -> dict:
    complete = not missing_fields(user)
    icon = "⚠️" if not complete else ICON.get(user.registration_status, "❓")
    return {"tg": user.telegram_id, "name": user.full_name or "Nomsiz", "icon": icon,
            "group": user.group_name or "", "blocked": bool(user.is_blocked)}


def card_of(user, admin_target=False) -> dict:
    complete = not missing_fields(user)
    st = user.registration_status
    return {
        "tg": user.telegram_id, "name": user.full_name or "Nomsiz", "username": user.username or "",
        "phone": user.phone_number or "", "group": user.group_name or "",
        "status": status_text(user), "complete": complete, "blocked": bool(user.is_blocked),
        "is_admin": bool(admin_target),
        "tests": int(user.total_tests or 0), "score": int(user.total_score or 0),
        "can_approve": st != "approved" and complete,
        "can_reject": st != "rejected" and not admin_target,
        "can_block": not admin_target,
    }


def clean_sort(value) -> str:
    return value if value in SORTS else "new"


def clean_page(value) -> int:
    try:
        p = int(value)
    except (TypeError, ValueError):
        return 0
    return p if 0 <= p <= 1_000_000 else 0


def uz_day_start_utc(now_utc: datetime) -> datetime:
    """Toshkent (UTC+5) kunining boshlanishi — UTC vaqtida (bazada vaqt UTC saqlanadi)."""
    local = now_utc + UZ_SHIFT
    return local.replace(hour=0, minute=0, second=0, microsecond=0) - UZ_SHIFT


def pct(a, b) -> float:
    return pct_1(a, b)


# ---------------------------------------------------------------
#  Foydalanuvchilar
# ---------------------------------------------------------------
async def api_u_list(request):
    await _admin(request)
    from .handlers.admin.foydalanuvchilar import USERS_PAGE_SIZE, _users_filter

    q = (request.query.get("q") or "").strip()[:50]
    sort = clean_sort(request.query.get("sort"))
    page = clean_page(request.query.get("page"))
    cond = _users_filter(q)
    stmt, cnt = select(User), select(func.count(User.id))
    if cond is not None:
        stmt, cnt = stmt.where(cond), cnt.where(cond)
    if sort == "old":
        stmt = stmt.order_by(User.created_at.asc(), User.id.asc())
    elif sort == "az":
        stmt = stmt.order_by(func.lower(User.full_name).asc(), User.id.asc())
    else:
        stmt = stmt.order_by(User.created_at.desc(), User.id.desc())
    async with SessionLocal() as s:
        total = int(await s.scalar(cnt) or 0)
        last = max(0, (total - 1) // USERS_PAGE_SIZE)
        page = min(page, last)
        users = (await s.scalars(stmt.offset(page * USERS_PAGE_SIZE).limit(USERS_PAGE_SIZE))).all()
    return web.json_response({"total": total, "page": page, "pages": last + 1, "sort": sort, "q": q,
                              "items": [row_of(u) for u in users]})


async def _get_user(s, tg):
    return await s.scalar(select(User).where(User.telegram_id == tg))


async def api_u_user(request):
    _err, _ = _helpers()
    await _admin(request)
    from .admin_roles import is_full_admin

    tg = int(request.match_info["tg"])
    async with SessionLocal() as s:
        u = await _get_user(s, tg)
        if u is None:
            return _err("Foydalanuvchi topilmadi.", 404)
        return web.json_response(card_of(u, is_full_admin(tg)))


async def _notify(tg, text, with_menu=False):
    try:
        from aiogram import Bot
        from aiogram.client.default import DefaultBotProperties
        from aiogram.enums import ParseMode

        from .config import settings

        bot = Bot(token=settings.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
        try:
            kw = {}
            if with_menu:
                from .keyboards import main_menu

                kw["reply_markup"] = main_menu(False)
            await _tg_retry(lambda: bot.send_message(tg, text, **kw), tries=3)
            return True
        finally:
            try:
                await bot.session.close()
            except Exception:
                pass
    except Exception:
        log.warning("veb: foydalanuvchiga xabar yuborilmadi (%s)", tg)
        return False


async def _tg_of(request):
    _err, _json = _helpers()
    try:
        return int((await _json(request)).get("tg")), None
    except (TypeError, ValueError):
        return None, _err("Noto'g'ri so'rov.")


async def api_u_approve(request):
    _err, _ = _helpers()
    await _admin(request)
    tg, bad = await _tg_of(request)
    if bad:
        return bad
    async with SessionLocal() as s:
        u = await _get_user(s, tg)
        if u is None:
            return _err("Foydalanuvchi topilmadi.", 404)
        miss = missing_fields(u)
        if miss:
            return _err("Ma'lumotlar to'liq emas: " + ", ".join(miss))
        u.registration_status = "approved"
        name = u.full_name or "Foydalanuvchi"
        await s.commit()
    sent = await _notify(tg, "<b>Ro'yxatdan o'tishingiz tasdiqlandi!</b>\n\n"
                             f"Ism-familiya: <b>{html.escape(name)}</b>\n\n"
                             "Endi Doniyor Academy dan toliq foydalanishingiz mumkin.", with_menu=True)
    return web.json_response({"ok": True, "notified": sent})


async def api_u_reject(request):
    _err, _ = _helpers()
    await _admin(request)
    from .admin_roles import is_full_admin

    tg, bad = await _tg_of(request)
    if bad:
        return bad
    if is_full_admin(tg):
        return _err("Adminni rad etib bo'lmaydi.")
    async with SessionLocal() as s:
        u = await _get_user(s, tg)
        if u is None:
            return _err("Foydalanuvchi topilmadi.", 404)
        u.registration_status = "rejected"
        await s.commit()
    sent = await _notify(tg, "<b>Royxatdan otish arizangiz rad etildi.</b>\n\n"
                             "Qaytadan royxatdan otish uchun /start buyrugini yuboring.")
    return web.json_response({"ok": True, "notified": sent})


async def _set_block(request, value):
    _err, _ = _helpers()
    await _admin(request)
    from .admin_roles import is_full_admin

    tg, bad = await _tg_of(request)
    if bad:
        return bad
    if value and is_full_admin(tg):
        return _err("Adminni bloklab bo'lmaydi.")
    async with SessionLocal() as s:
        u = await _get_user(s, tg)
        if u is None:
            return _err("Foydalanuvchi topilmadi.", 404)
        if bool(u.is_blocked) == value:
            return _err("Bu foydalanuvchi allaqachon bloklangan." if value else "Bu foydalanuvchi bloklanmagan.")
        u.is_blocked = value
        await s.commit()
    return web.json_response({"ok": True})


async def api_u_block(request):
    return await _set_block(request, True)


async def api_u_unblock(request):
    return await _set_block(request, False)


# ---------------------------------------------------------------
#  Statistika (har bir raqam aniq ta'rifga ega)
# ---------------------------------------------------------------
SOURCE_TITLES = {"quiz": "Oddiy test", "group": "Guruh testi", "diagnostic": "Diagnostika", "exam": "Imtihon"}


def source_key(value) -> str:
    return value if value in SOURCE_TITLES else "quiz"


def merge_sources(rows) -> list:
    """rows: [(source, urinishlar, savollar, to'g'ri)] -> manba bo'yicha birlashtirilgan ro'yxat
    (source bo'sh bo'lsa «Oddiy test» hisoblanadi, bot hisobotlaridagi kabi)."""
    acc = {}
    for src, n, tot, cor in rows:
        k = source_key(src)
        a = acc.setdefault(k, [0, 0, 0])
        a[0] += int(n or 0)
        a[1] += int(tot or 0)
        a[2] += int(cor or 0)
    out = []
    for k in ("quiz", "group", "diagnostic", "exam"):
        if k in acc:
            n, tot, cor = acc[k]
            out.append({"key": k, "title": SOURCE_TITLES[k], "attempts": n, "questions": tot, "correct": cor, "pct": pct(cor, tot)})
    return out


def user_buckets(rows) -> dict:
    """rows: [(registration_status, is_blocked, full_name, phone_number, group_name)]"""
    out = {"total": 0, "approved": 0, "pending": 0, "rejected": 0, "blocked": 0, "incomplete": 0}
    for st, blocked, name, phone, group in rows:
        out["total"] += 1
        if st in ("approved", "pending", "rejected"):
            out[st] += 1
        if blocked:
            out["blocked"] += 1
        if missing_fields(Obj(full_name=name, phone_number=phone, group_name=group)):
            out["incomplete"] += 1
    return out


class Obj:
    def __init__(self, **k):
        self.__dict__.update(k)


async def api_u_stats(request):
    await _admin(request)
    now = datetime.utcnow()
    today = uz_day_start_utc(now)
    week = now - timedelta(days=7)
    async with SessionLocal() as s:
        urows = (await s.execute(select(User.registration_status, User.is_blocked, User.full_name,
                                        User.phone_number, User.group_name))).all()
        new_today = int(await s.scalar(select(func.count(User.id)).where(User.created_at >= today)) or 0)
        active7 = int(await s.scalar(select(func.count(func.distinct(TestAttempt.user_id))).where(
            TestAttempt.created_at >= week)) or 0)
        srows = (await s.execute(select(TestAttempt.source, func.count(TestAttempt.id),
                                        func.coalesce(func.sum(TestAttempt.total), 0),
                                        func.coalesce(func.sum(TestAttempt.correct), 0)).group_by(TestAttempt.source))).all()
        answers = int(await s.scalar(select(func.count(AnswerLog.id))) or 0)
        correct = int(await s.scalar(select(func.count(AnswerLog.id)).where(AnswerLog.is_correct == True)) or 0)  # noqa: E712
        top = (await s.execute(select(User.full_name, User.total_score).order_by(User.total_score.desc(), User.id.asc()).limit(1))).fetchone()
        tied = 0
        if top:
            tied = int(await s.scalar(select(func.count(User.id)).where(User.total_score == top[1])) or 0)
    sources = merge_sources(srows)
    return web.json_response({
        "users": user_buckets(urows), "new_today": new_today, "active7": active7,
        "attempts": sum(x["attempts"] for x in sources), "sources": sources,
        "answers": answers, "correct": correct, "wrong": answers - correct, "pct": pct(correct, answers),
        "top": {"name": top[0], "score": int(top[1] or 0), "tied": tied} if top else None,
    })


# ---------------------------------------------------------------
#  Aniqlikni tekshirish (faqat o'qiydi, hech narsani o'zgartirmaydi)
# ---------------------------------------------------------------
SAMPLE = 15


def compare_counters(users, agg) -> list:
    """users: [(id, telegram_id, ism, testlar, savollar, to'g'ri, ball)] — foydalanuvchi hisoblagichlari.
    agg: {user_id: (testlar, savollar, to'g'ri, ball)} — test tarixidan (TestAttempt) qayta hisoblangan.
    Qaytaradi: farq qiladiganlar ro'yxati."""
    out = []
    for uid, tg, name, t, q, c, sc in users:
        got = (int(t or 0), int(q or 0), int(c or 0), int(sc or 0))
        real = agg.get(uid, (0, 0, 0, 0))
        if got != real:
            out.append({"id": uid, "tg": tg, "name": name or "Nomsiz", "counter": list(got), "real": list(real),
                        "diff_q": got[1] - real[1], "diff_c": got[2] - real[2]})
    out.sort(key=lambda r: (-(abs(r["diff_q"]) + abs(r["diff_c"])), r["id"]))
    return out


async def api_u_audit(request):
    await _admin(request)
    return web.json_response(await audit_report())


async def audit_report() -> dict:
    """Aniqlik tekshiruvi: profil hisoblagichlari va test tarixi solishtiriladi (bot va veb-ilova uchun bitta)."""
    async with SessionLocal() as s:
        users = (await s.execute(select(User.id, User.telegram_id, User.full_name, User.total_tests,
                                        User.total_questions, User.correct_answers, User.total_score))).all()
        agg = {r[0]: (int(r[1]), int(r[2]), int(r[3]), int(r[4])) for r in await s.execute(
            select(TestAttempt.user_id, func.count(TestAttempt.id), func.coalesce(func.sum(TestAttempt.total), 0),
                   func.coalesce(func.sum(TestAttempt.correct), 0), func.coalesce(func.sum(TestAttempt.score), 0))
            .group_by(TestAttempt.user_id))}
        bad_attempts = int(await s.scalar(select(func.count(TestAttempt.id)).where(
            (TestAttempt.correct > TestAttempt.total) | (TestAttempt.correct < 0) | (TestAttempt.total <= 0))) or 0)
        logs = (
            select(AnswerLog.attempt_id.label("aid"), func.count(AnswerLog.id).label("n"),
                   func.sum(case((AnswerLog.is_correct.is_(True), 1), else_=0)).label("ok"))
            .group_by(AnswerLog.attempt_id).subquery()
        )
        log_mismatch = int(await s.scalar(select(func.count(TestAttempt.id)).join(logs, logs.c.aid == TestAttempt.id).where(
            logs.c.ok != TestAttempt.correct)) or 0)
        log_overflow = int(await s.scalar(select(func.count(TestAttempt.id)).join(logs, logs.c.aid == TestAttempt.id).where(
            logs.c.n > TestAttempt.total)) or 0)
        log_samples = []
        if log_mismatch:
            try:
                from .services.student_report import fmt_datetime as _fmt
            except Exception:  # noqa: BLE001
                _fmt = lambda d: d.strftime("%d.%m.%Y %H:%M")
            lrows = (await s.execute(
                select(TestAttempt.id, TestAttempt.user_id, TestAttempt.total, TestAttempt.correct, TestAttempt.source,
                       TestAttempt.created_at, logs.c.n, logs.c.ok)
                .join(logs, logs.c.aid == TestAttempt.id).where(logs.c.ok != TestAttempt.correct)
                .order_by(TestAttempt.id.desc()).limit(SAMPLE))).all()
            who = {u[0]: (u[1], u[2]) for u in users}
            log_samples = [{"aid": r[0], "tg": who.get(r[1], (None, None))[0], "name": who.get(r[1], (None, None))[1] or "Nomsiz",
                            "total": int(r[2] or 0), "correct": int(r[3] or 0), "source": r[4] or "quiz",
                            "when": _fmt(r[5]) if r[5] else "", "log_n": int(r[6] or 0), "log_ok": int(r[7] or 0)} for r in lrows]
    diffs = compare_counters(users, agg)
    return {
        "users": len(users), "mismatch": len(diffs), "samples": diffs[:SAMPLE],
        "bad_attempts": bad_attempts, "log_mismatch": log_mismatch, "log_overflow": log_overflow, "log_samples": log_samples,
        "ok": not diffs and not bad_attempts and not log_mismatch and not log_overflow,
    }


def setup(app: web.Application) -> None:
    r = app.router
    r.add_get("/api/u/list", api_u_list)
    r.add_get("/api/u/user/{tg:\\d+}", api_u_user)
    r.add_post("/api/u/approve", api_u_approve)
    r.add_post("/api/u/reject", api_u_reject)
    r.add_post("/api/u/block", api_u_block)
    r.add_post("/api/u/unblock", api_u_unblock)
    r.add_get("/api/u/stats", api_u_stats)
    r.add_get("/api/u/audit", api_u_audit)

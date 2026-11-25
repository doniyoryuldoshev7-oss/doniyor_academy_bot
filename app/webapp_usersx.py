"""Veb-ilova: Foydalanuvchilar bo'limining qolgan qismi (botdagi bilan bir xil qoidalar):
Profili to'liq emas (+ eslatma yuborish), Botni bloklaganlar (tekshirish, fayl), Tanlab o'chirish
(avval baza zaxirasi, zaxira yuborilmasa hech kim o'chmaydi), Cheklangan admin (faqat superadmin).
Faqat adminlar. Botdagi funksiyalar qayta ishlatiladi, shuning uchun natijalar bir xil."""
from __future__ import annotations

import asyncio
import csv
import io
import json
import logging

from aiohttp import web
from sqlalchemy import select

from .db import SessionLocal

log = logging.getLogger(__name__)

PAGE = 10
FILTERS = {"all": "Hammasi", "inc": "To'liq emas", "idle": "Testsiz", "blk": "Bloklagan"}
MAX_SELECT = 2000
_DEL_LOCK = asyncio.Lock()
_SCAN = {"running": False, "result": None, "error": None}
_TASKS: set = set()


def _helpers():
    from .webapp import _err, _json

    return _err, _json


async def _admin(request):
    from .admin_roles import is_full_admin

    if not is_full_admin(request["tg_id"]):
        raise web.HTTPForbidden(
            text=json.dumps({"error": "Bu bo'lim faqat adminlar uchun."}), content_type="application/json"
        )


# ---------------- sof yordamchilar (bazasiz sinash mumkin) ----------------
def clean_filter(v) -> str:
    return v if v in FILTERS else "all"


def clean_page(v, pages) -> int:
    try:
        p = int(v)
    except (TypeError, ValueError):
        p = 0
    return max(0, min(p, max(0, pages - 1)))


def clean_tgs(raw) -> list:
    """Belgilangan Telegram ID lar: faqat musbat butun sonlar, takrorsiz, MAX_SELECT gacha."""
    if not isinstance(raw, list):
        return []
    out, seen = [], set()
    for x in raw:
        if isinstance(x, bool):
            continue
        try:
            v = int(x)
        except (TypeError, ValueError):
            continue
        if v > 0 and v not in seen:
            seen.add(v)
            out.append(v)
    return out[:MAX_SELECT]


def match_query(user, q: str) -> bool:
    q = (q or "").strip().casefold()
    if not q:
        return True
    hay = " ".join([user.full_name or "", user.group_name or "", user.username or "", user.phone_number or "",
                    str(user.telegram_id)]).casefold()
    return q in hay


def user_row(u) -> dict:
    return {"tg": u.telegram_id, "name": u.full_name or "Nomsiz", "group": u.group_name or "",
            "phone": u.phone_number or "", "username": u.username or "", "tests": int(u.total_tests or 0)}


def csv_bytes(users) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Ism-familiya", "Telefon", "Guruh", "Username", "Telegram ID", "Ro'yxatdan o'tgan"])
    for u in users:
        w.writerow([u.full_name or "", u.phone_number or "", u.group_name or "",
                    ("@" + u.username) if u.username else "", u.telegram_id,
                    u.created_at.strftime("%d.%m.%Y") if u.created_at else ""])
    return buf.getvalue().encode("utf-8-sig")  # Excel o'zbekcha harflarni to'g'ri ochadi


class _Bot:
    """Vaqtinchalik Bot (sessiya har doim yopiladi)."""

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


# ---------------- Profili to'liq emas ----------------
async def api_ux_incomplete(request):
    await _admin(request)
    from .handlers.admin.oquvchi_boshqaruv import _excluded_incomplete
    from .services.profil_eslatma import (
        _enabled, blocked_user_ids, incomplete_users, missing_fields, reminder_days, reminder_hour, reminder_max)

    users = await incomplete_users()
    blocked = len(await blocked_user_ids())
    excluded = await _excluded_incomplete({u.id for u in users})
    return web.json_response({
        "total": len(users),
        "items": [dict(user_row(u), missing=missing_fields(u)) for u in users[:50]],
        "excluded_total": len(excluded),
        "excluded": [dict(user_row(u), reason=r) for u, r in excluded[:20]],
        "blocked": blocked,
        "auto": {"on": bool(_enabled()), "days": reminder_days(), "hour": reminder_hour(), "max": reminder_max()},
    })


async def api_ux_inc_send(request):
    await _admin(request)
    from .services.profil_eslatma import incomplete_users, send_reminders

    _err, _ = _helpers()
    if not await incomplete_users():
        return _err("Profili to'liq bo'lmagan o'quvchi yo'q.")
    async with _Bot() as bot:
        res = await send_reminders(bot, force=True)
    return web.json_response(res)


# ---------------- Botni bloklaganlar ----------------
async def _blocked_all():
    from .handlers.admin.oquvchi_boshqaruv import _candidates

    return await _candidates("blk")


async def api_ux_blocked(request):
    await _admin(request)
    users = await _blocked_all()
    pages = max(1, (len(users) + PAGE - 1) // PAGE)
    page = clean_page(request.query.get("page"), pages)
    return web.json_response({"total": len(users), "page": page, "pages": pages,
                              "items": [user_row(u) for u in users[page * PAGE:(page + 1) * PAGE]],
                              "scan": {"running": _SCAN["running"]}})


async def _scan_job():
    from .services.profil_eslatma import scan_blocked

    try:
        async with _Bot() as bot:
            _SCAN["result"] = await scan_blocked(bot)
    except Exception as e:  # noqa: BLE001
        log.exception("veb: bloklaganlarni tekshirish xatosi")
        _SCAN["error"] = type(e).__name__
    finally:
        _SCAN["running"] = False


async def api_ux_scan_start(request):
    await _admin(request)
    from .services.profil_eslatma import scan_in_progress

    if _SCAN["running"] or scan_in_progress():
        return web.json_response({"running": True, "already": True})
    _SCAN.update(running=True, result=None, error=None)
    t = asyncio.create_task(_scan_job())
    _TASKS.add(t)
    t.add_done_callback(_TASKS.discard)
    return web.json_response({"running": True, "already": False})


async def api_ux_scan_status(request):
    await _admin(request)
    from .services.profil_eslatma import scan_in_progress

    return web.json_response({"running": bool(_SCAN["running"] or scan_in_progress()),
                              "result": _SCAN["result"], "error": _SCAN["error"]})


async def api_ux_blocked_csv(request):
    await _admin(request)
    from aiogram.types import BufferedInputFile

    _err, _ = _helpers()
    users = await _blocked_all()
    if not users:
        return _err("Ro'yxat bo'sh.")
    async with _Bot() as bot:
        await bot.send_document(request["tg_id"], BufferedInputFile(csv_bytes(users), filename="botni_bloklaganlar.csv"),
                                caption=f"🚫 Botni bloklaganlar: {len(users)} ta")
    return web.json_response({"sent": True, "count": len(users)})


# ---------------- Tanlab o'chirish ----------------
async def _candidates(flt, q):
    from .handlers.admin.oquvchi_boshqaruv import _candidates as cand

    return [u for u in await cand(flt) if match_query(u, q)]


async def api_ux_del_list(request):
    await _admin(request)
    flt = clean_filter(request.query.get("filter"))
    q = request.query.get("q", "")
    users = await _candidates(flt, q)
    pages = max(1, (len(users) + PAGE - 1) // PAGE)
    page = clean_page(request.query.get("page"), pages)
    return web.json_response({"filter": flt, "found": len(users), "page": page, "pages": pages,
                              "items": [user_row(u) for u in users[page * PAGE:(page + 1) * PAGE]]})


async def _load_selected(tgs):
    from .config import settings

    found = []
    async with SessionLocal() as s:
        for i in range(0, len(tgs), 400):
            found.extend((await s.scalars(select_user_in(tgs[i:i + 400]))).all())
    return [u for u in found if u.telegram_id not in settings.admins]


def select_user_in(part):
    from .models import User

    return select(User).where(User.telegram_id.in_(part))


async def api_ux_del_preview(request):
    await _admin(request)
    _err, rj = _helpers()
    tgs = clean_tgs((await rj(request)).get("tgs"))
    if not tgs:
        return _err("Avval o'chiriladigan o'quvchilarni belgilang.")
    users = await _load_selected(tgs)
    if not users:
        return _err("Belgilangan o'quvchilar topilmadi.")
    return web.json_response({"count": len(users), "names": [user_row(u) for u in users[:25]]})


async def api_ux_del_do(request):
    await _admin(request)
    _err, rj = _helpers()
    tgs = clean_tgs((await rj(request)).get("tgs"))
    if not tgs:
        return _err("Avval o'chiriladigan o'quvchilarni belgilang.")
    if _DEL_LOCK.locked():
        return _err("Boshqa o'chirish ketyapti. Kuting.", 409)
    async with _DEL_LOCK:
        users = await _load_selected(tgs)
        if not users:
            return _err("Belgilangan o'quvchilar topilmadi.")
        from .handlers.admin.oquvchi_boshqaruv import _delete_users

        async with _Bot() as bot:
            # Xavfsizlik: zaxira yuborilmasa, hech narsa o'chirilmaydi
            try:
                from .services.baza_zaxira import send_backup

                backup = await send_backup(bot, targets=[request["tg_id"]])
                ok = bool(backup.get("sent_to"))
            except Exception:
                log.exception("veb: o'chirishdan oldingi zaxira xatosi")
                ok = False
            if not ok:
                return _err("Zaxira nusxa yuborilmadi, shuning uchun hech kim o'chirilmadi. Birozdan keyin qayta urinib ko'ring.", 502)
            try:
                count = await _delete_users(users)
            except Exception as e:  # noqa: BLE001
                log.exception("veb: o'quvchilarni o'chirishda xato")
                return _err("O'chirishda xato yuz berdi, ma'lumotlar o'zgarmagan bo'lishi kerak. (" + type(e).__name__ + ")", 500)
            try:
                from datetime import datetime

                from .services.admin_ogoh import notify_admins
                from .textfix import now_uz

                listing = ", ".join((u.full_name or "Nomsiz")[:25] for u in users[:10])
                if len(users) > 10:
                    listing += f" va yana {len(users) - 10} ta"
                import html

                await notify_admins(bot, "🗑 <b>O'quvchilar o'chirildi (veb-ilova)</b>\n"
                                    f"👤 {request['tg_id']}\n🔢 {count} ta: {html.escape(listing)}\n"
                                    f"🕒 {now_uz().strftime('%d.%m.%Y %H:%M')}",
                                    f"udel:{datetime.now().timestamp()}", cooldown=0)
            except Exception:
                pass
    return web.json_response({"deleted": count, "skipped": len(tgs) - count})


# ---------------- Cheklangan admin ----------------
async def api_ux_role_info(request):
    await _admin(request)
    from .admin_roles import is_full_admin, is_superadmin
    from .handlers.start import registration_complete
    from .models import QuestionEditorGrant, User

    tg = int(request.match_info["tg"])
    async with SessionLocal() as s:
        u = await s.scalar(select(User).where(User.telegram_id == tg))
        if u is None:
            return web.json_response({"editor": False, "can_grant": False, "can_revoke": False, "is_super": False})
        editor = (await s.scalar(select(QuestionEditorGrant.id).where(QuestionEditorGrant.user_id == u.id))) is not None
    sup = is_superadmin(request["tg_id"])
    can_grant = bool(sup and not editor and not is_full_admin(tg) and u.registration_status == "approved"
                     and not u.is_blocked and registration_complete(u))
    return web.json_response({"editor": editor, "is_super": sup, "can_grant": can_grant,
                              "can_revoke": bool(sup and editor)})


async def api_ux_role_set(request):
    await _admin(request)
    from .admin_roles import is_superadmin
    from .role_management import RoleOperationRejected, RolePermissionDenied, set_question_editor_role

    _err, rj = _helpers()
    body = await rj(request)
    if not is_superadmin(request["tg_id"]):
        return _err("Bu amalni faqat bosh admin bajara oladi.", 403)
    try:
        tg = int(body.get("tg"))
    except (TypeError, ValueError):
        return _err("Telegram ID noto'g'ri.")
    enable = bool(body.get("enable"))
    try:
        await set_question_editor_role(actor_telegram_id=request["tg_id"], target_telegram_id=tg, enable=enable)
    except (RolePermissionDenied, RoleOperationRejected):
        return _err("Amal rad etildi. Foydalanuvchi holatini tekshiring.", 409)
    return web.json_response({"ok": True, "editor": enable})


def setup(app: web.Application) -> None:
    r = app.router
    r.add_get("/api/ux/incomplete", api_ux_incomplete)
    r.add_post("/api/ux/inc_send", api_ux_inc_send)
    r.add_get("/api/ux/blocked", api_ux_blocked)
    r.add_post("/api/ux/scan", api_ux_scan_start)
    r.add_get("/api/ux/scan", api_ux_scan_status)
    r.add_post("/api/ux/blocked_csv", api_ux_blocked_csv)
    r.add_get("/api/ux/del/list", api_ux_del_list)
    r.add_post("/api/ux/del/preview", api_ux_del_preview)
    r.add_post("/api/ux/del/do", api_ux_del_do)
    r.add_get("/api/ux/role/{tg:\\d+}", api_ux_role_info)
    r.add_post("/api/ux/role", api_ux_role_set)

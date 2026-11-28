"""Veb-ilova: Guruhlar va ro'yxatdan o'tish arizalari (botdagi /guruhlar va ariza tugmalari bilan bir xil).

Hamma mantiq app/handlers/royxat.py dagi funksiyalardan olinadi, shuning uchun bot va veb-ilova bir xil ishlaydi.
Faqat to'liq adminlar uchun.
"""
from __future__ import annotations

import html
import logging

from aiohttp import web
from sqlalchemy import or_, select, text

from .config import settings
from .db import SessionLocal, engine
from .models import User

log = logging.getLogger(__name__)


def _r():
    from .handlers import royxat

    return royxat


def _err(msg, status=400):
    return web.json_response({"error": msg}, status=status)


async def _body(request) -> dict:
    try:
        d = await request.json()
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


async def _admin(request):
    from .webapp_users import _admin as users_admin

    await users_admin(request)


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


async def api_home(request):
    await _admin(request)
    r = _r()
    groups = await r._groups()
    counts = dict(await r._user_groups())
    listed = {n for _g, n in groups}
    custom = [{"name": n, "count": c} for n, c in counts.items() if n and n not in listed]
    nogroup = sum(c for n, c in counts.items() if n not in listed)
    goals = [{"goal": g, "count": n} for g, n in await r.goal_counts()]
    return web.json_response({
        "groups": [{"id": g, "name": n, "count": counts.get(n, 0)} for g, n in groups],
        "custom": custom[:20], "nogroup": nogroup, "goals": goals,
    })


async def api_add(request):
    await _admin(request)
    r = _r()
    names = r._split_names(str((await _body(request)).get("names") or ""))
    if not names:
        return _err("Guruh nomini yozing (40 belgigacha).")
    added, existed, moved = await r._add_groups(names)
    return web.json_response({"added": added, "existed": existed, "moved": moved})


async def api_rename(request):
    await _admin(request)
    r = _r()
    d = await _body(request)
    gid, new = _int(d.get("id")), " ".join(str(d.get("name") or "").split())
    if not gid or not new or len(new) > 40:
        return _err("Yangi nomni to'g'ri yozing (40 belgigacha).")
    old = await r._group_by_id(gid)
    if not old:
        return _err("Guruh topilmadi.", 404)
    if any(r._gkey(n) == r._gkey(new) and g != gid for g, n in await r._groups()):
        return _err("Bunday nomli guruh allaqachon bor.")
    async with engine.begin() as conn:
        await conn.execute(text("UPDATE reg_groups SET name = :n WHERE id = :i"), {"n": new, "i": gid})
        res = await conn.execute(text("UPDATE users SET group_name = :n WHERE group_name = :o"), {"n": new, "o": old})
    return web.json_response({"ok": True, "moved": res.rowcount or 0})


async def api_delete(request):
    await _admin(request)
    gid = _int((await _body(request)).get("id"))
    if not gid:
        return _err("Noto'g'ri so'rov.")
    await _r().ensure_schema()
    async with engine.begin() as conn:
        await conn.execute(text("DELETE FROM reg_groups WHERE id = :i"), {"i": gid})
    return web.json_response({"ok": True})


async def api_group(request):
    await _admin(request)
    r = _r()
    gid = int(request.match_info["id"])
    name = await r._group_by_id(gid)
    if not name:
        return _err("Guruh topilmadi.", 404)
    users = await r._group_users(name)
    return web.json_response({"id": gid, "name": name, "users": [
        {"tg": u.telegram_id, "name": u.full_name or "Nomsiz", "status": u.registration_status} for u in users]})


async def api_cands(request):
    await _admin(request)
    r = _r()
    gid = int(request.match_info["id"])
    name = await r._group_by_id(gid)
    if not name:
        return _err("Guruh topilmadi.", 404)
    listed = {n for _g, n in await r._groups()}
    users = await r._candidates(name)
    return web.json_response({"id": gid, "name": name, "items": [
        {"tg": u.telegram_id, "name": u.full_name or "Nomsiz", "group": u.group_name or "",
         "listed": (u.group_name or "") in listed} for u in users]})


async def api_assign(request):
    await _admin(request)
    r = _r()
    d = await _body(request)
    gid = _int(d.get("id"))
    tgs = [t for t in (_int(x) for x in (d.get("tgs") or [])) if t][:500]
    name = await r._group_by_id(gid) if gid else None
    if not name:
        return _err("Guruh topilmadi.", 404)
    if not tgs:
        return _err("Hech kim belgilanmadi.")
    async with SessionLocal() as s:
        users = (await s.scalars(select(User).where(User.telegram_id.in_(tgs)))).all()
        for u in users:
            u.group_name = name
        await s.commit()
    return web.json_response({"ok": True, "count": len(users), "name": name})


async def api_info(request):
    """Foydalanuvchi kartasi uchun: maqsad, bot sezgan shubhalar, guruhlar ro'yxati."""
    await _admin(request)
    r = _r()
    tg = int(request.match_info["tg"])
    async with SessionLocal() as s:
        u = await s.scalar(select(User).where(User.telegram_id == tg))
    if u is None:
        return _err("Foydalanuvchi topilmadi.", 404)
    parts = (u.full_name or "").split()
    first, last = (parts[0], " ".join(parts[1:])) if len(parts) > 1 else ((parts[0] if parts else ""), "")
    groups = await r._groups()
    listed = {n for _g, n in groups}
    warn = r._warnings(first, last, u.group_name or "", (u.group_name or "") not in listed, "") if first else []
    return web.json_response({
        "goal": await r._get_exam_goal(tg) or "",
        "warnings": warn if u.registration_status != "approved" else [],
        "groups": [{"id": g, "name": n} for g, n in groups],
        "templates": [{"code": k, "label": r.REJECT_BTN[k], "text": v} for k, v in r.REJECT.items()],
        "pending": u.registration_status == "pending",
    })


async def api_setgroup(request):
    await _admin(request)
    r = _r()
    d = await _body(request)
    tg, gid = _int(d.get("tg")), _int(d.get("id"))
    name = await r._group_by_id(gid) if gid else None
    if not tg or not name:
        return _err("Guruh topilmadi.", 404)
    async with SessionLocal() as s:
        u = await s.scalar(select(User).where(User.telegram_id == tg))
        if u is None:
            return _err("Foydalanuvchi topilmadi.", 404)
        u.group_name = name
        await s.commit()
    return web.json_response({"ok": True, "name": name})


async def api_reject(request):
    """Shablon (code) yoki o'z so'zi (text) bilan rad etish: botdagi bilan bir xil xabar boradi."""
    await _admin(request)
    from .admin_roles import is_full_admin

    r = _r()
    d = await _body(request)
    tg = _int(d.get("tg"))
    if not tg:
        return _err("Noto'g'ri so'rov.")
    if is_full_admin(tg):
        return _err("Adminni rad etib bo'lmaydi.")
    code, own = str(d.get("code") or ""), " ".join(str(d.get("text") or "").split())
    if code in r.REJECT:
        reason = r.REJECT[code]
    elif own:
        reason = "Ustozingiz izohi: «" + html.escape(own[:500]) + "»"
    else:
        return _err("Sababni tanlang yoki yozing.")
    from aiogram import Bot
    from aiogram.client.default import DefaultBotProperties
    from aiogram.enums import ParseMode

    bot = Bot(token=settings.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    try:
        ok, msg = await r._reject(bot, tg, reason, force=True)
    finally:
        try:
            await bot.session.close()
        except Exception:
            pass
    if not ok:
        return _err(msg)
    return web.json_response({"ok": True, "message": msg})


def setup(app: web.Application) -> None:
    rt = app.router
    rt.add_get("/api/g/home", api_home)
    rt.add_post("/api/g/add", api_add)
    rt.add_post("/api/g/rename", api_rename)
    rt.add_post("/api/g/delete", api_delete)
    rt.add_get("/api/g/group/{id:\\d+}", api_group)
    rt.add_get("/api/g/cands/{id:\\d+}", api_cands)
    rt.add_post("/api/g/assign", api_assign)
    rt.add_get("/api/g/info/{tg:\\d+}", api_info)
    rt.add_post("/api/g/setgroup", api_setgroup)
    rt.add_post("/api/g/reject", api_reject)

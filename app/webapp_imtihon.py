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
    fixed = body.get("fixed") is not None
    if fixed:
        mode = "milliy"          # fayldan yig'ilgan imtihon faqat Milliy sertifikat
    if mode not in ("milliy", "oddiy"):
        return None, "Shablon noto'g'ri."
    try:
        sids = [] if fixed else [int(x) for x in (body.get("sids") or [])]
        dur = int(body.get("dur"))
        days = int(body.get("days"))
        tid = int(body["tid"]) if body.get("tid") else None
        c = MILLIY_C if mode == "milliy" else int(body.get("c"))
        o = MILLIY_O if mode == "milliy" else int(body.get("o"))
    except (TypeError, ValueError):
        return None, "Noto'g'ri so'rov."
    if not fixed and (not sids or len(set(sids)) != len(sids)):
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
    return {"mode": mode, "sids": sids, "tid": None if fixed else tid, "c": c, "o": o, "dur": dur, "days": days,
            "group": group, "title": title, "fixed": fixed}, None


MAX_CHUNKS = 500


def clean_chunks(raw, max_text):
    """Mijozdan kelgan qismlar ro'yxatini tekshiradi. Qaytaradi: (ro'yxat, xato)."""
    if not isinstance(raw, list) or len(raw) > MAX_CHUNKS or not all(isinstance(x, str) for x in raw):
        return None, "Qismlar noto'g'ri."
    return [x for x in raw if x.strip()], None


def check_stage(stage, chunks, fns):
    """Botdagi «✅ Tayyor — tekshirish» bilan BIR XIL tekshiruv. fns = (_join, _check_closed, build_open, split_all, parse_text, MAX_TEXT).
    Qaytaradi: (yopiq elementlar, ochiq elementlar, xatolar, ogohlantirishlar)."""
    join, check_closed, build_open, split_all, parse_text, max_text = fns
    buf = ""
    for ch in chunks:
        buf = join(buf, ch, stage)
    if not buf.strip():
        return [], [], ["Hali hech narsa yuborilmadi."], []
    if len(buf) > max_text:
        return [], [], ["Juda ko'p matn."], []
    if stage == "c":
        items, errors = check_closed(buf)
        return items, [], list(errors), []
    if stage == "o":
        items, errors, warnings = build_open(parse_text(buf))
        return [], items, list(errors), list(warnings)
    sp = split_all(buf)        # split_all_ex: oddiy (1–45) va 55 talik test fayli
    if sp is None:
        return [], [], ["Yopiq va ochiq qismni ajratib bo'lmadi. Ochiq savollardan oldin alohida qatorga «OCHIQ SAVOLLAR» deb yozing."], []
    extra_err, extra_warn = (sp[2], sp[3]) if len(sp) > 2 else ([], [])
    citems, cerr = check_closed(sp[0])
    oitems, oerr, warnings = build_open(parse_text(sp[1])) if sp[1].strip() else ([], ["Ochiq savollar topilmadi."], [])
    errors = ["Yopiq qism: " + e for e in cerr] + ["Ochiq qism: " + e for e in list(extra_err) + list(oerr)]
    return citems, oitems, errors, list(extra_warn) + list(warnings)


def _stage_fns():
    # build_open_text — ochiq savollarni izoh/manba bilan birga yig'adi (botdagi bilan bir xil); u matnni o'zi tahlil qiladi
    from .handlers.milliy_yigish import _check_closed, _join, build_open_text, open_text_as_is, split_all_ex
    from .services.savol_import import MAX_TEXT

    return _join, _check_closed, build_open_text, split_all_ex, open_text_as_is, MAX_TEXT


def fixed_payloads(fixed, fns=None):
    """Tayyor imtihon: qismlardan savollar QAYTA yig'iladi (mijozga ishonilmaydi). Qaytadan: (payloadlar, xato)."""
    fns = fns or _stage_fns()
    max_text = fns[5]
    if not isinstance(fixed, dict):
        return None, "Tayyor imtihon ma'lumoti noto'g'ri."
    parts = {}
    for key in ("a", "c", "o"):
        if fixed.get(key) is not None:
            parts[key], bad = clean_chunks(fixed.get(key), max_text)
            if bad:
                return None, bad
    if parts.get("a"):
        c, o, errors, _w = check_stage("a", parts["a"], fns)
    elif parts.get("c") and parts.get("o"):
        c, _x, e1, _w = check_stage("c", parts["c"], fns)
        _y, o, e2, _w = check_stage("o", parts["o"], fns)
        errors = list(e1) + list(e2)
    else:
        return None, "Avval yopiq (1-35) va ochiq (36-45) savollarni yuklang."
    if errors:
        return None, "Savollarda xato bor: " + "; ".join(errors[:3])
    from .services.imtihon import milliy_open_ok

    if len(c) != MILLIY_C or not milliy_open_ok(o):
        return None, f"Yopiq {MILLIY_C} ta va ochiq {MILLIY_O} ta (yoki 20 ta bitta javobli) savol bo'lishi kerak (hozir {len(c)} + {len(o)})."
    return list(c) + list(o), None


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
            vid, vtitle = None, None
            fx = body.get("fixed")
            if d["fixed"] and isinstance(fx, dict) and fx.get("v") is not None:
                payloads, vtitle, bad = await variant_payloads_for(s, fx.get("v"))
                if bad:
                    return _err(bad)
                vid = int(fx["v"])
                sname = "Milliy sertifikat"
            elif d["fixed"]:
                try:
                    payloads, bad = fixed_payloads(body.get("fixed"))
                except Exception:
                    log.exception("veb: tayyor imtihonni yig'ishda xato")
                    return _err("Savollarni tekshirib bo'lmadi. Qaytadan tekshiring.")
                if bad:
                    return _err(bad)
                sname = "Milliy sertifikat"
            else:
                subs = [await s.get(Subject, i) for i in d["sids"]]
                if any(x is None for x in subs):
                    return _err("Fan topilmadi.", 404)
                sname = " + ".join(x.name for x in subs)[:200]
            if not d["title"] and vtitle:
                from .services.milliy_variant import default_exam_title

                title = default_exam_title(vtitle)
            else:
                title = d["title"] or f"{sname} — {to_local(datetime.utcnow()).strftime('%d.%m.%Y')}"[:200]
            if d["fixed"]:
                real_c = sum(1 for p in payloads if p.get("k") == "c")
                real_o = len(payloads) - real_c
            elif len(d["sids"]) > 1:
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
    if vid:
        from .services.milliy_variant import mark_used

        await mark_used(vid)
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


# ---------------- Milliy sertifikatni fayldan yig'ish ----------------
async def api_mly_check(request):
    await _manager(request)
    _err, rj = _helpers()
    body = await rj(request)
    stage = body.get("stage")
    if stage not in ("c", "o", "a"):
        return _err("Bosqich noto'g'ri.")
    try:
        fns = _stage_fns()
        chunks, bad = clean_chunks(body.get("chunks"), fns[5])
        if bad:
            return _err(bad)
        c, o, errors, warnings = check_stage(stage, chunks, fns)
    except Exception:
        log.exception("veb: milliy tekshiruv xatosi")
        return _err("Savollarni tekshirib bo'lmadi. Matnni tekshirib qaytadan yuboring.")
    return web.json_response({"ok": not errors, "closed": len(c), "open": len(o), "errors": [str(e) for e in errors],
                              "warnings": [str(w) for w in warnings], "need_c": MILLIY_C, "need_o": MILLIY_O})


# ---------------- Milliy sertifikat: tayyor variantlar (zip) ----------------
async def variant_payloads_for(s, vid):
    """Variantdan imtihon savollari. Qaytaradi: (payloadlar, nomi, xato)."""
    from .models import MilliyVariant
    from .services.imtihon import milliy_open_ok
    from .services.milliy_variant import variant_payloads

    try:
        v = await s.get(MilliyVariant, int(vid))
    except (TypeError, ValueError):
        v = None
    if v is None:
        return None, None, "Variant topilmadi (o'chirilgan bo'lishi mumkin)."
    items = variant_payloads(v)
    closed = [p for p in items if p.get("k") == "c"]
    opened = [p for p in items if p.get("k") != "c"]
    if len(closed) != MILLIY_C or not milliy_open_ok(opened):
        return None, None, "Variant savollari to'liq emas. Faylni qayta yuklang."
    return closed + opened, v.title, None


def variant_row(v, uid, is_admin, fmt):
    from .services.milliy_variant import can_delete, variant_payloads

    items = variant_payloads(v)
    return {"id": v.id, "title": v.title, "closed": v.n_closed, "open": v.n_open, "used": v.used,
            "img": sum(1 for p in items if p.get("img")), "updated": fmt(v.updated_at or v.created_at),
            "can_del": can_delete(uid, v, is_admin)}


async def api_mlv_list(request):
    is_admin = await _manager(request)
    from .services.milliy_variant import list_variants
    from .services.student_report import fmt_datetime

    async with SessionLocal() as s:
        rows = await list_variants(s)
    uid = request["tg_id"]
    return web.json_response({"items": [variant_row(v, uid, is_admin, fmt_datetime) for v in rows], "admin": is_admin})


async def api_mlv_one(request):
    is_admin = await _manager(request)
    from .models import MilliyVariant
    from .services.milliy_variant import variant_payloads
    from .services.student_report import fmt_datetime

    _err, _ = _helpers()
    async with SessionLocal() as s:
        v = await s.get(MilliyVariant, int(request.match_info["id"]))
    if v is None:
        return _err("Variant topilmadi.", 404)
    row = variant_row(v, request["tg_id"], is_admin, fmt_datetime)
    first = next((p for p in variant_payloads(v) if p.get("k") == "c"), None)
    row["first"] = (first or {}).get("t") or ""
    from .services.milliy_variant import langs_line

    row["langs"] = langs_line(variant_payloads(v))
    return web.json_response(row)


async def api_mlv_upload(request):
    """Zip yoki bitta fayl (botdagi «📦 Zip yoki fayl yuklash» bilan bir xil servis)."""
    await _manager(request)
    from urllib.parse import unquote

    from .services import milliy_variant as MV

    _err, _ = _helpers()
    uid = request["tg_id"]
    key = (uid, "mlv")
    if key in _BUSY:
        return _err("Oldingi fayl hali tekshirilmoqda, kuting.", 409)
    _BUSY.add(key)
    try:
        data = await request.read()
        if len(data) > MV.MAX_ZIP:
            return _err("Fayl juda katta (60 MB dan oshmasin). 2 qismga bo'lib yuklang.")
        try:
            res = await asyncio.to_thread(MV.parse_upload, unquote(request.headers.get("X-Filename", "")), data)
        except ValueError as exc:
            return _err(str(exc))
        async with SessionLocal() as s:
            added, updated = await MV.save_variants(s, uid, res["items"])
    except Exception:
        log.exception("veb: milliy variant yuklash")
        return _err("Faylni o'qib bo'lmadi. Qaytadan urinib ko'ring.")
    finally:
        _BUSY.discard(key)
    items = [{"title": i["title"], "ok": i["ok"], "closed": i["closed"], "open": i["open"],
              "errors": i["errors"][:5], "warnings": i["warnings"][:3]} for i in res["items"]]
    return web.json_response({"ok": all(i["ok"] for i in items), "added": added, "updated": updated, "items": items,
                              "skipped": [{"file": a, "why": b} for a, b in res["skipped"]],
                              "lines": MV.report_lines(res, added, updated)})


async def api_mlv_delete(request):
    is_admin = await _manager(request)
    from .models import MilliyVariant
    from .services.milliy_variant import can_delete

    _err, _ = _helpers()
    async with SessionLocal() as s:
        v = await s.get(MilliyVariant, int(request.match_info["id"]))
        if v is None:
            return _err("Variant topilmadi.", 404)
        if not can_delete(request["tg_id"], v, is_admin):
            return _err("O'chirishga ruxsat yo'q.", 403)
        await s.delete(v)
        await s.commit()
    return web.json_response({"ok": True})


async def api_mlv_delete_all(request):
    is_admin = await _manager(request)
    from sqlalchemy import delete

    from .models import MilliyVariant

    _err, _ = _helpers()
    if not is_admin:
        return _err("Faqat admin.", 403)
    async with SessionLocal() as s:
        await s.execute(delete(MilliyVariant))
        await s.commit()
    return web.json_response({"ok": True})


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


# ---------------- yozma javoblarni ustoz tekshiradi ----------------
async def api_ex_openreview(request):
    """Yozma qismlar bo'yicha avtomatik qabul qilinmagan javoblar (bir xil yozilganlari birga)."""
    from .services.imtihon import attempt_answers, exam_results, open_answer_groups

    eid = int(request.match_info["id"])
    async with SessionLocal() as s:
        exam, err = await _load(request, s, eid)
        if err:
            return err
        items, results = await exam_results(s, exam)
        title = exam.title
    parts = open_answer_groups(items, [attempt_answers(r.attempt) for r in results])
    for p in parts:
        p["q"] = (p["q"] or "")[:300]
        p["wrong"] = p["wrong"][:60]
    return web.json_response({"title": title, "count": len(results), "parts": parts})


async def api_ex_accept(request):
    """Yozma qism uchun javobni shu imtihonda to'g'ri deb qabul qilish yoki bekor qilish.
    Savollar bazasi o'zgarmaydi; faqat shu imtihonning nusxasi. Ball darhol qayta hisoblanadi."""
    from .models import ExamItem
    from .services.imtihon import toggle_extra_variant

    _err, rj = _helpers()
    eid = int(request.match_info["id"])
    body = await rj(request)
    key = str(body.get("key") or "")
    text = body.get("text")
    accept = body.get("accept") is not False
    m = re.fullmatch(r"(\d{1,3})\.(\d)", key)
    if not m or not isinstance(text, str):
        return _err("Noto'g'ri so'rov.")
    pos, part = int(m.group(1)), int(m.group(2))
    async with SessionLocal() as s:
        exam, err = await _load(request, s, eid)
        if err:
            return err
        row = await s.scalar(select(ExamItem).where(ExamItem.exam_id == eid, ExamItem.position == pos).with_for_update())
        if row is None:
            return _err("Savol topilmadi.", 404)
        try:
            payload = json.loads(row.payload)
        except ValueError:
            return _err("Savol ma'lumoti buzilgan.")
        changed, note = toggle_extra_variant(payload, part, text, accept)
        if not changed:
            return _err(note)
        row.payload = json.dumps(payload, ensure_ascii=False)
        await s.commit()
    log.info("veb: imtihon %s, %s-qism: %s «%s» (%s)", eid, key, "qabul" if accept else "bekor", text[:60], request["tg_id"])
    return web.json_response({"ok": True, "note": note})


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
    from .services.savol_import import open_parts_json, parse_open_meta

    questions, errors = parse_open_meta(text)     # «Izoh:», «Manba:», «Qabul:» ham (botdagi bilan bir xil)
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
            s.add(OpenQuestion(topic_id=tid, stem=q["stem"], parts=open_parts_json(q), created_by=request["tg_id"]))
        await s.commit()
    return web.json_response({"count": len(questions), "errors": errs, "saved": True})


def setup(app: web.Application) -> None:
    r = app.router
    r.add_get("/api/ex/subjects", api_ex_subjects)
    r.add_get("/api/ex/topics/{id:\\d+}", api_ex_topics)
    r.add_post("/api/ex/avail", api_ex_avail)
    r.add_get("/api/ex/options", api_ex_options)
    r.add_get("/api/ex/groups", api_ex_groups)
    r.add_post("/api/mly/check", api_mly_check)
    r.add_get("/api/mlv/list", api_mlv_list)
    r.add_get("/api/mlv/{id:\\d+}", api_mlv_one)
    r.add_post("/api/mlv/upload", api_mlv_upload)
    r.add_post("/api/mlv/delete/{id:\\d+}", api_mlv_delete)
    r.add_post("/api/mlv/delete_all", api_mlv_delete_all)
    r.add_post("/api/ex/create", api_ex_create)
    r.add_get("/api/ex/send/{id:\\d+}", api_ex_sendstatus)
    r.add_get("/api/ex/list", api_ex_list)
    r.add_get("/api/ex/exam/{id:\\d+}", api_ex_exam)
    r.add_get("/api/ex/results/{id:\\d+}", api_ex_results)
    r.add_get("/api/ex/hard/{id:\\d+}", api_ex_hard)
    r.add_post("/api/ex/toggle/{id:\\d+}", api_ex_toggle)
    r.add_get("/api/ex/openreview/{id:\\d+}", api_ex_openreview)
    r.add_post("/api/ex/accept/{id:\\d+}", api_ex_accept)
    r.add_post("/api/ex/delete/{id:\\d+}", api_ex_delete)
    r.add_get("/api/ex/open_help", api_ex_open_help)
    r.add_post("/api/ex/open_add", api_ex_open_add)

"""Telegram veb-ilova (Mini App): server qismi.

Joylashuvi: app/webapp.py

Sahifa /app manzilida ochiladi (app/webui/index.html), ma'lumotlar
/api/... orqali olinadi. Kirish Telegram initData imzosi bilan
tekshiriladi, shuning uchun begona odam boshqaning nomidan kira olmaydi.
Ma'lumotlar bazasi bot bilan bir xil: imtihonni botda boshlab
veb-ilovada (yoki aksincha) davom ettirish mumkin.
"""

from __future__ import annotations

import calendar
import hashlib
import hmac
import json
import logging
import time
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qsl

from aiohttp import web
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from .config import settings
from .db import SessionLocal
from .models import Exam, ExamAttempt, User
from .services.imtihon import (
    MAX_ANSWER_LEN,
    MIN_STATS,
    SCALE_MAX,
    answers_to_view,
    attempt_answers,
    attempt_view,
    build_units,
    can_save_late,
    exam_is_open,
    exam_results,
    exam_targets_user,
    finish_attempt,
    load_items,
    score_attempt,
    unit_correct,
    view_key_to_canon,
)

log = logging.getLogger(__name__)

UI_DIR = Path(__file__).resolve().parent / "webui"
INIT_DATA_MAX_AGE = 24 * 3600


# ---------------------------------------------------------------
#  Telegram imzosini tekshirish
# ---------------------------------------------------------------
def verify_init_data(init_data: str, token: str, max_age: int = INIT_DATA_MAX_AGE, now: float | None = None):
    """initData to'g'ri va yangi bo'lsa — foydalanuvchi ma'lumotlari (dict), aks holda None."""
    if not init_data or not token:
        return None

    try:
        pairs = dict(parse_qsl(init_data, keep_blank_values=True, strict_parsing=False))
    except Exception:
        return None

    received = pairs.pop("hash", None)
    if not received:
        return None

    check = "\n".join(f"{k}={pairs[k]}" for k in sorted(pairs))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()

    if not hmac.compare_digest(expected, received):
        return None

    try:
        issued = int(pairs.get("auth_date", "0"))
    except ValueError:
        return None

    if (now if now is not None else time.time()) - issued > max_age:
        return None

    try:
        user = json.loads(pairs.get("user", ""))
    except ValueError:
        return None

    return user if isinstance(user, dict) and "id" in user else None


def _ms(value: datetime | None) -> int | None:
    return None if value is None else calendar.timegm(value.timetuple()) * 1000


def _err(message: str, status: int = 400):
    return web.json_response({"error": message}, status=status)


# ---------------------------------------------------------------
#  Kirish (middleware)
# ---------------------------------------------------------------
@web.middleware
async def auth_middleware(request: web.Request, handler):
    if not request.path.startswith("/api/"):
        return await handler(request)

    init_data = request.headers.get("X-Init-Data", "")
    tg = verify_init_data(init_data, settings.bot_token)

    if tg is None:
        return _err("Kirish tasdiqlanmadi. Ilovani bot ichidagi tugma orqali oching.", 401)

    async with SessionLocal() as session:
        user = await session.scalar(select(User).where(User.telegram_id == int(tg["id"])))

    if user is None or user.registration_status != "approved":
        return _err("Avval botda ro'yxatdan o'ting va tasdiqlanishni kuting.", 403)

    if user.is_blocked:
        return _err("Sizga kirish cheklangan.", 403)

    request["user_id"] = user.id
    request["tg_id"] = user.telegram_id
    return await handler(request)


async def _json(request: web.Request) -> dict:
    try:
        data = await request.json()
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


# ---------------------------------------------------------------
#  Yordamchilar
# ---------------------------------------------------------------
async def _my_attempt(session, request, attempt_id: int, lock: bool = False):
    # lock=True: javob yozilayotganda qator qulflanadi, shunda bir vaqtda kelgan
    # ikki so'rov (masalan, bot va veb-ilova) bir-birining javobini o'chirib yubormaydi.
    attempt = await session.get(ExamAttempt, attempt_id, with_for_update=lock)
    if attempt is None or attempt.user_id != request["user_id"]:
        return None, None
    exam = await session.get(Exam, attempt.exam_id)
    if exam is None or exam.is_removed:
        return None, None
    return attempt, exam


async def _settle(session, attempt) -> str:
    if attempt.status == "finished":
        return "finished"
    if datetime.utcnow() >= attempt.deadline_at:
        finish_attempt(attempt)
        await session.commit()
        return "finished"
    return "active"


def _client_items(items):
    out = []
    for it in items:
        if it.kind == "c":
            out.append({
                "pos": it.pos, "k": "c", "t": it.data.get("t", ""),
                "o": it.data["o"], "img": bool(it.data.get("img")),
            })
        else:
            out.append({
                "pos": it.pos, "k": "o", "s": it.data.get("s", ""),
                "p": [{"q": p["q"]} for p in it.data["p"]],
                "img": bool(it.data.get("img")),
            })
    return out


async def _localize(items, request):
    """Savollarni o'quvchi tiliga moslaydi (aralashtirishdan OLDIN: tarjimada harflar asl tartibda)."""
    from . import qtrans as _qt

    lang = _qt.lang_of(request)
    if lang not in _qt.LANGS:
        return items
    try:
        async with SessionLocal() as s:
            trs = await _qt.load(s, {(it.data or {}).get("qid") for it in items if it.kind == "c"}, lang)
        for it in items:
            tr = trs.get((it.data or {}).get("qid")) if it.kind == "c" else None
            if tr is not None:
                _qt._apply(it.data, tr)
            elif (it.data or {}).get("tr"):
                from .services.imtihon import apply_item_tr

                apply_item_tr(it.data, it.kind, lang)     # Milliy variant: tarjima savolning o'zida
    except Exception:  # noqa: BLE001 — tarjima o'qilmasa o'zbekcha qoladi
        pass
    return items


async def _view(request, exam, attempt, items):
    """O'quvchi ko'radigan tartib (aralashtirilgan bo'lishi mumkin): raqamlar va harflar shu urinishniki."""
    await _localize(items, request)
    return attempt_view(items, exam, attempt.id, getattr(attempt, "mix", None))


def _state(exam, attempt, items):
    """items — o'quvchi ko'rinishi (attempt_view)."""
    return {
        "attempt_id": attempt.id,
        "status": attempt.status,
        "exam": {"id": exam.id, "title": exam.title, "mode": exam.mode, "duration": exam.duration_min},
        "items": _client_items(items),
        "answers": {k: v for k, v in answers_to_view(attempt_answers(attempt), items).items() if not k.startswith("!")},
        "deadline_ms": _ms(attempt.deadline_at),
        "server_ms": int(time.time() * 1000),
    }


# ---------------------------------------------------------------
#  Sahifa
# ---------------------------------------------------------------
async def page(request: web.Request):
    return web.FileResponse(
        UI_DIR / "index.html",
        headers={"Cache-Control": "no-cache", "X-Content-Type-Options": "nosniff"},
    )


# ---------------------------------------------------------------
#  API
# ---------------------------------------------------------------
async def _panel_flag(tg_id):
    try:
        from .webapp_admin import has_panel

        return bool(await has_panel(tg_id))
    except Exception:
        return False


async def api_me(request: web.Request):
    async with SessionLocal() as session:
        user = await session.get(User, request["user_id"])
    return web.json_response({
        "name": user.full_name, "group": user.group_name,
        "tests": user.total_tests,
        "staff": await _is_staff(request["tg_id"]),
        "panel": await _panel_flag(request["tg_id"]),
        "lang": user.lang or None,
    })


async def api_exams(request: web.Request):
    async with SessionLocal() as session:
        user = await session.get(User, request["user_id"])
        exams = (
            await session.scalars(
                select(Exam).where(Exam.is_removed.is_(False)).order_by(Exam.id.desc()).limit(100)
            )
        ).all()
        mine = {
            a.exam_id: a
            for a in (
                await session.scalars(select(ExamAttempt).where(ExamAttempt.user_id == user.id))
            ).all()
        }
        out = []
        for exam in exams:
            if not exam_targets_user(exam, user):
                continue
            attempt = mine.get(exam.id)
            open_now = exam_is_open(exam)
            if attempt is None and not open_now:
                continue
            if attempt is not None and attempt.status == "active":
                await _settle(session, attempt)
            out.append({
                "id": exam.id, "title": exam.title, "subject": exam.subject_name,
                "mode": exam.mode, "duration": exam.duration_min,
                "closes_ms": _ms(exam.closes_at), "shuffle": bool(getattr(exam, "shuffle", False)),
                "state": "new" if attempt is None else ("active" if attempt.status == "active" else "done"),
                "attempt_id": attempt.id if attempt else None,
            })
            if len(out) >= 30:
                break
    return web.json_response({"exams": out})


async def api_exam_start(request: web.Request):
    exam_id = int(request.match_info["id"])

    async with SessionLocal() as session:
        user = await session.get(User, request["user_id"])
        exam = await session.get(Exam, exam_id)
        if exam is None or exam.is_removed or not exam_targets_user(exam, user):
            return _err("Imtihon topilmadi.", 404)

        attempt = await session.scalar(
            select(ExamAttempt).where(ExamAttempt.exam_id == exam_id, ExamAttempt.user_id == user.id)
        )
        items = await load_items(session, exam_id)

        if attempt is None:
            if not exam_is_open(exam):
                return _err("Bu imtihon yopilgan.", 409)
            if not items:
                return _err("Imtihonda savol yo'q.", 409)
            now = datetime.utcnow()
            attempt = ExamAttempt(
                exam_id=exam_id, user_id=user.id, started_at=now,
                deadline_at=now + timedelta(minutes=exam.duration_min),
                status="active", answers="{}", mix=bool(exam.shuffle),
            )
            session.add(attempt)
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                attempt = await session.scalar(
                    select(ExamAttempt).where(ExamAttempt.exam_id == exam_id, ExamAttempt.user_id == user.id)
                )

        await _settle(session, attempt)
        st = _state(exam, attempt, await _view(request, exam, attempt, items))
    return web.json_response(st)


async def api_attempt(request: web.Request):
    async with SessionLocal() as session:
        attempt, exam = await _my_attempt(session, request, int(request.match_info["id"]))
        if attempt is None:
            return _err("Imtihon topilmadi.", 404)
        await _settle(session, attempt)
        items = await load_items(session, exam.id)
        st = _state(exam, attempt, await _view(request, exam, attempt, items))
    return web.json_response(st)


async def api_answer(request: web.Request):
    body = await _json(request)
    key = str(body.get("key", ""))
    value = body.get("value", "")
    value = value if isinstance(value, str) else ""

    async with SessionLocal() as session:
        attempt, exam = await _my_attempt(session, request, int(request.match_info["id"]), lock=True)
        if attempt is None:
            return _err("Imtihon topilmadi.", 404)

        # Vaqt tugashidan sal oldin yuborilib, kechikib kelgan javob ham saqlanadi
        # (SAVE_GRACE_SEC); undan keyin urinish yakunlanadi.
        if not can_save_late(attempt):
            await _settle(session, attempt)
            return web.json_response({"status": "finished"})

        items = await load_items(session, exam.id)
        by_pos = {it.pos: it for it in items}
        # o'quvchi ko'rinishidagi raqam/harf -> imtihonning asl tartibi
        key, value = view_key_to_canon(key, value, attempt_view(items, exam, attempt.id, getattr(attempt, "mix", None)))
        if key is None:
            return _err("Noto'g'ri savol yoki variant.")
        head, _, tail = key.partition(".")

        if not head.isdigit() or int(head) not in by_pos:
            return _err("Noto'g'ri savol.")

        item = by_pos[int(head)]
        answers = attempt_answers(attempt)

        if item.kind == "c":
            if tail:
                return _err("Noto'g'ri kalit.")
            if value and value not in item.data["o"]:
                return _err("Noto'g'ri variant.")
        else:
            if not tail.isdigit() or int(tail) >= len(item.data["p"]):
                return _err("Noto'g'ri qism.")
            value = value.strip()
            if len(value) > MAX_ANSWER_LEN:
                return _err(f"Javob {MAX_ANSWER_LEN} belgidan oshmasin.")

        if value:
            answers[key] = value
        else:
            answers.pop(key, None)

        attempt.answers = json.dumps(answers, ensure_ascii=False)
        await session.commit()
        return web.json_response({"status": "active", "server_ms": int(time.time() * 1000)})


async def api_finish(request: web.Request):
    async with SessionLocal() as session:
        attempt, exam = await _my_attempt(session, request, int(request.match_info["id"]), lock=True)
        if attempt is None:
            return _err("Imtihon topilmadi.", 404)
        if attempt.status == "active":
            finish_attempt(attempt)
            await session.commit()
    return web.json_response({"status": "finished"})


async def api_result(request: web.Request):
    async with SessionLocal() as session:
        attempt, exam = await _my_attempt(session, request, int(request.match_info["id"]))
        if attempt is None:
            return _err("Natija topilmadi.", 404)
        if await _settle(session, attempt) != "finished":
            return _err("Imtihon hali tugamagan.", 409)

        _items, results = await exam_results(session, exam)

    mine = next((r for r in results if r.attempt.id == attempt.id), None)
    if mine is None:
        return _err("Natija topilmadi.", 404)

    sc = mine.score
    top = []  # Reyting: boshqa o'quvchilarning ismi va natijasi yashirin; o'rni rank/total da ko'rinadi

    return web.json_response({
        "title": exam.title, "mode": exam.mode,
        "c_ok": sc.c_ok, "c_total": sc.c_total, "o_ok": sc.o_ok, "o_total": sc.o_total,
        "value": sc.value, "level": sc.level, "scale": int(SCALE_MAX),
        "rank": results.index(mine) + 1, "total": len(results),
        "minutes": mine.minutes, "weighted": sc.weighted, "min_stats": MIN_STATS,
        "review": not exam_is_open(exam),
        "top": top,
    })


async def api_review(request: web.Request):
    async with SessionLocal() as session:
        attempt, exam = await _my_attempt(session, request, int(request.match_info["id"]))
        if attempt is None:
            return _err("Natija topilmadi.", 404)
        if await _settle(session, attempt) != "finished":
            return _err("Imtihon hali tugamagan.", 409)
        if exam_is_open(exam):
            return _err("Tahlil imtihon yopilgach ochiladi.", 403)
        items = await load_items(session, exam.id)

    # o'quvchi imtihonda qanday ko'rgan bo'lsa, tahlil ham shu tartibda
    items = await _view(request, exam, attempt, items)
    answers = answers_to_view(attempt_answers(attempt), items)
    out = []
    for it in items:
        if it.kind == "c":
            mine = answers.get(str(it.pos))
            out.append({
                "pos": it.pos, "k": "c", "t": it.data.get("t", ""), "o": it.data["o"],
                "correct": it.data["c"], "yours": mine, "ok": mine == it.data["c"],
                "e": it.data.get("e"), "m": it.data.get("m"), "img": bool(it.data.get("img")),
            })
        else:
            units = {u.part: u for u in build_units([it])}
            parts = []
            for i, p in enumerate(it.data["p"]):
                parts.append({
                    "q": p["q"], "yours": answers.get(f"{it.pos}.{i}", ""),
                    "ok": unit_correct(it, units[i], answers), "a": p["a"][:3],
                })
            out.append({"pos": it.pos, "k": "o", "s": it.data.get("s", ""), "p": parts,
                        "ok": all(x["ok"] for x in parts), "img": bool(it.data.get("img")),
                        "e": it.data.get("e"), "m": it.data.get("m")})
    return web.json_response({"items": out})


async def api_image(request: web.Request):
    pos = int(request.match_info["pos"])

    async with SessionLocal() as session:
        attempt, exam = await _my_attempt(session, request, int(request.match_info["id"]))
        if attempt is None:
            return _err("Topilmadi.", 404)
        items = await load_items(session, exam.id)
    await _localize(items, request)              # o'quvchi tilidagi rasm (bo'lsa)
    items = attempt_view(items, exam, attempt.id, getattr(attempt, "mix", None))

    item = next((i for i in items if i.pos == pos), None)   # pos — o'quvchi ko'rgan raqam
    path = item.data.get("img") if item else None

    if not path or not Path(path).is_file():
        return _err("Rasm topilmadi.", 404)

    return web.FileResponse(path, headers={"Cache-Control": "private, max-age=3600"})


# ---------------------------------------------------------------
#  Ulash
# ---------------------------------------------------------------
def setup_webapp(app: web.Application) -> None:
    app.middlewares.append(auth_middleware)
    app.router.add_get("/app", page)
    try:
        from .webapp_oquvchi import setup_oquvchi

        setup_oquvchi(app)
    except Exception:
        import logging as _lg

        _lg.getLogger(__name__).exception("veb-ilova: o'quvchi bo'limi ulanmadi")
    app.router.add_get("/api/me", api_me)
    app.router.add_get("/api/stats", api_stats)
    app.router.add_get("/api/exams", api_exams)
    app.router.add_post("/api/exams/{id:\\d+}/start", api_exam_start)
    app.router.add_get("/api/attempts/{id:\\d+}", api_attempt)
    app.router.add_post("/api/attempts/{id:\\d+}/answer", api_answer)
    app.router.add_post("/api/attempts/{id:\\d+}/finish", api_finish)
    app.router.add_get("/api/attempts/{id:\\d+}/result", api_result)
    app.router.add_get("/api/attempts/{id:\\d+}/review", api_review)
    app.router.add_get("/api/img/{id:\\d+}/{pos:\\d+}", api_image)
    app.router.add_get("/api/import/topics", api_import_topics)
    app.router.add_get("/api/import/template", api_import_template)
    app.router.add_post("/api/import/extract", api_import_extract)
    app.router.add_post("/api/import/preview", api_import_preview)
    app.router.add_post("/api/import/save", api_import_save)
    app.router.add_post("/api/import/undo/{id:\\d+}", api_import_undo)
    app.router.add_post("/api/import/redo/{id:\\d+}", api_import_redo)
    app.router.add_get("/api/import/history", api_import_history)
    app.router.add_post("/api/import/image", api_import_image)


# ---------------------------------------------------------------
#  Savol yuklash (ustoz / admin)
# ---------------------------------------------------------------
async def _staff(request: web.Request):
    from .services.savol_import import can_import

    if not await can_import(request["tg_id"]):
        raise web.HTTPForbidden(
            text=json.dumps({"error": "Bu bo'lim faqat ustoz va adminlar uchun."}),
            content_type="application/json",
        )


async def api_import_topics(request: web.Request):
    from .models import Subject, Topic

    await _staff(request)
    async with SessionLocal() as session:
        subjects = (await session.scalars(select(Subject).order_by(Subject.name))).all()
        topics = (await session.scalars(select(Topic).order_by(Topic.name))).all()
    return web.json_response({
        "subjects": [
            {"id": s.id, "name": s.name,
             "topics": [{"id": t.id, "name": t.name} for t in topics if t.subject_id == s.id]}
            for s in subjects
        ]
    })


async def api_import_template(request: web.Request):
    from .services.savol_import import TEMPLATE_TEXT

    await _staff(request)
    return web.json_response({"text": TEMPLATE_TEXT})


async def api_import_extract(request: web.Request):
    from .services.savol_import import MAX_FILE, extract_text

    await _staff(request)
    data = await request.read()
    if len(data) > MAX_FILE:
        return _err("Fayl juda katta (15 MB dan oshmasin).")
    name = request.headers.get("X-Filename", "")
    try:
        from urllib.parse import unquote

        text, notes = extract_text(unquote(name), data)
    except ValueError as exc:
        return _err(str(exc))
    return web.json_response({"text": text, "notes": notes})


async def _import_analyse(request: web.Request, body: dict):
    from .services.savol_import import can_import, parse_text, resolve_topics
    from .admin_roles import can_add_questions, is_full_admin

    tid = body.get("topic_id")
    tid = int(tid) if str(tid or "").isdigit() else None
    res = parse_text(str(body.get("text") or ""), auto_book=not tid)   # mavzu tanlanmagan bo'lsa — darslik fayldan aniqlanadi
    creator = is_full_admin(request["tg_id"]) or await can_add_questions(request["tg_id"])
    async with SessionLocal() as session:
        _m, new_topics, rerrors = await resolve_topics(session, res, tid, creator, create=False)
    return res, tid, creator, new_topics, rerrors


async def api_import_preview(request: web.Request):
    await _staff(request)
    body = await _json(request)
    res, _tid, _c, new_topics, rerrors = await _import_analyse(request, body)
    return web.json_response({
        "closed": len(res.closed), "open": len(res.open),
        "errors": list(res.errors) + rerrors, "warnings": res.warnings,
        "new_topics": sorted(set(new_topics)),
        "sample": (
            [{"t": q["text"][:120], "a": f"{q['correct']}) {q['options'][q['correct']][:60]}"} for q in res.closed[:5]]
            + [{"t": "[ochiq] " + q["stem"][:110], "a": f"{len(q['parts'])} qism"} for q in res.open[:3]]
        ),
        "can_save": bool(res.total) and not rerrors,
    })


async def api_import_save(request: web.Request):
    from .services.savol_import import save_parsed

    await _staff(request)
    body = await _json(request)
    res, tid, creator, _n, rerrors = await _import_analyse(request, body)
    if not res.total:
        return _err("Saqlanadigan savol yo'q.")
    if rerrors:
        return _err(rerrors[0])
    async with SessionLocal() as session:
        out = await save_parsed(session, tid, res, request["tg_id"], creator)
    if out["errors"]:
        return _err(out["errors"][0])
    return web.json_response(out)


async def api_import_undo(request: web.Request):
    from .admin_roles import is_full_admin
    from .services.savol_import import undo_batch

    await _staff(request)
    async with SessionLocal() as session:
        ok, msg = await undo_batch(session, int(request.match_info["id"]), request["tg_id"], is_full_admin(request["tg_id"]))
    return web.json_response({"ok": ok, "message": msg}) if ok else _err(msg)


async def api_import_redo(request: web.Request):
    from .admin_roles import is_full_admin
    from .services.savol_import import redo_batch

    await _staff(request)
    async with SessionLocal() as session:
        ok, msg = await redo_batch(session, int(request.match_info["id"]), request["tg_id"], is_full_admin(request["tg_id"]))
    return web.json_response({"ok": ok, "message": msg}) if ok else _err(msg)


async def api_import_history(request: web.Request):
    from .admin_roles import is_full_admin
    from .services.savol_import import _batch_counts, history

    await _staff(request)
    async with SessionLocal() as session:
        items = await history(session, request["tg_id"], is_full_admin(request["tg_id"]), 15)
    out = []
    for b in items:
        c, o = _batch_counts(b)
        out.append({"id": b.id, "closed": c, "open": o, "undone": bool(b.undone), "ms": _ms(b.created_at)})
    return web.json_response({"items": out})


async def api_import_image(request: web.Request):
    import asyncio

    from .services.savol_import import MAX_IMG, save_image_bytes

    await _staff(request)
    data = await request.read()
    if len(data) > MAX_IMG:
        return _err("Rasm juda katta (12 MB dan oshmasin).")
    try:
        name = await asyncio.to_thread(save_image_bytes, data)
    except ValueError as exc:
        return _err(str(exc))
    return web.json_response({"name": name})


async def _is_staff(tg_id):
    from .services.savol_import import can_import

    try:
        return bool(await can_import(tg_id))
    except Exception:
        return False


async def api_stats(request: web.Request):
    """Bosh sahifa uchun umumiy raqamlar (bazadan, haqiqiy)."""
    import os

    from sqlalchemy import func

    async with SessionLocal() as session:
        users = await session.scalar(
            select(func.count()).select_from(User).where(User.registration_status == "approved")
        )
        solved = await session.scalar(select(func.coalesce(func.sum(User.total_tests), 0)))
        exams = await session.scalar(
            select(func.count()).select_from(ExamAttempt).where(ExamAttempt.status == "finished")
        )
    return web.json_response({
        "users": int(users or 0), "tests": int(solved or 0), "exams": int(exams or 0),
        "channel": (os.environ.get("CHANNEL_URL") or "https://t.me/TARIX_MILLIY_SERTIFIKAT_A111").strip(),
    })

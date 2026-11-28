"""Imtihon rejimi: ustoz/admin tomoni (yaratish, natijalar, ochiq savollar).

Joylashuvi: app/handlers/imtihon_ustoz.py
"""

from __future__ import annotations
from ..tg_retry import retry as _tg_retry
from ..numfmt import round_half_up

import asyncio
import html
import json
import logging
import re
from datetime import datetime, timedelta

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from sqlalchemy import select

from ..admin_roles import is_full_admin, is_teacher
from ..db import SessionLocal
from ..models import Exam, ExamAttempt, OpenQuestion, Subject, Topic, User
from ..services.imtihon import (
    MILLIY_CLOSED,
    MILLIY_OPEN,
    SCALE_MAX,
    attempt_answers,
    bank_counts,
    create_exam,
    exam_is_open,
    exam_results,
    hardest_units,
    parse_open_text,
    pick_questions,
    pick_questions_multi,
    target_students,
)
from ..services.oqituvchi import get_teacher, teacher_groups
from ..services.student_report import fmt_datetime, to_local
from .milliy_yigish import clear_keep

log = logging.getLogger(__name__)

router = Router()

LIST_PAGE = 8
RESULT_PAGE = 15
TOPIC_PAGE = 10

_tasks: set = set()


class ExamMake(StatesGroup):
    title = State()
    open_paste = State()


def esc(value) -> str:
    return html.escape(str(value or ""))


def short(value: str, limit: int) -> str:
    value = (value or "").strip()
    return value if len(value) <= limit else value[: limit - 1].rstrip() + "…"


def kb(rows) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=rows)


def natural_key(name: str):
    return [int(p) if p.isdigit() else p.casefold() for p in re.split(r"(\d+)", name or "")]


async def show(c: CallbackQuery, text: str, markup):
    try:
        await c.message.edit_text(text, reply_markup=markup)
    except TelegramBadRequest as exc:
        if "not modified" in str(exc).lower():
            return
        await c.message.answer(text, reply_markup=markup)


async def gate(c: CallbackQuery) -> bool:
    uid = c.from_user.id
    if is_full_admin(uid) or await is_teacher(uid):
        return True
    await c.answer("Ruxsat yo'q.", show_alert=True)
    return False


def home_row(uid: int):
    if is_full_admin(uid):
        return [InlineKeyboardButton(text="⚙️ Admin panel", callback_data="admin")]
    return [InlineKeyboardButton(text="🧑‍🏫 Ustoz paneli", callback_data="ust:home")]


def can_see(uid: int, exam: Exam) -> bool:
    return is_full_admin(uid) or exam.created_by == uid


async def groups_for(session, uid: int) -> list[str]:
    if is_full_admin(uid):
        from ..services.assignments import group_names

        return await group_names(session)

    found = await get_teacher(session, uid)
    if found is None:
        return []
    rows = await teacher_groups(session, found[0].id)
    return sorted({g.group_name for g in rows}, key=str.casefold)


# ---------------------------------------------------------------
#  Bosh sahifa
# ---------------------------------------------------------------
@router.callback_query(F.data == "exm:t")
async def cb_home(c: CallbackQuery, state: FSMContext):
    if not await gate(c):
        return
    await clear_keep(state)
    await show(
        c,
        "🎓 <b>Milliy sertifikat imtihoni</b>\n\n"
        "Imtihon — vaqtli test: o'quvchi javobning to'g'riligini oxirigacha ko'rmaydi, "
        "faqat bir marta topshiradi. «Milliy sertifikat» shabloni: 35 yopiq + 10 ochiq savol, "
        "natija 75 ballik shkala va daraja (A+ … C) bilan.",
        kb([
            [InlineKeyboardButton(text="🏅 Milliy sertifikatni yig'ish (1–45 yoki 1–55)", callback_data="mly:home")],
            [InlineKeyboardButton(text="➕ Yangi imtihon (bankdan)", callback_data="exm:n")],
            [InlineKeyboardButton(text="📋 Imtihonlarim", callback_data="exm:ml:0")],
            [InlineKeyboardButton(text="✍️ Ochiq savollar qo'shish", callback_data="exm:oa")],
            [InlineKeyboardButton(text="📥 Savollarni yuklash", callback_data="imq:home")],
            home_row(c.from_user.id),
        ]),
    )
    await c.answer()


# ---------------------------------------------------------------
#  Yangi imtihon: qadamlar
# ---------------------------------------------------------------
async def _draft(state: FSMContext, c: CallbackQuery):
    data = await state.get_data()
    draft = data.get("d")
    if not draft:
        await c.answer("Jarayon eskirgan. Qaytadan boshlang.", show_alert=True)
        return None
    return draft


@router.callback_query(F.data == "exm:n")
async def cb_new(c: CallbackQuery, state: FSMContext):
    if not await gate(c):
        return
    await clear_keep(state)
    await show(
        c,
        "➕ <b>Yangi imtihon</b>\n\nShablonni tanlang:",
        kb([
            [InlineKeyboardButton(
                text=f"🏅 Milliy sertifikat ({MILLIY_CLOSED} yopiq + {MILLIY_OPEN} ochiq)",
                callback_data="exm:n:m",
            )],
            [InlineKeyboardButton(text="📝 Oddiy imtihon (o'zim belgilayman)", callback_data="exm:n:o")],
            [InlineKeyboardButton(text="⬅️ Orqaga", callback_data="exm:t")],
        ]),
    )
    await c.answer()


@router.callback_query(F.data.regexp(r"^exm:n:[mo]$"))
async def cb_template(c: CallbackQuery, state: FSMContext):
    if not await gate(c):
        return
    mode = "milliy" if c.data.endswith(":m") else "oddiy"
    draft = {"mode": mode}
    if mode == "milliy":
        draft.update(c=MILLIY_CLOSED, o=MILLIY_OPEN)
    await state.update_data(d=draft)
    await _ask_subject(c, draft)


async def _ask_subject(c: CallbackQuery, draft: dict):
    async with SessionLocal() as session:
        subjects = (
            await session.scalars(
                select(Subject).where(Subject.is_active.is_(True)).order_by(Subject.name)
            )
        ).all()

    if not subjects:
        await show(c, "Hali birorta fan yo'q.", kb([[InlineKeyboardButton(text="⬅️ Orqaga", callback_data="exm:t")]]))
        await c.answer()
        return

    picked = draft.get("pick") or []
    rows = [
        [InlineKeyboardButton(
            text=("✅ " if s.id in picked else "☐ ") + short(s.name, 38),
            callback_data=f"exm:nm:{s.id}",
        )]
        for s in subjects[:40]
    ]
    if picked:
        rows.append([InlineKeyboardButton(
            text=f"▶️ Davom etish ({len(picked)} ta fan)", callback_data="exm:nmo")])
    rows.append([InlineKeyboardButton(text="⬅️ Orqaga", callback_data="exm:n")])
    await show(
        c,
        "➕ <b>Yangi imtihon</b>\n\n1-qadam: qaysi fanlardan?\n"
        "Bir yoki bir nechta fanni belgilang, keyin «Davom etish»ni bosing.",
        kb(rows),
    )
    await c.answer()


@router.callback_query(F.data.regexp(r"^exm:nm:\d+$"))
async def cb_subject_toggle(c: CallbackQuery, state: FSMContext):
    if not await gate(c):
        return
    draft = await _draft(state, c)
    if draft is None:
        return
    sid = int(c.data.split(":")[2])
    picked = list(draft.get("pick") or [])
    if sid in picked:
        picked.remove(sid)
    else:
        picked.append(sid)
    draft["pick"] = picked
    await state.update_data(d=draft)
    await _ask_subject(c, draft)


@router.callback_query(F.data == "exm:nsel")
async def cb_subject_again(c: CallbackQuery, state: FSMContext):
    if not await gate(c):
        return
    draft = await _draft(state, c)
    if draft is None:
        return
    await _ask_subject(c, draft)


@router.callback_query(F.data == "exm:nmo")
async def cb_subject_done(c: CallbackQuery, state: FSMContext):
    if not await gate(c):
        return
    draft = await _draft(state, c)
    if draft is None:
        return
    picked = draft.get("pick") or []
    if not picked:
        await c.answer("Kamida bitta fanni belgilang.", show_alert=True)
        return

    async with SessionLocal() as session:
        found = [await session.get(Subject, i) for i in picked]
    subjects = [s for s in found if s is not None]
    if not subjects:
        await c.answer("Fan topilmadi.", show_alert=True)
        return

    if len(subjects) == 1:
        draft.pop("sids", None)
        draft.update(sid=subjects[0].id, sname=subjects[0].name)
        await state.update_data(d=draft)
        await _ask_topic(c, state, draft)
        return

    # bir nechta fan: mavzu bosqichi o'tkaziladi (hamma mavzulardan olinadi)
    ids = [s.id for s in subjects]
    async with SessionLocal() as session:
        closed, opened = await bank_counts(session, ids, None)
    draft.pop("sid", None)
    draft.update(
        sids=ids, sname=" + ".join(s.name for s in subjects)[:200],
        tid=None, tname=None, avail_c=closed, avail_o=opened,
    )
    await state.update_data(d=draft)
    if draft["mode"] == "milliy":
        await _ask_duration(c, draft)
    else:
        await _ask_closed(c, draft)


async def _ask_topic(c: CallbackQuery, state: FSMContext, draft: dict, page: int = 0):
    async with SessionLocal() as session:
        topics = (
            await session.scalars(select(Topic).where(Topic.subject_id == draft["sid"]))
        ).all()
        closed, opened = await bank_counts(session, draft["sid"], None)

    topics = sorted(topics, key=lambda t: natural_key(t.name))
    last = max(0, (len(topics) - 1) // TOPIC_PAGE)
    page = min(max(0, page), last)
    chunk = topics[page * TOPIC_PAGE:(page + 1) * TOPIC_PAGE]

    rows = [[InlineKeyboardButton(
        text=f"📚 Butun fan ({closed} yopiq, {opened} ochiq savol)", callback_data="exm:nt:0")]]
    rows += [
        [InlineKeyboardButton(text="📖 " + short(t.name, 40), callback_data=f"exm:nt:{t.id}")]
        for t in chunk
    ]
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"exm:ntp:{page - 1}"))
    if page < last:
        nav.append(InlineKeyboardButton(text="➡️", callback_data=f"exm:ntp:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton(text="⬅️ Orqaga", callback_data="exm:nsel")])

    await show(
        c,
        f"➕ <b>Yangi imtihon</b>\n\nFan: <b>{esc(draft['sname'])}</b>\n\n"
        "2-qadam: savollar qaysi mavzulardan olinsin?\n"
        "«Butun fan» — hamma mavzulardan tasodifiy.",
        kb(rows),
    )
    await c.answer()


@router.callback_query(F.data.regexp(r"^exm:ns:\d+$"))
async def cb_subject(c: CallbackQuery, state: FSMContext):
    if not await gate(c):
        return
    draft = await _draft(state, c)
    if draft is None:
        return
    sid = int(c.data.split(":")[2])
    async with SessionLocal() as session:
        subject = await session.get(Subject, sid)
    if subject is None:
        await c.answer("Fan topilmadi.", show_alert=True)
        return
    draft.update(sid=sid, sname=subject.name)
    await state.update_data(d=draft)
    await _ask_topic(c, state, draft)


@router.callback_query(F.data.regexp(r"^exm:ntp:\d+$"))
async def cb_topic_page(c: CallbackQuery, state: FSMContext):
    if not await gate(c):
        return
    draft = await _draft(state, c)
    if draft is None or "sid" not in draft:
        return
    await _ask_topic(c, state, draft, int(c.data.split(":")[2]))


@router.callback_query(F.data.regexp(r"^exm:nt:\d+$"))
async def cb_topic(c: CallbackQuery, state: FSMContext):
    if not await gate(c):
        return
    draft = await _draft(state, c)
    if draft is None or "sid" not in draft:
        return
    tid = int(c.data.split(":")[2])
    tname = None
    async with SessionLocal() as session:
        if tid:
            topic = await session.get(Topic, tid)
            if topic is None or topic.subject_id != draft["sid"]:
                await c.answer("Mavzu topilmadi.", show_alert=True)
                return
            tname = topic.name
        closed, opened = await bank_counts(session, draft["sid"], tid or None)

    draft.update(tid=tid or None, tname=tname, avail_c=closed, avail_o=opened)
    await state.update_data(d=draft)

    if draft["mode"] == "milliy":
        await _ask_duration(c, draft)
    else:
        await _ask_closed(c, draft)


def _bank_line(draft: dict) -> str:
    if draft.get("fixed"):  # tayyor (yig'ilgan) Milliy sertifikat
        return f"Tayyor imtihon: <b>{draft['c']}</b> yopiq, <b>{draft['o']}</b> ochiq savol"
    return (
        f"Bazada: <b>{draft['avail_c']}</b> yopiq, <b>{draft['avail_o']}</b> ochiq savol"
        + (f" ({esc(draft['tname'])})" if draft.get("tname") else "")
    )


async def _ask_closed(c: CallbackQuery, draft: dict):
    sizes = [10, 20, 30, 35, 45]
    rows = [[InlineKeyboardButton(text=str(n), callback_data=f"exm:nc:{n}") for n in sizes]]
    rows.append([InlineKeyboardButton(
        text="⬅️ Orqaga", callback_data="exm:nsel" if draft.get("sids") else "exm:ntp:0")])
    await show(
        c,
        f"➕ <b>Yangi imtihon</b>\n\n{_bank_line(draft)}\n\n3-qadam: nechta <b>yopiq</b> (A/B/C/D) savol?",
        kb(rows),
    )
    await c.answer()


@router.callback_query(F.data.regexp(r"^exm:nc:\d+$"))
async def cb_closed(c: CallbackQuery, state: FSMContext):
    if not await gate(c):
        return
    draft = await _draft(state, c)
    if draft is None or "avail_c" not in draft:
        return
    draft["c"] = int(c.data.split(":")[2])
    await state.update_data(d=draft)
    sizes = [0, 2, 5, 10]
    rows = [[InlineKeyboardButton(text=str(n), callback_data=f"exm:no:{n}") for n in sizes]]
    await show(
        c,
        f"➕ <b>Yangi imtihon</b>\n\n{_bank_line(draft)}\n\n4-qadam: nechta <b>ochiq</b> savol? "
        "(o'quvchi javobni o'zi yozadi; 0 — ochiq savolsiz)",
        kb(rows),
    )
    await c.answer()


@router.callback_query(F.data.regexp(r"^exm:no:\d+$"))
async def cb_open(c: CallbackQuery, state: FSMContext):
    if not await gate(c):
        return
    draft = await _draft(state, c)
    if draft is None or "c" not in draft:
        return
    draft["o"] = int(c.data.split(":")[2])
    await state.update_data(d=draft)
    await _ask_duration(c, draft)


@router.callback_query(F.data == "exm:fx")
async def cb_fixed(c: CallbackQuery, state: FSMContext):
    """3-bosqich: yuklangan yopiq va ochiq savollarni birlashtirib, imtihonni sozlashga o'tish."""
    if not await gate(c):
        return
    mly = (await state.get_data()).get("mly") or {}
    closed, opened = mly.get("c") or [], mly.get("o") or []
    from ..services.imtihon import milliy_open_ok

    if len(closed) != MILLIY_CLOSED or not milliy_open_ok(opened):
        await c.answer(
            f"Avval 1-bosqich (yopiq {MILLIY_CLOSED} ta) va 2-bosqich (ochiq {MILLIY_OPEN} ta) tugashi kerak.",
            show_alert=True,
        )
        return
    draft = {
        "mode": "milliy", "c": len(closed), "o": len(opened),
        "avail_c": len(closed), "avail_o": len(opened),
        "sname": "Milliy sertifikat", "tid": None, "tname": None,
        "fixed": closed + opened,
    }
    await state.update_data(d=draft)
    await _ask_duration(c, draft)


async def _ask_duration(c: CallbackQuery, draft: dict):
    rows = [
        [InlineKeyboardButton(text=f"{m} daq.", callback_data=f"exm:nd:{m}") for m in (30, 45, 60)],
        [InlineKeyboardButton(text=f"{m} daq.", callback_data=f"exm:nd:{m}") for m in (90, 120, 150, 180)],
    ]
    if draft.get("fixed"):
        rows.append([InlineKeyboardButton(text="⬅️ Orqaga", callback_data="mly:home")])
    await show(
        c,
        f"➕ <b>Yangi imtihon</b>\n\n{_bank_line(draft)}\n"
        f"Savollar: <b>{draft['c']}</b> yopiq + <b>{draft['o']}</b> ochiq\n\n"
        "Imtihon davomiyligi qancha bo'lsin?",
        kb(rows),
    )
    await c.answer()


@router.callback_query(F.data.regexp(r"^exm:nd:\d+$"))
async def cb_duration(c: CallbackQuery, state: FSMContext):
    if not await gate(c):
        return
    draft = await _draft(state, c)
    if draft is None or "c" not in draft:
        return
    draft["dur"] = int(c.data.split(":")[2])
    await state.update_data(d=draft)

    async with SessionLocal() as session:
        groups = await groups_for(session, c.from_user.id)

    admin = is_full_admin(c.from_user.id)
    if not admin and not groups:
        await show(
            c, "Sizga hali guruh biriktirilmagan. Admin bilan bog'laning.",
            kb([[InlineKeyboardButton(text="⬅️ Orqaga", callback_data="exm:t")]]),
        )
        await c.answer()
        return

    rows = []
    if admin:
        rows.append([InlineKeyboardButton(text="👥 Hamma o'quvchilar", callback_data="exm:ng:all")])
    for index, name in enumerate(groups[:40]):
        rows.append([InlineKeyboardButton(text="🏫 " + short(name, 30), callback_data=f"exm:ng:{index}")])
    await state.update_data(grp=groups[:40])
    await show(c, "➕ <b>Yangi imtihon</b>\n\nKimlar topshiradi?", kb(rows))
    await c.answer()


@router.callback_query(F.data.regexp(r"^exm:ng:(all|\d+)$"))
async def cb_group(c: CallbackQuery, state: FSMContext):
    if not await gate(c):
        return
    data = await state.get_data()
    draft = data.get("d")
    if not draft or "dur" not in draft:
        await c.answer("Jarayon eskirgan. Qaytadan boshlang.", show_alert=True)
        return

    key = c.data.split(":")[2]
    if key == "all":
        if not is_full_admin(c.from_user.id):
            await c.answer("Faqat o'z guruhlaringizni tanlay olasiz.", show_alert=True)
            return
        draft["grp"] = None
    else:
        groups = data.get("grp") or []
        index = int(key)
        if index >= len(groups):
            await c.answer("Ro'yxat eskirgan. Qaytadan boshlang.", show_alert=True)
            return
        draft["grp"] = groups[index]

    await state.update_data(d=draft)
    rows = [[InlineKeyboardButton(text=f"{d} kun", callback_data=f"exm:nx:{d}") for d in (1, 3, 7, 14)]]
    await show(
        c,
        "➕ <b>Yangi imtihon</b>\n\nO'quvchilar imtihonni necha kun ichida boshlay olishadi?\n"
        "<i>(Boshlagandan keyin esa faqat imtihon vaqti beriladi.)</i>",
        kb(rows),
    )
    await c.answer()


@router.callback_query(F.data.regexp(r"^exm:nx:\d+$"))
async def cb_days(c: CallbackQuery, state: FSMContext):
    if not await gate(c):
        return
    draft = await _draft(state, c)
    if draft is None or "dur" not in draft or "grp" not in draft:
        return
    draft["days"] = int(c.data.split(":")[2])
    stamp = to_local(datetime.utcnow()).strftime("%d.%m.%Y")
    draft["default_title"] = f"{draft['sname']} — {stamp}"[:200]
    await state.update_data(d=draft)
    await state.set_state(ExamMake.title)
    await show(
        c,
        "➕ <b>Yangi imtihon</b>\n\nImtihon nomini yozing yoki tayyor nomni tanlang:\n\n"
        f"<i>{esc(draft['default_title'])}</i>",
        kb([[InlineKeyboardButton(text="✅ Shu nom", callback_data="exm:nti")]]),
    )
    await c.answer()


async def _preview(target, state: FSMContext, draft: dict, title: str):
    draft["title"] = title[:200]
    await state.update_data(d=draft)
    await state.set_state(None)
    want_c, want_o = draft["c"], draft["o"]
    real_c, real_o = min(want_c, draft["avail_c"]), min(want_o, draft["avail_o"])

    lines = [
        "➕ <b>Imtihonni tasdiqlang</b>", "",
        f"📝 <b>{esc(draft['title'])}</b>",
        f"📚 {esc(draft['sname'])}" + (f" · {esc(draft['tname'])}" if draft.get("tname") else "")
        + (" (savollar fanlar orasida teng olinadi)" if draft.get("sids") else ""),
        f"🏅 Shablon: {'Milliy sertifikat' if draft['mode'] == 'milliy' else 'Oddiy'}",
        f"👥 Kimlar: {esc(draft['grp']) if draft['grp'] else 'hamma o‘quvchilar'}",
        f"⏱ Vaqt: {draft['dur']} daqiqa · boshlash muddati: {draft['days']} kun",
        "",
        f"Yopiq savollar: <b>{real_c}</b>" + (f" (so'raldi {want_c}, bazada yetarli emas)" if real_c < want_c else ""),
        f"Ochiq savollar: <b>{real_o}</b>" + (f" (so'raldi {want_o}, bazada yetarli emas)" if real_o < want_o else ""),
    ]
    rows = []
    if real_c + real_o == 0:
        lines += ["", "❌ Tanlangan fanda savol yo'q. Avval savol qo'shing."]
    else:
        rows.append([InlineKeyboardButton(text="✅ Yaratish va xabar yuborish", callback_data="exm:nok")])
    rows.append([InlineKeyboardButton(text="❌ Bekor qilish", callback_data="exm:t")])

    markup = kb(rows)
    if isinstance(target, CallbackQuery):
        await show(target, "\n".join(lines), markup)
        await target.answer()
    else:
        await target.answer("\n".join(lines), reply_markup=markup)


@router.callback_query(ExamMake.title, F.data == "exm:nti")
async def cb_title_default(c: CallbackQuery, state: FSMContext):
    if not await gate(c):
        return
    draft = await _draft(state, c)
    if draft is None or "default_title" not in draft:
        return
    await _preview(c, state, draft, draft["default_title"])


@router.message(ExamMake.title)
async def msg_title(m: Message, state: FSMContext):
    uid = m.from_user.id
    if not (is_full_admin(uid) or await is_teacher(uid)):
        return
    text = (m.text or "").strip()
    if not text:
        await m.answer("❗ Imtihon nomini matn ko'rinishida yozing.")
        return
    data = await state.get_data()
    draft = data.get("d")
    if not draft or "days" not in draft:
        await state.clear()
        await m.answer("Jarayon eskirgan. Qaytadan boshlang: /start")
        return
    await _preview(m, state, draft, text)


@router.callback_query(F.data == "exm:nok")
async def cb_create(c: CallbackQuery, state: FSMContext):
    if not await gate(c):
        return
    data = await state.get_data()
    draft = data.get("d")
    if not draft or "title" not in draft:
        await c.answer("Jarayon eskirgan. Qaytadan boshlang.", show_alert=True)
        return
    await state.clear()  # ikki marta bosishdan himoya

    await c.answer("Yaratilmoqda…")

    async with SessionLocal() as session:
        if draft.get("fixed"):
            payloads = list(draft["fixed"])
            real_c = sum(1 for p in payloads if p.get("k") == "c")
            real_o = len(payloads) - real_c
        elif draft.get("sids"):
            payloads, real_c, real_o = await pick_questions_multi(
                session, draft["sids"], draft["c"], draft["o"]
            )
        else:
            payloads, real_c, real_o = await pick_questions(
                session, draft["sid"], draft.get("tid"), draft["c"], draft["o"]
            )
        if not payloads:
            await show(c, "❌ Savol topilmadi. Imtihon yaratilmadi.",
                       kb([[InlineKeyboardButton(text="⬅️ Orqaga", callback_data="exm:t")]]))
            return
        exam = await create_exam(
            session,
            title=draft["title"],
            subject_name=draft["sname"],
            mode=draft["mode"],
            created_by=c.from_user.id,
            group_name=draft["grp"],
            duration_min=draft["dur"],
            days_open=draft["days"],
            payloads=payloads,
        )
        students = await target_students(session, exam)
        exam_id, title, duration = exam.id, exam.title, exam.duration_min
        deadline = exam.closes_at
        chat_ids = [u.telegram_id for u in students if u.telegram_id != c.from_user.id]

    task = asyncio.create_task(
        _announce(c.bot, c.from_user.id, exam_id, title, real_c, real_o, duration, deadline, chat_ids)
    )
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)

    await show(
        c,
        f"✅ <b>Imtihon yaratildi</b>\n\n📝 {esc(title)}\n"
        f"Yopiq: {real_c} · Ochiq: {real_o} · {duration} daqiqa\n\n"
        f"⏳ {len(chat_ids)} ta o'quvchiga xabar yuborilmoqda…",
        kb([
            [InlineKeyboardButton(text="📊 Boshqarish", callback_data=f"exm:m:{exam_id}")],
            [InlineKeyboardButton(text="⬅️ Orqaga", callback_data="exm:t")],
        ]),
    )


async def _announce(bot, owner_id, exam_id, title, real_c, real_o, duration, deadline, chat_ids):
    text = (
        "📝 <b>Yangi imtihon!</b>\n\n"
        f"<b>{esc(title)}</b>\n"
        f"Savollar: {real_c} yopiq" + (f" + {real_o} ochiq" if real_o else "") + "\n"
        f"⏱ Vaqt: {duration} daqiqa\n"
        f"📅 Boshlash muddati: {fmt_datetime(deadline)} gacha"
    )
    markup = kb([[InlineKeyboardButton(text="📝 Ko'rish", callback_data=f"exm:i:{exam_id}")]])
    sent = failed = 0
    for chat_id in chat_ids:
        try:
            await _tg_retry(lambda: bot.send_message(chat_id, text, reply_markup=markup), tries=3)
            sent += 1
        except Exception:
            failed += 1
        await asyncio.sleep(0.05)
    try:
        await _tg_retry(lambda: bot.send_message(
            owner_id,
            f"📨 Imtihon xabari yuborildi: <b>{sent}</b> ta"
            + (f" (yetmadi: {failed})" if failed else ""),
        ), tries=3)
    except Exception:
        pass


# ---------------------------------------------------------------
#  Imtihonlar ro'yxati va boshqarish
# ---------------------------------------------------------------
@router.callback_query(F.data.regexp(r"^exm:ml:\d+$"))
async def cb_my_list(c: CallbackQuery, state: FSMContext):
    if not await gate(c):
        return
    await state.clear()
    uid = c.from_user.id
    page = int(c.data.split(":")[2])

    async with SessionLocal() as session:
        stmt = select(Exam).where(Exam.is_removed.is_(False)).order_by(Exam.id.desc())
        if not is_full_admin(uid):
            stmt = stmt.where(Exam.created_by == uid)
        exams = (await session.scalars(stmt.limit(200))).all()

    if not exams:
        await show(c, "Hali imtihon yaratilmagan.", kb([[InlineKeyboardButton(text="⬅️ Orqaga", callback_data="exm:t")]]))
        await c.answer()
        return

    last = (len(exams) - 1) // LIST_PAGE
    page = min(page, last)
    rows = [
        [InlineKeyboardButton(
            text=f"{'🟢' if exam_is_open(e) else '🔒'} {short(e.title, 38)}",
            callback_data=f"exm:m:{e.id}",
        )]
        for e in exams[page * LIST_PAGE:(page + 1) * LIST_PAGE]
    ]
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"exm:ml:{page - 1}"))
    if page < last:
        nav.append(InlineKeyboardButton(text="➡️", callback_data=f"exm:ml:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton(text="⬅️ Orqaga", callback_data="exm:t")])
    await show(c, "📋 <b>Imtihonlarim</b>\n\n🟢 ochiq · 🔒 yopiq", kb(rows))
    await c.answer()


async def _load_exam(c: CallbackQuery, exam_id: int):
    async with SessionLocal() as session:
        exam = await session.get(Exam, exam_id)
    if exam is None or exam.is_removed or not can_see(c.from_user.id, exam):
        await c.answer("Imtihon topilmadi.", show_alert=True)
        return None
    return exam


@router.callback_query(F.data.regexp(r"^exm:m:\d+$"))
async def cb_manage(c: CallbackQuery, state: FSMContext):
    if not await gate(c):
        return
    await state.clear()
    await _manage_view(c, int(c.data.split(":")[2]))


async def _manage_view(c: CallbackQuery, exam_id: int, note: str | None = None):
    exam = await _load_exam(c, exam_id)
    if exam is None:
        return

    async with SessionLocal() as session:
        targets = await target_students(session, exam)
        attempts = (
            await session.scalars(select(ExamAttempt).where(ExamAttempt.exam_id == exam_id))
        ).all()
        _items, results = await exam_results(session, exam)

    started = {a.user_id for a in attempts}
    finished = sum(1 for a in attempts if a.status == "finished")
    active = sum(1 for a in attempts if a.status == "active")
    missing = [u for u in targets if u.id not in started]
    open_now = exam_is_open(exam)

    lines = [
        f"📝 <b>{esc(exam.title)}</b>",
        f"📚 {esc(exam.subject_name)} · {'Milliy sertifikat' if exam.mode == 'milliy' else 'Oddiy'}",
        f"👥 {esc(exam.group_name) if exam.group_name else 'hamma o‘quvchilar'}",
        f"⏱ {exam.duration_min} daqiqa",
        f"{'🟢 Ochiq' if open_now else '🔒 Yopiq'}"
        + (f" · muddat: {fmt_datetime(exam.closes_at)}" if exam.closes_at else ""),
        "",
        f"✅ Topshirgan: <b>{finished}</b> · ⏳ yechayotgan: <b>{active}</b> · "
        f"🕳 boshlamagan: <b>{len(missing)}</b>",
    ]
    if results:
        avg = sum(r.score.value for r in results) / len(results)
        unit = "" if exam.mode == "milliy" else "%"
        lines.append(f"📊 O'rtacha: <b>{avg:.1f}{unit}</b>" + (f" / {int(SCALE_MAX)}" if exam.mode == "milliy" else ""))
    if missing:
        names = ", ".join(esc(short(u.full_name, 18)) for u in missing[:10])
        lines.append(f"\nBoshlamaganlar: {names}" + (f" … +{len(missing) - 10}" if len(missing) > 10 else ""))

    rows = [
        [
            InlineKeyboardButton(text="📊 Natijalar", callback_data=f"exm:res:{exam_id}:0"),
            InlineKeyboardButton(text="📉 Qiyin savollar", callback_data=f"exm:hard:{exam_id}"),
        ],
        [InlineKeyboardButton(text="✍️ Yozma javoblarni tekshirish", callback_data=f"exm:yz:{exam_id}")],
        [InlineKeyboardButton(
            text="🔒 Yopish (tahlil ochiladi)" if open_now else "🔓 Qayta ochish",
            callback_data=f"exm:cl:{exam_id}",
        )],
        [InlineKeyboardButton(text="🗑 O'chirish", callback_data=f"exm:del:{exam_id}")],
        [InlineKeyboardButton(text="⬅️ Imtihonlarim", callback_data="exm:ml:0")],
    ]
    await show(c, "\n".join(lines), kb(rows))
    await c.answer(note, show_alert=bool(note))


@router.callback_query(F.data.regexp(r"^exm:res:\d+:\d+$"))
async def cb_results(c: CallbackQuery):
    if not await gate(c):
        return
    _, _, eid, page_s = c.data.split(":")
    exam = await _load_exam(c, int(eid))
    if exam is None:
        return

    async with SessionLocal() as session:
        _items, results = await exam_results(session, exam)

    if not results:
        await c.answer("Hali hech kim topshirmagan.", show_alert=True)
        return

    last = (len(results) - 1) // RESULT_PAGE
    page = min(max(0, int(page_s)), last)
    start = page * RESULT_PAGE
    lines = [f"📊 <b>{esc(short(exam.title, 40))}</b> — natijalar", ""]
    for i, r in enumerate(results[start:start + RESULT_PAGE], start=start + 1):
        s = r.score
        value = f"{s.value} ({s.level})" if exam.mode == "milliy" else f"{s.value}%"
        extra = f"{s.c_ok}/{s.c_total}" + (f" + {s.o_ok}/{s.o_total}" if s.o_total else "")
        lines.append(f"{i}. {esc(short(r.user.full_name, 24))} — <b>{value}</b> · {extra} · {r.minutes} daq.")

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"exm:res:{eid}:{page - 1}"))
    if page < last:
        nav.append(InlineKeyboardButton(text="➡️", callback_data=f"exm:res:{eid}:{page + 1}"))
    rows = [nav] if nav else []
    rows.append([InlineKeyboardButton(text="⬅️ Orqaga", callback_data=f"exm:m:{eid}")])
    await show(c, "\n".join(lines), kb(rows))
    await c.answer()


@router.callback_query(F.data.regexp(r"^exm:hard:\d+$"))
async def cb_hard(c: CallbackQuery):
    if not await gate(c):
        return
    eid = int(c.data.split(":")[2])
    exam = await _load_exam(c, eid)
    if exam is None:
        return

    async with SessionLocal() as session:
        items, results = await exam_results(session, exam)

    if not results:
        await c.answer("Hali hech kim topshirmagan.", show_alert=True)
        return

    answers_list = [attempt_answers(r.attempt) for r in results]
    by_pos = {it.pos: it for it in items}
    lines = [f"📉 <b>Eng qiyin savollar</b>\n{esc(short(exam.title, 40))}\n"]
    for unit, share in hardest_units(items, answers_list):
        item = by_pos[unit.pos]
        if item.kind == "c":
            label = short(item.data.get("t") or "(rasmli savol)", 60)
            tag = f"{unit.pos}-savol"
        else:
            label = short(item.data["p"][unit.part]["q"], 60)
            tag = f"{unit.pos}-savol, {'abcd'[unit.part]})"
        lines.append(f"• <b>{tag}</b> — {int(round_half_up(share * 100))}% to'g'ri\n   <i>{esc(label)}</i>")
    lines.append("\nShu mavzularni darsda takrorlash foydali.")
    await show(c, "\n".join(lines), kb([[InlineKeyboardButton(text="⬅️ Orqaga", callback_data=f"exm:m:{eid}")]]))
    await c.answer()


# ---------------- yozma javoblarni ustoz tekshiradi (veb-ilovadagi bilan bir xil servis) ----------------
async def _open_groups(exam):
    from ..services.imtihon import open_answer_groups

    async with SessionLocal() as session:
        items, results = await exam_results(session, exam)
    return open_answer_groups(items, [attempt_answers(r.attempt) for r in results]), len(results)


@router.callback_query(F.data.regexp(r"^exm:yz:\d+$"))
async def cb_open_review(c: CallbackQuery):
    """Yozma qismlar ro'yxati: har birida nechta ko'rib chiqiladigan yozilish bor."""
    if not await gate(c):
        return
    eid = int(c.data.split(":")[2])
    exam = await _load_exam(c, eid)
    if exam is None:
        return
    parts, count = await _open_groups(exam)
    if not parts:
        await c.answer("Bu imtihonda yozma savol yo'q.", show_alert=True)
        return
    if not count:
        await c.answer("Hali hech kim topshirmagan.", show_alert=True)
        return
    pending = sum(len(p["wrong"]) for p in parts)
    lines = [f"✍️ <b>Yozma javoblarni tekshirish</b>\n{esc(short(exam.title, 40))}\n",
             f"👥 Topshirganlar: <b>{count}</b> · 🔎 ko'rib chiqiladigan yozilishlar: <b>{pending}</b>\n",
             "Avtomatik to'g'ri deb topilmagan javoblar. «✅» bosilsa — shu yozilish SHU imtihonda to'g'ri hisoblanadi "
             "va shunday yozgan hammaning bali qayta hisoblanadi. Savollar bazasi o'zgarmaydi."]
    rows = []
    for i, p in enumerate(parts[:40]):
        tag = f"{p['pos']}-savol" + (f", {'abcd'[p['part']]})" if len([x for x in parts if x['pos'] == p['pos']]) > 1 else "")
        rows.append([InlineKeyboardButton(text=f"{tag} — {len(p['wrong'])} ta" + (f" · +{len(p['ax'])}" if p["ax"] else ""),
                                          callback_data=f"exm:yp:{eid}:{i}")])
    rows.append([InlineKeyboardButton(text="⬅️ Orqaga", callback_data=f"exm:m:{eid}")])
    await show(c, "\n".join(lines), kb(rows))
    await c.answer()


async def _open_part_view(c: CallbackQuery, exam, pi: int, note: str | None = None):
    parts, _count = await _open_groups(exam)
    if pi >= len(parts):
        await c.answer("Qism topilmadi.", show_alert=True)
        return
    p = parts[pi]
    lines = [f"✍️ <b>{p['pos']}-savol" + (f", {'abcd'[p['part']]})" if p["q"] and p["q"] != "Javobingizni yozing" else "") + "</b>",
             esc(short(p["q"], 300)) if p["q"] and p["q"] != "Javobingizni yozing" else "",
             f"✅ To'g'ri javob: <b>{esc(' / '.join(p['a'][:5]))}</b>",
             f"To'g'ri yechganlar: {p['ok']} ta"]
    rows = []
    if p["ax"]:
        lines.append("\n➕ <b>Qo'shimcha qabul qilinganlar</b> (bekor qilish uchun bosing):")
        for xi, x in enumerate(p["ax"][:15]):
            lines.append(f"  {xi + 1}) «{esc(short(x, 60))}»")
            rows.append([InlineKeyboardButton(text=f"↩️ Bekor: {xi + 1}) {short(x, 30)}", callback_data=f"exm:yu:{exam.id}:{pi}:{xi}")])
    if p["wrong"]:
        lines.append("\n🔎 <b>Ko'rib chiqiladigan javoblar</b> (to'g'ri bo'lsa, ✅ ni bosing):")
        for wi, w in enumerate(p["wrong"][:20]):
            lines.append(f"  {wi + 1}) «{esc(short(w['text'], 60))}» — {w['count']} kishi")
            rows.append([InlineKeyboardButton(text=f"✅ {wi + 1}) {short(w['text'], 30)} ({w['count']})", callback_data=f"exm:ya:{exam.id}:{pi}:{wi}")])
        if len(p["wrong"]) > 20:
            lines.append(f"  … va yana {len(p['wrong']) - 20} ta (avval shularni ko'rib chiqing)")
    else:
        lines.append("\nKo'rib chiqiladigan javob yo'q.")
    rows.append([InlineKeyboardButton(text="⬅️ Qismlar ro'yxati", callback_data=f"exm:yz:{exam.id}")])
    await show(c, "\n".join(x for x in lines if x is not None)[:3900], kb(rows))
    await c.answer(note, show_alert=bool(note))


@router.callback_query(F.data.regexp(r"^exm:yp:\d+:\d+$"))
async def cb_open_part(c: CallbackQuery):
    if not await gate(c):
        return
    _a, _b, eid, pi = c.data.split(":")
    exam = await _load_exam(c, int(eid))
    if exam is not None:
        await _open_part_view(c, exam, int(pi))


@router.callback_query(F.data.regexp(r"^exm:y[au]:\d+:\d+:\d+$"))
async def cb_open_accept(c: CallbackQuery):
    """exm:ya — yozilishni qabul qilish, exm:yu — qo'shimcha qabulni bekor qilish."""
    from ..models import ExamItem
    from ..services.imtihon import toggle_extra_variant

    if not await gate(c):
        return
    _a, act, eid, pi, wi = c.data.split(":")
    eid, pi, wi = int(eid), int(pi), int(wi)
    exam = await _load_exam(c, eid)
    if exam is None:
        return
    parts, _count = await _open_groups(exam)
    if pi >= len(parts):
        await c.answer("Ro'yxat o'zgargan. Qayta oching.", show_alert=True)
        return
    p = parts[pi]
    pool = [w["text"] for w in p["wrong"][:20]] if act == "ya" else list(p["ax"][:15])
    if wi >= len(pool):
        await c.answer("Ro'yxat o'zgargan. Qayta oching.", show_alert=True)
        return
    text = pool[wi]
    async with SessionLocal() as session:
        row = await session.scalar(
            select(ExamItem).where(ExamItem.exam_id == eid, ExamItem.position == p["pos"]).with_for_update())
        if row is None:
            await c.answer("Savol topilmadi.", show_alert=True)
            return
        try:
            payload = json.loads(row.payload)
        except ValueError:
            await c.answer("Savol ma'lumoti buzilgan.", show_alert=True)
            return
        changed, note = toggle_extra_variant(payload, p["part"], text, act == "ya")
        if changed:
            row.payload = json.dumps(payload, ensure_ascii=False)
            await session.commit()
    if changed:
        log.info("bot: imtihon %s, %s: %s «%s» (%s)", eid, p["key"], "qabul" if act == "ya" else "bekor", text[:60], c.from_user.id)
    await _open_part_view(c, exam, pi, note=f"«{short(text, 40)}»: {note}")


@router.callback_query(F.data.regexp(r"^exm:cl:\d+$"))
async def cb_toggle(c: CallbackQuery):
    if not await gate(c):
        return
    eid = int(c.data.split(":")[2])
    async with SessionLocal() as session:
        exam = await session.get(Exam, eid)
        if exam is None or exam.is_removed or not can_see(c.from_user.id, exam):
            await c.answer("Imtihon topilmadi.", show_alert=True)
            return
        if exam_is_open(exam):
            exam.is_open = False
            note = "Imtihon yopildi. Tahlil ochildi."
        else:
            exam.is_open = True
            if exam.closes_at is None or exam.closes_at <= datetime.utcnow():
                exam.closes_at = datetime.utcnow() + timedelta(days=1)
            note = "Imtihon qayta ochildi (1 kun)."
        await session.commit()
    await _manage_view(c, eid, note)


@router.callback_query(F.data.regexp(r"^exm:del:\d+$"))
async def cb_delete_ask(c: CallbackQuery):
    if not await gate(c):
        return
    eid = int(c.data.split(":")[2])
    exam = await _load_exam(c, eid)
    if exam is None:
        return
    await show(
        c,
        f"🗑 <b>«{esc(exam.title)}»</b> imtihoni o'chiriladi.\n\n"
        "O'quvchilar uni ko'rmaydi, natijalar ham yo'qoladi. Davom etamizmi?",
        kb([
            [InlineKeyboardButton(text="✅ Ha, o'chirish", callback_data=f"exm:delok:{eid}")],
            [InlineKeyboardButton(text="❌ Yo'q", callback_data=f"exm:m:{eid}")],
        ]),
    )
    await c.answer()


@router.callback_query(F.data.regexp(r"^exm:delok:\d+$"))
async def cb_delete(c: CallbackQuery):
    if not await gate(c):
        return
    eid = int(c.data.split(":")[2])
    async with SessionLocal() as session:
        exam = await session.get(Exam, eid)
        if exam is None or not can_see(c.from_user.id, exam):
            await c.answer("Imtihon topilmadi.", show_alert=True)
            return
        exam.is_removed = True
        await session.commit()
    await show(c, "✅ Imtihon o'chirildi.", kb([[InlineKeyboardButton(text="⬅️ Orqaga", callback_data="exm:t")]]))
    await c.answer()


# ---------------------------------------------------------------
#  Ochiq savollar qo'shish
# ---------------------------------------------------------------
@router.callback_query(F.data == "exm:oa")
async def cb_open_subject(c: CallbackQuery, state: FSMContext):
    if not await gate(c):
        return
    await state.clear()
    async with SessionLocal() as session:
        subjects = (
            await session.scalars(select(Subject).where(Subject.is_active.is_(True)).order_by(Subject.name))
        ).all()
    rows = [
        [InlineKeyboardButton(text="📚 " + short(s.name, 40), callback_data=f"exm:oas:{s.id}:0")]
        for s in subjects[:40]
    ]
    rows.append([InlineKeyboardButton(text="⬅️ Orqaga", callback_data="exm:t")])
    await show(c, "✍️ <b>Ochiq savollar</b>\n\nQaysi fanga qo'shamiz?", kb(rows))
    await c.answer()


@router.callback_query(F.data.regexp(r"^exm:oas:\d+:\d+$"))
async def cb_open_topic(c: CallbackQuery):
    if not await gate(c):
        return
    _, _, sid_s, page_s = c.data.split(":")
    async with SessionLocal() as session:
        topics = (await session.scalars(select(Topic).where(Topic.subject_id == int(sid_s)))).all()
    topics = sorted(topics, key=lambda t: natural_key(t.name))
    if not topics:
        await c.answer("Bu fanda mavzu yo'q.", show_alert=True)
        return
    last = (len(topics) - 1) // TOPIC_PAGE
    page = min(max(0, int(page_s)), last)
    rows = [
        [InlineKeyboardButton(text="📖 " + short(t.name, 40), callback_data=f"exm:oat:{t.id}")]
        for t in topics[page * TOPIC_PAGE:(page + 1) * TOPIC_PAGE]
    ]
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"exm:oas:{sid_s}:{page - 1}"))
    if page < last:
        nav.append(InlineKeyboardButton(text="➡️", callback_data=f"exm:oas:{sid_s}:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton(text="⬅️ Orqaga", callback_data="exm:oa")])
    await show(c, "✍️ <b>Ochiq savollar</b>\n\nQaysi mavzuga qo'shamiz?", kb(rows))
    await c.answer()


FORMAT_HELP = (
    "Savollarni quyidagi ko'rinishda yuboring. Savollar orasiga <code>---</code> qatori qo'ying.\n\n"
    "<code>XIII asr boshida mo'g'ul qabilalari birlashtirildi.\n"
    "a) Davlat asoschisi nomini yozing => Chingizxon | Temujin\n"
    "b) U qaysi qal'ani poytaxt qilgan? => Qorakorum\n"
    "---\n"
    "Qo'qon xonligi g'arbda kimlar bilan chegaradosh?\n"
    "a) Ikkala davlatni yozing => Buxoro + Xiva</code>\n\n"
    "• <code>|</code> — bir nechta to'g'ri yozilish (istalgani qabul)\n"
    "• <code>+</code> — ikkalasi ham yozilishi kerak (tartibi muhim emas)\n"
    "• Katta-kichik harf, bo'shliq va apostrof farqi hisobga olinmaydi\n"
    "• Har savolda 1 dan 4 tagacha qism (a, b, c, d)"
)


@router.callback_query(F.data.regexp(r"^exm:oat:\d+$"))
async def cb_open_ready(c: CallbackQuery, state: FSMContext):
    if not await gate(c):
        return
    tid = int(c.data.split(":")[2])
    async with SessionLocal() as session:
        topic = await session.get(Topic, tid)
    if topic is None:
        await c.answer("Mavzu topilmadi.", show_alert=True)
        return
    await state.set_state(ExamMake.open_paste)
    await state.update_data(open_topic=tid)
    await show(
        c,
        f"✍️ <b>{esc(topic.name)}</b> mavzusiga ochiq savollar\n\n{FORMAT_HELP}",
        kb([[InlineKeyboardButton(text="❌ Bekor qilish", callback_data="exm:t")]]),
    )
    await c.answer()


@router.message(ExamMake.open_paste)
async def msg_open_paste(m: Message, state: FSMContext):
    uid = m.from_user.id
    if not (is_full_admin(uid) or await is_teacher(uid)):
        return
    data = await state.get_data()
    tid = data.get("open_topic")
    questions, errors = parse_open_text(m.text or "")

    if not questions:
        await m.answer(
            "❌ Birorta ham savol o'qilmadi.\n\n"
            + ("\n".join("• " + esc(e) for e in errors[:10]) + "\n\n" if errors else "")
            + "Qaytadan yuboring yoki «Bekor qilish» ni bosing.\n\n" + FORMAT_HELP
        )
        return

    async with SessionLocal() as session:
        for q in questions:
            session.add(
                OpenQuestion(
                    topic_id=tid,
                    stem=q["stem"],
                    parts=json.dumps(q["parts"], ensure_ascii=False),
                    created_by=uid,
                )
            )
        await session.commit()

    await state.clear()
    text = f"✅ <b>{len(questions)}</b> ta ochiq savol saqlandi."
    if errors:
        text += "\n\n⚠️ Quyidagilar o'qilmadi (ularni tuzatib qayta yuboring):\n" + "\n".join(
            "• " + esc(e) for e in errors[:10]
        )
    await m.answer(
        text,
        reply_markup=kb([
            [InlineKeyboardButton(text="✍️ Yana qo'shish", callback_data="exm:oa")],
            [InlineKeyboardButton(text="⬅️ Orqaga", callback_data="exm:t")],
        ]),
    )

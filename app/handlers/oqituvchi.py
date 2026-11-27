"""Ustoz paneli: o'z guruhlari, o'quvchilari va guruhga xabar yuborish.

Joylashuvi: app/handlers/oqituvchi.py

Ustoz faqat o'ziga biriktirilgan guruhlarni ko'radi. Har bir bosishda
huquq qayta tekshiriladi (ustozlik olib tashlansa, panel darhol yopiladi).
"""

from __future__ import annotations

from ..tg_retry import retry as _tg_retry
import asyncio
import html
import logging

from aiogram import F, Router
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramRetryAfter,
)
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from ..db import SessionLocal
from ..models import TeacherGroup
from ..services.oqituvchi import (
    INACTIVE_DAYS,
    get_teacher,
    group_students,
    percent,
    teacher_groups,
)
from ..services.student_report import fmt_datetime, student_report

log = logging.getLogger(__name__)

router = Router()

PAGE_SIZE = 8
MAX_MESSAGE_LEN = 1000
SEND_PAUSE = 0.05

_tasks: set = set()


class UstozState(StatesGroup):
    message_text = State()
    message_confirm = State()


def esc(value) -> str:
    return html.escape(str(value or ""))


def short(value: str, limit: int) -> str:
    value = (value or "").strip()
    return value if len(value) <= limit else value[: limit - 1].rstrip() + "…"


async def show(c: CallbackQuery, text: str, kb: InlineKeyboardMarkup | None):
    """Xabarni yangilaydi; o'zgarmagan bo'lsa jim o'tadi."""
    try:
        await c.message.edit_text(text, reply_markup=kb)
    except TelegramBadRequest as exc:
        if "not modified" in str(exc).lower():
            return
        await c.message.answer(text, reply_markup=kb)


def home_btn() -> list:
    return [InlineKeyboardButton(text="🏠 Bosh menyu", callback_data="home")]


async def load_group(session, telegram_id: int, group_row_id: int):
    """(teacher, user, group) yoki None — guruh shu ustozniki bo'lsagina."""
    found = await get_teacher(session, telegram_id)
    if found is None:
        return None

    teacher, user = found
    group = await session.get(TeacherGroup, group_row_id)

    if group is None or group.teacher_id != teacher.id:
        return None

    return teacher, user, group


# ---------------------------------------------------------------
#  Bosh sahifa: guruhlarim
# ---------------------------------------------------------------
async def _home_view(telegram_id: int):
    async with SessionLocal() as session:
        found = await get_teacher(session, telegram_id)
        if found is None:
            return None

        teacher, user = found
        groups = await teacher_groups(session, teacher.id)
        rows = []
        for g in groups:
            students = await group_students(session, g.group_name)
            rows.append((g, students))

    lines = ["🧑‍🏫 <b>Ustoz paneli</b>", ""]
    buttons = []

    if not rows:
        lines.append(
            "Sizga hali guruh biriktirilmagan.\n"
            "Guruh biriktirish uchun admin bilan bog'laning."
        )
    else:
        lines.append("Guruhlaringiz:")
        for g, students in rows:
            idle = sum(1 for s in students if s.is_inactive())
            lines.append(
                f"• <b>{esc(g.group_name)}</b> — {len(students)} o'quvchi"
                + (f", 💤 {idle}" if idle else "")
            )
            buttons.append([
                InlineKeyboardButton(
                    text=f"👥 {short(g.group_name, 30)} ({len(students)})",
                    callback_data=f"ust:g:{g.id}",
                )
            ])

    buttons.append(home_btn())
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=buttons)


@router.message(Command("ustoz"))
async def cmd_ustoz(m: Message, state: FSMContext):
    view = await _home_view(m.from_user.id)
    if view is None:
        return
    await state.clear()
    await m.answer(view[0], reply_markup=view[1])


@router.callback_query(F.data == "ust:home")
async def cb_home(c: CallbackQuery, state: FSMContext):
    view = await _home_view(c.from_user.id)
    if view is None:
        await c.answer("Sizda ustoz huquqi yo'q.", show_alert=True)
        return
    await state.clear()
    await show(c, view[0], view[1])
    await c.answer()


# ---------------------------------------------------------------
#  Guruh sahifasi
# ---------------------------------------------------------------
@router.callback_query(F.data.regexp(r"^ust:g:\d+$"))
async def cb_group(c: CallbackQuery, state: FSMContext):
    gid = int(c.data.split(":")[2])

    async with SessionLocal() as session:
        ctx = await load_group(session, c.from_user.id, gid)
        if ctx is None:
            await c.answer("Bu guruhga huquqingiz yo'q.", show_alert=True)
            return
        group = ctx[2]
        students = await group_students(session, group.group_name)

    await state.clear()

    active = [s for s in students if not s.is_inactive()]
    idle = [s for s in students if s.is_inactive()]
    blocked = sum(1 for s in students if s.is_blocked)
    total = sum(s.total for s in students)
    correct = sum(s.correct for s in students)

    lines = [
        f"👥 <b>Guruh: {esc(group.group_name)}</b>",
        "",
        f"O'quvchilar: <b>{len(students)}</b>",
        f"✅ Oxirgi {INACTIVE_DAYS} kunda test ishlagan: <b>{len(active)}</b>",
        f"💤 Faol emas: <b>{len(idle)}</b>",
    ]

    if total:
        lines.append(
            f"🎯 Guruh bo'yicha o'rtacha natija: <b>{percent(correct, total)}%</b> "
            f"({correct}/{total})"
        )

    if blocked:
        lines.append(f"🚫 Botni bloklaganlar: {blocked}")

    if idle:
        lines.append("")
        lines.append("💤 <b>Test ishlamayotganlar:</b>")
        for s in idle[:10]:
            days = s.days_idle()
            note = "hali test ishlamagan" if days is None else f"{days} kun oldin"
            lines.append(f"  • {esc(short(s.full_name, 32))} — {note}")
        if len(idle) > 10:
            lines.append(f"  … va yana {len(idle) - 10} ta")

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="👤 O'quvchilar", callback_data=f"ust:l:{gid}:0"),
            InlineKeyboardButton(text="🏆 Reyting", callback_data=f"ust:r:{gid}"),
        ],
        [InlineKeyboardButton(text="📣 Guruhga xabar", callback_data=f"ust:m:{gid}")],
        [InlineKeyboardButton(text="📋 Topshiriqlar", callback_data="adm:assign")],
        [InlineKeyboardButton(text="🎓 Milliy sertifikat imtihoni", callback_data="exm:t")],
        [InlineKeyboardButton(text="⬅️ Guruhlarim", callback_data="ust:home")],
    ])

    await show(c, "\n".join(lines), kb)
    await c.answer()


# ---------------------------------------------------------------
#  O'quvchilar ro'yxati
# ---------------------------------------------------------------
@router.callback_query(F.data.regexp(r"^ust:l:\d+:\d+$"))
async def cb_list(c: CallbackQuery):
    _, _, gid_s, page_s = c.data.split(":")
    gid, page = int(gid_s), int(page_s)

    async with SessionLocal() as session:
        ctx = await load_group(session, c.from_user.id, gid)
        if ctx is None:
            await c.answer("Bu guruhga huquqingiz yo'q.", show_alert=True)
            return
        group = ctx[2]
        students = await group_students(session, group.group_name)

    if not students:
        await c.answer("Guruhda o'quvchi yo'q.", show_alert=True)
        return

    last_page = (len(students) - 1) // PAGE_SIZE
    page = min(max(0, page), last_page)
    chunk = students[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]

    lines = [
        f"👤 <b>{esc(group.group_name)}</b> — o'quvchilar "
        f"({page + 1}/{last_page + 1})",
        "",
    ]
    buttons = []

    for index, s in enumerate(chunk, start=page * PAGE_SIZE + 1):
        mark = "💤" if s.is_inactive() else "✅"
        if s.is_blocked:
            mark = "🚫"
        if s.attempts:
            stat = f"{s.attempts} test · {s.accuracy}% · {fmt_datetime(s.last_attempt)}"
        else:
            stat = "test ishlamagan"
        lines.append(f"{index}. {mark} <b>{esc(short(s.full_name, 30))}</b>\n     {stat}")
        buttons.append([
            InlineKeyboardButton(
                text=f"{mark} {short(s.full_name, 40)}",
                callback_data=f"ust:s:{gid}:{s.telegram_id}",
            )
        ])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️ Oldingi", callback_data=f"ust:l:{gid}:{page - 1}"))
    if page < last_page:
        nav.append(InlineKeyboardButton(text="Keyingi ➡️", callback_data=f"ust:l:{gid}:{page + 1}"))
    if nav:
        buttons.append(nav)

    buttons.append([InlineKeyboardButton(text="⬅️ Guruhga qaytish", callback_data=f"ust:g:{gid}")])

    await show(c, "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=buttons))
    await c.answer()


# ---------------------------------------------------------------
#  Guruh reytingi
# ---------------------------------------------------------------
@router.callback_query(F.data.regexp(r"^ust:r:\d+$"))
async def cb_rating(c: CallbackQuery):
    gid = int(c.data.split(":")[2])

    async with SessionLocal() as session:
        ctx = await load_group(session, c.from_user.id, gid)
        if ctx is None:
            await c.answer("Bu guruhga huquqingiz yo'q.", show_alert=True)
            return
        group = ctx[2]
        students = await group_students(session, group.group_name)

    ranked = sorted(
        (s for s in students if s.attempts),
        key=lambda s: (s.accuracy, s.total),
        reverse=True,
    )

    lines = [f"🏆 <b>{esc(group.group_name)}</b> — reyting", ""]

    if not ranked:
        lines.append("Hali hech kim test ishlamagan.")
    else:
        medals = ["🥇", "🥈", "🥉"]
        for i, s in enumerate(ranked[:10]):
            prefix = medals[i] if i < 3 else f"{i + 1}."
            lines.append(
                f"{prefix} {esc(short(s.full_name, 30))} — <b>{s.accuracy}%</b> "
                f"({s.correct}/{s.total}, {s.attempts} test)"
            )
        lines.append("")
        lines.append("Reyting umumiy to'g'ri javoblar foiziga ko'ra tuzilgan.")

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Guruhga qaytish", callback_data=f"ust:g:{gid}")],
    ])
    await show(c, "\n".join(lines), kb)
    await c.answer()


# ---------------------------------------------------------------
#  O'quvchi kartasi
# ---------------------------------------------------------------
@router.callback_query(F.data.regexp(r"^ust:s:\d+:\d+$"))
async def cb_student(c: CallbackQuery):
    _, _, gid_s, tg_s = c.data.split(":")
    gid, tg_id = int(gid_s), int(tg_s)

    async with SessionLocal() as session:
        ctx = await load_group(session, c.from_user.id, gid)
        if ctx is None:
            await c.answer("Bu guruhga huquqingiz yo'q.", show_alert=True)
            return
        group = ctx[2]
        students = await group_students(session, group.group_name)
        member = next((s for s in students if s.telegram_id == tg_id), None)
        if member is None:
            await c.answer("Bu o'quvchi sizning guruhingizda emas.", show_alert=True)
            return
        report = await student_report(session, tg_id)

    if report is None:
        await c.answer("Ma'lumot topilmadi.", show_alert=True)
        return

    from .student_reports import card_text

    text = card_text(report)
    if member.username:
        text += "\n\n✉️ Telegram: @" + esc(member.username)
    if member.is_blocked:
        text += "\n🚫 Bu o'quvchi botni bloklagan."

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Ro'yxatga qaytish", callback_data=f"ust:l:{gid}:0")],
    ])
    await show(c, text[:4000], kb)
    await c.answer()


# ---------------------------------------------------------------
#  Guruhga xabar yuborish
# ---------------------------------------------------------------
@router.callback_query(F.data.regexp(r"^ust:m:\d+$"))
async def cb_message_start(c: CallbackQuery, state: FSMContext):
    gid = int(c.data.split(":")[2])

    async with SessionLocal() as session:
        ctx = await load_group(session, c.from_user.id, gid)
        if ctx is None:
            await c.answer("Bu guruhga huquqingiz yo'q.", show_alert=True)
            return
        group = ctx[2]

    await state.clear()
    await state.set_state(UstozState.message_text)
    await state.update_data(gid=gid)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Bekor qilish", callback_data=f"ust:g:{gid}")],
    ])
    await show(
        c,
        f"📣 <b>{esc(group.group_name)}</b> guruhiga xabar\n\n"
        f"Xabar matnini yozib yuboring (eng ko'pi {MAX_MESSAGE_LEN} belgi).\n"
        "Matn guruhdagi barcha o'quvchilarga sizning ismingiz bilan boradi.",
        kb,
    )
    await c.answer()


@router.message(UstozState.message_text)
async def msg_text(m: Message, state: FSMContext):
    data = await state.get_data()
    gid = data.get("gid")
    text = (m.text or "").strip()

    if not text:
        await m.answer("❗ Faqat matn yuboring.")
        return

    if len(text) > MAX_MESSAGE_LEN:
        await m.answer(
            f"❗ Matn juda uzun ({len(text)} belgi). "
            f"{MAX_MESSAGE_LEN} belgigacha qisqartiring."
        )
        return

    async with SessionLocal() as session:
        ctx = await load_group(session, m.from_user.id, gid or 0)
        if ctx is None:
            await state.clear()
            await m.answer("Bu guruhga huquqingiz yo'q.")
            return
        group = ctx[2]
        students = await group_students(session, group.group_name)

    recipients = [
        s for s in students
        if not s.is_blocked and s.telegram_id != m.from_user.id
    ]

    if not recipients:
        await state.clear()
        await m.answer("Guruhda xabar yuboriladigan o'quvchi yo'q.")
        return

    await state.update_data(text=text)
    await state.set_state(UstozState.message_confirm)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"✅ Yuborish ({len(recipients)} kishi)", callback_data="ust:mok")],
        [InlineKeyboardButton(text="❌ Bekor qilish", callback_data=f"ust:g:{gid}")],
    ])
    await m.answer(
        "📣 <b>Xabar ko'rinishi:</b>\n\n"
        f"{esc(text)}\n\n"
        f"Guruh: <b>{esc(group.group_name)}</b> · qabul qiluvchilar: <b>{len(recipients)}</b>",
        reply_markup=kb,
    )


@router.callback_query(UstozState.message_confirm, F.data == "ust:mok")
async def cb_message_send(c: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    gid = data.get("gid")
    text = data.get("text")
    await state.clear()  # ikki marta bosishdan himoya

    if not gid or not text:
        await c.answer("Xabar topilmadi.", show_alert=True)
        return

    async with SessionLocal() as session:
        ctx = await load_group(session, c.from_user.id, gid)
        if ctx is None:
            await c.answer("Bu guruhga huquqingiz yo'q.", show_alert=True)
            return
        _, user, group = ctx
        students = await group_students(session, group.group_name)
        teacher_name = user.full_name or "Ustoz"
        group_name = group.group_name

    recipients = [
        s.telegram_id for s in students
        if not s.is_blocked and s.telegram_id != c.from_user.id
    ]

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Guruhga qaytish", callback_data=f"ust:g:{gid}")],
    ])
    await show(c, f"⏳ Xabar {len(recipients)} kishiga yuborilmoqda…", kb)
    await c.answer()

    task = asyncio.create_task(
        _broadcast(
            c.bot,
            c.from_user.id,
            teacher_name,
            group_name,
            recipients,
            text,
        )
    )
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


async def _broadcast(bot, teacher_tg, teacher_name, group_name, recipients, text):
    body = (
        f"📣 <b>{esc(teacher_name)}</b> — ustozingizdan xabar:\n\n"
        f"{esc(text)}"
    )
    sent = blocked = failed = 0

    for chat_id in recipients:
        for attempt in range(2):
            try:
                await _tg_retry(lambda: bot.send_message(chat_id, body), tries=3)
                sent += 1
                break
            except TelegramRetryAfter as exc:
                await asyncio.sleep(min(exc.retry_after, 30) + 1)
            except TelegramForbiddenError:
                blocked += 1
                break
            except Exception:
                failed += 1
                break
        else:
            failed += 1

        await asyncio.sleep(SEND_PAUSE)

    summary = (
        f"✅ <b>Xabar yuborildi</b>\n\n"
        f"Guruh: <b>{esc(group_name)}</b>\n"
        f"Yetib bordi: <b>{sent}</b>"
    )
    if blocked:
        summary += f"\n🚫 Botni bloklagan: {blocked}"
    if failed:
        summary += f"\n⚠️ Yuborilmadi: {failed}"

    try:
        await _tg_retry(lambda: bot.send_message(teacher_tg, summary), tries=3)
    except Exception:
        log.exception("Ustozga hisobot yuborilmadi")

    try:
        from ..services.admin_ogoh import notify_admins

        await notify_admins(
            bot,
            f"📣 Ustoz <b>{esc(teacher_name)}</b> «{esc(group_name)}» guruhiga "
            f"xabar yubordi (yetdi: {sent}, jami: {len(recipients)}).",
            key=f"ustoz_xabar:{teacher_tg}",
            cooldown=60,
        )
    except Exception:
        pass


@router.callback_query(F.data == "ust:mok")
async def cb_message_stale(c: CallbackQuery):
    await c.answer("Bu xabar eskirgan. Qaytadan boshlang.", show_alert=True)

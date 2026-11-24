"""Admin paneli: ustozlarni qo'shish/olib tashlash va guruh biriktirish.

Joylashuvi: app/handlers/admin/oqituvchilar.py
"""

from __future__ import annotations

import html
import logging

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
from sqlalchemy import delete, or_, select

from ...db import SessionLocal
from ...models import Teacher, TeacherGroup, User
from ...services.oqituvchi import all_group_names, norm, teacher_groups
from ._common import is_admin

log = logging.getLogger(__name__)

router = Router()

PICK_PAGE = 8
GROUP_PAGE = 10


class AdminUstozState(StatesGroup):
    search = State()


def esc(value) -> str:
    return html.escape(str(value or ""))


def short(value: str, limit: int) -> str:
    value = (value or "").strip()
    return value if len(value) <= limit else value[: limit - 1].rstrip() + "…"


async def show(c: CallbackQuery, text: str, kb: InlineKeyboardMarkup):
    try:
        await c.message.edit_text(text, reply_markup=kb)
    except TelegramBadRequest as exc:
        if "not modified" in str(exc).lower():
            return
        await c.message.answer(text, reply_markup=kb)


# ---------------------------------------------------------------
#  Ustozlar ro'yxati
# ---------------------------------------------------------------
async def _list_view():
    async with SessionLocal() as session:
        rows = (
            await session.execute(
                select(Teacher, User)
                .join(User, User.id == Teacher.user_id)
                .order_by(User.full_name)
            )
        ).all()
        counts = {}
        for teacher, _ in rows:
            counts[teacher.id] = len(await teacher_groups(session, teacher.id))

    lines = ["🧑‍🏫 <b>Ustozlar</b>", ""]
    buttons = []

    if not rows:
        lines.append("Hali ustoz qo'shilmagan.")
    else:
        lines.append(
            "Ustoz faqat o'ziga biriktirilgan guruhlarning o'quvchilarini ko'radi "
            "va shu guruhlarga xabar yubora oladi."
        )
        for teacher, user in rows:
            buttons.append([
                InlineKeyboardButton(
                    text=f"🧑‍🏫 {short(user.full_name or 'Nomsiz', 34)} · {counts[teacher.id]} guruh",
                    callback_data=f"adm:ust:t:{teacher.id}",
                )
            ])

    buttons.append([
        InlineKeyboardButton(text="➕ Ustoz qo'shish", callback_data="adm:ust:pick:0")
    ])
    buttons.append([
        InlineKeyboardButton(text="⬅️ Admin panel", callback_data="admin")
    ])
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=buttons)


@router.callback_query(F.data == "adm:ust")
async def adm_list(c: CallbackQuery, state: FSMContext):
    if not is_admin(c.from_user.id):
        await c.answer("Faqat admin uchun.", show_alert=True)
        return
    await state.clear()
    text, kb = await _list_view()
    await show(c, text, kb)
    await c.answer()


# ---------------------------------------------------------------
#  Ustoz kartasi
# ---------------------------------------------------------------
async def _teacher_view(teacher_id: int):
    async with SessionLocal() as session:
        teacher = await session.get(Teacher, teacher_id)
        if teacher is None:
            return None
        user = await session.get(User, teacher.user_id)
        groups = await teacher_groups(session, teacher.id)
        counts = {norm(name): n for name, n in await all_group_names(session)}

    lines = [
        f"🧑‍🏫 <b>{esc(user.full_name)}</b>",
    ]
    if user.username:
        lines.append(f"✉️ @{esc(user.username)}")
    lines.append("")

    if groups:
        lines.append("Biriktirilgan guruhlar:")
        for g in groups:
            lines.append(
                f"• {esc(g.group_name)} — {counts.get(norm(g.group_name), 0)} o'quvchi"
            )
    else:
        lines.append("Hali guruh biriktirilmagan.")

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👥 Guruhlarni tanlash", callback_data=f"adm:ust:gr:{teacher.id}:0")],
        [InlineKeyboardButton(text="🗑 Ustozlikdan olish", callback_data=f"adm:ust:rm:{teacher.id}")],
        [InlineKeyboardButton(text="⬅️ Ustozlar", callback_data="adm:ust")],
    ])
    return "\n".join(lines), kb


@router.callback_query(F.data.regexp(r"^adm:ust:t:\d+$"))
async def adm_teacher(c: CallbackQuery):
    if not is_admin(c.from_user.id):
        await c.answer("Faqat admin uchun.", show_alert=True)
        return
    view = await _teacher_view(int(c.data.split(":")[3]))
    if view is None:
        await c.answer("Ustoz topilmadi.", show_alert=True)
        return
    await show(c, *view)
    await c.answer()


# ---------------------------------------------------------------
#  Ustoz qo'shish: o'quvchini tanlash
# ---------------------------------------------------------------
async def _pick_view(page: int, query: str = ""):
    async with SessionLocal() as session:
        stmt = (
            select(User)
            .outerjoin(Teacher, Teacher.user_id == User.id)
            .where(
                Teacher.id.is_(None),
                User.registration_status == "approved",
                User.is_blocked.is_(False),
            )
            .order_by(User.full_name)
        )
        if query:
            like = f"%{query}%"
            stmt = stmt.where(
                or_(User.full_name.ilike(like), User.username.ilike(like))
            )
        users = (await session.scalars(stmt)).all()

    if not users:
        text = "Mos foydalanuvchi topilmadi." if query else "Ustoz qilish mumkin bo'lgan foydalanuvchi yo'q."
        users_page, last = [], 0
    else:
        last = (len(users) - 1) // PICK_PAGE
        page = min(max(0, page), last)
        users_page = users[page * PICK_PAGE:(page + 1) * PICK_PAGE]
        text = (
            "➕ <b>Ustoz qo'shish</b>\n\n"
            "Kimni ustoz qilmoqchisiz? Tanlang"
            + (f"\n🔍 Qidiruv: <i>{esc(query)}</i>" if query else "")
            + f"\n({page + 1}/{last + 1})"
        )

    buttons = []
    for u in users_page:
        buttons.append([
            InlineKeyboardButton(
                text=f"{short(u.full_name or 'Nomsiz', 34)}"
                     + (f" · {short(u.group_name, 14)}" if u.group_name else ""),
                callback_data=f"adm:ust:new:{u.id}",
            )
        ])

    if not query:
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"adm:ust:pick:{page - 1}"))
        if page < last:
            nav.append(InlineKeyboardButton(text="➡️", callback_data=f"adm:ust:pick:{page + 1}"))
        if nav:
            buttons.append(nav)

    buttons.append([InlineKeyboardButton(text="🔍 Ism bo'yicha qidirish", callback_data="adm:ust:find")])
    buttons.append([InlineKeyboardButton(text="⬅️ Ustozlar", callback_data="adm:ust")])
    return text, InlineKeyboardMarkup(inline_keyboard=buttons)


@router.callback_query(F.data.regexp(r"^adm:ust:pick:\d+$"))
async def adm_pick(c: CallbackQuery, state: FSMContext):
    if not is_admin(c.from_user.id):
        await c.answer("Faqat admin uchun.", show_alert=True)
        return
    await state.clear()
    text, kb = await _pick_view(int(c.data.split(":")[3]))
    await show(c, text, kb)
    await c.answer()


@router.callback_query(F.data == "adm:ust:find")
async def adm_find(c: CallbackQuery, state: FSMContext):
    if not is_admin(c.from_user.id):
        await c.answer("Faqat admin uchun.", show_alert=True)
        return
    await state.set_state(AdminUstozState.search)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Bekor qilish", callback_data="adm:ust:pick:0")],
    ])
    await show(c, "🔍 Ustoz bo'ladigan odamning ismi yoki @username ini yozing:", kb)
    await c.answer()


@router.message(AdminUstozState.search)
async def adm_search(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return
    query = (m.text or "").strip().lstrip("@")[:50]
    if not query:
        await m.answer("❗ Ism yoki username yozing.")
        return
    await state.clear()
    text, kb = await _pick_view(0, query)
    await m.answer(text, reply_markup=kb)


@router.callback_query(F.data.regexp(r"^adm:ust:new:\d+$"))
async def adm_new(c: CallbackQuery):
    if not is_admin(c.from_user.id):
        await c.answer("Faqat admin uchun.", show_alert=True)
        return
    user_id = int(c.data.split(":")[3])

    async with SessionLocal() as session:
        user = await session.get(User, user_id)
        if user is None:
            await c.answer("Foydalanuvchi topilmadi.", show_alert=True)
            return
        teacher = await session.scalar(select(Teacher).where(Teacher.user_id == user_id))
        created = False
        if teacher is None:
            teacher = Teacher(user_id=user_id)
            session.add(teacher)
            await session.commit()
            created = True
        teacher_id = teacher.id
        tg_id = user.telegram_id

    if created:
        try:
            await c.bot.send_message(
                tg_id,
                "🧑‍🏫 <b>Siz ustoz etib tayinlandingiz!</b>\n\n"
                "Admin sizga guruh biriktirgach, bosh menyudagi "
                "«Ustoz paneli» tugmasidan o'quvchilaringizni ko'rishingiz mumkin.\n"
                "Bosh menyuga qaytish uchun /start ni yuboring.",
            )
        except Exception:
            pass

    view = await _teacher_view(teacher_id)
    await show(c, *view)
    await c.answer("Ustoz qo'shildi" if created else "Bu odam allaqachon ustoz")


# ---------------------------------------------------------------
#  Guruhlarni tanlash
# ---------------------------------------------------------------
async def _groups_view(teacher_id: int, page: int):
    async with SessionLocal() as session:
        teacher = await session.get(Teacher, teacher_id)
        if teacher is None:
            return None
        user = await session.get(User, teacher.user_id)
        mine = await teacher_groups(session, teacher.id)
        names = await all_group_names(session)

    chosen = {norm(g.group_name) for g in mine}
    known = {norm(name) for name, _ in names}

    # Biriktirilgan, lekin hozir o'quvchisi yo'q guruhlar ham ko'rinsin
    for g in mine:
        if norm(g.group_name) not in known:
            names.append((g.group_name, 0))
    names.sort(key=lambda item: norm(item[0]))

    if not names:
        return (
            "Hali birorta o'quvchi guruh nomini kiritmagan.",
            InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Ortga", callback_data=f"adm:ust:t:{teacher_id}")]
            ]),
        )

    last = (len(names) - 1) // GROUP_PAGE
    page = min(max(0, page), last)

    buttons = []
    for index in range(page * GROUP_PAGE, min(len(names), (page + 1) * GROUP_PAGE)):
        name, count = names[index]
        mark = "✅" if norm(name) in chosen else "▫️"
        buttons.append([
            InlineKeyboardButton(
                text=f"{mark} {short(name, 30)} ({count})",
                callback_data=f"adm:ust:tg:{teacher_id}:{index}",
            )
        ])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"adm:ust:gr:{teacher_id}:{page - 1}"))
    if page < last:
        nav.append(InlineKeyboardButton(text="➡️", callback_data=f"adm:ust:gr:{teacher_id}:{page + 1}"))
    if nav:
        buttons.append(nav)
    buttons.append([InlineKeyboardButton(text="✔️ Tayyor", callback_data=f"adm:ust:t:{teacher_id}")])

    text = (
        f"👥 <b>{esc(user.full_name)}</b> uchun guruhlar\n\n"
        "Guruhni bosing — biriktiriladi (✅) yoki olinadi (▫️).\n"
        "Qavs ichida — o'quvchilar soni."
        f"\n({page + 1}/{last + 1})"
    )
    return text, InlineKeyboardMarkup(inline_keyboard=buttons)


@router.callback_query(F.data.regexp(r"^adm:ust:gr:\d+:\d+$"))
async def adm_groups(c: CallbackQuery):
    if not is_admin(c.from_user.id):
        await c.answer("Faqat admin uchun.", show_alert=True)
        return
    _, _, _, tid, page = c.data.split(":")
    view = await _groups_view(int(tid), int(page))
    if view is None:
        await c.answer("Ustoz topilmadi.", show_alert=True)
        return
    await show(c, *view)
    await c.answer()


@router.callback_query(F.data.regexp(r"^adm:ust:tg:\d+:\d+$"))
async def adm_toggle(c: CallbackQuery):
    if not is_admin(c.from_user.id):
        await c.answer("Faqat admin uchun.", show_alert=True)
        return
    _, _, _, tid_s, idx_s = c.data.split(":")
    tid, idx = int(tid_s), int(idx_s)

    async with SessionLocal() as session:
        teacher = await session.get(Teacher, tid)
        if teacher is None:
            await c.answer("Ustoz topilmadi.", show_alert=True)
            return
        mine = await teacher_groups(session, tid)
        names = await all_group_names(session)
        known = {norm(name) for name, _ in names}
        for g in mine:
            if norm(g.group_name) not in known:
                names.append((g.group_name, 0))
        names.sort(key=lambda item: norm(item[0]))

        if idx < 0 or idx >= len(names):
            await c.answer("Ro'yxat o'zgargan, qaytadan oching.", show_alert=True)
            return

        name = names[idx][0]
        existing = [g for g in mine if norm(g.group_name) == norm(name)]

        if existing:
            for g in existing:
                await session.delete(g)
            note = "Guruh olib tashlandi"
        else:
            session.add(TeacherGroup(teacher_id=tid, group_name=name[:100]))
            note = "Guruh biriktirildi"
        await session.commit()

    view = await _groups_view(tid, idx // GROUP_PAGE)
    await show(c, *view)
    await c.answer(note)


# ---------------------------------------------------------------
#  Ustozlikdan olish
# ---------------------------------------------------------------
@router.callback_query(F.data.regexp(r"^adm:ust:rm:\d+$"))
async def adm_remove_ask(c: CallbackQuery):
    if not is_admin(c.from_user.id):
        await c.answer("Faqat admin uchun.", show_alert=True)
        return
    tid = int(c.data.split(":")[3])
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Ha, olib tashlash", callback_data=f"adm:ust:rmy:{tid}")],
        [InlineKeyboardButton(text="❌ Yo'q", callback_data=f"adm:ust:t:{tid}")],
    ])
    await show(
        c,
        "🗑 Bu odam ustozlikdan olinadi va guruhlari bog'lanishi o'chadi.\n"
        "O'quvchi sifatidagi ma'lumotlari saqlanib qoladi.\n\nDavom etamizmi?",
        kb,
    )
    await c.answer()


@router.callback_query(F.data.regexp(r"^adm:ust:rmy:\d+$"))
async def adm_remove(c: CallbackQuery):
    if not is_admin(c.from_user.id):
        await c.answer("Faqat admin uchun.", show_alert=True)
        return
    tid = int(c.data.split(":")[3])

    async with SessionLocal() as session:
        await session.execute(delete(TeacherGroup).where(TeacherGroup.teacher_id == tid))
        await session.execute(delete(Teacher).where(Teacher.id == tid))
        await session.commit()

    text, kb = await _list_view()
    await show(c, text, kb)
    await c.answer("Ustozlikdan olindi")

"""Ro'yxatdan o'tish (yangi): telefonsiz, guruh ro'yxati, imtihon maqsadi, admin uchun tekshiruv va rad etish shablonlari.

Joylashuvi: app/handlers/royxat.py
start.py oxirida setup(router) bilan ulanadi (start.router dispatcherda birinchi turadi).

O'quvchi uchun bosqichlar: 1) ism  2) familiya  3) guruh (ro'yxatdan yoki o'zi yozadi)  4) qaysi imtihon uchun.
Admin buyruqlari: /guruhlar, /guruh_qosh, /guruh_ochir, /ajrat.
Bazaga o'zgarish (o'zi bajariladi, qayta ishga tushsa ham zararsiz):
  reg_groups jadvali, users.exam_goal ustuni, users.phone_number hammasi NULL qilinadi (telefonlar o'chiriladi).
"""

from __future__ import annotations

import asyncio
import html
import logging
import re

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    ReplyKeyboardRemove,
)
from sqlalchemy import or_, select, text

from ..config import settings
from ..db import SessionLocal, engine
from ..models import User
from ..tg_retry import retry as _tg_retry

log = logging.getLogger(__name__)


class Royxat(StatesGroup):
    ism = State()
    familiya = State()
    guruh = State()
    guruh_qolda = State()
    maqsad = State()
    maqsad_qolda = State()


class AdminRoyxat(StatesGroup):
    sabab = State()


# ---------------------------------------------------------------
#  Baza: jadval va ustunlar (bir marta, avtomatik)
# ---------------------------------------------------------------
_schema_ok = False
_schema_lock = asyncio.Lock()


async def ensure_schema() -> None:
    global _schema_ok
    if _schema_ok:
        return
    async with _schema_lock:
        if _schema_ok:
            return
        pg = engine.dialect.name == "postgresql"
        async with engine.begin() as conn:
            if pg:
                await conn.execute(text(
                    "CREATE TABLE IF NOT EXISTS reg_groups ("
                    "id SERIAL PRIMARY KEY, name VARCHAR(100) NOT NULL UNIQUE, "
                    "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
                ))
                await conn.execute(text(
                    "ALTER TABLE users ADD COLUMN IF NOT EXISTS exam_goal VARCHAR(200)"
                ))
            else:
                await conn.execute(text(
                    "CREATE TABLE IF NOT EXISTS reg_groups ("
                    "id INTEGER PRIMARY KEY AUTOINCREMENT, name VARCHAR(100) NOT NULL UNIQUE, "
                    "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
                ))
                cols = await conn.execute(text("PRAGMA table_info(users)"))
                if "exam_goal" not in {r[1] for r in cols.fetchall()}:
                    await conn.execute(text("ALTER TABLE users ADD COLUMN exam_goal VARCHAR(200)"))
            # Telefon raqamlar endi saqlanmaydi: borlari ham o'chiriladi
            await conn.execute(text(
                "UPDATE users SET phone_number = NULL WHERE phone_number IS NOT NULL"
            ))
        _schema_ok = True


async def _groups() -> list[tuple[int, str]]:
    await ensure_schema()
    async with engine.connect() as conn:
        rows = await conn.execute(text("SELECT id, name FROM reg_groups ORDER BY name"))
        return [(int(r[0]), str(r[1])) for r in rows.fetchall()]


async def _group_by_id(gid: int) -> str | None:
    await ensure_schema()
    async with engine.connect() as conn:
        row = (await conn.execute(text("SELECT name FROM reg_groups WHERE id = :i"), {"i": gid})).first()
        return str(row[0]) if row else None


def _gkey(name: str) -> str:
    """'175-guruh', '175 guruh', '175' -> '175' (taqqoslash uchun)."""
    s = (name or "").lower()
    s = re.sub(r"guruh(i)?", "", s)
    return re.sub(r"[^0-9a-zа-яёʻʼ'`‘’]", "", s)


async def _match_group(typed: str) -> str | None:
    key = _gkey(typed)
    if not key:
        return None
    for _gid, name in await _groups():
        if _gkey(name) == key:
            return name
    return None


async def _set_exam_goal(telegram_id: int, goal: str | None) -> None:
    await ensure_schema()
    async with engine.begin() as conn:
        await conn.execute(
            text("UPDATE users SET exam_goal = :g WHERE telegram_id = :t"),
            {"g": goal, "t": telegram_id},
        )


async def _get_exam_goal(telegram_id: int) -> str | None:
    await ensure_schema()
    async with engine.connect() as conn:
        row = (await conn.execute(
            text("SELECT exam_goal FROM users WHERE telegram_id = :t"), {"t": telegram_id}
        )).first()
        return row[0] if row else None


# ---------------------------------------------------------------
#  Tekshiruvlar
# ---------------------------------------------------------------
APOS = "'`‘’ʻʼ"
JUNK = {"-", "—", "–", "_", ".", "yo'q", "yo‘q", "yoq", "none", "null", "n/a", "na", "ism", "familiya", "bilmayman"}


def _clean_name(raw: str) -> tuple[str | None, str]:
    """(to'g'rilangan ism yoki None, xato sababi)."""
    s = " ".join((raw or "").split())
    if not s or s.startswith("/"):
        return None, "bo'sh"
    if s.lower() in JUNK:
        return None, "haqiqiy emas"
    if any(ch.isdigit() for ch in s):
        return None, "raqam bor"
    for ch in s:
        if not (ch.isalpha() or ch in APOS or ch in " -"):
            return None, "belgi bor"
    letters = sum(ch.isalpha() for ch in s)
    if letters < 2:
        return None, "juda qisqa"
    if len(s) > 30 or len(s.split()) > 2:
        return None, "juda uzun"
    parts = []
    for word in s.split(" "):
        sub = [w[:1].upper() + w[1:].lower() if w else w for w in word.split("-")]
        parts.append("-".join(sub))
    return " ".join(parts), ""


def _mixed_script(s: str) -> bool:
    lat = any("a" <= ch.lower() <= "z" for ch in s)
    cyr = any("а" <= ch.lower() <= "я" or ch.lower() in "ёўқғҳ" for ch in s)
    return lat and cyr


NAME_HELP = (
    "Faqat harflar bo'lsin: raqam, emoji va belgilar bo'lmasin. Masalan: <b>{ex}</b>.\n\n"
    "🔒 Ustoz sizni tanib olishi uchun to'liq ma'lumotlaringiz kerak. "
    "Ismingiz va natijalaringizni reytingda boshqa o'quvchilar ko'ra olmaydi."
)


def _is_admin(uid: int) -> bool:
    return uid in settings.admins


# ---------------------------------------------------------------
#  Klaviaturalar
# ---------------------------------------------------------------
GOALS = {
    "ms": "🎓 Milliy sertifikat",
    "mf": "📘 Majburiy fanlar",
    "ki": "👨‍🏫 Kasb imtihoni",
}


def _goal_kb() -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=t, callback_data=f"rg:m:{k}")] for k, t in GOALS.items()]
    rows.append([InlineKeyboardButton(text="✍️ Boshqa — o'zim yozaman", callback_data="rg:m:own")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _groups_kb(groups, prefix: str, extra=None) -> InlineKeyboardMarkup:
    rows, row = [], []
    for gid, name in groups[:60]:
        row.append(InlineKeyboardButton(text=name[:30], callback_data=f"{prefix}{gid}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    if extra:
        rows.extend(extra)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _again_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🔄 Qayta ro'yxatdan o'tish", callback_data="rg:again")
    ]])


REJECT = {
    "ism": "Ismingiz noto'g'ri yoki to'liq yozilmagan ko'rinadi.",
    "fam": "Familiyangiz kiritilmagan yoki xato yozilgan ko'rinadi.",
    "alm": "Ism va familiyangiz o'rni almashib qolgan ko'rinadi: avval ism, keyin familiya yoziladi.",
    "gur": "Guruhingiz noto'g'ri ko'rsatilgan.",
}
REJECT_BTN = {
    "ism": "❌ Ism xato",
    "fam": "❌ Familiya xato",
    "alm": "❌ O'rni almashgan",
    "gur": "❌ Guruh xato",
}
REJECT_TAIL = (
    "\n\nUstoz sizni tanib olishi uchun to'liq ma'lumotlaringiz kerak. "
    "Ismingiz va natijalaringizni reytingda boshqa o'quvchilar ko'ra olmaydi.\n\n"
    "Iltimos, ma'lumotlaringizni to'g'rilab, qaytadan ro'yxatdan o'ting 👇"
)


def _admin_kb(tid: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Tasdiqlash", callback_data=f"adm:approve_user:{tid}")],
        [InlineKeyboardButton(text="👥 Guruhga biriktirish", callback_data=f"rg:sg:{tid}")],
        [InlineKeyboardButton(text=REJECT_BTN["ism"], callback_data=f"rg:r:{tid}:ism"),
         InlineKeyboardButton(text=REJECT_BTN["fam"], callback_data=f"rg:r:{tid}:fam")],
        [InlineKeyboardButton(text=REJECT_BTN["alm"], callback_data=f"rg:r:{tid}:alm"),
         InlineKeyboardButton(text=REJECT_BTN["gur"], callback_data=f"rg:r:{tid}:gur")],
        [InlineKeyboardButton(text="✍️ O'z so'zim bilan rad etish", callback_data=f"rg:rc:{tid}")],
    ])


# ---------------------------------------------------------------
#  O'quvchi: ro'yxatdan o'tish
# ---------------------------------------------------------------
async def begin(message: Message, state: FSMContext) -> None:
    await ensure_schema()
    await state.clear()
    await state.set_state(Royxat.ism)
    await message.answer(
        "🎓 <b>Doniyor Academy</b>\n\n"
        "Botdan foydalanish uchun ro'yxatdan o'ting. Bu 4 ta qisqa qadam: "
        "ism, familiya, guruh va maqsad.\n\n"
        "🔒 Ustoz sizni tanib olishi uchun to'liq ma'lumotlaringiz kerak. "
        "Ismingiz va natijalaringizni reytingda boshqa o'quvchilar ko'ra olmaydi. Telefon raqamingiz so'ralmaydi.\n\n"
        "1️⃣ <b>Ismingizni</b> kiriting (masalan: <b>Sardor</b>):",
        reply_markup=ReplyKeyboardRemove(),
    )


async def on_ism(message: Message, state: FSMContext):
    name, why = _clean_name(message.text or "")
    if not name:
        await message.answer("❗ <b>Ismingizni kiritishda xatolik bor.</b>\n" + NAME_HELP.format(ex="Sardor"))
        return
    await state.update_data(first_name=name)
    await state.set_state(Royxat.familiya)
    await message.answer(f"Rahmat, <b>{html.escape(name)}</b>!\n\n2️⃣ <b>Familiyangizni</b> kiriting (masalan: <b>Aliyev</b>):")


async def on_familiya(message: Message, state: FSMContext):
    last, why = _clean_name(message.text or "")
    data = await state.get_data()
    if not last:
        await message.answer("❗ <b>Familiyangizni kiritishda xatolik bor.</b>\n" + NAME_HELP.format(ex="Aliyev"))
        return
    if last.lower() == (data.get("first_name") or "").lower():
        await message.answer(
            "❗ Familiya ism bilan bir xil bo'lmasligi kerak.\n"
            "Iltimos, <b>familiyangizni</b> kiriting (masalan: <b>Aliyev</b>):"
        )
        return
    await state.update_data(last_name=last)
    await _ask_group(message, state)


async def _ask_group(message: Message, state: FSMContext):
    groups = await _groups()
    if not groups:
        await state.set_state(Royxat.guruh_qolda)
        await message.answer("3️⃣ <b>Guruhingizni</b> yozing (masalan: <b>175-guruh</b>):")
        return
    await state.set_state(Royxat.guruh)
    kb = _groups_kb(groups, "rg:g:", extra=[[InlineKeyboardButton(
        text="✍️ Ro'yxatda yo'q — o'zim yozaman", callback_data="rg:gq")]])
    await message.answer("3️⃣ <b>Guruhingizni</b> tanlang:", reply_markup=kb)


async def on_group_pick(c: CallbackQuery, state: FSMContext):
    name = await _group_by_id(int(c.data.split(":")[2]))
    if not name:
        await c.answer("Bu guruh o'chirilgan, boshqasini tanlang.", show_alert=True)
        await _ask_group(c.message, state)
        return
    await state.update_data(group_name=name, group_custom=False)
    await c.answer()
    await c.message.edit_text(f"3️⃣ Guruh: <b>{html.escape(name)}</b>")
    await _ask_goal(c.message, state)


async def on_group_own(c: CallbackQuery, state: FSMContext):
    await state.set_state(Royxat.guruh_qolda)
    await c.answer()
    await c.message.edit_text("3️⃣ <b>Guruhingizni</b> yozing (masalan: <b>175-guruh</b>):")


async def on_group_typed(message: Message, state: FSMContext):
    raw = " ".join((message.text or "").split())
    if not raw or raw.startswith("/") or raw.lower() in JUNK or len(raw) > 40:
        await message.answer("❗ Guruh nomini to'g'ri yozing (masalan: <b>175-guruh</b>). 40 belgidan oshmasin.")
        return
    listed = await _match_group(raw)
    await state.update_data(group_name=listed or raw, group_custom=listed is None)
    await _ask_goal(message, state)


async def _ask_goal(message: Message, state: FSMContext):
    await state.set_state(Royxat.maqsad)
    await message.answer("4️⃣ <b>Tarix darslari sizga qaysi imtihon uchun kerak?</b>", reply_markup=_goal_kb())


async def on_goal_pick(c: CallbackQuery, state: FSMContext):
    key = c.data.split(":")[2]
    await c.answer()
    if key == "own":
        await state.set_state(Royxat.maqsad_qolda)
        await c.message.edit_text("4️⃣ Qaysi imtihon uchun kerakligini qisqa yozing:")
        return
    goal = GOALS.get(key)
    if not goal:
        return
    await c.message.edit_text(f"4️⃣ Maqsad: <b>{goal}</b>")
    await _finish(c.message, state, c.from_user, goal.split(" ", 1)[1])


async def on_goal_typed(message: Message, state: FSMContext):
    goal = " ".join((message.text or "").split())
    if len(goal) < 3 or goal.startswith("/") or len(goal) > 100:
        await message.answer("❗ Javobni qisqa va tushunarli yozing (3–100 belgi).")
        return
    await _finish(message, state, message.from_user, goal)


def _warnings(first: str, last: str, group: str, custom: bool, tg_name: str) -> list[str]:
    w = []
    if custom:
        w.append("guruh ro'yxatda yo'q, o'quvchi o'zi yozgan")
    if len(first) <= 2 or len(last) <= 2:
        w.append("ism yoki familiya juda qisqa")
    if _mixed_script(first + last):
        w.append("lotin va kirill harflari aralash")
    tg = (tg_name or "").lower()
    if tg and first.lower() not in tg and last.lower() not in tg:
        w.append("Telegramdagi ism bilan mos emas")
    if last.lower().endswith(("ovich", "ovna", "evich", "evna")) or first.lower().endswith(("ov", "ev", "ova", "eva")):
        w.append("ism va familiya o'rni almashgan bo'lishi mumkin")
    return w


async def _finish(message: Message, state: FSMContext, tg_user, goal: str):
    data = await state.get_data()
    first, last, group = data.get("first_name"), data.get("last_name"), data.get("group_name")
    custom = bool(data.get("group_custom"))
    if not (first and last and group):
        await state.clear()
        await message.answer("❗ Ma'lumotlar to'liq saqlanmadi. Iltimos, /start ni qayta bosing.")
        return
    full_name = f"{first} {last}"
    async with SessionLocal() as s:
        user = await s.scalar(select(User).where(User.telegram_id == tg_user.id))
        if user is None:
            user = User(telegram_id=tg_user.id, username=tg_user.username, full_name=full_name,
                        phone_number=None, group_name=group, registration_status="pending", is_blocked=False)
            s.add(user)
        else:
            user.username = tg_user.username
            user.full_name = full_name
            user.phone_number = None
            user.group_name = group
            user.registration_status = "pending"
        await s.commit()
    await _set_exam_goal(tg_user.id, goal)
    await state.clear()

    e = html.escape
    warn = _warnings(first, last, group, custom, tg_user.full_name or "")
    uname = f"@{e(tg_user.username)}" if tg_user.username else "yo'q"
    admin_text = (
        "🔔 <b>Yangi ro'yxatdan o'tish arizasi</b>\n\n"
        f"👤 Ism: <b>{e(first)}</b>\n"
        f"👤 Familiya: <b>{e(last)}</b>\n"
        f"👥 Guruh: <b>{e(group)}</b>{' ✍️ (o`zi yozgan)' if custom else ''}\n"
        f"🎯 Maqsad: <b>{e(goal)}</b>\n"
        f"💬 Telegramdagi ismi: {e(tg_user.full_name or '-')}\n"
        f"🆔 ID: <code>{tg_user.id}</code> · {uname}\n\n"
        "⚠️ <b>Tasdiqlashdan oldin tekshiring: ro'yxatdan o'tishda xatolik bormi?</b>\n"
        "• ism va familiya haqiqiy va to'liqmi, o'rni almashmaganmi\n"
        "• guruh to'g'rimi\n"
    )
    if warn:
        admin_text += "\n🔎 <b>Bot sezgan shubhalar:</b>\n" + "\n".join(f"• {e(x)}" for x in warn) + "\n"
    admin_text += "\nXato bo'lsa, tayyor shablon yoki o'z so'zingiz bilan rad eting: o'quvchiga qayta ro'yxatdan o'tish taklifi boradi."
    for admin_id in settings.admins:
        try:
            await _tg_retry(lambda: message.bot.send_message(admin_id, admin_text, reply_markup=_admin_kb(tg_user.id)), tries=3)
        except Exception:
            pass

    await message.answer(
        "✅ <b>Ro'yxatdan o'tish yakunlandi.</b>\n\n"
        f"👤 Ism-familiya: <b>{e(full_name)}</b>\n"
        f"👥 Guruh: <b>{e(group)}</b>\n"
        f"🎯 Maqsad: <b>{e(goal)}</b>\n\n"
        "⏳ Arizangiz ustozga yuborildi. Tasdiqlangach, xabar keladi.\n"
        "Xato kiritgan bo'lsangiz, pastdagi tugma bilan qayta kiriting.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="✏️ Ma'lumotni tuzatish", callback_data="rg:again")]]),
    )


async def on_again(c: CallbackQuery, state: FSMContext):
    async with SessionLocal() as s:
        user = await s.scalar(select(User).where(User.telegram_id == c.from_user.id))
    if user is not None and (user.is_blocked or user.registration_status == "approved"):
        await c.answer("Hisobingiz allaqachon tasdiqlangan yoki bloklangan.", show_alert=True)
        return
    await c.answer()
    try:
        await c.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await begin(c.message, state)


# ---------------------------------------------------------------
#  Admin: rad etish (shablon yoki o'z so'zi) va guruhga biriktirish
# ---------------------------------------------------------------
def card_buttons(tid: int, target_is_admin: bool) -> list:
    """Admin paneldagi foydalanuvchi kartasi uchun: guruhga biriktirish va rad etish shablonlari."""
    if target_is_admin:
        return []
    return [
        [InlineKeyboardButton(text="👥 Guruhga biriktirish", callback_data=f"rg:sg:{tid}")],
        [InlineKeyboardButton(text=REJECT_BTN["ism"], callback_data=f"rg:R:{tid}:ism"),
         InlineKeyboardButton(text=REJECT_BTN["fam"], callback_data=f"rg:R:{tid}:fam")],
        [InlineKeyboardButton(text=REJECT_BTN["alm"], callback_data=f"rg:R:{tid}:alm"),
         InlineKeyboardButton(text=REJECT_BTN["gur"], callback_data=f"rg:R:{tid}:gur")],
        [InlineKeyboardButton(text="✍️ O'z so'zim bilan rad etish", callback_data=f"rg:RC:{tid}")],
    ]


async def _reject(bot, tid: int, reason: str, force: bool = False) -> tuple[bool, str]:
    """force=True: admin paneldagi kartadan (tasdiqlangan o'quvchini ham rad etish mumkin, eski «Rad etish» kabi)."""
    async with SessionLocal() as s:
        user = await s.scalar(select(User).where(User.telegram_id == tid))
        if user is None:
            return False, "Foydalanuvchi topilmadi."
        if user.registration_status == "approved" and not force:
            return False, "Bu o'quvchi allaqachon tasdiqlangan. Kerak bo'lsa, admin paneldagi kartasidan rad eting."
        user.registration_status = "rejected"
        name = user.full_name or "o'quvchi"
        await s.commit()
    try:
        await _tg_retry(lambda: bot.send_message(
            tid, "⚠️ <b>Ro'yxatdan o'tishda xatolik bor</b>\n\n" + reason + REJECT_TAIL,
            reply_markup=_again_kb()), tries=3)
        sent = "O'quvchiga xabar yuborildi."
    except Exception:
        sent = "O'quvchiga xabar yetmadi (botni bloklagan bo'lishi mumkin)."
    return True, f"{name}: rad etildi. {sent}"


async def on_reject_tpl(c: CallbackQuery):
    if not _is_admin(c.from_user.id):
        return await c.answer()
    _, kind, tid, code = c.data.split(":")
    reason = REJECT.get(code)
    if not reason:
        return await c.answer()
    ok, msg = await _reject(c.bot, int(tid), reason, force=(kind == "R"))
    await c.answer(msg, show_alert=not ok)
    if ok:
        try:
            await c.message.edit_text(c.message.html_text + f"\n\n❌ <b>Rad etildi:</b> {html.escape(reason)}")
        except Exception:
            pass


async def on_reject_custom(c: CallbackQuery, state: FSMContext):
    if not _is_admin(c.from_user.id):
        return await c.answer()
    tid = int(c.data.split(":")[2])
    await state.set_state(AdminRoyxat.sabab)
    await state.update_data(rg_tid=tid, rg_force=c.data.startswith("rg:RC:"))
    await c.answer()
    await c.message.answer(
        "✍️ O'quvchiga yuboriladigan sababni yozing (masalan: «Familiyangizni to'liq yozing»).\n"
        "O'quvchiga xabar oxirida qayta ro'yxatdan o'tish tugmasi qo'shiladi.\n\n"
        "Bekor qilish: <b>bekor</b>"
    )


async def on_reject_custom_text(message: Message, state: FSMContext):
    if not _is_admin(message.from_user.id):
        return
    txt = (message.text or "").strip()
    data = await state.get_data()
    await state.clear()
    if not txt or txt.startswith("/") or txt.lower() == "bekor":
        await message.answer("Rad etish bekor qilindi. Arizadagi tugmalar ishlashda davom etadi.")
        return
    tid = data.get("rg_tid")
    if not tid:
        await message.answer("Qaysi o'quvchi ekani topilmadi. Arizadagi tugmani qayta bosing.")
        return
    ok, msg = await _reject(message.bot, int(tid), "Ustozingiz izohi: «" + html.escape(txt[:500]) + "»",
                            force=bool(data.get("rg_force")))
    await message.answer(("❌ " if ok else "⚠️ ") + msg)


async def on_set_group_menu(c: CallbackQuery):
    if not _is_admin(c.from_user.id):
        return await c.answer()
    tid = int(c.data.split(":")[2])
    groups = await _groups()
    if not groups:
        await c.answer("Avval guruh qo'shing: /guruh_qosh 175-guruh", show_alert=True)
        return
    await c.answer()
    await c.message.answer(
        "👥 Qaysi guruhga biriktiramiz?",
        reply_markup=_groups_kb(groups, f"rg:ss:{tid}:"),
    )


async def on_set_group(c: CallbackQuery):
    if not _is_admin(c.from_user.id):
        return await c.answer()
    _, _, tid, gid = c.data.split(":")
    name = await _group_by_id(int(gid))
    if not name:
        return await c.answer("Guruh topilmadi.", show_alert=True)
    async with SessionLocal() as s:
        user = await s.scalar(select(User).where(User.telegram_id == int(tid)))
        if user is None:
            return await c.answer("O'quvchi topilmadi.", show_alert=True)
        old = user.group_name or "-"
        user.group_name = name
        uname = user.full_name or "o'quvchi"
        await s.commit()
    await c.answer("Saqlandi")
    await c.message.edit_text(
        f"✅ <b>{html.escape(uname)}</b>: guruh <b>{html.escape(old)}</b> → <b>{html.escape(name)}</b>"
    )


# ---------------------------------------------------------------
#  Admin: guruhlar paneli (/guruhlar)
# ---------------------------------------------------------------
PAGE = 8


class AdminGuruh(StatesGroup):
    nom = State()
    yangi_nom = State()


def _kb(rows) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _say(target, text_value: str, markup, edit: bool):
    if edit:
        try:
            await target.edit_text(text_value, reply_markup=markup)
            return
        except Exception:
            pass
    await target.answer(text_value, reply_markup=markup)


async def _user_groups() -> list[tuple[str, int]]:
    """O'quvchilar guruhi bo'yicha soni (adminlar va bloklanganlar hisobga olinmaydi)."""
    await ensure_schema()
    async with SessionLocal() as s:
        users = (await s.scalars(select(User).where(
            User.registration_status.in_(("approved", "pending")),
            or_(User.is_blocked.is_(False), User.is_blocked.is_(None)),
        ))).all()
    out: dict[str, int] = {}
    for u in users:
        if u.telegram_id in settings.admins:
            continue
        g = (u.group_name or "").strip()
        out[g] = out.get(g, 0) + 1
    return sorted(out.items(), key=lambda kv: (-kv[1], kv[0]))


async def _add_groups(names: list[str]) -> tuple[list[str], list[str], int]:
    """Qo'shadi va o'quvchilar o'zi yozgan o'xshash nomlarni shu guruhga o'tkazadi (175 guruh -> 175-guruh)."""
    await ensure_schema()
    existing = {_gkey(n) for _g, n in await _groups()}
    added, existed, moved = [], [], 0
    async with engine.begin() as conn:
        typed = [r[0] for r in (await conn.execute(text(
            "SELECT DISTINCT group_name FROM users WHERE group_name IS NOT NULL"))).fetchall()]
        for n in names:
            k = _gkey(n)
            if not k:
                continue
            if k in existing:
                existed.append(n)
                continue
            await conn.execute(text("INSERT INTO reg_groups (name) VALUES (:n)"), {"n": n})
            existing.add(k)
            added.append(n)
            for old in typed:
                if old != n and _gkey(old) == k:
                    r = await conn.execute(text("UPDATE users SET group_name = :n WHERE group_name = :o"),
                                           {"n": n, "o": old})
                    moved += r.rowcount or 0
    return added, existed, moved


def _split_names(raw: str) -> list[str]:
    names = [" ".join(x.split()) for x in (raw or "").replace("\n", ",").split(",")]
    return [n for n in names if n and len(n) <= 40 and not n.startswith("/")]


async def goal_counts() -> list[tuple[str, int]]:
    """Imtihon maqsadi bo'yicha o'quvchilar soni (tasdiqlangan va kutayotganlar, adminlarsiz)."""
    await ensure_schema()
    async with engine.connect() as conn:
        rows = (await conn.execute(text(
            "SELECT telegram_id, exam_goal FROM users "
            "WHERE registration_status IN ('approved', 'pending') "
            "AND (is_blocked IS NULL OR is_blocked = :f)"
        ), {"f": False})).fetchall()
    out: dict[str, int] = {}
    for tid, goal in rows:
        if tid in settings.admins:
            continue
        g = (goal or "").strip() or "Ko'rsatilmagan"
        out[g] = out.get(g, 0) + 1
    return sorted(out.items(), key=lambda kv: (kv[0] == "Ko'rsatilmagan", -kv[1], kv[0]))


async def _render_home(target, state: FSMContext, edit: bool = False):
    groups = await _groups()
    counts = dict(await _user_groups())
    listed = {n for _g, n in groups}
    custom = [(n, c) for n, c in counts.items() if n and n not in listed]
    nogroup = sum(c for n, c in counts.items() if n not in listed)
    await state.update_data(rgp_custom=[n for n, _c in custom[:10]])
    rows, row = [], []
    for gid, name in groups:
        row.append(InlineKeyboardButton(text=f"{name[:22]} ({counts.get(name, 0)})", callback_data=f"rgp:v:{gid}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton(text="➕ Guruh qo'shish", callback_data="rgp:add")])
    for i, (n, c) in enumerate(custom[:10]):
        rows.append([InlineKeyboardButton(text=f"➕ «{n[:20]}» ({c}) ro'yxatga qo'shish", callback_data=f"rgp:ac:{i}")])
    if groups and nogroup:
        rows.append([InlineKeyboardButton(text=f"👥 Guruhga ajratilmaganlar: {nogroup} ta", callback_data="rgp:none")])
    lines = ["👥 <b>Guruhlar</b>", ""]
    if groups:
        lines.append("Guruhni bosing: o'quvchilarini ko'rasiz, yangi o'quvchi qo'shasiz, nomini o'zgartirasiz.")
    else:
        lines.append("Ro'yxat hali bo'sh. Guruh qo'shsangiz, o'quvchilar ro'yxatdan o'tishda uni tugma bilan tanlaydi.")
    if custom:
        lines += ["", "✍️ <b>O'quvchilar o'zi yozgan, ro'yxatda yo'q guruhlar:</b>"]
        lines += [f"• {html.escape(n)} — {c} ta" for n, c in custom[:10]]
        lines.append("Bitta tugma bilan ro'yxatga qo'shasiz; o'xshash yozilganlar (175 guruh, 175-guruh) bitta guruhga birlashadi.")
    goals = await goal_counts()
    if goals:
        lines += ["", "🎯 <b>Tarix qaysi imtihon uchun kerak:</b>"]
        lines += [f"• {html.escape(g)} — {n} ta" for g, n in goals[:12]]
    await _say(target, "\n".join(lines), _kb(rows), edit)


async def cmd_groups(message: Message, state: FSMContext):
    if not _is_admin(message.from_user.id):
        return
    await state.clear()
    await _render_home(message, state)


async def cb_home(c: CallbackQuery, state: FSMContext):
    if not _is_admin(c.from_user.id):
        return await c.answer()
    await c.answer()
    await state.set_state(None)
    await _render_home(c.message, state, edit=True)


async def cb_add(c: CallbackQuery, state: FSMContext):
    if not _is_admin(c.from_user.id):
        return await c.answer()
    await c.answer()
    await state.set_state(AdminGuruh.nom)
    await c.message.answer("✍️ Guruh nomini yozing. Bir nechta bo'lsa, vergul bilan: <b>175-guruh, 176-guruh</b>\n"
                           "Bekor qilish: <b>bekor</b>")


async def on_new_names(message: Message, state: FSMContext):
    if not _is_admin(message.from_user.id):
        return
    raw = (message.text or "").strip()
    await state.set_state(None)
    if not raw or raw.startswith("/") or raw.lower() == "bekor":
        await message.answer("Bekor qilindi.")
        return
    added, existed, moved = await _add_groups(_split_names(raw))
    await message.answer(_add_report(added, existed, moved))
    await _render_home(message, state)


def _add_report(added, existed, moved) -> str:
    out = []
    if added:
        out.append("✅ Qo'shildi: " + ", ".join(html.escape(x) for x in added))
    if existed:
        out.append("ℹ️ Allaqachon bor: " + ", ".join(html.escape(x) for x in existed))
    if moved:
        out.append(f"🔀 O'xshash yozilgan {moved} ta o'quvchi shu guruhga o'tkazildi.")
    return "\n".join(out) or "Hech narsa qo'shilmadi."


async def cb_add_custom(c: CallbackQuery, state: FSMContext):
    if not _is_admin(c.from_user.id):
        return await c.answer()
    names = (await state.get_data()).get("rgp_custom") or []
    i = int(c.data.split(":")[2])
    if i >= len(names):
        return await c.answer("Ro'yxat yangilandi, qaytadan bosing.", show_alert=True)
    added, existed, moved = await _add_groups([names[i]])
    await c.answer("Qo'shildi" if added else "Allaqachon bor")
    await _render_home(c.message, state, edit=True)


async def _group_users(name: str) -> list:
    async with SessionLocal() as s:
        users = (await s.scalars(select(User).where(User.group_name == name).order_by(User.full_name))).all()
    return [u for u in users if u.telegram_id not in settings.admins]


async def cb_view(c: CallbackQuery, state: FSMContext, gid: int | None = None):
    if not _is_admin(c.from_user.id):
        return await c.answer()
    gid = gid if gid is not None else int(c.data.split(":")[2])
    name = await _group_by_id(gid)
    if not name:
        try:
            await c.answer("Guruh topilmadi.", show_alert=True)
        except Exception:
            pass
        return await _render_home(c.message, state, edit=True)
    try:
        await c.answer()
    except Exception:
        pass
    await state.set_state(None)
    users = await _group_users(name)
    icon = {"approved": "✅", "pending": "⏳", "rejected": "❌"}
    lines = [f"👥 <b>{html.escape(name)}</b> — {len(users)} ta o'quvchi", ""]
    lines += [f"{icon.get(u.registration_status, '•')} {html.escape(u.full_name or 'Nomsiz')}" for u in users[:50]]
    if len(users) > 50:
        lines.append(f"… va yana {len(users) - 50} ta")
    if not users:
        lines.append("Hozircha o'quvchi yo'q.")
    rows = [
        [InlineKeyboardButton(text="➕ O'quvchi qo'shish / ko'chirish", callback_data=f"rgp:as:{gid}")],
        [InlineKeyboardButton(text="✏️ Nomini o'zgartirish", callback_data=f"rgp:ren:{gid}"),
         InlineKeyboardButton(text="🗑 O'chirish", callback_data=f"rgp:del:{gid}")],
        [InlineKeyboardButton(text="⬅️ Guruhlar", callback_data="rgp:home")],
    ]
    await _say(c.message, "\n".join(lines), _kb(rows), True)


async def cb_rename(c: CallbackQuery, state: FSMContext):
    if not _is_admin(c.from_user.id):
        return await c.answer()
    gid = int(c.data.split(":")[2])
    await c.answer()
    await state.set_state(AdminGuruh.yangi_nom)
    await state.update_data(rgp_ren=gid)
    await c.message.answer("✏️ Yangi nomni yozing. Shu guruhdagi barcha o'quvchilarning guruhi ham o'zgaradi.\n"
                           "Bekor qilish: <b>bekor</b>")


async def on_rename(message: Message, state: FSMContext):
    if not _is_admin(message.from_user.id):
        return
    new = " ".join((message.text or "").split())
    gid = (await state.get_data()).get("rgp_ren")
    await state.set_state(None)
    if not new or new.startswith("/") or new.lower() == "bekor" or not gid:
        await message.answer("Bekor qilindi.")
        return
    if len(new) > 40:
        await message.answer("Nom 40 belgidan oshmasin.")
        return
    old = await _group_by_id(int(gid))
    if not old:
        await message.answer("Guruh topilmadi.")
        return
    if any(_gkey(n) == _gkey(new) and g != int(gid) for g, n in await _groups()):
        await message.answer("Bunday nomli guruh allaqachon bor.")
        return
    async with engine.begin() as conn:
        await conn.execute(text("UPDATE reg_groups SET name = :n WHERE id = :i"), {"n": new, "i": int(gid)})
        r = await conn.execute(text("UPDATE users SET group_name = :n WHERE group_name = :o"), {"n": new, "o": old})
    await message.answer(f"✅ «{html.escape(old)}» → «{html.escape(new)}». O'quvchilar: {r.rowcount or 0} ta yangilandi.")
    await _render_home(message, state)


async def cb_delete(c: CallbackQuery):
    if not _is_admin(c.from_user.id):
        return await c.answer()
    gid = int(c.data.split(":")[2])
    name = await _group_by_id(gid)
    if not name:
        return await c.answer("Guruh topilmadi.", show_alert=True)
    await c.answer()
    await _say(c.message,
               f"🗑 «{html.escape(name)}» ro'yxatdan o'chirilsinmi?\n\n"
               "O'quvchilar o'chirilmaydi va guruh yozuvi ularda qoladi; faqat ro'yxatdan tanlab bo'lmaydi.",
               _kb([[InlineKeyboardButton(text="🗑 Ha, o'chirish", callback_data=f"rgp:dok:{gid}"),
                     InlineKeyboardButton(text="⬅️ Yo'q", callback_data=f"rgp:v:{gid}")]]), True)


async def cb_delete_ok(c: CallbackQuery, state: FSMContext):
    if not _is_admin(c.from_user.id):
        return await c.answer()
    gid = int(c.data.split(":")[2])
    async with engine.begin() as conn:
        await conn.execute(text("DELETE FROM reg_groups WHERE id = :i"), {"i": gid})
    await c.answer("O'chirildi")
    await _render_home(c.message, state, edit=True)


# --- bir nechta o'quvchini belgilab guruhga qo'shish ---
async def _candidates(target: str) -> list:
    listed = {n for _g, n in await _groups()}
    async with SessionLocal() as s:
        users = (await s.scalars(select(User).where(
            User.registration_status.in_(("approved", "pending")),
            or_(User.is_blocked.is_(False), User.is_blocked.is_(None)),
        ))).all()
    users = [u for u in users if u.telegram_id not in settings.admins and (u.group_name or "") != target]
    users.sort(key=lambda u: ((u.group_name or "") in listed, (u.full_name or "").lower()))
    return users


async def _render_pick(target_msg, state: FSMContext, edit: bool):
    d = await state.get_data()
    gid, sel, page = d.get("rgp_gid"), set(d.get("rgp_sel") or []), int(d.get("rgp_page") or 0)
    name = await _group_by_id(int(gid)) if gid else None
    if not name:
        return await _say(target_msg, "Guruh topilmadi.", None, edit)
    users = await _candidates(name)
    pages = max(1, (len(users) + PAGE - 1) // PAGE)
    page = min(max(page, 0), pages - 1)
    await state.update_data(rgp_page=page)
    listed = {n for _g, n in await _groups()}
    rows = []
    for u in users[page * PAGE:(page + 1) * PAGE]:
        mark = "☑️" if u.telegram_id in sel else "⬜"
        g = u.group_name or "guruhsiz"
        tag = g[:12] + ("" if g in listed else " ✍️")
        rows.append([InlineKeyboardButton(text=f"{mark} {(u.full_name or 'Nomsiz')[:22]} · {tag}",
                                          callback_data=f"rgp:t:{u.telegram_id}")])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"rgp:pg:{page - 1}"))
    nav.append(InlineKeyboardButton(text=f"{page + 1}/{pages}", callback_data="rgp:noop"))
    if page < pages - 1:
        nav.append(InlineKeyboardButton(text="➡️", callback_data=f"rgp:pg:{page + 1}"))
    rows.append(nav)
    rows.append([InlineKeyboardButton(text=f"✅ Saqlash ({len(sel)} ta)", callback_data="rgp:ok"),
                 InlineKeyboardButton(text="⬅️ Bekor", callback_data=f"rgp:v:{gid}")])
    txt = (f"➕ <b>{html.escape(name)}</b> guruhiga qo'shiladigan o'quvchilarni belgilang.\n"
           "Tepada guruhga ajratilmaganlar turadi (✍️ — o'zi yozgan guruh).\n"
           "Boshqa guruhdagi o'quvchini belgilasangiz, shu guruhga ko'chadi.")
    if not users:
        txt = f"👥 <b>{html.escape(name)}</b>: qo'shish uchun boshqa o'quvchi yo'q."
    await _say(target_msg, txt, _kb(rows), edit)


async def cb_pick_start(c: CallbackQuery, state: FSMContext):
    if not _is_admin(c.from_user.id):
        return await c.answer()
    await c.answer()
    await state.update_data(rgp_gid=int(c.data.split(":")[2]), rgp_sel=[], rgp_page=0)
    await _render_pick(c.message, state, True)


async def cb_pick_toggle(c: CallbackQuery, state: FSMContext):
    if not _is_admin(c.from_user.id):
        return await c.answer()
    tid = int(c.data.split(":")[2])
    sel = set((await state.get_data()).get("rgp_sel") or [])
    sel.symmetric_difference_update({tid})
    await state.update_data(rgp_sel=sorted(sel))
    await c.answer()
    await _render_pick(c.message, state, True)


async def cb_pick_page(c: CallbackQuery, state: FSMContext):
    if not _is_admin(c.from_user.id):
        return await c.answer()
    await state.update_data(rgp_page=int(c.data.split(":")[2]))
    await c.answer()
    await _render_pick(c.message, state, True)


async def cb_pick_save(c: CallbackQuery, state: FSMContext):
    if not _is_admin(c.from_user.id):
        return await c.answer()
    d = await state.get_data()
    gid, sel = d.get("rgp_gid"), list(d.get("rgp_sel") or [])
    name = await _group_by_id(int(gid)) if gid else None
    if not name:
        return await c.answer("Guruh topilmadi.", show_alert=True)
    if not sel:
        return await c.answer("Hech kim belgilanmadi.", show_alert=True)
    async with SessionLocal() as s:
        users = (await s.scalars(select(User).where(User.telegram_id.in_(sel)))).all()
        for u in users:
            u.group_name = name
        await s.commit()
    await state.update_data(rgp_sel=[])
    await c.answer(f"{len(users)} ta o'quvchi «{name}» guruhiga qo'shildi", show_alert=True)
    await cb_view(c, state, gid=int(gid))


async def cb_none(c: CallbackQuery, state: FSMContext):
    if not _is_admin(c.from_user.id):
        return await c.answer()
    await c.answer()
    await _ask_target(c.message, True)


async def _ask_target(msg, edit: bool):
    groups = await _groups()
    if not groups:
        return await _say(msg, "Avval guruh qo'shing: /guruhlar → ➕ Guruh qo'shish", None, edit)
    await _say(msg, "👥 Qaysi guruhga ajratamiz? Guruhni tanlang, keyin o'quvchilarni belgilaysiz.",
               _groups_kb(groups, "rgp:as:", extra=[[InlineKeyboardButton(text="⬅️ Guruhlar", callback_data="rgp:home")]]),
               edit)


async def cmd_sort(message: Message, state: FSMContext):
    if not _is_admin(message.from_user.id):
        return
    await state.clear()
    await _ask_target(message, False)


async def cmd_group_add(message: Message):
    if not _is_admin(message.from_user.id):
        return
    raw = (message.text or "").split(maxsplit=1)
    names = _split_names(raw[1] if len(raw) > 1 else "")
    if not names:
        await message.answer("Format: /guruh_qosh 175-guruh, 176-guruh")
        return
    await message.answer(_add_report(*await _add_groups(names)))


async def cmd_group_del(message: Message):
    if not _is_admin(message.from_user.id):
        return
    raw = (message.text or "").split(maxsplit=1)
    name = " ".join(raw[1].split()) if len(raw) > 1 else ""
    target = next(((g, n) for g, n in await _groups() if name and _gkey(n) == _gkey(name)), None)
    if not target:
        await message.answer("Format: /guruh_ochir 175-guruh (ro'yxat: /guruhlar)")
        return
    async with engine.begin() as conn:
        await conn.execute(text("DELETE FROM reg_groups WHERE id = :i"), {"i": target[0]})
    await message.answer(f"🗑 «{html.escape(target[1])}» ro'yxatdan olindi. O'quvchilarning guruh yozuvi o'zgarmadi.")


async def cb_noop(c: CallbackQuery):
    await c.answer()


# ---------------------------------------------------------------
#  Ulash
# ---------------------------------------------------------------
def setup(router: Router) -> None:
    # o'quvchi: ro'yxatdan o'tish
    router.message.register(on_ism, Royxat.ism)
    router.message.register(on_familiya, Royxat.familiya)
    router.message.register(on_group_typed, Royxat.guruh_qolda)
    router.message.register(on_goal_typed, Royxat.maqsad_qolda)
    router.callback_query.register(on_group_pick, F.data.startswith("rg:g:"))
    router.callback_query.register(on_group_own, F.data == "rg:gq")
    router.callback_query.register(on_goal_pick, F.data.startswith("rg:m:"))
    router.callback_query.register(on_again, F.data == "rg:again")
    # admin: ariza
    router.message.register(on_reject_custom_text, AdminRoyxat.sabab)
    router.callback_query.register(on_reject_tpl, F.data.startswith("rg:r:") | F.data.startswith("rg:R:"))
    router.callback_query.register(on_reject_custom, F.data.startswith("rg:rc:") | F.data.startswith("rg:RC:"))
    router.callback_query.register(on_set_group_menu, F.data.startswith("rg:sg:"))
    router.callback_query.register(on_set_group, F.data.startswith("rg:ss:"))
    # admin: guruhlar paneli
    router.message.register(on_new_names, AdminGuruh.nom)
    router.message.register(on_rename, AdminGuruh.yangi_nom)
    router.message.register(cmd_groups, Command("guruhlar"))
    router.message.register(cmd_group_add, Command("guruh_qosh"))
    router.message.register(cmd_group_del, Command("guruh_ochir"))
    router.message.register(cmd_sort, Command("ajrat"))
    for prefix, handler in (
        ("rgp:home", cb_home), ("rgp:add", cb_add), ("rgp:ac:", cb_add_custom), ("rgp:v:", cb_view),
        ("rgp:ren:", cb_rename), ("rgp:del:", cb_delete), ("rgp:dok:", cb_delete_ok), ("rgp:as:", cb_pick_start),
        ("rgp:t:", cb_pick_toggle), ("rgp:pg:", cb_pick_page), ("rgp:ok", cb_pick_save), ("rgp:none", cb_none),
        ("rgp:noop", cb_noop),
    ):
        router.callback_query.register(handler, F.data.startswith(prefix))

    async def _need_buttons(message: Message):
        await message.answer("Iltimos, yuqoridagi tugmalardan birini tanlang. Boshidan boshlash: /start")

    router.message.register(_need_buttons, Royxat.guruh)
    router.message.register(_need_buttons, Royxat.maqsad)

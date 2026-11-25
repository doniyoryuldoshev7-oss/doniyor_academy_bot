"""Milliy sertifikat imtihonini bosqichma-bosqich yig'ish.

1-bosqich: yopiq savollar (1-35)
2-bosqich: ochiq savollar (36-45, har birida a) va b))
3-bosqich: birlashtirib o'quvchilarga taqdim etish (imtihon yaratish ekrani
           imtihon_ustoz.py da: «exm:fx»)

Savollar bazaga yozilmaydi: ular faqat shu imtihonning o'zida saqlanadi.
Qoralama FSM ichida «mly» kaliti ostida turadi (bot qayta ishga tushsa ham saqlanadi).
"""
from __future__ import annotations

import html
import io
import logging
import re

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from ..admin_roles import is_full_admin, is_teacher
from ..services.savol_import import (
    MAX_FILE,
    MAX_TEXT,
    extract_text,
    image_ref,
    parse_text,
    save_image_bytes,
)

log = logging.getLogger(__name__)
router = Router()

CLOSED_N = 35   # 1-35
OPEN_N = 10     # 36-45 (har birida a va b)


_SEP = re.compile(r"^\s*(?:---+|\*\*\*+|===+)\s*$")
_PART = re.compile(r"^\s*[a-dA-D]\s*[\)\.]")


class MilliyYig(StatesGroup):
    closed = State()
    opened = State()


def kb(rows):
    return InlineKeyboardMarkup(inline_keyboard=rows)


def B(text, data):
    return InlineKeyboardButton(text=text, callback_data=data)


def esc(s):
    return html.escape(str(s))


async def show(c: CallbackQuery, text: str, markup):
    try:
        await c.message.edit_text(text, reply_markup=markup)
    except TelegramBadRequest as exc:
        if "not modified" in str(exc).lower():
            return
        await c.message.answer(text, reply_markup=markup)


async def allowed(uid: int) -> bool:
    return bool(is_full_admin(uid) or await is_teacher(uid))


async def gate(c: CallbackQuery) -> bool:
    if await allowed(c.from_user.id):
        return True
    await c.answer("Ruxsat yo'q.", show_alert=True)
    return False


async def clear_keep(state: FSMContext):
    """Holatni tozalaydi, lekin tugallanmagan Milliy sertifikat qoralamasini saqlab qoladi."""
    keep = (await state.get_data()).get("mly")
    await state.clear()
    if keep:
        await state.update_data(mly=keep)


# ---------------------------------------------------------------
#  Matnlar
# ---------------------------------------------------------------
HELP_CLOSED = (
    f"Yopiq savollarni yuboring (<b>{CLOSED_N} ta</b>, 1–{CLOSED_N}). "
    "Matn qilib (uzun bo'lsa bir necha xabar) yoki .txt / .docx / .xlsx fayl.\n\n"
    "<code>1. Savol matni\nA) variant\nB) variant\nC) variant\nD) variant\nJavob: B</code>\n\n"
    "Rasmli savol: rasmni savol matni bilan birga (izoh qilib) yuboring.\n"
    "Hammasini yuborgach «✅ Tayyor» ni bosing."
)

HELP_OPEN = (
    f"Ochiq savollarni yuboring (<b>{OPEN_N} ta</b>, {CLOSED_N + 1}–{CLOSED_N + OPEN_N}). "
    "Har birida a) va b) qismi bor. Savollar <code>---</code> qatori bilan ajratiladi.\n\n"
    "<code>Savol matni\na) birinchi qism =&gt; javob1 | javob2\nb) ikkinchi qism =&gt; javob</code>\n"
    "<code>|</code> — muqobil to'g'ri javoblar.\n\n"
    "Hammasini yuborgach «✅ Tayyor» ni bosing."
)


def _draft(data) -> dict:
    return dict(data.get("mly") or {})


def _home_text(m: dict) -> str:
    nc, no = len(m.get("c") or []), len(m.get("o") or [])
    ready = nc == CLOSED_N and no == OPEN_N
    return (
        "🏅 <b>Milliy sertifikat imtihonini yig'ish</b>\n\n"
        f"1️⃣ Yopiq savollar (1–{CLOSED_N}): "
        + (f"✅ {nc} ta yuklandi" if nc == CLOSED_N else "❌ hali yuklanmagan") + "\n"
        f"2️⃣ Ochiq savollar ({CLOSED_N + 1}–{CLOSED_N + OPEN_N}, har birida a va b): "
        + (f"✅ {no} ta yuklandi" if no == OPEN_N else "❌ hali yuklanmagan") + "\n"
        "3️⃣ Birlashtirib o'quvchilarga taqdim etish: "
        + ("✅ tayyor" if ready else "⏳ avval 1 va 2-bosqichni tugating")
    )


def _home_kb(m: dict):
    nc, no = len(m.get("c") or []), len(m.get("o") or [])
    ready = nc == CLOSED_N and no == OPEN_N
    rows = [
        [B(("♻️ " if nc == CLOSED_N else "1️⃣ ") + "Yopiq savollarni yuklash", "mly:c")],
        [B(("♻️ " if no == OPEN_N else "2️⃣ ") + "Ochiq savollarni yuklash", "mly:o")],
        [B(("✅ " if ready else "3️⃣ ") + "Birlashtirish va taqdim etish", "exm:fx")],
    ]
    if nc or no:
        rows.append([B("🗑 Qoralamani o'chirish", "mly:del")])
    rows.append([B("⬅️ Orqaga", "exm:t")])
    return kb(rows)


def _collect_kb(has_text: bool):
    rows = []
    if has_text:
        rows.append([B("✅ Tayyor — tekshirish", "mly:done")])
        rows.append([B("🗑 Yuborganlarimni tozalash", "mly:clr")])
    rows.append([B("⬅️ Orqaga", "mly:home")])
    return kb(rows)


# ---------------------------------------------------------------
#  Bosh ekran
# ---------------------------------------------------------------
@router.callback_query(F.data == "mly:home")
async def cb_home(c: CallbackQuery, state: FSMContext):
    if not await gate(c):
        return
    await state.set_state(None)
    m = _draft(await state.get_data())
    await show(c, _home_text(m), _home_kb(m))
    await c.answer()


@router.callback_query(F.data == "mly:del")
async def cb_delete_ask(c: CallbackQuery, state: FSMContext):
    if not await gate(c):
        return
    await show(
        c,
        "🗑 Yuklangan savollar o'chiriladi. Rostdan o'chiraymi?",
        kb([[B("✅ Ha, o'chir", "mly:delok")], [B("⬅️ Yo'q, orqaga", "mly:home")]]),
    )
    await c.answer()


@router.callback_query(F.data == "mly:delok")
async def cb_delete(c: CallbackQuery, state: FSMContext):
    if not await gate(c):
        return
    await state.set_state(None)
    await state.update_data(mly=None)
    await show(c, _home_text({}), _home_kb({}))
    await c.answer("O'chirildi.")


# ---------------------------------------------------------------
#  1- va 2-bosqich: yuborish
# ---------------------------------------------------------------
async def _start(c: CallbackQuery, state: FSMContext, stage: str):
    if not await gate(c):
        return
    m = _draft(await state.get_data())
    await state.set_state(MilliyYig.closed if stage == "c" else MilliyYig.opened)
    buf = m.get("b" + stage) or ""
    lines = len([x for x in buf.splitlines() if x.strip()])
    text = ("1️⃣ <b>Yopiq savollar</b>\n\n" + HELP_CLOSED) if stage == "c" else ("2️⃣ <b>Ochiq savollar</b>\n\n" + HELP_OPEN)
    if lines:
        text += f"\n\n📎 Oldin yuborganlaringiz saqlangan ({lines} qator). Davom eting yoki tozalang."
    await show(c, text, _collect_kb(bool(lines)))
    await c.answer()


@router.callback_query(F.data == "mly:c")
async def cb_start_closed(c: CallbackQuery, state: FSMContext):
    await _start(c, state, "c")


@router.callback_query(F.data == "mly:o")
async def cb_start_open(c: CallbackQuery, state: FSMContext):
    await _start(c, state, "o")


async def _stage(state: FSMContext) -> str | None:
    cur = await state.get_state()
    if cur == MilliyYig.closed.state:
        return "c"
    if cur == MilliyYig.opened.state:
        return "o"
    return None


def _join(buf: str, chunk: str, stage: str) -> str:
    """Yangi xabarni oldingisiga ulaydi: savollar bir-biriga yopishib ketmasin."""
    if not buf:
        return chunk
    if stage == "c":
        return buf + "\n\n" + chunk
    first = next((ln for ln in chunk.splitlines() if ln.strip()), "")
    last = next((ln for ln in reversed(buf.splitlines()) if ln.strip()), "")
    if _SEP.match(first) or _SEP.match(last):
        return buf + "\n" + chunk
    if _PART.match(first):  # savolning davomi (a) yoki b) qismi)
        return buf + "\n" + chunk
    return buf + "\n---\n" + chunk


async def _append(msg: Message, state: FSMContext, chunk: str, notes=None):
    stage = await _stage(state)
    if stage is None:
        return
    m = _draft(await state.get_data())
    buf = m.get("b" + stage) or ""
    buf = _join(buf, chunk, stage)
    if len(buf) > MAX_TEXT:
        await msg.answer("Juda ko'p matn. «✅ Tayyor» ni bosing.", reply_markup=_collect_kb(True))
        return
    m["b" + stage] = buf
    await state.update_data(mly=m)
    lines = len([x for x in buf.splitlines() if x.strip()])
    text = f"✅ Qabul qilindi ({lines} qator). Yana yuboring yoki «✅ Tayyor» ni bosing."
    if notes:
        text += "\n\n⚠️ " + "\n⚠️ ".join(esc(n) for n in notes)
    await msg.answer(text, reply_markup=_collect_kb(True))


async def _take_image(msg: Message, state: FSMContext, file_obj, caption):
    try:
        buf = io.BytesIO()
        await msg.bot.download(file_obj, destination=buf)
        name = save_image_bytes(buf.getvalue())
    except ValueError as exc:
        await msg.answer("❌ " + esc(exc), reply_markup=_collect_kb(False))
        return
    except Exception:
        log.exception("milliy: rasm")
        await msg.answer("❌ Rasmni qabul qilib bo'lmadi. Qayta yuboring.", reply_markup=_collect_kb(False))
        return
    chunk = f"[rasm:{name}]" + (("\n" + caption) if (caption or "").strip() else "")
    await _append(msg, state, chunk, None if (caption or "").strip() else
                  ["Rasm savol matnisiz keldi: u keyingi yuborgan savolingizga biriktiriladi."])


COLLECTING = StateFilter(MilliyYig.closed, MilliyYig.opened)


@router.message(COLLECTING, F.photo)
async def msg_photo(msg: Message, state: FSMContext):
    if not await allowed(msg.from_user.id):
        return
    await _take_image(msg, state, msg.photo[-1], msg.caption)


@router.message(COLLECTING, F.document)
async def msg_file(msg: Message, state: FSMContext):
    if not await allowed(msg.from_user.id):
        return
    doc = msg.document
    if (doc.mime_type or "").startswith("image/"):
        return await _take_image(msg, state, doc, msg.caption)
    if (doc.file_size or 0) > MAX_FILE:
        await msg.answer("Fayl juda katta (3 MB dan oshmasin).", reply_markup=_collect_kb(False))
        return
    try:
        buf = io.BytesIO()
        await msg.bot.download(doc, destination=buf)
        text, notes = extract_text(doc.file_name or "", buf.getvalue())
    except ValueError as exc:
        await msg.answer("❌ " + esc(exc), reply_markup=_collect_kb(False))
        return
    except Exception:
        log.exception("milliy: fayl")
        await msg.answer("❌ Faylni o'qib bo'lmadi. Matnni to'g'ridan-to'g'ri yuboring.",
                         reply_markup=_collect_kb(False))
        return
    await _append(msg, state, text, notes)


@router.message(COLLECTING, F.text)
async def msg_text(msg: Message, state: FSMContext):
    if not await allowed(msg.from_user.id):
        return
    await _append(msg, state, msg.text)


@router.message(COLLECTING)
async def msg_other(msg: Message):
    await msg.answer("Matn yoki .txt / .docx / .xlsx fayl yuboring.", reply_markup=_collect_kb(False))


@router.callback_query(COLLECTING, F.data == "mly:clr")
async def cb_clear(c: CallbackQuery, state: FSMContext):
    stage = await _stage(state)
    m = _draft(await state.get_data())
    m["b" + (stage or "c")] = ""
    await state.update_data(mly=m)
    await show(c, "🗑 Tozalandi. Savollarni qaytadan yuboring.", _collect_kb(False))
    await c.answer()


# ---------------------------------------------------------------
#  Tekshirish
# ---------------------------------------------------------------
def build_closed(res) -> tuple[list, list, list]:
    """Qaytaradi: (elementlar, xatolar, ogohlantirishlar)."""
    errors = list(res.errors)
    if res.open:
        errors.append(f"Bu bosqichda faqat yopiq savol bo'lishi kerak, lekin {len(res.open)} ta ochiq savol topildi.")
    if len(res.closed) != CLOSED_N:
        errors.append(f"Yopiq savol {CLOSED_N} ta bo'lishi kerak, topildi: {len(res.closed)} ta.")
    items = [
        {
            "k": "c", "t": q["text"], "o": dict(q["options"]), "c": q["correct"],
            "img": image_ref(q["image"]) if q.get("image") else None,
            "e": q.get("explanation"), "qid": None,
        }
        for q in res.closed
    ]
    return items, errors, list(res.warnings)


def build_open(res) -> tuple[list, list, list]:
    errors = list(res.errors)
    warnings = []
    if res.closed:
        errors.append(f"Bu bosqichda faqat ochiq savol bo'lishi kerak, lekin {len(res.closed)} ta yopiq savol topildi.")
    if len(res.open) != OPEN_N:
        errors.append(f"Ochiq savol {OPEN_N} ta bo'lishi kerak, topildi: {len(res.open)} ta.")
    items = []
    for i, q in enumerate(res.open, start=CLOSED_N + 1):
        if len(q["parts"]) != 2:
            warnings.append(f"{i}-savolda {len(q['parts'])} ta qism bor (odatda 2 ta: a va b).")
        items.append({
            "k": "o", "s": q["stem"], "p": [dict(p) for p in q["parts"]], "oid": None,
            "img": image_ref(q["image"]) if q.get("image") else None,
        })
    return items, errors, warnings


@router.callback_query(COLLECTING, F.data == "mly:done")
async def cb_done(c: CallbackQuery, state: FSMContext):
    if not await gate(c):
        return
    stage = await _stage(state)
    m = _draft(await state.get_data())
    buf = m.get("b" + (stage or "c")) or ""
    if not buf.strip():
        await c.answer("Hali hech narsa yuborilmadi.", show_alert=True)
        return

    res = parse_text(buf)
    items, errors, warnings = (build_closed if stage == "c" else build_open)(res)

    if not errors:
        m["c" if stage == "c" else "o"] = items
        m["b" + stage] = ""
        await state.update_data(mly=m)
        await state.set_state(None)
        if stage == "c":
            done = f"✅ <b>Yopiq savollar saqlandi:</b> {len(items)} ta (1–{CLOSED_N})."
        else:
            done = (f"✅ <b>Ochiq savollar saqlandi:</b> {len(items)} ta "
                    f"({CLOSED_N + 1}–{CLOSED_N + OPEN_N}, jami {sum(len(i['p']) for i in items)} ta javob).")
        if warnings:
            done += "\n\n<b>Ogohlantirish:</b>\n" + "\n".join("• " + esc(w) for w in warnings[:6])
        done += "\n\n" + _home_text(m)
        await show(c, done[:3900], _home_kb(m))
        await c.answer()
        return

    parts = [f"⚠️ <b>Saqlanmadi — {len(errors)} ta muammo</b>",
             "\n".join("• " + esc(e) for e in errors[:12])]
    if len(errors) > 12:
        parts.append(f"… va yana {len(errors) - 12} ta.")
    parts.append("\nYetishmaganini yana yuborishingiz mumkin (oldingilari saqlanib turadi) yoki boshidan boshlang.")
    await show(c, "\n".join(parts)[:3900], kb([
        [B("➕ Yana qo'shib yuboraman", "mly:more")],
        [B("🗑 Boshidan boshlayman", "mly:clr")],
        [B("⬅️ Orqaga", "mly:home")],
    ]))
    await c.answer()


@router.callback_query(COLLECTING, F.data == "mly:more")
async def cb_more(c: CallbackQuery, state: FSMContext):
    stage = await _stage(state)
    m = _draft(await state.get_data())
    lines = len([x for x in (m.get("b" + (stage or "c")) or "").splitlines() if x.strip()])
    await show(c, f"Yuboring — qo'shib boraman (hozir {lines} qator saqlangan). Tugagach «✅ Tayyor» ni bosing.",
               _collect_kb(True))
    await c.answer()

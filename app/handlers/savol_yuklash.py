"""Savol yuklash (bot): yopiq va ochiq savollar, bitta oddiy format.

Joylashuvi: app/handlers/savol_yuklash.py
Tugma: admin menyu, imtihonlar bosh sahifasi → «📥 Savol yuklash» (imq:home).
"""
from __future__ import annotations

import html
import io
import logging

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from sqlalchemy import select

from ..admin_roles import can_add_questions, is_full_admin
from ..db import SessionLocal
from ..models import ImportBatch, Subject, Topic
from ..services.savol_import import (
    HELP_HTML,
    MAX_FILE,
    MAX_TEXT,
    TEMPLATE_TEXT,
    can_import,
    extract_text,
    history,
    parse_text,
    preview,
    redo_batch,
    resolve_topics,
    save_image_bytes,
    save_parsed,
    template_docx_bytes,
    template_xlsx_bytes,
    undo_batch,
    _batch_counts,
)

log = logging.getLogger(__name__)
router = Router()


class ImportQ(StatesGroup):
    collect = State()


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


async def gate(c: CallbackQuery) -> bool:
    if await can_import(c.from_user.id):
        return True
    await c.answer("Ruxsat yo'q.", show_alert=True)
    return False


async def may_create(uid: int) -> bool:
    return bool(is_full_admin(uid) or await can_add_questions(uid))


def collect_kb(has_text: bool):
    rows = []
    if has_text:
        rows.append([B("✅ Tayyor — tekshirish", "imq:done")])
        rows.append([B("🗑 Yuborganlarimni tozalash", "imq:clr")])
    rows.append([B("📄 Namuna fayl olish", "imq:tpl")])
    rows.append([B("❌ Bekor", "imq:x")])
    return kb(rows)


# ---------------------------------------------------------------
@router.callback_query(F.data == "imq:x")
async def cb_cancel(c: CallbackQuery, state: FSMContext):
    await state.clear()
    await show(c, "Bekor qilindi.", kb([[B("📥 Savol yuklash", "imq:home")]]))
    await c.answer()


@router.callback_query(F.data.in_({"imq:home", "imq:again"}))
async def cb_home(c: CallbackQuery, state: FSMContext):
    if not await gate(c):
        return
    data = await state.get_data()
    if c.data == "imq:again" and data.get("imq_topic"):
        return await _start_collect(c, state, data["imq_topic"])
    await state.clear()
    async with SessionLocal() as s:
        subjects = (await s.scalars(select(Subject).order_by(Subject.name))).all()
    rows = [[B("📚 " + sub.name[:40], f"imq:s:{sub.id}")] for sub in subjects[:40]]
    rows.insert(0, [B("🗂 Fan va mavzu faylning o'zida yozilgan", "imq:auto")])
    rows.append([B("📜 Yuklashlarim (bekor qilish / tiklash)", "imq:hl")])
    rows.append([B("❌ Bekor", "imq:x")])
    await show(
        c,
        "📥 <b>Savol yuklash</b>\n\n"
        "Qaysi fanga yuklaymiz? Yoki «Fan va mavzu faylda yozilgan» ni bosing — "
        "bir fayldan bir necha mavzuga yuklash mumkin.",
        kb(rows),
    )
    await c.answer()


@router.callback_query(F.data.startswith("imq:s:"))
async def cb_subject(c: CallbackQuery, state: FSMContext):
    if not await gate(c):
        return
    sid = int(c.data.split(":")[2])
    async with SessionLocal() as s:
        sub = await s.get(Subject, sid)
        topics = (await s.scalars(select(Topic).where(Topic.subject_id == sid).order_by(Topic.name))).all()
    if sub is None:
        await c.answer("Fan topilmadi.", show_alert=True)
        return
    if not topics:
        await c.answer("Bu fanda hali mavzu yo'q.", show_alert=True)
        return
    rows = [[B("📖 " + t.name[:45], f"imq:t:{t.id}")] for t in topics[:60]]
    rows.append([B("⬅️ Orqaga", "imq:home")])
    await show(c, f"📚 <b>{esc(sub.name)}</b>\n\nMavzuni tanlang:", kb(rows))
    await c.answer()


@router.callback_query(F.data == "imq:auto")
async def cb_auto(c: CallbackQuery, state: FSMContext):
    if not await gate(c):
        return
    await _start_collect(c, state, None)


@router.callback_query(F.data.startswith("imq:t:"))
async def cb_topic(c: CallbackQuery, state: FSMContext):
    if not await gate(c):
        return
    await _start_collect(c, state, int(c.data.split(":")[2]))


async def _start_collect(c: CallbackQuery, state: FSMContext, topic_id):
    where = "Fan va mavzu faylda yozilgan bo'lishi kerak."
    if topic_id:
        async with SessionLocal() as s:
            t = await s.get(Topic, topic_id)
        if t is None:
            await c.answer("Mavzu topilmadi.", show_alert=True)
            return
        where = f"📖 <b>{esc(t.name)}</b>"
    await state.set_state(ImportQ.collect)
    await state.update_data(imq_topic=topic_id, imq_buf="")
    await show(
        c,
        f"{where}\n\n"
        "Savollarni yuboring: matn qilib (uzun bo'lsa bir necha xabar) yoki "
        ".txt / .docx fayl. Hammasini yuborgach «✅ Tayyor» ni bosing.\n\n" + HELP_HTML,
        collect_kb(False),
    )
    await c.answer()


# ---------------------------------------------------------------
@router.callback_query(F.data == "imq:tpl")
async def cb_template(c: CallbackQuery):
    if not await gate(c):
        return
    await c.message.answer_document(
        BufferedInputFile(("﻿" + TEMPLATE_TEXT).encode("utf-8"), filename="namuna.txt"),
        caption="Namuna (matn). Ochib, o'z savollaringiz bilan almashtiring.",
    )
    try:
        await c.message.answer_document(
            BufferedInputFile(template_docx_bytes(), filename="namuna.docx"),
            caption="Xuddi shu namuna Word ko'rinishida.",
        )
    except Exception:
        log.exception("namuna.docx")
    try:
        await c.message.answer_document(
            BufferedInputFile(template_xlsx_bytes(), filename="namuna.xlsx"),
            caption="Excel jadvali ko'rinishidagi namuna.",
        )
    except Exception:
        log.exception("namuna.xlsx")
    await c.answer()


async def _append(m: Message, state: FSMContext, chunk: str, notes=None):
    data = await state.get_data()
    buf = (data.get("imq_buf") or "")
    buf = (buf + "\n" + chunk) if buf else chunk
    if len(buf) > MAX_TEXT:
        await m.answer("Juda ko'p matn. «✅ Tayyor» ni bosing, qolganini keyingi yuklashda yuboring.",
                       reply_markup=collect_kb(True))
        return
    await state.update_data(imq_buf=buf)
    lines = len([x for x in buf.splitlines() if x.strip()])
    text = f"✅ Qabul qilindi ({lines} qator). Yana yuboring yoki «✅ Tayyor» ni bosing."
    if notes:
        text += "\n\n⚠️ " + "\n⚠️ ".join(esc(n) for n in notes)
    await m.answer(text, reply_markup=collect_kb(True))


async def _take_image(m: Message, state: FSMContext, file_obj, caption):
    try:
        buf = io.BytesIO()
        await m.bot.download(file_obj, destination=buf)
        name = save_image_bytes(buf.getvalue())
    except ValueError as exc:
        await m.answer("❌ " + esc(exc), reply_markup=collect_kb(False))
        return
    except Exception:
        log.exception("savol rasmi")
        await m.answer("❌ Rasmni qabul qilib bo'lmadi. Qayta yuboring.", reply_markup=collect_kb(False))
        return
    chunk = f"[rasm:{name}]" + (("\n" + caption) if (caption or "").strip() else "")
    await _append(m, state, chunk, None if (caption or "").strip() else
                  ["Rasm savol matnisiz keldi: u keyingi yuborgan savolingizga biriktiriladi."])


@router.message(ImportQ.collect, F.photo)
async def msg_photo(m: Message, state: FSMContext):
    if not await can_import(m.from_user.id):
        return
    await _take_image(m, state, m.photo[-1], m.caption)


@router.message(ImportQ.collect, F.document)
async def msg_file(m: Message, state: FSMContext):
    if not await can_import(m.from_user.id):
        return
    doc = m.document
    if (doc.mime_type or "").startswith("image/"):
        return await _take_image(m, state, doc, m.caption)
    if (doc.file_size or 0) > MAX_FILE:
        await m.answer("Fayl juda katta (3 MB dan oshmasin).", reply_markup=collect_kb(False))
        return
    try:
        buf = io.BytesIO()
        await m.bot.download(doc, destination=buf)
        text, notes = extract_text(doc.file_name or "", buf.getvalue())
    except ValueError as exc:
        await m.answer("❌ " + esc(exc), reply_markup=collect_kb(False))
        return
    except Exception:
        log.exception("savol fayli")
        await m.answer("❌ Faylni o'qib bo'lmadi. Matnni to'g'ridan-to'g'ri yuboring.", reply_markup=collect_kb(False))
        return
    await _append(m, state, text, notes)


@router.message(ImportQ.collect, F.text)
async def msg_text(m: Message, state: FSMContext):
    if not await can_import(m.from_user.id):
        return
    await _append(m, state, m.text)


@router.message(ImportQ.collect)
async def msg_other(m: Message):
    await m.answer("Matn yoki .txt / .docx fayl yuboring.", reply_markup=collect_kb(False))


@router.callback_query(ImportQ.collect, F.data == "imq:clr")
async def cb_clear(c: CallbackQuery, state: FSMContext):
    await state.update_data(imq_buf="")
    await show(c, "🗑 Tozalandi. Savollarni qaytadan yuboring.", collect_kb(False))
    await c.answer()


# ---------------------------------------------------------------
async def _analyse(uid, data):
    res = parse_text(data.get("imq_buf") or "")
    async with SessionLocal() as s:
        mapping, new_topics, rerrors = await resolve_topics(
            s, res, data.get("imq_topic"), await may_create(uid), create=False
        )
    return res, new_topics, rerrors


@router.callback_query(ImportQ.collect, F.data == "imq:done")
async def cb_done(c: CallbackQuery, state: FSMContext):
    if not await gate(c):
        return
    data = await state.get_data()
    if not (data.get("imq_buf") or "").strip():
        await c.answer("Hali hech narsa yuborilmadi.", show_alert=True)
        return
    res, new_topics, rerrors = await _analyse(c.from_user.id, data)
    errors = list(res.errors) + rerrors

    head = (
        f"{'✅' if not errors else '⚠️'} <b>Yopiq: {len(res.closed)} ta, ochiq: {len(res.open)} ta</b>"
        f"{f', xato: {len(errors)} ta' if errors else ''}"
    )
    parts = [head]
    if errors:
        parts.append("\n<b>Xatolar:</b>\n" + "\n".join("• " + esc(e) for e in errors[:12]))
        if len(errors) > 12:
            parts.append(f"… va yana {len(errors) - 12} ta xato.")
    if res.warnings:
        parts.append("\n<b>Ogohlantirish:</b>\n" + "\n".join("• " + esc(w) for w in res.warnings[:6]))
    if new_topics:
        parts.append("\n🆕 Yangi mavzu ochiladi: " + esc("; ".join(sorted(set(new_topics)))))
    if res.total:
        body = preview(res, 5).split("\n", 1)
        if len(body) > 1:
            parts.append("\n<b>Ko'rinish:</b>" + esc(body[1]))
    parts.append("\n⚠️ Hali bazaga yozilmagan.")
    text = "\n".join(parts)[:3900]

    rows = []
    if res.total and not rerrors and not any("Hech qanday" in e or "juda uzun" in e or "tadan ko'p" in e for e in res.errors):
        n = res.total
        rows.append([B(f"✅ Saqlash ({n} ta)" if not res.errors else f"✅ Faqat to'g'rilarini saqlash ({n} ta)", "imq:ok")])
    rows.append([B("✏️ Tuzatib qayta yuboraman", "imq:clr")])
    rows.append([B("❌ Bekor", "imq:x")])
    await show(c, text, kb(rows))
    await c.answer()


@router.callback_query(ImportQ.collect, F.data == "imq:ok")
async def cb_save(c: CallbackQuery, state: FSMContext):
    if not await gate(c):
        return
    data = await state.get_data()
    res = parse_text(data.get("imq_buf") or "")
    if not res.total:
        await c.answer("Saqlanadigan savol yo'q.", show_alert=True)
        return
    # bir marta bosilsa kifoya: holatni darhol bo'shatamiz (ikki marta saqlanmasin)
    topic = data.get("imq_topic")
    await state.update_data(imq_buf="")
    try:
        async with SessionLocal() as s:
            out = await save_parsed(s, topic, res, c.from_user.id, await may_create(c.from_user.id))
    except Exception:
        log.exception("savol yuklash")
        await state.update_data(imq_buf=data.get("imq_buf") or "")
        await c.answer("Saqlashda xato. Qaytadan urinib ko'ring.", show_alert=True)
        return
    if out["errors"]:
        await state.update_data(imq_buf=data.get("imq_buf") or "")
        await c.answer("Saqlanmadi: " + out["errors"][0][:150], show_alert=True)
        return
    await state.set_state(None)
    msg = f"✅ <b>Saqlandi:</b> {out['closed']} yopiq, {out['open']} ochiq savol."
    if out["dup"]:
        msg += f"\n♻️ Takror bo'lgani uchun o'tkazib yuborildi: {out['dup']} ta."
    if out["new_topics"]:
        msg += "\n🆕 Yangi mavzu: " + esc("; ".join(sorted(set(out["new_topics"]))))
    rows = []
    if out["batch"] and (out["closed"] or out["open"]):
        rows.append([B("↩️ Shu yuklashni bekor qilish", f"imq:undo:{out['batch']}")])
        rows.append([B("📜 Barcha yuklashlarim", "imq:hl")])
    if topic:
        rows.append([B("➕ Shu mavzuga yana yuklash", "imq:again")])
    rows.append([B("📥 Boshqa yuklash", "imq:home"), B("📝 Imtihonlar", "exm:t")])
    await show(c, msg, kb(rows))
    await c.answer()


def _batch_line(b):
    c, o = _batch_counts(b)
    when = b.created_at.strftime("%d.%m %H:%M") if b.created_at else "?"
    return f"{'↩️' if b.undone else '✅'} #{b.id} · {when} · {c}+{o}"


async def _show_history(c: CallbackQuery):
    uid = c.from_user.id
    async with SessionLocal() as s:
        items = await history(s, uid, is_full_admin(uid))
    if not items:
        await show(c, "📜 Hali birorta yuklash yo'q.", kb([[B("⬅️ Orqaga", "imq:home")]]))
        return
    rows = [[B(_batch_line(b), f"imq:h:{b.id}")] for b in items]
    rows.append([B("⬅️ Orqaga", "imq:home")])
    await show(
        c,
        "📜 <b>Yuklashlarim</b>\n\nOxirgi yuklashlar (✅ faol, ↩️ bekor qilingan; "
        "raqamlar: yopiq+ochiq savollar). Birini bosing.",
        kb(rows),
    )


@router.callback_query(F.data == "imq:hl")
async def cb_history(c: CallbackQuery):
    if not await gate(c):
        return
    await _show_history(c)
    await c.answer()


async def _show_batch(c: CallbackQuery, bid: int):
    uid = c.from_user.id
    async with SessionLocal() as s:
        b = await s.get(ImportBatch, bid)
    if b is None or not (b.user_tg_id == uid or is_full_admin(uid)):
        await c.answer("Yuklash topilmadi.", show_alert=True)
        return
    n_c, n_o = _batch_counts(b)
    state_txt = "↩️ Bekor qilingan" if b.undone else "✅ Faol (savollar bazada ishlayapti)"
    rows = [[B("♻️ Qayta tiklash", f"imq:redo:{bid}")] if b.undone else [B("↩️ Bekor qilish", f"imq:undo:{bid}")]]
    rows.append([B("⬅️ Yuklashlar ro'yxati", "imq:hl")])
    await show(
        c,
        f"📦 <b>Yuklash #{bid}</b>\n{b.created_at:%d.%m.%Y %H:%M}\n"
        f"Yopiq: {n_c} ta, ochiq: {n_o} ta\nHolati: {state_txt}",
        kb(rows),
    )


@router.callback_query(F.data.startswith("imq:h:"))
async def cb_batch(c: CallbackQuery):
    if not await gate(c):
        return
    await _show_batch(c, int(c.data.split(":")[2]))
    await c.answer()


@router.callback_query(F.data.startswith("imq:undo:"))
async def cb_undo(c: CallbackQuery):
    if not await gate(c):
        return
    bid = int(c.data.split(":")[2])
    async with SessionLocal() as s:
        ok, msg = await undo_batch(s, bid, c.from_user.id, is_full_admin(c.from_user.id))
    if not ok:
        await c.answer(msg, show_alert=True)
        return
    await _show_batch(c, bid)
    await c.answer("↩️ " + msg, show_alert=True)


@router.callback_query(F.data.startswith("imq:redo:"))
async def cb_redo(c: CallbackQuery):
    if not await gate(c):
        return
    bid = int(c.data.split(":")[2])
    async with SessionLocal() as s:
        ok, msg = await redo_batch(s, bid, c.from_user.id, is_full_admin(c.from_user.id))
    if not ok:
        await c.answer(msg, show_alert=True)
        return
    await _show_batch(c, bid)
    await c.answer("♻️ " + msg, show_alert=True)

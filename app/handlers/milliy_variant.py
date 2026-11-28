"""Milliy sertifikat: tayyor variantlar (zip yoki fayl) — bot tomoni.

Servis: app/services/milliy_variant.py (veb-ilova bilan bitta).
"""
from __future__ import annotations

import asyncio
import io
import logging

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message

from ..admin_roles import is_full_admin, is_teacher
from ..db import SessionLocal
from ..services import milliy_variant as MV
from ..services.student_report import fmt_datetime
from .imtihon_ustoz import esc, gate, kb, short, show
from .milliy_yigish import clear_keep

log = logging.getLogger(__name__)
router = Router()

PAGE = 10
TG_LIMIT = 20 * 1024 * 1024      # Telegram bot 20 MB dan katta faylni yuklab ololmaydi


class MlvUp(StatesGroup):
    wait = State()


def B(text, data):
    return InlineKeyboardButton(text=text, callback_data=data)


async def _rows():
    async with SessionLocal() as s:
        return await MV.list_variants(s)


@router.callback_query(F.data.regexp(r"^mlv:l:\d+$"))
async def cb_list(c: CallbackQuery, state: FSMContext):
    if not await gate(c):
        return
    await clear_keep(state)
    await _list_view(c, int(c.data.split(":")[2]))
    await c.answer()


async def _list_view(c: CallbackQuery, page: int):
    rows_db = await _rows()
    last = max(0, (len(rows_db) - 1) // PAGE)
    page = min(page, last)
    lines = ["🗂 <b>Tayyor variantlar</b> (Milliy sertifikat)\n"]
    if rows_db:
        lines.append(f"Jami: <b>{len(rows_db)}</b> ta variant. Variantni tanlang — undan imtihon ochasiz.\n"
                     "✅ — shu variantdan imtihon ochilgan.")
    else:
        lines.append("Hali variant yo'q. Zip (ichida 55 talik fayllar) yoki bitta fayl yuklang.")
    rows = [[B(("✅ " if v.used else "📄 ") + short(v.title, 40) + (f" ({v.used})" if v.used > 1 else ""), f"mlv:v:{v.id}")]
            for v in rows_db[page * PAGE:(page + 1) * PAGE]]
    nav = []
    if page > 0:
        nav.append(B("⬅️", f"mlv:l:{page - 1}"))
    if page < last:
        nav.append(B("➡️", f"mlv:l:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([B("📦 Zip yoki fayl yuklash", "mlv:up")])
    if rows_db and is_full_admin(c.from_user.id):
        rows.append([B("🗑 Hamma variantlarni o'chirish", "mlv:da")])
    rows.append([B("⬅️ Orqaga", "mly:home")])
    await show(c, "\n".join(lines), kb(rows))


@router.callback_query(F.data == "mlv:up")
async def cb_upload(c: CallbackQuery, state: FSMContext):
    if not await gate(c):
        return
    await clear_keep(state)
    await state.set_state(MlvUp.wait)
    await show(
        c,
        "📦 <b>Variantlarni yuklash</b>\n\n"
        "Zip faylni yuboring: ichida har bir variant alohida fayl (.docx, .txt yoki .xlsx). "
        "Bitta faylni ham yuborsangiz bo'ladi.\n\n"
        "• Har bir fayl — bitta variant: 55 ta test (1–35 yopiq, 36–55 yozma javobga aylanadi) "
        "yoki 1–45 (OCHIQ SAVOLLAR bilan).\n"
        "• To'g'ri javob variant oxirida <code>++</code> yoki <code>Javob: B</code>.\n"
        "• Fayl nomi — variant nomi. Shu nomli variant qayta yuklansa, yangilanadi.\n"
        "• Xato fayl saqlanmaydi, qolganlari saqlanadi.\n"
        "• <b>Izoh va manba</b> (ixtiyoriy): har savolning to'g'ri javobidan keyin "
        "<code>Izoh: …</code> va <code>Manba: …</code> qatorlari — o'quvchi imtihon yopilgach tahlilda ko'radi.\n"
        "• 36–55 da to'g'ri javob bir nechta bo'lsa: <code>Qabul: Samarqand | Qarshi | Termiz</code> — istalgani qabul qilinadi.\n"
        "• 3 til: savol tagida <code>RU:</code> va <code>EN:</code> tarjima bloklari (36–55 da <code>Ответ:</code> / <code>Answer:</code>).\n\n"
        "⚠️ Telegram botga 20 MB dan katta fayl yuborib bo'lmaydi. Katta zipni "
        "<b>veb-ilovadan</b> yuklang (60 MB gacha) yoki 2 ta zipga bo'ling.",
        kb([[B("⬅️ Orqaga", "mlv:l:0")]]),
    )
    await c.answer()


@router.message(StateFilter(MlvUp.wait), F.document)
async def msg_file(m: Message, state: FSMContext):
    uid = m.from_user.id
    if not (is_full_admin(uid) or await is_teacher(uid)):
        return
    doc = m.document
    back = kb([[B("🗂 Variantlar", "mlv:l:0")]])
    if (doc.file_size or 0) > TG_LIMIT:
        await m.answer(
            "❌ Fayl 20 MB dan katta — Telegram bot bunday faylni yuklab ololmaydi.\n\n"
            "Veb-ilovada yuklang: Milliy sertifikat imtihoni → Milliy sertifikatni yig'ish → Tayyor variantlar "
            "(60 MB gacha). Yoki zipni 2 qismga bo'lib yuboring.", reply_markup=back)
        return
    wait = await m.answer("⏳ Fayl tekshirilmoqda… (26 ta variant ~10–30 soniya)")
    try:
        buf = io.BytesIO()
        await m.bot.download(doc, destination=buf)
        res = await asyncio.to_thread(MV.parse_upload, doc.file_name or "", buf.getvalue())
        async with SessionLocal() as s:
            added, updated = await MV.save_variants(s, uid, res["items"])
    except ValueError as exc:
        await wait.edit_text("❌ " + esc(exc), reply_markup=back)
        return
    except Exception:
        log.exception("milliy variant: yuklash")
        await wait.edit_text("❌ Faylni o'qib bo'lmadi. Qaytadan urinib ko'ring.", reply_markup=back)
        return
    await state.set_state(None)
    ok = sum(1 for i in res["items"] if i["ok"])
    head = "✅ <b>Variantlar yuklandi</b>" if ok == len(res["items"]) else "⚠️ <b>Yuklash natijasi</b>"
    text = head + "\n\n" + "\n".join(esc(x) for x in MV.report_lines(res, added, updated))
    warn = [f"{i['title']}: {w}" for i in res["items"] if i["ok"] for w in i["warnings"][:1]]
    if warn:
        text += "\n\n<i>Ogohlantirishlar (saqlandi, lekin e'tibor bering):</i>\n" + "\n".join("• " + esc(short(w, 160)) for w in warn[:6])
    await wait.edit_text(text[:3900], reply_markup=back)


@router.message(StateFilter(MlvUp.wait))
async def msg_other(m: Message):
    await m.answer("Zip yoki .docx / .txt / .xlsx fayl yuboring.", reply_markup=kb([[B("⬅️ Orqaga", "mlv:l:0")]]))


async def _get(c: CallbackQuery, vid: int):
    from ..models import MilliyVariant

    async with SessionLocal() as s:
        v = await s.get(MilliyVariant, vid)
    if v is None:
        await c.answer("Variant topilmadi.", show_alert=True)
    return v


@router.callback_query(F.data.regexp(r"^mlv:v:\d+$"))
async def cb_view(c: CallbackQuery, state: FSMContext):
    if not await gate(c):
        return
    await clear_keep(state)
    v = await _get(c, int(c.data.split(":")[2]))
    if v is None:
        return
    items = MV.variant_payloads(v)
    imgs = sum(1 for p in items if p.get("img"))
    first = next((p for p in items if p.get("k") == "c"), None)
    lines = [
        f"📄 <b>{esc(v.title)}</b>\n",
        f"Yopiq: <b>{v.n_closed}</b> · Yozma: <b>{v.n_open}</b>" + (f" · rasmli: {imgs}" if imgs else ""),
        f"🌐 Tillar: {MV.langs_line(items)}",
        f"Yuklangan: {fmt_datetime(v.updated_at or v.created_at)}",
        f"Shu variantdan ochilgan imtihonlar: <b>{v.used}</b>",
    ]
    if first and first.get("t"):
        lines.append(f"\n1-savol: <i>{esc(short(first['t'], 160))}</i>")
    rows = [[B("🚀 Imtihon qilib ochish", f"mlv:go:{v.id}")]]
    if MV.can_delete(c.from_user.id, v, is_full_admin(c.from_user.id)):
        rows.append([B("🗑 O'chirish", f"mlv:d:{v.id}")])
    rows.append([B("⬅️ Variantlar", "mlv:l:0")])
    await show(c, "\n".join(lines), kb(rows))
    await c.answer()


@router.callback_query(F.data.regexp(r"^mlv:go:\d+$"))
async def cb_go(c: CallbackQuery, state: FSMContext):
    """Variantdan imtihon: keyingi qadamlar oddiy yaratish bilan bir xil (vaqt → guruh → muddat → nom)."""
    from ..services.imtihon import milliy_open_ok
    from .imtihon_ustoz import MILLIY_CLOSED, _ask_duration

    if not await gate(c):
        return
    v = await _get(c, int(c.data.split(":")[2]))
    if v is None:
        return
    items = MV.variant_payloads(v)
    closed = [p for p in items if p.get("k") == "c"]
    opened = [p for p in items if p.get("k") != "c"]
    if len(closed) != MILLIY_CLOSED or not milliy_open_ok(opened):
        await c.answer("Variant savollari to'liq emas. Faylni qayta yuklang.", show_alert=True)
        return
    draft = {
        "mode": "milliy", "c": len(closed), "o": len(opened),
        "avail_c": len(closed), "avail_o": len(opened),
        "sname": "Milliy sertifikat", "tid": None, "tname": None,
        "fixed": closed + opened, "vid": v.id, "vtitle": v.title,
    }
    await state.update_data(d=draft)
    await _ask_duration(c, draft)


@router.callback_query(F.data.regexp(r"^mlv:d:\d+$"))
async def cb_del(c: CallbackQuery):
    if not await gate(c):
        return
    v = await _get(c, int(c.data.split(":")[2]))
    if v is None:
        return
    await show(c, f"🗑 «{esc(v.title)}» varianti o'chirilsinmi?\n\nUndan ochilgan imtihonlar va natijalar o'chmaydi.",
               kb([[B("✅ Ha, o'chir", f"mlv:dok:{v.id}")], [B("⬅️ Yo'q", f"mlv:v:{v.id}")]]))
    await c.answer()


@router.callback_query(F.data.regexp(r"^mlv:dok:\d+$"))
async def cb_del_ok(c: CallbackQuery, state: FSMContext):
    from ..models import MilliyVariant

    if not await gate(c):
        return
    vid = int(c.data.split(":")[2])
    async with SessionLocal() as s:
        v = await s.get(MilliyVariant, vid)
        if v is None or not MV.can_delete(c.from_user.id, v, is_full_admin(c.from_user.id)):
            await c.answer("O'chirib bo'lmadi.", show_alert=True)
            return
        await s.delete(v)
        await s.commit()
    await c.answer("O'chirildi.")
    await _list_view(c, 0)


@router.callback_query(F.data == "mlv:da")
async def cb_del_all(c: CallbackQuery):
    if not is_full_admin(c.from_user.id):
        await c.answer("Faqat admin.", show_alert=True)
        return
    await show(c, "🗑 <b>Hamma variantlar</b> o'chirilsinmi?\n\nUlardan ochilgan imtihonlar va natijalar o'chmaydi.",
               kb([[B("✅ Ha, hammasini o'chir", "mlv:daok")], [B("⬅️ Yo'q", "mlv:l:0")]]))
    await c.answer()


@router.callback_query(F.data == "mlv:daok")
async def cb_del_all_ok(c: CallbackQuery, state: FSMContext):
    from sqlalchemy import delete

    from ..models import MilliyVariant

    if not is_full_admin(c.from_user.id):
        await c.answer("Faqat admin.", show_alert=True)
        return
    async with SessionLocal() as s:
        await s.execute(delete(MilliyVariant))
        await s.commit()
    await c.answer("Hammasi o'chirildi.")
    await _list_view(c, 0)


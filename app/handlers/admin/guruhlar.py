"""Admin panelning guruhlangan bo'limlari: Fanlar, Savol qo'shish, Export / Import.

Bu yerda faqat bo'lim ekranlari bor. Tugmalar eski ishlaydigan amallarga (adm:add_subject,
adm:add_question va h.k.) olib boradi, ularning o'zi o'zgarmagan.
"""
from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from ._common import is_admin

router = Router()


def _kb(rows):
    rows = [[InlineKeyboardButton(text=t, callback_data=d)] for t, d in rows]
    rows.append([InlineKeyboardButton(text="⬅️ Orqaga", callback_data="admin")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


SECTIONS = {
    "fan": (
        "📚 <b>Fanlar</b>\n\nKerakli amalni tanlang:",
        [
            ("➕ Fan qo'shish", "adm:add_subject"),
            ("➕ Mavzu qo'shish", "adm:add_topic"),
            ("🗑 Fanni o'chirish", "adm:delete_subject_menu"),
            ("📋 Fanlar va mavzular ro'yxati", "adm:catalog"),
        ],
    ),
    "sav": (
        "➕ <b>Savol qo'shish</b>\n\n"
        "• <b>Bitta savol</b> — savolni qadamma-qadam kiritasiz.\n"
        "• <b>Bir nechta savol</b> — ko'p savolni bitta xabarda yuborasiz.",
        [
            ("1️⃣ Bitta savol qo'shish", "adm:add_question"),
            ("⚡ Bir nechta savol qo'shish", "adm:add_questions_bulk"),
        ],
    ),
    "exim": (
        "📤 <b>Export / Import</b>\n\nKerakli amalni tanlang:",
        [
            ("📥 Excel/CSV import", "adm:import"),
            ("📄 Word eksport", "adm:export"),
        ],
    ),
}


async def show_section(c: CallbackQuery, state: FSMContext, key: str) -> bool:
    """Bo'lim ekranini ko'rsatadi. Bo'lim topilmasa False qaytaradi."""
    sec = SECTIONS.get(key)
    if not sec:
        return False
    await state.clear()
    await state.update_data(g=key)  # amaldan «Bekor qilish» bosilsa, shu bo'limga qaytiladi
    text, rows = sec
    try:
        await c.message.edit_text(text, reply_markup=_kb(rows))
    except Exception as exc:
        if "not modified" not in str(exc).lower():
            await c.message.answer(text, reply_markup=_kb(rows))
    await c.answer()
    return True


@router.callback_query(F.data.startswith("adm:g:"))
async def cb_section(c: CallbackQuery, state: FSMContext):
    if not is_admin(c.from_user.id):
        await c.answer("Ruxsat yo'q.", show_alert=True)
        return
    key = (c.data or "").split(":", 2)[-1]
    if not await show_section(c, state, key):
        await c.answer()

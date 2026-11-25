"""Bosh menyu: guruh ekranlari (Takrorlash, Imtihon va topshiriqlar, Natijalarim).

Tugmalarning ishi o'zgarmaydi: faqat mavjud tugmalar guruhlarga joylangan.
"""
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

router = Router()

HUBS = {
    "prof": (
        "\U0001F464 <b>Profil</b>\n\nSizning ma'lumotlaringiz, natijalaringiz va o'rningiz:",
        [
            ("\U0001F464 Mening profilim", "profile"),
            ("\U0001F4C8 Bilimlar xaritasi", "km:menu"),
            ("\U0001F3C6 Reyting", "leaderboard"),
            ("\U0001F4CA Statistikam", "stats"),
        ],
    ),
    "test": (
        "\U0001F4DD <b>Sinov va imtihon</b>\n\nBilimingizni sinab ko'ring:",
        [
            ("\U0001F4DA Fanlar", "subjects"),
            ("\U0001F393 Milliy sertifikat imtihoni", "exm:l"),
            ("\U0001F3AF Diagnostika", "diag:menu"),
        ],
    ),
    "rev": (
        "\U0001F9E0 <b>Takrorlash</b>\n\nBilimingizni mustahkamlang va xatolaringiz ustida ishlang:",
        [
            ("\U0001F9E0 Smart Takrorlash", "smart_review"),
            ("❌ Mening xatolarim", "my_errors"),
            ("\U0001F3AF Diagnostika", "diag:menu"),
        ],
    ),
    "exam": (
        "\U0001F4DD <b>Imtihon va topshiriqlar</b>\n\nImtihonlar va ustoz bergan topshiriqlar:",
        [
            ("\U0001F4DD Imtihonlar", "exm:l"),
            ("\U0001F4CB Topshiriqlar", "asg:my"),
        ],
    ),
    "res": (
        "\U0001F4CA <b>Natijalarim</b>\n\nNatijalaringiz va o'rningiz:",
        [
            ("\U0001F4CA Statistikam", "stats"),
            ("\U0001F3C6 Reyting", "leaderboard"),
            ("\U0001F4C8 Bilim xaritasi", "km:menu"),
        ],
    ),
}


def hub_kb(key: str) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=t, callback_data=cb)] for t, cb in HUBS[key][1]]
    rows.append([InlineKeyboardButton(text="⬅️ Bosh menyu", callback_data="home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data.regexp(r"^mn:(prof|test|rev|exam|res)$"))
async def hub(c: CallbackQuery, state: FSMContext):
    key = c.data.split(":", 1)[1]
    await state.clear()
    text, _ = HUBS[key]
    kb = hub_kb(key)
    msg = c.message
    try:
        if getattr(msg, "photo", None):
            await msg.delete()
            await msg.answer(text, reply_markup=kb)
        else:
            await msg.edit_text(text, reply_markup=kb)
    except Exception as exc:
        if "message is not modified" not in str(exc):
            await msg.answer(text, reply_markup=kb)
    await c.answer()


_FILE_ID = None


async def send_welcome(message, text, reply_markup=None):
    """Salomlashuv xabarini chiroyli rasm bilan yuboradi; rasm bo'lmasa oddiy matn."""
    global _FILE_ID
    try:
        from pathlib import Path

        from aiogram.types import FSInputFile

        photo = _FILE_ID or FSInputFile(Path(__file__).resolve().parent.parent / "assets" / "menu_banner.jpg")
        sent = await message.answer_photo(photo, caption=text, reply_markup=reply_markup)
        if getattr(sent, "photo", None):
            _FILE_ID = sent.photo[-1].file_id
        return sent
    except Exception:
        _FILE_ID = None
        return await message.answer(text, reply_markup=reply_markup)


def back_kb(key: str) -> InlineKeyboardMarkup:
    """Ichki ekran uchun: bir qadam orqaga (guruh ekraniga) + Bosh menyu.

    key: 'prof' | 'test' -> mn:<key>;  'profile' -> profil ekrani.
    """
    target = "profile" if key == "profile" else f"mn:{key}"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Orqaga", callback_data=target)],
        [InlineKeyboardButton(text="\U0001F3E0 Bosh menyu", callback_data="home")],
    ])

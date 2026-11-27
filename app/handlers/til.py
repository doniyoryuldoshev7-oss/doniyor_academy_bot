"""Til tanlash (bot): foydalanuvchi savollarni qaysi tilda ko'rishini tanlaydi. Veb-ilova bilan umumiy."""
from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

router = Router()

NAMES = {"uz": "🇺🇿 O'zbekcha", "ru": "🇷🇺 Русский", "en": "🇬🇧 English"}
TEXT = (
    "🌐 <b>Til / Язык / Language</b>\n\n"
    "Test savollari qaysi tilda chiqsin?\n"
    "На каком языке показывать вопросы?\n"
    "In which language should questions be shown?\n\n"
    "Tarjimasi bo'lmagan savollar o'zbekcha chiqadi.\n"
    "Veb-ilovada ham shu til ishlatiladi."
)


def lang_keyboard(current):
    rows = [[InlineKeyboardButton(text=NAMES[k] + ("  ✓" if k == current else ""), callback_data=f"lang:set:{k}")] for k in NAMES]
    rows.append([InlineKeyboardButton(text="⬅️ Bosh menyu", callback_data="home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data == "lang:menu")
async def lang_menu(c: CallbackQuery):
    from ..qlang import user_lang

    cur = await user_lang(c.from_user.id)
    await c.message.edit_text(TEXT, reply_markup=lang_keyboard(cur))
    await c.answer()


@router.callback_query(F.data.regexp(r"^lang:set:(uz|ru|en)$"))
async def lang_set(c: CallbackQuery):
    from ..qlang import set_lang

    lang = await set_lang(c.from_user.id, c.data.split(":")[2])
    try:
        await c.message.edit_text(TEXT, reply_markup=lang_keyboard(lang))
    except Exception:
        pass
    await c.answer({"uz": "✅ O'zbekcha", "ru": "✅ Русский", "en": "✅ English"}[lang])

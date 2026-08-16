from aiogram import Router, F
from aiogram.filters import CommandStart
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext

from sqlalchemy import select

from ..config import settings
from ..db import SessionLocal
from ..models import User
from ..keyboards import main_menu
from ..states import QuizState

router = Router()


@router.message(CommandStart())
async def start_cmd(message: Message, state: FSMContext):
    current_state = await state.get_state()

    # Agar foydalanuvchi test ishlayotgan bo‘lsa
    if current_state == QuizState.active.state:
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="↩️ Testga qaytish",
                        callback_data="start:continue"
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="🚪 Testdan chiqish",
                        callback_data="start:exit"
                    )
                ]
            ]
        )

        await message.answer(
            "⚠️ <b>Siz hozir test ishlayapsiz.</b>\n\n"
            "Testni tark etmoqchimisiz?\n\n"
            "↩️ Testga qaytsangiz, davom etishingiz mumkin.\n"
            "🚪 Chiqsangiz, joriy test yakunlanmaydi.",
            reply_markup=kb
        )
        return

    async with SessionLocal() as s:
        user = await s.scalar(
            select(User).where(
                User.telegram_id == message.from_user.id
            )
        )

        if user is None:
            user = User(
                telegram_id=message.from_user.id,
                username=message.from_user.username,
                full_name=message.from_user.full_name,
            )
            s.add(user)
        else:
            user.username = message.from_user.username
            user.full_name = message.from_user.full_name

        await s.commit()

    await message.answer(
        f"🎓 <b>Doniyor Academy</b>\n\n"
        f"Assalomu alaykum, <b>{message.from_user.first_name}</b>!\n\n"
        f"📚 Bilim oling • 📝 Test ishlang • 🏆 Reytingda yuqorilang",
        reply_markup=main_menu(
            message.from_user.id in settings.admins
        )
    )


@router.callback_query(
    QuizState.active,
    F.data == "start:continue"
)
async def start_continue(c: CallbackQuery, state: FSMContext):
    await c.message.delete()
    await c.answer("↩️ Test davom etmoqda")


@router.callback_query(
    QuizState.active,
    F.data == "start:exit"
)
async def start_exit(c: CallbackQuery, state: FSMContext):
    await state.clear()

    await c.message.edit_text(
        "🏠 <b>Bosh menyu</b>\n\n"
        "Kerakli bo‘limni tanlang:",
        reply_markup=main_menu(
            c.from_user.id in settings.admins
        )
    )

    await c.answer("🚪 Testdan chiqildi")
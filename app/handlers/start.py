from ..tg_retry import retry as _tg_retry
from aiogram import Router, F
from aiogram.filters import CommandStart
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    ReplyKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardRemove,
)
from aiogram.fsm.context import FSMContext

from sqlalchemy import select

from ..config import settings
from ..admin_roles import can_add_questions, is_teacher
from ..db import SessionLocal
from ..models import User
from ..keyboards import main_menu
from ..states import QuizState, RegistrationState

router = Router()


def valid_required(value):
    value = (value or "").strip()

    return bool(value) and value.lower() not in {
        "-",
        "—",
        "–",
        "_",
        ".",
        "yo'q",
        "yo‘q",
        "yoq",
        "none",
        "null",
        "n/a",
        "na",
    }


def registration_complete(user):
    # Telefon endi so'ralmaydi: ism-familiya va guruh yetarli
    return (
        len((user.full_name or "").strip().split()) >= 2
        and valid_required(user.group_name)
    )




async def start_registration(message: Message, state: FSMContext):
    from .royxat import begin
    await begin(message, state)


@router.message(CommandStart())
async def start_cmd(message: Message, state: FSMContext):
    parts = (message.text or "").split(maxsplit=1)
    payload = (
        parts[1].strip()
        if len(parts) > 1
        else ""
    )

    if (
        payload.startswith("gq_")
        and message.chat.type in {
            "group",
            "supergroup",
        }
    ):
        try:
            topic_id = int(payload[3:])
        except ValueError:
            await message.answer(
                "Guruh testi havolasi noto‘g‘ri."
            )
            return

        from .group_quiz import (
            prepare_group_quiz_from_deeplink
        )

        await prepare_group_quiz_from_deeplink(
            message,
            state,
            topic_id,
        )
        return

    if message.chat.type in {"group", "supergroup"}:
        from .group_quiz import (
            prepare_group_quiz_from_deeplink
        )
        from ..models import GroupQuiz

        pending_id = None
        pending_topic_id = None

        async with SessionLocal() as s:
            pending = await s.scalar(
                select(GroupQuiz)
                .where(
                    GroupQuiz.creator_telegram_id
                    == message.from_user.id,
                    GroupQuiz.status == "pending",
                )
                .order_by(GroupQuiz.id.desc())
            )

            if pending is not None:
                pending_id = pending.id
                pending_topic_id = pending.topic_id

        if pending_topic_id is not None:
            await prepare_group_quiz_from_deeplink(
                message,
                state,
                int(pending_topic_id),
            )

            if pending_id is not None:
                async with SessionLocal() as s:
                    pending = await s.get(
                        GroupQuiz,
                        pending_id,
                    )
                    if (
                        pending is not None
                        and pending.status == "pending"
                    ):
                        await s.delete(pending)
                        await s.commit()

            return

        # guruh: oddiy /start menyu ochmaydi
        return

    current_state = await state.get_state()

    # Agar foydalanuvchi test ishlayotgan bo‘lsa
    if current_state == QuizState.active.state:
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="↩️ Testga qaytish",
                        callback_data="start:continue",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="🚪 Testdan chiqish",
                        callback_data="start:exit",
                    )
                ],
            ]
        )

        await message.answer(
            "⚠️ <b>Siz hozir test ishlayapsiz.</b>\n\n"
            "Testni tark etmoqchimisiz?\n\n"
            "↩️ Testga qaytsangiz, davom etishingiz mumkin.\n"
            "🚪 Chiqsangiz, joriy test yakunlanmaydi.",
            reply_markup=kb,
        )
        return

    # Adminlar ro‘yxatdan o‘tish tizimidan mustaqil ishlaydi
    if message.from_user.id in settings.admins:
        await state.clear()

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
                    registration_status="approved",
                )
                s.add(user)
            else:
                user.username = message.from_user.username
                user.full_name = message.from_user.full_name
                user.registration_status = "approved"

            await s.commit()

        await message.answer(
            f"🎓 <b>Doniyor Academy</b>\n\n"
            f"Assalomu alaykum, "
            f"<b>{message.from_user.first_name}</b>!\n\n"
            f"📚 Bilim oling • 📝 Test ishlang • 🏆 Reytingda yuqorilang",
            reply_markup=main_menu(True, is_teacher=await is_teacher(message.from_user.id)),
        )
        return

    async with SessionLocal() as s:
        user = await s.scalar(
            select(User).where(
                User.telegram_id == message.from_user.id
            )
        )

        # Yangi foydalanuvchi
        if user is None:
            await start_registration(message, state)
            return

        # Bloklangan foydalanuvchi
        if user.is_blocked:
            await state.clear()
            await message.answer(
                "<b>Sizning hisobingiz bloklangan.</b>\n\n"
                "Botdan foydalanish uchun administratorga murojaat qiling."
            )
            return

        # Majburiy ma'lumotlari to'liq bo'lmasa
        # foydalanuvchi qayta ro'yxatdan o'tadi.
        if not registration_complete(user):
            if user.registration_status == "approved":
                user.registration_status = "pending"

            await s.commit()

            await message.answer(
                "\u26a0\ufe0f <b>Ro'yxatdan o'tish yakunlanmagan!</b>\n\n"
                "Botdan foydalanish uchun ism-familiyangizni, "
                "telefon raqamingizni va guruhingizni to'liq kiriting.\n\n"
                "Barcha ma'lumotlar to'ldirilmaguncha arizangiz "
                "administratorga yuborilmaydi."
            )

            await start_registration(message, state)
            return

        # Foydalanuvchi ma'lumotlarini yangilab turamiz
        user.username = message.from_user.username

        # Tasdiqlangan foydalanuvchi
        if user.registration_status == "approved":
            await s.commit()

            await state.clear()

            from .menyu import send_welcome

            await send_welcome(
                message,
                f"🎓 <b>Doniyor Academy</b>\n\n"
                f"Assalomu alaykum, "
                f"<b>{message.from_user.first_name}</b>!\n\n"
                f"📚 Bilim oling • 📝 Test ishlang • 🏆 Reytingda yuqorilang",
                reply_markup=ReplyKeyboardRemove(),
            )

            await message.answer(
                "Bosh menyu",
                reply_markup=main_menu(
                    await can_add_questions(message.from_user.id),
                    is_teacher=await is_teacher(message.from_user.id),
                ),
            )
            return

        # Tasdiqlanishini kutayotgan foydalanuvchi
        if user.registration_status == "pending":
            await s.commit()
            await state.clear()

            await message.answer(
                "⏳ <b>Arizangiz hali tasdiqlanmagan.</b>\n\n"
                "Administrator arizangizni ko‘rib chiqishini kuting."
            )
            return

        # Rad etilgan foydalanuvchiga qayta ro‘yxatdan o‘tish imkoniyati
        if user.registration_status == "rejected":
            await s.commit()
            await start_registration(message, state)
            return

        await s.commit()
        await start_registration(message, state)












@router.callback_query(
    QuizState.active,
    F.data == "start:continue",
)
async def start_continue(
    c: CallbackQuery,
    state: FSMContext,
):
    await c.message.delete()
    await c.answer("↩️ Test davom etmoqda")


@router.callback_query(
    QuizState.active,
    F.data == "start:exit",
)
async def start_exit(
    c: CallbackQuery,
    state: FSMContext,
):
    await state.clear()

    await c.message.edit_text(
        "🏠 <b>Bosh menyu</b>\n\n"
        "Kerakli bo‘limni tanlang:",
        reply_markup=main_menu(
            await can_add_questions(c.from_user.id),
            is_teacher=await is_teacher(c.from_user.id),
        ),
    )

    await c.answer("🚪 Testdan chiqildi")

# Yangi ro'yxatdan o'tish: telefonsiz, guruh ro'yxati, imtihon maqsadi, admin shablonlari
from .royxat import setup as _royxat_setup
_royxat_setup(router)

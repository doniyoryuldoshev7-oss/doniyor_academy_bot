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
from ..db import SessionLocal
from ..models import User
from ..keyboards import main_menu
from ..states import QuizState, RegistrationState

router = Router()


def valid_required(value):
    value = (value or "").strip()

    return bool(value) and value.lower() not in {
        "-",
        "?",
        "?",
        "_",
        ".",
        "yo'q",
        "yo?q",
        "yoq",
        "none",
        "null",
        "n/a",
        "na",
    }


def registration_complete(user):
    return (
        len((user.full_name or "").strip().split()) >= 2
        and valid_required(user.phone_number)
        and valid_required(user.group_name)
    )


def contact_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(
                    text="📱 Kontaktni yuborish",
                    request_contact=True,
                )
            ]
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


async def start_registration(message: Message, state: FSMContext):
    await state.clear()
    await state.set_state(RegistrationState.first_name)

    await message.answer(
        "🎓 <b>Doniyor Academy</b>\n\n"
        "Botdan foydalanish uchun ro‘yxatdan o‘tishingiz kerak.\n\n"
        "1️⃣ <b>Ismingizni</b> kiriting:",
        reply_markup=ReplyKeyboardRemove(),
    )


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
            reply_markup=main_menu(True),
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
                "? <b>Profil ma'lumotlaringiz to'liq emas.</b>\n\n"
                "Iltimos, ism, familiya, o'zingizning Telegram "
                "kontaktingiz va guruhingizni to'liq kiriting."
            )

            await start_registration(message, state)
            return

        # Foydalanuvchi ma'lumotlarini yangilab turamiz
        user.username = message.from_user.username

        # Tasdiqlangan foydalanuvchi
        if user.registration_status == "approved":
            await s.commit()

            await state.clear()

            await message.answer(
                f"🎓 <b>Doniyor Academy</b>\n\n"
                f"Assalomu alaykum, "
                f"<b>{message.from_user.first_name}</b>!\n\n"
                f"📚 Bilim oling • 📝 Test ishlang • 🏆 Reytingda yuqorilang",
                reply_markup=ReplyKeyboardRemove(),
            )

            await message.answer(
                "Bosh menyu",
                reply_markup=main_menu(False),
            )
            return

        # Tasdiqlanishini kutayotgan foydalanuvchi
        if user.registration_status == "pending":
            await s.commit()
            await state.clear()

            await message.answer(
                "вЏі <b>Arizangiz hali tasdiqlanmagan.</b>\n\n"
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


@router.message(RegistrationState.first_name)
async def registration_first_name(
    message: Message,
    state: FSMContext,
):
    first_name = (message.text or "").strip()

    if not valid_required(first_name):
        await message.answer(
            "❗ Iltimos, ismingizni matn ko‘rinishida kiriting."
        )
        return

    await state.update_data(first_name=first_name)
    await state.set_state(RegistrationState.last_name)

    await message.answer(
        "2️⃣ <b>Familiyangizni</b> kiriting:"
    )


@router.message(RegistrationState.last_name)
async def registration_last_name(
    message: Message,
    state: FSMContext,
):
    last_name = (message.text or "").strip()

    if not valid_required(last_name):
        await message.answer(
            "❗ Iltimos, familiyangizni matn ko‘rinishida kiriting."
        )
        return

    await state.update_data(last_name=last_name)
    await state.set_state(RegistrationState.phone)

    await message.answer(
        "3️⃣ <b>Telefon raqamingizni</b> yuboring.\n\n"
        "Quyidagi tugmani bosing:",
        reply_markup=contact_kb(),
    )


@router.message(RegistrationState.phone, F.contact)
async def registration_phone(
    message: Message,
    state: FSMContext,
):
    contact = message.contact

    if contact is None:
        await message.answer(
            "❗ Kontakt ma'lumoti olinmadi."
        )
        return

    # Foydalanuvchi faqat o‘z kontaktini yuborishi kerak
    if contact.user_id != message.from_user.id:
        await message.answer(
            "❗ Iltimos, <b>o‘zingizning telefon raqamingizni</b> "
            "Telegram kontakt tugmasi orqali yuboring."
        )
        return

    phone_number = (contact.phone_number or "").strip()

    if not valid_required(phone_number):
        await message.answer(
            "? Telefon raqamingiz olinmadi.\n\n"
            "?? <b>Kontaktni yuborish</b> tugmasini bosing.",
            reply_markup=contact_kb(),
        )
        return

    await state.update_data(
        phone_number=phone_number
    )
    await state.set_state(RegistrationState.group_name)

    await message.answer(
        "4️⃣ <b>Guruhingiz qaysi?</b>\n\n"
        "Masalan: <b>Tarix-01</b> yoki <b>Abituriyent A</b>.",
        reply_markup=ReplyKeyboardRemove(),
    )


@router.message(RegistrationState.phone)
async def registration_phone_invalid(
    message: Message,
):
    await message.answer(
        "❗ Telefon raqamingizni Telegram orqali yuboring.\n\n"
        "📱 <b>Kontaktni yuborish</b> tugmasini bosing.",
        reply_markup=contact_kb(),
    )


@router.message(RegistrationState.group_name)
async def registration_group_name(
    message: Message,
    state: FSMContext,
):
    group_name = (message.text or "").strip()

    if not valid_required(group_name):
        await message.answer(
            "❗ Iltimos, guruhingizni kiriting."
        )
        return

    data = await state.get_data()

    first_name = data.get("first_name")
    last_name = data.get("last_name")
    phone_number = data.get("phone_number")

    if not all(
        valid_required(value)
        for value in (
            first_name,
            last_name,
            phone_number,
            group_name,
        )
    ):
        await state.clear()
        await message.answer(
            "❗ Ro‘yxatdan o‘tish ma'lumotlarida xatolik yuz berdi.\n\n"
            "Iltimos, /start buyrug‘ini qayta bosing."
        )
        return

    full_name = f"{first_name} {last_name}"

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
                full_name=full_name,
                phone_number=phone_number,
                group_name=group_name,
                registration_status="pending",
                is_blocked=False,
            )
            s.add(user)
        else:
            user.username = message.from_user.username
            user.full_name = full_name
            user.phone_number = phone_number
            user.group_name = group_name
            user.registration_status = "pending"

        await s.commit()

    # Yangi ariza haqida administratorlarga xabar
    admin_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Tasdiqlash", callback_data=f"adm:approve_user:{message.from_user.id}"),
                InlineKeyboardButton(text="❌ Rad etish", callback_data=f"adm:reject_user:{message.from_user.id}"),
            ]
        ]
    )

    username = f"@{message.from_user.username}" if message.from_user.username else "yo‘q"

    admin_text = (
        "🔔 <b>Yangi ro‘yxatdan o‘tish arizasi!</b>\n\n"
        f"👤 Ism-familiya: <b>{full_name}</b>\n"
        f"📱 Telefon: <b>{phone_number}</b>\n"
        f"🎓 Guruh: <b>{group_name}</b>\n"
        f"🆔 Telegram ID: <code>{message.from_user.id}</code>\n"
        f"👤 Username: <b>{username}</b>"
    )

    for admin_id in settings.admins:
        try:
            await message.bot.send_message(
                admin_id,
                admin_text,
                reply_markup=admin_kb,
            )
        except Exception:
            pass

    await state.clear()

    await message.answer(
        "✅ <b>Ro‘yxatdan o‘tish yakunlandi.</b>\n\n"
        f"👤 Ism-familiya: <b>{full_name}</b>\n"
        f"📱 Telefon: <b>{phone_number}</b>\n"
        f"🎓 Guruh: <b>{group_name}</b>\n\n"
        "вЏі Ma'lumotlaringiz administratorga yuborildi.\n"
        "Tasdiqlangandan so‘ng Doniyor Academy’dan "
        "foydalanishingiz mumkin.",
        reply_markup=ReplyKeyboardRemove(),
    )


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
            c.from_user.id in settings.admins
        ),
    )

    await c.answer("🚪 Testdan chiqildi")


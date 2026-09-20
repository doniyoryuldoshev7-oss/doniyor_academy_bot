import sys
import asyncio
import json
from pathlib import Path
from uuid import uuid4
from ..models import Subject, Topic, Question, User, TestAttempt, AnswerLog
from ..keyboards import main_menu
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    ReplyKeyboardMarkup,
    KeyboardButton,
    WebAppInfo,
    ReplyKeyboardRemove,
)
from aiogram.fsm.context import FSMContext
from sqlalchemy import select, func

from ..config import settings
from ..db import SessionLocal
from ..models import Subject, Topic, Question, User
from ..states import AdminState
from ..image_cropper import crop_questions
from ..web_crop import create_session, web_app_url, finish_session, get_session

router = Router()


async def photo_bytes(m: Message):
    if not m.photo:
        return None

    photo = m.photo[-1]
    bot = m.bot

    file = await bot.get_file(photo.file_id)

    from io import BytesIO
    data = BytesIO()
    await bot.download_file(file.file_path, data)

    return data.getvalue()


async def save_question_image(image_data: bytes, question_id: int) -> str:
    folder = Path("app/question_images")
    folder.mkdir(parents=True, exist_ok=True)

    filename = f"question_{question_id}.jpg"
    path = folder / filename

    path.write_bytes(image_data)

    return path.as_posix()




def is_admin(uid: int) -> bool:
    return uid in settings.admins


def admin_menu():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="➕ Fan qo'shish", callback_data="adm:add_subject"
                )
            ],
            [
                InlineKeyboardButton(
                    text="➕ Mavzu qo'shish", callback_data="adm:add_topic"
                )
            ],
            [
                InlineKeyboardButton(
                    text="➕ Savol qo'shish", callback_data="adm:add_question"
                )
            ],
            [
                InlineKeyboardButton(
                    text="📥 Excel/CSV import", callback_data="adm:import"
                )
            ],
            [
                InlineKeyboardButton(
                    text="📚 Fanlar / mavzular", callback_data="adm:catalog"
                )
            ],
            [
                InlineKeyboardButton(
                    text="\U0001f5d1 Fanni o\u2018chirish",
                    callback_data="adm:delete_subject_menu",
                )
            ],
            [
                InlineKeyboardButton(
                    text="\U0001f465 Foydalanuvchilar",
                    callback_data="adm:users",
                )
            ],            [InlineKeyboardButton(text="📊 Statistika", callback_data="adm:stats")],
            [InlineKeyboardButton(text="📢 E'lonlar", callback_data="adm:send_ad")],
            [InlineKeyboardButton(text="❌ Bekor qilish", callback_data="adm:cancel")],
        ]
    )


def cancel_kb():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="❌ Bekor qilish", callback_data="adm:cancel")]
        ]
    )


def subject_kb(subjects, prefix="adm:qsub"):
    rows = [
        [InlineKeyboardButton(text=f"📚 {s.name}", callback_data=f"{prefix}:{s.id}")]
        for s in subjects
    ]
    rows.append(
        [InlineKeyboardButton(text="❌ Bekor qilish", callback_data="adm:cancel")]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def topic_kb(topics, prefix="adm:qtopic"):
    rows = [
        [InlineKeyboardButton(text=f"📖 {t.name}", callback_data=f"{prefix}:{t.id}")]
        for t in topics
    ]
    rows.append(
        [InlineKeyboardButton(text="⬅️ Fan tanlash", callback_data="adm:add_question")]
    )
    rows.append(
        [InlineKeyboardButton(text="❌ Bekor qilish", callback_data="adm:cancel")]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def show_admin(target, answer=True):
    text = "⚙️ <b>Doniyor Academy — Admin panel</b>\n\n" "Kerakli amalni tanlang:"

    if isinstance(target, CallbackQuery):
        try:
            await target.message.edit_text(text, reply_markup=admin_menu())
        except Exception as e:
            if "message is not modified" not in str(e):
                raise

        if answer:
            await target.answer()

    else:
        await target.answer(text, reply_markup=admin_menu())


@router.message(Command("admin"))
async def cmd_admin(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return
    await state.clear()
    await show_admin(m, answer=False)


@router.callback_query(F.data == "admin")
async def cb_admin(c: CallbackQuery, state: FSMContext):
    if not is_admin(c.from_user.id):
        await c.answer("Sizda admin huquqi yo'q.", show_alert=True)
        return
    await state.clear()
    await show_admin(c)


# Admin faylingizning tepasida main_menu import qilinganiga ishonch hosil qiling:
# from ..keyboards import main_menu


@router.callback_query(F.data == "adm:cancel")
async def cancel(c: CallbackQuery, state: FSMContext):
    if not is_admin(c.from_user.id):
        return

    # 1. Admin holatlarini tozalaymiz
    await state.clear()

    # 2. Sizning asl bosh menyu tugmalaringizni olamiz
    user_menu = main_menu()

    # 3. Agar foydalanuvchi admin bo'lsa, menyuning eng tagiga "Admin panel" tugmasini qo'shamiz
    # inline_keyboard bu massiv, unga yangi qator qo'shamiz
    admin_btn = [InlineKeyboardButton(text="⚙️ Admin panel", callback_data="admin")]
    if admin_btn not in user_menu.inline_keyboard:
        user_menu.inline_keyboard.append(admin_btn)

    # 4. Xabarni yangilaymiz, endi "Admin panel" tugmasi joyida bo'ladi!
    await c.message.edit_text(
        "🏠 <b>Doniyor Academy</b>\n\nBosh menyu:",
        reply_markup=user_menu,
        parse_mode="HTML",
    )
    await c.answer()


# -------------------- SUBJECT --------------------
@router.callback_query(F.data == "adm:add_subject")
async def add_subject_start(c: CallbackQuery, state: FSMContext):
    if not is_admin(c.from_user.id):
        return
    await state.set_state(AdminState.add_subject_name)
    await c.message.edit_text(
        "➕ <b>Yangi fan</b>\n\nFan nomini yuboring:\n\nMasalan: <i>O'zbekiston tarixi</i>",
        reply_markup=cancel_kb(),
    )
    await c.answer()


@router.message(AdminState.add_subject_name)
async def add_subject_finish(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return
    name = (m.text or "").strip()
    if not name:
        await m.answer("❗ Fan nomi bo'sh bo'lishi mumkin emas.")
        return
    async with SessionLocal() as s:
        exists = await s.scalar(
            select(Subject).where(func.lower(Subject.name) == name.lower())
        )
        if exists:
            await m.answer("⚠️ Bu fan allaqachon mavjud.")
            return
        s.add(Subject(name=name))
        await s.commit()
    await state.clear()
    await m.answer(f"✅ Fan qo'shildi: <b>{name}</b>", reply_markup=admin_menu())


# -------------------- TOPIC --------------------
@router.callback_query(F.data == "adm:add_topic")
async def add_topic_start(c: CallbackQuery, state: FSMContext):
    if not is_admin(c.from_user.id):
        return
    async with SessionLocal() as s:
        subjects = (await s.scalars(select(Subject).order_by(Subject.name))).all()
    if not subjects:
        await c.answer("Avval fan qo'shing.", show_alert=True)
        return
    await state.set_state(AdminState.add_topic_subject)
    await c.message.edit_text(
        "📚 <b>Mavzu qo'shish</b>\n\nFanni tanlang:",
        reply_markup=subject_kb(subjects, "adm:tsub"),
    )
    await c.answer()


@router.callback_query(F.data.startswith("adm:tsub:"))
async def add_topic_subject(c: CallbackQuery, state: FSMContext):
    if not is_admin(c.from_user.id):
        return
    sid = int(c.data.split(":")[2])
    await state.update_data(subject_id=sid)
    await state.set_state(AdminState.add_topic_name)
    await c.message.edit_text("📖 Mavzu nomini yuboring:", reply_markup=cancel_kb())
    await c.answer()


@router.message(AdminState.add_topic_name)
async def add_topic_finish(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return
    name = (m.text or "").strip()
    data = await state.get_data()
    async with SessionLocal() as s:
        subject = await s.get(Subject, data["subject_id"])
        if not subject:
            await state.clear()
            await m.answer("❌ Fan topilmadi.", reply_markup=admin_menu())
            return
        exists = await s.scalar(
            select(Topic).where(
                Topic.subject_id == subject.id, func.lower(Topic.name) == name.lower()
            )
        )
        if exists:
            await m.answer("⚠️ Bu mavzu allaqachon mavjud.")
            return
        s.add(Topic(subject_id=subject.id, name=name))
        await s.commit()
    await state.clear()
    await m.answer(
        f"✅ <b>{subject.name}</b> faniga <b>{name}</b> mavzusi qo'shildi.",
        reply_markup=admin_menu(),
    )


# -------------------- QUESTION --------------------
@router.callback_query(F.data == "adm:add_question")
async def add_question_start(c: CallbackQuery, state: FSMContext):
    if not is_admin(c.from_user.id):
        return
    async with SessionLocal() as s:
        subjects = (await s.scalars(select(Subject).order_by(Subject.name))).all()
    if not subjects:
        await c.answer("Avval fan qo'shing.", show_alert=True)
        return
    await state.set_state(AdminState.add_question_subject)
    await c.message.edit_text(
        "📋 <b>Yangi test savoli</b>\n\nFanni tanlang:",
        reply_markup=subject_kb(subjects),
    )
    await c.answer()


@router.callback_query(F.data.startswith("adm:qsub:"))
async def add_question_subject(c: CallbackQuery, state: FSMContext):
    if not is_admin(c.from_user.id):
        return
    sid = int(c.data.split(":")[2])
    async with SessionLocal() as s:
        topics = (
            await s.scalars(
                select(Topic).where(Topic.subject_id == sid).order_by(Topic.name)
            )
        ).all()
        subject = await s.get(Subject, sid)
    if not topics:
        await c.answer(
            "Bu fanda hali mavzu yo'q. Avval mavzu qo'shing.", show_alert=True
        )
        return
    await state.update_data(subject_id=sid)
    await state.set_state(AdminState.add_question_topic)
    await c.message.edit_text(
        f"📚 <b>{subject.name}</b>\n\nMavzuni tanlang:", reply_markup=topic_kb(topics)
    )
    await c.answer()


@router.callback_query(F.data.startswith("adm:qtopic:"))
async def add_question_topic(c: CallbackQuery, state: FSMContext):
    if not is_admin(c.from_user.id):
        return
    tid = int(c.data.split(":")[2])
    async with SessionLocal() as s:
        topic = await s.get(Topic, tid)
    await state.update_data(topic_id=tid)
    await state.set_state(AdminState.add_question_text)
    await c.message.edit_text(
        f"📖 <b>{topic.name}</b>\n\n1️⃣ Savol matnini yuboring:",
        reply_markup=cancel_kb(),
    )
    await c.answer()


@router.message(AdminState.add_question_text)
async def q_text(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return

    image_data = await photo_bytes(m)

    if image_data:
        session_id = uuid4().hex
        temp_dir = Path("app/question_images/_crop_sessions") / session_id
        temp_dir.mkdir(parents=True, exist_ok=True)

        image_path = temp_dir / "source.jpg"
        image_path.write_bytes(image_data)

        await state.update_data(
            text="",
            image_data=None,
            question_mode="image_multi",
            image_source_path=str(image_path),
            crop_dir=str(temp_dir),
        )

        await state.set_state(AdminState.add_question_image_count)

        await m.answer(
            "🖼️ <b>Rasm qabul qilindi.</b>\n\n"
            "Bu rasmda nechta savol bor?\n"
            "Masalan: <b>3</b>",
            reply_markup=cancel_kb(),
        )
        return

    value = (m.text or "").strip()

    if not value:
        await m.answer(
            "❗ Savol sifatida matn yoki surat yuboring."
        )
        return

    await state.update_data(
        text=value,
        image_data=None,
        question_mode="closed"
    )

    await state.set_state(AdminState.add_question_a)

    await m.answer(
        "2️⃣ <b>A variant</b>ni yuboring:",
        reply_markup=cancel_kb()
    )


@router.message(AdminState.add_question_image_count)
async def q_image_count(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return

    value = (m.text or "").strip()

    try:
        count = int(value)
    except ValueError:
        await m.answer(
            "❗ Faqat savollar sonini raqam bilan yuboring.\n"
            "Masalan: <b>3</b>"
        )
        return

    if count < 1 or count > 50:
        await m.answer(
            "❗ Savollar soni 1 dan 50 gacha bo'lishi kerak."
        )
        return

    data = await state.get_data()
    image_path = data.get("image_source_path")
    crop_dir = data.get("crop_dir")

    if not image_path or not crop_dir:
        await m.answer(
            "❗ Rasm sessiyasi topilmadi. Iltimos, qaytadan boshlang."
        )
        await state.clear()
        return

    session_id = create_session(
        admin_id=m.from_user.id,
        image_path=image_path,
        crop_dir=crop_dir,
        count=count,
    )

    app_url = web_app_url(session_id)

    if not app_url:
        await m.answer(
            "❗ Web App manzili sozlanmagan.\n\n"
            "Railway'da Public Domain yarating yoki "
            "<b>WEB_APP_URL</b> environment variable o'rnating."
        )
        finish_session(session_id)
        await state.clear()
        return

    await state.update_data(
        image_count=count,
        current_image_index=0,
        cropped_images=[],
        crop_session_id=session_id,
    )

    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(
                    text="✂️ Savollarni belgilash",
                    web_app=WebAppInfo(url=app_url),
                )
            ]
        ],
        resize_keyboard=True,
        one_time_keyboard=False,
    )

    await state.set_state(AdminState.add_question_crop)

    await m.answer(
        "✅ <b>Rasm tayyor.</b>\n\n"
        f"Jami: <b>{count}</b> ta savol.\n\n"
        "Quyidagi tugmani bosing va har bir savolni "
        "to'rtburchak qilib belgilang.",
        reply_markup=keyboard,
    )


@router.message(AdminState.add_question_crop, F.web_app_data)
async def q_image_crop_done(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return

    try:
        payload = json.loads(m.web_app_data.data)
    except Exception:
        await m.answer(
            "❗ Web App ma'lumoti noto'g'ri."
        )
        return

    if payload.get("action") != "done":
        return

    session_id = payload.get("session_id")

    data = await state.get_data()

    if session_id != data.get("crop_session_id"):
        await m.answer(
            "❗ Crop sessiyasi mos kelmadi."
        )
        return

    session = get_session(session_id)

    if not session:
        await m.answer(
            "❗ Crop sessiyasi topilmadi yoki muddati tugagan."
        )
        await state.clear()
        return

    if int(session["admin_id"]) != int(m.from_user.id):
        await m.answer(
            "❗ Bu crop sessiyasi sizga tegishli emas."
        )
        return

    crop_paths = []

    for i in range(1, int(session["count"]) + 1):
        crop_path = Path(session["crop_dir"]) / f"crop_{i}.jpg"

        if crop_path.exists():
            crop_paths.append(str(crop_path))

    if len(crop_paths) != int(session["count"]):
        await m.answer(
            "❗ Belgilangan savollar soni kutilgan songa teng emas.\n"
            f"Kutilgan: {session['count']}\n"
            f"Topilgan: {len(crop_paths)}"
        )
        return

    finish_session(session_id)

    await state.update_data(
        cropped_images=crop_paths,
        current_image_index=0,
    )

    await state.set_state(AdminState.add_question_correct)

    await m.answer(
        f"✅ <b>{len(crop_paths)} ta savol rasmi tayyor.</b>\n\n"
        "1-savol uchun to'g'ri javob harfini yuboring: "
        "<b>A</b>, <b>B</b>, <b>C</b> yoki <b>D</b>",
        reply_markup=ReplyKeyboardRemove(),
    )



async def save_option(
    m: Message,
    state: FSMContext,
    key: str,
    next_state,
    label: str
):
    if not is_admin(m.from_user.id):
        return

    value = (m.text or "").strip()

    if not value:
        await m.answer("❗ Variant bo'sh bo'lishi mumkin emas.")
        return

    await state.update_data(**{key: value})
    await state.set_state(next_state)
    await m.answer(label, reply_markup=cancel_kb())


@router.message(AdminState.add_question_a)
async def q_a(m, state):
    await save_option(
        m,
        state,
        "option_a",
        AdminState.add_question_b,
        "3️⃣ <b>B variant</b>ni yuboring:",
    )


@router.message(AdminState.add_question_b)
async def q_b(m, state):
    await save_option(
        m,
        state,
        "option_b",
        AdminState.add_question_c,
        "4️⃣ <b>C variant</b>ni yuboring:",
    )


@router.message(AdminState.add_question_c)
async def q_c(m, state):
    await save_option(
        m,
        state,
        "option_c",
        AdminState.add_question_d,
        "5️⃣ <b>D variant</b>ni yuboring:",
    )


@router.message(AdminState.add_question_d)
async def q_d(m, state):
    await save_option(
        m,
        state,
        "option_d",
        AdminState.add_question_correct,
        "6️⃣ To'g'ri javob harfini yuboring: "
        "<b>A</b>, <b>B</b>, <b>C</b> yoki <b>D</b>",
    )


@router.message(AdminState.add_question_correct)
async def q_correct(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return

    correct = (m.text or "").strip().upper()

    if correct not in {"A", "B", "C", "D"}:
        await m.answer(
            "❗ Faqat A, B, C yoki D yuboring."
        )
        return

    data = await state.get_data()

    if data.get("question_mode") == "image_multi":
        current_index = data.get("current_image_index", 0)
        cropped_images = data.get("cropped_images", [])

        if current_index >= len(cropped_images):
            await m.answer(
                "❗ Savol rasmi topilmadi. Jarayonni qaytadan boshlang."
            )
            await state.clear()
            return

        answers = data.get("image_correct_answers", [])
        answers.append(correct)

        await state.update_data(
            image_correct_answers=answers,
            current_correct_option=correct,
        )

        await state.set_state(AdminState.add_question_explanation)

        await m.answer(
            f"📝 <b>{current_index + 1}-savol</b> uchun izohni yuboring.\n"
            "Izoh kerak bo'lmasa <code>-</code> yuboring:",
            reply_markup=cancel_kb(),
        )
        return

    await state.update_data(correct_option=correct)
    await state.set_state(AdminState.add_question_explanation)

    await m.answer(
        "7️⃣ <b>Izoh</b>ni yuboring. "
        "Agar izoh kerak bo'lmasa, <code>-</code> yuboring:",
        reply_markup=cancel_kb(),
    )


@router.message(AdminState.add_question_explanation)
async def q_explanation(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return

    explanation = (m.text or "").strip()
    explanation = None if explanation == "-" else explanation

    data = await state.get_data()

    if data.get("question_mode") == "image_multi":
        current_index = data.get("current_image_index", 0)
        cropped_images = data.get("cropped_images", [])
        correct_answers = data.get("image_correct_answers", [])
        topic_id = data["topic_id"]

        if current_index >= len(cropped_images):
            await m.answer(
                "❗ Savol rasmi topilmadi. Jarayonni qaytadan boshlang."
            )
            await state.clear()
            return

        correct = correct_answers[current_index]
        crop_path = Path(cropped_images[current_index])

        if not crop_path.exists():
            await m.answer(
                "❗ Savol rasmi fayli topilmadi. Jarayon bekor qilindi."
            )
            await state.clear()
            return

        async with SessionLocal() as s:
            topic = await s.get(Topic, topic_id)

            if not topic:
                await m.answer(
                    "❗ Mavzu topilmadi."
                )
                await state.clear()
                return

            q = Question(
                topic_id=topic.id,
                text="",
                option_a="",
                option_b="",
                option_c="",
                option_d="",
                correct_option=correct,
                explanation=explanation,
                question_mode="image",
            )

            s.add(q)
            await s.commit()
            qid = q.id

            q.image_path = await save_question_image(
                crop_path.read_bytes(),
                qid,
            )

            await s.commit()

        next_index = current_index + 1

        if next_index < len(cropped_images):
            await state.update_data(
                current_image_index=next_index,
            )

            await state.set_state(
                AdminState.add_question_correct
            )

            await m.answer(
                f"✅ <b>{current_index + 1}-savol saqlandi.</b>\n\n"
                f"🎯 <b>{next_index + 1}-savol</b> uchun "
                "to'g'ri javobni yuboring: "
                "<b>A</b>, <b>B</b>, <b>C</b> yoki <b>D</b>",
                reply_markup=cancel_kb(),
            )
            return

        total = len(cropped_images)
        topic_name = topic.name

        await state.clear()

        await m.answer(
            f"✅ <b>{total} ta savol muvaffaqiyatli qo'shildi!</b>\n\n"
            f"📖 Mavzu: <b>{topic_name}</b>",
            reply_markup=admin_menu(),
        )
        return

    async with SessionLocal() as s:
        topic = await s.get(Topic, data["topic_id"])

        q = Question(
            topic_id=topic.id,
            text=data["text"],
            option_a=data["option_a"],
            option_b=data["option_b"],
            option_c=data["option_c"],
            option_d=data["option_d"],
            correct_option=data["correct_option"],
            explanation=explanation,
        )

        s.add(q)
        await s.commit()
        qid = q.id

        if data.get("image_data"):
            q.image_path = await save_question_image(
                data["image_data"],
                qid,
            )
            q.question_mode = "image"
            await s.commit()

    await state.clear()

    await m.answer(
        f"✅ <b>Savol muvaffaqiyatli qo'shildi!</b>\n\n"
        f"🆔 ID: <code>{qid}</code>\n"
        f"📖 Mavzu: <b>{topic.name}</b>\n"
        f"✅ To'g'ri javob: <b>{data['correct_option']}</b>",
        reply_markup=admin_menu(),
    )


# -------------------- EXCEL / CSV IMPORT --------------------
@router.callback_query(F.data == "adm:import")
async def import_start(c: CallbackQuery, state: FSMContext):
    if not is_admin(c.from_user.id):
        return

    await state.set_state(AdminState.import_file)

    await c.message.edit_text(
        "<b>Excel/CSV orqali test import</b>\n\n"
        "Faylni yuboring: <b>.xlsx</b> yoki <b>.csv</b>\n\n"
        "<b>Ustunlar:</b>\n"
        "<code>Fan | Mavzu | Savol | Savol rasmi | A | B | C | D | "
        "Savol turi | To'g'ri javob | To'g'ri javob matni | Izoh</code>\n\n"
        "Eski oddiy format ham ishlaydi.\n"
        "Savol turi: <code>closed</code> yoki <code>open</code>.\n"
        "Savol matni yoki Savol rasmi bo'lishi kerak.\n"
        "Open savolda A/B/C/D shart emas.",
        reply_markup=cancel_kb(),
    )
    await c.answer()


@router.message(AdminState.import_file, F.document)
async def import_document(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return

    doc = m.document
    filename = (doc.file_name or "").lower()

    if not (filename.endswith(".xlsx") or filename.endswith(".csv")):
        await m.answer(
            "Faqat .xlsx yoki .csv fayl yuboring.",
            reply_markup=cancel_kb(),
        )
        return

    import tempfile
    from pathlib import Path

    path = (
        Path(tempfile.gettempdir())
        / f"doniyor_import_{m.from_user.id}_"
          f"{doc.file_unique_id}{Path(filename).suffix}"
    )

    try:
        await m.bot.download(doc, destination=path)

        if filename.endswith(".xlsx"):
            from openpyxl import load_workbook

            wb = load_workbook(
                path,
                read_only=True,
                data_only=True,
            )
            rows = list(
                wb.active.iter_rows(values_only=True)
            )
            wb.close()

        else:
            import csv
            import io

            raw = path.read_bytes()
            csv_text = None

            for enc in (
                "utf-8-sig",
                "utf-8",
                "cp1251",
            ):
                try:
                    csv_text = raw.decode(enc)
                    break
                except UnicodeDecodeError:
                    pass

            if csv_text is None:
                raise ValueError(
                    "CSV kodlashini o'qib bo'lmadi."
                )

            rows = list(
                csv.reader(io.StringIO(csv_text))
            )

        if len(rows) < 2:
            raise ValueError(
                "Faylda savollar mavjud emas."
            )

        def normalize_header(value):
            if value is None:
                return ""

            value = str(value).strip().lower()

            for code in (
                0x2018,
                0x2019,
                0x02BB,
                0x02BC,
                0x0060,
            ):
                value = value.replace(
                    chr(code),
                    "'",
                )

            value = value.replace(
                chr(0xFEFF),
                "",
            )

            return " ".join(value.split())

        headers = [
            normalize_header(x)
            for x in rows[0]
        ]

        aliases = {
            "fan": {
                "fan",
                "subject",
            },
            "mavzu": {
                "mavzu",
                "topic",
            },
            "savol": {
                "savol",
                "question",
                "text",
            },
            "image": {
                "savol rasmi",
                "savol rasmi yo'li",
                "savol rasmi yoli",
                "image",
                "image path",
                "image_path",
                "rasm",
                "rasm yo'li",
                "rasm yoli",
            },
            "a": {
                "a",
                "variant a",
            },
            "b": {
                "b",
                "variant b",
            },
            "c": {
                "c",
                "variant c",
            },
            "d": {
                "d",
                "variant d",
            },
            "mode": {
                "savol turi",
                "question type",
                "question_type",
                "type",
                "mode",
            },
            "correct": {
                "to'g'ri javob",
                "tog'ri javob",
                "togri javob",
                "correct",
                "correct option",
                "correct answer",
                "javob",
                "to'g'ri",
                "togri",
            },
            "correct_answer": {
                "to'g'ri javob matni",
                "tog'ri javob matni",
                "togri javob matni",
                "correct answer text",
                "correct_answer",
                "answer text",
                "javob matni",
            },
            "izoh": {
                "izoh",
                "explanation",
                "comment",
            },
        }

        idx = {}

        for key, names in aliases.items():
            normalized_names = {
                normalize_header(x)
                for x in names
            }

            for i, header in enumerate(headers):
                if header in normalized_names:
                    idx[key] = i
                    break

        required = [
            "fan",
            "mavzu",
        ]

        missing = [
            x for x in required
            if x not in idx
        ]

        if missing:
            raise ValueError(
                "Majburiy ustunlar topilmadi: "
                + ", ".join(missing)
            )

        def cell(row, key):
            i = idx.get(key)

            if i is None:
                return ""

            if i >= len(row):
                return ""

            if row[i] is None:
                return ""

            return str(row[i]).strip()

        valid = []
        errors = []

        for row_no, row in enumerate(
            rows[1:],
            start=2,
        ):
            if not any(
                x is not None
                and str(x).strip()
                for x in row
            ):
                continue

            subject_name = cell(row, "fan")
            topic_name = cell(row, "mavzu")
            question = cell(row, "savol")
            image_value = cell(row, "image")

            option_a = cell(row, "a")
            option_b = cell(row, "b")
            option_c = cell(row, "c")
            option_d = cell(row, "d")

            mode = cell(row, "mode").lower()
            correct = cell(row, "correct").upper()
            correct_answer = cell(
                row,
                "correct_answer",
            )
            explanation = (
                cell(row, "izoh")
                or None
            )

            if not mode:
                mode = "closed"

            if mode in {
                "yopiq",
                "variantli",
                "test",
            }:
                mode = "closed"

            if mode in {
                "ochiq",
                "free",
                "free text",
            }:
                mode = "open"

            if mode not in {
                "closed",
                "open",
            }:
                errors.append(
                    f"{row_no}-qator: "
                    "Savol turi closed yoki open "
                    "bo'lishi kerak."
                )
                continue

            if not subject_name:
                errors.append(
                    f"{row_no}-qator: Fan bo'sh."
                )
                continue

            if not topic_name:
                errors.append(
                    f"{row_no}-qator: Mavzu bo'sh."
                )
                continue

            if not question and not image_value:
                errors.append(
                    f"{row_no}-qator: Savol yoki "
                    "Savol rasmi bo'lishi kerak."
                )
                continue

            if mode == "closed":
                if not all(
                    [
                        option_a,
                        option_b,
                        option_c,
                        option_d,
                    ]
                ):
                    errors.append(
                        f"{row_no}-qator: closed savolda "
                        "A/B/C/D to'liq bo'lishi kerak."
                    )
                    continue

                if correct not in {
                    "A",
                    "B",
                    "C",
                    "D",
                }:
                    errors.append(
                        f"{row_no}-qator: To'g'ri javob "
                        "A/B/C/D bo'lishi kerak."
                    )
                    continue

                correct_answer = None

            else:
                if not correct_answer:
                    errors.append(
                        f"{row_no}-qator: open savolda "
                        "To'g'ri javob matni bo'lishi kerak."
                    )
                    continue

                correct = None

            valid.append(
                {
                    "subject": subject_name,
                    "topic": topic_name,
                    "question": question or None,
                    "image": image_value or None,
                    "mode": mode,
                    "a": option_a or None,
                    "b": option_b or None,
                    "c": option_c or None,
                    "d": option_d or None,
                    "correct": correct,
                    "correct_answer": correct_answer,
                    "explanation": explanation,
                }
            )

        if errors:
            preview = "\n".join(
                errors[:10]
            )

            more = ""

            if len(errors) > 10:
                more = (
                    f"\n... yana "
                    f"{len(errors) - 10} ta xato"
                )

            await m.answer(
                "<b>Faylda xato bor.</b>\n\n"
                f"{preview}{more}\n\n"
                "Import bajarilmadi. "
                "Faylni tuzatib qayta yuboring.",
                reply_markup=cancel_kb(),
            )
            return

        if not valid:
            raise ValueError(
                "Import qilinadigan savol topilmadi."
            )

        async with SessionLocal() as s:
            subject_cache = {}
            topic_cache = {}

            for item in valid:
                subject_name = item["subject"]
                topic_name = item["topic"]

                skey = subject_name.casefold()

                subject = subject_cache.get(
                    skey
                )

                if not subject:
                    subject = await s.scalar(
                        select(Subject).where(
                            func.lower(
                                Subject.name
                            )
                            == subject_name.lower()
                        )
                    )

                    if not subject:
                        subject = Subject(
                            name=subject_name
                        )
                        s.add(subject)
                        await s.flush()

                    subject_cache[skey] = subject

                tkey = (
                    subject.id,
                    topic_name.casefold(),
                )

                topic = topic_cache.get(tkey)

                if not topic:
                    topic = await s.scalar(
                        select(Topic).where(
                            Topic.subject_id
                            == subject.id,
                            func.lower(
                                Topic.name
                            )
                            == topic_name.lower(),
                        )
                    )

                    if not topic:
                        topic = Topic(
                            subject_id=subject.id,
                            name=topic_name,
                        )
                        s.add(topic)
                        await s.flush()

                    topic_cache[tkey] = topic

                image_path_value = None

                if item["image"]:
                    image_path_value = (
                        Path(
                            item["image"]
                        ).name
                    )

                    image_path_value = (
                        Path(
                            "app/question_images"
                        )
                        / image_path_value
                    ).as_posix()

                s.add(
                    Question(
                        topic_id=topic.id,
                        text=item["question"],
                        image_path=image_path_value,
                        question_mode=item["mode"],
                        option_a=item["a"],
                        option_b=item["b"],
                        option_c=item["c"],
                        option_d=item["d"],
                        correct_option=item["correct"],
                        correct_answer=item[
                            "correct_answer"
                        ],
                        explanation=item[
                            "explanation"
                        ],
                    )
                )

            await s.commit()

        await state.clear()

        await m.answer(
            "<b>IMPORT MUVAFFAQIYATLI!</b>\n\n"
            f"Qo'shilgan savollar: "
            f"<b>{len(valid)}</b>\n"
            "Fan va mavzular avtomatik bog'landi.",
            reply_markup=admin_menu(),
        )

    except Exception as e:
        await m.answer(
            "<b>Import amalga oshmadi.</b>\n\n"
            f"Sabab: <code>{str(e)[:500]}</code>",
            reply_markup=cancel_kb(),
        )

    finally:
        try:
            path.unlink(
                missing_ok=True
            )
        except Exception:
            pass


@router.message(AdminState.import_file)
async def import_wrong_type(
    m: Message,
    state: FSMContext,
):
    if not is_admin(m.from_user.id):
        return

    await m.answer(
        "Excel/CSV faylini hujjat sifatida "
        "yuboring: <b>.xlsx</b> yoki <b>.csv</b>.",
        reply_markup=cancel_kb(),
    )


# -------------------- CATALOG / STATS --------------------
# -------------------- CATALOG / STATS --------------------
# -------------------- CATALOG / STATS --------------------
@router.callback_query(F.data == "adm:catalog")
async def catalog(c: CallbackQuery):
    if not is_admin(c.from_user.id):
        return

    async with SessionLocal() as s:
        subjects = (await s.scalars(select(Subject).order_by(Subject.name))).all()

    if not subjects:
        await c.answer("Hali fanlar yo'q.", show_alert=True)
        return

    buttons = [
        [
            InlineKeyboardButton(
                text=f"📚 {sub.name}", callback_data=f"adm:subject:{sub.id}"
            )
        ]
        for sub in subjects
    ]

    buttons.append([InlineKeyboardButton(text="⬅️ Admin panel", callback_data="admin")])

    await c.message.edit_text(
        "📚 <b>Fanlar</b>\n\nKerakli fanni tanlang:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )
    await c.answer()


@router.callback_query(F.data.startswith("adm:subject:"))
async def catalog_subject(c: CallbackQuery):
    if not is_admin(c.from_user.id):
        return

    sid = int(c.data.split(":")[2])

    async with SessionLocal() as s:
        subject = await s.get(Subject, sid)

        if not subject:
            await c.answer("Fan topilmadi.", show_alert=True)
            return

        topics = (
            await s.scalars(
                select(Topic).where(Topic.subject_id == sid).order_by(Topic.name)
            )
        ).all()

        topic_count = len(topics)

    buttons = [
        [
            InlineKeyboardButton(
                text=f"📖 {topic.name}", callback_data=f"adm:topic:{topic.id}"
            )
        ]
        for topic in topics
    ]

    if not topics:
        buttons.append(
            [
                InlineKeyboardButton(
                    text="➕ Mavzu qo'shish", callback_data="adm:add_topic"
                )
            ]
        )

    buttons.append(
        [
            InlineKeyboardButton(
                text="🗑 Fanni o'chirish",
                callback_data=f"adm:delete_subject:{subject.id}",
            )
        ]
    )

    buttons.append(
        [InlineKeyboardButton(text="⬅️ Fanlar", callback_data="adm:catalog")]
    )

    await c.message.edit_text(
        f"📚 <b>{subject.name}</b>\n\n"
        f"📖 Mavzular: <b>{topic_count} ta</b>\n\n"
        "Kerakli amalni tanlang:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        parse_mode="HTML",
    )

    await c.answer()


@router.callback_query(F.data.startswith("adm:topic:"))
async def catalog_topic(c: CallbackQuery):
    if not is_admin(c.from_user.id):
        return

    tid = int(c.data.split(":")[2])

    async with SessionLocal() as s:
        topic = await s.get(Topic, tid)

        if not topic:
            await c.answer("Mavzu topilmadi.", show_alert=True)
            return

        count = await s.scalar(
            select(func.count(Question.id)).where(Question.topic_id == tid)
        )

    buttons = [
        [
            InlineKeyboardButton(
                text="📋 Savollarni ko'rish", callback_data=f"adm:questions:{tid}"
            )
        ],
        [
            InlineKeyboardButton(
                text="✏️ Mavzuni tahrirlash", callback_data=f"adm:edit_topic:{tid}"
            )
        ],
        [
            InlineKeyboardButton(
                text="🗑 Mavzuni o'chirish", callback_data=f"adm:delete_topic:{tid}"
            )
        ],
        [
            InlineKeyboardButton(
                text="⬅️ Mavzular", callback_data=f"adm:subject:{topic.subject_id}"
            )
        ],
    ]

    await c.message.edit_text(
        f"📖 <b>{topic.name}</b>\n\n" f"📋 Savollar: <b>{count}</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )
    await c.answer()


@router.callback_query(F.data.startswith("adm:edit_topic:"))
async def edit_topic_start(c: CallbackQuery, state: FSMContext):
    if not is_admin(c.from_user.id):
        return

    tid = int(c.data.split(":")[2])

    async with SessionLocal() as s:
        topic = await s.get(Topic, tid)

    if not topic:
        await c.answer("Mavzu topilmadi.", show_alert=True)
        return

    await state.update_data(topic_id=tid)
    await state.set_state(AdminState.edit_topic_name)

    await c.message.edit_text(
        "✏️ <b>MAVZUNI TAHRIRLASH</b>\n\n"
        f"Eski nom: <b>{topic.name}</b>\n\n"
        "Yangi mavzu nomini yuboring:",
        reply_markup=cancel_kb(),
    )
    await c.answer()


@router.message(AdminState.edit_topic_name)
async def edit_topic_finish(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return

    name = (m.text or "").strip()

    if not name:
        await m.answer("❗ Mavzu nomi bo'sh bo'lishi mumkin emas.")
        return

    data = await state.get_data()
    tid = data["topic_id"]

    async with SessionLocal() as s:
        topic = await s.get(Topic, tid)

        if not topic:
            await state.clear()
            await m.answer("❌ Mavzu topilmadi.", reply_markup=admin_menu())
            return

        topic.name = name
        subject_id = topic.subject_id

        await s.commit()

    await state.clear()

    await m.answer(
        "✅ <b>MAVZU MUVAFFAQIYATLI TAHRIRLANDI!</b>\n\n" f"Yangi nom: <b>{name}</b>",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="📖 Mavzuni ko'rish", callback_data=f"adm:topic:{tid}"
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="⬅️ Mavzular", callback_data=f"adm:subject:{subject_id}"
                    )
                ],
                [InlineKeyboardButton(text="⚙️ Admin panel", callback_data="admin")],
            ]
        ),
    )


@router.callback_query(F.data.startswith("adm:delete_topic:"))
async def delete_topic(c: CallbackQuery):
    if not is_admin(c.from_user.id):
        return

    tid = int(c.data.split(":")[2])

    async with SessionLocal() as s:
        topic = await s.get(Topic, tid)

        if not topic:
            await c.answer("Mavzu topilmadi.", show_alert=True)
            return

        subject_id = topic.subject_id

        await s.delete(topic)
        await s.commit()

    await c.message.edit_text(
        "🗑 <b>MAVZU O'CHIRILDI!</b>",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="⬅️ Mavzular", callback_data=f"adm:subject:{subject_id}"
                    )
                ],
                [InlineKeyboardButton(text="⚙️ Admin panel", callback_data="admin")],
            ]
        ),
    )

    await c.answer("Mavzu o'chirildi.")


@router.callback_query(F.data.startswith("adm:questions:"))
async def catalog_questions(c: CallbackQuery):
    if not is_admin(c.from_user.id):
        return

    tid = int(c.data.split(":")[2])

    async with SessionLocal() as s:
        topic = await s.get(Topic, tid)

        if not topic:
            await c.answer("Mavzu topilmadi.", show_alert=True)
            return

        questions = (
            await s.scalars(
                select(Question).where(Question.topic_id == tid).order_by(Question.id)
            )
        ).all()

    if not questions:
        await c.message.edit_text(
            f"📖 <b>{topic.name}</b>\n\n" "📋 Bu mavzuda hozircha savollar yo'q.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="⬅️ Mavzu", callback_data=f"adm:topic:{tid}"
                        )
                    ]
                ]
            ),
        )
        await c.answer()
        return

    lines = [f"📖 <b>{topic.name}</b>", f"📋 Savollar: <b>{len(questions)}</b>", ""]

    buttons = []

    for i, q in enumerate(questions, 1):
        short_text = q.text.replace("\n", " ").strip()
        if len(short_text) > 55:
            short_text = short_text[:55] + "..."

        buttons.append(
            [
                InlineKeyboardButton(
                    text=f"{i}. {short_text}", callback_data=f"adm:question:{q.id}"
                )
            ]
        )

    buttons.append(
        [InlineKeyboardButton(text="⬅️ Mavzu", callback_data=f"adm:topic:{tid}")]
    )

    await c.message.edit_text(
        "\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
    )
    await c.answer()


@router.callback_query(F.data.startswith("adm:question:"))
async def catalog_question(c: CallbackQuery):
    if not is_admin(c.from_user.id):
        return

    qid = int(c.data.split(":")[2])

    async with SessionLocal() as s:
        q = await s.get(Question, qid)

        if not q:
            await c.answer("Savol topilmadi.", show_alert=True)
            return

        topic = await s.get(Topic, q.topic_id)

    text = (
        "📋 <b>SAVOL</b>\n"
        "──────────────\n\n"
        f"❓ <b>{q.text}</b>\n\n"
        f"A) {q.option_a}\n"
        f"B) {q.option_b}\n"
        f"C) {q.option_c}\n"
        f"D) {q.option_d}\n\n"
        f"✅ To'g'ri javob: <b>{q.correct_option}</b>"
    )

    if q.explanation:
        text += f"\n\n💡 <b>Izoh:</b> {q.explanation}"

    await c.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="✏️ Tahrirlash", callback_data=f"adm:edit_question:{q.id}"
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="🗑 O'chirish", callback_data=f"adm:delete_question:{q.id}"
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="⬅️ Savollar", callback_data=f"adm:questions:{q.topic_id}"
                    )
                ],
                [InlineKeyboardButton(text="⚙️ Admin panel", callback_data="admin")],
            ]
        ),
    )
    await c.answer()


@router.callback_query(F.data.startswith("adm:edit_question:"))
async def edit_question_start(c: CallbackQuery, state: FSMContext):
    if not is_admin(c.from_user.id):
        return

    qid = int(c.data.split(":")[2])

    async with SessionLocal() as s:
        q = await s.get(Question, qid)

    if not q:
        await c.answer("Savol topilmadi.", show_alert=True)
        return

    await state.update_data(
        question_id=q.id,
        old_image_path=q.image_path,
        old_question_mode=q.question_mode,
    )
    await state.set_state(AdminState.edit_question_text)

    if q.question_mode == "image" and q.image_path:
        await c.message.answer_photo(
            FSInputFile(q.image_path),
            caption="🖼️ <b>Hozirgi savol rasmi</b>\n\n"
                    "Yangi rasm yuboring yoki matn yuboring.",
            reply_markup=cancel_kb(),
        )
    else:
        await c.message.answer(
            "✏️ <b>SAVOLNI TAHRIRLASH</b>\n\n"
            f"Eski savol:\n<b>{q.text or '?'}</b>\n\n"
            "📷 Rasm yuborsangiz → rasmli savol bo'ladi.\n"
            "📝 Matn yuborsangiz → matnli savol bo'ladi.",
            reply_markup=cancel_kb(),
        )

    await c.answer()


@router.message(AdminState.edit_question_text)
async def edit_question_text_handler(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return

    # RASM yuborildi
    image_data = await photo_bytes(m)

    if image_data:
        await state.update_data(
            text="",
            image_data=image_data,
            question_mode="image",
        )
        await state.set_state(AdminState.edit_question_a)

        await m.answer(
            "🖼️ <b>Rasm qabul qilindi.</b>\n\n"
            "2️⃣ <b>A variant</b>ni yuboring:",
            reply_markup=cancel_kb(),
        )
        return

    # MATN yuborildi
    value = (m.text or "").strip()

    if not value:
        await m.answer(
            "❗ Rasm yoki matn yuboring."
        )
        return

    await state.update_data(
        text=value,
        image_data=None,
        question_mode="closed",
    )
    await state.set_state(AdminState.edit_question_a)

    await m.answer(
        "📝 <b>Matnli savol qabul qilindi.</b>\n\n"
        "2️⃣ <b>A variant</b>ni yuboring:",
        reply_markup=cancel_kb(),
    )


@router.message(AdminState.edit_question_a)
async def edit_question_a_handler(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return

    value = (m.text or "").strip()

    if not value:
        await m.answer("❗ A varianti bo'sh bo'lishi mumkin emas.")
        return

    await state.update_data(option_a=value)
    await state.set_state(AdminState.edit_question_b)

    await m.answer("3️⃣ <b>B variant</b>ni yuboring:", reply_markup=cancel_kb())


@router.message(AdminState.edit_question_b)
async def edit_question_b_handler(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return

    value = (m.text or "").strip()

    if not value:
        await m.answer("❗ B varianti bo'sh bo'lishi mumkin emas.")
        return

    await state.update_data(option_b=value)
    await state.set_state(AdminState.edit_question_c)

    await m.answer("4️⃣ <b>C variant</b>ni yuboring:", reply_markup=cancel_kb())


@router.message(AdminState.edit_question_c)
async def edit_question_c_handler(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return

    value = (m.text or "").strip()

    if not value:
        await m.answer("❗ C varianti bo'sh bo'lishi mumkin emas.")
        return

    await state.update_data(option_c=value)
    await state.set_state(AdminState.edit_question_d)

    await m.answer("5️⃣ <b>D variant</b>ni yuboring:", reply_markup=cancel_kb())


@router.message(AdminState.edit_question_d)
async def edit_question_d_handler(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return

    value = (m.text or "").strip()

    if not value:
        await m.answer("❗ D varianti bo'sh bo'lishi mumkin emas.")
        return

    await state.update_data(option_d=value)

    data = await state.get_data()
    qid = data.get("question_id")

    async with SessionLocal() as s:
        q = await s.get(Question, qid)

        if not q:
            await state.clear()
            await m.answer("❗ Savol topilmadi.")
            return

        # Variantlarni yangilaymiz
        q.option_a = data.get("option_a", q.option_a)
        q.option_b = data.get("option_b", q.option_b)
        q.option_c = data.get("option_c", q.option_c)
        q.option_d = value

        # To'g'ri javob va izoh eski holatda qoladi
        # Faqat savol turi: RASM yoki MATN
        image_data = data.get("image_data")

        if image_data:
            q.text = ""
            q.image_path = await save_question_image(image_data, q.id)
            q.question_mode = "image"
        else:
            q.text = data.get("text", "").strip()
            q.image_path = None
            q.question_mode = "closed"

        await s.commit()

    await state.clear()

    await m.answer(
        "✅ <b>Savol muvaffaqiyatli tahrirlandi!</b>",
        reply_markup=cancel_kb(),
    )


@router.callback_query(F.data.startswith("adm:delete_question:"))
async def delete_question(c: CallbackQuery):
    if not is_admin(c.from_user.id):
        return

    qid = int(c.data.split(":")[2])

    async with SessionLocal() as s:
        q = await s.get(Question, qid)

        if not q:
            await c.answer("Savol topilmadi.", show_alert=True)
            return

        topic_id = q.topic_id

        await s.delete(q)
        await s.commit()

    await c.message.edit_text(
        "🗑 <b>SAVOL O'CHIRILDI!</b>",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="⬅️ Savollar", callback_data=f"adm:questions:{topic_id}"
                    )
                ],
                [InlineKeyboardButton(text="⚙️ Admin panel", callback_data="admin")],
            ]
        ),
    )

    await c.answer("Savol o'chirildi.")


from datetime import datetime, timedelta


@router.callback_query(F.data == "adm:stats")
async def admin_statistics(c: CallbackQuery):
    if not is_admin(c.from_user.id):
        return

    async with SessionLocal() as s:
        # 1. FOYDALANUVCHILAR FAOLKIGI
        total_users = await s.scalar(select(func.count(User.id)))

        # Bugun qo'shilgan yangi o'quvchilar (created_at maydoni bor deb hisoblaymiz)
        today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        new_users_today = await s.scalar(
            select(func.count(User.id)).where(User.created_at >= today_start)
        )

        # Oxirgi 7 kunda test ishlagan faol o'quvchilar soni
        seven_days_ago = datetime.now() - timedelta(days=7)
        active_users_7d = await s.scalar(
            select(func.count(func.distinct(TestAttempt.user_id))).where(
                TestAttempt.created_at >= seven_days_ago
            )
        )

        # 2. TESTLAR SIFATI TAHLILI
        total_attempts = await s.scalar(select(func.count(TestAttempt.id))) or 0

        # Jami javoblar ichidan to'g'ri va xatolarini hisoblash
        total_answers = await s.scalar(select(func.count(AnswerLog.id))) or 0
        correct_answers = (
            await s.scalar(
                select(func.count(AnswerLog.id)).where(AnswerLog.is_correct == True)
            )
            or 0
        )
        wrong_answers = total_answers - correct_answers

        # To'g'ri javoblar foizi
        success_pct = (
            (correct_answers / total_answers * 100) if total_answers > 0 else 0
        )

        # 3. REKLAMA VA E'LONLAR SAMARADORLIGI
        # Botdagi barcha o'quvchilar ishlagan jami umumiy darslar/urinishlar soni
        total_views = await s.scalar(select(func.sum(User.total_tests))) or 0

        # 4. REYTING VA RAG'BATLANTIRISH (Eng zo'r o'quvchi)
        top_user_stmt = (
            select(User.full_name, User.total_score)
            .order_by(User.total_score.desc())
            .limit(1)
        )
        top_user_res = (await s.execute(top_user_stmt)).fetchone()

        if top_user_res:
            top_student_text = f"🏆 <b>{top_user_res[0]}</b> ({top_user_res[1]} ball)"
        else:
            top_student_text = "Hali mavjud emas"

    # 📋 STATISTIKA MATNINI SHAKLLANTIRISH
    stats_text = (
        "📊 <b>Doniyor Academy — Tizim statistikasi</b>\n"
        "────────────────────\n\n"
        "👥 <b>Foydalanuvchilar faolligi:</b>\n"
        f"• Jami o'quvchilar: <b>{total_users} ta</b>\n"
        f"• Bugun qo'shilganlar: <b>+{new_users_today} ta</b>\n"
        f"• Haftalik faol (aktiv): <b>{active_users_7d} ta</b>\n\n"
        "📋 <b>Testlar sifati tahlili:</b>\n"
        f"• Jami yakunlangan testlar: <b>{total_attempts} marta</b>\n"
        f"• To'g'ri javoblar foizi: <b>{success_pct:.1f}%</b>\n"
        f"• ✅ To'g'ri: {correct_answers} ta | ❌ Xato: {wrong_answers} ta\n\n"
        "📢 <b>E'lonlar va kontent samaradorligi:</b>\n"
        f"• Mavzularni ko'rishlar soni: <b>{total_views} marta</b>\n\n"
        "🏆 <b>Reyting va rag'batlantirish:</b>\n"
        f"• Eng yuqori balli talaba: {top_student_text}\n"
        "────────────────────"
    )

    # Statistika oynasining tagida faqat orqaga qaytish (Bekor qilish) tugmasi turadi
    await c.message.edit_text(
        stats_text,
        reply_markup=cancel_kb(),  # Sizda bor bo'lgan cancel_kb() funksiyasini chaqiramiz
        parse_mode="HTML",
    )
    await c.answer()


# Backward-compatible text commands
@router.message(Command("addsubject"))
async def addsubject_command(m: Message):
    if not is_admin(m.from_user.id):
        return
    name = m.text.partition(" ")[2].strip()
    if not name:
        await m.answer("Format: /addsubject Fan nomi")
        return
    async with SessionLocal() as s:
        exists = await s.scalar(
            select(Subject).where(func.lower(Subject.name) == name.lower())
        )
        if exists:
            await m.answer("⚠️ Bu fan allaqachon mavjud.")
            return
        s.add(Subject(name=name))
        await s.commit()
    await m.answer(f"✅ Fan qo'shildi: <b>{name}</b>")


@router.message(Command("addtopic"))
async def addtopic_command(m: Message):
    if not is_admin(m.from_user.id):
        return
    raw = m.text.partition(" ")[2].strip()
    if "|" not in raw:
        await m.answer("Format: /addtopic Fan nomi | Mavzu nomi")
        return
    sn, tn = [x.strip() for x in raw.split("|", 1)]
    async with SessionLocal() as s:
        sub = await s.scalar(select(Subject).where(Subject.name == sn))
        if not sub:
            await m.answer("Bunday fan topilmadi.")
            return
        s.add(Topic(subject_id=sub.id, name=tn))
        await s.commit()
    await m.answer(f"✅ Mavzu qo'shildi: <b>{tn}</b>")


@router.message(Command("users"))
async def users(m: Message):
    if not is_admin(m.from_user.id):
        return
    async with SessionLocal() as s:
        n = await s.scalar(select(func.count(User.id)))
    await m.answer(f"👥 Foydalanuvchilar: <b>{n}</b>")


@router.message(Command("broadcast"))
async def broadcast(m: Message):
    if not is_admin(m.from_user.id):
        return
    text = m.text.partition(" ")[2].strip()
    if not text:
        await m.answer("Format: /broadcast Xabar matni")
        return
    async with SessionLocal() as s:
        us = (await s.scalars(select(User).where(User.is_blocked.is_(False)))).all()
    sent = 0
    for u in us:
        try:
            await m.bot.send_message(u.telegram_id, text)
            sent += 1
        except Exception:
            pass
    await m.answer(f"📢 Yuborildi: {sent}/{len(us)}")


from aiogram.fsm.state import StatesGroup, State


# E'lon uchun alohida yangi holat
class AdState(StatesGroup):
    waiting_for_ad = State()


# 1. Admin panelda "📢 E'lonlar" tugmasi bosilganda ishlaydi
@router.callback_query(F.data == "adm:send_ad")
async def start_ad(c: CallbackQuery, state: FSMContext):
    if not is_admin(c.from_user.id):
        return

    await state.set_state(AdState.waiting_for_ad)

    # edit_text o'rniga answer ishlatamiz, shunda xato bermaydi
    await c.message.answer(
        "📢 E'lon bo'limi faollashdi! O'quvchilarga yubormoqchi bo'lgan xabaringizni yozib yuboring."
    )
    await c.answer()


# 2. Admin e'lon xabarini yuborganida ishlaydi
@router.message(AdState.waiting_for_ad)
async def send_ad_to_all_users(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return
    await state.clear()
    status_msg = await m.answer("📢 E'lon barcha o'quvchilarga yuborilmoqda...")

    async with SessionLocal() as s:
        users = (await s.scalars(select(User.telegram_id))).all()

    success_count = 0
    fail_count = 0

    for user_id in users:
        try:
            await m.copy_to(chat_id=user_id)
            success_count += 1
            if success_count % 10 == 0:
                await asyncio.sleep(0.3)
        except Exception:
            fail_count += 1

    await status_msg.edit_text(
        f"📢 <b>E'lon tarqatish yakunlandi!</b>\n\n"
        f"✅ Yetkazildi: <b>{success_count} ta</b>\n"
        f"❌ Yetkazilmadi: <b>{fail_count} ta</b>",
        reply_markup=admin_menu(),
        parse_mode="HTML",
    )


# -------------------- SUBJECT DELETE --------------------


@router.callback_query(F.data.startswith("adm:delete_subject:"))
async def delete_subject_confirm(c: CallbackQuery):
    if not is_admin(c.from_user.id):
        return

    sid = int(c.data.split(":")[2])

    async with SessionLocal() as s:
        subject = await s.get(Subject, sid)

        if not subject:
            await c.answer("Fan topilmadi.", show_alert=True)
            return

        topic_count = (
            await s.scalar(select(func.count(Topic.id)).where(Topic.subject_id == sid))
            or 0
        )

        question_count = (
            await s.scalar(
                select(func.count(Question.id))
                .join(Topic, Question.topic_id == Topic.id)
                .where(Topic.subject_id == sid)
            )
            or 0
        )

        subject_name = subject.name

    await c.message.edit_text(
        "⚠️ <b>FANNI O'CHIRISH</b>\n\n"
        f"📚 Fan: <b>{subject_name}</b>\n\n"
        f"📖 Mavzular: <b>{topic_count} ta</b>\n"
        f"📋 Savollar: <b>{question_count} ta</b>\n\n"
        "❌ <b>DIQQAT!</b>\n"
        "Bu fanni o'chirsangiz, unga tegishli "
        "barcha mavzular va savollar ham o'chiriladi.\n\n"
        "Rostdan ham o'chirmoqchimisiz?",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="✅ Ha, o'chirish",
                        callback_data=f"adm:delete_subject_confirm:{sid}",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="❌ Bekor qilish", callback_data=f"adm:subject:{sid}"
                    )
                ],
            ]
        ),
        parse_mode="HTML",
    )

    await c.answer()


@router.callback_query(F.data.startswith("adm:delete_subject_confirm:"))
async def delete_subject(c: CallbackQuery):
    if not is_admin(c.from_user.id):
        return

    sid = int(c.data.split(":")[2])

    async with SessionLocal() as s:
        subject = await s.get(Subject, sid)

        if not subject:
            await c.answer("Fan topilmadi.", show_alert=True)
            return

        subject_name = subject.name

        await s.delete(subject)
        await s.commit()

    await c.message.edit_text(
        "🗑 <b>FAN O'CHIRILDI!</b>\n\n"
        f"📚 <b>{subject_name}</b>\n\n"
        "Fan va unga tegishli mavzular hamda savollar "
        "muvaffaqiyatli o'chirildi.",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="📚 Fanlar", callback_data="adm:catalog")],
                [InlineKeyboardButton(text="⚙️ Admin panel", callback_data="admin")],
            ]
        ),
        parse_mode="HTML",
    )

    await c.answer("Fan o'chirildi.")


# -------------------- DELETE SUBJECT MENU --------------------


@router.callback_query(F.data == "adm:delete_subject_menu")
async def delete_subject_menu(c: CallbackQuery):
    if not is_admin(c.from_user.id):
        return

    async with SessionLocal() as s:
        subjects = (await s.scalars(select(Subject).order_by(Subject.name))).all()

    if not subjects:
        await c.answer("Hali fanlar yo'q.", show_alert=True)
        return

    buttons = [
        [
            InlineKeyboardButton(
                text=f"📚 {subject.name}",
                callback_data=f"adm:delete_subject:{subject.id}",
            )
        ]
        for subject in subjects
    ]

    buttons.append([InlineKeyboardButton(text="⬅️ Admin panel", callback_data="admin")])

    await c.message.edit_text(
        "🗑 <b>FANNI O'CHIRISH</b>\n\n" "O'chirmoqchi bo'lgan fanni tanlang:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        parse_mode="HTML",
    )

    await c.answer()



@router.callback_query(F.data.startswith("adm:approve_user:"))
async def approve_user(c: CallbackQuery):
    if not is_admin(c.from_user.id):
        return

    telegram_id = int(c.data.split(":")[2])

    async with SessionLocal() as s:
        user = await s.scalar(
            select(User).where(User.telegram_id == telegram_id)
        )

        if not user:
            await c.answer("Foydalanuvchi topilmadi.", show_alert=True)
            return

        invalid_values = {
            "",
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

        missing = []

        full_name = (user.full_name or "").strip()
        phone_number = (user.phone_number or "").strip()
        group_name = (user.group_name or "").strip()

        if len(full_name.split()) < 2:
            missing.append("ism-familiya")

        if phone_number.lower() in invalid_values:
            missing.append("telefon")

        if group_name.lower() in invalid_values:
            missing.append("guruh")

        if missing:
            await c.answer(
                "Ma'lumotlar to'liq emas: "
                + ", ".join(missing),
                show_alert=True,
            )
            return

        user.registration_status = "approved"
        await s.commit()

        full_name = user.full_name or "Foydalanuvchi"

    try:
        await c.bot.send_message(
            telegram_id,
            "<b>Ro'yxatdan o'tishingiz tasdiqlandi!</b>\n\n"
            f"Ism-familiya: <b>{full_name}</b>\n\n"
            "Endi Doniyor Academy dan toliq foydalanishingiz mumkin.",
            reply_markup=main_menu(False),
        )
    except Exception:
        pass

    await c.message.edit_text(
        "<b>FOYDALANUVCHI TASDIQLANDI!</b>\n\n"
        f"Ism-familiya: <b>{full_name}</b>\n"
        f"Telegram ID: <code>{telegram_id}</code>\n\n"
        "Foydalanuvchiga tasdiqlanganligi haqida xabar yuborildi.",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="Admin panel", callback_data="admin")]
            ]
        ),
        parse_mode="HTML",
    )

    await c.answer("Foydalanuvchi tasdiqlandi.")


@router.callback_query(F.data.startswith("adm:reject_user:"))
async def reject_user(c: CallbackQuery):
    if not is_admin(c.from_user.id):
        return

    telegram_id = int(c.data.split(":")[2])

    async with SessionLocal() as s:
        user = await s.scalar(
            select(User).where(User.telegram_id == telegram_id)
        )

        if not user:
            await c.answer("Foydalanuvchi topilmadi.", show_alert=True)
            return

        user.registration_status = "rejected"
        await s.commit()

        full_name = user.full_name or "Foydalanuvchi"

    try:
        await c.bot.send_message(
            telegram_id,
            "<b>Royxatdan otish arizangiz rad etildi.</b>\n\n"
            "Qaytadan royxatdan otish uchun /start buyrugini yuboring.",
        )
    except Exception:
        pass

    await c.message.edit_text(
        "<b>FOYDALANUVCHI RAD ETILDI!</b>\n\n"
        f"Ism-familiya: <b>{full_name}</b>\n"
        f"Telegram ID: <code>{telegram_id}</code>",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="Admin panel", callback_data="admin")]
            ]
        ),
        parse_mode="HTML",
    )

    await c.answer("Foydalanuvchi rad etildi.")

@router.callback_query(F.data == "adm:users")
async def admin_users(c: CallbackQuery):
    if not is_admin(c.from_user.id):
        return

    async with SessionLocal() as s:
        users = (
            await s.scalars(
                select(User).order_by(User.created_at.desc())
            )
        ).all()

    if not users:
        await c.message.edit_text(
            "Foydalanuvchilar hali mavjud emas.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="Admin panel", callback_data="admin")]
                ]
            ),
        )
        await c.answer()
        return

    keyboard = []

    for user in users:
        status_icon = {
            "approved": "\u2705",
            "pending": "\u23f3",
            "rejected": "\u274c",
        }.get(user.registration_status, "\u2753")

        name = user.full_name or "Nomsiz"

        keyboard.append(
            [
                InlineKeyboardButton(
                    text=f"{status_icon} {name}",
                    callback_data=f"adm:user:{user.telegram_id}",
                )
            ]
        )

    keyboard.append(
        [
            InlineKeyboardButton(
                text="Admin panel",
                callback_data="admin",
            )
        ]
    )

    await c.message.edit_text(
        "<b>\U0001f465 FOYDALANUVCHILAR</b>\n\n"
        "Kerakli foydalanuvchini tanlang:",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=keyboard
        ),
        parse_mode="HTML",
    )

    await c.answer()
@router.callback_query(F.data.startswith("adm:user:"))
async def admin_user_detail(c: CallbackQuery):
    if not is_admin(c.from_user.id):
        return

    telegram_id = int(c.data.split(":")[2])

    async with SessionLocal() as s:
        user = await s.scalar(
            select(User).where(User.telegram_id == telegram_id)
        )

    if not user:
        await c.answer(
            "Foydalanuvchi topilmadi.",
            show_alert=True,
        )
        return

    status = {
        "approved": "\u2705 Tasdiqlangan",
        "pending": "\u23f3 Kutilmoqda",
        "rejected": "\u274c Rad etilgan",
    }.get(
        user.registration_status,
        user.registration_status,
    )

    blocked = "\U0001f6ab Bloklangan" if user.is_blocked else "\u2705 Faol"

    text = (
        "<b>\U0001f464 FOYDALANUVCHI MA'LUMOTLARI</b>\n\n"
        f"\U0001f464 Ism-familiya: <b>{user.full_name or 'Nomsiz'}</b>\n"
        f"\U0001f4f1 Username: @{user.username or '-'}\n"
        f"\U0001f194 Telegram ID: <code>{user.telegram_id}</code>\n"
        f"\U0001f4de Telefon: {user.phone_number or '-'}\n"
        f"\U0001f465 Guruh: {user.group_name or '-'}\n"
        f"\U0001f4cc Holat: <b>{status}</b>\n"
        f"\U0001f512 Hisob: <b>{blocked}</b>"
    )

    buttons = []

    if user.registration_status != "approved":
        buttons.append(
            [
                InlineKeyboardButton(
                    text="\u2705 Tasdiqlash",
                    callback_data=f"adm:approve_user:{user.telegram_id}",
                )
            ]
        )

    if user.registration_status != "rejected":
        buttons.append(
            [
                InlineKeyboardButton(
                    text="\u274c Rad etish",
                    callback_data=f"adm:reject_user:{user.telegram_id}",
                )
            ]
        )

    if user.is_blocked:
        buttons.append(
            [
                InlineKeyboardButton(
                    text="\U0001f513 Blokdan chiqarish",
                    callback_data=f"adm:unblock_user:{user.telegram_id}",
                )
            ]
        )
    else:
        buttons.append(
            [
                InlineKeyboardButton(
                    text="\U0001f6ab Bloklash",
                    callback_data=f"adm:block_user:{user.telegram_id}",
                )
            ]
        )

    buttons.append(
        [
            InlineKeyboardButton(
                text="\U0001f465 Foydalanuvchilar",
                callback_data="adm:users",
            )
        ]
    )

    buttons.append(
        [
            InlineKeyboardButton(
                text="Admin panel",
                callback_data="admin",
            )
        ]
    )

    await c.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=buttons
        ),
        parse_mode="HTML",
    )

    await c.answer()

@router.callback_query(F.data.startswith("adm:block_user:"))
async def block_user(c: CallbackQuery):
    if not is_admin(c.from_user.id):
        return

    telegram_id = int(c.data.split(":")[2])

    async with SessionLocal() as s:
        user = await s.scalar(
            select(User).where(User.telegram_id == telegram_id)
        )

        if not user:
            await c.answer(
                "Foydalanuvchi topilmadi.",
                show_alert=True,
            )
            return

        if user.is_blocked:
            await c.answer(
                "Bu foydalanuvchi allaqachon bloklangan.",
                show_alert=True,
            )
            return

        user.is_blocked = True
        await s.commit()

    await c.answer("\U0001f6ab Foydalanuvchi bloklandi.")

    await admin_user_detail(c)


@router.callback_query(F.data.startswith("adm:unblock_user:"))
async def unblock_user(c: CallbackQuery):
    if not is_admin(c.from_user.id):
        return

    telegram_id = int(c.data.split(":")[2])

    async with SessionLocal() as s:
        user = await s.scalar(
            select(User).where(User.telegram_id == telegram_id)
        )

        if not user:
            await c.answer(
                "Foydalanuvchi topilmadi.",
                show_alert=True,
            )
            return

        if not user.is_blocked:
            await c.answer(
                "Bu foydalanuvchi bloklanmagan.",
                show_alert=True,
            )
            return

        user.is_blocked = False
        await s.commit()

    await c.answer("\U0001f513 Foydalanuvchi blokdan chiqarildi.")

    await admin_user_detail(c)




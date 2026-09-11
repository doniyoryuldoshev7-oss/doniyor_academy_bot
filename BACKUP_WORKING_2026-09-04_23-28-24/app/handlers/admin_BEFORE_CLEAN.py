import asyncio
from ..models import Subject, Topic, Question, User, TestAttempt, AnswerLog
from ..keyboards import main_menu
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from sqlalchemy import select, func

from ..config import settings
from ..db import SessionLocal
from ..models import Subject, Topic, Question, User
from ..states import AdminState

router = Router()


def is_admin(uid: int) -> bool:
    return uid in settings.admins


def admin_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="вћ• Fan qoвЂshish", callback_data="adm:add_subject")],
        [InlineKeyboardButton(text="вћ• Mavzu qoвЂshish", callback_data="adm:add_topic")],
        [InlineKeyboardButton(text="вћ• Savol qoвЂshish", callback_data="adm:add_question")],
        [InlineKeyboardButton(text="рџ“Ґ Excel/CSV import", callback_data="adm:import")],
        [InlineKeyboardButton(text="рџ“љ Fanlar / mavzular", callback_data="adm:catalog")],
        [InlineKeyboardButton(text="рџ“Љ Statistika", callback_data="adm:stats")],
        [InlineKeyboardButton(text="рџ“ў E'lonlar", callback_data="adm:send_ad")],
	[InlineKeyboardButton(text="вќЊ Bekor qilish", callback_data="adm:cancel")],

    ])


def cancel_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="вќЊ Bekor qilish", callback_data="adm:cancel")]
    ])


def subject_kb(subjects, prefix="adm:qsub"):
    rows = [[InlineKeyboardButton(text=f"рџ“љ {s.name}", callback_data=f"{prefix}:{s.id}")] for s in subjects]
    rows.append([InlineKeyboardButton(text="вќЊ Bekor qilish", callback_data="adm:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def topic_kb(topics, prefix="adm:qtopic"):
    rows = [[InlineKeyboardButton(text=f"рџ“– {t.name}", callback_data=f"{prefix}:{t.id}")] for t in topics]
    rows.append([InlineKeyboardButton(text="в¬…пёЏ Fan tanlash", callback_data="adm:add_question")])
    rows.append([InlineKeyboardButton(text="вќЊ Bekor qilish", callback_data="adm:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def show_admin(target, answer=True):
    text = (
        "вљ™пёЏ <b>Doniyor Academy вЂ” Admin panel</b>\n\n"
        "Kerakli amalni tanlang:"
    )

    if isinstance(target, CallbackQuery):
        try:
            await target.message.edit_text(
                text,
                reply_markup=admin_menu()
            )
        except Exception as e:
            if "message is not modified" not in str(e):
                raise

        if answer:
            await target.answer()

    else:
        await target.answer(
            text,
            reply_markup=admin_menu()
        )


@router.message(Command("admin"))
async def cmd_admin(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return
    await state.clear()
    await show_admin(m, answer=False)


@router.callback_query(F.data == "admin")
async def cb_admin(c: CallbackQuery, state: FSMContext):
    if not is_admin(c.from_user.id):
        await c.answer("Sizda admin huquqi yoвЂq.", show_alert=True)
        return
    await state.clear()
    await show_admin(c)


from aiogram.types import ReplyKeyboardMarkup, KeyboardButton

from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove

from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton

from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton

from aiogram.types import ReplyKeyboardRemove, InlineKeyboardMarkup, InlineKeyboardButton

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
    admin_btn = [InlineKeyboardButton(text="вљ™пёЏ Admin panel", callback_data="admin")]
    if admin_btn not in user_menu.inline_keyboard:
        user_menu.inline_keyboard.append(admin_btn)
    
    # 4. Xabarni yangilaymiz, endi "Admin panel" tugmasi joyida bo'ladi!
    await c.message.edit_text(
        "рџЋ“ <b>Doniyor Academy</b>\n\nBosh menyu:",
        reply_markup=user_menu,
        parse_mode="HTML"
    )
    await c.answer()


# -------------------- SUBJECT --------------------
@router.callback_query(F.data == "adm:add_subject")
async def add_subject_start(c: CallbackQuery, state: FSMContext):
    if not is_admin(c.from_user.id): return
    await state.set_state(AdminState.add_subject_name)
    await c.message.edit_text(
        "вћ• <b>Yangi fan</b>\n\nFan nomini yuboring:\n\nMasalan: <i>OвЂzbekiston tarixi</i>",
        reply_markup=cancel_kb(),
    )
    await c.answer()


@router.message(AdminState.add_subject_name)
async def add_subject_finish(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id): return
    name = (m.text or "").strip()
    if not name:
        await m.answer("вќ— Fan nomi boвЂsh boвЂlishi mumkin emas.")
        return
    async with SessionLocal() as s:
        exists = await s.scalar(select(Subject).where(func.lower(Subject.name) == name.lower()))
        if exists:
            await m.answer("вљ пёЏ Bu fan allaqachon mavjud.")
            return
        s.add(Subject(name=name))
        await s.commit()
    await state.clear()
    await m.answer(f"вњ… Fan qoвЂshildi: <b>{name}</b>", reply_markup=admin_menu())


# -------------------- TOPIC --------------------
@router.callback_query(F.data == "adm:add_topic")
async def add_topic_start(c: CallbackQuery, state: FSMContext):
    if not is_admin(c.from_user.id): return
    async with SessionLocal() as s:
        subjects = (await s.scalars(select(Subject).order_by(Subject.name))).all()
    if not subjects:
        await c.answer("Avval fan qoвЂshing.", show_alert=True)
        return
    await state.set_state(AdminState.add_topic_subject)
    await c.message.edit_text("рџ“љ <b>Mavzu qoвЂshish</b>\n\nFanni tanlang:", reply_markup=subject_kb(subjects, "adm:tsub"))
    await c.answer()


@router.callback_query(F.data.startswith("adm:tsub:"))
async def add_topic_subject(c: CallbackQuery, state: FSMContext):
    if not is_admin(c.from_user.id): return
    sid = int(c.data.split(":")[2])
    await state.update_data(subject_id=sid)
    await state.set_state(AdminState.add_topic_name)
    await c.message.edit_text("рџ“– Mavzu nomini yuboring:", reply_markup=cancel_kb())
    await c.answer()


@router.message(AdminState.add_topic_name)
async def add_topic_finish(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id): return
    name = (m.text or "").strip()
    data = await state.get_data()
    async with SessionLocal() as s:
        subject = await s.get(Subject, data["subject_id"])
        if not subject:
            await state.clear(); await m.answer("вќЊ Fan topilmadi.", reply_markup=admin_menu()); return
        exists = await s.scalar(select(Topic).where(Topic.subject_id == subject.id, func.lower(Topic.name) == name.lower()))
        if exists:
            await m.answer("вљ пёЏ Bu mavzu allaqachon mavjud.")
            return
        s.add(Topic(subject_id=subject.id, name=name))
        await s.commit()
    await state.clear()
    await m.answer(f"вњ… <b>{subject.name}</b> faniga <b>{name}</b> mavzusi qoвЂshildi.", reply_markup=admin_menu())


# -------------------- QUESTION --------------------
@router.callback_query(F.data == "adm:add_question")
async def add_question_start(c: CallbackQuery, state: FSMContext):
    if not is_admin(c.from_user.id): return
    async with SessionLocal() as s:
        subjects = (await s.scalars(select(Subject).order_by(Subject.name))).all()
    if not subjects:
        await c.answer("Avval fan qoвЂshing.", show_alert=True); return
    await state.set_state(AdminState.add_question_subject)
    await c.message.edit_text("рџ“ќ <b>Yangi test savoli</b>\n\nFanni tanlang:", reply_markup=subject_kb(subjects))
    await c.answer()


@router.callback_query(F.data.startswith("adm:qsub:"))
async def add_question_subject(c: CallbackQuery, state: FSMContext):
    if not is_admin(c.from_user.id): return
    sid = int(c.data.split(":")[2])
    async with SessionLocal() as s:
        topics = (await s.scalars(select(Topic).where(Topic.subject_id == sid).order_by(Topic.name))).all()
        subject = await s.get(Subject, sid)
    if not topics:
        await c.answer("Bu fanda hali mavzu yoвЂq. Avval mavzu qoвЂshing.", show_alert=True); return
    await state.update_data(subject_id=sid)
    await state.set_state(AdminState.add_question_topic)
    await c.message.edit_text(f"рџ“љ <b>{subject.name}</b>\n\nMavzuni tanlang:", reply_markup=topic_kb(topics))
    await c.answer()


@router.callback_query(F.data.startswith("adm:qtopic:"))
async def add_question_topic(c: CallbackQuery, state: FSMContext):
    if not is_admin(c.from_user.id): return
    tid = int(c.data.split(":")[2])
    async with SessionLocal() as s:
        topic = await s.get(Topic, tid)
    await state.update_data(topic_id=tid)
    await state.set_state(AdminState.add_question_text)
    await c.message.edit_text(f"рџ“– <b>{topic.name}</b>\n\n1пёЏвѓЈ Savol matnini yuboring:", reply_markup=cancel_kb())
    await c.answer()


@router.message(AdminState.add_question_text)
async def q_text(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id): return
    text = (m.text or "").strip()
    if not text:
        await m.answer("вќ— Savol matni boвЂsh boвЂlishi mumkin emas."); return
    await state.update_data(text=text)
    await state.set_state(AdminState.add_question_a)
    await m.answer("2пёЏвѓЈ <b>A variant</b>ni yuboring:", reply_markup=cancel_kb())


async def save_option(m: Message, state: FSMContext, key: str, next_state, label: str):
    if not is_admin(m.from_user.id): return
    value = (m.text or "").strip()
    if not value:
        await m.answer("вќ— Variant boвЂsh boвЂlishi mumkin emas."); return
    await state.update_data(**{key: value})
    await state.set_state(next_state)
    await m.answer(label, reply_markup=cancel_kb())


@router.message(AdminState.add_question_a)
async def q_a(m, state): await save_option(m, state, "option_a", AdminState.add_question_b, "3пёЏвѓЈ <b>B variant</b>ni yuboring:")

@router.message(AdminState.add_question_b)
async def q_b(m, state): await save_option(m, state, "option_b", AdminState.add_question_c, "4пёЏвѓЈ <b>C variant</b>ni yuboring:")

@router.message(AdminState.add_question_c)
async def q_c(m, state): await save_option(m, state, "option_c", AdminState.add_question_d, "5пёЏвѓЈ <b>D variant</b>ni yuboring:")

@router.message(AdminState.add_question_d)
async def q_d(m, state): await save_option(m, state, "option_d", AdminState.add_question_correct, "6пёЏвѓЈ ToвЂgвЂri javob harfini yuboring: <b>A</b>, <b>B</b>, <b>C</b> yoki <b>D</b>")


@router.message(AdminState.add_question_correct)
async def q_correct(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id): return
    correct = (m.text or "").strip().upper()
    if correct not in {"A", "B", "C", "D"}:
        await m.answer("вќ— Faqat A, B, C yoki D yuboring."); return
    await state.update_data(correct_option=correct)
    await state.set_state(AdminState.add_question_explanation)
    await m.answer("7пёЏвѓЈ <b>Izoh</b>ni yuboring. Agar izoh kerak boвЂlmasa, <code>-</code> yuboring:", reply_markup=cancel_kb())


@router.message(AdminState.add_question_explanation)
async def q_explanation(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id): return
    explanation = (m.text or "").strip()
    data = await state.get_data()
    explanation = None if explanation == "-" else explanation
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
    await state.clear()
    await m.answer(
        f"вњ… <b>Savol muvaffaqiyatli qoвЂshildi!</b>\n\n"
        f"рџ†” ID: <code>{qid}</code>\n"
        f"рџ“– Mavzu: <b>{topic.name}</b>\n"
        f"вњ… ToвЂgвЂri javob: <b>{data['correct_option']}</b>",
        reply_markup=admin_menu(),
    )


# -------------------- EXCEL / CSV IMPORT --------------------
@router.callback_query(F.data == "adm:import")
async def import_start(c: CallbackQuery, state: FSMContext):
    if not is_admin(c.from_user.id):
        return
    await state.set_state(AdminState.import_file)
    await c.message.edit_text(
        "рџ“Ґ <b>Excel/CSV orqali test import</b>\n\n"
        "Faylni yuboring: <b>.xlsx</b> yoki <b>.csv</b>\n\n"
        "Ustunlar:\n"
        "<code>Fan | Mavzu | Savol | A | B | C | D | ToвЂgвЂri javob | Izoh</code>\n\n"
        "вЂў ToвЂgвЂri javob: A, B, C yoki D\n"
        "вЂў Izoh ixtiyoriy\n"
        "вЂў Fan va mavzu mavjud boвЂlmasa, avtomatik yaratiladi",
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
        await m.answer("вќ— Faqat .xlsx yoki .csv fayl yuboring.", reply_markup=cancel_kb())
        return

    import tempfile
    from pathlib import Path
    path = Path(tempfile.gettempdir()) / f"doniyor_import_{m.from_user.id}_{doc.file_unique_id}{Path(filename).suffix}"

    try:
        await m.bot.download(doc, destination=path)

        if filename.endswith(".xlsx"):
            from openpyxl import load_workbook
            wb = load_workbook(path, read_only=True, data_only=True)
            rows = list(wb.active.iter_rows(values_only=True))
            wb.close()
        else:
            import csv, io
            raw = path.read_bytes()
            for enc in ("utf-8-sig", "utf-8", "cp1251"):
                try:
                    text = raw.decode(enc)
                    break
                except UnicodeDecodeError:
                    text = None
            if text is None:
                raise ValueError("CSV kodlashini oвЂqib boвЂlmadi.")
            rows = list(csv.reader(io.StringIO(text)))

        if len(rows) < 2:
            raise ValueError("Faylda savollar mavjud emas.")

        headers = [str(x).strip().lower() if x is not None else "" for x in rows[0]]
        aliases = {
            "fan": {"fan", "subject"},
            "mavzu": {"mavzu", "topic"},
            "savol": {"savol", "question", "text"},
            "a": {"a", "variant a"},
            "b": {"b", "variant b"},
            "c": {"c", "variant c"},
            "d": {"d", "variant d"},
            "correct": {"toвЂgвЂri javob", "togri javob", "to'g'ri javob", "correct", "correct option", "javob"},
            "izoh": {"izoh", "explanation", "comment"},
        }
        idx = {}
        for key, names in aliases.items():
            for i, h in enumerate(headers):
                if h in names:
                    idx[key] = i
                    break

        required = ["fan", "mavzu", "savol", "a", "b", "c", "d", "correct"]
        missing = [x for x in required if x not in idx]
        if missing:
            raise ValueError("Majburiy ustunlar topilmadi: " + ", ".join(missing))

        def cell(row, key):
            i = idx.get(key)
            if i is None or i >= len(row) or row[i] is None:
                return ""
            return str(row[i]).strip()

        valid = []
        errors = []
        for row_no, row in enumerate(rows[1:], start=2):
            if not any(x is not None and str(x).strip() for x in row):
                continue
            vals = [cell(row, k) for k in ("fan","mavzu","savol","a","b","c","d")]
            correct = cell(row, "correct").upper()
            explanation = cell(row, "izoh") or None
            if not all(vals):
                errors.append(f"{row_no}-qator: majburiy maydon boвЂsh.")
                continue
            if correct not in {"A","B","C","D"}:
                errors.append(f"{row_no}-qator: toвЂgвЂri javob A/B/C/D boвЂlishi kerak.")
                continue
            valid.append((*vals, correct, explanation))

        if errors:
            preview = "\n".join(errors[:10])
            more = f"\n... yana {len(errors)-10} ta xato" if len(errors) > 10 else ""
            await m.answer(
                f"вљ пёЏ <b>Faylda xato bor.</b>\n\n{preview}{more}\n\n"
                "Import bajarilmadi. Faylni tuzatib qayta yuboring.",
                reply_markup=cancel_kb(),
            )
            return

        if not valid:
            raise ValueError("Import qilinadigan savol topilmadi.")

        async with SessionLocal() as s:
            subject_cache = {}
            topic_cache = {}
            for subject_name, topic_name, question, a, b, copt, d, correct, explanation in valid:
                skey = subject_name.casefold()
                subject = subject_cache.get(skey)
                if not subject:
                    subject = await s.scalar(select(Subject).where(func.lower(Subject.name) == subject_name.lower()))
                    if not subject:
                        subject = Subject(name=subject_name)
                        s.add(subject)
                        await s.flush()
                    subject_cache[skey] = subject

                tkey = (subject.id, topic_name.casefold())
                topic = topic_cache.get(tkey)
                if not topic:
                    topic = await s.scalar(select(Topic).where(
                        Topic.subject_id == subject.id,
                        func.lower(Topic.name) == topic_name.lower()
                    ))
                    if not topic:
                        topic = Topic(subject_id=subject.id, name=topic_name)
                        s.add(topic)
                        await s.flush()
                    topic_cache[tkey] = topic

                s.add(Question(
                    topic_id=topic.id,
                    text=question,
                    option_a=a,
                    option_b=b,
                    option_c=copt,
                    option_d=d,
                    correct_option=correct,
                    explanation=explanation,
                ))
            await s.commit()

        await state.clear()
        await m.answer(
            "рџЋ‰ <b>IMPORT MUVAFFAQIYATLI!</b>\n\n"
            f"рџ“ќ QoвЂshilgan savollar: <b>{len(valid)}</b>\n"
            "рџ“љ Fan va mavzular avtomatik bogвЂlandi.",
            reply_markup=admin_menu(),
        )
    except Exception as e:
        await m.answer(
            f"вќЊ <b>Import amalga oshmadi.</b>\n\nSabab: <code>{str(e)[:500]}</code>",
            reply_markup=cancel_kb(),
        )
    finally:
        try:
            path.unlink(missing_ok=True)
        except Exception:
            pass


@router.message(AdminState.import_file)
async def import_wrong_type(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return
    await m.answer(
        "вќ— Excel/CSV faylini hujjat sifatida yuboring: <b>.xlsx</b> yoki <b>.csv</b>.",
        reply_markup=cancel_kb()
    )


# -------------------- CATALOG / STATS --------------------
# -------------------- CATALOG / STATS --------------------
# -------------------- CATALOG / STATS --------------------
@router.callback_query(F.data == "adm:catalog")
async def catalog(c: CallbackQuery):
    if not is_admin(c.from_user.id):
        return

    async with SessionLocal() as s:
        subjects = (
            await s.scalars(
                select(Subject).order_by(Subject.name)
            )
        ).all()

    if not subjects:
        await c.answer("Hali fanlar yoвЂq.", show_alert=True)
        return

    buttons = [
        [InlineKeyboardButton(
            text=f"рџ“љ {sub.name}",
            callback_data=f"adm:subject:{sub.id}"
        )]
        for sub in subjects
    ]

    buttons.append([
        InlineKeyboardButton(
            text="в¬…пёЏ Admin panel",
            callback_data="admin"
        )
    ])

    await c.message.edit_text(
        "рџ“љ <b>Fanlar</b>\n\nKerakli fanni tanlang:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
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
                select(Topic)
                .where(Topic.subject_id == sid)
                .order_by(Topic.name)
            )
        ).all()

        topic_count = len(topics)

    buttons = [
        [
            InlineKeyboardButton(
                text=f"рџ“– {topic.name}",
                callback_data=f"adm:topic:{topic.id}"
            )
        ]
        for topic in topics
    ]

    if not topics:
        buttons.append([
            InlineKeyboardButton(
                text="вћ• Mavzu qoвЂshish",
                callback_data="adm:add_topic"
            )
        ])

    buttons.append([
        InlineKeyboardButton(
            text="рџ—‘ Fanni oвЂchirish",
            callback_data=f"adm:delete_subject:{subject.id}"
        )
    ])

    buttons.append([
        InlineKeyboardButton(
            text="в¬…пёЏ Fanlar",
            callback_data="adm:catalog"
        )
    ])

    await c.message.edit_text(
        f"рџ“љ <b>{subject.name}</b>\n\n"
        f"рџ“– Mavzular: <b>{topic_count} ta</b>\n\n"
        "Kerakli amalni tanlang:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        parse_mode="HTML"
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
            select(func.count(Question.id))
            .where(Question.topic_id == tid)
        )

    buttons = [
        [InlineKeyboardButton(
            text="рџ“‹ Savollarni koвЂrish",
            callback_data=f"adm:questions:{tid}"
        )],
        [InlineKeyboardButton(
            text="вњЏпёЏ Mavzuni tahrirlash",
            callback_data=f"adm:edit_topic:{tid}"
        )],
        [InlineKeyboardButton(
            text="рџ—‘ Mavzuni oвЂchirish",
            callback_data=f"adm:delete_topic:{tid}"
        )],
        [InlineKeyboardButton(
            text="в¬…пёЏ Mavzular",
            callback_data=f"adm:subject:{topic.subject_id}"
        )]
    ]

    await c.message.edit_text(
        f"рџ“– <b>{topic.name}</b>\n\n"
        f"рџ“ќ Savollar: <b>{count}</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
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
        "вњЏпёЏ <b>MAVZUNI TAHRIRLASH</b>\n\n"
        f"Eski nom: <b>{topic.name}</b>\n\n"
        "Yangi mavzu nomini yuboring:",
        reply_markup=cancel_kb()
    )
    await c.answer()


@router.message(AdminState.edit_topic_name)
async def edit_topic_finish(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return

    name = (m.text or "").strip()

    if not name:
        await m.answer("вќ— Mavzu nomi boвЂsh boвЂlishi mumkin emas.")
        return

    data = await state.get_data()
    tid = data["topic_id"]

    async with SessionLocal() as s:
        topic = await s.get(Topic, tid)

        if not topic:
            await state.clear()
            await m.answer(
                "вќЊ Mavzu topilmadi.",
                reply_markup=admin_menu()
            )
            return

        topic.name = name
        subject_id = topic.subject_id

        await s.commit()

    await state.clear()

    await m.answer(
        "вњ… <b>MAVZU MUVAFFAQIYATLI TAHRIRLANDI!</b>\n\n"
        f"Yangi nom: <b>{name}</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="рџ“– Mavzuni koвЂrish",
                    callback_data=f"adm:topic:{tid}"
                )
            ],
            [
                InlineKeyboardButton(
                    text="в¬…пёЏ Mavzular",
                    callback_data=f"adm:subject:{subject_id}"
                )
            ],
            [
                InlineKeyboardButton(
                    text="рџЏ  Admin panel",
                    callback_data="admin"
                )
            ]
        ])
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
        "рџ—‘ <b>MAVZU OвЂCHIRILDI!</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="в¬…пёЏ Mavzular",
                    callback_data=f"adm:subject:{subject_id}"
                )
            ],
            [
                InlineKeyboardButton(
                    text="рџЏ  Admin panel",
                    callback_data="admin"
                )
            ]
        ])
    )

    await c.answer("Mavzu oвЂchirildi.")


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
                select(Question)
                .where(Question.topic_id == tid)
                .order_by(Question.id)
            )
        ).all()

    if not questions:
        await c.message.edit_text(
            f"рџ“– <b>{topic.name}</b>\n\n"
            "рџ“ќ Bu mavzuda hozircha savollar yoвЂq.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(
                    text="в¬…пёЏ Mavzu",
                    callback_data=f"adm:topic:{tid}"
                )]
            ])
        )
        await c.answer()
        return

    lines = [
        f"рџ“– <b>{topic.name}</b>",
        f"рџ“ќ Savollar: <b>{len(questions)}</b>",
        ""
    ]

    buttons = []

    for i, q in enumerate(questions, 1):
        short_text = q.text.replace("\n", " ").strip()
        if len(short_text) > 55:
            short_text = short_text[:55] + "..."

        buttons.append([
            InlineKeyboardButton(
                text=f"{i}. {short_text}",
                callback_data=f"adm:question:{q.id}"
            )
        ])

    buttons.append([
        InlineKeyboardButton(
            text="в¬…пёЏ Mavzu",
            callback_data=f"adm:topic:{tid}"
        )
    ])

    await c.message.edit_text(
        "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
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
        "рџ“ќ <b>SAVOL</b>\n"
        "в”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓ\n\n"
        f"вќ“ <b>{q.text}</b>\n\n"
        f"A) {q.option_a}\n"
        f"B) {q.option_b}\n"
        f"C) {q.option_c}\n"
        f"D) {q.option_d}\n\n"
        f"вњ… ToвЂgвЂri javob: <b>{q.correct_option}</b>"
    )

    if q.explanation:
        text += f"\n\nрџ’Ў <b>Izoh:</b> {q.explanation}"

    await c.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text="вњЏпёЏ Tahrirlash",
                callback_data=f"adm:edit_question:{q.id}"
            )],
            [InlineKeyboardButton(
                text="рџ—‘ OвЂchirish",
                callback_data=f"adm:delete_question:{q.id}"
            )],
            [InlineKeyboardButton(
                text="в¬…пёЏ Savollar",
                callback_data=f"adm:questions:{q.topic_id}"
            )],
            [InlineKeyboardButton(
                text="рџЏ  Admin panel",
                callback_data="admin"
            )]
        ])
    )
    await c.answer()






    await state.update_data(option_a=value)
    await state.set_state(AdminState.edit_question_b)

    await m.answer(
        "3пёЏвѓЈ <b>B variant</b>ni yuboring:",
        reply_markup=cancel_kb()
    )


@router.message(AdminState.edit_question_b)
async def edit_question_b_handler(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return

    value = (m.text or "").strip()

    if not value:
        await m.answer("вќ— Variant boвЂsh boвЂlishi mumkin emas.")
        return

    await state.update_data(option_b=value)
    await state.set_state(AdminState.edit_question_c)

    await m.answer(
        "4пёЏвѓЈ <b>C variant</b>ni yuboring:",
        reply_markup=cancel_kb()
    )


@router.message(AdminState.edit_question_c)
async def edit_question_c_handler(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return

    value = (m.text or "").strip()

    if not value:
        await m.answer("вќ— Variant boвЂsh boвЂlishi mumkin emas.")
        return

    await state.update_data(option_c=value)
    await state.set_state(AdminState.edit_question_d)

    await m.answer(
        "5пёЏвѓЈ <b>D variant</b>ni yuboring:",
        reply_markup=cancel_kb()
    )


@router.message(AdminState.edit_question_d)
async def edit_question_d_handler(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return

    value = (m.text or "").strip()

    if not value:
        await m.answer("вќ— Variant boвЂsh boвЂlishi mumkin emas.")
        return

    data = await state.get_data()

    async with SessionLocal() as s:
        q = await s.get(Question, data["question_id"])

        if not q:
            await state.clear()
            await m.answer(
                "вќЊ Savol topilmadi.",
                reply_markup=admin_menu()
            )
            return

        q.text = data["text"]
        q.option_a = data["option_a"]
        q.option_b = data["option_b"]
        q.option_c = data["option_c"]
        q.option_d = value

        await s.commit()

        qid = q.id
        topic_id = q.topic_id

    await state.clear()

    await m.answer(
        "вњ… <b>SAVOL MUVAFFAQIYATLI TAHRIRLANDI!</b>\n\n"
        "Savol va barcha variantlar yangilandi.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="рџ“‹ Savolni koвЂrish",
                    callback_data=f"adm:question:{qid}"
                )
            ],
            [
                InlineKeyboardButton(
                    text="в¬…пёЏ Savollar",
                    callback_data=f"adm:questions:{topic_id}"
                )
            ],
            [
                InlineKeyboardButton(
                    text="рџЏ  Admin panel",
                    callback_data="admin"
                )
            ]
        ])
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
        "рџ—‘ <b>SAVOL OвЂCHIRILDI!</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="в¬…пёЏ Savollar",
                    callback_data=f"adm:questions:{topic_id}"
                )
            ],
            [
                InlineKeyboardButton(
                    text="рџЏ  Admin panel",
                    callback_data="admin"
                )
            ]
        ])
    )

    await c.answer("Savol oвЂchirildi.")


from datetime import datetime, timedelta
from sqlalchemy import select, func

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
            select(func.count(func.distinct(TestAttempt.user_id)))
            .where(TestAttempt.created_at >= seven_days_ago)
        )

        # 2. TESTLAR SIFATI TAHLILI
        total_attempts = await s.scalar(select(func.count(TestAttempt.id))) or 0
        
        # Jami javoblar ichidan to'g'ri va xatolarini hisoblash
        total_answers = await s.scalar(select(func.count(AnswerLog.id))) or 0
        correct_answers = await s.scalar(select(func.count(AnswerLog.id)).where(AnswerLog.is_correct == True)) or 0
        wrong_answers = total_answers - correct_answers
        
        # To'g'ri javoblar foizi
        success_pct = (correct_answers / total_answers * 100) if total_answers > 0 else 0

        # 3. REKLAMA VA E'LONLAR SAMARADORLIGI
        # Botdagi barcha o'quvchilar ishlagan jami umumiy darslar/urinishlar soni
        total_views = await s.scalar(select(func.sum(User.total_tests))) or 0

        # 4. REYTING VA RAG'BATLANTIRISH (Eng zo'r o'quvchi)
        top_user_stmt = select(User.full_name, User.total_score).order_by(User.total_score.desc()).limit(1)
        top_user_res = (await s.execute(top_user_stmt)).fetchone()
        
        if top_user_res:
            top_student_text = f"в­ђ <b>{top_user_res[0]}</b> ({top_user_res[1]} ball)"
        else:
            top_student_text = "Hali mavjud emas"

    # рџ“ќ STATISTIKA MATNINI SHAKLLANTIRISH
    stats_text = (
        "рџ“Љ <b>Doniyor Academy вЂ” Tizim statistikasi</b>\n"
        "в”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓ\n\n"
        
        "рџ‘Ґ <b>Foydalanuvchilar faolligi:</b>\n"
        f"вЂў Jami oвЂquvchilar: <b>{total_users} ta</b>\n"
        f"вЂў Bugun qoвЂshilganlar: <b>+{new_users_today} ta</b>\n"
        f"вЂў Haftalik faol (aktiv): <b>{active_users_7d} ta</b>\n\n"
        
        "рџ“ќ <b>Testlar sifati tahlili:</b>\n"
        f"вЂў Jami yakunlangan testlar: <b>{total_attempts} marta</b>\n"
        f"вЂў ToвЂgвЂri javoblar foizi: <b>{success_pct:.1f}%</b>\n"
        f"вЂў вњ… ToвЂgвЂri: {correct_answers} ta | вќЊ Xato: {wrong_answers} ta\n\n"
        
        "рџ“ў <b>E'lonlar va kontent samaradorligi:</b>\n"
        f"вЂў Mavzularni koвЂrishlar soni: <b>{total_views} marta</b>\n\n"
        
        "рџЏ† <b>Reyting va ragвЂbatlantirish:</b>\n"
        f"вЂў Eng yuqori balli talaba: {top_student_text}\n"
        "в”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓ"
    )

    # Statistika oynasining tagida faqat orqaga qaytish (Bekor qilish) tugmasi turadi
    await c.message.edit_text(
        stats_text,
        reply_markup=cancel_kb(), # Sizda bor bo'lgan cancel_kb() funksiyasini chaqiramiz
        parse_mode="HTML"
    )
    await c.answer()


# Backward-compatible text commands
@router.message(Command("addsubject"))
async def addsubject_command(m: Message):
    if not is_admin(m.from_user.id): return
    name = m.text.partition(" ")[2].strip()
    if not name:
        await m.answer("Format: /addsubject Fan nomi"); return
    async with SessionLocal() as s:
        exists = await s.scalar(select(Subject).where(func.lower(Subject.name) == name.lower()))
        if exists:
            await m.answer("вљ пёЏ Bu fan allaqachon mavjud."); return
        s.add(Subject(name=name)); await s.commit()
    await m.answer(f"вњ… Fan qoвЂshildi: <b>{name}</b>")


@router.message(Command("addtopic"))
async def addtopic_command(m: Message):
    if not is_admin(m.from_user.id): return
    raw=m.text.partition(" ")[2].strip()
    if "|" not in raw:
        await m.answer("Format: /addtopic Fan nomi | Mavzu nomi"); return
    sn, tn=[x.strip() for x in raw.split("|",1)]
    async with SessionLocal() as s:
        sub=await s.scalar(select(Subject).where(Subject.name==sn))
        if not sub:
            await m.answer("Bunday fan topilmadi."); return
        s.add(Topic(subject_id=sub.id,name=tn)); await s.commit()
    await m.answer(f"вњ… Mavzu qoвЂshildi: <b>{tn}</b>")


@router.message(Command("users"))
async def users(m: Message):
    if not is_admin(m.from_user.id): return
    async with SessionLocal() as s:
        n=await s.scalar(select(func.count(User.id)))
    await m.answer(f"рџ‘Ґ Foydalanuvchilar: <b>{n}</b>")


@router.message(Command("broadcast"))
async def broadcast(m: Message):
    if not is_admin(m.from_user.id): return
    text=m.text.partition(" ")[2].strip()
    if not text:
        await m.answer("Format: /broadcast Xabar matni"); return
    async with SessionLocal() as s:
        us=(await s.scalars(select(User).where(User.is_blocked.is_(False)))).all()
    sent=0
    for u in us:
        try:
            await m.bot.send_message(u.telegram_id,text); sent+=1
        except Exception:
            pass
    await m.answer(f"рџ“ў Yuborildi: {sent}/{len(us)}")



from aiogram.fsm.state import StatesGroup, State

# E'lon uchun alohida yangi holat
class AdState(StatesGroup):
    waiting_for_ad = State()

# 1. Admin panelda "рџ“ў E'lonlar" tugmasi bosilganda ishlaydi
@router.callback_query(F.data == "adm:send_ad")
async def start_ad(c: CallbackQuery, state: FSMContext):
    if not is_admin(c.from_user.id): 
        return
    
    await state.set_state(AdState.waiting_for_ad)
    
    # edit_text o'rniga answer ishlatamiz, shunda xato bermaydi
    await c.message.answer("рџ“ў E'lon bo'limi faollashdi! O'quvchilarga yubormoqchi bo'lgan xabaringizni yozib yuboring.")
    await c.answer()

# 2. Admin e'lon xabarini yuborganida ishlaydi
@router.message(AdState.waiting_for_ad)
async def send_ad_to_all_users(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id): 
        return
    await state.clear()
    status_msg = await m.answer("вЏі E'lon barcha o'quvchilarga yuborilmoqda...")
    
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
        f"рџЏЃ <b>E'lon tarqatish yakunlandi!</b>\n\n"
        f"вњ… Yetkazildi: <b>{success_count} ta</b>\n"
        f"вќЊ Yetkazilmadi: <b>{fail_count} ta</b>",
        reply_markup=admin_menu(),
        parse_mode="HTML"
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

        topic_count = await s.scalar(
            select(func.count(Topic.id))
            .where(Topic.subject_id == sid)
        ) or 0

        question_count = await s.scalar(
            select(func.count(Question.id))
            .join(Topic, Question.topic_id == Topic.id)
            .where(Topic.subject_id == sid)
        ) or 0

        subject_name = subject.name

    await c.message.edit_text(
        "вљ пёЏ <b>FANNI OвЂCHIRISH</b>\n\n"
        f"рџ“љ Fan: <b>{subject_name}</b>\n\n"
        f"рџ“– Mavzular: <b>{topic_count} ta</b>\n"
        f"рџ“ќ Savollar: <b>{question_count} ta</b>\n\n"
        "вќ— <b>DIQQAT!</b>\n"
        "Bu fanni oвЂchirsangiz, unga tegishli "
        "barcha mavzular va savollar ham oвЂchiriladi.\n\n"
        "Rostdan ham oвЂchirmoqchimisiz?",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="вњ… Ha, oвЂchirish",
                    callback_data=f"adm:delete_subject_confirm:{sid}"
                )
            ],
            [
                InlineKeyboardButton(
                    text="вќЊ Bekor qilish",
                    callback_data=f"adm:subject:{sid}"
                )
            ]
        ]),
        parse_mode="HTML"
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
        "рџ—‘ <b>FAN OвЂCHIRILDI!</b>\n\n"
        f"рџ“љ <b>{subject_name}</b>\n\n"
        "Fan va unga tegishli mavzular hamda savollar "
        "muvaffaqiyatli oвЂchirildi.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="рџ“љ Fanlar",
                    callback_data="adm:catalog"
                )
            ],
            [
                InlineKeyboardButton(
                    text="рџЏ  Admin panel",
                    callback_data="admin"
                )
            ]
        ]),
        parse_mode="HTML"
    )

    await c.answer("Fan oвЂchirildi.")

import asyncio
import random
from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile
from aiogram.fsm.context import FSMContext
from sqlalchemy import select
from ..config import settings
from ..db import SessionLocal
from ..models import User, Subject, Topic, Question, TestAttempt, AnswerLog
from ..keyboards import main_menu, back_menu
from ..states import QuizState

router = Router()


async def edit_or_answer(message, *args, **kwargs):
    if message.photo:
        await message.delete()
        return await message.answer(*args, **kwargs)
    return await message.edit_text(*args, **kwargs)



def opt(q, l): 
    return {"A": q.option_a, "B": q.option_b, "C": q.option_c, "D": q.option_d}[l]

def answer_kb(tid, qid): 
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="A", callback_data=f"answer:{tid}:{qid}:A"), InlineKeyboardButton(text="B", callback_data=f"answer:{tid}:{qid}:B")],
        [InlineKeyboardButton(text="C", callback_data=f"answer:{tid}:{qid}:C"), InlineKeyboardButton(text="D", callback_data=f"answer:{tid}:{qid}:D")],
        [InlineKeyboardButton(text="⛔ Testni tugatish", callback_data="quiz:finish")]
    ])

async def render(call, state):
    print(">>> RENDER ENTERED", flush=True)

    d = await state.get_data()
    print(f">>> RENDER INDEX: {d.get("index")}", flush=True)
    print(f">>> RENDER QIDS: {d.get("qids")}", flush=True)

    async with SessionLocal() as s:
        q = await s.get(Question, d["qids"][d["index"]])
        topic = await s.get(Topic, d["topic_id"])

    n = d["index"] + 1
    total = len(d["qids"])
    progress = int(n / total * 100)

    print(f">>> RENDER QUESTION ID: {q.id}", flush=True)
    print(f">>> RENDER IMAGE PATH: {q.image_path!r}", flush=True)
    print(f">>> RENDER MODE: {q.question_mode!r}", flush=True)

    text = (
        f"{chr(0x1F4DA)} <b>{topic.name}</b>\n\n"
        f"{chr(0x2501) * 12}\n"
        f"{chr(0x2753)} <b>Savol: {n}/{total}</b>\n"
        f"{chr(0x1F4CA)} <b>Progress: {progress}%</b>\n"
        f"{chr(0x2501) * 12}\n\n"
        f"{q.text or ''}\n\n"
        f"<b>A)</b> {q.option_a}\n"
        f"<b>B)</b> {q.option_b}\n"
        f"<b>C)</b> {q.option_c}\n"
        f"<b>D)</b> {q.option_d}"
    )

    if q.image_path:
        image_file = FSInputFile(q.image_path)
        print(f">>> IMAGE SEND: {q.image_path}", flush=True)

        try:
            await call.message.delete()
            print(">>> OLD MESSAGE DELETED", flush=True)

            sent = await call.message.answer_photo(
                photo=image_file,
                caption=text,
                reply_markup=answer_kb(topic.id, q.id)
            )

            print(f">>> PHOTO SENT: {sent.photo[-1].file_id}", flush=True)

        except Exception as e:
            print(f">>> PHOTO SEND ERROR: {type(e).__name__}: {e}", flush=True)
            raise
    else:
        await call.message.edit_text(
            text,
            reply_markup=answer_kb(topic.id, q.id)
        )

@router.callback_query(F.data == 'home')
async def home(c, state): 
    await state.clear()
    await edit_or_answer(c.message, '🎓 <b>Doniyor Academy</b>\n\nBosh menyu:', reply_markup=main_menu(c.from_user.id in settings.admins))
    await c.answer()

@router.callback_query(F.data == 'subjects')
async def subjects(c, state):
    await state.clear()
    async with SessionLocal() as s: 
        xs = (await s.scalars(select(Subject).where(Subject.is_active.is_(True)).order_by(Subject.name))).all()
    kb = [[InlineKeyboardButton(text=f'📚 {x.name}', callback_data=f'sub:{x.id}')] for x in xs]
    kb.append([InlineKeyboardButton(text='⬅️ Bosh menyu', callback_data='home')])
    await edit_or_answer(c.message, '📚 <b>Fanlar</b>\n\nFanni tanlang:', reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))
    await c.answer()

@router.callback_query(F.data.startswith('sub:'))
async def topics(c, state):
    await state.clear()
    sid = int(c.data.split(':')[1])
    async with SessionLocal() as s: 
        sub = await s.get(Subject, sid)
        xs = (await s.scalars(select(Topic).where(Topic.subject_id == sid).order_by(Topic.name))).all()
    kb = [[InlineKeyboardButton(text=f'📖 {x.name}', callback_data=f'topic:{x.id}')] for x in xs]
    kb.append([InlineKeyboardButton(text='⬅️ Fanlar', callback_data='subjects')])
    await edit_or_answer(c.message, f'📚 <b>{sub.name}</b>\n\nMavzuni tanlang:', reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))
    await c.answer()

@router.callback_query(F.data.startswith('topic:'))
async def choose(c, state):
    await state.clear()

    tid = int(c.data.split(':')[1])

    async with SessionLocal() as s:
        topic = await s.get(Topic, tid)
        count = len(
            (
                await s.scalars(
                    select(Question).where(
                        Question.topic_id == tid
                    )
                )
            ).all()
        )

    if not count:
        await c.answer(
            'Bu mavzuda hozircha test yo\u2018q.',
            show_alert=True,
        )
        return

    kb = [
        [
            InlineKeyboardButton(
                text=f"\U0001F4DD Testni boshlash \u2014 {count} ta savol",
                callback_data=f"quizsize:{tid}:{count}",
            )
        ],
        [
            InlineKeyboardButton(
                text="\U0001F465 Guruh testi",
                callback_data=f"gquiz:choose:{tid}",
            )
        ],
        [
            InlineKeyboardButton(
                text="\u2B05\uFE0F Mavzular",
                callback_data=f"sub:{topic.subject_id}",
            )
        ],
    ]

    await edit_or_answer(
        c.message,
        f'\U0001F4D6 <b>{topic.name}</b>\n\n'
        f'Jami savollar: <b>{count}</b>\n\n'
        'Test turini tanlang:',
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=kb
        ),
    )

    await c.answer()


@router.callback_query(F.data.startswith('quizsize:'))
async def start(c, state):
    print(">>> QUIZSIZE CALLBACK RECEIVED:", c.data, flush=True)
    _, tid, size = c.data.split(':')
    tid = int(tid)
    size = int(size)
    async with SessionLocal() as s: 
        qs = (await s.scalars(select(Question).where(Question.topic_id == tid))).all()
    qs = random.sample(qs, min(size, len(qs)))

    print(">>> START HANDLER REACHED", flush=True)
    print(">>> SELECTED QIDS:", [q.id for q in qs], flush=True)
    print(">>> SELECTED IMAGES:", [(q.id, q.image_path, q.question_mode) for q in qs], flush=True)

    await state.set_state(QuizState.active)
    await state.update_data(topic_id=tid, qids=[q.id for q in qs], index=0, correct=0, answered=0, selected=[])
    await render(c, state)
    await c.answer('Test boshlandi! 🚀')

@router.callback_query(QuizState.active, F.data.startswith("answer:"))
async def answer(c, state):
    _, tid, qid, sel = c.data.split(":")
    qid = int(qid)
    d = await state.get_data()

    if qid != d["qids"][d["index"]]:
        await c.answer("Bu savol allaqachon javoblangan.", show_alert=True)
        return

    async with SessionLocal() as s:
        q = await s.get(Question, qid)

    if not q:
        await c.answer("❌ Savol topilmadi.", show_alert=True)
        return

    ok = sel == q.correct_option

    # ==========================================
    # 🔄 XATONI QAYTA ISHLASH REJIMI
    # ==========================================
    if d.get("retry_mode"):
        if not ok:
            keyboard = InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="A", callback_data=f"answer:{tid}:{qid}:A"), InlineKeyboardButton(text="B", callback_data=f"answer:{tid}:{qid}:B")],
                    [InlineKeyboardButton(text="C", callback_data=f"answer:{tid}:{qid}:C"), InlineKeyboardButton(text="D", callback_data=f"answer:{tid}:{qid}:D")],
                    [InlineKeyboardButton(text="⬅️ Xatolarimga qaytish", callback_data="my_errors")]
                ]
            )

            retry_text = (
                f"❌ <b>Yana bir bor urinib ko‘ring!</b>\n\n"
                f"? <b>{q.text}</b>\n\n"
                f"<b>A)</b> {q.option_a}\n"
                f"<b>B)</b> {q.option_b}\n"
                f"<b>C)</b> {q.option_c}\n"
                f"<b>D)</b> {q.option_d}\n\n"
                f"💡 <i>To‘g‘ri javobni topmaguningizcha davom etamiz.</i>"
            )

            if q.image_path:
                try:
                    await c.message.delete()
                except Exception:
                    pass

                await c.message.answer_photo(
                    FSInputFile(q.image_path),
                    caption=retry_text,
                    reply_markup=keyboard
                )
            else:
                await edit_or_answer(
                    c.message,
                    retry_text,
                    reply_markup=keyboard
                )
            await c.answer("❌ Noto‘g‘ri. Yana urinib ko‘ring!")
            return

        async with SessionLocal() as s:
            u = await s.scalar(select(User).where(User.telegram_id == c.from_user.id))
            old_errors = (
                await s.scalars(
                    select(AnswerLog)
                    .join(TestAttempt, AnswerLog.attempt_id == TestAttempt.id)
                    .where(
                        TestAttempt.user_id == u.id,
                        AnswerLog.question_id == qid,
                        AnswerLog.is_correct.is_(False)
                    )
                )
            ).all()

            for log in old_errors:
                await s.delete(log)
            await s.commit()

        await state.clear()
        await edit_or_answer(c.message, 
            f"🎉 <b>BARAKALLA!</b>\n"
            f"━━━━━━━━━━━━━━\n\n"
            f"✅ Siz savolga to‘g‘ri javob berdingiz!\n\n"
            f"❓ <b>{q.text}</b>\n\n"
            f"✅ To‘g‘ri javob:\n"
            f"<b>{q.correct_option}) {opt(q, q.correct_option)}</b>\n\n"
            f"🏆 <b>Bu savol o‘zlashtirildi!</b>\n\n"
            f"Endi u <b>“Mening xatolarim”</b> bo‘limida chiqmaydi.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="❌ Mening xatolarim", callback_data="my_errors")],
                    [InlineKeyboardButton(text="🏠 Bosh menyu", callback_data="home")]
                ]
            )
        )
        await c.answer("✅ To‘g‘ri! Savol o‘zlashtirildi.")
        return

    # ==========================================
    # 📝 ODDIY TEST REJIMI
    # ==========================================
    correct = d["correct"] + int(ok)
    answered = d["answered"] + 1
    logs = d["selected"] + [{"qid": qid, "selected": sel, "correct": ok}]

    await state.update_data(correct=correct, answered=answered, selected=logs)

    if ok:
        result = "✅ <b>To‘g‘ri!</b>"
    else:
        result = (
            f"❌ <b>Xato.</b>\n"
            f"To‘g‘ri javob: <b>{q.correct_option}) {opt(q, q.correct_option)}</b>"
        )

    if q.explanation:
        result += f"\n\n💡 <b>Izoh:</b> {q.explanation}"

    if d["index"] + 1 >= len(d["qids"]):
        buttons = [[InlineKeyboardButton(text="🏆 Natijani ko‘rish", callback_data="quiz:finish")]]

        await edit_or_answer(
            c.message,
            f"{result}\n\n📊 Hozirgi natija: <b>{correct}/{answered}</b>",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
        )
        await c.answer()
        return

    await edit_or_answer(
        c.message,
        f"{result}\n\n📊 Hozirgi natija: <b>{correct}/{answered}</b>"
    )
    await c.answer()
    await asyncio.sleep(1.2)

    await state.update_data(index=d["index"] + 1)
    await render(c, state)
    return
@router.callback_query(QuizState.active, F.data == "quiz:next")
async def quiz_next(c, state):
    d = await state.get_data()

    if d.get("retry_mode"):
        await c.answer("Bu savolni avval to‘g‘ri javoblang.", show_alert=True)
        return

    next_index = d["index"] + 1

    if next_index >= len(d["qids"]):
        await c.answer("Test yakunlandi.", show_alert=True)
        return

    await state.update_data(index=next_index)

    await render(c, state)
    await c.answer()


@router.callback_query(QuizState.active, F.data == "quiz:finish")
async def quiz_finish(c, state):
    d = await state.get_data()

    if d.get("retry_mode"):
        await c.answer("Bu rejimda testni tugatib bo‘lmaydi.", show_alert=True)
        return

    qids = d.get("qids", [])
    selected = d.get("selected", [])
    total = len(qids)
    correct = d.get("correct", 0)
    topic_id = d.get("topic_id")

    async with SessionLocal() as s:
        u = await s.scalar(
            select(User).where(User.telegram_id == c.from_user.id)
        )

        if not u:
            await c.answer("Foydalanuvchi topilmadi.", show_alert=True)
            return

        attempt = TestAttempt(
            user_id=u.id,
            topic_id=topic_id,
            total=total,
            correct=correct,
            score=correct
        )

        s.add(attempt)
        await s.flush()

        for item in selected:
            log = AnswerLog(
                attempt_id=attempt.id,
                question_id=item["qid"],
                selected_option=item["selected"],
                is_correct=item["correct"]
            )
            s.add(log)

        u.total_tests += 1
        u.total_questions += total
        u.correct_answers += correct
        u.total_score += correct

        await s.commit()

    percentage = (correct / total * 100) if total else 0

    grade = (
        "🏆 A'lo" if percentage >= 90
        else "🥇 Yaxshi" if percentage >= 70
        else "📖 Qoniqarli" if percentage >= 50
        else "💪 Ko‘proq mashq kerak"
    )

    await state.clear()

    await edit_or_answer(c.message, 
        f"🏆 <b>TEST NATIJASI</b>\n"
        f"━━━━━━━━━━━━━━\n\n"
        f"🎯 Natija: <b>{correct}/{total}</b>\n"
        f"📈 Foiz: <b>{percentage:.1f}%</b>\n"
        f"⭐ Ball: <b>+{correct}</b>\n"
        f"🏅 Baho: <b>{grade}</b>\n",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(
                    text="📖 Test tafsilotlari",
                    callback_data=f"attempt:{attempt.id}"
                )],
                [InlineKeyboardButton(
                    text="❌ Mening xatolarim",
                    callback_data="my_errors"
                )],
                [InlineKeyboardButton(
                    text="🏠 Bosh menyu",
                    callback_data="home"
                )]
            ]
        )
    )

    await c.answer()
@router.callback_query(F.data == "my_errors")
async def my_errors(c, state):
    await errors_page(c, state)

@router.callback_query(F.data.startswith("errors_page:"))
async def errors_page(c, state):
    await state.clear()
    page = int(c.data.split(":")[1]) if ":" in c.data else 0
    per_page = 5

    async with SessionLocal() as s:
        u = await s.scalar(select(User).where(User.telegram_id == c.from_user.id))
        logs = (
            await s.scalars(
                select(AnswerLog)
                .join(TestAttempt, AnswerLog.attempt_id == TestAttempt.id)
                .where(TestAttempt.user_id == u.id, AnswerLog.is_correct.is_(False))
                .order_by(AnswerLog.id.desc())
            )
        ).all()

        latest = {}
        for log in logs:
            if log.question_id not in latest:
                latest[log.question_id] = log

        errors_list = list(latest.values())

    if not errors_list:
        await edit_or_answer(c.message, 
            "❌ <b>Mening xatolarim</b>\n\n🎉 Xatolar mavjud emas!",
            reply_markup=back_menu()
        )
        await c.answer()
        return

    total_pages = (len(errors_list) + per_page - 1) // per_page
    if page >= total_pages:
        page = total_pages - 1

    start_index = page * per_page
    page_items = errors_list[start_index:start_index + per_page]
    buttons = []

    async with SessionLocal() as s:
        for i, log in enumerate(page_items, start_index + 1):
            q = await s.get(Question, log.question_id)
            if q:
                short_text = q.text[:45].replace("\n", " ")
                if len(q.text) > 45:
                    short_text += "..."
                buttons.append([InlineKeyboardButton(text=f"❌ {i}. {short_text}", callback_data=f"errorq:{q.id}")])

    navigation = []
    if page > 0:
        navigation.append(InlineKeyboardButton(text="⬅️ Oldingi", callback_data=f"errors_page:{page - 1}"))
    if page < total_pages - 1:
        navigation.append(InlineKeyboardButton(text="Keyingi ➡️", callback_data=f"errors_page:{page + 1}"))

    if navigation:
        buttons.append(navigation)

    buttons.append([InlineKeyboardButton(text="🏠 Bosh menyu", callback_data="home")])

    await edit_or_answer(c.message, 
        f"❌ <b>Mening xatolarim</b>\n\n"
        f"Jami: <b>{len(errors_list)}</b> ta xato\n"
        f"Sahifa: <b>{page + 1}/{total_pages}</b>\n\n"
        f"Ko'rish uchun savolni tanlang:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
    )
    await c.answer()
@router.callback_query(F.data.startswith("retry_error:"))
async def retry_error(c, state):
    await state.clear()

    qid = int(c.data.split(":")[1])

    async with SessionLocal() as s:
        q = await s.get(Question, qid)

        if not q:
            await c.answer("Savol topilmadi.", show_alert=True)
            return

        topic = await s.get(Topic, q.topic_id)

    await state.set_state(QuizState.active)

    await state.update_data(
        topic_id=topic.id,
        qids=[qid],
        index=0,
        correct=0,
        answered=0,
        selected=[],
        retry_mode=True
    )

    retry_text = (
        f"{chr(0x1F504)} <b>XATO SAVOLNI QAYTA ISHLASH</b>\n\n"
        f"{chr(0x1F4DA)} <b>{topic.name}</b>\n\n"
        f"{chr(0x2753)} <b>{q.text or ''}</b>\n\n"
        f"<b>A)</b> {q.option_a}\n"
        f"<b>B)</b> {q.option_b}\n"
        f"<b>C)</b> {q.option_c}\n"
        f"<b>D)</b> {q.option_d}\n\n"
        f"{chr(0x1F4A1)} <i>To'g'ri javobni toping.</i>"
    )

    if q.image_path:
        try:
            await c.message.delete()
        except Exception:
            pass

        await c.message.answer_photo(
            FSInputFile(q.image_path),
            caption=retry_text,
            reply_markup=answer_kb(topic.id, q.id)
        )
    else:
        await edit_or_answer(
            c.message,
            retry_text,
            reply_markup=answer_kb(topic.id, q.id)
        )

    await c.answer(f"{chr(0x1F504)} Savol qayta yuklandi.")


@router.callback_query(F.data.startswith('errorq:'))
async def error_question(c, state):
    await state.clear()
    qid = int(c.data.split(":")[1])

    async with SessionLocal() as s:
        q = await s.get(Question, qid)

        if not q:
            await c.answer("Savol topilmadi.", show_alert=True)
            return

        topic = await s.get(Topic, q.topic_id)
        u = await s.scalar(
            select(User).where(User.telegram_id == c.from_user.id)
        )

        log = await s.scalar(
            select(AnswerLog)
            .join(TestAttempt, AnswerLog.attempt_id == TestAttempt.id)
            .where(
                TestAttempt.user_id == u.id,
                AnswerLog.question_id == qid
            )
            .order_by(AnswerLog.id.desc())
        )

    if not log:
        await c.answer("Javob tarixi topilmadi.", show_alert=True)
        return

    selected_text = opt(q, log.selected_option)
    correct_text = opt(q, q.correct_option)

    text = (
        f"{chr(0x274C)} <b>XATO SAVOL</b>\n"
        f"{chr(0x2501) * 14}\n\n"
        f"{chr(0x1F4DA)} <b>{topic.name}</b>\n\n"
        f"{chr(0x2753)} <b>{q.text or ''}</b>\n\n"
        f"<b>A)</b> {q.option_a}\n"
        f"<b>B)</b> {q.option_b}\n"
        f"<b>C)</b> {q.option_c}\n"
        f"<b>D)</b> {q.option_d}\n\n"
        f"{chr(0x274C)} Sizning javobingiz:\n"
        f"<b>{log.selected_option}) {selected_text}</b>\n\n"
        f"{chr(0x2705)} To'g'ri javob:\n"
        f"<b>{q.correct_option}) {correct_text}</b>"
    )

    if q.explanation:
        text += f"\n\n{chr(0x1F4A1)} <b>Izoh:</b>\n{q.explanation}"

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔄 Qayta ishlash",
                    callback_data=f"retry_error:{q.id}"
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ Xatolarimga qaytish",
                    callback_data="my_errors"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🏠 Bosh menyu",
                    callback_data="home"
                )
            ]
        ]
    )

    if q.image_path:
        try:
            await c.message.delete()
        except Exception:
            pass

        await c.message.answer_photo(
            FSInputFile(q.image_path),
            caption=text,
            reply_markup=keyboard
        )
    else:
        await edit_or_answer(
            c.message,
            text,
            reply_markup=keyboard
        )

    await c.answer()


@router.callback_query(F.data == 'profile')
async def profile(c):
    async with SessionLocal() as s:
        u = await s.scalar(select(User).where(User.telegram_id == c.from_user.id))

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📋 Testlar tarixi", callback_data="history")],
        [InlineKeyboardButton(text="⬅️ Bosh menyu", callback_data="home")]
    ])

    await edit_or_answer(c.message, 
        f"👤 <b>Profil</b>\n\n"
        f"Ism: {u.full_name}\n"
        f"Username: @{u.username or '—'}\n"
        f"Testlar: {u.total_tests}\n"
        f"Savollar: {u.total_questions}\n"
        f"To‘g‘ri: {u.correct_answers}\n"
        f"Ball: {u.total_score}",
        reply_markup=kb
    )
    await c.answer()

@router.callback_query(F.data == 'history')
async def history(c):
    async with SessionLocal() as s:
        u = await s.scalar(select(User).where(User.telegram_id == c.from_user.id))
        attempts = (
            await s.scalars(
                select(TestAttempt)
                .where(TestAttempt.user_id == u.id)
                .order_by(TestAttempt.created_at.desc())
                .limit(10)
            )
        ).all()

        if not attempts:
            await edit_or_answer(c.message, "📋 <b>Testlar tarixi</b>\n\nHozircha test ishlanmagan.", reply_markup=back_menu())
            await c.answer()
            return

        lines = ["📋 <b>TESTLAR TARIXI</b>\n"]
        buttons = []

        for i, a in enumerate(attempts, 1):
            topic = await s.get(Topic, a.topic_id)
            pct = (a.correct / a.total * 100) if a.total else 0
            date = a.created_at.strftime("%d.%m.%Y %H:%M")

            lines.append(
                f"<b>{i}. {topic.name if topic else 'Mavzu o‘chirilgan'}</b>\n"
                f"   🎯 {a.correct}/{a.total} — {pct:.1f}%\n"
                f"   ⭐ +{a.score} ball · {date}\n"
            )

            buttons.append([InlineKeyboardButton(text=f"📖 {i}-test natijasini ko‘rish", callback_data=f"attempt:{a.id}")])

        buttons.append([InlineKeyboardButton(text="↩️ Profil", callback_data="profile")])

        await edit_or_answer(c.message, "\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await c.answer()

@router.callback_query(F.data.startswith('attempt:'))
async def attempt_detail(c):
    attempt_id = int(c.data.split(":")[1])

    async with SessionLocal() as s:
        u = await s.scalar(select(User).where(User.telegram_id == c.from_user.id))
        attempt = await s.get(TestAttempt, attempt_id)

        if not attempt or attempt.user_id != u.id:
            await c.answer("Bu testni ko‘rishga ruxsat yo‘q.", show_alert=True)
            return

        topic = await s.get(Topic, attempt.topic_id)
        logs = (await s.scalars(select(AnswerLog).where(AnswerLog.attempt_id == attempt.id).order_by(AnswerLog.id))).all()

        questions = {}
        for log in logs:
            question = await s.get(Question, log.question_id)
            questions[log.question_id] = question

    pct = (attempt.correct / attempt.total * 100) if attempt.total else 0
    answered = len(logs)
    unanswered = attempt.total - answered

    grade = (
        "🏆 A'lo" if pct >= 90
        else "🥇 Yaxshi" if pct >= 70
        else "📖 Qoniqarli" if pct >= 50
        else "💪 Ko‘proq mashq kerak"
    )

    lines = [
        "📖 <b>TEST NATIJASI</b>",
        "━━━━━━━━━━━━━━",
        "",
        f"📚 Mavzu: <b>{topic.name if topic else 'Mavzu o‘chirilgan'}</b>",
        "",
        f"🎯 Natija: <b>{attempt.correct}/{attempt.total}</b>",
        f"📈 Foiz: <b>{pct:.1f}%</b>",
        f"❓ Javob berilmagan: <b>{unanswered}</b>",
        f"⭐ Ball: <b>+{attempt.score}</b>",
        f"🏅 Baho: <b>{grade}</b>",
        f"⏰ Sana: <b>{attempt.created_at.strftime('%d.%m.%Y %H:%M')}</b>",
        "",
        "📋 <b>JAVOBLAR:</b>",
        ""
    ]

    for i, log in enumerate(logs, 1):
        question = questions.get(log.question_id)
        if not question:
            continue

        correct_option = (question.correct_option or "").strip().upper()
        selected_option = (log.selected_option or "").strip().upper()

        selected_text = opt(question, selected_option)
        correct_text = opt(question, correct_option)

        if log.is_correct:
            lines.append(f"{i}. ✅ <b>To‘g‘ri</b>")
        else:
            lines.append(f"{i}. ❌ <b>Xato</b>")

        lines.append(f"   ❓ <b>{question.text}</b>")
        lines.append(f"   👤 Sizning javobingiz:")
        lines.append(f"   <b>{selected_option})</b> {selected_text}")

        if not log.is_correct:
            lines.append(f"   ✅ To‘g‘ri javob:")
            lines.append(f"   <b>{correct_option})</b> {correct_text}")

        if question.explanation:
            lines.append(f"   💡 <b>Izoh:</b> {question.explanation}")

        lines.append("")

    if unanswered > 0:
        lines.append(f"⚠️ <b>{unanswered} ta savolga javob berilmagan.</b>")

    await edit_or_answer(c.message, 
        "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Testlar tarixi", callback_data="history")],
                [InlineKeyboardButton(text="👤 Profil", callback_data="profile")],
                [InlineKeyboardButton(text="🏠 Bosh menyu", callback_data="home")]
            ]
        )
    )
    await c.answer()

@router.callback_query(F.data == 'stats')
async def stats(c):
    async with SessionLocal() as s: 
        u = await s.scalar(select(User).where(User.telegram_id == c.from_user.id))
    pct = u.correct_answers / u.total_questions * 100 if u.total_questions else 0
    await edit_or_answer(c.message, f'📊 <b>Statistika</b>\n\n📝 Savollar: {u.total_questions}\n✅ To‘g‘ri: {u.correct_answers}\n❌ Xato: {u.total_questions-u.correct_answers}\n📈 Aniqlik: {pct:.1f}%\n⭐ Ball: {u.total_score}', reply_markup=back_menu())
    await c.answer()

@router.callback_query(F.data == 'leaderboard')
async def leaderboard(c):
    async with SessionLocal() as s: 
        us = (await s.scalars(select(User).order_by(User.total_score.desc()).limit(10))).all()
    lines = ['🏆 <b>TOP 10 REYTING</b>\n']
    medals = {1: '🥇', 2: '🥈', 3: '🥉'}
    for i, u in enumerate(us, 1): 
        lines.append(f"{medals.get(i, str(i) + '.')} {u.full_name} — <b>{u.total_score}</b> ball")
    await edit_or_answer(c.message, '\n'.join(lines), reply_markup=back_menu())
    await c.answer()

@router.callback_query(F.data == 'announcements')
async def announcements(c): 
    await edit_or_answer(c.message, "📢 <b>E'lonlar</b>\n\nHozircha yangi e'lonlar mavjud emas.", reply_markup=back_menu())
    await c.answer()



from pathlib import Path

path = Path(r"C:\doniyor_academy_bot\app\handlers\admin.py")
text = path.read_text(encoding="utf-8")

marker = '@router.callback_query(F.data == "adm:stats")'

new_block = '''@router.callback_query(F.data.startswith("adm:questions:"))
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
            f"📖 <b>{topic.name}</b>\\n\\n"
            "📝 Bu mavzuda hozircha savollar yo‘q.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(
                    text="⬅️ Mavzu",
                    callback_data=f"adm:topic:{tid}"
                )]
            ])
        )
        await c.answer()
        return

    lines = [
        f"📖 <b>{topic.name}</b>",
        f"📝 Savollar: <b>{len(questions)}</b>",
        ""
    ]

    buttons = []

    for i, q in enumerate(questions, 1):
        short_text = q.text.replace("\\n", " ").strip()
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
            text="⬅️ Mavzu",
            callback_data=f"adm:topic:{tid}"
        )
    ])

    await c.message.edit_text(
        "\\n".join(lines),
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
        "📝 <b>SAVOL</b>\\n"
        "━━━━━━━━━━━━━━\\n\\n"
        f"❓ <b>{q.text}</b>\\n\\n"
        f"A) {q.option_a}\\n"
        f"B) {q.option_b}\\n"
        f"C) {q.option_c}\\n"
        f"D) {q.option_d}\\n\\n"
        f"✅ To‘g‘ri javob: <b>{q.correct_option}</b>"
    )

    if q.explanation:
        text += f"\\n\\n💡 <b>Izoh:</b> {q.explanation}"

    await c.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text="✏️ Tahrirlash",
                callback_data=f"adm:edit_question:{q.id}"
            )],
            [InlineKeyboardButton(
                text="🗑 O‘chirish",
                callback_data=f"adm:delete_question:{q.id}"
            )],
            [InlineKeyboardButton(
                text="⬅️ Savollar",
                callback_data=f"adm:questions:{q.topic_id}"
            )],
            [InlineKeyboardButton(
                text="🏠 Admin panel",
                callback_data="admin"
            )]
        ])
    )
    await c.answer()


'''

if marker not in text:
    raise SystemExit("MARKER TOPILMADI")

path.write_text(
    text.replace(marker, new_block + marker, 1),
    encoding="utf-8"
)

print("QUESTION CATALOG UPDATED")

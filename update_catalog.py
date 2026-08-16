from pathlib import Path

path = Path(r"C:\doniyor_academy_bot\app\handlers\admin.py")
text = path.read_text(encoding="utf-8")

start = text.index('@router.callback_query(F.data == "adm:catalog")')
end = text.index('@router.callback_query(F.data == "adm:stats")')

new_block = '''# -------------------- CATALOG / STATS --------------------
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
        await c.answer("Hali fanlar yo‘q.", show_alert=True)
        return

    buttons = [
        [InlineKeyboardButton(
            text=f"📚 {sub.name}",
            callback_data=f"adm:subject:{sub.id}"
        )]
        for sub in subjects
    ]

    buttons.append([
        InlineKeyboardButton(
            text="⬅️ Admin panel",
            callback_data="admin"
        )
    ])

    await c.message.edit_text(
        "📚 <b>Fanlar</b>\\n\\nKerakli fanni tanlang:",
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

        buttons = [
            [InlineKeyboardButton(
                text=f"📖 {topic.name}",
                callback_data=f"adm:topic:{topic.id}"
            )]
            for topic in topics
        ]

    if not topics:
        buttons.append([
            InlineKeyboardButton(
                text="➕ Mavzu qo‘shish",
                callback_data="adm:add_topic"
            )
        ])

    buttons.append([
        InlineKeyboardButton(
            text="⬅️ Fanlar",
            callback_data="adm:catalog"
        )
    ])

    await c.message.edit_text(
        f"📚 <b>{subject.name}</b>\\n\\n"
        "📖 Mavzuni tanlang:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
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
            text="📋 Savollarni ko‘rish",
            callback_data=f"adm:questions:{tid}"
        )],
        [InlineKeyboardButton(
            text="✏️ Mavzuni tahrirlash",
            callback_data=f"adm:edit_topic:{tid}"
        )],
        [InlineKeyboardButton(
            text="🗑 Mavzuni o‘chirish",
            callback_data=f"adm:delete_topic:{tid}"
        )],
        [InlineKeyboardButton(
            text="⬅️ Mavzular",
            callback_data=f"adm:subject:{topic.subject_id}"
        )]
    ]

    await c.message.edit_text(
        f"📖 <b>{topic.name}</b>\\n\\n"
        f"📝 Savollar: <b>{count}</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
    )
    await c.answer()


'''

path.write_text(
    text[:start] + new_block + text[end:],
    encoding="utf-8"
)

print("CATALOG NAVIGATION UPDATED")

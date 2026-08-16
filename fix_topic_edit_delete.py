from pathlib import Path

p = Path(r"C:\doniyor_academy_bot\app\handlers\admin.py")
text = p.read_text(encoding="utf-8")

marker = '# -------------------- QUESTION EDIT / DELETE --------------------'

if marker not in text:
    raise SystemExit("XATO: QUESTION EDIT / DELETE bo‘limi topilmadi.")

# Eski topic edit/delete bo‘limlari bo‘lsa olib tashlash uchun
start_marker = "# -------------------- TOPIC EDIT / DELETE --------------------"

if start_marker in text:
    start = text.index(start_marker)
    next_marker = "# -------------------- QUESTION EDIT / DELETE --------------------"
    end = text.index(next_marker, start)
    text = text[:start] + text[end:]

insert_before = "# -------------------- QUESTION EDIT / DELETE --------------------"

code = r'''
# -------------------- TOPIC EDIT / DELETE --------------------

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
        f"✏️ <b>Mavzuni tahrirlash</b>\n\n"
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
        await m.answer("❗ Mavzu nomi bo‘sh bo‘lishi mumkin emas.")
        return

    data = await state.get_data()
    tid = data.get("topic_id")

    async with SessionLocal() as s:
        topic = await s.get(Topic, tid)

        if not topic:
            await state.clear()
            await m.answer(
                "❌ Mavzu topilmadi.",
                reply_markup=admin_menu()
            )
            return

        exists = await s.scalar(
            select(Topic).where(
                Topic.subject_id == topic.subject_id,
                func.lower(Topic.name) == name.lower(),
                Topic.id != topic.id
            )
        )

        if exists:
            await m.answer("⚠️ Bu nomdagi mavzu allaqachon mavjud.")
            return

        topic.name = name
        subject_id = topic.subject_id

        await s.commit()

    await state.clear()

    await m.answer(
        "✅ <b>Mavzu muvaffaqiyatli tahrirlandi!</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📖 Mavzuni ko‘rish",
                    callback_data=f"adm:topic:{tid}"
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ Fan",
                    callback_data=f"adm:subject:{subject_id}"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🏠 Admin panel",
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
        "🗑 <b>Mavzu o‘chirildi!</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⬅️ Fan",
                    callback_data=f"adm:subject:{subject_id}"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🏠 Admin panel",
                    callback_data="admin"
                )
            ]
        ])
    )

    await c.answer("Mavzu o‘chirildi.")


'''

text = text.replace(insert_before, code + "\n" + insert_before, 1)

p.write_text(text, encoding="utf-8")

print("OK — TOPIC EDIT/DELETE HANDLERS ADDED")

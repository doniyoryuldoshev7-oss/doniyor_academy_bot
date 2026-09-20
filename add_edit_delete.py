from pathlib import Path

path = Path(r"C:\doniyor_academy_bot\app\handlers\admin.py")
text = path.read_text(encoding="utf-8")

marker = '\n@router.callback_query(F.data == "adm:stats")'

code = '''

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

    await state.update_data(question_id=qid)
    await state.set_state(AdminState.edit_question_text)

    await c.message.edit_text(
        "✏️ <b>Savolni tahrirlash</b>\\n\\n"
        "Yangi savol matnini yuboring:",
        reply_markup=cancel_kb()
    )
    await c.answer()


@router.message(AdminState.edit_question_text)
async def edit_question_text(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return

    value = (m.text or "").strip()

    if not value:
        await m.answer("❗ Savol matni bo‘sh bo‘lishi mumkin emas.")
        return

    data = await state.get_data()

    async with SessionLocal() as s:
        q = await s.get(Question, data["question_id"])

        if not q:
            await state.clear()
            await m.answer("❌ Savol topilmadi.", reply_markup=admin_menu())
            return

        q.text = value
        await s.commit()

    await state.clear()

    await m.answer(
        "✅ <b>Savol muvaffaqiyatli tahrirlandi!</b>",
        reply_markup=admin_menu()
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
        "🗑 <b>Savol o‘chirildi.</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text="⬅️ Savollar",
                callback_data=f"adm:questions:{topic_id}"
            )],
            [InlineKeyboardButton(
                text="🏠 Admin panel",
                callback_data="admin"
            )]
        ])
    )

    await c.answer("Savol o‘chirildi.")
'''

if marker not in text:
    raise SystemExit("MARKER TOPILMADI")

text = text.replace(marker, code + marker, 1)

path.write_text(text, encoding="utf-8")

print("OK — EDIT DELETE HANDLERS ADDED")

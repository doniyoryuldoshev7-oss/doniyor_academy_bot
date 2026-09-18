from pathlib import Path

path = Path(r"C:\doniyor_academy_bot\app\handlers\admin.py")
text = path.read_text(encoding="utf-8")

start = text.index('@router.callback_query(F.data.startswith("adm:edit_question:"))')
end = text.index('@router.callback_query(F.data == "adm:stats")', start)

new_block = r'''
# -------------------- QUESTION EDIT / DELETE --------------------

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
        "✏️ <b>SAVOLNI TAHRIRLASH</b>\n\n"
        "1️⃣ Yangi savol matnini yuboring:",
        reply_markup=cancel_kb()
    )
    await c.answer()


@router.message(AdminState.edit_question_text)
async def edit_question_text_handler(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return

    value = (m.text or "").strip()

    if not value:
        await m.answer("❗ Savol bo‘sh bo‘lishi mumkin emas.")
        return

    await state.update_data(text=value)
    await state.set_state(AdminState.edit_question_a)

    await m.answer(
        "2️⃣ <b>A variant</b>ni yuboring:",
        reply_markup=cancel_kb()
    )


@router.message(AdminState.edit_question_a)
async def edit_question_a_handler(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return

    value = (m.text or "").strip()

    if not value:
        await m.answer("❗ Variant bo‘sh bo‘lishi mumkin emas.")
        return

    await state.update_data(option_a=value)
    await state.set_state(AdminState.edit_question_b)

    await m.answer(
        "3️⃣ <b>B variant</b>ni yuboring:",
        reply_markup=cancel_kb()
    )


@router.message(AdminState.edit_question_b)
async def edit_question_b_handler(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return

    value = (m.text or "").strip()

    if not value:
        await m.answer("❗ Variant bo‘sh bo‘lishi mumkin emas.")
        return

    await state.update_data(option_b=value)
    await state.set_state(AdminState.edit_question_c)

    await m.answer(
        "4️⃣ <b>C variant</b>ni yuboring:",
        reply_markup=cancel_kb()
    )


@router.message(AdminState.edit_question_c)
async def edit_question_c_handler(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return

    value = (m.text or "").strip()

    if not value:
        await m.answer("❗ Variant bo‘sh bo‘lishi mumkin emas.")
        return

    await state.update_data(option_c=value)
    await state.set_state(AdminState.edit_question_d)

    await m.answer(
        "5️⃣ <b>D variant</b>ni yuboring:",
        reply_markup=cancel_kb()
    )


@router.message(AdminState.edit_question_d)
async def edit_question_d_handler(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return

    value = (m.text or "").strip()

    if not value:
        await m.answer("❗ Variant bo‘sh bo‘lishi mumkin emas.")
        return

    data = await state.get_data()

    async with SessionLocal() as s:
        q = await s.get(Question, data["question_id"])

        if not q:
            await state.clear()
            await m.answer(
                "❌ Savol topilmadi.",
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
        "✅ <b>SAVOL MUVAFFAQIYATLI TAHRIRLANDI!</b>\n\n"
        "Savol va barcha variantlar yangilandi.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📋 Savolni ko‘rish",
                    callback_data=f"adm:question:{qid}"
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ Savollar",
                    callback_data=f"adm:questions:{topic_id}"
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
        "🗑 <b>SAVOL O‘CHIRILDI!</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⬅️ Savollar",
                    callback_data=f"adm:questions:{topic_id}"
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

    await c.answer("Savol o‘chirildi.")


'''

path.write_text(text[:start] + new_block + text[end:], encoding="utf-8")
print("QUESTION EDIT DELETE BLOCK REPLACED")

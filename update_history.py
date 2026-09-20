from pathlib import Path

path = Path(r"C:\doniyor_academy_bot\app\handlers\student.py")
text = path.read_text(encoding="utf-8")

start = text.index("@router.callback_query(F.data=='history')")
end = text.index("@router.callback_query(F.data=='stats')")

new_block = '''@router.callback_query(F.data=='history')
async def history(c):
    async with SessionLocal() as s:
        u = await s.scalar(
            select(User).where(User.telegram_id == c.from_user.id)
        )

        attempts = (
            await s.scalars(
                select(TestAttempt)
                .where(TestAttempt.user_id == u.id)
                .order_by(TestAttempt.created_at.desc())
                .limit(10)
            )
        ).all()

        if not attempts:
            await c.message.edit_text(
                "📋 <b>Testlar tarixi</b>\\n\\n"
                "Hozircha test ishlanmagan.",
                reply_markup=back_menu()
            )
            await c.answer()
            return

        lines = ["📋 <b>TESTLAR TARIXI</b>\\n"]
        buttons = []

        for i, a in enumerate(attempts, 1):
            topic = await s.get(Topic, a.topic_id)

            pct = (a.correct / a.total * 100) if a.total else 0
            date = a.created_at.strftime("%d.%m.%Y %H:%M")

            lines.append(
                f"<b>{i}. {topic.name if topic else 'Mavzu o‘chirilgan'}</b>\\n"
                f"   🎯 {a.correct}/{a.total} — {pct:.1f}%\\n"
                f"   ⭐ +{a.score} ball · {date}\\n"
            )

            buttons.append([
                InlineKeyboardButton(
                    text=f"📖 {i}-test natijasini ko‘rish",
                    callback_data=f"attempt:{a.id}"
                )
            ])

        buttons.append([
            InlineKeyboardButton(
                text="⬅️ Profil",
                callback_data="profile"
            )
        ])

    await c.message.edit_text(
        "\\n".join(lines),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
    )
    await c.answer()


@router.callback_query(F.data.startswith('attempt:'))
async def attempt_detail(c):
    attempt_id = int(c.data.split(":")[1])

    async with SessionLocal() as s:
        u = await s.scalar(
            select(User).where(User.telegram_id == c.from_user.id)
        )

        attempt = await s.get(TestAttempt, attempt_id)

        if not attempt or attempt.user_id != u.id:
            await c.answer(
                "Bu testni ko‘rishga ruxsat yo‘q.",
                show_alert=True
            )
            return

        topic = await s.get(Topic, attempt.topic_id)

    pct = (attempt.correct / attempt.total * 100) if attempt.total else 0
    unanswered = attempt.total - attempt.correct

    text = (
        "📖 <b>TEST NATIJASI</b>\\n"
        "━━━━━━━━━━━━━━\\n\\n"
        f"📚 Mavzu: <b>{topic.name if topic else 'Mavzu o‘chirilgan'}</b>\\n\\n"
        f"🎯 Natija: <b>{attempt.correct}/{attempt.total}</b>\\n"
        f"📈 Foiz: <b>{pct:.1f}%</b>\\n"
        f"⭐ Ball: <b>+{attempt.score}</b>\\n"
        f"🕐 Sana: <b>{attempt.created_at.strftime('%d.%m.%Y %H:%M')}</b>"
    )

    await c.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="⬅️ Testlar tarixi",
                        callback_data="history"
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
    )
    await c.answer()


'''

path.write_text(text[:start] + new_block + text[end:], encoding="utf-8")
print("HISTORY BLOCK UPDATED")

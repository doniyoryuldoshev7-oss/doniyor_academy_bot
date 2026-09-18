from pathlib import Path

path = Path(r"C:\doniyor_academy_bot\app\handlers\student.py")
text = path.read_text(encoding="utf-8")

start = text.index("@router.callback_query(F.data.startswith('attempt:'))")
end = text.index("@router.callback_query(F.data=='stats')", start)

new_block = '''@router.callback_query(F.data.startswith('attempt:'))
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

        logs = (
            await s.scalars(
                select(AnswerLog)
                .where(AnswerLog.attempt_id == attempt.id)
                .order_by(AnswerLog.id)
            )
        ).all()

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
        else "📚 Qoniqarli" if pct >= 50
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
        f"🕐 Sana: <b>{attempt.created_at.strftime('%d.%m.%Y %H:%M')}</b>",
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

        if log.is_correct:
            lines.append(f"{i}. ✅ <b>To‘g‘ri</b>")
        else:
            lines.append(f"{i}. ❌ <b>Xato</b>")

        lines.append(
            f"   ❓ {question.text}"
        )

        lines.append(
            f"   👤 Sizning javobingiz: <b>{selected_option}</b>"
        )

        if not log.is_correct:
            lines.append(
                f"   ✅ To‘g‘ri javob: <b>{correct_option}</b>"
            )

        lines.append("")

    if unanswered > 0:
        lines.append(
            f"⚠️ <b>{unanswered} ta savolga javob berilmagan.</b>"
        )

    await c.message.edit_text(
        "\\n".join(lines),
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
                        text="👤 Profil",
                        callback_data="profile"
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
print("ATTEMPT DETAIL UPDATED")

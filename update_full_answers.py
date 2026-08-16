from pathlib import Path

path = Path(r"C:\doniyor_academy_bot\app\handlers\student.py")
text = path.read_text(encoding="utf-8")

old = '''    for i, log in enumerate(logs, 1):
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
'''

new = '''    for i, log in enumerate(logs, 1):
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

        lines.append(
            f"   ❓ <b>{question.text}</b>"
        )

        lines.append(
            f"   👤 Sizning javobingiz:"
        )
        lines.append(
            f"   <b>{selected_option})</b> {selected_text}"
        )

        if not log.is_correct:
            lines.append(
                f"   ✅ To‘g‘ri javob:"
            )
            lines.append(
                f"   <b>{correct_option})</b> {correct_text}"
            )

        if question.explanation:
            lines.append(
                f"   💡 <b>Izoh:</b> {question.explanation}"
            )

        lines.append("")
'''

if old not in text:
    raise SystemExit("KERAKLI BLOK TOPILMADI — FAYL O‘ZGARTIRILMADI")

path.write_text(text.replace(old, new, 1), encoding="utf-8")
print("FULL ANSWERS UPDATED")

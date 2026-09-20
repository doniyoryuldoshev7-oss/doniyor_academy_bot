from pathlib import Path

p = Path(r"C:\doniyor_academy_bot\app\states.py")
text = p.read_text(encoding="utf-8")

if "edit_topic_name = State()" not in text:
    text = text.replace(
        "    edit_question_text = State()",
        "    edit_topic_name = State()\n\n    edit_question_text = State()"
    )

p.write_text(text, encoding="utf-8")

print("OK — edit_topic_name STATE ADDED")

from pathlib import Path

p = Path(r"C:\doniyor_academy_bot\app\states.py")
text = p.read_text(encoding="utf-8")

states = [
    "    edit_question_text = State()",
    "    edit_question_a = State()",
    "    edit_question_b = State()",
    "    edit_question_c = State()",
    "    edit_question_d = State()",
]

for line in states:
    if line not in text:
        text += "\n" + line

p.write_text(text, encoding="utf-8")
print("STATES FIXED")

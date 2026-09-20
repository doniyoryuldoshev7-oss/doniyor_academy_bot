from pathlib import Path
import ftfy

p = Path("app/handlers/admin.py")

text = p.read_text(encoding="utf-8")

for _ in range(3):
    fixed = ftfy.fix_text(text)
    if fixed == text:
        break
    text = fixed

p.write_text(text, encoding="utf-8", newline="\n")

print("ADMIN.PY UTF-8 TOZALANDI")

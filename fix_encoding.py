from pathlib import Path

path = Path("app/handlers/admin.py")

text = path.read_text(encoding="utf-8")

for _ in range(3):
    try:
        fixed = text.encode("cp1251").decode("utf-8")
    except UnicodeEncodeError:
        fixed = text
        break

    if fixed == text:
        break

    text = fixed

path.write_text(text, encoding="utf-8")

print("Encoding fix tugadi.")
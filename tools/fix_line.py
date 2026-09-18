from pathlib import Path

p = Path("app/handlers/admin.py")
lines = p.read_text(encoding="utf-8").splitlines()

lines[1415] = '        f"📢 <b>E\'lon tarqatish yakunlandi!</b>\\n\\n"'

p.write_text("\n".join(lines) + "\n", encoding="utf-8")
print("1416-qator tuzatildi.")

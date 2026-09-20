from pathlib import Path

p = Path("app/handlers/admin.py")
lines = p.read_text(encoding="utf-8").splitlines()

lines[156:161] = [
    '        await c.message.edit_text(',
    '            "🏠 <b>Doniyor Academy</b>\\n\\nBosh menyu:",',
    '            reply_markup=user_menu,',
    '            parse_mode="HTML",',
    '        )',
]

p.write_text("\n".join(lines) + "\n", encoding="utf-8")

print("157-161-qatorlar tuzatildi.")

from pathlib import Path

p = Path("app/handlers/admin.py")
text = p.read_text(encoding="utf-8")

DQ = chr(34)

fixes = [
    # Emoji mojibake
    ("рџ" + DQ + "Ґ", "📥"),
    ("рџ" + DQ + "љ", "📚"),
    ("рџ" + DQ + "Љ", "📊"),
    ("рџ" + DQ + "ў", "📢"),
    ("рџ" + DQ + "–", "📖"),
    ("рџ" + DQ + "‹", "📋"),
    ("рџЋ‰", "🎉"),
    ("рџЏ†", "🏆"),
    ("рџ" + chr(39) + "Ў", "💡"),
    ("рџ" + chr(39) + "Ґ", "👥"),

    ("вљ " + "пёЏ", "⚠️"),
    ("вљ пёЏ", "⚠️"),
    ("вњЏпёЏ", "✏️"),
    ("вњ…", "✅"),
    ("вќ—", "❌"),
    ("в¬…пёЏ", "⬅️"),
    ("вљ™пёЏ", "⚙️"),

    ("1пёЏвѓЈ", "1️⃣"),
    ("2пёЏвѓЈ", "2️⃣"),
    ("3пёЏвѓЈ", "3️⃣"),
    ("4пёЏвѓЈ", "4️⃣"),
    ("5пёЏвѓЈ", "5️⃣"),

    ("в" + DQ + "Ѓ", "─"),

    # Bekor qilish
    ("РІСњРЉ Bekor qilish", "❌ Bekor qilish"),

    # Admin panel
    ("СЂСџРЏВ Р'В  Admin panel", "⚙️ Admin panel"),

    # E'lon status
    ("РІРЏС– E'lon barcha o'quvchilarga yuborilmoqda...", "📢 E'lon barcha o'quvchilarga yuborilmoqda..."),
    ("СЂСџРЏВ Р С" + DQ + " <b>E'lon tarqatish yakunlandi!</b>", "📢 <b>E'lon tarqatish yakunlandi!</b>"),
    ("РІСњРЉ Yetkazilmadi:", "❌ Yetkazilmadi:"),

    # Question separator / corrupted line
    ('f"вќ" <b>{q.text}</b>\\n\\n"', 'f"❓ <b>{q.text}</b>\\n\\n"'),
]

count = 0

for bad, good in fixes:
    n = text.count(bad)
    if n:
        text = text.replace(bad, good)
        print("Tuzatildi:", repr(bad), "=>", repr(good), "|", n)
        count += n

# cancel() ichidagi noto'g'ri indentationni tuzatish
old = '''    # 4. Xabarni yangilaymiz, endi "Admin panel" tugmasi joyida bo'ladi!
        await c.message.edit_text(
            "🏠 <b>Doniyor Academy</b>\\n\\nBosh menyu:",
            reply_markup=user_menu,
            parse_mode="HTML",
        )
    await c.answer()
'''

new = '''    # 4. Xabarni yangilaymiz, endi "Admin panel" tugmasi joyida bo'ladi!
    await c.message.edit_text(
        "🏠 <b>Doniyor Academy</b>\\n\\nBosh menyu:",
        reply_markup=user_menu,
        parse_mode="HTML",
    )
    await c.answer()
'''

if old in text:
    text = text.replace(old, new)
    print("cancel() indentation tuzatildi.")

p.write_text(text, encoding="utf-8")

print()
print("========================================")
print("admin.py avtomatik tozalandi.")
print("Jami almashtirishlar:", count)
print("Encoding: UTF-8")
print("========================================")

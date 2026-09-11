from pathlib import Path

p = Path("app/handlers/admin.py")
text = p.read_text(encoding="utf-8")

dq = chr(34)

fixes = [
    ("рџ" + dq + "Ґ", chr(0x1F4E5)),
    ("рџ" + dq + "љ", chr(0x1F4DA)),
    ("рџ" + dq + "Љ", chr(0x1F4CA)),
    ("рџ" + dq + "ў", chr(0x1F4E2)),
    ("рџ" + dq + "–", chr(0x1F4D6)),
    ("рџ" + dq + "‹", chr(0x1F4CB)),
    ("рџЋ‰", chr(0x1F389)),
    ("рџЏ†", chr(0x1F3C6)),
    ("рџ" + chr(39) + "Ў", chr(0x1F4A1)),
    ("рџ" + chr(39) + "Ґ", chr(0x1F465)),

    ("вљ " + "пёЏ", chr(0x26A0) + chr(0xFE0F)),
    ("вњЏпёЏ", chr(0x270F) + chr(0xFE0F)),
    ("вњ…", chr(0x2705)),
    ("вќ—", chr(0x274C)),
    ("в¬…пёЏ", chr(0x2B05) + chr(0xFE0F)),
    ("вљ™пёЏ", chr(0x2699) + chr(0xFE0F)),

    ("1пёЏвѓЈ", chr(0x31) + chr(0xFE0F) + chr(0x20E3)),
    ("2пёЏвѓЈ", chr(0x32) + chr(0xFE0F) + chr(0x20E3)),
    ("3пёЏвѓЈ", chr(0x33) + chr(0xFE0F) + chr(0x20E3)),
    ("4пёЏвѓЈ", chr(0x34) + chr(0xFE0F) + chr(0x20E3)),
    ("5пёЏвѓЈ", chr(0x35) + chr(0xFE0F) + chr(0x20E3)),

    ("в" + chr(34) + "Ѓ", "─"),
]

count = 0

for bad, good in fixes:
    n = text.count(bad)
    if n:
        text = text.replace(bad, good)
        count += n
        print("Tuzatildi:", repr(bad), "->", repr(good), "soni:", n)

p.write_text(text, encoding="utf-8")

print()
print("Jami almashtirish:", count)
print("admin.py UTF-8 sifatida saqlandi.")

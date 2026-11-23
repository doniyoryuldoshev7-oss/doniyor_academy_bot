"""Smart Takrorlashdagi buzilgan emojilarni tuzatadi.

Ishlatish (loyiha ildizida):
    python patch_smart_review_emoji.py

Ikki joyda emoji savol belgisiga aylanib qolgan edi: savol raqami
oldida va "To'g'ri javob!" yozuvida. Uchinchi joyda esa bitta
xabarda ikkita bir xil belgi chiqmasligi uchun almashtiriladi.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

BS = chr(92)

ROOT = Path(__file__).resolve().parent
TARGET = ROOT / "app/handlers/smart_review.py"

changed = []
skipped = []
failed = []

REPLACEMENTS = [
    (
        'f"? <b>Savol {index + 1}/{total}</b>' + BS + 'n' + BS + 'n"',
        'f"' + BS + 'u2753 <b>Savol {index + 1}/{total}</b>'
        + BS + 'n' + BS + 'n"',
        "savol raqami oldidagi belgi",
    ),
    (
        'feedback = "? <b>To' + chr(39) + 'g' + chr(39) + 'ri javob!</b>"',
        'feedback = "' + BS + 'u2705 <b>To' + chr(39) + 'g' + chr(39)
        + 'ri javob!</b>"',
        "To" + chr(39) + "g" + chr(39) + "ri javob belgisi",
    ),
    (
        'feedback += "' + BS + 'n' + BS + 'u2705 Savol mustahkamlandi!"',
        'feedback += "' + BS + 'n' + BS + 'U0001F3C6 Savol mustahkamlandi!"',
        "Savol mustahkamlandi belgisi",
    ),
]


if not TARGET.is_file():
    print("XATO: app/handlers/smart_review.py topilmadi.")
    print("Bu faylni loyiha ildiziga qo" + chr(39) + "ying (app papkasi yonida).")
    sys.exit(1)

text = TARGET.read_text(encoding="utf-8")
original = text

for old, new, label in REPLACEMENTS:
    if new in text:
        skipped.append(label + " (allaqachon tuzatilgan)")
        continue

    count = text.count(old)

    if count != 1:
        failed.append(label + ": " + str(count) + " marta topildi")
        continue

    text = text.replace(old, new, 1)
    changed.append(label)

if text != original:
    backup = TARGET.with_suffix(".py.bak_before_emoji_fix2")

    if not backup.exists():
        shutil.copy2(TARGET, backup)

    TARGET.write_text(text, encoding="utf-8")


print("=" * 56)
print("  SMART TAKRORLASH - BELGILARNI TUZATISH")
print("=" * 56)

if changed:
    print()
    print("Tuzatildi:")
    for item in changed:
        print("   +", item)

if skipped:
    print()
    print("O" + chr(39) + "tkazib yuborildi:")
    for item in skipped:
        print("   =", item)

if failed:
    print()
    print("XATO:")
    for item in failed:
        print("   !", item)

print()
print("-" * 56)

if failed:
    print("To" + chr(39) + "liq tuzatilmadi.")
    sys.exit(1)

print("Tayyor.")

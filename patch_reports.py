"""O'quvchilar natijasi bo'limini loyihaga ulaydi.

Ishlatish (loyiha ildizida):
    python patch_reports.py

Oldin fayllarni joyiga ko'chiring:
    student_report.py   -> app/services/student_report.py
    student_reports.py  -> app/handlers/student_reports.py
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

changed = []
skipped = []
failed = []


def backup(path):
    target = path.with_suffix(path.suffix + ".bak_before_reports")

    if not target.exists():
        shutil.copy2(path, target)


def patch(relative, old, new, label, marker=None):
    path = ROOT / relative

    if not path.is_file():
        failed.append(label + ": " + relative + " topilmadi")
        return

    text = path.read_text(encoding="utf-8")

    if (marker or new) in text:
        skipped.append(label + " (allaqachon bor)")
        return

    count = text.count(old)

    if count != 1:
        failed.append(label + ": anchor " + str(count) + " marta topildi")
        return

    backup(path)
    path.write_text(text.replace(old, new, 1), encoding="utf-8")
    changed.append(label)


required = [
    "app/services/student_report.py",
    "app/handlers/student_reports.py",
]

missing = [name for name in required if not (ROOT / name).is_file()]

if missing:
    print("XATO: yangi fayllar joyida emas:")
    for name in missing:
        print("   -", name)
    sys.exit(1)


REMINDER_BUTTON = """            [
                InlineKeyboardButton(
                    text="\U0001F514 Eslatmalar", callback_data="adm:reminders"
                )
            ],"""

REPORTS_BUTTON = """            [
                InlineKeyboardButton(
                    text="\U0001F4CA O'quvchilar natijasi",
                    callback_data="adm:reports",
                )
            ],"""

USERS_BUTTON = """            [
                InlineKeyboardButton(
                    text="\U0001f465 Foydalanuvchilar",
                    callback_data="adm:users",
                )
            ],"""

target = ROOT / "app/handlers/admin.py"
admin_text = target.read_text(encoding="utf-8") if target.is_file() else ""

# Eslatmalar tugmasi bor bo'lsa — uning yoniga, bo'lmasa
# Foydalanuvchilar tugmasi yoniga qo'yamiz.
if REMINDER_BUTTON in admin_text:
    anchor = REMINDER_BUTTON
elif USERS_BUTTON in admin_text:
    anchor = USERS_BUTTON
else:
    anchor = None

if anchor is None:
    failed.append(
        "admin.py: tugma qo'yish uchun mos joy topilmadi"
    )
else:
    patch(
        "app/handlers/admin.py",
        anchor,
        anchor + "\n" + REPORTS_BUTTON,
        "admin.py: O'quvchilar natijasi tugmasi",
        marker='callback_data="adm:reports"',
    )


patch(
    "app/__main__.py",
    "    dp.include_router(admin.router)",
    "    from .handlers import student_reports\n\n"
    "    dp.include_router(student_reports.router)\n"
    "    dp.include_router(admin.router)",
    "__main__.py: student_reports routeri",
    marker="dp.include_router(student_reports.router)",
)


print("=" * 56)
print("  O'QUVCHILAR NATIJASI")
print("=" * 56)

if changed:
    print()
    print("Qo" + chr(39) + "shildi:")
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
    print("Patch qo" + chr(39) + "llanmadi.")
    sys.exit(1)

print("Tayyor.")

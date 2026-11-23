"""Admin panelga "Eslatmalar" tugmasini qo'shadi.

Ishlatish (loyiha ildizida):
    python patch_reminder_button.py

Shart: oldin patch_word_export.py qo'llangan bo'lishi kerak
("Word eksport" tugmasi mavjud bo'lsin).
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

changed: list[str] = []
skipped: list[str] = []
failed: list[str] = []


def backup(path: Path) -> None:
    target = path.with_suffix(path.suffix + ".bak_before_rem_button")

    if not target.exists():
        shutil.copy2(path, target)


def patch(relative, old, new, label, *, marker=None) -> None:
    path = ROOT / relative

    if not path.is_file():
        failed.append(f"{label}: {relative} topilmadi")
        return

    text = path.read_text(encoding="utf-8")

    if (marker or new) in text:
        skipped.append(f"{label} (allaqachon bor)")
        return

    count = text.count(old)

    if count != 1:
        failed.append(
            f"{label}: anchor {count} marta topildi (1 bo'lishi kerak)"
        )
        return

    backup(path)
    path.write_text(text.replace(old, new, 1), encoding="utf-8")
    changed.append(label)


def append_line(relative, line, label) -> None:
    path = ROOT / relative

    if not path.is_file():
        failed.append(f"{label}: {relative} topilmadi")
        return

    text = path.read_text(encoding="utf-8")

    if line in text.split("\n"):
        skipped.append(f"{label} (allaqachon bor)")
        return

    backup(path)

    if text and not text.endswith("\n"):
        text += "\n"

    path.write_text(text + line + "\n", encoding="utf-8")
    changed.append(label)


# ---------------------------------------------------------------
#  1) Admin panelga tugma
# ---------------------------------------------------------------

patch(
    "app/handlers/admin.py",
    """            [
                InlineKeyboardButton(
                    text="📄 Word eksport", callback_data="adm:export"
                )
            ],""",
    """            [
                InlineKeyboardButton(
                    text="📄 Word eksport", callback_data="adm:export"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔔 Eslatmalar", callback_data="adm:reminders"
                )
            ],""",
    "admin.py: Eslatmalar tugmasi",
    marker='callback_data="adm:reminders"',
)


# ---------------------------------------------------------------
#  2) Yordamchi fayllarni gitdan chiqarish
# ---------------------------------------------------------------

for line in ["*.bat", "deploy_log.txt", "natija.txt"]:
    append_line(".gitignore", line, f".gitignore: {line}")


# ---------------------------------------------------------------
#  Natija
# ---------------------------------------------------------------

print("=" * 56)
print("  ESLATMALAR TUGMASI PATCH")
print("=" * 56)

if changed:
    print("\nQo'shildi:")
    for item in changed:
        print("   +", item)

if skipped:
    print("\nO'tkazib yuborildi:")
    for item in skipped:
        print("   =", item)

if failed:
    print("\nXATO:")
    for item in failed:
        print("   !", item)

print("\n" + "-" * 56)

if failed:
    print("Patch qo'llanmadi.")
    sys.exit(1)

print("Patch muvaffaqiyatli qo'llandi.")

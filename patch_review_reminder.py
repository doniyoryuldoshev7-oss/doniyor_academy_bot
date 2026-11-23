"""Takrorlash eslatmalarini loyihaga ulaydi — bir marta ishga tushiriladi.

Ishlatish (loyiha ildizida):
    python patch_review_reminder.py

Oldin fayllarni joyiga ko'chiring:
    review_reminder.py  -> app/services/review_reminder.py
    reminders.py        -> app/handlers/reminders.py

Skript o'zgartiradigan fayllar:
    .env.example, app/__main__.py

Skript idempotent. O'zgartirishdan oldin
.bak_before_reminder nusxasi yaratiladi.
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
    target = path.with_suffix(path.suffix + ".bak_before_reminder")

    if not target.exists():
        shutil.copy2(path, target)


def patch(
    relative: str,
    old: str,
    new: str,
    label: str,
    *,
    marker: str | None = None,
) -> None:
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


def append_line(relative: str, line: str, label: str) -> None:
    path = ROOT / relative

    if not path.is_file():
        failed.append(f"{label}: {relative} topilmadi")
        return

    text = path.read_text(encoding="utf-8")
    key = line.split("=")[0].strip()

    if key in text:
        skipped.append(f"{label} (allaqachon bor)")
        return

    backup(path)

    if text and not text.endswith("\n"):
        text += "\n"

    path.write_text(text + line + "\n", encoding="utf-8")
    changed.append(label)


def check_new_files() -> bool:
    required = [
        "app/services/review_reminder.py",
        "app/handlers/reminders.py",
    ]

    missing = [name for name in required if not (ROOT / name).is_file()]

    if missing:
        print("XATO: quyidagi yangi fayllar joyida emas:")
        for name in missing:
            print("   -", name)
        print("\nAvval ularni ko'chiring, keyin skriptni qayta ishlating.")
        return False

    return True


if not check_new_files():
    sys.exit(1)


# ---------------------------------------------------------------
#  1) .env.example
# ---------------------------------------------------------------

for line, label in [
    ("SMART_REVIEW_ENABLED=1", "SMART_REVIEW_ENABLED"),
    ("REVIEW_REMINDER_ENABLED=1", "REVIEW_REMINDER_ENABLED"),
    ("REVIEW_REMINDER_HOUR=9", "REVIEW_REMINDER_HOUR"),
    ("REVIEW_REMINDER_UTC_OFFSET=5", "REVIEW_REMINDER_UTC_OFFSET"),
]:
    append_line(".env.example", line, f".env.example: {label}")


# ---------------------------------------------------------------
#  2) app/__main__.py — admin buyruqlari routeri
# ---------------------------------------------------------------

patch(
    "app/__main__.py",
    "    dp.include_router(admin.router)",
    "    from .handlers import reminders\n\n"
    "    dp.include_router(reminders.router)\n"
    "    dp.include_router(admin.router)",
    "__main__.py: reminders routeri",
    marker="dp.include_router(reminders.router)",
)


# ---------------------------------------------------------------
#  3) app/__main__.py — kunlik eslatma sikli
# ---------------------------------------------------------------

patch(
    "app/__main__.py",
    "    await asyncio.Event().wait()",
    "    from .services.review_reminder import reminder_loop\n\n"
    "    reminder_task = asyncio.create_task(reminder_loop(bot))\n\n"
    "    print(\n"
    "        \">>> TAKRORLASH ESLATMALARI SIKLI ISHGA TUSHDI\",\n"
    "        flush=True,\n"
    "    )\n\n"
    "    await asyncio.Event().wait()",
    "__main__.py: eslatma sikli",
    marker="reminder_task = asyncio.create_task",
)


# ---------------------------------------------------------------
#  Natija
# ---------------------------------------------------------------

print("=" * 56)
print("  TAKRORLASH ESLATMALARI PATCH")
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
    print("Patch to'liq qo'llanmadi. Xatolarni tekshiring.")
    sys.exit(1)

print("Patch muvaffaqiyatli qo'llandi.\n")
print("Railway Variables ga qo'shing:")
print("  SMART_REVIEW_ENABLED = 1")
print("  REVIEW_REMINDER_HOUR = 9")
print("\nDeploydan keyin tekshirish:")
print("  /eslatma_holat  -- kimga eslatma ketishini ko'rsatadi")
print("  /eslatma_yubor  -- darhol yuboradi")

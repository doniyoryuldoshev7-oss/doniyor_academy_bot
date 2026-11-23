"""Ustoz topshiriqlari bo'limini loyihaga ulaydi.

Ishlatish (loyiha ildizida):
    python patch_assignments.py

Yangi jadval (assignments) init_db paytida o'zi yaratiladi.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

changed = []
skipped = []
failed = []

MODEL_CODE = """

# Ustoz topshiriqlari
class Assignment(Base):
    __tablename__ = "assignments"

    id: Mapped[int] = mapped_column(primary_key=True)

    topic_id: Mapped[int] = mapped_column(
        ForeignKey("topics.id", ondelete="CASCADE"),
        index=True,
    )

    created_by: Mapped[int] = mapped_column(BigInteger, index=True)

    # Kimga: aniq o'quvchi -> user_id, guruh -> group_name,
    # ikkalasi ham bo'sh bo'lsa, hammaga.
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    group_name: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )

    required_questions: Mapped[int] = mapped_column(Integer, default=10)

    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    deadline: Mapped[datetime] = mapped_column(DateTime, index=True)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
    )
"""


def backup(path):
    target = path.with_suffix(path.suffix + ".bak_before_assignments")

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


def append_block(relative, block, label, marker):
    path = ROOT / relative

    if not path.is_file():
        failed.append(label + ": " + relative + " topilmadi")
        return

    text = path.read_text(encoding="utf-8")

    if marker in text:
        skipped.append(label + " (allaqachon bor)")
        return

    backup(path)

    if not text.endswith("\n"):
        text += "\n"

    path.write_text(text + block, encoding="utf-8")
    changed.append(label)


required = [
    "app/services/assignments.py",
    "app/handlers/assignments.py",
]

missing = [name for name in required if not (ROOT / name).is_file()]

if missing:
    print("XATO: yangi fayllar joyida emas:")
    for name in missing:
        print("   -", name)
    sys.exit(1)


# 1) Jadval
append_block(
    "app/models.py",
    MODEL_CODE,
    "models.py: assignments jadvali",
    '__tablename__ = "assignments"',
)


# 2) Admin panelga tugma
QSTAT_BUTTON = """            [
                InlineKeyboardButton(
                    text="\U0001F52C Savollar sifati",
                    callback_data="adm:qstat",
                )
            ],"""

REPORTS_BUTTON = """            [
                InlineKeyboardButton(
                    text="\U0001F4CA O'quvchilar natijasi",
                    callback_data="adm:reports",
                )
            ],"""

ASSIGN_BUTTON = """            [
                InlineKeyboardButton(
                    text="\U0001F4CB Ustoz topshirig'i",
                    callback_data="adm:assign",
                )
            ],"""

admin_path = ROOT / "app/handlers/admin.py"
admin_text = admin_path.read_text(encoding="utf-8") if admin_path.is_file() else ""

if QSTAT_BUTTON in admin_text:
    anchor = QSTAT_BUTTON
elif REPORTS_BUTTON in admin_text:
    anchor = REPORTS_BUTTON
else:
    anchor = None

if anchor is None:
    failed.append("admin.py: tugma uchun mos joy topilmadi")
else:
    patch(
        "app/handlers/admin.py",
        anchor,
        anchor + "\n" + ASSIGN_BUTTON,
        "admin.py: Ustoz topshirig'i tugmasi",
        marker='callback_data="adm:assign"',
    )


# 3) Bosh menyuga o'quvchi tugmasi
BS = chr(92)

# keyboards.py da belgilar escape ko'rinishida yozilgan,
# shuning uchun anchor ham xuddi shunday tuzilishi kerak.
MAP_BUTTON = (
    '          [InlineKeyboardButton(text="' + BS + 'U0001F4C8 '
    'Bilim xaritasi", callback_data="km:menu")]'
)

SMART_BUTTON = (
    '          [InlineKeyboardButton(text="' + BS + 'U0001F9E0 '
    'Smart Takrorlash", callback_data="smart_review")]'
)

TASK_BUTTON = (
    '          [InlineKeyboardButton(text="' + BS + 'U0001F4CB '
    'Topshiriqlar", callback_data="asg:my")]'
)

keyboards_path = ROOT / "app/keyboards.py"
keyboards_text = (
    keyboards_path.read_text(encoding="utf-8")
    if keyboards_path.is_file()
    else ""
)

if MAP_BUTTON in keyboards_text:
    menu_anchor = MAP_BUTTON
elif SMART_BUTTON in keyboards_text:
    menu_anchor = SMART_BUTTON
else:
    menu_anchor = None

if menu_anchor is None:
    failed.append(
        "keyboards.py: bosh menyuda mos joy topilmadi"
    )
else:
    patch(
        "app/keyboards.py",
        menu_anchor,
        menu_anchor + ",\n" + TASK_BUTTON,
        "keyboards.py: Topshiriqlar tugmasi",
        marker='callback_data="asg:my"',
    )


# 4) Router
patch(
    "app/__main__.py",
    "    dp.include_router(admin.router)",
    "    from .handlers import assignments\n\n"
    "    dp.include_router(assignments.router)\n"
    "    dp.include_router(admin.router)",
    "__main__.py: assignments routeri",
    marker="dp.include_router(assignments.router)",
)


print("=" * 56)
print("  USTOZ TOPSHIRIG'I")
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

"""Diagnostikani loyihaga ulaydi va test yozuviga "manba" ustunini qo'shadi.

Ishlatish (loyiha ildizida):
    python patch_diagnostic.py

Fayllar joyida bo'lishi kerak:
    diagnostic.py          -> app/services/diagnostic.py
    diagnostic_handler.py  -> app/handlers/diagnostic.py
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

BS = chr(92)

ROOT = Path(__file__).resolve().parent

changed = []
skipped = []
failed = []


def backup(path):
    target = path.with_suffix(path.suffix + ".bak_before_diagnostic")

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
    "app/services/diagnostic.py",
    "app/handlers/diagnostic.py",
]

missing = [name for name in required if not (ROOT / name).is_file()]

if missing:
    print("XATO: yangi fayllar joyida emas:")
    for name in missing:
        print("   -", name)
    sys.exit(1)


# 1) models.py — test yozuviga manba ustuni
patch(
    "app/models.py",
    "    score: Mapped[int]=mapped_column(Integer)\n"
    "    created_at: Mapped[datetime]=mapped_column("
    "DateTime,default=datetime.utcnow)\n"
    "\n"
    "class AnswerLog(Base):",
    "    score: Mapped[int]=mapped_column(Integer)\n"
    "    source: Mapped[str]=mapped_column(String(20),default=\"quiz\")\n"
    "    created_at: Mapped[datetime]=mapped_column("
    "DateTime,default=datetime.utcnow)\n"
    "\n"
    "class AnswerLog(Base):",
    "models.py: test yozuviga manba ustuni",
    marker='source: Mapped[str]=mapped_column(String(20)',
)


# 2) db.py — mavjud bazaga ustunni qo'shish
patch(
    "app/db.py",
    "        await conn.run_sync(Base.metadata.create_all)\n",
    "        await conn.run_sync(Base.metadata.create_all)\n"
    "\n"
    "        # test_attempts: yozuv manbasi (oddiy test / diagnostika)\n"
    "        if database_url.startswith(\"postgresql+asyncpg://\"):\n"
    "            await conn.execute(text(\n"
    "                \"ALTER TABLE test_attempts ADD COLUMN IF NOT EXISTS \"\n"
    "                \"source VARCHAR(20) NOT NULL DEFAULT 'quiz'\"\n"
    "            ))\n"
    "        else:\n"
    "            _src_cols = await conn.execute(\n"
    "                text(\"PRAGMA table_info(test_attempts)\")\n"
    "            )\n"
    "            if \"source\" not in {r[1] for r in _src_cols.fetchall()}:\n"
    "                await conn.execute(text(\n"
    "                    \"ALTER TABLE test_attempts ADD COLUMN \"\n"
    "                    \"source VARCHAR(20) NOT NULL DEFAULT 'quiz'\"\n"
    "                ))\n",
    "db.py: manba ustuni migratsiyasi",
    marker="ALTER TABLE test_attempts ADD COLUMN",
)


# 3) Bosh menyuga tugma
TASK_BUTTON = (
    '          [InlineKeyboardButton(text="' + BS + 'U0001F4CB '
    'Topshiriqlar", callback_data="asg:my")]'
)

MAP_BUTTON = (
    '          [InlineKeyboardButton(text="' + BS + 'U0001F4C8 '
    'Bilim xaritasi", callback_data="km:menu")]'
)

SMART_BUTTON = (
    '          [InlineKeyboardButton(text="' + BS + 'U0001F9E0 '
    'Smart Takrorlash", callback_data="smart_review")]'
)

DIAG_BUTTON = (
    '          [InlineKeyboardButton(text="' + BS + 'U0001F3AF '
    'Diagnostika", callback_data="diag:menu")]'
)

keyboards_path = ROOT / "app/keyboards.py"
keyboards_text = (
    keyboards_path.read_text(encoding="utf-8")
    if keyboards_path.is_file()
    else ""
)

anchor = None

for candidate in (TASK_BUTTON, MAP_BUTTON, SMART_BUTTON):
    if candidate in keyboards_text:
        anchor = candidate
        break

if anchor is None:
    failed.append("keyboards.py: bosh menyuda mos joy topilmadi")
else:
    patch(
        "app/keyboards.py",
        anchor,
        anchor + ",\n" + DIAG_BUTTON,
        "keyboards.py: Diagnostika tugmasi",
        marker='callback_data="diag:menu"',
    )


# 4) Router
patch(
    "app/__main__.py",
    "    dp.include_router(admin.router)",
    "    from .handlers import diagnostic\n\n"
    "    dp.include_router(diagnostic.router)\n"
    "    dp.include_router(admin.router)",
    "__main__.py: diagnostic routeri",
    marker="dp.include_router(diagnostic.router)",
)


# 5) admin.py: rasmli savolni tahrirlashda FSInputFile import qilinmagan edi
admin_text = (ROOT / "app/handlers/admin.py").read_text(encoding="utf-8")

if "FSInputFile(" in admin_text and "FSInputFile" not in admin_text.split("FSInputFile(")[0]:
    patch(
        "app/handlers/admin.py",
        "from aiogram.fsm.context import FSMContext\n",
        "from aiogram.fsm.context import FSMContext\n"
        "from aiogram.types import FSInputFile\n",
        "admin.py: FSInputFile importi",
        marker="from aiogram.types import FSInputFile",
    )


print("=" * 56)
print("  DIAGNOSTIKA")
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

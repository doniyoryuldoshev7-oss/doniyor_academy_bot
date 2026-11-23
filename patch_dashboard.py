"""Smart Takrorlash boshqaruv panelidagi hisobni tuzatadi.

Ishlatish (loyiha ildizida):
    python patch_dashboard.py

Muammo: panel "Bugun takrorlanadigan: 2 ta" deb yozardi, lekin
boshlaganda "savol yo'q" chiqardi. Chunki hisob SmartReview
yozuvlarini sanardi, savolning o'zi ko'rsatishga yaroqlimi-yo'qmi
tekshirmasdan. Masalan ochiq (yozma) savollar hisobga kirardi,
takrorlash esa faqat A/B/C/D bilan ishlaydi.

Endi hisob takrorlash so'rovi bilan bitta shartdan foydalanadi.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TARGET = ROOT / "app/handlers/student.py"

changed = []
skipped = []
failed = []

IMPORT_OLD = """    from sqlalchemy import func
    from ..models import SmartReview
"""

IMPORT_NEW = """    from sqlalchemy import func
    from ..models import Question, SmartReview
    from ..services.smart_review_queue import reviewable_condition
"""

DUE_OLD = """        due = await s.scalar(
            select(func.count(SmartReview.id)).where(
                SmartReview.user_id == user.id,
                SmartReview.status == "active",
                SmartReview.next_review_at <= now,
            )
        )"""

DUE_NEW = """        due = await s.scalar(
            select(func.count(SmartReview.id))
            .join(Question, Question.id == SmartReview.question_id)
            .where(
                SmartReview.user_id == user.id,
                SmartReview.status == "active",
                SmartReview.next_review_at <= now,
                reviewable_condition(),
            )
        )"""

UPCOMING_OLD = """        upcoming = await s.scalar(
            select(func.count(SmartReview.id)).where(
                SmartReview.user_id == user.id,
                SmartReview.status == "active",
                SmartReview.next_review_at > now,
            )
        )"""

UPCOMING_NEW = """        upcoming = await s.scalar(
            select(func.count(SmartReview.id))
            .join(Question, Question.id == SmartReview.question_id)
            .where(
                SmartReview.user_id == user.id,
                SmartReview.status == "active",
                SmartReview.next_review_at > now,
                reviewable_condition(),
            )
        )"""

REPLACEMENTS = [
    (IMPORT_OLD, IMPORT_NEW, "reviewable_condition importi"),
    (DUE_OLD, DUE_NEW, "bugungi savollar hisobi"),
    (UPCOMING_OLD, UPCOMING_NEW, "keyingi savollar hisobi"),
]


if not TARGET.is_file():
    print("XATO: app/handlers/student.py topilmadi.")
    print("Bu faylni loyiha ildiziga qo" + chr(39) + "ying.")
    sys.exit(1)

text = TARGET.read_text(encoding="utf-8")
original = text

for old, new, label in REPLACEMENTS:
    if new in text:
        skipped.append(label + " (allaqachon bor)")
        continue

    count = text.count(old)

    if count != 1:
        failed.append(label + ": anchor " + str(count) + " marta topildi")
        continue

    text = text.replace(old, new, 1)
    changed.append(label)

if text != original:
    backup = TARGET.with_suffix(".py.bak_before_dashboard_fix")

    if not backup.exists():
        shutil.copy2(TARGET, backup)

    TARGET.write_text(text, encoding="utf-8")


print("=" * 56)
print("  BOSHQARUV PANELI HISOBI")
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

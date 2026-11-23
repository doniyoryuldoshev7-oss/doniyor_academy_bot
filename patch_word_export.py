"""Word eksport tizimini loyihaga ulaydi — bir marta ishga tushiriladi.

Ishlatish (loyiha ildizida):
    python patch_word_export.py

Oldin quyidagi fayllarni joyiga ko'chiring:
    word_export.py   -> app/services/word_export.py
    test_archive.py  -> app/services/test_archive.py
    export.py        -> app/handlers/export.py

Skript o'zgartiradigan fayllar:
    requirements.txt, .env.example, app/config.py,
    app/__main__.py, app/handlers/admin.py

Skript idempotent: ikkinchi marta ishga tushsa, hech narsani
takrorlamaydi. O'zgartirishdan oldin .bak_before_word_export
nusxasi yaratiladi.
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
    target = path.with_suffix(path.suffix + ".bak_before_word_export")

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

    if line.split(">=")[0].split("=")[0].strip() in text:
        skipped.append(f"{label} (allaqachon bor)")
        return

    backup(path)

    if text and not text.endswith("\n"):
        text += "\n"

    path.write_text(text + line + "\n", encoding="utf-8")
    changed.append(label)


def check_new_files() -> bool:
    required = [
        "app/services/word_export.py",
        "app/services/test_archive.py",
        "app/handlers/export.py",
    ]

    missing = [name for name in required if not (ROOT / name).is_file()]

    if missing:
        print("XATO: quyidagi yangi fayllar joyida emas:")
        for name in missing:
            print("   -", name)
        print("\nAvval ularni ko'chirib, keyin skriptni qayta ishga tushiring.")
        return False

    return True


if not check_new_files():
    sys.exit(1)


# ---------------------------------------------------------------
#  1) requirements.txt
# ---------------------------------------------------------------

append_line(
    "requirements.txt",
    "python-docx>=1.1,<2",
    "requirements.txt: python-docx",
)

# ---------------------------------------------------------------
#  2) .env.example
# ---------------------------------------------------------------

append_line(
    ".env.example",
    "ARCHIVE_CHAT_ID=",
    ".env.example: ARCHIVE_CHAT_ID",
)

# ---------------------------------------------------------------
#  3) app/config.py — arxiv kanali sozlamasi
# ---------------------------------------------------------------

patch(
    "app/config.py",
    '    web_app_url: str = ""',
    '    web_app_url: str = ""\n'
    '    archive_chat_id: str = ""',
    "config.py: archive_chat_id",
    marker="archive_chat_id",
)

# ---------------------------------------------------------------
#  4) app/__main__.py — export routerni ulash
# ---------------------------------------------------------------

patch(
    "app/__main__.py",
    "from .handlers import start, student, admin, group_quiz, smart_review",
    "from .handlers import ("
    "\n    start,"
    "\n    student,"
    "\n    admin,"
    "\n    group_quiz,"
    "\n    smart_review,"
    "\n    export,"
    "\n)",
    "__main__.py: export importi",
    marker="    export,",
)

patch(
    "app/__main__.py",
    "    dp.include_router(admin.router)",
    "    dp.include_router(export.router)\n"
    "    dp.include_router(admin.router)",
    "__main__.py: export routeri",
    marker="dp.include_router(export.router)",
)

# ---------------------------------------------------------------
#  5) app/handlers/admin.py — logging importi
# ---------------------------------------------------------------

patch(
    "app/handlers/admin.py",
    "import sys\nimport asyncio",
    "import sys\nimport asyncio\nimport logging",
    "admin.py: logging importi",
    marker="\nimport logging",
)

# ---------------------------------------------------------------
#  6) app/handlers/admin.py — admin panelga tugma
# ---------------------------------------------------------------

patch(
    "app/handlers/admin.py",
    """            [
                InlineKeyboardButton(
                    text="📥 Excel/CSV import", callback_data="adm:import"
                )
            ],""",
    """            [
                InlineKeyboardButton(
                    text="📥 Excel/CSV import", callback_data="adm:import"
                )
            ],
            [
                InlineKeyboardButton(
                    text="📄 Word eksport", callback_data="adm:export"
                )
            ],""",
    "admin.py: Word eksport tugmasi",
    marker='callback_data="adm:export"',
)

# ---------------------------------------------------------------
#  7) app/handlers/admin.py — bulk saqlashdan keyin avto-eksport
# ---------------------------------------------------------------

AUTO_EXPORT = '''        topic_name = topic.name

        subject_obj = await db.get(Subject, topic.subject_id)
        subject_name = subject_obj.name if subject_obj else "—"

    saved_count = len(items)

    # --- Word avto-eksport (har bir yuklangan test uchun) ---
    try:
        from ..services.word_export import (
            build_questions_docx,
            export_filename,
            items_from_bulk,
        )
        from ..services.test_archive import deliver_test_export

        export_items = items_from_bulk(items)

        docx_bytes = build_questions_docx(
            subject_name=subject_name,
            topic_name=topic_name,
            items=export_items,
        )

        await deliver_test_export(
            bot=c.bot,
            admin_chat_id=c.from_user.id,
            data=docx_bytes,
            filename=export_filename(subject_name, topic_name),
            subject_name=subject_name,
            topic_name=topic_name,
            count=saved_count,
            author=c.from_user.full_name,
            note="Yangi yuklangan test — avtomatik eksport.",
        )
    except Exception as exc:
        logging.getLogger(__name__).exception(
            "Word avto-eksport xatosi: %s", exc
        )
    # --- /Word avto-eksport ---
'''

patch(
    "app/handlers/admin.py",
    "        topic_name = topic.name\n\n    saved_count = len(items)\n",
    AUTO_EXPORT,
    "admin.py: bulk saqlashdan keyin avto-eksport",
    marker="Word avto-eksport",
)


# ---------------------------------------------------------------
#  Natija
# ---------------------------------------------------------------

print("=" * 56)
print("  WORD EKSPORT PATCH")
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
    print("Patch to'liq qo'llanmadi. Yuqoridagi xatolarni tekshiring.")
    sys.exit(1)

print("Patch muvaffaqiyatli qo'llandi.\n")
print("Keyingi qadamlar:")
print("  1. ARCHIVE_CHAT_ID ni Railway Variables ga qo'shing")
print("     (arxiv kanalining ID si, masalan -1001234567890)")
print("  2. pip install -r requirements.txt")
print("  3. git add -A && git commit -m 'Word eksport' && git push")

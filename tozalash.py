"""Loyihani tozalash: eski/ortiqcha fayllarni _arxiv_eski papkasiga ko'chiradi.

Hech narsa O'CHIRILMAYDI: fayllar _arxiv_eski/<sana>/ ichida o'z yo'li
bilan saqlanadi, GitHub tarixida ham qoladi. Bot kodi (app/ ichidagi
ishlaydigan modullar) o'zgarmaydi.

Qayta ishga tushirish xavfsiz.
"""
from __future__ import annotations

import fnmatch
import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# Windows konsoli (cp1251) ba'zi belgilarni chiqara olmaydi: yiqilmasin
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors="backslashreplace")
    except Exception:
        pass
SELF = {Path(__file__).name, "ORNAT_VA_YUBOR.bat"}

# Ildizdagi eski bir martalik skriptlar va qoldiqlar
ROOT_PATTERNS = [
    "patch_*.py", "fix_*.py", "update_*.py", "check_*.py", "test_*.py",
    "replace_*.py", "add_*.py", "make_excel.py", "db_test.py",
    "user_test.py", "_image_question_patch.py", "*_patch.py",
    "test_big_image.jpg", "requirements.txt.bak*", ".env.example.bak*",
    ".gitignore.bak*", "app_backup_*", "test_crops", ".append*", "= p.read_text*",
]

# app/ ichidagi zaxira nusxalar (ishlaydigan modullar emas)
APP_MARKERS = (
    ".bak", ".BEFORE", ".before_", "_before_", "_BEFORE_", "_BEFORE.",
    ".emoji_backup", "_backup.py", ".HISTORY_",
)

# Hech qachon tegilmaydi
PROTECT_ROOT = {"README.md", "requirements.txt", ".gitignore", ".env.example"}


def is_app_junk(name: str) -> bool:
    return any(marker in name for marker in APP_MARKERS)


def main() -> int:
    stamp = datetime.now().strftime("%Y%m%d")
    target_root = ROOT / "_arxiv_eski" / stamp
    moved = []

    def move(path: Path):
        relative = path.relative_to(ROOT)
        destination = target_root / relative

        destination.parent.mkdir(parents=True, exist_ok=True)

        if destination.exists():
            destination = destination.with_name(
                destination.name + datetime.now().strftime(".%H%M%S")
            )

        shutil.move(str(path), str(destination))
        moved.append(relative.as_posix())

    # 1) ildiz
    for item in sorted(ROOT.iterdir()):
        name = item.name

        if name in PROTECT_ROOT or name in SELF:
            continue

        if name in {"app", "tools", "_arxiv_eski", ".git"}:
            continue

        if name.startswith("pkg_") or name.endswith(".zip"):
            continue

        if any(fnmatch.fnmatch(name, pattern) for pattern in ROOT_PATTERNS):
            move(item)

    # 2) app/ ichidagi zaxira nusxalar
    app_dir = ROOT / "app"

    if app_dir.is_dir():
        for item in sorted(app_dir.rglob("*")):
            if "__pycache__" in item.parts or not item.is_file():
                continue

            if is_app_junk(item.name):
                move(item)

    # 3) .gitignore: arxiv papkasi GitHub'ga yuklanmasin
    gitignore = ROOT / ".gitignore"
    line = "_arxiv_eski/"

    if gitignore.is_file():
        with open(gitignore, "r", encoding="utf-8", newline="") as handle:
            content = handle.read()

        if line not in content.splitlines() and line not in content.replace("\r\n", "\n").split("\n"):
            newline = "\r\n" if "\r\n" in content else "\n"

            if content and not content.endswith(("\n", "\r\n")):
                content += newline

            with open(gitignore, "w", encoding="utf-8", newline="") as handle:
                handle.write(content + line + newline)

    print("=" * 56)
    print("  LOYIHANI TOZALASH")
    print("=" * 56)
    print()

    if moved:
        print(f"_arxiv_eski/{stamp}/ ga ko'chirildi: {len(moved)} ta")
        print()

        for name in moved:
            print("  ", name)
    else:
        print("Ko'chiriladigan ortiqcha fayl topilmadi.")

    print()
    print("Hech narsa o'chirilmadi. Tayyor.")
    return 0


sys.exit(main())

"""Xavfsiz tozalash: faqat zaxira nusxalarni (.bak_before_*) _arxiv_eski/<sana>/ ga ko'chiradi.

- Hech narsa O'CHIRILMAYDI, faqat ko'chiriladi.
- Bot kodi, veb-ilova (webapp.py, webui/), patch_*.py va .bat fayllarga TEGILMAYDI.
- GitHub'ga hech narsa yuborilmaydi (bu fayllar baribir .gitignore da).
"""
from __future__ import annotations

import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(errors="backslashreplace")
    except Exception:
        pass

MARKERS = (".bak_before_", ".bak")
SKIP_DIRS = {".git", "_arxiv_eski", "__pycache__", ".venv", "venv"}


def is_backup(name: str) -> bool:
    return any(m in name for m in MARKERS)


def main() -> int:
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    target = ROOT / "_arxiv_eski" / ("bak_" + stamp)
    moved = []
    for p in sorted(ROOT.rglob("*")):
        if not p.is_file() or any(part in SKIP_DIRS for part in p.relative_to(ROOT).parts):
            continue
        if not is_backup(p.name):
            continue
        rel = p.relative_to(ROOT)
        dest = target / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            dest = dest.with_name(dest.name + datetime.now().strftime(".%H%M%S%f"))
        shutil.move(str(p), str(dest))
        moved.append(rel.as_posix())
    print("=" * 50)
    print("  ZAXIRA NUSXALARNI TOZALASH")
    print("=" * 50)
    if moved:
        print(f"_arxiv_eski/bak_{stamp}/ ga ko'chirildi: {len(moved)} ta")
        for m in moved:
            print("  ", m)
    else:
        print("Ko'chiriladigan zaxira nusxa topilmadi.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Tozalash 2-bosqich: app/handlers/admin.py ni tuzatgan eski bir martalik skriptlar.
admin.py hozir papka (paket) bo'lgani uchun bu skriptlar endi ishlamaydi.
Faqat ko'chiriladi (o'chirilmaydi): _arxiv_eski/tools_<sana>/ ga. Boshqa hech narsaga tegilmaydi."""
import shutil, sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
for s in (sys.stdout, sys.stderr):
    try:
        s.reconfigure(errors="backslashreplace")
    except Exception:
        pass
FILES = ["fix_admin_157.py", "fix_admin_encoding.py", "fix_line.py", "repair_admin.py", "repair_admin_full.py"]
target = ROOT / "_arxiv_eski" / ("tools_" + datetime.now().strftime("%Y%m%d_%H%M"))
moved = []
for n in FILES:
    p = ROOT / "tools" / n
    if p.is_file():
        target.mkdir(parents=True, exist_ok=True)
        shutil.move(str(p), str(target / n))
        moved.append(n)
print("Ko'chirildi:", len(moved), "ta")
for n in moved:
    print("  tools/" + n)

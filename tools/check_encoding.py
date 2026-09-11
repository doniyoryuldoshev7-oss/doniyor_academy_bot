from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

SKIP_DIRS = {
    ".venv",
    "venv",
    "__pycache__",
    ".git",
    "BACKUP",
    "backups",
}

EXTENSIONS = {".py", ".txt", ".csv", ".md"}

# Haqiqiy mojibake uchun kuchli belgilar
MARKERS = (
    "РІР‚",
    "РІС",
    "СЂСџ",
    "вЂ",
    "рџ",
)

errors = 0

for path in ROOT.rglob("*"):
    if not path.is_file():
        continue

    if path.suffix.lower() not in EXTENSIONS:
        continue

    if any(part in SKIP_DIRS for part in path.parts):
        continue

    if path.name == "check_encoding.py":
        continue

    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        print(f"[UTF8 ERROR] {path}")
        errors += 1
        continue

    found = [marker for marker in MARKERS if marker in text]

    if found:
        print(f"[MOJIBAKE] {path}")
        print(f"  Topildi: {', '.join(found)}")
        errors += 1

if errors == 0:
    print("OK: Encoding muammosi topilmadi.")
else:
    print()
    print(f"DIQQAT: {errors} ta faylda muammo topildi.")

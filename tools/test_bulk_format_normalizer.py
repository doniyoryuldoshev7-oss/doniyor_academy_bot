"""Standalone, read-only audit of Doniyor Academy bulk format normalizer."""
from pathlib import Path
import ast
import importlib.util
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
admin_path = ROOT / "app" / "handlers" / "admin.py"
normalizer_path = ROOT / "app" / "bulk_format_normalizer.py"

if not admin_path.is_file() or not normalizer_path.is_file():
    raise SystemExit("Missing admin.py or bulk_format_normalizer.py")

spec = importlib.util.spec_from_file_location("bulk_format_normalizer", normalizer_path)
normalizer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(normalizer)

source = admin_path.read_text(encoding="utf-8-sig")
tree = ast.parse(source)
wanted = {
    "_BULK_QUESTION_RE",
    "_BULK_OPTION_RE",
    "_BULK_BRACKET_OPTION_RE",
    "_bulk_extract_correct_marker",
    "_bulk_normalize_text",
    "_bulk_parse_questions",
}
nodes = []
for node in tree.body:
    if isinstance(node, ast.Assign):
        if any(isinstance(target, ast.Name) and target.id in wanted for target in node.targets):
            nodes.append(node)
    elif isinstance(node, ast.FunctionDef) and node.name in wanted:
        nodes.append(node)

found = {
    name
    for node in nodes
    for name in (
        ([node.name] if isinstance(node, ast.FunctionDef) else
         [target.id for target in node.targets if isinstance(target, ast.Name)])
    )
}
missing = wanted - found
if missing:
    raise SystemExit("Legacy parser changed; missing: " + ", ".join(sorted(missing)))

namespace = {"re": re}
exec(compile(ast.Module(body=nodes, type_ignores=[]), "<legacy-parser>", "exec"), namespace)
legacy = namespace["_bulk_parse_questions"]

TESTS = [('simple numbered', '1. Savol?\nA) Bir\nB) Ikki ++\nC) Uch\nD) Tort', 1, 'B', True, ''), ('inner numbers', '28. Tarixchilarni moslang.\nI. Arrian; II. Gerodot.\n1) Tarix;\n2) Harbiy yurishlar.\nA) I-1\nB) I-2 ++\nC) I-3\nD) I-4', 1, 'B', True, '1) Tarix;'), ('lower inner', '29. Moslikni toping.\na) Misr;\nb) Rim;\nc) Yunoniston.\nA) 1-a\nB) 2-b ++\nC) 3-c\nD) 4-d', 1, 'B', True, 'a) Misr;'), ('lower inner 4', '29. Moslikni toping.\na) Misr;\nb) Rim;\nc) Yunoniston;\nd) Arabiston.\nA) 1-a\nB) 2-b ++\nC) 3-c\nD) 4-d', 1, 'B', True, 'd) Arabiston.'), ('inline full', '30. Natijani toping.\nA) 10 B) 20 ++ C) 30 D) 40', 1, 'B', True, ''), ('inline pair', '35. Natijani toping.\nA) 10 B) 20 ++\nC) 30 D) 40', 1, 'B', True, ''), ('lower options', '31. Natijani toping.\na) 10\nb) 20 ++\nc) 30\nd) 40', 1, 'B', True, ''), ('two numbered', '1. Birinchi?\nA) Bir\nB) Ikki ++\nC) Uch\nD) Tort\n2) Ikkinchi?\nA) Bir\nB) Ikki\nC) Uch ++\nD) Tort', 2, 'B', True, ''), ('one no number', 'Savol?\nA Bir\nB Ikki ++\nC Uch\nD Tort', 1, 'B', True, ''), ('lone letters', 'Savol?\nA\nBir\nB\nIkki ++\nC\nUch\nD\nTort', 1, 'B', True, ''), ('two no number blank', 'Savol 1?\nA Bir\nB Ikki ++\nC Uch\nD Tort\n\nSavol 2?\nA Bir\nB Ikki\nC Uch ++\nD Tort', 2, 'B', True, ''), ('two no number contiguous', 'Savol 1?\nA Bir\nB Ikki ++\nC Uch\nD Tort\nSavol 2?\nA Bir\nB Ikki\nC Uch ++\nD Tort', 2, 'B', True, ''), ('dot marker', 'Savol?\nA) Bir\nB) Ikki ...\nC) Uch\nD) Tort', 1, 'B', True, ''), ('missing marker', 'Savol?\nA) Bir\nB) Ikki\nC) Uch\nD) Tort', 1, None, False, ''), ('double marker', 'Savol?\nA) Bir ++\nB) Ikki ++\nC) Uch\nD) Tort', 1, None, False, ''), ('missing d', 'Savol?\nA) Bir\nB) Ikki ++\nC) Uch', 0, None, False, ''), ('inline question+options', 'Natijani toping. A) 10 B) 20 ++ C) 30 D) 40', 1, 'B', True, ''), ('en dash', '1 – Savol?\nA – Bir\nB – Ikki ++\nC – Uch\nD – Tort', 1, 'B', True, ''), ('bracket options', 'Savol?\n(A) Bir\n[B] Ikki ++\n(C) Uch\n[D] Tort', 1, 'B', True, ''), ('math plus', '2 + 2 nechaga teng?\nA) 1\nB) 2\nC) 4 ++\nD) 5', 1, 'C', True, ''), ('mixed lower and upper same line', 'Savol?\na) bir; b) ikki; c) uch; d) tort\nA) 1\nB) 2 ++\nC) 3\nD) 4', 1, 'B', True, 'a) bir;')]

passed = 0
for name, raw, expected_n, expected_correct, expected_valid, fragment in TESTS:
    canonical, prep_errors = normalizer.normalize_bulk_questions(raw)
    items, errors = ([], prep_errors) if prep_errors else legacy(canonical)
    correct_ok = expected_correct is None or (
        bool(items) and items[0].get("correct") == expected_correct
    )
    fragment_ok = not fragment or (
        bool(items) and fragment in items[0]["text"]
    )
    ok = (
        len(items) == expected_n
        and (not errors) == expected_valid
        and correct_ok
        and fragment_ok
    )
    print(("PASS" if ok else "FAIL") + "  " + name)
    if not ok:
        print("  count:", len(items), "errors:", [ascii(e) for e in errors[:3]])
        print("  canonical sample:", ascii(canonical[:400]))
    passed += int(ok)

print("\nTEST RESULT: {}/{}".format(passed, len(TESTS)))
if passed != len(TESTS):
    raise SystemExit("TESTS FAILED: do not integrate or deploy")
print("ALL SAMPLE TESTS PASSED; NO BOT FILES OR DATABASE CHANGED")

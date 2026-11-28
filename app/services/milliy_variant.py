"""Milliy sertifikat: tayyor variantlar (bot va veb-ilova uchun BITTA servis).

Zip (ichida bir nechta 55 talik yoki 1–45 talik fayl) yoki bitta fayl yuklanadi. Har bir fayl — bitta variant.
Variant savollari «Hammasini bitta fayldan yuklash» bilan AYNAN bir xil tekshiriladi (check_stage "a").
Variantdan keyin oddiy tartibda imtihon ochiladi (vaqt, guruh, muddat, nom) — savollar umumiy bazaga yozilmaydi.
Fayl nomi variant nomi bo'ladi. Shu nomli variant qayta yuklansa — yangilanadi (takrorlanmaydi).
"""
from __future__ import annotations

import io
import json
import re
import zipfile
from datetime import datetime
from pathlib import PurePosixPath

MAX_ZIP = 60 * 1024 * 1024          # zip faylning o'zi
MAX_UNPACKED = 200 * 1024 * 1024    # ichidagilar jami (zip-bomba himoyasi)
MAX_FILES = 60
EXTS = (".docx", ".txt", ".xlsx")


def natural_key(name: str):
    return [int(p) if p.isdigit() else p.casefold() for p in re.split(r"(\d+)", name or "")]


def _entry_name(info: zipfile.ZipInfo) -> str:
    """Windows'da yaratilgan zip nomlari (UTF-8 belgisi yo'q) kirillcha bo'lsa ham to'g'ri o'qilsin."""
    name = info.filename
    if info.flag_bits & 0x800:
        return name
    try:
        raw = name.encode("cp437")
    except UnicodeEncodeError:
        return name
    for enc in ("utf-8", "cp866"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            pass
    return name


def clean_title(name: str) -> str:
    stem = PurePosixPath(name.replace("\\", "/")).stem
    stem = " ".join(stem.replace("_", " ").split())
    return (stem or "Variant")[:200]


def is_zip(filename: str, data: bytes) -> bool:
    name = (filename or "").lower()
    if name.endswith(".zip"):
        return True
    return data[:4] == b"PK\x03\x04" and not name.endswith((".docx", ".xlsx"))


def zip_entries(data: bytes):
    """Zip ichidagi fayllar. Qaytaradi: ([(nom, fayl_nomi, baytlar)], [(fayl_nomi, sabab)] — o'tkazib yuborilganlar)."""
    from .savol_import import MAX_FILE

    if len(data) > MAX_ZIP:
        raise ValueError("Zip juda katta (60 MB dan oshmasin). 2 ta zipga bo'lib yuklang.")
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise ValueError("Zip faylni ochib bo'lmadi. Fayl buzilgan yoki .zip emas (.rar bo'lsa, .zip qilib qayta siqing).")
    files, skipped, total = [], [], 0
    with zf:
        infos = [i for i in zf.infolist() if not i.is_dir()]
        for info in infos:
            name = _entry_name(info)
            base = PurePosixPath(name.replace("\\", "/")).name
            if not base or "__MACOSX" in name or base.startswith((".", "~$")):
                continue                                  # tizim / Word vaqtinchalik fayllari
            if not base.lower().endswith(EXTS):
                skipped.append((base, "fayl turi mos emas (faqat .docx, .txt, .xlsx)"))
                continue
            if info.file_size > MAX_FILE:
                skipped.append((base, "juda katta (15 MB dan oshmasin)"))
                continue
            total += info.file_size
            if total > MAX_UNPACKED:
                raise ValueError("Zip ichidagi fayllar juda katta. 2 ta zipga bo'lib yuklang.")
            if len(files) >= MAX_FILES:
                raise ValueError(f"Bitta zipda {MAX_FILES} tadan ortiq fayl bo'lmasin.")
            try:
                raw = zf.read(info)
            except Exception:
                skipped.append((base, "zipdan chiqarib bo'lmadi"))
                continue
            files.append((clean_title(name), base, raw))
    # bir xil nomlar (turli papkalarda) — tartib raqami qo'shiladi
    seen, out, counter = {}, [], {}
    for t, _b, _r in files:
        seen[t.casefold()] = seen.get(t.casefold(), 0) + 1
    for t, base, raw in files:
        k = t.casefold()
        if seen[k] > 1:
            counter[k] = counter.get(k, 0) + 1
            t = f"{t} ({counter[k]})"[:200]
        out.append((t, base, raw))
    out.sort(key=lambda x: natural_key(x[0]))
    return out, skipped


def parse_variant(filename: str, data: bytes) -> dict:
    """Bitta faylni tekshiradi («Hammasini bitta fayldan yuklash» bilan bir xil). Rasmlar ham olinadi."""
    from ..webapp_imtihon import MILLIY_C, _stage_fns, check_stage
    from .imtihon import milliy_open_ok
    from .savol_import import extract_text

    res = {"file": filename, "title": clean_title(filename), "ok": False, "payloads": [], "closed": 0, "open": 0,
           "errors": [], "warnings": []}
    try:
        text, notes = extract_text(filename, data)
    except ValueError as exc:
        res["errors"] = [str(exc)]
        return res
    res["warnings"] = [n for n in (notes or []) if "rasm olindi" not in n]
    c, o, errors, warnings = check_stage("a", [text], _stage_fns())
    res["closed"], res["open"] = len(c), len(o)
    res["warnings"] += list(warnings)
    if not errors and (len(c) != MILLIY_C or not milliy_open_ok(o)):
        errors = [f"Yopiq {MILLIY_C} ta va ochiq 20 ta (yoki 10 ta a/b li) savol bo'lishi kerak (hozir {len(c)} + {len(o)})."]
    res["errors"] = [str(e) for e in errors]
    if not res["errors"]:
        res["ok"] = True
        res["payloads"] = list(c) + list(o)
    return res


def parse_upload(filename: str, data: bytes) -> dict:
    """Zip yoki bitta fayl. Qaytaradi: {"items": [parse_variant natijalari], "skipped": [(fayl, sabab)]}."""
    if is_zip(filename, data):
        files, skipped = zip_entries(data)
        if not files:
            raise ValueError("Zip ichida .docx / .txt / .xlsx fayl topilmadi.")
        items = []
        for title, base, raw in files:
            r = parse_variant(base, raw)
            r["title"] = title
            items.append(r)
        return {"items": items, "skipped": skipped}
    if not (filename or "").lower().endswith(EXTS):
        raise ValueError("Zip yoki .docx / .txt / .xlsx fayl yuboring.")
    return {"items": [parse_variant(filename, data)], "skipped": []}


async def save_variants(session, uid: int, items: list[dict]) -> tuple[int, int]:
    """To'g'ri variantlarni saqlaydi. Shu nomli variant bo'lsa — yangilanadi. Qaytaradi: (yangi, yangilangan)."""
    from sqlalchemy import select

    from ..models import MilliyVariant

    existing = {v.title.casefold(): v for v in await session.scalars(select(MilliyVariant))}
    added = updated = 0
    now = datetime.utcnow()
    for it in items:
        if not it.get("ok"):
            continue
        blob = json.dumps(it["payloads"], ensure_ascii=False)
        nc = sum(1 for p in it["payloads"] if p.get("k") == "c")
        no = len(it["payloads"]) - nc
        old = existing.get(it["title"].casefold())
        if old is not None:
            old.payload, old.n_closed, old.n_open, old.updated_at = blob, nc, no, now
            updated += 1
        else:
            v = MilliyVariant(title=it["title"], created_by=uid, payload=blob, n_closed=nc, n_open=no,
                              created_at=now, updated_at=now)
            session.add(v)
            existing[it["title"].casefold()] = v
            added += 1
    await session.commit()
    return added, updated


async def list_variants(session) -> list:
    from sqlalchemy import select

    from ..models import MilliyVariant

    rows = list(await session.scalars(select(MilliyVariant)))
    rows.sort(key=lambda v: natural_key(v.title))
    return rows


def variant_payloads(v) -> list:
    try:
        data = json.loads(v.payload or "[]")
    except ValueError:
        return []
    return data if isinstance(data, list) else []


def langs_line(items) -> str:
    """«o'zbek, rus (55/55), ingliz (55/55)» — variantda qaysi tillar bor."""
    n = len(items)
    out = ["o'zbek"]
    for code, name in (("ru", "rus"), ("en", "ingliz")):
        k = sum(1 for p in items if code in (p.get("tr") or {}))
        if k:
            out.append(name if k == n else f"{name} ({k}/{n})")
    return ", ".join(out)


def default_exam_title(vtitle: str) -> str:
    vtitle = (vtitle or "").strip()
    if "milliy" in vtitle.casefold():
        return vtitle[:200]
    return f"Milliy sertifikat — {vtitle}"[:200]


def can_delete(uid: int, v, is_admin: bool) -> bool:
    return is_admin or v.created_by == uid


def report_lines(res: dict, added: int, updated: int, limit: int = 12) -> list[str]:
    """Yuklash natijasi (bot va veb uchun bir xil matn)."""
    items = res["items"]
    ok = [i for i in items if i["ok"]]
    bad = [i for i in items if not i["ok"]]
    lines = [f"Fayllar: {len(items)} ta · to'g'ri: {len(ok)} ta · xato: {len(bad)} ta"]
    if added or updated:
        lines.append(f"Saqlandi: {added} ta yangi variant" + (f", {updated} ta yangilandi" if updated else ""))
    for i in bad[:limit]:
        lines.append(f"❌ {i['title']}: " + "; ".join(i["errors"][:2]))
    if len(bad) > limit:
        lines.append(f"… va yana {len(bad) - limit} ta xato fayl")
    for name, why in res.get("skipped", [])[:6]:
        lines.append(f"⏭ {name}: {why}")
    return lines


async def mark_used(vid) -> None:
    """Variantdan imtihon ochildi — hisoblagich (bot va veb). Xato imtihon yaratishni to'xtatmaydi."""
    import logging

    from sqlalchemy import update

    from ..db import SessionLocal
    from ..models import MilliyVariant

    try:
        async with SessionLocal() as s:
            await s.execute(update(MilliyVariant).where(MilliyVariant.id == int(vid)).values(used=MilliyVariant.used + 1))
            await s.commit()
    except Exception:
        logging.getLogger(__name__).exception("milliy variant: hisoblagich")

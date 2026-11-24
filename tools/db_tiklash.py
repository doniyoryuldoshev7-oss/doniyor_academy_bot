"""Zaxira faylidan bazani tiklash.

DIQQAT: faqat BO'SH bazaga tiklang (yangi Railway Postgres). Mavjud
ma'lumotlar ustiga yozilmaydi: id bir xil bo'lsa, qator o'tkazib
yuboriladi.

Ishlatish (loyiha papkasida, DATABASE_URL tegishli baza bo'lsin):
    python tools/db_tiklash.py doniyor_zaxira_2026-11-24_03-00.json.gz
"""

import asyncio
import base64
import gzip
import json
import sys
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text  # noqa: E402

from app.db import Base, engine, init_db  # noqa: E402


def decode(value):
    if isinstance(value, dict):
        if "__dt__" in value:
            return datetime.fromisoformat(value["__dt__"])
        if "__d__" in value:
            return date.fromisoformat(value["__d__"])
        if "__b64__" in value:
            return base64.b64decode(value["__b64__"])
        if "__dec__" in value:
            return Decimal(value["__dec__"])
    return value


async def main(path: str):
    await init_db()

    tables = {t.name: t for t in Base.metadata.sorted_tables}
    inserted = {}
    skipped = {}

    # image_blobs jadvali
    async with engine.begin() as conn:
        blob = "BYTEA" if engine.dialect.name == "postgresql" else "BLOB"
        await conn.execute(
            text(
                "CREATE TABLE IF NOT EXISTS image_blobs ("
                f"path TEXT PRIMARY KEY, data {blob} NOT NULL, "
                "size INTEGER NOT NULL DEFAULT 0)"
            )
        )

    rows_by_table = {}

    with gzip.open(path, "rt", encoding="utf-8") as handle:
        for line in handle:
            item = json.loads(line)

            if "meta" in item:
                continue

            rows_by_table.setdefault(item["t"], []).append(
                {k: decode(v) for k, v in item["r"].items()}
            )

    order = [t.name for t in Base.metadata.sorted_tables] + ["image_blobs"]

    for name in order:
        rows = rows_by_table.get(name)

        if not rows:
            continue

        inserted[name] = 0
        skipped[name] = 0

        for row in rows:
            try:
                async with engine.begin() as conn:
                    if name == "image_blobs":
                        await conn.execute(
                            text(
                                "INSERT INTO image_blobs (path, data, size) "
                                "VALUES (:path, :data, :size) "
                                "ON CONFLICT (path) DO NOTHING"
                            ),
                            row,
                        )
                    else:
                        await conn.execute(tables[name].insert(), row)

                inserted[name] += 1
            except Exception:
                skipped[name] += 1

    # Postgres: id hisoblagichlarini tiklash
    if engine.dialect.name == "postgresql":
        async with engine.begin() as conn:
            for name, table in tables.items():
                if "id" in table.c:
                    await conn.execute(
                        text(
                            f"SELECT setval(pg_get_serial_sequence('{name}', 'id'), "
                            f"COALESCE((SELECT MAX(id) FROM {name}), 1), "
                            f"(SELECT COUNT(*) FROM {name}) > 0)"
                        )
                    )

    print("Tiklandi:")

    for name in inserted:
        print(f"  {name}: {inserted[name]} qator, o'tkazib yuborildi: {skipped[name]}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(1)

    asyncio.run(main(sys.argv[1]))

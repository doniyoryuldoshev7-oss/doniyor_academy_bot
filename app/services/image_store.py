"""Rasmli savollar rasmlarini bazada ham saqlash.

Muammo: rasmlar server diskida (app/question_images) turadi. Railway
botni qayta joylashtirganda (deploy) disk tozalanadi va rasmlar
yo'qolardi. Bu modul har bir rasmning nusxasini bazada saqlaydi va bot
ishga tushganda yo'qolgan rasmlarni bazadan qayta tiklaydi.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from sqlalchemy import text

from ..db import engine

log = logging.getLogger(__name__)

IMAGE_DIR = Path("app/question_images")
EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
MAX_BYTES = 10 * 1024 * 1024


def _blob_type() -> str:
    return "BYTEA" if engine.dialect.name == "postgresql" else "BLOB"


async def _ensure_table() -> None:
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "CREATE TABLE IF NOT EXISTS image_blobs ("
                "path TEXT PRIMARY KEY, "
                f"data {_blob_type()} NOT NULL, "
                "size INTEGER NOT NULL DEFAULT 0)"
            )
        )


async def store_image(path, data: bytes | None = None) -> bool:
    """Bitta rasmni bazaga yozadi (mavjud bo'lsa yangilaydi)."""
    try:
        key = Path(path).as_posix()

        if data is None:
            data = await asyncio.to_thread(Path(path).read_bytes)

        if not data or len(data) > MAX_BYTES:
            return False

        await _ensure_table()

        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO image_blobs (path, data, size) "
                    "VALUES (:p, :d, :s) "
                    "ON CONFLICT (path) DO UPDATE SET "
                    "data = excluded.data, size = excluded.size"
                ),
                {"p": key, "d": data, "s": len(data)},
            )

        return True

    except Exception:
        log.exception("Rasm bazaga saqlanmadi: %s", path)
        return False


def _local_files() -> dict[str, Path]:
    result = {}

    if not IMAGE_DIR.is_dir():
        return result

    for file in IMAGE_DIR.iterdir():
        if file.is_file() and file.suffix.lower() in EXTENSIONS:
            result[file.as_posix()] = file

    return result


async def sync_images() -> tuple[int, int]:
    """Bot ishga tushganda chaqiriladi.

    1) Diskda yo'q, lekin bazada bor rasmlarni diskka tiklaydi.
    2) Diskda bor, lekin bazada yo'q rasmlarni bazaga saqlaydi.

    Qaytaradi: (tiklangan, bazaga saqlangan).
    """
    restored = 0
    saved = 0

    try:
        await _ensure_table()

        async with engine.connect() as conn:
            rows = (
                await conn.execute(text("SELECT path FROM image_blobs"))
            ).all()

        in_db = {row[0] for row in rows}
        local = await asyncio.to_thread(_local_files)

        # 1. tiklash
        for key in in_db - set(local):
            async with engine.connect() as conn:
                row = (
                    await conn.execute(
                        text("SELECT data FROM image_blobs WHERE path = :p"),
                        {"p": key},
                    )
                ).first()

            if not row or row[0] is None:
                continue

            target = Path(key)
            data = bytes(row[0])

            def _write(target=target, data=data):
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)

            await asyncio.to_thread(_write)
            restored += 1

        # 2. zaxiralash
        for key, file in local.items():
            if key in in_db:
                continue

            if await store_image(file):
                saved += 1

    except Exception:
        log.exception("Rasmlarni sinxronlashda xato")

    log.info(
        "Rasmlar: diskka tiklandi=%s, bazaga saqlandi=%s",
        restored,
        saved,
    )

    return restored, saved

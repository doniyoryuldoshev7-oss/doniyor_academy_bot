"""Bazaning kunlik zaxira nusxasi.

Joylashuvi: app/services/db_backup.py

Har kuni (standart 03:00, Toshkent vaqti) butun bazani siqilgan fayl
qilib adminlarga Telegram orqali yuboradi. Fayl Telegram'da saqlanadi,
shuning uchun Railway buzilsa ham nusxa yo'qolmaydi.

Qo'lda olish: admin botga /zaxira deb yozadi.
Tiklash: tools/db_tiklash.py (ishlatilishi fayl boshida yozilgan).

Sozlamalar (ixtiyoriy, Railway Variables):
  BACKUP_ENABLED=1      0 qilinsa o'chadi
  BACKUP_HOUR=3         soat (mahalliy vaqt)
  ARCHIVE_CHAT_ID       bo'lsa, nusxa shu chatga ham yuboriladi
  SUPERADMIN_IDS        bo'lsa, nusxa faqat shularga yuboriladi,
                        bo'lmasa -- ADMIN_IDS dagi hammaga
"""

from __future__ import annotations

import asyncio
import base64
import gzip
import io
import json
import logging
import os
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import BufferedInputFile, Message
from sqlalchemy import select, text

from ..config import settings
from ..db import Base, engine
from .kv import kv_get, kv_set

log = logging.getLogger(__name__)

router = Router()

# Zaxiraga kirmaydigan jadvallar (vaqtinchalik holat)
SKIP_TABLES = {"fsm_storage", "bot_kv"}
CHUNK_ROWS = 1000
# Telegram bot fayl chegarasi 50 MB; zaxira bilan
PART_BYTES = 45 * 1024 * 1024


def _enabled() -> bool:
    return os.getenv("BACKUP_ENABLED", "1").strip() not in {"0", "false", "no"}


def _hour() -> int:
    try:
        return min(23, max(0, int(os.getenv("BACKUP_HOUR", "3"))))
    except ValueError:
        return 3


def _offset() -> timedelta:
    try:
        hours = int(os.getenv("REVIEW_REMINDER_UTC_OFFSET", "5"))
    except ValueError:
        hours = 5

    return timedelta(hours=hours)


def _encode(value):
    if value is None or isinstance(value, (bool, int, float, str)):
        return value

    if isinstance(value, datetime):
        return {"__dt__": value.isoformat()}

    if isinstance(value, date):
        return {"__d__": value.isoformat()}

    if isinstance(value, (bytes, bytearray, memoryview)):
        return {"__b64__": base64.b64encode(bytes(value)).decode("ascii")}

    if isinstance(value, Decimal):
        return {"__dec__": str(value)}

    return str(value)


def _line(table: str, row: dict) -> bytes:
    payload = {"t": table, "r": {k: _encode(v) for k, v in row.items()}}
    return (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")


async def build_backup() -> tuple[bytes, dict]:
    """Butun bazani siqilgan JSON-qatorlar faylga aylantiradi.

    Qaytaradi: (baytlar, {jadval: qatorlar_soni}).
    """
    buffer = io.BytesIO()
    counts: dict[str, int] = {}

    with gzip.GzipFile(fileobj=buffer, mode="wb", compresslevel=6) as gz:
        header = {
            "format": "doniyor-backup-1",
            "created": datetime.utcnow().isoformat(),
        }
        gz.write((json.dumps({"meta": header}) + "\n").encode("utf-8"))

        async with engine.connect() as conn:
            for table in Base.metadata.sorted_tables:
                if table.name in SKIP_TABLES:
                    continue

                counts[table.name] = 0
                result = await conn.stream(select(table))

                async for chunk in result.mappings().partitions(CHUNK_ROWS):
                    for row in chunk:
                        gz.write(_line(table.name, dict(row)))
                        counts[table.name] += 1

            # Rasmlar zaxirasi (raw jadval, metadata'da yo'q)
            try:
                rows = await conn.execute(
                    text("SELECT path, data, size FROM image_blobs")
                )
                count = 0

                for path, data, size in rows:
                    gz.write(
                        _line(
                            "image_blobs",
                            {"path": path, "data": data, "size": size},
                        )
                    )
                    count += 1

                counts["image_blobs"] = count
            except Exception:
                # jadval hali yo'q bo'lishi mumkin
                pass

    return buffer.getvalue(), counts


def _recipients() -> list[int | str]:
    raw = os.getenv("SUPERADMIN_IDS", "")
    owners = []

    for part in raw.split(","):
        part = part.strip()

        if part.lstrip("-").isdigit():
            owners.append(int(part))

    targets: list[int | str] = owners or sorted(settings.admins)

    archive = (settings.archive_chat_id or "").strip()

    if archive:
        try:
            targets.append(int(archive))
        except ValueError:
            targets.append(archive)

    seen = []

    for target in targets:
        if target not in seen:
            seen.append(target)

    return seen


async def send_backup(bot, targets=None) -> dict:
    """Zaxirani yaratib yuboradi. Qaytaradi: natija lug'ati."""
    data, counts = await build_backup()

    stamp = (datetime.now(timezone.utc) + _offset()).strftime("%Y-%m-%d_%H-%M")

    total_rows = sum(counts.values())
    size_mb = len(data) / (1024 * 1024)

    parts = [data[i : i + PART_BYTES] for i in range(0, len(data), PART_BYTES)]

    sent_to = []
    errors = []

    for target in targets or _recipients():
        try:
            for number, part in enumerate(parts, 1):
                name = f"doniyor_zaxira_{stamp}.json.gz"

                if len(parts) > 1:
                    name += f".part{number}"

                caption = (
                    "🗄 Baza zaxirasi\n"
                    f"🕒 {stamp}\n"
                    f"📦 {size_mb:.2f} MB · {total_rows} qator"
                )

                if len(parts) > 1:
                    caption += f"\n🧩 Qism {number}/{len(parts)}"

                await bot.send_document(
                    target,
                    BufferedInputFile(part, filename=name),
                    caption=caption,
                )

            sent_to.append(target)

        except Exception as exc:
            errors.append(f"{target}: {type(exc).__name__}: {exc}")
            log.warning("Zaxira yuborilmadi (%s): %s", target, exc)

    if sent_to:
        await kv_set("backup_last_run", datetime.utcnow().isoformat())

    return {
        "size_mb": size_mb,
        "rows": total_rows,
        "tables": counts,
        "sent_to": sent_to,
        "errors": errors,
    }


async def _seconds_until_next() -> float:
    now = datetime.now(timezone.utc) + _offset()

    target = now.replace(hour=_hour(), minute=0, second=0, microsecond=0)

    if target <= now:
        target += timedelta(days=1)

    delay = (target - now).total_seconds()

    # Bot uzoq o'chiq turgan bo'lsa: nusxa 26 soatdan eski -- tez olinadi
    try:
        last = await kv_get("backup_last_run")

        if last:
            age = datetime.utcnow() - datetime.fromisoformat(last)

            if age > timedelta(hours=26):
                return 120.0
        else:
            return 120.0
    except Exception:
        pass

    return delay


async def backup_loop(bot) -> None:
    if not _enabled():
        log.info("Kunlik zaxira o'chirilgan (BACKUP_ENABLED=0).")
        return

    log.info("Kunlik zaxira sikli ishga tushdi (soat %02d:00).", _hour())

    while True:
        try:
            delay = await _seconds_until_next()
            log.info("Keyingi zaxira %.0f daqiqadan keyin.", delay / 60)

            await asyncio.sleep(delay)

            result = await send_backup(bot)

            log.info(
                "Zaxira: %.2f MB, %s qator, yuborildi=%s, xato=%s",
                result["size_mb"],
                result["rows"],
                result["sent_to"],
                result["errors"],
            )

        except asyncio.CancelledError:
            raise

        except Exception:
            log.exception("Zaxira siklida xato")
            await asyncio.sleep(600)
            continue

        # bir kunda ikki marta ishlamasligi uchun pauza
        await asyncio.sleep(120)


@router.message(Command("zaxira"))
async def backup_command(message: Message):
    if message.chat.type != "private":
        return

    if message.from_user is None or message.from_user.id not in settings.admins:
        return

    wait = await message.answer("⏳ Zaxira tayyorlanmoqda...")

    try:
        result = await send_backup(message.bot, targets=[message.chat.id])
    except Exception as exc:
        log.exception("Qo'lda zaxira xatosi")
        await wait.edit_text(f"❌ Zaxira olinmadi: {type(exc).__name__}")
        return

    if result["errors"]:
        await wait.edit_text("❌ Fayl yuborilmadi:\n" + "\n".join(result["errors"]))
        return

    await wait.edit_text(
        f"✅ Zaxira yuborildi ({result['size_mb']:.2f} MB, "
        f"{result['rows']} qator)."
    )

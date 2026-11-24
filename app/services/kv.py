"""Oddiy kalit-qiymat saqlash (bazada).

Joylashuvi: app/services/kv.py

Bot qayta ishga tushsa ham yo'qolmaydigan kichik belgilar uchun,
masalan "eslatma bugun yuborilgan".
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import text

from ..db import engine

_ready = False


async def _ensure() -> None:
    global _ready

    if _ready:
        return

    async with engine.begin() as conn:
        await conn.execute(
            text(
                "CREATE TABLE IF NOT EXISTS bot_kv ("
                "key VARCHAR(100) PRIMARY KEY, "
                "value TEXT NOT NULL, "
                "updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP)"
            )
        )

    _ready = True


async def kv_get(key: str) -> str | None:
    await _ensure()

    async with engine.begin() as conn:
        row = (
            await conn.execute(
                text("SELECT value FROM bot_kv WHERE key = :key"),
                {"key": key},
            )
        ).fetchone()

    return row[0] if row else None


async def kv_set(key: str, value: str) -> None:
    await _ensure()

    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO bot_kv (key, value, updated_at) "
                "VALUES (:key, :value, :now) "
                "ON CONFLICT (key) DO UPDATE SET "
                "value = :value, updated_at = :now"
            ),
            {"key": key, "value": value, "now": datetime.utcnow()},
        )

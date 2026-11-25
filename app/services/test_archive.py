"""Test eksportini yetkazish va arxivlash — Doniyor Academy bot.

Joylashuvi: app/services/test_archive.py

Har bir yuklangan test uchun:
  1) admin shaxsiy chatiga .docx yuboriladi;
  2) nusxasi arxiv kanaliga tashlanadi (ARCHIVE_CHAT_ID);
  3) imkon bo'lsa lokal papkaga ham yoziladi.

MUHIM: Railway diski vaqtinchalik (ephemeral) — deploydan keyin
lokal fayllar o'chib ketadi. Shuning uchun ishonchli saqlash joyi —
arxiv kanali. Lokal nusxa faqat qo'shimcha.
"""

from __future__ import annotations

import html
import logging
from datetime import datetime

from ..textfix import now_uz
from pathlib import Path

from aiogram.types import BufferedInputFile

from ..config import settings

log = logging.getLogger(__name__)

LOCAL_EXPORT_DIR = Path("app/test_exports")


def archive_chat_id() -> int | str | None:
    """ARCHIVE_CHAT_ID: -100... raqam yoki @kanal_nomi."""
    raw = str(getattr(settings, "archive_chat_id", "") or "").strip()

    if not raw:
        return None

    if raw.startswith("@"):
        return raw

    try:
        return int(raw)
    except ValueError:
        return raw


def build_caption(
    subject_name: str,
    topic_name: str,
    count: int,
    *,
    author: str | None = None,
    note: str | None = None,
) -> str:
    lines = [
        "\U0001F4C4 <b>Test eksporti</b>",
        "",
        "\U0001F4DA Fan: <b>" + html.escape(subject_name or "—") + "</b>",
        "\U0001F4D6 Mavzu: <b>" + html.escape(topic_name or "—") + "</b>",
        "\U0001F522 Savollar: <b>" + str(count) + "</b>",
        "\U0001F553 " + now_uz().strftime("%d.%m.%Y %H:%M"),
    ]

    if author:
        lines.append("\U0001F464 " + html.escape(author))

    if note:
        lines.append("")
        lines.append(html.escape(note))

    return "\n".join(lines)


def save_local_copy(data: bytes, filename: str) -> str | None:
    """Qo'shimcha lokal nusxa. Xato bo'lsa jim o'tadi."""
    try:
        LOCAL_EXPORT_DIR.mkdir(parents=True, exist_ok=True)

        path = LOCAL_EXPORT_DIR / filename
        path.write_bytes(data)

        return path.as_posix()
    except Exception as exc:
        log.warning("Lokal nusxa saqlanmadi: %s", exc)
        return None


async def send_document(
    bot,
    chat_id: int | str,
    data: bytes,
    filename: str,
    caption: str | None = None,
) -> bool:
    try:
        await bot.send_document(
            chat_id,
            BufferedInputFile(data, filename=filename),
            caption=caption,
        )
        return True
    except Exception as exc:
        log.warning("Hujjat yuborilmadi (chat=%s): %s", chat_id, exc)
        return False


async def deliver_test_export(
    bot,
    admin_chat_id: int | None,
    data: bytes,
    filename: str,
    *,
    subject_name: str,
    topic_name: str,
    count: int,
    author: str | None = None,
    note: str | None = None,
    to_archive: bool = True,
    local_copy: bool = True,
) -> dict:
    """Eksportni adminga va arxiv kanaliga yetkazadi.

    Natija: {"admin": bool, "archive": bool, "local": str | None}
    """
    caption = build_caption(
        subject_name,
        topic_name,
        count,
        author=author,
        note=note,
    )

    result: dict = {"admin": False, "archive": False, "local": None}

    if admin_chat_id:
        result["admin"] = await send_document(
            bot,
            admin_chat_id,
            data,
            filename,
            caption,
        )

    if to_archive:
        target = archive_chat_id()

        if target is not None:
            result["archive"] = await send_document(
                bot,
                target,
                data,
                filename,
                caption,
            )

    if local_copy:
        result["local"] = save_local_copy(data, filename)

    return result

"""Adminga ogohlantirish: bot xato qilsa yoki qayta ishga tushsa.

Joylashuvi: app/services/admin_ogoh.py

Bir xil xato 15 daqiqada ko'pi bilan bir marta yuboriladi (xabarlar
bo'roni bo'lmasligi uchun). O'chirish: ADMIN_ALERTS=0.
"""

from __future__ import annotations

import asyncio
import html
import logging
import os
import time
import traceback
from datetime import datetime, timedelta, timezone

from .baza_zaxira import _recipients
from .kv import kv_get, kv_set

log = logging.getLogger(__name__)

COOLDOWN_SECONDS = 15 * 60
START_COOLDOWN_SECONDS = 10 * 60

_last_sent: dict[str, float] = {}


def _local_now() -> datetime:
    """Mahalliy vaqt (server UTC da ishlaydi). Standart: UTC+5."""
    try:
        hours = int(os.getenv("REVIEW_REMINDER_UTC_OFFSET", "5"))
    except ValueError:
        hours = 5

    return datetime.now(timezone.utc) + timedelta(hours=hours)


def _enabled() -> bool:
    return os.getenv("ADMIN_ALERTS", "1").strip() not in {"0", "false", "no"}


async def notify_admins(bot, text: str, key: str, cooldown: int = COOLDOWN_SECONDS) -> bool:
    """Xabarni adminlarga yuboradi. Qaytaradi: yuborildimi."""
    if not _enabled():
        return False

    now = time.monotonic()
    previous = _last_sent.get(key)

    if previous is not None and now - previous < cooldown:
        return False

    _last_sent[key] = now

    # xotira cheksiz o'smasin
    if len(_last_sent) > 200:
        for old_key in sorted(_last_sent, key=_last_sent.get)[:100]:
            _last_sent.pop(old_key, None)

    delivered = False

    for target in _recipients():
        try:
            await bot.send_message(
                target,
                text[:3900],
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
            delivered = True
        except Exception as exc:
            log.warning("Ogohlantirish yuborilmadi (%s): %s", target, exc)

    return delivered


def describe_error(exc: BaseException, update=None) -> tuple[str, str]:
    """(xabar matni, takrorlanishni aniqlovchi kalit)."""
    name = type(exc).__name__
    message = str(exc).strip().replace("\n", " ")[:300]

    where = "?"

    try:
        frames = traceback.extract_tb(exc.__traceback__)

        # eng oxirgi app/ ichidagi qator
        own = [f for f in frames if "/app/" in f.filename.replace("\\", "/")]
        frame = (own or frames)[-1]
        short = frame.filename.replace("\\", "/").split("/app/")[-1]
        where = f"{short}:{frame.lineno} ({frame.name})"
    except Exception:
        pass

    who = ""
    kind = ""

    if update is not None:
        try:
            if update.callback_query is not None:
                kind = "tugma: " + str(update.callback_query.data)[:60]
                who = str(update.callback_query.from_user.id)
            elif update.message is not None:
                kind = "xabar: " + str(update.message.text or "")[:60]
                who = str(update.message.from_user.id)
        except Exception:
            pass

    lines = [
        "⚠️ <b>Botda xato</b>",
        f"<code>{html.escape(name)}</code>: {html.escape(message)}",
        f"📍 {html.escape(where)}",
    ]

    if kind:
        lines.append("🖱 " + html.escape(kind))

    if who:
        lines.append("👤 foydalanuvchi: " + who)

    lines.append("🕒 " + _local_now().strftime("%d.%m.%Y %H:%M:%S"))

    return "\n".join(lines), f"{name}|{where}"


def alert_error(bot, exc: BaseException, update=None) -> None:
    """Xatoni fon vazifasi sifatida yuboradi (handlerni kutdirmaydi)."""
    if bot is None or not _enabled():
        return

    try:
        text, key = describe_error(exc, update)
        asyncio.create_task(notify_admins(bot, text, key))
    except Exception:
        log.exception("Ogohlantirishni tayyorlashda xato")


async def announce_start(bot) -> None:
    """Bot ishga tushganda adminga xabar (tez-tez qayta ishga tushsa, kamida
    10 daqiqada bir marta)."""
    if not _enabled():
        return

    try:
        last = await kv_get("start_alert_ts")

        if last and time.time() - float(last) < START_COOLDOWN_SECONDS:
            return

        await kv_set("start_alert_ts", str(time.time()))
    except Exception:
        pass

    await notify_admins(
        bot,
        "✅ <b>Bot ishga tushdi</b>\n🕒 "
        + _local_now().strftime("%d.%m.%Y %H:%M:%S"),
        "start",
        cooldown=0,
    )

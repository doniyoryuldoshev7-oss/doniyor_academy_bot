"""Telegram vaqtinchalik xatolarida (502 Bad Gateway, tarmoq uzilishi, "kuting") qayta urinish.
Bot bloklangan yoki so'rov noto'g'ri bo'lsa (Forbidden/BadRequest) qayta urinmaydi, darhol xato beradi."""
from __future__ import annotations

import asyncio
import logging

log = logging.getLogger(__name__)

TRANSIENT = ("TelegramServerError", "TelegramNetworkError", "TelegramRetryAfter", "ClientError", "TimeoutError", "ServerDisconnectedError")


def is_transient(exc: BaseException) -> bool:
    return any(c.__name__ in TRANSIENT for c in type(exc).__mro__)


def delay_for(exc: BaseException, attempt: int, base: float = 2.0, cap: float = 30.0) -> float:
    """attempt: 1 dan boshlanadi. 'Kuting' desa, Telegram aytgan vaqt; aks holda 2, 4, 8... (30 gacha)."""
    ra = getattr(exc, "retry_after", None)
    if isinstance(ra, (int, float)) and ra > 0:
        return float(min(ra + 1, 60))
    return float(min(base * (2 ** (attempt - 1)), cap))


async def retry(make_call, tries: int = 4, sleep=asyncio.sleep):
    """make_call() — har safar yangi coroutine qaytaradi. Vaqtinchalik xatoda qayta urinadi."""
    last = None
    for attempt in range(1, tries + 1):
        try:
            return await make_call()
        except Exception as exc:  # noqa: BLE001
            if not is_transient(exc) or attempt == tries:
                raise
            last = exc
            wait = delay_for(exc, attempt)
            log.warning("Telegram vaqtinchalik xato (%s), %s soniyadan keyin qayta uriniladi (%s/%s)", type(exc).__name__, wait, attempt, tries)
            await sleep(wait)
    raise last  # pragma: no cover


async def send_message(bot, chat_id, text, reply_markup=None, tries: int = 3):
    return await retry(lambda: bot.send_message(chat_id, text, reply_markup=reply_markup), tries=tries)

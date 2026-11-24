"""Umumiy himoya qatlamlari.

Joylashuvi: app/middlewares.py

1) UserSerializeMiddleware -- bir foydalanuvchining so'rovlarini
   ketma-ket bajaradi. Webhook so'rovlari parallel ishlaydi, shuning
   uchun tugmani tez ikki marta bosish bitta javobni ikki marta
   hisoblashi yoki natijani ikki marta saqlashi mumkin edi.

2) on_error -- kutilmagan xatoni yutib yubormasdan, foydalanuvchiga
   tushunarli xabar beradi (aks holda tugma "qotib" qolardi).
"""

from __future__ import annotations

import asyncio
import logging

from aiogram import BaseMiddleware
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import ErrorEvent

log = logging.getLogger(__name__)


class UserSerializeMiddleware(BaseMiddleware):
    def __init__(self) -> None:
        self._locks: dict[int, asyncio.Lock] = {}
        self._waiting: dict[int, int] = {}

    async def __call__(self, handler, event, data):
        user = getattr(event, "from_user", None)

        if user is None:
            return await handler(event, data)

        uid = user.id

        lock = self._locks.get(uid)

        if lock is None:
            lock = self._locks[uid] = asyncio.Lock()

        self._waiting[uid] = self._waiting.get(uid, 0) + 1

        try:
            async with lock:
                return await handler(event, data)
        finally:
            self._waiting[uid] -= 1

            # Kutayotgan yo'q bo'lsa, xotirani bo'shatamiz
            if self._waiting[uid] <= 0:
                self._waiting.pop(uid, None)
                self._locks.pop(uid, None)


ERROR_TEXT = (
    "⚠️ Kutilmagan xatolik yuz berdi. Qaytadan urinib ko'ring "
    "yoki /start ni bosing."
)


# Zararsiz Telegram xatolari: foydalanuvchiga ogohlantirish ko'rsatilmaydi
HARMLESS = (
    "message is not modified",
    "query is too old",
    "query id is invalid",
    "message to edit not found",
    "message can't be edited",
    "message to delete not found",
)


async def on_error(event: ErrorEvent) -> bool:
    exc = event.exception

    if isinstance(exc, TelegramForbiddenError):
        log.warning("Foydalanuvchi botni bloklagan: %s", exc)
        return True

    if isinstance(exc, TelegramBadRequest) and any(
        text in str(exc).lower() for text in HARMLESS
    ):
        log.info("Zararsiz Telegram xatosi: %s", exc)
        return True

    log.error(
        "Handlerda xato: %s: %s",
        type(event.exception).__name__,
        event.exception,
        exc_info=event.exception,
    )

    update = event.update

    try:
        if update.callback_query is not None:
            await update.callback_query.answer(ERROR_TEXT, show_alert=True)
        elif update.message is not None:
            await update.message.answer(ERROR_TEXT)
    except Exception:
        # Javob berishning o'zi xato bersa (masalan eski tugma), e'tibor bermaymiz
        pass

    return True

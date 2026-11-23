"""Smart Takrorlash eslatmalari — Doniyor Academy bot.

Joylashuvi: app/services/review_reminder.py

Har kuni belgilangan soatda muddati kelgan takrorlashlari bor
o'quvchilarga xabar yuboradi. Hisoblash shartlari
smart_review_queue.due_reviews_query bilan bir xil — shuning uchun
"12 ta savol" deb yozilsa, tugmani bosganda aynan shuncha savol
chiqadi.

Env o'zgaruvchilari:
    SMART_REVIEW_ENABLED        -- 1 bo'lmasa, eslatma ham yuborilmaydi
    REVIEW_REMINDER_ENABLED     -- 1 (standart) / 0
    REVIEW_REMINDER_HOUR        -- mahalliy soat, standart 9
    REVIEW_REMINDER_UTC_OFFSET  -- standart 5 (Toshkent, UTC+5)
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import func, select

from ..db import SessionLocal
from ..models import Question, SmartReview, User

log = logging.getLogger(__name__)

DEFAULT_HOUR = 9
DEFAULT_UTC_OFFSET = 5

# Telegram sekundiga ~30 xabarga ruxsat beradi.
SEND_DELAY = 0.06


# ---------------------------------------------------------------
#  Sozlamalar
# ---------------------------------------------------------------

def smart_review_enabled() -> bool:
    return os.getenv("SMART_REVIEW_ENABLED", "0") == "1"


def reminders_enabled() -> bool:
    if not smart_review_enabled():
        return False

    return os.getenv("REVIEW_REMINDER_ENABLED", "1") == "1"


def _env_int(name: str, default: int, low: int, high: int) -> int:
    raw = (os.getenv(name) or "").strip()

    if not raw:
        return default

    try:
        value = int(raw)
    except ValueError:
        log.warning("%s noto'g'ri qiymat: %r", name, raw)
        return default

    if not low <= value <= high:
        log.warning("%s chegaradan tashqarida: %s", name, value)
        return default

    return value


def reminder_hour() -> int:
    return _env_int("REVIEW_REMINDER_HOUR", DEFAULT_HOUR, 0, 23)


def local_offset() -> timedelta:
    return timedelta(
        hours=_env_int(
            "REVIEW_REMINDER_UTC_OFFSET",
            DEFAULT_UTC_OFFSET,
            -12,
            14,
        )
    )


# ---------------------------------------------------------------
#  Kimga eslatma kerak
# ---------------------------------------------------------------

def due_counts_query(now: datetime):
    """Muddati kelgan takrorlashlar soni, o'quvchilar kesimida.

    Shartlar smart_review_queue.due_reviews_query bilan bir xil,
    shuning uchun eslatmadagi son takrorlash ko'rsatadigan savollar
    soniga aniq mos keladi (rasmli savollar ham kiradi).
    """
    return (
        select(
            User.telegram_id,
            User.full_name,
            func.count(SmartReview.id).label("due_count"),
        )
        .join(SmartReview, SmartReview.user_id == User.id)
        .join(Question, Question.id == SmartReview.question_id)
        .where(
            SmartReview.status == "active",
            SmartReview.next_review_at.is_not(None),
            SmartReview.next_review_at <= now,
            Question.question_mode == "closed",
            Question.is_active.is_(True),
            Question.correct_option.in_(("A", "B", "C", "D")),
            Question.option_a.is_not(None),
            Question.option_b.is_not(None),
            Question.option_c.is_not(None),
            Question.option_d.is_not(None),
            User.is_blocked.is_(False),
            User.registration_status == "approved",
        )
        .group_by(User.telegram_id, User.full_name)
        .order_by(func.count(SmartReview.id).desc())
    )


async def due_counts(session, now: datetime | None = None) -> list[tuple]:
    if now is None:
        now = datetime.now(timezone.utc)

    if now.tzinfo is None:
        raise ValueError("UTC vaqt zonasi ko'rsatilishi kerak")

    result = await session.execute(due_counts_query(now))

    return result.all()


# ---------------------------------------------------------------
#  Xabar
# ---------------------------------------------------------------

def reminder_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="\U0001F9E0 Takrorlashni boshlash",
                    callback_data="sr:start",
                )
            ],
            [
                InlineKeyboardButton(
                    text="\U0001F3E0 Bosh menyu",
                    callback_data="home",
                )
            ],
        ]
    )


def reminder_text(due_count: int) -> str:
    return (
        "\U0001F9E0 <b>SMART TAKRORLASH</b>\n\n"
        "Bugun takrorlash uchun <b>"
        + str(due_count)
        + " ta savol</b> kutib turibdi.\n\n"
        "Bu savollarda ilgari xato qilgansiz. Aynan bugun qayta "
        "ishlasangiz, ular xotirada mustahkam qoladi — "
        "bir necha daqiqa yetarli."
    )


# ---------------------------------------------------------------
#  Yuborish
# ---------------------------------------------------------------

async def send_reminders(bot, now: datetime | None = None) -> dict:
    """Muddati kelganlarga eslatma yuboradi.

    Natija: {"users": n, "sent": n, "failed": n, "skipped": bool}
    """
    if not reminders_enabled():
        log.info("Takrorlash eslatmalari o'chirilgan.")
        return {"users": 0, "sent": 0, "failed": 0, "skipped": True}

    async with SessionLocal() as session:
        rows = await due_counts(session, now)

    sent = 0
    failed = 0

    for telegram_id, _full_name, due_count in rows:
        try:
            await bot.send_message(
                telegram_id,
                reminder_text(int(due_count)),
                reply_markup=reminder_keyboard(),
            )
            sent += 1
        except Exception as exc:
            failed += 1
            log.warning(
                "Eslatma yuborilmadi (telegram_id=%s): %s",
                telegram_id,
                exc,
            )

        await asyncio.sleep(SEND_DELAY)

    return {
        "users": len(rows),
        "sent": sent,
        "failed": failed,
        "skipped": False,
    }


# ---------------------------------------------------------------
#  Kunlik sikl
# ---------------------------------------------------------------

def seconds_until_next_run(now: datetime | None = None) -> float:
    """Keyingi ishga tushishgacha necha sekund qolgani."""
    now_utc = now or datetime.now(timezone.utc)

    if now_utc.tzinfo is None:
        raise ValueError("UTC vaqt zonasi ko'rsatilishi kerak")

    offset = local_offset()
    local_now = now_utc + offset

    target = local_now.replace(
        hour=reminder_hour(),
        minute=0,
        second=0,
        microsecond=0,
    )

    if target <= local_now:
        target += timedelta(days=1)

    return (target - local_now).total_seconds()


async def reminder_loop(bot) -> None:
    """Botning fon vazifasi: har kuni bir marta eslatma yuboradi."""
    log.info(
        "Takrorlash eslatmalari sikli ishga tushdi "
        "(soat %02d:00, UTC%+d).",
        reminder_hour(),
        int(local_offset().total_seconds() // 3600),
    )

    while True:
        delay = seconds_until_next_run()

        log.info(
            "Keyingi eslatma %.0f daqiqadan keyin.",
            delay / 60,
        )

        await asyncio.sleep(delay)

        try:
            result = await send_reminders(bot)
            log.info("Takrorlash eslatmalari: %s", result)
        except Exception as exc:
            log.exception("Eslatma siklida xato: %s", exc)

        # Bir kunda ikki marta ishlamasligi uchun kichik pauza.
        await asyncio.sleep(90)

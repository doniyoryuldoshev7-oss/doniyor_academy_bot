"""To'liq bo'lmagan profillarga eslatma yuborish.

Joylashuvi: app/services/profil_eslatma.py

Ismi-familiyasi, telefoni yoki guruhi to'liq kiritilmagan o'quvchilarga
bot muntazam eslatma yuboradi va "To'ldirish" tugmasi bilan ro'yxatdan
o'tishni davom ettirtiradi.

Sozlamalar (ixtiyoriy, Railway Variables):
  PROFILE_REMINDER_ENABLED=1   0 qilinsa avtomatik eslatma o'chadi
  PROFILE_REMINDER_HOUR=11     soat (mahalliy vaqt)
  PROFILE_REMINDER_DAYS=2      necha kunda bir marta
  PROFILE_REMINDER_MAX=8       bir o'quvchiga eng ko'pi bilan nechta eslatma
"""

from __future__ import annotations

import asyncio
import html
import logging
import os
from datetime import datetime, timedelta, timezone

from aiogram import F, Router
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
)
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import select, text

from ..config import settings
from ..db import SessionLocal, engine
from ..models import User
from .kv import kv_get, kv_set

log = logging.getLogger(__name__)

router = Router()

_table_ready = False


def _enabled() -> bool:
    return os.getenv("PROFILE_REMINDER_ENABLED", "1").strip() not in {"0", "false", "no"}


def _int_env(name: str, default: int, low: int, high: int) -> int:
    try:
        return min(high, max(low, int(os.getenv(name, str(default)))))
    except ValueError:
        return default


def reminder_hour() -> int:
    return _int_env("PROFILE_REMINDER_HOUR", 11, 0, 23)


def reminder_days() -> int:
    return _int_env("PROFILE_REMINDER_DAYS", 2, 1, 30)


def reminder_max() -> int:
    return _int_env("PROFILE_REMINDER_MAX", 8, 1, 100)


def _local_now() -> datetime:
    hours = _int_env("REVIEW_REMINDER_UTC_OFFSET", 5, -12, 14)
    return datetime.now(timezone.utc) + timedelta(hours=hours)


# ---------------------------------------------------------------
#  Profil to'liqligi
# ---------------------------------------------------------------

def missing_fields(user) -> list[str]:
    """Foydalanuvchida yetishmayotgan majburiy maydonlar."""
    from ..handlers.start import valid_required

    missing = []

    if len((user.full_name or "").strip().split()) < 2:
        missing.append("ism-familiya")

    if not valid_required(user.phone_number):
        missing.append("telefon raqam")

    if not valid_required(user.group_name):
        missing.append("guruh")

    return missing


def is_incomplete(user) -> bool:
    return bool(missing_fields(user))


async def incomplete_users() -> list:
    """Profili to'liq bo'lmagan, bloklanmagan, admin bo'lmagan o'quvchilar."""
    async with SessionLocal() as session:
        users = (
            await session.scalars(
                select(User).order_by(User.created_at.desc(), User.id.desc())
            )
        ).all()

    result = []

    for user in users:
        if user.is_blocked or user.telegram_id in settings.admins:
            continue

        if user.registration_status == "rejected":
            continue

        if is_incomplete(user):
            result.append(user)

    return result


# ---------------------------------------------------------------
#  Eslatma holati (necha marta yuborilgan)
# ---------------------------------------------------------------

async def _ensure_table() -> None:
    global _table_ready

    if _table_ready:
        return

    async with engine.begin() as conn:
        await conn.execute(
            text(
                "CREATE TABLE IF NOT EXISTS profile_reminders ("
                "user_id INTEGER PRIMARY KEY, "
                "sent_count INTEGER NOT NULL DEFAULT 0, "
                "last_sent TEXT)"
            )
        )

    _table_ready = True


async def _load_state() -> dict[int, tuple[int, datetime | None]]:
    await _ensure_table()

    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                text("SELECT user_id, sent_count, last_sent FROM profile_reminders")
            )
        ).all()

    state = {}

    for user_id, count, last in rows:
        try:
            moment = datetime.fromisoformat(last) if last else None
        except ValueError:
            moment = None

        state[int(user_id)] = (int(count), moment)

    return state


async def _save_state(user_id: int, count: int) -> None:
    await _ensure_table()

    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO profile_reminders (user_id, sent_count, last_sent) "
                "VALUES (:u, :c, :t) "
                "ON CONFLICT (user_id) DO UPDATE SET "
                "sent_count = excluded.sent_count, last_sent = excluded.last_sent"
            ),
            {"u": user_id, "c": count, "t": datetime.utcnow().isoformat()},
        )


async def forget_users(user_ids: list[int]) -> None:
    """O'chirilgan o'quvchilarning eslatma yozuvlarini tozalaydi."""
    if not user_ids:
        return

    try:
        await _ensure_table()

        async with engine.begin() as conn:
            for user_id in user_ids:
                await conn.execute(
                    text("DELETE FROM profile_reminders WHERE user_id = :u"),
                    {"u": int(user_id)},
                )
    except Exception:
        log.exception("Eslatma yozuvlari tozalanmadi")


# ---------------------------------------------------------------
#  Yuborish
# ---------------------------------------------------------------

def _reminder_text(user) -> str:
    items = "\n".join("• " + item for item in missing_fields(user))

    first = (user.full_name or "").strip().split()
    greeting = f", {html.escape(first[0])}" if first else ""

    return (
        f"👋 Assalomu alaykum{greeting}!\n\n"
        "⚠️ <b>Profilingiz to'liq emas.</b> "
        "Botdan to'liq foydalanish uchun quyidagilarni kiriting:\n"
        f"{items}\n\n"
        "Pastdagi tugmani bosing, bir daqiqa ketadi."
    )


def _reminder_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✍️ Ma'lumotlarimni to'ldirish",
                    callback_data="profil:fill",
                )
            ]
        ]
    )


async def send_reminders(bot, force: bool = False) -> dict:
    """Eslatma yuboradi.

    force=False: faqat navbati kelganlarga (oraliq va chegara hisobga olinadi).
    force=True : hammasiga (admin tugmasi bilan), oraliqsiz.
    Botni bloklaganlarga qayta yuborilmaydi.
    """
    users = await incomplete_users()
    state = await _load_state()

    now = datetime.utcnow()
    interval = timedelta(days=reminder_days()) - timedelta(hours=2)
    limit = reminder_max()

    result = {
        "total": len(users),
        "sent": 0,
        "skipped": 0,
        "blocked": 0,
        "failed": 0,
    }

    for user in users:
        count, last = state.get(user.id, (0, None))

        # -1: bot bloklangan (qayta urinilmaydi)
        if count < 0:
            result["skipped"] += 1
            continue

        if not force:
            if count >= limit:
                result["skipped"] += 1
                continue

            if last is not None and now - last < interval:
                result["skipped"] += 1
                continue

        try:
            for attempt in (1, 2):
                try:
                    await bot.send_message(
                        user.telegram_id,
                        _reminder_text(user),
                        parse_mode="HTML",
                        reply_markup=_reminder_keyboard(),
                    )
                    break
                except TelegramRetryAfter as exc:
                    if attempt == 2:
                        raise
                    await asyncio.sleep(min(30, max(1, int(exc.retry_after))))

            await _save_state(user.id, count + 1)
            result["sent"] += 1

        except TelegramForbiddenError:
            await _save_state(user.id, -1)
            result["blocked"] += 1

        except TelegramBadRequest as exc:
            if any(
                part in str(exc).lower()
                for part in ("chat not found", "deactivated", "user not found")
            ):
                await _save_state(user.id, -1)
                result["blocked"] += 1
            else:
                log.warning("Profil eslatmasi: %s: %s", user.telegram_id, exc)
                result["failed"] += 1

        except (TelegramNetworkError, Exception) as exc:
            log.warning("Profil eslatmasi yuborilmadi (%s): %s", user.telegram_id, exc)
            result["failed"] += 1

        # Telegram chegarasi (soniyasiga ~30 xabar)
        await asyncio.sleep(0.05)

    return result


# ---------------------------------------------------------------
#  Kunlik sikl
# ---------------------------------------------------------------

async def _delay_until_next() -> float:
    now = _local_now()
    today = now.strftime("%Y-%m-%d")

    target = now.replace(hour=reminder_hour(), minute=0, second=0, microsecond=0)

    try:
        last = await kv_get("profile_reminder_last_run")
    except Exception:
        last = None

    # Soat o'tib ketgan, bugun hali ishlamagan: tez ishlaydi
    if now >= target and last != today:
        return 90.0

    if now >= target:
        target += timedelta(days=1)

    return max(60.0, (target - now).total_seconds())


async def profile_reminder_loop(bot) -> None:
    if not _enabled():
        log.info("Profil eslatmalari o'chirilgan (PROFILE_REMINDER_ENABLED=0).")
        return

    log.info(
        "Profil eslatmalari sikli ishga tushdi (soat %02d:00, har %s kunda).",
        reminder_hour(),
        reminder_days(),
    )

    while True:
        try:
            delay = await _delay_until_next()
            log.info("Keyingi profil eslatmasi %.0f daqiqadan keyin.", delay / 60)

            await asyncio.sleep(delay)

            result = await send_reminders(bot)

            await kv_set("profile_reminder_last_run", _local_now().strftime("%Y-%m-%d"))

            log.info("Profil eslatmalari: %s", result)

            if result["sent"] or result["blocked"]:
                try:
                    from .admin_ogoh import notify_admins

                    await notify_admins(
                        bot,
                        "📨 <b>Profil eslatmasi</b>\n"
                        f"To'liq emas: {result['total']} ta\n"
                        f"Yuborildi: {result['sent']}\n"
                        f"Botni bloklagan: {result['blocked']}",
                        "profil_eslatma",
                        cooldown=0,
                    )
                except Exception:
                    pass

        except asyncio.CancelledError:
            raise

        except Exception:
            log.exception("Profil eslatma siklida xato")
            await asyncio.sleep(600)
            continue

        await asyncio.sleep(120)


# ---------------------------------------------------------------
#  "To'ldirish" tugmasi
# ---------------------------------------------------------------

@router.callback_query(F.data == "profil:fill")
async def profile_fill(c: CallbackQuery, state: FSMContext):
    from ..handlers.start import start_registration

    async with SessionLocal() as session:
        user = await session.scalar(
            select(User).where(User.telegram_id == c.from_user.id)
        )

    if user is not None and not is_incomplete(user):
        await c.answer("Profilingiz allaqachon to'liq ✅", show_alert=True)
        return

    current = await state.get_state()

    # Boshqa jarayon (masalan, test) yo'qolib ketmasin
    if current and not str(current).startswith("RegistrationState"):
        await c.answer(
            "Avval joriy amalni tugating, so'ng /start bosing.",
            show_alert=True,
        )
        return

    await c.answer()

    await start_registration(c.message, state)

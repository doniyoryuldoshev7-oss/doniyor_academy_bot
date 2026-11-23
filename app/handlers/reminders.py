"""Takrorlash eslatmalarini boshqarish — admin uchun.

Joylashuvi: app/handlers/reminders.py

Admin panelda "\U0001F514 Eslatmalar" tugmasi, shuningdek buyruqlar:
    /eslatma_holat  -- hozir kimga eslatma ketishini ko'rsatadi (yubormaydi)
    /eslatma_yubor  -- eslatmalarni darhol yuboradi
"""

from __future__ import annotations

import html
import logging
from datetime import datetime, timezone

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from ..admin_roles import is_full_admin
from ..db import SessionLocal
from ..services.review_reminder import (
    due_counts,
    local_offset,
    reminder_hour,
    reminders_enabled,
    send_reminders,
    smart_review_enabled,
)

log = logging.getLogger(__name__)

router = Router()


# ---------------------------------------------------------------
#  Matn va tugmalar
# ---------------------------------------------------------------

def panel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="\U0001F4E8 Hozir yuborish",
                    callback_data="adm:rem_send",
                )
            ],
            [
                InlineKeyboardButton(
                    text="\U0001F504 Yangilash",
                    callback_data="adm:rem_refresh",
                )
            ],
            [
                InlineKeyboardButton(
                    text="⚙️ Admin panel",
                    callback_data="admin",
                )
            ],
        ]
    )


def _settings_lines() -> list[str]:
    offset_hours = int(local_offset().total_seconds() // 3600)

    return [
        "⚙️ Smart Takrorlash: "
        + ("yoqilgan" if smart_review_enabled() else "<b>o'chirilgan</b>"),
        "\U0001F514 Eslatmalar: "
        + ("yoqilgan" if reminders_enabled() else "<b>o'chirilgan</b>"),
        f"\U0001F553 Yuborish vaqti: {reminder_hour():02d}:00 "
        f"(UTC{offset_hours:+d})",
    ]


async def status_text() -> str:
    now = datetime.now(timezone.utc)

    async with SessionLocal() as session:
        rows = await due_counts(session, now)

    total_questions = sum(int(row[2]) for row in rows)

    lines = ["\U0001F514 <b>Takrorlash eslatmalari</b>", ""]
    lines.extend(_settings_lines())
    lines.append("")
    lines.append(
        f"\U0001F465 Eslatma kutayotgan o'quvchilar: <b>{len(rows)}</b>"
    )
    lines.append(
        f"\U0001F522 Jami muddati kelgan savollar: <b>{total_questions}</b>"
    )

    if rows:
        lines.append("")
        lines.append("<b>Eng ko'p savoli borlar:</b>")

        for telegram_id, full_name, due_count in rows[:10]:
            name = html.escape(full_name or str(telegram_id))
            lines.append(f"  • {name} — {int(due_count)} ta")

        if len(rows) > 10:
            lines.append(f"  ... va yana {len(rows) - 10} ta o'quvchi")
    else:
        lines.append("")
        lines.append(
            "<i>Hozircha hech kimda muddati kelgan takrorlash yo'q. "
            "O'quvchilar test ishlab, xato qilgandan keyin "
            "bu ro'yxat to'la boshlaydi.</i>"
        )

    if not smart_review_enabled():
        lines.append("")
        lines.append(
            "⚠️ <b>SMART_REVIEW_ENABLED=1</b> qo'yilmagan — "
            "eslatma yuborilmaydi."
        )

    lines.append("")
    lines.append(
        f"<i>Oxirgi tekshiruv: "
        f"{(now + local_offset()).strftime('%d.%m.%Y %H:%M')}</i>"
    )

    return "\n".join(lines)


async def _send_and_report(bot) -> str:
    result = await send_reminders(bot)

    if result.get("skipped"):
        return (
            "⏸ <b>Eslatmalar o'chirilgan</b>\n\n"
            + "\n".join(_settings_lines())
        )

    return (
        "✅ <b>Eslatmalar yuborildi</b>\n\n"
        f"\U0001F465 O'quvchilar: <b>{result['users']}</b>\n"
        f"\U0001F4E8 Yuborildi: <b>{result['sent']}</b>\n"
        f"❌ Yuborilmadi: <b>{result['failed']}</b>\n\n"
        "<i>Yuborilmaganlar — botni bloklagan yoki chatni "
        "o'chirgan o'quvchilar.</i>"
    )


# ---------------------------------------------------------------
#  Admin paneldagi tugma
# ---------------------------------------------------------------

@router.callback_query(F.data.in_({"adm:reminders", "adm:rem_refresh"}))
async def reminders_panel(c: CallbackQuery):
    if not is_full_admin(c.from_user.id):
        await c.answer("Ruxsat yo'q.", show_alert=True)
        return

    text = await status_text()

    try:
        await c.message.edit_text(text, reply_markup=panel_keyboard())
    except Exception as exc:
        if "message is not modified" not in str(exc):
            raise

    await c.answer()


@router.callback_query(F.data == "adm:rem_send")
async def reminders_send(c: CallbackQuery):
    if not is_full_admin(c.from_user.id):
        await c.answer("Ruxsat yo'q.", show_alert=True)
        return

    await c.answer("Yuborilmoqda...")

    try:
        text = await _send_and_report(c.bot)
    except Exception as exc:
        log.exception("Eslatma yuborishda xato: %s", exc)

        await c.message.answer(
            "⚠️ Xato yuz berdi. Loglarni tekshiring."
        )
        return

    await c.message.edit_text(text, reply_markup=panel_keyboard())


# ---------------------------------------------------------------
#  Buyruqlar
# ---------------------------------------------------------------

@router.message(Command("eslatma_holat"))
async def reminder_status(m: Message):
    if not is_full_admin(m.from_user.id):
        return

    await m.answer(await status_text(), reply_markup=panel_keyboard())


@router.message(Command("eslatma_yubor"))
async def reminder_send_now(m: Message):
    if not is_full_admin(m.from_user.id):
        return

    notice = await m.answer("⏳ Eslatmalar yuborilmoqda...")

    try:
        text = await _send_and_report(m.bot)
    except Exception as exc:
        log.exception("Eslatma yuborishda xato: %s", exc)

        await notice.edit_text(
            "⚠️ Xato yuz berdi. Loglarni tekshiring."
        )
        return

    await notice.edit_text(text, reply_markup=panel_keyboard())

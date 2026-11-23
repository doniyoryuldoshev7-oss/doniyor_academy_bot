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
from sqlalchemy import select, update

from ..admin_roles import is_full_admin
from ..db import SessionLocal
from ..models import Question, SmartReview, User
from ..services.smart_review_queue import (
    image_missing,
    unreviewable_reason,
)
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
                    text="\U0001F9EA Sinov: muddatni hozirga surish",
                    callback_data="adm:rem_test",
                )
            ],
            [
                InlineKeyboardButton(
                    text="\U0001F50D Mening navbatim",
                    callback_data="adm:rem_debug",
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

    task_line = ""

    try:
        from ..services.assignment_reminder import run_daily

        task_result = await run_daily(bot)

        if not task_result.get("skipped"):
            task_line = (
                "\n\n\U0001F4CB <b>Topshiriqlar</b>\n"
                f"\u23F0 Muddat eslatmasi: <b>{task_result.get('sent', 0)}</b> ta\n"
                f"\U0001F4CA Ustozga hisobot: <b>{task_result.get('summaries', 0)}</b> ta"
            )
    except Exception as exc:
        log.exception("Topshiriq eslatmasi xatosi: %s", exc)

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
        + task_line
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


# ---------------------------------------------------------------
#  Sinov uchun: o'z savollaring muddatini hozirga surish
# ---------------------------------------------------------------

async def _pull_reviews_forward(telegram_id: int) -> int:
    """Faqat so'rayotgan adminning o'z yozuvlarini oldinga suradi.

    Boshqa o'quvchilarning jadvaliga tegmaydi — aks holda ularning
    takrorlash oralig'i buzilgan bo'lardi.
    """
    now = datetime.now(timezone.utc)

    async with SessionLocal() as session:
        user = await session.scalar(
            select(User).where(User.telegram_id == telegram_id)
        )

        if user is None:
            return -1

        result = await session.execute(
            update(SmartReview)
            .where(
                SmartReview.user_id == user.id,
                SmartReview.status == "active",
            )
            .values(next_review_at=now)
        )

        await session.commit()

        return int(result.rowcount or 0)


def _test_result_text(count: int) -> str:
    if count < 0:
        return (
            "\u26A0\uFE0F Sizning o'quvchi yozuvingiz topilmadi. "
            "Avval botdan ro'yxatdan o'ting."
        )

    if count == 0:
        return (
            "\U0001F9EA <b>Sinov</b>\n\n"
            "Sizda takrorlash navbatida savol yo'q.\n\n"
            "Sinab ko'rish uchun: <b>Fanlar</b> dan test ishlang va "
            "bir-ikki savolga <b>ataylab xato</b> javob bering. "
            "Keyin shu tugmani qayta bosing."
        )

    return (
        "\U0001F9EA <b>Sinov tayyor</b>\n\n"
        f"<b>{count}</b> ta savolning muddati hozirgi vaqtga surildi.\n\n"
        "Endi <b>\U0001F9E0 Smart Takrorlash</b> ni oching — "
        "savollar darhol chiqadi."
    )


@router.callback_query(F.data == "adm:rem_test")
async def reminders_test(c: CallbackQuery):
    if not is_full_admin(c.from_user.id):
        await c.answer("Ruxsat yo'q.", show_alert=True)
        return

    await c.answer("Bajarilmoqda...")

    try:
        count = await _pull_reviews_forward(c.from_user.id)
    except Exception as exc:
        log.exception("Sinov xatosi: %s", exc)

        await c.message.answer(
            "\u26A0\uFE0F Xato yuz berdi. Loglarni tekshiring."
        )
        return

    await c.message.edit_text(
        _test_result_text(count),
        reply_markup=panel_keyboard(),
    )


@router.message(Command("sr_sinov"))
async def smart_review_test_command(m: Message):
    if not is_full_admin(m.from_user.id):
        return

    try:
        count = await _pull_reviews_forward(m.from_user.id)
    except Exception as exc:
        log.exception("Sinov xatosi: %s", exc)

        await m.answer("\u26A0\uFE0F Xato yuz berdi. Loglarni tekshiring.")
        return

    await m.answer(_test_result_text(count), reply_markup=panel_keyboard())


# ---------------------------------------------------------------
#  Tekshiruv: navbatda nima turibdi va nega chiqmayapti
# ---------------------------------------------------------------

async def queue_report(telegram_id: int, limit: int = 15) -> str:
    """Adminning o'z takrorlash navbatini sabablari bilan ko'rsatadi."""
    now = datetime.now(timezone.utc)
    offset = local_offset()

    async with SessionLocal() as session:
        user = await session.scalar(
            select(User).where(User.telegram_id == telegram_id)
        )

        if user is None:
            return "\u26A0\uFE0F O'quvchi yozuvingiz topilmadi."

        rows = list(
            await session.execute(
                select(SmartReview, Question)
                .outerjoin(
                    Question,
                    Question.id == SmartReview.question_id,
                )
                .where(SmartReview.user_id == user.id)
                .order_by(SmartReview.id)
            )
        )

    if not rows:
        return (
            "\U0001F50D <b>Mening navbatim</b>\n\n"
            "Takrorlash yozuvlari yo'q. Test ishlab, xato javob "
            "berganingizdan keyin paydo bo'ladi."
        )

    ready = 0
    blocked = 0
    later = 0
    mastered = 0

    lines = ["\U0001F50D <b>Mening navbatim</b>", ""]
    details = []

    for review, question in rows:
        reason = unreviewable_reason(question)

        if review.status == "mastered":
            mastered += 1
            mark = "\U0001F3C6 mustahkamlangan"
        elif reason is not None:
            blocked += 1
            mark = "\u274C " + reason
        elif review.next_review_at is None:
            blocked += 1
            mark = "\u274C muddat belgilanmagan"
        elif review.next_review_at > now:
            later += 1
            when = (review.next_review_at + offset).strftime("%d.%m %H:%M")
            mark = "\U0001F552 " + when
        else:
            ready += 1
            mark = "\u2705 bugun chiqadi"

            if image_missing(question):
                mark += " (rasm fayli yo'q)"

        mode = (question.question_mode if question else None) or "—"

        details.append(
            f"  \u2022 #{review.question_id} [{mode}] {mark}"
        )

    lines.append(f"\u2705 Bugun chiqadi: <b>{ready}</b>")
    lines.append(f"\u274C Chiqa olmaydi: <b>{blocked}</b>")
    lines.append(f"\U0001F552 Keyinroq: <b>{later}</b>")
    lines.append(f"\U0001F3C6 Mustahkamlangan: <b>{mastered}</b>")
    lines.append("")
    lines.extend(details[:limit])

    if len(details) > limit:
        lines.append(f"  ... va yana {len(details) - limit} ta yozuv")

    if blocked:
        lines.append("")
        lines.append(
            "<i>\"Chiqa olmaydi\" — bu savollar Smart Takrorlashda "
            "ko'rsatilmaydi, lekin boshqaruv panelidagi eski hisobga "
            "kirib ketardi. Masalan ochiq (yozma) savollar: "
            "takrorlash faqat A/B/C/D bilan ishlaydi.</i>"
        )

    return "\n".join(lines)


@router.callback_query(F.data == "adm:rem_debug")
async def reminders_debug(c: CallbackQuery):
    if not is_full_admin(c.from_user.id):
        await c.answer("Ruxsat yo'q.", show_alert=True)
        return

    await c.answer("Tekshirilmoqda...")

    try:
        text = await queue_report(c.from_user.id)
    except Exception as exc:
        log.exception("Navbat tekshiruvi xatosi: %s", exc)

        await c.message.answer(
            "\u26A0\uFE0F Xato yuz berdi. Loglarni tekshiring."
        )
        return

    await c.message.edit_text(text, reply_markup=panel_keyboard())


@router.message(Command("sr_navbat"))
async def queue_report_command(m: Message):
    if not is_full_admin(m.from_user.id):
        return

    try:
        text = await queue_report(m.from_user.id)
    except Exception as exc:
        log.exception("Navbat tekshiruvi xatosi: %s", exc)

        await m.answer("\u26A0\uFE0F Xato yuz berdi. Loglarni tekshiring.")
        return

    await m.answer(text, reply_markup=panel_keyboard())

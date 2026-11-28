"""Admin paneli: o'quvchilarni belgilab o'chirish va to'liq bo'lmagan
profillar bilan ishlash.

Joylashuvi: app/handlers/admin/oquvchi_boshqaruv.py
"""

from __future__ import annotations

import html
import logging
from datetime import datetime

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import delete, select, text

from ...config import settings
from ...db import SessionLocal
from ...models import (
    AnswerLog,
    Assignment,
    GroupQuizAnswer,
    GroupQuizParticipant,
    QuestionEditorGrant,
    SmartReview,
    TestAttempt,
    User,
)
from ._common import is_admin

log = logging.getLogger(__name__)

router = Router()

PAGE_SIZE = 10
MAX_NAMES_IN_CONFIRM = 25
CHUNK = 400

FILTERS = {
    "all": "Hammasi",
    "inc": "To'liq emas",
    "idle": "Testsiz",
    "blk": "Bloklagan",
}


# ---------------------------------------------------------------
#  Yordamchilar
# ---------------------------------------------------------------

def _short(value: str, limit: int = 34) -> str:
    value = (value or "Nomsiz").strip()
    return value if len(value) <= limit else value[: limit - 1].rstrip() + "…"


async def _safe_edit(message, text_value: str, markup) -> None:
    from aiogram.exceptions import TelegramBadRequest

    try:
        await message.edit_text(text_value, reply_markup=markup, parse_mode="HTML")
    except TelegramBadRequest as exc:
        if "message is not modified" not in str(exc).lower():
            raise


async def _candidates(filter_key: str) -> list:
    """Filtr bo'yicha o'quvchilar (adminlar ro'yxatga kirmaydi)."""
    from ...services.profil_eslatma import blocked_user_ids, is_incomplete

    async with SessionLocal() as session:
        users = (
            await session.scalars(
                select(User).order_by(User.created_at.desc(), User.id.desc())
            )
        ).all()

    blocked = await blocked_user_ids() if filter_key == "blk" else set()

    result = []

    for user in users:
        if user.telegram_id in settings.admins:
            continue

        if filter_key == "inc" and not is_incomplete(user):
            continue

        if filter_key == "idle" and int(user.total_tests or 0) > 0:
            continue

        if filter_key == "blk" and user.id not in blocked:
            continue

        result.append(user)

    return result


def _chunks(items: list, size: int = CHUNK):
    for index in range(0, len(items), size):
        yield items[index : index + size]


LRM = "\u200e"  # arabcha/RTL ismlar qatorni buzmasligi uchun


async def _excluded_incomplete(included_ids: set) -> list[tuple]:
    """Profili to'liq emas, lekin eslatma yuborilmaydiganlar va sababi."""
    from ...services.profil_eslatma import is_incomplete

    async with SessionLocal() as session:
        users = (await session.scalars(select(User))).all()

    result = []

    for user in users:
        if user.id in included_ids or not is_incomplete(user):
            continue

        if user.telegram_id in settings.admins:
            reason = "admin"
        elif user.is_blocked:
            reason = "bloklangan"
        elif user.registration_status == "rejected":
            reason = "rad etilgan"
        else:
            reason = "boshqa"

        result.append((user, reason))

    return result


# ---------------------------------------------------------------
#  To'liq bo'lmagan profillar
# ---------------------------------------------------------------

@router.callback_query(F.data == "adm:incomplete")
async def incomplete_overview(c: CallbackQuery):
    if not is_admin(c.from_user.id):
        return

    from ...services.profil_eslatma import (
        _enabled,
        blocked_user_ids,
        incomplete_users,
        missing_fields,
        reminder_days,
        reminder_hour,
        reminder_max,
    )

    users = await incomplete_users()
    blocked_count = len(await blocked_user_ids())

    lines = [
        "<b>⚠️ PROFILI TO'LIQ EMAS</b>",
        "",
        f"Jami: <b>{len(users)}</b> ta o'quvchi",
        "",
    ]

    for user in users[:15]:
        lines.append(
            f"{LRM}• {LRM}{html.escape(_short(user.full_name, 30))}{LRM} — "
            + html.escape(", ".join(missing_fields(user)))
        )

    if len(users) > 15:
        lines.append(f"… va yana {len(users) - 15} ta")

    excluded = await _excluded_incomplete({user.id for user in users})

    if excluded:
        lines.append("")
        lines.append(
            "<i>Eslatma yuborilmaydi (hisobga olinmadi):</i>"
        )

        for user, reason in excluded[:10]:
            lines.append(
                f"{LRM}• {LRM}{html.escape(_short(user.full_name, 30))}{LRM}"
                f" — {reason}"
            )

        if len(excluded) > 10:
            lines.append(f"… va yana {len(excluded) - 10} ta")

    lines.append("")

    if _enabled():
        lines.append(
            f"🔔 Avtomatik eslatma: har {reminder_days()} kunda soat "
            f"{reminder_hour():02d}:00 da, bir o'quvchiga ko'pi bilan "
            f"{reminder_max()} marta."
        )
    else:
        lines.append("🔕 Avtomatik eslatma o'chirilgan.")

    rows = []

    if users:
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"📨 Hozir eslatma yuborish ({len(users)})",
                    callback_data="adm:inc_send",
                )
            ]
        )
        rows.append(
            [
                InlineKeyboardButton(
                    text="🗑 Shulardan tanlab o'chirish",
                    callback_data="adm:udel:new:inc",
                )
            ]
        )

    rows.append(
        [
            InlineKeyboardButton(
                text=f"🚫 Botni bloklaganlar ({blocked_count})",
                callback_data="adm:blocked",
            )
        ]
    )

    rows.append(
        [InlineKeyboardButton(text="⬅️ Foydalanuvchilar", callback_data="adm:users")]
    )

    await _safe_edit(c.message, "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows))
    await c.answer()


@router.callback_query(F.data == "adm:inc_send")
async def incomplete_send_ask(c: CallbackQuery):
    if not is_admin(c.from_user.id):
        return

    from ...services.profil_eslatma import incomplete_users

    count = len(await incomplete_users())

    if not count:
        await c.answer("Profili to'liq bo'lmagan o'quvchi yo'q.", show_alert=True)
        return

    await _safe_edit(
        c.message,
        f"📨 <b>{count}</b> ta o'quvchiga profilni to'ldirish eslatmasi "
        "yuboriladi.\n\nDavom etamizmi?",
        InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="✅ Yuborish", callback_data="adm:inc_send_yes"),
                    InlineKeyboardButton(text="❌ Bekor qilish", callback_data="adm:incomplete"),
                ]
            ]
        ),
    )
    await c.answer()


@router.callback_query(F.data == "adm:inc_send_yes")
async def incomplete_send_go(c: CallbackQuery):
    if not is_admin(c.from_user.id):
        return

    from ...services.profil_eslatma import send_reminders

    await c.answer("Yuborilmoqda...")
    await _safe_edit(c.message, "⏳ Eslatmalar yuborilmoqda...", None)

    result = await send_reminders(c.bot, force=True)

    await _safe_edit(
        c.message,
        "✅ <b>Eslatma yuborildi</b>\n\n"
        f"To'liq emas: {result['total']}\n"
        f"Yuborildi: <b>{result['sent']}</b>\n"
        f"Botni bloklagan: {result['blocked']}\n"
        f"Yuborilmadi: {result['failed']}",
        InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Orqaga", callback_data="adm:incomplete")]
            ]
        ),
    )


# ---------------------------------------------------------------
#  Botni bloklaganlar
# ---------------------------------------------------------------

async def _blocked_users() -> list:
    return await _candidates("blk")


def _contact_line(user) -> str:
    parts = [LRM + html.escape(_short(user.full_name, 30)) + LRM]

    if user.phone_number:
        parts.append("📱 " + html.escape(user.phone_number))

    if user.group_name:
        parts.append("👥 " + html.escape(_short(user.group_name, 15)))

    if user.username:
        parts.append("@" + html.escape(user.username))

    return " — ".join(parts)


@router.callback_query(F.data == "adm:blocked")
async def blocked_overview(c: CallbackQuery):
    if not is_admin(c.from_user.id):
        return

    users = await _blocked_users()

    lines = [
        "<b>🚫 BOTNI BLOKLAGANLAR</b>",
        "",
        f"Jami: <b>{len(users)}</b> ta",
        "",
    ]

    for user in users[:30]:
        lines.append(LRM + "• " + _contact_line(user))

    if len(users) > 30:
        lines.append(f"… va yana {len(users) - 30} ta (to'liq ro'yxat fayli pastda)")

    if not users:
        lines.append(
            "Hozircha bloklagan o'quvchi aniqlanmagan.\n"
            "Tekshirish tugmasi hammani yashirin tekshiradi."
        )
    else:
        lines.append("")
        lines.append(
            "Ular botga xabar yoza olmaydi, shuning uchun "
            "guruh orqali bog'laning. Botni qayta ishga tushirsa, "
            "tekshirishda blok belgisi o'zi olib tashlanadi."
        )

    rows = [
        [
            InlineKeyboardButton(
                text="🔍 Hammasini tekshirib yangilash",
                callback_data="adm:blocked_scan",
            )
        ]
    ]

    if users:
        rows.append(
            [
                InlineKeyboardButton(
                    text="📄 To'liq ro'yxat (fayl)",
                    callback_data="adm:blocked_csv",
                )
            ]
        )
        rows.append(
            [
                InlineKeyboardButton(
                    text="🗑 Shulardan tanlab o'chirish",
                    callback_data="adm:udel:new:blk",
                )
            ]
        )

    rows.append(
        [InlineKeyboardButton(text="⬅️ Orqaga", callback_data="adm:incomplete")]
    )

    await _safe_edit(c.message, "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows))
    await c.answer()


@router.callback_query(F.data == "adm:blocked_scan")
async def blocked_scan(c: CallbackQuery):
    if not is_admin(c.from_user.id):
        return

    from ...services.profil_eslatma import scan_blocked, scan_in_progress

    if scan_in_progress():
        await c.answer("Tekshiruv allaqachon ketyapti. Kuting.", show_alert=True)
        return

    await c.answer("Tekshirilmoqda...")
    await _safe_edit(
        c.message,
        "⏳ <b>Tekshirilmoqda...</b>\n\nO'quvchilarga hech qanday xabar "
        "bormaydi. O'quvchilar ko'p bo'lsa, bir necha daqiqa ketishi mumkin.",
        None,
    )

    result = await scan_blocked(c.bot)

    if result.get("busy"):
        return

    await _safe_edit(
        c.message,
        "✅ <b>Tekshiruv tugadi</b>\n\n"
        f"Tekshirildi: {result['total']} ta\n"
        f"Botni bloklagan: <b>{result['blocked']}</b> "
        f"(yangi aniqlangan: {result['new_blocked']})\n"
        f"Qaytib kelgan: {result['restored']}\n"
        f"Tekshirib bo'lmadi: {result['failed']}",
        InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🚫 Ro'yxatni ko'rish", callback_data="adm:blocked")]
            ]
        ),
    )


@router.callback_query(F.data == "adm:blocked_csv")
async def blocked_csv(c: CallbackQuery):
    if not is_admin(c.from_user.id):
        return

    import csv
    import io

    from aiogram.types import BufferedInputFile

    users = await _blocked_users()

    if not users:
        await c.answer("Ro'yxat bo'sh.", show_alert=True)
        return

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["Ism-familiya", "Guruh", "Username", "Telegram ID", "Ro'yxatdan o'tgan"])

    for user in users:
        writer.writerow(
            [
                user.full_name or "",
                user.group_name or "",
                ("@" + user.username) if user.username else "",
                user.telegram_id,
                user.created_at.strftime("%d.%m.%Y") if user.created_at else "",
            ]
        )

    # utf-8-sig: Excel o'zbekcha harflarni to'g'ri ochadi
    data = buffer.getvalue().encode("utf-8-sig")

    await c.message.answer_document(
        BufferedInputFile(data, filename="botni_bloklaganlar.csv"),
        caption=f"🚫 Botni bloklaganlar: {len(users)} ta",
    )
    await c.answer()


# ---------------------------------------------------------------
#  Belgilab o'chirish
# ---------------------------------------------------------------

async def _render_selection(c: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()

    filter_key = data.get("udel_f", "all")
    page = int(data.get("udel_p", 0))
    selected = set(data.get("udel_sel", []))

    users = await _candidates(filter_key)

    last_page = max(0, (len(users) - 1) // PAGE_SIZE)
    page = min(max(0, page), last_page)

    await state.update_data(udel_p=page)

    chunk = users[page * PAGE_SIZE : (page + 1) * PAGE_SIZE]

    rows = [
        [
            InlineKeyboardButton(
                text=("• " if key == filter_key else "") + title,
                callback_data=f"adm:udel:f:{key}",
            )
            for key, title in FILTERS.items()
        ]
    ]

    for user in chunk:
        mark = "☑" if user.id in selected else "☐"
        group = f" · {_short(user.group_name, 12)}" if user.group_name else ""

        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{mark} {_short(user.full_name, 30)}{group}",
                    callback_data=f"adm:udel:t:{user.id}",
                )
            ]
        )

    navigation = []

    if page > 0:
        navigation.append(
            InlineKeyboardButton(text="⬅️ Oldingi", callback_data=f"adm:udel:p:{page - 1}")
        )

    if page < last_page:
        navigation.append(
            InlineKeyboardButton(text="Keyingi ➡️", callback_data=f"adm:udel:p:{page + 1}")
        )

    if navigation:
        rows.append(navigation)

    rows.append(
        [
            InlineKeyboardButton(text="☑ Sahifani belgilash", callback_data="adm:udel:pg"),
            InlineKeyboardButton(text="☐ Belgini olib tashlash", callback_data="adm:udel:clr"),
        ]
    )

    rows.append(
        [
            InlineKeyboardButton(
                text=f"🗑 O'chirish ({len(selected)})",
                callback_data="adm:udel:go",
            )
        ]
    )

    rows.append(
        [InlineKeyboardButton(text="⬅️ Foydalanuvchilar", callback_data="adm:users")]
    )

    body = (
        "<b>🗑 O'QUVCHILARNI O'CHIRISH</b>\n\n"
        f"Filtr: <b>{FILTERS.get(filter_key, '')}</b> — {len(users)} ta\n"
        f"Belgilangan: <b>{len(selected)}</b> ta\n"
        f"Sahifa: {page + 1}/{last_page + 1}\n\n"
        "O'chiriladigan o'quvchilarni bosib belgilang. Belgilar "
        "sahifa va filtr almashganda saqlanadi."
    )

    if not users:
        body += "\n\n<i>Bu filtrda o'quvchi yo'q.</i>"

    await _safe_edit(c.message, body, InlineKeyboardMarkup(inline_keyboard=rows))


@router.callback_query(F.data == "adm:udel:open")
async def udel_open(c: CallbackQuery, state: FSMContext):
    if not is_admin(c.from_user.id):
        return

    await state.update_data(udel_f="all", udel_p=0, udel_sel=[])
    await _render_selection(c, state)
    await c.answer()


@router.callback_query(F.data.startswith("adm:udel:new:"))
async def udel_new(c: CallbackQuery, state: FSMContext):
    """Boshqa bo'limdan kelganda: yangi tanlov (eski belgilar tozalanadi)."""
    if not is_admin(c.from_user.id):
        return

    key = c.data.rsplit(":", 1)[-1]

    if key not in FILTERS:
        await c.answer("Filtr noto'g'ri.", show_alert=True)
        return

    await state.update_data(udel_f=key, udel_p=0, udel_sel=[])
    await _render_selection(c, state)
    await c.answer()


@router.callback_query(F.data.startswith("adm:udel:f:"))
async def udel_filter(c: CallbackQuery, state: FSMContext):
    if not is_admin(c.from_user.id):
        return

    key = c.data.rsplit(":", 1)[-1]

    if key not in FILTERS:
        await c.answer("Filtr noto'g'ri.", show_alert=True)
        return

    data = await state.get_data()

    await state.update_data(
        udel_f=key,
        udel_p=0,
        udel_sel=data.get("udel_sel", []),
    )
    await _render_selection(c, state)
    await c.answer()


@router.callback_query(F.data.startswith("adm:udel:p:"))
async def udel_page(c: CallbackQuery, state: FSMContext):
    if not is_admin(c.from_user.id):
        return

    try:
        page = int(c.data.rsplit(":", 1)[-1])
    except ValueError:
        await c.answer("Sahifa noto'g'ri.", show_alert=True)
        return

    await state.update_data(udel_p=page)
    await _render_selection(c, state)
    await c.answer()


@router.callback_query(F.data.startswith("adm:udel:t:"))
async def udel_toggle(c: CallbackQuery, state: FSMContext):
    if not is_admin(c.from_user.id):
        return

    try:
        user_id = int(c.data.rsplit(":", 1)[-1])
    except ValueError:
        await c.answer("Noto'g'ri so'rov.", show_alert=True)
        return

    data = await state.get_data()
    selected = set(data.get("udel_sel", []))

    if user_id in selected:
        selected.discard(user_id)
    else:
        selected.add(user_id)

    await state.update_data(udel_sel=sorted(selected))
    await _render_selection(c, state)
    await c.answer()


@router.callback_query(F.data == "adm:udel:pg")
async def udel_select_page(c: CallbackQuery, state: FSMContext):
    if not is_admin(c.from_user.id):
        return

    data = await state.get_data()

    users = await _candidates(data.get("udel_f", "all"))
    page = int(data.get("udel_p", 0))
    chunk = users[page * PAGE_SIZE : (page + 1) * PAGE_SIZE]

    selected = set(data.get("udel_sel", []))
    selected.update(user.id for user in chunk)

    await state.update_data(udel_sel=sorted(selected))
    await _render_selection(c, state)
    await c.answer()


@router.callback_query(F.data == "adm:udel:clr")
async def udel_clear(c: CallbackQuery, state: FSMContext):
    if not is_admin(c.from_user.id):
        return

    await state.update_data(udel_sel=[])
    await _render_selection(c, state)
    await c.answer("Belgilar olib tashlandi.")


@router.callback_query(F.data == "adm:udel:back")
async def udel_back(c: CallbackQuery, state: FSMContext):
    if not is_admin(c.from_user.id):
        return

    await _render_selection(c, state)
    await c.answer()


async def _selected_users(selected_ids: list[int]) -> list:
    """Belgilanganlardan hozir ham mavjud va admin bo'lmaganlari."""
    found = []

    async with SessionLocal() as session:
        for part in _chunks(list(selected_ids)):
            found.extend(
                (await session.scalars(select(User).where(User.id.in_(part)))).all()
            )

    return [user for user in found if user.telegram_id not in settings.admins]


@router.callback_query(F.data == "adm:udel:go")
async def udel_confirm(c: CallbackQuery, state: FSMContext):
    if not is_admin(c.from_user.id):
        return

    data = await state.get_data()
    selected = list(data.get("udel_sel", []))

    if not selected:
        await c.answer("Avval o'chiriladigan o'quvchilarni belgilang.", show_alert=True)
        return

    users = await _selected_users(selected)

    if not users:
        await c.answer("Belgilangan o'quvchilar topilmadi.", show_alert=True)
        return

    names = [
        f"• {html.escape(_short(user.full_name, 40))}"
        + (f" ({html.escape(_short(user.group_name, 15))})" if user.group_name else "")
        for user in users[:MAX_NAMES_IN_CONFIRM]
    ]

    if len(users) > MAX_NAMES_IN_CONFIRM:
        names.append(f"… va yana {len(users) - MAX_NAMES_IN_CONFIRM} ta")

    await _safe_edit(
        c.message,
        f"⚠️ <b>{len(users)} ta o'quvchi o'chiriladi</b>\n\n"
        + "\n".join(names)
        + "\n\nBularning barcha test natijalari, xatolari va tarixi "
        "o'chadi. <b>Qaytarib bo'lmaydi.</b>\n"
        "Xavfsizlik uchun avval sizga baza zaxirasi yuboriladi.",
        InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="✅ Ha, o'chirish", callback_data="adm:udel:yes"),
                    InlineKeyboardButton(text="❌ Yo'q", callback_data="adm:udel:back"),
                ]
            ]
        ),
    )
    await c.answer()


async def _delete_users(users: list) -> int:
    """O'quvchilarni va ularga bog'liq hamma ma'lumotni o'chiradi."""
    ids = [user.id for user in users]
    tids = [user.telegram_id for user in users]

    async with SessionLocal() as session:
        for part in _chunks(ids):
            attempt_ids = list(
                (
                    await session.scalars(
                        select(TestAttempt.id).where(TestAttempt.user_id.in_(part))
                    )
                ).all()
            )

            for sub in _chunks(attempt_ids):
                await session.execute(
                    delete(AnswerLog).where(AnswerLog.attempt_id.in_(sub))
                )

            participant_ids = list(
                (
                    await session.scalars(
                        select(GroupQuizParticipant.id).where(
                            GroupQuizParticipant.user_id.in_(part)
                        )
                    )
                ).all()
            )

            for sub in _chunks(participant_ids):
                await session.execute(
                    delete(GroupQuizAnswer).where(GroupQuizAnswer.participant_id.in_(sub))
                )

            await session.execute(
                delete(GroupQuizParticipant).where(GroupQuizParticipant.user_id.in_(part))
            )
            await session.execute(delete(TestAttempt).where(TestAttempt.user_id.in_(part)))
            await session.execute(delete(SmartReview).where(SmartReview.user_id.in_(part)))
            await session.execute(
                delete(QuestionEditorGrant).where(QuestionEditorGrant.user_id.in_(part))
            )
            await session.execute(delete(Assignment).where(Assignment.user_id.in_(part)))
            await session.execute(delete(User).where(User.id.in_(part)))

        await session.commit()

    # Qolgan izlar: ochiq holatlar va eslatma yozuvlari
    try:
        from ...services.profil_eslatma import forget_users

        await forget_users(ids)
    except Exception:
        log.exception("Eslatma yozuvlari tozalanmadi")

    try:
        from ...db import engine

        async with engine.begin() as conn:
            for tid in tids:
                await conn.execute(
                    text("DELETE FROM fsm_storage WHERE key LIKE :pattern"),
                    {"pattern": f"%:{int(tid)}:{int(tid)}:%"},
                )
    except Exception:
        # jadval bo'lmasligi yoki xotirada saqlash: zarar yo'q
        pass

    return len(ids)


@router.callback_query(F.data == "adm:udel:yes")
async def udel_go(c: CallbackQuery, state: FSMContext):
    if not is_admin(c.from_user.id):
        return

    data = await state.get_data()
    selected = list(data.get("udel_sel", []))

    users = await _selected_users(selected)

    if not users:
        await c.answer("Belgilangan o'quvchilar topilmadi.", show_alert=True)
        return

    await c.answer("Zaxira tayyorlanmoqda...")
    await _safe_edit(c.message, "⏳ Avval baza zaxirasi yuboriladi, so'ng o'chiriladi...", None)

    # Xavfsizlik: zaxira yuborilmasa, hech narsa o'chirilmaydi
    try:
        from ...services.baza_zaxira import send_backup

        backup = await send_backup(c.bot, targets=[c.from_user.id])
        backup_ok = bool(backup.get("sent_to"))
    except Exception:
        log.exception("O'chirishdan oldingi zaxira xatosi")
        backup_ok = False

    back = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Orqaga", callback_data="adm:udel:back")]
        ]
    )

    if not backup_ok:
        await _safe_edit(
            c.message,
            "❌ Zaxira nusxa yuborilmadi, shuning uchun <b>hech kim o'chirilmadi</b>.\n"
            "Birozdan keyin qayta urinib ko'ring.",
            back,
        )
        return

    try:
        count = await _delete_users(users)
    except Exception as exc:
        log.exception("O'quvchilarni o'chirishda xato")
        await _safe_edit(
            c.message,
            "❌ O'chirishda xato yuz berdi, ma'lumotlar o'zgarmagan bo'lishi kerak.\n"
            f"<code>{html.escape(type(exc).__name__)}</code>",
            back,
        )
        return

    await state.update_data(udel_sel=[], udel_p=0)

    # Jurnal: kim, nechta, qachon
    try:
        from ...services.admin_ogoh import notify_admins

        who = c.from_user.full_name or str(c.from_user.id)
        listing = ", ".join(_short(user.full_name, 25) for user in users[:10])

        if len(users) > 10:
            listing += f" va yana {len(users) - 10} ta"

        await notify_admins(
            c.bot,
            "🗑 <b>O'quvchilar o'chirildi</b>\n"
            f"👤 {html.escape(who)}\n"
            f"🔢 {count} ta: {html.escape(listing)}\n"
            f"🕒 {datetime.now().strftime('%d.%m.%Y %H:%M')}",
            f"udel:{datetime.now().timestamp()}",
            cooldown=0,
        )
    except Exception:
        pass

    await _safe_edit(
        c.message,
        f"✅ <b>{count} ta o'quvchi o'chirildi.</b>\n\n"
        "Baza zaxirasi sizga yuborilgan (kerak bo'lsa tiklash mumkin).",
        InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Foydalanuvchilar", callback_data="adm:users")]
            ]
        ),
    )

"""Imtihon rejimi: o'quvchi tomoni (boshlash, yechish, natija, tahlil).

Joylashuvi: app/handlers/imtihon.py
"""

from __future__ import annotations

import html
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from ..db import SessionLocal
from ..models import Exam, ExamAttempt, User
from ..services.imtihon import (
    MAX_ANSWER_LEN,
    SCALE_MAX,
    MIN_STATS,
    answers_from_view,
    answers_to_view,
    attempt_answers,
    attempt_view,
    exam_is_open,
    exam_results,
    exam_targets_user,
    finish_attempt,
    item_answered,
    load_items,
    score_attempt,
    unit_correct,
    build_units,
)
from ..services.student_report import fmt_datetime

log = logging.getLogger(__name__)

router = Router()


async def _tl_items(items, tg):
    """Imtihon savollarini foydalanuvchi tiliga moslaydi (xato bo'lsa, o'zbekcha qoladi)."""
    try:
        from ..qlang import localize_items

        return await localize_items(items, tg)
    except Exception:
        return items

REVIEW_PAGE = 20


class ExamTake(StatesGroup):
    typing = State()


def esc(value) -> str:
    return html.escape(str(value or ""))


def short(value: str, limit: int) -> str:
    value = (value or "").strip()
    return value if len(value) <= limit else value[: limit - 1].rstrip() + "…"


def clock(seconds: int) -> str:
    seconds = max(0, int(seconds))
    h, rest = divmod(seconds, 3600)
    m, s = divmod(rest, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def kb(rows) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def present(c: CallbackQuery, text: str, markup, photo: str | None = None):
    """Xabarni yangilaydi (rasmli bo'lsa, yangidan yuboradi)."""
    msg = c.message
    has_photo = bool(photo) and Path(photo).is_file()

    if has_photo:
        try:
            await msg.delete()
        except Exception:
            pass
        file = FSInputFile(photo)
        if len(text) <= 1000:
            return await msg.answer_photo(file, caption=text, reply_markup=markup)
        await msg.answer_photo(file)
        return await msg.answer(text, reply_markup=markup)

    if photo:  # rasm kerak edi, lekin fayl topilmadi
        text += "\n\n<i>(savol rasmi hozir topilmadi)</i>"

    if getattr(msg, "photo", None):
        try:
            await msg.delete()
        except Exception:
            pass
        return await msg.answer(text, reply_markup=markup)

    try:
        await msg.edit_text(text, reply_markup=markup)
        return msg
    except TelegramBadRequest as exc:
        if "not modified" in str(exc).lower():
            return msg
        return await msg.answer(text, reply_markup=markup)


# ---------------------------------------------------------------
#  Kirish huquqi va holat
# ---------------------------------------------------------------
async def get_user(session, telegram_id: int):
    return await session.scalar(select(User).where(User.telegram_id == telegram_id))


async def attempt_ctx(session, telegram_id: int, attempt_id: int, lock: bool = False):
    """(attempt, exam, items, user) yoki None. lock=True — javob yozishda qator qulflanadi
    (bot va veb-ilovadan bir vaqtda kelgan javoblar bir-birini o'chirmasin)."""
    attempt = await session.get(ExamAttempt, attempt_id, with_for_update=lock)
    user = await get_user(session, telegram_id)

    if attempt is None or user is None or attempt.user_id != user.id:
        return None

    exam = await session.get(Exam, attempt.exam_id)
    if exam is None or exam.is_removed:
        return None

    items = await load_items(session, exam.id)
    await _tl_items(items, telegram_id)          # tarjima asl harflar bo'yicha, aralashtirishdan oldin
    # o'quvchi ko'radigan tartib: savollar va variantlar shu urinish uchun aralashtirilgan bo'lishi mumkin
    return attempt, exam, attempt_view(items, exam, attempt.id), user


def _answers(attempt, items) -> dict:
    """Javoblar o'quvchi ko'rinishida (raqam va harflar items bilan bir xil)."""
    return answers_to_view(attempt_answers(attempt), items)


def _store(attempt, items, answers: dict) -> str:
    """Ko'rinishdagi javoblarni asl tartibda saqlash uchun JSON."""
    return json.dumps(answers_from_view(answers, items, attempt_answers(attempt)), ensure_ascii=False)


async def settle(session, attempt) -> str:
    """'active' | 'finished' | 'expired' (hozirgina yopildi)."""
    if attempt.status == "finished":
        return "finished"

    if datetime.utcnow() >= attempt.deadline_at:
        finish_attempt(attempt)
        await session.commit()
        return "expired"

    return "active"


# ---------------------------------------------------------------
#  Savolni ko'rsatish
# ---------------------------------------------------------------
def question_view(exam, attempt, items, pos: int, answers: dict):
    total = len(items)
    pos = min(max(1, pos), total)
    item = items[pos - 1]
    left = (attempt.deadline_at - datetime.utcnow()).total_seconds()

    lines = [
        f"📝 <b>{esc(short(exam.title, 40))}</b>  ·  ⏳ <b>{clock(left)}</b>",
        "",
        f"<b>{pos}-savol</b> ({pos}/{total})",
        "",
    ]
    rows = []
    photo = None

    if item.kind == "c":
        data = item.data
        if data.get("t"):
            lines.append(esc(short(data["t"], 2500)))
            lines.append("")
        for letter, option in data["o"].items():
            if option:
                lines.append(f"<b>{letter})</b> {esc(short(option, 300))}")

        picked = answers.get(str(pos))
        rows.append([
            InlineKeyboardButton(
                text=("✅ " if picked == letter else "") + letter,
                callback_data=f"exm:a:{attempt.id}:{pos}:{letter}",
            )
            for letter in data["o"]
        ])
        photo = data.get("img")
    else:
        lines.append(esc(short(item.data.get("s", ""), 2000)))
        lines.append("")
        photo = item.data.get("img")
        single = len(item.data["p"]) == 1
        for i, part in enumerate(item.data["p"]):
            letter = "abcd"[i]
            typed = (answers.get(f"{pos}.{i}") or "").strip()
            if single:      # bitta javobli ochiq savol: «a)» belgisi kerak emas
                lines.append("✍️ <b>Javobingiz:</b> " + (f"<i>{esc(typed)}</i>" if typed else "— yozilmagan"))
            else:
                lines.append(f"<b>{letter})</b> {esc(short(part['q'], 300))}")
                lines.append("    ✍️ " + (f"<i>{esc(typed)}</i>" if typed else "— yozilmagan"))
            rows.append([
                InlineKeyboardButton(
                    text=f"{'✏️' if typed else '✍️'} " + ("Javob yozish" if single else f"{letter}) javob yozish"),
                    callback_data=f"exm:w:{attempt.id}:{pos}:{i}",
                )
            ])

    nav = []
    if pos > 1:
        nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"exm:q:{attempt.id}:{pos - 1}"))
    nav.append(InlineKeyboardButton(text="🔢 Savollar", callback_data=f"exm:g:{attempt.id}:{pos}"))
    if pos < total:
        nav.append(InlineKeyboardButton(text="➡️", callback_data=f"exm:q:{attempt.id}:{pos + 1}"))
    rows.append(nav)
    rows.append([InlineKeyboardButton(text="🏁 Yakunlash", callback_data=f"exm:f:{attempt.id}")])

    return "\n".join(lines), kb(rows), photo


async def _expired_notice(c: CallbackQuery, attempt_id: int):
    await c.answer("⏰ Vaqt tugadi", show_alert=True)
    await show_result(c, attempt_id, note="⏰ Vaqt tugadi, imtihon yakunlandi.")


# ---------------------------------------------------------------
#  Imtihonlar ro'yxati
# ---------------------------------------------------------------
@router.callback_query(F.data == "exm:l")
async def cb_list(c: CallbackQuery, state: FSMContext):
    await state.clear()

    async with SessionLocal() as session:
        user = await get_user(session, c.from_user.id)
        if user is None:
            await c.answer("Avval ro'yxatdan o'ting.", show_alert=True)
            return

        exams = (
            await session.scalars(
                select(Exam).where(Exam.is_removed.is_(False)).order_by(Exam.id.desc()).limit(100)
            )
        ).all()
        mine = {
            a.exam_id: a
            for a in (
                await session.scalars(select(ExamAttempt).where(ExamAttempt.user_id == user.id))
            ).all()
        }

    rows, lines = [], ["🎓 <b>Milliy Sertifikat Imtihoni</b>", ""]
    shown = 0

    for exam in exams:
        if not exam_targets_user(exam, user):
            continue
        attempt = mine.get(exam.id)
        open_now = exam_is_open(exam)

        if attempt is None and not open_now:
            continue

        if attempt is None:
            tag = "🆕"
        elif attempt.status == "active":
            tag = "⏳"
        else:
            tag = "✅"

        rows.append([
            InlineKeyboardButton(
                text=f"{tag} {short(exam.title, 40)}",
                callback_data=f"exm:i:{exam.id}",
            )
        ])
        shown += 1
        if shown >= 15:
            break

    if not shown:
        lines.append("Hozircha siz uchun imtihon yo'q.")
    else:
        lines.append("🆕 yangi · ⏳ davom etmoqda · ✅ topshirilgan")

    rows.append([InlineKeyboardButton(text="⬅️ Orqaga", callback_data="mn:test")])
    rows.append([InlineKeyboardButton(text="🏠 Bosh menyu", callback_data="home")])
    await present(c, "\n".join(lines), kb(rows), photo=_milliy_banner())
    await c.answer()


# ---------------------------------------------------------------
#  Imtihon haqida ma'lumot / boshlash
# ---------------------------------------------------------------
@router.callback_query(F.data.regexp(r"^exm:i:\d+$"))
async def cb_info(c: CallbackQuery):
    exam_id = int(c.data.split(":")[2])

    async with SessionLocal() as session:
        user = await get_user(session, c.from_user.id)
        exam = await session.get(Exam, exam_id)
        if user is None or exam is None or exam.is_removed or not exam_targets_user(exam, user):
            await c.answer("Imtihon topilmadi.", show_alert=True)
            return
        attempt = await session.scalar(
            select(ExamAttempt).where(
                ExamAttempt.exam_id == exam_id, ExamAttempt.user_id == user.id
            )
        )
        items = await load_items(session, exam_id)
        if attempt is not None:
            await settle(session, attempt)

    closed = sum(1 for i in items if i.kind == "c")
    opened = len(items) - closed
    parts = sum(len(i.data["p"]) for i in items if i.kind == "o")

    lines = [
        f"📝 <b>{esc(exam.title)}</b>",
        f"📚 {esc(exam.subject_name)}",
        "",
        f"Yopiq savollar: <b>{closed}</b>",
    ]
    if opened:
        lines.append(f"Ochiq savollar: <b>{opened}</b> ({parts} ta javob)")
    lines.append(f"⏱ Vaqt: <b>{exam.duration_min}</b> daqiqa")
    if exam.closes_at:
        lines.append(f"📅 Topshirish muddati: <b>{fmt_datetime(exam.closes_at)}</b>")

    rows = []
    if attempt is None:
        if exam_is_open(exam) and items:
            lines += [
                "",
                "⚠️ <b>Qoidalar:</b>",
                "• Boshlaganingizdan so'ng taymer to'xtamaydi.",
                "• Imtihon faqat <b>bir marta</b> topshiriladi.",
                "• Savollar orasida erkin yurasiz, javobni o'zgartirish mumkin.",
                "• Imtihon davomida to'g'ri javob ko'rsatilmaydi.",
            ]
            if getattr(exam, "shuffle", False):
                lines.append("• Savollar va A–D variantlar tartibi har bir o'quvchida har xil.")
            lines.append("• Imtihon yopilgach xatolaringiz «Mening xatolarim» bo'limiga qo'shiladi.")
            rows.append([InlineKeyboardButton(text="▶️ Boshlash", callback_data=f"exm:go:{exam_id}")])
        else:
            lines += ["", "🔒 Bu imtihon yopilgan."]
    elif attempt.status == "active":
        rows.append([InlineKeyboardButton(text="▶️ Davom etish", callback_data=f"exm:q:{attempt.id}:1")])
    else:
        rows.append([InlineKeyboardButton(text="📊 Natijam", callback_data=f"exm:r:{attempt.id}")])

    rows.append([InlineKeyboardButton(text="⬅️ Orqaga", callback_data="exm:l")])
    await present(c, "\n".join(lines), kb(rows))
    await c.answer()


@router.callback_query(F.data.regexp(r"^exm:go:\d+$"))
async def cb_start(c: CallbackQuery, state: FSMContext):
    exam_id = int(c.data.split(":")[2])

    async with SessionLocal() as session:
        user = await get_user(session, c.from_user.id)
        exam = await session.get(Exam, exam_id)
        if user is None or exam is None or exam.is_removed or not exam_targets_user(exam, user):
            await c.answer("Imtihon topilmadi.", show_alert=True)
            return

        attempt = await session.scalar(
            select(ExamAttempt).where(
                ExamAttempt.exam_id == exam_id, ExamAttempt.user_id == user.id
            )
        )

        if attempt is None:
            if not exam_is_open(exam):
                await c.answer("Bu imtihon yopilgan.", show_alert=True)
                return
            items = await load_items(session, exam_id)
            await _tl_items(items, c.from_user.id)
            if not items:
                await c.answer("Imtihonda savol yo'q.", show_alert=True)
                return

            now = datetime.utcnow()
            attempt = ExamAttempt(
                exam_id=exam_id,
                user_id=user.id,
                started_at=now,
                deadline_at=now + timedelta(minutes=exam.duration_min),
                status="active",
                answers="{}",
            )
            session.add(attempt)
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                attempt = await session.scalar(
                    select(ExamAttempt).where(
                        ExamAttempt.exam_id == exam_id, ExamAttempt.user_id == user.id
                    )
                )
        attempt_id = attempt.id

    await state.clear()
    await _show_question(c, attempt_id, 1)


async def _show_question(c: CallbackQuery, attempt_id: int, pos: int):
    async with SessionLocal() as session:
        ctx = await attempt_ctx(session, c.from_user.id, attempt_id)
        if ctx is None:
            await c.answer("Imtihon topilmadi.", show_alert=True)
            return
        attempt, exam, items, _user = ctx
        status = await settle(session, attempt)
        if status == "expired":
            await _expired_notice(c, attempt_id)
            return
        if status == "finished":
            await c.answer()
            await show_result(c, attempt_id)
            return
        text, markup, photo = question_view(exam, attempt, items, pos, _answers(attempt, items))

    await present(c, text, markup, photo)
    await c.answer()


@router.callback_query(F.data.regexp(r"^exm:q:\d+:\d+$"))
async def cb_question(c: CallbackQuery, state: FSMContext):
    _, _, aid, pos = c.data.split(":")
    await state.clear()
    await _show_question(c, int(aid), int(pos))


# ---------------------------------------------------------------
#  Javob berish
# ---------------------------------------------------------------
@router.callback_query(F.data.regexp(r"^exm:a:\d+:\d+:[A-F]$"))
async def cb_answer(c: CallbackQuery):
    _, _, aid, pos_s, letter = c.data.split(":")
    pos = int(pos_s)

    async with SessionLocal() as session:
        ctx = await attempt_ctx(session, c.from_user.id, int(aid), lock=True)
        if ctx is None:
            await c.answer("Imtihon topilmadi.", show_alert=True)
            return
        attempt, exam, items, _user = ctx
        status = await settle(session, attempt)
        if status == "expired":
            await _expired_notice(c, attempt.id)
            return
        if status == "finished":
            await c.answer("Imtihon yakunlangan.", show_alert=True)
            return
        if pos < 1 or pos > len(items) or items[pos - 1].kind != "c" or letter not in items[pos - 1].data["o"]:
            await c.answer("Noto'g'ri so'rov.", show_alert=True)
            return

        answers = _answers(attempt, items)
        if answers.get(str(pos)) == letter:
            answers.pop(str(pos), None)      # qayta bosilsa, javob bekor
            note = "Javob bekor qilindi"
        else:
            answers[str(pos)] = letter
            note = f"Javob: {letter}"
        attempt.answers = _store(attempt, items, answers)
        await session.commit()
        text, markup, photo = question_view(exam, attempt, items, pos, answers)

    await present(c, text, markup, photo)
    await c.answer(note)


@router.callback_query(F.data.regexp(r"^exm:w:\d+:\d+:\d$"))
async def cb_write(c: CallbackQuery, state: FSMContext):
    _, _, aid, pos_s, part_s = c.data.split(":")
    pos, part = int(pos_s), int(part_s)

    async with SessionLocal() as session:
        ctx = await attempt_ctx(session, c.from_user.id, int(aid))
        if ctx is None:
            await c.answer("Imtihon topilmadi.", show_alert=True)
            return
        attempt, exam, items, _user = ctx
        status = await settle(session, attempt)
        if status == "expired":
            await _expired_notice(c, attempt.id)
            return
        if status == "finished":
            await c.answer("Imtihon yakunlangan.", show_alert=True)
            return
        if pos < 1 or pos > len(items) or items[pos - 1].kind != "o" or part >= len(items[pos - 1].data["p"]):
            await c.answer("Noto'g'ri so'rov.", show_alert=True)
            return
        prompt = items[pos - 1].data["p"][part]["q"]
        if len(items[pos - 1].data["p"]) == 1:      # bitta javobli: savolning o'zi ko'rsatiladi
            head, prompt = f"{pos}-savol", items[pos - 1].data.get("s") or prompt
        else:
            head = f"{pos}-savol, {'abcd'[part]}) qism"

    await state.set_state(ExamTake.typing)
    sent = await present(
        c,
        f"✍️ <b>{head}</b>\n\n{esc(short(prompt, 600))}\n\n"
        "Javobingizni yozib yuboring.\n"
        "<i>Javobni o'chirish uchun «-» yuboring.</i>",
        kb([[InlineKeyboardButton(text="❌ Bekor qilish", callback_data=f"exm:q:{aid}:{pos}")]]),
    )
    await state.update_data(
        aid=int(aid), pos=pos, part=part,
        mid=getattr(sent, "message_id", c.message.message_id),
        photo=False,
    )
    await c.answer()


@router.message(ExamTake.typing)
async def msg_typed(m: Message, state: FSMContext):
    data = await state.get_data()
    aid, pos, part = data.get("aid"), data.get("pos"), data.get("part")
    text = (m.text or "").strip()

    if not text:
        await m.answer("❗ Javobni matn ko'rinishida yuboring.")
        return

    if len(text) > MAX_ANSWER_LEN:
        await m.answer(f"❗ Javob juda uzun. {MAX_ANSWER_LEN} belgigacha yozing.")
        return

    async with SessionLocal() as session:
        ctx = await attempt_ctx(session, m.from_user.id, aid or 0, lock=True)
        if ctx is None:
            await state.clear()
            await m.answer("Imtihon topilmadi.")
            return
        attempt, exam, items, _user = ctx
        status = await settle(session, attempt)
        if status != "active":
            await state.clear()
            await m.answer(
                "⏰ Vaqt tugadi, imtihon yakunlandi." if status == "expired" else "Imtihon yakunlangan.",
                reply_markup=kb([[InlineKeyboardButton(text="📊 Natijam", callback_data=f"exm:r:{attempt.id}")]]),
            )
            return
        if pos is None or pos < 1 or pos > len(items) or items[pos - 1].kind != "o":
            await state.clear()
            return

        answers = _answers(attempt, items)
        key = f"{pos}.{part}"
        if text == "-":
            answers.pop(key, None)
        else:
            answers[key] = text
        attempt.answers = _store(attempt, items, answers)
        await session.commit()
        view_text, markup, photo = question_view(exam, attempt, items, pos, answers)

    await state.clear()

    try:
        await m.delete()          # o'quvchining javob xabarini tozalaymiz
    except Exception:
        pass

    old_id = data.get("mid")
    if photo and Path(photo).is_file():
        if old_id:
            try:
                await m.bot.delete_message(m.chat.id, old_id)
            except Exception:
                pass
        if len(view_text) <= 1000:
            await m.answer_photo(FSInputFile(photo), caption=view_text, reply_markup=markup)
        else:
            await m.answer_photo(FSInputFile(photo))
            await m.answer(view_text, reply_markup=markup)
        return
    if old_id and not data.get("photo"):
        try:
            await m.bot.edit_message_text(
                view_text, chat_id=m.chat.id, message_id=old_id, reply_markup=markup,
            )
            return
        except Exception:
            pass

    if old_id:
        try:
            await m.bot.delete_message(m.chat.id, old_id)
        except Exception:
            pass

    await m.answer(view_text, reply_markup=markup)


# ---------------------------------------------------------------
#  Savollar paneli va yakunlash
# ---------------------------------------------------------------
@router.callback_query(F.data.regexp(r"^exm:g:\d+:\d+$"))
async def cb_grid(c: CallbackQuery):
    _, _, aid, pos_s = c.data.split(":")

    async with SessionLocal() as session:
        ctx = await attempt_ctx(session, c.from_user.id, int(aid))
        if ctx is None:
            await c.answer("Imtihon topilmadi.", show_alert=True)
            return
        attempt, exam, items, _user = ctx
        status = await settle(session, attempt)
        if status == "expired":
            await _expired_notice(c, attempt.id)
            return
        if status == "finished":
            await c.answer()
            await show_result(c, attempt.id)
            return
        answers = _answers(attempt, items)

    done = sum(1 for it in items if item_answered(it, answers))
    left = (attempt.deadline_at - datetime.utcnow()).total_seconds()
    buttons = [
        InlineKeyboardButton(
            text=("✅" if item_answered(it, answers) else "▫️") + str(it.pos),
            callback_data=f"exm:q:{attempt.id}:{it.pos}",
        )
        for it in items
    ]
    rows = [buttons[i:i + 5] for i in range(0, len(buttons), 5)]
    rows.append([InlineKeyboardButton(text="⬅️ Savolga qaytish", callback_data=f"exm:q:{attempt.id}:{int(pos_s)}")])
    rows.append([InlineKeyboardButton(text="🏁 Yakunlash", callback_data=f"exm:f:{attempt.id}")])

    await present(
        c,
        f"🔢 <b>Savollar</b>  ·  ⏳ <b>{clock(left)}</b>\n\n"
        f"Javob berilgan: <b>{done}</b> / {len(items)}\n"
        "✅ javob bor · ▫️ javob yo'q",
        kb(rows),
    )
    await c.answer()


@router.callback_query(F.data.regexp(r"^exm:f:\d+$"))
async def cb_finish_ask(c: CallbackQuery):
    aid = int(c.data.split(":")[2])

    async with SessionLocal() as session:
        ctx = await attempt_ctx(session, c.from_user.id, aid)
        if ctx is None:
            await c.answer("Imtihon topilmadi.", show_alert=True)
            return
        attempt, exam, items, _user = ctx
        status = await settle(session, attempt)
        if status == "expired":
            await _expired_notice(c, aid)
            return
        if status == "finished":
            await c.answer()
            await show_result(c, aid)
            return
        answers = _answers(attempt, items)

    empty = [it.pos for it in items if not item_answered(it, answers)]
    text = "🏁 <b>Imtihonni yakunlaysizmi?</b>\n\n"
    if empty:
        shown = ", ".join(str(p) for p in empty[:25]) + (" …" if len(empty) > 25 else "")
        text += f"⚠️ Javobsiz savollar: <b>{len(empty)}</b> ta ({shown})\n\n"
    text += "Yakunlagandan keyin javoblarni o'zgartirib bo'lmaydi."

    await present(
        c, text,
        kb([
            [InlineKeyboardButton(text="✅ Ha, yakunlash", callback_data=f"exm:fy:{aid}")],
            [InlineKeyboardButton(text="⬅️ Imtihonga qaytish", callback_data=f"exm:q:{aid}:1")],
        ]),
    )
    await c.answer()


@router.callback_query(F.data.regexp(r"^exm:fy:\d+$"))
async def cb_finish_do(c: CallbackQuery, state: FSMContext):
    aid = int(c.data.split(":")[2])

    async with SessionLocal() as session:
        ctx = await attempt_ctx(session, c.from_user.id, aid)
        if ctx is None:
            await c.answer("Imtihon topilmadi.", show_alert=True)
            return
        attempt = ctx[0]
        if attempt.status == "active":
            finish_attempt(attempt)
            await session.commit()

    await state.clear()
    await c.answer("Imtihon yakunlandi")
    await show_result(c, aid)


# ---------------------------------------------------------------
#  Natija
# ---------------------------------------------------------------
async def show_result(c: CallbackQuery, attempt_id: int, note: str = ""):
    async with SessionLocal() as session:
        ctx = await attempt_ctx(session, c.from_user.id, attempt_id)
        if ctx is None:
            await c.answer("Natija topilmadi.", show_alert=True)
            return
        attempt, exam, items, user = ctx

        if attempt.status != "finished":
            await _show_question(c, attempt_id, 1)
            return

        _items, results = await exam_results(session, exam)

    mine = next((r for r in results if r.attempt.id == attempt.id), None)
    if mine is None:
        await c.answer("Natija topilmadi.", show_alert=True)
        return

    rank = results.index(mine) + 1
    sc = mine.score

    lines = []
    if note:
        lines += [note, ""]
    lines += [
        "🏁 <b>Imtihon natijasi</b>",
        f"📝 {esc(exam.title)}",
        "",
        f"✅ Yopiq savollar: <b>{sc.c_ok}</b> / {sc.c_total}",
    ]
    if sc.o_total:
        lines.append(f"✍️ Ochiq javoblar: <b>{sc.o_ok}</b> / {sc.o_total}")

    if exam.mode == "milliy":
        lines.append(f"🎯 Ball: <b>{sc.value}</b> / {int(SCALE_MAX)}")
        lines.append(f"🏅 Daraja: <b>{esc(sc.level)}</b>")
    else:
        lines.append(f"🎯 Natija: <b>{sc.value}%</b>")

    lines.append(f"📍 Guruhdagi o'rningiz: <b>{rank}</b> / {len(results)}")
    lines.append(f"⏱ Sarflangan vaqt: {mine.minutes} daqiqa")
    lines.append("")

    if sc.weighted:
        lines.append("<i>Ball qiyin savollarga ko'proq ball berish bilan hisoblangan. "
                     "Boshqalar topshirgach biroz o'zgarishi mumkin.</i>")
    else:
        lines.append(f"<i>Ishtirokchilar hali {MIN_STATS} tadan kam, shuning uchun ball oddiy "
                     "hisoblangan. Ko'proq odam topshirsa, qiyin savollar ko'proq ball beradi.</i>")

    if exam.mode == "milliy":
        lines.append("<i>Bu mashq uchun taxminiy ball, rasmiy natija emas.</i>")

    rows = [[InlineKeyboardButton(text="🏆 Reyting", callback_data=f"exm:top:{exam.id}")]]
    if exam_is_open(exam):
        lines += ["", "🔒 Xatolar tahlili imtihon yopilgach ochiladi."]
    else:
        rows.insert(0, [InlineKeyboardButton(text="🔍 Xatolar tahlili", callback_data=f"exm:rw:{attempt.id}:0")])
    rows.append([InlineKeyboardButton(text="🎓 Milliy sertifikat imtihoni", callback_data="exm:l")])
    rows.append([InlineKeyboardButton(text="🏠 Bosh menyu", callback_data="home")])

    await present(c, "\n".join(lines), kb(rows))


@router.callback_query(F.data.regexp(r"^exm:r:\d+$"))
async def cb_result(c: CallbackQuery, state: FSMContext):
    await state.clear()
    await show_result(c, int(c.data.split(":")[2]))
    await c.answer()


@router.callback_query(F.data.regexp(r"^exm:top:\d+$"))
async def cb_top(c: CallbackQuery):
    exam_id = int(c.data.split(":")[2])

    async with SessionLocal() as session:
        user = await get_user(session, c.from_user.id)
        exam = await session.get(Exam, exam_id)
        if user is None or exam is None or exam.is_removed:
            await c.answer("Imtihon topilmadi.", show_alert=True)
            return
        mine = await session.scalar(
            select(ExamAttempt).where(
                ExamAttempt.exam_id == exam_id,
                ExamAttempt.user_id == user.id,
                ExamAttempt.status == "finished",
            )
        )
        if mine is None:
            await c.answer("Reyting imtihonni topshirgach ochiladi.", show_alert=True)
            return
        _items, results = await exam_results(session, exam)

    medals = ["🥇", "🥈", "🥉"]
    lines = [f"🏆 <b>{esc(short(exam.title, 40))}</b> — reyting", ""]
    for i, r in enumerate(results[:10]):
        prefix = medals[i] if i < 3 else f"{i + 1}."
        value = f"{r.score.value}" + ("" if exam.mode == "milliy" else "%")
        you = "  ← siz" if r.attempt.id == mine.id else ""
        lines.append(f"{prefix} {esc(short(r.user.full_name, 28))} — <b>{value}</b>{you}")

    my_rank = next((i for i, r in enumerate(results) if r.attempt.id == mine.id), None)
    if my_rank is not None and my_rank >= 10:
        lines += ["…", f"{my_rank + 1}. Siz — <b>{results[my_rank].score.value}</b>"]

    await present(
        c, "\n".join(lines),
        kb([[InlineKeyboardButton(text="⬅️ Natijam", callback_data=f"exm:r:{mine.id}")]]),
    )
    await c.answer()


# ---------------------------------------------------------------
#  Xatolar tahlili (imtihon yopilgach)
# ---------------------------------------------------------------
async def _review_ctx(c: CallbackQuery, attempt_id: int):
    async with SessionLocal() as session:
        ctx = await attempt_ctx(session, c.from_user.id, attempt_id)
        if ctx is None:
            await c.answer("Natija topilmadi.", show_alert=True)
            return None
        attempt, exam, items, _user = ctx
        if attempt.status != "finished":
            await c.answer("Imtihon hali tugamagan.", show_alert=True)
            return None
        if exam_is_open(exam):
            await c.answer("Tahlil imtihon yopilgach ochiladi.", show_alert=True)
            return None
        return attempt, exam, items


@router.callback_query(F.data.regexp(r"^exm:rw:\d+:\d+$"))
async def cb_review_list(c: CallbackQuery):
    _, _, aid, page_s = c.data.split(":")
    got = await _review_ctx(c, int(aid))
    if got is None:
        return
    attempt, exam, items = got

    answers = _answers(attempt, items)
    sc = score_attempt(items, answers, None, exam.mode)
    wrong_positions = sorted({u.pos for u in sc.wrong})

    if not wrong_positions:
        await present(
            c, "🎉 <b>Xato yo'q!</b> Hamma javoblar to'g'ri.",
            kb([[InlineKeyboardButton(text="⬅️ Natijam", callback_data=f"exm:r:{attempt.id}")]]),
        )
        await c.answer()
        return

    last = (len(wrong_positions) - 1) // REVIEW_PAGE
    page = min(max(0, int(page_s)), last)
    chunk = wrong_positions[page * REVIEW_PAGE:(page + 1) * REVIEW_PAGE]

    buttons = [
        InlineKeyboardButton(text=str(p), callback_data=f"exm:rv:{attempt.id}:{p}")
        for p in chunk
    ]
    rows = [buttons[i:i + 5] for i in range(0, len(buttons), 5)]
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"exm:rw:{attempt.id}:{page - 1}"))
    if page < last:
        nav.append(InlineKeyboardButton(text="➡️", callback_data=f"exm:rw:{attempt.id}:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton(text="⬅️ Natijam", callback_data=f"exm:r:{attempt.id}")])

    await present(
        c,
        f"🔍 <b>Xatolar tahlili</b>\n\nNoto'g'ri yoki javobsiz savollar: <b>{len(wrong_positions)}</b> ta.\n"
        "Ko'rmoqchi bo'lgan savol raqamini bosing:",
        kb(rows),
    )
    await c.answer()


@router.callback_query(F.data.regexp(r"^exm:rv:\d+:\d+$"))
async def cb_review_item(c: CallbackQuery):
    _, _, aid, pos_s = c.data.split(":")
    pos = int(pos_s)
    got = await _review_ctx(c, int(aid))
    if got is None:
        return
    attempt, exam, items = got

    if pos < 1 or pos > len(items):
        await c.answer("Savol topilmadi.", show_alert=True)
        return

    item = items[pos - 1]
    answers = _answers(attempt, items)
    lines = [f"🔍 <b>{pos}-savol</b>", ""]
    photo = None

    if item.kind == "c":
        data = item.data
        if data.get("t"):
            lines += [esc(short(data["t"], 2500)), ""]
        mine = answers.get(str(pos))
        for letter, option in data["o"].items():
            mark = "✅" if letter == data["c"] else ("❌" if letter == mine else "▫️")
            lines.append(f"{mark} <b>{letter})</b>" + (f" {esc(short(option, 300))}" if option else ""))
        lines.append("")
        lines.append(f"Sizning javobingiz: <b>{mine or 'yo‘q'}</b> · To'g'ri javob: <b>{data['c']}</b>")
        if data.get("e"):
            lines += ["", "💡 " + esc(short(data["e"], 800))]
        photo = data.get("img")
    else:
        lines += [esc(short(item.data.get("s", ""), 2000)), ""]
        photo = item.data.get("img")
        units = {u.part: u for u in build_units([item])}
        single = len(item.data["p"]) == 1
        for i, part in enumerate(item.data["p"]):
            ok = unit_correct(item, units[i], answers)
            typed = (answers.get(f"{pos}.{i}") or "").strip() or "yo‘q"
            lines.append(f"{'✅' if ok else '❌'} " + ("<b>Javob</b>" if single else f"<b>{'abcd'[i]})</b> {esc(short(part['q'], 300))}"))
            lines.append(f"    Sizniki: <i>{esc(typed)}</i>")
            if not ok:
                lines.append("    To'g'ri: <b>" + esc(" / ".join(part["a"][:3])) + "</b>")

    await present(
        c, "\n".join(lines),
        kb([[InlineKeyboardButton(text="⬅️ Xatolar ro'yxati", callback_data=f"exm:rw:{attempt.id}:0")]]),
        photo,
    )
    await c.answer()


def _milliy_banner():
    """Milliy sertifikat bannerining yo'li (fayl bo'lmasa None)."""
    p = Path(__file__).resolve().parent.parent / "assets" / "milliy_banner.jpg"
    return str(p) if p.is_file() else None

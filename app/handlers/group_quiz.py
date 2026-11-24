"""Guruh testi.

Joylashuvi: app/handlers/group_quiz.py

Oqim:
  1. Guruhda boshlovchi (yoki guruh admini) savollar sonini tanlaydi.
  2. Har savolga vaqtni tanlaydi (15-60 soniya) yoki "cheklovsiz".
  3. Har savol so'rovnoma (quiz) ko'rinishida chiqadi. Hamma
     ishtirokchining javobi hisoblanadi.
  4. Vaqt tugasa (yoki barcha ishtirokchi javob bersa) keyingi savol
     chiqadi. Cheklovsiz rejimda keyingi savolga o'tkazish
     "Keyingi savol" tugmasi bilan bajariladi.
  5. Test tugagach natijalar e'lon qilinadi va har o'quvchining
     umumiy test tarixiga yoziladi.

Boshqaruv (keyingi savol, to'xtatish) faqat testni boshlagan
foydalanuvchi, guruh adminlari va bot adminlari uchun.
"""

from __future__ import annotations

import asyncio
import html
import json
import random
import time
from datetime import datetime
from pathlib import Path

from aiogram import F, Router
from aiogram.exceptions import TelegramNetworkError, TelegramRetryAfter
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    PollAnswer,
)
from sqlalchemy import select, update

from ..config import settings
from ..db import SessionLocal
from ..models import (
    GroupQuiz,
    GroupQuizAnswer,
    GroupQuizParticipant,
    Question,
    Topic,
    User,
)


router = Router()

# Savolga beriladigan vaqt variantlari (soniya). 0 = cheklovsiz.
TIME_CHOICES = (15, 20, 30, 45, 60)

# Telegram so'rovnomasi chegaralari
POLL_QUESTION_LIMIT = 300
POLL_OPTION_LIMIT = 100
INTRO_LIMIT = 3900

# Hamma ishtirokchi javob bergach, keyingi savolgacha kutish (soniya)
EARLY_ADVANCE_DELAY = 3.0

# Eskirgan testni yangisiga yo'l berish uchun yopish muddatlari
STALE_STARTING_SECONDS = 180
STALE_ACTIVE_SECONDS = 6 * 3600

_GROUP_LOCKS: dict[int, asyncio.Lock] = {}
_GROUP_START_LOCKS: dict[int, asyncio.Lock] = {}
_TIMERS: dict[int, asyncio.Task] = {}


def _group_lock(gid: int) -> asyncio.Lock:
    return _GROUP_LOCKS.setdefault(gid, asyncio.Lock())


def _group_start_lock(chat_id: int) -> asyncio.Lock:
    return _GROUP_START_LOCKS.setdefault(chat_id, asyncio.Lock())


# ---------------------------------------------------------------
#  Savolni so'rovnomaga aylantirish
# ---------------------------------------------------------------

def _valid_question(question: Question) -> bool:
    correct = (question.correct_option or "").strip().upper()

    if question.image_path:
        try:
            image_exists = Path(question.image_path).is_file()
        except (OSError, TypeError):
            image_exists = False

        return bool(image_exists and correct in {"A", "B", "C", "D"})

    text = (question.text or "").strip()
    options = [
        (question.option_a or "").strip(),
        (question.option_b or "").strip(),
        (question.option_c or "").strip(),
        (question.option_d or "").strip(),
    ]

    return bool(text and all(options) and correct in {"A", "B", "C", "D"})


def _build_poll(question: Question):
    """Savoldan so'rovnoma tayyorlaydi.

    Qaytaradi: (so'rovnoma savoli, variantlar, to'g'ri indeks, oldingi xabar)
    yoki yaroqsiz savol uchun None.

    Telegram savolni 300, variantni 100 belgigacha cheklaydi. Savol
    yoki variant undan uzun bo'lsa, to'liq matn alohida xabar bilan
    yuboriladi, so'rovnomada esa A/B/C/D harflari bo'ladi (aks holda
    so'rovnoma umuman yuborilmasdi yoki matn kesilib qolardi).
    """
    if not _valid_question(question):
        return None

    correct = (question.correct_option or "").strip().upper()

    if question.image_path:
        order = ["A", "B", "C", "D"]
        return (
            "Rasmga qarab javobni tanlang.",
            list(order),
            order.index(correct),
            None,
        )

    text = (question.text or "").strip()

    option_map = {
        "A": (question.option_a or "").strip(),
        "B": (question.option_b or "").strip(),
        "C": (question.option_c or "").strip(),
        "D": (question.option_d or "").strip(),
    }

    too_long = len(text) > POLL_QUESTION_LIMIT or any(
        len(value) > POLL_OPTION_LIMIT for value in option_map.values()
    )

    if too_long:
        order = ["A", "B", "C", "D"]

        intro = (
            f"❓ <b>{html.escape(text)}</b>\n\n"
            + "\n".join(
                f"<b>{key})</b> {html.escape(value)}"
                for key, value in option_map.items()
            )
        )

        return (
            "Javobni tanlang (A, B, C yoki D).",
            list(order),
            order.index(correct),
            intro[:INTRO_LIMIT],
        )

    order = list(option_map)
    random.shuffle(order)

    return (
        text,
        [option_map[key] for key in order],
        order.index(correct),
        None,
    )


async def _get_valid_questions(topic_id: int):
    async with SessionLocal() as session:
        questions = (
            await session.scalars(
                select(Question).where(
                    Question.is_active.is_(True),
                    Question.topic_id == topic_id,
                )
            )
        ).all()

    return [question for question in questions if _valid_question(question)]


async def _call_with_retry(make_call, attempts: int = 4):
    """Telegram so'rovini cheklangan marta qayta urinib bajaradi.

    Oldin cheksiz qayta urinish bor edi: tarmoq uzilsa, test abadiy
    qotib qolishi mumkin edi.
    """
    last_error = None

    for number in range(1, attempts + 1):
        try:
            return await make_call()

        except TelegramRetryAfter as exc:
            last_error = exc
            await asyncio.sleep(min(30, max(1, int(exc.retry_after))))

        except TelegramNetworkError as exc:
            last_error = exc

            print(
                ">>> GROUP NETWORK RETRY: "
                f"{type(exc).__name__}: {exc} | retry={number}",
                flush=True,
            )

            await asyncio.sleep(min(10, 2 * number))

    raise last_error


# ---------------------------------------------------------------
#  Tugmalar
# ---------------------------------------------------------------

def _size_keyboard(total: int, topic_id: int) -> InlineKeyboardMarkup:
    presets = [n for n in (3, 5, 10, 15, 20, 25, 30, 40, 50) if n <= total]

    if total and total not in presets:
        presets.append(total)

    rows = []
    row = []

    for size in sorted(set(presets)):
        row.append(
            InlineKeyboardButton(
                text=f"{size} ta",
                callback_data=f"gquiz:size:{topic_id}:{size}",
            )
        )

        if len(row) == 3:
            rows.append(row)
            row = []

    if row:
        rows.append(row)

    rows.append(
        [
            InlineKeyboardButton(
                text="✍️ O'zim kiritaman",
                callback_data=f"gquiz:size:{topic_id}:custom",
            )
        ]
    )

    rows.append(
        [
            InlineKeyboardButton(
                text="❌ Bekor qilish",
                callback_data="gquiz:cancel",
            )
        ]
    )

    return InlineKeyboardMarkup(inline_keyboard=rows)


def _time_keyboard(topic_id: int) -> InlineKeyboardMarkup:
    row = [
        InlineKeyboardButton(
            text=f"{seconds} soniya",
            callback_data=f"gquiz:time:{topic_id}:{seconds}",
        )
        for seconds in TIME_CHOICES
    ]

    return InlineKeyboardMarkup(
        inline_keyboard=[
            row[:3],
            row[3:],
            [
                InlineKeyboardButton(
                    text="♾ Cheklovsiz (tugma bilan o'tkaziladi)",
                    callback_data=f"gquiz:time:{topic_id}:0",
                )
            ],
            [
                InlineKeyboardButton(
                    text="❌ Bekor qilish",
                    callback_data="gquiz:cancel",
                )
            ],
        ]
    )


def _pad_keyboard(topic_id: int) -> InlineKeyboardMarkup:
    def key(text, value):
        return InlineKeyboardButton(
            text=text,
            callback_data=f"gquiz:pad:{topic_id}:{value}",
        )

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [key("1", "1"), key("2", "2"), key("3", "3")],
            [key("4", "4"), key("5", "5"), key("6", "6")],
            [key("7", "7"), key("8", "8"), key("9", "9")],
            [key("⌫", "del"), key("0", "0"), key("✅ Tayyor", "ok")],
            [
                InlineKeyboardButton(
                    text="❌ Bekor qilish",
                    callback_data="gquiz:cancel",
                )
            ],
        ]
    )


def _pad_text(total: int, value: str) -> str:
    shown = value if value else "—"

    return (
        "✍️ <b>Savollar sonini kiriting</b>\n\n"
        f"1 dan {total} gacha. Raqam tugmalarini bosing, so'ng "
        "«✅ Tayyor».\n\n"
        f"Tanlangan son: <b>{shown}</b>"
    )


def _control_keyboard(gid: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⏭ Keyingi savol",
                    callback_data=f"gq:next:{gid}",
                ),
                InlineKeyboardButton(
                    text="⏹ To'xtatish",
                    callback_data=f"gq:stop:{gid}",
                ),
            ]
        ]
    )


# ---------------------------------------------------------------
#  Ruxsatlar
# ---------------------------------------------------------------

async def _remember_initiator(chat_id: int, user_id: int) -> None:
    try:
        from ..services.kv import kv_set

        await kv_set(f"gq_init:{chat_id}", str(user_id))
    except Exception as exc:
        print(f">>> GROUP INITIATOR SAVE ERROR: {exc}", flush=True)


async def _is_controller(
    bot,
    chat_id: int,
    user_id: int,
    creator_id: int | None = None,
) -> bool:
    """Testni boshqara oladimi: bot admini, boshlovchi yoki guruh admini."""
    if user_id in settings.admins:
        return True

    if creator_id is not None and int(creator_id) == int(user_id):
        return True

    try:
        from ..services.kv import kv_get

        initiator = await kv_get(f"gq_init:{chat_id}")

        if initiator and initiator == str(user_id):
            return True
    except Exception:
        pass

    try:
        member = await bot.get_chat_member(chat_id, user_id)
        return getattr(member, "status", None) in ("creator", "administrator")
    except Exception:
        return False


NOT_ALLOWED = (
    "Guruh testini faqat uni boshlagan foydalanuvchi yoki "
    "guruh adminlari boshqara oladi."
)


# ---------------------------------------------------------------
#  Taymer
# ---------------------------------------------------------------

def _cancel_timer(gid: int) -> None:
    task = _TIMERS.pop(gid, None)

    if task is not None and not task.done() and task is not asyncio.current_task():
        task.cancel()


async def _timer_job(bot, gid: int, poll_id, delay: float) -> None:
    try:
        await asyncio.sleep(max(0.0, delay))

        async with _group_lock(gid):
            await _advance_group_question_locked(
                bot,
                gid,
                expected_poll_id=str(poll_id),
            )

    except asyncio.CancelledError:
        raise

    except Exception as exc:
        print(
            f">>> GROUP TIMER ERROR: {type(exc).__name__}: {exc}",
            flush=True,
        )

    finally:
        if _TIMERS.get(gid) is asyncio.current_task():
            _TIMERS.pop(gid, None)


def _schedule_timer(bot, gid: int, poll_id, delay: float) -> None:
    _cancel_timer(gid)
    _TIMERS[gid] = asyncio.create_task(_timer_job(bot, gid, poll_id, delay))


def _build_meta(sent: dict, open_period: int) -> dict:
    now = time.time()

    meta = {
        "current_index": sent["index"],
        "current_poll_id": sent["poll_id"],
        "current_poll_message_id": sent["message_id"],
        "questions": {
            str(sent["poll_id"]): {
                "qid": sent["question_id"],
                "correct_idx": sent["correct_idx"],
            }
        },
        "open_period": int(open_period or 0),
        "sent_ts": now,
    }

    if open_period:
        meta["deadline_ts"] = now + int(open_period)

    return meta


def _schedule_for_meta(bot, gid: int, meta: dict) -> None:
    open_period = int(meta.get("open_period") or 0)

    if open_period and meta.get("current_poll_id"):
        _schedule_timer(bot, gid, meta["current_poll_id"], open_period + 1.0)


# ---------------------------------------------------------------
#  So'rovnoma yuborish
# ---------------------------------------------------------------

async def _send_poll(
    bot,
    chat_id: int,
    gid: int,
    question_ids: list,
    start_index: int,
    open_period: int,
):
    """start_index dan boshlab birinchi yaroqli savolni yuboradi.

    Yaroqsiz (o'chirilgan, rasmi yo'q) savol tushirib ketiladi, test
    to'xtab qolmaydi. Qaytaradi: dict yoki None (savol qolmagan).
    """
    index = start_index

    while index < len(question_ids):
        question_id = int(question_ids[index])

        async with SessionLocal() as session:
            question = await session.get(Question, question_id)

        built = _build_poll(question) if question else None

        if built is None:
            index += 1
            continue

        poll_question, options, correct_idx, intro = built

        if question.image_path:
            image_path = question.image_path

            await _call_with_retry(
                lambda: bot.send_photo(
                    chat_id=chat_id,
                    photo=FSInputFile(image_path),
                )
            )

        if intro:
            await _call_with_retry(lambda: bot.send_message(chat_id, intro))

        kwargs = dict(
            chat_id=chat_id,
            question=poll_question,
            options=options,
            type="quiz",
            is_anonymous=False,
            correct_option_id=correct_idx,
            allows_multiple_answers=False,
            reply_markup=_control_keyboard(gid),
        )

        explanation = (question.explanation or "").strip()

        # Belgilar Telegram tomonidan HTML deb o'qilishi mumkin: faqat
        # oddiy matnli izohlar yuboriladi
        if explanation and not any(ch in explanation for ch in "<>&"):
            kwargs["explanation"] = explanation[:200]

        if open_period:
            kwargs["open_period"] = int(open_period)

        poll = await _call_with_retry(lambda: bot.send_poll(**kwargs))

        return {
            "index": index,
            "poll_id": poll.poll.id,
            "message_id": poll.message_id,
            "question_id": question_id,
            "correct_idx": correct_idx,
        }

    return None


async def _stop_poll_quietly(bot, chat_id, message_id) -> None:
    if not message_id:
        return

    try:
        await bot.stop_poll(chat_id, int(message_id))
    except Exception:
        # Vaqt bilan yopilgan yoki o'chirilgan bo'lishi mumkin
        pass


# ---------------------------------------------------------------
#  Testni yakunlash
# ---------------------------------------------------------------

async def _finish_group_quiz_locked(bot, gid: int) -> bool:
    participants = []
    names: dict[int, str] = {}
    chat_id = None
    topic_name = "Test"
    total_questions = 0
    last_message_id = None

    async with SessionLocal() as session:
        result = await session.execute(
            update(GroupQuiz)
            .where(GroupQuiz.id == gid, GroupQuiz.status == "active")
            .values(status="finished")
        )

        if (result.rowcount or 0) != 1:
            return False

        group_quiz = await session.get(GroupQuiz, gid)

        if not group_quiz:
            await session.rollback()
            return False

        chat_id = group_quiz.chat_id
        total_questions = int(group_quiz.total_questions)

        try:
            meta = json.loads(group_quiz.poll_map_json or "{}")
            last_message_id = meta.get("current_poll_message_id")
        except Exception:
            last_message_id = None

        topic = await session.get(Topic, group_quiz.topic_id)
        topic_name = topic.name if topic else "Test"

        participants = (
            await session.scalars(
                select(GroupQuizParticipant)
                .where(GroupQuizParticipant.group_quiz_id == gid)
                .order_by(
                    GroupQuizParticipant.score.desc(),
                    GroupQuizParticipant.answered.desc(),
                    GroupQuizParticipant.id.asc(),
                )
            )
        ).all()

        _users = {
            _u.id: _u
            for _u in (
                await session.scalars(
                    select(User).where(
                        User.id.in_([p.user_id for p in participants])
                    )
                )
            ).all()
        }  # tezlik2: foydalanuvchilar bitta so'rovda

        for participant in participants:
            user = _users.get(participant.user_id)

            if user:
                names[participant.user_id] = user.full_name or ""
                user.total_tests += 1
                user.total_questions += total_questions
                user.correct_answers += participant.score
                user.total_score += participant.score

        # Natijani umumiy test tarixiga ham yozamiz (hisobot, bilim
        # xaritasi, Smart Takrorlash uchun). Xato chiqsa ham guruh
        # natijasi e'lon qilinaveradi.
        try:
            async with session.begin_nested():
                from ..services.group_results import record_group_attempts

                await record_group_attempts(
                    session,
                    group_quiz,
                    list(participants),
                )
        except Exception as exc:
            print(
                ">>> GROUP HISTORY ERROR: "
                f"{type(exc).__name__}: {exc}",
                flush=True,
            )

        await session.commit()

    _cancel_timer(gid)
    await _stop_poll_quietly(bot, chat_id, last_message_id)

    rows = []

    for index, participant in enumerate(participants, 1):
        name = names.get(participant.user_id) or str(participant.user_id)

        medal = ["🥇", "🥈", "🥉"][index - 1] if index <= 3 else f"{index}."

        rows.append(
            f"{medal} <b>{html.escape(name or 'Foydalanuvchi')}</b> — "
            f"{participant.score}/{total_questions}"
        )

    text = (
        "🏆 <b>GURUH TESTI NATIJASI</b>\n\n"
        f"📚 <b>{html.escape(topic_name)}</b>\n"
        f"👥 Ishtirokchilar: <b>{len(participants)}</b>\n\n"
        + ("\n".join(rows) if rows else "Hali hech kim javob bermadi.")
    )

    try:
        await bot.send_message(chat_id, text[:4000])
    except Exception as exc:
        print(
            ">>> GROUP FINISH MESSAGE ERROR: "
            f"{type(exc).__name__}: {exc}",
            flush=True,
        )

    return True


async def _finish_group_quiz(bot, gid: int) -> bool:
    async with _group_lock(gid):
        return await _finish_group_quiz_locked(bot, gid)


async def _advance_group_question_locked(
    bot,
    gid: int,
    expected_poll_id: str | None = None,
) -> bool:
    async with SessionLocal() as session:
        group_quiz = await session.get(GroupQuiz, gid)

        if not group_quiz or group_quiz.status != "active":
            return False

        meta = json.loads(group_quiz.poll_map_json or "{}")

        current_poll_id = meta.get("current_poll_id")
        current_index = int(meta.get("current_index", 0))

        if (
            expected_poll_id is not None
            and str(current_poll_id) != str(expected_poll_id)
        ):
            return False

        question_ids = json.loads(group_quiz.question_ids_json or "[]")
        chat_id = group_quiz.chat_id
        open_period = int(meta.get("open_period") or 0)
        previous_message_id = meta.get("current_poll_message_id")

    _cancel_timer(gid)

    # Oldingi savol yopiladi: unga javob berish endi hisobga olinmaydi
    await _stop_poll_quietly(bot, chat_id, previous_message_id)

    next_index = current_index + 1

    if next_index >= len(question_ids):
        return await _finish_group_quiz_locked(bot, gid)

    try:
        sent = await _send_poll(
            bot,
            chat_id,
            gid,
            question_ids,
            next_index,
            open_period,
        )
    except Exception as exc:
        print(
            f">>> GROUP NEXT POLL ERROR: {type(exc).__name__}: {exc}",
            flush=True,
        )

        try:
            await bot.send_message(
                chat_id,
                "⚠️ Keyingi savolni yuborib bo'lmadi, test yakunlanmoqda.",
            )
        except Exception:
            pass

        await _finish_group_quiz_locked(bot, gid)
        return False

    if not sent:
        await _finish_group_quiz_locked(bot, gid)
        return False

    meta = _build_meta(sent, open_period)

    async with SessionLocal() as session:
        group_quiz = await session.get(GroupQuiz, gid)

        if not group_quiz or group_quiz.status != "active":
            return False

        group_quiz.poll_map_json = json.dumps(meta, ensure_ascii=False)

        await session.commit()

    _schedule_for_meta(bot, gid, meta)

    return True


async def _mark_start_failed(gid: int) -> None:
    async with SessionLocal() as session:
        result = await session.execute(
            update(GroupQuiz)
            .where(GroupQuiz.id == gid, GroupQuiz.status == "starting")
            .values(status="finished")
        )

        if (result.rowcount or 0) == 1:
            await session.commit()
        else:
            await session.rollback()


# ---------------------------------------------------------------
#  Boshlash
# ---------------------------------------------------------------

async def _start_group(
    message: Message,
    state: FSMContext,
    creator_id: int | None = None,
):
    chat_id = message.chat.id

    lock = _group_start_lock(chat_id)

    async with lock:
        try:
            return await _start_group_locked(message, state, creator_id)
        finally:
            if _GROUP_START_LOCKS.get(chat_id) is lock:
                _GROUP_START_LOCKS.pop(chat_id, None)


async def _start_group_locked(
    message: Message,
    state: FSMContext,
    creator_id: int | None = None,
):
    data = await state.get_data()

    topic_id = int(data["group_topic_id"])
    question_ids = list(data["group_question_ids"])
    open_period = int(data.get("group_open_period") or 0)

    if creator_id is None:
        creator_id = message.from_user.id

    if not question_ids:
        await message.answer("❌ Test uchun savollar topilmadi.")
        await state.clear()
        return

    # Eskirib qolgan (qotib qolgan) testni yopamiz, aks holda guruh
    # abadiy band bo'lib qolardi
    stale_id = None
    stale_status = None

    async with SessionLocal() as session:
        active = await session.scalar(
            select(GroupQuiz).where(
                GroupQuiz.chat_id == message.chat.id,
                GroupQuiz.status.in_(["starting", "active"]),
            )
        )

        if active:
            created = active.created_at or datetime.utcnow()
            age = (datetime.utcnow() - created).total_seconds()

            is_stale = (
                active.status == "starting" and age > STALE_STARTING_SECONDS
            ) or (
                active.status == "active" and age > STALE_ACTIVE_SECONDS
            )

            if not is_stale:
                await message.answer(
                    "⚠️ Bu guruhda allaqachon faol guruh testi bor.\n"
                    "To'xtatish: testdagi «⏹ To'xtatish» tugmasi yoki "
                    "/guruh_toxtat buyrug'i."
                )
                await state.clear()
                return

            stale_id = active.id
            stale_status = active.status

    if stale_id is not None:
        if stale_status == "starting":
            await _mark_start_failed(stale_id)
        else:
            await _finish_group_quiz(message.bot, stale_id)

    async with SessionLocal() as session:
        group_quiz = GroupQuiz(
            chat_id=message.chat.id,
            creator_telegram_id=creator_id,
            topic_id=topic_id,
            mode="question",
            question_ids_json=json.dumps(question_ids),
            poll_map_json=json.dumps(
                {"open_period": open_period},
                ensure_ascii=False,
            ),
            status="starting",
            total_questions=len(question_ids),
        )

        session.add(group_quiz)

        await session.flush()

        gid = group_quiz.id

        await session.commit()

    try:
        sent = await _send_poll(
            message.bot,
            message.chat.id,
            gid,
            question_ids,
            0,
            open_period,
        )
    except Exception as exc:
        await _mark_start_failed(gid)

        await message.answer(
            "❌ Birinchi guruh savolini yuborib bo‘lmadi.\n"
            f"<code>{html.escape(str(exc)[:500])}</code>"
        )

        await state.clear()
        return

    if not sent:
        await _mark_start_failed(gid)

        await message.answer("❌ Guruh testi uchun yaroqli savol topilmadi.")

        await state.clear()
        return

    meta = _build_meta(sent, open_period)

    try:
        async with SessionLocal() as session:
            db_group_quiz = await session.get(GroupQuiz, gid)

            if not db_group_quiz or db_group_quiz.status != "starting":
                raise RuntimeError("Guruh testi starting holatda topilmadi.")

            db_group_quiz.status = "active"
            db_group_quiz.poll_map_json = json.dumps(meta, ensure_ascii=False)

            await session.commit()

    except Exception as exc:
        await _stop_poll_quietly(
            message.bot, message.chat.id, sent["message_id"]
        )

        try:
            await message.bot.delete_message(
                message.chat.id,
                sent["message_id"],
            )
        except Exception:
            pass

        await _mark_start_failed(gid)

        await message.answer(
            "❌ Guruh testi bazada faollashtirilmadi.\n"
            f"<code>{html.escape(str(exc)[:500])}</code>"
        )

        await state.clear()
        return

    _schedule_for_meta(message.bot, gid, meta)

    await state.clear()

    if open_period:
        timing = (
            f"⏱ Har savolga <b>{open_period}</b> soniya.\n"
            "➡️ Hamma javob bersa yoki vaqt tugasa, keyingi savol chiqadi."
        )
    else:
        timing = (
            "♾️ Vaqt cheklanmagan.\n"
            "➡️ Keyingi savolga «⏭ Keyingi savol» tugmasi bilan o'tiladi "
            "(birinchi savoldan keyin hamma javob bersa, o'zi o'tadi)."
        )

    await message.answer(
        "👥 <b>GURUH TESTI BOSHLANDI!</b>\n\n"
        f"📝 Savollar: <b>{len(question_ids)}</b>\n"
        f"{timing}\n\n"
        "To'xtatish: «⏹ To'xtatish» tugmasi yoki /guruh_toxtat."
    )


async def prepare_group_quiz_from_deeplink(
    message: Message,
    state: FSMContext,
    topic_id: int,
) -> bool:
    if message.chat.type not in {"group", "supergroup"}:
        return False

    questions = await _get_valid_questions(topic_id)

    if not questions:
        await message.answer(
            "❌ Bu mavzuda guruh testi uchun yaroqli savollar topilmadi."
        )
        return True

    await state.clear()

    await state.update_data(
        group_topic_id=topic_id,
        available_count=len(questions),
        group_chat_id=message.chat.id,
    )

    if message.from_user is not None:
        await _remember_initiator(message.chat.id, message.from_user.id)

    await message.answer(
        "👥 <b>Guruh testi</b>\n\n"
        f"📊 Savollar: <b>{len(questions)}</b> ta.\n"
        "Savollar sonini tanlang.",
        reply_markup=_size_keyboard(len(questions), topic_id),
    )

    return True


# ---------------------------------------------------------------
#  Tugmalar: tanlash
# ---------------------------------------------------------------

@router.callback_query(F.data.startswith("gquiz:choose:"))
async def group_choose(callback: CallbackQuery, state: FSMContext):
    topic_id = int(callback.data.split(":")[-1])

    if callback.message.chat.type not in {"group", "supergroup"}:
        me = await callback.bot.get_me()

        if not me.username:
            await callback.answer("Bot username'i topilmadi.", show_alert=True)
            return

        async with SessionLocal() as session:
            pending = await session.scalar(
                select(GroupQuiz)
                .where(
                    GroupQuiz.creator_telegram_id == callback.from_user.id,
                    GroupQuiz.chat_id == callback.message.chat.id,
                    GroupQuiz.status == "pending",
                )
                .order_by(GroupQuiz.id.desc())
            )

            if pending is None:
                pending = GroupQuiz(
                    chat_id=callback.message.chat.id,
                    creator_telegram_id=callback.from_user.id,
                    topic_id=topic_id,
                    mode="question",
                    question_ids_json="[]",
                    poll_map_json=json.dumps(
                        {"pending": True},
                        ensure_ascii=False,
                    ),
                    status="pending",
                    total_questions=0,
                )
                session.add(pending)
            else:
                pending.topic_id = topic_id
                pending.question_ids_json = "[]"
                pending.poll_map_json = json.dumps(
                    {"pending": True},
                    ensure_ascii=False,
                )
                pending.total_questions = 0

            await session.commit()

        link = f"https://t.me/{me.username}?startgroup=gq_{topic_id}"

        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="➕ Guruhga qo‘shish va boshlash",
                        url=link,
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="⬅️ Orqaga",
                        callback_data=f"topic:{topic_id}",
                    )
                ],
            ]
        )

        await callback.message.answer(
            "👥 <b>Guruh testi</b>\n\n"
            "Testni guruhda ishlatish uchun botni kerakli guruhga qo‘shing.",
            reply_markup=keyboard,
        )

        await callback.answer()

        return

    if not await _is_controller(
        callback.bot, callback.message.chat.id, callback.from_user.id
    ):
        await callback.answer(NOT_ALLOWED, show_alert=True)
        return

    questions = await _get_valid_questions(topic_id)

    total = len(questions)

    if not total:
        await callback.answer(
            "Bu mavzuda guruh testi uchun mos savollar yo‘q.",
            show_alert=True,
        )
        return

    await state.update_data(group_topic_id=topic_id, available_count=total)

    async with SessionLocal() as session:
        topic = await session.get(Topic, topic_id)

    topic_name = topic.name if topic else "Mavzu"

    await callback.message.answer(
        "👥 <b>Guruh testi hajmi</b>\n\n"
        f"📖 {html.escape(topic_name)}\n"
        f"📋 Yaroqli savollar: <b>{total}</b>\n\n"
        "Nechta savol ishlansin?",
        reply_markup=_size_keyboard(total, topic_id),
    )

    await callback.answer()


async def _ask_time(message: Message, topic_id: int, size: int):
    """Vaqt menyusini shu xabarning o'zida ko'rsatadi (yangi xabar
    qo'shilmaydi, test boshlanganda o'chiriladi)."""
    text = (
        "⏱ <b>Har savolga qancha vaqt beriladi?</b>\n\n"
        f"📝 Savollar: <b>{size}</b> ta\n\n"
        "Vaqt tugasa yoki hamma javob bersa, keyingi savol chiqadi. "
        "Cheklovsiz rejimda «⏭ Keyingi savol» tugmasi bosiladi."
    )

    try:
        await message.edit_text(text, reply_markup=_time_keyboard(topic_id))
    except Exception:
        await message.answer(text, reply_markup=_time_keyboard(topic_id))


@router.callback_query(F.data == "gquiz:cancel")
async def group_cancel(callback: CallbackQuery, state: FSMContext):
    if not await _is_controller(
        callback.bot, callback.message.chat.id, callback.from_user.id
    ):
        await callback.answer(NOT_ALLOWED, show_alert=True)
        return

    await state.clear()

    try:
        await callback.message.delete()
    except Exception:
        pass

    await callback.answer("Bekor qilindi.")


@router.callback_query(F.data.startswith("gquiz:size:"))
async def group_size(callback: CallbackQuery, state: FSMContext):
    _, _, topic_id_raw, action = callback.data.split(":")

    topic_id = int(topic_id_raw)

    if not await _is_controller(
        callback.bot, callback.message.chat.id, callback.from_user.id
    ):
        await callback.answer(NOT_ALLOWED, show_alert=True)
        return

    questions = await _get_valid_questions(topic_id)

    total = len(questions)

    if not total:
        await callback.answer(
            "Bu mavzuda yaroqli savollar qolmagan.",
            show_alert=True,
        )
        return

    if action == "custom":
        await state.update_data(
            group_topic_id=topic_id,
            available_count=total,
            group_pad="",
        )

        await callback.answer()

        try:
            await callback.message.edit_text(
                _pad_text(total, ""),
                reply_markup=_pad_keyboard(topic_id),
            )
        except Exception:
            pass

        return

    size = int(action)

    if size < 1 or size > total:
        await callback.answer(f"1 dan {total} gacha tanlang.", show_alert=True)
        return

    selected = random.sample(questions, size)

    await state.update_data(
        group_topic_id=topic_id,
        group_question_ids=[question.id for question in selected],
    )

    await callback.answer()

    await _ask_time(callback.message, topic_id, size)


@router.callback_query(F.data.startswith("gquiz:pad:"))
async def group_pad(callback: CallbackQuery, state: FSMContext):
    try:
        _, _, topic_id_raw, key = callback.data.split(":")
        topic_id = int(topic_id_raw)
    except ValueError:
        await callback.answer("Noto'g'ri so'rov.", show_alert=True)
        return

    if not await _is_controller(
        callback.bot, callback.message.chat.id, callback.from_user.id
    ):
        await callback.answer(NOT_ALLOWED, show_alert=True)
        return

    data = await state.get_data()

    total = int(data.get("available_count") or 0)
    value = str(data.get("group_pad") or "")

    if int(data.get("group_topic_id") or 0) != topic_id or not total:
        await callback.answer(
            "Menyu eskirgan. Qaytadan boshlang.", show_alert=True
        )
        return

    if key == "del":
        value = value[:-1]

    elif key.isdigit():
        candidate = (value + key).lstrip("0")

        if len(candidate) > len(str(total)):
            await callback.answer()
            return

        value = candidate

    elif key == "ok":
        size = int(value) if value else 0

        if size < 1 or size > total:
            await callback.answer(
                f"1 dan {total} gacha son kiriting.", show_alert=True
            )
            return

        questions = await _get_valid_questions(topic_id)

        if size > len(questions):
            await callback.answer(
                f"Hozir yaroqli savollar {len(questions)} ta.",
                show_alert=True,
            )
            return

        selected = random.sample(questions, size)

        await state.update_data(
            group_question_ids=[question.id for question in selected]
        )

        await callback.answer()
        await _ask_time(callback.message, topic_id, size)
        return

    else:
        await callback.answer()
        return

    await state.update_data(group_pad=value)

    await callback.answer()

    try:
        await callback.message.edit_text(
            _pad_text(total, value),
            reply_markup=_pad_keyboard(topic_id),
        )
    except Exception:
        pass


@router.callback_query(F.data.startswith("gquiz:time:"))
async def group_time(callback: CallbackQuery, state: FSMContext):
    try:
        _, _, topic_id_raw, seconds_raw = callback.data.split(":")
        topic_id = int(topic_id_raw)
        seconds = int(seconds_raw)
    except ValueError:
        await callback.answer("Noto'g'ri so'rov.", show_alert=True)
        return

    if seconds != 0 and seconds not in TIME_CHOICES:
        await callback.answer("Noto'g'ri vaqt.", show_alert=True)
        return

    if not await _is_controller(
        callback.bot, callback.message.chat.id, callback.from_user.id
    ):
        await callback.answer(NOT_ALLOWED, show_alert=True)
        return

    data = await state.get_data()

    if (
        not data.get("group_question_ids")
        or int(data.get("group_topic_id") or 0) != topic_id
    ):
        await callback.answer(
            "Avval savollar sonini tanlang. Vaqtni sonni tanlagan "
            "foydalanuvchi belgilaydi.",
            show_alert=True,
        )
        return

    await state.update_data(group_open_period=seconds)

    await callback.answer("Test boshlanmoqda...")

    menu_message = callback.message

    await _start_group(
        menu_message,
        state,
        creator_id=callback.from_user.id,
    )

    # Menyu xabari qolib ketmasin
    try:
        await menu_message.delete()
    except Exception:
        pass


# ---------------------------------------------------------------
#  Boshqaruv: keyingi savol, to'xtatish
# ---------------------------------------------------------------

async def _load_active_quiz(gid: int):
    async with SessionLocal() as session:
        group_quiz = await session.get(GroupQuiz, gid)

        if group_quiz is None:
            return None

        try:
            meta = json.loads(group_quiz.poll_map_json or "{}")
        except Exception:
            meta = {}

        return {
            "status": group_quiz.status,
            "chat_id": group_quiz.chat_id,
            "creator": group_quiz.creator_telegram_id,
            "meta": meta,
        }


@router.callback_query(F.data.startswith("gq:next:"))
async def group_next(callback: CallbackQuery):
    try:
        gid = int(callback.data.split(":")[-1])
    except ValueError:
        await callback.answer("Noto'g'ri so'rov.", show_alert=True)
        return

    info = await _load_active_quiz(gid)

    if info is None or info["status"] != "active":
        await callback.answer("Bu test faol emas.", show_alert=True)
        return

    if not await _is_controller(
        callback.bot, info["chat_id"], callback.from_user.id, info["creator"]
    ):
        await callback.answer(NOT_ALLOWED, show_alert=True)
        return

    current_message = info["meta"].get("current_poll_message_id")

    if (
        callback.message is not None
        and current_message
        and int(current_message) != callback.message.message_id
    ):
        await callback.answer("Bu savol allaqachon o'tgan.", show_alert=True)
        return

    await callback.answer()

    async with _group_lock(gid):
        await _advance_group_question_locked(
            callback.bot,
            gid,
            expected_poll_id=str(info["meta"].get("current_poll_id")),
        )


@router.callback_query(F.data.startswith("gq:stop:"))
async def group_stop(callback: CallbackQuery):
    try:
        gid = int(callback.data.split(":")[-1])
    except ValueError:
        await callback.answer("Noto'g'ri so'rov.", show_alert=True)
        return

    info = await _load_active_quiz(gid)

    if info is None or info["status"] != "active":
        await callback.answer("Bu test faol emas.", show_alert=True)
        return

    if not await _is_controller(
        callback.bot, info["chat_id"], callback.from_user.id, info["creator"]
    ):
        await callback.answer(NOT_ALLOWED, show_alert=True)
        return

    await callback.answer("Test yakunlanmoqda...")

    await _finish_group_quiz(callback.bot, gid)


@router.message(Command("guruh_toxtat"))
async def group_stop_command(message: Message):
    if message.chat.type not in {"group", "supergroup"}:
        return

    async with SessionLocal() as session:
        quiz = await session.scalar(
            select(GroupQuiz)
            .where(
                GroupQuiz.chat_id == message.chat.id,
                GroupQuiz.status.in_(["starting", "active"]),
            )
            .order_by(GroupQuiz.id.desc())
        )

        quiz_id = quiz.id if quiz else None
        quiz_status = quiz.status if quiz else None
        creator = quiz.creator_telegram_id if quiz else None

    if quiz_id is None:
        await message.answer("Bu guruhda faol guruh testi yo'q.")
        return

    if message.from_user is None or not await _is_controller(
        message.bot, message.chat.id, message.from_user.id, creator
    ):
        await message.answer(NOT_ALLOWED)
        return

    if quiz_status == "starting":
        await _mark_start_failed(quiz_id)
        await message.answer("Test to'xtatildi.")
        return

    await _finish_group_quiz(message.bot, quiz_id)


# ---------------------------------------------------------------
#  Javoblar
# ---------------------------------------------------------------

@router.poll_answer()
async def on_poll_answer(poll_answer: PollAnswer, bot):
    poll_id = poll_answer.poll_id
    user_id = poll_answer.user.id
    selected = poll_answer.option_ids

    gid = None

    try:
        async with SessionLocal() as session:
            active = (
                await session.scalars(
                    select(GroupQuiz).where(
                        GroupQuiz.status == "active",
                        GroupQuiz.poll_map_json.contains(str(poll_id)),
                    )
                )
            ).all()

            for candidate in active:
                try:
                    meta = json.loads(candidate.poll_map_json or "{}")
                except Exception:
                    continue

                if str(meta.get("current_poll_id")) == str(poll_id):
                    gid = candidate.id
                    break

        if gid is None:
            return

        async with _group_lock(gid):
            question_id = None
            early_advance = False

            async with SessionLocal() as session:
                group_quiz = await session.get(GroupQuiz, gid)

                if not group_quiz or group_quiz.status != "active":
                    return

                meta = json.loads(group_quiz.poll_map_json or "{}")

                if str(meta.get("current_poll_id")) != str(poll_id):
                    return

                info = (meta.get("questions") or {}).get(str(poll_id))

                if not info:
                    return

                question_id = int(info["qid"])
                correct_idx = int(info["correct_idx"])

                selected_idx = selected[0] if selected else -1

                is_correct = selected_idx == correct_idx

                user = await session.scalar(
                    select(User).where(User.telegram_id == user_id)
                )

                if (
                    user
                    and user.is_blocked
                    and user.telegram_id not in settings.admins
                ):
                    return

                if not user:
                    user = User(
                        telegram_id=user_id,
                        username=poll_answer.user.username,
                        full_name=poll_answer.user.full_name
                        or "Foydalanuvchi",
                    )

                    session.add(user)

                    await session.flush()

                participant = await session.scalar(
                    select(GroupQuizParticipant).where(
                        GroupQuizParticipant.group_quiz_id == gid,
                        GroupQuizParticipant.user_id == user.id,
                    )
                )

                if not participant:
                    participant = GroupQuizParticipant(
                        group_quiz_id=gid,
                        user_id=user.id,
                    )

                    session.add(participant)

                    await session.flush()

                existing_answer = await session.scalar(
                    select(GroupQuizAnswer).where(
                        GroupQuizAnswer.group_quiz_id == gid,
                        GroupQuizAnswer.participant_id == participant.id,
                        GroupQuizAnswer.question_id == question_id,
                    )
                )

                if existing_answer is None:
                    session.add(
                        GroupQuizAnswer(
                            group_quiz_id=gid,
                            participant_id=participant.id,
                            question_id=question_id,
                            selected_option=str(selected_idx),
                            is_correct=is_correct,
                        )
                    )

                    participant.answered += 1
                    participant.score += int(is_correct)

                await session.commit()

                # Hamma ilgari qatnashganlar shu savolga ham javob
                # bergan bo'lsa, vaqtni kutmasdan keyingisiga o'tamiz
                previous_ids = set(
                    (
                        await session.scalars(
                            select(GroupQuizAnswer.participant_id)
                            .where(
                                GroupQuizAnswer.group_quiz_id == gid,
                                GroupQuizAnswer.question_id != question_id,
                            )
                            .distinct()
                        )
                    ).all()
                )

                current_ids = set(
                    (
                        await session.scalars(
                            select(GroupQuizAnswer.participant_id).where(
                                GroupQuizAnswer.group_quiz_id == gid,
                                GroupQuizAnswer.question_id == question_id,
                            )
                        )
                    ).all()
                )

                if previous_ids and previous_ids <= current_ids:
                    early_advance = True

            if early_advance:
                _schedule_timer(bot, gid, poll_id, EARLY_ADVANCE_DELAY)

    except asyncio.CancelledError:
        raise

    except Exception as exc:
        print(
            f">>> GROUP ANSWER ERROR: {type(exc).__name__}: {exc}",
            flush=True,
        )


# ---------------------------------------------------------------
#  Bot qayta ishga tushganda
# ---------------------------------------------------------------

async def recover_active_group_quizzes(bot) -> int:
    """Qayta ishga tushishdan oldingi guruh testlarini tiklaydi.

    Taymerli testlar uchun qolgan vaqt hisoblanib, taymer qayta
    ishga tushiriladi. Boshlanish payti uzilgan testlar yopiladi.
    """
    async with SessionLocal() as session:
        await session.execute(
            update(GroupQuiz)
            .where(GroupQuiz.status == "starting")
            .values(status="finished")
        )

        await session.commit()

        active = (
            await session.scalars(
                select(GroupQuiz).where(GroupQuiz.status == "active")
            )
        ).all()

    restored = 0

    for group_quiz in active:
        try:
            meta = json.loads(group_quiz.poll_map_json or "{}")
        except Exception:
            meta = {}

        if not meta.get("current_poll_id"):
            await _finish_group_quiz(bot, group_quiz.id)
            continue

        restored += 1

        open_period = int(meta.get("open_period") or 0)
        deadline = meta.get("deadline_ts")

        if open_period and deadline:
            delay = max(1.0, float(deadline) - time.time() + 1.0)

            _schedule_timer(bot, group_quiz.id, meta["current_poll_id"], delay)

    return restored

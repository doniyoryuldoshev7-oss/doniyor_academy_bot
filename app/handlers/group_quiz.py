from pathlib import Path
import asyncio
import html
import json
import random

from aiogram import F, Router
from aiogram.exceptions import TelegramNetworkError, TelegramRetryAfter
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    ForceReply,
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
from ..states import GroupQuizState


router = Router()

_GROUP_LOCKS: dict[int, asyncio.Lock] = {}
_GROUP_START_LOCKS: dict[int, asyncio.Lock] = {}


def _group_lock(gid: int) -> asyncio.Lock:
    return _GROUP_LOCKS.setdefault(gid, asyncio.Lock())


def _group_start_lock(chat_id: int) -> asyncio.Lock:
    return _GROUP_START_LOCKS.setdefault(chat_id, asyncio.Lock())


def _valid_question(question: Question) -> bool:
    correct = (question.correct_option or "").strip().upper()

    if question.image_path:
        try:
            image_exists = Path(
                question.image_path
            ).is_file()
        except (OSError, TypeError):
            image_exists = False

        return bool(
            image_exists
            and correct in {"A", "B", "C", "D"}
        )

    text = (question.text or "").strip()
    options = [
        (question.option_a or "").strip(),
        (question.option_b or "").strip(),
        (question.option_c or "").strip(),
        (question.option_d or "").strip(),
    ]

    return bool(
        text
        and all(options)
        and correct in {"A", "B", "C", "D"}
    )


def _poll_payload(question: Question):
    if not _valid_question(question):
        return None

    if question.image_path:
        order = ["A", "B", "C", "D"]
        option_map = {
            "A": "A",
            "B": "B",
            "C": "C",
            "D": "D",
        }
    else:
        option_map = {
            "A": (question.option_a or "").strip(),
            "B": (question.option_b or "").strip(),
            "C": (question.option_c or "").strip(),
            "D": (question.option_d or "").strip(),
        }
        order = list(option_map)
        random.shuffle(order)

    options = [option_map[key] for key in order]
    correct_idx = order.index(
        (question.correct_option or "").strip().upper()
    )

    return options, correct_idx


async def _get_valid_questions(topic_id: int):
    async with SessionLocal() as session:
        questions = (
            await session.scalars(
                select(Question).where(Question.topic_id == topic_id)
            )
        ).all()

    return [
        question
        for question in questions
        if _valid_question(question)
    ]


def _size_keyboard(total: int, topic_id: int) -> InlineKeyboardMarkup:
    presets = [
        n for n in (3, 5, 10, 15, 20, 25, 30, 40, 50)
        if n <= total
    ]

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
                text="⬅️ Orqaga",
                callback_data=f"topic:{topic_id}",
            )
        ]
    )

    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _finish_group_quiz_locked(
    bot,
    gid: int,
) -> bool:
    participants = []
    chat_id = None
    topic_name = "Test"
    total_questions = 0

    async with SessionLocal() as session:
        result = await session.execute(
            update(GroupQuiz)
            .where(
                GroupQuiz.id == gid,
                GroupQuiz.status == "active",
            )
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

        topic = await session.get(
            Topic,
            group_quiz.topic_id,
        )
        topic_name = topic.name if topic else "Test"

        participants = (
            await session.scalars(
                select(GroupQuizParticipant)
                .where(
                    GroupQuizParticipant.group_quiz_id == gid
                )
                .order_by(
                    GroupQuizParticipant.score.desc(),
                    GroupQuizParticipant.answered.desc(),
                    GroupQuizParticipant.id.asc(),
                )
            )
        ).all()

        for participant in participants:
            user = await session.get(
                User,
                participant.user_id,
            )

            if user:
                user.total_tests += 1
                user.total_questions += total_questions
                user.correct_answers += participant.score
                user.total_score += participant.score

        await session.commit()

    rows = []

    for index, participant in enumerate(participants, 1):
        async with SessionLocal() as session:
            user = await session.get(
                User,
                participant.user_id,
            )

        name = (
            user.full_name
            if user
            else str(participant.user_id)
        ) or "Foydalanuvchi"

        medal = (
            ["🥇", "🥈", "🥉"][index - 1]
            if index <= 3
            else f"{index}."
        )

        rows.append(
            f"{medal} <b>{html.escape(name)}</b> — "
            f"{participant.score}/{total_questions}"
        )

    text = (
        "🏆 <b>GURUH TESTI NATIJASI</b>\n\n"
        f"📚 <b>{html.escape(topic_name)}</b>\n"
        f"👥 Ishtirokchilar: <b>{len(participants)}</b>\n\n"
        + (
            "\n".join(rows)
            if rows
            else "Hali hech kim javob bermadi."
        )
    )

    try:
        await bot.send_message(
            chat_id,
            text,
        )
    except Exception as exc:
        print(
            ">>> GROUP FINISH MESSAGE ERROR: "
            f"{type(exc).__name__}: {exc}",
            flush=True,
        )

    return True


async def _finish_group_quiz(
    bot,
    gid: int,
) -> bool:
    async with _group_lock(gid):
        return await _finish_group_quiz_locked(
            bot,
            gid,
        )


async def _send_group_poll(
    bot,
    group_quiz,
    index: int,
):
    question_ids = json.loads(
        group_quiz.question_ids_json or "[]"
    )

    if index >= len(question_ids):
        return None

    question_id = int(
        question_ids[index]
    )

    async with SessionLocal() as session:
        question = await session.get(
            Question,
            question_id,
        )

    if not question:
        return None

    payload = _poll_payload(question)

    if not payload:
        return None

    options, correct_idx = payload

    if question.image_path:
        await bot.send_photo(
            chat_id=group_quiz.chat_id,
            photo=FSInputFile(
                question.image_path
            ),
        )
        poll_question = "Rasmga qarab javobni tanlang."
    else:
        poll_question = (
            (question.text or "").strip()[:300]
        )

    poll = await bot.send_poll(
        chat_id=group_quiz.chat_id,
        question=poll_question,
        options=options,
        type="quiz",
        is_anonymous=False,
        correct_option_id=correct_idx,
        explanation=(
            "Doniyor Academy - "
            "Javob berilgach keyingi savol chiqadi."
        ),
        allows_multiple_answers=False,
    )

    return (
        poll.poll.id,
        poll.message_id,
        question_id,
        correct_idx,
    )


async def _send_next_poll(
    bot,
    snapshot: dict,
    index: int,
):
    class GroupQuizSnapshot:
        pass

    group_quiz = GroupQuizSnapshot()

    for key, value in snapshot.items():
        setattr(
            group_quiz,
            key,
            value,
        )

    question_ids = json.loads(
        group_quiz.question_ids_json or "[]"
    )

    if index >= len(question_ids):
        return None

    question_id = int(
        question_ids[index]
    )

    async with SessionLocal() as session:
        question = await session.get(
            Question,
            question_id,
        )

    if not question:
        return None

    payload = _poll_payload(question)

    if not payload:
        return None

    options, correct_idx = payload

    if question.image_path:
        network_attempt = 0
        retry_after_attempt = 0

        while True:
            try:
                await bot.send_photo(
                    chat_id=group_quiz.chat_id,
                    photo=FSInputFile(
                        question.image_path
                    ),
                )
                break

            except TelegramRetryAfter as exc:
                retry_after_attempt += 1

                if retry_after_attempt >= 2:
                    raise

                await asyncio.sleep(
                    max(
                        1,
                        int(exc.retry_after),
                    )
                )

            except TelegramNetworkError as exc:
                network_attempt += 1

                delay = min(
                    10,
                    2 * network_attempt,
                )

                print(
                    ">>> GROUP IMAGE NETWORK RETRY: "
                    f"{type(exc).__name__}: {exc} "
                    f"| retry={network_attempt} "
                    f"| delay={delay}s",
                    flush=True,
                )

                await asyncio.sleep(delay)

        poll_question = (
            "Rasmga qarab javobni tanlang."
        )
    else:
        poll_question = (
            (question.text or "").strip()[:300]
        )

    retry_after_attempt = 0
    network_attempt = 0

    while True:
        try:
            poll = await bot.send_poll(
                chat_id=group_quiz.chat_id,
                question=poll_question,
                options=options,
                type="quiz",
                is_anonymous=False,
                correct_option_id=correct_idx,
                explanation=(
                    "Doniyor Academy - "
                    "Javob berilgach keyingi savol chiqadi."
                ),
                allows_multiple_answers=False,
            )

            return (
                poll.poll.id,
                poll.message_id,
                question_id,
                correct_idx,
            )

        except TelegramRetryAfter as exc:
            retry_after_attempt += 1

            if retry_after_attempt >= 2:
                raise

            await asyncio.sleep(
                max(
                    1,
                    int(exc.retry_after),
                )
            )

        except TelegramNetworkError as exc:
            network_attempt += 1

            delay = min(
                10,
                2 * network_attempt,
            )

            print(
                ">>> GROUP NEXT POLL NETWORK RETRY: "
                f"{type(exc).__name__}: {exc} "
                f"| retry={network_attempt} "
                f"| delay={delay}s",
                flush=True,
            )

            await asyncio.sleep(delay)


async def _advance_group_question_locked(
    bot,
    gid: int,
    expected_poll_id: str | None = None,
) -> bool:
    async with SessionLocal() as session:
        group_quiz = await session.get(
            GroupQuiz,
            gid,
        )

        if (
            not group_quiz
            or group_quiz.status != "active"
        ):
            return False

        meta = json.loads(
            group_quiz.poll_map_json or "{}"
        )

        current_poll_id = meta.get(
            "current_poll_id"
        )

        current_index = int(
            meta.get(
                "current_index",
                0,
            )
        )

        if (
            expected_poll_id is not None
            and str(current_poll_id)
            != str(expected_poll_id)
        ):
            return False

        question_ids = json.loads(
            group_quiz.question_ids_json or "[]"
        )

        next_index = current_index + 1

        if next_index >= len(question_ids):
            should_finish = True
            snapshot = None
        else:
            should_finish = False

            snapshot = {
                "id": group_quiz.id,
                "chat_id": group_quiz.chat_id,
                "question_ids_json":
                    group_quiz.question_ids_json,
                "total_questions":
                    group_quiz.total_questions,
            }

    if should_finish:
        return await _finish_group_quiz_locked(
            bot,
            gid,
        )

    try:
        result = await _send_next_poll(
            bot,
            snapshot,
            next_index,
        )
    except Exception as exc:
        print(
            ">>> GROUP NEXT POLL ERROR: "
            f"{type(exc).__name__}: {exc}",
            flush=True,
        )

        await _finish_group_quiz_locked(
            bot,
            gid,
        )

        return False

    if not result:
        await _finish_group_quiz_locked(
            bot,
            gid,
        )
        return False

    (
        poll_id,
        poll_message_id,
        question_id,
        correct_idx,
    ) = result

    async with SessionLocal() as session:
        group_quiz = await session.get(
            GroupQuiz,
            gid,
        )

        if (
            not group_quiz
            or group_quiz.status != "active"
        ):
            return False

        group_quiz.poll_map_json = json.dumps(
            {
                "current_index": next_index,
                "current_poll_id": poll_id,
                "current_poll_message_id":
                    poll_message_id,
                "questions": {
                    str(poll_id): {
                        "qid": question_id,
                        "correct_idx": correct_idx,
                    }
                },
            },
            ensure_ascii=False,
        )

        await session.commit()

    return True


async def _mark_start_failed(gid: int) -> None:
    async with SessionLocal() as session:
        result = await session.execute(
            update(GroupQuiz)
            .where(
                GroupQuiz.id == gid,
                GroupQuiz.status == "starting",
            )
            .values(status="finished")
        )

        if (result.rowcount or 0) == 1:
            await session.commit()
        else:
            await session.rollback()


async def _start_group(
    message: Message,
    state: FSMContext,
):
    chat_id = message.chat.id

    lock = _group_start_lock(
        chat_id
    )

    async with lock:
        try:
            return await _start_group_locked(
                message,
                state,
            )
        finally:
            if (
                _GROUP_START_LOCKS.get(chat_id)
                is lock
            ):
                _GROUP_START_LOCKS.pop(
                    chat_id,
                    None,
                )


async def _start_group_locked(
    message: Message,
    state: FSMContext,
):
    data = await state.get_data()

    topic_id = int(
        data["group_topic_id"]
    )

    question_ids = list(
        data["group_question_ids"]
    )

    if not question_ids:
        await message.answer(
            "❌ Test uchun savollar topilmadi."
        )
        await state.clear()
        return

    async with SessionLocal() as session:
        active = await session.scalar(
            select(GroupQuiz).where(
                GroupQuiz.chat_id
                == message.chat.id,
                GroupQuiz.status.in_(
                    ["starting", "active"]
                ),
            )
        )

        if active:
            await message.answer(
                "⚠️ Bu guruhda allaqachon "
                "faol guruh testi bor."
            )
            await state.clear()
            return

        group_quiz = GroupQuiz(
            chat_id=message.chat.id,
            creator_telegram_id=
                message.from_user.id,
            topic_id=topic_id,
            mode="question",
            question_ids_json=
                json.dumps(
                    question_ids
                ),
            poll_map_json=
                json.dumps(
                    {"no_timer": True},
                    ensure_ascii=False,
                ),
            status="starting",
            total_questions=
                len(question_ids),
        )

        session.add(group_quiz)

        await session.flush()

        gid = group_quiz.id

        await session.commit()

    try:
        result = await _send_group_poll(
            message.bot,
            group_quiz,
            0,
        )
    except Exception as exc:
        await _mark_start_failed(
            gid
        )

        await message.answer(
            "❌ Birinchi guruh savolini "
            "yuborib bo‘lmadi.\n"
            f"<code>{html.escape(str(exc)[:500])}</code>"
        )

        await state.clear()
        return

    if not result:
        await _mark_start_failed(
            gid
        )

        await message.answer(
            "❌ Birinchi guruh savoli yaroqsiz."
        )

        await state.clear()
        return

    (
        poll_id,
        poll_message_id,
        question_id,
        correct_idx,
    ) = result

    poll_meta = {
        "current_index": 0,
        "current_poll_id": poll_id,
        "current_poll_message_id":
            poll_message_id,
        "questions": {
            str(poll_id): {
                "qid": question_id,
                "correct_idx": correct_idx,
            }
        },
    }

    try:
        async with SessionLocal() as session:
            db_group_quiz = await session.get(
                GroupQuiz,
                gid,
            )

            if (
                not db_group_quiz
                or db_group_quiz.status
                != "starting"
            ):
                raise RuntimeError(
                    "Guruh testi starting "
                    "holatda topilmadi."
                )

            db_group_quiz.status = "active"

            db_group_quiz.poll_map_json = (
                json.dumps(
                    poll_meta,
                    ensure_ascii=False,
                )
            )

            await session.commit()

    except Exception as exc:
        try:
            await message.bot.delete_message(
                message.chat.id,
                poll_message_id,
            )
        except Exception:
            pass

        await _mark_start_failed(
            gid
        )

        await message.answer(
            "❌ Guruh testi bazada "
            "faollashtirilmadi.\n"
            f"<code>{html.escape(str(exc)[:500])}</code>"
        )

        await state.clear()
        return

    await state.clear()

    await message.answer(
        "👥 <b>GURUH TESTI BOSHLANDI!</b>\n\n"
        f"📝 Savollar: <b>{len(question_ids)}</b>\n"
        "♾️ Vaqt cheklanmagan.\n"
        "➡️ Javob berilgach keyingi savol chiqadi."
    )


async def prepare_group_quiz_from_deeplink(
    message: Message,
    state: FSMContext,
    topic_id: int,
) -> bool:
    if message.chat.type not in {
        "group",
        "supergroup",
    }:
        return False

    questions = await _get_valid_questions(
        topic_id
    )

    if not questions:
        await message.answer(
            f"{chr(0x274C)} Bu mavzuda guruh testi uchun "
            "yaroqli savollar topilmadi."
        )
        return True

    await state.clear()

    await state.set_state(
        GroupQuizState.waiting_size
    )

    await state.update_data(
        group_topic_id=topic_id,
        available_count=len(questions),
        group_chat_id=message.chat.id,
    )

    await message.answer(
        f"{chr(0x1F465)} <b>Guruh testi</b>\n\n"
        f"{chr(0x1F4CA)} Savollar: <b>{len(questions)}</b> ta.\n"
        "Savollar sonini tanlang.\n\n"
        f"{chr(0x267E)} Vaqt cheklanmagan.",
        reply_markup=_size_keyboard(
            len(questions),
            topic_id,
        ),
    )

    return True


@router.callback_query(
    F.data.startswith("gquiz:choose:")
)
async def group_choose(
    callback: CallbackQuery,
    state: FSMContext,
):
    topic_id = int(
        callback.data.split(":")[-1]
    )

    if callback.message.chat.type not in {
        "group",
        "supergroup",
    }:
        me = await callback.bot.get_me()

        if not me.username:
            await callback.answer(
                "Bot username'i topilmadi.",
                show_alert=True,
            )
            return

        async with SessionLocal() as session:
            pending = await session.scalar(
                select(GroupQuiz)
                .where(
                    GroupQuiz.creator_telegram_id
                    == callback.from_user.id,
                    GroupQuiz.chat_id
                    == callback.message.chat.id,
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

        link = (
            f"https://t.me/{me.username}"
            f"?startgroup=gq_{topic_id}"
        )

        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="➕ Guruhga qo‘shish "
                             "va boshlash",
                        url=link,
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="⬅️ Orqaga",
                        callback_data=
                            f"topic:{topic_id}",
                    )
                ],
            ]
        )

        await callback.message.answer(
            "👥 <b>Guruh testi</b>\n\n"
            "Testni guruhda ishlatish uchun "
            "botni kerakli guruhga qo‘shing.",
            reply_markup=keyboard,
        )

        await callback.answer()

        return

    questions = await _get_valid_questions(
        topic_id
    )

    total = len(questions)

    if not total:
        await callback.answer(
            "Bu mavzuda guruh testi uchun "
            "mos savollar yo‘q.",
            show_alert=True,
        )
        return

    await state.update_data(
        group_topic_id=topic_id,
        available_count=total,
    )

    async with SessionLocal() as session:
        topic = await session.get(
            Topic,
            topic_id,
        )

    topic_name = (
        topic.name
        if topic
        else "Mavzu"
    )

    await callback.message.answer(
        "👥 <b>Guruh testi hajmi</b>\n\n"
        f"📖 {html.escape(topic_name)}\n"
        f"📋 Yaroqli savollar: "
        f"<b>{total}</b>\n\n"
        "Nechta savol ishlansin?",
        reply_markup=
            _size_keyboard(
                total,
                topic_id,
            ),
    )

    await callback.answer()


@router.callback_query(
    F.data.startswith("gquiz:size:")
)
async def group_size(
    callback: CallbackQuery,
    state: FSMContext,
):
    _, _, topic_id_raw, action = (
        callback.data.split(":")
    )

    topic_id = int(
        topic_id_raw
    )

    questions = await _get_valid_questions(
        topic_id
    )

    total = len(questions)

    if not total:
        await callback.answer(
            "Bu mavzuda yaroqli savollar qolmagan.",
            show_alert=True,
        )
        return

    if action == "custom":
        await state.set_state(
            GroupQuizState.waiting_size
        )

        await state.update_data(
            group_topic_id=topic_id,
            available_count=total,
        )

        await callback.message.answer(
            "✍️ <b>Savollar sonini kiriting</b>\n\n"
            f"1 dan {total} gacha son yuboring.",
            reply_markup=ForceReply(
                input_field_placeholder=
                    f"1–{total}",
            ),
        )

        await callback.answer()
        return

    size = int(action)

    if size < 1 or size > total:
        await callback.answer(
            f"1 dan {total} gacha tanlang.",
            show_alert=True,
        )
        return

    selected = random.sample(
        questions,
        size,
    )

    await state.update_data(
        group_topic_id=topic_id,
        group_question_ids=[
            question.id
            for question in selected
        ],
    )

    await _start_group(
        callback.message,
        state,
    )

    await callback.answer()


@router.message(
    GroupQuizState.waiting_size
)
async def group_receive_size(
    message: Message,
    state: FSMContext,
):
    if message.chat.type not in {
        "group",
        "supergroup",
    }:
        return

    data = await state.get_data()

    topic_id = int(
        data.get(
            "group_topic_id",
            0,
        )
    )

    total = int(
        data.get(
            "available_count",
            0,
        )
    )

    try:
        size = int(
            (message.text or "").strip()
        )
    except ValueError:
        size = 0

    if size < 1 or size > total:
        await message.answer(
            f"❌ 1 dan {total} gacha "
            "son kiriting."
        )
        return

    questions = await _get_valid_questions(
        topic_id
    )

    if size > len(questions):
        await message.answer(
            f"❌ Hozir yaroqli savollar "
            f"{len(questions)} ta."
        )
        return

    selected = random.sample(
        questions,
        size,
    )

    await state.update_data(
        group_question_ids=[
            question.id
            for question in selected
        ]
    )

    await _start_group(
        message,
        state,
    )


@router.poll_answer()
async def on_poll_answer(
    poll_answer: PollAnswer,
    bot,
):
    poll_id = poll_answer.poll_id
    user_id = poll_answer.user.id
    selected = poll_answer.option_ids

    gid = None

    try:
        async with SessionLocal() as session:
            active = (
                await session.scalars(
                    select(GroupQuiz).where(
                        GroupQuiz.status
                        == "active"
                    )
                )
            ).all()

            for candidate in active:
                try:
                    meta = json.loads(
                        candidate.poll_map_json
                        or "{}"
                    )
                except Exception:
                    continue

                if (
                    str(
                        meta.get(
                            "current_poll_id"
                        )
                    )
                    == str(poll_id)
                ):
                    gid = candidate.id
                    break

        if gid is None:
            return

        async with _group_lock(gid):
            async with SessionLocal() as session:
                group_quiz = await session.get(
                    GroupQuiz,
                    gid,
                )

                if (
                    not group_quiz
                    or group_quiz.status
                    != "active"
                ):
                    return

                meta = json.loads(
                    group_quiz.poll_map_json
                    or "{}"
                )

                if (
                    str(
                        meta.get(
                            "current_poll_id"
                        )
                    )
                    != str(poll_id)
                ):
                    return

                info = (
                    meta.get("questions")
                    or {}
                ).get(
                    str(poll_id)
                )

                if not info:
                    return

                question_id = int(
                    info["qid"]
                )

                correct_idx = int(
                    info["correct_idx"]
                )

                selected_idx = (
                    selected[0]
                    if selected
                    else -1
                )

                is_correct = (
                    selected_idx
                    == correct_idx
                )

                user = await session.scalar(
                    select(User).where(
                        User.telegram_id
                        == user_id
                    )
                )

                if (
                    user
                    and user.is_blocked
                    and user.telegram_id
                    not in settings.admins
                ):
                    return

                if not user:
                    user = User(
                        telegram_id=user_id,
                        username=
                            poll_answer.user.username,
                        full_name=
                            poll_answer.user.full_name
                            or "Foydalanuvchi",
                    )

                    session.add(user)

                    await session.flush()

                participant = await session.scalar(
                    select(GroupQuizParticipant).where(
                        GroupQuizParticipant.group_quiz_id
                        == gid,
                        GroupQuizParticipant.user_id
                        == user.id,
                    )
                )

                if not participant:
                    participant = GroupQuizParticipant(
                        group_quiz_id=gid,
                        user_id=user.id,
                    )

                    session.add(
                        participant
                    )

                    await session.flush()

                existing_answer = (
                    await session.scalar(
                        select(
                            GroupQuizAnswer
                        ).where(
                            GroupQuizAnswer.group_quiz_id
                            == gid,
                            GroupQuizAnswer.participant_id
                            == participant.id,
                            GroupQuizAnswer.question_id
                            == question_id,
                        )
                    )
                )

                if existing_answer is None:
                    session.add(
                        GroupQuizAnswer(
                            group_quiz_id=gid,
                            participant_id=
                                participant.id,
                            question_id=
                                question_id,
                            selected_option=
                                str(selected_idx),
                            is_correct=
                                is_correct,
                        )
                    )

                    participant.answered += 1
                    participant.score += int(
                        is_correct
                    )

                await session.commit()

            await _advance_group_question_locked(
                bot,
                gid,
                expected_poll_id=
                    poll_id,
            )

    except asyncio.CancelledError:
        raise

    except Exception as exc:
        print(
            ">>> GROUP ANSWER ERROR: "
            f"{type(exc).__name__}: {exc}",
            flush=True,
        )


async def recover_active_group_quizzes(
    bot,
) -> int:
    async with SessionLocal() as session:
        active = (
            await session.scalars(
                select(GroupQuiz).where(
                    GroupQuiz.status
                    == "active"
                )
            )
        ).all()

    restored = 0

    for group_quiz in active:
        try:
            meta = json.loads(
                group_quiz.poll_map_json
                or "{}"
            )
        except Exception:
            meta = {}

        if not meta.get(
            "current_poll_id"
        ):
            await _finish_group_quiz(
                bot,
                group_quiz.id,
            )
            continue

        restored += 1

    return restored

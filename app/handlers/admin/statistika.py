"""Admin paneli: statistika bo'limi (admin.py dan ajratilgan)."""

from ...tg_retry import retry as _tg_retry
from ...numfmt import pct_1
import sys
import asyncio
import logging
import json
import re
import html
from pathlib import Path
from uuid import uuid4
from ...models import Subject, Topic, Question, User, TestAttempt, AnswerLog
from ...keyboards import main_menu
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton, WebAppInfo, ReplyKeyboardRemove
from aiogram.fsm.context import FSMContext
from aiogram.types import FSInputFile
from sqlalchemy import select, func
from ...config import settings
from ...db import SessionLocal
from ...models import Subject, Topic, Question, User
from ...states import AdminState
from ...image_cropper import crop_questions
from ...web_crop import create_session, web_app_url, finish_session, get_session
from ...admin_roles import can_add_questions, is_superadmin
from datetime import datetime, timedelta
from aiogram.fsm.state import StatesGroup, State
from ..start import registration_complete
from ...models import QuestionEditorGrant
from ...role_management import RoleOperationRejected, RolePermissionDenied, set_question_editor_role
from ._common import admin_menu, cancel_kb, is_admin

router = Router()


@router.callback_query(F.data == "adm:stats")
async def admin_statistics(c: CallbackQuery):
    if not is_admin(c.from_user.id):
        return

    async with SessionLocal() as s:
        # 1. FOYDALANUVCHILAR FAOLKIGI
        total_users = await s.scalar(select(func.count(User.id)))

        # Bugun qo'shilgan yangi o'quvchilar (created_at maydoni bor deb hisoblaymiz)
        today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        new_users_today = await s.scalar(
            select(func.count(User.id)).where(User.created_at >= today_start)
        )

        # Oxirgi 7 kunda test ishlagan faol o'quvchilar soni
        seven_days_ago = datetime.now() - timedelta(days=7)
        active_users_7d = await s.scalar(
            select(func.count(func.distinct(TestAttempt.user_id))).where(
                TestAttempt.created_at >= seven_days_ago
            )
        )

        # 2. TESTLAR SIFATI TAHLILI
        total_attempts = await s.scalar(select(func.count(TestAttempt.id))) or 0

        # Jami javoblar ichidan to'g'ri va xatolarini hisoblash
        total_answers = await s.scalar(select(func.count(AnswerLog.id))) or 0
        correct_answers = (
            await s.scalar(
                select(func.count(AnswerLog.id)).where(AnswerLog.is_correct == True)
            )
            or 0
        )
        wrong_answers = total_answers - correct_answers

        # To'g'ri javoblar foizi
        success_pct = (
            (correct_answers / total_answers * 100) if total_answers > 0 else 0
        )

        # 3. REKLAMA VA E'LONLAR SAMARADORLIGI
        # Botdagi barcha o'quvchilar ishlagan jami umumiy darslar/urinishlar soni
        total_views = await s.scalar(select(func.sum(User.total_tests))) or 0

        # 4. REYTING VA RAG'BATLANTIRISH (Eng zo'r o'quvchi)
        top_user_stmt = (
            select(User.full_name, User.total_score)
            .order_by(User.total_score.desc())
            .limit(1)
        )
        top_user_res = (await s.execute(top_user_stmt)).fetchone()

        if top_user_res:
            top_student_text = f"🏆 <b>{top_user_res[0]}</b> ({top_user_res[1]} ball)"
        else:
            top_student_text = "Hali mavjud emas"

    # 📋 STATISTIKA MATNINI SHAKLLANTIRISH
    stats_text = (
        "📊 <b>Doniyor Academy — Tizim statistikasi</b>\n"
        "────────────────────\n\n"
        "👥 <b>Foydalanuvchilar faolligi:</b>\n"
        f"• Jami o'quvchilar: <b>{total_users} ta</b>\n"
        f"• Bugun qo'shilganlar: <b>+{new_users_today} ta</b>\n"
        f"• Haftalik faol (aktiv): <b>{active_users_7d} ta</b>\n\n"
        "📋 <b>Testlar sifati tahlili:</b>\n"
        f"• Jami yakunlangan testlar: <b>{total_attempts} marta</b>\n"
        f"• To'g'ri javoblar foizi: <b>{pct_1(correct_answers, total_answers):.1f}%</b>\n"
        f"• ✅ To'g'ri: {correct_answers} ta | ❌ Xato: {wrong_answers} ta\n\n"
        "📢 <b>E'lonlar va kontent samaradorligi:</b>\n"
        f"• Mavzularni ko'rishlar soni: <b>{total_views} marta</b>\n\n"
        "🏆 <b>Reyting va rag'batlantirish:</b>\n"
        f"• Eng yuqori balli talaba: {top_student_text}\n"
        "────────────────────"
    )

    # Statistika oynasining tagida faqat orqaga qaytish (Bekor qilish) tugmasi turadi
    await c.message.edit_text(
        stats_text,
        reply_markup=cancel_kb(),  # Sizda bor bo'lgan cancel_kb() funksiyasini chaqiramiz
        parse_mode="HTML",
    )
    await c.answer()


# Backward-compatible text commands
@router.message(Command("addsubject"))
async def addsubject_command(m: Message):
    if not is_admin(m.from_user.id):
        return
    name = m.text.partition(" ")[2].strip()
    if not name:
        await m.answer("Format: /addsubject Fan nomi")
        return
    async with SessionLocal() as s:
        exists = await s.scalar(
            select(Subject).where(func.lower(Subject.name) == name.lower())
        )
        if exists:
            await m.answer("⚠️ Bu fan allaqachon mavjud.")
            return
        s.add(Subject(name=name))
        await s.commit()
    await m.answer(f"✅ Fan qo'shildi: <b>{name}</b>")


@router.message(Command("addtopic"))
async def addtopic_command(m: Message):
    if not is_admin(m.from_user.id):
        return
    raw = m.text.partition(" ")[2].strip()
    if "|" not in raw:
        await m.answer("Format: /addtopic Fan nomi | Mavzu nomi")
        return
    sn, tn = [x.strip() for x in raw.split("|", 1)]
    async with SessionLocal() as s:
        sub = await s.scalar(select(Subject).where(Subject.name == sn))
        if not sub:
            await m.answer("Bunday fan topilmadi.")
            return
        s.add(Topic(subject_id=sub.id, name=tn))
        await s.commit()
    await m.answer(f"✅ Mavzu qo'shildi: <b>{tn}</b>")


@router.message(Command("users"))
async def users(m: Message):
    if not is_admin(m.from_user.id):
        return
    async with SessionLocal() as s:
        n = await s.scalar(select(func.count(User.id)))
    await m.answer(f"👥 Foydalanuvchilar: <b>{n}</b>")


@router.message(Command("broadcast"))
async def broadcast(m: Message):
    if not is_admin(m.from_user.id):
        return
    text = m.text.partition(" ")[2].strip()
    if not text:
        await m.answer("Format: /broadcast Xabar matni")
        return
    async with SessionLocal() as s:
        us = (await s.scalars(select(User).where(User.is_blocked.is_(False)))).all()
    sent = 0
    for u in us:
        try:
            await _tg_retry(lambda: m.bot.send_message(u.telegram_id, text), tries=3)
            sent += 1
        except Exception:
            pass
    await m.answer(f"📢 Yuborildi: {sent}/{len(us)}")


# E'lon uchun alohida yangi holat
class AdState(StatesGroup):
    waiting_for_ad = State()


# 1. Admin panelda "📢 E'lonlar" tugmasi bosilganda ishlaydi
@router.callback_query(F.data == "adm:send_ad")
async def start_ad(c: CallbackQuery, state: FSMContext):
    if not is_admin(c.from_user.id):
        return

    await state.set_state(AdState.waiting_for_ad)

    # edit_text o'rniga answer ishlatamiz, shunda xato bermaydi
    await c.message.answer(
        "📢 E'lon bo'limi faollashdi! O'quvchilarga yubormoqchi bo'lgan xabaringizni yozib yuboring."
    )
    await c.answer()


# 2. Admin e'lon xabarini yuborganida ishlaydi
@router.message(AdState.waiting_for_ad)
async def send_ad_to_all_users(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return
    await state.clear()
    status_msg = await m.answer("📢 E'lon barcha o'quvchilarga yuborilmoqda...")

    async with SessionLocal() as s:
        users = (await s.scalars(select(User.telegram_id))).all()

    success_count = 0
    fail_count = 0

    for user_id in users:
        try:
            await _tg_retry(lambda: m.copy_to(chat_id=user_id), tries=3)
            success_count += 1
            if success_count % 10 == 0:
                await asyncio.sleep(0.3)
        except Exception:
            fail_count += 1

    await status_msg.edit_text(
        f"📢 <b>E'lon tarqatish yakunlandi!</b>\n\n"
        f"✅ Yetkazildi: <b>{success_count} ta</b>\n"
        f"❌ Yetkazilmadi: <b>{fail_count} ta</b>",
        reply_markup=admin_menu(),
        parse_mode="HTML",
    )


# -------------------- SUBJECT DELETE --------------------


@router.callback_query(F.data.startswith("adm:delete_subject:"))
async def delete_subject_confirm(c: CallbackQuery):
    if not is_admin(c.from_user.id):
        return

    sid = int(c.data.split(":")[2])

    async with SessionLocal() as s:
        subject = await s.get(Subject, sid)

        if not subject:
            await c.answer("Fan topilmadi.", show_alert=True)
            return

        topic_count = (
            await s.scalar(select(func.count(Topic.id)).where(Topic.subject_id == sid))
            or 0
        )

        question_count = (
            await s.scalar(
                select(func.count(Question.id))
                .join(Topic, Question.topic_id == Topic.id)
                .where(Topic.subject_id == sid)
            )
            or 0
        )

        subject_name = subject.name

    await c.message.edit_text(
        "⚠️ <b>FANNI O'CHIRISH</b>\n\n"
        f"📚 Fan: <b>{subject_name}</b>\n\n"
        f"📖 Mavzular: <b>{topic_count} ta</b>\n"
        f"📋 Savollar: <b>{question_count} ta</b>\n\n"
        "❌ <b>DIQQAT!</b>\n"
        "Bu fanni o'chirsangiz, unga tegishli "
        "barcha mavzular va savollar ham o'chiriladi.\n\n"
        "Rostdan ham o'chirmoqchimisiz?",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="✅ Ha, o'chirish",
                        callback_data=f"adm:delete_subject_confirm:{sid}",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="❌ Bekor qilish", callback_data=f"adm:subject:{sid}"
                    )
                ],
            ]
        ),
        parse_mode="HTML",
    )

    await c.answer()


@router.callback_query(F.data.startswith("adm:delete_subject_confirm:"))
async def delete_subject(c: CallbackQuery):
    if not is_admin(c.from_user.id):
        return

    sid = int(c.data.split(":")[2])

    async with SessionLocal() as s:
        subject = await s.get(Subject, sid)

        if not subject:
            await c.answer("Fan topilmadi.", show_alert=True)
            return

        subject_name = subject.name

        await s.delete(subject)
        await s.commit()

    await c.message.edit_text(
        "🗑 <b>FAN O'CHIRILDI!</b>\n\n"
        f"📚 <b>{subject_name}</b>\n\n"
        "Fan va unga tegishli mavzular hamda savollar "
        "muvaffaqiyatli o'chirildi.",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="📚 Fanlar", callback_data="adm:catalog")],
                [InlineKeyboardButton(text="⚙️ Admin panel", callback_data="admin")],
            ]
        ),
        parse_mode="HTML",
    )

    await c.answer("Fan o'chirildi.")


# -------------------- DELETE SUBJECT MENU --------------------


@router.callback_query(F.data == "adm:delete_subject_menu")
async def delete_subject_menu(c: CallbackQuery):
    if not is_admin(c.from_user.id):
        return

    async with SessionLocal() as s:
        subjects = (await s.scalars(select(Subject).order_by(Subject.name))).all()

    if not subjects:
        await c.answer("Hali fanlar yo'q.", show_alert=True)
        return

    buttons = [
        [
            InlineKeyboardButton(
                text=f"📚 {subject.name}",
                callback_data=f"adm:delete_subject:{subject.id}",
            )
        ]
        for subject in subjects
    ]

    buttons.append([InlineKeyboardButton(text="⬅️ Orqaga", callback_data="adm:g:fan")])

    await c.message.edit_text(
        "🗑 <b>FANNI O'CHIRISH</b>\n\n" "O'chirmoqchi bo'lgan fanni tanlang:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        parse_mode="HTML",
    )

    await c.answer()

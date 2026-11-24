"""Admin paneli: asosiy bo'limi (admin.py dan ajratilgan)."""

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
from ._common import admin_menu, cancel_kb, is_admin, show_admin, subject_kb

router = Router()


@router.message(Command("admin"))
async def cmd_admin(m: Message, state: FSMContext):
    if not await can_add_questions(m.from_user.id):
        return
    await state.clear()
    await show_admin(m, answer=False)


@router.callback_query(F.data == "admin")
async def cb_admin(c: CallbackQuery, state: FSMContext):
    if not await can_add_questions(c.from_user.id):
        await c.answer("Sizda admin huquqi yo'q.", show_alert=True)
        return
    await state.clear()
    await show_admin(c)


# Admin faylingizning tepasida main_menu import qilinganiga ishonch hosil qiling:
# from ..keyboards import main_menu


@router.callback_query(F.data == "adm:cancel")
async def cancel(c: CallbackQuery, state: FSMContext):
    if not await can_add_questions(c.from_user.id):
        return

    # 1. Admin holatlarini tozalaymiz
    await state.clear()

    # 2. Sizning asl bosh menyu tugmalaringizni olamiz
    user_menu = main_menu()

    # 3. Agar foydalanuvchi admin bo'lsa, menyuning eng tagiga "Admin panel" tugmasini qo'shamiz
    # inline_keyboard bu massiv, unga yangi qator qo'shamiz
    admin_btn = [InlineKeyboardButton(text="⚙️ Admin panel", callback_data="admin")]
    if admin_btn not in user_menu.inline_keyboard:
        user_menu.inline_keyboard.append(admin_btn)

    # 4. Xabarni yangilaymiz, endi "Admin panel" tugmasi joyida bo'ladi!
    await c.message.edit_text(
        "🏠 <b>Doniyor Academy</b>\n\nBosh menyu:",
        reply_markup=user_menu,
        parse_mode="HTML",
    )
    await c.answer()


# -------------------- SUBJECT --------------------
@router.callback_query(F.data == "adm:add_subject")
async def add_subject_start(c: CallbackQuery, state: FSMContext):
    if not is_admin(c.from_user.id):
        return
    await state.set_state(AdminState.add_subject_name)
    await c.message.edit_text(
        "➕ <b>Yangi fan</b>\n\nFan nomini yuboring:\n\nMasalan: <i>O'zbekiston tarixi</i>",
        reply_markup=cancel_kb(),
    )
    await c.answer()


@router.message(AdminState.add_subject_name)
async def add_subject_finish(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return
    name = (m.text or "").strip()
    if not name:
        await m.answer("❗ Fan nomi bo'sh bo'lishi mumkin emas.")
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
    await state.clear()
    await m.answer(f"✅ Fan qo'shildi: <b>{name}</b>", reply_markup=admin_menu())


# -------------------- TOPIC --------------------
@router.callback_query(F.data == "adm:add_topic")
async def add_topic_start(c: CallbackQuery, state: FSMContext):
    if not is_admin(c.from_user.id):
        return
    async with SessionLocal() as s:
        subjects = (await s.scalars(select(Subject).order_by(Subject.name))).all()
    if not subjects:
        await c.answer("Avval fan qo'shing.", show_alert=True)
        return
    await state.set_state(AdminState.add_topic_subject)
    await c.message.edit_text(
        "📚 <b>Mavzu qo'shish</b>\n\nFanni tanlang:",
        reply_markup=subject_kb(subjects, "adm:tsub"),
    )
    await c.answer()


@router.callback_query(F.data.startswith("adm:tsub:"))
async def add_topic_subject(c: CallbackQuery, state: FSMContext):
    if not is_admin(c.from_user.id):
        return
    sid = int(c.data.split(":")[2])
    await state.update_data(subject_id=sid)
    await state.set_state(AdminState.add_topic_name)
    await c.message.edit_text("📖 Mavzu nomini yuboring:", reply_markup=cancel_kb())
    await c.answer()


@router.message(AdminState.add_topic_name)
async def add_topic_finish(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return
    name = (m.text or "").strip()
    data = await state.get_data()
    async with SessionLocal() as s:
        subject = await s.get(Subject, data["subject_id"])
        if not subject:
            await state.clear()
            await m.answer("❌ Fan topilmadi.", reply_markup=admin_menu())
            return
        exists = await s.scalar(
            select(Topic).where(
                Topic.subject_id == subject.id, func.lower(Topic.name) == name.lower()
            )
        )
        if exists:
            await m.answer("⚠️ Bu mavzu allaqachon mavjud.")
            return
        s.add(Topic(subject_id=subject.id, name=name))
        await s.commit()
    await state.clear()
    await m.answer(
        f"✅ <b>{subject.name}</b> faniga <b>{name}</b> mavzusi qo'shildi.",
        reply_markup=admin_menu(),
    )

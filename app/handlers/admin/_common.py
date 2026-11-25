"""Admin paneli: _common bo'limi (admin.py dan ajratilgan)."""

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



async def photo_bytes(m: Message):
    if not m.photo:
        return None

    photo = m.photo[-1]
    bot = m.bot

    file = await bot.get_file(photo.file_id)

    from io import BytesIO
    data = BytesIO()
    await bot.download_file(file.file_path, data)

    return data.getvalue()


async def save_question_image(image_data: bytes, question_id: int) -> str:
    folder = Path("app/question_images")
    folder.mkdir(parents=True, exist_ok=True)

    filename = f"question_{question_id}.jpg"
    path = folder / filename

    path.write_bytes(image_data)

    # rasm zaxirasi: bazada ham saqlanadi
    try:
        from ...services.image_store import store_image

        await store_image(path, image_data)
    except Exception:
        pass

    return path.as_posix()





def natural_topic_key(topic):
    parts = re.split(r"(\d+)", topic.name.casefold())
    return tuple(
        (0, int(part)) if part.isdigit() else (1, part)
        for part in parts
    )


def is_admin(uid: int) -> bool:
    return uid in settings.admins


def admin_menu(limited=False):
    if limited:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(
                    text="\u2795 Bitta savol qo'shish",
                    callback_data="adm:add_question",
                )],
                [InlineKeyboardButton(
                    text="\u26a1 Bir nechta savol qo'shish",
                    callback_data="adm:add_questions_bulk",
                )],
                [InlineKeyboardButton(
                    text="\U0001F4E5 Savol yuklash",
                    callback_data="imq:home",
                )],
                [InlineKeyboardButton(
                    text="\u274c Bekor qilish",
                    callback_data="adm:cancel",
                )],
            ]
        )
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="➕ Fan qo'shish", callback_data="adm:add_subject"
                )
            ],
            [
                InlineKeyboardButton(
                    text="➕ Mavzu qo'shish", callback_data="adm:add_topic"
                )
            ],
            [
                InlineKeyboardButton(
                    text="➕ Bitta savol qo'shish", callback_data="adm:add_question"
                )
            ],
            [
                InlineKeyboardButton(
                    text="⚡ Bir nechta savol qo'shish",
                    callback_data="adm:add_questions_bulk",
                )
            ],
            [
                InlineKeyboardButton(
                    text="📥 Excel/CSV import", callback_data="adm:import"
                )
            ],
            [
                InlineKeyboardButton(
                    text="📄 Word eksport", callback_data="adm:export"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔔 Eslatmalar", callback_data="adm:reminders"
                )
            ],
            [
                InlineKeyboardButton(
                    text="📊 O'quvchilar natijasi",
                    callback_data="adm:reports",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔬 Savollar sifati",
                    callback_data="adm:qstat",
                )
            ],
            [
                InlineKeyboardButton(
                    text="📋 Ustoz topshirig'i",
                    callback_data="adm:assign",
                )
            ],
            [
                InlineKeyboardButton(
                    text="📚 Fanlar / mavzular", callback_data="adm:catalog"
                )
            ],
            [
                InlineKeyboardButton(
                    text="\U0001f5d1 Fanni o\u2018chirish",
                    callback_data="adm:delete_subject_menu",
                )
            ],
            [
                InlineKeyboardButton(
                    text="\U0001f465 Foydalanuvchilar",
                    callback_data="adm:users",
                )
            ],            [InlineKeyboardButton(text="📊 Statistika", callback_data="adm:stats")],
            [InlineKeyboardButton(text="📢 E'lonlar", callback_data="adm:send_ad")],
            [InlineKeyboardButton(text="\U0001F9D1\u200D\U0001F3EB Ustozlar", callback_data="adm:ust")],
            [InlineKeyboardButton(text="\U0001F4E5 Savol yuklash", callback_data="imq:home")],
            [InlineKeyboardButton(text="\U0001F4DD Imtihonlar", callback_data="exm:t")],
            [InlineKeyboardButton(text="❌ Bekor qilish", callback_data="adm:cancel")],
        ]
    )


def cancel_kb():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="❌ Bekor qilish", callback_data="adm:cancel")]
        ]
    )


def subject_kb(subjects, prefix="adm:qsub"):
    rows = [
        [InlineKeyboardButton(text=f"📚 {s.name}", callback_data=f"{prefix}:{s.id}")]
        for s in subjects
    ]
    rows.append(
        [InlineKeyboardButton(text="❌ Bekor qilish", callback_data="adm:cancel")]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def topic_kb(topics, prefix="adm:qtopic"):
    rows = [
        [InlineKeyboardButton(text=f"📖 {t.name}", callback_data=f"{prefix}:{t.id}")]
        for t in topics
    ]
    rows.append(
        [InlineKeyboardButton(text="⬅️ Fan tanlash", callback_data="adm:add_question")]
    )
    rows.append(
        [InlineKeyboardButton(text="❌ Bekor qilish", callback_data="adm:cancel")]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def show_admin(target, answer=True):
    text = "⚙️ <b>Doniyor Academy — Admin panel</b>\n\n" "Kerakli amalni tanlang:"

    if isinstance(target, CallbackQuery):
        try:
            await target.message.edit_text(text, reply_markup=admin_menu(limited=not is_admin(target.from_user.id)))
        except Exception as e:
            if "message is not modified" not in str(e):
                raise

        if answer:
            await target.answer()

    else:
        await target.answer(text, reply_markup=admin_menu(limited=not is_admin(target.from_user.id)))

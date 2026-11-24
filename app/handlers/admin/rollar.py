"""Admin paneli: rollar bo'limi (admin.py dan ajratilgan)."""

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
from ._common import is_admin

router = Router()


@router.callback_query(F.data.startswith("adm:role:"))
async def editor_role_callback(c: CallbackQuery):
    from html import escape

    if not is_superadmin(c.from_user.id):
        await c.answer("Ruxsat yo'q.", show_alert=True)
        return

    parts = (c.data or "").split(":")

    if (
        len(parts) != 5
        or parts[:2] != ["adm", "role"]
        or parts[2] not in {"grant", "revoke"}
        or parts[3] not in {"ask", "do"}
    ):
        await c.answer("Noto'g'ri so'rov.", show_alert=True)
        return

    action = parts[2]
    phase = parts[3]

    try:
        telegram_id = int(parts[4])
        if telegram_id <= 0:
            raise ValueError
    except ValueError:
        await c.answer("Telegram ID noto'g'ri.", show_alert=True)
        return

    back = InlineKeyboardButton(
        text="\u2b05\ufe0f Foydalanuvchi kartasi",
        callback_data=f"adm:user:{telegram_id}",
    )

    if phase == "do":
        try:
            await set_question_editor_role(
                actor_telegram_id=c.from_user.id,
                target_telegram_id=telegram_id,
                enable=(action == "grant"),
            )
        except (RolePermissionDenied, RoleOperationRejected):
            await c.answer(
                "Amal rad etildi. Foydalanuvchi holatini tekshiring.",
                show_alert=True,
            )
            return

        result = (
            "Cheklangan admin huquqi berildi."
            if action == "grant"
            else "Cheklangan admin huquqi bekor qilindi."
        )

        await c.message.edit_text(
            "\u2705 " + result,
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[back]]
            ),
        )
        await c.answer()
        return

    async with SessionLocal() as s:
        user = await s.scalar(
            select(User).where(User.telegram_id == telegram_id)
        )

        if user is None:
            await c.answer(
                "Foydalanuvchi topilmadi.",
                show_alert=True,
            )
            return

        grant_exists = (
            await s.scalar(
                select(QuestionEditorGrant.id).where(
                    QuestionEditorGrant.user_id == user.id
                )
            )
        ) is not None

        if action == "grant":
            if (
                grant_exists
                or is_admin(telegram_id)
                or user.registration_status != "approved"
                or user.is_blocked
                or not registration_complete(user)
            ):
                await c.answer(
                    "Bu foydalanuvchiga huquq berib bo'lmaydi.",
                    show_alert=True,
                )
                return
        elif not grant_exists:
            await c.answer(
                "Bu foydalanuvchida huquq mavjud emas.",
                show_alert=True,
            )
            return

        full_name = user.full_name or "Foydalanuvchi"

    title = (
        "Cheklangan admin huquqini berasizmi?"
        if action == "grant"
        else "Cheklangan admin huquqini bekor qilasizmi?"
    )

    await c.message.edit_text(
        f"<b>{title}</b>\n\n"
        f"Foydalanuvchi: <b>{escape(full_name)}</b>\n"
        f"Telegram ID: <code>{telegram_id}</code>",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(
                    text="\u2705 Tasdiqlash",
                    callback_data=(
                        f"adm:role:{action}:do:{telegram_id}"
                    ),
                )],
                [back],
            ]
        ),
        parse_mode="HTML",
    )
    await c.answer()

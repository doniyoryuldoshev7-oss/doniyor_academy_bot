import os

from sqlalchemy import select

from .config import settings
from .db import SessionLocal
from .models import QuestionEditorGrant, User


def is_full_admin(user_id: int) -> bool:
    return user_id in settings.admins


def is_superadmin(user_id: int) -> bool:
    if not is_full_admin(user_id):
        return False

    raw = os.getenv("SUPERADMIN_IDS", "")
    try:
        owners = {
            int(part.strip())
            for part in raw.split(",")
            if part.strip()
        }
    except ValueError:
        return False

    return user_id in owners


async def is_question_editor(user_id: int) -> bool:
    if is_full_admin(user_id):
        return False

    async with SessionLocal() as session:
        user = await session.scalar(
            select(User)
            .join(
                QuestionEditorGrant,
                QuestionEditorGrant.user_id == User.id,
            )
            .where(
                User.telegram_id == user_id,
                User.registration_status == "approved",
                User.is_blocked.is_(False),
            )
        )

    if user is None:
        return False

    from .handlers.start import registration_complete
    return registration_complete(user)


async def can_add_questions(user_id: int) -> bool:
    if is_full_admin(user_id):
        return True

    return await is_question_editor(user_id)


async def is_teacher(user_id: int) -> bool:
    """Tasdiqlangan, bloklanmagan ustozmi (bosh menyu tugmasi uchun)."""
    from .models import Teacher

    try:
        async with SessionLocal() as session:
            found = await session.scalar(
                select(Teacher.id)
                .join(User, User.id == Teacher.user_id)
                .where(
                    User.telegram_id == user_id,
                    User.registration_status == "approved",
                    User.is_blocked.is_(False),
                )
            )
        return found is not None
    except Exception:
        return False

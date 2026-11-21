from sqlalchemy import select

from .admin_roles import is_full_admin, is_superadmin
from .db import SessionLocal
from .models import AdminRoleEvent, QuestionEditorGrant, User


class RolePermissionDenied(Exception):
    pass


class RoleOperationRejected(Exception):
    pass


async def set_question_editor_role(
    *,
    actor_telegram_id: int,
    target_telegram_id: int,
    enable: bool,
) -> str:
    if not is_superadmin(actor_telegram_id):
        raise RolePermissionDenied("Superadmin permission required")

    if target_telegram_id <= 0:
        raise RoleOperationRejected("Invalid Telegram ID")

    async with SessionLocal() as session:
        async with session.begin():
            user = await session.scalar(
                select(User)
                .where(User.telegram_id == target_telegram_id)
                .with_for_update()
            )

            if user is None:
                raise RoleOperationRejected("User not found")

            existing = await session.scalar(
                select(QuestionEditorGrant).where(
                    QuestionEditorGrant.user_id == user.id
                )
            )

            if enable:
                from .handlers.start import registration_complete

                if (
                    is_full_admin(target_telegram_id)
                    or user.registration_status != "approved"
                    or user.is_blocked is not False
                    or not registration_complete(user)
                ):
                    raise RoleOperationRejected(
                        "User is not eligible for editor access"
                    )

                if existing is not None:
                    raise RoleOperationRejected(
                        "Editor access already granted"
                    )

                session.add(
                    QuestionEditorGrant(
                        user_id=user.id,
                        granted_by=actor_telegram_id,
                    )
                )
                action = "grant"

            else:
                if existing is None:
                    raise RoleOperationRejected(
                        "Editor access is not active"
                    )

                await session.delete(existing)
                action = "revoke"

            session.add(
                AdminRoleEvent(
                    actor_telegram_id=actor_telegram_id,
                    target_telegram_id=target_telegram_id,
                    action=action,
                )
            )

    return action

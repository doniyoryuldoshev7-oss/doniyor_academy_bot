import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.dispatcher.middlewares.base import BaseMiddleware

from sqlalchemy import select

from .config import settings
from .db import init_db
from .handlers import start, student, admin, group_quiz
from .models import User
from .db import SessionLocal
from .web_crop import start_web_server


class BlockedUserMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        if not getattr(event, "from_user", None):
            return await handler(event, data)

        telegram_id = event.from_user.id

        if telegram_id in settings.admins:
            return await handler(event, data)

        async with SessionLocal() as s:
            user = await s.scalar(
                select(User).where(
                    User.telegram_id == telegram_id
                )
            )

        if user and user.is_blocked:
            if hasattr(event, "answer"):
                if event.__class__.__name__ == "CallbackQuery":
                    await event.answer(
                        "?? Hisobingiz bloklangan.",
                        show_alert=True,
                    )
                else:
                    await event.answer(
                        "?? <b>Sizning hisobingiz bloklangan.</b>\n\n"
                        "Botdan foydalanish uchun administratorga murojaat qiling."
                    )
            return

        return await handler(event, data)


async def main():
    logging.basicConfig(level=logging.INFO)

    await init_db()

    await start_web_server()

    session = AiohttpSession()

    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(
            parse_mode=ParseMode.HTML
        ),
        session=session,
    )

    dp = Dispatcher(
        storage=MemoryStorage()
    )

    blocked_middleware = BlockedUserMiddleware()
    dp.message.middleware(blocked_middleware)
    dp.callback_query.middleware(blocked_middleware)

    dp.include_router(start.router)
    dp.include_router(student.router)
    dp.include_router(group_quiz.router)
    dp.include_router(admin.router)

    await bot.delete_webhook(
        drop_pending_updates=True
    )

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())

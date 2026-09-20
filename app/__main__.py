import asyncio
import logging
import os

from aiohttp import web

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.dispatcher.middlewares.base import BaseMiddleware
from aiogram.webhook.aiohttp_server import (
    SimpleRequestHandler,
    setup_application,
)

from sqlalchemy import select

from .config import settings
from .db import init_db, SessionLocal
from .handlers import start, student, admin, group_quiz
from .models import User
from .web_crop import create_app


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
                        "Hisobingiz bloklangan.",
                        show_alert=True,
                    )
                else:
                    await event.answer(
                        "<b>Sizning hisobingiz bloklangan.</b>\n\n"
                        "Botdan foydalanish uchun administratorga murojaat qiling."
                    )
            return

        return await handler(event, data)


async def main():
    logging.basicConfig(level=logging.INFO)

    await init_db()

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

    app = create_app()

    webhook_path = "/telegram/webhook"

    webhook_handler = SimpleRequestHandler(
        dispatcher=dp,
        bot=bot,
        handle_in_background=True,
    )
    webhook_handler.register(
        app,
        path=webhook_path,
    )

    setup_application(
        app,
        dp,
        bot=bot,
    )

    runner = web.AppRunner(app)
    await runner.setup()

    port = int(os.getenv("PORT", "8080"))

    site = web.TCPSite(
        runner,
        host="0.0.0.0",
        port=port,
    )

    await site.start()

    webhook_url = (
        settings.web_app_url.rstrip("/")
        + webhook_path
    )

    await bot.set_webhook(
        webhook_url,
        drop_pending_updates=True,
        allowed_updates=dp.resolve_used_update_types(),
    )

    print(
        f">>> WEBHOOK SERVER STARTED: 0.0.0.0:{port}",
        flush=True,
    )

    print(
        f">>> TELEGRAM WEBHOOK: {webhook_url}",
        flush=True,
    )

    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())

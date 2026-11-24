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
from .handlers import (
    start,
    student,
    admin,
    group_quiz,
    smart_review,
    export,
)
from .models import User
from .web_crop import create_app


_BLOCK_CACHE: dict = {}


class BlockedUserMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        if not getattr(event, "from_user", None):
            return await handler(event, data)

        telegram_id = event.from_user.id

        if telegram_id in settings.admins:
            return await handler(event, data)

        # Har bosishda bazaga bormaslik uchun 30 soniyalik xotira
        import time as _time

        cached = _BLOCK_CACHE.get(telegram_id)
        now_ts = _time.monotonic()

        if cached is None or now_ts - cached[1] > 30:
            async with SessionLocal() as s:
                user = await s.scalar(
                    select(User).where(
                        User.telegram_id == telegram_id
                    )
                )

            cached = (bool(user and user.is_blocked), now_ts)
            _BLOCK_CACHE[telegram_id] = cached

        if cached[0]:
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

    from .maintenance import ensure_indexes

    await ensure_indexes()

    # rasm zaxirasi: yo'qolgan rasmlarni tiklash
    from .services.image_store import sync_images

    _restored, _saved = await sync_images()
    print(
        f"RASMLAR: tiklandi={_restored}, zaxiralandi={_saved}",
        flush=True,
    )

    # Oldin tugagan guruh testlarini test tarixiga qo'shish (bir marta)
    from .services.group_results import backfill_group_attempts

    _backfill_task = asyncio.create_task(
        backfill_group_attempts(SessionLocal)
    )

    session = AiohttpSession()

    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(
            parse_mode=ParseMode.HTML
        ),
        session=session,
    )

    from .db import engine as _engine
    from .fsm_storage import make_storage

    dp = Dispatcher(
        storage=await make_storage(_engine)
    )

    blocked_middleware = BlockedUserMiddleware()
    dp.message.middleware(blocked_middleware)
    dp.callback_query.middleware(blocked_middleware)

    # Bir foydalanuvchining so'rovlari ketma-ket bajariladi (ikki marta
    # bosishdan himoya) va kutilmagan xatolar foydalanuvchiga ko'rsatiladi
    from .middlewares import UserSerializeMiddleware, on_error

    _serialize = UserSerializeMiddleware()
    dp.message.outer_middleware(_serialize)
    dp.callback_query.outer_middleware(_serialize)
    dp.errors.register(on_error)

    dp.include_router(start.router)
    dp.include_router(student.router)
    dp.include_router(smart_review.router)
    dp.include_router(group_quiz.router)
    dp.include_router(export.router)
    from .handlers import reminders

    dp.include_router(reminders.router)
    from .services import db_backup  # baza zaxirasi

    dp.include_router(db_backup.router)
    from .handlers import knowledge_map

    dp.include_router(knowledge_map.router)
    from .handlers import student_reports

    dp.include_router(student_reports.router)
    from .handlers import question_stats

    dp.include_router(question_stats.router)
    from .handlers import assignments

    dp.include_router(assignments.router)
    from .handlers import diagnostic

    dp.include_router(diagnostic.router)
    dp.include_router(admin.router)

    app = create_app()

    webhook_path = "/telegram/webhook"

    # Webhook manzilini bilgan begona odam soxta so'rov yubora olmasligi
    # uchun Telegram maxfiy belgisi (WEBHOOK_SECRET berilmasa, bot
    # tokenidan hosil qilinadi)
    import hashlib

    wh_secret = (os.getenv("WEBHOOK_SECRET") or "").strip() or hashlib.sha256(
        ("wh:" + settings.bot_token).encode()
    ).hexdigest()[:48]

    webhook_handler = SimpleRequestHandler(
        dispatcher=dp,
        bot=bot,
        handle_in_background=True,
        secret_token=wh_secret,
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
        secret_token=wh_secret,
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

    from .services.review_reminder import reminder_loop

    reminder_task = asyncio.create_task(reminder_loop(bot))

    backup_task = asyncio.create_task(db_backup.backup_loop(bot))

    # Qayta ishga tushishdan oldin boshlangan guruh testlarini tekshirish
    try:
        restored = await group_quiz.recover_active_group_quizzes(bot)
        print(f">>> FAOL GURUH TESTLARI: {restored}", flush=True)
    except Exception as exc:
        logging.getLogger(__name__).exception(
            "Guruh testlarini tiklashda xato: %s", exc
        )

    print(
        ">>> TAKRORLASH ESLATMALARI SIKLI ISHGA TUSHDI",
        flush=True,
    )

    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())

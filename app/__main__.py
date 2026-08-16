import asyncio,logging
from aiogram import Bot,Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from .config import settings
from .db import init_db
from .handlers import start,student,admin
async def main():
    logging.basicConfig(level=logging.INFO); await init_db()
    bot=Bot(token=settings.bot_token,default=DefaultBotProperties(parse_mode=ParseMode.HTML)); dp=Dispatcher(storage=MemoryStorage())
    dp.include_router(start.router); dp.include_router(student.router); dp.include_router(admin.router)
    await bot.delete_webhook(drop_pending_updates=True); await dp.start_polling(bot)
if __name__=='__main__': asyncio.run(main())

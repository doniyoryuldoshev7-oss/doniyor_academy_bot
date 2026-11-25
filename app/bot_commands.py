"""Telegram 'Menu' tugmasidagi buyruqlar ro'yxati (har safar yozib o'tirmaslik uchun)."""
import logging

from aiogram import Bot
from aiogram.types import BotCommand, BotCommandScopeChat, BotCommandScopeDefault

log = logging.getLogger(__name__)

# Hamma uchun
COMMON = [
    BotCommand(command="start", description="🏠 Bosh menyu"),
    BotCommand(command="topshiriqlar", description="📋 Topshiriqlar"),
]
# Faqat adminlar uchun (ularning shaxsiy chatida)
ADMIN = COMMON + [
    BotCommand(command="admin", description="⚙️ Admin panel"),
    BotCommand(command="natijalar", description="📊 O'quvchilar natijasi"),
    BotCommand(command="ustoz", description="👨‍🏫 Ustoz paneli"),
    BotCommand(command="savol_sifati", description="🔎 Savollar sifati"),
]


async def install_commands(bot: Bot, admin_ids) -> str:
    """Xato bo'lsa bot to'xtamaydi; natija matni log uchun."""
    try:
        await bot.set_my_commands(COMMON, scope=BotCommandScopeDefault())
    except Exception as e:  # noqa: BLE001
        return f"umumiy ro'yxat xato: {e}"
    ok = 0
    for uid in sorted(admin_ids):
        try:
            await bot.set_my_commands(ADMIN, scope=BotCommandScopeChat(chat_id=uid))
            ok += 1
        except Exception as e:  # noqa: BLE001
            log.warning("admin %s buyruqlari o'rnatilmadi: %s", uid, e)
    return f"umumiy={len(COMMON)}, admin chatlari={ok}/{len(admin_ids)}"

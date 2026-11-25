from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo

def _main_menu_eski(is_admin=False, is_teacher=False):
    rows=[[InlineKeyboardButton(text="📚 Fanlar",callback_data="subjects")],
          [InlineKeyboardButton(text="👤 Profil",callback_data="profile"),InlineKeyboardButton(text="📊 Statistikam",callback_data="stats")],
          [InlineKeyboardButton(text="🏆 Reyting",callback_data="leaderboard")],
	  [InlineKeyboardButton(text="❌ Mening xatolarim", callback_data="my_errors")],
          [InlineKeyboardButton(text="\U0001F9E0 Smart Takrorlash", callback_data="smart_review")],
          [InlineKeyboardButton(text="\U0001F4C8 Bilim xaritasi", callback_data="km:menu")],
          [InlineKeyboardButton(text="\U0001F4CB Topshiriqlar", callback_data="asg:my")],
          [InlineKeyboardButton(text="\U0001F4DD Imtihonlar", callback_data="exm:l")],
          [InlineKeyboardButton(text="\U0001F3AF Diagnostika", callback_data="diag:menu")]
    ]
    try:
        from .web_crop import public_base_url
        _web = public_base_url()
    except Exception:
        _web = ""
    if _web: rows.append([InlineKeyboardButton(text="\U0001F310 Veb-ilova", web_app=WebAppInfo(url=_web + "/app"))])
    if is_teacher: rows.append([InlineKeyboardButton(text="\U0001F9D1\u200D\U0001F3EB Ustoz paneli",callback_data="ust:home")])
    if is_admin: rows.append([InlineKeyboardButton(text="⚙️ Admin panel",callback_data="admin")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def back_menu():
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Bosh menyu",callback_data="home")]])


def main_menu(is_admin=False, is_teacher=False):
    """Ixcham bosh menyu (menyu v2). Eski menyu _main_menu_eski da saqlangan."""
    rows = []
    try:
        from .web_crop import public_base_url
        _web = public_base_url()
    except Exception:
        _web = ""
    if _web:
        rows.append([InlineKeyboardButton(text="\U0001F310 Veb-ilovadan foydalanish", web_app=WebAppInfo(url=_web + "/app"))])
    rows += [
        [InlineKeyboardButton(text="\U0001F464 Profil", callback_data="mn:prof")],
        [InlineKeyboardButton(text="\U0001F4DD Sinov va imtihon", callback_data="mn:test")],
        [InlineKeyboardButton(text="\u274C Mening xatolarim", callback_data="my_errors")],
        [InlineKeyboardButton(text="\U0001F9E0 Smart Takrorlash", callback_data="smart_review")],
        [InlineKeyboardButton(text="\U0001F4CB Topshiriqlar", callback_data="asg:my")],
    ]
    if is_teacher:
        rows.append([InlineKeyboardButton(text="\U0001F9D1\u200D\U0001F3EB Ustoz paneli", callback_data="ust:home")])
    if is_admin:
        rows.append([InlineKeyboardButton(text="\u2699\uFE0F Admin panel", callback_data="admin")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

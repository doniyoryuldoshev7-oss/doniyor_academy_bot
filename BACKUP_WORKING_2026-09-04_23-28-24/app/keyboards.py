from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

def main_menu(is_admin=False):
    rows=[[InlineKeyboardButton(text="📚 Fanlar",callback_data="subjects")],
          [InlineKeyboardButton(text="👤 Profil",callback_data="profile"),InlineKeyboardButton(text="📊 Statistikam",callback_data="stats")],
          [InlineKeyboardButton(text="🏆 Reyting",callback_data="leaderboard")],
	  [InlineKeyboardButton(text="❌ Mening xatolarim", callback_data="my_errors")]
    ]
    if is_admin: rows.append([InlineKeyboardButton(text="⚙️ Admin panel",callback_data="admin")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def back_menu():
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Bosh menyu",callback_data="home")]])

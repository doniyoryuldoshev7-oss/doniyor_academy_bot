from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

def main_menu(is_admin=False):
    rows=[[InlineKeyboardButton(text="📚 Fanlar",callback_data="subjects")],
          [InlineKeyboardButton(text="👤 Profil",callback_data="profile"),InlineKeyboardButton(text="📊 Statistikam",callback_data="stats")],
          [InlineKeyboardButton(text="🏆 Reyting",callback_data="leaderboard")],
	  [InlineKeyboardButton(text="❌ Mening xatolarim", callback_data="my_errors")],
          [InlineKeyboardButton(text="\U0001F9E0 Smart Takrorlash", callback_data="smart_review")],
          [InlineKeyboardButton(text="\U0001F4C8 Bilim xaritasi", callback_data="km:menu")],
          [InlineKeyboardButton(text="\U0001F4CB Topshiriqlar", callback_data="asg:my")],
          [InlineKeyboardButton(text="\U0001F3AF Diagnostika", callback_data="diag:menu")]
    ]
    if is_admin: rows.append([InlineKeyboardButton(text="⚙️ Admin panel",callback_data="admin")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def back_menu():
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Bosh menyu",callback_data="home")]])

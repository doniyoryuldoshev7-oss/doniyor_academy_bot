# Doniyor Academy Telegram Bot — v1.0

Professional educational Telegram bot starter built with Python, aiogram 3, SQLAlchemy 2 async ORM and PostgreSQL.

## v1 features
- /start registration
- Main menu
- Profile
- Subjects and topics
- Test engine
- Immediate answer feedback
- Score/result calculation
- Personal statistics
- Leaderboard
- Admin-only panel
- Add subjects/topics/questions from Telegram
- Broadcast text messages
- Seed demo data

## 1. Requirements
- Python 3.10+
- PostgreSQL 14+
- Telegram bot token from @BotFather

## 2. Install
```bash
python -m venv .venv
# Windows:
.venv\Scripts\activate
# Linux/macOS:
source .venv/bin/activate

pip install -r requirements.txt
```

## 3. Configure
Copy `.env.example` to `.env` and set:
- BOT_TOKEN
- DATABASE_URL
- ADMIN_IDS

Example:
```env
BOT_TOKEN=123456:REPLACE_ME
DATABASE_URL=postgresql+asyncpg://postgres:password@localhost:5432/doniyor_academy
ADMIN_IDS=123456789
```

## 4. Create database
Create an empty PostgreSQL database named `doniyor_academy`, then run:
```bash
python -m app.seed
```

## 5. Start
```bash
python -m app
```

The bot uses long polling in v1. For production, webhook + HTTPS can be added later.

## Project structure
```text
app/
  __main__.py
  config.py
  db.py
  models.py
  keyboards.py
  handlers/
    start.py
    student.py
    admin.py
  services/
    stats.py
    quiz.py
  seed.py
requirements.txt
.env.example
```

Security:
- Never put the real bot token in source code or Git.
- Keep `.env` private.

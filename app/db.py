from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy import text
from .config import settings


class Base(DeclarativeBase):
    pass


database_url = settings.database_url

if database_url.startswith("postgresql://"):
    database_url = database_url.replace(
        "postgresql://",
        "postgresql+asyncpg://",
        1
    )

_engine_kwargs = {}

if database_url.startswith("postgresql"):
    _engine_kwargs = dict(
        pool_pre_ping=True,
        pool_recycle=1800,
        pool_size=10,
        max_overflow=20,
    )

engine = create_async_engine(database_url, echo=False, **_engine_kwargs)

SessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False
)


async def init_db():
    from . import models

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

        # answer_logs: xato "o'zlashtirildi" deb belgilanadi (o'chirilmaydi)
        if database_url.startswith("postgresql+asyncpg://"):
            await conn.execute(text(
                "ALTER TABLE answer_logs ADD COLUMN IF NOT EXISTS "
                "resolved BOOLEAN NOT NULL DEFAULT FALSE"
            ))
        else:
            _al_cols = await conn.execute(
                text("PRAGMA table_info(answer_logs)")
            )
            if "resolved" not in {r[1] for r in _al_cols.fetchall()}:
                await conn.execute(text(
                    "ALTER TABLE answer_logs ADD COLUMN "
                    "resolved BOOLEAN NOT NULL DEFAULT 0"
                ))

        # group_quiz_participants: yaratilgan test yozuvi havolasi
        if database_url.startswith("postgresql+asyncpg://"):
            await conn.execute(text(
                "ALTER TABLE group_quiz_participants "
                "ADD COLUMN IF NOT EXISTS attempt_id INTEGER"
            ))
        else:
            _gp_cols = await conn.execute(
                text("PRAGMA table_info(group_quiz_participants)")
            )
            if "attempt_id" not in {r[1] for r in _gp_cols.fetchall()}:
                await conn.execute(text(
                    "ALTER TABLE group_quiz_participants "
                    "ADD COLUMN attempt_id INTEGER"
                ))

        # test_attempts: yozuv manbasi (oddiy test / diagnostika)
        if database_url.startswith("postgresql+asyncpg://"):
            await conn.execute(text(
                "ALTER TABLE test_attempts ADD COLUMN IF NOT EXISTS "
                "source VARCHAR(20) NOT NULL DEFAULT 'quiz'"
            ))
        else:
            _src_cols = await conn.execute(
                text("PRAGMA table_info(test_attempts)")
            )
            if "source" not in {r[1] for r in _src_cols.fetchall()}:
                await conn.execute(text(
                    "ALTER TABLE test_attempts ADD COLUMN "
                    "source VARCHAR(20) NOT NULL DEFAULT 'quiz'"
                ))

        # questions jadvaliga is_active ustunini qo'shish (soft delete)
        if database_url.startswith("postgresql+asyncpg://"):
            await conn.execute(text(
                "ALTER TABLE questions ADD COLUMN IF NOT EXISTS "
                "is_active BOOLEAN NOT NULL DEFAULT TRUE"
            ))
        else:
            _cols = await conn.execute(text("PRAGMA table_info(questions)"))
            if "is_active" not in {r[1] for r in _cols.fetchall()}:
                await conn.execute(text(
                    "ALTER TABLE questions ADD COLUMN "
                    "is_active BOOLEAN NOT NULL DEFAULT 1"
                ))

        # users jadvaliga group_name ustunini qo'shish
        if database_url.startswith("postgresql+asyncpg://"):
            await conn.execute(
                text(
                    "ALTER TABLE users "
                    "ADD COLUMN IF NOT EXISTS group_name VARCHAR(100)"
                )
            )
        else:
            rows = await conn.execute(
                text("PRAGMA table_info(users)")
            )
            existing = {row[1] for row in rows.fetchall()}

            if "group_name" not in existing:
                await conn.execute(
                    text(
                        "ALTER TABLE users "
                        "ADD COLUMN group_name VARCHAR(100)"
                    )
                )

        # questions jadvaliga yangi ustunlarni qo'shish
        if database_url.startswith("postgresql+asyncpg://"):
            await conn.execute(text("""
                ALTER TABLE questions
                ADD COLUMN IF NOT EXISTS image_path VARCHAR(500),
                ADD COLUMN IF NOT EXISTS question_mode VARCHAR(20) DEFAULT 'closed',
                ADD COLUMN IF NOT EXISTS correct_answer TEXT
            """))

        else:
            # SQLite
            columns = await conn.execute(
                text("PRAGMA table_info(questions)")
            )
            existing = {row[1] for row in columns.fetchall()}

            if "image_path" not in existing:
                await conn.execute(
                    text("ALTER TABLE questions ADD COLUMN image_path VARCHAR(500)")
                )

            if "question_mode" not in existing:
                await conn.execute(
                    text(
                        "ALTER TABLE questions "
                        "ADD COLUMN question_mode VARCHAR(20) DEFAULT 'closed'"
                    )
                )

            if "correct_answer" not in existing:
                await conn.execute(
                    text("ALTER TABLE questions ADD COLUMN correct_answer TEXT")
                )
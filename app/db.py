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

engine = create_async_engine(database_url, echo=False)

SessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False
)


async def init_db():
    from . import models

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

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
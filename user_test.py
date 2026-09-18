import asyncio
from sqlalchemy import text
from app.db import engine

async def main():
    async with engine.connect() as conn:
        result = await conn.execute(text("""
            SELECT id, telegram_id, username, full_name,
                   total_tests, total_questions,
                   correct_answers, total_score
            FROM users
            ORDER BY id
        """))

        for row in result:
            print(row)

    await engine.dispose()

asyncio.run(main())

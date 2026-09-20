import asyncio
from sqlalchemy import text
from app.db import engine

async def main():
    try:
        async with engine.connect() as conn:

            print("=== TEST ATTEMPTS ===")

            result = await conn.execute(text("""
                SELECT id, user_id, topic_id, total, correct, score, created_at
                FROM test_attempts
                ORDER BY id DESC
                LIMIT 12
            """))

            for row in result:
                print(row)

            print()
            print("=== ANSWER LOGS ===")

            result = await conn.execute(text("""
                SELECT id, attempt_id, question_id, selected_option, is_correct
                FROM answer_logs
                ORDER BY id DESC
                LIMIT 12
            """))

            for row in result:
                print(row)

    except Exception as e:
        print("DATABASE ERROR:", repr(e))
    finally:
        await engine.dispose()

asyncio.run(main())

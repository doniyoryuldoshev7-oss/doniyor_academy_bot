import asyncio
from app.db import SessionLocal
from app.models import AnswerLog, TestAttempt
from sqlalchemy import select

async def main():
    async with SessionLocal() as s:
        attempts = (await s.scalars(
            select(TestAttempt)
            .order_by(TestAttempt.id.desc())
            .limit(3)
        )).all()

        for a in attempts:
            logs = (await s.scalars(
                select(AnswerLog)
                .where(AnswerLog.attempt_id == a.id)
            )).all()

            print(f"TEST ID={a.id} | {a.correct}/{a.total} | LOGS={len(logs)}")

            for x in logs:
                print(
                    f"  QID={x.question_id} | "
                    f"SELECTED={x.selected_option} | "
                    f"CORRECT={x.is_correct}"
                )

            print("-" * 60)

if __name__ == "__main__":
    asyncio.run(main())

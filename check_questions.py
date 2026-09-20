import asyncio
from app.db import SessionLocal
from app.models import Question, Topic
from sqlalchemy import select

async def main():
    async with SessionLocal() as s:
        qs = (await s.scalars(select(Question).limit(20))).all()

        for q in qs:
            topic = await s.get(Topic, q.topic_id)
            print(f"ID={q.id} | TOPIC={topic.name if topic else '?'} | CORRECT=[{q.correct_option}]")
            print(f"  A={q.option_a}")
            print(f"  B={q.option_b}")
            print(f"  C={q.option_c}")
            print(f"  D={q.option_d}")
            print("-" * 60)

if __name__ == "__main__":
    asyncio.run(main())

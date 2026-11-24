"""Bazani tezlashtirish: indekslar.

Joylashuvi: app/maintenance.py

PostgreSQL begona kalit (FOREIGN KEY) ustunlariga indeksni o'zi
yaratmaydi. Hisobot, statistika, Bilim xaritasi va Smart Takrorlash
so'rovlari answer_logs va test_attempts jadvallarini shu ustunlar
bo'yicha bog'laydi, shuning uchun ma'lumot ko'payganda ular
sekinlashib boradi.

Hamma buyruq "IF NOT EXISTS" bilan, ya'ni qayta-qayta ishga
tushirish xavfsiz. Bitta indeks xato bersa, qolganlari davom etadi.
"""

from __future__ import annotations

import logging

from sqlalchemy import text

from .db import engine

log = logging.getLogger(__name__)

INDEXES = (
    ("ix_answer_logs_attempt_id", "answer_logs", "attempt_id"),
    ("ix_answer_logs_question_id", "answer_logs", "question_id"),
    ("ix_test_attempts_user_created", "test_attempts", "user_id, created_at"),
    ("ix_test_attempts_topic_id", "test_attempts", "topic_id"),
    ("ix_questions_topic_active", "questions", "topic_id, is_active"),
    ("ix_topics_subject_id", "topics", "subject_id"),
    ("ix_group_quizzes_status", "group_quizzes", "status"),
    ("ix_users_total_score", "users", "total_score"),
    ("ix_users_created_at", "users", "created_at"),
    ("ix_answer_logs_wrong", "answer_logs", "is_correct, resolved, question_id"),
    ("ix_group_participants_quiz", "group_quiz_participants", "group_quiz_id"),
    ("ix_group_answers_quiz_q", "group_quiz_answers", "group_quiz_id, question_id"),
)


async def ensure_indexes() -> int:
    created = 0

    for name, table, columns in INDEXES:
        try:
            async with engine.begin() as conn:
                await conn.execute(
                    text(
                        f"CREATE INDEX IF NOT EXISTS {name} "
                        f"ON {table} ({columns})"
                    )
                )

            created += 1
        except Exception:
            log.exception("Indeks yaratilmadi: %s", name)

    log.info("Baza indekslari tekshirildi (%s ta).", created)
    return created

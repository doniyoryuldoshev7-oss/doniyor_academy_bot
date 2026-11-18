from datetime import datetime, timedelta, timezone

INTERVALS = (1, 3, 7, 14)


def schedule_review(stage=0, is_correct=False, now=None):
    """
    stage: 0, 1, 2, 3 yoki 4
    is_correct: o'quvchining javobi
    """

    if now is None:
        now = datetime.now(timezone.utc)

    if now.tzinfo is None:
        raise ValueError("Vaqt timezone bilan berilishi kerak")

    if not isinstance(stage, int) or not 0 <= stage <= 4:
        raise ValueError("Noto'g'ri bosqich")

    if stage == 4 and is_correct:
        return {
            "stage": 4,
            "status": "mastered",
            "next_review_at": None,
        }

    if not is_correct:
        stage = 0
    else:
        stage += 1

    if stage >= 4:
        return {
            "stage": 4,
            "status": "mastered",
            "next_review_at": None,
        }

    days = INTERVALS[stage]

    return {
        "stage": stage,
        "status": "active",
        "next_review_at": now + timedelta(days=days),
    }

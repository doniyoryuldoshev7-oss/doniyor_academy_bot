"""Kichik tuzatishlar: mavzu nomidagi paragraf belgisi (§) va Toshkent vaqti.

1) Mavzu nomi «1-&. Mavzu» ko'rinishida kelsa (fayl/matn kodlashda § belgisi & ga aylanib qolgan),
   saqlashda avtomatik «1-§. Mavzu» qilinadi. Bu har qanday yo'l uchun ishlaydi: botdagi import,
   admin menyu, veb-ilova. Bot ishga tushganda mavjud mavzu nomlari ham bir marta tuzatiladi.
2) now_uz(): Toshkent vaqti (UTC+5), eksport sarlavhalari va izohlari uchun.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone

log = logging.getLogger(__name__)

UZ = timezone(timedelta(hours=5))
_AFTER_NUM = re.compile(r"(?<=\d)\s*-\s*&(?=[\s.]|$)")
_LEADING = re.compile(r"^(\s*)&(?=\s*\d)")


def now_uz() -> datetime:
    return datetime.now(UZ)


def fix_topic_name(name):
    """«1-&. Matn» -> «1-§. Matn»; «& 5. Matn» -> «§ 5. Matn». Boshqa & belgilariga tegmaydi."""
    if not isinstance(name, str) or "&" not in name:
        return name
    out = _AFTER_NUM.sub("-§", name)
    out = _LEADING.sub(r"\1§", out)
    return out


_installed = False


def install() -> bool:
    """Mavzu saqlanayotganda (yaratish va o'zgartirish) nomni avtomatik tuzatadi."""
    global _installed
    if _installed:
        return True
    try:
        from sqlalchemy import event

        from .models import Topic

        def _fix(mapper, connection, target):
            fixed = fix_topic_name(target.name)
            if fixed != target.name:
                target.name = fixed

        event.listen(Topic, "before_insert", _fix)
        event.listen(Topic, "before_update", _fix)
        _installed = True
        return True
    except Exception:
        log.exception("textfix: avtomatik tuzatish ulanmadi")
        return False


async def fix_existing() -> int:
    """Bazadagi mavjud mavzu nomlarini tuzatadi. Qaytaradi: tuzatilganlar soni."""
    try:
        from sqlalchemy import select

        from .db import SessionLocal
        from .models import Topic

        n = 0
        async with SessionLocal() as s:
            rows = (await s.scalars(select(Topic).where(Topic.name.like("%&%")))).all()
            for t in rows:
                fixed = fix_topic_name(t.name)
                if fixed != t.name:
                    t.name = fixed
                    n += 1
            if n:
                await s.commit()
        return n
    except Exception:
        log.exception("textfix: mavjud nomlarni tuzatib bo'lmadi")
        return -1

"""Barqarorlik yangilanishi.

1) Test jarayoni bazada saqlanadi (bot qayta ishga tushsa ham yo'qolmaydi).
2) Guruh testi natijalari umumiy test tarixiga yoziladi.
3) Test yozuviga "manba" ustuni (agar hali qo'shilmagan bo'lsa).

Ishlatish (loyiha ildizida):  python patch_chidamlilik.py
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

changed = []
skipped = []
failed = []


def backup(path):
    target = path.with_suffix(path.suffix + ".bak_before_barqaror")

    if not target.exists():
        shutil.copy2(path, target)


def patch(relative, old, new, label, marker):
    path = ROOT / relative

    if not path.is_file():
        failed.append(label + ": " + relative + " topilmadi")
        return

    with open(path, "r", encoding="utf-8", newline="") as handle:
        text = handle.read()

    crlf = "\r\n" in text

    if crlf:
        old = old.replace("\n", "\r\n")
        new = new.replace("\n", "\r\n")
        marker = marker.replace("\n", "\r\n")

    if marker in text:
        skipped.append(label + " (allaqachon bor)")
        return

    count = text.count(old)

    if count != 1:
        failed.append(label + ": anchor " + str(count) + " marta topildi")
        return

    backup(path)

    with open(path, "w", encoding="utf-8", newline="") as handle:
        handle.write(text.replace(old, new, 1))

    changed.append(label)


for name in ("app/fsm_storage.py", "app/services/group_results.py"):
    if not (ROOT / name).is_file():
        print("XATO: yangi fayl joyida emas:", name)
        sys.exit(1)


# --- test yozuvi: manba ustuni (diagnostika patchi bilan bir xil)
patch(
    "app/models.py",
    "    score: Mapped[int]=mapped_column(Integer)\n"
    "    created_at: Mapped[datetime]=mapped_column("
    "DateTime,default=datetime.utcnow)\n"
    "\n"
    "class AnswerLog(Base):",
    "    score: Mapped[int]=mapped_column(Integer)\n"
    "    source: Mapped[str]=mapped_column(String(20),default=\"quiz\")\n"
    "    created_at: Mapped[datetime]=mapped_column("
    "DateTime,default=datetime.utcnow)\n"
    "\n"
    "class AnswerLog(Base):",
    "models.py: test yozuvi manbasi",
    'source: Mapped[str]=mapped_column(String(20)',
)

# --- guruh ishtirokchisi: test yozuvi havolasi
patch(
    "app/models.py",
    "    joined_at: Mapped[datetime] = mapped_column(\n"
    "        DateTime,\n"
    "        default=datetime.utcnow\n"
    "    )\n",
    "    joined_at: Mapped[datetime] = mapped_column(\n"
    "        DateTime,\n"
    "        default=datetime.utcnow\n"
    "    )\n"
    "    attempt_id: Mapped[int | None] = mapped_column(\n"
    "        Integer,\n"
    "        nullable=True\n"
    "    )\n",
    "models.py: guruh ishtirokchisi -> test yozuvi",
    "attempt_id: Mapped[int | None]",
)

# --- bazaga ustunlar
patch(
    "app/db.py",
    "        await conn.run_sync(Base.metadata.create_all)\n",
    "        await conn.run_sync(Base.metadata.create_all)\n"
    "\n"
    "        # test_attempts: yozuv manbasi (oddiy test / diagnostika / guruh)\n"
    "        if database_url.startswith(\"postgresql+asyncpg://\"):\n"
    "            await conn.execute(text(\n"
    "                \"ALTER TABLE test_attempts ADD COLUMN IF NOT EXISTS \"\n"
    "                \"source VARCHAR(20) NOT NULL DEFAULT 'quiz'\"\n"
    "            ))\n"
    "        else:\n"
    "            _src_cols = await conn.execute(\n"
    "                text(\"PRAGMA table_info(test_attempts)\")\n"
    "            )\n"
    "            if \"source\" not in {r[1] for r in _src_cols.fetchall()}:\n"
    "                await conn.execute(text(\n"
    "                    \"ALTER TABLE test_attempts ADD COLUMN \"\n"
    "                    \"source VARCHAR(20) NOT NULL DEFAULT 'quiz'\"\n"
    "                ))\n",
    "db.py: test_attempts.source",
    "ALTER TABLE test_attempts ADD COLUMN",
)

patch(
    "app/db.py",
    "        await conn.run_sync(Base.metadata.create_all)\n",
    "        await conn.run_sync(Base.metadata.create_all)\n"
    "\n"
    "        # group_quiz_participants: yaratilgan test yozuvi havolasi\n"
    "        if database_url.startswith(\"postgresql+asyncpg://\"):\n"
    "            await conn.execute(text(\n"
    "                \"ALTER TABLE group_quiz_participants \"\n"
    "                \"ADD COLUMN IF NOT EXISTS attempt_id INTEGER\"\n"
    "            ))\n"
    "        else:\n"
    "            _gp_cols = await conn.execute(\n"
    "                text(\"PRAGMA table_info(group_quiz_participants)\")\n"
    "            )\n"
    "            if \"attempt_id\" not in {r[1] for r in _gp_cols.fetchall()}:\n"
    "                await conn.execute(text(\n"
    "                    \"ALTER TABLE group_quiz_participants \"\n"
    "                    \"ADD COLUMN attempt_id INTEGER\"\n"
    "                ))\n",
    "db.py: group_quiz_participants.attempt_id",
    "ALTER TABLE group_quiz_participants",
)

# --- guruh testi tugaganda natijani tarixga yozish
patch(
    "app/handlers/group_quiz.py",
    "                user.total_score += participant.score\n"
    "\n"
    "        await session.commit()\n",
    "                user.total_score += participant.score\n"
    "\n"
    "        # Natijani umumiy test tarixiga ham yozamiz (hisobot, bilim\n"
    "        # xaritasi, Smart Takrorlash uchun). Xato chiqsa ham guruh\n"
    "        # natijasi e'lon qilinaveradi.\n"
    "        try:\n"
    "            async with session.begin_nested():\n"
    "                from ..services.group_results import (\n"
    "                    record_group_attempts,\n"
    "                )\n"
    "\n"
    "                await record_group_attempts(\n"
    "                    session,\n"
    "                    group_quiz,\n"
    "                    list(participants),\n"
    "                )\n"
    "        except Exception as exc:\n"
    "            print(\n"
    "                \">>> GROUP HISTORY ERROR: \"\n"
    "                f\"{type(exc).__name__}: {exc}\",\n"
    "                flush=True,\n"
    "            )\n"
    "\n"
    "        await session.commit()\n",
    "group_quiz.py: natijani test tarixiga yozish",
    "record_group_attempts(",
)

# --- bot holatini bazada saqlash + eski guruh testlarini tarixga qo'shish
patch(
    "app/__main__.py",
    "    dp = Dispatcher(\n"
    "        storage=MemoryStorage()\n"
    "    )\n",
    "    from .db import engine as _engine\n"
    "    from .fsm_storage import make_storage\n"
    "\n"
    "    dp = Dispatcher(\n"
    "        storage=await make_storage(_engine)\n"
    "    )\n",
    "__main__.py: holat bazada saqlanadi",
    "await make_storage(_engine)",
)

patch(
    "app/__main__.py",
    "    await init_db()\n",
    "    await init_db()\n"
    "\n"
    "    # Oldin tugagan guruh testlarini test tarixiga qo'shish (bir marta)\n"
    "    from .services.group_results import backfill_group_attempts\n"
    "\n"
    "    _backfill_task = asyncio.create_task(\n"
    "        backfill_group_attempts(SessionLocal)\n"
    "    )\n",
    "__main__.py: eski guruh testlarini qo'shish",
    "backfill_group_attempts(SessionLocal)",
)

# --- tanlangan variant noma'lum bo'lganda yiqilmasligi uchun
patch(
    "app/handlers/student.py",
    "    return {\"A\": q.option_a, \"B\": q.option_b, \"C\": q.option_c, "
    "\"D\": q.option_d}[l]\n",
    "    return {\"A\": q.option_a, \"B\": q.option_b, \"C\": q.option_c, "
    "\"D\": q.option_d}.get((l or \"\").strip().upper()) or \"—\"\n",
    "student.py: noma'lum variantda yiqilmaslik",
    ".get((l or \"\").strip().upper()) or",
)


print("=" * 56)
print("  BARQARORLIK")
print("=" * 56)

for title, items, sign in (
    ("Qo'shildi:", changed, "+"),
    ("O'tkazib yuborildi:", skipped, "="),
    ("XATO:", failed, "!"),
):
    if items:
        print()
        print(title)

        for item in items:
            print("  ", sign, item)

print()
print("-" * 56)

if failed:
    print("Patch to'liq qo'llanmadi.")
    sys.exit(1)

print("Tayyor.")

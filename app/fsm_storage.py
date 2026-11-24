"""Bot holatini (FSM) bazada saqlash.

Joylashuvi: app/fsm_storage.py

MemoryStorage faqat xotirada saqlaydi, shuning uchun bot qayta
ishga tushganda (deploy, Railway qayta ishga tushirishi) barcha
tugallanmagan testlar yo'qolardi. Bu sinf holatni mavjud
PostgreSQL bazasiga ham yozadi.

Ishlash tartibi:
  * o'qish va yozish avval xotiradagi nusxada bajariladi (tez va
    bir vaqtdagi bosishlarda tartib buzilmaydi);
  * har o'zgarish bazaga ham yoziladi;
  * bot qayta ishga tushsa, holat bazadan avtomatik tiklanadi.

Baza bilan xato bo'lsa, bot to'xtamaydi: xotiradagi nusxa ishlayveradi.
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
from datetime import date, datetime, timedelta
from decimal import Decimal

from aiogram.fsm.state import State
from aiogram.fsm.storage.base import BaseStorage, StorageKey
from sqlalchemy import text

log = logging.getLogger(__name__)

# Shu muddatdan eski holat tiklanmaydi (eskirgan seans)
STATE_TTL = timedelta(hours=24)

# Bazadan butunlay o'chiriladigan muddat
PURGE_AFTER = timedelta(days=7)

TAG = "__t"


# ---------------------------------------------------------------
#  Ma'lumotni JSON ga aylantirish (turini yo'qotmasdan)
# ---------------------------------------------------------------

def encode(value):
    """Python qiymatini JSON ga mos ko'rinishga o'tkazadi.

    Oddiy lug'at/ro'yxat/son/matn o'zgarishsiz qoladi. Kalitlari
    matn bo'lmagan lug'at, tuple, set, sana va Decimal esa belgi
    bilan yoziladi, shunda tiklanganda aynan o'sha turda qaytadi.
    """
    if value is None or isinstance(value, (bool, int, float, str)):
        return value

    if isinstance(value, dict):
        if all(isinstance(key, str) for key in value) and TAG not in value:
            return {key: encode(item) for key, item in value.items()}

        return {
            TAG: "map",
            "v": [[encode(key), encode(item)] for key, item in value.items()],
        }

    if isinstance(value, list):
        return [encode(item) for item in value]

    if isinstance(value, tuple):
        return {TAG: "tuple", "v": [encode(item) for item in value]}

    if isinstance(value, (set, frozenset)):
        return {TAG: "set", "v": [encode(item) for item in value]}

    if isinstance(value, datetime):
        return {TAG: "dt", "v": value.isoformat()}

    if isinstance(value, date):
        return {TAG: "date", "v": value.isoformat()}

    if isinstance(value, Decimal):
        return {TAG: "dec", "v": str(value)}

    return {TAG: "str", "v": str(value)}


def decode(value):
    if isinstance(value, list):
        return [decode(item) for item in value]

    if isinstance(value, dict):
        kind = value.get(TAG)

        if kind is None:
            return {key: decode(item) for key, item in value.items()}

        raw = value.get("v")

        if kind == "map":
            return {decode(key): decode(item) for key, item in raw}

        if kind == "tuple":
            return tuple(decode(item) for item in raw)

        if kind == "set":
            return {_hashable(decode(item)) for item in raw}

        if kind == "dt":
            return datetime.fromisoformat(raw)

        if kind == "date":
            return date.fromisoformat(raw)

        if kind == "dec":
            return Decimal(raw)

        return raw

    return value


def _hashable(item):
    if isinstance(item, list):
        return tuple(_hashable(part) for part in item)

    return item


# ---------------------------------------------------------------
#  Saqlash joyi
# ---------------------------------------------------------------

class PgStorage(BaseStorage):
    TABLE = "fsm_storage"

    def __init__(self, engine):
        self.engine = engine
        self._cache: dict[str, dict] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._ready = False

    # -- yordamchilar -------------------------------------------

    @staticmethod
    def make_key(key: StorageKey) -> str:
        return ":".join(
            str(part)
            for part in (
                key.bot_id,
                key.chat_id,
                key.user_id,
                getattr(key, "thread_id", None),
                getattr(key, "business_connection_id", None),
                key.destiny,
            )
        )

    def _lock(self, name: str) -> asyncio.Lock:
        lock = self._locks.get(name)

        if lock is None:
            lock = self._locks[name] = asyncio.Lock()

        return lock

    async def setup(self) -> None:
        async with self.engine.begin() as conn:
            await conn.execute(
                text(
                    f"CREATE TABLE IF NOT EXISTS {self.TABLE} ("
                    "key VARCHAR(255) PRIMARY KEY, "
                    "state VARCHAR(255), "
                    "data TEXT NOT NULL DEFAULT '{}', "
                    "updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP)"
                )
            )

            await conn.execute(
                text(
                    f"DELETE FROM {self.TABLE} WHERE updated_at < :limit"
                ),
                {"limit": datetime.utcnow() - PURGE_AFTER},
            )

        self._ready = True

    # -- bazaga yozish / bazadan o'qish --------------------------

    async def _persist(self, name: str) -> None:
        """Xotiradagi eng oxirgi nusxani bazaga yozadi."""
        if not self._ready:
            return

        async with self._lock(name):
            entry = self._cache.get(name)

            try:
                async with self.engine.begin() as conn:
                    if entry is None or (
                        entry["state"] is None and not entry["data"]
                    ):
                        await conn.execute(
                            text(f"DELETE FROM {self.TABLE} WHERE key = :key"),
                            {"key": name},
                        )
                        return

                    await conn.execute(
                        text(
                            f"INSERT INTO {self.TABLE} "
                            "(key, state, data, updated_at) "
                            "VALUES (:key, :state, :data, :now) "
                            "ON CONFLICT (key) DO UPDATE SET "
                            "state = :state, data = :data, "
                            "updated_at = :now"
                        ),
                        {
                            "key": name,
                            "state": entry["state"],
                            "data": json.dumps(
                                encode(entry["data"]), ensure_ascii=False
                            ),
                            "now": datetime.utcnow(),
                        },
                    )
            except Exception:
                log.exception("FSM holatini bazaga yozib bo'lmadi: %s", name)

    async def _load(self, name: str) -> dict:
        """Xotirada bo'lmasa, bazadan tiklaydi."""
        entry = self._cache.get(name)

        if entry is not None:
            return entry

        async with self._lock(name):
            entry = self._cache.get(name)

            if entry is not None:
                return entry

            entry = {"state": None, "data": {}}

            if self._ready:
                try:
                    async with self.engine.begin() as conn:
                        row = (
                            await conn.execute(
                                text(
                                    f"SELECT state, data, updated_at FROM "
                                    f"{self.TABLE} WHERE key = :key"
                                ),
                                {"key": name},
                            )
                        ).fetchone()

                    if row is not None:
                        updated = row[2]

                        if isinstance(updated, str):
                            updated = datetime.fromisoformat(updated)

                        fresh = (
                            updated is None
                            or datetime.utcnow() - updated <= STATE_TTL
                        )

                        if fresh:
                            entry = {
                                "state": row[0],
                                "data": decode(json.loads(row[1] or "{}")),
                            }
                except Exception:
                    log.exception(
                        "FSM holatini bazadan o'qib bo'lmadi: %s", name
                    )

            # Boshqa vazifa o'rtada yozib qo'ygan bo'lsa, uni buzmaymiz
            return self._cache.setdefault(name, entry)

    # -- BaseStorage interfeysi ----------------------------------

    async def set_state(self, key: StorageKey, state=None) -> None:
        name = self.make_key(key)
        entry = await self._load(name)

        entry["state"] = state.state if isinstance(state, State) else state

        await self._persist(name)

    async def get_state(self, key: StorageKey):
        entry = await self._load(self.make_key(key))
        return entry["state"]

    async def set_data(self, key: StorageKey, data) -> None:
        if not isinstance(data, dict):
            raise ValueError(
                f"Data must be a dict, got {type(data).__name__}"
            )

        name = self.make_key(key)
        entry = await self._load(name)

        entry["data"] = copy.deepcopy(data)

        await self._persist(name)

    async def get_data(self, key: StorageKey) -> dict:
        entry = await self._load(self.make_key(key))
        return copy.deepcopy(entry["data"])

    async def update_data(self, key: StorageKey, data) -> dict:
        name = self.make_key(key)
        entry = await self._load(name)

        # Xotiradagi o'zgarish bir zumda bajariladi (await yo'q), shuning
        # uchun bir vaqtdagi ikki so'rov bir-birining yozuvini yo'qotmaydi
        entry["data"].update(copy.deepcopy(data))

        await self._persist(name)

        return copy.deepcopy(entry["data"])

    async def close(self) -> None:
        return None


async def make_storage(engine):
    """Bazali saqlash; bo'lmasa xotiradagisiga qaytadi (bot ishlayveradi)."""
    try:
        storage = PgStorage(engine)
        await storage.setup()
        log.info("FSM holati bazada saqlanadi.")
        return storage
    except Exception:
        log.exception(
            "FSM bazasini tayyorlab bo'lmadi, xotiradagi saqlash ishlatiladi."
        )

        from aiogram.fsm.storage.memory import MemoryStorage

        return MemoryStorage()

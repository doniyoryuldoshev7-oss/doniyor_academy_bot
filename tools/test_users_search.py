"""Isolated regression tests for admin users search; no production DB access."""

import asyncio
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.handlers import admin
from app.models import User


class AsyncLocalSession:
    def __init__(self, engine):
        self.engine = engine

    async def __aenter__(self):
        self.db = Session(self.engine)
        return self

    async def __aexit__(self, *args):
        self.db.close()

    async def scalar(self, statement):
        return self.db.scalar(statement)

    async def scalars(self, statement):
        return self.db.scalars(statement)


class FakeState:
    def __init__(self):
        self.data = {}
        self.current = None

    async def get_state(self):
        return self.current

    async def set_state(self, value):
        self.current = getattr(value, "state", value)

    async def get_data(self):
        return dict(self.data)

    async def update_data(self, **kwargs):
        self.data.update(kwargs)


class FakeCallback:
    def __init__(self, data, actor=111):
        self.data = data
        self.from_user = SimpleNamespace(id=actor)
        self.message = SimpleNamespace(edit_text=AsyncMock())
        self.answer = AsyncMock()


class TestUsersSearch(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite:///:memory:", poolclass=StaticPool, connect_args={"check_same_thread": False}
        )

        @event.listens_for(self.engine, "connect")
        def add_translate(dbapi_conn, _):
            def translate(text, source, dest):
                return (text or "").translate(str.maketrans(source, dest))
            dbapi_conn.create_function("translate", 3, translate)

        Base.metadata.create_all(self.engine, tables=[User.__table__])
        now = datetime(2026, 11, 22)
        with Session(self.engine) as session:
            for i in range(34):
                session.add(User(
                    telegram_id=1000 + i,
                    full_name=f"Ali Valiyev {i}",
                    username=f"ali_{i}",
                    phone_number=f"+99890123{i:04d}",
                    group_name=f"Guruh {i % 2}",
                    registration_status="approved",
                    is_blocked=False,
                    created_at=now + timedelta(minutes=i),
                ))
            session.add(User(
                telegram_id=9999,
                full_name="𝒟 Doniyor",  # mathematical script capital D
                username="fancy_account",
                phone_number="+998901112233",
                group_name="Sinov",
                registration_status="approved",
                is_blocked=False,
                created_at=now,
            ))
            session.commit()
        self.factory = lambda: AsyncLocalSession(self.engine)
        self.patch_session = patch.object(admin, "SessionLocal", side_effect=self.factory)
        self.patch_session.start()

    def tearDown(self):
        self.patch_session.stop()
        self.engine.dispose()

    async def test_pagination_and_sort(self):
        text, markup = await admin._users_view(1, "new", "")
        self.assertIn("Jami: 35 | Sahifa: 2/3", text)
        callbacks = [b.callback_data for row in markup.inline_keyboard for b in row]
        self.assertIn("adm:users:page:0", callbacks)
        self.assertIn("adm:users:page:2", callbacks)
        self.assertEqual(sum(x.startswith("adm:user:") for x in callbacks), 15)

        text, markup = await admin._users_view(0, "az", "")
        self.assertIn("Tartib: A-Z", text)

    async def test_search_fields_and_literal_wildcards(self):
        for value in ("Valiyev 12", "@ali_12", "+998901230012", "1012"):
            text, markup = await admin._users_view(0, "new", value)
            self.assertIn("Jami:", text)
            self.assertTrue(any(
                b.callback_data == "adm:user:1012"
                for row in markup.inline_keyboard for b in row
            ), value)
        text, markup = await admin._users_view(0, "new", "Guruh 1")
        self.assertIn("Jami: 17", text)
        text, _ = await admin._users_view(0, "new", "%_\\")
        self.assertIn("Jami: 0", text)

    async def test_compatibility_glyph_and_html_escape(self):
        text, markup = await admin._users_view(0, "new", "D")
        self.assertTrue(any(
            b.callback_data == "adm:user:9999"
            for row in markup.inline_keyboard for b in row
        ))
        text, _ = await admin._users_view(0, "new", "<demo&>")
        self.assertIn("&lt;demo&amp;&gt;", text)

    async def test_security_state_and_callbacks(self):
        state = FakeState()
        user = FakeCallback("adm:users", actor=999)
        with patch.object(admin, "is_admin", return_value=False):
            await admin.admin_users(user, state)
            user.message.edit_text.assert_not_awaited()
            await admin.admin_users_find(user, state)
            self.assertIsNone(state.current)

        with patch.object(admin, "is_admin", return_value=True):
            callback = FakeCallback("adm:users:find")
            await admin.admin_users_find(callback, state)
            self.assertEqual(state.current, admin.AdminState.user_search.state)
            message = SimpleNamespace(
                from_user=SimpleNamespace(id=111),
                text="@ali_12",
                answer=AsyncMock(),
            )
            await admin.admin_users_find_result(message, state)
            self.assertIsNone(state.current)
            self.assertEqual(state.data["users_query"], "@ali_12")
            message.answer.assert_awaited_once()

            callback = FakeCallback("adm:users:clear")
            await admin.admin_users(callback, state)
            self.assertEqual(state.data["users_query"], "")


if __name__ == "__main__":
    unittest.main(verbosity=2)

"""Offline role regression tests. No network or production DB access."""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.handlers import admin
from app.models import AdminRoleEvent, QuestionEditorGrant
import app.admin_roles as roles
import app.role_management as management


class FakeSession:
    def __init__(self, *results):
        self.results = list(results)
        self.added = []
        self.deleted = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def begin(self):
        return self

    async def scalar(self, _query):
        return self.results.pop(0)

    def add(self, item):
        self.added.append(item)

    async def delete(self, item):
        self.deleted.append(item)


class FakeMessage:
    def __init__(self, text="A varianti"):
        self.text = text
        self.from_user = SimpleNamespace(id=222)
        self.edits = []
        self.answers = []

    async def edit_text(self, text, **kw):
        self.edits.append((text, kw))

    async def answer(self, text=None, **kw):
        self.answers.append((text, kw))


class FakeCallback:
    def __init__(self, actor, data):
        self.data = data
        self.from_user = SimpleNamespace(id=actor)
        self.message = FakeMessage()
        self.answers = []

    async def answer(self, text=None, **kw):
        self.answers.append(text)


class TestAdminRoleWorkflow(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.user = SimpleNamespace(
            id=2, telegram_id=222, registration_status="approved",
            is_blocked=False, full_name="Ali Valiyev",
            phone_number="+998901234567", group_name="175-guruh",
        )

    async def test_question_editor_permissions(self):
        with patch.object(roles, "is_full_admin", return_value=False):
            for result, expected in [(self.user, True), (None, False)]:
                db = FakeSession(result)
                with patch.object(roles, "SessionLocal", return_value=db):
                    self.assertEqual(await roles.can_add_questions(222), expected)

            self.user.registration_status = "pending"
            db = FakeSession(None)
            with patch.object(roles, "SessionLocal", return_value=db):
                self.assertFalse(await roles.can_add_questions(222))

        with patch.object(roles, "is_full_admin", return_value=True):
            self.assertTrue(await roles.can_add_questions(111))

    async def test_grant_revoke_and_audit(self):
        with patch.object(management, "is_superadmin", return_value=True), patch.object(
            management, "is_full_admin", return_value=False
        ):
            db = FakeSession(self.user, None)
            with patch.object(management, "SessionLocal", return_value=db):
                self.assertEqual(await management.set_question_editor_role(
                    actor_telegram_id=111, target_telegram_id=222, enable=True
                ), "grant")
            self.assertTrue(any(isinstance(x, QuestionEditorGrant) for x in db.added))
            self.assertTrue(any(isinstance(x, AdminRoleEvent) and x.action == "grant" for x in db.added))

            grant = QuestionEditorGrant(user_id=2, granted_by=111)
            db = FakeSession(self.user, grant)
            with patch.object(management, "SessionLocal", return_value=db):
                self.assertEqual(await management.set_question_editor_role(
                    actor_telegram_id=111, target_telegram_id=222, enable=False
                ), "revoke")
            self.assertEqual(db.deleted, [grant])
            self.assertTrue(any(isinstance(x, AdminRoleEvent) and x.action == "revoke" for x in db.added))

    async def test_reject_unauthorized_and_ineligible(self):
        with patch.object(management, "is_superadmin", return_value=False):
            with self.assertRaises(management.RolePermissionDenied):
                await management.set_question_editor_role(
                    actor_telegram_id=999, target_telegram_id=222, enable=True
                )

        self.user.is_blocked = True
        db = FakeSession(self.user, None)
        with patch.object(management, "is_superadmin", return_value=True), patch.object(
            management, "is_full_admin", return_value=False
        ), patch.object(management, "SessionLocal", return_value=db):
            with self.assertRaises(management.RoleOperationRejected):
                await management.set_question_editor_role(
                    actor_telegram_id=111, target_telegram_id=222, enable=True
                )
        self.assertEqual(db.added, [])

    async def test_choice_a_for_editor_and_denial(self):
        state = SimpleNamespace(
            update_data=AsyncMock(), set_state=AsyncMock(),
        )
        message = FakeMessage("Variant A")
        with patch.object(admin, "can_add_questions", new=AsyncMock(return_value=True)):
            await admin.q_a(message, state)
        state.update_data.assert_awaited_once_with(option_a="Variant A")
        state.set_state.assert_awaited_once_with(admin.AdminState.add_question_b)

        state.update_data.reset_mock()
        state.set_state.reset_mock()
        with patch.object(admin, "can_add_questions", new=AsyncMock(return_value=False)):
            await admin.q_a(message, state)
        state.update_data.assert_not_awaited()
        state.set_state.assert_not_awaited()

    async def test_role_callback_superadmin_enforced(self):
        with patch.object(admin, "is_superadmin", return_value=False), patch.object(
            admin, "set_question_editor_role", new_callable=AsyncMock
        ) as service:
            callback = FakeCallback(999, "adm:role:grant:do:222")
            await admin.editor_role_callback(callback)
            service.assert_not_awaited()
            self.assertEqual(callback.answers, ["Ruxsat yo'q."])

        with patch.object(admin, "is_superadmin", return_value=True), patch.object(
            admin, "set_question_editor_role", new_callable=AsyncMock
        ) as service:
            for action, enable in [("grant", True), ("revoke", False)]:
                service.reset_mock()
                callback = FakeCallback(111, f"adm:role:{action}:do:222")
                await admin.editor_role_callback(callback)
                service.assert_awaited_once_with(
                    actor_telegram_id=111, target_telegram_id=222, enable=enable
                )
                self.assertTrue(callback.message.edits)

    async def test_user_detail_buttons_and_html_escaping(self):
        self.user.full_name = "Ali <Valiyev>"
        self.user.username = "<unsafe>"
        callback = FakeCallback(111, "adm:user:222")
        db = FakeSession(self.user, None)
        with patch.object(admin, "is_admin", side_effect=lambda uid: uid in {111, 333}), patch.object(
            admin, "is_superadmin", return_value=True
        ), patch.object(admin, "SessionLocal", return_value=db):
            await admin.admin_user_detail(callback)
        text, kwargs = callback.message.edits[0]
        self.assertIn("Ali &lt;Valiyev&gt;", text)
        self.assertIn("@&lt;unsafe&gt;", text)
        callbacks = [b.callback_data for row in kwargs["reply_markup"].inline_keyboard for b in row]
        self.assertIn("adm:role:grant:ask:222", callbacks)

        callback = FakeCallback(333, "adm:user:222")
        db = FakeSession(self.user, None)
        with patch.object(admin, "is_admin", side_effect=lambda uid: uid in {111, 333}), patch.object(
            admin, "is_superadmin", return_value=False
        ), patch.object(admin, "SessionLocal", return_value=db):
            await admin.admin_user_detail(callback)
        _, kwargs = callback.message.edits[0]
        callbacks = [b.callback_data for row in kwargs["reply_markup"].inline_keyboard for b in row]
        self.assertFalse(any(item.startswith("adm:role:") for item in callbacks))

        callback = FakeCallback(111, "adm:user:222")
        db = FakeSession(self.user, 42)
        with patch.object(admin, "is_admin", side_effect=lambda uid: uid in {111, 333}), patch.object(
            admin, "is_superadmin", return_value=True
        ), patch.object(admin, "SessionLocal", return_value=db):
            await admin.admin_user_detail(callback)
        _, kwargs = callback.message.edits[0]
        callbacks = [b.callback_data for row in kwargs["reply_markup"].inline_keyboard for b in row]
        self.assertIn("adm:role:revoke:ask:222", callbacks)

    async def test_user_list_pagination(self):
        users = [SimpleNamespace(
            telegram_id=i, full_name=f"Foydalanuvchi {i}",
            registration_status="approved", phone_number="123",
            group_name="group",
        ) for i in range(15)]

        class Result:
            def all(self):
                return users

        class ListSession(FakeSession):
            async def scalar(self, query):
                return 31

            async def scalars(self, query):
                self.query = query
                return Result()

        db = ListSession()
        callback = FakeCallback(111, "adm:users:page:1")
        with patch.object(admin, "is_admin", return_value=True), patch.object(
            admin, "SessionLocal", return_value=db
        ):
            await admin.admin_users(callback)
        text, kwargs = callback.message.edits[0]
        self.assertIn("Sahifa: 2/3", text)
        buttons = [b.callback_data for row in kwargs["reply_markup"].inline_keyboard for b in row]
        self.assertIn("adm:users:page:0", buttons)
        self.assertIn("adm:users:page:2", buttons)
        self.assertEqual(db.query._limit_clause.value, 15)
        self.assertEqual(db.query._offset_clause.value, 15)


if __name__ == "__main__":
    unittest.main(verbosity=2)

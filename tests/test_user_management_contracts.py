from pathlib import Path
import unittest

from app.web import auth_web
from app.web.users_web import _company_assignment_for_role

CLOUD_ROOT = Path(__file__).resolve().parents[1]


class UserManagementContractTest(unittest.TestCase):
    def test_user_management_is_reserved_for_ppg_admins(self):
        can_manage_users = getattr(auth_web, "_can_manage_users", None)

        self.assertIsNotNone(can_manage_users)
        self.assertTrue(can_manage_users("ppg_admin"))

        for role in ("ppg_support", "ship_owner", "crew", "external", None):
            with self.subTest(role=role):
                self.assertFalse(can_manage_users(role))

    def test_user_routes_require_ppg_admin_session(self):
        source = (CLOUD_ROOT / "app" / "web" / "users_web.py").read_text(encoding="utf-8")

        self.assertIn("require_ppg_admin_session", source)
        self.assertNotIn("Depends(require_admin_session)", source)

    def test_users_nav_item_is_visible_only_to_ppg_admins(self):
        source = (CLOUD_ROOT / "app" / "web" / "templates" / "base.html").read_text(encoding="utf-8")
        users_link = '<a href="/admin/users"'
        guard = "{% if request.session.get('user_role') == 'ppg_admin' %}"

        self.assertIn(users_link, source)
        self.assertIn(guard, source[:source.index(users_link)])

    def test_client_roles_require_company(self):
        valid, company_id, error = _company_assignment_for_role("ship_owner", "")

        self.assertFalse(valid)
        self.assertIsNone(company_id)
        self.assertEqual(error, "Client users must be assigned to a company")

    def test_client_roles_keep_company_assignment(self):
        valid, company_id, error = _company_assignment_for_role("crew", "company-123")

        self.assertTrue(valid)
        self.assertEqual(company_id, "company-123")
        self.assertIsNone(error)

    def test_ppg_staff_roles_clear_company_assignment(self):
        valid, company_id, error = _company_assignment_for_role("ppg_support", "company-123")

        self.assertTrue(valid)
        self.assertIsNone(company_id)
        self.assertIsNone(error)

    def test_unknown_roles_are_rejected(self):
        valid, company_id, error = _company_assignment_for_role("external", "company-123")

        self.assertFalse(valid)
        self.assertIsNone(company_id)
        self.assertEqual(error, "Invalid user role")


if __name__ == "__main__":
    unittest.main()

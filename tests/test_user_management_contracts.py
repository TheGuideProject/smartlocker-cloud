from pathlib import Path
import unittest

from app.web import auth_web
from app.web import users_web
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

    def test_user_portal_context_separates_ppg_and_client_roles(self):
        user_portal_context = getattr(users_web, "_user_portal_context", None)

        self.assertIsNotNone(user_portal_context)
        self.assertEqual(
            user_portal_context("ppg_admin"),
            {
                "label": "PPG Portal",
                "login_href": "/admin/login",
                "detail": "PPG operations workspace",
            },
        )
        self.assertEqual(
            user_portal_context("crew"),
            {
                "label": "Client Portal",
                "login_href": "/client/login",
                "detail": "Customer vessel workspace",
            },
        )
        self.assertEqual(
            user_portal_context("external"),
            {
                "label": "No web portal",
                "login_href": "",
                "detail": "Role is not enabled for web access",
            },
        )

    def test_user_role_options_use_client_facing_labels(self):
        user_role_options = getattr(users_web, "_user_role_options", None)

        self.assertIsNotNone(user_role_options)
        self.assertIn({"value": "ship_owner", "label": "Client Admin"}, user_role_options())
        self.assertIn({"value": "crew", "label": "Crew"}, user_role_options())

    def test_admin_templates_use_client_language_for_customer_side(self):
        fleet_template = (CLOUD_ROOT / "app" / "web" / "templates" / "admin" / "fleet.html").read_text(encoding="utf-8")
        users_template = (CLOUD_ROOT / "app" / "web" / "templates" / "admin" / "users.html").read_text(encoding="utf-8")

        self.assertNotIn("Ship Owner", fleet_template)
        self.assertNotIn("ship owner company", fleet_template)
        self.assertIn("Add Client Company", fleet_template)
        self.assertIn("client company", fleet_template)

        self.assertNotIn("Ship Owner", users_template)
        self.assertIn("Client Admin and Crew use the Client Portal", users_template)
        self.assertIn("Required for Client Admin and Crew", users_template)
        self.assertIn("Client Admin", users_template)

    def test_company_model_uses_client_company_language(self):
        source = (CLOUD_ROOT / "app" / "models" / "company.py").read_text(encoding="utf-8")

        self.assertNotIn("Ship owners", source)
        self.assertIn("Client companies", source)

    def test_users_route_attaches_portal_context_for_table(self):
        source = (CLOUD_ROOT / "app" / "web" / "users_web.py").read_text(encoding="utf-8")

        self.assertIn("_user_portal_context", source)
        self.assertIn("user.portal_context", source)

    def test_users_template_shows_portal_column_and_login_links(self):
        template = (CLOUD_ROOT / "app" / "web" / "templates" / "admin" / "users.html").read_text(encoding="utf-8")

        self.assertIn("<th>Portal</th>", template)
        self.assertIn("user.portal_context.label", template)
        self.assertIn("user.portal_context.detail", template)
        self.assertIn("user.portal_context.login_href", template)
        self.assertIn("Open login", template)


if __name__ == "__main__":
    unittest.main()

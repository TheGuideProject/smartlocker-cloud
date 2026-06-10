import unittest

from app.web.users_web import _company_assignment_for_role


class UserManagementContractTest(unittest.TestCase):
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

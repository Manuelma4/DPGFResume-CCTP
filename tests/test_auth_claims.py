import unittest

from app.auth import InvalidOAuthResponse, identity_from_claims


class IdentityClaimsTests(unittest.TestCase):
    def test_identity_preserves_moduo_role_and_groups(self):
        identity = identity_from_claims(
            {
                "sub": "authentik-user-uuid",
                "name": "Alice Martin",
                "email": "alice@example.test",
                "preferred_username": "alice",
                "groups": [
                    "Moduo Users",
                    "Moduo Role - Copil",
                    "Moduo Access - DPGF Resume CCTP",
                ],
            },
            required_group="Moduo Access - DPGF Resume CCTP",
        )

        self.assertEqual(identity.sub, "authentik-user-uuid")
        self.assertEqual(identity.role, "Copil")
        self.assertEqual(identity.username, "alice")
        self.assertEqual(
            identity.groups,
            (
                "Moduo Users",
                "Moduo Role - Copil",
                "Moduo Access - DPGF Resume CCTP",
            ),
        )
        self.assertFalse(identity.is_superuser)

    def test_identity_rejects_missing_application_access(self):
        with self.assertRaisesRegex(InvalidOAuthResponse, "pas autorise"):
            identity_from_claims(
                {
                    "sub": "user-1",
                    "groups": ["Moduo Users", "Moduo Role - Collaborateur"],
                },
                required_group="Moduo Access - DPGF Resume CCTP",
            )

    def test_identity_rejects_ambiguous_role(self):
        with self.assertRaisesRegex(InvalidOAuthResponse, "exactement un role"):
            identity_from_claims(
                {
                    "sub": "user-1",
                    "groups": [
                        "Moduo Role - Admin",
                        "Moduo Role - Copil",
                        "Moduo Access - DPGF Resume CCTP",
                    ],
                },
                required_group="Moduo Access - DPGF Resume CCTP",
            )

    def test_identity_preserves_superuser_marker_without_changing_app_role(self):
        identity = identity_from_claims(
            {
                "sub": "user-1",
                "groups": [
                    "Moduo Role - Admin",
                    "Moduo Security - Superusers",
                    "Moduo Access - DPGF Resume CCTP",
                ],
            },
            required_group="Moduo Access - DPGF Resume CCTP",
        )

        self.assertEqual(identity.role, "Admin")
        self.assertTrue(identity.is_superuser)

from __future__ import annotations

import unittest

from fastapi.testclient import TestClient

from app.main import app


class AuthCallbackTests(unittest.TestCase):
    def test_provider_error_is_reported_instead_of_missing_code(self) -> None:
        response = TestClient(app).get(
            "/api/auth/callback",
            params={
                "error": "invalid_request",
                "error_description": "The request is otherwise malformed",
                "state": "opaque-state",
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.json()["detail"],
            (
                "Le fournisseur OIDC a refusé la connexion (invalid_request) : "
                "The request is otherwise malformed"
            ),
        )


if __name__ == "__main__":
    unittest.main()

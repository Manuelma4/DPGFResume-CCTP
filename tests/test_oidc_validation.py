import json
import time
import unittest

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

from app.oidc_validation import TokenVerificationError, verify_signed_id_token


class OidcValidationTests(unittest.TestCase):
    def test_accepts_trusted_signature_and_rejects_forgery(self):
        issuer = "https://auth.example.test/application/o/dpgf-resume-cctp/"
        trusted = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        attacker = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(trusted.public_key()))
        jwk.update({"kid": "key-1", "alg": "RS256"})
        metadata = {
            "issuer": issuer,
            "id_token_signing_alg_values_supported": ["RS256"],
        }
        payload = {
            "iss": issuer,
            "aud": "dpgf-resume-cctp",
            "sub": "user-1",
            "nonce": "nonce-1",
            "iat": int(time.time()),
            "exp": int(time.time()) + 300,
        }

        valid = jwt.encode(
            payload,
            trusted,
            algorithm="RS256",
            headers={"kid": "key-1"},
        )
        claims = verify_signed_id_token(
            valid,
            metadata=metadata,
            jwks={"keys": [jwk]},
            client_id="dpgf-resume-cctp",
            expected_nonce="nonce-1",
        )
        self.assertEqual(claims["sub"], "user-1")

        forged = jwt.encode(
            payload,
            attacker,
            algorithm="RS256",
            headers={"kid": "key-1"},
        )
        with self.assertRaises(TokenVerificationError):
            verify_signed_id_token(
                forged,
                metadata=metadata,
                jwks={"keys": [jwk]},
                client_id="dpgf-resume-cctp",
                expected_nonce="nonce-1",
            )


if __name__ == "__main__":
    unittest.main()

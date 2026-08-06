from __future__ import annotations

import hmac
from typing import Any

import jwt


ASYMMETRIC_ID_TOKEN_ALGORITHMS = frozenset(
    {
        "RS256",
        "RS384",
        "RS512",
        "PS256",
        "PS384",
        "PS512",
        "ES256",
        "ES384",
        "ES512",
    }
)


class TokenVerificationError(ValueError):
    pass


class UnknownSigningKey(TokenVerificationError):
    pass


def verify_signed_id_token(
    token: str,
    *,
    metadata: dict[str, Any],
    jwks: dict[str, Any],
    client_id: str,
    expected_nonce: str,
) -> dict[str, Any]:
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError as exc:
        raise TokenVerificationError("Malformed ID token header") from exc

    algorithm = str(header.get("alg") or "")
    key_id = str(header.get("kid") or "")
    supported = set(metadata.get("id_token_signing_alg_values_supported") or [])
    if (
        algorithm not in ASYMMETRIC_ID_TOKEN_ALGORITHMS
        or algorithm not in supported
    ):
        raise TokenVerificationError("Unapproved ID token signing algorithm")
    if not key_id:
        raise TokenVerificationError("ID token has no signing key id")

    candidates = [
        key
        for key in list(jwks.get("keys") or [])
        if str(key.get("kid") or "") == key_id
        and (not key.get("alg") or key.get("alg") == algorithm)
    ]
    if len(candidates) != 1:
        raise UnknownSigningKey("No unique matching OIDC signing key")

    try:
        signing_key = jwt.PyJWK.from_dict(candidates[0], algorithm=algorithm).key
        claims = jwt.decode(
            token,
            signing_key,
            algorithms=[algorithm],
            audience=client_id,
            issuer=str(metadata.get("issuer") or ""),
            leeway=30,
            options={"require": ["iss", "sub", "aud", "exp", "iat"]},
        )
    except (jwt.PyJWTError, ValueError) as exc:
        raise TokenVerificationError("ID token signature or claims are invalid") from exc

    audience = claims.get("aud")
    audiences = audience if isinstance(audience, list) else [audience]
    if len(audiences) > 1 and claims.get("azp") != client_id:
        raise TokenVerificationError("ID token authorized party is invalid")
    if not hmac.compare_digest(
        str(claims.get("nonce") or ""),
        str(expected_nonce or ""),
    ):
        raise TokenVerificationError("ID token nonce is invalid")
    return dict(claims)

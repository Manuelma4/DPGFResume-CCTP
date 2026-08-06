from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Iterator
from urllib.parse import urlencode

import httpx
from fastapi import Request

from . import config
from .oidc_validation import (
    TokenVerificationError,
    UnknownSigningKey,
    verify_signed_id_token,
)


class AuthenticationRequired(PermissionError):
    pass


class AuthConfigurationError(RuntimeError):
    pass


class InvalidOAuthResponse(ValueError):
    pass


ROLE_GROUP_PREFIX = "Moduo Role - "
KNOWN_ROLES = {"Admin", "Copil", "Collaborateur", "Inactif"}
SUPERUSER_GROUP = "Moduo Security - Superusers"


@dataclass(frozen=True)
class UserIdentity:
    sub: str
    name: str = ""
    email: str = ""
    username: str = ""
    role: str = ""
    groups: tuple[str, ...] = ()
    is_superuser: bool = False
    is_local: bool = False

    def snapshot(self) -> dict[str, Any]:
        return {
            "sub": self.sub,
            "name": self.name,
            "email": self.email,
            "username": self.username,
            "role": self.role,
            "groups": list(self.groups),
            "is_superuser": self.is_superuser,
            "is_local": self.is_local,
        }


def identity_from_claims(
    claims: dict[str, Any],
    *,
    required_group: str = "",
) -> UserIdentity:
    sub = str(claims.get("sub") or "").strip()
    if not sub:
        raise InvalidOAuthResponse("Subject OIDC absent")

    raw_groups = claims.get("groups") or []
    if isinstance(raw_groups, str):
        raw_groups = [raw_groups]
    if not isinstance(raw_groups, (list, tuple, set)):
        raise InvalidOAuthResponse("Liste de groupes OIDC invalide")
    groups = tuple(
        dict.fromkeys(
            str(group).strip() for group in raw_groups if str(group).strip()
        )
    )

    if required_group and required_group not in groups:
        raise InvalidOAuthResponse(
            "Votre compte n'est pas autorise a acceder a DPGF Resume CCTP"
        )

    roles = [
        group.removeprefix(ROLE_GROUP_PREFIX)
        for group in groups
        if group.startswith(ROLE_GROUP_PREFIX)
    ]
    if len(roles) != 1 or roles[0] not in KNOWN_ROLES:
        raise InvalidOAuthResponse(
            "Le compte doit avoir exactement un role Moduo reconnu"
        )
    if roles[0] == "Inactif":
        raise InvalidOAuthResponse("Le compte Moduo est inactif")

    return UserIdentity(
        sub=sub,
        name=str(claims.get("name") or "").strip(),
        email=str(claims.get("email") or "").strip(),
        username=str(claims.get("preferred_username") or "").strip(),
        role=roles[0],
        groups=groups,
        is_superuser=SUPERUSER_GROUP in groups,
    )


class SessionStore:
    def __init__(self) -> None:
        config.AUTH_DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
        self.secret = (
            config.SESSION_SECRET
            or ("local-development-secret" if config.LOCAL_MODE else "")
        ).encode("utf-8")
        self._initialize()

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(str(config.AUTH_DATABASE_PATH), timeout=15)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self.connection() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS oidc_states (
                    state_hash TEXT PRIMARY KEY,
                    verifier TEXT NOT NULL,
                    nonce TEXT NOT NULL,
                    redirect_after TEXT NOT NULL,
                    expires_at INTEGER NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    session_hash TEXT PRIMARY KEY,
                    user_json TEXT NOT NULL,
                    id_token TEXT NOT NULL,
                    expires_at INTEGER NOT NULL
                )
                """
            )

    def digest(self, value: str) -> str:
        if not self.secret:
            raise AuthConfigurationError("DPGF_SESSION_SECRET est obligatoire")
        return hmac.new(self.secret, value.encode("utf-8"), hashlib.sha256).hexdigest()

    def create_state(self, redirect_after: str) -> tuple[str, str, str]:
        state = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(64)
        nonce = secrets.token_urlsafe(32)
        expires_at = int(time.time()) + 600
        with self.connection() as connection:
            connection.execute("DELETE FROM oidc_states WHERE expires_at <= ?", (int(time.time()),))
            connection.execute(
                "INSERT INTO oidc_states VALUES (?, ?, ?, ?, ?)",
                (self.digest(state), verifier, nonce, safe_redirect(redirect_after), expires_at),
            )
        return state, verifier, nonce

    def consume_state(self, state: str) -> dict[str, str]:
        state_hash = self.digest(state)
        with self.connection() as connection:
            row = connection.execute(
                "SELECT * FROM oidc_states WHERE state_hash = ? AND expires_at > ?",
                (state_hash, int(time.time())),
            ).fetchone()
            connection.execute("DELETE FROM oidc_states WHERE state_hash = ?", (state_hash,))
        if row is None:
            raise InvalidOAuthResponse("État OIDC absent ou expiré")
        return dict(row)

    def create_session(self, user: UserIdentity, id_token: str) -> str:
        token = secrets.token_urlsafe(48)
        expires_at = int(time.time()) + config.SESSION_TTL_SECONDS
        with self.connection() as connection:
            connection.execute("DELETE FROM sessions WHERE expires_at <= ?", (int(time.time()),))
            connection.execute(
                "INSERT INTO sessions VALUES (?, ?, ?, ?)",
                (
                    self.digest(token),
                    json.dumps(user.snapshot(), ensure_ascii=False),
                    id_token,
                    expires_at,
                ),
            )
        return token

    def get_session(self, token: str) -> tuple[UserIdentity, str] | None:
        if not token:
            return None
        with self.connection() as connection:
            row = connection.execute(
                "SELECT * FROM sessions WHERE session_hash = ? AND expires_at > ?",
                (self.digest(token), int(time.time())),
            ).fetchone()
        if row is None:
            return None
        value = json.loads(row["user_json"])
        return (
            UserIdentity(
                sub=value["sub"],
                name=value.get("name", ""),
                email=value.get("email", ""),
                username=value.get("username", ""),
                role=value.get("role", ""),
                groups=tuple(value.get("groups") or []),
                is_superuser=bool(value.get("is_superuser", False)),
                is_local=bool(value.get("is_local", False)),
            ),
            row["id_token"],
        )

    def delete_session(self, token: str) -> str:
        if not token:
            return ""
        session = self.get_session(token)
        with self.connection() as connection:
            connection.execute(
                "DELETE FROM sessions WHERE session_hash = ?", (self.digest(token),)
            )
        return session[1] if session else ""


def safe_redirect(value: str | None) -> str:
    value = str(value or "/").strip()
    return value if value.startswith("/") and not value.startswith("//") else "/"


class AuthService:
    def __init__(self) -> None:
        self.store = SessionStore()
        self._discovery: tuple[float, dict[str, Any]] | None = None
        self._jwks: tuple[float, dict[str, Any]] | None = None

    def configured(self) -> bool:
        return bool(
            config.OIDC_ISSUER_URL
            and config.OIDC_CLIENT_ID
            and config.OIDC_CLIENT_SECRET
            and config.OIDC_REDIRECT_URI
            and config.SESSION_SECRET
        )

    def discovery(self) -> dict[str, Any]:
        if self._discovery and self._discovery[0] > time.time():
            return self._discovery[1]
        if not config.OIDC_ISSUER_URL:
            raise AuthConfigurationError("OIDC_ISSUER_URL est absent")
        response = httpx.get(
            f"{config.OIDC_ISSUER_URL}/.well-known/openid-configuration",
            timeout=config.OIDC_HTTP_TIMEOUT,
        )
        response.raise_for_status()
        payload = response.json()
        if (
            str(payload.get("issuer") or "").rstrip("/")
            != config.OIDC_ISSUER_URL.rstrip("/")
        ):
            raise AuthConfigurationError(
                "L'issuer OIDC publie ne correspond pas a la configuration"
            )
        for field in (
            "authorization_endpoint",
            "token_endpoint",
            "userinfo_endpoint",
            "jwks_uri",
        ):
            endpoint = str(payload.get(field) or "")
            if not endpoint:
                raise AuthConfigurationError(f"Endpoint OIDC absent: {field}")
            if not config.LOCAL_MODE and not endpoint.startswith("https://"):
                raise AuthConfigurationError(f"Endpoint OIDC non securise: {field}")
        self._discovery = (time.time() + 300, payload)
        return payload

    def _jwks_document(self, *, force: bool = False) -> dict[str, Any]:
        if not force and self._jwks and self._jwks[0] > time.time():
            return self._jwks[1]
        endpoint = str(self.discovery().get("jwks_uri") or "")
        response = httpx.get(endpoint, timeout=config.OIDC_HTTP_TIMEOUT)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload.get("keys"), list):
            raise AuthConfigurationError("Document JWKS OIDC invalide")
        self._jwks = (time.time() + 300, payload)
        return payload

    def _verify_id_token(
        self,
        id_token: str,
        *,
        expected_nonce: str,
        discovery: dict[str, Any],
    ) -> dict[str, Any]:
        for force in (False, True):
            try:
                return verify_signed_id_token(
                    id_token,
                    metadata=discovery,
                    jwks=self._jwks_document(force=force),
                    client_id=config.OIDC_CLIENT_ID,
                    expected_nonce=expected_nonce,
                )
            except UnknownSigningKey:
                if force:
                    break
            except TokenVerificationError as exc:
                raise InvalidOAuthResponse(str(exc)) from exc
        raise InvalidOAuthResponse("Cle de signature OIDC inconnue")

    def login_url(self, redirect_after: str) -> tuple[str, str]:
        if not self.configured():
            raise AuthConfigurationError("La configuration OIDC est incomplète")
        state, verifier, nonce = self.store.create_state(redirect_after)
        challenge = base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode("ascii")).digest()
        ).decode("ascii").rstrip("=")
        endpoint = self.discovery().get("authorization_endpoint")
        if not endpoint:
            raise AuthConfigurationError("authorization_endpoint OIDC absent")
        query = urlencode(
            {
                "response_type": "code",
                "client_id": config.OIDC_CLIENT_ID,
                "redirect_uri": config.OIDC_REDIRECT_URI,
                "scope": config.OIDC_SCOPES,
                "state": state,
                "nonce": nonce,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            }
        )
        return f"{endpoint}?{query}", state

    def callback(self, code: str, state: str, state_cookie: str) -> tuple[str, str]:
        if not state_cookie or not hmac.compare_digest(state, state_cookie):
            raise InvalidOAuthResponse("Le cookie d'état OIDC ne correspond pas")
        stored = self.store.consume_state(state)
        discovery = self.discovery()
        endpoint = discovery.get("token_endpoint")
        if not endpoint:
            raise AuthConfigurationError("token_endpoint OIDC absent")
        response = httpx.post(
            endpoint,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": config.OIDC_REDIRECT_URI,
                "client_id": config.OIDC_CLIENT_ID,
                "client_secret": config.OIDC_CLIENT_SECRET,
                "code_verifier": stored["verifier"],
            },
            timeout=config.OIDC_HTTP_TIMEOUT,
        )
        response.raise_for_status()
        tokens = response.json()
        id_token = str(tokens.get("id_token") or "")
        if not id_token:
            raise InvalidOAuthResponse("ID token OIDC absent")
        claims = self._verify_id_token(
            id_token,
            expected_nonce=stored["nonce"],
            discovery=discovery,
        )
        audience = claims.get("aud")
        audience_values = audience if isinstance(audience, list) else [audience]
        if claims.get("iss", "").rstrip("/") != config.OIDC_ISSUER_URL:
            raise InvalidOAuthResponse("Émetteur OIDC inattendu")
        if config.OIDC_CLIENT_ID not in audience_values:
            raise InvalidOAuthResponse("Audience OIDC inattendue")
        if claims.get("nonce") != stored["nonce"]:
            raise InvalidOAuthResponse("Nonce OIDC invalide")
        if int(claims.get("exp") or 0) <= int(time.time()):
            raise InvalidOAuthResponse("ID token expiré")
        user = identity_from_claims(
            claims,
            required_group=config.OIDC_REQUIRED_GROUP,
        )
        session_token = self.store.create_session(user, id_token)
        return session_token, safe_redirect(stored["redirect_after"])

    def current_user(self, request: Request) -> UserIdentity:
        token = request.cookies.get(config.SESSION_COOKIE_NAME, "")
        session = self.store.get_session(token)
        if session:
            return session[0]
        if config.LOCAL_MODE and not config.AUTH_REQUIRED:
            return UserIdentity(
                sub=config.LOCAL_USER_SUB,
                name=config.LOCAL_USER_NAME,
                email=config.LOCAL_USER_EMAIL,
                username="local",
                role="Local",
                is_local=True,
            )
        if config.AUTH_REQUIRED and not self.configured():
            raise AuthConfigurationError("L'authentification est exigée mais OIDC est incomplet")
        raise AuthenticationRequired("Connexion requise")

    def logout_url(self, request: Request) -> str:
        token = request.cookies.get(config.SESSION_COOKIE_NAME, "")
        id_token = self.store.delete_session(token)
        redirect_uri = config.OIDC_POST_LOGOUT_REDIRECT_URI or "/"
        if config.LOCAL_MODE and not config.AUTH_REQUIRED:
            return redirect_uri
        endpoint = str(self.discovery().get("end_session_endpoint") or "")
        if not endpoint:
            return redirect_uri
        params = {
            "client_id": config.OIDC_CLIENT_ID,
            "post_logout_redirect_uri": redirect_uri,
        }
        if id_token:
            params["id_token_hint"] = id_token
        return f"{endpoint}?{urlencode(params)}"


service = AuthService()

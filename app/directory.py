from __future__ import annotations

from typing import Any

import httpx

from . import config


class DirectoryError(RuntimeError):
    pass


def available() -> bool:
    return bool(config.AUTHENTIK_API_URL and config.AUTHENTIK_API_TOKEN)


def _normalize_member(entry: dict[str, Any]) -> dict[str, str] | None:
    email = str(entry.get("email") or "").strip().lower()
    if not email:
        return None
    return {
        "email": email,
        "name": str(entry.get("name") or "").strip(),
        "username": str(entry.get("username") or "").strip(),
    }


def list_group_members() -> list[dict[str, str]]:
    """List every user in config.OIDC_REQUIRED_GROUP via authentik's admin API.

    Used to populate the "partager avec" picker — real people, not just those
    who already logged into DPGF at least once.
    """
    if not available():
        raise DirectoryError(
            "AUTHENTIK_API_URL / AUTHENTIK_API_TOKEN ne sont pas configurés"
        )
    headers = {"Authorization": f"Bearer {config.AUTHENTIK_API_TOKEN}"}
    url = f"{config.AUTHENTIK_API_URL}/api/v3/core/users/"
    params: dict[str, Any] | None = {
        "groups_by_name": config.OIDC_REQUIRED_GROUP,
        "page_size": 200,
    }
    members: list[dict[str, str]] = []
    seen_urls: set[str] = set()
    for _ in range(20):  # hard cap: never loop forever on a malformed "next"
        try:
            response = httpx.get(
                url, params=params, headers=headers, timeout=config.OIDC_HTTP_TIMEOUT
            )
        except httpx.HTTPError as exc:
            raise DirectoryError(f"Annuaire authentik injoignable : {exc}") from exc
        if response.status_code != 200:
            raise DirectoryError(
                f"Annuaire authentik : réponse {response.status_code}"
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise DirectoryError("Réponse authentik non JSON") from exc
        results = payload.get("results")
        if not isinstance(results, list):
            raise DirectoryError("Structure de réponse authentik inattendue")
        for entry in results:
            if not isinstance(entry, dict):
                continue
            member = _normalize_member(entry)
            if member:
                members.append(member)
        next_url = payload.get("next")
        if not next_url or next_url in seen_urls:
            break
        seen_urls.add(next_url)
        url = next_url
        params = None
    return members

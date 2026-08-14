from __future__ import annotations

import json
import re
from typing import Any

import httpx

from . import config


SYSTEM_PROMPT = """Tu es économiste de la construction en France.
Tu transformes un CCTP en lignes de DPGF sans inventer de prestation.
Chaque ligne doit être traçable vers un titre ou un passage de la source.
Conserve la hiérarchie et les codes du CCTP. Une quantité absente reste null.
Choisis uniquement une unité parmi m², m³, ml, U, Ens, kg, h, PM
(PM = pour mémoire, poste informatif non chiffré).
Retourne exclusivement un objet JSON avec les clés lot_code, lot_title et lines.
Chaque élément de lines contient kind (section ou item), level, code, designation,
unit, quantity, source_page, source_excerpt, confidence et review_reason."""


PERIMETER_SYSTEM_PROMPT = """Tu es économiste de la construction en France.
On te donne la liste des titres numérotés d'un CCTP (pas le texte complet).
Identifie lequel de ces titres ouvre le chapitre descriptif des ouvrages à
chiffrer (les prestations techniques à réaliser), par opposition aux chapitres
administratifs (généralités, documents de référence, normes, prescriptions
communes...). Réponds uniquement par un objet JSON {"anchor_code": "..."} avec
le code exact d'un des titres fournis, ou {"anchor_code": null} si aucun ne
convient. N'invente jamais un code absent de la liste."""

UNIT_SYSTEM_PROMPT = """Tu es économiste de la construction en France.
On te donne une liste de postes de CCTP (code + désignation, sans contexte).
Pour chacun, donne l'unité de métré la plus probable parmi exactement :
m², m³, ml, U, Ens, kg, PM (PM = pour mémoire, poste informatif non
chiffré). Réponds uniquement par un objet JSON
{"units": {"<code>": "<unite>", ...}} avec une entrée par code fourni. N'utilise
jamais une unité hors de cette liste et n'ajoute pas de code absent de la
liste fournie."""

ALLOWED_UNITS = {"m²", "m³", "ml", "U", "Ens", "kg", "PM"}


def available() -> bool:
    return bool(
        config.USE_LLM
        and config.LIHA_URL
        and config.LIHA_MODEL
        and config.LIHA_TOKEN
    )


def _json_object(content: str) -> dict[str, Any]:
    content = content.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", content, re.DOTALL | re.IGNORECASE)
    if fenced:
        content = fenced.group(1)
    start = content.find("{")
    end = content.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("La réponse LIHA ne contient pas d'objet JSON")
    payload = json.loads(content[start : end + 1])
    if not isinstance(payload, dict):
        raise ValueError("Structure JSON LIHA invalide")
    return payload


def _json_payload(content: str) -> dict[str, Any]:
    payload = _json_object(content)
    if not isinstance(payload.get("lines"), list):
        raise ValueError("Structure JSON LIHA invalide")
    return payload


def _chat_completion(
    system_prompt: str, user_prompt: str, *, timeout: float, max_tokens: int
) -> str:
    response = httpx.post(
        config.LIHA_URL,
        headers={
            "Authorization": f"Bearer {config.LIHA_TOKEN}",
            "Content-Type": "application/json",
        },
        json={
            "model": config.LIHA_MODEL,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0,
            "max_tokens": max_tokens,
        },
        timeout=timeout,
    )
    response.raise_for_status()
    message = response.json()["choices"][0]["message"]
    content = message.get("content")
    if not content:
        # Reasoning models (ex. gpt-oss) can spend the whole max_tokens budget
        # on the "reasoning" field and stop (finish_reason "length") before
        # ever writing the final answer to "content". Surface that clearly
        # instead of a bare KeyError so it is obvious more budget is needed.
        raise ValueError(
            "Réponse LIHA sans contenu final (probablement tronquée par "
            "max_tokens pendant le raisonnement du modèle)."
        )
    return str(content)


def refine(text: str, deterministic_lot: dict[str, Any]) -> dict[str, Any]:
    if not available():
        return deterministic_lot
    deterministic_outline = [
        {
            "kind": line.get("kind"),
            "level": line.get("level"),
            "code": line.get("code"),
            "designation": line.get("designation"),
            "unit": line.get("unit"),
            "source_page": line.get("source_page"),
        }
        for line in deterministic_lot.get("lines", [])
    ]
    user_prompt = (
        "Vérifie et améliore cette extraction déterministe. N'ajoute pas de poste qui "
        "n'est pas soutenu par la source.\n\nEXTRACTION:\n"
        + json.dumps(
            {
                "lot_code": deterministic_lot.get("code"),
                "lot_title": deterministic_lot.get("title"),
                "lines": deterministic_outline,
            },
            ensure_ascii=False,
        )
        + "\n\nSOURCE CCTP:\n"
        + text[: config.LLM_MAX_CHARS]
    )
    content = _chat_completion(
        SYSTEM_PROMPT, user_prompt, timeout=config.LLM_TIMEOUT, max_tokens=12000
    )
    return _json_payload(content)


def suggest_perimeter_anchor(
    candidates: list[dict[str, Any]], lot_title: str
) -> str | None:
    """Ask LIHA which candidate heading opens the priceable-works chapter,
    for use when the deterministic heuristics fell back to the weakest
    perimeter methods (dominant_numbered_chapter / not_found). Returns a code
    from `candidates` or None — never invents one."""
    if not available() or not candidates:
        return None
    valid_codes = {str(c.get("code") or "") for c in candidates}
    user_prompt = (
        f"Lot : {lot_title}\n\nTITRES NUMÉROTÉS (dans l'ordre du document) :\n"
        + json.dumps(candidates, ensure_ascii=False)
    )
    try:
        content = _chat_completion(
            PERIMETER_SYSTEM_PROMPT,
            user_prompt,
            timeout=config.LLM_SUGGEST_TIMEOUT,
            # LIHA's model reasons before answering (separate "reasoning"
            # field) — a short budget can be spent entirely on that and
            # leave "content" empty (finish_reason "length"). Generous
            # headroom here even though the final JSON itself is tiny.
            max_tokens=2000,
        )
        payload = _json_object(content)
    except Exception:
        return None
    anchor_code = payload.get("anchor_code")
    if not anchor_code or str(anchor_code) not in valid_codes:
        return None
    return str(anchor_code)


def suggest_units(items: list[dict[str, Any]]) -> dict[str, str]:
    """Ask LIHA for a unit only for the items the deterministic UNIT_RULES
    could not classify (unit_source == "default"). Returns a {code: unit}
    map restricted to ALLOWED_UNITS and to codes actually asked about —
    anything else in the response is dropped, never applied."""
    if not available() or not items:
        return {}
    valid_codes = {str(item.get("code") or "") for item in items}
    user_prompt = "POSTES :\n" + json.dumps(items, ensure_ascii=False)
    try:
        content = _chat_completion(
            UNIT_SYSTEM_PROMPT,
            user_prompt,
            timeout=config.LLM_SUGGEST_TIMEOUT,
            # Same reasoning-budget concern as suggest_perimeter_anchor,
            # scaled up further since this call can carry many items.
            max_tokens=4000,
        )
        payload = _json_object(content)
    except Exception:
        return {}
    raw_units = payload.get("units")
    if not isinstance(raw_units, dict):
        return {}
    return {
        code: unit
        for code, unit in raw_units.items()
        if code in valid_codes and unit in ALLOWED_UNITS
    }


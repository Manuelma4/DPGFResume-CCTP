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
Choisis uniquement une unité parmi m², m³, ml, U, Ens, kg, h.
Retourne exclusivement un objet JSON avec les clés lot_code, lot_title et lines.
Chaque élément de lines contient kind (section ou item), level, code, designation,
unit, quantity, source_page, source_excerpt, confidence et review_reason."""


def available() -> bool:
    return bool(
        config.USE_LLM
        and config.LIHA_URL
        and config.LIHA_MODEL
        and config.LIHA_TOKEN
    )


def _json_payload(content: str) -> dict[str, Any]:
    content = content.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", content, re.DOTALL | re.IGNORECASE)
    if fenced:
        content = fenced.group(1)
    start = content.find("{")
    end = content.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("La réponse LIHA ne contient pas d'objet JSON")
    payload = json.loads(content[start : end + 1])
    if not isinstance(payload, dict) or not isinstance(payload.get("lines"), list):
        raise ValueError("Structure JSON LIHA invalide")
    return payload


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
    response = httpx.post(
        config.LIHA_URL,
        headers={
            "Authorization": f"Bearer {config.LIHA_TOKEN}",
            "Content-Type": "application/json",
        },
        json={
            "model": config.LIHA_MODEL,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0,
            "max_tokens": 12000,
        },
        timeout=config.LLM_TIMEOUT,
    )
    response.raise_for_status()
    payload = _json_payload(response.json()["choices"][0]["message"]["content"])
    return payload


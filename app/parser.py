from __future__ import annotations

import hashlib
import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any

from .extractors import ExtractedDocument, TextBlock


NUMBERED_HEADING = re.compile(
    r"^\s*(?:ARTICLE\s+)?"
    r"(?P<code>[A-Z]?\d{1,3}(?:[.\-]\d{1,3}){0,6})[.)]?"
    r"(?:\s*[-–—:]\s*|\s+)(?P<title>\S.+?)\s*$",
    re.IGNORECASE,
)
LOT_PATTERN = re.compile(
    r"\bLOT\s*(?:N[°O]\s*)?(?P<code>[A-Z]?\d{1,3}(?:[.\-]\d{1,3})?)"
    r"\s*[-–—:]\s*(?P<title>[^\n|]{3,100})",
    re.IGNORECASE,
)
TOC_PATTERN = re.compile(r"\.{3,}\s*\d+\s*$")
WORK_ANCHOR = re.compile(
    r"\b(?:description|descriptif)\s+(?:d[eu']\s+|des\s+)?"
    r"(?:ouvrages?|travaux|prestations?)\b",
    re.IGNORECASE,
)
OPTION_PATTERN = re.compile(
    r"\b(?:option|variante|pse|prestation\s+supplémentaire)\b", re.IGNORECASE
)
EXPLICIT_QUANTITY = re.compile(
    r"\b(?:quantité|qté|nombre)\s*(?:totale?)?\s*(?:[:=]\s*)?"
    r"(?P<value>\d+(?:[.,]\d+)?)\b",
    re.IGNORECASE,
)
EXPLICIT_UNIT = re.compile(
    r"\b(?:unité(?:\s+de\s+(?:règlement|mesure|prix))?|u\.o\.)\s*[:=]\s*"
    r"(?P<unit>m[²2]|m[³3]|ml|u(?:nité)?|ens(?:emble)?|forfait|kg|h(?:eure)?|mois)\b",
    re.IGNORECASE,
)

UNIT_RULES: list[tuple[str, float, re.Pattern[str]]] = [
    (
        "U",
        0.87,
        re.compile(
            r"\b(?:unites?|porte|fenetre|chassis|trappe|regard|caniveau|appareil|"
            r"equipement|robinet|lavabo|vasque|wc|urinoir|evier|radiateur|ventilo|"
            r"extracteur|escalier|echelle|garde-corps|store|luminaire|"
            r"tableau|coffret|prise|interrupteur|detecteur|extincteur|"
            r"pompe|siphon|douche|miroir|seche[\s-]mains?|"
            r"barre d'appui|borne)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "ml",
        0.88,
        re.compile(
            r"\bisolation\b.*\b(?:canalisations?|evacuations?|tuyaux?)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "ml",
        0.86,
        re.compile(
            r"\b(?:canalisations?|tuyauteries?|cablage|cables?|fourreaux?|plinthes?|"
            r"bordure|gouttiere|descente|profile|main[\s-]courante|reseau)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "m²",
        0.88,
        re.compile(
            r"\b(?:peinture|enduit|revetement|carrelage|faience|sol souple|"
            r"etancheite|isolation|doublage|cloison|plafond|bardage|toiture|"
            r"membrane|chape|ragreage|moquette)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "m³",
        0.82,
        re.compile(
            r"\b(?:terrassement|deblai|remblai|beton|grave|terre vegetale|"
            r"fouille|evacuation des terres)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "kg",
        0.78,
        re.compile(r"\b(?:acier|armature|charpente metallique)\b", re.IGNORECASE),
    ),
    (
        "Ens",
        0.82,
        re.compile(
            r"\b(?:installation|preparation|etudes?|plans?|dossier|essais?|"
            r"controle|mise en service|nettoyage|depose|demolition|"
            r"signalisation|implantation|repliement|protection|reservations?)\b",
            re.IGNORECASE,
        ),
    ),
]

GENERIC_SECTION_TITLES = {
    "description",
    "prescription",
    "prescriptions",
    "generalites",
    "objet",
    "consistance des travaux",
    "description des ouvrages",
    "description des travaux",
    "descriptif des ouvrages",
}
SPECIFICATION_ONLY = re.compile(
    r"^(?:nature des prestations|localisation|aperçu(?: de l['’]ouvrage)?|"
    r"mode d['’]exécution|mise en œuvre|caractéristiques(?: techniques)?|"
    r"performances?|documents? de référence|normes? et réglementations?)$",
    re.IGNORECASE,
)


def _normalized(value: str) -> str:
    value = unicodedata.normalize("NFKD", value)
    value = "".join(
        character for character in value if not unicodedata.combining(character)
    )
    return re.sub(r"\s+", " ", value).strip().casefold()


def _clean_title(value: str) -> str:
    value = re.sub(r"\.{3,}\s*\d+\s*$", "", value)
    value = re.sub(r"\s+", " ", value).strip(" \t-–—:.;")
    return value[:240]


def _normalize_code(value: str) -> str:
    return re.sub(r"[.\-]+$", "", value.replace("-", ".").strip())


def _code_parts(code: str) -> tuple[str, ...]:
    return tuple(part for part in re.split(r"[.\-]", code) if part)


def _code_level(code: str) -> int:
    return len(_code_parts(code))


def _natural_code_key(code: str) -> tuple:
    parts: list[tuple[int, int | str]] = []
    for part in _code_parts(code):
        if part.isdigit():
            parts.append((0, int(part)))
        else:
            parts.append((1, part.casefold()))
    return tuple(parts)


def lot_sort_key(lot: dict[str, Any]) -> tuple:
    code = str(lot.get("code") or "")
    return (_natural_code_key(code), str(lot.get("title") or "").casefold())


def _heading_level(block: TextBlock, code: str) -> int:
    if code:
        return _code_level(code)
    style_match = re.search(r"(?:heading|titre)\s*(\d+)", block.style, re.IGNORECASE)
    if style_match:
        return max(1, min(8, int(style_match.group(1))))
    return 2


def _canonical_unit(value: str) -> str:
    normalized = _normalized(value)
    if normalized in {"m2", "metre carre", "metres carres"}:
        return "m²"
    if normalized in {"m3", "metre cube", "metres cubes"}:
        return "m³"
    if normalized in {"ml", "metre lineaire", "metres lineaires"}:
        return "ml"
    if normalized in {"u", "unite", "unites"}:
        return "U"
    if normalized in {"kg", "kilogramme", "kilogrammes"}:
        return "kg"
    if normalized in {"h", "heure", "heures"}:
        return "h"
    if normalized == "mois":
        return "mois"
    return "Ens"


def _infer_unit(title: str, context: str) -> tuple[str, str, float]:
    explicit = EXPLICIT_UNIT.search(f"{title}. {context[:500]}")
    if explicit:
        return _canonical_unit(explicit.group("unit")), "explicit", 0.99
    normalized_title = _normalized(title)
    for unit, confidence, pattern in UNIT_RULES:
        if pattern.search(normalized_title):
            return unit, "rule", confidence
    return "Ens", "default", 0.62


def _infer_quantity(title: str, context: str) -> tuple[float | None, str]:
    # Dimensions such as "15 mm" and technical counts are deliberately ignored.
    match = EXPLICIT_QUANTITY.search(f"{title}. {context[:500]}")
    if not match:
        return None, "missing"
    try:
        return float(match.group("value").replace(",", ".")), "explicit"
    except ValueError:
        return None, "missing"


def _stable_id(source_id: str, code: str, title: str, index: int) -> str:
    digest = hashlib.sha1(
        f"{source_id}|{code}|{title}|{index}".encode("utf-8")
    ).hexdigest()
    return f"ln_{digest[:14]}"


def _lot_identity(document: ExtractedDocument) -> tuple[str, str]:
    beginning = "\n".join(block.text for block in document.blocks[:160])
    match = LOT_PATTERN.search(beginning)
    if match:
        code = match.group("code").upper()
        if code.isdigit() and len(code) < 2:
            code = code.zfill(2)
        title = re.sub(
            r"^\s*CCTP\s+", "", _clean_title(match.group("title")), flags=re.IGNORECASE
        )
        return code, title
    stem = re.sub(r"(?i)\bCCTP\b", "", document.path.stem)
    stem = re.sub(r"[_\-]+", " ", stem)
    stem = re.sub(r"\s+", " ", stem).strip()
    code_match = re.search(
        r"\b(?:LOT\s*)?([A-Z]?\d{1,3}(?:[.\-]\d+)?)\b", stem, re.IGNORECASE
    )
    code = code_match.group(1).upper() if code_match else ""
    if code.isdigit() and len(code) < 2:
        code = code.zfill(2)
    title = stem or document.path.stem
    return code, title[:100]


@dataclass
class HeadingCandidate:
    block_index: int
    code: str
    title: str
    level: int
    page: int | None
    source_kind: str
    font_size: float
    bold: bool


def _toc_pages(blocks: list[TextBlock]) -> set[int]:
    by_page: dict[int, list[str]] = defaultdict(list)
    for block in blocks:
        if block.page is not None:
            by_page[block.page].append(block.text)
    pages: set[int] = set()
    for page, texts in by_page.items():
        normalized = [_normalized(text) for text in texts]
        dotted = sum(1 for text in texts if TOC_PATTERN.search(text))
        if any(text in {"sommaire", "table des matieres"} for text in normalized):
            pages.add(page)
        elif dotted >= 4:
            pages.add(page)
    return pages


def _looks_like_heading(block: TextBlock, code: str) -> bool:
    if block.source_kind in {"paragraph", "table_row"}:
        return bool(
            re.search(r"(?:heading|titre)", block.style, re.IGNORECASE)
            or block.bold
            or code
        )
    return bool(
        block.bold
        or block.style.casefold().startswith("heading")
        or block.source_kind == "pdf_merged_heading"
        or _code_level(code) >= 2
    )


def _all_numbered_candidates(
    blocks: list[TextBlock], ignored_pages: set[int]
) -> list[HeadingCandidate]:
    candidates: list[HeadingCandidate] = []
    for index, block in enumerate(blocks):
        text = block.text.strip()
        if (
            not text
            or len(text) > 260
            or block.page in ignored_pages
            or TOC_PATTERN.search(text)
            or LOT_PATTERN.search(text)
        ):
            continue
        match = NUMBERED_HEADING.match(text)
        if not match:
            continue
        code = _normalize_code(match.group("code"))
        title = _clean_title(match.group("title"))
        if not code or len(title) < 2 or not _looks_like_heading(block, code):
            continue
        candidates.append(
            HeadingCandidate(
                block_index=index,
                code=code,
                title=title,
                level=_heading_level(block, code),
                page=block.page,
                source_kind=block.source_kind,
                font_size=block.font_size,
                bold=block.bold,
            )
        )
    return candidates


def _select_work_perimeter(
    candidates: list[HeadingCandidate],
) -> tuple[list[HeadingCandidate], dict[str, Any]]:
    anchors = [
        candidate for candidate in candidates if WORK_ANCHOR.search(candidate.title)
    ]
    if anchors:
        anchor = anchors[0]
        anchor_prefixes = {
            _code_parts(candidate.code)[0]
            for candidate in anchors
            if _code_parts(candidate.code)
        }
        selected = [
            candidate
            for candidate in candidates
            if candidate.block_index >= anchor.block_index
            and _code_parts(candidate.code)
            and _code_parts(candidate.code)[0] in anchor_prefixes
        ]
        return selected, {
            "method": "explicit_anchor",
            "anchor_code": anchor.code,
            "anchor_title": anchor.title,
            "start_page": anchor.page,
            "confidence": 0.98,
        }

    prefix_counts: Counter[str] = Counter()
    first_by_prefix: dict[str, HeadingCandidate] = {}
    for candidate in candidates:
        parts = _code_parts(candidate.code)
        if len(parts) < 2:
            continue
        prefix_counts[parts[0]] += 1
        first_by_prefix.setdefault(parts[0], candidate)
    if prefix_counts:
        prefix, count = max(
            prefix_counts.items(),
            key=lambda item: (
                item[1],
                _natural_code_key(item[0]),
            ),
        )
        selected = [
            candidate
            for candidate in candidates
            if _code_parts(candidate.code)
            and _code_parts(candidate.code)[0] == prefix
        ]
        first = first_by_prefix[prefix]
        confidence = 0.92 if count >= 6 else 0.78
        return selected, {
            "method": "dominant_numbered_chapter",
            "anchor_code": prefix,
            "anchor_title": first.title,
            "start_page": first.page,
            "confidence": confidence,
        }

    return [], {
        "method": "not_found",
        "anchor_code": "",
        "anchor_title": "",
        "start_page": None,
        "confidence": 0.0,
    }


def _deduplicate_candidates(
    candidates: list[HeadingCandidate],
) -> tuple[list[HeadingCandidate], set[str]]:
    result: list[HeadingCandidate] = []
    exact_seen: set[tuple[str, str]] = set()
    titles_by_code: dict[str, set[str]] = defaultdict(set)
    conflicting_codes: set[str] = set()
    for candidate in candidates:
        if (
            candidate.level == 1
            and candidate.code in titles_by_code
            and not WORK_ANCHOR.search(candidate.title)
        ):
            # Prevent sentences such as "3 voies revient en position..." from
            # becoming a second chapter 3 inside a technical description.
            continue
        key = (candidate.code.casefold(), _normalized(candidate.title))
        if key in exact_seen:
            continue
        exact_seen.add(key)
        title_key = _normalized(candidate.title)
        titles_by_code[candidate.code].add(title_key)
        if len(titles_by_code[candidate.code]) > 1:
            conflicting_codes.add(candidate.code)
        result.append(candidate)
    return result, conflicting_codes


def parse_document(document: ExtractedDocument, source_id: str) -> dict[str, Any]:
    lot_code, lot_title = _lot_identity(document)
    ignored_pages = _toc_pages(document.blocks)
    all_candidates = _all_numbered_candidates(document.blocks, ignored_pages)
    candidates, perimeter = _select_work_perimeter(all_candidates)
    candidates, conflicting_codes = _deduplicate_candidates(candidates)
    lines: list[dict[str, Any]] = []
    warnings = list(document.warnings)

    if not candidates:
        warnings.append(
            "Le chapitre des ouvrages n'a pas été identifié avec assez de certitude. "
            "Aucune ligne hasardeuse n'a été créée."
        )
    elif perimeter["method"] == "dominant_numbered_chapter":
        warnings.append(
            f"Périmètre déduit du chapitre dominant {perimeter['anchor_code']} "
            "(aucun titre « Description des ouvrages » explicite)."
        )

    anchor_code = str(perimeter.get("anchor_code") or "")
    for index, candidate in enumerate(candidates):
        next_index = (
            candidates[index + 1].block_index
            if index + 1 < len(candidates)
            else len(document.blocks)
        )
        context_blocks = document.blocks[
            candidate.block_index + 1 : min(next_index, candidate.block_index + 14)
        ]
        context = re.sub(
            r"\s+",
            " ",
            " ".join(block.text for block in context_blocks),
        ).strip()
        has_child = (
            index + 1 < len(candidates)
            and candidates[index + 1].level > candidate.level
            and _code_parts(candidates[index + 1].code)[: candidate.level]
            == _code_parts(candidate.code)
        )
        is_anchor = (
            perimeter["method"] == "explicit_anchor"
            and candidate.code == anchor_code
            and WORK_ANCHOR.search(candidate.title)
        )
        # The hierarchy is structural: a numbered heading is a section only
        # when it really owns smaller numerals. A leaf such as 4.6 is always a
        # priceable row with a unit, even if its label is "Généralités".
        is_section = is_anchor or has_child or candidate.level <= 1
        kind = "section" if is_section else "item"
        unit, unit_source, unit_confidence = _infer_unit(candidate.title, context)
        quantity, quantity_source = _infer_quantity(candidate.title, context)

        classification_confidence = float(perimeter["confidence"])
        if candidate.source_kind == "pdf_merged_heading":
            classification_confidence = min(0.99, classification_confidence + 0.01)
        if not candidate.bold and not candidate.source_kind == "paragraph":
            classification_confidence -= 0.03
        if is_section:
            confidence = max(0.9, classification_confidence)
        else:
            confidence = (
                classification_confidence * 0.78 + unit_confidence * 0.22
            )

        review_reasons: list[str] = []
        if kind == "item" and candidate.code in conflicting_codes:
            review_reasons.append(f"Code {candidate.code} présent avec plusieurs intitulés")
            confidence -= 0.12
        if kind == "item" and float(perimeter["confidence"]) < 0.85:
            review_reasons.append("Périmètre des ouvrages à confirmer")
            confidence -= 0.08
        confidence = round(max(0.35, min(0.99, confidence)), 2)
        included = not bool(OPTION_PATTERN.search(candidate.title))
        lines.append(
            {
                "id": _stable_id(source_id, candidate.code, candidate.title, index),
                "kind": kind,
                "level": candidate.level,
                "code": candidate.code,
                "designation": candidate.title,
                "description": context[:1200],
                "unit": None if kind == "section" else unit,
                "unit_source": None if kind == "section" else unit_source,
                "unit_confidence": None if kind == "section" else unit_confidence,
                "quantity": None if kind == "section" else quantity,
                "quantity_source": None if kind == "section" else quantity_source,
                "unit_price": None,
                "included": included,
                "confidence": confidence,
                "review_status": "to_review" if review_reasons else "validated",
                "review_reason": " · ".join(review_reasons),
                "review_fields": (
                    ["code"] if candidate.code in conflicting_codes else []
                ),
                "source_id": source_id,
                "source_page": candidate.page,
                "source_excerpt": f"{candidate.code} {candidate.title}. {context[:500]}".strip(),
                "origin": "deterministic-v2",
            }
        )

    item_count = sum(1 for line in lines if line["kind"] == "item")
    if item_count == 0:
        warnings.append("Aucun poste chiffrable n'a été identifié automatiquement.")
    return {
        "id": f"lot_{hashlib.sha1(source_id.encode('utf-8')).hexdigest()[:12]}",
        "code": lot_code,
        "title": lot_title,
        "source_id": source_id,
        "lines": lines,
        "warnings": warnings,
        "perimeter": {
            **perimeter,
            "ignored_toc_pages": sorted(ignored_pages),
            "candidate_count_before_perimeter": len(all_candidates),
            "selected_heading_count": len(candidates),
        },
    }


def recompute_stats(lots: list[dict[str, Any]]) -> dict[str, Any]:
    all_lines = [line for lot in lots for line in lot.get("lines", [])]
    items = [line for line in all_lines if line.get("kind") == "item"]
    review = [line for line in items if line.get("review_status") != "validated"]
    trusted_units = [
        line
        for line in items
        if line.get("unit_source") in {"explicit", "rule"}
        or line.get("origin") == "manual"
    ]
    explicit_quantities = [
        line
        for line in items
        if line.get("quantity_source") == "explicit"
        or (line.get("origin") == "manual" and line.get("quantity") is not None)
    ]
    classification = (
        sum(float(line.get("confidence") or 0) for line in items) / len(items)
        if items
        else 0.0
    )
    accepted_ratio = (len(items) - len(review)) / len(items) if items else 0.0
    unit_quality = (
        sum(float(line.get("unit_confidence") or 1.0) for line in items) / len(items)
        if items
        else 0.0
    )
    extraction_index = (
        classification * 0.58 + accepted_ratio * 0.27 + unit_quality * 0.15
        if items
        else 0.0
    )
    return {
        "lots": len(lots),
        "sections": sum(1 for line in all_lines if line.get("kind") == "section"),
        "items": len(items),
        "to_review": len(review),
        "validated": len(items) - len(review),
        "unit_coverage": round(len(trusted_units) / len(items) * 100) if items else 0,
        "quantity_coverage": (
            round(len(explicit_quantities) / len(items) * 100) if items else 0
        ),
        "classification_confidence": round(classification, 2),
        "accepted_ratio": round(accepted_ratio, 2),
        "average_confidence": round(min(0.99, extraction_index), 2),
    }

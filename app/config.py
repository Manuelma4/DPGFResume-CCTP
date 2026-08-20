from __future__ import annotations

import os
from pathlib import Path

from dotenv import dotenv_values


BASE_DIR = Path(__file__).resolve().parent.parent
APP_DIR = BASE_DIR / "app"
ASSETS_DIR = APP_DIR / "assets"
FRONTEND_DIST = BASE_DIR / "frontend" / "dist"
OUTPUT_DIR = BASE_DIR / "output"
ANALYSES_DIR = OUTPUT_DIR / "analyses"
DATABASE_PATH = OUTPUT_DIR / "dpgf_resume.sqlite3"
AUTH_DATABASE_PATH = OUTPUT_DIR / "auth.sqlite3"
TEMPLATE_PATH = ASSETS_DIR / "DPGF TYPE.xlsx"


def _load_environment() -> dict[str, str]:
    values: dict[str, str] = {}
    for candidate in (BASE_DIR.parent / "etc" / ".env", BASE_DIR / ".env"):
        if candidate.exists():
            values.update(
                {key: str(value) for key, value in dotenv_values(candidate).items() if value is not None}
            )
    values.update({key: str(value) for key, value in os.environ.items()})
    return values


ENV = _load_environment()


def env_bool(name: str, default: bool = False) -> bool:
    raw = str(ENV.get(name, "") or "").strip().lower()
    return default if not raw else raw in {"1", "true", "yes", "on"}


ENVIRONMENT = str(ENV.get("DPGF_ENVIRONMENT", "local") or "local").strip().lower()
LOCAL_MODE = ENVIRONMENT in {"local", "dev", "development", "test"}
AUTH_REQUIRED = env_bool("DPGF_AUTH_REQUIRED", not LOCAL_MODE)
PUBLIC_URL = str(ENV.get("DPGF_PUBLIC_URL", "") or "").strip().rstrip("/")
SESSION_SECRET = str(ENV.get("DPGF_SESSION_SECRET", "") or "").strip()
SESSION_COOKIE_NAME = "dpgf_resume_session"
OIDC_STATE_COOKIE_NAME = "dpgf_resume_oidc_state"
SESSION_COOKIE_SECURE = env_bool("DPGF_SESSION_COOKIE_SECURE", not LOCAL_MODE)
SESSION_TTL_SECONDS = int(ENV.get("DPGF_SESSION_TTL_SECONDS", "28800") or 28800)

OIDC_ISSUER_URL = str(
    ENV.get("OIDC_ISSUER_URL") or ENV.get("OIDC_ISSUER") or ""
).strip().rstrip("/")
OIDC_CLIENT_ID = str(ENV.get("OIDC_CLIENT_ID", "dpgf-resume-cctp") or "").strip()
OIDC_CLIENT_SECRET = str(ENV.get("OIDC_CLIENT_SECRET", "") or "").strip()
OIDC_REDIRECT_URI = str(
    ENV.get("OIDC_REDIRECT_URI")
    or (f"{PUBLIC_URL}/api/auth/callback" if PUBLIC_URL else "")
).strip()
OIDC_POST_LOGOUT_REDIRECT_URI = str(
    ENV.get("OIDC_POST_LOGOUT_REDIRECT_URI")
    or (f"{PUBLIC_URL}/" if PUBLIC_URL else "/")
).strip()
OIDC_SCOPES = str(ENV.get("OIDC_SCOPES", "openid profile email") or "").strip()
OIDC_REQUIRED_GROUP = str(
    ENV.get("OIDC_REQUIRED_GROUP", "Moduo Access - DPGF Resume CCTP")
    or ""
).strip()
OIDC_HTTP_TIMEOUT = float(ENV.get("OIDC_HTTP_TIMEOUT", "12") or 12)

# API d'administration authentik (racine du panneau, distincte de
# OIDC_ISSUER_URL qui pointe vers /application/o/<slug>/) — utilisée pour
# lister les membres d'OIDC_REQUIRED_GROUP dans le sélecteur de partage.
AUTHENTIK_API_URL = str(ENV.get("AUTHENTIK_API_URL", "") or "").strip().rstrip("/")
AUTHENTIK_API_TOKEN = str(ENV.get("AUTHENTIK_API_TOKEN", "") or "").strip()

LOCAL_USER_SUB = str(ENV.get("DPGF_LOCAL_USER_SUB", "local") or "local").strip()
LOCAL_USER_NAME = str(ENV.get("DPGF_LOCAL_USER_NAME", "Utilisateur local") or "").strip()
LOCAL_USER_EMAIL = str(ENV.get("DPGF_LOCAL_USER_EMAIL", "") or "").strip()

USE_LLM = env_bool("DPGF_USE_LLM", False)
LIHA_URL = str(ENV.get("LIHA_CHAT_COMPLETIONS_URL", "") or "").strip()
LIHA_MODEL = str(ENV.get("LIHA_CHAT_MODEL", "") or "").strip()
LIHA_TOKEN = str(ENV.get("LIHA_CHAT_TOKEN", "") or "").strip()
LLM_TIMEOUT = float(ENV.get("DPGF_LLM_TIMEOUT", "180") or 180)
LLM_MAX_CHARS = int(ENV.get("DPGF_LLM_MAX_CHARS", "65000") or 65000)
# Timeout séparé pour les assistances ciblées (confirmation de périmètre,
# suggestion d'unité) : prompts courts, doivent rester rapides même si
# refine() (document entier) a un budget plus large.
#
# Plafonné : une valeur héritée de la configuration partagée
# (LIHA_RULES_SUGGEST_TIMEOUT_SECONDS=700, pensée pour un autre usage)
# autorisait 700 s d'attente par appel et par document, soit plus de 25
# minutes de blocage pour un seul CCTP avant de se rabattre sur le
# déterministe. Au-delà de deux minutes, l'assistance n'a plus d'intérêt :
# mieux vaut rendre le résultat déterministe tout de suite.
LLM_SUGGEST_TIMEOUT = min(
    float(ENV.get("LIHA_RULES_SUGGEST_TIMEOUT_SECONDS", "45") or 45),
    float(ENV.get("DPGF_LLM_SUGGEST_TIMEOUT_CAP", "120") or 120),
)

# refine() envoie le document entier (jusqu'à LLM_MAX_CHARS) et demande
# jusqu'à 12 000 tokens de réponse : c'est de loin l'appel le plus coûteux du
# pipeline. Or _merge_llm_result ne sait que réécrire des champs de lignes
# déjà trouvées — une ligne proposée sans correspondance déterministe est
# ignorée. Sur un lot inconnu, là où il manque justement des lignes, cet
# appel ne peut donc rien apporter. Il reste disponible pour l'expérimentation
# mais n'est plus dans le chemin par défaut.
LLM_DEEP_REFINE = env_bool("DPGF_LLM_DEEP_REFINE", False)

# Les CCTP d'un même dossier sont indépendants : ils sont traités en
# parallèle. Le gain est net dès que l'assistance LIHA est active (attente
# réseau), et reste sensible sur les PDF (PyMuPDF libère le GIL pendant
# l'extraction).
PROCESSING_WORKERS = max(1, int(ENV.get("DPGF_PROCESSING_WORKERS", "4") or 4))

MAX_FILE_BYTES = int(float(ENV.get("DPGF_MAX_FILE_MB", "80") or 80) * 1024 * 1024)
MAX_DOCUMENTS = int(ENV.get("DPGF_MAX_DOCUMENTS", "30") or 30)
ALLOWED_EXTENSIONS = {".pdf", ".docx"}

for directory in (OUTPUT_DIR, ANALYSES_DIR, ASSETS_DIR):
    directory.mkdir(parents=True, exist_ok=True)

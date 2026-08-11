from __future__ import annotations

import json
import shutil
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator

from . import config
from .parser import recompute_stats


class AnalysisNotFound(FileNotFoundError):
    pass


class InvalidAnalysisId(ValueError):
    pass


_write_lock = threading.RLock()


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def validate_analysis_id(value: str) -> str:
    value = str(value or "").strip()
    if len(value) != 32 or any(character not in "0123456789abcdef" for character in value):
        raise InvalidAnalysisId("Identifiant d'analyse invalide")
    return value


@contextmanager
def _connection() -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(str(config.DATABASE_PATH), timeout=20)
    connection.row_factory = sqlite3.Row
    try:
        yield connection
        connection.commit()
    finally:
        connection.close()


def initialize() -> None:
    with _write_lock, _connection() as connection:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS analyses (
                id TEXT PRIMARY KEY,
                owner_sub TEXT NOT NULL,
                project_name TEXT NOT NULL,
                project_reference TEXT NOT NULL DEFAULT '',
                client_name TEXT NOT NULL DEFAULT '',
                phase TEXT NOT NULL DEFAULT 'DCE',
                due_date TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL,
                progress INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                document_count INTEGER NOT NULL DEFAULT 0,
                lot_count INTEGER NOT NULL DEFAULT 0,
                line_count INTEGER NOT NULL DEFAULT 0,
                review_count INTEGER NOT NULL DEFAULT 0,
                error TEXT NOT NULL DEFAULT '',
                export_name TEXT NOT NULL DEFAULT '',
                payload_json TEXT NOT NULL
            )
            """
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_analyses_owner_updated "
            "ON analyses(owner_sub, updated_at DESC)"
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS analysis_shares (
                analysis_id TEXT NOT NULL,
                email TEXT NOT NULL,
                permission TEXT NOT NULL,
                name TEXT NOT NULL DEFAULT '',
                shared_by TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                PRIMARY KEY (analysis_id, email)
            )
            """
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_analysis_shares_email "
            "ON analysis_shares(email)"
        )


def analysis_directory(analysis_id: str) -> Path:
    analysis_id = validate_analysis_id(analysis_id)
    return config.ANALYSES_DIR / analysis_id


def source_directory(analysis_id: str) -> Path:
    path = analysis_directory(analysis_id) / "sources"
    path.mkdir(parents=True, exist_ok=True)
    return path


def export_directory(analysis_id: str) -> Path:
    path = analysis_directory(analysis_id) / "exports"
    path.mkdir(parents=True, exist_ok=True)
    return path


def snapshot_analysis(analysis_id: str) -> Path:
    """Keep a recoverable copy before a deterministic reprocessing."""
    payload = get_analysis(analysis_id)
    directory = analysis_directory(analysis_id).resolve()
    allowed_parent = config.ANALYSES_DIR.resolve()
    if directory.parent != allowed_parent:
        raise RuntimeError("Répertoire d'analyse hors périmètre")
    revisions = directory / "revisions"
    revisions.mkdir(parents=True, exist_ok=True)
    stamp = now_iso().replace(":", "").replace("-", "")
    destination = revisions / f"{stamp}_{uuid.uuid4().hex[:8]}.json"
    with destination.open("x", encoding="utf-8") as output:
        json.dump(payload, output, ensure_ascii=False, indent=2)
    return destination


def _row_to_payload(row: sqlite3.Row) -> dict[str, Any]:
    try:
        payload = json.loads(row["payload_json"])
    except json.JSONDecodeError:
        payload = {}
    payload.update(
        {
            "id": row["id"],
            "owner_sub": row["owner_sub"],
            "status": row["status"],
            "progress": row["progress"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "error": row["error"],
            "export_name": row["export_name"],
        }
    )
    return payload


def _summary(row: sqlite3.Row) -> dict[str, Any]:
    owner = {}
    try:
        owner = json.loads(row["payload_json"]).get("owner") or {}
    except json.JSONDecodeError:
        pass
    row_keys = row.keys()
    return {
        "id": row["id"],
        "project_name": row["project_name"],
        "project_reference": row["project_reference"],
        "client_name": row["client_name"],
        "phase": row["phase"],
        "due_date": row["due_date"],
        "status": row["status"],
        "progress": row["progress"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "document_count": row["document_count"],
        "lot_count": row["lot_count"],
        "line_count": row["line_count"],
        "review_count": row["review_count"],
        "error": row["error"],
        "has_export": bool(row["export_name"]),
        "owner_sub": row["owner_sub"],
        "owner_name": str(owner.get("name") or owner.get("email") or ""),
        "shared_permission": row["shared_permission"]
        if "shared_permission" in row_keys
        else None,
    }


def create_analysis(
    metadata: dict[str, Any],
    documents: list[dict[str, Any]],
    owner: dict[str, Any],
) -> dict[str, Any]:
    analysis_id = uuid.uuid4().hex
    created_at = now_iso()
    project_name = str(metadata.get("project_name") or "").strip()
    if not project_name:
        raise ValueError("Le nom du projet est obligatoire")
    payload = {
        "id": analysis_id,
        "schema_version": "1.0",
        "project": {
            "name": project_name,
            "reference": str(metadata.get("project_reference") or "").strip(),
            "client": str(metadata.get("client_name") or "").strip(),
            "phase": str(metadata.get("phase") or "DCE").strip() or "DCE",
            "due_date": str(metadata.get("due_date") or "").strip(),
        },
        "owner": {
            "sub": str(owner.get("sub") or "").strip(),
            "name": str(owner.get("name") or "").strip(),
            "email": str(owner.get("email") or "").strip(),
        },
        "status": "queued",
        "progress": 0,
        "created_at": created_at,
        "updated_at": created_at,
        "documents": documents,
        "lots": [],
        "stats": recompute_stats([]),
        "warnings": [],
        "processing": {
            "method": "deterministic",
            "llm_requested": bool(config.USE_LLM),
            "llm_used": False,
        },
        "tco": {
            "schema_version": "1.0",
            "status": "ready_for_future_connection",
            "last_transmitted_at": None,
        },
    }
    owner_sub = payload["owner"]["sub"]
    if not owner_sub:
        raise ValueError("Le propriétaire de l'analyse est obligatoire")
    analysis_directory(analysis_id).mkdir(parents=True, exist_ok=False)
    with _write_lock, _connection() as connection:
        connection.execute(
            """
            INSERT INTO analyses (
                id, owner_sub, project_name, project_reference, client_name,
                phase, due_date, status, progress, created_at, updated_at,
                document_count, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                analysis_id,
                owner_sub,
                payload["project"]["name"],
                payload["project"]["reference"],
                payload["project"]["client"],
                payload["project"]["phase"],
                payload["project"]["due_date"],
                "queued",
                0,
                created_at,
                created_at,
                len(documents),
                json.dumps(payload, ensure_ascii=False),
            ),
        )
    return payload


def get_analysis(analysis_id: str) -> dict[str, Any]:
    analysis_id = validate_analysis_id(analysis_id)
    with _connection() as connection:
        row = connection.execute(
            "SELECT * FROM analyses WHERE id = ?",
            (analysis_id,),
        ).fetchone()
    if row is None:
        raise AnalysisNotFound("Analyse introuvable")
    return _row_to_payload(row)


def list_analyses(
    search: str = "", *, owner_sub: str = "", viewer_email: str = ""
) -> list[dict[str, Any]]:
    # A row is visible only to its owner or to someone it was explicitly
    # shared with (see analysis_shares) — sharing is opt-in, decided by the
    # owner/an admin per project, not automatic for every app user. Write
    # permission on top of that is decided in app/main.py, not here.
    viewer_email = str(viewer_email or "").strip().lower()
    parameters: list[Any] = [viewer_email, owner_sub]
    query = (
        "SELECT a.*, s.permission AS shared_permission FROM analyses a "
        "LEFT JOIN analysis_shares s "
        "ON s.analysis_id = a.id AND s.email = ? "
        "WHERE a.owner_sub = ? OR s.email IS NOT NULL"
    )
    search = str(search or "").strip()
    if search:
        query += (
            " AND (a.project_name LIKE ? OR a.project_reference LIKE ? "
            "OR a.client_name LIKE ?)"
        )
        term = f"%{search}%"
        parameters.extend([term, term, term])
    query += " ORDER BY a.updated_at DESC"
    with _connection() as connection:
        rows = connection.execute(query, parameters).fetchall()
    return [_summary(row) for row in rows]


def list_shares(analysis_id: str) -> list[dict[str, Any]]:
    analysis_id = validate_analysis_id(analysis_id)
    with _connection() as connection:
        rows = connection.execute(
            "SELECT email, permission, name FROM analysis_shares "
            "WHERE analysis_id = ? ORDER BY email",
            (analysis_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def get_share(analysis_id: str, email: str) -> dict[str, Any] | None:
    analysis_id = validate_analysis_id(analysis_id)
    email = str(email or "").strip().lower()
    if not email:
        return None
    with _connection() as connection:
        row = connection.execute(
            "SELECT email, permission, name FROM analysis_shares "
            "WHERE analysis_id = ? AND email = ?",
            (analysis_id, email),
        ).fetchone()
    return dict(row) if row else None


def replace_shares(
    analysis_id: str, shares: list[dict[str, Any]], shared_by: str
) -> list[dict[str, Any]]:
    analysis_id = validate_analysis_id(analysis_id)
    get_analysis(analysis_id)  # raises AnalysisNotFound if missing
    cleaned: list[tuple[str, str, str]] = []
    seen_emails: set[str] = set()
    for entry in shares:
        if not isinstance(entry, dict):
            continue
        email = str(entry.get("email") or "").strip().lower()
        permission = str(entry.get("permission") or "").strip().lower()
        if not email or permission not in {"view", "edit"}:
            raise ValueError(
                f"Partage invalide : email et permission ('view' ou 'edit') requis"
            )
        if email in seen_emails:
            continue
        seen_emails.add(email)
        cleaned.append((email, permission, str(entry.get("name") or "").strip()))
    created_at = now_iso()
    with _write_lock, _connection() as connection:
        connection.execute(
            "DELETE FROM analysis_shares WHERE analysis_id = ?", (analysis_id,)
        )
        connection.executemany(
            "INSERT INTO analysis_shares "
            "(analysis_id, email, permission, name, shared_by, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [
                (analysis_id, email, permission, name, shared_by, created_at)
                for email, permission, name in cleaned
            ],
        )
    return list_shares(analysis_id)


def update_analysis(
    analysis_id: str,
    payload: dict[str, Any],
    *,
    status: str | None = None,
    progress: int | None = None,
    error: str | None = None,
    export_name: str | None = None,
) -> dict[str, Any]:
    previous = get_analysis(analysis_id)
    # The owner never changes just because someone else (an editor with an
    # elevated role, or an Admin) saves the analysis — preserve it from the
    # stored row, never from whoever is currently writing.
    immutable_owner = previous.get("owner") or {}
    payload["id"] = analysis_id
    payload["owner"] = immutable_owner
    payload["owner_sub"] = str(immutable_owner.get("sub") or "")
    payload["created_at"] = previous.get("created_at")
    payload["updated_at"] = now_iso()
    if status is not None:
        payload["status"] = status
    if progress is not None:
        payload["progress"] = max(0, min(100, int(progress)))
    payload["stats"] = recompute_stats(payload.get("lots") or [])
    project = payload.get("project") or {}
    stats = payload["stats"]
    next_error = str(error if error is not None else payload.get("error") or "")
    next_export = str(
        export_name if export_name is not None else previous.get("export_name") or ""
    )
    with _write_lock, _connection() as connection:
        connection.execute(
            """
            UPDATE analyses SET
                project_name = ?, project_reference = ?, client_name = ?,
                phase = ?, due_date = ?, status = ?, progress = ?,
                updated_at = ?, document_count = ?, lot_count = ?,
                line_count = ?, review_count = ?, error = ?, export_name = ?,
                payload_json = ?
            WHERE id = ?
            """,
            (
                str(project.get("name") or ""),
                str(project.get("reference") or ""),
                str(project.get("client") or ""),
                str(project.get("phase") or "DCE"),
                str(project.get("due_date") or ""),
                str(payload.get("status") or previous.get("status") or "processing"),
                int(payload.get("progress") or 0),
                payload["updated_at"],
                len(payload.get("documents") or []),
                int(stats["lots"]),
                int(stats["items"]),
                int(stats["to_review"]),
                next_error,
                next_export,
                json.dumps(payload, ensure_ascii=False),
                analysis_id,
            ),
        )
    payload["error"] = next_error
    payload["export_name"] = next_export
    return payload


def update_progress(
    analysis_id: str,
    *,
    status: str,
    progress: int,
    error: str = "",
) -> dict[str, Any]:
    payload = get_analysis(analysis_id)
    return update_analysis(
        analysis_id,
        payload,
        status=status,
        progress=progress,
        error=error,
    )


def delete_analysis(analysis_id: str) -> None:
    get_analysis(analysis_id)
    directory = analysis_directory(analysis_id).resolve()
    allowed_parent = config.ANALYSES_DIR.resolve()
    if directory.parent != allowed_parent:
        raise RuntimeError("Répertoire d'analyse hors périmètre")
    with _write_lock, _connection() as connection:
        connection.execute(
            "DELETE FROM analyses WHERE id = ?",
            (analysis_id,),
        )
        connection.execute(
            "DELETE FROM analysis_shares WHERE analysis_id = ?",
            (analysis_id,),
        )
    if directory.exists():
        shutil.rmtree(directory)


initialize()

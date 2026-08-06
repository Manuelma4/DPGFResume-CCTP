from __future__ import annotations

import hashlib
import mimetypes
import re
import shutil
import uuid
from pathlib import Path
from typing import Any
from zipfile import ZIP_DEFLATED, ZipFile

from fastapi import (
    BackgroundTasks,
    Body,
    FastAPI,
    File,
    Form,
    HTTPException,
    Request,
    Response,
    UploadFile,
)
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from . import __version__, auth, config, llm, store
from .excel_export import (
    export_analysis_files,
    safe_export_archive_name,
)
from .extractors import extract_document
from .parser import lot_sort_key, parse_document, recompute_stats


app = FastAPI(
    title="DPGF Résumé CCTP",
    version=__version__,
    description="Extraction traçable des CCTP Word/PDF et génération de DPGF Excel standardisés.",
)
app.add_middleware(GZipMiddleware, minimum_size=1200)


@app.middleware("http")
async def request_context(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
    try:
        response = await call_next(request)
    except Exception:
        raise
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "same-origin"
    return response


def _user(request: Request) -> auth.UserIdentity:
    try:
        return auth.service.current_user(request)
    except auth.AuthenticationRequired as exc:
        raise HTTPException(401, str(exc)) from exc
    except auth.AuthConfigurationError as exc:
        raise HTTPException(503, str(exc)) from exc


def _analysis(analysis_id: str, user: auth.UserIdentity) -> dict[str, Any]:
    try:
        return store.get_analysis(analysis_id, user.sub)
    except store.InvalidAnalysisId as exc:
        raise HTTPException(400, str(exc)) from exc
    except store.AnalysisNotFound as exc:
        raise HTTPException(404, str(exc)) from exc


def _safe_original_name(value: str) -> str:
    value = Path(str(value or "document")).name
    value = re.sub(r"[\x00-\x1f<>:\"/\\|?*]", "_", value).strip(" .")
    return value[:180] or "document"


async def _save_upload(upload: UploadFile, destination: Path) -> tuple[int, str]:
    size = 0
    digest = hashlib.sha256()
    try:
        with destination.open("xb") as output:
            while chunk := await upload.read(1024 * 1024):
                size += len(chunk)
                if size > config.MAX_FILE_BYTES:
                    raise HTTPException(
                        413,
                        f"{upload.filename or 'Le fichier'} dépasse la taille maximale autorisée.",
                    )
                digest.update(chunk)
                output.write(chunk)
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    finally:
        await upload.close()
    return size, digest.hexdigest()


def _merge_llm_result(
    deterministic: dict[str, Any], refined: dict[str, Any]
) -> dict[str, Any]:
    by_code = {
        str(line.get("code") or "").strip().casefold(): line
        for line in deterministic.get("lines", [])
        if line.get("code")
    }
    by_title = {
        str(line.get("designation") or "").strip().casefold(): line
        for line in deterministic.get("lines", [])
    }
    changed = 0
    for suggestion in refined.get("lines") or []:
        if not isinstance(suggestion, dict):
            continue
        code = str(suggestion.get("code") or "").strip().casefold()
        title = str(suggestion.get("designation") or "").strip()
        target = by_code.get(code) if code else by_title.get(title.casefold())
        if not target:
            continue
        for key in ("designation", "unit", "quantity", "review_reason"):
            value = suggestion.get(key)
            if value not in (None, ""):
                target[key] = value
        try:
            target["confidence"] = max(
                float(target.get("confidence") or 0),
                min(0.99, float(suggestion.get("confidence") or 0)),
            )
        except (TypeError, ValueError):
            pass
        if suggestion.get("source_excerpt"):
            target["source_excerpt"] = str(suggestion["source_excerpt"])[:500]
        target["origin"] = "deterministic+llm"
        target["review_status"] = (
            "to_review" if target.get("review_reason") else "validated"
        )
        changed += 1
    if refined.get("lot_code"):
        deterministic["code"] = str(refined["lot_code"])[:40]
    if refined.get("lot_title"):
        deterministic["title"] = str(refined["lot_title"])[:140]
    deterministic["llm_updated_lines"] = changed
    return deterministic


def _restore_user_content(
    lot: dict[str, Any], previous_lot: dict[str, Any] | None
) -> dict[str, Any]:
    if not previous_lot:
        return lot
    previous_lines = list(previous_lot.get("lines") or [])
    overrides = {
        str(line.get("id") or ""): line
        for line in previous_lines
        if line.get("user_edited") and line.get("id")
    }
    for line in lot.get("lines") or []:
        override = overrides.get(str(line.get("id") or ""))
        if not override:
            continue
        for key in (
            "code",
            "designation",
            "description",
            "unit",
            "quantity",
            "unit_price",
            "included",
            "review_status",
            "review_reason",
        ):
            if key in override:
                line[key] = override[key]
        line["user_edited"] = True
        line["origin"] = "deterministic-v2+manual-edit"
    manual_lines = [
        line
        for line in previous_lines
        if str(line.get("origin") or "").startswith("manual")
    ]
    existing_ids = {str(line.get("id") or "") for line in lot.get("lines") or []}
    lot["lines"].extend(
        line for line in manual_lines if str(line.get("id") or "") not in existing_ids
    )
    return lot


def process_analysis(
    analysis_id: str, owner_sub: str, preserve_user_content: bool = False
) -> None:
    try:
        analysis = store.get_analysis(analysis_id, owner_sub)
        analysis = store.update_analysis(
            analysis_id, owner_sub, analysis, status="processing", progress=5, error=""
        )
        previous_lots = {
            str(lot.get("source_id") or ""): lot
            for lot in analysis.get("lots") or []
        }
        lots: list[dict[str, Any]] = []
        warnings: list[dict[str, Any]] = []
        llm_used = False
        documents = analysis.get("documents") or []
        for index, source in enumerate(documents):
            path = store.source_directory(analysis_id) / source["stored_name"]
            try:
                extracted = extract_document(path)
                source["page_count"] = extracted.page_count
                source["character_count"] = extracted.character_count
                source["status"] = "processed"
                lot = parse_document(extracted, source["id"])
                if llm.available() and extracted.character_count:
                    try:
                        refined = llm.refine(extracted.text, lot)
                        lot = _merge_llm_result(lot, refined)
                        llm_used = True
                    except Exception as exc:
                        lot.setdefault("warnings", []).append(
                            f"Assistance LIHA indisponible : {type(exc).__name__}. "
                            "L'extraction déterministe a été conservée."
                        )
                if preserve_user_content:
                    lot = _restore_user_content(
                        lot, previous_lots.get(str(source.get("id") or ""))
                    )
                lots.append(lot)
                for warning in lot.get("warnings") or []:
                    warnings.append(
                        {
                            "source_id": source["id"],
                            "source_name": source["original_name"],
                            "message": warning,
                        }
                    )
            except Exception as exc:
                source["status"] = "failed"
                source["error"] = str(exc)
                warnings.append(
                    {
                        "source_id": source["id"],
                        "source_name": source["original_name"],
                        "message": f"Document non traité : {exc}",
                    }
                )
            progress = 10 + round((index + 1) / max(1, len(documents)) * 78)
            analysis["lots"] = lots
            analysis["warnings"] = warnings
            analysis = store.update_analysis(
                analysis_id,
                owner_sub,
                analysis,
                status="processing",
                progress=progress,
            )

        if not lots:
            raise ValueError("Aucun CCTP n'a pu être traité")
        lots.sort(key=lot_sort_key)
        analysis["lots"] = lots
        analysis["warnings"] = warnings
        analysis["processing"]["llm_used"] = llm_used
        analysis["processing"]["method"] = (
            "deterministic+llm" if llm_used else "deterministic"
        )
        analysis["processing"]["parser_version"] = "2.0"
        analysis["processing"]["reprocessing"] = False
        stats = recompute_stats(lots)
        final_status = "needs_review" if stats["to_review"] else "ready"
        store.update_analysis(
            analysis_id,
            owner_sub,
            analysis,
            status=final_status,
            progress=100,
            error="",
        )
    except Exception as exc:
        try:
            analysis = store.get_analysis(analysis_id, owner_sub)
            store.update_analysis(
                analysis_id,
                owner_sub,
                analysis,
                status="failed",
                progress=100,
                error=str(exc),
            )
        except Exception:
            pass


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "application": "dpgf-resume-cctp",
        "version": __version__,
        "environment": config.ENVIRONMENT,
        "template_available": config.TEMPLATE_PATH.exists(),
        "llm_available": llm.available(),
    }


@app.get("/api/auth/me")
def current_user(request: Request) -> dict[str, Any]:
    user = _user(request)
    return {
        **user.snapshot(),
        "auth_required": config.AUTH_REQUIRED,
        "environment": config.ENVIRONMENT,
    }


@app.get("/api/auth/login")
def login(request: Request, next: str = "/"):
    if config.LOCAL_MODE and not config.AUTH_REQUIRED:
        return RedirectResponse(url=auth.safe_redirect(next), status_code=302)
    try:
        url, state = auth.service.login_url(next)
    except auth.AuthConfigurationError as exc:
        raise HTTPException(503, str(exc)) from exc
    response = RedirectResponse(url=url, status_code=302)
    response.set_cookie(
        config.OIDC_STATE_COOKIE_NAME,
        state,
        max_age=600,
        secure=config.SESSION_COOKIE_SECURE,
        httponly=True,
        samesite="lax",
        path="/api/auth",
    )
    return response


@app.get("/api/auth/callback")
def callback(
    request: Request,
    code: str = "",
    state: str = "",
    error: str = "",
    error_description: str = "",
):
    if error:
        description = error_description.strip() or "Erreur OIDC sans description"
        raise HTTPException(
            400,
            f"Le fournisseur OIDC a refusé la connexion ({error}) : {description}",
        )
    if not code or not state:
        raise HTTPException(400, "Code ou état OIDC absent")
    try:
        session_token, redirect_after = auth.service.callback(
            code,
            state,
            request.cookies.get(config.OIDC_STATE_COOKIE_NAME, ""),
        )
    except (auth.InvalidOAuthResponse, auth.AuthConfigurationError, httpx.HTTPError) as exc:
        raise HTTPException(400, f"Connexion refusée : {exc}") from exc
    response = RedirectResponse(redirect_after, status_code=302)
    response.delete_cookie(config.OIDC_STATE_COOKIE_NAME, path="/api/auth")
    response.set_cookie(
        config.SESSION_COOKIE_NAME,
        session_token,
        max_age=config.SESSION_TTL_SECONDS,
        secure=config.SESSION_COOKIE_SECURE,
        httponly=True,
        samesite="lax",
        path="/",
    )
    return response


@app.get("/api/auth/logout")
def logout(request: Request):
    try:
        destination = auth.service.logout_url(request)
    except (auth.AuthConfigurationError, httpx.HTTPError):
        destination = config.OIDC_POST_LOGOUT_REDIRECT_URI or "/"
    response = RedirectResponse(destination, status_code=302)
    response.delete_cookie(
        config.SESSION_COOKIE_NAME,
        secure=config.SESSION_COOKIE_SECURE,
        httponly=True,
        samesite="lax",
        path="/",
    )
    return response


@app.post("/api/v1/analyses", status_code=202)
async def create_analysis(
    background_tasks: BackgroundTasks,
    request: Request,
    files: list[UploadFile] = File(...),
    project_name: str = Form(...),
    project_reference: str = Form(""),
    client_name: str = Form(""),
    phase: str = Form("DCE"),
    due_date: str = Form(""),
):
    user = _user(request)
    if not files:
        raise HTTPException(422, "Déposez au moins un CCTP")
    if len(files) > config.MAX_DOCUMENTS:
        raise HTTPException(
            422, f"Un dossier est limité à {config.MAX_DOCUMENTS} documents."
        )
    documents: list[dict[str, Any]] = []
    for upload in files:
        original_name = _safe_original_name(upload.filename or "document")
        extension = Path(original_name).suffix.casefold()
        if extension not in config.ALLOWED_EXTENSIONS:
            raise HTTPException(
                415,
                f"{original_name} : format non pris en charge. Utilisez PDF ou DOCX.",
            )
        source_id = f"src_{uuid.uuid4().hex[:14]}"
        documents.append(
            {
                "id": source_id,
                "original_name": original_name,
                "stored_name": f"{source_id}{extension}",
                "extension": extension,
                "size": 0,
                "sha256": "",
                "status": "uploaded",
            }
        )

    try:
        analysis = store.create_analysis(
            {
                "project_name": project_name,
                "project_reference": project_reference,
                "client_name": client_name,
                "phase": phase,
                "due_date": due_date,
            },
            documents,
            user.snapshot(),
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc

    try:
        source_dir = store.source_directory(analysis["id"])
        for upload, document in zip(files, documents):
            size, digest = await _save_upload(
                upload, source_dir / document["stored_name"]
            )
            document["size"] = size
            document["sha256"] = digest
        analysis["documents"] = documents
        store.update_analysis(
            analysis["id"], user.sub, analysis, status="queued", progress=2
        )
    except Exception:
        try:
            store.delete_analysis(analysis["id"], user.sub)
        except Exception:
            pass
        raise

    background_tasks.add_task(process_analysis, analysis["id"], user.sub)
    return {
        "id": analysis["id"],
        "status": "queued",
        "progress": 2,
        "location": f"/api/v1/analyses/{analysis['id']}",
    }


@app.post("/api/v1/analyses/{analysis_id}/reprocess", status_code=202)
def reprocess_analysis(
    analysis_id: str,
    background_tasks: BackgroundTasks,
    request: Request,
):
    user = _user(request)
    analysis = _analysis(analysis_id, user)
    if analysis.get("status") in {"queued", "processing"}:
        raise HTTPException(409, "Le traitement est déjà en cours")
    if not analysis.get("documents"):
        raise HTTPException(422, "Aucun document source n'est disponible")
    store.snapshot_analysis(analysis_id, user.sub)
    processing = analysis.setdefault("processing", {})
    processing["reprocessing"] = True
    processing["previous_method"] = processing.get("method", "deterministic")
    store.update_analysis(
        analysis_id,
        user.sub,
        analysis,
        status="queued",
        progress=2,
        error="",
        export_name="",
    )
    background_tasks.add_task(process_analysis, analysis_id, user.sub, True)
    return {
        "id": analysis_id,
        "status": "queued",
        "progress": 2,
        "location": f"/api/v1/analyses/{analysis_id}",
    }


@app.get("/api/v1/analyses")
def analyses(request: Request, search: str = ""):
    user = _user(request)
    return {"analyses": store.list_analyses(user.sub, search)}


@app.get("/api/v1/analyses/{analysis_id}")
def analysis_detail(analysis_id: str, request: Request):
    return _analysis(analysis_id, _user(request))


@app.put("/api/v1/analyses/{analysis_id}")
def save_analysis(
    analysis_id: str,
    request: Request,
    payload: dict[str, Any] = Body(...),
):
    user = _user(request)
    previous = _analysis(analysis_id, user)
    if previous.get("status") in {"queued", "processing"}:
        raise HTTPException(409, "Le traitement est encore en cours")
    project = payload.get("project")
    if isinstance(project, dict):
        name = str(project.get("name") or "").strip()
        if not name:
            raise HTTPException(422, "Le nom du projet est obligatoire")
        previous["project"] = {
            "name": name[:180],
            "reference": str(project.get("reference") or "")[:100],
            "client": str(project.get("client") or "")[:180],
            "phase": str(project.get("phase") or "DCE")[:30],
            "due_date": str(project.get("due_date") or "")[:20],
        }
    lots = payload.get("lots")
    if isinstance(lots, list):
        previous_lines_by_id = {
            str(line.get("id") or ""): line
            for previous_lot in previous.get("lots") or []
            for line in previous_lot.get("lines") or []
            if line.get("id")
        }
        cleaned_lots: list[dict[str, Any]] = []
        for lot_index, lot in enumerate(lots):
            if not isinstance(lot, dict):
                continue
            cleaned_lines: list[dict[str, Any]] = []
            for line_index, line in enumerate(lot.get("lines") or []):
                if not isinstance(line, dict):
                    continue
                designation = str(line.get("designation") or "").strip()
                if not designation:
                    continue
                kind = "section" if line.get("kind") == "section" else "item"
                unit = str(line.get("unit") or "Ens")
                if unit not in {"m²", "m³", "ml", "U", "Ens", "kg", "h"}:
                    unit = "Ens"
                quantity = line.get("quantity")
                try:
                    quantity = None if quantity in (None, "") else float(quantity)
                except (TypeError, ValueError):
                    raise HTTPException(
                        422, f"Quantité invalide pour « {designation} »"
                    )
                cleaned_line = {
                        **line,
                        "id": str(line.get("id") or f"manual_{uuid.uuid4().hex[:14]}"),
                        "kind": kind,
                        "level": max(1, min(8, int(line.get("level") or 1))),
                        "code": str(line.get("code") or "")[:60],
                        "designation": designation[:240],
                        "description": str(line.get("description") or "")[:2000],
                        "unit": None if kind == "section" else unit,
                        "quantity": None if kind == "section" else quantity,
                        "included": bool(line.get("included", True)),
                        "review_status": (
                            "validated"
                            if line.get("review_status") == "validated"
                            else "to_review"
                        ),
                        "origin": str(line.get("origin") or "manual"),
                        "sort_order": line_index,
                    }
                prior_line = previous_lines_by_id.get(cleaned_line["id"])
                if prior_line and not cleaned_line["origin"].startswith("manual"):
                    editable_fields = (
                        "kind",
                        "level",
                        "code",
                        "designation",
                        "description",
                        "unit",
                        "quantity",
                        "unit_price",
                        "included",
                        "review_status",
                        "review_reason",
                    )
                    cleaned_line["user_edited"] = bool(
                        prior_line.get("user_edited")
                        or any(
                            cleaned_line.get(key) != prior_line.get(key)
                            for key in editable_fields
                        )
                    )
                cleaned_lines.append(cleaned_line)
            cleaned_lots.append(
                {
                    **lot,
                    "id": str(lot.get("id") or f"lot_{uuid.uuid4().hex[:12]}"),
                    "code": str(lot.get("code") or "")[:40],
                    "title": str(lot.get("title") or f"Lot {lot_index + 1}")[:140],
                    "lines": cleaned_lines,
                }
            )
        previous["lots"] = cleaned_lots
    stats = recompute_stats(previous.get("lots") or [])
    next_status = "needs_review" if stats["to_review"] else "ready"
    return store.update_analysis(
        analysis_id,
        user.sub,
        previous,
        status=next_status,
        progress=100,
        export_name="",
    )


@app.post("/api/v1/analyses/{analysis_id}/export")
def generate_export(analysis_id: str, request: Request):
    user = _user(request)
    analysis = _analysis(analysis_id, user)
    if analysis.get("status") not in {"ready", "needs_review"}:
        raise HTTPException(409, "L'analyse n'est pas prête à être exportée")
    export_directory = store.export_directory(analysis_id)
    try:
        lot_files = export_analysis_files(analysis, export_directory)
        if len(lot_files) == 1:
            export_name = lot_files[0].name
        else:
            export_name = safe_export_archive_name(analysis.get("project") or {})
            archive_path = export_directory / export_name
            with ZipFile(archive_path, "w", compression=ZIP_DEFLATED) as archive:
                for lot_file in lot_files:
                    archive.write(lot_file, arcname=lot_file.name)
    except (ValueError, OSError) as exc:
        raise HTTPException(422, f"Échec de la génération Excel : {exc}") from exc
    store.update_analysis(
        analysis_id,
        user.sub,
        analysis,
        export_name=export_name,
    )
    return {
        "file_name": export_name,
        "lot_files": [path.name for path in lot_files],
        "download_url": f"/api/v1/analyses/{analysis_id}/export.xlsx",
        "review_count": analysis.get("stats", {}).get("to_review", 0),
    }


@app.get("/api/v1/analyses/{analysis_id}/export.xlsx")
def download_export(analysis_id: str, request: Request):
    user = _user(request)
    analysis = _analysis(analysis_id, user)
    export_name = Path(str(analysis.get("export_name") or "")).name
    if not export_name:
        raise HTTPException(404, "Aucun export n'a encore été généré")
    path = store.export_directory(analysis_id) / export_name
    if not path.exists():
        raise HTTPException(404, "Le fichier exporté est introuvable")
    media_type = (
        "application/zip"
        if path.suffix.casefold() == ".zip"
        else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    return FileResponse(
        path,
        media_type=media_type,
        filename=export_name,
    )


@app.get("/api/v1/analyses/{analysis_id}/sources/{source_id}")
def download_source(analysis_id: str, source_id: str, request: Request):
    user = _user(request)
    analysis = _analysis(analysis_id, user)
    source = next(
        (item for item in analysis.get("documents") or [] if item.get("id") == source_id),
        None,
    )
    if source is None:
        raise HTTPException(404, "Document source introuvable")
    path = store.source_directory(analysis_id) / Path(source["stored_name"]).name
    if not path.exists():
        raise HTTPException(404, "Fichier source introuvable")
    media_type = mimetypes.guess_type(source["original_name"])[0] or "application/octet-stream"
    return FileResponse(path, media_type=media_type, filename=source["original_name"])


@app.get("/api/v1/analyses/{analysis_id}/tco")
def tco_contract(analysis_id: str, request: Request):
    user = _user(request)
    analysis = _analysis(analysis_id, user)
    return {
        "schema_version": "1.0",
        "source_application": "dpgf-resume-cctp",
        "analysis_id": analysis["id"],
        "generated_at": analysis.get("updated_at"),
        "project": analysis.get("project"),
        "lots": [
            {
                "external_id": lot.get("id"),
                "code": lot.get("code"),
                "title": lot.get("title"),
                "source_document_id": lot.get("source_id"),
                "lines": [
                    {
                        "external_id": line.get("id"),
                        "parent_level": line.get("level"),
                        "kind": line.get("kind"),
                        "code": line.get("code"),
                        "designation": line.get("designation"),
                        "unit": line.get("unit"),
                        "quantity": line.get("quantity"),
                        "included": line.get("included", True),
                        "source_page": line.get("source_page"),
                    }
                    for line in lot.get("lines") or []
                ],
            }
            for lot in analysis.get("lots") or []
        ],
    }


@app.delete("/api/v1/analyses/{analysis_id}", status_code=204)
def remove_analysis(analysis_id: str, request: Request):
    user = _user(request)
    try:
        store.delete_analysis(analysis_id, user.sub)
    except store.InvalidAnalysisId as exc:
        raise HTTPException(400, str(exc)) from exc
    except store.AnalysisNotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    return Response(status_code=204)


@app.get("/api/template")
def download_template(request: Request):
    _user(request)
    if not config.TEMPLATE_PATH.exists():
        raise HTTPException(404, "Modèle DPGF absent")
    return FileResponse(
        config.TEMPLATE_PATH,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename="DPGF TYPE.xlsx",
    )


@app.get("/api/assets/logo")
def logo():
    path = config.ASSETS_DIR / "moduo_logo.png"
    if not path.exists():
        raise HTTPException(404, "Logo absent")
    return FileResponse(path, media_type="image/png")


if config.FRONTEND_DIST.exists():
    assets = config.FRONTEND_DIST / "assets"
    if assets.exists():
        app.mount("/assets", StaticFiles(directory=assets), name="frontend-assets")


@app.get("/{full_path:path}", include_in_schema=False)
def frontend(full_path: str):
    index = config.FRONTEND_DIST / "index.html"
    requested = (config.FRONTEND_DIST / full_path).resolve()
    if (
        full_path
        and requested.parent == config.FRONTEND_DIST.resolve()
        and requested.exists()
        and requested.is_file()
    ):
        return FileResponse(requested)
    if index.exists():
        return FileResponse(index)
    return JSONResponse(
        {
            "message": "Frontend non compilé",
            "hint": "Exécutez setup.ps1 puis run.ps1",
        },
        status_code=503,
    )

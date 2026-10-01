"""
Documentos con revisión humana, lotes, políticas por tipo documental (v1.1).

Flujo: POST /api/v1/documents → workers (hilos) extraen y detectan → estado `review`
→ el revisor acepta/rechaza/agrega hallazgos → POST .../approve → tachado + verificador
→ `protected` o `protected_with_warnings` (descargables, con aviso si corresponde).
"""
from __future__ import annotations

import csv
import fcntl
import hashlib
import io
import json
import os
import shutil
import threading
import time
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse

from . import engine, pipeline
from .app import (ANALYSIS_HELP, DATA_DIR, DOCUMENT_TYPE_HELP, ENTITIES_HELP, MAX_UPLOAD, audit, choice,
                  current_user, db, ip_of, now, require)

router = APIRouter()
# PROCESSING_WORKERS = hilos de procesamiento por proceso web (WORKERS queda como alias
# heredado). Los procesos web se controlan aparte con WEB_WORKERS (Dockerfile).
PROCESSING_WORKERS = int(os.environ.get("PROCESSING_WORKERS", os.environ.get("WORKERS", "2")))
EXECUTOR = ThreadPoolExecutor(max_workers=PROCESSING_WORKERS)
REVIEWERS = ("revisor", "aprobador", "administrador")
TMP_DIR = DATA_DIR / "tmp"
QUEUE_STATES = ("uploaded", "processing", "submitted", "approved", "protecting")
MB = 1048576
DOWNLOADABLE = ("protected", "protected_with_warnings")
EDITABLE = ("review", "verification_failed", "protected_with_warnings")
VERIFICATION_DISCLAIMER = (
    "La verificación automática detectó posibles datos personales o comprobaciones "
    "pendientes. Puede descargar el archivo, pero no se garantiza su anonimización completa."
)


def _has_warning(doc) -> bool:
    verification = doc["verification"]
    if isinstance(verification, str):
        verification = json.loads(verification)
    return bool(verification and not verification["ok"])


def _completed_status(verification: dict) -> str:
    return "protected" if verification["ok"] else "protected_with_warnings"



def _env_int(name: str, default: int) -> int:
    return int(os.environ.get(name, str(default)))


def doc_dir(doc_id: str) -> Path:
    return DATA_DIR / "docs" / doc_id


def _can_see(user, doc) -> bool:
    if user["role"] in ("revisor", "aprobador", "auditor", "administrador"):
        return True
    return doc["uploaded_by"] == user["username"]


def _is_regular_owner(user, doc) -> bool:
    return user["role"] == "regular" and doc["uploaded_by"] == user["username"]


def _get_doc(con, doc_id, user):
    doc = con.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
    if not doc:
        raise HTTPException(404, "Documento no existe")
    if not _can_see(user, doc):
        raise HTTPException(403, "Sin permiso sobre este documento")
    return doc


def effective_entities(con, doc_type: str) -> list[str]:
    rules = {(r["document_type"], r["entity_code"]): r["enabled"]
             for r in con.execute("SELECT * FROM policies")}
    return [c for c in engine.ENTITY_TYPES
            if rules.get((doc_type, c), rules.get(("*", c), 1))]


# ---------------------------------------------------------------------------
# Jobs (hilos)
# ---------------------------------------------------------------------------

def process_document(doc_id: str):
    con = db()
    try:
        doc = con.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
        if not doc:
            return
        if doc["status"] not in ("uploaded", "processing"):
            return  # ya procesado por otro worker (p. ej. requeue duplicado)
        con.execute("UPDATE documents SET status='processing' WHERE id=?", (doc_id,))
        con.execute("DELETE FROM findings WHERE document_id=?", (doc_id,))  # idempotente ante reintento
        con.commit()
        d = doc_dir(doc_id)
        wd = d / "work"
        t0 = time.time()
        try:
            analysis_level = doc["analysis_level"] or "fast"
            stage_started = time.perf_counter()
            ext = pipeline.extract(d / f"original.{doc['format']}", wd, analysis_level)
            extraction_seconds = time.perf_counter() - stage_started
            stage_started = time.perf_counter()
            findings = pipeline.detect_findings(ext, json.loads(doc["entities"]), wd)
            detection_seconds = time.perf_counter() - stage_started
            for f in findings:
                con.execute("INSERT INTO findings(id,document_id,entity_code,text,score,page,boxes,location,start_off,end_off,source,status) "
                            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                            (f["id"], doc_id, f["entity_code"], f["text"], f["score"], f["page"],
                             json.dumps(f["boxes"]), f["location"], f["start"], f["end"], f["source"], f["status"]))
            pages = len(ext["pages"]) if ext["kind"] == "paged" else None
            stats = {"seconds": round(time.time() - t0, 2), "findings": len(findings),
                     "extraction_seconds": round(extraction_seconds, 4),
                     "detection_seconds": round(detection_seconds, 4),
                     "analysis_seconds": round(extraction_seconds + detection_seconds, 4),
                     "analysis_seconds_per_page": round((extraction_seconds + detection_seconds) / pages, 4) if pages else None,
                     "engine_version": engine.ENGINE_VERSION,
                     "ocr_pages": len(ext.get("ocr_pages", [])),
                     "hybrid_ocr_pages": len(ext.get("hybrid_ocr_pages", [])),
                     "verification_ocr_pages": len(ext.get("verification_ocr_pages", [])),
                     "analysis_level": analysis_level}
            con.execute("UPDATE documents SET status='review', pages=?, stats=? WHERE id=?",
                        (pages, json.dumps(stats), doc_id))
            audit(con, doc["uploaded_by"], None, "processed", {"document_id": doc_id, "file": doc["original_name"], **stats})
        except engine.EngineError as e:
            con.rollback()  # Postgres aborta la transacción ante un error SQL; sin esto el UPDATE también falla
            con.execute("UPDATE documents SET status='failed', error=? WHERE id=?", (str(e), doc_id))
            audit(con, doc["uploaded_by"], None, "process_failed",
                  {"document_id": doc_id, "analysis_level": doc["analysis_level"] or "fast", "error": e.code})
        except Exception as e:  # noqa: BLE001
            con.rollback()
            con.execute("UPDATE documents SET status='failed', error=? WHERE id=?", (f"Error interno: {type(e).__name__}", doc_id))
            audit(con, doc["uploaded_by"], None, "process_failed",
                  {"document_id": doc_id, "analysis_level": doc["analysis_level"] or "fast", "error": "INTERNAL"})
        con.commit()
    finally:
        con.close()


def _persistent_pseudonym_map(con, doc, findings: list[dict]) -> dict[str, str]:
    """Asigna alias estables por lote/documento guardando solo el hash del valor.

    El advisory lock por alcance evita que dos documentos del mismo lote reciban el
    mismo número bajo procesamiento concurrente. No se conserva el dato original.
    """
    scope_id = f"batch:{doc['batch_id']}" if doc["batch_id"] else f"document:{doc['id']}"
    mapping: dict[str, str] = {}
    con.commit()
    con.execute("SELECT pg_advisory_xact_lock(hashtext(?))", (scope_id,))
    try:
        for finding in sorted(findings, key=pipeline.pseudonym_key):
            key = pipeline.pseudonym_key(finding)
            digest = hashlib.sha256(key.encode()).hexdigest()
            row = con.execute(
                "SELECT alias FROM pseudonym_aliases WHERE scope_id=? AND entity_code=? AND value_hash=?",
                (scope_id, finding["entity_code"], digest),
            ).fetchone()
            if row:
                mapping[key] = row["alias"]
                continue
            used = {r["alias"] for r in con.execute(
                "SELECT alias FROM pseudonym_aliases WHERE scope_id=? AND entity_code=?",
                (scope_id, finding["entity_code"]),
            )}
            prefix, number = pipeline.pseudonym_prefix(finding["entity_code"]), 1
            while f"{prefix}-{number:02d}" in used:
                number += 1
            alias = f"{prefix}-{number:02d}"
            con.execute(
                "INSERT INTO pseudonym_aliases(scope_id,entity_code,value_hash,alias) VALUES(?,?,?,?)",
                (scope_id, finding["entity_code"], digest, alias),
            )
            mapping[key] = alias
        con.commit()
        return mapping
    except Exception:
        con.rollback()
        raise


def protect_document(doc_id: str, approved_by: str, final_status: str = "protected"):
    con = db()
    try:
        doc = con.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
        con.execute("UPDATE documents SET status='protecting' WHERE id=?", (doc_id,))
        con.commit()
        d = doc_dir(doc_id)
        wd = d / "work"
        try:
            ext = json.loads((wd / "extraction.json").read_text())
            rows = con.execute("SELECT * FROM findings WHERE document_id=? AND status IN ('accepted','edited')", (doc_id,)).fetchall()
            findings = [{"id": r["id"], "entity_code": r["entity_code"], "text": r["text"], "status": r["status"],
                         "page": r["page"], "boxes": json.loads(r["boxes"]) if r["boxes"] else None,
                         "location": r["location"], "start": r["start_off"], "end": r["end_off"]} for r in rows]
            out = d / f"protected.{doc['format']}"
            treatment = doc["treatment"] or "redact"  # compatibilidad con trabajos creados antes de v0.5
            pseudonyms = _persistent_pseudonym_map(con, doc, findings) if treatment == "pseudonymize" else None
            stage_started = time.perf_counter()
            stats = pipeline.apply_findings(d / f"original.{doc['format']}", ext, findings, wd, out,
                                            treatment, pseudonyms, json.loads(doc["entities"]))
            application_seconds = time.perf_counter() - stage_started
            suppressed = [f["text"] for f in findings if f["text"] and not f["text"].startswith("(")]
            stage_started = time.perf_counter()
            ver = pipeline.verify(out, suppressed)
            verification_seconds = time.perf_counter() - stage_started
            previous_stats = json.loads(doc["stats"]) if doc["stats"] else {}
            analysis_seconds = previous_stats.get("analysis_seconds", previous_stats.get("seconds", 0))
            total_seconds = analysis_seconds + application_seconds + verification_seconds
            pages = stats.get("pages")
            stats.update({key: previous_stats[key] for key in
                          ("extraction_seconds", "detection_seconds", "analysis_seconds", "analysis_seconds_per_page",
                           "ocr_pages", "hybrid_ocr_pages", "verification_ocr_pages", "analysis_level")
                          if key in previous_stats})
            stats.update(application_seconds=round(application_seconds, 4),
                         verification_seconds=round(verification_seconds, 4),
                         pipeline_seconds=round(total_seconds, 4),
                         pipeline_seconds_per_page=round(total_seconds / pages, 4) if pages else None,
                         measured_pages=pages)
            # El resultado del verificador se conserva, pero sus alertas no bloquean.
            # El flujo Regular todavía requiere la decisión administrativa.
            status = _completed_status(ver) if final_status == "protected" else final_status
            con.execute("UPDATE documents SET status=?, stats=?, verification=?, protected_at=? WHERE id=?",
                        (status, json.dumps(stats), json.dumps(ver), now(), doc_id))
            audit(con, approved_by, None, status,
                  {"document_id": doc_id, "file": doc["original_name"],
                   "treatment": treatment, "stats": stats, "verification_warning": not ver["ok"]})
        except Exception as e:  # noqa: BLE001
            con.rollback()
            con.execute("UPDATE documents SET status='failed', error=? WHERE id=?", (f"Error al proteger: {type(e).__name__}: {e}", doc_id))
            audit(con, approved_by, None, "process_failed", {"document_id": doc_id, "error": "PROTECT"})
        con.commit()
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Carga (múltiple + ZIP) y lotes
# ---------------------------------------------------------------------------

def _new_doc(con, user, name: str, tmp_path: Path, size: int, sha: str, document_type: str,
             entities: list[str], batch_id: str | None, analysis_level: str = "fast") -> dict:
    uploaded_ext = Path(name).suffix.lower().lstrip(".")
    # Normalizamos sólo alias completos. Un replace("tif", "tiff") también
    # modifica "tiff" y produce el formato inválido "tifff".
    ext = {"jpeg": "jpg", "tif": "tiff"}.get(uploaded_ext, uploaded_ext) or "bin"
    doc_id = uuid.uuid4().hex
    d = doc_dir(doc_id)
    d.mkdir(parents=True)
    os.replace(tmp_path, d / f"original.{ext}")  # mismo volumen: movimiento atómico, sin copia
    con.execute("INSERT INTO documents(id,batch_id,original_name,format,size_bytes,sha256,document_type,entities,analysis_level,status,uploaded_by,created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,'uploaded',?,?)",
                (doc_id, batch_id, Path(name).name, ext, size, sha,
                 document_type, json.dumps(entities), analysis_level, user["username"], now()))
    return {"id": doc_id, "name": Path(name).name, "status": "uploaded",
            "analysis_level": analysis_level}


class _LimitExceeded(Exception):
    pass


async def _spool_upload(up: UploadFile, dest: Path, limit: int) -> tuple[int, str]:
    """Escribe el archivo subido a disco en trozos de 1 MB, calculando SHA-256.
    Nunca acumula el archivo en RAM. Lanza _LimitExceeded si supera `limit`."""
    h, size = hashlib.sha256(), 0
    with dest.open("wb") as fh:
        while chunk := await up.read(MB):
            size += len(chunk)
            if size > limit:
                raise _LimitExceeded
            h.update(chunk)
            fh.write(chunk)
    return size, h.hexdigest()


def _spool_zip_entry(zf: zipfile.ZipFile, info, dest: Path, limit: int) -> tuple[int, str]:
    """Extrae una entrada del ZIP a disco con tope real de bytes descomprimidos
    (protege de ZIP bomba: no se confía en el tamaño declarado)."""
    h, size = hashlib.sha256(), 0
    with zf.open(info) as src, dest.open("wb") as fh:
        while chunk := src.read(MB):
            size += len(chunk)
            if size > limit:
                raise _LimitExceeded
            h.update(chunk)
            fh.write(chunk)
    return size, h.hexdigest()


def _check_backpressure(con):
    """Rechaza cargas nuevas cuando la cola o el disco no dan más (el cliente reintenta)."""
    max_queue = _env_int("MAX_QUEUE", 150)
    placeholders = ",".join("?" for _ in QUEUE_STATES)
    pending = con.execute(
        f"SELECT COUNT(*) n FROM documents WHERE status IN ({placeholders})", QUEUE_STATES
    ).fetchone()["n"]
    if pending >= max_queue:
        raise HTTPException(503, f"Cola de procesamiento llena ({pending} documentos en curso, máximo {max_queue}). "
                                 "Reintente en unos minutos.")
    if shutil.disk_usage(DATA_DIR).free < _env_int("MIN_FREE_DISK_MB", 500) * MB:
        raise HTTPException(507, "Espacio en disco insuficiente en el servidor")


@router.post("/api/v1/documents", status_code=201)
async def upload_documents(request: Request, files: list[UploadFile] = File(...),
                           document_type: str = Form("otro", description=DOCUMENT_TYPE_HELP),
                           entities: str = Form("", description=ENTITIES_HELP),
                           batch_name: str = Form(""),
                           analysis_level: str = Form("fast", description=ANALYSIS_HELP),
                           user=Depends(require("regular", "operador", "revisor", "aprobador", "administrador"))):
    max_request = _env_int("MAX_REQUEST_MB", 500) * MB      # total por solicitud (ZIP descomprimido incluido)
    max_files = _env_int("MAX_FILES_PER_REQUEST", 200)
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    con = db()
    tmp_files: list[Path] = []
    try:
        if not con.execute("SELECT 1 FROM document_types WHERE code=? AND active=1", (document_type,)).fetchone():
            raise HTTPException(400, "Tipo documental inválido")
        analysis_level = choice(analysis_level, pipeline.ANALYSIS_LEVELS, "Nivel de análisis inválido")
        _check_backpressure(con)
        if len(files) > max_files:
            raise HTTPException(413, f"Máximo {max_files} archivos por solicitud")
        codes = [c for c in entities.split(",") if c in engine.ENTITY_TYPES] or effective_entities(con, document_type)
        items: list[tuple[str, Path, int, str]] = []   # (nombre, ruta temporal, bytes, sha256)
        skipped: list[str] = []
        total = 0

        def budget() -> int:
            restante = max_request - total
            if restante <= 0:
                raise HTTPException(413, f"La solicitud supera el máximo total de {max_request // MB} MB (MAX_REQUEST_MB)")
            return min(MAX_UPLOAD, restante)

        for up in files:
            name = Path(up.filename or "documento").name
            tmp = TMP_DIR / uuid.uuid4().hex
            tmp_files.append(tmp)
            if name.lower().endswith(".zip"):
                try:
                    size, _ = await _spool_upload(up, tmp, budget())
                except _LimitExceeded:
                    skipped.append(f"{name} (supera el máximo)")
                    continue
                try:
                    zf = zipfile.ZipFile(tmp)
                except zipfile.BadZipFile:
                    skipped.append(f"{name} (ZIP corrupto)")
                    continue
                with zf:
                    entries = zf.infolist()
                    if len(entries) > max_files:
                        raise HTTPException(413, f"El ZIP contiene más de {max_files} archivos")
                    for info in entries:
                        base = Path(info.filename).name
                        if info.is_dir() or base.startswith(".") or "__MACOSX" in info.filename:
                            continue
                        if Path(base).suffix.lower() not in engine.SUPPORTED:
                            skipped.append(f"{base} (formato no soportado)")
                            continue
                        etmp = TMP_DIR / uuid.uuid4().hex
                        tmp_files.append(etmp)
                        try:
                            esize, esha = _spool_zip_entry(zf, info, etmp, budget())
                        except _LimitExceeded:
                            skipped.append(f"{base} (supera el máximo)")
                            continue
                        total += esize
                        items.append((base, etmp, esize, esha))
            elif Path(name).suffix.lower() in engine.SUPPORTED:
                try:
                    size, sha = await _spool_upload(up, tmp, budget())
                except _LimitExceeded:
                    skipped.append(f"{name} (supera el máximo)")
                    continue
                total += size
                items.append((name, tmp, size, sha))
            else:
                skipped.append(f"{name} (formato no soportado)")
        if not items:
            raise HTTPException(415, "Ningún archivo válido. " + ("; ".join(skipped) if skipped else ""))

        batch_id = None
        if batch_name or len(items) > 1:
            batch_id = uuid.uuid4().hex
            con.execute("INSERT INTO batches(id,name,document_type,created_by,created_at) VALUES(?,?,?,?,?)",
                        (batch_id, batch_name or f"Lote {now()[:16]}", document_type, user["username"], now()))
            audit(con, user["username"], ip_of(request), "batch_created",
                  {"batch_id": batch_id, "name": batch_name, "docs": len(items),
                   "analysis_level": analysis_level})

        docs = [_new_doc(con, user, name, tmp, size, sha, document_type, codes, batch_id, analysis_level)
                for name, tmp, size, sha in items]
        audit(con, user["username"], ip_of(request), "document_uploaded",
              {"files": [d["name"] for d in docs], "ids": [d["id"] for d in docs],
               "document_type": document_type, "analysis_level": analysis_level,
               "mb": round(total / MB, 1)})
        con.commit()
        for doc in docs:
            EXECUTOR.submit(process_document, doc["id"])
        return {"batch_id": batch_id, "documents": docs, "skipped": skipped}
    finally:
        con.close()
        for t in tmp_files:
            t.unlink(missing_ok=True)


@router.get("/api/v1/documents")
async def list_documents(status: str = "", batch_id: str = "", limit: int = 200, user=Depends(current_user)):
    con = db()
    try:
        q = ("SELECT d.*, (SELECT COUNT(*) FROM findings f WHERE f.document_id=d.id) AS n_findings, "
             "(SELECT COUNT(*) FROM findings f WHERE f.document_id=d.id AND f.status IN ('accepted','edited')) AS n_accepted "
             "FROM documents d WHERE 1=1")
        args: list = []
        if user["role"] in ("regular", "operador") or user.get("api"):
            q += " AND d.uploaded_by=?"
            args.append(user["username"])
        if status:
            q += " AND d.status=?"
            args.append(status)
        if batch_id:
            q += " AND d.batch_id=?"
            args.append(batch_id)
        q += " ORDER BY d.created_at DESC LIMIT ?"
        args.append(min(limit, 1000))
        return [dict(r) for r in con.execute(q, args)]
    finally:
        con.close()


@router.get("/api/v1/effective-entities")
async def get_effective_entities(document_type: str = "otro", user=Depends(current_user)):
    """Los tipos que el motor buscará de verdad con la política vigente. Lo consulta la
    pantalla de carga: el desplegable mostraba quince casillas marcadas que en modo
    «según política» no significaban nada, porque la lista real la decide el servidor."""
    con = db()
    try:
        return {"document_type": document_type, "entities": effective_entities(con, document_type)}
    finally:
        con.close()


@router.get("/api/v1/documents/{doc_id}")
async def get_document(doc_id: str, user=Depends(current_user)):
    con = db()
    try:
        doc = dict(_get_doc(con, doc_id, user))
        for k in ("stats", "verification"):
            doc[k] = json.loads(doc[k]) if doc[k] else None
        doc["entities"] = json.loads(doc["entities"])
        return doc
    finally:
        con.close()


@router.get("/api/v1/documents/{doc_id}/content")
async def get_content(doc_id: str, user=Depends(current_user)):
    con = db()
    try:
        doc = _get_doc(con, doc_id, user)
    finally:
        con.close()
    ext_path = doc_dir(doc_id) / "work" / "extraction.json"
    if not ext_path.exists():
        raise HTTPException(409, "El documento aún no ha sido procesado")
    e = json.loads(ext_path.read_text())
    if e["kind"] == "paged":
        return {"kind": "paged", "format": e["format"],
                "pages": [{"n": p["n"], "width": p["width"], "height": p["height"]} for p in e["pages"]]}
    return {"kind": "text", "format": e["format"],
            "blocks": [{"location": loc, "text": e["texts"][loc]} for loc in e["order"]]}


@router.get("/api/v1/documents/{doc_id}/pages/{n}/words")
async def get_page_words(doc_id: str, n: int, request: Request, q: str = "", user=Depends(current_user)):
    """Diagnóstico: cómo quedó troceada la página en palabras y qué texto ve el motor.

    Es lo único que explica un «no lo detectó» cuando el dato está a la vista en la página:
    si la extracción partió la palabra (`soporte@softrock` + `.cl`), el patrón nunca la ve
    entera. Sin esto hay que adivinar. `q` filtra las palabras que contienen ese texto.
    """
    con = db()
    try:
        _get_doc(con, doc_id, user)
        audit(con, user["username"], ip_of(request), "page_words_read", {"document_id": doc_id, "page": n})
        con.commit()
    finally:
        con.close()
    ext_path = doc_dir(doc_id) / "work" / "extraction.json"
    if not ext_path.exists():
        raise HTTPException(409, "El documento aún no ha sido procesado")
    e = json.loads(ext_path.read_text())
    if e["kind"] != "paged":
        raise HTTPException(400, "El documento no tiene páginas; use /content")
    page = next((p for p in e["pages"] if p["n"] == n), None)
    if page is None:
        raise HTTPException(404, "La página no existe")
    text, _ = engine._words_text_map([tuple(w) for w in page["words"]])
    words = page["words"]
    if q:
        words = [w for w in words if q.lower() in str(w[0]).lower()]
    return {"n": n, "ocr": n in e.get("ocr_pages", []), "total_words": len(page["words"]),
            "words": words, "text": text}


@router.get("/api/v1/documents/{doc_id}/pages/{n}.png")
async def get_page(doc_id: str, n: int, user=Depends(current_user)):
    con = db()
    try:
        doc = _get_doc(con, doc_id, user)
    finally:
        con.close()
    d = doc_dir(doc_id)
    p = d / "work" / "pages" / f"p{n}.png"
    if not pipeline.page_image_ok(p):
        # La imagen de trabajo falta o quedó incompleta: se regenera desde el
        # original para que la revisión no dependa de archivos temporales.
        regenerated = pipeline.render_page(d / f"original.{doc['format']}", d / "work", n)
        # Dos peticiones simultáneas pueden regenerar la misma página. Si otra
        # terminó primero, su archivo válido también satisface la solicitud.
        if not regenerated and not pipeline.page_image_ok(p):
            raise HTTPException(404, "Página no existe")
    return FileResponse(p, media_type="image/png")


# ---------------------------------------------------------------------------
# Hallazgos y revisión
# ---------------------------------------------------------------------------

def _reviewer(con, doc_id, user):
    doc = _get_doc(con, doc_id, user)
    if user["role"] == "auditor":
        raise HTTPException(403, "El rol auditor es de solo lectura")
    if user["role"] in ("regular", "operador") and doc["uploaded_by"] != user["username"]:
        raise HTTPException(403, "Solo puedes revisar tus propios documentos")
    return doc


def _require_editable(doc, con):
    if doc["status"] not in EDITABLE:
        raise HTTPException(409, f"El documento está en estado {doc['status']}, no en revisión")
    if doc["status"] == "protected_with_warnings":
        # Una edición invalida el resultado anterior hasta volver a generarlo.
        con.execute("UPDATE documents SET status='review', verification=NULL, protected_at=NULL WHERE id=?",
                    (doc["id"],))


@router.get("/api/v1/documents/{doc_id}/findings")
async def list_findings(doc_id: str, user=Depends(current_user)):
    con = db()
    try:
        _get_doc(con, doc_id, user)
        rows = []
        for r in con.execute("SELECT * FROM findings WHERE document_id=? ORDER BY page, location, start_off", (doc_id,)):
            f = dict(r)
            f["boxes"] = json.loads(f["boxes"]) if f["boxes"] else None
            rows.append(f)
        return rows
    finally:
        con.close()


@router.patch("/api/v1/documents/{doc_id}/findings/{fid}")
async def review_finding(request: Request, doc_id: str, fid: str, body: dict, user=Depends(current_user)):
    if body.get("status") not in ("accepted", "rejected"):
        raise HTTPException(400, "status debe ser accepted o rejected")
    con = db()
    try:
        doc = _reviewer(con, doc_id, user)
        _require_editable(doc, con)
        f = con.execute("SELECT * FROM findings WHERE id=? AND document_id=?", (fid, doc_id)).fetchone()
        if not f:
            raise HTTPException(404, "Hallazgo no existe")
        con.execute("UPDATE findings SET status=?, reviewed_by=?, reviewed_at=? WHERE id=?",
                    (body["status"], user["username"], now(), fid))
        audit(con, user["username"], ip_of(request), "finding_reviewed",
              {"document_id": doc_id, "finding_id": fid, "entity_code": f["entity_code"], "review_status": body["status"]})
        con.commit()
        return {"ok": True}
    finally:
        con.close()


@router.post("/api/v1/documents/{doc_id}/findings/bulk")
async def review_bulk(request: Request, doc_id: str, body: dict, user=Depends(current_user)):
    if body.get("status") not in ("accepted", "rejected"):
        raise HTTPException(400, "status debe ser accepted o rejected")
    con = db()
    try:
        doc = _reviewer(con, doc_id, user)
        _require_editable(doc, con)
        q, args = "UPDATE findings SET status=?, reviewed_by=?, reviewed_at=? WHERE document_id=?", \
                  [body["status"], user["username"], now(), doc_id]
        if body.get("entity_code"):
            q += " AND entity_code=?"
            args.append(body["entity_code"])
        n = con.execute(q, args).rowcount
        audit(con, user["username"], ip_of(request), "finding_reviewed",
              {"document_id": doc_id, "bulk": n, "entity_code": body.get("entity_code"), "review_status": body["status"]})
        con.commit()
        return {"updated": n}
    finally:
        con.close()


@router.post("/api/v1/documents/{doc_id}/findings", status_code=201)
async def add_finding(request: Request, doc_id: str, body: dict, user=Depends(current_user)):
    code = body.get("entity_code")
    if code not in engine.ENTITY_TYPES:
        raise HTTPException(400, "entity_code inválido")
    con = db()
    try:
        doc = _reviewer(con, doc_id, user)
        _require_editable(doc, con)
        fid = uuid.uuid4().hex
        if body.get("box"):  # paginado: [x0,y0,x1,y1] en píxeles de la página renderizada
            b = body["box"]
            if not (isinstance(b, list) and len(b) == 4 and b[2] > b[0] and b[3] > b[1]):
                raise HTTPException(400, "box inválido")
            con.execute("INSERT INTO findings(id,document_id,entity_code,text,score,page,boxes,source,status) "
                        "VALUES(?,?,?,?,1.0,?,?,'manual','accepted')",
                        (fid, doc_id, code, body.get("text") or "(manual)", int(body.get("page", 1)), json.dumps([b])))
        elif body.get("location") is not None:
            s, e = int(body.get("start", -1)), int(body.get("end", -1))
            if not (0 <= s < e):
                raise HTTPException(400, "start/end inválidos")
            con.execute("INSERT INTO findings(id,document_id,entity_code,text,score,location,start_off,end_off,source,status) "
                        "VALUES(?,?,?,?,1.0,?,?,?,'manual','accepted')",
                        (fid, doc_id, code, body.get("text") or "(manual)", body["location"], s, e))
        else:
            raise HTTPException(400, "Se requiere box (páginas) o location+start+end (texto)")
        audit(con, user["username"], ip_of(request), "finding_added",
              {"document_id": doc_id, "finding_id": fid, "entity_code": code})
        con.commit()
        return {"id": fid}
    finally:
        con.close()


@router.delete("/api/v1/documents/{doc_id}/findings/{fid}")
async def delete_manual_finding(request: Request, doc_id: str, fid: str, user=Depends(current_user)):
    """Quita un hallazgo manual, esté tachado o no tachado."""
    con = db()
    try:
        doc = _reviewer(con, doc_id, user)
        _require_editable(doc, con)
        finding = con.execute(
            "SELECT * FROM findings WHERE id=? AND document_id=?", (fid, doc_id)
        ).fetchone()
        if not finding:
            raise HTTPException(404, "Hallazgo no existe")
        if finding["source"] != "manual":
            raise HTTPException(409, "Solo se pueden eliminar hallazgos manuales")
        con.execute("DELETE FROM findings WHERE id=?", (fid,))
        audit(con, user["username"], ip_of(request), "finding_deleted",
              {"document_id": doc_id, "file": doc["original_name"],
               "finding_id": fid, "entity_code": finding["entity_code"]})
        con.commit()
        return {"ok": True}
    finally:
        con.close()


def _selected_treatment(body: dict) -> str:
    return choice(str((body or {}).get("treatment") or ""), pipeline.TREATMENTS,
                  "Debe seleccionar un tipo de tratamiento válido antes de continuar")


@router.post("/api/v1/documents/{doc_id}/approve")
async def approve(request: Request, doc_id: str, body: dict, user=Depends(current_user)):
    if user["role"] == "regular":
        raise HTTPException(403, "Un usuario Regular debe pasar el documento a revisión")
    treatment = _selected_treatment(body)
    con = db()
    try:
        doc = _reviewer(con, doc_id, user)
        if doc["status"] not in EDITABLE:
            raise HTTPException(409, f"No se puede aprobar en estado {doc['status']}")
        con.execute("UPDATE documents SET status='approved', treatment=? WHERE id=?", (treatment, doc_id))
        audit(con, user["username"], ip_of(request), "approved",
              {"document_id": doc_id, "file": doc["original_name"], "treatment": treatment})
        con.commit()
    finally:
        con.close()
    EXECUTOR.submit(protect_document, doc_id, user["username"])
    return {"ok": True, "status": "approved"}


# ---------------------------------------------------------------------------
# Flujo Regular → aprobación administrativa
# ---------------------------------------------------------------------------

@router.post("/api/v1/documents/{doc_id}/submit-review")
async def submit_for_approval(request: Request, doc_id: str,
                              body: dict,
                              user=Depends(require("regular"))):
    treatment = _selected_treatment(body)
    con = db()
    try:
        doc = _get_doc(con, doc_id, user)
        if not _is_regular_owner(user, doc):
            raise HTTPException(403, "Solo puedes enviar tus propios documentos")
        if doc["status"] not in EDITABLE:
            raise HTTPException(409, f"No se puede enviar en estado {doc['status']}")
        submitted_at = now()
        con.execute(
            "UPDATE documents SET status='submitted', treatment=?, submitted_at=?, submitted_by=?, "
            "decision_at=NULL, decision_by=NULL, decision_note=NULL WHERE id=?",
            (treatment, submitted_at, user["username"], doc_id),
        )
        audit(con, user["username"], ip_of(request), "submitted_for_approval",
              {"document_id": doc_id, "file": doc["original_name"],
               "submitted_at": submitted_at, "treatment": treatment})
        con.commit()
    finally:
        con.close()
    # Se genera el borrador protegido antes de la decisión. El administrador puede
    # revisarlo/descargarlo; el usuario Regular sólo accede después de aprobarse.
    EXECUTOR.submit(protect_document, doc_id, user["username"], "pending_approval")
    return {"ok": True, "status": "submitted"}


@router.get("/api/v1/pending-approvals")
async def pending_approvals(user=Depends(require("administrador"))):
    con = db()
    try:
        q = ("SELECT d.*, "
             "(SELECT COUNT(*) FROM findings f WHERE f.document_id=d.id) AS n_findings, "
             "(SELECT COUNT(*) FROM findings f WHERE f.document_id=d.id "
             " AND f.status IN ('accepted','edited')) AS n_accepted "
             "FROM documents d WHERE d.status IN ('submitted','protecting','pending_approval') "
             "ORDER BY d.submitted_at ASC")
        return [dict(r) for r in con.execute(q)]
    finally:
        con.close()


def _pending_doc(con, doc_id: str):
    doc = con.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
    if not doc:
        raise HTTPException(404, "Documento no existe")
    if doc["status"] != "pending_approval":
        raise HTTPException(409, "El documento todavía no está listo para una decisión")
    return doc


@router.post("/api/v1/pending-approvals/{doc_id}/approve")
async def approve_pending(request: Request, doc_id: str,
                          user=Depends(require("administrador"))):
    con = db()
    try:
        doc = _pending_doc(con, doc_id)
        decided_at = now()
        status = _completed_status(json.loads(doc["verification"]))
        con.execute(
            "UPDATE documents SET status=?, decision_at=?, decision_by=?, decision_note=NULL WHERE id=?",
            (status, decided_at, user["username"], doc_id),
        )
        audit(con, user["username"], ip_of(request), "approval_approved",
              {"document_id": doc_id, "file": doc["original_name"],
               "assigned_to": doc["uploaded_by"], "decision_at": decided_at,
               "treatment": doc["treatment"]})
        con.commit()
        return {"ok": True, "status": status}
    finally:
        con.close()


@router.post("/api/v1/pending-approvals/{doc_id}/reject")
async def reject_pending(request: Request, doc_id: str, body: dict,
                         user=Depends(require("administrador"))):
    con = db()
    try:
        doc = _pending_doc(con, doc_id)
        decided_at, note = now(), (body.get("note") or "").strip()
        con.execute(
            "UPDATE documents SET status='rejected', decision_at=?, decision_by=?, decision_note=? WHERE id=?",
            (decided_at, user["username"], note, doc_id),
        )
        audit(con, user["username"], ip_of(request), "approval_rejected",
              {"document_id": doc_id, "file": doc["original_name"],
               "assigned_to": doc["uploaded_by"], "note": note,
               "treatment": doc["treatment"]})
        con.commit()
        return {"ok": True, "status": "rejected"}
    finally:
        con.close()


@router.post("/api/v1/pending-approvals/{doc_id}/return")
async def return_pending(request: Request, doc_id: str, body: dict,
                         user=Depends(require("administrador"))):
    con = db()
    try:
        doc = _pending_doc(con, doc_id)
        decided_at, note = now(), (body.get("note") or "").strip()
        con.execute(
            "UPDATE documents SET status='review', protected_at=NULL, verification=NULL, "
            "decision_at=?, decision_by=?, decision_note=? WHERE id=?",
            (decided_at, user["username"], note, doc_id),
        )
        audit(con, user["username"], ip_of(request), "approval_returned",
              {"document_id": doc_id, "file": doc["original_name"],
               "assigned_to": doc["uploaded_by"], "note": note,
               "treatment": doc["treatment"]})
        con.commit()
    finally:
        con.close()
    (doc_dir(doc_id) / f"protected.{doc['format']}").unlink(missing_ok=True)
    return {"ok": True, "status": "review"}


@router.delete("/api/v1/pending-approvals/{doc_id}")
async def delete_pending(request: Request, doc_id: str,
                         user=Depends(require("administrador"))):
    con = db()
    try:
        doc = _pending_doc(con, doc_id)
        con.execute("DELETE FROM findings WHERE document_id=?", (doc_id,))
        con.execute("DELETE FROM documents WHERE id=?", (doc_id,))
        audit(con, user["username"], ip_of(request), "document_deleted",
              {"document_id": doc_id, "file": doc["original_name"],
               "assigned_to": doc["uploaded_by"], "treatment": doc["treatment"]})
        con.commit()
    finally:
        con.close()
    shutil.rmtree(doc_dir(doc_id), ignore_errors=True)
    return {"ok": True}


@router.get("/api/v1/documents/{doc_id}/protected")
async def download_protected(request: Request, doc_id: str, user=Depends(current_user)):
    con = db()
    try:
        doc = _get_doc(con, doc_id, user)
        if doc["status"] == "verification_failed":
            raise HTTPException(409, "La verificación automática falló: descarga bloqueada. Revise el informe.")
        admin_draft = user["role"] == "administrador" and doc["status"] in ("pending_approval", "rejected")
        if doc["status"] not in DOWNLOADABLE and not admin_draft:
            raise HTTPException(409, f"El documento no está protegido aún (estado: {doc['status']})")
        warning = _has_warning(doc)
        audit(con, user["username"], ip_of(request), "download",
              {"document_id": doc_id, "file": doc["original_name"], "treatment": doc["treatment"],
               "verification_warning": warning})
        con.commit()
    finally:
        con.close()
    p = doc_dir(doc_id) / f"protected.{doc['format']}"
    stem = Path(doc["original_name"]).stem
    suffix = "con_alertas" if warning else "protegido"
    return FileResponse(p, filename=f"{stem}_{suffix}.{doc['format']}", media_type="application/octet-stream",
                        headers={"X-Verification-Status": "warning" if warning else "ok"})


# ---------------------------------------------------------------------------
# Lotes
# ---------------------------------------------------------------------------

@router.get("/api/v1/batches")
async def list_batches(user=Depends(current_user)):
    con = db()
    try:
        q = "SELECT * FROM batches"
        args: list = []
        if user["role"] in ("regular", "operador") or user.get("api"):
            q += " WHERE created_by=?"
            args.append(user["username"])
        q += " ORDER BY created_at DESC LIMIT 200"
        out = []
        for b in con.execute(q, args):
            counts = {r["status"]: r["n"] for r in con.execute(
                "SELECT status, COUNT(*) n FROM documents WHERE batch_id=? GROUP BY status", (b["id"],))}
            out.append({**dict(b), "total": sum(counts.values()), "counts": counts})
        return out
    finally:
        con.close()


@router.get("/api/v1/batches/{batch_id}/protected.zip")
async def batch_zip(request: Request, batch_id: str, user=Depends(current_user)):
    con = db()
    try:
        docs = [d for d in con.execute("SELECT * FROM documents WHERE batch_id=? AND status IN ('protected','protected_with_warnings')", (batch_id,))
                if _can_see(user, d)]
        if not docs:
            raise HTTPException(409, "El lote no tiene documentos protegidos disponibles")
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for d in docs:
                p = doc_dir(d["id"]) / f"protected.{d['format']}"
                if p.exists():
                    suffix = "con_alertas" if _has_warning(d) else "protegido"
                    zf.write(p, f"{Path(d['original_name']).stem}_{suffix}.{d['format']}")
            warnings = [d["original_name"] for d in docs if _has_warning(d)]
            if warnings:
                zf.writestr("AVISO_DE_VERIFICACION.txt", VERIFICATION_DISCLAIMER + "\n\nArchivos con alertas:\n" + "\n".join(warnings))
        audit(con, user["username"], ip_of(request), "download", {"batch_id": batch_id, "zip": len(docs), "verification_warnings": len(warnings)})
        con.commit()
    finally:
        con.close()
    buf.seek(0)
    return StreamingResponse(buf, media_type="application/zip",
                             headers={"Content-Disposition": "attachment; filename=lote_protegido.zip"})


@router.get("/api/v1/batches/{batch_id}/report.csv")
async def batch_report(batch_id: str, user=Depends(current_user)):
    con = db()
    try:
        rows = [d for d in con.execute("SELECT * FROM documents WHERE batch_id=?", (batch_id,)) if _can_see(user, d)]
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["documento", "formato", "tratamiento", "estado", "paginas", "hallazgos_aceptados", "usuario", "creado", "protegido", "alerta_verificacion", "aviso"])
        for d in rows:
            n = con.execute("SELECT COUNT(*) n FROM findings WHERE document_id=? AND status IN ('accepted','edited')", (d["id"],)).fetchone()["n"]
            w.writerow([d["original_name"], d["format"], d["treatment"], d["status"], d["pages"], n,
                        d["uploaded_by"], d["created_at"], d["protected_at"],
                        "sí" if _has_warning(d) else "no", VERIFICATION_DISCLAIMER if _has_warning(d) else ""])
    finally:
        con.close()
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": "attachment; filename=reporte_lote.csv"})


# ---------------------------------------------------------------------------
# Tipos documentales y políticas
# ---------------------------------------------------------------------------

@router.get("/api/document-types")
async def list_types(user=Depends(current_user)):
    con = db()
    try:
        return [dict(r) for r in con.execute("SELECT code, name FROM document_types WHERE active=1 ORDER BY name")]
    finally:
        con.close()


@router.post("/api/document-types", status_code=201)
async def create_type(request: Request, body: dict, user=Depends(require("administrador"))):
    code = (body.get("code") or "").strip().lower().replace(" ", "_")
    name = (body.get("name") or "").strip()
    if not code.isidentifier() or not name:
        raise HTTPException(400, "code (identificador) y name son requeridos")
    con = db()
    try:
        con.execute("INSERT INTO document_types(code,name) VALUES(?,?) ON CONFLICT DO NOTHING", (code, name))
        audit(con, user["username"], ip_of(request), "document_type_created", {"code": code})
        con.commit()
        return {"ok": True}
    finally:
        con.close()


@router.get("/api/policies")
async def get_policies(user=Depends(require("administrador"))):
    con = db()
    try:
        types = ["*"] + [r["code"] for r in con.execute("SELECT code FROM document_types WHERE active=1 ORDER BY name")]
        rules = {(r["document_type"], r["entity_code"]): bool(r["enabled"]) for r in con.execute("SELECT * FROM policies")}
        matrix = {t: {c: rules.get((t, c), rules.get(("*", c), True)) for c in engine.ENTITY_TYPES} for t in types}
        return {"types": types, "entities": [{"code": c, "label": l} for c, l in engine.ENTITY_TYPES.items()],
                "matrix": matrix}
    finally:
        con.close()


@router.put("/api/policies")
async def put_policies(request: Request, body: dict, user=Depends(require("administrador"))):
    matrix = body.get("matrix") or {}
    con = db()
    try:
        con.execute("DELETE FROM policies")
        n = 0
        for t, ents in matrix.items():
            for c, enabled in ents.items():
                if c in engine.ENTITY_TYPES:
                    con.execute("INSERT INTO policies(document_type,entity_code,enabled) VALUES(?,?,?)", (t, c, int(bool(enabled))))
                    n += 1
        audit(con, user["username"], ip_of(request), "policy_updated", {"rules": n})
        con.commit()
        return {"ok": True, "rules": n}
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Mantención: recuperación tras reinicio + retención/limpieza
# ---------------------------------------------------------------------------

_maintenance_lock_fh = None  # el flock vive lo que vive el proceso


def requeue_pending() -> int:
    """Reencola documentos que quedaron a medias por un reinicio del servicio.

    El estado vive en Postgres y los archivos en /data, así que nada se pierde:
    lo que estaba en `uploaded/processing` se vuelve a procesar (idempotente) y
    lo que estaba en `approved/protecting` se vuelve a proteger.
    """
    con = db()
    try:
        for old in con.execute("SELECT * FROM documents WHERE status='verification_failed'").fetchall():
            if not old["verification"] or not (doc_dir(old["id"]) / f"protected.{old['format']}").is_file():
                continue
            status = "pending_approval" if old["submitted_at"] and not old["decision_at"] else _completed_status(json.loads(old["verification"]))
            con.execute("UPDATE documents SET status=? WHERE id=?", (status, old["id"]))
            audit(con, "sistema", None, "verification_warning_migrated",
                  {"document_id": old["id"], "status": status})
        redo_process = [r["id"] for r in con.execute(
            "SELECT id FROM documents WHERE status IN ('uploaded','processing')")]
        redo_regular = [r["id"] for r in con.execute(
            "SELECT id FROM documents WHERE status='submitted' OR "
            "(status='protecting' AND submitted_at IS NOT NULL AND decision_at IS NULL)")]
        redo_protect = [r["id"] for r in con.execute(
            "SELECT id FROM documents WHERE status='approved' OR "
            "(status='protecting' AND submitted_at IS NULL)")]
        con.execute("UPDATE documents SET status='uploaded' WHERE id = ANY(?)", (redo_process,))
        con.execute("UPDATE documents SET status='approved' WHERE id = ANY(?)", (redo_protect,))
        con.execute("UPDATE documents SET status='submitted' WHERE id = ANY(?)", (redo_regular,))
        con.commit()
        if redo_process or redo_protect or redo_regular:
            audit(con, "sistema", None, "requeued",
                  {"process": len(redo_process), "protect": len(redo_protect),
                   "regular_approval": len(redo_regular)})
    finally:
        con.close()
    for doc_id in redo_process:
        EXECUTOR.submit(process_document, doc_id)
    for doc_id in redo_protect:
        EXECUTOR.submit(protect_document, doc_id, "sistema")
    for doc_id in redo_regular:
        EXECUTOR.submit(protect_document, doc_id, "sistema", "pending_approval")
    return len(redo_process) + len(redo_protect) + len(redo_regular)


def _retention_pass() -> dict:
    """Borra temporales viejos y aplica retención de originales/protegidos."""
    removed = {"tmp": 0, "originals": 0, "protected": 0}
    cutoff_tmp = time.time() - 3600
    if TMP_DIR.exists():
        for f in TMP_DIR.iterdir():
            try:
                if f.stat().st_mtime < cutoff_tmp:
                    f.unlink()
                    removed["tmp"] += 1
            except OSError:
                pass
    con = db()
    try:
        days_o = _env_int("RETENTION_DAYS_ORIGINAL", 30)
        days_p = _env_int("RETENTION_DAYS_PROTECTED", 365)
        rows = con.execute("SELECT id, format, protected_at FROM documents "
                           "WHERE status IN ('protected','protected_with_warnings','verification_failed','failed') AND protected_at IS NOT NULL").fetchall()
        now_ts = time.time()
        for r in rows:
            try:
                from datetime import datetime
                age_days = (now_ts - datetime.fromisoformat(r["protected_at"]).timestamp()) / 86400
            except ValueError:
                continue
            d = doc_dir(r["id"])
            if age_days > days_o:
                orig = d / f"original.{r['format']}"
                if orig.exists() or (d / "work").exists():
                    orig.unlink(missing_ok=True)
                    shutil.rmtree(d / "work", ignore_errors=True)
                    removed["originals"] += 1
            if age_days > days_p:
                prot = d / f"protected.{r['format']}"
                if prot.exists():
                    prot.unlink()
                    removed["protected"] += 1
        if removed["originals"] or removed["protected"]:
            audit(con, "sistema", None, "retention", removed)
    finally:
        con.close()
    return removed


def _retention_loop():
    while True:
        time.sleep(3600)
        try:
            _retention_pass()
        except Exception:  # noqa: BLE001 — la limpieza nunca debe botar el proceso
            pass


def start_maintenance():
    """Con varios procesos web, sólo uno (el que gana el flock sobre /data) hace
    la recuperación y la retención; el lock se libera solo si el proceso muere."""
    global _maintenance_lock_fh
    if _maintenance_lock_fh is not None:
        return
    lock_path = DATA_DIR / ".maintenance.lock"
    fh = lock_path.open("w")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.close()
        return  # otro proceso web es el encargado
    _maintenance_lock_fh = fh
    try:
        requeue_pending()
    except Exception:  # noqa: BLE001
        pass
    threading.Thread(target=_retention_loop, daemon=True, name="retention").start()

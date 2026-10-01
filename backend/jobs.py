"""
Trabajos masivos desde buckets (acuerdo con SUBTEL para el acervo histórico).

POST /api/v1/jobs recibe las opciones (las mismas que elige una persona en la plataforma)
y una lista de documentos con URL prefirmada de lectura y de escritura. Sirve para S3,
MinIO, GCS o Azure sin SDK: el bucket firma, nosotros solo hacemos GET y PUT.

Cola en Postgres: cada proceso corre JOB_WORKERS hilos que toman ítems con
`FOR UPDATE SKIP LOCKED`, así que se escala agregando pods. Un ítem tomado queda con un
arriendo (JOB_LEASE_SECONDS); si el pod muere, otro lo retoma al vencer. El archivo se
procesa en un temporal que se borra al terminar: el pod no acumula documentos.
El original nunca se modifica. Sin revisión humana: si el verificador detecta datos
remanentes, la copia NO se sube y el ítem queda `verification_failed` para revisión.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import tempfile
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request

from . import engine, pipeline
from .app import MAX_UPLOAD, audit, db, ip_of, now, require, resolve_options

router = APIRouter()
MAX_ATTEMPTS = 3
TERMINAL = ("done", "verification_failed", "failed", "cancelled")
SUBMITTERS = ("operador", "revisor", "aprobador", "administrador")


def _env_int(name: str, default: int) -> int:
    return int(os.environ.get(name, str(default)))


class PermanentError(Exception):
    """Error que no mejora reintentando (URL vencida, formato no soportado, etc.)."""


def _allowed_url(url: str) -> bool:
    """Protección SSRF: el servidor solo habla con los hosts de bucket declarados."""
    allowed = {h.strip().lower() for h in os.environ.get("BUCKET_ALLOWED_HOSTS", "").split(",") if h.strip()}
    parts = urlsplit(url)
    return parts.scheme in ("http", "https") and (parts.hostname or "").lower() in allowed


def _host(url: str | None) -> str:
    return urlsplit(url or "").hostname or ""  # nunca registrar la URL completa: la firma es una credencial


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

@router.post("/api/v1/jobs", status_code=202, summary="Procesa en lote documentos que viven en un bucket")
async def create_job(request: Request, body: dict, user=Depends(require(*SUBMITTERS))):
    """Cuerpo JSON:

    ```json
    {"options": {"document_type": "resolucion", "entities": ["RUT", "NOMBRE"],
                 "treatment": "anonimizar", "analysis_level": "equilibrado"},
     "items": [{"source_url": "https://bucket/.../orig.pdf?firma", "target_url": "https://bucket/.../anon.pdf?firma",
                "target_headers": {"Content-Type": "application/pdf"}, "name": "orig.pdf", "ref": "id-externo"}]}
    ```
    `options` es opcional (mismos valores por defecto que la plataforma); `name` se deduce de la
    ruta de `source_url` si falta. Responde 202 con el id del trabajo; el estado se consulta en
    GET /api/v1/jobs/{id}.
    """
    items = body.get("items")
    max_items = _env_int("MAX_JOB_ITEMS", 1000)
    if not isinstance(items, list) or not items:
        raise HTTPException(400, "items debe ser una lista no vacía")
    if len(items) > max_items:
        raise HTTPException(413, f"Máximo {max_items} documentos por trabajo (MAX_JOB_ITEMS)")
    rows = []
    for i, it in enumerate(items):
        if not isinstance(it, dict):
            raise HTTPException(400, f"items[{i}] debe ser un objeto")
        src, dst = str(it.get("source_url") or ""), str(it.get("target_url") or "")
        if not _allowed_url(src) or not _allowed_url(dst):
            raise HTTPException(400, f"items[{i}]: source_url y target_url deben ser http(s) hacia un host de "
                                     "BUCKET_ALLOWED_HOSTS")
        name = Path(str(it.get("name") or urlsplit(src).path)).name
        if Path(name).suffix.lower() not in engine.SUPPORTED:
            raise HTTPException(415, f"items[{i}]: formato no soportado ({name or 'sin nombre'})")
        headers = it.get("target_headers") or {}
        if not isinstance(headers, dict) or not all(isinstance(v, str) for v in headers.values()):
            raise HTTPException(400, f"items[{i}]: target_headers debe ser un objeto de textos")
        rows.append((str(it.get("ref") or "")[:200] or None, name, src, dst, json.dumps(headers)))

    o = body.get("options") or {}
    entities = o.get("entities") or []
    con = db()
    try:
        opts = resolve_options(con, str(o.get("document_type") or "otro"),
                               entities.split(",") if isinstance(entities, str) else [str(e) for e in entities],
                               str(o.get("treatment") or "redact"), str(o.get("analysis_level") or "fast"))
        queued = con.execute("SELECT COUNT(*) n FROM job_items WHERE status IN ('queued','processing')").fetchone()["n"]
        max_queue = _env_int("MAX_JOB_QUEUE", 100_000)
        if queued + len(rows) > max_queue:
            raise HTTPException(503, f"Cola masiva llena ({queued} documentos pendientes, máximo {max_queue}). "
                                     "Reintente más tarde.")
        job_id = uuid.uuid4().hex
        con.execute("INSERT INTO jobs(id,created_by,options,created_at) VALUES(?,?,?,?)",
                    (job_id, user["username"], json.dumps(opts), now()))
        with con.cursor() as cur:
            cur.executemany("INSERT INTO job_items(job_id,ref,name,source_url,target_url,target_headers) "
                            "VALUES(%s,%s,%s,%s,%s,%s)", [(job_id, *r) for r in rows])
        audit(con, user["username"], ip_of(request), "job_created",
              {"job_id": job_id, "items": len(rows), **opts,
               "hosts": sorted({_host(r[2]) for r in rows} | {_host(r[3]) for r in rows})})
        return {"job_id": job_id, "items": len(rows), "options": opts}
    finally:
        con.close()


def _job_for(con, job_id: str, user):
    job = con.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    if not job:
        raise HTTPException(404, "Trabajo no existe")
    if job["created_by"] != user["username"] and user["role"] not in ("administrador", "auditor"):
        raise HTTPException(403, "Sin permiso sobre este trabajo")
    return job


@router.get("/api/v1/jobs/{job_id}")
async def get_job(job_id: str, status: str = "", limit: int = 1000, offset: int = 0,
                  user=Depends(require(*SUBMITTERS, "auditor"))):
    con = db()
    try:
        job = _job_for(con, job_id, user)
        counts = {r["status"]: r["n"] for r in con.execute(
            "SELECT status, COUNT(*) n FROM job_items WHERE job_id=? GROUP BY status", (job_id,))}
        q, args = ("SELECT id,ref,name,status,attempts,sha256,stats,verification,error,finished_at "
                   "FROM job_items WHERE job_id=?"), [job_id]
        if status:
            q += " AND status=?"
            args.append(status)
        q += " ORDER BY id LIMIT ? OFFSET ?"
        args += [min(limit, 5000), max(offset, 0)]
        items = []
        for r in con.execute(q, args):
            for k in ("stats", "verification"):
                r[k] = json.loads(r[k]) if r[k] else None
            items.append(r)
        return {"job_id": job_id, "created_by": job["created_by"], "created_at": job["created_at"],
                "options": json.loads(job["options"]), "counts": counts,
                "finished": not (counts.get("queued") or counts.get("processing")), "items": items}
    finally:
        con.close()


@router.delete("/api/v1/jobs/{job_id}")
async def cancel_job(request: Request, job_id: str, user=Depends(require(*SUBMITTERS))):
    """Cancela lo que aún no empieza; lo que está en proceso termina normalmente."""
    con = db()
    try:
        _job_for(con, job_id, user)
        n = con.execute("UPDATE job_items SET status='cancelled', source_url=NULL, target_url=NULL, finished_at=? "
                        "WHERE job_id=? AND status='queued'", (now(), job_id)).rowcount
        audit(con, user["username"], ip_of(request), "job_cancelled", {"job_id": job_id, "cancelled": n})
        return {"cancelled": n}
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Workers
# ---------------------------------------------------------------------------

def _claim(con):
    lease = time.time() + _env_int("JOB_LEASE_SECONDS", 1800)
    row = con.execute(
        "UPDATE job_items SET status='processing', attempts=attempts+1, lease_until=? "
        "WHERE id = (SELECT id FROM job_items WHERE status='queued' OR (status='processing' AND lease_until < ?) "
        "            ORDER BY id LIMIT 1 FOR UPDATE SKIP LOCKED) RETURNING *",
        (lease, time.time())).fetchone()
    con.commit()
    return row


def _download(url: str, dest: Path) -> str:
    h, size = hashlib.sha256(), 0
    with httpx.stream("GET", url, timeout=60, follow_redirects=False) as r:
        if r.status_code != 200:
            err = f"Descarga rechazada por el bucket ({r.status_code})"
            raise PermanentError(err) if 400 <= r.status_code < 500 else RuntimeError(err)
        with dest.open("wb") as fh:
            for chunk in r.iter_bytes(1048576):
                size += len(chunk)
                if size > MAX_UPLOAD:
                    raise PermanentError(f"Archivo supera el máximo de {MAX_UPLOAD // 1048576} MB")
                h.update(chunk)
                fh.write(chunk)
    return h.hexdigest()


def _upload(url: str, src: Path, headers: dict):
    with src.open("rb") as fh:
        r = httpx.put(url, content=fh, headers=headers, timeout=120, follow_redirects=False)
    if not 200 <= r.status_code < 300:
        err = f"El bucket rechazó la copia protegida ({r.status_code})"
        raise PermanentError(err) if 400 <= r.status_code < 500 else RuntimeError(err)


def _finish(con, item, user: str, status: str, event: str, **fields):
    """Cierra el ítem y borra las URL: son credenciales temporales que no deben quedar en la base."""
    sets = ", ".join(f"{k}=?" for k in fields)
    con.execute(f"UPDATE job_items SET status=?, source_url=NULL, target_url=NULL, finished_at=?"
                f"{', ' + sets if sets else ''} WHERE id=?", (status, now(), *fields.values(), item["id"]))
    detail = {k: (json.loads(v) if k in ("stats", "verification") and v else v) for k, v in fields.items()}
    audit(con, user, None, event,
          {"job_id": item["job_id"], "item": item["id"], "ref": item["ref"], "file": item["name"], **detail})


def process_item(con, item):
    from .documents import _persistent_pseudonym_map  # import diferido: documents importa app
    job = con.execute("SELECT options, created_by FROM jobs WHERE id=?", (item["job_id"],)).fetchone()
    opts, user = json.loads(job["options"]), job["created_by"]
    if item["attempts"] > MAX_ATTEMPTS:
        return _finish(con, item, user, "failed", "job_item_failed", error="Reintentos agotados")
    tmp = Path(tempfile.mkdtemp(prefix="job_"))
    ext = Path(item["name"]).suffix.lower()
    src, dst = tmp / ("in" + ext), tmp / ("out" + ext)
    sha = None
    try:
        sha = _download(item["source_url"], src)
        # Alias estables por trabajo: la misma persona recibe el mismo seudónimo en todo el lote.
        pseudonyms = lambda fs: _persistent_pseudonym_map(  # noqa: E731
            con, {"batch_id": item["job_id"], "id": item["id"]}, fs)
        stats, verification = pipeline.protect(src, dst, tmp / "work", opts["entities"], opts["treatment"],
                                               opts["analysis_level"], pseudonyms)
        if not verification["ok"]:
            return _finish(con, item, user, "verification_failed", "job_item_failed", sha256=sha,
                           stats=json.dumps(stats), verification=json.dumps(verification),
                           error="El verificador detectó datos remanentes: la copia no se subió")
        _upload(item["target_url"], dst, json.loads(item["target_headers"] or "{}"))
        _finish(con, item, user, "done", "job_item_protected", sha256=sha, stats=json.dumps(stats),
                verification=json.dumps(verification))
    except (PermanentError, engine.EngineError) as e:
        con.rollback()
        _finish(con, item, user, "failed", "job_item_failed", sha256=sha, error=str(e))
    except Exception as e:  # noqa: BLE001 — red caída, bucket 5xx: se reintenta
        con.rollback()
        if item["attempts"] >= MAX_ATTEMPTS:
            _finish(con, item, user, "failed", "job_item_failed", sha256=sha, error=f"{type(e).__name__}: {e}"[:500])
        else:
            con.execute("UPDATE job_items SET status='queued', lease_until=NULL, error=? WHERE id=?",
                        (f"Reintento: {type(e).__name__}"[:500], item["id"]))
            con.commit()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _worker_loop():
    while True:
        try:
            con = db()
            try:
                while item := _claim(con):
                    process_item(con, item)
            finally:
                con.close()
        except Exception:  # noqa: BLE001 — base caída o bug: se registra, el worker espera y sigue
            logging.getLogger(__name__).exception("worker de trabajos masivos")
        time.sleep(float(os.environ.get("JOB_POLL_SECONDS", "2")))


_started = False


def start_job_workers():
    """JOB_WORKERS hilos por proceso web (0 = este pod no procesa trabajos masivos)."""
    global _started
    if _started:
        return
    _started = True
    for i in range(_env_int("JOB_WORKERS", 1)):
        threading.Thread(target=_worker_loop, daemon=True, name=f"job-worker-{i}").start()

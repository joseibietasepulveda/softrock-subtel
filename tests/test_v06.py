"""v0.6: PDF/A de entrada y salida, paridad de opciones API ↔ plataforma, bitácora en Postgres."""
import io
import os
import re
import sys
import tempfile
from pathlib import Path

import pytest

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp())
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import psycopg  # noqa: E402
import pdfplumber  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from pdfminer.pdftypes import resolve1  # noqa: E402
from backend.app import app, db  # noqa: E402

PDFA = Path(__file__).parent / "corpus" / "resolucion_pdfa.pdf"


def _login(c):
    assert c.post("/api/auth/login", json={"username": "admin", "password": "Admin.2026"}).status_code == 200


def test_pdfa_entra_y_sale_como_pdfa():
    with TestClient(app) as c:
        _login(c)
        r = c.post("/api/v1/redact", files={"file": ("res.pdf", PDFA.read_bytes(), "application/pdf")},
                   data={"entities": "RUT,EMAIL", "analysis_level": "Rápido"})
        assert r.status_code == 200, r.text
    with pdfplumber.open(io.BytesIO(r.content)) as pdf:
        text = "".join(p.extract_text() or "" for p in pdf.pages)
        catalog = pdf.doc.catalog
        intent = resolve1(resolve1(catalog["OutputIntents"])[0])
        xmp = resolve1(catalog["Metadata"]).get_data()
    assert "12.345.678-5" not in text and "contacto@ejemplo.cl" not in text
    assert intent["S"].name == "GTS_PDFA1" and resolve1(intent["DestOutputProfile"])
    assert re.search(rb"pdfaid:part=.1.", xmp) and re.search(rb"pdfaid:conformance=.B.", xmp)
    assert "EmbeddedFiles" not in str(catalog.get("Names"))  # PDF/A-3 podría traer adjuntos: no pasan


def test_api_acepta_las_mismas_opciones_que_la_plataforma():
    body = b"RUT 12.345.678-5 y correo a@b.cl"
    with TestClient(app) as c:
        _login(c)
        assert c.put("/api/policies", json={"matrix": {"resolucion": {"EMAIL": False}}}).status_code == 200
        try:
            for level in ("exhaustivo", "Equilibrado", "rapido", "balanced"):
                r = c.post("/api/v1/redact", files={"file": ("x.txt", body, "text/plain")},
                           data={"document_type": "resolucion", "analysis_level": level})
                assert r.status_code == 200, (level, r.text)
                # la política de «resolucion» excluye EMAIL, igual que en la carga de la plataforma
                assert "12.345.678-5" not in r.content.decode() and "a@b.cl" in r.content.decode()
            r = c.post("/api/v1/redact", files={"file": ("x.txt", body, "text/plain")},
                       data={"treatment": "Anonimizar", "document_type": "otro"})
            assert r.status_code == 200 and "a@b.cl" not in r.content.decode()
            bad = [c.post("/api/v1/redact", files={"file": ("x.txt", body, "text/plain")}, data=d).status_code
                   for d in ({"analysis_level": "turbo"}, {"treatment": "borrar"}, {"document_type": "nope"})]
            assert bad == [422, 422, 400]
            up = c.post("/api/v1/documents", files=[("files", ("y.txt", body, "text/plain"))],
                        data={"analysis_level": "Exhaustivo"})
            assert up.status_code == 201 and up.json()["documents"][0]["analysis_level"] == "exhaustive"
        finally:
            c.put("/api/policies", json={"matrix": {}})
        assert "exhaustive (Exhaustivo)" in c.get("/openapi.json").text


def test_bitacora_inalterable_en_postgres():
    with TestClient(app) as c:
        _login(c)
    con = db()
    try:
        for sql in ("UPDATE audit_log SET event='x'", "DELETE FROM audit_log", "TRUNCATE audit_log"):
            with pytest.raises(psycopg.errors.RaiseException, match="inalterable"):
                con.execute(sql)
            con.rollback()
    finally:
        con.close()


def test_error_sql_en_procesamiento_deja_el_documento_fallido(monkeypatch):
    """En Postgres un error SQL aborta la transacción: el manejador debe hacer rollback
    antes de marcar `failed`, o el documento queda pegado en `processing`."""
    import time

    import backend.documents as documents
    dup = {"id": "dup", "entity_code": "RUT", "text": "x", "score": 1, "page": None, "boxes": None,
           "location": "txt", "start": 0, "end": 1, "source": "auto", "status": "accepted"}
    monkeypatch.setattr(documents.pipeline, "detect_findings", lambda *a: [dup, dup])
    with TestClient(app) as c:
        _login(c)
        did = c.post("/api/v1/documents", files=[("files", ("z.txt", b"hola", "text/plain"))]).json()["documents"][0]["id"]
        for _ in range(40):
            doc = c.get(f"/api/v1/documents/{did}").json()
            if doc["status"] not in ("uploaded", "processing"):
                break
            time.sleep(0.25)
    assert doc["status"] == "failed" and "UniqueViolation" in doc["error"]

"""Arreglos de resistencia: límites globales, backpressure, recuperación, auditoría concurrente."""
import io
import os
import sys
import tempfile
import threading
import time
import zipfile
from pathlib import Path

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp())
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient  # noqa: E402
from backend.app import app, audit, db  # noqa: E402  (backend.app primero: evita el import circular)
import backend.documents as documents  # noqa: E402
from backend import pipeline  # noqa: E402


def _login(c, u="admin", p="Admin.2026"):
    r = c.post("/api/auth/login", json={"username": u, "password": p})
    assert r.status_code == 200, r.text
    return r.json()


def _wait(c, doc_id, until, timeout=30):
    for _ in range(timeout * 4):
        st = c.get(f"/api/v1/documents/{doc_id}").json()["status"]
        if st in until:
            return st
        time.sleep(0.25)
    raise AssertionError(f"timeout esperando {until}, último estado: {st}")


def test_limite_total_por_solicitud(monkeypatch):
    monkeypatch.setenv("MAX_REQUEST_MB", "1")
    with TestClient(app) as c:
        _login(c)
        big = b"Sin datos. " * 65_000  # ~700 KB
        r = c.post("/api/v1/documents", files=[("files", ("a.txt", big, "text/plain")),
                                               ("files", ("b.txt", big, "text/plain"))])
        # el presupuesto total (1 MB) acepta el primero y omite el segundo, informándolo
        assert r.status_code == 201
        out = r.json()
        assert len(out["documents"]) == 1 and any("supera el máximo" in s for s in out["skipped"])


def test_zip_bomba_rechazada(monkeypatch):
    monkeypatch.setenv("MAX_REQUEST_MB", "1")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("bomba.txt", b"\x00" * (30 * 1024 * 1024))  # 30 MB → comprime a casi nada
    assert buf.tell() < 200_000
    with TestClient(app) as c:
        _login(c)
        r = c.post("/api/v1/documents", files=[("files", ("docs.zip", buf.getvalue(), "application/zip"))])
        # la entrada que se pasa del presupuesto se omite; sin entradas válidas → 415
        assert r.status_code in (413, 415)


def test_backpressure_cola_llena(monkeypatch):
    monkeypatch.setenv("MAX_QUEUE", "0")
    with TestClient(app) as c:
        _login(c)
        r = c.post("/api/v1/documents", files=[("files", ("x.txt", b"hola", "text/plain"))])
        assert r.status_code == 503 and "Cola" in r.json()["error"]["message"]


def test_requeue_recupera_documentos_atascados():
    with TestClient(app) as c:
        _login(c)
        r = c.post("/api/v1/documents", files=[("files", ("st.txt", b"RUT 12.345.678-5", "text/plain"))])
        did = r.json()["documents"][0]["id"]
        _wait(c, did, {"review"})
        # simula un reinicio a mitad de procesamiento
        con = db()
        con.execute("UPDATE documents SET status='processing' WHERE id=?", (did,))
        con.commit()
        con.close()
        n = documents.requeue_pending()
        assert n >= 1
        assert _wait(c, did, {"review"}) == "review"
        fs = c.get(f"/api/v1/documents/{did}/findings").json()
        assert len([f for f in fs if f["entity_code"] == "RUT"]) == 1  # sin duplicados


def test_auditoria_concurrente_no_rompe_cadena():
    def worker(i):
        con = db()
        for j in range(15):
            audit(con, f"hilo{i}", None, "stress", {"j": j})
        con.close()

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    with TestClient(app) as c:
        _login(c)
        assert c.get("/api/audit/verify").json() == {"ok": True}


def test_health_reporta_estado_real():
    with TestClient(app) as c:
        h = c.get("/api/health").json()
        assert h["db"] == "ok" and "queue" in h and h["disk_free_mb"] > 0
        assert h["ocr"] == "ok" and h["processing_workers"] >= 1
        assert h["db_engine"] == "postgresql"


def test_health_rechaza_despliegue_con_base_caida(monkeypatch):
    import backend.app as appmod
    import psycopg

    with TestClient(app) as c:
        def unavailable():
            raise psycopg.OperationalError("private connection details")
        monkeypatch.setattr(appmod, "db", unavailable)
        response = c.get("/api/health")
        assert response.status_code == 503
        assert response.json()["status"] == "degraded"
        assert "private connection details" not in response.text


def test_max_pages(monkeypatch, tmp_path):
    from PIL import Image
    monkeypatch.setenv("MAX_PAGES", "2")
    frames = [Image.new("RGB", (100, 100), "white") for _ in range(3)]
    src = tmp_path / "t.tiff"
    frames[0].save(src, save_all=True, append_images=frames[1:])
    try:
        pipeline.extract(src, tmp_path / "wd")
        raise AssertionError("debió rechazar por MAX_PAGES")
    except Exception as e:
        assert "MAX_PAGES" in str(e)

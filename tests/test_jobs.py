"""Trabajos masivos: bucket (URL prefirmadas) → cola Postgres → bucket."""
import io
import os
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp())
os.environ["JOB_POLL_SECONDS"] = "0.2"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pdfplumber  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from backend.app import app, db  # noqa: E402
from backend import pipeline  # noqa: E402

PDFA = Path(__file__).parent / "corpus" / "resolucion_pdfa.pdf"


class Bucket(BaseHTTPRequestHandler):
    """Bucket de juguete: GET/PUT por ruta. `fail[path] = [códigos]` responde esos códigos primero."""
    objects: dict = {}
    fail: dict = {}
    puts: dict = {}

    def _fail(self):
        codes = self.fail.get(self.path.split("?")[0])
        if codes:
            self.send_response(codes.pop(0))
            self.end_headers()
            return True
        return False

    def do_GET(self):  # noqa: N802
        if self._fail():
            return
        body = self.objects.get(self.path.split("?")[0])
        self.send_response(200 if body is not None else 404)
        self.end_headers()
        self.wfile.write(body or b"")

    def do_PUT(self):  # noqa: N802
        if self._fail():
            return
        data = self.rfile.read(int(self.headers["Content-Length"]))
        self.objects[self.path.split("?")[0]] = data
        self.puts[self.path.split("?")[0]] = dict(self.headers)
        self.send_response(200)
        self.end_headers()

    def log_message(self, *a):
        pass


@pytest.fixture
def bucket(monkeypatch):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Bucket)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setenv("BUCKET_ALLOWED_HOSTS", "127.0.0.1")
    Bucket.objects.clear(), Bucket.fail.clear(), Bucket.puts.clear()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


def _login(c):
    assert c.post("/api/auth/login", json={"username": "admin", "password": "Admin.2026"}).status_code == 200


def _wait(c, job_id, timeout=60):
    for _ in range(timeout * 5):
        job = c.get(f"/api/v1/jobs/{job_id}").json()
        if job["finished"]:
            return job
        time.sleep(0.2)
    raise AssertionError(f"trabajo sin terminar: {job['counts']}")


def test_trabajo_masivo_desde_bucket(bucket):
    Bucket.objects.update({"/orig/a.txt": b"RUT 12.345.678-5 correo a@b.cl", "/orig/b.pdf": PDFA.read_bytes(),
                           "/orig/c.txt": b"RUT 11.111.111-1", "/orig/d.txt": b"RUT 22.222.222-2"})
    Bucket.fail.update({"/orig/c.txt": [403], "/orig/d.txt": [503]})  # c: URL vencida · d: caída transitoria
    items = [{"source_url": f"{bucket}/orig/{n}?X-Amz-Signature=secreto", "target_url": f"{bucket}/anon/{n}?sig=s",
              "ref": f"ext-{n}"} for n in ("a.txt", "b.pdf", "c.txt", "d.txt")]
    items[1]["target_headers"] = {"Content-Type": "application/pdf"}
    with TestClient(app) as c:
        _login(c)
        r = c.post("/api/v1/jobs", json={"options": {"treatment": "anonimizar", "analysis_level": "equilibrado"},
                                         "items": items})
        assert r.status_code == 202, r.text
        assert r.json()["options"]["treatment"] == "anonymize"
        job = _wait(c, r.json()["job_id"])
        assert "secreto" not in c.get("/api/audit?limit=5000").text
        assert c.get("/api/audit/verify").json() == {"ok": True}
    by_ref = {i["ref"]: i for i in job["items"]}
    assert by_ref["ext-a.txt"]["status"] == "done" and by_ref["ext-b.pdf"]["status"] == "done"
    assert by_ref["ext-c.txt"]["status"] == "failed" and "403" in by_ref["ext-c.txt"]["error"]
    assert by_ref["ext-d.txt"]["status"] == "done" and by_ref["ext-d.txt"]["attempts"] == 2
    assert "12.345.678-5" not in Bucket.objects["/anon/a.txt"].decode()
    assert Bucket.objects["/orig/a.txt"] == b"RUT 12.345.678-5 correo a@b.cl"  # el original no se toca
    assert Bucket.puts["/anon/b.pdf"]["Content-Type"] == "application/pdf"
    with pdfplumber.open(io.BytesIO(Bucket.objects["/anon/b.pdf"])) as pdf:
        assert "OutputIntents" in pdf.doc.catalog  # sale como PDF/A
    con = db()
    left = con.execute("SELECT COUNT(*) n FROM job_items WHERE source_url IS NOT NULL OR target_url IS NOT NULL"
                       ).fetchone()["n"]
    con.close()
    assert left == 0  # las URL firmadas no quedan guardadas


def test_verificacion_fallida_no_sube_la_copia(bucket, monkeypatch):
    monkeypatch.setattr(pipeline, "verify", lambda dst, sup: {"ok": False, "checks": []})
    Bucket.objects["/orig/x.txt"] = b"RUT 12.345.678-5"
    with TestClient(app) as c:
        _login(c)
        r = c.post("/api/v1/jobs", json={"items": [{"source_url": f"{bucket}/orig/x.txt",
                                                    "target_url": f"{bucket}/anon/x.txt"}]})
        job = _wait(c, r.json()["job_id"])
    assert job["items"][0]["status"] == "verification_failed" and "/anon/x.txt" not in Bucket.objects


def test_seudonimos_estables_en_todo_el_trabajo(bucket):
    Bucket.objects.update({"/o/1.txt": b"RUT 12.345.678-5", "/o/2.txt": b"RUT 11.111.111-1 y RUT 12.345.678-5"})
    with TestClient(app) as c:
        _login(c)
        r = c.post("/api/v1/jobs", json={"options": {"treatment": "seudonimizar", "entities": ["RUT"]}, "items": [
            {"source_url": f"{bucket}/o/{n}", "target_url": f"{bucket}/a/{n}"} for n in ("1.txt", "2.txt")]})
        _wait(c, r.json()["job_id"])
    one, two = Bucket.objects["/a/1.txt"].decode(), Bucket.objects["/a/2.txt"].decode()
    alias = one.split()[-1]
    assert alias.startswith("RUT-") and two.count(alias) == 1 and "12.345.678-5" not in two


def test_solo_hosts_permitidos(bucket, monkeypatch):
    with TestClient(app) as c:
        _login(c)
        ok = {"source_url": f"{bucket}/a.txt", "target_url": f"{bucket}/b.txt"}
        for bad in ({**ok, "source_url": "http://169.254.169.254/latest/meta-data/x.txt"},
                    {**ok, "target_url": "file:///etc/passwd.txt"}, {**ok, "source_url": f"{bucket}/a.exe"}):
            assert c.post("/api/v1/jobs", json={"items": [bad]}).status_code in (400, 415)
        assert c.post("/api/v1/jobs", json={"items": [ok], "options": {"analysis_level": "turbo"}}).status_code == 422
        monkeypatch.setenv("BUCKET_ALLOWED_HOSTS", "")
        assert c.post("/api/v1/jobs", json={"items": [ok]}).status_code == 400

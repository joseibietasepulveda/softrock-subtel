"""API: login, RBAC, bitácora encadenada, tachado end-to-end. Ejecutar: .venv/bin/pytest -q"""
import os, sys, tempfile
from pathlib import Path

os.environ["DATA_DIR"] = tempfile.mkdtemp()
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient  # noqa: E402
from backend.app import app  # noqa: E402


def test_flujo_completo():
    with TestClient(app) as c:
        assert c.get("/api/me").status_code == 401
        assert c.post("/api/auth/login", json={"username": "admin", "password": "mala"}).status_code == 401
        r = c.post("/api/auth/login", json={"username": "admin", "password": "Admin.2026"})
        assert r.status_code == 200 and r.json()["role"] == "administrador"

        # usuarios y roles
        assert c.post("/api/users", json={"username": "ana", "role": "auditor", "password": "corta"}).status_code == 400
        assert c.post("/api/users", json={"username": "ana", "role": "auditor", "password": "Auditora2026"}).status_code == 201
        assert c.post("/api/users", json={"username": "ana", "role": "auditor", "password": "Auditora2026"}).status_code == 409

        # tachado
        r = c.post("/api/v1/redact", files={"file": ("x.txt", b"RUT 12.345.678-5 y correo a@b.cl", "text/plain")}, data={"entities": "RUT"})
        assert r.status_code == 200 and r.content.decode() == "RUT ████████████ y correo a@b.cl"
        assert "x_protegido.txt" in r.headers["content-disposition"]
        assert c.post("/api/v1/redact", files={"file": ("x.exe", b"..", "application/octet-stream")}).status_code == 415

        # token de API
        tok = c.post("/api/tokens", json={"name": "gestor"}).json()["token"]
        c2 = TestClient(app)
        r = c2.post("/api/v1/redact", headers={"Authorization": f"Bearer {tok}"}, files={"file": ("y.txt", b"a@b.cl", "text/plain")})
        assert r.status_code == 200 and r.content.decode() == "██████"
        assert c2.get("/api/users", headers={"Authorization": f"Bearer {tok}"}).status_code == 403

        # bitácora íntegra
        assert c.get("/api/audit/verify").json() == {"ok": True}
        events = [e["event"] for e in c.get("/api/audit").json()]
        assert {"login", "login_failed", "user_created", "protected", "token_created"} <= set(events)

        # RBAC: auditor no procesa, sí lee bitácora
        c3 = TestClient(app)
        assert c3.post("/api/auth/login", json={"username": "ana", "password": "Auditora2026"}).status_code == 200
        assert c3.post("/api/v1/redact", files={"file": ("z.txt", b"x", "text/plain")}).status_code == 403
        assert c3.get("/api/audit").status_code == 200
        assert c3.get("/api/users").status_code == 403

        # cambio de contraseña: ambas digitaciones se validan también en la API
        assert c.post("/api/users", json={"username": "cambio", "role": "operador", "password": "Original2026"}).status_code == 201
        c4 = TestClient(app)
        assert c4.post("/api/auth/login", json={"username": "cambio", "password": "Original2026"}).status_code == 200
        endpoint = "/api/me/password"
        assert c4.post(endpoint, json={"current": "Original2026", "current_confirm": "Distinta2026",
                                      "password": "Actualizada2026", "password_confirm": "Actualizada2026"}).status_code == 400
        assert c4.post(endpoint, json={"current": "Original2026", "current_confirm": "Original2026",
                                      "password": "Actualizada2026", "password_confirm": "Distinta2026"}).status_code == 400
        assert c4.post(endpoint, json={"current": "Original2026", "current_confirm": "Original2026",
                                      "password": "Actualizada2026", "password_confirm": "Actualizada2026"}).status_code == 200
        c4.post("/api/auth/logout")
        assert c4.post("/api/auth/login", json={"username": "cambio", "password": "Original2026"}).status_code == 401
        assert c4.post("/api/auth/login", json={"username": "cambio", "password": "Actualizada2026"}).status_code == 200

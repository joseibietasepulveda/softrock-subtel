"""Comprueba una instalación vacía y la persistencia de un documento tras reiniciar."""
import argparse
import json
import os
import time
from pathlib import Path

import httpx


def wait_ready(client):
    for _ in range(90):
        try:
            response = client.get("/api/health")
            if response.status_code == 200:
                assert response.json()["db_engine"] == "postgresql"
                return
        except httpx.HTTPError:
            pass
        time.sleep(2)
    raise AssertionError("La instalación no alcanzó un estado saludable")


def wait_document(client, doc_id, expected):
    for _ in range(120):
        response = client.get(f"/api/v1/documents/{doc_id}")
        response.raise_for_status()
        state = response.json()["status"]
        if state == expected:
            return
        assert state not in {"failed", "verification_failed"}, state
        time.sleep(0.5)
    raise AssertionError("El documento no alcanzó el estado esperado")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://localhost:8000")
    parser.add_argument("--state", required=True)
    parser.add_argument("--verify-restart", action="store_true")
    args = parser.parse_args()
    with httpx.Client(base_url=args.url, timeout=120) as client:
        wait_ready(client)
        response = client.post("/api/auth/login", json={"username": "admin", "password": os.environ["ADMIN_PASSWORD"]})
        response.raise_for_status()
        assert response.json()["role"] == "administrador"
        if args.verify_restart:
            doc_id = json.loads(Path(args.state).read_text())["document_id"]
        else:
            users = client.get("/api/users").json()
            assert not any(user["username"] == "regular" for user in users), "No debe crearse una cuenta Regular sin configuración"
            sample = b"RUT 12.345.678-5 y correo persona@example.test"
            response = client.post("/api/v1/redact", files={"file": ("prueba.txt", sample, "text/plain")},
                                   data={"entities": "RUT,EMAIL", "treatment": "redact"})
            response.raise_for_status()
            assert b"12.345.678-5" not in response.content and b"persona@example.test" not in response.content
            response = client.post("/api/v1/documents", files=[("files", ("prueba.txt", sample, "text/plain"))],
                                   data={"document_type": "oficio"})
            response.raise_for_status()
            doc_id = response.json()["documents"][0]["id"]
            wait_document(client, doc_id, "review")
            response = client.post(f"/api/v1/documents/{doc_id}/approve", json={"treatment": "redact"})
            response.raise_for_status()
            wait_document(client, doc_id, "protected")
            Path(args.state).write_text(json.dumps({"document_id": doc_id}))
        response = client.get(f"/api/v1/documents/{doc_id}/protected")
        response.raise_for_status()
        assert b"12.345.678-5" not in response.content and b"persona@example.test" not in response.content
        assert client.get("/api/audit/verify").json()["ok"] is True
    print("Instalación y persistencia: OK" if args.verify_restart else "Instalación y flujo completo: OK")


if __name__ == "__main__":
    main()

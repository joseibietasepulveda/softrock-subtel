"""Flujo v1.1 vía API: carga → revisión → aprobación → verificación → descarga; lotes/ZIP; políticas; LDAP."""
import io
import os
import sys
import tempfile
import time
import zipfile
from pathlib import Path

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp())
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient  # noqa: E402
import backend.app as appmod  # noqa: E402
import backend.documents as documents  # noqa: E402
from backend import pipeline  # noqa: E402
from backend.app import app  # noqa: E402


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


def test_flujo_revision_completo():
    with TestClient(app) as c:
        _login(c)
        r = c.post("/api/v1/documents", data={"document_type": "oficio", "batch_name": "Lote test"},
                   files=[("files", ("uno.txt", b"RUT 12.345.678-5 y correo a@b.cl", "text/plain")),
                          ("files", ("dos.txt", b"Telefono +56 9 8765 4321", "text/plain"))])
        assert r.status_code == 201, r.text
        out = r.json()
        batch_id = out["batch_id"]
        assert batch_id and len(out["documents"]) == 2
        d1, d2 = [d["id"] for d in out["documents"]]

        assert _wait(c, d1, {"review"}) == "review"
        _wait(c, d2, {"review"})

        # descarga bloqueada antes de aprobar
        assert c.get(f"/api/v1/documents/{d1}/protected").status_code == 409

        fs = c.get(f"/api/v1/documents/{d1}/findings").json()
        codes = {f["entity_code"] for f in fs}
        assert {"RUT", "EMAIL"} <= codes
        email = next(f for f in fs if f["entity_code"] == "EMAIL")
        assert c.patch(f"/api/v1/documents/{d1}/findings/{email['id']}", json={"status": "rejected"}).status_code == 200
        # hallazgo manual sobre el texto
        assert c.post(f"/api/v1/documents/{d1}/findings",
                      json={"entity_code": "NOMBRE", "location": "txt", "start": 0, "end": 3, "text": "RUT"}).status_code == 201

        assert c.post(f"/api/v1/documents/{d1}/approve", json={}).status_code == 422
        assert c.post(f"/api/v1/documents/{d1}/approve", json={"treatment": "redact"}).status_code == 200
        st = _wait(c, d1, {"protected", "verification_failed", "failed"})
        assert st == "protected", c.get(f"/api/v1/documents/{d1}").json()
        body = c.get(f"/api/v1/documents/{d1}/protected")
        assert body.status_code == 200
        text = body.content.decode()
        assert "12.345.678-5" not in text and "a@b.cl" in text and not text.startswith("RUT")
        doc = c.get(f"/api/v1/documents/{d1}").json()
        assert doc["verification"]["ok"] and doc["status"] == "protected"
        stats = doc["stats"]
        assert stats["analysis_seconds"] >= 0
        assert stats["application_seconds"] >= 0
        assert stats["verification_seconds"] >= 0
        assert abs(stats["pipeline_seconds"] - sum(stats[key] for key in
                   ("analysis_seconds", "application_seconds", "verification_seconds"))) < 0.001
        assert stats["measured_pages"] is None
        assert stats["pipeline_seconds_per_page"] is None

        # segundo documento: acepta todo por defecto y aprueba
        c.post(f"/api/v1/documents/{d2}/approve", json={"treatment": "redact"})
        _wait(c, d2, {"protected"})

        # lote: progreso, ZIP y CSV
        b = next(x for x in c.get("/api/v1/batches").json() if x["id"] == batch_id)
        assert b["total"] == 2 and b["counts"]["protected"] == 2
        z = c.get(f"/api/v1/batches/{batch_id}/protected.zip")
        assert z.status_code == 200
        names = zipfile.ZipFile(io.BytesIO(z.content)).namelist()
        assert sorted(names) == ["dos_protegido.txt", "uno_protegido.txt"]
        report = c.get(f"/api/v1/batches/{batch_id}/report.csv").text
        assert "uno.txt" in report and "tratamiento" in report and "redact" in report


def test_carga_zip_crea_lote():
    with TestClient(app) as c:
        _login(c)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("carpeta/a.txt", "correo x@y.cl")
            zf.writestr("b.txt", "RUT 12.345.678-5")
            zf.writestr(".oculto.txt", "no")
            zf.writestr("c.exe", "no")
        r = c.post("/api/v1/documents", files=[("files", ("docs.zip", buf.getvalue(), "application/zip"))])
        out = r.json()
        assert len(out["documents"]) == 2 and out["batch_id"]
        assert any("c.exe" in s for s in out["skipped"])


def test_nivel_de_analisis_se_persiste_e_informa_en_bitacora():
    with TestClient(app) as c:
        _login(c)
        invalid = c.post(
            "/api/v1/documents",
            data={"analysis_level": "demasiado"},
            files=[("files", ("nivel-invalido.txt", b"texto", "text/plain"))],
        )
        assert invalid.status_code == 422

        default = c.post(
            "/api/v1/documents",
            files=[("files", ("nivel-rapido.txt", b"RUT 12.345.678-5", "text/plain"))],
        )
        default_id = default.json()["documents"][0]["id"]
        assert _wait(c, default_id, {"review"}) == "review"
        assert c.get(f"/api/v1/documents/{default_id}").json()["analysis_level"] == "fast"

        selected = c.post(
            "/api/v1/documents",
            data={"analysis_level": "exhaustive"},
            files=[("files", ("nivel-exhaustivo.txt", b"correo a@b.cl", "text/plain"))],
        )
        selected_id = selected.json()["documents"][0]["id"]
        assert selected.json()["documents"][0]["analysis_level"] == "exhaustive"
        assert _wait(c, selected_id, {"review"}) == "review"
        document = c.get(f"/api/v1/documents/{selected_id}").json()
        assert document["analysis_level"] == "exhaustive"
        assert document["stats"]["analysis_level"] == "exhaustive"

        rows = c.get("/api/audit").json()
        uploaded = next(row for row in rows if "nivel-exhaustivo.txt" in row["details"])
        assert uploaded["analysis_level"] == "exhaustive"


def test_carga_tiff_conserva_extension_valida():
    from PIL import Image

    image = io.BytesIO()
    Image.new("RGB", (240, 120), "white").save(image, format="TIFF")
    with TestClient(app) as c:
        _login(c)
        response = c.post(
            "/api/v1/documents",
            files=[("files", ("imagen_ocr_03.tiff", image.getvalue(), "image/tiff"))],
        )
        assert response.status_code == 201, response.text
        doc_id = response.json()["documents"][0]["id"]
        assert _wait(c, doc_id, {"review"}) == "review"
        document = c.get(f"/api/v1/documents/{doc_id}").json()
        assert document["format"] == "tiff"


def test_seudonimos_consistentes_en_lote_aunque_se_protejan_en_distinto_orden():
    with TestClient(app) as c:
        _login(c)
        response = c.post(
            "/api/v1/documents",
            data={"batch_name": "Lote seudónimos"},
            files=[
                ("files", ("primero.txt", b"RUT 12.345.678-5", "text/plain")),
                ("files", ("segundo.txt", b"RUT 11.111.111-1 y RUT 12.345.678-5", "text/plain")),
            ],
        ).json()
        first, second = [item["id"] for item in response["documents"]]
        assert _wait(c, first, {"review"}) == "review"
        assert _wait(c, second, {"review"}) == "review"

        assert c.post(f"/api/v1/documents/{first}/approve",
                      json={"treatment": "pseudonymize"}).status_code == 200
        assert _wait(c, first, {"protected"}) == "protected"
        assert c.post(f"/api/v1/documents/{second}/approve",
                      json={"treatment": "pseudonymize"}).status_code == 200
        assert _wait(c, second, {"protected"}) == "protected"

        first_text = c.get(f"/api/v1/documents/{first}/protected").content.decode()
        second_text = c.get(f"/api/v1/documents/{second}/protected").content.decode()
        assert "RUT-01" in first_text and "RUT-01" in second_text and "RUT-02" in second_text

        con = appmod.db()
        stored = " ".join(str(value) for row in con.execute("SELECT * FROM pseudonym_aliases") for value in row.values())
        con.close()
        assert "12.345.678-5" not in stored and "11.111.111-1" not in stored


def test_regenera_imagen_de_pagina_incompleta():
    from PIL import Image

    image = io.BytesIO()
    Image.new("RGB", (320, 180), "white").save(image, format="PNG")
    with TestClient(app) as c:
        _login(c)
        response = c.post(
            "/api/v1/documents",
            files=[("files", ("pagina.png", image.getvalue(), "image/png"))],
        )
        doc_id = response.json()["documents"][0]["id"]
        assert _wait(c, doc_id, {"review"}) == "review"
        page = documents.doc_dir(doc_id) / "work" / "pages" / "p1.png"
        page.write_bytes(b"\x89PNG\r\n\x1a\narchivo-incompleto")
        assert not pipeline.page_image_ok(page)

        rendered = c.get(f"/api/v1/documents/{doc_id}/pages/1.png")
        assert rendered.status_code == 200
        assert rendered.content.startswith(b"\x89PNG\r\n\x1a\n")
        assert pipeline.page_image_ok(page)


def test_politicas_por_tipo_documental():
    with TestClient(app) as c:
        _login(c)
        p = c.get("/api/policies").json()
        assert p["matrix"]["*"]["RUT"] is True
        # contrato: solo RUT
        p["matrix"]["contrato"] = {e["code"]: e["code"] == "RUT" for e in p["entities"]}
        assert c.put("/api/policies", json={"matrix": p["matrix"]}).status_code == 200
        r = c.post("/api/v1/documents", data={"document_type": "contrato"},
                   files=[("files", ("k.txt", b"RUT 12.345.678-5 y correo a@b.cl", "text/plain"))])
        did = r.json()["documents"][0]["id"]
        with TestClient(app) as c2:
            _login(c2)
            _wait(c2, did, {"review"})
            codes = {f["entity_code"] for f in c2.get(f"/api/v1/documents/{did}/findings").json()}
        assert codes == {"RUT"}
        # la pantalla de carga puede saber, antes de subir nada, qué se buscará de verdad
        ef = c.get("/api/v1/effective-entities?document_type=contrato").json()
        assert ef["entities"] == ["RUT"]
        assert "EMAIL" in c.get("/api/v1/effective-entities?document_type=oficio").json()["entities"]
        # restaurar
        p["matrix"].pop("contrato")
        c.put("/api/policies", json={"matrix": p["matrix"]})
        assert "EMAIL" in c.get("/api/v1/effective-entities?document_type=contrato").json()["entities"]


def test_rbac_documentos():
    with TestClient(app) as c:
        _login(c)
        c.post("/api/users", json={"username": "op1", "role": "operador", "password": "Operador2026"})
        r = c.post("/api/v1/documents", files=[("files", ("mio.txt", b"a@b.cl", "text/plain"))])
        did = r.json()["documents"][0]["id"]
        _wait(c, did, {"review"})
    with TestClient(app) as c:
        _login(c, "op1", "Operador2026")
        # operador no ve documentos ajenos
        assert all(d["id"] != did for d in c.get("/api/v1/documents").json())
        assert c.get(f"/api/v1/documents/{did}").status_code == 403
        # la aprobación automática ya no forma parte del contrato ni cambia el flujo
        assert "auto_approve" not in str(c.get("/openapi.json").json())
        r = c.post("/api/v1/documents", data={"auto_approve": "1"},
                   files=[("files", ("suyo.txt", b"correo z@w.cl", "text/plain"))])
        did2 = r.json()["documents"][0]["id"]
        assert _wait(c, did2, {"review"}) == "review"


def test_flujo_regular_aprobaciones_y_bitacora():
    with TestClient(app) as regular, TestClient(app) as admin:
        _login(regular, "regular", "Regular.2026")
        _login(admin)

        # El usuario Regular trabaja el documento, incluido un hallazgo manual que
        # puede eliminar tanto si estaba aceptado como rechazado.
        r = regular.post("/api/v1/documents",
                         files=[("files", ("solicitud_regular.txt", b"correo regular@subtel.cl", "text/plain"))])
        doc_id = r.json()["documents"][0]["id"]
        assert _wait(regular, doc_id, {"review"}) == "review"
        manual = regular.post(
            f"/api/v1/documents/{doc_id}/findings",
            json={"entity_code": "NOMBRE", "location": "txt", "start": 0, "end": 6, "text": "correo"},
        ).json()["id"]
        assert regular.patch(
            f"/api/v1/documents/{doc_id}/findings/{manual}", json={"status": "rejected"}
        ).status_code == 200
        assert regular.delete(f"/api/v1/documents/{doc_id}/findings/{manual}").status_code == 200

        assert regular.post(f"/api/v1/documents/{doc_id}/submit-review", json={}).status_code == 422
        assert regular.post(f"/api/v1/documents/{doc_id}/submit-review",
                            json={"treatment": "anonymize"}).status_code == 200
        assert _wait(regular, doc_id, {"pending_approval", "verification_failed"}) == "pending_approval"
        pending = admin.get("/api/v1/pending-approvals").json()
        assert any(d["id"] == doc_id and d["uploaded_by"] == "regular" for d in pending)

        # El borrador es descargable por el administrador, no por el Regular.
        assert regular.get(f"/api/v1/documents/{doc_id}/protected").status_code == 409
        assert admin.get(f"/api/v1/documents/{doc_id}/protected").status_code == 200
        assert admin.post(f"/api/v1/pending-approvals/{doc_id}/approve").status_code == 200
        assert regular.get(f"/api/v1/documents/{doc_id}/protected").status_code == 200

        # Devolver permite seguir editando; rechazar cierra el flujo sin habilitar
        # la descarga del usuario asignado.
        r = regular.post("/api/v1/documents",
                         files=[("files", ("para_devolver.txt", b"RUT 12.345.678-5", "text/plain"))])
        returned_id = r.json()["documents"][0]["id"]
        _wait(regular, returned_id, {"review"})
        regular.post(f"/api/v1/documents/{returned_id}/submit-review", json={"treatment": "redact"})
        _wait(regular, returned_id, {"pending_approval"})
        assert admin.post(f"/api/v1/pending-approvals/{returned_id}/return",
                          json={"note": "Corregir categoría"}).json()["status"] == "review"
        assert regular.get(f"/api/v1/documents/{returned_id}").json()["status"] == "review"
        regular.post(f"/api/v1/documents/{returned_id}/submit-review", json={"treatment": "mask_partial"})
        _wait(regular, returned_id, {"pending_approval"})
        assert admin.post(f"/api/v1/pending-approvals/{returned_id}/reject",
                          json={"note": "No corresponde"}).json()["status"] == "rejected"
        assert regular.get(f"/api/v1/documents/{returned_id}/protected").status_code == 409

        # Eliminar quita el documento, pero conserva el hecho en la bitácora.
        r = regular.post("/api/v1/documents",
                         files=[("files", ("para_eliminar.txt", b"correo borrar@subtel.cl", "text/plain"))])
        deleted_id = r.json()["documents"][0]["id"]
        _wait(regular, deleted_id, {"review"})
        regular.post(f"/api/v1/documents/{deleted_id}/submit-review", json={"treatment": "mask_full"})
        _wait(regular, deleted_id, {"pending_approval"})
        assert admin.delete(f"/api/v1/pending-approvals/{deleted_id}").status_code == 200
        assert regular.get(f"/api/v1/documents/{deleted_id}").status_code == 404

        audit_rows = admin.get("/api/audit").json()
        events = {row["event"] for row in audit_rows}
        assert {"submitted_for_approval", "approval_approved", "approval_returned",
                "approval_rejected", "document_deleted", "download"} <= events
        submitted = next(row for row in audit_rows if row["event"] == "submitted_for_approval"
                         and "solicitud_regular.txt" in row["details"])
        detail = __import__("json").loads(submitted["details"])
        assert detail["documento"] == "solicitud_regular.txt" and "document_id" not in detail
        assert detail["treatment"] == "anonymize" and submitted["treatment"] == "anonymize"
        assert submitted["ts"].endswith("-03:00")


def test_login_ldap(monkeypatch):
    monkeypatch.setattr(appmod, "ldap_authenticate",
                        lambda u, p: {"display_name": "Usuaria LDAP", "role": "revisor"} if (u, p) == ("mvallejos", "SecretaAD1") else None)
    with TestClient(app) as c:
        assert c.post("/api/auth/login", json={"username": "mvallejos", "password": "mala"}).status_code == 401
        me = _login(c, "mvallejos", "SecretaAD1")
        assert me["role"] == "revisor" and me["display_name"] == "Usuaria LDAP"
    with TestClient(app) as c:
        _login(c)
        row = next(u for u in c.get("/api/users").json() if u["username"] == "mvallejos")
        assert row["auth_source"] == "ldap"


def test_diagnostico_de_palabras_de_la_pagina():
    """Sin esto, un «no lo detectó» sobre un dato visible en la página no se puede
    explicar: hay que poder ver cómo quedó troceado el texto que llegó al motor."""
    from PIL import Image, ImageDraw, ImageFont
    import tempfile as tf
    img = Image.new("RGB", (1400, 300), "white")
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("DejaVuSans.ttf", 60)
    except OSError:
        font = ImageFont.load_default(size=60)
    draw.text((40, 100), "Canal: soporte@softrock.cl", fill="black", font=font)
    ruta = Path(tf.mkdtemp()) / "pagina.png"
    img.save(ruta)

    with TestClient(app) as c:
        _login(c)
        r = c.post("/api/v1/documents", data={"document_type": "otro"},
                   files=[("files", ("pagina.png", ruta.read_bytes(), "image/png"))])
        did = r.json()["documents"][0]["id"]
        _wait(c, did, {"review"})
        d = c.get(f"/api/v1/documents/{did}/pages/1/words").json()
        assert d["ocr"] is True and d["total_words"] >= 2
        assert "Canal" in d["text"]
        assert c.get(f"/api/v1/documents/{did}/pages/1/words?q=canal").json()["words"]
        assert c.get(f"/api/v1/documents/{did}/pages/9/words").status_code == 404


def _upload_with_residual_name(client, *, batch=False):
    """Una aparición rechazada queda en el resultado y dispara el verificador real."""
    r = client.post(
        "/api/v1/documents", data={"entities": "NOMBRE", "batch_name": "Alertas" if batch else ""},
        files=[("files", ("nombres.txt", b"Jean Pierre Matus\nJean Pierre Matus", "text/plain"))],
    )
    assert r.status_code == 201
    doc_id = r.json()["documents"][0]["id"]
    _wait(client, doc_id, {"review"})
    fs = client.get(f"/api/v1/documents/{doc_id}/findings").json()
    assert len(fs) == 2
    client.patch(f"/api/v1/documents/{doc_id}/findings/{fs[-1]['id']}", json={"status": "rejected"})
    return doc_id, r.json()["batch_id"], fs


def test_alerta_no_bloquea_descarga_lote_y_editar_invalida_resultado():
    with TestClient(app) as c:
        _login(c)
        did, bid, fs = _upload_with_residual_name(c, batch=True)
        assert c.post(f"/api/v1/documents/{did}/approve", json={"treatment": "anonymize"}).status_code == 200
        assert _wait(c, did, {"protected_with_warnings", "failed"}) == "protected_with_warnings"
        doc = c.get(f"/api/v1/documents/{did}").json()
        assert not doc["verification"]["ok"]
        assert "Jean Pierre Matus" in doc["verification"]["checks"][0]["detail"]
        downloaded = c.get(f"/api/v1/documents/{did}/protected")
        assert downloaded.status_code == 200
        assert downloaded.headers["X-Verification-Status"] == "warning"
        assert "con_alertas" in downloaded.headers["Content-Disposition"]
        assert downloaded.text == "[NOMBRE]\nJean Pierre Matus"
        archive = c.get(f"/api/v1/batches/{bid}/protected.zip")
        assert archive.status_code == 200
        with zipfile.ZipFile(io.BytesIO(archive.content)) as z:
            assert "nombres_con_alertas.txt" in z.namelist()
            assert documents.VERIFICATION_DISCLAIMER in z.read("AVISO_DE_VERIFICACION.txt").decode()
        report = c.get(f"/api/v1/batches/{bid}/report.csv").text
        assert "protected_with_warnings" in report and documents.VERIFICATION_DISCLAIMER in report
        events = c.get("/api/audit").json()
        import json
        assert any(e["event"] == "download" and json.loads(e["details"]).get("documento") == "nombres.txt"
                   and json.loads(e["details"]).get("verification_warning") for e in events)
        # Al corregir el hallazgo pendiente, no se puede descargar el resultado viejo.
        assert c.patch(f"/api/v1/documents/{did}/findings/{fs[-1]['id']}", json={"status": "accepted"}).status_code == 200
        assert c.get(f"/api/v1/documents/{did}/protected").status_code == 409
        c.post(f"/api/v1/documents/{did}/approve", json={"treatment": "anonymize"})
        assert _wait(c, did, {"protected", "failed"}) == "protected"
        assert c.get(f"/api/v1/documents/{did}/protected").headers["X-Verification-Status"] == "ok"


def test_alerta_regular_conserva_aprobacion_y_migra_bloqueos_anteriores():
    with TestClient(app) as regular, TestClient(app) as admin:
        _login(regular, "regular", "Regular.2026")
        _login(admin)
        did, _, _ = _upload_with_residual_name(regular)
        regular.post(f"/api/v1/documents/{did}/submit-review", json={"treatment": "anonymize"})
        assert _wait(regular, did, {"pending_approval", "failed"}) == "pending_approval"
        assert not regular.get(f"/api/v1/documents/{did}").json()["verification"]["ok"]
        assert regular.get(f"/api/v1/documents/{did}/protected").status_code == 409
        assert admin.get(f"/api/v1/documents/{did}/protected").headers["X-Verification-Status"] == "warning"
        # Un bloqueo heredado del flujo Regular vuelve a aprobación, no a descarga.
        con = appmod.db()
        con.execute("UPDATE documents SET status='verification_failed' WHERE id=?", (did,))
        con.commit()
        con.close()
        documents.requeue_pending()
        assert regular.get(f"/api/v1/documents/{did}").json()["status"] == "pending_approval"
        assert regular.get(f"/api/v1/documents/{did}/protected").status_code == 409
        decision = admin.post(f"/api/v1/pending-approvals/{did}/approve")
        assert decision.json()["status"] == "protected_with_warnings"
        assert regular.get(f"/api/v1/documents/{did}/protected").status_code == 200
        # Un resultado con decisión previa migra a descarga con alertas.
        con = appmod.db()
        con.execute("UPDATE documents SET status='verification_failed' WHERE id=?", (did,))
        con.commit()
        con.close()
        documents.requeue_pending()
        assert regular.get(f"/api/v1/documents/{did}").json()["status"] == "protected_with_warnings"

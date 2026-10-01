"""Login con Keycloak (OIDC): el servidor de Keycloak se simula; se prueba la validación real de claims y roles."""
import base64
import json
import os
import sys
import tempfile
import time
import urllib.parse
from pathlib import Path

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp())
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
import backend.app as appmod  # noqa: E402
from backend.app import app  # noqa: E402

ISS = "https://kc.subtel.test/realms/subtel"
META = {"issuer": ISS, "authorization_endpoint": ISS + "/auth", "token_endpoint": ISS + "/token",
        "end_session_endpoint": ISS + "/logout"}


def _jwt(claims):
    enc = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()  # noqa: E731
    return f"{enc({'alg': 'RS256'})}.{enc(claims)}.firma"


@pytest.fixture
def kc(monkeypatch):
    """Keycloak falso: devuelve tokens con el nonce que la app mandó al /auth."""
    monkeypatch.setenv("OIDC_ISSUER", ISS)
    monkeypatch.setenv("OIDC_CLIENT_ID", "sellodoc")
    monkeypatch.setattr(appmod, "_oidc_meta_cache", dict(META))
    state = {"roles": ["revisor"], "aud": "sellodoc", "sent": None}

    def fake_http(url, data=None):
        assert url == META["token_endpoint"] and data["code"] == "cod-1" and data["code_verifier"]
        state["sent"] = data
        idt = {"iss": ISS, "aud": state["aud"], "nonce": state["nonce"], "exp": time.time() + 60,
               "sub": "abc", "preferred_username": "Usuario@institucion.test", "name": "Usuario de Prueba"}
        at = {"resource_access": {"sellodoc": {"roles": state["roles"]}}, "realm_access": {"roles": ["offline_access"]}}
        return {"id_token": _jwt(idt), "access_token": _jwt(at)}
    monkeypatch.setattr(appmod, "_http_json", fake_http)
    return state


def _login(c, kc, state_override=None):
    r = c.get("/api/auth/oidc/login", follow_redirects=False)
    assert r.status_code == 302
    q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(r.headers["location"]).query))
    assert q["code_challenge_method"] == "S256" and q["client_id"] == "sellodoc"
    kc["nonce"] = q["nonce"]
    return c.get("/api/auth/oidc/callback", params={"code": "cod-1", "state": state_override or q["state"]},
                 follow_redirects=False)


def test_oidc_login_mapea_rol_y_crea_sesion(kc):
    kc["roles"] = ["revisor", "aprobador"]  # gana el de más privilegio
    with TestClient(app) as c:
        assert c.get("/api/auth/oidc").json()["enabled"] is True
        r = _login(c, kc)
        assert r.status_code == 302 and r.headers["location"] == "/"
        me = c.get("/api/me").json()
        assert me["username"] == "usuario@institucion.test" and me["role"] == "aprobador"
        out = c.post("/api/auth/logout").json()
        assert out["logout_url"].startswith(META["end_session_endpoint"] + "?client_id=sellodoc")
        assert c.get("/api/me").status_code == 401


def test_oidc_rechaza_state_audience_y_sin_rol(kc):
    with TestClient(app) as c:
        assert _login(c, kc, state_override="otro").headers["location"] == "/?login_error=keycloak"
        kc["aud"] = "otra-app"
        assert _login(c, kc).headers["location"] == "/?login_error=keycloak"
        kc["aud"], kc["roles"] = "sellodoc", ["offline_access"]
        assert _login(c, kc).headers["location"] == "/?login_error=keycloak"
        assert c.get("/api/me").status_code == 401


def test_oidc_deshabilitado_sin_variables(monkeypatch):
    monkeypatch.delenv("OIDC_ISSUER", raising=False)
    with TestClient(app) as c:
        assert c.get("/api/auth/oidc").json()["enabled"] is False
        assert c.get("/api/auth/oidc/login", follow_redirects=False).status_code == 404


def test_oidc_role_map_traduce_roles_propios_de_subtel(kc, monkeypatch):
    monkeypatch.setenv("OIDC_ROLE_MAP", '{"abogado-dj": "revisor", "jefatura-dj": "aprobador"}')
    kc["roles"] = ["abogado-dj"]
    with TestClient(app) as c:
        assert _login(c, kc).headers["location"] == "/"
        assert c.get("/api/me").json()["role"] == "revisor"

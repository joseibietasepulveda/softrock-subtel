"""Base Postgres limpia por sesión de pytest. DATABASE_URL debe apuntar a una base `*_test`:
el esquema `public` se borra entero al empezar."""
import os

import psycopg

if not os.environ.get("DATABASE_URL"):
    raise RuntimeError("Las pruebas requieren DATABASE_URL hacia una base desechable cuyo nombre termine en _test")
# Valores exclusivos de las pruebas: el producto no contiene credenciales predeterminadas.
os.environ["ADMIN_PASSWORD"] = "Admin.2026"
os.environ["REGULAR_PASSWORD"] = "Regular.2026"
_url = os.environ["DATABASE_URL"]
assert psycopg.conninfo.conninfo_to_dict(_url).get("dbname", "").endswith("_test"), \
    "Las pruebas borran el esquema public: DATABASE_URL debe apuntar a una base cuyo nombre termine en _test"
try:
    with psycopg.connect(_url, autocommit=True) as _c:
        _c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
except psycopg.OperationalError:
    pass  # sin Postgres: las pruebas del motor corren igual; las de la API fallan al conectar

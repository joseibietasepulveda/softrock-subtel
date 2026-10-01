"""Instalación nueva: credenciales explícitas, creación opcional y reinicio seguro."""
import os
import subprocess
import sys
import uuid

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo

from backend.app import check_password


@pytest.fixture
def empty_install(tmp_path):
    schema = "bootstrap_" + uuid.uuid4().hex
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        url = make_conninfo(os.environ["DATABASE_URL"], options=f"-c search_path={schema}")
        env = {**os.environ, "DATABASE_URL": url, "DATA_DIR": str(tmp_path), "EXPLICIT_RULES_FILE": ""}
        for name in ("ADMIN_PASSWORD", "REGULAR_PASSWORD", "MIGRATE_SQLITE_PATH"):
            env.pop(name, None)
        yield url, env
        connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def initialize(env):
    return subprocess.run([sys.executable, "-c", "from backend.app import init_db; init_db()"],
                          env=env, capture_output=True, text=True)


def test_no_initial_account_without_explicit_password(empty_install):
    url, env = empty_install
    result = initialize(env)
    assert result.returncode != 0 and "Configure ADMIN_PASSWORD" in result.stderr
    with psycopg.connect(url) as connection:
        assert connection.execute("SELECT to_regclass('users')").fetchone()[0] is None


def test_optional_regular_and_restart_preserve_password(empty_install):
    url, env = empty_install
    password = "PruebaInicial2026"
    env["ADMIN_PASSWORD"] = password
    assert initialize(env).returncode == 0
    env["ADMIN_PASSWORD"] = "OtraPrueba2026"
    assert initialize(env).returncode == 0
    with psycopg.connect(url) as connection:
        users = connection.execute("SELECT username,password_hash FROM users").fetchall()
        assert len(users) == 1 and users[0][0] == "admin"
        assert check_password(password, users[0][1])
        assert not check_password(env["ADMIN_PASSWORD"], users[0][1])


def test_invalid_regular_rolls_back_initialization(empty_install):
    _, env = empty_install
    env.update(ADMIN_PASSWORD="PruebaInicial2026", REGULAR_PASSWORD="corta")
    result = initialize(env)
    assert result.returncode != 0 and "REGULAR_PASSWORD requiere" in result.stderr
    env["REGULAR_PASSWORD"] = "RegularPrueba2026"
    assert initialize(env).returncode == 0

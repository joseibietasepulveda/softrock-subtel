"""Real PostgreSQL migration: preservation, safe refusal, rollback and concurrent startup."""
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import uuid
from pathlib import Path

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo

from backend.app import Connection, LOCK_SCHEMA, SCHEMA, hash_password
from backend.migrate_sqlite import MIGRATION, TABLES, migrate_sqlite


@pytest.fixture
def target():
    schema = "migration_" + uuid.uuid4().hex
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        url = make_conninfo(os.environ["DATABASE_URL"], options=f"-c search_path={schema}")
        yield url
        admin.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


@pytest.fixture
def source(tmp_path):
    path = tmp_path / "tachado.db"
    with sqlite3.connect(path) as c:
        c.executescript((Path(__file__).parent / "fixtures/sqlite_v05.sql").read_text())
        c.execute("ALTER TABLE documents ADD COLUMN auto_approve INTEGER DEFAULT 0")
        c.execute("INSERT INTO users(id,username,display_name,role,password_hash,created_at) VALUES(8,?,?,?,?,?)",
                  ("admin", "María O'Connor ¿100%?", "administrador", hash_password("Migracion2026"), "2026-09-28"))
        c.execute("INSERT INTO sessions VALUES('session-existing',8,2000000000.125)")
        c.execute("INSERT INTO api_tokens(id,name,token_hash,created_by,created_at) VALUES(12,'integracion','hash',8,'2026-09-28')")
        c.execute("INSERT INTO document_types VALUES('oficio','Oficio',1)")
        c.execute("INSERT INTO policies VALUES('oficio','RUT',1)")
        c.execute("INSERT INTO batches VALUES('batch','Lote','oficio','admin','2026-09-28')")
        c.execute("INSERT INTO documents(id,batch_id,original_name,format,entities,status,uploaded_by,created_at) "
                  "VALUES('doc','batch','original.txt','txt','[\"RUT\"]','review','admin','2026-09-28')")
        c.execute("INSERT INTO findings(id,document_id,entity_code,text,score) VALUES('finding','doc','RUT','12.345.678-5',0.95)")
        c.execute("INSERT INTO pseudonym_aliases VALUES('batch:batch','RUT','hash','RUT-01')")
        details = json.dumps({"texto": "á ? %\n"}, ensure_ascii=False)
        prev = "0" * 64
        digest = hashlib.sha256(f"{prev}|2026-09-28|admin|None|login|{details}".encode()).hexdigest()
        c.execute("INSERT INTO audit_log VALUES(44,?,?,?,?,?,?,?)",
                  ("2026-09-28", "admin", None, "login", details, prev, digest))
    return path


def connect(url):
    return Connection.connect(url, row_factory=psycopg.rows.dict_row)


def import_source(url, path):
    with connect(url) as c:
        c.execute("SELECT pg_advisory_xact_lock(%s)", (LOCK_SCHEMA,))
        c.execute(SCHEMA)
        return migrate_sqlite(c, path)


def test_preserves_every_table_id_hash_and_sequence(target, source):
    before = source.read_bytes()
    report = import_source(target, source)
    assert source.read_bytes() == before
    assert set(report["tables"]) == set(TABLES)
    assert all(r["rows"] == 1 for r in report["tables"].values())
    assert Path(report["backup"]).stat().st_mode & 0o777 == 0o600
    with sqlite3.connect(source) as old, connect(target) as new:
        for table in TABLES:
            old.row_factory = sqlite3.Row
            expected = dict(old.execute(f'SELECT * FROM "{table}"').fetchone())
            actual = new.execute(sql.SQL("SELECT * FROM {}").format(sql.Identifier(table))).fetchone()
            assert expected == actual
        assert new.execute("INSERT INTO users(username,display_name,role,password_hash,created_at) "
                           "VALUES('next','Next','auditor','hash','now') RETURNING id").fetchone()["id"] == 9
        assert new.execute("INSERT INTO api_tokens(name,token_hash,created_by,created_at) "
                           "VALUES('next','next',9,'now') RETURNING id").fetchone()["id"] == 13
        assert new.execute("SELECT nextval(pg_get_serial_sequence('audit_log','id')) n").fetchone()["n"] == 45


def test_restart_does_not_overwrite_new_postgres_data(target, source):
    report = import_source(target, source)
    with connect(target) as c:
        c.execute("UPDATE users SET display_name='Edited in Postgres' WHERE id=8")
    source.unlink()  # a completed migration must not depend on the retired SQLite file
    assert import_source(target, source) == report
    with connect(target) as c:
        assert c.execute("SELECT display_name FROM users WHERE id=8").fetchone()["display_name"] == "Edited in Postgres"


def test_refuses_populated_target(target, source):
    with connect(target) as c:
        c.execute(SCHEMA)
        c.execute("INSERT INTO document_types VALUES('existing','Existing',1)")
    with pytest.raises(RuntimeError, match="not empty"):
        import_source(target, source)
    with connect(target) as c:
        assert c.execute("SELECT code FROM document_types").fetchone()["code"] == "existing"
        assert c.execute("SELECT COUNT(*) n FROM users").fetchone()["n"] == 0


def test_bad_audit_or_incompatible_data_rolls_back_every_table(target, source):
    with sqlite3.connect(source) as c:
        c.execute("UPDATE findings SET text=?", ("unsupported\x00value",))
    with pytest.raises(psycopg.Error):
        import_source(target, source)
    with connect(target) as c:
        assert c.execute("SELECT to_regclass('users') n").fetchone()["n"] is None
        assert c.execute("SELECT to_regclass('database_migrations') n").fetchone()["n"] is None
    with sqlite3.connect(source) as c:
        c.execute("UPDATE findings SET text='fixed'")
        c.execute("DROP TRIGGER audit_no_update")
        c.execute("UPDATE audit_log SET hash='invalid'")
    with pytest.raises(RuntimeError, match="audit chain"):
        import_source(target, source)


def test_missing_source_never_creates_empty_database(target, tmp_path):
    path = tmp_path / "missing.db"
    with pytest.raises(FileNotFoundError):
        import_source(target, path)
    assert not path.exists()


def test_concurrent_startup_imports_once_and_keeps_passwords(target, source, tmp_path):
    env = {**os.environ, "DATABASE_URL": target, "DATA_DIR": str(tmp_path / "files"),
           "MIGRATE_SQLITE_PATH": str(source)}
    processes = [subprocess.Popen([sys.executable, "-c", "from backend.app import init_db; init_db()"],
                                 env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE) for _ in range(2)]
    for p in processes:
        _, err = p.communicate(timeout=40)
        assert p.returncode == 0, err.decode()
    with connect(target) as c:
        from backend.app import check_password
        assert check_password("Migracion2026", c.execute("SELECT password_hash FROM users WHERE id=8").fetchone()["password_hash"])
        assert c.execute("SELECT COUNT(*) n FROM database_migrations WHERE name=%s", (MIGRATION,)).fetchone()["n"] == 1
        assert c.execute("SELECT COUNT(*) n FROM audit_log").fetchone()["n"] == 1
    assert len(list((source.parent / "backups").glob("*.db"))) == 1

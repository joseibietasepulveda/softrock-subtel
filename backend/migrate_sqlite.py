"""One-time, verified SQLite import inside the startup PostgreSQL transaction.

Run only after the old SQLite application has stopped writing. The caller holds
LOCK_SCHEMA and commits the schema, all rows and the migration receipt together.
No source files or existing PostgreSQL records are changed by this module.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from psycopg import sql

TABLES = (
    "users", "sessions", "api_tokens", "audit_log", "document_types", "policies",
    "batches", "documents", "findings", "pseudonym_aliases",
)
IDENTITIES = ("users", "api_tokens", "audit_log")
MIGRATION = "sqlite-v05-to-postgres-v06"


def _digest(rows):
    digest, count = hashlib.sha256(), 0
    for row in rows:
        digest.update(json.dumps(list(row), ensure_ascii=False, allow_nan=False,
                                 separators=(",", ":")).encode("utf-8") + b"\n")
        count += 1
    return {"rows": count, "sha256": digest.hexdigest()}


def _verify_audit(source):
    previous = "0" * 64
    for row in source.execute("SELECT * FROM audit_log ORDER BY id"):
        expected = hashlib.sha256(
            f"{previous}|{row['ts']}|{row['username']}|{row['ip']}|{row['event']}|{row['details']}".encode()
        ).hexdigest()
        if row["prev_hash"] != previous or row["hash"] != expected:
            raise RuntimeError(f"SQLite audit chain is broken at event {row['id']}")
        previous = row["hash"]
    return previous


def migrate_sqlite(con, source_path: Path) -> dict:
    """Import once, refusing nonempty targets and comparing every source column.

    The caller MUST roll back on failure. Retained SQLite backups remain usable
    even if PostgreSQL import or verification fails.
    """
    con.execute("""CREATE TABLE IF NOT EXISTS database_migrations(
        name TEXT PRIMARY KEY, completed_at TEXT NOT NULL, report TEXT NOT NULL)""")
    done = con.execute("SELECT report FROM database_migrations WHERE name=%s", (MIGRATION,)).fetchone()
    if done:
        return json.loads(done["report"])
    # Never seed over, merge into, or truncate an existing application database.
    for table in (*TABLES, "jobs", "job_items"):
        if con.execute(sql.SQL("SELECT 1 FROM {} LIMIT 1").format(sql.Identifier(table))).fetchone():
            raise RuntimeError(f"SQLite import refused: PostgreSQL table {table} is not empty")

    source_path = Path(source_path).resolve(strict=True)
    backup_dir = source_path.parent / "backups"
    backup_dir.mkdir(mode=0o700, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = backup_dir / f"pre-postgres-{stamp}-{uuid.uuid4().hex[:8]}.db"
    fd = os.open(backup, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(fd)
    original = sqlite3.connect(source_path.as_uri() + "?mode=ro", uri=True)
    snapshot = sqlite3.connect(backup)
    try:
        original.backup(snapshot)
    finally:
        snapshot.close()
        original.close()

    source = sqlite3.connect(backup.as_uri() + "?mode=ro", uri=True)
    source.row_factory = sqlite3.Row
    try:
        if source.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("SQLite integrity check failed")
        tables = {r[0] for r in source.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
        if tables != set(TABLES):
            raise RuntimeError(f"Unexpected SQLite tables: missing={set(TABLES)-tables}, extra={tables-set(TABLES)}")
        report = {"source": str(source_path), "backup": str(backup), "tables": {},
                  "audit_head": _verify_audit(source)}
        for table in TABLES:
            info = source.execute(f'PRAGMA table_info("{table}")').fetchall()
            columns = [r["name"] for r in info]
            # Earlier dev releases persisted this flag. Current code no longer uses
            # it, but dropping its values would make rollback/reconciliation lossy.
            if table == "documents" and "auto_approve" in columns:
                con.execute("ALTER TABLE documents ADD COLUMN IF NOT EXISTS auto_approve INTEGER")
            primary = [r["name"] for r in sorted(info, key=lambda r: r["pk"]) if r["pk"]]
            if not primary:
                raise RuntimeError(f"Missing primary key in {table}")
            identifiers = sql.SQL(", ").join(map(sql.Identifier, columns))
            query = sql.SQL("SELECT {} FROM {} ORDER BY {}").format(
                identifiers, sql.Identifier(table), sql.SQL(", ").join(map(sql.Identifier, primary)))
            # The same explicit column list and PK order makes the comparison independent
            # of differences in SQLite/PostgreSQL physical column order or collation.
            select = 'SELECT ' + ','.join('"' + c.replace('"', '""') + '"' for c in columns)
            select += f' FROM "{table}"'
            expected = []
            with con.cursor() as cursor:
                with cursor.copy(sql.SQL("COPY {} ({}) FROM STDIN").format(
                        sql.Identifier(table), identifiers)) as copy:
                    for row in source.execute(select):
                        copy.write_row(tuple(row))
            # Compare row digests as a multiset: text collation order differs by engine.
            for row in source.execute(select):
                expected.append(_digest([row])["sha256"])
            actual = [_digest([tuple(row[c] for c in columns)])["sha256"] for row in con.execute(query)]
            source_hash = _digest((value,) for value in sorted(expected))
            target_hash = _digest((value,) for value in sorted(actual))
            if source_hash != target_hash:
                raise RuntimeError(f"SQLite import verification failed for {table}")
            report["tables"][table] = source_hash

        for table in IDENTITIES:
            maximum = con.execute(sql.SQL("SELECT MAX(id) AS n FROM {}").format(
                sql.Identifier(table))).fetchone()["n"]
            con.execute("SELECT setval(pg_get_serial_sequence(%s, 'id'), %s, %s)",
                        (table, maximum or 1, maximum is not None))
        completed = datetime.now(timezone.utc).isoformat()
        con.execute("INSERT INTO database_migrations(name,completed_at,report) VALUES(%s,%s,%s)",
                    (MIGRATION, completed, json.dumps(report, ensure_ascii=False, sort_keys=True)))
        return report
    finally:
        source.close()

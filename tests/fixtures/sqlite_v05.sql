
CREATE TABLE IF NOT EXISTS users(
  id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL, display_name TEXT NOT NULL,
  role TEXT NOT NULL, password_hash TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL, last_login_at TEXT);
CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY, user_id INTEGER NOT NULL, expires REAL NOT NULL);
CREATE TABLE IF NOT EXISTS api_tokens(
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, token_hash TEXT UNIQUE NOT NULL,
  created_by INTEGER NOT NULL, active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, last_used_at TEXT);
CREATE TABLE IF NOT EXISTS audit_log(
  id INTEGER PRIMARY KEY, ts TEXT NOT NULL, username TEXT NOT NULL, ip TEXT, event TEXT NOT NULL,
  details TEXT NOT NULL, prev_hash TEXT NOT NULL, hash TEXT NOT NULL);
-- Bitácora inalterable: la app nunca hace UPDATE/DELETE aquí; los triggers lo impiden a nivel de base.
CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit_log BEGIN SELECT RAISE(ABORT,'audit_log es inalterable'); END;
CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit_log BEGIN SELECT RAISE(ABORT,'audit_log es inalterable'); END;
CREATE TABLE IF NOT EXISTS document_types(code TEXT PRIMARY KEY, name TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS policies(document_type TEXT NOT NULL, entity_code TEXT NOT NULL, enabled INTEGER NOT NULL,
  PRIMARY KEY(document_type, entity_code));
CREATE TABLE IF NOT EXISTS batches(id TEXT PRIMARY KEY, name TEXT NOT NULL, document_type TEXT,
  created_by TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS documents(
  id TEXT PRIMARY KEY, batch_id TEXT, original_name TEXT NOT NULL, format TEXT NOT NULL,
  size_bytes INTEGER, sha256 TEXT, pages INTEGER, document_type TEXT, entities TEXT NOT NULL,
  treatment TEXT, analysis_level TEXT NOT NULL DEFAULT 'fast', status TEXT NOT NULL, uploaded_by TEXT NOT NULL,
  error TEXT, stats TEXT, verification TEXT, created_at TEXT NOT NULL, protected_at TEXT,
  submitted_at TEXT, submitted_by TEXT, decision_at TEXT, decision_by TEXT, decision_note TEXT);
CREATE TABLE IF NOT EXISTS findings(
  id TEXT PRIMARY KEY, document_id TEXT NOT NULL, entity_code TEXT NOT NULL, text TEXT, score REAL,
  page INTEGER, boxes TEXT, location TEXT, start_off INTEGER, end_off INTEGER,
  source TEXT NOT NULL DEFAULT 'auto', status TEXT NOT NULL DEFAULT 'accepted', reviewed_by TEXT, reviewed_at TEXT);
CREATE INDEX IF NOT EXISTS idx_documents_status ON documents(status);
CREATE INDEX IF NOT EXISTS idx_documents_batch ON documents(batch_id);
CREATE INDEX IF NOT EXISTS idx_findings_doc ON findings(document_id);
CREATE TABLE IF NOT EXISTS pseudonym_aliases(
  scope_id TEXT NOT NULL, entity_code TEXT NOT NULL, value_hash TEXT NOT NULL, alias TEXT NOT NULL,
  PRIMARY KEY(scope_id, entity_code, value_hash), UNIQUE(scope_id, entity_code, alias));

ALTER TABLE users ADD COLUMN auth_source TEXT NOT NULL DEFAULT 'local';

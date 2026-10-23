import sqlite3
from pathlib import Path

from .migrations.v001_split_sources_and_outputs import migrate
from .models import publication_timestamp

DATABASE_SCHEMA_VERSION = 1

SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS sources (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source_url TEXT NOT NULL UNIQUE,
  enabled INTEGER NOT NULL DEFAULT 1,
  refresh_interval INTEGER NOT NULL DEFAULT 30 CHECK(refresh_interval >= 1),
  last_fetched_at TEXT,
  last_error TEXT,
  source_title TEXT,
  icon_url TEXT,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS feeds (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source_id INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_feeds_source ON feeds(source_id);
CREATE TABLE IF NOT EXISTS filter_rules (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  feed_id INTEGER NOT NULL REFERENCES feeds(id) ON DELETE CASCADE,
  field TEXT NOT NULL CHECK(field IN ('title','content','author','category','all')),
  operator TEXT NOT NULL CHECK(operator IN ('contains','regex','equals')),
  value TEXT NOT NULL,
  action TEXT NOT NULL CHECK(action IN ('exclude','include')),
  enabled INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS entries (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source_id INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
  guid TEXT NOT NULL,
  title TEXT NOT NULL DEFAULT '',
  url TEXT NOT NULL DEFAULT '',
  content TEXT NOT NULL DEFAULT '',
  author TEXT NOT NULL DEFAULT '',
  category TEXT NOT NULL DEFAULT '',
  published TEXT,
  fetched_at TEXT NOT NULL,
  UNIQUE(source_id, guid)
);
CREATE INDEX IF NOT EXISTS idx_entries_source_published ON entries(source_id, published);
CREATE TABLE IF NOT EXISTS feed_entry_visibility (
  feed_id INTEGER NOT NULL REFERENCES feeds(id) ON DELETE CASCADE,
  entry_id INTEGER NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
  hidden INTEGER NOT NULL CHECK(hidden IN (0, 1)),
  PRIMARY KEY(feed_id, entry_id)
);
CREATE INDEX IF NOT EXISTS idx_visibility_entry ON feed_entry_visibility(entry_id);
CREATE INDEX IF NOT EXISTS idx_visibility_feed_hidden ON feed_entry_visibility(feed_id, hidden);
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  google_sub TEXT NOT NULL UNIQUE,
  email TEXT NOT NULL,
  name TEXT NOT NULL DEFAULT '',
  picture_url TEXT,
  created_at TEXT NOT NULL,
  last_login_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS user_permissions (
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  permission TEXT NOT NULL,
  granted_at TEXT NOT NULL,
  PRIMARY KEY(user_id, permission)
);
CREATE TABLE IF NOT EXISTS config_state (
  id INTEGER PRIMARY KEY CHECK(id = 1),
  revision INTEGER NOT NULL CHECK(revision >= 1)
);
INSERT OR IGNORE INTO config_state(id, revision) VALUES(1, 1);
"""


class Database:
    def __init__(self, path: str):
        self.path = path

    def connect(self) -> sqlite3.Connection:
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.create_function("publication_timestamp", 1, publication_timestamp, deterministic=True)
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def initialize(self) -> None:
        conn = self.connect()
        try:
            # SQLite table rebuilds must not cascade deletes into extension
            # tables referencing entries/feeds. This is connection-local and
            # must be set before BEGIN; integrity is checked before commit.
            conn.execute("PRAGMA foreign_keys = OFF")
            with conn:
                conn.execute("BEGIN IMMEDIATE")
                version = conn.execute("PRAGMA user_version").fetchone()[0]
                if version not in (0, DATABASE_SCHEMA_VERSION):
                    raise RuntimeError(f"Unsupported database schema version {version}; restore a compatible backup")
                if version == 0:
                    try:
                        migrate(conn, SCHEMA)
                        if conn.execute("PRAGMA foreign_key_check").fetchall():
                            raise RuntimeError("Database migration failed foreign key checks")
                        conn.execute(f"PRAGMA user_version = {DATABASE_SCHEMA_VERSION}")
                    except sqlite3.Error as exc:
                        raise RuntimeError(f"Database migration failed and was rolled back: {exc}; check the database or restore a backup") from exc
        finally:
            conn.execute("PRAGMA foreign_keys = ON")
            conn.close()

import sqlite3
from pathlib import Path


SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS feeds (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  source_url TEXT NOT NULL UNIQUE,
  enabled INTEGER NOT NULL DEFAULT 1,
  refresh_interval INTEGER NOT NULL DEFAULT 30 CHECK(refresh_interval >= 1),
  last_fetched_at TEXT,
  last_error TEXT,
  source_title TEXT,
  icon_url TEXT,
  created_at TEXT NOT NULL
);
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
  feed_id INTEGER NOT NULL REFERENCES feeds(id) ON DELETE CASCADE,
  guid TEXT NOT NULL,
  title TEXT NOT NULL DEFAULT '',
  url TEXT NOT NULL DEFAULT '',
  content TEXT NOT NULL DEFAULT '',
  author TEXT NOT NULL DEFAULT '',
  category TEXT NOT NULL DEFAULT '',
  published TEXT,
  fetched_at TEXT NOT NULL,
  hidden INTEGER NOT NULL DEFAULT 0,
  UNIQUE(feed_id, guid)
);
CREATE INDEX IF NOT EXISTS idx_entries_feed_visible ON entries(feed_id, hidden, published);
"""


class Database:
    def __init__(self, path: str):
        self.path = path

    def connect(self) -> sqlite3.Connection:
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def initialize(self) -> None:
        with self.connect() as conn:
            conn.executescript(SCHEMA)
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(feeds)")}
            if "source_title" not in columns:
                conn.execute("ALTER TABLE feeds ADD COLUMN source_title TEXT")
            if "icon_url" not in columns:
                conn.execute("ALTER TABLE feeds ADD COLUMN icon_url TEXT")

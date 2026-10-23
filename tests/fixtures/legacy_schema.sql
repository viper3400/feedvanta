PRAGMA foreign_keys = ON;
CREATE TABLE feeds (
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
CREATE TABLE filter_rules (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  feed_id INTEGER NOT NULL REFERENCES feeds(id) ON DELETE CASCADE,
  field TEXT NOT NULL CHECK(field IN ('title','content','author','category','all')),
  operator TEXT NOT NULL CHECK(operator IN ('contains','regex','equals')),
  value TEXT NOT NULL,
  action TEXT NOT NULL CHECK(action IN ('exclude','include')),
  enabled INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE entries (
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
CREATE INDEX idx_entries_feed_visible ON entries(feed_id, hidden, published);
CREATE TABLE users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  google_sub TEXT NOT NULL UNIQUE,
  email TEXT NOT NULL,
  name TEXT NOT NULL DEFAULT '',
  picture_url TEXT,
  created_at TEXT NOT NULL,
  last_login_at TEXT NOT NULL
);
CREATE TABLE user_permissions (
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  permission TEXT NOT NULL,
  granted_at TEXT NOT NULL,
  PRIMARY KEY(user_id, permission)
);
CREATE TABLE config_state (
  id INTEGER PRIMARY KEY CHECK(id = 1),
  revision INTEGER NOT NULL CHECK(revision >= 1)
);
INSERT INTO config_state(id, revision) VALUES(1, 12);

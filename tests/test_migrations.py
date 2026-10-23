import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.db import Database
from app.main import create_app


def legacy_database(path: Path, source_title=True, icon_url=True):
    schema = (Path(__file__).parent / "fixtures" / "legacy_schema.sql").read_text()
    if not source_title:
        schema = schema.replace("  source_title TEXT,\n", "")
    if not icon_url:
        schema = schema.replace("  icon_url TEXT,\n", "")
    with sqlite3.connect(path) as conn:
        conn.executescript(schema)
        conn.execute(
            "INSERT INTO feeds(id,name,source_url,enabled,refresh_interval,last_fetched_at,last_error,created_at) "
            "VALUES(7,'Custom','https://example.com/feed',0,45,'2026-10-20T00:00:00+00:00','Last error','created')"
        )
        conn.execute("INSERT INTO feeds(id,name,source_url,created_at) VALUES(8,'Empty','https://example.com/empty','created')")
        conn.execute("INSERT INTO feeds(id,name,source_url,created_at) VALUES(100,'Deleted','https://example.com/deleted','created')")
        conn.execute("DELETE FROM feeds WHERE id=100")
        if source_title:
            conn.execute("UPDATE feeds SET source_title='Original title' WHERE id=7")
        if icon_url:
            conn.execute("UPDATE feeds SET icon_url='https://example.com/icon.png' WHERE id=7")
        for entry_id, hidden in ((10, 0), (11, 1)):
            conn.execute(
                "INSERT INTO entries(id,feed_id,guid,title,url,content,author,category,published,fetched_at,hidden) "
                "VALUES(?,7,?,?,'https://example.com/video.mp4','body','Ada','Tech',?,?,?)",
                (entry_id, f"guid-{entry_id}", f"Article {entry_id}",
                 datetime.now(timezone.utc).isoformat(), "2000-01-01T00:00:00+00:00", hidden),
            )
        conn.execute("INSERT INTO filter_rules(id,feed_id,field,operator,value,action) VALUES(5,7,'title','contains','11','exclude')")
        conn.execute("INSERT INTO users(id,google_sub,email,created_at,last_login_at) VALUES(3,'google-1','admin@example.com','created','login')")
        conn.execute("INSERT INTO user_permissions(user_id,permission,granted_at) VALUES(3,'Global Administration','granted')")


def snapshot(path):
    with sqlite3.connect(path) as conn:
        return list(conn.iterdump()), conn.execute("PRAGMA user_version").fetchone()[0]


def structure(db):
    with db.connect() as conn:
        return [(row["name"], " ".join(row["sql"].split()).replace("IF NOT EXISTS ", ""))
                for row in conn.execute("SELECT name,sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY name")]


@pytest.mark.parametrize("title,icon", [(True, True), (False, False), (True, False), (False, True)])
def test_legacy_migration_preserves_data_and_matches_fresh_schema(tmp_path, title, icon):
    path = tmp_path / "legacy.db"
    legacy_database(path, title, icon)
    with sqlite3.connect(path) as conn:
        old_entries = conn.execute("SELECT * FROM entries ORDER BY id").fetchall()
    db = Database(str(path))
    db.initialize()
    with db.connect() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
        assert not conn.execute("PRAGMA foreign_key_check").fetchall()
        source = dict(conn.execute("SELECT * FROM sources WHERE id=7").fetchone())
        assert source["source_url"] == "https://example.com/feed"
        assert source["enabled"] == 0 and source["refresh_interval"] == 45
        assert source["last_error"] == "Last error"
        assert source["last_fetched_at"] == "2026-10-20T00:00:00+00:00"
        assert source["source_title"] == ("Original title" if title else None)
        assert source["icon_url"] == ("https://example.com/icon.png" if icon else None)
        feed = conn.execute("SELECT * FROM feeds WHERE id=7").fetchone()
        assert feed["source_id"] == 7
        assert feed["name"] == ("Original title" if title else "Custom")
        assert feed["created_at"] == "created"
        assert [tuple(row) for row in conn.execute("SELECT * FROM entries ORDER BY id")] == [row[:-1] for row in old_entries]
        assert [tuple(row) for row in conn.execute("SELECT * FROM feed_entry_visibility ORDER BY entry_id")] == [(7, 10, 0), (7, 11, 1)]
        assert conn.execute("SELECT value FROM filter_rules WHERE id=5").fetchone()[0] == "11"
        assert conn.execute("SELECT email FROM users WHERE id=3").fetchone()[0] == "admin@example.com"
        assert conn.execute("SELECT COUNT(*) FROM user_permissions").fetchone()[0] == 1
        assert conn.execute("SELECT revision FROM config_state").fetchone()[0] == 12
    fresh = Database(str(tmp_path / "fresh.db"))
    fresh.initialize()
    assert structure(db) == structure(fresh)
    before = snapshot(path)
    db.initialize()
    assert snapshot(path) == before
    with db.connect() as conn:
        assert conn.execute("INSERT INTO feeds(source_id,name,created_at) VALUES(7,'New','now')").lastrowid > 100


def test_migration_preserves_public_urls_and_visibility(tmp_path, monkeypatch):
    path = tmp_path / "legacy.db"
    legacy_database(path)
    # Startup normally prunes old entries, but pruning is not part of migration.
    monkeypatch.setattr("app.services.FeedService.prune_expired_entries", lambda self: None)
    app = create_app(str(path), start_scheduler=False)
    with TestClient(app) as client:
        rss = client.get("/feed/7.xml")
        assert rss.status_code == 200
        assert "<title>Original title</title>" in rss.text
        assert "guid-10" in rss.text and "guid-11" not in rss.text
        assert 'enclosure url="https://example.com/video.mp4"' in rss.text
        reader = client.get("/reader/7")
        assert "Article 10" in reader.text and "Article 11" not in reader.text


def test_failed_migration_rolls_back_everything(tmp_path, monkeypatch):
    path = tmp_path / "legacy.db"
    legacy_database(path)
    before = snapshot(path)
    from app.db import migrate

    def fail(conn, schema):
        migrate(conn, schema)
        raise RuntimeError("simulated migration failure")

    monkeypatch.setattr("app.db.migrate", fail)
    with pytest.raises(RuntimeError, match="simulated"):
        Database(str(path)).initialize()
    assert snapshot(path) == before


@pytest.mark.parametrize("kind", ["future", "missing_core", "columns", "broken_fk", "trigger", "conflict"])
def test_unsupported_databases_are_not_modified(tmp_path, kind):
    path = tmp_path / "test.db"
    legacy_database(path)
    with sqlite3.connect(path) as conn:
        if kind == "future":
            conn.execute("PRAGMA user_version=999")
        elif kind == "missing_core":
            conn.execute("DROP TABLE filter_rules")
        elif kind == "columns":
            conn.execute("ALTER TABLE entries ADD COLUMN unexpected TEXT")
        elif kind == "broken_fk":
            conn.execute("UPDATE entries SET feed_id=999")
        elif kind == "trigger":
            conn.execute("CREATE TRIGGER custom_trigger AFTER INSERT ON feeds BEGIN SELECT 1; END")
        else:
            conn.execute("CREATE TABLE sources(value TEXT)")
    before = snapshot(path)
    with pytest.raises(RuntimeError):
        Database(str(path)).initialize()
    assert snapshot(path) == before


def test_concurrent_startup_runs_migration_once(tmp_path, monkeypatch):
    path = tmp_path / "legacy.db"
    legacy_database(path)
    from app.db import migrate
    calls = []

    def record(conn, schema):
        calls.append(True)
        migrate(conn, schema)

    monkeypatch.setattr("app.db.migrate", record)
    with ThreadPoolExecutor(max_workers=2) as executor:
        list(executor.map(lambda _: Database(str(path)).initialize(), range(2)))
    assert len(calls) == 1


def test_sqlite_backup_can_restore_pre_upgrade_database(tmp_path):
    path = tmp_path / "legacy.db"
    backup = tmp_path / "backup.db"
    legacy_database(path)
    before = snapshot(path)
    with sqlite3.connect(path) as source, sqlite3.connect(backup) as target:
        source.backup(target)
    Database(str(path)).initialize()
    assert snapshot(backup) == before
    with sqlite3.connect(backup) as source, sqlite3.connect(path) as target:
        source.backup(target)
    assert snapshot(path) == before


@pytest.mark.parametrize("missing", ["config", "auth", "all"])
def test_older_legacy_database_can_lack_auth_and_config_tables(tmp_path, missing):
    path = tmp_path / "legacy.db"
    legacy_database(path)
    with sqlite3.connect(path) as conn:
        if missing in ("auth", "all"):
            conn.execute("DROP TABLE user_permissions")
            conn.execute("DROP TABLE users")
        if missing in ("config", "all"):
            conn.execute("DROP TABLE config_state")
    db = Database(str(path))
    db.initialize()
    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM feeds").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM entries").fetchone()[0] == 2
        assert conn.execute("SELECT revision FROM config_state").fetchone()[0] == (1 if missing in ("config", "all") else 12)
        assert conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == (0 if missing in ("auth", "all") else 1)
        assert not conn.execute("PRAGMA foreign_key_check").fetchall()
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def extension_tables(path):
    with sqlite3.connect(path) as conn:
        conn.executescript("""
            CREATE TABLE downloads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entry_id INTEGER NOT NULL UNIQUE REFERENCES entries(id) ON DELETE CASCADE,
                status TEXT NOT NULL
            );
            CREATE INDEX idx_downloads_status ON downloads(status, id);
            CREATE TABLE digest_topics (id INTEGER PRIMARY KEY, title TEXT NOT NULL);
            CREATE TABLE digest_topic_sources (
                id INTEGER PRIMARY KEY,
                topic_id INTEGER REFERENCES digest_topics(id) ON DELETE CASCADE,
                feed_id INTEGER NOT NULL,
                source_guid TEXT NOT NULL
            );
            CREATE INDEX idx_digest_topic_sources_topic ON digest_topic_sources(topic_id);
            CREATE TABLE extension_feed_refs (
                id INTEGER PRIMARY KEY,
                feed_id INTEGER NOT NULL REFERENCES feeds(id) ON DELETE CASCADE
            );
            INSERT INTO downloads(id,entry_id,status) VALUES(20,10,'completed');
            INSERT INTO digest_topics VALUES(30,'Topic');
            INSERT INTO digest_topic_sources VALUES(40,30,7,'guid-10');
            INSERT INTO extension_feed_refs VALUES(50,7);
        """)


def extension_snapshot(path):
    tables = ("downloads", "digest_topics", "digest_topic_sources", "extension_feed_refs")
    with sqlite3.connect(path) as conn:
        return {
            "rows": {table: conn.execute(f"SELECT * FROM {table}").fetchall() for table in tables},
            "schema": conn.execute(
                "SELECT type,name,sql FROM sqlite_master WHERE tbl_name IN (?,?,?,?) ORDER BY name", tables
            ).fetchall(),
        }


def test_extension_tables_rows_indexes_and_references_survive_rebuild(tmp_path):
    path = tmp_path / "legacy.db"
    legacy_database(path)
    extension_tables(path)
    before = extension_snapshot(path)
    db = Database(str(path))
    db.initialize()
    assert extension_snapshot(path) == before
    with db.connect() as conn:
        assert not conn.execute("PRAGMA foreign_key_check").fetchall()
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        conn.execute("DELETE FROM entries WHERE id=10")
        assert conn.execute("SELECT COUNT(*) FROM downloads").fetchone()[0] == 0
        conn.execute("DELETE FROM feeds WHERE id=7")
        assert conn.execute("SELECT COUNT(*) FROM extension_feed_refs").fetchone()[0] == 0


def test_extension_tables_survive_failed_migration(tmp_path, monkeypatch):
    path = tmp_path / "legacy.db"
    legacy_database(path)
    extension_tables(path)
    before = snapshot(path)
    from app.db import migrate

    def fail(conn, schema):
        migrate(conn, schema)
        raise RuntimeError("simulated extension migration failure")

    monkeypatch.setattr("app.db.migrate", fail)
    with pytest.raises(RuntimeError, match="simulated"):
        Database(str(path)).initialize()
    assert snapshot(path) == before


def test_incompatible_extension_foreign_key_aborts_without_data_loss(tmp_path):
    path = tmp_path / "legacy.db"
    legacy_database(path)
    with sqlite3.connect(path) as conn:
        conn.executescript("""
            CREATE TABLE extension_urls (
                source_url TEXT REFERENCES feeds(source_url)
            );
            INSERT INTO extension_urls VALUES('https://example.com/feed');
        """)
    before = snapshot(path)
    with pytest.raises(RuntimeError, match="migration failed"):
        Database(str(path)).initialize()
    assert snapshot(path) == before

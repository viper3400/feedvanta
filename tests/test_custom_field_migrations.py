from concurrent.futures import ThreadPoolExecutor

import pytest

from app.db import Database, SCHEMA_V1
from app.migrations.v001_split_sources_and_outputs import create_schema
from test_migrations import legacy_database, snapshot, structure


def version1(path):
    db = Database(str(path))
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        create_schema(conn, SCHEMA_V1)
        conn.execute("PRAGMA user_version=1")
        conn.execute("INSERT INTO sources(id,source_url,created_at) VALUES(7,'https://example.com/rss','created')")
        conn.execute("INSERT INTO feeds(id,source_id,name,created_at) VALUES(8,7,'Old','created')")
        conn.execute("INSERT INTO entries(id,source_id,guid,title,fetched_at) VALUES(9,7,'a','Old article','old')")
        conn.execute("INSERT INTO filter_rules(id,feed_id,field,operator,value,action) VALUES(10,8,'title','contains','Old','exclude')")
        conn.execute("INSERT INTO filter_rules(id,feed_id,field,operator,value,action) VALUES(100,8,'author','equals','Ada','include')")
        conn.execute("DELETE FROM filter_rules WHERE id=100")
        conn.execute("INSERT INTO feed_entry_visibility VALUES(8,9,1)")
        conn.execute("UPDATE config_state SET revision=15")
    return db


def test_version1_upgrade_retains_data_extensions_and_sequences(tmp_path):
    path = tmp_path / "old.db"
    db = version1(path)
    with db.connect() as conn:
        conn.execute("CREATE TABLE extension_rules(id INTEGER PRIMARY KEY, rule_id INTEGER REFERENCES filter_rules(id) ON DELETE CASCADE)")
        conn.execute("INSERT INTO extension_rules VALUES(1,10)")
        conn.execute("CREATE INDEX extension_index ON filter_rules(value)")
        conn.execute("CREATE TABLE audit(rule_id INTEGER)")
        conn.execute("CREATE TRIGGER extension_trigger AFTER INSERT ON filter_rules BEGIN INSERT INTO audit VALUES(new.id); END")
        conn.execute("CREATE VIEW extension_view AS SELECT id FROM filter_rules")
    db.initialize()
    with db.connect() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 2
        assert conn.execute("SELECT custom_fields FROM entries WHERE id=9").fetchone()[0] == "{}"
        assert conn.execute("SELECT title FROM entries WHERE id=9").fetchone()[0] == "Old article"
        assert conn.execute("SELECT field_extraction_error FROM sources WHERE id=7").fetchone()[0] is None
        assert tuple(conn.execute("SELECT * FROM filter_rules WHERE id=10").fetchone()) == (10,8,"title","contains","Old","exclude",1)
        assert tuple(conn.execute("SELECT * FROM feed_entry_visibility").fetchone()) == (8,9,1)
        assert conn.execute("SELECT revision FROM config_state").fetchone()[0] == 15
        assert conn.execute("SELECT COUNT(*) FROM extension_rules").fetchone()[0] == 1
        assert conn.execute("SELECT id FROM extension_view").fetchone()[0] == 10
        assert conn.execute("SELECT COUNT(*) FROM audit").fetchone()[0] == 0
        rule_id = conn.execute("INSERT INTO filter_rules(feed_id,field,operator,value,action) VALUES(8,'xml:[\"\",\"authors\"]','contains','Redaktion','exclude')").lastrowid
        assert rule_id > 100
        assert conn.execute("SELECT rule_id FROM audit").fetchone()[0] == rule_id
        assert not conn.execute("PRAGMA foreign_key_check").fetchall()
        assert conn.execute("SELECT name FROM sqlite_master WHERE name='extension_index'").fetchone()
        conn.execute("DELETE FROM filter_rules WHERE id=10")
        assert conn.execute("SELECT COUNT(*) FROM extension_rules").fetchone()[0] == 0
    before = snapshot(path)
    db.initialize()
    assert snapshot(path) == before


def test_fresh_version1_and_legacy_converge(tmp_path):
    old = version1(tmp_path / "old.db")
    legacy = tmp_path / "legacy.db"
    legacy_database(legacy)
    databases = [old, Database(str(legacy)), Database(str(tmp_path / "fresh.db"))]
    for db in databases:
        db.initialize()
    assert structure(databases[0]) == structure(databases[1]) == structure(databases[2])


@pytest.mark.parametrize("version", [0, 1])
def test_version2_failure_rolls_back_complete_upgrade(tmp_path, monkeypatch, version):
    path = tmp_path / "old.db"
    if version == 0:
        legacy_database(path)
    else:
        version1(path)
    before = snapshot(path)
    from app.db import migrate_v2

    def fail(conn, schema):
        migrate_v2(conn, schema)
        raise RuntimeError("forced failure")

    monkeypatch.setattr("app.db.migrate_v2", fail)
    with pytest.raises(RuntimeError, match="forced failure"):
        Database(str(path)).initialize()
    assert snapshot(path) == before


@pytest.mark.parametrize("kind", ["columns", "foreign_key", "future"])
def test_invalid_version1_is_not_modified(tmp_path, kind):
    path = tmp_path / "old.db"
    db = version1(path)
    with db.connect() as conn:
        if kind == "columns":
            conn.execute("ALTER TABLE entries ADD COLUMN unexpected TEXT")
        elif kind == "foreign_key":
            conn.execute("CREATE TABLE extension_rules(rule_id INTEGER REFERENCES filter_rules(id))")
            # Use a separate connection with foreign keys disabled for a corrupt fixture.
    if kind == "foreign_key":
        with db.connect() as conn:
            conn.execute("PRAGMA foreign_keys=OFF")
            conn.execute("INSERT INTO extension_rules VALUES(9999)")
    elif kind == "future":
        with db.connect() as conn:
            conn.execute("PRAGMA user_version=999")
    before = snapshot(path)
    with pytest.raises(RuntimeError):
        db.initialize()
    assert snapshot(path) == before


def test_concurrent_version1_migration_runs_once(tmp_path, monkeypatch):
    path = tmp_path / "old.db"
    version1(path)
    from app.db import migrate_v2
    calls = []

    def record(conn, schema):
        calls.append(True)
        migrate_v2(conn, schema)

    monkeypatch.setattr("app.db.migrate_v2", record)
    with ThreadPoolExecutor(max_workers=2) as executor:
        list(executor.map(lambda _: Database(str(path)).initialize(), range(2)))
    assert calls == [True]

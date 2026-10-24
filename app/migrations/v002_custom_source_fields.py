"""Preserve version 1 data while enabling original XML field rules."""

import sqlite3


def migrate(conn: sqlite3.Connection, schema: str) -> None:
    expected = {
        "entries": {"id", "source_id", "guid", "title", "url", "content", "author", "category", "published", "fetched_at"},
        "sources": {"id", "source_url", "enabled", "refresh_interval", "last_fetched_at", "last_error", "source_title", "icon_url", "created_at"},
        "filter_rules": {"id", "feed_id", "field", "operator", "value", "action", "enabled"},
        "feeds": {"id", "source_id", "name", "created_at"},
        "feed_entry_visibility": {"feed_id", "entry_id", "hidden"},
        "users": {"id", "google_sub", "email", "name", "picture_url", "created_at", "last_login_at"},
        "user_permissions": {"user_id", "permission", "granted_at"},
        "config_state": {"id", "revision"},
    }
    for table, columns in expected.items():
        if {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")} != columns:
            raise RuntimeError(f"Unrecognized version 1 database layout: {table}")
    if conn.execute("PRAGMA foreign_key_check").fetchall():
        raise RuntimeError("Version 1 database has broken foreign keys")
    # Keep extension indexes/triggers and references to the rebuilt table. The
    # caller disables foreign keys before the transaction and checks afterwards.
    extensions = [row[0] for row in conn.execute(
        "SELECT sql FROM sqlite_master WHERE tbl_name='filter_rules' "
        "AND type IN ('index','trigger') AND sql IS NOT NULL ORDER BY type,name"
    )]
    rules = list(conn.execute("SELECT * FROM filter_rules"))
    sequence = conn.execute("SELECT seq FROM sqlite_sequence WHERE name='filter_rules'").fetchone()
    conn.execute("DROP TABLE filter_rules")
    statement = next(s for s in schema.split(";") if "CREATE TABLE IF NOT EXISTS filter_rules" in s)
    conn.execute(statement)
    conn.executemany("INSERT INTO filter_rules(id,feed_id,field,operator,value,action,enabled) VALUES(?,?,?,?,?,?,?)", rules)
    if sequence:
        conn.execute("DELETE FROM sqlite_sequence WHERE name='filter_rules'")
        conn.execute("INSERT INTO sqlite_sequence(name,seq) VALUES('filter_rules',?)", (sequence[0],))
    for sql in extensions:
        conn.execute(sql)
    conn.execute("ALTER TABLE entries ADD COLUMN custom_fields TEXT NOT NULL DEFAULT '{}'")
    conn.execute("ALTER TABLE sources ADD COLUMN field_extraction_error TEXT")

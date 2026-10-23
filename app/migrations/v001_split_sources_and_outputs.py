"""Convert the recognized unversioned schema into sources and output feeds."""

import sqlite3


LEGACY_COLUMNS = {
    "feeds": {"id", "name", "source_url", "enabled", "refresh_interval", "last_fetched_at", "last_error", "created_at"},
    "entries": {"id", "feed_id", "guid", "title", "url", "content", "author", "category", "published", "fetched_at", "hidden"},
    "filter_rules": {"id", "feed_id", "field", "operator", "value", "action", "enabled"},
    "users": {"id", "google_sub", "email", "name", "picture_url", "created_at", "last_login_at"},
    "user_permissions": {"user_id", "permission", "granted_at"},
    "config_state": {"id", "revision"},
}
CORE_TABLES = {"feeds", "entries", "filter_rules"}
NEW_OBJECTS = {"sources", "feed_entry_visibility", "idx_feeds_source",
               "idx_entries_source_published", "idx_visibility_entry", "idx_visibility_feed_hidden"}


def create_schema(conn: sqlite3.Connection, schema: str) -> None:
    # executescript() implicitly commits; individual statements preserve the
    # caller's transaction, including DDL and the schema version update.
    for statement in schema.split(";"):
        if statement.strip():
            conn.execute(statement)


def migrate(conn: sqlite3.Connection, schema: str) -> None:
    tables = {row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    )}
    if not tables:
        create_schema(conn, schema)
        return
    objects = {row[0] for row in conn.execute("SELECT name FROM sqlite_master")}
    missing = CORE_TABLES - tables
    conflicts = NEW_OBJECTS & objects
    if missing or conflicts:
        raise RuntimeError(
            f"Unrecognized legacy database tables; missing core tables: {sorted(missing)}; "
            f"conflicting migration objects: {sorted(conflicts)}; migration aborted"
        )
    # Older installations may lack authentication/configuration tables. Other
    # historical feature tables are kept, not rebuilt or silently discarded.
    unexpected = conn.execute(
        "SELECT name FROM sqlite_master WHERE type IN ('view','trigger') OR "
        "(type='index' AND tbl_name IN ('feeds','entries','filter_rules') "
        "AND sql IS NOT NULL AND name!='idx_entries_feed_visible')"
    ).fetchall()
    if unexpected:
        raise RuntimeError("Unrecognized legacy database indexes, triggers or views; migration aborted")
    for table, required in LEGACY_COLUMNS.items():
        if table not in tables:
            continue
        columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        optional = {"source_title", "icon_url"} if table == "feeds" else set()
        if not required <= columns or columns - required - optional:
            raise RuntimeError(f"Unrecognized legacy database layout: {table}; migration aborted")
    if conn.execute("PRAGMA foreign_key_check").fetchall():
        raise RuntimeError("Legacy database has broken foreign keys; migration aborted")

    feeds = [dict(row) for row in conn.execute("SELECT * FROM feeds")]
    entries = [dict(row) for row in conn.execute("SELECT * FROM entries")]
    rules = [dict(row) for row in conn.execute("SELECT * FROM filter_rules")]
    sequences = dict(conn.execute("SELECT name,seq FROM sqlite_sequence"))
    for table in ("filter_rules", "entries", "feeds"):
        conn.execute(f"DROP TABLE {table}")
    create_schema(conn, schema)
    for feed in feeds:
        conn.execute(
            "INSERT INTO sources(id,source_url,enabled,refresh_interval,last_fetched_at,last_error,source_title,icon_url,created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (feed["id"], feed["source_url"], feed["enabled"], feed["refresh_interval"],
             feed["last_fetched_at"], feed["last_error"], feed.get("source_title"),
             feed.get("icon_url"), feed["created_at"]),
        )
        conn.execute("INSERT INTO feeds(id,source_id,name,created_at) VALUES(?,?,?,?)", (
            feed["id"], feed["id"], feed.get("source_title") or feed["name"], feed["created_at"],
        ))
    for rule in rules:
        conn.execute("INSERT INTO filter_rules(id,feed_id,field,operator,value,action,enabled) VALUES(?,?,?,?,?,?,?)",
                     tuple(rule[key] for key in ("id", "feed_id", "field", "operator", "value", "action", "enabled")))
    for entry in entries:
        conn.execute(
            "INSERT INTO entries(id,source_id,guid,title,url,content,author,category,published,fetched_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            tuple(entry[key] for key in ("id", "feed_id", "guid", "title", "url", "content", "author", "category", "published", "fetched_at")),
        )
        conn.execute("INSERT INTO feed_entry_visibility(feed_id,entry_id,hidden) VALUES(?,?,?)",
                     (entry["feed_id"], entry["id"], entry["hidden"]))
    # Preserve high-water marks so deleted public output IDs are not reused.
    for table in ("feeds", "entries", "filter_rules"):
        if table in sequences:
            conn.execute("DELETE FROM sqlite_sequence WHERE name=?", (table,))
            conn.execute("INSERT INTO sqlite_sequence(name,seq) VALUES(?,?)", (table, sequences[table]))
    for table, rows in (("feeds", feeds), ("sources", feeds), ("entries", entries),
                        ("feed_entry_visibility", entries), ("filter_rules", rules)):
        if conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] != len(rows):
            raise RuntimeError(f"Migration row count mismatch: {table}")

import json
import sqlite3
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator

from .db import Database
from .models import utcnow
from .version import APP_VERSION, CONFIG_SCHEMA_VERSION


class ConfigRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field: Literal["title", "content", "author", "category", "all"]
    operator: Literal["contains", "regex", "equals"]
    value: str = Field(min_length=1, max_length=1000)
    action: Literal["exclude", "include"]
    enabled: bool = True


class ConfigFeed(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    source_url: HttpUrl
    enabled: bool = True
    refresh_interval: int = Field(ge=1, le=10080)
    rules: list[ConfigRule] = Field(default_factory=list)


class ConfigDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    app_version: str = Field(min_length=1)
    schema_version: int = Field(ge=1)
    config_version: int = Field(ge=1)
    exported_at: str | None = None
    feeds: list[ConfigFeed]

    @model_validator(mode="after")
    def unique_feed_urls(self):
        urls = [str(feed.source_url) for feed in self.feeds]
        if len(urls) != len(set(urls)):
            raise ValueError("Feed-URLs müssen innerhalb einer Konfiguration eindeutig sein")
        return self


def bump_config_revision(conn: sqlite3.Connection) -> None:
    conn.execute("UPDATE config_state SET revision = revision + 1 WHERE id = 1")


class ConfigurationService:
    def __init__(self, db: Database):
        self.db = db

    def revision(self) -> int:
        with self.db.connect() as conn:
            return conn.execute("SELECT revision FROM config_state WHERE id=1").fetchone()[0]

    def export(self) -> ConfigDocument:
        with self.db.connect() as conn:
            feeds = []
            for row in conn.execute("SELECT * FROM feeds ORDER BY id"):
                rules = [ConfigRule(
                    field=rule["field"], operator=rule["operator"], value=rule["value"],
                    action=rule["action"], enabled=bool(rule["enabled"]),
                ) for rule in conn.execute(
                    "SELECT * FROM filter_rules WHERE feed_id=? ORDER BY id", (row["id"],)
                )]
                feeds.append(ConfigFeed(
                    name=row["name"], source_url=row["source_url"], enabled=bool(row["enabled"]),
                    refresh_interval=row["refresh_interval"], rules=rules,
                ))
            revision = conn.execute("SELECT revision FROM config_state WHERE id=1").fetchone()[0]
        return ConfigDocument(
            app_version=APP_VERSION, schema_version=CONFIG_SCHEMA_VERSION,
            config_version=revision, exported_at=utcnow(), feeds=feeds,
        )

    def export_json(self) -> str:
        return json.dumps(self.export().model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n"

    def import_document(self, document: ConfigDocument) -> int:
        if document.schema_version != CONFIG_SCHEMA_VERSION:
            raise ValueError(
                f"Schema-Version {document.schema_version} ist nicht kompatibel; "
                f"unterstützt wird Version {CONFIG_SCHEMA_VERSION}"
            )
        with self.db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("DELETE FROM feeds")
            for feed in document.feeds:
                cursor = conn.execute(
                    "INSERT INTO feeds(name,source_url,enabled,refresh_interval,created_at) VALUES(?,?,?,?,?)",
                    (feed.name, str(feed.source_url), int(feed.enabled), feed.refresh_interval, utcnow()),
                )
                for rule in feed.rules:
                    conn.execute(
                        "INSERT INTO filter_rules(feed_id,field,operator,value,action,enabled) "
                        "VALUES(?,?,?,?,?,?)",
                        (cursor.lastrowid, rule.field, rule.operator, rule.value,
                         rule.action, int(rule.enabled)),
                    )
            conn.execute("UPDATE config_state SET revision=? WHERE id=1", (document.config_version,))
        return len(document.feeds)

import json
import sqlite3
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator

from .db import Database
from .fields import RuleField
from .models import utcnow
from .version import APP_VERSION, CONFIG_SCHEMA_VERSION


class ConfigRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field: RuleField
    operator: Literal["contains", "regex", "equals"]
    value: str = Field(min_length=1, max_length=1000)
    action: Literal["exclude", "include"]
    enabled: bool = True


class ConfigSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1)
    source_url: HttpUrl
    enabled: bool = True
    refresh_interval: int = Field(ge=1, le=10080)


class ConfigFeed(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    source: str = Field(min_length=1)
    rules: list[ConfigRule] = Field(default_factory=list)


class ConfigDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    app_version: str = Field(min_length=1)
    schema_version: Literal[3]
    config_version: int = Field(ge=1)
    exported_at: str | None = None
    sources: list[ConfigSource]
    feeds: list[ConfigFeed]

    @model_validator(mode="after")
    def valid_sources(self):
        urls = [str(source.source_url) for source in self.sources]
        if len(urls) != len(set(urls)):
            raise ValueError("Feed-URLs müssen innerhalb einer Konfiguration eindeutig sein")
        keys = [source.key for source in self.sources]
        if len(keys) != len(set(keys)):
            raise ValueError("Quellenschlüssel müssen eindeutig sein")
        if any(feed.source not in keys for feed in self.feeds):
            raise ValueError("Feed verweist auf eine unbekannte Quelle")
        return self


def parse_document(payload: dict) -> ConfigDocument:
    if not isinstance(payload, dict):
        raise ValueError("Konfiguration muss ein JSON-Objekt sein")
    version = payload.get("schema_version")
    if type(version) is not int or version not in (1, 2, CONFIG_SCHEMA_VERSION):
        raise ValueError(f"Schema-Version {version} ist nicht kompatibel; unterstützt werden Versionen 1, 2 und 3")
    if version == 1:
        from .config_migrations.v1_to_v2 import migrate

        payload = migrate(payload)
    if version in (1, 2):
        from .config_migrations.v2_to_v3 import migrate

        payload = migrate(payload)
    return ConfigDocument.model_validate(payload)


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
            conn.execute("BEGIN")
            sources = [ConfigSource(
                key=f"source-{row['id']}", source_url=row["source_url"],
                enabled=bool(row["enabled"]), refresh_interval=row["refresh_interval"],
            ) for row in conn.execute("SELECT * FROM sources ORDER BY id")]
            feeds = []
            for row in conn.execute("SELECT * FROM feeds ORDER BY id"):
                rules = [ConfigRule(
                    field=rule["field"], operator=rule["operator"], value=rule["value"],
                    action=rule["action"], enabled=bool(rule["enabled"]),
                ) for rule in conn.execute(
                    "SELECT * FROM filter_rules WHERE feed_id=? ORDER BY id", (row["id"],)
                )]
                feeds.append(ConfigFeed(
                    name=row["name"], source=f"source-{row['source_id']}", rules=rules,
                ))
            revision = conn.execute("SELECT revision FROM config_state WHERE id=1").fetchone()[0]
        return ConfigDocument(
            app_version=APP_VERSION, schema_version=CONFIG_SCHEMA_VERSION,
            config_version=revision, exported_at=utcnow(), sources=sources, feeds=feeds,
        )

    def export_json(self) -> str:
        return json.dumps(self.export().model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n"

    def import_document(self, document: ConfigDocument) -> int:
        document = ConfigDocument.model_validate(document.model_dump())
        with self.db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("DELETE FROM sources")
            source_ids = {}
            for source in document.sources:
                source_ids[source.key] = conn.execute(
                    "INSERT INTO sources(source_url,enabled,refresh_interval,created_at) VALUES(?,?,?,?)",
                    (str(source.source_url), int(source.enabled), source.refresh_interval, utcnow()),
                ).lastrowid
            for feed in document.feeds:
                cursor = conn.execute(
                    "INSERT INTO feeds(name,source_id,created_at) VALUES(?,?,?)",
                    (feed.name, source_ids[feed.source], utcnow()),
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

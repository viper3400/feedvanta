"""Frozen schema 2 validation before admitting original-field identifiers."""

from typing import Literal

from pydantic import Field

from ..configuration import ConfigDocument, ConfigFeed, ConfigRule


class LegacyRule(ConfigRule):
    field: Literal["title", "content", "author", "category", "all"]


class LegacyFeed(ConfigFeed):
    rules: list[LegacyRule] = Field(default_factory=list)


class LegacyDocument(ConfigDocument):
    schema_version: Literal[2]
    feeds: list[LegacyFeed]


def migrate(payload: dict) -> dict:
    result = LegacyDocument.model_validate(payload).model_dump(mode="json")
    result["schema_version"] = 3
    return result

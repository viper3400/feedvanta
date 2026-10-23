from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator

from ..configuration import ConfigRule


class LegacyFeed(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    source_url: HttpUrl
    enabled: bool = True
    refresh_interval: int = Field(ge=1, le=10080)
    rules: list[ConfigRule] = Field(default_factory=list)


class LegacyDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    app_version: str = Field(min_length=1)
    schema_version: Literal[1]
    config_version: int = Field(ge=1)
    exported_at: str | None = None
    feeds: list[LegacyFeed]

    @model_validator(mode="after")
    def unique_urls(self):
        urls = [str(feed.source_url) for feed in self.feeds]
        if len(urls) != len(set(urls)):
            raise ValueError("Feed-URLs müssen eindeutig sein")
        return self


def migrate(payload: dict) -> dict:
    legacy = LegacyDocument.model_validate(payload)
    result = legacy.model_dump(mode="json", exclude={"feeds"})
    result.update(schema_version=2, sources=[], feeds=[])
    for index, feed in enumerate(legacy.feeds, start=1):
        key = f"source-{index}"
        result["sources"].append({
            "key": key, "source_url": str(feed.source_url),
            "enabled": feed.enabled, "refresh_interval": feed.refresh_interval,
        })
        result["feeds"].append({
            "name": feed.name, "source": key,
            "rules": [rule.model_dump() for rule in feed.rules],
        })
    return result

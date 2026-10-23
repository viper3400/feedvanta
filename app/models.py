from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

Field = Literal["title", "content", "author", "category", "all"]
Operator = Literal["contains", "regex", "equals"]
Action = Literal["exclude", "include"]


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def publication_timestamp(value: str | None) -> float | None:
    """Compare ISO publication instants, including offsets and microseconds."""
    if not value:
        return None
    try:
        published = datetime.fromisoformat(value)
        if published.tzinfo is None:
            published = published.replace(tzinfo=timezone.utc)
        return published.timestamp()
    except (ValueError, TypeError, OverflowError):
        return None


@dataclass(slots=True)
class ContentEvaluator:
    """Extension point for a future local or hosted AI evaluator."""

    async def evaluate(self, entry: dict) -> dict:
        return {}

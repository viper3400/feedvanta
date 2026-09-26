from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

Field = Literal["title", "content", "author", "category", "all"]
Operator = Literal["contains", "regex", "equals"]
Action = Literal["exclude", "include"]


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(slots=True)
class ContentEvaluator:
    """Extension point for a future local or hosted AI evaluator."""

    async def evaluate(self, entry: dict) -> dict:
        return {}

import json
import re
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from hashlib import sha256
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

from .db import Database
from .fields import field_label
from .models import utcnow
from .source_fields import MAX_FEED_BYTES, parse_source

ENTRY_RETENTION = timedelta(days=7)


def _entry_text(entry: dict, field: str) -> str:
    values = {
        "title": entry.get("title", ""),
        "content": entry.get("content", ""),
        "author": entry.get("author", ""),
        "category": entry.get("category", ""),
    }
    if field == "all":
        return "\n".join(values.values())
    return values[field]


def rule_matches(entry: dict, rule: dict) -> bool:
    if rule["field"].startswith("xml:"):
        fields = entry.get("custom_fields", {})
        if isinstance(fields, str):
            fields = json.loads(fields)
        texts = [text for text in fields.get(rule["field"], []) if text]
    else:
        texts = [_entry_text(entry, rule["field"])]
    value = rule["value"]
    if rule["operator"] == "contains":
        return any(value.casefold() in text.casefold() for text in texts)
    if rule["operator"] == "equals":
        return any(value.casefold() == text.casefold() for text in texts)
    try:
        return any(re.search(value, text, re.IGNORECASE) is not None for text in texts)
    except re.error:
        return False


def is_hidden(entry: dict, rules: list[dict]) -> bool:
    active = [r for r in rules if r["enabled"]]
    includes = [r for r in active if r["action"] == "include"]
    excludes = [r for r in active if r["action"] == "exclude"]
    if any(rule_matches(entry, r) for r in excludes):
        return True
    return bool(includes) and not any(rule_matches(entry, r) for r in includes)


def _rule_description(rule: dict) -> str:
    field = field_label(rule["field"]) if rule["field"].startswith("xml:") else rule["field"]
    return f"{field} {rule['operator']} „{rule['value']}“"


def hidden_reasons(entry: dict, rules: list[dict]) -> list[str]:
    active = [rule for rule in rules if rule["enabled"]]
    excludes = [rule for rule in active if rule["action"] == "exclude"]
    matched_excludes = [rule for rule in excludes if rule_matches(entry, rule)]
    if matched_excludes:
        return [
            f"Ausschlussregel: {_rule_description(rule)}"
            for rule in matched_excludes
        ]

    includes = [rule for rule in active if rule["action"] == "include"]
    if includes and not any(rule_matches(entry, rule) for rule in includes):
        return [
            "Keine aktive Einschlussregel trifft zu: "
            + "; ".join(_rule_description(rule) for rule in includes)
        ]
    return []


def _published(value: Any) -> str | None:
    if not value:
        return None
    try:
        return parsedate_to_datetime(str(value)).astimezone(timezone.utc).isoformat()
    except (TypeError, ValueError, OverflowError):
        return str(value)


def normalize_entry(raw: Any) -> dict:
    content_parts = raw.get("content") or []
    content = "\n".join(part.get("value", "") for part in content_parts)
    if not content:
        content = raw.get("summary", "")
    categories = ", ".join(tag.get("term", "") for tag in raw.get("tags", []))
    url = raw.get("link", "")
    title = raw.get("title", "")
    guid = raw.get("id") or url or sha256((title + content).encode()).hexdigest()
    return {
        "guid": str(guid), "title": title, "url": url, "content": content,
        "author": raw.get("author", ""), "category": categories,
        "published": _published(raw.get("published") or raw.get("updated")),
    }


def feed_metadata(parsed: Any, source_url: str) -> tuple[str | None, str | None]:
    metadata = getattr(parsed, "feed", {})
    title = str(metadata.get("title", "")).strip() or None
    image = metadata.get("image") or {}
    icon = image.get("href") or image.get("url") or metadata.get("icon") or metadata.get("logo")
    return title, urljoin(source_url, str(icon)) if icon else None


class _SiteIconParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.icons: list[tuple[int, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "link":
            return
        attributes = {name.lower(): value or "" for name, value in attrs}
        relations = attributes.get("rel", "").lower().split()
        href = attributes.get("href", "").strip()
        if "icon" not in relations or not href or href.lower().startswith("data:"):
            return
        media_type = attributes.get("type", "").lower()
        priority = 0 if "svg" in media_type else 1 if "png" in media_type else 2
        self.icons.append((priority, href))


def site_icon_url(html: str, page_url: str) -> str | None:
    parser = _SiteIconParser()
    parser.feed(html)
    if not parser.icons:
        return None
    _, href = min(parser.icons, key=lambda icon: icon[0])
    icon_url = urljoin(page_url, href)
    return icon_url if urlparse(icon_url).scheme in {"http", "https"} else None


def fetch_site_icon(page_url: str) -> str | None:
    try:
        request = Request(page_url, headers={"User-Agent": "FeedVanta/0.1"})
        with urlopen(request, timeout=5) as response:
            html = response.read().decode("utf-8", errors="replace")
            return site_icon_url(html, response.geturl())
    except Exception:
        return None


class FeedService:
    def __init__(self, db: Database):
        self.db = db

    def refresh(self, feed_id: int) -> int:
        """Compatibility entry point: refreshing an output refreshes its source."""
        with self.db.connect() as conn:
            feed = conn.execute("SELECT source_id FROM feeds WHERE id=?", (feed_id,)).fetchone()
            if not feed:
                raise LookupError("Feed nicht gefunden")
        return self.refresh_source(feed["source_id"])

    def refresh_source(self, source_id: int) -> int:
        with self.db.connect() as conn:
            source = conn.execute("SELECT * FROM sources WHERE id=?", (source_id,)).fetchone()
            if not source:
                raise LookupError("Quelle nicht gefunden")
        try:
            request = Request(source["source_url"], headers={"User-Agent": "FeedVanta/0.1"})
            with urlopen(request, timeout=20) as response:
                data = response.read(MAX_FEED_BYTES + 1)
            parsed, original_fields, marker, extraction_error = parse_source(data)
            if getattr(parsed, "bozo", False) and not parsed.entries:
                raise ValueError(str(parsed.bozo_exception))
            source_title, icon_url = feed_metadata(parsed, source["source_url"])
            if not icon_url:
                site_url = getattr(parsed, "feed", {}).get("link") or source["source_url"]
                icon_url = fetch_site_icon(str(site_url))
            now = utcnow()
            with self.db.connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                for raw in parsed.entries:
                    entry = normalize_entry(raw)
                    conn.execute("""
                        INSERT INTO entries
                          (source_id,guid,title,url,content,author,category,published,fetched_at,custom_fields)
                        VALUES (?,?,?,?,?,?,?,?,?,?)
                        ON CONFLICT(source_id,guid) DO UPDATE SET
                          title=excluded.title,url=excluded.url,content=excluded.content,
                          author=excluded.author,category=excluded.category,
                          published=excluded.published,fetched_at=excluded.fetched_at,
                          custom_fields=excluded.custom_fields
                    """, (source_id, entry["guid"], entry["title"], entry["url"],
                          entry["content"], entry["author"], entry["category"],
                          entry["published"], now,
                          json.dumps(original_fields.get(raw.get(marker), {}), ensure_ascii=False)))
                for output in conn.execute("SELECT id FROM feeds WHERE source_id=?", (source_id,)).fetchall():
                    self.reapply_rules_in_transaction(conn, output["id"])
                conn.execute(
                    "UPDATE sources SET last_fetched_at=?, last_error=NULL, "
                    "source_title=COALESCE(?,source_title), icon_url=COALESCE(?,icon_url), "
                    "field_extraction_error=? WHERE id=?",
                    (now, source_title, icon_url, extraction_error, source_id),
                )
            return len(parsed.entries)
        except Exception as exc:
            with self.db.connect() as conn:
                conn.execute("UPDATE sources SET last_error=? WHERE id=?", (str(exc)[:500], source_id))
            raise

    def reapply_rules(self, feed_id: int) -> None:
        with self.db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self.reapply_rules_in_transaction(conn, feed_id)

    @staticmethod
    def reapply_rules_in_transaction(conn, feed_id: int) -> None:
        feed = conn.execute("SELECT source_id FROM feeds WHERE id=?", (feed_id,)).fetchone()
        if not feed:
            raise LookupError("Feed nicht gefunden")
        rules = [dict(r) for r in conn.execute(
            "SELECT * FROM filter_rules WHERE feed_id=?", (feed_id,)
        )]
        entries = conn.execute("SELECT * FROM entries WHERE source_id=?", (feed["source_id"],)).fetchall()
        for row in entries:
            entry = dict(row)
            entry["custom_fields"] = json.loads(entry["custom_fields"])
            conn.execute(
                "INSERT INTO feed_entry_visibility(feed_id,entry_id,hidden) VALUES(?,?,?) "
                "ON CONFLICT(feed_id,entry_id) DO UPDATE SET hidden=excluded.hidden",
                (feed_id, row["id"], int(is_hidden(entry, rules))),
            )

    def prune_expired_entries(self) -> None:
        cutoff = (datetime.now(timezone.utc) - ENTRY_RETENTION).isoformat()
        with self.db.connect() as conn:
            conn.execute(
                "DELETE FROM entries WHERE datetime(fetched_at) < datetime(?)",
                (cutoff,),
            )

    def refresh_due(self) -> None:
        now = datetime.now(timezone.utc)
        self.prune_expired_entries()
        with self.db.connect() as conn:
            sources = conn.execute("SELECT * FROM sources WHERE enabled = 1").fetchall()
        for source in sources:
            last = datetime.fromisoformat(source["last_fetched_at"]) if source["last_fetched_at"] else None
            if last is None or (now - last).total_seconds() >= source["refresh_interval"] * 60:
                try:
                    self.refresh_source(source["id"])
                except Exception:
                    pass

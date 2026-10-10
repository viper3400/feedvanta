import re
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from hashlib import sha256
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

import feedparser

from .db import Database
from .models import utcnow

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
    text = _entry_text(entry, rule["field"])
    value = rule["value"]
    if rule["operator"] == "contains":
        return value.casefold() in text.casefold()
    if rule["operator"] == "equals":
        return value.casefold() == text.casefold()
    try:
        return re.search(value, text, re.IGNORECASE) is not None
    except re.error:
        return False


def is_hidden(entry: dict, rules: list[dict]) -> bool:
    active = [r for r in rules if r["enabled"]]
    includes = [r for r in active if r["action"] == "include"]
    excludes = [r for r in active if r["action"] == "exclude"]
    if any(rule_matches(entry, r) for r in excludes):
        return True
    return bool(includes) and not any(rule_matches(entry, r) for r in includes)


def hidden_reasons(entry: dict, rules: list[dict]) -> list[str]:
    active = [rule for rule in rules if rule["enabled"]]
    excludes = [rule for rule in active if rule["action"] == "exclude"]
    matched_excludes = [rule for rule in excludes if rule_matches(entry, rule)]
    if matched_excludes:
        return [
            f"Ausschlussregel: {rule['field']} {rule['operator']} „{rule['value']}“"
            for rule in matched_excludes
        ]

    includes = [rule for rule in active if rule["action"] == "include"]
    if includes and not any(rule_matches(entry, rule) for rule in includes):
        return [
            "Keine aktive Einschlussregel trifft zu: "
            + "; ".join(
                f"{rule['field']} {rule['operator']} „{rule['value']}“"
                for rule in includes
            )
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
        with self.db.connect() as conn:
            feed = conn.execute("SELECT * FROM feeds WHERE id = ?", (feed_id,)).fetchone()
            if not feed:
                raise LookupError("Feed nicht gefunden")
            rules = [dict(r) for r in conn.execute(
                "SELECT * FROM filter_rules WHERE feed_id = ?", (feed_id,)
            )]
        try:
            request = Request(feed["source_url"], headers={"User-Agent": "FeedVanta/0.1"})
            with urlopen(request, timeout=20) as response:
                parsed = feedparser.parse(response.read())
            if getattr(parsed, "bozo", False) and not parsed.entries:
                raise ValueError(str(parsed.bozo_exception))
            source_title, icon_url = feed_metadata(parsed, feed["source_url"])
            if not icon_url:
                site_url = getattr(parsed, "feed", {}).get("link") or feed["source_url"]
                icon_url = fetch_site_icon(str(site_url))
            now = utcnow()
            with self.db.connect() as conn:
                for raw in parsed.entries:
                    entry = normalize_entry(raw)
                    entry["hidden"] = int(is_hidden(entry, rules))
                    conn.execute("""
                        INSERT INTO entries
                          (feed_id,guid,title,url,content,author,category,published,fetched_at,hidden)
                        VALUES (?,?,?,?,?,?,?,?,?,?)
                        ON CONFLICT(feed_id,guid) DO UPDATE SET
                          title=excluded.title,url=excluded.url,content=excluded.content,
                          author=excluded.author,category=excluded.category,
                          published=excluded.published,fetched_at=excluded.fetched_at,
                          hidden=excluded.hidden
                    """, (feed_id, entry["guid"], entry["title"], entry["url"],
                          entry["content"], entry["author"], entry["category"],
                          entry["published"], now, entry["hidden"]))
                conn.execute(
                    "UPDATE feeds SET last_fetched_at=?, last_error=NULL, "
                    "source_title=COALESCE(?,source_title), icon_url=COALESCE(?,icon_url) WHERE id=?",
                    (now, source_title, icon_url, feed_id),
                )
            return len(parsed.entries)
        except Exception as exc:
            with self.db.connect() as conn:
                conn.execute("UPDATE feeds SET last_error=? WHERE id=?", (str(exc)[:500], feed_id))
            raise

    def reapply_rules(self, feed_id: int) -> None:
        with self.db.connect() as conn:
            rules = [dict(r) for r in conn.execute(
                "SELECT * FROM filter_rules WHERE feed_id = ?", (feed_id,)
            )]
            entries = conn.execute("SELECT * FROM entries WHERE feed_id = ?", (feed_id,)).fetchall()
            for row in entries:
                conn.execute("UPDATE entries SET hidden=? WHERE id=?", (
                    int(is_hidden(dict(row), rules)), row["id"]
                ))

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
            feeds = conn.execute("SELECT * FROM feeds WHERE enabled = 1").fetchall()
        for feed in feeds:
            last = datetime.fromisoformat(feed["last_fetched_at"]) if feed["last_fetched_at"] else None
            if last is None or (now - last).total_seconds() >= feed["refresh_interval"] * 60:
                try:
                    self.refresh(feed["id"])
                except Exception:
                    pass

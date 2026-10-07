#!/usr/bin/env python3
"""Download media files from a prepared RSS or Atom feed."""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys
import time
import unicodedata
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urljoin, urlparse
from urllib.request import Request, urlopen

VIDEO_EXTENSIONS = {".mp4", ".m4v", ".webm", ".mkv", ".mov"}
MIME_EXTENSIONS = {
    "video/mp4": ".mp4",
    "video/x-m4v": ".m4v",
    "video/webm": ".webm",
    "video/x-matroska": ".mkv",
    "video/quicktime": ".mov",
}
USER_AGENT = "FeedVanta-Downloader/0.1"


@dataclass(frozen=True, slots=True)
class MediaItem:
    title: str
    identifier: str
    url: str
    extension: str


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def text_of(element: ET.Element, name: str) -> str:
    for child in element:
        if local_name(child.tag) == name and child.text:
            return child.text.strip()
    return ""


def extension_for(url: str, media_type: str = "") -> str | None:
    extension = Path(unquote(urlparse(url).path)).suffix.lower()
    if extension in VIDEO_EXTENSIONS:
        return extension
    return MIME_EXTENSIONS.get(media_type.split(";", 1)[0].strip().lower())


def choose_media(element: ET.Element, base_url: str) -> tuple[str, str] | None:
    enclosures: list[tuple[str, str]] = []
    fallback_links: list[str] = []
    for child in element:
        name = local_name(child.tag)
        if name == "enclosure":
            url = child.attrib.get("url", "").strip()
            if url:
                enclosures.append((urljoin(base_url, url), child.attrib.get("type", "")))
        elif name == "link":
            href = child.attrib.get("href", "").strip()
            relation = child.attrib.get("rel", "alternate")
            if href and relation == "enclosure":
                enclosures.append((urljoin(base_url, href), child.attrib.get("type", "")))
            elif href and relation in {"", "alternate"}:
                fallback_links.append(urljoin(base_url, href))
            elif child.text and child.text.strip():
                fallback_links.append(urljoin(base_url, child.text.strip()))

    for url, media_type in enclosures:
        if extension := extension_for(url, media_type):
            return url, extension
    for url in fallback_links:
        if extension := extension_for(url):
            return url, extension
    return None


def parse_feed(xml_data: bytes, base_url: str = "") -> tuple[list[MediaItem], int]:
    root = ET.fromstring(xml_data)
    elements = [element for element in root.iter() if local_name(element.tag) in {"item", "entry"}]
    items: list[MediaItem] = []
    skipped = 0
    seen: set[str] = set()
    for index, element in enumerate(elements, start=1):
        media = choose_media(element, base_url)
        if not media:
            skipped += 1
            continue
        url, extension = media
        title = text_of(element, "title") or f"video-{index}"
        identifier = text_of(element, "guid") or text_of(element, "id") or url
        identity = hashlib.sha256(identifier.encode("utf-8")).hexdigest()[:12]
        if identity in seen:
            skipped += 1
            continue
        seen.add(identity)
        items.append(MediaItem(title=title, identifier=identity, url=url, extension=extension))
    return items, skipped


def load_feed(source: str, timeout: int = 30) -> tuple[bytes, str]:
    if source == "-":
        return sys.stdin.buffer.read(), ""
    parsed = urlparse(source)
    if parsed.scheme in {"http", "https"}:
        request = Request(source, headers={"User-Agent": USER_AGENT})
        with urlopen(request, timeout=timeout) as response:
            return response.read(), response.geturl()
    path = Path(source).expanduser().resolve()
    return path.read_bytes(), path.as_uri()


def safe_filename(item: MediaItem) -> str:
    normalized = unicodedata.normalize("NFKD", item.title).encode("ascii", "ignore").decode()
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", normalized).strip("-._")[:120]
    return f"{stem or 'video'}-{item.identifier}{item.extension}"


def download_item(
    item: MediaItem,
    output_dir: Path,
    attempts: int = 3,
    timeout: int = 60,
) -> tuple[MediaItem, Path | None, str | None]:
    target = output_dir / safe_filename(item)
    partial = target.with_suffix(target.suffix + ".part")
    last_error = "Unbekannter Downloadfehler"
    for attempt in range(1, attempts + 1):
        try:
            partial.unlink(missing_ok=True)
            request = Request(item.url, headers={"User-Agent": USER_AGENT})
            with urlopen(request, timeout=timeout) as response, partial.open("wb") as output:
                expected = response.headers.get("Content-Length")
                written = 0
                while chunk := response.read(1024 * 1024):
                    output.write(chunk)
                    written += len(chunk)
                if expected and expected.isdigit() and written != int(expected):
                    raise OSError(f"Unvollständiger Download: {written} von {expected} Bytes")
            os.replace(partial, target)
            return item, target, None
        except Exception as exc:
            last_error = str(exc)
            partial.unlink(missing_ok=True)
            if attempt < attempts:
                time.sleep(2 ** (attempt - 1))
    return item, None, last_error


def positive_integer(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("muss mindestens 1 sein")
    return number


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Medien aus einem vorbereiteten RSS-/Atom-Feed laden")
    parser.add_argument("feed", help="HTTP(S)-URL, lokale XML-Datei oder '-' für stdin")
    parser.add_argument("--output", type=Path, default=Path("downloads"), help="Zielordner (Standard: ./downloads)")
    parser.add_argument("--workers", type=positive_integer, default=3, help="Parallele Downloads (Standard: 3)")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        args.output.mkdir(parents=True, exist_ok=True)
        xml_data, base_url = load_feed(args.feed)
        items, skipped = parse_feed(xml_data, base_url)
    except (OSError, ET.ParseError, ValueError) as exc:
        print(f"Fehler: Feed konnte nicht geladen werden: {exc}", file=sys.stderr)
        return 2

    print(f"{len(items)} Medien gefunden, {skipped} Einträge übersprungen.")
    succeeded = 0
    failures: list[tuple[MediaItem, str]] = []
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = [executor.submit(download_item, item, args.output) for item in items]
        for future in as_completed(futures):
            item, target, error = future.result()
            if error:
                failures.append((item, error))
                print(f"FEHLER  {item.title}: {error}", file=sys.stderr)
            else:
                succeeded += 1
                print(f"OK      {item.title} -> {target}")

    print(f"Fertig: {succeeded} heruntergeladen, {skipped} übersprungen, {len(failures)} fehlgeschlagen.")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

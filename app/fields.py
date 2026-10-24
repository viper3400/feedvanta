"""Shared identifiers and presentation for normalized and original XML fields."""

import json
from typing import Annotated
from xml.parsers import expat

from pydantic import BeforeValidator

STANDARD_FIELDS = {"all": "alles", "title": "Titel", "content": "Inhalt", "author": "Autor", "category": "Kategorie"}
MAX_FIELD_ID = 4096


def xml_field(namespace: str, local_name: str) -> str:
    return "xml:" + json.dumps([namespace, local_name], ensure_ascii=False, separators=(",", ":"))


def validate_field(value: str) -> str:
    if not isinstance(value, str) or len(value) > MAX_FIELD_ID:
        raise ValueError("Ungültiges Regelfeld")
    if value in STANDARD_FIELDS:
        return value
    if not value.startswith("xml:"):
        raise ValueError("Unbekanntes Regelfeld")
    try:
        parts = json.loads(value[4:])
        if not isinstance(parts, list) or len(parts) != 2 or not all(isinstance(p, str) for p in parts):
            raise ValueError
        namespace, name = parts
        namespace.encode("utf-8")
        name.encode("utf-8")
        if not name or ":" in name or any(ord(c) < 32 for c in namespace):
            raise ValueError
        # Let the XML parser validate Unicode XML names, not an ASCII-only regex.
        parser = expat.ParserCreate()
        names = []
        parser.StartElementHandler = lambda tag, attrs: names.append((tag, attrs))
        parser.Parse(f"<{name}/>", True)
        if names != [(name, {})]:
            raise ValueError
        canonical = xml_field(namespace, name)
        if len(canonical) > MAX_FIELD_ID:
            raise ValueError
        return canonical
    except (ValueError, UnicodeError, expat.ExpatError) as exc:
        raise ValueError("Ungültiger XML-Feldbezeichner") from exc


RuleField = Annotated[str, BeforeValidator(validate_field)]


def field_info(identifier: str, available: bool = True) -> dict:
    if identifier in STANDARD_FIELDS:
        return {"id": identifier, "label": STANDARD_FIELDS[identifier], "namespace": None,
                "local_name": None, "available": True, "kind": "normalized"}
    namespace, name = json.loads(identifier[4:])
    return {"id": identifier, "label": f"{name} ({namespace})" if namespace else name,
            "namespace": namespace, "local_name": name, "available": available, "kind": "xml"}


def field_label(identifier: str) -> str:
    return field_info(identifier)["label"]


def field_catalog(conn, source_id: int, referenced=()) -> list[dict]:
    available = set()
    for row in conn.execute("SELECT custom_fields FROM entries WHERE source_id=?", (source_id,)):
        available.update(key for key, values in json.loads(row[0]).items() if any(values))
    identifiers = available | {key for key in referenced if key.startswith("xml:")}
    return ([field_info(key) for key in STANDARD_FIELDS]
            + [field_info(key, key in available) for key in sorted(identifiers)])

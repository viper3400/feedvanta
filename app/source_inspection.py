"""Bounded retrieval and safe formatting of original feed XML."""

from urllib.error import URLError
from urllib.request import Request, urlopen
from xml.dom import minidom
from xml.parsers import expat

MAX_SOURCE_BYTES = 2 * 1024 * 1024
SOURCE_TIMEOUT = 10


def inspect_source(source_url: str) -> str:
    request = Request(source_url, headers={"User-Agent": "FeedVanta/0.1"})
    try:
        with urlopen(request, timeout=SOURCE_TIMEOUT) as response:
            data = response.read(MAX_SOURCE_BYTES + 1)
    except (OSError, URLError, ValueError) as exc:
        raise ValueError("Die Originalquelle konnte nicht geladen werden.") from exc
    if len(data) > MAX_SOURCE_BYTES:
        raise ValueError("Die Originalquelle ist zu groß (maximal 2 MiB).")

    # Reject DTDs before DOM parsing: no entity expansion or external resources.
    parser = expat.ParserCreate()
    depth = 0

    def reject_doctype(*args):
        raise ValueError("XML mit DTD/Entitätsdefinitionen wird aus Sicherheitsgründen nicht angezeigt.")

    def start_element(*args):
        nonlocal depth
        depth += 1
        if depth > 100:
            raise ValueError("Die XML-Struktur ist zu tief verschachtelt (maximal 100 Ebenen).")

    def end_element(*args):
        nonlocal depth
        depth -= 1

    parser.StartDoctypeDeclHandler = reject_doctype
    parser.StartElementHandler = start_element
    parser.EndElementHandler = end_element
    try:
        parser.Parse(data, True)
        with minidom.parseString(data) as document:
            return document.toprettyxml(indent="  ")
    except expat.ExpatError as exc:
        raise ValueError("Die Originalquelle enthält kein gültiges XML.") from exc

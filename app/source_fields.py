"""Extract original fields and correlate them through explicit per-entry markers."""

from uuid import uuid4
from xml.dom import Node, minidom
from xml.parsers import expat

import feedparser

from .fields import validate_field, xml_field

MAX_FEED_BYTES = 5 * 1024 * 1024
MAX_DEPTH = 100
MAX_ENTRIES = 5000
MAX_FIELDS = 256
MAX_ENTRY_TEXT = 256 * 1024
MAX_TOTAL_TEXT = 8 * 1024 * 1024
ATOM = "http://www.w3.org/2005/Atom"
RSS = "http://purl.org/rss/1.0/"
RDF = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"


def validate_xml(data: bytes) -> None:
    parser = expat.ParserCreate(namespace_separator="\x1f")
    depth = 0

    def reject_dtd(*args):
        raise ValueError("DTD/Entitätsdefinitionen werden nicht unterstützt")

    def start(*args):
        nonlocal depth
        depth += 1
        if depth > MAX_DEPTH:
            raise ValueError("XML überschreitet 100 Verschachtelungsebenen")

    def end(*args):
        nonlocal depth
        depth -= 1

    parser.StartDoctypeDeclHandler = reject_dtd
    parser.StartElementHandler = start
    parser.EndElementHandler = end
    parser.Parse(data, True)


def _elements(node):
    return [child for child in node.childNodes if child.nodeType == Node.ELEMENT_NODE]


def _text(node):
    return "".join(child.data if child.nodeType in (Node.TEXT_NODE, Node.CDATA_SECTION_NODE)
                   else _text(child) if child.nodeType == Node.ELEMENT_NODE else ""
                   for child in node.childNodes)


def _parse_feed(data):
    parsed = feedparser.parse(data)
    if len(parsed.entries) > MAX_ENTRIES:
        raise ValueError("Quelle überschreitet 5000 Einträge")
    return parsed


def parse_source(data: bytes):
    """Return feedparser data, fields by marker, marker key, and extraction warning.

    Insert a random, unqualified marker into each actual source entry before the
    single feedparser pass. No GUID, positional zip, or normalized title matching
    is involved. DOM serialization preserves XML prefixes, CDATA and xml:base.
    Malformed XML keeps tolerant legacy parsing but provides no custom values.
    Security/size violations fail the refresh rather than falling back.
    """
    if len(data) > MAX_FEED_BYTES:
        raise ValueError("Quelle überschreitet 5 MiB")
    try:
        validate_xml(data)
    except (expat.ExpatError, LookupError):
        return _parse_feed(data), {}, "", "Ungültiges XML: Originalfelder sind für diesen Abruf nicht verfügbar."

    marker = "feedvanta_correlation_" + uuid4().hex
    fields_by_marker = {}
    total = 0
    with minidom.parseString(data) as document:
        root = document.documentElement
        if (root.namespaceURI, root.localName) == (ATOM, "feed"):
            entries = [node for node in _elements(root) if (node.namespaceURI, node.localName) == (ATOM, "entry")]
        elif (root.namespaceURI, root.localName) == (RDF, "RDF"):
            entries = [node for node in _elements(root) if (node.namespaceURI, node.localName) == (RSS, "item")]
        elif root.localName == "rss" and not root.namespaceURI:
            entries = [node for channel in _elements(root) if channel.localName == "channel" and not channel.namespaceURI
                       for node in _elements(channel) if node.localName == "item" and not node.namespaceURI]
        else:
            return _parse_feed(data), {}, "", "Feedformat ohne Originalfeld-Erkennung."
        if len(entries) > MAX_ENTRIES:
            raise ValueError("Quelle überschreitet 5000 Einträge")
        for index, entry in enumerate(entries):
            fields = {}
            size = 0
            children = _elements(entry)
            if len(children) > MAX_FIELDS:
                raise ValueError("Eintrag überschreitet 256 Originalfelder")
            for child in children:
                identifier = validate_field(xml_field(child.namespaceURI or "", child.localName))
                value = _text(child).strip()
                size += len(value.encode("utf-8"))
                if size > MAX_ENTRY_TEXT:
                    raise ValueError("Eintrag überschreitet 256 KiB Originalfeldtext")
                if value:
                    fields.setdefault(identifier, []).append(value)
            total += size
            if total > MAX_TOTAL_TEXT:
                raise ValueError("Quelle überschreitet 8 MiB Originalfeldtext")
            token = str(index)
            fields_by_marker[token] = fields
            node = document.createElement(marker)
            node.appendChild(document.createTextNode(token))
            entry.appendChild(node)
        parsed = _parse_feed(document.toxml(encoding="utf-8"))
    if any(raw.get(marker) not in fields_by_marker for raw in parsed.entries):
        return parsed, {}, marker, "Einträge ohne sichere XML-Zuordnung: Originalfelder sind für diesen Abruf nicht verfügbar."
    return parsed, fields_by_marker, marker, None

import json
from datetime import datetime, timedelta, timezone
from html import unescape
from io import BytesIO

import feedparser
import pytest
from fastapi.testclient import TestClient

from app.fields import field_info, validate_field, xml_field
from app.main import SubpathMiddleware, create_app
from app.services import hidden_reasons, normalize_entry, rule_matches
from app.source_fields import MAX_FEED_BYTES, parse_source
from test_app import TEST_SESSION_SECRET, authenticate


AUTHORS = xml_field("", "authors")


def rss(items, extra=""):
    return (f'<rss version="2.0" xmlns:dc="http://purl.org/dc/elements/1.1/" {extra}>'
            '<channel><title>Original</title><image><url>https://example.com/icon.png</url></image>'
            f'{items}</channel></rss>').encode()


def item(title="News", custom="<authors>hans dampf, Gaby Mueller, Redaktion</authors>", guid="1"):
    published = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S GMT")
    return f'<item><guid>{guid}</guid><title>{title}</title><pubDate>{published}</pubDate>{custom}</item>'


def extracted(data):
    parsed, fields, marker, warning = parse_source(data)
    assert warning is None
    return [(normalize_entry(raw), fields[raw[marker]]) for raw in parsed.entries]


@pytest.fixture
def configured(tmp_path):
    app = create_app(str(tmp_path / "test.db"), start_scheduler=False, session_secret=TEST_SESSION_SECRET)
    with TestClient(app) as client:
        authenticate(client, app)
        source = client.post("/api/sources", json={"name": "News", "source_url": "https://example.com/rss"}).json()
        yield app, client, source["id"], source["feed_id"]


def refresh(configured, monkeypatch, data):
    app, _, source_id, _ = configured
    calls = []

    def fetch(request, timeout):
        calls.append(request.full_url)
        return BytesIO(data)

    monkeypatch.setattr("app.services.urlopen", fetch)
    count = app.state.service.refresh_source(source_id)
    assert calls == ["https://example.com/rss"]
    return count


def rule(client, output, field=AUTHORS, value="Redaktion", operator="contains", action="exclude"):
    return client.post(f"/api/feeds/{output}/rules", json={
        "field": field, "operator": operator, "value": value, "action": action,
    })


def stored(app):
    with app.state.db.connect() as conn:
        return [dict(row) for row in conn.execute("SELECT * FROM entries ORDER BY id")]


def test_authors_rule_is_explicit_and_output_specific(configured, monkeypatch):
    app, client, source_id, first = configured
    second = client.post("/api/feeds", json={"name": "Sibling", "source_id": source_id}).json()["id"]
    revision = app.state.configuration_service.revision()
    assert refresh(configured, monkeypatch, rss(item())) == 1
    assert app.state.configuration_service.revision() == revision
    assert stored(app)[0]["author"] == ""
    assert json.loads(stored(app)[0]["custom_fields"])[AUTHORS] == ["hans dampf, Gaby Mueller, Redaktion"]
    # The old normalized author rule still does not match the extension.
    assert rule(client, second, field="author").status_code == 201
    created = rule(client, first)
    assert created.status_code == 201
    assert "<article" not in client.get(f"/reader/{first}").text
    assert "hans dampf" not in client.get(f"/feed/{second}.xml").text
    assert "<item>" not in client.get(f"/feed/{first}.xml").text
    assert "<item>" in client.get(f"/feed/{second}.xml").text
    details = client.get(f"/feeds/{first}").text
    assert 'label="Normalisierte Felder"' in details
    assert 'label="Originalfelder der Quelle"' in details
    assert "authors contains" in details
    assert "Originalfelder werden beim Abruf" in details
    assert f'value="{AUTHORS}" selected' in unescape(details)
    fields = client.get(f"/api/sources/{source_id}/fields").json()
    assert fields["extraction_error"] is None
    assert field_info(AUTHORS) in fields["fields"]
    # Rule editing immediately affects only the selected output.
    assert client.patch(f"/api/rules/{created.json()['id']}", json={
        "field": AUTHORS, "operator": "contains", "value": "Nobody", "action": "exclude",
    }).status_code == 200
    assert "<item>" in client.get(f"/feed/{first}.xml").text


@pytest.mark.parametrize("operator,value,expected", [
    ("contains", "redAKTION", True), ("equals", "redaktion", True),
    ("equals", "Ada\nRedaktion", False), ("regex", "^Red.*$", True),
    ("regex", "[", False), ("contains", "Nobody", False),
])
def test_repeated_values_match_individually(operator, value, expected):
    entry = {"custom_fields": json.dumps({AUTHORS: ["Ada", "Redaktion"]})}
    assert rule_matches(entry, {"field": AUTHORS, "operator": operator, "value": value}) is expected


def test_missing_values_and_all_keep_existing_meanings():
    for custom in ({}, {AUTHORS: []}, {AUTHORS: [""]}):
        assert not rule_matches({"custom_fields": custom}, {"field": AUTHORS, "operator": "regex", "value": "^$"})
    entry = {"title": "Ordinary", "custom_fields": {AUTHORS: ["Redaktion"]}}
    assert not rule_matches(entry, {"field": "all", "operator": "contains", "value": "Redaktion"})
    reasons = hidden_reasons(entry, [{"field": AUTHORS, "operator": "equals", "value": "Other", "action": "include", "enabled": 1}])
    assert "Keine aktive Einschlussregel" in reasons[0]
    assert "authors equals" in reasons[0]


def test_namespace_identity_repetitions_and_text():
    data = rss(item(custom='''<x:authors> Ada </x:authors><y:authors>Redaktion</y:authors>
        <z:authors>Different</z:authors><authors>Größe <![CDATA[<p>HTML</p>]]> &amp; Text</authors>
        <authors>  </authors><Authors>Uppercase</Authors><mixed>one<b>two</b> three</mixed>
        <empty href="only-an-attribute"/><wrapper><nested>inner</nested></wrapper>'''),
        'xmlns:x="urn:one" xmlns:y="urn:one" xmlns:z="urn:two"')
    _, fields = extracted(data)[0]
    assert fields[xml_field("urn:one", "authors")] == ["Ada", "Redaktion"]
    assert fields[xml_field("urn:two", "authors")] == ["Different"]
    assert fields[AUTHORS] == ["Größe <p>HTML</p> & Text"]
    assert fields[xml_field("", "Authors")] == ["Uppercase"]
    assert fields[xml_field("", "mixed")] == ["onetwo three"]
    assert fields[xml_field("", "wrapper")] == ["inner"]
    assert xml_field("", "nested") not in fields
    assert xml_field("", "empty") not in fields
    assert "urn:one" in field_info(xml_field("urn:one", "authors"))["label"]


@pytest.mark.parametrize("data", [
    rss(item(custom='<author>ada@example.com (Ada)</author><description><![CDATA[<p>Body</p>]]></description>')),
    rss(item(custom='<dc:creator>Ada</dc:creator><category>Tech</category>')),
    b'''<feed xmlns="http://www.w3.org/2005/Atom" xml:base="https://example.com/base/">
      <title>Atom</title><entry><title>A</title><link href="article"/>
      <author><name>Ada</name></author><content type="xhtml"><div xmlns="http://www.w3.org/1999/xhtml"><p>Body</p></div></content></entry></feed>''',
    b'''<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" xmlns="http://purl.org/rss/1.0/">
      <channel rdf:about="https://example.com"><title>RDF</title></channel>
      <item rdf:about="https://example.com/article"><title>RDF item</title><description>Body</description></item></rdf:RDF>''',
    '<?xml version="1.0" encoding="iso-8859-1"?><rss version="2.0"><channel><item><title>Größe</title><authors>Gaby Müller</authors></item></channel></rss>'.encode("iso-8859-1"),
])
def test_normalization_is_unchanged_for_supported_formats(data):
    original = [normalize_entry(raw) for raw in feedparser.parse(data).entries]
    result = extracted(data)
    assert [entry for entry, _ in result] == original
    assert all(fields for _, fields in result)


def test_correlation_does_not_depend_on_ids_or_parser_order(monkeypatch):
    data = rss(item("First", "<authors>First author</authors>", guid="same")
               + item("Second", "<authors>Second author</authors>", guid="same")
               + '<item><title>No ID</title><authors>Third author</authors></item>')
    parse = feedparser.parse

    def reorder_and_skip(data):
        result = parse(data)
        result.entries = [result.entries[2], result.entries[0]]
        return result

    monkeypatch.setattr("app.source_fields.feedparser.parse", reorder_and_skip)
    result = extracted(data)
    assert [(entry["title"], fields[AUTHORS]) for entry, fields in result] == [
        ("No ID", ["Third author"]), ("First", ["First author"]),
    ]


def test_duplicate_guid_upsert_keeps_matching_custom_values(configured, monkeypatch):
    app, _, _, _ = configured
    refresh(configured, monkeypatch, rss(item("First", "<authors>Ada</authors>")
                                       + item("Second", "<authors>Gaby</authors>")))
    assert len(stored(app)) == 1
    assert stored(app)[0]["title"] == "Second"
    assert json.loads(stored(app)[0]["custom_fields"])[AUTHORS] == ["Gaby"]


def test_field_lifecycle_unavailable_rules_and_new_outputs(configured, monkeypatch):
    app, client, source_id, first = configured
    created = rule(client, first)
    assert created.status_code == 201  # Valid before discovery.
    assert "derzeit nicht verfügbar" in client.get(f"/feeds/{first}").text
    refresh(configured, monkeypatch, rss(item()))
    second = client.post("/api/feeds", json={"name": "New", "source_id": source_id}).json()["id"]
    assert rule(client, second).status_code == 201
    assert "<item>" not in client.get(f"/feed/{second}.xml").text
    # Another refresh leaves an older retained article and its field available.
    refresh(configured, monkeypatch, rss(item("Other", "", guid="2")))
    assert field_info(AUTHORS) in client.get(f"/api/sources/{source_id}/fields").json()["fields"]
    # An update replaces custom fields, never merges stale values.
    refresh(configured, monkeypatch, rss(item("Updated", "", guid="1")))
    assert AUTHORS not in json.loads(stored(app)[0]["custom_fields"])
    assert AUTHORS not in [f["id"] for f in client.get(f"/api/sources/{source_id}/fields").json()["fields"]]
    details = client.get(f"/feeds/{first}").text
    assert "derzeit nicht verfügbar" in details
    assert f'value="{AUTHORS}" selected' in unescape(details)
    assert client.post(f"/rules/{created.json()['id']}/edit", data={
        "field": AUTHORS, "value": "Redaktion", "operator": "contains", "action": "exclude",
    }, follow_redirects=False).status_code == 303
    assert "<item>" in client.get(f"/feed/{first}.xml").text


def test_retention_removes_catalog_values_not_rules(configured, monkeypatch):
    app, client, source_id, first = configured
    refresh(configured, monkeypatch, rss(item()))
    rule(client, first)
    with app.state.db.connect() as conn:
        conn.execute("UPDATE entries SET fetched_at=?", ((datetime.now(timezone.utc) - timedelta(days=8)).isoformat(),))
    app.state.service.prune_expired_entries()
    assert AUTHORS not in [f["id"] for f in client.get(f"/api/sources/{source_id}/fields").json()["fields"]]
    assert "derzeit nicht verfügbar" in client.get(f"/feeds/{first}").text
    assert len(client.get("/api/feeds").json()[0]["rules"]) == 1


@pytest.mark.parametrize("field", ["unknown", "xml:[]", 'xml:["","bad name"]',
    'xml:["","x a=\"b\""]', 'xml:["","a:b"]', 'xml:["","1name"]',
    'xml:["",12]', 'xml:["","x",null]', 'xml:["\\u0000","x"]',
    'xml:["\\ud800","x"]', 'xml:["","x"] trailing', "x" * 4097])
def test_invalid_identifiers_rejected_everywhere(configured, field):
    app, client, _, first = configured
    revision = app.state.configuration_service.revision()
    with pytest.raises(ValueError):
        validate_field(field)
    assert rule(client, first, field=field).status_code == 422
    assert client.post(f"/feeds/{first}/rules", data={
        "field": field, "value": "x", "operator": "contains", "action": "exclude",
    }).status_code == 422
    document = client.get("/api/config/export").json()
    document["feeds"][0]["rules"] = [{"field": field, "value": "x", "operator": "contains", "action": "exclude"}]
    assert client.post("/api/config/import", json=document).status_code == 409
    assert app.state.configuration_service.revision() == revision


def test_canonical_fields_config_roundtrip_and_strict_legacy(configured, monkeypatch):
    app, client, source_id, first = configured
    refresh(configured, monkeypatch, rss(item()))
    assert rule(client, first, field='xml: [ "", "authors" ]').status_code == 201
    document = client.get("/api/config/export").json()
    assert document["schema_version"] == 3
    assert document["feeds"][0]["rules"][0]["field"] == AUTHORS
    assert "custom_fields" not in json.dumps(document)
    assert "hans dampf" not in json.dumps(document)
    before = stored(app)
    revision = app.state.configuration_service.revision()
    document["schema_version"] = 2
    assert client.post("/api/config/import", json=document).status_code == 409
    legacy1 = {"app_version": "0.4.0", "schema_version": 1, "config_version": 1,
               "feeds": [{"name": "Old", "source_url": "https://example.com/rss", "refresh_interval": 10,
                          "rules": document["feeds"][0]["rules"]}]}
    assert client.post("/api/config/import", json=legacy1).status_code == 409
    assert stored(app) == before
    assert app.state.configuration_service.revision() == revision
    document["schema_version"] = 3
    assert client.post("/api/config/import", json=document).status_code == 200
    assert stored(app) == []
    exported = client.get("/api/config/export").json()
    assert exported["feeds"][0]["rules"][0]["field"] == AUTHORS
    output = client.get("/api/feeds").json()[0]
    assert "derzeit nicht verfügbar" in client.get(f"/feeds/{output['id']}").text
    # An actual schema 2 document still migrates, preserving the old identifiers.
    document["schema_version"] = 2
    document["feeds"][0]["rules"][0]["field"] = "author"
    assert client.post("/api/config/import", json=document).status_code == 200
    assert client.get("/api/config/export").json()["schema_version"] == 3
    assert client.get("/api/config/export").json()["feeds"][0]["rules"][0]["field"] == "author"


def test_catalog_auth_source_isolation_prefixes_and_escaping(configured, monkeypatch):
    app, client, source_id, first = configured
    dangerous = xml_field('urn:"><script>alert(1)</script>', "authors")
    assert rule(client, first, field=dangerous).status_code == 201
    details = client.get(f"/feeds/{first}").text
    assert "&lt;script&gt;" in details
    assert '<script>alert(1)</script>' not in details
    refresh(configured, monkeypatch, rss(item()))
    other = client.post("/api/sources", json={"name": "Other", "source_url": "https://example.com/other"}).json()["id"]
    assert AUTHORS not in [f["id"] for f in client.get(f"/api/sources/{other}/fields").json()["fields"]]
    assert client.get("/api/sources/99999/fields").status_code == 404
    with TestClient(SubpathMiddleware(app, "/tools/feedvanta")) as prefixed:
        authenticate(prefixed, app)
        assert prefixed.get(f"/tools/feedvanta/api/sources/{source_id}/fields").status_code == 200
        assert f"/tools/feedvanta/feeds/{first}/rules" in prefixed.get(f"/tools/feedvanta/feeds/{first}").text
    client.cookies.clear()
    assert client.get(f"/api/sources/{source_id}/fields").status_code == 401
    authenticate(client, app, sub="other", email="other@example.com")
    assert client.get(f"/api/sources/{source_id}/fields").status_code == 403


@pytest.mark.parametrize("data", [b"x" * (MAX_FEED_BYTES + 1),
    b'<!DOCTYPE rss [<!ENTITY x "expanded">]><rss>&x;</rss>',
    b'<!DOCTYPE rss SYSTEM "https://example.com/external"><rss/>',
    b"<x>" * 101 + b"</x>" * 101,
    rss(item(custom="<x>a</x>" * 257)),
    rss(item(custom="<x>" + "a" * (256 * 1024 + 1) + "</x>")),
])
def test_unsafe_feeds_fail_without_partial_writes(configured, monkeypatch, data):
    app, client, source_id, first = configured
    refresh(configured, monkeypatch, rss(item()))
    rule(client, first)
    before = stored(app)
    revision = app.state.configuration_service.revision()
    with app.state.db.connect() as conn:
        visibility = list(conn.execute("SELECT * FROM feed_entry_visibility"))
        last_fetch = conn.execute("SELECT last_fetched_at FROM sources").fetchone()[0]
    monkeypatch.setattr("app.services.urlopen", lambda *args, **kwargs: BytesIO(data))
    with pytest.raises(ValueError):
        app.state.service.refresh_source(source_id)
    assert stored(app) == before
    assert app.state.configuration_service.revision() == revision
    with app.state.db.connect() as conn:
        assert list(conn.execute("SELECT * FROM feed_entry_visibility")) == visibility
        assert conn.execute("SELECT last_fetched_at FROM sources").fetchone()[0] == last_fetch
        assert conn.execute("SELECT last_error FROM sources").fetchone()[0]


def test_malformed_xml_keeps_tolerant_ingestion_and_warns(configured, monkeypatch):
    app, client, source_id, first = configured
    refresh(configured, monkeypatch, rss(item()))
    malformed = rss(item(custom="<authors>Redaktion</authors><broken>"))
    assert feedparser.parse(malformed).entries
    refresh(configured, monkeypatch, malformed)
    assert json.loads(stored(app)[0]["custom_fields"]) == {}
    catalog = client.get(f"/api/sources/{source_id}/fields").json()
    assert "Ungültiges XML" in catalog["extraction_error"]
    assert "Ungültiges XML" in client.get(f"/feeds/{first}").text
    assert "Ungültiges XML" in client.get("/").text
    refresh(configured, monkeypatch, rss(item()))
    assert client.get(f"/api/sources/{source_id}/fields").json()["extraction_error"] is None


def test_unbound_namespace_keeps_tolerant_ingestion(configured, monkeypatch):
    app, client, source_id, _ = configured
    data = rss(item(custom="<unknown:authors>Redaktion</unknown:authors>"))
    assert refresh(configured, monkeypatch, data) == 1
    assert stored(app)[0]["title"] == "News"
    assert json.loads(stored(app)[0]["custom_fields"]) == {}
    assert client.get(f"/api/sources/{source_id}/fields").json()["extraction_error"]
    monkeypatch.setattr("app.source_fields.MAX_ENTRIES", 0)
    with pytest.raises(ValueError, match="5000 Einträge"):
        parse_source(data)


def test_extraction_limits_and_bounded_download(configured, monkeypatch):
    app, _, source_id, _ = configured
    sizes = []

    class BoundedResponse(BytesIO):
        def read(self, size=-1):
            sizes.append(size)
            return super().read(size)

    monkeypatch.setattr("app.services.urlopen", lambda *args, **kwargs: BoundedResponse(rss(item())))
    app.state.service.refresh_source(source_id)
    assert sizes == [MAX_FEED_BYTES + 1]
    before = stored(app)
    for name, limit in [("MAX_ENTRIES", 0), ("MAX_TOTAL_TEXT", 1)]:
        with monkeypatch.context() as patch:
            patch.setattr(f"app.source_fields.{name}", limit)
            with pytest.raises(ValueError):
                app.state.service.refresh_source(source_id)
            assert stored(app) == before


def test_limits_allow_boundary_values(monkeypatch):
    # Count all direct source children, including standard ones.
    data = rss('<item><x>  A  B\nC  </x><y>second</y></item>')
    monkeypatch.setattr("app.source_fields.MAX_FIELDS", 2)
    monkeypatch.setattr("app.source_fields.MAX_ENTRY_TEXT", len("A  B\nCsecond".encode()))
    monkeypatch.setattr("app.source_fields.MAX_TOTAL_TEXT", len("A  B\nCsecond".encode()))
    monkeypatch.setattr("app.source_fields.MAX_ENTRIES", 1)
    _, fields = extracted(data)[0]
    assert fields[xml_field("", "x")] == ["A  B\nC"]
    assert validate_field('xml: ["urn:example", "größe"]') == xml_field("urn:example", "größe")


@pytest.mark.parametrize("atom", [False, True])
def test_inspector_to_field_selection_to_filter_workflow(configured, monkeypatch, atom):
    app, client, source_id, output = configured
    if atom:
        data = (f'<feed xmlns="http://www.w3.org/2005/Atom" xmlns:e="urn:editorial">'
                '<title>Atom source</title><icon>https://example.com/icon.png</icon><entry>'
                f'<id>article</id><title>Target article</title><updated>{datetime.now(timezone.utc).isoformat()}</updated>'
                '<e:authors>Gaby, Redaktion</e:authors></entry></feed>').encode()
        identifier = xml_field("urn:editorial", "authors")
    else:
        data = rss(item("Target article"))
        identifier = AUTHORS
    monkeypatch.setattr("app.source_inspection.urlopen", lambda *args, **kwargs: BytesIO(data))
    inspection = client.get(f"/sources/{source_id}/inspect")
    assert inspection.status_code == 200
    assert "authors" in inspection.text and "Redaktion" in inspection.text
    assert stored(app) == []  # Inspection itself must not discover/persist values.
    refresh(configured, monkeypatch, data)
    catalog = client.get(f"/api/sources/{source_id}/fields").json()["fields"]
    assert field_info(identifier) in catalog
    assert 'Target article' in client.get(f"/reader/{output}").text
    assert client.post(f"/feeds/{output}/rules", data={
        "field": identifier, "operator": "contains", "value": "Redaktion", "action": "exclude",
    }, follow_redirects=False).status_code == 303
    details = client.get(f"/feeds/{output}")
    assert "authors" in details.text
    assert "Ausschlussregel" in details.text
    client.cookies.clear()
    assert "<item>" not in client.get(f"/feed/{output}.xml").text
    assert '<article' not in client.get(f"/reader/{output}").text

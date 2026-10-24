from html import unescape
from io import BytesIO
from urllib.error import URLError

import pytest
from fastapi.testclient import TestClient

from app.main import SubpathMiddleware, create_app
from app.source_inspection import MAX_SOURCE_BYTES, SOURCE_TIMEOUT, inspect_source
from test_app import TEST_SESSION_SECRET, authenticate


XML = b'''<?xml version="1.0"?><rss xmlns:content="http://purl.org/rss/1.0/modules/content/" version="2.0"><channel><title>News</title><item><title>Article</title><description>Summary</description><content:encoded><![CDATA[<script>alert(1)</script><p>Full content</p>]]></content:encoded><pubDate>Sat, 24 Oct 2026 10:00:00 GMT</pubDate></item></channel></rss>'''


def mock_xml(monkeypatch, data=XML):
    calls = []

    def fetch(request, timeout):
        calls.append((request.full_url, timeout))
        return BytesIO(data)

    monkeypatch.setattr("app.source_inspection.urlopen", fetch)
    return calls


@pytest.fixture
def configured(tmp_path):
    app = create_app(str(tmp_path / "test.db"), start_scheduler=False, session_secret=TEST_SESSION_SECRET)
    with TestClient(app) as client:
        authenticate(client, app)
        source = client.post("/api/sources", json={
            "name": "News", "source_url": "https://example.com/rss",
        }).json()
        yield app, client, source


def test_inspector_formats_original_fields_without_mutation(configured, monkeypatch):
    app, client, source = configured
    calls = mock_xml(monkeypatch)
    with app.state.db.connect() as conn:
        before = dict(conn.execute("SELECT * FROM sources").fetchone())
    revision = app.state.configuration_service.revision()
    response = client.get(f"/sources/{source['id']}/inspect")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert calls == [("https://example.com/rss", SOURCE_TIMEOUT)]
    assert "\n      <content:encoded>" in unescape(response.text)
    assert "xmlns:content=" in response.text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in response.text
    assert "<script>" not in response.text
    assert "<description>Summary</description>" in unescape(response.text)
    assert "<pubDate>" in unescape(response.text)
    assert 'href="https://example.com/rss"' in response.text
    assert "Frischer Abruf" in response.text
    assert f"/sources/{source['id']}/inspect" in client.get("/").text
    assert f"/sources/{source['id']}/inspect" in client.get(f"/feeds/{source['feed_id']}").text
    with app.state.db.connect() as conn:
        assert dict(conn.execute("SELECT * FROM sources").fetchone()) == before
        assert conn.execute("SELECT COUNT(*) FROM entries").fetchone()[0] == 0
    assert app.state.configuration_service.revision() == revision


def test_inspector_requires_admin_and_existing_source(configured, monkeypatch):
    app, client, source = configured
    calls = mock_xml(monkeypatch)
    assert client.get("/sources/99999/inspect").status_code == 404
    client.cookies.clear()
    assert client.get(f"/sources/{source['id']}/inspect").status_code == 401
    authenticate(client, app, sub="other", email="other@example.com")
    assert client.get(f"/sources/{source['id']}/inspect").status_code == 403
    assert calls == []


@pytest.mark.parametrize(("data", "message"), [
    (b"<rss>", "kein gültiges XML"),
    (b"x" * (MAX_SOURCE_BYTES + 1), "zu groß"),
    (b'<!DOCTYPE rss [<!ENTITY x "expanded">]><rss>&x;</rss>', "DTD"),
    (b'<!DOCTYPE rss SYSTEM "file:///etc/passwd"><rss/>', "DTD"),
    (b"<x>" * 101 + b"</x>" * 101, "zu tief"),
])
def test_inspector_errors_are_readable(configured, monkeypatch, data, message):
    _, client, source = configured
    mock_xml(monkeypatch, data)
    response = client.get(f"/sources/{source['id']}/inspect")
    assert response.status_code == 502
    assert 'role="alert"' in response.text
    assert message in response.text


@pytest.mark.parametrize("error", [URLError("offline"), TimeoutError("timeout")])
def test_fetch_failure(configured, monkeypatch, error):
    _, client, source = configured

    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr("app.source_inspection.urlopen", fail)
    response = client.get(f"/sources/{source['id']}/inspect")
    assert response.status_code == 502
    assert "konnte nicht geladen werden" in response.text


def test_atom_and_encoding(monkeypatch):
    mock_xml(monkeypatch, '<?xml version="1.0" encoding="iso-8859-1"?><feed xmlns="http://www.w3.org/2005/Atom"><entry><content type="html">Größe &lt;p&gt;Text&lt;/p&gt;</content></entry></feed>'.encode("iso-8859-1"))
    xml = inspect_source("https://example.com/atom")
    assert 'xmlns="http://www.w3.org/2005/Atom"' in xml
    assert 'type="html"' in xml
    assert "Größe &lt;p&gt;Text&lt;/p&gt;" in xml


def test_inspector_links_support_prefix(configured, monkeypatch):
    app, _, source = configured
    mock_xml(monkeypatch)
    with TestClient(SubpathMiddleware(app, "/tools/feedvanta")) as client:
        authenticate(client, app)
        overview = client.get("/tools/feedvanta/")
        assert f"/tools/feedvanta/sources/{source['id']}/inspect" in overview.text
        response = client.get(f"/tools/feedvanta/sources/{source['id']}/inspect")
        assert response.status_code == 200
        assert 'href="http://testserver/tools/feedvanta/"' in response.text

import sqlite3
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from io import BytesIO
from xml.etree.ElementTree import fromstring

import pytest
from fastapi.testclient import TestClient

from app.main import SubpathMiddleware, create_app
from test_app import TEST_SESSION_SECRET, authenticate


@pytest.fixture
def configured(tmp_path):
    app = create_app(str(tmp_path / "test.db"), start_scheduler=False, session_secret=TEST_SESSION_SECRET)
    with TestClient(app) as client:
        authenticate(client, app)
        response = client.post("/api/sources", json={"name": "All news", "source_url": "https://example.com/news"})
        assert response.status_code == 201
        yield app, client, response.json()["id"], response.json()["feed_id"]


def add_output(client, source_id, name):
    response = client.post("/api/feeds", json={"name": name, "source_id": source_id})
    assert response.status_code == 201
    return response.json()["id"]


def add_rule(client, feed_id, value, action="include"):
    response = client.post(f"/api/feeds/{feed_id}/rules", json={
        "field": "title", "operator": "contains", "value": value, "action": action,
    })
    assert response.status_code == 201
    return response.json()["id"]


def article_titles(client, feed_id):
    return [item.findtext("title") for item in fromstring(client.get(f"/feed/{feed_id}.xml").content).findall("./channel/item")]


def mock_feed(monkeypatch, titles=("Python news", "Security news")):
    requests = []
    published = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S GMT")
    xml = '<rss version="2.0"><channel><title>Original source title</title><image><url>https://example.com/icon.png</url></image>'
    for index, title in enumerate(titles):
        xml += f"<item><guid>{index}</guid><title>{title}</title><link>https://example.com/{index}.mp4</link><pubDate>{published}</pubDate></item>"
    xml += "</channel></rss>"

    def fetch(request, timeout):
        requests.append(request.full_url)
        return BytesIO(xml.encode())

    monkeypatch.setattr("app.services.urlopen", fetch)
    return requests


def test_independent_outputs_share_fetch_and_articles(configured, monkeypatch):
    app, client, source_id, first = configured
    python = add_output(client, source_id, "Python only")
    security = add_output(client, source_id, "Security only")
    rule_id = add_rule(client, python, "python")
    add_rule(client, security, "security")
    revision = app.state.configuration_service.revision()
    requests = mock_feed(monkeypatch)
    # An output ID intentionally differs from the source ID.
    assert python != source_id
    assert client.post(f"/api/feeds/{python}/refresh").json() == {"fetched": 2}
    assert requests == ["https://example.com/news"]
    assert article_titles(client, first) == ["Security news", "Python news"]
    assert article_titles(client, python) == ["Python news"]
    assert article_titles(client, security) == ["Security news"]
    assert "Security news" not in client.get(f"/reader/{python}").text
    assert "Python only" in client.get(f"/reader/{python}").text
    assert "<title>Python only</title>" in client.get(f"/feed/{python}.xml").text
    assert "Ausschlussregel" not in client.get(f"/feeds/{python}").text
    assert "Keine aktive Einschlussregel" in client.get(f"/feeds/{python}").text
    assert app.state.configuration_service.revision() == revision
    with app.state.db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM entries").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM feed_entry_visibility").fetchone()[0] == 6
    assert client.delete(f"/api/rules/{rule_id}").status_code == 204
    assert len(article_titles(client, python)) == 2
    assert article_titles(client, security) == ["Security news"]
    # New outputs use cached articles immediately and do not fetch again.
    extra = add_output(client, source_id, "Cached")
    assert len(article_titles(client, extra)) == 2 and len(requests) == 1
    assert "Gefilterten Feed hinzufügen" in client.get("/").text


def test_edit_rule_reapplies_only_its_output(configured, monkeypatch):
    app, client, source_id, first = configured
    sibling = add_output(client, source_id, "Sibling")
    rule_id = add_rule(client, first, "python")
    add_rule(client, sibling, "python")
    mock_feed(monkeypatch)
    app.state.service.refresh_source(source_id)
    revision = app.state.configuration_service.revision()
    payload = {"field": "title", "operator": "equals", "value": "Security news", "action": "include"}

    response = client.patch(f"/api/rules/{rule_id}", json=payload)
    assert response.status_code == 200
    assert response.json() == {"id": rule_id}
    assert article_titles(client, first) == ["Security news"]
    assert article_titles(client, sibling) == ["Python news"]
    assert "Python news" not in client.get(f"/reader/{first}").text
    assert app.state.configuration_service.revision() == revision + 1
    rule = client.get("/api/feeds").json()[0]["rules"][0]
    assert rule == {"id": rule_id, "feed_id": first, "enabled": 1, **payload}
    assert client.patch(f"/api/rules/{rule_id}", json=payload).status_code == 200
    assert app.state.configuration_service.revision() == revision + 1


def test_edit_rule_form_preserves_disabled_state_and_prefix(configured, monkeypatch):
    app, client, source_id, first = configured
    rule_id = add_rule(client, first, "python")
    with app.state.db.connect() as conn:
        conn.execute("UPDATE filter_rules SET enabled=0 WHERE id=?", (rule_id,))
    mock_feed(monkeypatch)
    app.state.service.refresh_source(source_id)
    with TestClient(SubpathMiddleware(app, "/prefix/feedvanta")) as prefixed:
        authenticate(prefixed, app)
        base = "/prefix/feedvanta"
        details = prefixed.get(f"{base}/feeds/{first}").text
        assert f'action="http://testserver{base}/rules/{rule_id}/edit"' in details
        assert 'value="python"' in details
        for selected in ("include", "title", "contains"):
            assert f'value="{selected}" selected' in details
        response = prefixed.post(f"{base}/rules/{rule_id}/edit", data={
            "field": "all", "operator": "regex", "value": 'Security|"Python"', "action": "exclude",
        }, follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"] == f"http://testserver{base}/feeds/{first}"
        assert 'value="Security|&#34;Python&#34;"' in prefixed.get(response.headers["location"]).text
    rule = client.get("/api/feeds").json()[0]["rules"][0]
    assert rule["id"] == rule_id and rule["enabled"] == 0
    assert rule["field"] == "all" and rule["operator"] == "regex" and rule["action"] == "exclude"
    assert len(article_titles(client, first)) == 2


@pytest.mark.parametrize("changes", [
    {"field": "unknown"}, {"operator": "unknown"}, {"action": "unknown"},
    {"value": ""}, {"value": "x" * 1001}, {"field": None},
])
def test_edit_rule_rejects_invalid_values(configured, changes):
    app, client, _, first = configured
    rule_id = add_rule(client, first, "python")
    before = client.get("/api/feeds").json()
    revision = app.state.configuration_service.revision()
    payload = {"field": "title", "operator": "contains", "value": "python", "action": "include", **changes}
    assert client.patch(f"/api/rules/{rule_id}", json=payload).status_code == 422
    assert client.post(f"/rules/{rule_id}/edit", data=payload).status_code == 422
    assert client.get("/api/feeds").json() == before
    assert app.state.configuration_service.revision() == revision


def test_edit_rule_missing_and_unauthorized(configured):
    app, client, _, first = configured
    rule_id = add_rule(client, first, "python")
    payload = {"field": "title", "operator": "contains", "value": "security", "action": "include"}
    revision = app.state.configuration_service.revision()
    assert client.patch("/api/rules/999", json=payload).status_code == 404
    assert client.post("/rules/999/edit", data=payload).status_code == 404
    client.cookies.clear()
    assert client.patch(f"/api/rules/{rule_id}", json=payload).status_code == 401
    assert client.post(f"/rules/{rule_id}/edit", data=payload).status_code == 401
    authenticate(client, app, sub="other", email="other@example.com")
    assert client.patch(f"/api/rules/{rule_id}", json=payload).status_code == 403
    assert client.post(f"/rules/{rule_id}/edit", data=payload).status_code == 403
    assert app.state.configuration_service.revision() == revision


def test_edit_rule_rolls_back_on_reapplication_failure(configured, monkeypatch):
    app, client, _, first = configured
    rule_id = add_rule(client, first, "python")
    before = client.get("/api/feeds").json()
    revision = app.state.configuration_service.revision()

    def fail(conn, feed_id):
        raise RuntimeError("Reapplication failed")

    monkeypatch.setattr(app.state.service, "reapply_rules_in_transaction", fail)
    with pytest.raises(RuntimeError, match="Reapplication failed"):
        client.patch(f"/api/rules/{rule_id}", json={
            "field": "title", "operator": "contains", "value": "security", "action": "include",
        })
    assert client.get("/api/feeds").json() == before
    assert app.state.configuration_service.revision() == revision


def test_refresh_upsert_failure_and_retention(configured, monkeypatch):
    app, client, source_id, first = configured
    second = add_output(client, source_id, "Python")
    add_rule(client, second, "python")
    mock_feed(monkeypatch)
    app.state.service.refresh_source(source_id)
    mock_feed(monkeypatch, ("Changed", "Python changed"))
    app.state.service.refresh_source(source_id)
    assert article_titles(client, second) == ["Python changed"]
    before = article_titles(client, first)
    with app.state.db.connect() as conn:
        before_fetch = conn.execute("SELECT last_fetched_at FROM sources WHERE id=?", (source_id,)).fetchone()[0]

    def fail(*args, **kwargs):
        raise OSError("offline")

    monkeypatch.setattr("app.services.urlopen", fail)
    assert client.post(f"/api/sources/{source_id}/refresh").status_code == 502
    assert article_titles(client, first) == before
    with app.state.db.connect() as conn:
        source = conn.execute("SELECT * FROM sources WHERE id=?", (source_id,)).fetchone()
        assert source["last_error"] == "offline" and source["last_fetched_at"] == before_fetch
        conn.execute("UPDATE entries SET fetched_at=? WHERE guid='0'", ((datetime.now(timezone.utc) - timedelta(days=8)).isoformat(),))
    app.state.service.prune_expired_entries()
    assert article_titles(client, first) == ["Python changed"]
    with app.state.db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM feed_entry_visibility").fetchone()[0] == 2
        assert not conn.execute("PRAGMA foreign_key_check").fetchall()


def test_source_settings_scheduler_and_zero_output_sources(configured, monkeypatch):
    app, client, source_id, first = configured
    second = add_output(client, source_id, "Sibling")
    assert client.patch(f"/api/feeds/{second}/refresh-interval", json={"refresh_interval": 10}).status_code == 200
    assert {feed["refresh_interval"] for feed in client.get("/api/feeds").json()} == {10}
    refreshed = []
    monkeypatch.setattr(app.state.service, "refresh_source", refreshed.append)
    app.state.service.refresh_due()
    assert refreshed == [source_id]  # Not once per output.
    assert client.patch(f"/api/sources/{source_id}", json={"enabled": False}).status_code == 200
    assert client.get(f"/feed/{first}.xml").status_code == 200
    app.state.service.refresh_due()
    assert refreshed == [source_id]
    assert client.delete(f"/api/feeds/{first}").status_code == 204
    assert client.delete(f"/api/feeds/{second}").status_code == 204
    assert len(client.get("/api/sources").json()) == 1
    exported = client.get("/api/config/export").json()
    assert len(exported["sources"]) == 1 and exported["feeds"] == []
    assert "Noch keine Ausgabe-Feeds" in client.get("/").text
    client.patch(f"/api/sources/{source_id}", json={"enabled": True})
    app.state.service.refresh_due()
    assert refreshed == [source_id, source_id]
    assert client.delete(f"/api/sources/{source_id}").status_code == 204


def test_output_deletion_preserves_source_and_source_delete_requires_confirmation(configured, monkeypatch):
    app, client, source_id, first = configured
    second = add_output(client, source_id, "Sibling")
    mock_feed(monkeypatch)
    app.state.service.refresh_source(source_id)
    other = client.post("/api/sources", json={"name": "Other", "source_url": "https://other.example/feed"}).json()
    assert client.delete(f"/api/sources/{source_id}").status_code == 409
    assert client.delete(f"/api/feeds/{first}").status_code == 204
    assert len(article_titles(client, second)) == 2
    assert client.get(f"/feed/{first}.xml").status_code == 404
    assert client.delete(f"/api/sources/{source_id}?confirm=true").status_code == 204
    assert client.get(f"/reader/{second}").status_code == 404
    assert [source["id"] for source in client.get("/api/sources").json()] == [other["id"]]
    with app.state.db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM entries").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM feed_entry_visibility").fetchone()[0] == 0


@pytest.mark.parametrize("path", ["/api/feeds", "/feeds"])
@pytest.mark.parametrize("duplicate_path", ["/api/feeds", "/feeds"])
def test_duplicate_source_url_rejected_even_with_different_name(configured, path, duplicate_path):
    app, client, _, _ = configured

    def submit(path, payload):
        return client.post(path, **{"json" if path.startswith("/api/") else "data": payload}, follow_redirects=False)

    original = {"name": "Original", "source_url": "https://example.com/unique"}
    assert submit(path, original).status_code in (201, 303)
    revision = app.state.configuration_service.revision()
    before = client.get("/api/sources").json()
    assert submit(duplicate_path, {**original, "name": "Different"}).status_code == 409
    assert client.get("/api/sources").json() == before
    assert app.state.configuration_service.revision() == revision


def test_source_and_output_validation_and_rename(configured):
    app, client, source_id, first = configured
    for payload in ({"name": "No source"}, {"name": "Both", "source_id": source_id, "source_url": "https://example.com"},
                    {"name": "Shared setting", "source_id": source_id, "refresh_interval": 10}):
        assert client.post("/api/feeds", json=payload).status_code == 422
    assert client.post("/api/feeds", json={"name": "Missing", "source_id": 999}).status_code == 404
    assert client.patch(f"/api/sources/{source_id}", json={"refresh_interval": 0}).status_code == 422
    assert client.patch("/api/sources/999", json={"enabled": False}).status_code == 404
    assert client.delete("/api/sources/999").status_code == 404
    assert client.post("/api/sources/999/refresh").status_code == 404
    assert client.patch(f"/api/feeds/{first}", json={"name": "Renamed"}).status_code == 200
    assert "<title>Renamed</title>" in client.get(f"/feed/{first}.xml").text
    revision = app.state.configuration_service.revision()
    client.patch(f"/api/feeds/{first}", json={"name": "Renamed"})
    client.patch(f"/api/sources/{source_id}", json={"enabled": True})
    assert app.state.configuration_service.revision() == revision
    assert client.post("/feeds", data={"name": "Invalid", "source_url": "bad"}).status_code == 422


def test_publication_boundary_and_missing_visibility(configured, monkeypatch):
    app, client, source_id, first = configured
    now = datetime(2026, 10, 22, 12, tzinfo=timezone.utc)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return now

    monkeypatch.setattr("app.main.datetime", Clock)
    with app.state.db.connect() as conn:
        for guid, published in (
            ("inside", (now - timedelta(hours=24) + timedelta(microseconds=1)).isoformat()),
            ("boundary", (now - timedelta(hours=24)).isoformat()),
            ("outside", (now - timedelta(hours=24, microseconds=1)).isoformat()),
            ("offset", "2026-10-21T14:00:00+02:00"), ("missing", None), ("invalid", "not-a-date"),
        ):
            conn.execute("INSERT INTO entries(source_id,guid,title,published,fetched_at) VALUES(?,?,?,?,?)",
                         (source_id, guid, guid, published, now.isoformat()))
    assert article_titles(client, first) == []  # Missing results are not public.
    app.state.service.reapply_rules(first)
    assert set(article_titles(client, first)) == {"inside", "boundary", "offset"}
    reader = client.get(f"/reader/{first}").text
    assert "outside" not in reader and "missing" not in reader


def test_new_forms_and_routes_under_prefix(configured):
    app, _, source_id, _ = configured
    with TestClient(SubpathMiddleware(app, "/prefix/feedvanta")) as client:
        authenticate(client, app)
        base = "/prefix/feedvanta"
        response = client.post(f"{base}/sources/{source_id}/feeds", data={"name": "Form output"}, follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"].startswith(f"http://testserver{base}/feeds/")
        page = client.get(base + "/").text
        assert f"http://testserver{base}/sources/{source_id}/settings" in page
        assert client.post(f"{base}/sources/{source_id}/settings", data={"refresh_interval": 45}, follow_redirects=False).status_code == 303
        assert client.get(base + "/api/sources").json()[0]["enabled"] == 0
        assert client.post(f"{base}/sources/{source_id}/delete", data={}).status_code == 409
        assert client.post(f"{base}/sources/{source_id}/delete", data={"confirm": "true"}, follow_redirects=False).status_code == 303


@pytest.mark.parametrize("role,expected", [("anonymous", 401), ("nonadmin", 403)])
def test_source_management_requires_admin(configured, role, expected):
    app, _, source_id, first = configured
    with TestClient(app) as client:
        if role == "nonadmin":
            authenticate(client, app, sub="nonadmin", email="other@example.com")
        for method, path, kwargs in (
            ("get", "/api/sources", {}),
            ("post", "/api/sources", {"json": {"name": "New", "source_url": "https://example.com/new"}}),
            ("patch", f"/api/sources/{source_id}", {"json": {"enabled": False}}),
            ("delete", f"/api/sources/{source_id}?confirm=true", {}),
            ("post", f"/api/sources/{source_id}/refresh", {}),
            ("post", f"/sources/{source_id}/feeds", {"data": {"name": "New"}}),
            ("post", f"/sources/{source_id}/settings", {"data": {"refresh_interval": 30}}),
            ("post", f"/sources/{source_id}/delete", {"data": {"confirm": "true"}}),
            ("post", f"/sources/{source_id}/refresh", {}),
            ("patch", f"/api/feeds/{first}", {"json": {"name": "New"}}),
            ("post", f"/feeds/{first}/name", {"data": {"name": "New"}}),
        ):
            assert getattr(client, method)(path, **kwargs).status_code == expected
        assert client.get(f"/reader/{first}").status_code == 200


def test_v1_import_and_current_shared_source_roundtrip(configured, monkeypatch):
    app, client, source_id, _ = configured
    mock_feed(monkeypatch)
    app.state.service.refresh_source(source_id)
    legacy = {
        "app_version": "0.4.0", "schema_version": 1, "config_version": 42,
        "feeds": [{"name": "Old output", "source_url": "https://example.com/legacy", "enabled": False,
                   "refresh_interval": 60, "rules": [{"field": "title", "operator": "contains", "value": "Python", "action": "include"}]}],
    }
    assert client.post("/api/config/import", json=legacy).json() == {"imported_feeds": 1, "config_version": 42}
    exported = client.get("/api/config/export").json()
    assert exported["schema_version"] == 3
    assert exported["sources"][0]["enabled"] is False
    assert exported["sources"][0]["refresh_interval"] == 60
    assert exported["feeds"][0]["name"] == "Old output"
    assert exported["feeds"][0]["rules"][0]["value"] == "Python"
    exported["feeds"].append({"name": "Unfiltered sibling", "source": exported["sources"][0]["key"], "rules": []})
    assert client.post("/api/config/import", json=exported).status_code == 200
    roundtrip = client.get("/api/config/export").json()
    assert len(roundtrip["sources"]) == 1 and len(roundtrip["feeds"]) == 2
    assert len({feed["source"] for feed in roundtrip["feeds"]}) == 1
    assert roundtrip["config_version"] == 42
    with app.state.db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM entries").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM user_permissions").fetchone()[0] == 1


@pytest.mark.parametrize("invalid", ["url", "key", "reference", "rule", "interval", "version", "extra"])
def test_invalid_config_import_is_atomic(configured, invalid, monkeypatch):
    app, client, source_id, first = configured
    mock_feed(monkeypatch)
    app.state.service.refresh_source(source_id)
    titles = article_titles(client, first)
    before = client.get("/api/config/export").json()
    document = deepcopy(before)
    if invalid in ("url", "key"):
        source = deepcopy(document["sources"][0])
        source["key" if invalid == "url" else "source_url"] = "different" if invalid == "url" else "https://other.example/feed"
        document["sources"].append(source)
    elif invalid == "reference":
        document["feeds"][0]["source"] = "unknown"
    elif invalid == "rule":
        document["feeds"][0]["rules"] = [{"field": "invalid", "operator": "contains", "value": "x", "action": "include"}]
    elif invalid == "interval":
        document["sources"][0]["refresh_interval"] = 0
    elif invalid == "version":
        document["schema_version"] = 999
    else:
        document["unexpected"] = True
    assert client.post("/api/config/import", json=document).status_code == 409
    after = client.get("/api/config/export").json()
    before.pop("exported_at")
    after.pop("exported_at")
    assert after == before
    assert article_titles(client, first) == titles


def test_config_form_migrates_v1_and_rejects_malformed_input(configured):
    _, client, _, _ = configured
    for payload in (b"[]", b"not json", b'{"schema_version":999}'):
        assert client.post("/admin/config/import", files={"config_file": ("config.json", payload, "application/json")}).status_code == 409
    payload = b'{"app_version":"0.4.0","schema_version":1,"config_version":9,"feeds":[]}'
    assert client.post("/admin/config/import", files={"config_file": ("config.json", payload, "application/json")}, follow_redirects=False).status_code == 303
    assert client.get("/api/config/export").json()["schema_version"] == 3


@pytest.mark.parametrize("invalid", ["duplicate", "extra", "rule", "interval", "name"])
def test_v1_import_uses_strict_legacy_validation(configured, invalid):
    _, client, _, _ = configured
    before = client.get("/api/sources").json()
    feed = {"name": "Legacy", "source_url": "https://example.com", "refresh_interval": 30, "rules": []}
    document = {"app_version": "0.4.0", "schema_version": 1, "config_version": 9, "feeds": [feed]}
    if invalid == "duplicate":
        document["feeds"].append({**feed, "source_url": "https://example.com/"})
    elif invalid == "extra":
        feed["unknown"] = True
    elif invalid == "rule":
        feed["rules"] = [{"field": "invalid", "operator": "contains", "value": "x", "action": "include"}]
    elif invalid == "interval":
        feed["refresh_interval"] = 0
    else:
        feed["name"] = ""
    assert client.post("/api/config/import", json=document).status_code == 409
    assert client.get("/api/sources").json() == before


def test_import_database_failure_rolls_back_replacement(configured, monkeypatch):
    from app.configuration import parse_document

    app, client, source_id, first = configured
    mock_feed(monkeypatch)
    app.state.service.refresh_source(source_id)
    before = client.get("/api/sources").json()
    document = parse_document(client.get("/api/config/export").json())
    with app.state.db.connect() as conn:
        conn.execute("CREATE TRIGGER fail_import BEFORE INSERT ON sources BEGIN SELECT RAISE(ABORT,'simulated failure'); END")
    with pytest.raises(sqlite3.IntegrityError, match="simulated"):
        app.state.configuration_service.import_document(document)
    assert client.get("/api/sources").json() == before
    assert len(article_titles(client, first)) == 2


def test_rule_update_rolls_back_if_visibility_fails(configured, monkeypatch):
    app, client, _, first = configured
    before = client.get("/api/feeds").json()
    revision = app.state.configuration_service.revision()

    def fail(conn, feed_id):
        raise RuntimeError("visibility failure")

    monkeypatch.setattr(app.state.service, "reapply_rules_in_transaction", fail)
    with pytest.raises(RuntimeError, match="visibility failure"):
        add_rule(client, first, "python")
    assert client.get("/api/feeds").json() == before
    assert app.state.configuration_service.revision() == revision


def test_public_output_limits_are_applied_after_filtering(configured):
    app, client, source_id, first = configured
    now = datetime.now(timezone.utc)
    with app.state.db.connect() as conn:
        conn.executemany(
            "INSERT INTO entries(source_id,guid,title,published,fetched_at) VALUES(?,?,?,?,?)",
            [(source_id, str(index), f"Article {index}", (now - timedelta(seconds=index)).isoformat(), now.isoformat())
             for index in range(505)],
        )
    add_rule(client, first, "Article 0", action="exclude")
    titles = article_titles(client, first)
    assert len(titles) == 500
    assert titles[0] == "Article 1" and titles[-1] == "Article 500"
    reader = client.get(f"/reader/{first}").text
    assert reader.count("<article ") == 100
    assert "Article 100<" in reader and "Article 101<" not in reader

from pathlib import Path

from fastapi.testclient import TestClient

from app.main import SubpathMiddleware, create_app
from app.services import feed_metadata, is_hidden, rule_matches


def test_filter_semantics():
    entry = {"title": "Python und Bitcoin", "content": "News", "author": "Ada", "category": "Tech"}
    assert rule_matches(entry, {"field": "title", "operator": "contains", "value": "python"})
    assert rule_matches(entry, {"field": "all", "operator": "regex", "value": "Ada|Bob"})
    assert is_hidden(entry, [{"enabled": 1, "action": "exclude", "field": "title", "operator": "contains", "value": "bitcoin"}])
    assert not is_hidden(entry, [{"enabled": 1, "action": "include", "field": "title", "operator": "contains", "value": "python"}])
    assert is_hidden(entry, [{"enabled": 1, "action": "include", "field": "title", "operator": "contains", "value": "rust"}])


def test_original_feed_metadata():
    parsed = type("Parsed", (), {"feed": {
        "title": "Originaltitel", "image": {"href": "/assets/icon.png"}
    }})()
    assert feed_metadata(parsed, "https://example.com/rss/feed.xml") == (
        "Originaltitel", "https://example.com/assets/icon.png"
    )


def test_feed_rule_and_rss_api(tmp_path: Path):
    app = create_app(str(tmp_path / "test.db"), start_scheduler=False)
    with TestClient(app) as client:
        response = client.post("/api/feeds", json={
            "name": "Test", "source_url": "https://example.com/rss.xml", "refresh_interval": 15
        })
        assert response.status_code == 201
        feed_id = response.json()["id"]
        with app.state.db.connect() as conn:
            conn.execute(
                "UPDATE feeds SET source_title=?, icon_url=? WHERE id=?",
                ("Original Feed", "https://example.com/icon.png", feed_id),
            )
            conn.execute(
                "INSERT INTO entries(feed_id,guid,title,url,content,author,category,published,fetched_at,hidden) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                (feed_id, "visible", "Sichtbarer Artikel", "https://example.com/visible",
                 "<p>Eine kurze Meldung</p>", "Ada", "Tech", "2026-09-25T10:00:00+00:00",
                 "2026-09-25T10:01:00+00:00", 0),
            )
            conn.execute(
                "INSERT INTO entries(feed_id,guid,title,url,content,fetched_at,hidden) VALUES(?,?,?,?,?,?,?)",
                (feed_id, "hidden", "Sport: Versteckter Artikel", "https://example.com/hidden",
                 "Nicht anzeigen", "2026-09-25T10:01:00+00:00", 1),
            )
        response = client.post(f"/api/feeds/{feed_id}/rules", json={
            "field": "title", "operator": "contains", "value": "Sport", "action": "exclude"
        })
        assert response.status_code == 201
        listing = client.get("/api/feeds").json()
        assert listing[0]["rules"][0]["value"] == "Sport"
        assert listing[0]["visible_count"] == 1
        assert listing[0]["hidden_count"] == 1
        overview = client.get("/")
        assert "1 sichtbar" in overview.text
        assert "1 ausgefiltert" in overview.text
        rss = client.get(f"/feed/{feed_id}.xml")
        assert rss.status_code == 200
        assert rss.headers["content-type"].startswith("application/rss+xml")
        assert b"<title>Original Feed</title>" in rss.content
        assert b"<url>https://example.com/icon.png</url>" in rss.content
        assert b"atom:link" in rss.content
        reader = client.get(f"/reader/{feed_id}")
        assert reader.status_code == 200
        assert "Sichtbarer Artikel" in reader.text
        assert "Versteckter Artikel" not in reader.text
        assert "Eine kurze Meldung" in reader.text
        assert "Original Feed" in reader.text
        assert "https://example.com/icon.png" in reader.text


def test_delete_missing_returns_404(tmp_path: Path):
    app = create_app(str(tmp_path / "test.db"), start_scheduler=False)
    with TestClient(app) as client:
        assert client.delete("/api/feeds/123").status_code == 404


def test_application_below_subpath(tmp_path: Path):
    core = create_app(str(tmp_path / "test.db"), start_scheduler=False)
    app = SubpathMiddleware(core, "/feedvanta/")
    with TestClient(app) as client:
        response = client.post("/feedvanta/api/feeds", json={
            "name": "Test", "source_url": "https://example.com/rss.xml"
        })
        assert response.status_code == 201
        feed_id = response.json()["id"]
        page = client.get("/feedvanta/")
        assert page.status_code == 200
        assert f'http://testserver/feedvanta/reader/{feed_id}' in page.text
        assert f'http://testserver/feedvanta/feed/{feed_id}.xml' in page.text
        assert client.get("/").status_code == 404

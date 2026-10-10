import json
from base64 import b64encode
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi.testclient import TestClient
from itsdangerous import TimestampSigner

from app.auth import GLOBAL_ADMINISTRATION
from app.main import SubpathMiddleware, create_app
from app.plugin import PLUGIN
from app.services import FeedService, feed_metadata, hidden_reasons, is_hidden, rule_matches, site_icon_url
from dataclasses import replace
from tool_host.app import create_app as create_host

TEST_SESSION_SECRET = "test-session-secret"


def authenticate(client: TestClient, app, sub: str = "admin", email: str = "admin@example.com") -> dict:
    user = app.state.auth_service.login_google_user({"sub": sub, "email": email, "name": "Test User"})
    data = b64encode(json.dumps({"user_id": user["id"]}).encode("utf-8"))
    client.cookies.set("session", TimestampSigner(TEST_SESSION_SECRET).sign(data).decode("utf-8"))
    return user


def test_filter_semantics():
    entry = {"title": "Python und Bitcoin", "content": "News", "author": "Ada", "category": "Tech"}
    assert rule_matches(entry, {"field": "title", "operator": "contains", "value": "python"})
    assert rule_matches(entry, {"field": "all", "operator": "regex", "value": "Ada|Bob"})
    assert is_hidden(entry, [{"enabled": 1, "action": "exclude", "field": "title", "operator": "contains", "value": "bitcoin"}])
    assert not is_hidden(entry, [{"enabled": 1, "action": "include", "field": "title", "operator": "contains", "value": "python"}])
    assert is_hidden(entry, [{"enabled": 1, "action": "include", "field": "title", "operator": "contains", "value": "rust"}])


def test_hidden_reasons_explain_exclude_and_include_rules():
    entry = {"title": "Python news", "content": "", "author": "", "category": ""}
    exclude = {"enabled": 1, "action": "exclude", "field": "title", "operator": "contains", "value": "python"}
    include = {"enabled": 1, "action": "include", "field": "title", "operator": "contains", "value": "rust"}
    assert "Ausschlussregel" in hidden_reasons(entry, [exclude])[0]
    assert "Keine aktive Einschlussregel trifft zu" in hidden_reasons(entry, [include])[0]


def test_original_feed_metadata():
    parsed = type("Parsed", (), {"feed": {
        "title": "Originaltitel", "image": {"href": "/assets/icon.png"}
    }})()
    assert feed_metadata(parsed, "https://example.com/rss/feed.xml") == (
        "Originaltitel", "https://example.com/assets/icon.png"
    )


def test_site_icon_url_resolves_declared_favicon():
    html = '<link rel="icon" href="/favicon.ico"><link rel="icon" type="image/svg+xml" href="/brand.svg">'
    assert site_icon_url(html, "https://example.com/news/feed") == "https://example.com/brand.svg"


def test_database_retention_uses_fetch_time_not_publication_time(tmp_path: Path):
    app = create_app(str(tmp_path / "test.db"), start_scheduler=False, session_secret=TEST_SESSION_SECRET)
    now = datetime.now(timezone.utc)
    with app.state.db.connect() as conn:
        feed_id = conn.execute(
            "INSERT INTO feeds(name,source_url,created_at) VALUES(?,?,?)",
            ("Test", "https://example.com/feed.xml", now.isoformat()),
        ).lastrowid
        conn.executemany(
            "INSERT INTO entries(feed_id,guid,title,published,fetched_at) VALUES(?,?,?,?,?)",
            [
                (feed_id, "expired", "Expired", now.isoformat(),
                 (now - timedelta(days=8)).isoformat()),
                (feed_id, "recent", "Recent", (now - timedelta(days=30)).isoformat(),
                 (now - timedelta(days=6)).isoformat()),
            ],
        )

    FeedService(app.state.db).prune_expired_entries()
    with app.state.db.connect() as conn:
        stored = conn.execute(
            "SELECT guid FROM entries WHERE feed_id=?", (feed_id,)
        ).fetchall()
    assert [row["guid"] for row in stored] == ["recent"]


def test_feed_rule_and_rss_api(tmp_path: Path):
    app = create_app(str(tmp_path / "test.db"), start_scheduler=False, session_secret=TEST_SESSION_SECRET)
    with TestClient(app) as client:
        authenticate(client, app)
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
                (feed_id, "visible", "Sichtbarer Artikel", "https://example.com/visible.mp4",
                 "<p>Eine kurze Meldung</p>", "Ada", "Tech", datetime.now(timezone.utc).isoformat(),
                 datetime.now(timezone.utc).isoformat(), 0),
            )
            conn.execute(
                "INSERT INTO entries(feed_id,guid,title,url,content,fetched_at,hidden) VALUES(?,?,?,?,?,?,?)",
                (feed_id, "hidden", "Sport: Versteckter Artikel", "https://example.com/hidden",
                 "Nicht anzeigen", "2026-09-25T10:01:00+00:00", 1),
            )
            conn.execute(
                "INSERT INTO entries(feed_id,guid,title,url,content,published,fetched_at,hidden) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (feed_id, "old", "Alter Artikel", "https://example.com/old",
                 "Älter als 24 Stunden", (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat(),
                 datetime.now(timezone.utc).isoformat(), 0),
            )
        response = client.post(f"/api/feeds/{feed_id}/rules", json={
            "field": "title", "operator": "contains", "value": "Sport", "action": "exclude"
        })
        assert response.status_code == 201
        listing = client.get("/api/feeds").json()
        assert listing[0]["rules"][0]["value"] == "Sport"
        assert listing[0]["visible_count"] == 2
        assert listing[0]["hidden_count"] == 1
        overview = client.get("/")
        assert "2 sichtbar" in overview.text
        assert "1 ausgefiltert" in overview.text
        assert "Regel hinzufügen" not in overview.text
        details = client.get(f"/feeds/{feed_id}")
        assert details.status_code == 200
        assert "Regel hinzufügen" in details.text
        assert "Feed löschen" in details.text
        assert "Ausgefilterte Einträge" in details.text
        assert "Versteckter Artikel" in details.text
        assert "Ausschlussregel" in details.text
        rss = client.get(f"/feed/{feed_id}.xml")
        assert rss.status_code == 200
        assert rss.headers["content-type"].startswith("application/rss+xml")
        assert b"<title>Original Feed</title>" in rss.content
        assert b"<url>https://example.com/icon.png</url>" in rss.content
        assert b"atom:link" in rss.content
        assert b'<enclosure url="https://example.com/visible.mp4" length="0" type="video/mp4"' in rss.content
        assert b"Alter Artikel" not in rss.content
        reader = client.get(f"/reader/{feed_id}")
        assert reader.status_code == 200
        assert "Sichtbarer Artikel" in reader.text
        assert "Versteckter Artikel" not in reader.text
        assert "Alter Artikel" not in reader.text
        assert "Eine kurze Meldung" in reader.text
        assert "Original Feed" in reader.text
        assert "https://example.com/icon.png" in reader.text


def test_refresh_interval_is_editable_and_applies_on_next_scheduler_check(tmp_path: Path):
    app = create_app(str(tmp_path / "test.db"), start_scheduler=False, session_secret=TEST_SESSION_SECRET)
    now = datetime.now(timezone.utc)
    with TestClient(app) as client:
        authenticate(client, app)
        feed_id = client.post("/api/feeds", json={
            "name": "Test", "source_url": "https://example.com/feed.xml", "refresh_interval": 30,
        }).json()["id"]
        with app.state.db.connect() as conn:
            conn.execute(
                "UPDATE feeds SET last_fetched_at=? WHERE id=?",
                ((now - timedelta(minutes=20)).isoformat(), feed_id),
            )

        assert "Abrufintervall (Min.)" in client.get("/").text
        details = client.get(f"/feeds/{feed_id}")
        assert "Abrufintervall (Min.)" in details.text

        saved = client.post(
            f"/feeds/{feed_id}/refresh-interval", data={"refresh_interval": "10"},
            follow_redirects=False,
        )
        assert saved.status_code == 303

        refreshed = []
        service = FeedService(app.state.db)
        service.refresh = lambda current_feed_id: refreshed.append(current_feed_id)
        service.refresh_due()
        assert refreshed == [feed_id]

        with app.state.db.connect() as conn:
            feed = conn.execute(
                "SELECT refresh_interval FROM feeds WHERE id=?", (feed_id,)
            ).fetchone()
        assert feed["refresh_interval"] == 10


def test_refresh_interval_update_validates_and_returns_not_found(tmp_path: Path):
    app = create_app(str(tmp_path / "test.db"), start_scheduler=False, session_secret=TEST_SESSION_SECRET)
    with TestClient(app) as client:
        authenticate(client, app)
        feed_id = client.post("/api/feeds", json={
            "name": "Test", "source_url": "https://example.com/feed.xml",
        }).json()["id"]
        assert client.patch(
            f"/api/feeds/{feed_id}/refresh-interval", json={"refresh_interval": 0}
        ).status_code == 422
        assert client.patch(
            "/api/feeds/999/refresh-interval", json={"refresh_interval": 10}
        ).status_code == 404


def test_delete_missing_returns_404(tmp_path: Path):
    app = create_app(str(tmp_path / "test.db"), start_scheduler=False, session_secret=TEST_SESSION_SECRET)
    with TestClient(app) as client:
        authenticate(client, app)
        assert client.delete("/api/feeds/123").status_code == 404


def test_application_below_subpath(tmp_path: Path):
    core = create_app(str(tmp_path / "test.db"), start_scheduler=False, session_secret=TEST_SESSION_SECRET)
    app = SubpathMiddleware(core, "/feedvanta/")
    with TestClient(app) as client:
        authenticate(client, core)
        response = client.post("/feedvanta/api/feeds", json={
            "name": "Test", "source_url": "https://example.com/rss.xml"
        })
        assert response.status_code == 201
        feed_id = response.json()["id"]
        page = client.get("/feedvanta/")
        assert page.status_code == 200
        assert 'href="http://testserver/feedvanta/static/app.css"' in page.text
        stylesheet = client.get("/feedvanta/static/app.css")
        assert stylesheet.status_code == 200
        assert "text/css" in stylesheet.headers["content-type"]
        assert b"--color-slate-100" in stylesheet.content
        assert f'http://testserver/feedvanta/reader/{feed_id}' in page.text
        assert f'http://testserver/feedvanta/feed/{feed_id}.xml' in page.text
        assert client.get("/").status_code == 404


def test_host_discovers_and_mounts_feedvanta(tmp_path: Path):
    plugin = replace(
        PLUGIN,
        app_factory=lambda: create_app(
            str(tmp_path / "host.db"), start_scheduler=False, session_secret=TEST_SESSION_SECRET
        ),
    )
    host = create_host([plugin])
    with TestClient(host) as client:
        assert client.get("/health").json() == {"status": "ok", "plugins": ["feedvanta"]}
        assert '/feedvanta/' in client.get("/").text
        landing = client.get("/feedvanta/", follow_redirects=False)
        assert landing.status_code == 303
        assert landing.headers["location"].endswith("/feedvanta/login")
        assert client.get("/feedvanta/login").status_code == 200
        assert client.get("/feedvanta/health").json() == {"status": "ok"}
        mounted_app = host.routes[-1].app
        authenticate(client, mounted_app)
        overview = client.get("/feedvanta/")
        assert overview.status_code == 200
        assert "/feedvanta/admin/config\">Konfiguration" in overview.text


def test_host_honors_nginx_forwarded_prefix(tmp_path: Path):
    plugin = replace(
        PLUGIN,
        app_factory=lambda: create_app(
            str(tmp_path / "prefixed-host.db"), start_scheduler=False, session_secret=TEST_SESSION_SECRET
        ),
    )
    host = create_host([plugin])
    with TestClient(host) as client:
        headers = {"X-Forwarded-Prefix": "/suburl"}
        home = client.get("/", headers=headers)
        assert 'href="/suburl/feedvanta/"' in home.text
        landing = client.get("/feedvanta/", headers=headers, follow_redirects=False)
        assert landing.status_code == 303
        assert landing.headers["location"].endswith("/suburl/feedvanta/login")
        assert client.get("/feedvanta/login", headers=headers).status_code == 200


def test_host_base_path_environment_variable(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("TOOL_HOST_BASE_PATH", "/private-tools")
    plugin = replace(
        PLUGIN,
        app_factory=lambda: create_app(
            str(tmp_path / "configured-prefix.db"), start_scheduler=False, session_secret=TEST_SESSION_SECRET
        ),
    )
    with TestClient(create_host([plugin])) as client:
        home = client.get("/")
        assert 'href="/private-tools/feedvanta/"' in home.text
        landing = client.get("/feedvanta/", follow_redirects=False)
        assert landing.headers["location"].endswith("/private-tools/feedvanta/login")


def test_host_rejects_duplicate_plugin_paths():
    from tool_host.app import create_app as create_host

    duplicate = replace(PLUGIN, id="second")
    try:
        create_host([PLUGIN, duplicate])
    except ValueError as exc:
        assert "Duplicate plugin mount path" in str(exc)
    else:
        raise AssertionError("Duplicate plugin mount path was accepted")


def test_first_google_user_is_persistent_admin(tmp_path: Path):
    db_path = str(tmp_path / "test.db")
    app = create_app(db_path, start_scheduler=False, session_secret=TEST_SESSION_SECRET)
    first = app.state.auth_service.login_google_user({
        "sub": "google-1", "email": "first@example.com", "name": "First"
    })
    second = app.state.auth_service.login_google_user({
        "sub": "google-2", "email": "second@example.com", "name": "Second"
    })
    assert GLOBAL_ADMINISTRATION in first["permissions"]
    assert GLOBAL_ADMINISTRATION not in second["permissions"]

    restarted = create_app(db_path, start_scheduler=False, session_secret=TEST_SESSION_SECRET)
    assert GLOBAL_ADMINISTRATION in restarted.state.auth_service.get_user(first["id"])["permissions"]
    restarted.state.auth_service.set_permission(second["id"], GLOBAL_ADMINISTRATION, True)
    assert GLOBAL_ADMINISTRATION in restarted.state.auth_service.get_user(second["id"])["permissions"]


def test_unverified_google_email_is_rejected(tmp_path: Path):
    app = create_app(str(tmp_path / "test.db"), start_scheduler=False, session_secret=TEST_SESSION_SECRET)
    try:
        app.state.auth_service.login_google_user({
            "sub": "google-1", "email": "user@example.com", "email_verified": False,
        })
    except ValueError as exc:
        assert "bestätigte E-Mail-Adresse" in str(exc)
    else:
        raise AssertionError("Unverified Google email was accepted")


def test_configuration_requires_admin_but_feeds_are_public(tmp_path: Path):
    app = create_app(str(tmp_path / "test.db"), start_scheduler=False, session_secret=TEST_SESSION_SECRET)
    with app.state.db.connect() as conn:
        feed_id = conn.execute(
            "INSERT INTO feeds(name,source_url,refresh_interval,created_at) VALUES(?,?,?,?)",
            ("Public", "https://example.com/rss.xml", 30, "2026-09-26T00:00:00+00:00"),
        ).lastrowid
    with TestClient(app) as client:
        assert client.get("/", follow_redirects=False).status_code == 303
        login = client.get("/login")
        assert login.status_code == 200
        assert "FeedVanta" in login.text
        assert 'href="http://testserver/static/app.css"' in login.text
        assert client.get("/static/app.css").status_code == 200
        assert client.get("/api/feeds").status_code == 401
        assert client.get(f"/feed/{feed_id}.xml").status_code == 200
        assert client.get(f"/reader/{feed_id}").status_code == 200


def test_signed_in_user_without_permission_cannot_configure(tmp_path: Path):
    app = create_app(str(tmp_path / "test.db"), start_scheduler=False, session_secret=TEST_SESSION_SECRET)
    app.state.auth_service.login_google_user({"sub": "first", "email": "first@example.com"})
    with TestClient(app) as client:
        authenticate(client, app, sub="second", email="second@example.com")
        assert client.get("/").status_code == 403
        assert client.get("/api/feeds").status_code == 403


def test_versioned_config_export_import_roundtrip(tmp_path: Path):
    app = create_app(str(tmp_path / "test.db"), start_scheduler=False, session_secret=TEST_SESSION_SECRET)
    with TestClient(app) as client:
        authenticate(client, app)
        feed_id = client.post("/api/feeds", json={
            "name": "Configured feed", "source_url": "https://example.com/feed.xml",
            "refresh_interval": 45,
        }).json()["id"]
        client.post(f"/api/feeds/{feed_id}/rules", json={
            "field": "title", "operator": "contains", "value": "Sport", "action": "exclude",
        })

        exported_response = client.get("/api/config/export")
        assert exported_response.status_code == 200
        assert "attachment;" in exported_response.headers["content-disposition"]
        exported = exported_response.json()
        assert exported["app_version"] == "0.2.0"
        assert exported["schema_version"] == 1
        assert exported["config_version"] == 3
        assert exported["feeds"][0]["rules"][0]["value"] == "Sport"
        assert "users" not in exported
        assert "permissions" not in exported
        config_page = client.get("/admin/config")
        assert config_page.status_code == 200
        assert "App-Version" in config_page.text
        assert "Config-Version" in config_page.text

        client.post("/api/feeds", json={
            "name": "Temporary", "source_url": "https://example.com/temporary.xml",
        })
        imported = client.post("/api/config/import", json=exported)
        assert imported.status_code == 200
        assert imported.json() == {"imported_feeds": 1, "config_version": 3}
        feeds = client.get("/api/feeds").json()
        assert [feed["name"] for feed in feeds] == ["Configured feed"]
        assert app.state.configuration_service.revision() == 3


def test_config_import_rejects_incompatible_schema_without_changes(tmp_path: Path):
    app = create_app(str(tmp_path / "test.db"), start_scheduler=False, session_secret=TEST_SESSION_SECRET)
    with TestClient(app) as client:
        authenticate(client, app)
        client.post("/api/feeds", json={
            "name": "Keep me", "source_url": "https://example.com/feed.xml",
        })
        document = client.get("/api/config/export").json()
        document["schema_version"] = 999
        response = client.post("/api/config/import", json=document)
        assert response.status_code == 409
        assert client.get("/api/feeds").json()[0]["name"] == "Keep me"

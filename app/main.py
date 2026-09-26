import os
import secrets
from contextlib import asynccontextmanager
from datetime import datetime
from email.utils import format_datetime
from pathlib import Path
from xml.etree.ElementTree import Element, SubElement, tostring

from apscheduler.schedulers.background import BackgroundScheduler
from authlib.integrations.starlette_client import OAuth, OAuthError
from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field, HttpUrl
from dotenv import load_dotenv
from starlette.middleware.sessions import SessionMiddleware

from .auth import AuthService, GLOBAL_ADMINISTRATION
from .db import Database
from .models import utcnow
from .services import FeedService

BASE_DIR = Path(__file__).parent
load_dotenv()


def normalize_base_path(value: str) -> str:
    value = value.strip()
    if not value or value == "/":
        return ""
    return "/" + value.strip("/")


class SubpathMiddleware:
    """Expose an ASGI application below a configurable URL prefix."""

    def __init__(self, app, prefix: str):
        self.app = app
        self.prefix = normalize_base_path(prefix)

    async def __call__(self, scope, receive, send):
        if scope["type"] not in {"http", "websocket"} or not self.prefix:
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        if path == self.prefix:
            path = "/"
        elif path.startswith(self.prefix + "/"):
            path = path[len(self.prefix):]
        else:
            response = Response("Not Found", status_code=404)
            await response(scope, receive, send)
            return
        child_scope = dict(scope)
        child_scope["path"] = path
        child_scope["raw_path"] = path.encode("utf-8")
        child_scope["root_path"] = scope.get("root_path", "") + self.prefix
        await self.app(child_scope, receive, send)


class FeedCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    source_url: HttpUrl
    refresh_interval: int = Field(default=30, ge=1, le=10080)


class RuleCreate(BaseModel):
    field: str = Field(pattern="^(title|content|author|category|all)$")
    operator: str = Field(pattern="^(contains|regex|equals)$")
    value: str = Field(min_length=1, max_length=1000)
    action: str = Field(default="exclude", pattern="^(exclude|include)$")


def create_app(
    db_path: str | None = None,
    start_scheduler: bool = True,
    session_secret: str | None = None,
) -> FastAPI:
    database = Database(db_path or os.getenv("FEEDVANTA_DB", "data/feedvanta.db"))
    database.initialize()
    service = FeedService(database)
    auth_service = AuthService(database)
    scheduler = BackgroundScheduler(daemon=True)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if start_scheduler:
            scheduler.add_job(service.refresh_due, "interval", minutes=1, max_instances=1)
            scheduler.start()
        yield
        if scheduler.running:
            scheduler.shutdown(wait=False)

    app = FastAPI(title="FeedVanta", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        SessionMiddleware,
        secret_key=session_secret or os.getenv("FEEDVANTA_SESSION_SECRET") or secrets.token_urlsafe(32),
        same_site="lax",
        https_only=os.getenv("FEEDVANTA_SECURE_COOKIES", "false").lower() in {"1", "true", "yes"},
    )
    app.state.db = database
    app.state.service = service
    app.state.auth_service = auth_service
    templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
    oauth = OAuth()
    google_client_id = os.getenv("GOOGLE_CLIENT_ID")
    google_client_secret = os.getenv("GOOGLE_CLIENT_SECRET")
    if google_client_id and google_client_secret:
        oauth.register(
            name="google",
            client_id=google_client_id,
            client_secret=google_client_secret,
            server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
            client_kwargs={"scope": "openid email profile"},
        )

    def current_user(request: Request) -> dict | None:
        user_id = request.session.get("user_id")
        return auth_service.get_user(int(user_id)) if user_id else None

    def require_admin(request: Request) -> dict:
        user = current_user(request)
        if not user:
            raise HTTPException(401, "Anmeldung erforderlich")
        if GLOBAL_ADMINISTRATION not in user["permissions"]:
            raise HTTPException(403, "Global Administration erforderlich")
        return user

    def feeds_with_rules():
        with database.connect() as conn:
            feeds = [dict(row) for row in conn.execute("SELECT * FROM feeds ORDER BY name")]
            for feed in feeds:
                feed["rules"] = [dict(r) for r in conn.execute(
                    "SELECT * FROM filter_rules WHERE feed_id=? ORDER BY id", (feed["id"],)
                )]
                feed["visible_count"] = conn.execute(
                    "SELECT COUNT(*) FROM entries WHERE feed_id=? AND hidden=0", (feed["id"],)
                ).fetchone()[0]
                feed["hidden_count"] = conn.execute(
                    "SELECT COUNT(*) FROM entries WHERE feed_id=? AND hidden=1", (feed["id"],)
                ).fetchone()[0]
            return feeds

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request):
        user = current_user(request)
        if not user:
            return RedirectResponse(request.url_for("login"), status_code=303)
        if GLOBAL_ADMINISTRATION not in user["permissions"]:
            raise HTTPException(403, "Global Administration erforderlich")
        return templates.TemplateResponse(request, "index.html", {
            "feeds": feeds_with_rules(), "current_user": user,
            "users": auth_service.list_users(), "admin_permission": GLOBAL_ADMINISTRATION,
        })

    @app.get("/login", response_class=HTMLResponse)
    def login(request: Request):
        return templates.TemplateResponse(request, "login.html", {
            "google_configured": bool(google_client_id and google_client_secret),
        })

    @app.get("/auth/google")
    async def auth_google(request: Request):
        if not google_client_id or not google_client_secret:
            raise HTTPException(503, "Google-Anmeldung ist noch nicht konfiguriert")
        redirect_uri = request.url_for("auth_callback")
        return await oauth.google.authorize_redirect(request, redirect_uri)

    @app.get("/auth/google/callback")
    async def auth_callback(request: Request):
        if not google_client_id or not google_client_secret:
            raise HTTPException(503, "Google-Anmeldung ist noch nicht konfiguriert")
        try:
            token = await oauth.google.authorize_access_token(request)
            profile = token.get("userinfo")
            if not profile:
                profile = await oauth.google.userinfo(token=token)
            user = auth_service.login_google_user(dict(profile))
        except (OAuthError, ValueError) as exc:
            raise HTTPException(400, f"Google-Anmeldung fehlgeschlagen: {exc}") from exc
        request.session.clear()
        request.session["user_id"] = user["id"]
        return RedirectResponse(request.url_for("index"), status_code=303)

    @app.post("/logout")
    def logout(request: Request):
        request.session.clear()
        return RedirectResponse(request.url_for("login"), status_code=303)

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.get("/api/feeds")
    def list_feeds(_user: dict = Depends(require_admin)):
        return feeds_with_rules()

    @app.get("/reader/{feed_id}", response_class=HTMLResponse)
    def reader(request: Request, feed_id: int):
        with database.connect() as conn:
            feed = conn.execute("SELECT * FROM feeds WHERE id=?", (feed_id,)).fetchone()
            if not feed:
                raise HTTPException(404, "Feed nicht gefunden")
            entries = [dict(row) for row in conn.execute(
                "SELECT * FROM entries WHERE feed_id=? AND hidden=0 "
                "ORDER BY COALESCE(published,fetched_at) DESC LIMIT 100", (feed_id,)
            )]
        return templates.TemplateResponse(request, "reader.html", {
            "feed": dict(feed), "entries": entries,
        })

    def insert_feed(payload: FeedCreate) -> int:
        try:
            with database.connect() as conn:
                cur = conn.execute(
                    "INSERT INTO feeds(name,source_url,refresh_interval,created_at) VALUES(?,?,?,?)",
                    (payload.name, str(payload.source_url), payload.refresh_interval, utcnow()),
                )
                return cur.lastrowid
        except Exception as exc:
            if "UNIQUE" in str(exc):
                raise HTTPException(409, "Diese Feed-URL existiert bereits") from exc
            raise

    @app.post("/api/feeds", status_code=201)
    def create_feed(payload: FeedCreate, _user: dict = Depends(require_admin)):
        return {"id": insert_feed(payload)}

    @app.post("/feeds")
    def create_feed_form(request: Request, name: str = Form(...), source_url: str = Form(...), refresh_interval: int = Form(30), _user: dict = Depends(require_admin)):
        insert_feed(FeedCreate(name=name, source_url=source_url, refresh_interval=refresh_interval))
        return RedirectResponse(request.url_for("index"), status_code=303)

    @app.delete("/api/feeds/{feed_id}", status_code=204)
    def delete_feed(feed_id: int, _user: dict = Depends(require_admin)):
        with database.connect() as conn:
            if not conn.execute("DELETE FROM feeds WHERE id=?", (feed_id,)).rowcount:
                raise HTTPException(404, "Feed nicht gefunden")

    @app.post("/feeds/{feed_id}/delete")
    def delete_feed_form(request: Request, feed_id: int, _user: dict = Depends(require_admin)):
        delete_feed(feed_id, _user)
        return RedirectResponse(request.url_for("index"), status_code=303)

    def insert_rule(feed_id: int, payload: RuleCreate) -> int:
        with database.connect() as conn:
            if not conn.execute("SELECT 1 FROM feeds WHERE id=?", (feed_id,)).fetchone():
                raise HTTPException(404, "Feed nicht gefunden")
            cur = conn.execute(
                "INSERT INTO filter_rules(feed_id,field,operator,value,action) VALUES(?,?,?,?,?)",
                (feed_id, payload.field, payload.operator, payload.value, payload.action),
            )
        service.reapply_rules(feed_id)
        return cur.lastrowid

    @app.post("/api/feeds/{feed_id}/rules", status_code=201)
    def create_rule(feed_id: int, payload: RuleCreate, _user: dict = Depends(require_admin)):
        return {"id": insert_rule(feed_id, payload)}

    @app.post("/feeds/{feed_id}/rules")
    def create_rule_form(request: Request, feed_id: int, field: str = Form(...), operator: str = Form(...),
                         value: str = Form(...), action: str = Form(...), _user: dict = Depends(require_admin)):
        insert_rule(feed_id, RuleCreate(field=field, operator=operator, value=value, action=action))
        return RedirectResponse(request.url_for("index"), status_code=303)

    @app.delete("/api/rules/{rule_id}", status_code=204)
    def delete_rule(rule_id: int, _user: dict = Depends(require_admin)):
        with database.connect() as conn:
            row = conn.execute("SELECT feed_id FROM filter_rules WHERE id=?", (rule_id,)).fetchone()
            if not row:
                raise HTTPException(404, "Regel nicht gefunden")
            conn.execute("DELETE FROM filter_rules WHERE id=?", (rule_id,))
        service.reapply_rules(row["feed_id"])

    @app.post("/rules/{rule_id}/delete")
    def delete_rule_form(request: Request, rule_id: int, _user: dict = Depends(require_admin)):
        delete_rule(rule_id, _user)
        return RedirectResponse(request.url_for("index"), status_code=303)

    @app.post("/api/feeds/{feed_id}/refresh")
    def refresh(feed_id: int, _user: dict = Depends(require_admin)):
        try:
            return {"fetched": service.refresh(feed_id)}
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc
        except Exception as exc:
            raise HTTPException(502, f"Feed konnte nicht geladen werden: {exc}") from exc

    @app.post("/feeds/{feed_id}/refresh")
    def refresh_form(request: Request, feed_id: int, _user: dict = Depends(require_admin)):
        refresh(feed_id, _user)
        return RedirectResponse(request.url_for("index"), status_code=303)

    @app.post("/admin/users/{user_id}/global-administration")
    def update_global_administration(
        request: Request, user_id: int, enabled: bool = Form(False),
        _user: dict = Depends(require_admin),
    ):
        try:
            auth_service.set_permission(user_id, GLOBAL_ADMINISTRATION, enabled)
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        return RedirectResponse(request.url_for("index"), status_code=303)

    @app.get("/feed/{feed_id}.xml")
    def rss(feed_id: int, request: Request):
        with database.connect() as conn:
            feed = conn.execute("SELECT * FROM feeds WHERE id=?", (feed_id,)).fetchone()
            if not feed:
                raise HTTPException(404, "Feed nicht gefunden")
            entries = conn.execute(
                "SELECT * FROM entries WHERE feed_id=? AND hidden=0 "
                "ORDER BY COALESCE(published,fetched_at) DESC LIMIT 500", (feed_id,)
            ).fetchall()
        root = Element("rss", {"version": "2.0", "xmlns:atom": "http://www.w3.org/2005/Atom"})
        channel = SubElement(root, "channel")
        display_name = feed["source_title"] or feed["name"]
        SubElement(channel, "title").text = display_name
        SubElement(channel, "link").text = feed["source_url"]
        SubElement(channel, "description").text = f"Gefiltert durch FeedVanta: {display_name}"
        SubElement(channel, "generator").text = "FeedVanta"
        SubElement(channel, "atom:link", {"href": str(request.url), "rel": "self", "type": "application/rss+xml"})
        if feed["icon_url"]:
            image = SubElement(channel, "image")
            SubElement(image, "url").text = feed["icon_url"]
            SubElement(image, "title").text = display_name
            SubElement(image, "link").text = feed["source_url"]
        for entry in entries:
            item = SubElement(channel, "item")
            SubElement(item, "title").text = entry["title"]
            SubElement(item, "link").text = entry["url"]
            SubElement(item, "guid", isPermaLink="false").text = entry["guid"]
            SubElement(item, "description").text = entry["content"]
            if entry["author"]:
                SubElement(item, "author").text = entry["author"]
            if entry["published"]:
                try:
                    published = format_datetime(datetime.fromisoformat(entry["published"]))
                except ValueError:
                    published = entry["published"]
                SubElement(item, "pubDate").text = published
        xml = b'<?xml version="1.0" encoding="UTF-8"?>\n' + tostring(root, encoding="utf-8")
        return Response(xml, media_type="application/rss+xml; charset=utf-8")

    return app


_core_app = create_app()
_base_path = normalize_base_path(os.getenv("FEEDVANTA_BASE_PATH", ""))
app = SubpathMiddleware(_core_app, _base_path) if _base_path else _core_app

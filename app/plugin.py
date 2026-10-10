from contextlib import asynccontextmanager
from types import SimpleNamespace

from .main import create_app
from .version import APP_VERSION


@asynccontextmanager
async def lifespan(app):
    app.state.start_refresh_scheduler()
    try:
        yield
    finally:
        app.state.stop_refresh_scheduler()


PLUGIN = SimpleNamespace(
    id="feedvanta",
    name="FeedVanta",
    description="RSS/Atom feed reader, filtering, and publishing",
    version=APP_VERSION,
    path="/feedvanta",
    app_factory=lambda: create_app(start_scheduler=False),
    lifespan=lifespan,
)

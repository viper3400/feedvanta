from contextlib import asynccontextmanager

from .main import create_app
from .version import APP_VERSION
from tool_host.plugins import Plugin


@asynccontextmanager
async def lifespan(app):
    app.state.start_refresh_scheduler()
    try:
        yield
    finally:
        app.state.stop_refresh_scheduler()


PLUGIN = Plugin(
    id="feedvanta",
    name="FeedVanta",
    description="RSS/Atom feed reader, filtering, and publishing",
    version=APP_VERSION,
    path="/feedvanta",
    app_factory=lambda: create_app(start_scheduler=False),
    lifespan=lifespan,
)

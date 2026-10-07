from contextlib import AsyncExitStack, asynccontextmanager
from importlib.metadata import entry_points
from typing import Iterable

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from .plugins import ENTRY_POINT_GROUP, Plugin


def discover_plugins() -> list[Plugin]:
    discovered: list[Plugin] = []
    for point in entry_points(group=ENTRY_POINT_GROUP):
        plugin = point.load()
        if not isinstance(plugin, Plugin):
            raise TypeError(f"Plugin entry point {point.name!r} must expose a Plugin instance")
        discovered.append(plugin)
    return discovered


def create_app(plugins: Iterable[Plugin] | None = None) -> FastAPI:
    loaded_plugins = list(discover_plugins() if plugins is None else plugins)
    ids: set[str] = set()
    paths: set[str] = set()
    for plugin in loaded_plugins:
        if not plugin.id or plugin.id in ids:
            raise ValueError(f"Duplicate or empty plugin id: {plugin.id!r}")
        if not plugin.path.startswith("/") or plugin.path == "/" or plugin.path.endswith("/"):
            raise ValueError(f"Plugin {plugin.id!r} path must be an absolute non-root path without trailing slash")
        if plugin.path in paths:
            raise ValueError(f"Duplicate plugin mount path: {plugin.path!r}")
        ids.add(plugin.id)
        paths.add(plugin.path)

    apps = [(plugin, plugin.app_factory()) for plugin in loaded_plugins]

    @asynccontextmanager
    async def lifespan(host: FastAPI):
        async with AsyncExitStack() as stack:
            for plugin, plugin_app in apps:
                if plugin.lifespan:
                    await stack.enter_async_context(plugin.lifespan(plugin_app))
            yield

    host = FastAPI(title="Tool Host", lifespan=lifespan)
    host.get("/", response_class=HTMLResponse, name="home")(
        lambda: "<!doctype html><title>Tools</title><h1>Tools</h1><ul>"
        + "".join(
            f'<li><a href="{plugin.path}/">{plugin.name}</a> — {plugin.description}</li>'
            for plugin in loaded_plugins
        )
        + "</ul>"
    )
    host.get("/health", name="health")(
        lambda: {"status": "ok", "plugins": [plugin.id for plugin in loaded_plugins]}
    )
    for plugin, plugin_app in apps:
        host.mount(plugin.path, plugin_app, name=plugin.id)
    return host

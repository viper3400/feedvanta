import os
import re
from html import escape
from pathlib import Path
from contextlib import AsyncExitStack, asynccontextmanager
from importlib.metadata import entry_points
from typing import Iterable

from fastapi import FastAPI
from fastapi import Request
from fastapi.responses import FileResponse, HTMLResponse
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

    configured_prefix = os.getenv("TOOL_HOST_BASE_PATH", "").strip().rstrip("/")
    if configured_prefix and (
        not re.fullmatch(r"/(?:[A-Za-z0-9._~-]+/)*[A-Za-z0-9._~-]*", configured_prefix)
        or ".." in configured_prefix.split("/")
        or "//" in configured_prefix
    ):
        raise ValueError("TOOL_HOST_BASE_PATH must be a URL path prefix, e.g. '/suburl'")

    @asynccontextmanager
    async def lifespan(host: FastAPI):
        async with AsyncExitStack() as stack:
            for plugin, plugin_app in apps:
                if plugin.lifespan:
                    await stack.enter_async_context(plugin.lifespan(plugin_app))
            yield

    host = FastAPI(title="Tool Host", lifespan=lifespan)

    @host.get("/static/app.css", name="host_stylesheet")
    def host_stylesheet():
        stylesheet = Path(__file__).resolve().parent.parent / "app" / "static" / "app.css"
        return FileResponse(stylesheet, media_type="text/css")

    @host.middleware("http")
    async def forwarded_prefix(request: Request, call_next):
        prefix = request.headers.get("x-forwarded-prefix", "").strip() or configured_prefix
        if prefix and re.fullmatch(r"/(?:[A-Za-z0-9._~-]+/)*[A-Za-z0-9._~-]*", prefix):
            segments = prefix.split("/")
            if ".." not in segments and "//" not in prefix:
                prefix = prefix.rstrip("/")
                request.scope["root_path"] = prefix
                path = request.scope["path"]
                if path != prefix and not path.startswith(prefix + "/"):
                    request.scope["path"] = prefix + path
                    request.scope["raw_path"] = prefix.encode() + request.scope.get("raw_path", path.encode())
        return await call_next(request)

    @host.get("/", response_class=HTMLResponse, name="home")
    def home(request: Request):
        root_path = escape(request.scope.get("root_path", ""), quote=True)
        plugin_cards = "".join(
            f'<a class="group flex items-center gap-4 rounded-2xl border border-slate-200/80 bg-white/90 p-5 shadow-sm transition hover:-translate-y-0.5 hover:border-indigo-200 hover:shadow-lg hover:shadow-indigo-100/60" href="{root_path}{escape(plugin.path, quote=True)}/">'
            f'<span class="grid size-12 shrink-0 place-items-center rounded-xl bg-indigo-50 text-lg font-bold text-indigo-700 transition group-hover:bg-indigo-600 group-hover:text-white">{escape(plugin.name[:1].upper())}</span>'
            f'<span class="min-w-0 flex-1"><span class="block font-semibold tracking-tight text-slate-900">{escape(plugin.name)}</span>'
            f'<span class="mt-1 block text-sm leading-relaxed text-slate-500">{escape(plugin.description)}</span></span>'
            '<span class="text-lg text-slate-300 transition group-hover:translate-x-1 group-hover:text-indigo-600" aria-hidden="true">→</span></a>'
            for plugin in loaded_plugins
        )
        empty_state = (
            '<div class="rounded-2xl border border-dashed border-slate-300 bg-white/60 px-6 py-12 text-center text-slate-500">'
            'No tools are available yet.</div>'
        )
        stylesheet_url = f'{root_path}/static/app.css'
        return (
            '<!doctype html><html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>Tools</title>'
            f'<link rel="stylesheet" href="{stylesheet_url}"></head>'
            '<body class="min-h-screen bg-gradient-to-br from-slate-50 via-slate-100 to-indigo-50 font-sans text-slate-900 antialiased">'
            '<main class="mx-auto w-full max-w-4xl px-4 py-12 sm:px-6 sm:py-20">'
            '<header class="mb-8 rounded-3xl border border-white/80 bg-white/80 p-7 shadow-sm shadow-slate-200/60 backdrop-blur sm:p-9">'
            '<span class="mb-5 inline-flex rounded-full bg-indigo-50 px-3 py-1 text-xs font-semibold uppercase tracking-wider text-indigo-700">Workspace</span>'
            '<h1 class="text-3xl font-bold tracking-tight text-slate-950 sm:text-4xl">Your tools, in one place.</h1>'
            '<p class="mt-3 max-w-xl leading-relaxed text-slate-500">Choose a tool to get started. Each workspace is ready when you are.</p>'
            '</header><section class="grid gap-3 sm:grid-cols-2" aria-label="Available tools">'
            f'{plugin_cards or empty_state}'
            '</section></main></body></html>'
        )
    host.get("/health", name="health")(
        lambda: {"status": "ok", "plugins": [plugin.id for plugin in loaded_plugins]}
    )
    for plugin, plugin_app in apps:
        host.mount(plugin.path, plugin_app, name=plugin.id)
    return host

"""Flask application factory (spec 10.2)."""
from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import Any, Callable

from flask import Flask, abort, make_response, redirect, request, send_from_directory

from . import (api_calendars, api_files, api_history, api_items, api_search, api_system, api_tags, api_vault, common,
               security)
from .events import Hub, Watcher
from .sync_manager import SyncManager, default_adapter_factory

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
AGENT_FILE = Path(__file__).resolve().parents[3] / "agent" / "reframe-agent-instructions.md"
ICON_FILE = Path(__file__).resolve().parents[3] / "assets" / "reframe.ico"

# The Windows registry can map .js to text/plain, which breaks ES module loading.
for _ext, _type in ((".js", "text/javascript"), (".mjs", "text/javascript"), (".css", "text/css"),
                    (".woff2", "font/woff2"), (".woff", "font/woff"), (".svg", "image/svg+xml")):
    mimetypes.add_type(_type, _ext)


def create_app(db_path: str | Path, *, token: str | None = None, port: int = 0, host: Any = None,
               adapter_factory: Callable[[Any], Any] = default_adapter_factory) -> Flask:
    app = Flask(__name__, static_folder=str(WEB_DIR), static_url_path="/static")
    app.config.update(
        DB_PATH=str(db_path), TOKEN=token or security.new_token(), PORT=port, HOST=host,
        ADAPTER_FACTORY=adapter_factory, AGENT_FILE=str(AGENT_FILE), MAX_CONTENT_LENGTH=8 * 1024 * 1024,
        JSON_SORT_KEYS=False,
    )
    app.json.sort_keys = False
    hub = Hub()
    app.config["HUB"] = hub
    app.config["SYNC"] = SyncManager(str(db_path), hub, adapter_factory)
    security.install(app)
    common.register_errors(app)
    for bp in (api_items.bp, api_tags.bp, api_history.bp, api_system.bp, api_calendars.bp):
        app.register_blueprint(bp)
    api_search.init_app(app)
    api_files.init_app(app)
    api_vault.init_app(app)

    @app.get("/")
    def index():
        supplied = request.args.get("t")
        if supplied is not None:
            if not security._token_ok(supplied, app.config["TOKEN"]):
                abort(403, "missing or wrong token")
            resp = make_response(redirect("/"))
            resp.set_cookie(security.TOKEN_COOKIE, supplied, httponly=True, samesite="Strict")
            return resp
        if not security._token_ok(request.cookies.get(security.TOKEN_COOKIE), app.config["TOKEN"]):
            abort(403, "missing or wrong token")
        return send_from_directory(WEB_DIR, "index.html")

    @app.get("/favicon.ico")
    def favicon():
        return send_from_directory(ICON_FILE.parent, ICON_FILE.name, mimetype="image/x-icon")

    return app


def start_watcher(app: Flask, interval_s: float = 1.0) -> Watcher:
    w = Watcher(app.config["DB_PATH"], app.config["HUB"], interval_s)
    w.start()
    app.config["WATCHER"] = w
    return w

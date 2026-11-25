"""Everything file search API (spec R-121 - R-128). Results are data only; nothing here opens files."""
from __future__ import annotations

import threading

from flask import Blueprint, Flask, current_app, jsonify, request

from ..domain import settings
from ..search.everything import SORTS, EverythingService
from . import common

bp = Blueprint("search", __name__, url_prefix="/api")
_create_lock = threading.Lock()
PAGE_MAX = 500


def init_app(app: Flask) -> None:
    if bp.name not in app.blueprints:
        app.register_blueprint(bp)
    app.config.setdefault("EVERYTHING", None)


def service() -> EverythingService:
    """The app's Everything service, created on first use (EVERYTHING_FACTORY overrides for tests)."""
    cfg = current_app.config
    svc = cfg.get("EVERYTHING")
    if svc is None:
        with _create_lock:
            svc = cfg.get("EVERYTHING")
            if svc is None:
                factory = cfg.get("EVERYTHING_FACTORY") or EverythingService
                svc = cfg["EVERYTHING"] = factory()
    return svc


def _enabled() -> bool:
    return bool(settings.get(common.db(), "everything_enabled"))


def _int_arg(name: str, default: int, lo: int, hi: int) -> int:
    raw = request.args.get(name, "")
    try:
        v = int(raw) if raw != "" else default
    except ValueError as e:
        raise ValueError(f"{name} must be an integer") from e
    return max(lo, min(v, hi))


@bp.get("/search/files")
def search_files():
    rid = request.args.get("rid", "")
    if not _enabled():
        return jsonify(request_id=rid, state="disabled", message="Everything search is off in Settings.",
                       results=[], total=None)
    q = request.args.get("q", "").strip()
    sort = request.args.get("sort", "modified")
    if sort not in SORTS:
        sort = "modified"
    offset = _int_arg("offset", 0, 0, 10_000_000)
    limit = _int_arg("limit", 100, 1, PAGE_MAX)
    if not q:
        return jsonify(request_id=rid, state="empty", message="", results=[], total=None, offset=offset, limit=limit)
    client = request.args.get("client", "path_bar")[:64]
    return jsonify(service().search(q, offset=offset, limit=limit, sort=sort, request_id=rid, client=client))


@bp.get("/search/status")
def search_status():
    return jsonify(enabled=_enabled(), **service().status())


@bp.post("/search/start-everything")
def start_everything():
    ok = service().start_everything()
    return jsonify(started=ok, message="" if ok else "Everything.exe was not found or could not be started.")

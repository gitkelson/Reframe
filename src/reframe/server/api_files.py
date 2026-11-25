"""File timeline endpoints (spec 3.12, 10.2). Read-only over the owner's files: entries are addressed by opaque ids of
the current session index; no endpoint accepts a file path to read, serve or open.

The host wires it up with ``init_app(app)`` (creates the service, does not start it), then ``start(app)`` after the
server is running and ``stop(app)`` on quit.
"""
from __future__ import annotations

from typing import Any

from flask import Blueprint, Response, current_app, jsonify, request

from ..files.roots import RootsError
from ..files.icons import file_type_icon
from ..files.scanner import suggested_exclusions
from ..files.service import FileTimelineService, OpenRefused
from .common import body, err

bp = Blueprint("files", __name__, url_prefix="/api")


def init_app(app, **service_kwargs: Any) -> FileTimelineService:
    if "files" not in app.blueprints:
        app.register_blueprint(bp)
    svc = FileTimelineService(app.config["DB_PATH"], app.config.get("HUB"), **service_kwargs)
    app.config["FILES"] = svc
    return svc


def start(app) -> None:
    svc = app.config.get("FILES")
    if svc is not None:
        svc.start()


def stop(app) -> None:
    svc = app.config.get("FILES")
    if svc is not None:
        svc.stop()


def _svc() -> FileTimelineService:
    return current_app.config["FILES"]


@bp.get("/files/status")
def status():
    return jsonify(_svc().status())


@bp.get("/files/options")
def get_options():
    return jsonify(_svc().options())


@bp.put("/files/options")
def put_options():
    b = body()
    unknown = sorted(set(b) - {"roots", "excluded_ext", "include_hidden"})
    if unknown:
        return err("Unknown option(s): " + ", ".join(unknown), 400, "invalid")
    try:
        out = _svc().set_options(roots=b.get("roots"), excluded_ext=b.get("excluded_ext"),
                                 include_hidden=b.get("include_hidden"))
    except RootsError as e:
        return err("These folders cannot be used as roots: " + " ".join(e.problems), 400, "bad_roots",
                   problems=e.problems)
    return jsonify(out)


@bp.get("/files/suggested-exclusions")
def suggested():
    return jsonify(suggestions=suggested_exclusions())


@bp.post("/files/rescan")
def rescan():
    return jsonify(_svc().rescan())


@bp.post("/files/cancel")
def cancel():
    return jsonify(_svc().cancel())


@bp.get("/files/entries")
def entries():
    mode = request.args.get("mode", "created")
    if mode not in ("created", "modified"):
        return err("mode must be 'created' or 'modified'", 400, "invalid")
    return jsonify(_svc().entries(mode))


@bp.get("/files/entry/<eid>")
def entry(eid):
    d = _svc().details(eid)
    if d is None:
        return err("No such file in the current list.", 404, "not_found")
    return jsonify(d)


@bp.get("/files/thumb/<eid>")
def thumb(eid):
    try:
        gen = int(request.args.get("g", "0"))
    except ValueError:
        gen = 0
    res = _svc().thumb(eid, gen)
    if res is None:
        return err("No such image in the current list.", 404, "not_found")
    if res.status == "ok":
        return Response(res.data, mimetype=res.mimetype)
    # busy / cancelled (stale generation) are not failures: the client simply asks again later (200 keeps the
    # browser console free of resource errors)
    retry = res.reason in ("cancelled", "busy")
    return jsonify(placeholder=res.reason, retry=retry)


@bp.get("/files/icon")
def icon():
    ext = request.args.get("ext", "").lower()
    if len(ext) > 255 or (ext and (not ext.startswith(".") or any(c in ext for c in '\\/:\0*?"<>|'))):
        return err("Invalid file extension.", 400, "invalid")
    try:
        return Response(file_type_icon(ext), mimetype="image/png")
    except (ImportError, OSError) as exc:
        current_app.logger.warning("Could not load Windows icon for %r: %s", ext, exc)
        return jsonify(placeholder="Windows icon unavailable", retry=False)


def _act(eid: str, what: str):
    try:
        path = getattr(_svc(), what)(eid)
    except OpenRefused as e:
        return err(str(e), e.status, "refused")
    except OSError as e:
        return err(f"Windows could not open it: {e.strerror or e}", 500, "open_failed")
    return jsonify(ok=True, path=path)


@bp.post("/files/open/<eid>")
def open_file(eid):
    return _act(eid, "open")


@bp.post("/files/reveal/<eid>")
def reveal_file(eid):
    return _act(eid, "reveal")

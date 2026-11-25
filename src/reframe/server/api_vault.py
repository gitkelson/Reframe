"""Attachment vault endpoints (R-160 - R-166).

Registered by ``init_app(app)``, which also creates ``app.config["VAULT"]`` (a ``VaultService``, NOT started:
the host starts it after the launch backup and stops it on Quit).
"""
from __future__ import annotations

from pathlib import Path

from flask import Blueprint, current_app, jsonify

from ..vault import materialize
from ..vault.materialize import OpenError
from ..vault.service import VaultService
from .common import body, db, err

bp = Blueprint("vault", __name__, url_prefix="/api")


def init_app(app) -> None:
    if bp.name not in app.blueprints:
        app.register_blueprint(bp)
    if app.config.get("VAULT") is None:
        app.config["VAULT"] = VaultService(app.config["DB_PATH"], app.config["HUB"])


def _svc() -> VaultService:
    return current_app.config["VAULT"]


@bp.errorhandler(OpenError)
def _open_error(e: OpenError):
    return err(e.message, e.status, e.kind)


@bp.errorhandler(OSError)
def _os_error(e: OSError):
    return err(f"File operation failed: {e.strerror or e}", 500, "io")


# ------------------------------------------------------------------ status / per-link

@bp.get("/vault/status")
def vault_status():
    """Totals for Settings > Attachments: versions, stored/logical size, savings, path, warnings, unprotected."""
    s = _svc().status(db(), detail=True)
    return jsonify(s)


@bp.get("/attachments/<att_id>/protection")
def protection(att_id):
    return jsonify(materialize.protection(db(), att_id))


@bp.get("/attachments/<att_id>/versions")
def versions(att_id):
    return jsonify(versions=materialize.versions(db(), att_id))


# ------------------------------------------------------------------ opening (R-162)

def _legacy_open(att_id):
    from . import api_items
    return api_items.open_attachment(att_id)


@bp.post("/attachments/<att_id>/open-smart")
def open_smart(att_id):
    """Click on a link: file links per R-162; folders and URLs are opened directly (R-169)."""
    r = db().execute("SELECT kind FROM attachment WHERE id=?", (att_id,)).fetchone()
    if r is None:
        return err("No such link", 404, "not_found")
    if r["kind"] != "file":
        return _legacy_open(att_id)
    return jsonify(materialize.open_attachment(db(), _svc().store, att_id, "pinned"))


@bp.post("/attachments/<att_id>/open-version")
def open_version(att_id):
    """Notice actions: {"version": "current" | "latest" | "<file_version id>"} (``version_id`` also accepted)."""
    b = body()
    which = str(b.get("version") or b.get("version_id") or "").strip()
    if not which:
        return err("Say which version to open: 'current', 'latest' or a version id", 400, "invalid")
    return jsonify(materialize.open_attachment(db(), _svc().store, att_id, which))


@bp.post("/vault/versions/<vid>/restore")
def restore_version(vid):
    """{"dest": "<path from the native save chooser>"}. Refuses an existing path (never overwrites)."""
    dest = str(body().get("dest") or "").strip()
    if not dest:
        return err("Choose where to save the version", 400, "invalid")
    path = materialize.restore_version(db(), _svc().store, vid, dest)
    return jsonify(ok=True, path=str(path))


# ------------------------------------------------------------------ storage controls

@bp.post("/vault/prune")
def prune():
    """R-165: {"confirm": false} reports what would go; {"confirm": true} deletes it."""
    return jsonify(_svc().prune(confirm=bool(body().get("confirm"))))


@bp.post("/vault/move")
def move():
    """R-166: {"dest": "<folder>"} copies + verifies every blob, then switches. The old folder is kept."""
    dest = str(body().get("dest") or "").strip()
    if not dest:
        return err("Choose a folder for the vault", 400, "invalid")
    p = Path(dest)
    if p.exists() and not p.is_dir():
        return err("That path is a file, not a folder", 400, "invalid")
    if p.is_dir() and any(x for x in p.iterdir() if x.name not in ("objects", "staging")):
        return err("Choose an empty folder (or an existing vault folder)", 400, "invalid")
    return jsonify(_svc().move(dest))


@bp.post("/vault/reconcile")
def reconcile_now():
    """Retry now: check every attached file (also done at launch and every 15 min)."""
    _svc().reconcile_now(deep=bool(body().get("deep", True)))
    return jsonify(ok=True), 202

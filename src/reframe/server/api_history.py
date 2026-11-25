"""History, undo and redo endpoints (R-81, R-82)."""
from __future__ import annotations

from flask import Blueprint, jsonify, request

from ..db import undo as undomod
from ..domain import history as historymod
from ..domain import items as itemmod
from .common import db, undo_state

bp = Blueprint("history", __name__, url_prefix="/api")


@bp.get("/history")
def list_history():
    before = request.args.get("before", type=int)
    return jsonify(history=historymod.list_change_sets(db(), int(request.args.get("limit", 200)), before))


@bp.get("/history/<int:cs_id>")
def detail(cs_id):
    return jsonify(historymod.change_set_detail(db(), cs_id))


@bp.post("/history/<int:cs_id>/revert")
def revert(cs_id):
    res = undomod.revert_change_set(db(), cs_id)
    return jsonify(ok=True, **res)


@bp.get("/history/hidden-removed")
def hidden_removed():
    return jsonify(historymod.hidden_and_removed(db()))


@bp.get("/undo-state")
def state():
    return jsonify(undo_state(db()))


@bp.post("/undo")
def do_undo():
    try:
        return jsonify(ok=True, **undomod.undo(db()))
    except undomod.NothingToDo as e:
        return jsonify(ok=False, message=str(e))


@bp.post("/redo")
def do_redo():
    try:
        return jsonify(ok=True, **undomod.redo(db()))
    except undomod.NothingToDo as e:
        return jsonify(ok=False, message=str(e))


@bp.get("/needs-attention")
def needs_attention():
    return jsonify(itemmod.needs_attention(db()))

"""Request security for the local server (spec 10.2).

Loopback only, per-launch token, Host/Origin checks, strict CSP, JSON-only writes.
"""
from __future__ import annotations

import hmac
import secrets

from flask import Flask, Response, abort, jsonify, request

TOKEN_HEADER = "X-Reframe-Token"
TOKEN_COOKIE = "reframe_token"

CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; font-src 'self'; "
       "connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'")


def new_token() -> str:
    return secrets.token_urlsafe(32)


def _token_ok(supplied: str | None, expected: str) -> bool:
    return bool(supplied) and hmac.compare_digest(supplied.encode(), expected.encode())


def install(app: Flask) -> None:
    @app.before_request
    def _guard():
        allowed_host = f"127.0.0.1:{app.config['PORT']}"
        if request.headers.get("Host") != allowed_host:
            abort(403, "bad host")
        origin = request.headers.get("Origin")
        if origin is not None and origin != f"http://{allowed_host}":
            abort(403, "bad origin")
        path = request.path
        if path.startswith("/api/"):
            supplied = request.headers.get(TOKEN_HEADER) or request.cookies.get(TOKEN_COOKIE)
            if not _token_ok(supplied, app.config["TOKEN"]):
                abort(403, "missing or wrong token")
            if request.method not in ("GET", "HEAD", "OPTIONS"):
                # JSON-only writes defeat cross-site form posts (no CORS is ever sent)
                if request.content_length and not request.is_json:
                    abort(415, "JSON body required")

    @app.after_request
    def _headers(resp: Response) -> Response:
        resp.headers["Content-Security-Policy"] = CSP
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Referrer-Policy"] = "no-referrer"
        resp.headers["Cache-Control"] = "no-store"
        resp.headers.pop("Access-Control-Allow-Origin", None)
        return resp

    @app.errorhandler(403)
    @app.errorhandler(415)
    def _plain(e):
        return jsonify(error="forbidden", message=getattr(e, "description", "forbidden")), e.code

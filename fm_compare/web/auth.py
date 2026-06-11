"""
Minimal single-password gate.

The whole service is protected by one shared password (APP_PASSWORD). On
correct entry we set a signed session cookie; subsequent requests are checked
against it. This guards financial data from casual access on the LAN.

The password and the session secret are read from the environment only — never
hardcoded, never logged. If APP_PASSWORD is unset the gate is OPEN and we log a
loud warning (useful for local dev, unsafe for the VM).
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets

from fastapi import Request
from fastapi.responses import JSONResponse, RedirectResponse

from fm_compare.security import safe_logger as log

COOKIE_NAME = "fm_session"
_PUBLIC_PREFIXES = ("/login", "/healthz", "/readyz", "/static/login")


def _password() -> str | None:
    pw = os.environ.get("APP_PASSWORD")
    return pw if pw else None


def _secret() -> bytes:
    # Stable per-process secret; if APP_SECRET unset, derive a random one
    # (sessions won't survive restart, which is acceptable here).
    s = os.environ.get("APP_SECRET")
    if s:
        return s.encode("utf-8")
    if not hasattr(_secret, "_cached"):
        _secret._cached = secrets.token_bytes(32)  # type: ignore[attr-defined]
    return _secret._cached  # type: ignore[attr-defined]


def _expected_token() -> str:
    pw = _password() or ""
    return hmac.new(_secret(), pw.encode("utf-8"), hashlib.sha256).hexdigest()


def check_password(candidate: str) -> bool:
    pw = _password()
    if pw is None:
        return True
    return hmac.compare_digest(candidate or "", pw)


def session_token() -> str:
    return _expected_token()


def is_authenticated(request: Request) -> bool:
    if _password() is None:
        return True  # gate open in dev
    token = request.cookies.get(COOKIE_NAME, "")
    return hmac.compare_digest(token, _expected_token())


async def auth_middleware(request: Request, call_next):
    path = request.url.path
    if any(path.startswith(p) for p in _PUBLIC_PREFIXES) or is_authenticated(request):
        return await call_next(request)

    # API calls get 401 JSON; page navigations get redirected to /login.
    if path.startswith("/api/"):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    return RedirectResponse(url="/login", status_code=302)


def warn_if_open() -> None:
    if _password() is None:
        log.warning("APP_PASSWORD is not set — the service is UNPROTECTED (dev mode).")

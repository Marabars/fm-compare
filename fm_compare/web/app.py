"""
FastAPI application entry point.

Run locally:
    uvicorn fm_compare.web.app:app --host 0.0.0.0 --port 8000

The browser front-end (static/) is served from the same origin, so all LLM
calls go through this backend — the frontend never holds gateway credentials.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from fm_compare.core.llm.config import _find_dotenv, _load_dotenv
from fm_compare.security import safe_logger as log
from fm_compare.web import auth
from fm_compare.web.routes import router

_STATIC_DIR = Path(__file__).parent / "static"


def _apply_dotenv() -> None:
    """Load .env / .gateway.env into os.environ (only missing keys)."""
    import os
    path = _find_dotenv()
    if path is None:
        return
    for key, val in _load_dotenv(path).items():
        if key not in os.environ:
            os.environ[key] = val


def create_app() -> FastAPI:
    _apply_dotenv()
    log.setup_logging()
    auth.warn_if_open()

    app = FastAPI(title="FM Compare", docs_url=None, redoc_url=None)

    # Password gate for everything except /login, /healthz, /static/login*.
    app.middleware("http")(auth.auth_middleware)

    app.include_router(router)
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

    @app.get("/healthz", response_class=PlainTextResponse)
    async def healthz():
        return "ok"

    @app.get("/readyz", response_class=PlainTextResponse)
    async def readyz():
        return "ready"

    @app.get("/", response_class=HTMLResponse)
    async def index():
        return (_STATIC_DIR / "index.html").read_text(encoding="utf-8")

    @app.get("/login", response_class=HTMLResponse)
    async def login_page(request: Request):
        # If already authenticated, bounce to the app.
        if auth.is_authenticated(request):
            from fastapi.responses import RedirectResponse
            return RedirectResponse(url="/", status_code=302)
        return (_STATIC_DIR / "login.html").read_text(encoding="utf-8")

    return app


app = create_app()

"""Webdelen (FastAPI).

    uvicorn app.api.main:app --host 0.0.0.0 --port 8000

GET /health – til overvågning; svarer aldrig med hemmeligheder eller kundedata.
Login med Microsoft (app/api/login.py): /login, /logout, /mig og /admin/medarbejdere.
"""

import secrets
from html import escape

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import text
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware

from app.api.data import router as data_router
from app.api.login import db, nuvaerende_medarbejder
from app.api.login import router as login_router
from app.config import get_settings
from app.db import get_engine
from app.fejlrapport import init_fejlrapport

init_fejlrapport("api")

app = FastAPI(title="Afstemningsmotor", version="0.1")

_indstillinger = get_settings()
app.add_middleware(
    SessionMiddleware,
    # Uden SESSION_SECRET er login slået fra; en tilfældig nøgle gør så alle cookies ugyldige.
    secret_key=(_indstillinger.session_secret.get_secret_value() if _indstillinger.session_secret
                else secrets.token_hex(32)),
    session_cookie="afstemning_login",
    max_age=10 * 3600,  # en arbejdsdag – derefter logges man ind igen
    same_site="lax",
    https_only=_indstillinger.app_env == "production",
)
app.include_router(login_router)
app.include_router(data_router)


@app.get("/", response_class=HTMLResponse)
def forside(request: Request, session: Session = Depends(db)) -> str:
    hoved = '<!doctype html><meta charset="utf-8"><title>Afstemning</title><h1>Afstemningsmotor</h1>'
    try:
        m = nuvaerende_medarbejder(request, session)
    except HTTPException:
        return hoved + '<p><a href="/login">Log ind med Microsoft</a></p>'
    rolle = "administrator" if m.rolle == "admin" else "medarbejder"
    return hoved + f'<p>Logget ind som {escape(m.navn)} ({rolle}).</p><p><a href="/logout">Log ud</a></p>'


@app.get("/health")
def health() -> JSONResponse:
    """200, når webdelen kører OG databasen svarer og er opdateret; ellers 503."""
    try:
        with get_engine().connect() as forbindelse:
            version = forbindelse.execute(text("SELECT version_num FROM alembic_version")).scalar()
    except Exception:  # noqa: BLE001 – detaljer kan indeholde serveradresser; vises ikke
        return JSONResponse({"status": "fejl", "database": "svarer ikke"}, status_code=503)
    return JSONResponse({"status": "ok", "database": "ok", "databaseversion": version})

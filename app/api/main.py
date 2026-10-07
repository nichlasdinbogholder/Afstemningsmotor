"""Webdelen (FastAPI).

    uvicorn app.api.main:app --host 0.0.0.0 --port 8000

Indtil videre kun GET /health – til overvågning og til at se, at serveren kører.
Den svarer aldrig med hemmeligheder eller kundedata. Alt andet end /health ligger
på serveren bag en adgangskode i Caddy, indtil der er rigtigt login.
"""

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.db import get_engine
from app.fejlrapport import init_fejlrapport

init_fejlrapport("api")

app = FastAPI(title="Afstemningsmotor", version="0.1")


@app.get("/health")
def health() -> JSONResponse:
    """200, når webdelen kører OG databasen svarer og er opdateret; ellers 503."""
    try:
        with get_engine().connect() as forbindelse:
            version = forbindelse.execute(text("SELECT version_num FROM alembic_version")).scalar()
    except Exception:  # noqa: BLE001 – detaljer kan indeholde serveradresser; vises ikke
        return JSONResponse({"status": "fejl", "database": "svarer ikke"}, status_code=503)
    return JSONResponse({"status": "ok", "database": "ok", "databaseversion": version})


# MIDLERTIDIG – kun til at se, at fejl fra webdelen lander i Sentry. FJERNES IGEN.
@app.get("/debug/boom")
def debug_boom() -> None:
    raise RuntimeError("Test af Sentry: /debug/boom")

"""Login med Microsoft – samme konto og adgangskode som Outlook.

Sådan virker det:
1. /login sender brugeren til Microsofts login-side (jeres egen Microsoft 365 – ingen andre).
2. Microsoft sender brugeren tilbage til /auth/callback med et underskrevet bevis på, hvem
   de er. Beviset tjekkes (underskrift, udsteder, at det er JERES Microsoft 365).
3. E-mailen skal findes som AKTIV medarbejder i tabellen `staff`. Ellers ingen adgang –
   også selvom personen har en Microsoft-konto hos jer.
4. Brugeren får en underskrevet login-cookie. Ved HVER forespørgsel slås medarbejderen op
   igen, så en medarbejder, der sættes til aktiv = false, mister adgangen med det samme.

Roller: `admin` (administrator) og `medarbejder` (almindelig bruger) – se Staff.
Medarbejdere oprettes med: python -m app.personale.bruger opret --email ... --navn ... --rolle ...

Intet her logger eller viser hemmeligheder (client secret, cookie-nøgle, Microsofts beviser).
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit.models import AuditLog
from app.config import get_settings
from app.db import ny_session
from app.personale.models import Staff

router = APIRouter()
SESSION_NOEGLE = "medarbejder_id"
_oauth = None


class LoginAfvist(Exception):
    """Microsoft-login lykkedes, men personen må ikke komme ind her."""


def db():
    with ny_session() as session:
        yield session


def login_slaaet_til() -> bool:
    s = get_settings()
    return bool(s.ms_tenant_id and s.ms_client_id and s.ms_client_secret and s.session_secret)


def _microsoft():
    global _oauth
    if _oauth is None:
        from authlib.integrations.starlette_client import OAuth

        s = get_settings()
        _oauth = OAuth()
        _oauth.register(
            "microsoft",
            client_id=s.ms_client_id,
            client_secret=s.ms_client_secret.get_secret_value(),
            # Kun JERES Microsoft 365 (tenant) – ikke "alle Microsoft-konti".
            server_metadata_url=f"https://login.microsoftonline.com/{s.ms_tenant_id}/v2.0/"
                                ".well-known/openid-configuration",
            client_kwargs={"scope": "openid email profile"},
        )
    return _oauth.microsoft


def find_medarbejder(session: Session, claims: dict) -> Staff:
    """Fra Microsofts (allerede kontrollerede) oplysninger til en aktiv medarbejder – eller LoginAfvist."""
    s = get_settings()
    if claims.get("tid") != s.ms_tenant_id:
        raise LoginAfvist("kontoen hører ikke til jeres Microsoft 365")
    oid = claims.get("oid")
    email = (claims.get("email") or claims.get("preferred_username") or "").strip().lower()
    if not oid or not email:
        raise LoginAfvist("Microsoft sendte ingen e-mail eller id")
    medarbejder = session.scalars(select(Staff).where(Staff.email == email)).one_or_none()
    if medarbejder is None or not medarbejder.aktiv:
        raise LoginAfvist(f"{email} er ikke oprettet som aktiv medarbejder")
    if medarbejder.microsoft_oid is None:
        medarbejder.microsoft_oid = oid  # bindes ved første login
    elif medarbejder.microsoft_oid != oid:
        raise LoginAfvist(f"{email} tilhører en anden Microsoft-konto end første gang")
    medarbejder.sidst_logget_ind = datetime.now(timezone.utc)
    return medarbejder


def nuvaerende_medarbejder(request: Request, session: Session = Depends(db)) -> Staff:
    staff_id = request.session.get(SESSION_NOEGLE) if "session" in request.scope else None
    medarbejder = session.get(Staff, staff_id) if staff_id else None
    if medarbejder is None or not medarbejder.aktiv:
        if "session" in request.scope:
            request.session.clear()
        raise HTTPException(status_code=401, detail="Log ind først: /login")
    return medarbejder


def kraev_admin(medarbejder: Staff = Depends(nuvaerende_medarbejder)) -> Staff:
    if medarbejder.rolle != "admin":
        raise HTTPException(status_code=403, detail="Kun for administratorer")
    return medarbejder


@router.get("/login")
async def login(request: Request):
    if not login_slaaet_til():
        raise HTTPException(status_code=503, detail="Microsoft-login er ikke sat op endnu")
    tilbage = get_settings().public_url.rstrip("/") + "/auth/callback"
    return await _microsoft().authorize_redirect(request, tilbage)


@router.get("/auth/callback")
async def callback(request: Request, session: Session = Depends(db)):
    if not login_slaaet_til():
        raise HTTPException(status_code=503, detail="Microsoft-login er ikke sat op endnu")
    try:
        svar = await _microsoft().authorize_access_token(request)  # tjekker underskrift, udsteder, nonce
    except Exception:  # noqa: BLE001 – detaljerne kan indeholde beviser fra Microsoft; vises ikke
        raise HTTPException(status_code=400, detail="Login mislykkedes – prøv igen") from None
    claims = dict(svar.get("userinfo") or {})
    try:
        medarbejder = find_medarbejder(session, claims)
    except LoginAfvist as fejl:
        session.add(AuditLog(handling="login_afvist", detaljer={"aarsag": str(fejl)}))
        session.commit()
        raise HTTPException(status_code=403, detail="Du har ikke adgang. Kontakt en administrator.") from None
    request.session.clear()
    request.session[SESSION_NOEGLE] = medarbejder.id
    session.add(AuditLog(staff_id=medarbejder.id, handling="login", detaljer={"rolle": medarbejder.rolle}))
    session.commit()
    return RedirectResponse("/", status_code=303)


@router.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/", status_code=303)


@router.get("/mig")
def mig(medarbejder: Staff = Depends(nuvaerende_medarbejder)) -> dict:
    return {"navn": medarbejder.navn, "email": medarbejder.email, "rolle": medarbejder.rolle}


@router.get("/admin/medarbejdere")
def medarbejdere(_: Staff = Depends(kraev_admin), session: Session = Depends(db)) -> list[dict]:
    return [{"id": m.id, "navn": m.navn, "email": m.email, "rolle": m.rolle, "aktiv": m.aktiv,
             "sidst_logget_ind": m.sidst_logget_ind.isoformat() if m.sidst_logget_ind else None}
            for m in session.scalars(select(Staff).order_by(Staff.navn))]

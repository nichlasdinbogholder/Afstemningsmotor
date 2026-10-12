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

import logging
import re
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit.models import AuditLog
from app.config import get_settings
from app.db import ny_session
from app.personale.models import Staff

log = logging.getLogger(__name__)
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


# Forklaringer på de almindeligste fejl fra Microsoft (koderne står i Microsofts dokumentation).
KENDTE_KODER = {
    "AADSTS7000215": "forkert client secret – brug 'Value', ikke 'Secret ID'",
    "AADSTS7000222": "client secret er udløbet",
    "AADSTS50011": "redirect-adressen i Entra passer ikke",
    "AADSTS65001": "appen mangler samtykke",
    "AADSTS700016": "appen findes ikke i jeres Microsoft 365 (forkert client ID eller tenant)",
    "AADSTS50020": "kontoen hører ikke til jeres Microsoft 365",
}


def fejlkode(fejl: Exception, request: Request) -> str:
    """En kort, ufarlig fejlkode: OAuth-fejlen og evt. Microsofts AADSTS-nummer – aldrig selve beviserne."""
    if type(fejl).__name__ == "MismatchingStateError":
        return "login-sessionen passede ikke (cookie mistet eller for gammel)"
    tekst = " ".join(str(x) for x in (getattr(fejl, "error", None), getattr(fejl, "description", None),
                                      request.query_params.get("error"),
                                      request.query_params.get("error_description")) if x)
    aadsts = re.search(r"AADSTS\d+", tekst)
    oauth = re.search(r"\b(invalid_client|invalid_grant|access_denied|consent_required|"
                      r"interaction_required|invalid_request|unauthorized_client)\b", tekst)
    dele = [d for d in (oauth and oauth.group(1), aadsts and aadsts.group(0)) if d]
    if aadsts and aadsts.group(0) in KENDTE_KODER:
        dele.append(KENDTE_KODER[aadsts.group(0)])
    return ", ".join(dele) or type(fejl).__name__


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
    except Exception as fejl:  # noqa: BLE001 – kun fejlkoden vises; aldrig beviser eller nøgler
        kode = fejlkode(fejl, request)
        log.warning("Microsoft-login mislykkedes: %s", kode)
        session.add(AuditLog(handling="login_fejl", detaljer={"kode": kode}))
        session.commit()
        raise HTTPException(status_code=400, detail=f"Login mislykkedes ({kode}) – prøv igen fra /login") from None
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


@router.get("/dev-login")
def dev_login(email: str, request: Request, session: Session = Depends(db)):
    """Kun til udvikling lokalt (DEV_LOGIN=true og ikke production). Ellers findes siden ikke."""
    s = get_settings()
    if s.app_env == "production" or not s.dev_login:
        raise HTTPException(status_code=404)
    m = session.scalars(select(Staff).where(Staff.email == email.strip().lower(), Staff.aktiv)).one_or_none()
    if m is None:
        raise HTTPException(status_code=403, detail="Ingen aktiv medarbejder med den e-mail")
    request.session.clear()
    request.session[SESSION_NOEGLE] = m.id
    session.add(AuditLog(staff_id=m.id, handling="login_udvikling", detaljer={"rolle": m.rolle}))
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

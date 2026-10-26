"""Kundekortet under /api/kundekort (login krævet) – Kundeoversigten, trin 3: hovedet og fanen Kontaktpersoner.
De øvrige faner kommer i trin 5. Logikken ligger i app/kunder/kontakter.py. Ændringer kræver headeren X-Afstemning.

Alle medarbejdere ser kortet og kontaktpersonerne. Kun kundens ansvarlige, assistent og administratorer kan rette.

- `GET  /api/kundekort/{id}`                         kundens hoved (navn, CVR, status, ansvarlig, assistent, kan_rette)
- `GET  /api/kundekort/{id}/kontakter?arkiverede=`   kontaktpersonerne
- `POST /api/kundekort/{id}/kontakter`               opret
- `POST /api/kundekort/kontakter/{kid}`              ret
- `POST /api/kundekort/kontakter/{kid}/primaer`      gør til primær
- `POST /api/kundekort/kontakter/{kid}/arkiver`      arkivér (slettes aldrig)
- `POST /api/kundekort/{id}/raadgiver`               tilknyt revisor/bankrådgiver fra registret (eller opret ny)
- `GET  /api/raadgivere?type=revisor|bank&q=`        rådgiverregistret (alle ser)
- `POST /api/raadgivere/{rid}`                       ret en rådgiver – KUN admin (gælder alle kunder med rådgiveren)
- `GET  /api/kundekort/{id}/aftale`                  aftalegrundlaget (gældende + tidligere versioner)
- `POST /api/kundekort/{id}/aftale`                  upload PDF (filen sendes direkte; navnet i headeren X-Filnavn)
- `GET  /api/kundekort/{id}/aftale/{did}/pdf`        åbn PDF'en
- `GET  /api/kundekort/{id}/stamdata`                navn, CVR, virksomhedsform (CVR) + regnskabsperiode (regnskabssystemet)
"""

from datetime import datetime
from urllib.parse import quote, unquote

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.data import kraev_header
from app.api.login import db, kraev_admin, nuvaerende_medarbejder
from app.kunder import aftale, kartotek, kontakter, stamdata
from app.kunder.models import Client
from app.kunder.tabeller import KundeAftaleDokument
from app.personale.models import Staff
from app.tid import TIDSZONE

router = APIRouter(prefix="/api/kundekort", dependencies=[Depends(nuvaerende_medarbejder)])
raadgiver_router = APIRouter(prefix="/api/raadgivere", dependencies=[Depends(nuvaerende_medarbejder)])
IKKE_TILLADT = "Kun kundens ansvarlige, assistent eller en administrator kan rette kontaktpersoner"


def _kunde(session: Session, kunde_id: int) -> Client:
    k = session.get(Client, kunde_id)
    if k is None:
        raise HTTPException(status_code=404, detail="Kunden findes ikke")
    return k


@router.get("/{kunde_id}")
def hoved(kunde_id: int, m: Staff = Depends(nuvaerende_medarbejder), session: Session = Depends(db)) -> dict:
    k = _kunde(session, kunde_id)
    ansvarlig = session.get(Staff, k.ansvarlig_medarbejder_id) if k.ansvarlig_medarbejder_id else None
    assistent = session.get(Staff, k.assistent_id) if k.assistent_id else None
    return {"id": k.id, "navn": k.navn, "kundenummer": k.kundenummer, "cvr": k.cvr,
            "status": kartotek.STATUS_TEKST[k.status], "status_kode": k.status,
            "ansvarlig": ansvarlig.navn if ansvarlig else None, "ansvarlig_id": k.ansvarlig_medarbejder_id,
            "assistent": assistent.navn if assistent else None, "assistent_id": k.assistent_id,
            "kan_rette": kontakter.maa_rette(k, m), "admin": m.rolle == "admin",
            "mangler_aftale": aftale.mangler_aftale(session, k, datetime.now(TIDSZONE).date())}


@router.get("/{kunde_id}/kontakter")
def kontaktliste(kunde_id: int, arkiverede: bool = False, session: Session = Depends(db)) -> list[dict]:
    _kunde(session, kunde_id)
    return [kontakter.json(c) for c in kontakter.liste(session, kunde_id, med_arkiverede=arkiverede)]


class Kontakt(BaseModel):
    navn: str = Field(max_length=255)
    rolle: str | None = Field(default=None, max_length=100)
    telefon: str | None = Field(default=None, max_length=50)
    email: str | None = Field(default=None, max_length=255)
    underskriver: bool = False
    maa_kontaktes_af: str = "alle"


class NyKontakt(Kontakt):
    primaer: bool = False


def _udfoer(session: Session, handling):
    try:
        with session.begin_nested():
            resultat = handling()
    except kontakter.IkkeTilladt:
        raise HTTPException(status_code=403, detail=IKKE_TILLADT) from None
    except kontakter.KontaktFejl as fejl:
        raise HTTPException(status_code=422, detail=str(fejl)) from None
    session.commit()
    return kontakter.json(resultat)


@router.post("/{kunde_id}/kontakter", dependencies=[Depends(kraev_header)])
def opret(kunde_id: int, data: NyKontakt, m: Staff = Depends(nuvaerende_medarbejder),
          session: Session = Depends(db)) -> dict:
    return _udfoer(session, lambda: kontakter.opret(session, m, kunde_id, data.navn, data.rolle, data.telefon,
                                                    data.email, data.primaer, data.underskriver,
                                                    data.maa_kontaktes_af))


@router.post("/kontakter/{kontakt_id}", dependencies=[Depends(kraev_header)])
def ret(kontakt_id: int, data: Kontakt, m: Staff = Depends(nuvaerende_medarbejder),
        session: Session = Depends(db)) -> dict:
    return _udfoer(session, lambda: kontakter.ret(session, m, kontakt_id, data.navn, data.rolle, data.telefon,
                                                  data.email, data.underskriver, data.maa_kontaktes_af))


@router.post("/kontakter/{kontakt_id}/primaer", dependencies=[Depends(kraev_header)])
def primaer(kontakt_id: int, m: Staff = Depends(nuvaerende_medarbejder), session: Session = Depends(db)) -> dict:
    return _udfoer(session, lambda: kontakter.saet_primaer(session, m, kontakt_id))


@router.post("/kontakter/{kontakt_id}/arkiver", dependencies=[Depends(kraev_header)])
def arkiver(kontakt_id: int, m: Staff = Depends(nuvaerende_medarbejder), session: Session = Depends(db)) -> dict:
    return _udfoer(session, lambda: kontakter.arkiver(session, m, kontakt_id))


@router.get("/{kunde_id}/stamdata")
def stamdata_vis(kunde_id: int, session: Session = Depends(db)) -> dict:
    return stamdata.json(session, _kunde(session, kunde_id), datetime.now(TIDSZONE).date())


# --- Rådgiverregistret: revisorer og bankrådgivere (bygherren 26.10.2026) -----------------------------------------

class NyRaadgiver(BaseModel):
    navn: str = Field(max_length=255)
    firma: str = Field(max_length=255)
    telefon: str | None = Field(default=None, max_length=50)
    email: str | None = Field(default=None, max_length=255)


class Tilknytning(BaseModel):
    type: str                                   # revisor | bank
    raadgiver_id: int | None = None             # en, der allerede er i registret ...
    ny: NyRaadgiver | None = None               # ... eller en ny, der oprettes i registret
    maa_kontaktes_af: str = "alle"


@router.post("/{kunde_id}/raadgiver", dependencies=[Depends(kraev_header)])
def tilknyt(kunde_id: int, data: Tilknytning, m: Staff = Depends(nuvaerende_medarbejder),
            session: Session = Depends(db)) -> dict:
    return _udfoer(session, lambda: kontakter.tilknyt_raadgiver(
        session, m, kunde_id, data.type, data.raadgiver_id, data.ny.model_dump() if data.ny else None,
        data.maa_kontaktes_af))


@raadgiver_router.get("")
def raadgiverliste(type: str, q: str | None = None, session: Session = Depends(db)) -> list[dict]:
    return [kontakter.raadgiver_json(r) for r in kontakter.raadgivere(session, type, q)]


@raadgiver_router.post("/{raadgiver_id}", dependencies=[Depends(kraev_header)])
def ret_raadgiver(raadgiver_id: int, data: NyRaadgiver, m: Staff = Depends(kraev_admin),
                  session: Session = Depends(db)) -> dict:
    try:
        with session.begin_nested():
            r = kontakter.ret_raadgiver(session, m, raadgiver_id, data.navn, data.firma, data.telefon, data.email)
    except kontakter.IkkeTilladt:
        raise HTTPException(status_code=403, detail="Kun administratorer kan rette en rådgiver") from None
    except kontakter.KontaktFejl as fejl:
        raise HTTPException(status_code=422, detail=str(fejl)) from None
    session.commit()
    return kontakter.raadgiver_json(r)


# --- Aftalegrundlaget (PDF) – bygherren 26.10.2026 --------------------------------------------------------------

@router.get("/{kunde_id}/aftale")
def aftale_vis(kunde_id: int, m: Staff = Depends(nuvaerende_medarbejder), session: Session = Depends(db)) -> dict:
    return aftale.oversigt(session, _kunde(session, kunde_id), m, datetime.now(TIDSZONE).date())


@router.post("/{kunde_id}/aftale", dependencies=[Depends(kraev_header)])
async def aftale_upload(kunde_id: int, request: Request, x_filnavn: str = Header(default="aftalegrundlag.pdf"),
                        m: Staff = Depends(nuvaerende_medarbejder), session: Session = Depends(db)) -> dict:
    """PDF'en sendes som selve indholdet (Content-Type: application/pdf) – så kræves intet ekstra bibliotek."""
    indhold = await request.body()
    try:
        with session.begin_nested():
            d = aftale.upload(session, m, kunde_id, unquote(x_filnavn), indhold)
    except aftale.IkkeTilladt:
        raise HTTPException(status_code=403, detail="Kun kundens ansvarlige, assistent eller en administrator kan "
                                                    "uploade aftalegrundlaget") from None
    except aftale.AftaleFejl as fejl:
        raise HTTPException(status_code=422, detail=str(fejl)) from None
    session.commit()
    return aftale.json(session, d)


@router.get("/{kunde_id}/aftale/{dokument_id}/pdf")
def aftale_pdf(kunde_id: int, dokument_id: int, session: Session = Depends(db)) -> Response:
    d = session.get(KundeAftaleDokument, dokument_id)
    if d is None or d.client_id != kunde_id:
        raise HTTPException(status_code=404, detail="Dokumentet findes ikke")
    return Response(content=d.indhold, media_type="application/pdf", headers={
        "Content-Disposition": f"inline; filename*=UTF-8''{quote(d.filnavn)}", "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff"})

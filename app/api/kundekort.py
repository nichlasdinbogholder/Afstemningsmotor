"""Kundekortet under /api/kundekort (login krævet) – Kundeoversigten, trin 3: hovedet og fanen Kontaktpersoner.
De øvrige faner kommer i trin 5. Logikken ligger i app/kunder/kontakter.py. Ændringer kræver headeren X-Afstemning.

Alle medarbejdere ser kortet og kontaktpersonerne. Kun kundens ansvarlige, assistent og administratorer kan rette.

- `GET  /api/kundekort/{id}`                         kundens hoved (navn, CVR, status, ansvarlig, assistent, kan_rette)
- `GET  /api/kundekort/{id}/kontakter?arkiverede=`   kontaktpersonerne
- `POST /api/kundekort/{id}/kontakter`               opret
- `POST /api/kundekort/kontakter/{kid}`              ret
- `POST /api/kundekort/kontakter/{kid}/primaer`      gør til primær
- `POST /api/kundekort/kontakter/{kid}/arkiver`      arkivér (slettes aldrig)
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.data import kraev_header
from app.api.login import db, nuvaerende_medarbejder
from app.kunder import kontakter
from app.kunder.models import Client
from app.personale.models import Staff

router = APIRouter(prefix="/api/kundekort", dependencies=[Depends(nuvaerende_medarbejder)])
STATUS = {"emne": "Emne", "aktiv": "Aktiv", "pause": "Pause", "opsagt": "Arkiveret"}
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
    return {"id": k.id, "navn": k.navn, "kundenummer": k.kundenummer, "cvr": k.cvr, "status": STATUS[k.status],
            "ansvarlig": ansvarlig.navn if ansvarlig else None, "assistent": assistent.navn if assistent else None,
            "kan_rette": kontakter.maa_rette(k, m)}


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

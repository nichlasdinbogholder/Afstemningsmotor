"""Svends videnbase under /api/v1/kb (login krævet).

- Alle medarbejdere kan LÆSE dokumenterne. Kun administratorer kan oprette og rette (beslutning 16.10.2026) –
  det, der står her, bliver Svends svar til alle.
- Dokumenter slettes aldrig (historik). Et dokument sættes ud af brug med en slutdato (gyldig til) eller som
  kladde. 'superseded' sættes kun af godkendelsen af en ny sats (fase 3).
- En sats skal have værdi og enhed (også en regel i databasen). Når en administrator selv lægger en sats ind
  eller ændrer dens værdi, er den bekræftet af et menneske: `last_verified_at` sættes til nu.
- Hver oprettelse og ændring logges i audit_log med fra/til.
"""

import re
from datetime import date, datetime, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.data import kraev_header
from app.api.login import db, kraev_admin, nuvaerende_medarbejder
from app.assistent import satskontrol as sk
from app.assistent.models import KATEGORIER, KbDocument, KbRateCheck
from app.audit.models import AuditLog
from app.personale.models import Staff

router = APIRouter(prefix="/api/v1/kb", dependencies=[Depends(nuvaerende_medarbejder)])

REDIGERBARE_STATUSSER = ("active", "draft")
URL = re.compile(r"^https?://\S+$")
FELTER = ("title", "category", "body", "value", "unit", "source_url", "valid_from", "valid_to", "status",
          "verify_source_url", "verify_hint")


def _json(d: KbDocument, med_tekst: bool = True) -> dict:
    data = {"id": d.id, "titel": d.title, "kategori": d.category, "status": d.status,
            "vaerdi": None if d.value is None else str(d.value), "enhed": d.unit, "kilde": d.source_url,
            "gyldig_fra": d.valid_from.isoformat() if d.valid_from else None,
            "gyldig_til": d.valid_to.isoformat() if d.valid_to else None,
            "senest_bekraeftet": d.last_verified_at.isoformat() if d.last_verified_at else None,
            "kontrol_kilde": d.verify_source_url, "kontrol_hint": d.verify_hint,
            "afloeser": d.replaces_id, "rettet_af": d.updated_by, "rettet": d.updated_at.isoformat()}
    if med_tekst:
        data["tekst"] = d.body
    return data


def _som_tekst(v) -> str | None:
    if v is None:
        return None
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    return str(v)


class Dokument(BaseModel):
    titel: str = Field(min_length=1, max_length=300)
    kategori: str
    tekst: str = Field(min_length=1, max_length=100_000)
    status: str = "active"
    kilde: str | None = Field(default=None, max_length=1000)
    gyldig_fra: date | None = None
    gyldig_til: date | None = None
    vaerdi: Decimal | None = Field(default=None, max_digits=15, decimal_places=4)
    enhed: str | None = Field(default=None, max_length=30)
    kontrol_kilde: str | None = Field(default=None, max_length=1000)
    kontrol_hint: str | None = Field(default=None, max_length=2000)


def _tjek(data: Dokument) -> None:
    if data.kategori not in KATEGORIER:
        raise HTTPException(status_code=422, detail="Ukendt kategori")
    if data.status not in REDIGERBARE_STATUSSER:
        raise HTTPException(status_code=422, detail="Status skal være aktiv eller kladde")
    for felt, url in (("Kildelinket", data.kilde), ("Kontrolsiden", data.kontrol_kilde)):
        if url and not URL.match(url.strip()):
            raise HTTPException(status_code=422, detail=f"{felt} skal starte med http:// eller https://")
    if data.gyldig_fra and data.gyldig_til and data.gyldig_til < data.gyldig_fra:
        raise HTTPException(status_code=422, detail="'Gyldig til' ligger før 'gyldig fra'")
    if data.kategori == "satser" and (data.vaerdi is None or not (data.enhed or "").strip()):
        raise HTTPException(status_code=422, detail="En sats skal have både værdi og enhed")


def _udfyld(d: KbDocument, data: Dokument, af: Staff) -> None:
    sats = data.kategori == "satser"
    ny_vaerdi = data.vaerdi if sats else None
    if sats and (d.value is None or ny_vaerdi != d.value or d.last_verified_at is None):
        d.last_verified_at = datetime.now(timezone.utc)  # bekræftet af administratoren selv
    d.title = data.titel.strip()
    d.category = data.kategori
    d.body = data.tekst
    d.status = data.status
    d.source_url = (data.kilde or "").strip() or None
    d.valid_from = data.gyldig_fra
    d.valid_to = data.gyldig_til
    d.value = ny_vaerdi
    d.unit = ((data.enhed or "").strip() or None) if sats else None
    d.verify_source_url = ((data.kontrol_kilde or "").strip() or None) if sats else None
    d.verify_hint = ((data.kontrol_hint or "").strip() or None) if sats else None
    d.updated_by = af.email
    d.updated_at = datetime.now(timezone.utc)


def _foer(d: KbDocument) -> dict:
    return {f: _som_tekst(getattr(d, f)) for f in FELTER}


@router.get("/documents")
def liste(kategori: str | None = None, session: Session = Depends(db)) -> list[dict]:
    q = select(KbDocument).order_by(KbDocument.category, KbDocument.title, KbDocument.id)
    if kategori:
        q = q.where(KbDocument.category == kategori)
    return [_json(d, med_tekst=False) for d in session.scalars(q)]


@router.get("/documents/{dok_id}")
def et(dok_id: int, session: Session = Depends(db)) -> dict:
    d = session.get(KbDocument, dok_id)
    if d is None:
        raise HTTPException(status_code=404, detail="Dokumentet findes ikke")
    return _json(d)


@router.post("/documents", dependencies=[Depends(kraev_header)])
def opret(data: Dokument, af: Staff = Depends(kraev_admin), session: Session = Depends(db)) -> dict:
    _tjek(data)
    d = KbDocument()
    _udfyld(d, data, af)
    session.add(d)
    session.flush()
    session.add(AuditLog(staff_id=af.id, handling="kb_oprettet",
                         detaljer={"dokument_id": d.id, "til": _foer(d), "udfoert_af": af.email}))
    session.commit()
    return _json(d)


@router.post("/documents/{dok_id}", dependencies=[Depends(kraev_header)])
def ret(dok_id: int, data: Dokument, af: Staff = Depends(kraev_admin), session: Session = Depends(db)) -> dict:
    d = session.get(KbDocument, dok_id)
    if d is None:
        raise HTTPException(status_code=404, detail="Dokumentet findes ikke")
    if d.status == "superseded":
        raise HTTPException(status_code=409, detail="Dokumentet er afløst af et nyere og kan ikke rettes")
    _tjek(data)
    foer = _foer(d)
    _udfyld(d, data, af)
    efter = _foer(d)
    aendret = {f: {"fra": foer[f], "til": efter[f]} for f in FELTER if foer[f] != efter[f]}
    if aendret:
        session.add(AuditLog(staff_id=af.id, handling="kb_aendret",
                             detaljer={"dokument_id": d.id, "aendringer": aendret, "udfoert_af": af.email}))
    session.commit()
    return _json(d)


# --- Satskontrollen (fase 3): fund, godkendelse og afvisning – KUN administratorer ---------------------------

def _kontrol_json(session: Session, k: KbRateCheck) -> dict:
    dok = session.get(KbDocument, k.kb_id)
    udkast = session.get(KbDocument, k.draft_id) if k.draft_id else None
    afgjort_af = session.get(Staff, k.reviewed_by) if k.reviewed_by else None
    return {"id": k.id, "tidspunkt": k.checked_at.isoformat(), "udfald": k.outcome, "metode": k.method,
            "dokument": {"id": dok.id, "titel": dok.title, "enhed": dok.unit, "status": dok.status,
                         "kontrol_kilde": dok.verify_source_url, "kontrol_hint": dok.verify_hint,
                         "gyldig_fra": dok.valid_from.isoformat() if dok.valid_from else None},
            "gammel_vaerdi": _som_tekst(k.previous_value), "ny_vaerdi": _som_tekst(k.fetched_value),
            "uddrag": k.raw_excerpt, "fejl": k.error, "udkast_id": udkast.id if udkast else None,
            "afgoerelse": k.decision, "afgjort_af": afgjort_af.navn if afgjort_af else None,
            "afgjort": k.reviewed_at.isoformat() if k.reviewed_at else None, "note": k.review_note}


@router.get("/kontroller", dependencies=[Depends(kraev_admin)])
def kontroller(aabne: bool = True, session: Session = Depends(db)) -> list[dict]:
    q = select(KbRateCheck).order_by(KbRateCheck.checked_at.desc(), KbRateCheck.id.desc()).limit(200)
    if aabne:
        q = q.where(KbRateCheck.outcome.in_(("changed", "failed")), KbRateCheck.decision.is_(None))
    return [_kontrol_json(session, k) for k in session.scalars(q)]


@router.post("/kontroller/koer", dependencies=[Depends(kraev_header)])
def koer_nu(af: Staff = Depends(kraev_admin), session: Session = Depends(db)) -> dict:
    """Læg satskontrollen forrest i køen nu (ud over den faste kørsel om mandagen)."""
    from app.assistent.jobs import SATSKONTROL
    from app.jobs.koe import laeg_i_koe

    nu = datetime.now(timezone.utc)
    r = laeg_i_koe(session, SATSKONTROL, payload={"dag": nu.date().isoformat(), "manuel": True},
                   idempotens_noegle=f"{SATSKONTROL}:manuel:{nu.strftime('%Y-%m-%dT%H:%M')}", prioritet=5)
    session.add(AuditLog(staff_id=af.id, handling="satskontrol_startet", detaljer={"udfoert_af": af.email}))
    session.commit()
    return {"job_id": r.job_id}


class Godkendelse(BaseModel):
    gyldig_fra: date
    note: str | None = Field(default=None, max_length=2000)


class Afvisning(BaseModel):
    note: str = Field(min_length=1, max_length=2000)


def _hent_kontrol(session: Session, kontrol_id: int) -> KbRateCheck:
    k = session.get(KbRateCheck, kontrol_id)
    if k is None:
        raise HTTPException(status_code=404, detail="Kontrollen findes ikke")
    return k


@router.post("/kontroller/{kontrol_id}/godkend", dependencies=[Depends(kraev_header)])
def godkend_ny_sats(kontrol_id: int, data: Godkendelse, af: Staff = Depends(kraev_admin),
                    session: Session = Depends(db)) -> dict:
    k = _hent_kontrol(session, kontrol_id)
    try:
        ny = sk.godkend(session, k, af.id, data.gyldig_fra, data.note)
    except sk.AfgoerelsesFejl as fejl:
        raise HTTPException(status_code=409, detail=str(fejl))
    session.add(AuditLog(staff_id=af.id, handling="kb_sats_godkendt", detaljer={
        "kontrol_id": k.id, "gammelt_dokument": k.kb_id, "nyt_dokument": ny.id, "fra": _som_tekst(k.previous_value),
        "til": _som_tekst(k.fetched_value), "gyldig_fra": data.gyldig_fra.isoformat(), "udfoert_af": af.email}))
    session.commit()
    return _kontrol_json(session, k)


@router.post("/kontroller/{kontrol_id}/afvis", dependencies=[Depends(kraev_header)])
def afvis_kontrol(kontrol_id: int, data: Afvisning, af: Staff = Depends(kraev_admin),
                  session: Session = Depends(db)) -> dict:
    k = _hent_kontrol(session, kontrol_id)
    try:
        sk.afvis(session, k, af.id, data.note)
    except sk.AfgoerelsesFejl as fejl:
        raise HTTPException(status_code=409, detail=str(fejl))
    session.add(AuditLog(staff_id=af.id, handling="kb_sats_afvist" if k.outcome == "changed" else "kb_satsfejl_kvitteret",
                         detaljer={"kontrol_id": k.id, "dokument": k.kb_id, "note": data.note.strip(),
                                   "udfoert_af": af.email}))
    session.commit()
    return _kontrol_json(session, k)

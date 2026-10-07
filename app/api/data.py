"""Data til webdelen (Next.js): kunder, fund og kontoudtog. Alt kræver login.

Siderne læser KUN vores egen database – aldrig e-conomic direkte. Det er det, der gør dem
hurtige, også med mange kunder og medarbejdere: data hentes fra e-conomic i baggrunden
(natkørslen og "Opdater nu", der lægger et job forrest i køen).

Ændringer (status på fund) kræver headeren `X-Afstemning: 1`. En anden hjemmeside kan ikke
sende den uden browserens tilladelse (CORS er ikke slået til), så et link eller en formular
udefra kan ikke ændre noget i en medarbejders navn.
"""

from datetime import datetime, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.api.login import db, nuvaerende_medarbejder
from app.jobs.koe import laeg_i_koe
from app.jobs.models import Job
from app.kontoudtog.models import Statement
from app.kunder.models import Client
from app.natkoersel.job import OPDATER_JOBTYPE, OPDATER_PRIORITET
from app.personale.models import Staff
from app.rules.models import STATUSSER, Finding, FindingEvent
from app.rules.status import UgyldigStatus, saet_status
from app.rules.visning import fund_med_aktuel, regelnavne, sorteringsnoegle

router = APIRouter(prefix="/api", dependencies=[Depends(nuvaerende_medarbejder)])

MAKS_FUND = 500  # pr. side


def kraev_header(x_afstemning: str | None = Header(default=None)) -> None:
    if x_afstemning != "1":
        raise HTTPException(status_code=403, detail="Mangler X-Afstemning-header")


def _kr(b: Decimal | None) -> str | None:
    return None if b is None else str(b)


@router.get("/mig")
def mig(m: Staff = Depends(nuvaerende_medarbejder)) -> dict:
    return {"id": m.id, "navn": m.navn, "email": m.email, "rolle": m.rolle}


KUNDER_SQL = text("""
WITH aabne AS (
    -- aktuelle, åbne fund: ingen kørsel af reglen siden fundet sidst blev set
    SELECT f.client_id, f.severity
    FROM findings f
    WHERE f.status = 'open'
      AND NOT EXISTS (SELECT 1 FROM rule_runs r
                      WHERE r.client_id = f.client_id AND r.rule_code = f.rule_code
                        AND r.koert_at > f.last_seen_at)
)
SELECT c.id, c.navn, c.kundenummer, c.cvr, c.status, c.regnskabssystem,
       count(a.severity) FILTER (WHERE a.severity = 'high')   AS hoej,
       count(a.severity) FILTER (WHERE a.severity = 'medium') AS mellem,
       count(a.severity) FILTER (WHERE a.severity = 'low')    AS lav,
       nat.tidspunkt AS nat_tidspunkt, nat.status AS nat_status
FROM clients c
LEFT JOIN aabne a ON a.client_id = c.id
LEFT JOIN LATERAL (
    SELECT tidspunkt, detaljer->>'status' AS status FROM audit_log
    WHERE client_id = c.id AND handling IN ('natkoersel_kunde', 'opdater_kunde')
    ORDER BY tidspunkt DESC LIMIT 1
) nat ON TRUE
WHERE (CAST(:status AS varchar) IS NULL OR c.status = :status)
GROUP BY c.id, nat.tidspunkt, nat.status
ORDER BY c.navn
""")


@router.get("/kunder")
def kunder(status: str | None = "aktiv", session: Session = Depends(db)) -> list[dict]:
    return [{"id": r.id, "navn": r.navn, "kundenummer": r.kundenummer, "cvr": r.cvr, "status": r.status,
             "system": r.regnskabssystem,
             "aabne_fund": {"high": r.hoej, "medium": r.mellem, "low": r.lav},
             "natkoersel": ({"tidspunkt": r.nat_tidspunkt.isoformat(), "status": r.nat_status}
                            if r.nat_tidspunkt else None)}
            for r in session.execute(KUNDER_SQL, {"status": status or None})]


def _kunde(session: Session, client_id: int) -> Client:
    kunde = session.get(Client, client_id)
    if kunde is None:
        raise HTTPException(status_code=404, detail="Kunden findes ikke")
    return kunde


@router.get("/kunder/{client_id}")
def kunde(client_id: int, session: Session = Depends(db)) -> dict:
    k = _kunde(session, client_id)
    seneste = session.execute(text("""
        SELECT tidspunkt, handling, detaljer->>'status' AS status FROM audit_log
        WHERE client_id = :c AND handling IN ('natkoersel_kunde', 'opdater_kunde')
        ORDER BY tidspunkt DESC LIMIT 1"""), {"c": client_id}).first()
    return {"id": k.id, "navn": k.navn, "kundenummer": k.kundenummer, "cvr": k.cvr, "status": k.status,
            "system": k.regnskabssystem,
            "kontoudtog_fra": k.kontoudtog_fra.isoformat() if k.kontoudtog_fra else None,
            "sidst_opdateret": ({"tidspunkt": seneste.tidspunkt.isoformat(), "status": seneste.status,
                                 "hvordan": "natkørsel" if seneste.handling == "natkoersel_kunde" else "opdater nu"}
                                if seneste else None)}


def _fund_json(f: Finding, aktuel: bool, navne: dict[str, str]) -> dict:
    return {"id": f.id, "regel": f.rule_code, "regel_navn": navne.get(f.rule_code, f.rule_code),
            "alvor": f.severity, "status": f.status, "titel": f.title, "aktuel": aktuel,
            "periode_fra": f.period_start.isoformat() if f.period_start else None,
            "periode_til": f.period_end.isoformat() if f.period_end else None,
            "foerst_set": f.first_seen_at.isoformat(), "sidst_set": f.last_seen_at.isoformat()}


@router.get("/kunder/{client_id}/fund")
def fund(client_id: int, status: str | None = Query(default="open"), regel: str | None = None,
         alle: bool = False, session: Session = Depends(db)) -> dict:
    _kunde(session, client_id)
    stmt = fund_med_aktuel(client_id)
    if status:
        stmt = stmt.where(Finding.status == status)
    if regel:
        stmt = stmt.where(Finding.rule_code == regel)
    raekker = [(f, a) for f, a in session.execute(stmt) if alle or a]
    raekker.sort(key=lambda x: sorteringsnoegle(x[0]))
    navne = regelnavne()
    return {"antal": len(raekker), "fund": [_fund_json(f, a, navne) for f, a in raekker[:MAKS_FUND]],
            "afkortet": len(raekker) > MAKS_FUND}


@router.get("/fund/{finding_id}")
def et_fund(finding_id: int, session: Session = Depends(db)) -> dict:
    f = session.get(Finding, finding_id)
    if f is None:
        raise HTTPException(status_code=404, detail="Fundet findes ikke")
    aktuel = next(a for ff, a in session.execute(fund_med_aktuel(f.client_id).where(Finding.id == f.id)))
    historik = session.query(FindingEvent).filter(FindingEvent.finding_id == f.id).order_by(FindingEvent.id).all()
    return {**_fund_json(f, aktuel, regelnavne()), "client_id": f.client_id, "detaljer": f.detail,
            "historik": [{"fra": h.from_status, "til": h.to_status, "af": h.actor, "note": h.note,
                          "tidspunkt": h.created_at.isoformat()} for h in historik]}


class NyStatus(BaseModel):
    status: str
    note: str | None = Field(default=None, max_length=2000)


@router.post("/fund/{finding_id}/status", dependencies=[Depends(kraev_header)])
def ny_status(finding_id: int, data: NyStatus, m: Staff = Depends(nuvaerende_medarbejder),
              session: Session = Depends(db)) -> dict:
    if data.status not in STATUSSER:
        raise HTTPException(status_code=422, detail=f"Ukendt status – brug {', '.join(STATUSSER)}")
    if data.status in ("accepted", "ignored") and not (data.note or "").strip():
        raise HTTPException(status_code=422, detail="Skriv en note, når et fund godkendes eller ignoreres")
    try:
        skift = saet_status(session, finding_id, data.status, actor=m.email, note=data.note)
    except UgyldigStatus as fejl:
        raise HTTPException(status_code=404 if "findes ikke" in str(fejl) else 422, detail=str(fejl)) from None
    session.commit()
    return {"id": finding_id, "fra": skift.fra, "til": skift.til}


@router.post("/kunder/{client_id}/opdater", dependencies=[Depends(kraev_header)])
def opdater(client_id: int, m: Staff = Depends(nuvaerende_medarbejder), session: Session = Depends(db)) -> dict:
    """Hent frisk data fra kundens regnskabssystem og kør reglerne – forrest i køen.
    Ligger der allerede en opdatering i kø eller i gang, genbruges den."""
    k = _kunde(session, client_id)
    if k.status != "aktiv":
        raise HTTPException(status_code=409, detail="Kun aktive kunder kan opdateres")
    igang = session.scalar(select(Job).where(Job.client_id == client_id, Job.type == OPDATER_JOBTYPE,
                                             Job.status.in_(("koe", "i_gang"))).order_by(Job.id))
    if igang is not None:
        return {"job_id": igang.id, "status": igang.status, "ny": False}
    r = laeg_i_koe(session, OPDATER_JOBTYPE, client_id=client_id, payload={"bestilt_af": m.email},
                   idempotens_noegle=f"opdater:{client_id}:{datetime.now(timezone.utc).isoformat()}",
                   prioritet=OPDATER_PRIORITET, max_forsoeg=2)
    session.commit()
    return {"job_id": r.job_id, "status": "koe", "ny": True}


@router.get("/jobs/{job_id}")
def job_status(job_id: int, session: Session = Depends(db)) -> dict:
    j = session.get(Job, job_id)
    if j is None:
        raise HTTPException(status_code=404, detail="Jobbet findes ikke")
    # Kun status og tider – fejltekster kan indeholde tekniske detaljer og vises ikke her.
    return {"id": j.id, "type": j.type, "client_id": j.client_id, "status": j.status,
            "oprettet": j.oprettet.isoformat(), "paabegyndt": j.paabegyndt.isoformat() if j.paabegyndt else None,
            "afsluttet": j.afsluttet.isoformat() if j.afsluttet else None, "forsoeg": j.forsoeg}


@router.get("/kunder/{client_id}/kontoudtog")
def kontoudtog(client_id: int, session: Session = Depends(db)) -> list[dict]:
    _kunde(session, client_id)
    raekker = session.execute(text("""
        SELECT s.id, s.kilde, s.kontonummer, s.modpart, s.periode_fra, s.periode_til, s.kildefil, s.oprettet,
               count(l.id) AS linjer, count(l.match_entry_id) AS matchet, sum(l.beloeb) AS sum_linjer,
               (SELECT navn FROM suppliers su WHERE su.client_id = s.client_id
                  AND s.modpart = 'kreditor:' || su.leverandoernummer) AS modpart_navn
        FROM statements s LEFT JOIN statement_lines l ON l.statement_id = s.id
        WHERE s.client_id = :c
        GROUP BY s.id ORDER BY s.periode_til DESC, s.id DESC
    """), {"c": client_id})
    return [{"id": r.id, "kilde": r.kilde, "kontonummer": r.kontonummer, "modpart": r.modpart,
             "modpart_navn": r.modpart_navn, "periode_fra": r.periode_fra.isoformat(),
             "periode_til": r.periode_til.isoformat(), "kildefil": r.kildefil,
             "indlaest": r.oprettet.isoformat(), "linjer": r.linjer, "matchet": r.matchet,
             "matchprocent": round(100 * r.matchet / r.linjer, 1) if r.linjer else None,
             "sum_linjer": _kr(r.sum_linjer)} for r in raekker]


@router.get("/kontoudtog/{statement_id}")
def kontoudtog_linjer(statement_id: int, session: Session = Depends(db)) -> dict:
    s = session.get(Statement, statement_id)
    if s is None:
        raise HTTPException(status_code=404, detail="Kontoudtoget findes ikke")
    raekker = session.execute(text("""
        SELECT l.linje_nr, l.dato, l.reference, l.tekst, l.beloeb, l.match_trin,
               e.dato AS bogfoert_dato, e.bilagsnummer, e.beloeb AS bogfoert_beloeb
        FROM statement_lines l LEFT JOIN entries e ON e.id = l.match_entry_id
        WHERE l.statement_id = :s ORDER BY l.linje_nr
    """), {"s": statement_id})
    return {"id": s.id, "client_id": s.client_id, "kilde": s.kilde, "modpart": s.modpart,
            "periode_fra": s.periode_fra.isoformat(), "periode_til": s.periode_til.isoformat(),
            "linjer": [{"nr": r.linje_nr, "dato": r.dato.isoformat(), "reference": r.reference,
                        "tekst": r.tekst, "beloeb": _kr(r.beloeb), "match": r.match_trin,
                        "bogfoert": ({"dato": r.bogfoert_dato.isoformat() if r.bogfoert_dato else None,
                                      "bilag": r.bilagsnummer, "beloeb": _kr(r.bogfoert_beloeb)}
                                     if r.match_trin else None)} for r in raekker]}

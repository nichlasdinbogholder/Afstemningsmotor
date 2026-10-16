"""Svends faste job (APScheduler i natkørslens proces lægger dem i køen):

  mandag kl. 06.00   `assistent_satskontrol` – alle aktive satser efterses mod deres kildeside
  hver dag kl. 06.30 `assistent_foraeldelse` – satser, der ikke er bekræftet i 2 × ASSISTANT_RATE_STALE_DAYS, giver fund

Resultatet ligger i kb_rate_checks; ændringer og fejl venter på et menneske (siden Svend → Satskontrol).
"""

from datetime import datetime

from sqlalchemy.orm import Session

from app.assistent.satskontrol import kontroller_alle, tjek_foraeldelse
from app.audit.models import AuditLog
from app.jobs.koe import laeg_i_koe
from app.jobs.register import JobKontekst, jobtype
from app.tid import TIDSZONE

SATSKONTROL = "assistent_satskontrol"
FORAELDELSE = "assistent_foraeldelse"


def planlaeg(session: Session, type_: str, nu: datetime) -> int:
    """Ét job pr. type pr. dag, det planlægges (idempotent)."""
    dag = nu.astimezone(TIDSZONE).date()
    return laeg_i_koe(session, type_, payload={"dag": dag.isoformat()},
                      idempotens_noegle=f"{type_}:{dag.isoformat()}", prioritet=10).ny


def planlaeg_satskontrol(session: Session, nu: datetime) -> int:
    return planlaeg(session, SATSKONTROL, nu)


@jobtype(SATSKONTROL)
def satskontrol(job: JobKontekst) -> None:
    kontroller = kontroller_alle(job.session)
    job.session.add(AuditLog(handling="satskontrol", detaljer={
        "dag": job.payload.get("dag"),
        "uaendret": sum(k.outcome == "unchanged" for k in kontroller),
        "aendret": sum(k.outcome == "changed" for k in kontroller),
        "fejl": sum(k.outcome == "failed" for k in kontroller)}))


@jobtype(FORAELDELSE)
def foraeldelse(job: JobKontekst) -> None:
    nye = tjek_foraeldelse(job.session)
    if nye:
        job.session.add(AuditLog(handling="satser_foraeldet", detaljer={
            "dag": job.payload.get("dag"), "nye_fund": len(nye), "dokumenter": [k.kb_id for k in nye]}))

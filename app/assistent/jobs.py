"""Svends faste job (APScheduler i natkørslens proces lægger dem i køen):

  mandag kl. 06.00   `assistent_satskontrol` – alle aktive satser efterses mod deres kildeside

Resultatet ligger i kb_rate_checks; ændringer og fejl venter på et menneske (siden Svend → Satskontrol).
"""

from datetime import datetime

from sqlalchemy.orm import Session

from app.assistent.satskontrol import kontroller_alle
from app.audit.models import AuditLog
from app.jobs.koe import laeg_i_koe
from app.jobs.register import JobKontekst, jobtype
from app.tid import TIDSZONE

SATSKONTROL = "assistent_satskontrol"


def planlaeg_satskontrol(session: Session, nu: datetime) -> int:
    """Ét job pr. dag, det planlægges (idempotent)."""
    dag = nu.astimezone(TIDSZONE).date()
    return laeg_i_koe(session, SATSKONTROL, payload={"dag": dag.isoformat()},
                      idempotens_noegle=f"{SATSKONTROL}:{dag.isoformat()}", prioritet=10).ny


@jobtype(SATSKONTROL)
def satskontrol(job: JobKontekst) -> None:
    kontroller = kontroller_alle(job.session)
    job.session.add(AuditLog(handling="satskontrol", detaljer={
        "dag": job.payload.get("dag"),
        "uaendret": sum(k.outcome == "unchanged" for k in kontroller),
        "aendret": sum(k.outcome == "changed" for k in kontroller),
        "fejl": sum(k.outcome == "failed" for k in kontroller)}))

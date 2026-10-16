"""Jobtypen run_rules: kør alle aktive regler for én kunde.

Planlægges én gang i døgnet for hver aktiv kunde (kl. 23:30 dansk tid, efter
dagens synkronisering) af `planlaeg_regler`, som workeren kører med --planlaeg.
Idempotensnøgle "rules:<client_id>:<dato>".
"""

import logging
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.jobs.koe import laeg_i_koe
from app.jobs.register import JobKontekst, jobtype
from app.kunder.models import Client
from app.rules.koersel import koer_regler

log = logging.getLogger(__name__)

TIDSZONE = ZoneInfo("Europe/Copenhagen")
KLOKKEN = time(23, 30)


@jobtype("run_rules")
def run_rules(job: JobKontekst) -> None:
    if job.client_id is None:
        raise ValueError(f"Job {job.job_id} mangler client_id")
    r = koer_regler(job.session, job.client_id)
    log.info("Job %s: kunde %s – %s fund i alt", job.job_id, job.client_id,
             sum(x.fundet for x in r.regler))


def planlaeg_regler(session: Session, dag: date | None = None) -> int:
    """Læg run_rules i kø for alle aktive kunder. Returnerer antal nye job."""
    nu = session.scalar(select(func.now())).astimezone(TIDSZONE)
    dag = dag or nu.date()
    tidspunkt = max(datetime.combine(dag, KLOKKEN, TIDSZONE), nu)
    nye = 0
    for client_id in session.scalars(select(Client.id).where(Client.status == "aktiv").order_by(Client.id)):
        nye += laeg_i_koe(session, "run_rules", client_id=client_id, planlagt_til=tidspunkt,
                          idempotens_noegle=f"rules:{client_id}:{dag.isoformat()}").ny
    return nye

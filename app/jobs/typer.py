"""Jobtyper. Hver funktion registreres med @jobtype("navn")."""

import logging

from app.jobs.register import JobKontekst, jobtype

log = logging.getLogger(__name__)


@jobtype("log_klient")
def log_klient(job: JobKontekst) -> None:
    """Eksempel: skriver bare kundens id i loggen."""
    log.info("Job %s (log_klient): client_id=%s", job.job_id, job.client_id)

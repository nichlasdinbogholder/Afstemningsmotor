"""Jobtyper for synkronisering: én pr. ressource.

    synk_customers, synk_suppliers, synk_entries, synk_open_entries

Idempotensnøgle: "<ressource>:<client_id>:<tidspunkt>" (se planlaegger.py).
- For mange kald (rate limit): jobbet udskydes og prøves igen – det fejler ikke.
- Er kunden sat på pause/opsagt eller synkroniseringen slået fra, efter jobbet
  blev lagt i kø, springes det over (jobbet afsluttes uden at hente noget).
"""

import logging

from app.adaptere.regnskab.base import ForMangeKald
from app.jobs.register import JobKontekst, UdskydJob, jobtype
from app.synk.ressourcer import SYNK_FUNKTIONER
from app.synk.tilstand import KundeIkkeAktiv, SynkDeaktiveret, SynkIGang

log = logging.getLogger(__name__)

JOBTYPER = {f"synk_{ressource}": ressource for ressource in SYNK_FUNKTIONER}


def _koer(job: JobKontekst, ressource: str) -> None:
    if job.client_id is None:
        raise ValueError(f"Job {job.job_id} mangler client_id")
    try:
        resultat = SYNK_FUNKTIONER[ressource](job.session, job.client_id)
    except ForMangeKald as fejl:
        raise UdskydJob(fejl.vent_sekunder, str(fejl)) from None
    except SynkIGang as fejl:
        raise UdskydJob(120, str(fejl)) from None
    except (KundeIkkeAktiv, SynkDeaktiveret) as fejl:
        log.info("Job %s springes over: %s", job.job_id, fejl)
        return
    log.info("Job %s: kunde %s, %s – %s hentet%s", job.job_id, job.client_id, ressource,
             resultat.antal, f", {resultat.fjernet} fjernet" if resultat.fjernet else "")


def _lav_jobtype(navn: str, ressource: str) -> None:
    @jobtype(navn)
    def koer(job: JobKontekst) -> None:
        _koer(job, ressource)


for _navn, _ressource in JOBTYPER.items():
    _lav_jobtype(_navn, _ressource)

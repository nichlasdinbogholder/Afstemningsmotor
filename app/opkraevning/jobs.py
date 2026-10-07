"""Rykkernes faste tider (APScheduler i natkørslens proces lægger jobbene i køen):

  kl. 16.00 på bankdage  `rykker_koe`     – næste bankdags rykkere lægges i kø
  kl. 09.00 på bankdage  `rykker_kontrol` – alle spærrer kontrolleres igen lige før afsendelse

Kun kunder med rykkere i drift (LIVE_TILSTANDE) får job. 'live' kan først vælges, når udsendelsen
(trin 4) er bygget – indtil da lægges ingen rykkere i kø automatisk.
"""

from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit.models import AuditLog
from app.jobs.koe import laeg_i_koe
from app.jobs.register import JobKontekst, jobtype
from app.kunder.models import Client
from app.opkraevning.rykkerkoersel import kontroller_foer_afsendelse, laeg_i_koe as rykkere_i_koe
from app.tid import TIDSZONE, er_bankdag, naeste_bankdag

KOE = "rykker_koe"
KONTROL = "rykker_kontrol"
LIVE_TILSTANDE = ("live",)


def planlaeg_rykkerjob(session: Session, type_: str, nu: datetime) -> int:
    """Læg dagens rykkerjob i kø for kunder med rykkere i drift. Kun på bankdage. Idempotent."""
    dag = nu.astimezone(TIDSZONE).date()
    if not er_bankdag(dag):
        return 0
    maaldag = naeste_bankdag(dag) if type_ == KOE else dag
    nye = 0
    for client_id in session.scalars(select(Client.id).where(Client.status == "aktiv",
                                                             Client.dunning_mode.in_(LIVE_TILSTANDE))):
        nye += laeg_i_koe(session, type_, client_id=client_id, payload={"dag": maaldag.isoformat()},
                          idempotens_noegle=f"{type_}:{client_id}:{maaldag.isoformat()}", prioritet=20).ny
    return nye


@jobtype(KOE)
def rykker_koe(job: JobKontekst) -> None:
    dag = date.fromisoformat(job.payload["dag"])
    r = rykkere_i_koe(job.session, job.client_id, dag)
    job.session.add(AuditLog(client_id=job.client_id, handling="rykkere_lagt_i_koe",
                             detaljer={"afsendelsesdag": dag.isoformat(), "i_koe": len(r.lagt_i_koe),
                                       "sprunget_over": len(r.sprunget_over)}))


@jobtype(KONTROL)
def rykker_kontrol(job: JobKontekst) -> None:
    dag = date.fromisoformat(job.payload["dag"])
    r = kontroller_foer_afsendelse(job.session, job.client_id, dag)
    job.session.add(AuditLog(client_id=job.client_id, handling="rykkere_kontrolleret",
                             detaljer={"dag": dag.isoformat(), "klar": len(r.lagt_i_koe),
                                       "annulleret": len(r.sprunget_over)}))

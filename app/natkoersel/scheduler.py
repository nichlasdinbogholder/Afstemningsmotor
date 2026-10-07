"""Natkørslen: APScheduler i sin egen proces.

    python -m app.natkoersel.scheduler              # kører for evigt (serverens `scheduler`)
    python -m app.natkoersel.scheduler --koer-nu    # læg nattens job i kø nu – én gang

Kl. 05:00 (dansk tid) mandag–fredag lægges ét `natkoersel_kunde`-job i køen for hver
aktiv kunde med aktiv adgang. Jobbene SPREDES jævnt over SPREDNING (2 timer), så
regnskabssystemet ikke rammes af alle på én gang. Workerne udfører dem.

- Idempotent: nøglen "nat:<kunde>:<dato>" – genstartes scheduleren, eller køres
  --koer-nu to gange samme dag, kommer samme kunde ikke i kø to gange.
- Hver planlægning skrives i audit_log (handling `natkoersel_planlagt`).
- Kan én kunde ikke lægges i kø, fortsætter de øvrige.
- Var serveren slukket kl. 05:00, køres planlægningen, når den starter igen, hvis det
  er højst 3 timer siden (misfire_grace_time).
"""

import argparse
import logging
import sys
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import func, select
from sqlalchemy.orm import Session

import app.models  # noqa: F401
from app.audit.models import AuditLog
from app.db import ny_session
from app.fejlrapport import init_fejlrapport
from app.jobs.koe import laeg_i_koe
from app.natkoersel.job import JOBTYPE
from app.sikkerhed.hemmeligheder import rediger
from app.synk.planlaegger import aktive_kunder

log = logging.getLogger(__name__)

TIDSZONE = ZoneInfo("Europe/Copenhagen")
KLOKKEN = (5, 0)            # 05:00
UGEDAGE = "mon-fri"         # hverdage
SPREDNING = timedelta(hours=2)
MIN_AFSTAND = timedelta(seconds=20)


def trigger() -> CronTrigger:
    return CronTrigger(day_of_week=UGEDAGE, hour=KLOKKEN[0], minute=KLOKKEN[1], timezone=TIDSZONE)


def idempotens_noegle(client_id: int, dag: date) -> str:
    return f"nat:{client_id}:{dag.isoformat()}"


def planlaeg_nat(session: Session, nu: datetime | None = None) -> dict:
    """Læg nattens job i kø – spredt ud. Gemmes ved session.commit() hos den, der kalder."""
    nu = (nu or session.scalar(select(func.now()))).astimezone(TIDSZONE)
    dag = nu.date()
    kunder = aktive_kunder(session)
    afstand = max(SPREDNING / max(len(kunder), 1), MIN_AFSTAND)
    nye, fandtes, fejl = 0, 0, {}
    for i, client_id in enumerate(kunder):
        try:
            with session.begin_nested():  # én kundes fejl ruller kun den kunde tilbage
                r = laeg_i_koe(session, JOBTYPE, client_id=client_id, payload={"dag": dag.isoformat()},
                               planlagt_til=nu + i * afstand,
                               idempotens_noegle=idempotens_noegle(client_id, dag))
            nye += r.ny
            fandtes += not r.ny
        except Exception as e:  # noqa: BLE001 – de øvrige kunder skal stadig i kø
            fejl[str(client_id)] = rediger(f"{type(e).__name__}: {e}")[:300]
            log.exception("Kunne ikke lægge natkørsel i kø for kunde %s", client_id)
    resultat = {
        "dag": dag.isoformat(), "kunder": len(kunder), "nye_job": nye, "fandtes_allerede": fandtes,
        "fejl": fejl, "spredt_over_minutter": int(SPREDNING.total_seconds() // 60),
        "afstand_sekunder": int(afstand.total_seconds()),
    }
    session.add(AuditLog(client_id=None, handling="natkoersel_planlagt", detaljer=resultat))
    return resultat


def koer_planlaegning() -> dict:
    with ny_session() as session:
        resultat = planlaeg_nat(session)
        session.commit()
    log.info("Natkørsel planlagt: %s kunder, %s nye job, %s fandtes allerede, %s fejl",
             resultat["kunder"], resultat["nye_job"], resultat["fandtes_allerede"], len(resultat["fejl"]))
    return resultat


def koer_rykkerplanlaegning(type_: str) -> int:
    from app.opkraevning.jobs import planlaeg_rykkerjob

    with ny_session() as session:
        nye = planlaeg_rykkerjob(session, type_, datetime.now(TIDSZONE))
        session.commit()
    log.info("Rykkerjob %s: %s nye job", type_, nye)
    return nye


def lav_scheduler() -> BlockingScheduler:
    from app.opkraevning.jobs import KOE, KONTROL

    scheduler = BlockingScheduler(timezone=TIDSZONE)
    scheduler.add_job(koer_planlaegning, trigger(), id="natkoersel", name="Natkørsel",
                      misfire_grace_time=3 * 3600, coalesce=True, max_instances=1, replace_existing=True)
    # Rykkere: kl. 16 lægges næste bankdags rykkere i kø; kl. 9 kontrolleres de igen før afsendelse.
    for id_, type_, time in (("rykker_koe", KOE, 16), ("rykker_kontrol", KONTROL, 9)):
        scheduler.add_job(koer_rykkerplanlaegning, CronTrigger(day_of_week=UGEDAGE, hour=time, minute=0,
                                                               timezone=TIDSZONE),
                          args=[type_], id=id_, name=f"Rykkere ({type_})", misfire_grace_time=3600,
                          coalesce=True, max_instances=1, replace_existing=True)
    return scheduler


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Natkørsel: kl. 05:00 på hverdage.")
    parser.add_argument("--koer-nu", action="store_true", help="læg nattens job i kø nu (én gang)")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        datefmt="%Y-%m-%d %H:%M:%S")
    init_fejlrapport("scheduler")
    if args.koer_nu:
        r = koer_planlaegning()
        print(f"{r['kunder']} aktive kunder: {r['nye_job']} nye job, {r['fandtes_allerede']} "
              f"fandtes allerede, {len(r['fejl'])} fejl.")
        return 1 if r["fejl"] else 0
    scheduler = lav_scheduler()
    naeste = scheduler.get_jobs()[0].trigger.get_next_fire_time(None, datetime.now(TIDSZONE))
    log.info("Scheduler startet – næste natkørsel %s", f"{naeste:%A %d.%m.%Y kl. %H:%M}")
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        log.info("Scheduler stoppet")
    return 0


if __name__ == "__main__":
    sys.exit(main())

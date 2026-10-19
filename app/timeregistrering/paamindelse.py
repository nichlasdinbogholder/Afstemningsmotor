"""Påmindelse sidst på dagen (fase 1, trin 5): kl. 16.05 på arbejdsdage (bygherren 19.10.2026).

Dagen låses ALDRIG ved midnat – påmindelsen er en hjælp, og huller fanges, når ugen afleveres (fase 2).

- Hvem: aktive medarbejdere, der er sat op til timeregistrering, og som ikke har én eneste linje på dagen
  (timer, fravær eller "Ikke på arbejde").
- Hvornår: arbejdsdage (mandag–fredag, ikke helligdage og banklukkedage) kl. 16.05 – planlagt af natkørslens scheduler.
- Fra: MAIL_AFSENDER (info@dinbogholder.dk). Til: medarbejderens egen adresse. Med et direkte link til ugen.
- Højst én pr. medarbejder pr. dag (time_reminders er unik på medarbejder + dag og kan ikke ændres bagefter).
- TIME_REMINDER_SEND=false (standard) er tørløb: påmindelsen registreres som 'toerloeb', men sendes ikke.

Kommando (vis eller send for en dag):
    python -m app.timeregistrering.paamindelse vis [--dato ÅÅÅÅ-MM-DD]
"""

import argparse
import sys
from datetime import date, datetime

from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from app.audit.models import AuditLog
from app.config import get_settings
from app.jobs.koe import laeg_i_koe
from app.jobs.register import JobKontekst, jobtype
from app.mail import MailFejl, mail_slaaet_til, send_mail
from app.personale.models import Staff
from app.tid import TIDSZONE, er_bankdag
from app.timeregistrering.models import TimeEmployee, TimeRegistration, TimeReminder

JOBTYPE = "tid_paamindelse"
DAGE = ("mandag", "tirsdag", "onsdag", "torsdag", "fredag", "lørdag", "søndag")


def mangler(session: Session, dag: date) -> list[tuple[TimeEmployee, Staff]]:
    """Aktive medarbejdere uden en eneste linje på dagen."""
    har_linje = exists().where(TimeRegistration.employee_id == TimeEmployee.id, TimeRegistration.work_date == dag)
    return list(session.execute(
        select(TimeEmployee, Staff).join(Staff, Staff.id == TimeEmployee.staff_id)
        .where(Staff.aktiv.is_(True), ~har_linje).order_by(Staff.navn)).tuples())


def tekst(navn: str, dag: date) -> tuple[str, str]:
    link = f"{get_settings().public_url.rstrip('/')}/timeregistrering/?dato={dag.isoformat()}"
    emne = f"Husk timeregistreringen for i dag ({DAGE[dag.weekday()]} {dag:%d.%m.%Y})"
    krop = (f"Hej {navn}\n\n"
            f"Du har ikke registreret noget for i dag, {DAGE[dag.weekday()]} den {dag:%d.%m.%Y}.\n"
            "Udfyld dagen, inden du går hjem – timer, fravær eller \"Ikke på arbejde\", hvis du er timelønnet.\n\n"
            f"Registrér her: {link}\n\n"
            "Venlig hilsen\nDin Bogholder\n\n"
            "(Mailen er sendt automatisk fra hub'en.)")
    return emne, krop


def send_paamindelser(session: Session, dag: date) -> dict:
    """Send (eller registrér i tørløb) dagens påmindelser. Gør intet på andet end arbejdsdage."""
    if not er_bankdag(dag):
        return {"arbejdsdag": False, "sendt": 0, "toerloeb": 0, "fejl": 0, "allerede": 0}
    send = get_settings().time_reminder_send and mail_slaaet_til()
    tal = {"arbejdsdag": True, "sendt": 0, "toerloeb": 0, "fejl": 0, "allerede": 0}
    for e, m in mangler(session, dag):
        if session.scalar(select(TimeReminder.id).where(TimeReminder.employee_id == e.id,
                                                        TimeReminder.work_date == dag)) is not None:
            tal["allerede"] += 1
            continue
        status, fejl = "toerloeb", None
        if send:
            emne, krop = tekst(m.navn, dag)
            try:
                send_mail(m.email, emne, krop)
                status = "sendt"
            except MailFejl as f:
                status, fejl = "fejl", str(f)
        session.add(TimeReminder(employee_id=e.id, work_date=dag, status=status, email=m.email, error=fejl))
        # Gem med det samme, så en afsendt påmindelse aldrig sendes igen, hvis noget senere går galt.
        session.commit()
        tal[status] += 1
    session.add(AuditLog(handling="tid_paamindelser", detaljer={"dag": dag.isoformat(), **tal}))
    session.commit()
    return tal


def planlaeg(session: Session, nu: datetime) -> int:
    """Ét job pr. dag (idempotent). Kaldes af scheduleren kl. 16.05 mandag–fredag."""
    dag = nu.astimezone(TIDSZONE).date()
    return laeg_i_koe(session, JOBTYPE, payload={"dag": dag.isoformat()},
                      idempotens_noegle=f"{JOBTYPE}:{dag.isoformat()}", prioritet=10).ny


@jobtype(JOBTYPE)
def paamindelse_job(job: JobKontekst) -> None:
    """Bruger sin egen session og gemmer efter HVER mail – så en sendt påmindelse aldrig sendes igen, heller ikke hvis
    jobbet stopper midtvejs og prøves igen."""
    from app.db import ny_session

    with ny_session() as session:
        send_paamindelser(session, date.fromisoformat(job.payload["dag"]))


def main(argv: list[str] | None = None) -> int:
    import app.models  # noqa: F401
    from app.db import ny_session

    parser = argparse.ArgumentParser(description="Påmindelser i timeregistreringen.")
    parser.add_argument("kommando", choices=("vis",), help="vis hvem der mangler at udfylde dagen")
    parser.add_argument("--dato", type=date.fromisoformat, default=None)
    args = parser.parse_args(argv)
    dag = args.dato or datetime.now(TIDSZONE).date()
    with ny_session() as session:
        mangel = mangler(session, dag)
        print(f"{DAGE[dag.weekday()].capitalize()} {dag:%d.%m.%Y} – "
              f"{'arbejdsdag' if er_bankdag(dag) else 'IKKE arbejdsdag (ingen påmindelser)'}")
        print(f"Afsendelse: {'slået til' if get_settings().time_reminder_send and mail_slaaet_til() else 'tørløb'}"
              f" (fra {get_settings().mail_afsender})")
        for _, m in mangel:
            print(f"  Mangler: {m.navn} <{m.email}>")
        if not mangel:
            print("  Alle har udfyldt dagen.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

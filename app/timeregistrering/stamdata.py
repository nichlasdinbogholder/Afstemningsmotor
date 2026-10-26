"""Stamdata til timeregistreringen: ansættelsesform og satser (normtid + kostpris) pr. medarbejder.

Alt indtastes manuelt – der beregnes ingenting automatisk. En sats gælder fra en dato; en ny sats afslutter den
forrige (slutdato = den nye sats' startdato), så perioderne aldrig overlapper (databasen afviser overlap). En sats
med samme startdato som en eksisterende retter den (fx en tastefejl). Satser slettes aldrig. Hver ændring logges
af databasen i time_audit med aktøren fra `saet_aktoer()`.
"""

from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.timeregistrering.models import ANSAETTELSER, TimeEmployee, TimeEmployeeRate
from app.timeregistrering.satser import placer_periode


class StamdataFejl(Exception):
    """Fejl ved indtastning. Beskeden er dansk og kan vises direkte."""


def saet_aktoer(session: Session, staff_id: int) -> None:
    """Hvem der laver ændringerne i denne transaktion – revisionssporet (time_audit) skriver det på hver række."""
    session.execute(text("SELECT set_config('app.actor_id', :id, true)"), {"id": str(int(staff_id))})


def ansat(session: Session, staff_id: int) -> TimeEmployee | None:
    return session.scalars(select(TimeEmployee).where(TimeEmployee.staff_id == staff_id)).one_or_none()


def saet_ansaettelse(session: Session, staff_id: int, ansaettelse: str, flexjob: bool,
                     loenref: str | None) -> TimeEmployee:
    """Timelønnede optjener aldrig afspadsering (håndhæves også i databasen); fastlønnede gør."""
    if ansaettelse not in ANSAETTELSER:
        raise StamdataFejl("Ansættelsesformen skal være fastlønnet eller timelønnet")
    e = ansat(session, staff_id)
    if e is None:
        e = TimeEmployee(staff_id=staff_id)
        session.add(e)
    e.employment = ansaettelse
    e.flexjob = flexjob
    e.accrues_toil = ansaettelse == "fastloennet"
    e.payroll_ref = (loenref or "").strip() or None
    session.flush()
    return e


def sats_paa(session: Session, employee_id: int, dag: date) -> TimeEmployeeRate | None:
    return session.scalars(select(TimeEmployeeRate).where(
        TimeEmployeeRate.employee_id == employee_id, TimeEmployeeRate.valid_from <= dag,
        (TimeEmployeeRate.valid_to.is_(None)) | (TimeEmployeeRate.valid_to > dag))).one_or_none()


def naeste_sats(session: Session, employee_id: int, dag: date) -> TimeEmployeeRate | None:
    return session.scalars(select(TimeEmployeeRate).where(
        TimeEmployeeRate.employee_id == employee_id, TimeEmployeeRate.valid_from > dag)
        .order_by(TimeEmployeeRate.valid_from)).first()


def satser(session: Session, employee_id: int) -> list[TimeEmployeeRate]:
    return list(session.scalars(select(TimeEmployeeRate).where(TimeEmployeeRate.employee_id == employee_id)
                                .order_by(TimeEmployeeRate.valid_from.desc())))


# Standard for fastlønnede på fuld tid (bygherren 26.10.2026). Kan rettes pr. medarbejder.
STANDARD_PRODUKTIVE_TIMER = Decimal(1400)
FULD_TID_TIMER_UGE = Decimal(37)


def standard_produktive_timer(normtid: Decimal | None) -> Decimal:
    """Forholdsmæssigt efter normtiden: 1.400 × normtid / 37, afrundet til hele timer (fx 20 t/uge -> 757).
    Uden normtid (eller 0) bruges fuld tid: 1.400."""
    if not normtid:
        return STANDARD_PRODUKTIVE_TIMER
    return (STANDARD_PRODUKTIVE_TIMER * normtid / FULD_TID_TIMER_UGE).quantize(Decimal(1), rounding=ROUND_HALF_UP)


def ny_sats(session: Session, e: TimeEmployee, gyldig_fra: date, normtid: Decimal | None, kostpris: Decimal,
            produktive_timer: Decimal | None, note: str | None, af_staff_id: int) -> TimeEmployeeRate:
    """Timelønnede har ingen produktive timer – kostprisen tager udgangspunkt i timelønnen. Fastlønnede får
    standarden ud fra normtiden (standard_produktive_timer), hvis intet er angivet."""
    if e.employment == "timeloennet":
        if produktive_timer is not None:
            raise StamdataFejl("Timelønnede har ingen produktive timer – kostprisen tager udgangspunkt i timelønnen")
    elif produktive_timer is None:
        produktive_timer = standard_produktive_timer(normtid)
    if produktive_timer is not None and produktive_timer <= 0:
        raise StamdataFejl("Produktive timer pr. år skal være større end 0")
    if kostpris < 0:
        raise StamdataFejl("Kostprisen kan ikke være negativ")
    if normtid is not None and not (0 <= normtid <= 168):
        raise StamdataFejl("Normtiden skal være mellem 0 og 168 timer om ugen")
    note = (note or "").strip() or None
    # Den sats, der gælder på startdatoen, afsluttes dagen før – den nye gælder til den næste sats' start.
    samme, foer, efter = placer_periode(satser(session, e.id), gyldig_fra)
    if samme is not None:
        samme.weekly_norm, samme.cost_per_hour, samme.productive_hours_year = normtid, kostpris, produktive_timer
        samme.note, samme.created_by = note, af_staff_id
        session.flush()
        return samme
    if foer is not None:
        foer.valid_to = gyldig_fra
        session.flush()
    r = TimeEmployeeRate(employee_id=e.id, valid_from=gyldig_fra, valid_to=efter, weekly_norm=normtid,
                         cost_per_hour=kostpris, productive_hours_year=produktive_timer, note=note,
                         created_by=af_staff_id)
    session.add(r)
    session.flush()
    return r

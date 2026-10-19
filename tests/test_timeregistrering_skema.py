"""Timeregistrering, trin 1: reglerne, som DATABASEN håndhæver (spec-test 1–7, 10, 11, 23, 25–27, 36, 41).

Rigtig PostgreSQL – reglerne prøves direkte mod tabellerne, uden om Python-koden.
"""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.kunder.models import Client
from app.personale.models import Staff
from app.timeregistrering import stamdata
from app.timeregistrering.models import (
    TimeAudit,
    TimeClientTerms,
    TimeEmployee,
    TimeEmployeeRate,
    TimeEntryNote,
    TimePayrollBatch,
    TimePayrollLine,
    TimeRegistration,
    TimeSalesRate,
    TimeTaskType,
    TimeToilMovement,
    TimeWeek,
)

MANDAG = date(2026, 10, 12)


@pytest.fixture
def grund(db_session):
    s = Staff(navn="Tid Testesen", email="tid-skema@dinbogholder.dk", rolle="medarbejder", aktiv=True)
    k = Client(navn="Tid ApS", kundenummer="TID-1", regnskabssystem="economic", status="aktiv")
    db_session.add_all([s, k])
    db_session.flush()
    stamdata.saet_aktoer(db_session, s.id)
    fast = TimeEmployee(staff_id=s.id, employment="fastloennet")
    db_session.add(fast)
    db_session.flush()
    uge = TimeWeek(employee_id=fast.id, iso_year=2026, iso_week=42, week_start=MANDAG)
    db_session.add(uge)
    db_session.flush()
    return s, k, fast, uge


def _type(session, code):
    return session.scalars(select(TimeTaskType).where(TimeTaskType.code == code)).one()


def _linje(session, s, e, uge, code, timer="1", kunde=None, dag=MANDAG, **ekstra):
    r = TimeRegistration(week_id=uge.id, employee_id=e.id, work_date=dag, task_type_id=_type(session, code).id,
                         task_kind="kunde", client_id=kunde.id if kunde else None, hours=Decimal(timer),
                         created_by=s.id, **ekstra)
    session.add(r)
    session.flush()
    session.refresh(r)  # task_kind sættes af databasen
    return r


def _afvist(session, fejl, match, fn):
    with pytest.raises(fejl, match=match):
        with session.begin_nested():
            fn()


def test_stamdata_er_lagt_ind(db_session):
    koder = {t.code: t for t in db_session.scalars(select(TimeTaskType))}
    assert len(koder) == 20 and koder["materialerykning"].kind == "kunde"
    assert koder["oplaering"].excluded_from_efficiency and koder["ferie"].blocked_for_hourly
    assert koder["afspadsering"].reduces_toil
    assert koder["ikke_paa_arbejde"].zero_hours and not koder["ikke_paa_arbejde"].payable
    assert not koder["salg"].active  # venter på pipelinen
    # Bygherren 19.10.2026: Afstemning -> Controlling, Bogføring -> Kundearbejde, Øvrigt kundearbejde ude af brug.
    assert (koder["controlling"].name, koder["kundearbejde"].name) == ("Controlling", "Kundearbejde")
    assert "afstemning" not in koder and "bogfoering" not in koder and not koder["oevrigt_kunde"].active
    pris = db_session.scalars(select(TimeSalesRate).where(TimeSalesRate.valid_from == date(2026, 10, 18))).one()
    assert pris.rate_per_hour == Decimal("699.00") and pris.valid_to is None


def test_registrering_regler(db_session, grund):
    s, k, e, uge = grund
    kunde = _linje(db_session, s, e, uge, "kundearbejde", "1.25", k)
    assert kunde.task_kind == "kunde"  # sat af databasen
    intern = _linje(db_session, s, e, uge, "administration", "0.5")
    assert intern.task_kind == "intern"
    # 10. Lørdag kan registreres
    _linje(db_session, s, e, uge, "kundearbejde", "2", k, dag=date(2026, 10, 17))
    # 1. Kvarte timer
    _afvist(db_session, IntegrityError, "kvarte_timer", lambda: _linje(db_session, s, e, uge, "kundearbejde", "1.1", k))
    # 2. Kundetype uden kunde
    _afvist(db_session, IntegrityError, "kunde_kun_paa_kundetid", lambda: _linje(db_session, s, e, uge, "moms", "1"))
    # 4. Fravær med kunde
    _afvist(db_session, IntegrityError, "kunde_kun_paa_kundetid",
            lambda: _linje(db_session, s, e, uge, "sygdom", "7.5", k))
    # 5. Faktureres særskilt uden beskrivelse
    _afvist(db_session, IntegrityError, "saerskilt_kraever_beskrivelse",
            lambda: _linje(db_session, s, e, uge, "kundearbejde", "1", k, billable_separately=True, description=" "))
    _linje(db_session, s, e, uge, "kundearbejde", "1", k, billable_separately=True, description="Ekstra opgave")
    # 6. 'Ikke på arbejde' med timer
    _afvist(db_session, DBAPIError, "altid med 0 timer", lambda: _linje(db_session, s, e, uge, "ikke_paa_arbejde", "1"))
    _linje(db_session, s, e, uge, "ikke_paa_arbejde", "0")
    # 3. 'salg' kan ikke bruges, før pipeline-emner findes
    _afvist(db_session, DBAPIError, "ikke i brug", lambda: _linje(db_session, s, e, uge, "salg", "1"))
    # Linjen skal ligge i ugen
    _afvist(db_session, DBAPIError, "ikke i ugen",
            lambda: _linje(db_session, s, e, uge, "kundearbejde", "1", k, dag=date(2026, 10, 19)))


def test_7_ferie_afvises_for_timeloennet(db_session, grund):
    s, k, _, _ = grund
    t = Staff(navn="Time Lønsen", email="time-skema@dinbogholder.dk", aktiv=True)
    db_session.add(t)
    db_session.flush()
    _afvist(db_session, IntegrityError, "timeloennet_uden_afspadsering",
            lambda: (db_session.add(TimeEmployee(staff_id=t.id, employment="timeloennet", accrues_toil=True)),
                     db_session.flush()))
    e = TimeEmployee(staff_id=t.id, employment="timeloennet", accrues_toil=False)
    db_session.add(e)
    db_session.flush()
    uge = TimeWeek(employee_id=e.id, iso_year=2026, iso_week=42, week_start=MANDAG)
    db_session.add(uge)
    db_session.flush()
    _afvist(db_session, DBAPIError, "kan ikke registreres for timelønnede",
            lambda: _linje(db_session, t, e, uge, "ferie", "7.5"))
    _linje(db_session, t, e, uge, "sygdom", "7.5")


def test_25_27_godkendt_uge_er_laast(db_session, grund):
    s, k, e, uge = grund
    linje = _linje(db_session, s, e, uge, "kundearbejde", "2", k)
    uge.status = "godkendt"
    db_session.flush()
    _afvist(db_session, DBAPIError, "er godkendt og kan ikke ændres",
            lambda: db_session.execute(text("UPDATE time_entries SET hours = 3 WHERE id = :i"), {"i": linje.id}))
    _afvist(db_session, DBAPIError, "er godkendt og kan ikke ændres",
            lambda: db_session.execute(text("DELETE FROM time_entries WHERE id = :i"), {"i": linje.id}))
    _afvist(db_session, DBAPIError, "er godkendt og kan ikke ændres",
            lambda: _linje(db_session, s, e, uge, "kundearbejde", "1", k))
    # En linje kan heller ikke flyttes ud af den godkendte uge.
    ny = TimeWeek(employee_id=e.id, iso_year=2026, iso_week=43, week_start=date(2026, 10, 19))
    db_session.add(ny)
    db_session.flush()
    _afvist(db_session, DBAPIError, "er godkendt og kan ikke ændres",
            lambda: db_session.execute(text("UPDATE time_entries SET week_id = :u, work_date = '2026-10-19' "
                                            "WHERE id = :i"), {"u": ny.id, "i": linje.id}))
    # 28 (forberedt): en korrektionslinje i en åben uge går igennem.
    _linje(db_session, s, e, ny, "kundearbejde", "0.5", k, dag=date(2026, 10, 19),
           is_correction=True, corrects_entry_id=linje.id)


def test_41_kun_godkendte_uger_til_loen(db_session, grund):
    _, _, e, uge = grund
    batch = TimePayrollBatch(period_start=MANDAG, period_end=date(2026, 10, 18), idempotency_key="test-batch-41")
    db_session.add(batch)
    db_session.flush()

    def loen(noegle):
        db_session.add(TimePayrollLine(batch_id=batch.id, employee_id=e.id, week_id=uge.id, kind="timeloen",
                                       hours=Decimal("10"), idempotency_key=noegle))
        db_session.flush()

    _afvist(db_session, DBAPIError, "må ikke sendes til løn", lambda: loen("n1"))
    uge.status = "godkendt"
    db_session.flush()
    loen("n2")
    _afvist(db_session, IntegrityError, "idempotency_key", lambda: loen("n2"))


def test_23_36_historik_kan_ikke_aendres(db_session, grund):
    s, k, e, uge = grund
    linje = _linje(db_session, s, e, uge, "kundearbejde", "2", k)
    note = TimeEntryNote(entry_id=linje.id, week_id=uge.id, author_id=s.id, note="Mangler beskrivelse")
    bev = TimeToilMovement(employee_id=e.id, movement_date=MANDAG, kind="optjent", hours=Decimal("2"))
    db_session.add_all([note, bev])
    db_session.flush()
    for tabel, i in (("time_entry_notes", note.id), ("time_toil_movements", bev.id)):
        _afvist(db_session, DBAPIError, "kan ikke ændres eller slettes",
                lambda: db_session.execute(text(f"DELETE FROM {tabel} WHERE id = :i"), {"i": i}))
        _afvist(db_session, DBAPIError, "kan ikke ændres eller slettes",
                lambda: db_session.execute(text(f"UPDATE {tabel} SET created_at = now() WHERE id = :i"), {"i": i}))
    _afvist(db_session, DBAPIError, "kan ikke ændres eller slettes",
            lambda: db_session.execute(text("DELETE FROM time_audit")))


def test_11_satsperioder_kan_ikke_overlappe_eller_slettes(db_session, grund):
    s, k, e, _ = grund

    def sats(fra, til=None):
        db_session.add(TimeEmployeeRate(employee_id=e.id, valid_from=fra, valid_to=til, cost_per_hour=400,
                                        productive_hours_year=1450))
        db_session.flush()

    sats(date(2026, 1, 1), date(2026, 7, 1))
    _afvist(db_session, IntegrityError, "ingen_overlap", lambda: sats(date(2026, 6, 1)))
    sats(date(2026, 7, 1))  # slutdatoen er ikke med – perioderne støder op til hinanden
    _afvist(db_session, IntegrityError, "ingen_overlap", lambda: (db_session.add(TimeSalesRate(
        valid_from=date(2026, 11, 1), rate_per_hour=750)), db_session.flush()))
    db_session.add(TimeClientTerms(client_id=k.id, valid_from=date(2026, 1, 1), billing_model="timeafregning"))
    db_session.flush()
    _afvist(db_session, IntegrityError, "abonnement_kraever_pris", lambda: (db_session.add(TimeClientTerms(
        client_id=k.id, valid_from=date(2025, 1, 1), valid_to=date(2026, 1, 1), billing_model="abonnement")),
        db_session.flush()))
    _afvist(db_session, IntegrityError, "ingen_overlap", lambda: (db_session.add(TimeClientTerms(
        client_id=k.id, valid_from=date(2026, 3, 1), billing_model="abonnement", subscription_price=1500)),
        db_session.flush()))
    for tabel in ("time_employee_rates", "time_client_terms", "time_sales_rates"):
        _afvist(db_session, DBAPIError, "kan ikke slettes", lambda: db_session.execute(text(f"DELETE FROM {tabel}")))


def test_aendringer_logges_med_aktoer_og_fra_til(db_session, grund):
    s, k, e, uge = grund
    linje = _linje(db_session, s, e, uge, "kundearbejde", "2", k)
    linje.hours = Decimal("2.5")
    db_session.flush()
    log = db_session.scalars(select(TimeAudit).where(TimeAudit.table_name == "time_entries",
                                                     TimeAudit.row_id == linje.id).order_by(TimeAudit.id)).all()
    assert [x.action for x in log] == ["INSERT", "UPDATE"]
    assert log[1].before["hours"] == 2 and log[1].after["hours"] == 2.5 and log[1].actor_id == s.id

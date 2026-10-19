"""Timeregistrering, trin 5: påmindelse kl. 16.05 (fra info@) og udgående mail via Microsoft Graph.

Microsoft er SIMULERET (httpx.MockTransport) – der sendes aldrig en rigtig mail.
"""

from datetime import date, datetime
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from app import mail
from app.config import get_settings
from app.jobs.models import Job
from app.personale.models import Staff
from app.tid import TIDSZONE
from app.timeregistrering import paamindelse as p
from app.timeregistrering import stamdata
from app.timeregistrering.models import TimeRegistration, TimeReminder, TimeTaskType, TimeWeek

MANDAG = date(2027, 2, 22)


@pytest.fixture
def grund(db_session, monkeypatch):
    # Testdatabasen kan have andre medarbejdere – de tæller ikke med her.
    for m in db_session.scalars(select(Staff)):
        m.aktiv = False
    a = Staff(navn="Anna Påmind", email="anna-pm@dinbogholder.dk", aktiv=True)
    b = Staff(navn="Bo Påmind", email="bo-pm@dinbogholder.dk", aktiv=True)
    uden = Staff(navn="Ikke sat op", email="uden-pm@dinbogholder.dk", aktiv=True)
    db_session.add_all([a, b, uden])
    db_session.flush()
    ea = stamdata.saet_ansaettelse(db_session, a.id, "fastloennet", False, None)
    eb = stamdata.saet_ansaettelse(db_session, b.id, "timeloennet", False, None)
    monkeypatch.setattr(db_session, "commit", db_session.flush)
    return a, b, ea, eb


def _linje(db_session, staff, e, code, timer, dag=MANDAG):
    uge = db_session.scalars(select(TimeWeek).where(TimeWeek.employee_id == e.id)).one_or_none()
    if uge is None:
        uge = TimeWeek(employee_id=e.id, iso_year=2027, iso_week=8, week_start=MANDAG)
        db_session.add(uge)
        db_session.flush()
    t = db_session.scalars(select(TimeTaskType).where(TimeTaskType.code == code)).one()
    db_session.add(TimeRegistration(week_id=uge.id, employee_id=e.id, work_date=dag, task_type_id=t.id,
                                    task_kind=t.kind, hours=Decimal(timer), created_by=staff.id))
    db_session.flush()


def test_hvem_mangler(db_session, grund):
    a, b, ea, eb = grund
    assert [m.navn for _, m in p.mangler(db_session, MANDAG)] == ["Anna Påmind", "Bo Påmind"]
    _linje(db_session, a, ea, "administration", "7.5")
    # "Ikke på arbejde" tæller som udfyldt for en timelønnet (0 timer).
    _linje(db_session, b, eb, "ikke_paa_arbejde", "0")
    assert p.mangler(db_session, MANDAG) == []
    assert [m.navn for _, m in p.mangler(db_session, date(2027, 2, 23))] == ["Anna Påmind", "Bo Påmind"]
    b.aktiv = False
    db_session.flush()
    assert [m.navn for _, m in p.mangler(db_session, date(2027, 2, 23))] == ["Anna Påmind"]


def test_kun_arbejdsdage(db_session, grund, monkeypatch):
    sendt = []
    monkeypatch.setattr(p, "send_mail", lambda *a: sendt.append(a))
    assert p.send_paamindelser(db_session, date(2027, 2, 27))["arbejdsdag"] is False  # lørdag
    assert p.send_paamindelser(db_session, date(2027, 3, 26))["arbejdsdag"] is False  # langfredag
    assert sendt == [] and db_session.scalars(select(TimeReminder)).all() == []


def test_toerloeb_sender_intet(db_session, grund, monkeypatch):
    sendt = []
    monkeypatch.setattr(p, "send_mail", lambda *a: sendt.append(a))
    monkeypatch.setattr(get_settings(), "time_reminder_send", False)
    tal = p.send_paamindelser(db_session, MANDAG)
    assert (tal["toerloeb"], tal["sendt"], sendt) == (2, 0, [])
    assert {r.status for r in db_session.scalars(select(TimeReminder))} == {"toerloeb"}


def test_sendes_kun_en_gang_og_fejl_registreres(db_session, grund, monkeypatch):
    a, b, *_ = grund
    sendt = []

    def falsk(til, emne, krop):
        if til == b.email:
            raise mail.MailFejl("Microsoft afviste mailen (status 403)")
        sendt.append((til, emne, krop))

    monkeypatch.setattr(p, "send_mail", falsk)
    monkeypatch.setattr(p, "mail_slaaet_til", lambda: True)
    monkeypatch.setattr(get_settings(), "time_reminder_send", True)
    monkeypatch.setattr(get_settings(), "public_url", "https://hub.dinbogholder.dk")
    tal = p.send_paamindelser(db_session, MANDAG)
    assert (tal["sendt"], tal["fejl"]) == (1, 1)
    til, emne, krop = sendt[0]
    assert til == a.email and "mandag 22.02.2027" in emne
    assert "Hej Anna Påmind" in krop and "https://hub.dinbogholder.dk/timeregistrering/?dato=2027-02-22" in krop
    fejl = db_session.scalars(select(TimeReminder).where(TimeReminder.status == "fejl")).one()
    assert fejl.email == b.email and "403" in fejl.error
    # Kører den igen (fx jobbet prøves igen), sendes intet en gang til.
    assert p.send_paamindelser(db_session, MANDAG)["allerede"] == 2 and len(sendt) == 1
    with pytest.raises(DBAPIError, match="kan ikke ændres eller slettes"):
        with db_session.begin_nested():
            db_session.execute(text("DELETE FROM time_reminders"))


def test_planlaegning_er_idempotent(db_session, grund):
    nu = datetime(2027, 2, 22, 16, 5, tzinfo=TIDSZONE)
    assert p.planlaeg(db_session, nu) == 1 and p.planlaeg(db_session, nu) == 0
    job = db_session.scalars(select(Job).where(Job.type == p.JOBTYPE)).one()
    assert job.payload == {"dag": "2027-02-22"}


# --- Udgående mail via Microsoft Graph (simuleret) --------------------------------------------


@pytest.fixture
def mailopsaetning(monkeypatch, token):
    s = get_settings()
    monkeypatch.setattr(s, "mail_tenant_id", "tenant-test")
    monkeypatch.setattr(s, "mail_client_id", "klient-test")
    from pydantic import SecretStr

    monkeypatch.setattr(s, "mail_client_secret", SecretStr(token))
    monkeypatch.setattr(s, "mail_afsender", "info@dinbogholder.dk")
    return token


def test_mail_sendes_fra_info(mailopsaetning):
    kald = []

    def microsoft(request):
        kald.append(request)
        if "oauth2" in request.url.path:
            return httpx.Response(200, json={"access_token": "adgang-123"})
        return httpx.Response(202)

    mail.send_mail("anna@dinbogholder.dk", "Emne", "Tekst", transport=httpx.MockTransport(microsoft))
    assert kald[1].url.path == "/v1.0/users/info@dinbogholder.dk/sendMail"
    assert kald[1].headers["Authorization"] == "Bearer adgang-123"
    assert b'"address":"anna@dinbogholder.dk"' in kald[1].content.replace(b" ", b"")


def test_mail_proever_igen_og_fejl_uden_hemmeligheder(mailopsaetning):
    svar = iter([httpx.Response(200, json={"access_token": "a"}), httpx.Response(429), httpx.Response(202)])
    mail.send_mail("x@dinbogholder.dk", "E", "T", transport=httpx.MockTransport(lambda r: next(svar)),
                   vent=lambda _: 0)
    with pytest.raises(mail.MailFejl) as fejl:
        mail.send_mail("x@dinbogholder.dk", "E", "T", vent=lambda _: 0, transport=httpx.MockTransport(
            lambda r: httpx.Response(401, json={"error": mailopsaetning})))
    assert "status 401" in str(fejl.value) and mailopsaetning not in str(fejl.value)


def test_mail_slaaet_fra_uden_noegler(monkeypatch):
    monkeypatch.setattr(get_settings(), "mail_client_secret", None)
    assert mail.mail_slaaet_til() is False
    with pytest.raises(mail.MailFejl, match="ikke slået til"):
        mail.send_mail("x@dinbogholder.dk", "E", "T")

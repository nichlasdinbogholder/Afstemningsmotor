"""Medarbejdere administreres fra websiden – kun af administratorer, og ingen kan låse alle ude."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api import login
from app.api.main import app
from app.audit.models import AuditLog
from app.personale.models import Staff

H = {"X-Afstemning": "1"}


@pytest.fixture
def admin(db_session):
    # Testdatabasen kan have andre medarbejdere – sæt dem ud af spillet, så "sidste admin" kan testes.
    for m in db_session.scalars(select(Staff)):
        m.aktiv = False
    a = Staff(navn="Admin", email="admin-med@dinbogholder.dk", rolle="admin", aktiv=True)
    db_session.add(a)
    db_session.flush()
    return a


@pytest.fixture
def klient(db_session, admin):
    app.dependency_overrides[login.db] = lambda: db_session
    app.dependency_overrides[login.nuvaerende_medarbejder] = lambda: admin
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_kun_administratorer(db_session, klient):
    m = Staff(navn="Bo", email="bo-med@dinbogholder.dk", rolle="medarbejder", aktiv=True)
    db_session.add(m)
    db_session.flush()
    app.dependency_overrides[login.nuvaerende_medarbejder] = lambda: m
    assert klient.get("/api/medarbejdere").status_code == 403
    assert klient.post("/api/medarbejdere", headers=H, json={"email": "x@dinbogholder.dk", "navn": "X"}
                       ).status_code == 403


def test_opret_logges_og_kraever_header(db_session, klient, admin):
    ny = {"email": " Info@DinBogholder.dk ", "navn": "Testbruger"}
    assert klient.post("/api/medarbejdere", json=ny).status_code == 403          # mangler header
    svar = klient.post("/api/medarbejdere", headers=H, json=ny)
    assert svar.status_code == 200
    assert (svar.json()["email"], svar.json()["rolle"], svar.json()["aktiv"]) == (
        "info@dinbogholder.dk", "medarbejder", True)
    assert klient.post("/api/medarbejdere", headers=H, json=ny).status_code == 409   # findes allerede
    assert klient.post("/api/medarbejdere", headers=H, json={"email": "ikke-en-mail", "navn": "X"}).status_code == 422
    assert klient.post("/api/medarbejdere", headers=H,
                       json={"email": "y@dinbogholder.dk", "navn": "Y", "rolle": "chef"}).status_code == 422
    log = db_session.scalars(select(AuditLog).where(AuditLog.handling == "medarbejder_opret")).one()
    assert log.staff_id == admin.id and log.detaljer["udfoert_af"] == admin.email
    assert "info@dinbogholder.dk" in [m["email"] for m in klient.get("/api/medarbejdere").json()]


def test_rolle_og_lukket_adgang(db_session, klient):
    mid = klient.post("/api/medarbejdere", headers=H, json={"email": "bo2@dinbogholder.dk", "navn": "Bo"}).json()["id"]
    assert klient.post(f"/api/medarbejdere/{mid}", headers=H, json={"rolle": "admin"}).json()["rolle"] == "admin"
    assert klient.post(f"/api/medarbejdere/{mid}", headers=H, json={"aktiv": False}).json()["aktiv"] is False
    assert db_session.get(Staff, mid).aktiv is False
    handlinger = db_session.scalars(select(AuditLog.handling).where(
        AuditLog.detaljer["medarbejder_id"].as_integer() == mid).order_by(AuditLog.id)).all()
    assert handlinger == ["medarbejder_opret", "medarbejder_invitation", "medarbejder_rolle", "medarbejder_deaktiver"]


def test_ingen_kan_laase_alle_ude(db_session, klient, admin):
    assert klient.post(f"/api/medarbejdere/{admin.id}", headers=H, json={"aktiv": False}).status_code == 409
    assert klient.post(f"/api/medarbejdere/{admin.id}", headers=H, json={"rolle": "medarbejder"}).status_code == 409
    anden = Staff(navn="Anden", email="anden-med@dinbogholder.dk", rolle="admin", aktiv=True)
    db_session.add(anden)
    db_session.flush()
    # To aktive admins: den anden må gerne lukkes – men så er der kun én tilbage.
    assert klient.post(f"/api/medarbejdere/{anden.id}", headers=H, json={"aktiv": False}).status_code == 200
    assert klient.post(f"/api/medarbejdere/{anden.id}", headers=H, json={"aktiv": True}).json()["aktiv"] is True


# Kostpris pr. time – kun administratorer; gemmes med gyldig-fra-dato (historik), kan ikke slettes
def test_satser_kun_for_administratorer(db_session, klient):
    m = Staff(navn="Bo", email="bo-sats@dinbogholder.dk", rolle="medarbejder", aktiv=True)
    db_session.add(m)
    db_session.flush()
    app.dependency_overrides[login.nuvaerende_medarbejder] = lambda: m
    assert klient.get(f"/api/medarbejdere/{m.id}/satser").status_code == 403
    assert klient.post(f"/api/medarbejdere/{m.id}/sats", headers=H, json={
        "gyldig_fra": "2026-01-01", "kostpris_pr_time": "1", "produktive_timer_aar": "1"}).status_code == 403
    assert klient.post(f"/api/medarbejdere/{m.id}/ansaettelse", headers=H,
                       json={"ansaettelse": "fastloennet"}).status_code == 403


def test_ansaettelse_og_satser_med_historik_og_log(db_session, klient, admin):
    from app.timeregistrering.models import TimeAudit

    m = Staff(navn="Sara", email="sara-sats@dinbogholder.dk", rolle="medarbejder", aktiv=True)
    db_session.add(m)
    db_session.flush()
    sats = {"gyldig_fra": "2026-01-01", "normtid": "37", "kostpris_pr_time": "400", "produktive_timer_aar": "1450"}
    svar = klient.post(f"/api/medarbejdere/{m.id}/sats", headers=H, json=sats)
    assert svar.status_code == 422 and "ansættelsesform" in svar.json()["detail"]
    rad = klient.post(f"/api/medarbejdere/{m.id}/ansaettelse", headers=H,
                      json={"ansaettelse": "timeloennet", "flexjob": False, "loenref": " Z-17 "}).json()
    assert (rad["tid"]["ansaettelse"], rad["tid"]["afspadsering"], rad["tid"]["loenref"]) == ("timeloennet", False, "Z-17")
    klient.post(f"/api/medarbejdere/{m.id}/ansaettelse", headers=H, json={"ansaettelse": "fastloennet", "flexjob": True})
    assert klient.post(f"/api/medarbejdere/{m.id}/sats", headers=H, json=sats).status_code == 200
    # En ny sats afslutter den forrige; samme startdato retter den (tastefejl).
    klient.post(f"/api/medarbejdere/{m.id}/sats", headers=H, json=sats | {"gyldig_fra": "2026-06-01",
                                                                         "kostpris_pr_time": "425.5"})
    rad = klient.post(f"/api/medarbejdere/{m.id}/sats", headers=H, json=sats | {"gyldig_fra": "2026-06-01",
                                                                               "kostpris_pr_time": "425.50"}).json()
    assert rad["tid"]["flexjob"] is True and rad["tid"]["afspadsering"] is True
    historik = klient.get(f"/api/medarbejdere/{m.id}/satser").json()
    assert [(h["fra"], h["til"], h["kostpris_pr_time"]) for h in historik] == [
        ("2026-06-01", None, "425.50"), ("2026-01-01", "2026-06-01", "400.00")]
    assert rad["tid"]["sats"]["kostpris_pr_time"] == "425.50"
    # En sats ind imellem to andre slutter, hvor den næste begynder.
    klient.post(f"/api/medarbejdere/{m.id}/sats", headers=H, json=sats | {"gyldig_fra": "2026-03-01"})
    historik = klient.get(f"/api/medarbejdere/{m.id}/satser").json()
    assert [(h["fra"], h["til"]) for h in historik] == [
        ("2026-06-01", None), ("2026-03-01", "2026-06-01"), ("2026-01-01", "2026-03-01")]
    log = db_session.scalars(select(TimeAudit).where(TimeAudit.table_name == "time_employee_rates")).all()
    assert log and all(x.actor_id == admin.id for x in log)
    # Fra hvad til hvad: afslutningen af den første sats står i loggen.
    assert any(x.action == "UPDATE" and x.before["valid_to"] is None and x.after["valid_to"] == "2026-06-01"
               for x in log)


def test_tidligere_kostpris_vises_til_den_er_tastet_igen(db_session, klient, admin):
    m = Staff(navn="Kim", email="kim-sats@dinbogholder.dk", rolle="medarbejder", aktiv=True)
    db_session.add(m)
    db_session.flush()
    db_session.add(AuditLog(staff_id=m.id, handling="kostpris_flyttet",
                            detaljer={"kostpris_pr_time": "425.50", "gyldig_fra": "2026-10-18"}))
    db_session.flush()
    rad = [x for x in klient.get("/api/medarbejdere").json() if x["id"] == m.id][0]
    assert rad["tid"]["tidligere_kostpris"] == {"kostpris_pr_time": "425.50", "fra": "2026-10-18"}
    klient.post(f"/api/medarbejdere/{m.id}/ansaettelse", headers=H, json={"ansaettelse": "fastloennet"})
    rad = klient.post(f"/api/medarbejdere/{m.id}/sats", headers=H, json={
        "gyldig_fra": "2026-10-18", "kostpris_pr_time": "425.50", "produktive_timer_aar": "1450"}).json()
    assert rad["tid"]["tidligere_kostpris"] is None


# Invitation (bygherren 26.10.2026): velkomstmail ved oprettelse – og "Send invitation igen" fra listen.
def test_invitation_uden_mail_slaaet_til(db_session, klient):
    ny = klient.post("/api/medarbejdere", headers=H, json={"email": "ny-inv@dinbogholder.dk", "navn": "Ny"}).json()
    assert ny["invitation_sendt"]["status"] == "ikke_slaaet_til"     # oprettelsen lykkes alligevel
    raekke = [m for m in klient.get("/api/medarbejdere").json() if m["id"] == ny["id"]][0]
    assert raekke["invitation"]["status"] == "ikke_slaaet_til"
    svar = klient.post(f"/api/medarbejdere/{ny['id']}/invitation", headers=H)
    assert svar.status_code == 503 and "ikke slået til" in svar.json()["detail"]
    assert klient.post(f"/api/medarbejdere/{ny['id']}/invitation").status_code == 403   # mangler header


def test_invitation_sendes_og_kan_sendes_igen(db_session, klient, admin, monkeypatch):
    from app.mail import MailFejl
    from app.personale import invitation

    sendt = []
    monkeypatch.setattr(invitation, "mail_slaaet_til", lambda: True)
    monkeypatch.setattr(invitation, "send_mail", lambda til, emne, tekst: sendt.append((til, emne, tekst)))
    ny = klient.post("/api/medarbejdere", headers=H, json={"email": "Gen-Inv@dinbogholder.dk", "navn": "Gitte"}).json()
    assert ny["invitation_sendt"]["status"] == "sendt"
    til, emne, tekst = sendt[0]
    assert til == "gen-inv@dinbogholder.dk" and "adgang" in emne
    assert "Hej Gitte" in tekst and "Log ind med Microsoft" in tekst and "udløber ikke" in tekst
    assert klient.post(f"/api/medarbejdere/{ny['id']}/invitation", headers=H).status_code == 200
    assert len(sendt) == 2
    logs = db_session.scalars(select(AuditLog).where(AuditLog.handling == "medarbejder_invitation",
                                                     AuditLog.detaljer["medarbejder_id"].as_integer() == ny["id"])).all()
    assert [l.detaljer["status"] for l in logs] == ["sendt", "sendt"] and logs[0].detaljer["udfoert_af"] == admin.email

    def fejler(*a):
        raise MailFejl("Microsoft afviste mailen (status 403)")
    monkeypatch.setattr(invitation, "send_mail", fejler)
    svar = klient.post(f"/api/medarbejdere/{ny['id']}/invitation", headers=H)
    assert svar.status_code == 502 and "status 403" in svar.json()["detail"]
    # Lukket adgang -> ingen invitation.
    klient.post(f"/api/medarbejdere/{ny['id']}", headers=H, json={"aktiv": False})
    assert klient.post(f"/api/medarbejdere/{ny['id']}/invitation", headers=H).status_code == 409


# Bygherren 26.10.2026: timelønnede har ingen produktive timer (kostpris ud fra timelønnen); fastlønnede standard 1.400.
def test_produktive_timer_kun_for_fastloennede(db_session, klient):
    t = Staff(navn="Timo", email="timo-sats@dinbogholder.dk", rolle="medarbejder", aktiv=True)
    f = Staff(navn="Fie", email="fie-sats@dinbogholder.dk", rolle="medarbejder", aktiv=True)
    db_session.add_all([t, f])
    db_session.flush()
    klient.post(f"/api/medarbejdere/{t.id}/ansaettelse", headers=H, json={"ansaettelse": "timeloennet", "flexjob": False})
    klient.post(f"/api/medarbejdere/{f.id}/ansaettelse", headers=H, json={"ansaettelse": "fastloennet", "flexjob": False})
    sats = {"gyldig_fra": "2026-11-01", "kostpris_pr_time": "210"}
    svar = klient.post(f"/api/medarbejdere/{t.id}/sats", headers=H, json=sats | {"produktive_timer_aar": "1400"})
    assert svar.status_code == 422 and "timelønnen" in svar.json()["detail"]
    assert klient.post(f"/api/medarbejdere/{t.id}/sats", headers=H, json=sats).status_code == 200
    assert klient.get(f"/api/medarbejdere/{t.id}/satser").json()[0]["produktive_timer_aar"] is None
    # Fastlønnet uden tal -> standarden 1.400; et andet tal kan stadig angives.
    klient.post(f"/api/medarbejdere/{f.id}/sats", headers=H, json=sats | {"kostpris_pr_time": "390"})
    assert klient.get(f"/api/medarbejdere/{f.id}/satser").json()[0]["produktive_timer_aar"] == "1400.00"
    klient.post(f"/api/medarbejdere/{f.id}/sats", headers=H, json=sats | {"produktive_timer_aar": "1300"})
    assert klient.get(f"/api/medarbejdere/{f.id}/satser").json()[0]["produktive_timer_aar"] == "1300.00"


# Bygherren 26.10.2026: standarden følger normtiden – 1.400 × normtid / 37, afrundet til hele timer.
def test_produktive_timer_foelger_normtiden(db_session, klient):
    from decimal import Decimal

    from app.timeregistrering.stamdata import standard_produktive_timer

    assert standard_produktive_timer(Decimal(37)) == Decimal(1400)
    assert standard_produktive_timer(Decimal(20)) == Decimal(757)
    assert standard_produktive_timer(None) == standard_produktive_timer(Decimal(0)) == Decimal(1400)
    assert klient.get("/api/medarbejdere/standard-produktive-timer?normtid=20").json() == {"produktive_timer_aar": "757"}
    assert klient.get("/api/medarbejdere/standard-produktive-timer").json() == {"produktive_timer_aar": "1400"}
    d = Staff(navn="Dorte", email="dorte-sats@dinbogholder.dk", rolle="medarbejder", aktiv=True)
    db_session.add(d)
    db_session.flush()
    klient.post(f"/api/medarbejdere/{d.id}/ansaettelse", headers=H, json={"ansaettelse": "fastloennet", "flexjob": False})
    klient.post(f"/api/medarbejdere/{d.id}/sats", headers=H,
                json={"gyldig_fra": "2026-11-01", "normtid": "20", "kostpris_pr_time": "390"})
    assert klient.get(f"/api/medarbejdere/{d.id}/satser").json()[0]["produktive_timer_aar"] == "757.00"

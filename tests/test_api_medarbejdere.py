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
    assert handlinger == ["medarbejder_opret", "medarbejder_rolle", "medarbejder_deaktiver"]


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
def test_kostpris_kun_for_administratorer(db_session, klient):
    m = Staff(navn="Bo", email="bo-kost@dinbogholder.dk", rolle="medarbejder", aktiv=True)
    db_session.add(m)
    db_session.flush()
    app.dependency_overrides[login.nuvaerende_medarbejder] = lambda: m
    assert klient.post(f"/api/medarbejdere/{m.id}/kostpris", headers=H,
                       json={"kostpris_pr_time": "400.00", "gyldig_fra": "2026-01-01"}).status_code == 403
    assert klient.get(f"/api/medarbejdere/{m.id}/kostpriser").status_code == 403
    assert "kostpris" not in klient.get("/api/mig").text


def test_kostpris_med_historik_og_log(db_session, klient, admin):
    from datetime import date, timedelta

    m = Staff(navn="Bo", email="bo-kost2@dinbogholder.dk", rolle="medarbejder", aktiv=True)
    db_session.add(m)
    db_session.flush()
    url = f"/api/medarbejdere/{m.id}/kostpris"
    assert klient.post(url, json={"kostpris_pr_time": "400", "gyldig_fra": "2026-01-01"}).status_code == 403  # header
    assert klient.post(url, headers=H, json={"kostpris_pr_time": "-1", "gyldig_fra": "2026-01-01"}).status_code == 422
    svar = klient.post(url, headers=H, json={"kostpris_pr_time": "400.00", "gyldig_fra": "2026-01-01"}).json()
    assert (svar["kostpris_pr_time"], svar["kostpris_fra"]) == ("400.00", "2026-01-01")
    fremtid = (date.today() + timedelta(days=30)).isoformat()
    svar = klient.post(url, headers=H, json={"kostpris_pr_time": "450.00", "gyldig_fra": fremtid}).json()
    assert svar["kostpris_pr_time"] == "400.00"  # den nye gælder først senere
    assert svar["naeste_kostpris"] == {"kostpris_pr_time": "450.00", "fra": fremtid}
    # Tastefejl samme dato rettes (ikke en ny række)
    klient.post(url, headers=H, json={"kostpris_pr_time": "410.00", "gyldig_fra": "2026-01-01"})
    historik = klient.get(f"/api/medarbejdere/{m.id}/kostpriser").json()
    assert [(h["kostpris_pr_time"], h["fra"]) for h in historik] == [("450.00", fremtid), ("410.00", "2026-01-01")]
    log = db_session.scalars(select(AuditLog).where(AuditLog.handling == "medarbejder_kostpris")).all()
    assert [(l.detaljer["fra"], l.detaljer["til"]) for l in log] == [(None, "400.00"), (None, "450.00"), ("400.00", "410.00")]
    rad = [x for x in klient.get("/api/medarbejdere").json() if x["id"] == m.id][0]
    assert rad["kostpris_pr_time"] == "410.00"


def test_kostpriser_kan_ikke_slettes(db_session, admin):
    from datetime import date

    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError

    from app.personale.models import StaffCostRate

    db_session.add(StaffCostRate(staff_id=admin.id, hourly_cost=400, valid_from=date(2026, 1, 1), created_by=admin.id))
    db_session.flush()
    with pytest.raises(DBAPIError, match="kan ikke slettes"):
        with db_session.begin_nested():
            db_session.execute(text("DELETE FROM staff_cost_rates WHERE staff_id = :s"), {"s": admin.id})

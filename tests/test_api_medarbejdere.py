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

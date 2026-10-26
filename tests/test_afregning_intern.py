"""Afregningsformen 'intern' – vores eget firma (bygherren 26.10.2026): faktureres aldrig og indgår ikke i
omsætningen, men timerne er arbejdstid. Kun på interne kunder (kun_admin) – håndhævet i databasen."""

from datetime import date, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.api import login
from app.api.main import app
from app.kunder import kartotek
from app.kunder.models import Client
from app.personale.models import Staff
from app.timeregistrering import registrering, satser, stamdata
from app.timeregistrering.satser import ManglerSats

H = {"X-Afstemning": "1"}
MANDAG = date(2026, 10, 19)


@pytest.fixture
def admin(db_session):
    a = Staff(navn="Int Admin", email="int-admin@dinbogholder.dk", rolle="admin", aktiv=True)
    db_session.add(a)
    db_session.flush()
    return a


@pytest.fixture
def klient(db_session, admin):
    app.dependency_overrides[login.db] = lambda: db_session
    app.dependency_overrides[login.nuvaerende_medarbejder] = lambda: admin
    yield TestClient(app)
    app.dependency_overrides.clear()


def _kunde(db_session, kun_admin):
    k = Client(navn=f"Int {kun_admin}", kundenummer=f"INT-{kun_admin}", regnskabssystem="economic", status="aktiv",
               kun_admin=kun_admin)
    db_session.add(k)
    db_session.flush()
    return k


def test_oprettes_intern_og_bliver_kun_admin(klient, db_session):
    svar = klient.post("/api/kundekartotek", headers=H, json={
        "navn": "Int Eget Firma ApS", "kundenummer": "INT-EGET", "system": "economic", "startdato": "2026-10-01",
        "afregning": "intern", "abonnementspris": "999.00"})
    assert svar.status_code == 200, svar.text
    k = db_session.get(Client, svar.json()["id"])
    assert k.kun_admin is True
    # Intern har aldrig en pris – en indtastet pris smides væk.
    assert db_session.execute(text("SELECT time_afregning(:k, '2026-10-26'), time_abonnementspris(:k, '2026-10-26')"),
                              {"k": k.id}).one() == ("intern", None)
    raekke = [r for r in klient.get("/api/kundekartotek").json() if r["id"] == k.id][0]
    assert raekke["afregning"] == "intern"
    assert [r["navn"] for r in klient.get("/api/kundekartotek?afregning=intern").json()] == ["Int Eget Firma ApS"]
    # Kunden kan ikke længere gøres til en almindelig kunde hos Svend, mens den er intern.
    svar = klient.post(f"/api/kundekartotek/{k.id}/kun-admin", headers=H, json={"kun_admin": False})
    assert svar.status_code == 422 and "Intern" in svar.json()["detail"]


def test_kun_paa_interne_kunder(db_session, admin):
    almindelig = _kunde(db_session, False)
    with pytest.raises(ManglerSats, match="interne kunder"):
        satser.nye_vilkaar(db_session, almindelig.id, MANDAG, "intern", None, admin.id)
    # Databasen afviser det også, selv uden om koden.
    with pytest.raises(DBAPIError, match="interne kunder"), db_session.begin_nested():
        db_session.execute(text("INSERT INTO time_client_terms (client_id, valid_from, billing_model) "
                                "VALUES (:k, '2026-10-01', 'intern')"), {"k": almindelig.id})


def test_databasen_holder_kun_admin_paa_intern_kunde(db_session, admin):
    k = _kunde(db_session, True)
    satser.nye_vilkaar(db_session, k.id, MANDAG, "intern", None, admin.id)
    with pytest.raises(DBAPIError, match="skal forblive intern"), db_session.begin_nested():
        db_session.execute(text("UPDATE clients SET kun_admin = false WHERE id = :k"), {"k": k.id})
    with pytest.raises(DBAPIError, match="intern_uden_pris"), db_session.begin_nested():
        db_session.execute(text("UPDATE time_client_terms SET subscription_price = 100 WHERE client_id = :k"),
                           {"k": k.id})


def test_interne_timer_er_arbejdstid_men_faktureres_aldrig(db_session, admin):
    k = _kunde(db_session, True)
    satser.nye_vilkaar(db_session, k.id, MANDAG - timedelta(days=30), "intern", None, admin.id)
    e = stamdata.saet_ansaettelse(db_session, admin.id, "fastloennet", False, None)
    stamdata.ny_sats(db_session, e, MANDAG - timedelta(days=60), Decimal(37), Decimal(300), None, None, admin.id)
    raekker = registrering.gem_linje(db_session, e, admin.id, MANDAG, [],
                                     registrering.Linje("kundearbejde", k.id, None, False), {MANDAG: Decimal(2)})
    assert [(r.billing_model, r.hours, r.billable_separately) for r in raekker] == [("intern", Decimal(2), False)]
    with pytest.raises(registrering.RegistreringsFejl, match="faktureres aldrig"):
        registrering.gem_linje(db_session, e, admin.id, MANDAG, [],
                               registrering.Linje("kundearbejde", k.id, "Ekstra", True), {MANDAG: Decimal(1)})
    with pytest.raises(DBAPIError, match="intern_aldrig_faktureret"), db_session.begin_nested():
        db_session.execute(text("UPDATE time_entries SET billable_separately = true, description = 'x' WHERE id = :i"),
                           {"i": raekker[0].id})


def test_kartotek_opret_intern_uden_api(db_session, admin):
    k = kartotek.opret(db_session, admin, "Int Direkte", "INT-D", None, "economic", None, MANDAG, "intern", None)
    assert k.kun_admin is True

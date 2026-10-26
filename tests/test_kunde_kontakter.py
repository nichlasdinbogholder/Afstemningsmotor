"""Kundeoversigten, trin 3: kontaktpersoner (bygherren 26.10.2026). Alle ser; kun ansvarlig, assistent og
administratorer retter. Præcis én primær. Underskriver og "må kontaktes af" er uafhængige."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api import login
from app.api.main import app
from app.kunder.models import Client, Contact
from app.kunder.tabeller import KundeLog
from app.personale.models import Staff

H = {"X-Afstemning": "1"}


@pytest.fixture
def folk(db_session):
    ansvarlig = Staff(navn="Kon Ansvarlig", email="kon-a@dinbogholder.dk", rolle="medarbejder", aktiv=True)
    assistent = Staff(navn="Kon Assistent", email="kon-b@dinbogholder.dk", rolle="medarbejder", aktiv=True)
    anden = Staff(navn="Kon Anden", email="kon-c@dinbogholder.dk", rolle="medarbejder", aktiv=True)
    admin = Staff(navn="Kon Admin", email="kon-d@dinbogholder.dk", rolle="admin", aktiv=True)
    db_session.add_all([ansvarlig, assistent, anden, admin])
    db_session.flush()
    return ansvarlig, assistent, anden, admin


@pytest.fixture
def kunde(db_session, folk):
    k = Client(navn="Kon Tømrer ApS", kundenummer="KON-1", regnskabssystem="economic", status="aktiv",
               ansvarlig_medarbejder_id=folk[0].id, assistent_id=folk[1].id)
    db_session.add(k)
    db_session.flush()
    return k


@pytest.fixture
def klient(db_session, folk):
    app.dependency_overrides[login.db] = lambda: db_session
    app.dependency_overrides[login.nuvaerende_medarbejder] = lambda: folk[0]
    yield TestClient(app)
    app.dependency_overrides.clear()


def _som(m):
    app.dependency_overrides[login.nuvaerende_medarbejder] = lambda: m


def _opret(klient, kunde, **felter):
    return klient.post(f"/api/kundekort/{kunde.id}/kontakter", headers=H, json={"navn": "Hanne", **felter})


def test_foerste_kontakt_bliver_primaer_og_ny_primaer_afloeser(klient, kunde):
    a = _opret(klient, kunde, navn="Hanne", email="Hanne@Kunde.dk").json()
    assert (a["primaer"], a["email"]) == (True, "hanne@kunde.dk")
    b = _opret(klient, kunde, navn="Bo").json()
    assert b["primaer"] is False
    c = _opret(klient, kunde, navn="Carl", primaer=True).json()
    liste = klient.get(f"/api/kundekort/{kunde.id}/kontakter").json()
    assert [(k["navn"], k["primaer"]) for k in liste] == [("Carl", True), ("Bo", False), ("Hanne", False)]
    klient.post(f"/api/kundekort/kontakter/{b['id']}/primaer", headers=H)
    primaere = [k["navn"] for k in klient.get(f"/api/kundekort/{kunde.id}/kontakter").json() if k["primaer"]]
    assert primaere == ["Bo"] and c["id"]


def test_primaer_kan_ikke_arkiveres_mens_der_er_andre(klient, kunde, db_session):
    a = _opret(klient, kunde, navn="Hanne").json()
    b = _opret(klient, kunde, navn="Bo").json()
    svar = klient.post(f"/api/kundekort/kontakter/{a['id']}/arkiver", headers=H)
    assert svar.status_code == 422 and "primær" in svar.json()["detail"]
    assert klient.post(f"/api/kundekort/kontakter/{b['id']}/arkiver", headers=H).status_code == 200
    # Nu er Hanne den eneste – så må hun gerne arkiveres. Intet slettes.
    assert klient.post(f"/api/kundekort/kontakter/{a['id']}/arkiver", headers=H).status_code == 200
    assert klient.get(f"/api/kundekort/{kunde.id}/kontakter").json() == []
    assert len(klient.get(f"/api/kundekort/{kunde.id}/kontakter?arkiverede=true").json()) == 2
    assert db_session.scalars(select(Contact).where(Contact.client_id == kunde.id)).all()


def test_underskriver_og_kontaktes_af_er_uafhaengige(klient, kunde):
    k = _opret(klient, kunde, underskriver=True, maa_kontaktes_af="ansvarlig").json()
    assert (k["underskriver"], k["maa_kontaktes_af"]) == (True, "ansvarlig")
    rettet = klient.post(f"/api/kundekort/kontakter/{k['id']}", headers=H,
                         json={"navn": "Hanne", "underskriver": True, "maa_kontaktes_af": "alle"}).json()
    assert (rettet["underskriver"], rettet["maa_kontaktes_af"]) == (True, "alle")
    assert _opret(klient, kunde, maa_kontaktes_af="nogen").status_code == 422
    assert _opret(klient, kunde, email="ikke-en-mail").status_code == 422
    assert _opret(klient, kunde, navn="  ").status_code == 422


def test_alle_ser_men_kun_ansvarlig_assistent_admin_retter(klient, kunde, folk):
    k = _opret(klient, kunde, maa_kontaktes_af="ansvarlig").json()
    _som(folk[2])                                                     # anden medarbejder
    assert klient.get(f"/api/kundekort/{kunde.id}").json()["kan_rette"] is False
    liste = klient.get(f"/api/kundekort/{kunde.id}/kontakter").json()
    assert liste[0]["maa_kontaktes_af"] == "ansvarlig"               # ser kontakten – og at den er begrænset
    assert _opret(klient, kunde, navn="Ulovlig").status_code == 403
    assert klient.post(f"/api/kundekort/kontakter/{k['id']}/arkiver", headers=H).status_code == 403
    for m in (folk[1], folk[3]):                                     # assistent og administrator
        _som(m)
        assert klient.get(f"/api/kundekort/{kunde.id}").json()["kan_rette"] is True
        assert _opret(klient, kunde, navn=f"Af {m.navn}").status_code == 200
    assert klient.post(f"/api/kundekort/{kunde.id}/kontakter", json={"navn": "X"}).status_code == 403  # header


def test_alt_skrives_i_kundens_log(klient, kunde, folk, db_session):
    k = _opret(klient, kunde, navn="Hanne").json()
    klient.post(f"/api/kundekort/kontakter/{k['id']}", headers=H, json={"navn": "Hanne Jensen"})
    log = db_session.scalars(select(KundeLog).where(KundeLog.client_id == kunde.id).order_by(KundeLog.id)).all()
    assert [(r.event, r.detail, r.actor_staff_id) for r in log] == [
        ("kontakt_oprettet", "Hanne (primær)", folk[0].id), ("kontakt_rettet", "Hanne Jensen", folk[0].id)]


def test_kundekortets_hoved(klient, kunde):
    h = klient.get(f"/api/kundekort/{kunde.id}").json()
    assert (h["navn"], h["status"], h["ansvarlig"], h["assistent"]) == (
        "Kon Tømrer ApS", "Aktiv", "Kon Ansvarlig", "Kon Assistent")
    assert klient.get("/api/kundekort/999999").status_code == 404

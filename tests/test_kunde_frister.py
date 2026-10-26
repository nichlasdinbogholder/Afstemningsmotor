"""Fanen Frister (bygherren 26.10.2026): moms månedligt/kvartalsvis/halvårligt, lønsumsafgift efter Skats metode 1-4 –
eller ikke tilmeldt. KUN administratorer retter (det styrer planlægningen)."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from app.api import login
from app.api.main import app
from app.kunder.models import Client
from app.kunder.tabeller import KundeLog, KundeObligations
from app.personale.models import Staff

H = {"X-Afstemning": "1"}


@pytest.fixture
def folk(db_session):
    a = Staff(navn="Fri Ansvarlig", email="fri-a@dinbogholder.dk", rolle="medarbejder", aktiv=True)
    admin = Staff(navn="Fri Admin", email="fri-admin@dinbogholder.dk", rolle="admin", aktiv=True)
    db_session.add_all([a, admin])
    db_session.flush()
    return a, admin


@pytest.fixture
def kunde(db_session, folk):
    k = Client(navn="Fri Tømrer ApS", kundenummer="FRI-1", regnskabssystem="economic", status="aktiv",
               ansvarlig_medarbejder_id=folk[0].id)
    db_session.add(k)
    db_session.flush()
    return k


@pytest.fixture
def klient(db_session, folk):
    app.dependency_overrides[login.db] = lambda: db_session
    app.dependency_overrides[login.nuvaerende_medarbejder] = lambda: folk[1]
    yield TestClient(app)
    app.dependency_overrides.clear()


def _perioder(svar):
    return {f["kode"]: f["periode"] for f in svar["frister"]}


def test_valgene_er_skats_ordninger(klient, kunde):
    d = klient.get(f"/api/kundekort/{kunde.id}/frister").json()
    assert _perioder(d) == {"moms": None, "loensumsafgift": None, "aarsafslutning": None}
    assert [v["tekst"] for v in d["frister"][0]["valg"]] == ["Månedligt", "Kvartalsvis", "Halvårligt", "Ikke tilmeldt"]
    assert [v["tekst"] for v in d["frister"][1]["valg"]] == [
        "Metode 1", "Metode 2", "Metode 3", "Metode 4", "Ikke tilmeldt"]
    assert [v["tekst"] for v in d["frister"][2]["valg"]] == ["Årsregnskab + oplysningsskema", "Årsopgørelse"]


def test_admin_vaelger_og_det_logges(klient, kunde, db_session):
    klient.post(f"/api/kundekort/{kunde.id}/frister", headers=H, json={"frist": "moms", "periode": "kvartal"})
    d = klient.post(f"/api/kundekort/{kunde.id}/frister", headers=H,
                    json={"frist": "loensumsafgift", "periode": "metode_2"}).json()
    assert _perioder(d) == {"moms": "kvartal", "loensumsafgift": "metode_2", "aarsafslutning": None}
    o = db_session.get(KundeObligations, kunde.id)
    assert (o.vat_period, o.loensumsafgift_metode) == ("kvartal", "metode_2")
    log = db_session.scalars(select(KundeLog.detail).where(KundeLog.client_id == kunde.id).order_by(KundeLog.id)).all()
    assert log == ["Moms: Ikke valgt → Kvartalsvis", "Lønsumsafgift: Ikke valgt → Metode 2"]


def test_kun_administratorer_retter(klient, kunde, folk):
    assert klient.post(f"/api/kundekort/{kunde.id}/frister", headers=H,
                       json={"frist": "moms", "periode": "aar"}).status_code == 422        # ingen årlig moms
    app.dependency_overrides[login.nuvaerende_medarbejder] = lambda: folk[0]         # selv den ansvarlige
    assert klient.get(f"/api/kundekort/{kunde.id}/frister").json()["kan_rette"] is False
    svar = klient.post(f"/api/kundekort/{kunde.id}/frister", headers=H, json={"frist": "moms", "periode": "kvartal"})
    assert svar.status_code == 403 and "administratorer" in svar.json()["detail"]


def test_databasen_tillader_kun_faste_vaerdier(db_session, kunde):
    with pytest.raises(IntegrityError), db_session.begin_nested():
        db_session.execute(text("INSERT INTO kunde_obligations (client_id, vat_period) VALUES (:k, 'aar')"),
                           {"k": kunde.id})
    with pytest.raises(IntegrityError), db_session.begin_nested():
        db_session.execute(text("INSERT INTO kunde_obligations (client_id, loensumsafgift_metode) "
                                "VALUES (:k, 'kvartal')"), {"k": kunde.id})

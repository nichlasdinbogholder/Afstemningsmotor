"""Fanen Frister (bygherren 26.10.2026): moms og lønsumsafgift indberettes hver især månedligt, kvartalsvis,
halvårligt eller årligt – eller kunden er ikke tilmeldt. Kun ansvarlig, assistent og admin retter."""

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
    b = Staff(navn="Fri Anden", email="fri-b@dinbogholder.dk", rolle="medarbejder", aktiv=True)
    db_session.add_all([a, b])
    db_session.flush()
    return a, b


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
    app.dependency_overrides[login.nuvaerende_medarbejder] = lambda: folk[0]
    yield TestClient(app)
    app.dependency_overrides.clear()


def _perioder(svar):
    return {f["kode"]: f["periode"] for f in svar["frister"]}


def test_ikke_valgt_til_at_begynde_med_og_alle_fire_perioder(klient, kunde):
    d = klient.get(f"/api/kundekort/{kunde.id}/frister").json()
    assert _perioder(d) == {"moms": None, "loensumsafgift": None}
    assert [v["tekst"] for v in d["frister"][0]["valg"]] == [
        "Månedligt", "Kvartalsvis", "Halvårligt", "Årligt", "Ikke tilmeldt"]


def test_vaelg_hver_for_sig_og_log(klient, kunde, db_session, folk):
    klient.post(f"/api/kundekort/{kunde.id}/frister", headers=H, json={"frist": "moms", "periode": "kvartal"})
    d = klient.post(f"/api/kundekort/{kunde.id}/frister", headers=H,
                    json={"frist": "loensumsafgift", "periode": "aar"}).json()
    assert _perioder(d) == {"moms": "kvartal", "loensumsafgift": "aar"}
    klient.post(f"/api/kundekort/{kunde.id}/frister", headers=H, json={"frist": "moms", "periode": "halvaar"})
    o = db_session.get(KundeObligations, kunde.id)
    assert (o.vat_period, o.loensumsafgift_period) == ("halvaar", "aar")
    log = db_session.scalars(select(KundeLog.detail).where(KundeLog.client_id == kunde.id).order_by(KundeLog.id)).all()
    assert log == ["Moms: Ikke valgt → Kvartalsvis", "Lønsumsafgift: Ikke valgt → Årligt",
                   "Moms: Kvartalsvis → Halvårligt"]


def test_forkerte_valg_og_rettigheder(klient, kunde, folk):
    assert klient.post(f"/api/kundekort/{kunde.id}/frister", headers=H,
                       json={"frist": "moms", "periode": "ugentlig"}).status_code == 422
    assert klient.post(f"/api/kundekort/{kunde.id}/frister", headers=H,
                       json={"frist": "skat", "periode": "aar"}).status_code == 422
    app.dependency_overrides[login.nuvaerende_medarbejder] = lambda: folk[1]
    assert klient.get(f"/api/kundekort/{kunde.id}/frister").json()["kan_rette"] is False
    assert klient.post(f"/api/kundekort/{kunde.id}/frister", headers=H,
                       json={"frist": "moms", "periode": "aar"}).status_code == 403


def test_databasen_tillader_kun_faste_vaerdier(db_session, kunde):
    with pytest.raises(IntegrityError), db_session.begin_nested():
        db_session.execute(text("INSERT INTO kunde_obligations (client_id, loensumsafgift_period) "
                                "VALUES (:k, 'ugentlig')"), {"k": kunde.id})

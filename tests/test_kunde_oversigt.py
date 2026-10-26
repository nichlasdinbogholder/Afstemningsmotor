"""Kundeoversigten, trin 4: listevisning med "mine kunder", filtre og sortering efter nærmeste frist (spec-test 35)."""

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app.api import login
from app.api.main import app
from app.kunder.models import Client
from app.kunder.tabeller import KundeAml, KundeLedger, KundeTask, KundeTaskType
from app.personale.models import Staff
from app.timeregistrering import satser

I_DAG = date.today()


@pytest.fixture
def folk(db_session):
    mig = Staff(navn="Ovs Mig", email="ovs-mig@dinbogholder.dk", rolle="medarbejder", aktiv=True)
    anden = Staff(navn="Ovs Anden", email="ovs-anden@dinbogholder.dk", rolle="medarbejder", aktiv=True)
    admin = Staff(navn="Ovs Admin", email="ovs-admin@dinbogholder.dk", rolle="admin", aktiv=True)
    db_session.add_all([mig, anden, admin])
    db_session.flush()
    return mig, anden, admin


@pytest.fixture
def kunder(db_session, folk):
    mig, anden, admin = folk
    a = Client(navn="Ovs A ApS", kundenummer="OVS-A", regnskabssystem="economic", status="aktiv",
               ansvarlig_medarbejder_id=mig.id)
    b = Client(navn="Ovs B ApS", kundenummer="OVS-B", regnskabssystem="dinero", status="pause",
               ansvarlig_medarbejder_id=anden.id, assistent_id=mig.id)
    c = Client(navn="Ovs C ApS", kundenummer="OVS-C", regnskabssystem="economic", status="aktiv",
               ansvarlig_medarbejder_id=anden.id)
    db_session.add_all([a, b, c])
    db_session.flush()
    satser.nye_vilkaar(db_session, a.id, I_DAG - timedelta(days=30), "abonnement", 1500, admin.id)
    satser.nye_vilkaar(db_session, c.id, I_DAG - timedelta(days=30), "timeafregning", None, admin.id)
    db_session.add_all([KundeLedger(client_id=a.id, system="economic", label="e-conomic", status="aktiv"),
                        KundeLedger(client_id=c.id, system="economic", label="e-conomic", status="aktiv"),
                        KundeLedger(client_id=c.id, system="economic", label="Datter", status="afvist")])
    db_session.add(KundeAml(client_id=a.id, risk_level="lav", assessed_on=I_DAG))
    tt = KundeTaskType(code="ovs_test", name="Ovs test", workday_number=5)
    db_session.add(tt)
    db_session.flush()
    foerste = I_DAG.replace(day=1)
    db_session.add_all([KundeTask(client_id=c.id, task_type_id=tt.id, period=foerste, due_date=I_DAG - timedelta(days=2)),
                        KundeTask(client_id=a.id, task_type_id=tt.id, period=foerste, due_date=I_DAG + timedelta(days=5))])
    db_session.flush()
    return a, b, c


@pytest.fixture
def klient(db_session, folk):
    app.dependency_overrides[login.db] = lambda: db_session
    app.dependency_overrides[login.nuvaerende_medarbejder] = lambda: folk[0]
    yield TestClient(app)
    app.dependency_overrides.clear()


def _navne(klient, **q):
    return [r["navn"] for r in klient.get("/api/kundekartotek", params=q).json() if r["navn"].startswith("Ovs")]


def test_mine_kunder_er_ansvarlig_eller_assistent(klient, kunder):
    assert _navne(klient, mine=True) == ["Ovs A ApS", "Ovs B ApS"]        # A: ansvarlig, B: assistent


def test_sorteret_efter_naermeste_frist(klient, kunder):
    assert _navne(klient) == ["Ovs C ApS", "Ovs A ApS", "Ovs B ApS"]     # forfalden, om 5 dage, ingen frist
    c = [r for r in klient.get("/api/kundekartotek").json() if r["navn"] == "Ovs C ApS"][0]
    assert (c["dage_til_frist"], c["forbindelse"], c["forbindelse_tekst"]) == (-2, "afvist", "Afvist")


def test_filtrene(klient, kunder, folk):
    assert _navne(klient, ansvarlig_id=folk[1].id) == ["Ovs C ApS", "Ovs B ApS"]
    assert _navne(klient, afregning="abonnement") == ["Ovs A ApS"]
    assert _navne(klient, system="dinero") == ["Ovs B ApS"]
    assert _navne(klient, status="pause") == ["Ovs B ApS"]
    assert _navne(klient, forfaldne=True) == ["Ovs C ApS"]
    assert _navne(klient, doede_forbindelser=True) == ["Ovs C ApS"]   # én af to aftaler er afvist
    assert _navne(klient, mangler_hvidvask=True) == ["Ovs C ApS", "Ovs B ApS"]
    assert _navne(klient, mine=True, system="economic") == ["Ovs A ApS"]


def test_emner_og_arkiverede_er_ikke_med(klient, kunder, db_session):
    db_session.add_all([Client(navn="Ovs Emne", kundenummer="OVS-E", regnskabssystem="economic", status="emne"),
                        Client(navn="Ovs Opsagt", kundenummer="OVS-O", regnskabssystem="economic", status="opsagt")])
    db_session.flush()
    assert "Ovs Emne" not in _navne(klient) and "Ovs Opsagt" not in _navne(klient)
    assert "Ovs Opsagt" in [r["navn"] for r in klient.get("/api/kundekartotek?arkiverede=true").json()]

"""Kundeoversigten, trin 4: listevisning med "mine kunder", filtre og sortering efter navn (spec-test 35)."""

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app.api import login
from app.api.main import app
from app.kunder.models import Client
from app.kunder.tabeller import KundeLedger, KundeTask, KundeTaskType
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


def test_sorteret_efter_navn(klient, kunder):
    assert _navne(klient) == ["Ovs A ApS", "Ovs B ApS", "Ovs C ApS"]
    c = [r for r in klient.get("/api/kundekartotek").json() if r["navn"] == "Ovs C ApS"][0]
    assert (c["forbindelse"], c["forbindelse_tekst"]) == ("afvist", "Afvist")


def test_filtrene(klient, kunder, folk):
    assert _navne(klient, ansvarlig_id=folk[1].id) == ["Ovs B ApS", "Ovs C ApS"]
    assert _navne(klient, afregning="abonnement") == ["Ovs A ApS"]
    assert _navne(klient, system="dinero") == ["Ovs B ApS"]
    assert _navne(klient, status="pause") == ["Ovs B ApS"]
    assert _navne(klient, doede_forbindelser=True) == ["Ovs C ApS"]   # én af to aftaler er afvist
    assert _navne(klient, mine=True, system="economic") == ["Ovs A ApS"]


def test_emner_og_arkiverede_er_ikke_med(klient, kunder, db_session):
    db_session.add_all([Client(navn="Ovs Emne", kundenummer="OVS-E", regnskabssystem="economic", status="emne"),
                        Client(navn="Ovs Opsagt", kundenummer="OVS-O", regnskabssystem="economic", status="opsagt")])
    db_session.flush()
    assert "Ovs Emne" not in _navne(klient) and "Ovs Opsagt" not in _navne(klient)
    assert "Ovs Opsagt" in [r["navn"] for r in klient.get("/api/kundekartotek?arkiverede=true").json()]


# Bygherren 26.10.2026: månedens status = "x af y" af denne måneds opgaver; dækningsgrad KUN for abonnementskunder,
# seneste hele måned: (abonnement + særskilte timer × salgspris − timer × kostpris) / indtægt.
def test_maanedens_status_bruges_i_controlling_ikke_i_oversigten(klient, kunder, db_session):
    from datetime import datetime, timezone

    from app.kunder.oversigt import maanedens_status

    a = kunder[0]
    tt2 = KundeTaskType(code="ovs_test2", name="Ovs test 2", workday_number=10)
    db_session.add(tt2)
    db_session.flush()
    db_session.add(KundeTask(client_id=a.id, task_type_id=tt2.id, period=I_DAG.replace(day=1), due_date=I_DAG,
                             completed_at=datetime.now(timezone.utc)))
    db_session.flush()
    assert maanedens_status(db_session, [a.id], I_DAG) == {a.id: (1, 2)}
    raekke = [r for r in klient.get("/api/kundekartotek").json() if r["navn"] == "Ovs A ApS"][0]
    assert "maanedens_opgaver" not in raekke and "naeste_frist" not in raekke


def test_daekningsgrad_kun_for_abonnement(klient, kunder, folk, db_session):
    from decimal import Decimal

    from app.kunder.oversigt import daekningsgrader, seneste_hele_maaned
    from app.timeregistrering import registrering, stamdata

    fra, _ = seneste_hele_maaned(I_DAG)
    mig, anden, admin = folk
    satser.ny_salgspris(db_session, fra - timedelta(days=60), Decimal(900))
    e = stamdata.saet_ansaettelse(db_session, mig.id, "fastloennet", False, None)
    stamdata.ny_sats(db_session, e, fra - timedelta(days=60), Decimal(37), Decimal(300), None, None, admin.id)
    a, _, c = kunder
    satser.nye_vilkaar(db_session, a.id, fra - timedelta(days=60), "abonnement", Decimal(2000), admin.id)
    registrering.gem_linje(db_session, e, mig.id, fra, [], registrering.Linje("kundearbejde", a.id, None, False),
                           {fra: Decimal(2)})
    registrering.gem_linje(db_session, e, mig.id, fra, [],
                           registrering.Linje("kundearbejde", a.id, "Ekstra opgave", True), {fra: Decimal(1)})
    # Bruges i ledervisningen (trin 18) – ikke i Kundeoversigten.
    dg = daekningsgrader(db_session, [a.id, c.id], I_DAG)
    # (2.000 + 1 × 900 − 3 × 300) / 2.900 = 68,97 %
    assert dg[a.id] == {"procent": "69.0", "tekst": None}
    assert c.id not in dg                                       # timeafregning – ingen dækningsgrad
    assert "daekningsgrad" not in klient.get("/api/kundekartotek").json()[0]
    # En medarbejder uden kostpris -> "mangler sats" – aldrig et gættet tal.
    e2 = stamdata.saet_ansaettelse(db_session, anden.id, "fastloennet", False, None)
    registrering.gem_linje(db_session, e2, anden.id, fra, [], registrering.Linje("kundearbejde", a.id, None, False),
                           {fra: Decimal(1)})
    assert daekningsgrader(db_session, [a.id], I_DAG)[a.id] == {"procent": None, "tekst": "mangler sats"}

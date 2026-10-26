"""Kundekartoteket: Kundeoversigt og Arkiverede kunder (bygherren 23.10.2026). Kun administratorer ændrer."""

from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from app.api import login
from app.api.main import app
from app.audit.models import AuditLog
from app.kunder.models import Client
from app.personale.models import Staff
from app.timeregistrering import stamdata

H = {"X-Afstemning": "1"}
NY = {"navn": "Kartotek Tømrer ApS", "kundenummer": "KART-1", "cvr": "12345678", "system": "economic",
      "startdato": "2026-11-01", "afregning": "abonnement", "abonnementspris": "1800.00"}


@pytest.fixture
def folk(db_session):
    a = Staff(navn="Kartotek Admin", email="kartotek-admin@dinbogholder.dk", rolle="admin", aktiv=True)
    m = Staff(navn="Kartotek Medarbejder", email="kartotek-med@dinbogholder.dk", rolle="medarbejder", aktiv=True)
    db_session.add_all([a, m])
    db_session.flush()
    return a, m


@pytest.fixture
def klient(db_session, folk):
    app.dependency_overrides[login.db] = lambda: db_session
    app.dependency_overrides[login.nuvaerende_medarbejder] = lambda: folk[0]
    yield TestClient(app)
    app.dependency_overrides.clear()


def _som(medarbejder):
    app.dependency_overrides[login.nuvaerende_medarbejder] = lambda: medarbejder


def _arkiver(klient, kid, aarsag="engangsopgave", dato="2027-03-31"):
    return klient.post(f"/api/kundekartotek/{kid}/arkiver", headers=H, json={"aarsag": aarsag, "ophoersdato": dato})


def test_opret_med_startdato_og_afregningsform(db_session, klient, folk):
    svar = klient.post("/api/kundekartotek", headers=H, json={**NY, "ansvarlig_id": folk[1].id})
    assert svar.status_code == 200, svar.text
    k = db_session.get(Client, svar.json()["id"])
    assert (k.status, k.startdato, k.ansvarlig_medarbejder_id) == ("aktiv", date(2026, 11, 1), folk[1].id)
    # Afregningsformen gælder fra startdatoen – ikke før.
    assert db_session.scalar(text("SELECT time_afregning(:k, '2026-11-01')"), {"k": k.id}) == "abonnement"
    assert db_session.scalar(text("SELECT time_afregning(:k, '2026-10-31')"), {"k": k.id}) is None
    raekke = [r for r in klient.get("/api/kundekartotek").json() if r["id"] == k.id][0]
    assert (raekke["startdato"], raekke["system"], raekke["ansvarlig"]) == ("2026-11-01", "e-conomic",
                                                                            "Kartotek Medarbejder")
    # Samarbejdet er ikke begyndt endnu – oversigten viser afregningen fra startdatoen.
    assert (raekke["afregning"], raekke["abonnementspris"]) == ("abonnement", "1800.00")
    log = db_session.scalars(select(AuditLog).where(AuditLog.handling == "kunde_oprettet",
                                                    AuditLog.client_id == k.id)).one()
    assert log.detaljer["udfoert_af"] == folk[0].email and log.detaljer["startdato"] == "2026-11-01"


def test_opret_afviser_forkerte_oplysninger(klient):
    uden_start = {k: v for k, v in NY.items() if k != "startdato"}
    assert klient.post("/api/kundekartotek", headers=H, json=uden_start).status_code == 422
    assert klient.post("/api/kundekartotek", headers=H, json={**NY, "abonnementspris": None}).status_code == 422
    assert klient.post("/api/kundekartotek", headers=H, json={**NY, "afregning": "gratis"}).status_code == 422
    assert klient.post("/api/kundekartotek", headers=H, json={**NY, "cvr": "123"}).status_code == 422
    assert klient.post("/api/kundekartotek", json=NY).status_code == 403                     # mangler header
    assert klient.post("/api/kundekartotek", headers=H, json=NY).status_code == 200
    dublet = klient.post("/api/kundekartotek", headers=H, json={**NY, "kundenummer": "KART-2"})
    assert dublet.status_code == 422 and "findes allerede" in dublet.json()["detail"]


def test_arkiver_og_genaktiver(db_session, klient, folk):
    kid = klient.post("/api/kundekartotek", headers=H, json=NY).json()["id"]
    assert _arkiver(klient, kid, aarsag="gratis").status_code == 422                          # ukendt årsag
    for_tidligt = _arkiver(klient, kid, dato="2026-10-31")
    assert for_tidligt.status_code == 422 and "før startdatoen" in for_tidligt.json()["detail"]
    assert _arkiver(klient, kid, aarsag="egen_bogholder").json()["status"] == "opsagt"
    assert _arkiver(klient, kid).status_code == 422                                           # allerede arkiveret

    assert kid not in [r["id"] for r in klient.get("/api/kundekartotek").json()]
    arkiv = [r for r in klient.get("/api/kundekartotek?arkiverede=true").json() if r["id"] == kid][0]
    assert (arkiv["aarsag"], arkiv["ophoersdato"], arkiv["arkiveret_af"]) == (
        "Har ansat egen bogholder", "2027-03-31", "Kartotek Admin")

    assert klient.post(f"/api/kundekartotek/{kid}/genaktiver", headers=H).json()["status"] == "aktiv"
    k = db_session.get(Client, kid)
    assert (k.opsagt_dato, k.arkiv_aarsag, k.arkiveret_af) == (None, None, None)
    handlinger = db_session.scalars(select(AuditLog.handling).where(AuditLog.client_id == kid)
                                    .order_by(AuditLog.id)).all()
    assert handlinger == ["kunde_oprettet", "kunde_arkiveret", "kunde_genaktiveret"]
    genaktiveret = db_session.scalars(select(AuditLog).where(AuditLog.client_id == kid,
                                                             AuditLog.handling == "kunde_genaktiveret")).one()
    assert genaktiveret.detaljer["tidligere_aarsag"] == "egen_bogholder"


def test_medarbejdere_kan_se_men_ikke_aendre(klient, folk):
    kid = klient.post("/api/kundekartotek", headers=H, json=NY).json()["id"]
    _som(folk[1])
    assert klient.get("/api/kundekartotek").status_code == 200
    assert klient.get("/api/kundekartotek/valg").status_code == 200
    assert klient.post("/api/kundekartotek", headers=H, json={**NY, "kundenummer": "KART-9", "cvr": None}
                       ).status_code == 403
    assert _arkiver(klient, kid).status_code == 403
    assert klient.post(f"/api/kundekartotek/{kid}/genaktiver", headers=H).status_code == 403


def test_valg_har_de_fire_aarsager(klient):
    assert [a["navn"] for a in klient.get("/api/kundekartotek/valg").json()["aarsager"]] == [
        "Prisen for høj", "Engangsopgave", "Uoverensstemmelse", "Har ansat egen bogholder"]


def test_arkiveret_kunde_kan_ikke_vaelges_i_timeregistreringen(db_session, klient, folk):
    kid = klient.post("/api/kundekartotek", headers=H, json=NY).json()["id"]
    stamdata.saet_ansaettelse(db_session, folk[1].id, "fastloennet", False, None)
    _som(folk[1])
    assert kid in [k["id"] for k in klient.get("/api/tid/uge?dato=2026-11-02").json()["kunder"]]
    _som(folk[0])
    _arkiver(klient, kid)
    _som(folk[1])
    assert kid not in [k["id"] for k in klient.get("/api/tid/uge?dato=2026-11-02").json()["kunder"]]
    svar = klient.post("/api/tid/linje", headers=H, json={
        "dato": "2026-11-02", "ids": [], "opgavetype": "kundearbejde", "kunde_id": kid, "beskrivelse": None,
        "saerskilt": False, "timer": {"2026-11-02": "1"}})
    assert svar.status_code == 422


def test_databasen_kraever_opsagt_for_aarsag(db_session, folk):
    k = Client(navn="Kartotek DB ApS", kundenummer="KART-DB", regnskabssystem="economic", status="aktiv",
               arkiv_aarsag="engangsopgave")
    db_session.add(k)
    with pytest.raises(Exception, match="arkiv_aarsag_kun_paa_opsagt"):
        db_session.flush()
    db_session.rollback()


def test_skift_ansvarlig_styrer_svend(db_session, klient, folk):
    from app.assistent.samtale import er_tilknyttet

    kid = klient.post("/api/kundekartotek", headers=H, json=NY).json()["id"]
    assert not er_tilknyttet(db_session, folk[1], kid)
    assert klient.post(f"/api/kundekartotek/{kid}/ansvarlig", headers=H, json={"ansvarlig_id": folk[1].id}
                       ).status_code == 200
    assert er_tilknyttet(db_session, folk[1], kid)
    log = db_session.scalars(select(AuditLog).where(AuditLog.client_id == kid,
                                                    AuditLog.handling == "kunde_ansvarlig")).one()
    assert (log.detaljer["fra_staff_id"], log.detaljer["til_staff_id"]) == (None, folk[1].id)
    _arkiver(klient, kid)
    assert not er_tilknyttet(db_session, folk[1], kid)          # arkiveret = ingen adgang hos Svend
    _som(folk[1])
    assert klient.post(f"/api/kundekartotek/{kid}/ansvarlig", headers=H, json={"ansvarlig_id": None}
                       ).status_code == 403


def test_kun_admin_hos_svend(db_session, klient, folk):
    kid = klient.post("/api/kundekartotek", headers=H, json=NY).json()["id"]
    assert [r["kun_admin"] for r in klient.get("/api/kundekartotek").json() if r["id"] == kid] == [False]
    assert klient.post(f"/api/kundekartotek/{kid}/kun-admin", headers=H, json={"kun_admin": True}
                       ).json()["kun_admin"] is True
    log = db_session.scalars(select(AuditLog).where(AuditLog.client_id == kid,
                                                    AuditLog.handling == "kunde_kun_admin")).one()
    assert log.detaljer == {"udfoert_af": folk[0].email, "kun_admin": True}
    _som(folk[1])
    assert klient.post(f"/api/kundekartotek/{kid}/kun-admin", headers=H, json={"kun_admin": False}
                       ).status_code == 403

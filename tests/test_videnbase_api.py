"""Svends videnbase, trin 7: alle kan læse, kun administratorer kan rette, alt logges, satser kræver værdi."""

from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api import login
from app.api.main import app
from app.assistent.models import KbDocument
from app.audit.models import AuditLog
from app.personale.models import Staff

H = {"X-Afstemning": "1"}
# Opdigtet tal – testene handler om reglerne, ikke om den rigtige sats.
SATS = {"titel": "Kilometersats (test)", "kategori": "satser", "tekst": "Sats for kørsel i egen bil.",
        "vaerdi": "1.2345", "enhed": "kr/km", "gyldig_fra": "2026-01-01", "gyldig_til": "2026-12-31",
        "kilde": "https://skat.dk/eksempel", "kontrol_kilde": "https://skat.dk/eksempel",
        "kontrol_hint": "Tallet efter 'over 20.000 km'"}


@pytest.fixture
def admin(db_session):
    a = Staff(navn="Admin", email="admin-kb@dinbogholder.dk", rolle="admin", aktiv=True)
    db_session.add(a)
    db_session.flush()
    return a


@pytest.fixture
def elev(db_session):
    m = Staff(navn="Elev", email="elev-kb@dinbogholder.dk", rolle="medarbejder", aktiv=True)
    db_session.add(m)
    db_session.flush()
    return m


@pytest.fixture
def klient(db_session, admin):
    app.dependency_overrides[login.db] = lambda: db_session
    app.dependency_overrides[login.nuvaerende_medarbejder] = lambda: admin
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_uden_login_401(db_session):
    app.dependency_overrides[login.db] = lambda: db_session
    try:
        assert TestClient(app).get("/api/v1/kb/documents").status_code == 401
    finally:
        app.dependency_overrides.clear()


def test_admin_opretter_sats_som_er_bekraeftet_og_logget(db_session, klient, admin):
    svar = klient.post("/api/v1/kb/documents", headers=H, json=SATS)
    assert svar.status_code == 200, svar.text
    d = svar.json()
    assert (d["vaerdi"], d["enhed"], d["status"], d["rettet_af"]) == ("1.2345", "kr/km", "active", admin.email)
    assert d["senest_bekraeftet"] is not None
    log = db_session.scalars(select(AuditLog).where(AuditLog.handling == "kb_oprettet")).one()
    assert log.detaljer["dokument_id"] == d["id"] and log.detaljer["til"]["value"] == "1.2345"


def test_medarbejder_kan_laese_men_ikke_rette(db_session, klient, elev):
    dok = klient.post("/api/v1/kb/documents", headers=H, json=SATS).json()
    app.dependency_overrides[login.nuvaerende_medarbejder] = lambda: elev
    assert dok["id"] in [d["id"] for d in klient.get("/api/v1/kb/documents").json()]
    assert klient.get(f"/api/v1/kb/documents/{dok['id']}").json()["tekst"] == SATS["tekst"]
    assert klient.post("/api/v1/kb/documents", headers=H, json=SATS).status_code == 403
    assert klient.post(f"/api/v1/kb/documents/{dok['id']}", headers=H, json=SATS).status_code == 403


def test_kraever_header(klient):
    assert klient.post("/api/v1/kb/documents", json=SATS).status_code == 403


@pytest.mark.parametrize("aendring, besked", [
    ({"vaerdi": None}, "værdi og enhed"),
    ({"enhed": " "}, "værdi og enhed"),
    ({"kategori": "jura"}, "Ukendt kategori"),
    ({"status": "superseded"}, "aktiv eller kladde"),
    ({"kilde": "skat.dk"}, "http"),
    ({"gyldig_til": "2025-12-31"}, "før"),
])
def test_ugyldige_dokumenter_afvises(klient, aendring, besked):
    svar = klient.post("/api/v1/kb/documents", headers=H, json={**SATS, **aendring})
    assert svar.status_code == 422 and besked in svar.json()["detail"]


def test_rettelse_logges_med_fra_og_til(db_session, klient):
    dok = klient.post("/api/v1/kb/documents", headers=H, json=SATS).json()
    svar = klient.post(f"/api/v1/kb/documents/{dok['id']}", headers=H, json={**SATS, "vaerdi": "1.5000"})
    assert svar.status_code == 200 and svar.json()["vaerdi"] == "1.5000"
    log = db_session.scalars(select(AuditLog).where(AuditLog.handling == "kb_aendret")).one()
    assert log.detaljer["aendringer"]["value"] == {"fra": "1.2345", "til": "1.5000"}


def test_andre_kategorier_faar_ingen_satsfelter(klient):
    d = klient.post("/api/v1/kb/documents", headers=H, json={**SATS, "kategori": "moms", "titel": "Byggeri for egen regning"}).json()
    assert (d["vaerdi"], d["enhed"], d["kontrol_kilde"], d["senest_bekraeftet"]) == (None, None, None, None)


def test_afloest_dokument_kan_ikke_rettes(db_session, klient):
    d = KbDocument(title="Gammel sats", category="satser", body="x", value=1, unit="kr", status="superseded",
                   valid_from=date(2025, 1, 1), valid_to=date(2025, 12, 31))
    db_session.add(d)
    db_session.flush()
    assert klient.post(f"/api/v1/kb/documents/{d.id}", headers=H, json=SATS).status_code == 409


def test_filter_paa_kategori(klient):
    klient.post("/api/v1/kb/documents", headers=H, json=SATS)
    klient.post("/api/v1/kb/documents", headers=H, json={**SATS, "kategori": "intern", "titel": "Kundekontakt-guide"})
    kategorier = {d["kategori"] for d in klient.get("/api/v1/kb/documents?kategori=intern").json()}
    assert kategorier == {"intern"}

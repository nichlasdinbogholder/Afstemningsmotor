"""Webdelens data (til Next.js): kræver login, viser kun aktuelle fund, statusskift logges."""

from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api import login
from app.api.main import app
from app.jobs.models import Job
from app.kontoudtog.importer import indlaes
from app.kunder.models import Client
from app.personale.models import Staff
from app.rules.models import Finding, FindingEvent, RuleRun

H = {"X-Afstemning": "1"}


@pytest.fixture
def medarbejder(db_session):
    m = Staff(navn="Bo Bruger", email="bo@dinbogholder.dk", rolle="medarbejder", aktiv=True)
    db_session.add(m)
    db_session.flush()
    return m


@pytest.fixture
def klient(db_session, medarbejder):
    app.dependency_overrides[login.db] = lambda: db_session
    app.dependency_overrides[login.nuvaerende_medarbejder] = lambda: medarbejder
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def kunde(db_session):
    k = Client(navn="Web ApS", kundenummer="WEB-1", regnskabssystem="economic", status="aktiv")
    db_session.add(k)
    db_session.flush()
    return k


def _fund(session, kunde, nr, severity="high", status="open", regel="duplicate_entries"):
    f = Finding(client_id=kunde.id, rule_code=regel, fingerprint=f"fp{nr}", severity=severity, status=status,
                title=f"Fund {nr}", detail={"x": nr}, entry_ids=[], period_start=date(2026, 9, nr))
    session.add(f)
    session.flush()
    return f


def test_alt_kraever_login(db_session):
    app.dependency_overrides[login.db] = lambda: db_session
    try:
        k = TestClient(app)
        for sti in ("/api/mig", "/api/kunder", "/api/kunder/1/fund", "/api/kontoudtog/1", "/api/jobs/1"):
            assert k.get(sti).status_code == 401, sti
        assert k.post("/api/fund/1/status", json={"status": "resolved"}, headers=H).status_code == 401
    finally:
        app.dependency_overrides.clear()


def test_kundeoversigt_taeller_kun_aabne_aktuelle_fund(klient, db_session, kunde):
    _fund(db_session, kunde, 1, "high")
    _fund(db_session, kunde, 2, "medium")
    _fund(db_session, kunde, 3, "high", status="resolved")
    gammel = _fund(db_session, kunde, 4, "low")
    db_session.add(RuleRun(client_id=kunde.id, rule_code="duplicate_entries", rule_version=1, fund=2))
    db_session.flush()
    # fund 4 blev ikke set i seneste kørsel -> ikke aktuelt
    from sqlalchemy import text
    db_session.execute(text("UPDATE findings SET last_seen_at = now() + interval '1 second' WHERE client_id = :c "
                            "AND id <> :g"), {"c": kunde.id, "g": gammel.id})
    db_session.execute(text("UPDATE findings SET last_seen_at = now() - interval '1 day' WHERE id = :g"),
                       {"g": gammel.id})
    raekke = next(k for k in klient.get("/api/kunder").json() if k["id"] == kunde.id)
    assert raekke["aabne_fund"] == {"high": 1, "medium": 1, "low": 0}

    svar = klient.get(f"/api/kunder/{kunde.id}/fund").json()
    assert [f["titel"] for f in svar["fund"]] == ["Fund 1", "Fund 2"]
    assert svar["fund"][0]["regel_navn"] == "Muligt dobbeltbogført beløb"
    alle = klient.get(f"/api/kunder/{kunde.id}/fund", params={"status": "", "alle": True}).json()
    assert alle["antal"] == 4


def test_statusskift_kraever_header_og_note_og_logges(klient, db_session, kunde, medarbejder):
    f = _fund(db_session, kunde, 1)
    url = f"/api/fund/{f.id}/status"
    assert klient.post(url, json={"status": "resolved"}).status_code == 403          # ingen header
    assert klient.post(url, json={"status": "ignored"}, headers=H).status_code == 422  # ingen note
    assert klient.post(url, json={"status": "nej"}, headers=H).status_code == 422
    svar = klient.post(url, json={"status": "ignored", "note": "Mellemregning"}, headers=H)
    assert svar.status_code == 200 and svar.json() == {"id": f.id, "fra": "open", "til": "ignored"}
    h = db_session.scalars(select(FindingEvent).where(FindingEvent.finding_id == f.id,
                                                      FindingEvent.to_status == "ignored")).one()
    assert (h.actor, h.note) == ("bo@dinbogholder.dk", "Mellemregning")
    detalje = klient.get(f"/api/fund/{f.id}").json()
    assert detalje["status"] == "ignored" and detalje["historik"][-1]["af"] == "bo@dinbogholder.dk"
    assert klient.post("/api/fund/999999999/status", json={"status": "resolved"}, headers=H).status_code == 404


def test_kontoudtog_med_matchprocent_og_linjer(klient, db_session, kunde):
    u = indlaes(db_session, kunde.id, "grossist", date(2026, 9, 1), date(2026, 9, 30), "modsat",
                [{"linje_nr": 1, "dato": date(2026, 9, 3), "reference": "7", "tekst": "FAKTURA",
                  "beloeb": Decimal("100.00"), "raa_data": None}], modpart="kreditor:45", kildefil="a.pdf")
    liste = klient.get(f"/api/kunder/{kunde.id}/kontoudtog").json()
    assert liste[0]["id"] == u.id and liste[0]["linjer"] == 1 and liste[0]["matchprocent"] == 0.0
    linjer = klient.get(f"/api/kontoudtog/{u.id}").json()["linjer"]
    assert linjer == [{"nr": 1, "dato": "2026-09-03", "reference": "7", "tekst": "FAKTURA", "beloeb": "100.00",
                       "match": None, "bogfoert": None}]


def test_opdater_nu_laegger_et_job_forrest_i_koeen_og_genbruger_det(klient, db_session, kunde):
    url = f"/api/kunder/{kunde.id}/opdater"
    assert klient.post(url).status_code == 403
    foerste = klient.post(url, headers=H).json()
    anden = klient.post(url, headers=H).json()
    assert foerste["ny"] and not anden["ny"] and anden["job_id"] == foerste["job_id"]
    job = db_session.get(Job, foerste["job_id"])
    assert (job.type, job.prioritet, job.payload) == ("opdater_kunde", 10, {"bestilt_af": "bo@dinbogholder.dk"})
    assert klient.get(f"/api/jobs/{job.id}").json()["status"] == "koe"


def test_ukendt_kunde_giver_404(klient):
    assert klient.get("/api/kunder/999999999").status_code == 404
    assert klient.get("/api/kunder/999999999/fund").status_code == 404

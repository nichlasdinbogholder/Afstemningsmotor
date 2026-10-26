"""Fanen Stamdata (bygherren 26.10.2026): navn og virksomhedsform hentes automatisk fra CVR (Datafordeleren, indtil
Erhvervsstyrelsens adgang er på plads); regnskabsperioden kommer fra kundens regnskabssystem, og CVR bruges som kontrol."""

import json
from datetime import date

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.api import login
from app.api.main import app
from app.config import get_settings
from app.kunder import cvr, stamdata
from app.kunder.models import Client
from app.kunder.tabeller import KundeCvrChange, KundeCvrSnapshot
from app.personale.models import Staff
from app.regnskab.models import AccountingYearCache

NU = {"virkningTil": None, "registreringTil": None}


@pytest.fixture
def datafordeler_til(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "datafordeler_api_key", SecretStr("df-noegle-stamdata-1"))
    monkeypatch.setattr(s, "kunde_cvr_api_bruger", None)
    monkeypatch.setattr(s, "kunde_cvr_api_kode", None)


@pytest.fixture
def kunde(db_session):
    m = Staff(navn="Stam M", email="stam-m@dinbogholder.dk", rolle="medarbejder", aktiv=True)
    db_session.add(m)
    db_session.flush()
    k = Client(navn="Murer Hansen", kundenummer="STAM-1", cvr="12345678", regnskabssystem="economic",
               status="aktiv", ansvarlig_medarbejder_id=m.id)
    db_session.add(k)
    db_session.flush()
    return k


def _datafordeler(navn="Murer Hansen ApS", form="Anpartsselskab", status="aktiv"):
    def h(request):
        q = json.loads(request.content)["query"]
        if "CVR_Virksomhed(" in q:
            nodes = [{"id": "E1", "CVRNummer": 12345678, "status": status, **NU}]
            return httpx.Response(200, json={"data": {"CVR_Virksomhed": {"nodes": nodes}}})
        if "CVR_Navn(" in q:
            return httpx.Response(200, json={"data": {"CVR_Navn": {"nodes": [{"vaerdi": navn, **NU}]}}})
        nodes = [{"vaerdi": "80", "vaerdiTekst": form, **NU}]
        return httpx.Response(200, json={"data": {"CVR_Virksomhedsform": {"nodes": nodes}}})
    return httpx.MockTransport(h)


def test_datafordeleren_slaar_cvr_til(datafordeler_til):
    assert cvr.cvr_slaaet_til() and not cvr.erst_slaaet_til()


def test_hentes_fra_datafordeleren_og_gemmes(db_session, kunde, datafordeler_til):
    assert cvr.opdater(db_session, kunde, transport=_datafordeler()) == []
    snap = db_session.get(KundeCvrSnapshot, kunde.id)
    assert (snap.name, snap.company_form, snap.status, snap.fiscal_year_end) == (
        "Murer Hansen ApS", "Anpartsselskab", "aktiv", None)
    assert kunde.navn == "Murer Hansen"                          # kartotekets navn er urørt
    # Et nyt navn hos CVR bliver en forskel, der vises – aldrig skrevet ind over kartoteket.
    fund = cvr.opdater(db_session, kunde, transport=_datafordeler(navn="Hansen & Søn ApS"))
    assert fund == [("name", "Murer Hansen ApS", "Hansen & Søn ApS", "info")]


def test_felt_der_bliver_kendt_er_ikke_en_aendring(db_session, kunde, datafordeler_til):
    cvr.opdater(db_session, kunde, transport=_datafordeler())
    # Senere kommer Erhvervsstyrelsens adgang med regnskabsåret – det hidtil tomme felt er ikke en "ændring".
    data = cvr.CvrData(cvr="12345678", name="Murer Hansen ApS", address="Vejen 1", company_form="Anpartsselskab",
                       industry_code="433200", fiscal_year_end="30-06", status="aktiv")
    assert cvr.sammenlign(db_session, kunde, data) == []
    assert db_session.get(KundeCvrSnapshot, kunde.id).fiscal_year_end == "30-06"
    assert not db_session.query(KundeCvrChange).filter_by(client_id=kunde.id).all()


def test_regnskabsperioden_fra_regnskabssystemet_og_kontrol_mod_cvr(db_session, kunde):
    db_session.add_all([AccountingYearCache(client_id=kunde.id, navn="2025", fra=date(2025, 1, 1), til=date(2025, 12, 31)),
                        AccountingYearCache(client_id=kunde.id, navn="2026", fra=date(2026, 1, 1), til=date(2026, 12, 31))])
    db_session.flush()
    d = stamdata.json(db_session, kunde, date(2026, 10, 26))
    assert d["regnskabsperiode"] == {"fra": "2026-01-01", "til": "2026-12-31", "navn": "2026"}
    assert d["navn"] is None and d["cvr"] == "12345678" and d["regnskabsperiode_advarsel"] is None
    # CVR kender regnskabsåret (Erhvervsstyrelsen) og er uenig -> advarsel.
    db_session.add(KundeCvrSnapshot(client_id=kunde.id, name="Murer Hansen ApS", fiscal_year_end="30-06"))
    db_session.flush()
    advarsel = stamdata.json(db_session, kunde, date(2026, 10, 26))["regnskabsperiode_advarsel"]
    assert "30.06" in advarsel and "31.12" in advarsel
    # Intet regnskabsår, der omfatter dagen -> intet svar (gætter ikke).
    assert stamdata.json(db_session, kunde, date(2027, 3, 1))["regnskabsperiode"] is None


def test_api_kraever_login_og_viser_stamdata(db_session, kunde):
    app.dependency_overrides[login.db] = lambda: db_session
    app.dependency_overrides[login.nuvaerende_medarbejder] = lambda: db_session.get(Staff, kunde.ansvarlig_medarbejder_id)
    try:
        svar = TestClient(app).get(f"/api/kundekort/{kunde.id}/stamdata")
    finally:
        app.dependency_overrides.clear()
    assert svar.status_code == 200 and svar.json()["cvr"] == "12345678"

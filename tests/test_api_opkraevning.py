"""Debitorstyringens data til webdelen: fakturaer med status og kanal, debitorer, rykkere."""

from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api import login
from app.api.main import app
from app.audit.models import AuditLog
from app.kunder.models import Client
from app.opkraevning.models import Debtor, DunningSkip, DunningStep, Invoice, ReferenceRate
from app.opkraevning.rykkerkoersel import laeg_i_koe
from app.personale.models import Staff
from app.tid import TIDSZONE

H = {"X-Afstemning": "1"}
I_DAG = datetime.now(TIDSZONE).date()


@pytest.fixture
def klient(db_session):
    m = Staff(navn="Bo", email="bo-api@dinbogholder.dk", rolle="medarbejder", aktiv=True)
    db_session.add(m)
    db_session.flush()
    app.dependency_overrides[login.db] = lambda: db_session
    app.dependency_overrides[login.nuvaerende_medarbejder] = lambda: m
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def kunde(db_session):
    k = Client(navn="Debitor ApS", kundenummer="DEB-1", regnskabssystem="economic", status="aktiv",
               dunning_mode="preview")
    db_session.add(k)
    db_session.flush()
    d1 = Debtor(client_id=k.id, external_id="30", name="FERM Entreprenør ApS", email="a@b.dk")
    d2 = Debtor(client_id=k.id, external_id="159", name="Bodil Rissom Pedersen")   # ingen kanal
    db_session.add_all([d1, d2])
    db_session.flush()

    def f(nr, d, forfald, beloeb, rest, **kw):
        db_session.add(Invoice(client_id=k.id, external_id=str(nr), invoice_no=str(nr), debtor_id=d.id,
                               issue_date=forfald - timedelta(days=14), due_date=forfald, amount=Decimal(beloeb),
                               amount_outstanding=Decimal(rest), **kw))
    f(742, d1, I_DAG + timedelta(days=5), "664.80", "0", status="paid")
    f(739, d1, I_DAG + timedelta(days=5), "8607.08", "8607.08")
    f(738, d1, I_DAG - timedelta(days=3), "-8607.08", "-8607.08", kind="credit_note")
    f(733, d2, I_DAG - timedelta(days=20), "8492.75", "8492.75")
    f(731, d2, I_DAG - timedelta(days=20), "1487.50", "487.50")
    db_session.flush()
    return k


def test_alt_kraever_login(db_session):
    app.dependency_overrides[login.db] = lambda: db_session
    try:
        k = TestClient(app)
        for sti in ("/api/opkraevning/kunder", "/api/opkraevning/1/fakturaer", "/api/opkraevning/1/debitorer",
                    "/api/opkraevning/1/rykkere", "/api/opkraevning/faktura/1"):
            assert k.get(sti).status_code == 401, sti
    finally:
        app.dependency_overrides.clear()


def test_fakturaer_med_betalingsstatus_kanal_og_antal(klient, kunde):
    svar = klient.get(f"/api/opkraevning/{kunde.id}/fakturaer").json()
    status = {f["fakturanummer"]: (f["betalingsstatus"], f["kanal"]) for f in svar["fakturaer"]}
    assert status == {"742": ("betalt", "email"), "739": ("ikke_betalt", "email"), "738": ("kreditnota", "email"),
                      "733": ("forfaldet", None), "731": ("forfaldet", None)}
    assert svar["antal"]["alt"] == 5 and svar["antal"]["forfaldet"] == 2 and svar["antal"]["ingen_kanal"] == 2
    ingen = klient.get(f"/api/opkraevning/{kunde.id}/fakturaer", params={"filter": "ingen_kanal"}).json()
    assert sorted(f["fakturanummer"] for f in ingen["fakturaer"]) == ["731", "733"]
    soeg = klient.get(f"/api/opkraevning/{kunde.id}/fakturaer", params={"q": "bodil"}).json()
    assert {f["debitor"] for f in soeg["fakturaer"]} == {"Bodil Rissom Pedersen"}
    assert klient.get(f"/api/opkraevning/{kunde.id}/fakturaer", params={"filter": "nej"}).status_code == 422


def test_kundeliste_med_forfaldne(klient, kunde):
    r = next(k for k in klient.get("/api/opkraevning/kunder").json() if k["id"] == kunde.id)
    assert (r["aabne"], r["forfaldne"], r["forfaldent_beloeb"]) == (3, 2, "8980.25")


def test_debitor_blokeres_og_det_logges(klient, db_session, kunde):
    d = next(x for x in klient.get(f"/api/opkraevning/{kunde.id}/debitorer").json() if x["nummer"] == "159")
    assert d["aabent"] == "8980.25" and d["aabne_fakturaer"] == 2
    assert klient.post(f"/api/opkraevning/debitor/{d['id']}", json={"blokeret": True}).status_code == 403
    svar = klient.post(f"/api/opkraevning/debitor/{d['id']}", headers=H,
                       json={"blokeret": True, "note": "Aftale om betaling 1/11", "kanal": "print"})
    assert svar.json()["blokeret"] is True and svar.json()["kanal"] == "print"
    log = db_session.scalars(select(AuditLog).where(AuditLog.handling == "debitor_aendret")).one()
    assert log.detaljer["blokeret"] is True and log.staff_id is not None
    assert klient.post(f"/api/opkraevning/debitor/{d['id']}", headers=H, json={"kanal": "brevdue"}).status_code == 422


def test_rykker_i_koe_vises_og_kan_fjernes_med_note(klient, db_session, kunde):
    db_session.add(ReferenceRate(valid_from=date(I_DAG.year, 1 if I_DAG.month < 7 else 7, 1),
                                 rate=Decimal("1.60"), source="test"))
    db_session.flush()
    laeg_i_koe(db_session, kunde.id, I_DAG)
    data = klient.get(f"/api/opkraevning/{kunde.id}/rykkere").json()
    assert {r["fakturanummer"] for r in data["i_koe"]} == {"733", "731"}
    assert any(s["aarsag"] == "under_minimumsbeloeb" for s in data["spaerrer"]) is False  # 487,50 > 100
    rid = data["i_koe"][0]["id"]
    assert klient.post(f"/api/opkraevning/rykker/{rid}/fjern", headers=H, json={"note": ""}).status_code == 422
    assert klient.post(f"/api/opkraevning/rykker/{rid}/fjern", headers=H,
                       json={"note": "Kunden ringede"}).json()["status"] == "cancelled"
    assert klient.post(f"/api/opkraevning/rykker/{rid}/fjern", headers=H, json={"note": "igen"}).status_code == 409
    assert db_session.get(DunningStep, rid).status == "cancelled"
    assert db_session.scalars(select(DunningSkip).where(DunningSkip.reason == "fjernet_af_medarbejder")).one()


def test_fakturadetaljer(klient, db_session, kunde):
    f = db_session.scalars(select(Invoice).where(Invoice.client_id == kunde.id, Invoice.invoice_no == "731")).one()
    d = klient.get(f"/api/opkraevning/faktura/{f.id}").json()
    assert (d["fakturanummer"], d["restbeloeb"], d["debitor"]["navn"]) == ("731", "487.50", "Bodil Rissom Pedersen")
    assert d["rykkere"] == [] and d["betalinger"] == []


def test_samme_spaerre_vises_kun_een_gang(klient, db_session, kunde):
    f = db_session.scalars(select(Invoice).where(Invoice.client_id == kunde.id, Invoice.invoice_no == "733")).one()
    for _ in range(3):
        db_session.add(DunningSkip(invoice_id=f.id, reason="debitor_blokeret"))
        db_session.flush()
    db_session.add(DunningSkip(invoice_id=f.id, reason="afbetalingsordning"))
    db_session.flush()
    data = klient.get(f"/api/opkraevning/{kunde.id}/rykkere").json()
    assert sorted(s["aarsag"] for s in data["spaerrer"]) == ["afbetalingsordning", "debitor_blokeret"]

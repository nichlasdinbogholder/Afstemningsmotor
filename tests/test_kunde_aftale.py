"""Fanen Aftale (bygherren 26.10.2026): det underskrevne aftalegrundlag som PDF. Mangler det, er der alarm ved fanen.
Kun ansvarlig, assistent og admin uploader; intet slettes eller ændres – en ny version arkiverer den gamle."""

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from app.api import login
from app.api.main import app
from app.kunder.models import Client
from app.kunder.tabeller import KundeAftaleDokument, KundeLog
from app.personale.models import Staff
from app.timeregistrering import satser

PDF = b"%PDF-1.4\n% opdigtet testaftale\n%%EOF\n"


def _h(navn="Aftale 2026.pdf"):
    from urllib.parse import quote
    return {"X-Afstemning": "1", "Content-Type": "application/pdf", "X-Filnavn": quote(navn)}


@pytest.fixture
def folk(db_session):
    a = Staff(navn="Aft Ansvarlig", email="aft-a@dinbogholder.dk", rolle="medarbejder", aktiv=True)
    b = Staff(navn="Aft Anden", email="aft-b@dinbogholder.dk", rolle="medarbejder", aktiv=True)
    db_session.add_all([a, b])
    db_session.flush()
    return a, b


@pytest.fixture
def kunde(db_session, folk):
    k = Client(navn="Aft Tømrer ApS", kundenummer="AFT-1", regnskabssystem="economic", status="aktiv",
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


def test_alarm_indtil_aftalen_er_uploadet(klient, kunde, db_session, folk):
    assert klient.get(f"/api/kundekort/{kunde.id}").json()["mangler_aftale"] is True
    svar = klient.post(f"/api/kundekort/{kunde.id}/aftale", headers=_h("Aftalegrundlag – Tømrer.pdf"), content=PDF)
    assert svar.status_code == 200, svar.text
    assert svar.json()["filnavn"] == "Aftalegrundlag – Tømrer.pdf"
    assert klient.get(f"/api/kundekort/{kunde.id}").json()["mangler_aftale"] is False
    d = klient.get(f"/api/kundekort/{kunde.id}/aftale").json()
    pdf = klient.get(f"/api/kundekort/{kunde.id}/aftale/{d['gaeldende']['id']}/pdf")
    assert pdf.content == PDF and pdf.headers["content-type"] == "application/pdf"
    log = db_session.scalars(select(KundeLog).where(KundeLog.client_id == kunde.id)).one()
    assert (log.event, log.actor_staff_id) == ("aftale_uploadet", folk[0].id)


def test_ny_version_arkiverer_den_gamle(klient, kunde):
    klient.post(f"/api/kundekort/{kunde.id}/aftale", headers=_h("v1.pdf"), content=PDF)
    klient.post(f"/api/kundekort/{kunde.id}/aftale", headers=_h("v2.pdf"), content=PDF + b"2")
    d = klient.get(f"/api/kundekort/{kunde.id}/aftale").json()
    assert d["gaeldende"]["filnavn"] == "v2.pdf" and [x["filnavn"] for x in d["tidligere"]] == ["v1.pdf"]


def test_kun_pdf_og_kun_ansvarlig_assistent_admin(klient, kunde, folk):
    assert "PDF" in klient.post(f"/api/kundekort/{kunde.id}/aftale", headers=_h("x.docx"),
                                content=b"PK\x03\x04 ikke en pdf").json()["detail"]
    assert "tom" in klient.post(f"/api/kundekort/{kunde.id}/aftale", headers=_h(), content=b"").json()["detail"]
    app.dependency_overrides[login.nuvaerende_medarbejder] = lambda: folk[1]
    assert klient.post(f"/api/kundekort/{kunde.id}/aftale", headers=_h(), content=PDF).status_code == 403
    assert klient.get(f"/api/kundekort/{kunde.id}/aftale").json()["kan_uploade"] is False    # men kan se
    assert klient.post(f"/api/kundekort/{kunde.id}/aftale", content=PDF,
                       headers={"Content-Type": "application/pdf"}).status_code == 403        # header


def test_interne_kunder_har_ingen_alarm(klient, kunde, db_session, folk):
    kunde.kun_admin = True
    db_session.flush()
    satser.nye_vilkaar(db_session, kunde.id, date.today() - timedelta(days=10), "intern", None, folk[0].id)
    assert klient.get(f"/api/kundekort/{kunde.id}").json()["mangler_aftale"] is False


def test_databasen_sletter_og_aendrer_aldrig(klient, kunde, db_session):
    klient.post(f"/api/kundekort/{kunde.id}/aftale", headers=_h(), content=PDF)
    d = db_session.scalars(select(KundeAftaleDokument).where(KundeAftaleDokument.client_id == kunde.id)).one()
    with pytest.raises(DBAPIError, match="slettes aldrig"), db_session.begin_nested():
        db_session.execute(text("DELETE FROM kunde_aftaledokumenter WHERE id = :i"), {"i": d.id})
    with pytest.raises(DBAPIError, match="kan ikke ændres"), db_session.begin_nested():
        db_session.execute(text("UPDATE kunde_aftaledokumenter SET filnavn = 'x.pdf' WHERE id = :i"), {"i": d.id})
    with pytest.raises(DBAPIError), db_session.begin_nested():
        db_session.execute(text("INSERT INTO kunde_aftaledokumenter (client_id, filnavn, indhold, stoerrelse, sha256, "
                                "uploadet_af) VALUES (:k, 'x', 'ikke pdf'::bytea, 8, 'x', :s)"),
                           {"k": kunde.id, "s": d.uploadet_af})

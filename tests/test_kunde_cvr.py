"""Kundeoversigten, trin 2: CVR-opslag (spec-test 20, 21, 22, 24 m.fl.). Opdigtede svar – intet sendes til CVR."""

import json
from datetime import datetime

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.config import get_settings
from app.jobs.models import Job
from app.kunder import cvr
from app.kunder.models import Client
from app.kunder.tabeller import KundeAlias, KundeCvrChange, KundeCvrSnapshot, KundeLog
from app.notifikationer.models import Notification
from app.personale.models import Staff
from app.tid import TIDSZONE

KODE = "cvr-test-adgangskode-9f8e"


def _svar(cvr_nr="12345678", navn="Murer Hansen ApS", status="NORMAL", navne=None, form="Anpartsselskab",
          regnskabsaar="--12-31"):
    return {"hits": {"total": {"value": 1}, "hits": [{"_source": {"Vrvirksomhed": {
        "cvrNummer": int(cvr_nr),
        "navne": navne or [{"navn": navn, "periode": {"gyldigFra": "2015-01-01", "gyldigTil": None}}],
        "attributter": [{"type": "REGNSKABSÅR_SLUT",
                         "vaerdier": [{"vaerdi": regnskabsaar, "periode": {"gyldigFra": "2015-01-01",
                                                                         "gyldigTil": None}}]}],
        "virksomhedMetadata": {
            "nyesteNavn": {"navn": navn},
            "nyesteBeliggenhedsadresse": {"vejnavn": "Byggevej", "husnummerFra": 12, "bogstavFra": "B",
                                          "etage": None, "sidedoer": None, "postnummer": 8000,
                                          "postdistrikt": "Aarhus C", "conavn": None},
            "nyesteVirksomhedsform": {"virksomhedsformkode": 80, "kortBeskrivelse": "APS", "langBeskrivelse": form},
            "nyesteHovedbranche": {"branchekode": "412000", "branchetekst": "Opførelse af bygninger"},
            "sammensatStatus": status,
        }}}}]}}


@pytest.fixture
def slaaet_til(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "kunde_cvr_api_bruger", "testbruger")
    monkeypatch.setattr(s, "kunde_cvr_api_kode", SecretStr(KODE))


class FalskCvr:
    def __init__(self, svar, status=200):
        self.svar, self.status, self.kald = svar, status, []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.kald.append(request)
        svar = self.svar.pop(0) if isinstance(self.svar, list) else self.svar
        return httpx.Response(self.status, json=svar)

    @property
    def transport(self):
        return httpx.MockTransport(self)


@pytest.fixture
def folk(db_session):
    a = Staff(navn="CVR Admin", email="cvr-admin@dinbogholder.dk", rolle="admin", aktiv=True)
    m = Staff(navn="CVR Ansvarlig", email="cvr-m@dinbogholder.dk", rolle="medarbejder", aktiv=True)
    db_session.add_all([a, m])
    db_session.flush()
    return a, m


@pytest.fixture
def kunde(db_session, folk):
    k = Client(navn="Murer Hansen", kundenummer="CVR-1", cvr="12345678", regnskabssystem="economic", status="aktiv",
               ansvarlig_medarbejder_id=folk[1].id)
    db_session.add(k)
    db_session.flush()
    return k


# --- Aflæsning af svaret --------------------------------------------------------------------------------------

def test_svaret_laeses_uden_at_gaette():
    d = cvr.laes(_svar(navne=[{"navn": "Hansen Byg", "periode": {"gyldigTil": "2020-05-01"}},
                              {"navn": "Murer Hansen ApS", "periode": {"gyldigTil": None}}]), "12345678")
    assert (d.name, d.address, d.company_form, d.industry_code, d.fiscal_year_end, d.status) == (
        "Murer Hansen ApS", "Byggevej 12B, 8000 Aarhus C", "Anpartsselskab", "412000", "31-12", "NORMAL")
    assert d.tidligere_navne == ["Hansen Byg"]
    # Mangler et felt, bliver det tomt – det gættes ikke.
    svar = _svar()
    del svar["hits"]["hits"][0]["_source"]["Vrvirksomhed"]["virksomhedMetadata"]["nyesteHovedbranche"]
    svar["hits"]["hits"][0]["_source"]["Vrvirksomhed"]["attributter"] = []
    d = cvr.laes(svar, "12345678")
    assert d.industry_code is None and d.fiscal_year_end is None


def test_ukendt_flere_eller_forkert_virksomhed():
    assert cvr.laes({"hits": {"hits": []}}, "12345678") is None
    to = _svar()
    to["hits"]["hits"].append(to["hits"]["hits"][0])
    with pytest.raises(cvr.CvrFejl, match="flere virksomheder"):
        cvr.laes(to, "12345678")
    with pytest.raises(cvr.CvrFejl, match="anden virksomhed"):
        cvr.laes(_svar(cvr_nr="87654321"), "12345678")
    with pytest.raises(cvr.CvrFejl, match="forventede form"):
        cvr.laes({"fejl": "x"}, "12345678")


# --- Hentning -------------------------------------------------------------------------------------------------

def test_slaaet_fra_uden_login(monkeypatch):
    monkeypatch.setattr(get_settings(), "kunde_cvr_api_bruger", None)
    assert cvr.cvr_slaaet_til() is False
    with pytest.raises(cvr.CvrFejl, match="ikke slået til"):
        cvr.hent_raat("12345678")


def test_hent_bruger_login_og_viser_aldrig_adgangskoden(slaaet_til):
    falsk = FalskCvr(_svar())
    assert cvr.hent("12345678", falsk.transport).name == "Murer Hansen ApS"
    k = falsk.kald[0]
    assert k.method == "POST" and k.headers["Authorization"].startswith("Basic ")
    assert json.loads(k.content) == {"query": {"term": {"Vrvirksomhed.cvrNummer": 12345678}}, "size": 2}
    with pytest.raises(cvr.CvrFejl) as fejl:
        cvr.hent("12345678", FalskCvr({}, status=401).transport)
    assert "status 401" in str(fejl.value) and KODE not in str(fejl.value)
    falsk = FalskCvr({}, status=503)
    with pytest.raises(cvr.CvrFejl, match="prøvet 3 gange"):
        cvr.hent("12345678", falsk.transport, vent=lambda _: 0)
    assert len(falsk.kald) == 3
    with pytest.raises(cvr.CvrFejl, match="8 cifre"):
        cvr.hent("1234", falsk.transport)


# --- Sammenligning: CVR overskriver aldrig kundens felter -----------------------------------------------------

def test_foerste_opslag_gemmer_svaret_og_tidligere_navne(db_session, kunde):
    data = cvr.laes(_svar(navne=[{"navn": "Hansen Byg", "periode": {"gyldigTil": "2020-05-01"}}]), "12345678")
    assert cvr.sammenlign(db_session, kunde, data) == []
    snap = db_session.get(KundeCvrSnapshot, kunde.id)
    assert (snap.name, snap.status, snap.fiscal_year_end) == ("Murer Hansen ApS", "NORMAL", "31-12")
    assert db_session.scalars(select(KundeAlias.alias).where(KundeAlias.client_id == kunde.id)).all() == ["Hansen Byg"]
    assert kunde.navn == "Murer Hansen"                       # kartotekets navn er urørt


def test_navneaendring_skrives_som_forskel_ikke_ind_over_stamdata(db_session, kunde):
    cvr.sammenlign(db_session, kunde, cvr.laes(_svar(), "12345678"))
    fund = cvr.sammenlign(db_session, kunde, cvr.laes(_svar(navn="Hansen & Søn ApS"), "12345678"))
    assert fund == [("name", "Murer Hansen ApS", "Hansen & Søn ApS", "info")]
    assert kunde.navn == "Murer Hansen"
    aendring = db_session.scalars(select(KundeCvrChange).where(KundeCvrChange.client_id == kunde.id)).one()
    assert (aendring.field, aendring.severity, aendring.handled_at) == ("name", "info", None)
    # Det gamle navn bliver et alias, og loggen viser ændringen (uden aktør = systemet).
    assert "Murer Hansen ApS" in db_session.scalars(select(KundeAlias.alias)
                                                    .where(KundeAlias.client_id == kunde.id)).all()
    log = db_session.scalars(select(KundeLog).where(KundeLog.event == "cvr_aendring",
                                                    KundeLog.client_id == kunde.id)).one()
    assert log.detail == "Navn: Murer Hansen ApS → Hansen & Søn ApS" and log.actor_staff_id is None
    # Samme forskel skrives ikke igen, mens den venter på at blive håndteret.
    db_session.get(KundeCvrSnapshot, kunde.id).name = "Murer Hansen ApS"
    assert cvr.sammenlign(db_session, kunde, cvr.laes(_svar(navn="Hansen & Søn ApS"), "12345678")) == []


def test_konkurs_er_kritisk_og_sendes_samme_dag(db_session, kunde, folk):
    cvr.sammenlign(db_session, kunde, cvr.laes(_svar(), "12345678"))
    fund = cvr.sammenlign(db_session, kunde, cvr.laes(_svar(status="UNDER KONKURS"), "12345678"))
    assert fund == [("status", "NORMAL", "UNDER KONKURS", "kritisk")]
    modtagere = db_session.scalars(select(Notification.staff_id).where(Notification.client_id == kunde.id)).all()
    assert folk[0].id in modtagere and folk[1].id in modtagere   # ansvarlig og administrator
    n = db_session.scalars(select(Notification).where(Notification.staff_id == folk[1].id)).one()
    assert (n.alvor, n.titel) == ("kritisk", "Murer Hansen: UNDER KONKURS")


def test_kritisk_status_meldes_ogsaa_ved_foerste_opslag(db_session, kunde):
    fund = cvr.sammenlign(db_session, kunde, cvr.laes(_svar(status="UNDER TVANGSOPLØSNING"), "12345678"))
    assert [f[3] for f in fund] == ["kritisk"]


def test_ukendt_cvr_nummer_meldes(db_session, kunde):
    fund = cvr.sammenlign(db_session, kunde, None)
    assert fund == [("status", None, "Findes ikke i CVR", "advarsel")]


def test_to_kunder_kan_ikke_have_samme_cvr(db_session, kunde):
    db_session.add(Client(navn="Kopi", kundenummer="CVR-2", cvr="12345678", regnskabssystem="economic"))
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()


# --- Planlægning og job ---------------------------------------------------------------------------------------

def test_dagligt_opslag_kun_for_aktive_med_cvr(db_session, kunde, slaaet_til):
    db_session.add_all([Client(navn="Uden CVR", kundenummer="CVR-3", regnskabssystem="economic", status="aktiv"),
                        Client(navn="Pause", kundenummer="CVR-4", cvr="11112222", regnskabssystem="economic",
                               status="pause")])
    db_session.flush()
    nu = datetime(2026, 10, 27, 6, 0, tzinfo=TIDSZONE)
    for _ in range(2):                                         # to kørsler samme dag = ét job
        cvr.planlaeg(db_session, nu)
    jobs = db_session.scalars(select(Job).where(Job.type == cvr.JOBTYPE)).all()
    assert [j.client_id for j in jobs] == [kunde.id]


def test_uden_login_planlaegges_intet(db_session, kunde, monkeypatch):
    monkeypatch.setattr(get_settings(), "kunde_cvr_api_bruger", None)
    assert cvr.planlaeg(db_session, datetime(2026, 10, 27, 6, 0, tzinfo=TIDSZONE)) == 0


def test_ny_kunde_faar_opslag_med_det_samme(db_session, folk, slaaet_til):
    from datetime import date

    from app.kunder import kartotek

    k = kartotek.opret(db_session, folk[0], "Ny Tømrer ApS", "CVR-9", "99887766", "economic", None,
                       date(2026, 11, 1), "timeafregning", None)
    job = db_session.scalars(select(Job).where(Job.type == cvr.JOBTYPE, Job.client_id == k.id)).one()
    assert job.prioritet == 10


# --- Notifikationer (klokken) ---------------------------------------------------------------------------------

def test_notifikationer_kun_egne_og_kan_markeres_laest(db_session, folk, kunde):
    from fastapi.testclient import TestClient

    from app.api import login
    from app.api.main import app
    from app.notifikationer.tjeneste import send_notifikation

    send_notifikation(db_session, [folk[0].id, folk[1].id, None], "advarsel", "Prøve", client_id=kunde.id)
    app.dependency_overrides[login.db] = lambda: db_session
    app.dependency_overrides[login.nuvaerende_medarbejder] = lambda: folk[1]
    try:
        k = TestClient(app)
        mine = k.get("/api/notifikationer").json()
        assert mine["ulaeste"] == 1 and [n["titel"] for n in mine["notifikationer"]] == ["Prøve"]
        andens = db_session.scalars(select(Notification.id).where(Notification.staff_id == folk[0].id)).one()
        h = {"X-Afstemning": "1"}
        assert k.post(f"/api/notifikationer/{andens}/laest", headers=h).status_code == 404
        assert k.post(f"/api/notifikationer/{mine['notifikationer'][0]['id']}/laest").status_code == 403
        assert k.post(f"/api/notifikationer/{mine['notifikationer'][0]['id']}/laest", headers=h).status_code == 200
        assert k.get("/api/notifikationer").json()["ulaeste"] == 0
    finally:
        app.dependency_overrides.clear()


def test_jobbet_henter_og_sammenligner(db_session, kunde, slaaet_til, monkeypatch):
    from app.jobs.register import JobKontekst

    monkeypatch.setattr(cvr, "hent", lambda nr, *a, **k: cvr.laes(_svar(status="UNDER KONKURS"), nr))
    cvr.cvr_job(JobKontekst(job_id=1, type=cvr.JOBTYPE, client_id=kunde.id, payload={}, forsoeg=1, max_forsoeg=5,
                            session=db_session))
    assert db_session.get(KundeCvrSnapshot, kunde.id).status == "UNDER KONKURS"
    assert db_session.scalars(select(KundeCvrChange.severity).where(KundeCvrChange.client_id == kunde.id)).all() == [
        "kritisk"]

"""Natkørslen: scheduler kl. 05:00 på hverdage, spredte job, trin for trin pr. kunde,
audit_log og statuskommandoen."""

from contextlib import contextmanager
from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select, text

import app.natkoersel.scheduler as scheduler_modul
import app.natkoersel.status as status_modul
from app.adaptere.regnskab import base
from app.adaptere.regnskab.base import (
    AdapterFejl, ForMangeKald, Kassekladde, KladdePost, Konto, PosteringsSvar,
)
from app.audit.models import AuditLog
from app.jobs.register import JobKontekst, UdskydJob, hent_funktion
from app.kunder.models import Client, Credential
from app.natkoersel.job import JOBTYPE, NatkoerselFejl, koer_natkoersel
from app.natkoersel.scheduler import planlaeg_nat, trigger
from app.rules.models import Finding

DK = ZoneInfo("Europe/Copenhagen")


# --- Simuleret regnskabssystem -----------------------------------------------------


class FalskSystem:
    system = "economic"

    def __init__(self, fejl=None, kladdelinjer=()):
        self.fejl = fejl or {}
        self.kladdelinjer = list(kladdelinjer)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def _tjek(self, metode):
        if metode in self.fejl:
            raise self.fejl[metode]

    def fetch_accounts(self):
        self._tjek("fetch_accounts")
        return [Konto(5820, "Bankkonto", "status", "debit", None, False, False, Decimal("0"), None, {}),
                Konto(9900, "Fejlkonto", "status", "debit", None, False, False, Decimal("0"), None, {})]

    def fetch_customers(self):
        self._tjek("fetch_customers")
        return []

    def fetch_suppliers(self):
        self._tjek("fetch_suppliers")
        return []

    def fetch_entries(self, efter):
        self._tjek("fetch_entries")
        return PosteringsSvar([], efter, None)

    def fetch_open_entries(self):
        self._tjek("fetch_open_entries")
        return []

    def fetch_journals(self):
        self._tjek("fetch_journals")
        return [Kassekladde(1, "Kassekladde")]

    def fetch_journal_entries(self, nummer):
        return self.kladdelinjer


def _kladdelinje(linje, konto=9900, beloeb="2500.00"):
    return KladdePost(1, linje, 500 + linje, date(2026, 10, 1), konto, 5820, "Ukendt indbetaling",
                      Decimal(beloeb), "DKK", "financeVoucher")


@pytest.fixture
def system(monkeypatch):
    falsk = FalskSystem()
    monkeypatch.setitem(base._FABRIKKER, "economic", lambda s, c: falsk)
    return falsk


def _kunde(session, nr, status="aktiv", med_adgang=True):
    k = Client(navn=f"Natkunde {nr}", kundenummer=f"NAT-{nr}", regnskabssystem="economic", status=status)
    session.add(k)
    session.flush()
    if med_adgang:
        c = Credential(client_id=k.id, system="economic")
        c.saet_token(f"token-{nr}")
        session.add(c)
    session.commit()
    return k


def _seneste_audit(session, client_id):
    return session.scalars(select(AuditLog).where(AuditLog.client_id == client_id,
                                                  AuditLog.handling == "natkoersel_kunde")
                           .order_by(AuditLog.id.desc())).first()


# --- Tidsplanen -----------------------------------------------------------------------


@pytest.mark.parametrize("fra, forventet", [
    (datetime(2026, 10, 10, 12, 0, tzinfo=DK), datetime(2026, 10, 12, 5, 0, tzinfo=DK)),  # lør → man
    (datetime(2026, 10, 7, 6, 0, tzinfo=DK), datetime(2026, 10, 8, 5, 0, tzinfo=DK)),     # ons → tor
    (datetime(2026, 10, 9, 4, 59, tzinfo=DK), datetime(2026, 10, 9, 5, 0, tzinfo=DK)),    # fre før 5
    (datetime(2026, 10, 9, 5, 1, tzinfo=DK), datetime(2026, 10, 12, 5, 0, tzinfo=DK)),    # fre efter 5 → man
    (datetime(2026, 12, 4, 6, 0, tzinfo=DK), datetime(2026, 12, 7, 5, 0, tzinfo=DK)),     # vintertid
])
def test_koerer_kl_5_paa_hverdage_dansk_tid(fra, forventet):
    naeste = trigger().get_next_fire_time(None, fra)
    assert naeste == forventet
    assert naeste.astimezone(DK).hour == 5


# --- Planlægningen: ét job pr. aktiv kunde, spredt ud ----------------------------------


def _vores_job(session, kunder):
    return session.execute(text(
        "SELECT client_id, planlagt_til FROM jobs WHERE type = :t AND client_id = ANY(:k) ORDER BY planlagt_til"),
        {"t": JOBTYPE, "k": [k.id for k in kunder]}).all()


def test_et_job_pr_aktiv_kunde_spredt_og_logget(db_session):
    aktive = [_kunde(db_session, n) for n in ("1", "2", "3")]
    pause = _kunde(db_session, "P", status="pause")
    uden_adgang = _kunde(db_session, "U", med_adgang=False)
    nu = datetime(2026, 10, 8, 5, 0, tzinfo=DK)

    r = planlaeg_nat(db_session, nu)
    job = _vores_job(db_session, aktive + [pause, uden_adgang])

    assert {j.client_id for j in job} == {k.id for k in aktive}
    tider = [j.planlagt_til for j in job]
    assert len(set(tider)) == 3, "jobbene må ikke ligge i køen på samme tidspunkt"
    assert all(b - a >= timedelta(seconds=20) for a, b in zip(tider, tider[1:]))
    assert max(tider) - nu.astimezone(tider[0].tzinfo) <= timedelta(hours=2)
    log = db_session.scalars(select(AuditLog).where(AuditLog.handling == "natkoersel_planlagt")
                             .order_by(AuditLog.id.desc())).first()
    assert log.detaljer["dag"] == "2026-10-08" and log.detaljer == r


def test_planlaegning_er_idempotent(db_session):
    k = _kunde(db_session, "I")
    nu = datetime(2026, 10, 8, 5, 0, tzinfo=DK)
    planlaeg_nat(db_session, nu)
    anden = planlaeg_nat(db_session, nu + timedelta(minutes=30))  # fx genstart af scheduleren
    assert len(_vores_job(db_session, [k])) == 1
    assert anden["nye_job"] == 0 and anden["fandtes_allerede"] >= 1


def test_en_kunde_der_ikke_kan_laegges_i_koe_stopper_ikke_de_andre(db_session, monkeypatch):
    a, b, c = (_kunde(db_session, n) for n in ("A", "B", "C"))
    original = scheduler_modul.laeg_i_koe

    def laeg(session, type_, client_id=None, **kw):
        if client_id == b.id:
            raise RuntimeError("databasen drillede")
        return original(session, type_, client_id=client_id, **kw)

    monkeypatch.setattr(scheduler_modul, "laeg_i_koe", laeg)
    r = planlaeg_nat(db_session, datetime(2026, 10, 8, 5, 0, tzinfo=DK))
    assert {j.client_id for j in _vores_job(db_session, [a, b, c])} == {a.id, c.id}
    assert str(b.id) in r["fejl"]


# --- Natjobbet for én kunde -----------------------------------------------------------


def test_alle_trin_koerer_og_logges(db_session, system):
    k = _kunde(db_session, "OK")
    system.kladdelinjer = [_kladdelinje(1), _kladdelinje(2, konto=1010)]  # kun linje 1 er på 9900

    r = koer_natkoersel(db_session, k.id)

    assert r["status"] == "ok"
    assert set(r["trin"]) == {"synk_kontoplan", "synk_customers", "synk_suppliers", "synk_entries",
                              "synk_open_entries", "regler", "kassekladde"}
    assert all(s == "ok" for s in r["trin"].values()), r["trin"]
    assert r["kassekladde_fund"] == 1
    assert _seneste_audit(db_session, k.id).detaljer == r
    fund = db_session.scalars(select(Finding).where(Finding.client_id == k.id,
                                                    Finding.rule_code == "fejlkonto_kassekladde")).all()
    assert len(fund) == 1 and "fejlkonto 9900" in fund[0].title and "2.500,00 kr." in fund[0].title


def test_et_trin_der_fejler_stopper_ikke_de_naeste(db_session, system):
    k = _kunde(db_session, "FEJL")
    system.fejl["fetch_customers"] = AdapterFejl("svarede 500")

    r = koer_natkoersel(db_session, k.id)

    assert r["status"] == "fejl"
    assert r["trin"]["synk_customers"].startswith("fejl")
    assert r["trin"]["synk_entries"] == "ok"      # næste synk-trin kørte
    assert r["trin"]["regler"] == "ok"            # regelmotoren kørte
    assert r["trin"]["kassekladde"] == "ok"       # kassekladdekontrollen kørte
    assert _seneste_audit(db_session, k.id).detaljer["status"] == "fejl"


def test_jobbet_fejler_til_sidst_saa_det_proeves_igen(db_session, system):
    k = _kunde(db_session, "J")
    system.fejl["fetch_journals"] = AdapterFejl("kassekladden kunne ikke hentes")
    job = JobKontekst(job_id=1, type=JOBTYPE, client_id=k.id, payload={}, forsoeg=1, max_forsoeg=5,
                      session=db_session)
    with pytest.raises(NatkoerselFejl, match="kassekladde"):
        hent_funktion(JOBTYPE)(job)
    assert _seneste_audit(db_session, k.id).detaljer["trin"]["regler"] == "ok"


def test_for_mange_kald_udskyder_hele_jobbet(db_session, system):
    k = _kunde(db_session, "R")
    system.fejl["fetch_entries"] = ForMangeKald("for mange kald", 120)
    job = JobKontekst(job_id=1, type=JOBTYPE, client_id=k.id, payload={}, forsoeg=1, max_forsoeg=5,
                      session=db_session)
    with pytest.raises(UdskydJob) as udskyd:
        hent_funktion(JOBTYPE)(job)
    assert udskyd.value.sekunder == 120


def test_en_kunde_der_fejler_paavirker_ikke_en_anden(db_session, monkeypatch):
    god, daarlig = _kunde(db_session, "GOD"), _kunde(db_session, "DAARLIG")
    systemer = {god.id: FalskSystem(), daarlig.id: FalskSystem(fejl={
        m: AdapterFejl("nede") for m in ("fetch_accounts", "fetch_customers", "fetch_suppliers",
                                         "fetch_entries", "fetch_open_entries", "fetch_journals")})}
    monkeypatch.setitem(base._FABRIKKER, "economic", lambda s, c: systemer[c])

    resultater = {}
    for k in (daarlig, god):  # den dårlige først
        job = JobKontekst(job_id=1, type=JOBTYPE, client_id=k.id, payload={}, forsoeg=1, max_forsoeg=5,
                          session=db_session)
        try:
            hent_funktion(JOBTYPE)(job)
            resultater[k.id] = "ok"
        except NatkoerselFejl:
            resultater[k.id] = "fejl"
    assert resultater == {daarlig.id: "fejl", god.id: "ok"}


# --- Statuskommandoen -----------------------------------------------------------------


@pytest.fixture
def samme_session(db_session, monkeypatch):
    @contextmanager
    def _s():
        yield db_session
    monkeypatch.setattr(status_modul, "ny_session", _s)
    return db_session


def _saet_jobstatus(session, client_id, status, fejl=None):
    session.execute(text("UPDATE jobs SET status = CAST(:s AS varchar), sidste_fejl = :f, forsoeg = 1, "
                         "afsluttet = CASE WHEN CAST(:s AS varchar) IN ('faerdig', 'fejlet') THEN now() END "
                         "WHERE type = :t AND client_id = :k"),
                    {"s": status, "f": fejl, "t": JOBTYPE, "k": client_id})


def test_status_siger_gik_godt(samme_session, system, capsys):
    k = _kunde(samme_session, "S1")
    planlaeg_nat(samme_session, datetime(2031, 3, 3, 5, 0, tzinfo=DK))
    koer_natkoersel(samme_session, k.id)
    _saet_jobstatus(samme_session, k.id, "faerdig")
    samme_session.execute(text("UPDATE jobs SET status = 'faerdig', afsluttet = now() "
                               "WHERE type = :t AND idempotens_noegle LIKE :m"), {"t": JOBTYPE, "m": "%:2031-03-03"})

    kode = status_modul.main(["--dato", "2031-03-03"])
    ud = capsys.readouterr().out
    assert kode == 0, ud
    assert "Natkørslen gik godt" in ud and "NAT-S1" in ud


def test_status_viser_fejlet_kunde(samme_session, system, capsys):
    god, daarlig = _kunde(samme_session, "S2"), _kunde(samme_session, "S3")
    planlaeg_nat(samme_session, datetime(2031, 3, 4, 5, 0, tzinfo=DK))
    samme_session.execute(text("UPDATE jobs SET status = 'faerdig', afsluttet = now() "
                               "WHERE type = :t AND idempotens_noegle LIKE :m"), {"t": JOBTYPE, "m": "%:2031-03-04"})
    system.fejl["fetch_journals"] = AdapterFejl("nede")
    koer_natkoersel(samme_session, daarlig.id)
    _saet_jobstatus(samme_session, daarlig.id, "fejlet", "NatkoerselFejl: kassekladde fejlede")

    kode = status_modul.main(["--dato", "2031-03-04"])
    ud = capsys.readouterr().out
    assert kode == 1, ud
    assert "FEJLET" in ud and "kassekladde" in ud and "kunde(r) FEJLEDE" in ud


def test_status_uden_natkoersel(samme_session, capsys):
    assert status_modul.main(["--dato", "2031-03-08"]) == 2
    assert "Ingen natkørsel" in capsys.readouterr().out

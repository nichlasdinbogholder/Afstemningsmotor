"""Satskontrollen (fase 3, trin 10): spec-test 23–28, databasens regler og godkendelsen.

Kildesiden og modellen er erstattet af faste, opdigtede tekster – der hentes intet fra nettet i testene.
Tallene er opdigtede; testene handler om reglerne, ikke om de rigtige satser.
"""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from app.api import login
from app.api.main import app
from app.assistent import samtale as sm
from app.assistent import satskontrol as sk
from app.assistent.models import KbDocument, KbRateCheck
from app.audit.models import AuditLog
from app.personale.models import Staff

NU = datetime(2026, 10, 19, 6, 0, tzinfo=timezone.utc)
SIDE = ("<html><head><script>var x = 9,99;</script></head><body><h1>Satser for kørsel</h1>"
        "<table><tr><td>Op til 20.000 km</td><td>1,23 kr.</td></tr>"
        "<tr><td>Over 20.000 km</td><td>0,77 kr.</td></tr></table></body></html>")
H = {"X-Afstemning": "1"}


@pytest.fixture
def side(monkeypatch):
    tekst = {"html": SIDE}
    monkeypatch.setattr(sk, "hent_side", lambda url: sk.html_til_tekst(tekst["html"]))
    return tekst


@pytest.fixture
def sats(db_session):
    d = KbDocument(title="Kilometersats (opdigtet)", category="satser", body="Kørsel i egen bil.",
                   value=Decimal("1.2300"), unit="kr/km", status="active", valid_from=date(2026, 1, 1),
                   verify_source_url="https://skat.dk/eksempel", verify_hint="Tallet efter 'Op til 20.000 km'",
                   last_verified_at=datetime(2026, 1, 5, tzinfo=timezone.utc))
    db_session.add(d)
    db_session.flush()
    return d


@pytest.fixture
def admin(db_session):
    a = Staff(navn="Admin", email="admin-sats@dinbogholder.dk", rolle="admin", aktiv=True)
    db_session.add(a)
    db_session.flush()
    return a


def _kontroller(db_session):
    return db_session.scalars(select(KbRateCheck).order_by(KbRateCheck.id)).all()


def test_html_bliver_ren_tekst_uden_script():
    tekst = sk.html_til_tekst(SIDE)
    assert "Op til 20.000 km 1,23 kr." in tekst and "9,99" not in tekst


def test_dansk_tal():
    assert sk.dansk_tal("1.234,50") == Decimal("1234.50")
    assert sk.dansk_tal("3,79") == Decimal("3.79")
    assert sk.dansk_tal("25") == Decimal("25")
    with pytest.raises(sk.KontrolFejl):
        sk.dansk_tal("1.5")


def test_regel_kraever_entydigt_anker():
    tekst = sk.html_til_tekst(SIDE)
    assert sk.find_med_regel(tekst, "'Over 20.000 km'").vaerdi == Decimal("0.77")
    assert sk.find_med_regel(tekst, "'20.000 km'") is None          # står to steder → ikke entydigt
    assert sk.find_med_regel(tekst, "'findes ikke'") is None


# 23. Uændret værdi → last_verified_at opdateres, intet udkast, intet fund
def test_23_uaendret(db_session, sats, side):
    k = sk.kontroller(db_session, sats, NU)
    assert (k.outcome, k.fetched_value, k.method, k.draft_id) == ("unchanged", Decimal("1.2300"), "regel", None)
    assert sats.last_verified_at == NU
    assert db_session.scalars(select(KbDocument).where(KbDocument.replaces_id == sats.id)).all() == []
    assert k.decision is None  # et uændret udfald er ikke et fund


# 24. Ændret værdi → udkast + fund; det gamle dokument er STADIG aktivt
def test_24_aendret(db_session, sats, side):
    side["html"] = SIDE.replace("1,23 kr.", "1,31 kr.")
    k = sk.kontroller(db_session, sats, NU)
    udkast = db_session.get(KbDocument, k.draft_id)
    assert (k.outcome, k.previous_value, k.fetched_value) == ("changed", Decimal("1.2300"), Decimal("1.3100"))
    assert "Op til 20.000 km 1,31" in k.raw_excerpt
    assert (udkast.status, udkast.value, udkast.replaces_id) == ("draft", Decimal("1.3100"), sats.id)
    assert sats.status == "active" and sats.value == Decimal("1.2300")
    assert sats.last_verified_at == datetime(2026, 1, 5, tzinfo=timezone.utc)
    # Kørt igen samme dag: samme udkast genbruges
    k2 = sk.kontroller(db_session, sats, NU + timedelta(minutes=5))
    assert k2.draft_id == udkast.id


# 25. Hentefejl → fund; gammelt dokument aktivt; last_verified_at uændret
def test_25_hentefejl(db_session, sats, monkeypatch):
    def fejler(url):
        raise sk.KontrolFejl("Kildesiden kunne ikke hentes")
    monkeypatch.setattr(sk, "hent_side", fejler)
    k = sk.kontroller(db_session, sats, NU)
    assert (k.outcome, k.error) == ("failed", "Kildesiden kunne ikke hentes")
    assert sats.status == "active" and sats.last_verified_at == datetime(2026, 1, 5, tzinfo=timezone.utc)


def test_siden_lagt_om_giver_fejl_naar_modellen_ikke_kan(db_session, sats, side, monkeypatch):
    side["html"] = "<p>Siden er flyttet.</p>"
    monkeypatch.setattr(sk, "spoerg_model", lambda *a: {"fundet": False, "vaerdi": "", "tekststykke": "",
                                                         "begrundelse": "Satsen står ikke på siden"})
    k = sk.kontroller(db_session, sats, NU)
    assert k.outcome == "failed" and "Satsen står ikke på siden" in k.error


def test_modellen_bruges_naar_reglen_ikke_er_entydig(db_session, sats, side, monkeypatch):
    sats.verify_hint = "Satsen for de første 20.000 km"  # intet citat, og teksten står ikke ordret på siden
    monkeypatch.setattr(sk, "spoerg_model", lambda *a: {"fundet": True, "vaerdi": "1,23",
                                                         "tekststykke": "Op til 20.000 km 1,23 kr.",
                                                         "begrundelse": "Rækken for op til 20.000 km"})
    k = sk.kontroller(db_session, sats, NU)
    assert (k.outcome, k.method, k.raw_excerpt) == ("unchanged", "model", "Op til 20.000 km 1,23 kr.")


def test_modellen_maa_aldrig_finde_paa_et_tal(db_session, sats, side, monkeypatch):
    sats.verify_hint = "Satsen for de første 20.000 km"
    monkeypatch.setattr(sk, "spoerg_model", lambda *a: {"fundet": True, "vaerdi": "4,56", "tekststykke": "x",
                                                         "begrundelse": "gæt"})
    k = sk.kontroller(db_session, sats, NU)
    assert k.outcome == "failed" and "står ikke på siden" in k.error
    assert db_session.scalars(select(KbDocument).where(KbDocument.replaces_id == sats.id)).all() == []


# 26. Godkendelse → udkast aktivt med valid_from; gammelt 'superseded' med valid_to dagen før
def test_26_godkendelse(db_session, sats, side, admin):
    side["html"] = SIDE.replace("1,23 kr.", "1,31 kr.")
    k = sk.kontroller(db_session, sats, NU)
    ny = sk.godkend(db_session, k, admin.id, date(2027, 1, 1), "Ny sats fra skat.dk", NU)
    assert (ny.status, ny.valid_from) == ("active", date(2027, 1, 1))
    assert (sats.status, sats.valid_to) == ("superseded", date(2026, 12, 31))
    assert (k.decision, k.reviewed_by) == ("approved", admin.id)
    with pytest.raises(sk.AfgoerelsesFejl):
        sk.godkend(db_session, k, admin.id, date(2027, 1, 1), None, NU)


# 27. Uden godkendelse når den nye værdi aldrig modellen – og databasen afviser at gøre den aktiv
def test_27_uden_godkendelse(db_session, sats, side):
    side["html"] = SIDE.replace("1,23 kr.", "1,31 kr.")
    k = sk.kontroller(db_session, sats, NU)
    tekst = sm.systemtekst(sm.find_materiale(db_session, "Hvad er kilometersatsen?", dag=date(2026, 10, 19)))
    assert "1,23 kr/km" in tekst and "1,31" not in tekst
    udkast = db_session.get(KbDocument, k.draft_id)
    with pytest.raises(DBAPIError, match="godkendt"):
        with db_session.begin_nested():
            udkast.status = "active"
            db_session.flush()


def test_databasen_afviser_ny_aktiv_sats_der_afloeser_uden_godkendelse(db_session, sats):
    with pytest.raises(DBAPIError, match="godkendt"):
        with db_session.begin_nested():
            db_session.add(KbDocument(title="x", category="satser", body="x", value=1, unit="kr",
                                      status="active", replaces_id=sats.id))
            db_session.flush()


# 28. Hver kørsel skriver en række – også ved uændret
def test_28_hver_koersel_logges(db_session, sats, side):
    sk.kontroller_alle(db_session, NU)
    sk.kontroller_alle(db_session, NU + timedelta(days=7))
    assert [k.outcome for k in _kontroller(db_session) if k.kb_id == sats.id] == ["unchanged", "unchanged"]


def test_kontrolloggen_kan_ikke_slettes_eller_aendres(db_session, sats, side, admin):
    k = sk.kontroller(db_session, sats, NU)
    with pytest.raises(DBAPIError, match="slettes"):
        with db_session.begin_nested():
            db_session.execute(text("DELETE FROM kb_rate_checks WHERE id = :i"), {"i": k.id})
    with pytest.raises(DBAPIError, match="ændres"):
        with db_session.begin_nested():
            db_session.execute(text("UPDATE kb_rate_checks SET fetched_value = 5 WHERE id = :i"), {"i": k.id})


def test_afvisning_og_kvittering(db_session, sats, side, admin, monkeypatch):
    side["html"] = SIDE.replace("1,23 kr.", "1,31 kr.")
    k = sk.kontroller(db_session, sats, NU)
    with pytest.raises(sk.AfgoerelsesFejl):
        sk.afvis(db_session, k, admin.id, " ")
    sk.afvis(db_session, k, admin.id, "Forkert række på siden")
    assert k.decision == "rejected" and sats.status == "active"
    monkeypatch.setattr(sk, "hent_side", lambda url: (_ for _ in ()).throw(sk.KontrolFejl("nede")))
    f = sk.kontroller(db_session, sats, NU + timedelta(days=7))
    sk.afvis(db_session, f, admin.id, "skat.dk var nede")
    assert f.decision == "noted"


def test_kun_aktive_satser_med_kontrolside_kontrolleres(db_session, sats, side):
    db_session.add_all([
        KbDocument(title="Uden kontrolside", category="satser", body="x", value=1, unit="kr", status="active"),
        KbDocument(title="Kladde", category="satser", body="x", value=1, unit="kr", status="draft",
                   verify_source_url="https://skat.dk/x", verify_hint="'Op til 20.000 km'"),
    ])
    db_session.flush()
    assert {k.kb_id for k in sk.kontroller_alle(db_session, NU)} == {sats.id}


def test_mandagsjobbet_er_planlagt():
    from app.natkoersel.scheduler import lav_scheduler

    job = {j.id: str(j.trigger) for j in lav_scheduler().get_jobs()}
    assert "satskontrol" in job and "day_of_week='mon'" in job["satskontrol"] and "hour='6'" in job["satskontrol"]


# API: kun administratorer; godkendelse logges
@pytest.fixture
def klient(db_session, admin):
    app.dependency_overrides[login.db] = lambda: db_session
    app.dependency_overrides[login.nuvaerende_medarbejder] = lambda: admin
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_api_godkendelse_logges_og_kun_admin(db_session, klient, sats, side):
    side["html"] = SIDE.replace("1,23 kr.", "1,31 kr.")
    k = sk.kontroller(db_session, sats, NU)
    aabne = klient.get("/api/v1/kb/kontroller").json()
    assert [(x["id"], x["gammel_vaerdi"], x["ny_vaerdi"]) for x in aabne] == [(k.id, "1.2300", "1.3100")]
    elev = Staff(navn="Elev", email="elev-sats@dinbogholder.dk", rolle="medarbejder", aktiv=True)
    db_session.add(elev)
    db_session.flush()
    app.dependency_overrides[login.nuvaerende_medarbejder] = lambda: elev
    assert klient.get("/api/v1/kb/kontroller").status_code == 403
    assert klient.post(f"/api/v1/kb/kontroller/{k.id}/godkend", headers=H,
                       json={"gyldig_fra": "2027-01-01"}).status_code == 403
    app.dependency_overrides[login.nuvaerende_medarbejder] = lambda: db_session.scalars(
        select(Staff).where(Staff.email == "admin-sats@dinbogholder.dk")).one()
    svar = klient.post(f"/api/v1/kb/kontroller/{k.id}/godkend", headers=H, json={"gyldig_fra": "2027-01-01"})
    assert svar.status_code == 200 and svar.json()["afgoerelse"] == "approved"
    log = db_session.scalars(select(AuditLog).where(AuditLog.handling == "kb_sats_godkendt")).one()
    assert (log.detaljer["fra"], log.detaljer["til"], log.detaljer["gyldig_fra"]) == ("1.2300", "1.3100", "2027-01-01")
    assert klient.get("/api/v1/kb/kontroller").json() == []


# Trin 11: forældelse – sikkerhedsnettet under mandagsjobbet (standard 45 dage)
def _sats_bekraeftet(db_session, dage_siden, titel="Kilometersats gammel (opdigtet)"):
    d = KbDocument(title=titel, category="satser", body="Sats.", value=Decimal("1.2300"), unit="kr/km",
                   status="active", last_verified_at=NU - timedelta(days=dage_siden))
    db_session.add(d)
    db_session.flush()
    return d


def test_29_foraeldet_sats_markeres_og_svend_advarer(db_session):
    frisk = _sats_bekraeftet(db_session, 10, "Frisk sats")
    gammel = _sats_bekraeftet(db_session, 46, "Gammel sats")
    assert not sk.er_foraeldet(frisk, NU) and sk.er_foraeldet(gammel, NU)
    # materialeteksten regner fra i dag – sæt datoen i forhold til nu
    gammel.last_verified_at = datetime.now(timezone.utc) - timedelta(days=60)
    tekst = sm.materialetekst([gammel])
    dato_ = gammel.last_verified_at.strftime("%d.%m.%Y")
    assert f"Satsen er senest bekræftet {dato_} — tjek den, før du bruger den til noget bindende." in tekst
    frisk.last_verified_at = datetime.now(timezone.utc) - timedelta(days=3)
    assert "FORÆLDET" not in sm.materialetekst([frisk])


def test_fund_foerst_efter_det_dobbelte_og_kun_et_aabent(db_session):
    _sats_bekraeftet(db_session, 80, "Over 45, under 90")
    gammel = _sats_bekraeftet(db_session, 91, "Over 90")
    nye = sk.tjek_foraeldelse(db_session, NU)
    assert [k.kb_id for k in nye] == [gammel.id] and nye[0].outcome == "stale"
    assert "ikke bekræftet siden" in nye[0].error
    assert sk.tjek_foraeldelse(db_session, NU + timedelta(days=1)) == []  # allerede et åbent fund


def test_kvittering_for_foraeldelse(db_session, admin):
    _sats_bekraeftet(db_session, 100)
    k = sk.tjek_foraeldelse(db_session, NU)[0]
    sk.afvis(db_session, k, admin.id, "Satsen er tjekket på skat.dk")
    assert k.decision == "noted"


def test_foraeldelsesjobbet_er_planlagt_hver_dag():
    from app.natkoersel.scheduler import lav_scheduler

    job = {j.id: str(j.trigger) for j in lav_scheduler().get_jobs()}
    assert "hour='6'" in job["satsforaeldelse"] and "minute='30'" in job["satsforaeldelse"]
    assert "day_of_week" not in job["satsforaeldelse"]

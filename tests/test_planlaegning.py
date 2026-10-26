"""AI Planlægning (bygherren 26.10.2026): kommende frister for moms og lønsumsafgift ud fra kundens ordning (fanen
Frister) og Skats fristkalender. Fristerne beregnes ALDRIG – kun godkendte datoer fra Skat bruges."""

from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.api import login
from app.api.main import app
from app.kunder.models import Client
from app.kunder.tabeller import KundeObligations
from app.personale.models import Staff

H = {"X-Afstemning": "1"}
SKAT = "https://skat.dk/erhverv/moms/frister-indberet-og-betal-moms"


@pytest.fixture
def folk(db_session):
    m = Staff(navn="Pla Mig", email="pla-mig@dinbogholder.dk", rolle="medarbejder", aktiv=True)
    admin = Staff(navn="Pla Admin", email="pla-admin@dinbogholder.dk", rolle="admin", aktiv=True)
    db_session.add_all([m, admin])
    db_session.flush()
    return m, admin


@pytest.fixture
def kunder(db_session, folk):
    a = Client(navn="Pla A ApS", kundenummer="PLA-A", regnskabssystem="economic", status="aktiv",
               ansvarlig_medarbejder_id=folk[0].id)
    b = Client(navn="Pla B ApS", kundenummer="PLA-B", regnskabssystem="economic", status="onboarding",
               ansvarlig_medarbejder_id=folk[1].id)
    c = Client(navn="Pla C ApS", kundenummer="PLA-C", regnskabssystem="economic", status="aktiv")
    db_session.add_all([a, b, c])
    db_session.flush()
    db_session.add_all([KundeObligations(client_id=a.id, vat_period="kvartal", loensumsafgift_metode="ingen"),
                        KundeObligations(client_id=b.id, vat_period="halvaar", loensumsafgift_metode="metode_1")])
    db_session.flush()
    return a, b, c


@pytest.fixture
def klient(db_session, folk):
    app.dependency_overrides[login.db] = lambda: db_session
    app.dependency_overrides[login.nuvaerende_medarbejder] = lambda: folk[1]
    yield TestClient(app)
    app.dependency_overrides.clear()


def _frist(klient, **data):
    return klient.post("/api/planlaegning/kalender", headers=H, json={"kilde": SKAT, **data})


def _ind(klient):
    assert _frist(klient, afgift="moms", ordning="kvartal", periode_fra="2026-07-01", periode_til="2026-09-30",
                  frist="2026-12-01").status_code == 200
    _frist(klient, afgift="moms", ordning="halvaar", periode_fra="2026-07-01", periode_til="2026-12-31",
           frist="2027-03-01")
    _frist(klient, afgift="loensumsafgift", ordning="metode_1", periode_fra="2026-10-01", periode_til="2026-12-31",
           frist="2027-01-15")


def _rk(klient, **q):
    return klient.get("/api/planlaegning/frister", params={"fra": "2026-10-26", "til": "2027-03-31", **q}).json()


def test_fristerne_vises_naermeste_foerst(klient, kunder):
    _ind(klient)
    d = _rk(klient)
    assert [(r["kunde"], r["afgift"], r["periode"], r["frist"]) for r in d["raekker"] if r["kunde"].startswith("Pla")] == [
        ("Pla A ApS", "moms", "3. kvartal 2026", "2026-12-01"),
        ("Pla B ApS", "loensumsafgift", "4. kvartal 2026", "2027-01-15"),
        ("Pla B ApS", "moms", "2. halvår 2026", "2027-03-01")]
    assert [r for r in d["raekker"] if r["kunde"] == "Pla A ApS"][0]["dage_til"] == 36
    # Kunde C har intet valgt – det står som advarsel, så den ikke forsvinder stille.
    assert any("Pla C ApS" in a and "moms" in a for a in d["advarsler"])


def test_filtre(klient, kunder, folk):
    _ind(klient)
    assert {r["kunde"] for r in _rk(klient, afgift="loensumsafgift")["raekker"] if r["kunde"].startswith("Pla")} == {
        "Pla B ApS"}
    assert {r["kunde"] for r in _rk(klient, ansvarlig_id=folk[0].id)["raekker"]} == {"Pla A ApS"}
    app.dependency_overrides[login.nuvaerende_medarbejder] = lambda: folk[0]
    assert {r["kunde"] for r in _rk(klient, mine=True)["raekker"]} == {"Pla A ApS"}


def test_advarsel_naar_skats_datoer_mangler(klient, kunder):
    d = _rk(klient)
    assert [r for r in d["raekker"] if r["kunde"].startswith("Pla")] == []
    assert any("kvartalsvis" in a for a in d["advarsler"])


def test_kun_admin_og_aldrig_beregnet(klient, kunder, folk):
    assert "skat.dk" in _frist(klient, afgift="moms", ordning="kvartal", periode_fra="2026-07-01",
                               periode_til="2026-09-30", frist="2026-12-01", kilde="https://andet.dk").json()["detail"]
    assert _frist(klient, afgift="moms", ordning="metode_1", periode_fra="2026-07-01", periode_til="2026-09-30",
                  frist="2026-12-01").status_code == 422
    assert _frist(klient, afgift="moms", ordning="kvartal", periode_fra="2026-07-01", periode_til="2026-09-30",
                  frist="2026-09-01").status_code == 422                                  # frist før periodens slut
    _ind(klient)
    assert "allerede" in _frist(klient, afgift="moms", ordning="kvartal", periode_fra="2026-07-01",
                                periode_til="2026-09-30", frist="2026-12-02").json()["detail"]
    app.dependency_overrides[login.nuvaerende_medarbejder] = lambda: folk[0]
    assert _frist(klient, afgift="moms", ordning="maaned", periode_fra="2026-10-01", periode_til="2026-10-31",
                  frist="2026-11-25").status_code == 403
    assert klient.get("/api/planlaegning/kalender").status_code == 200                   # alle kan se


def test_tilbagekald_og_databasens_vagt(klient, kunder, db_session):
    _ind(klient)
    f = klient.get("/api/planlaegning/kalender").json()[0]
    assert klient.post(f"/api/planlaegning/kalender/{f['id']}/tilbagekald", headers=H).status_code == 200
    assert f["id"] not in [x["id"] for x in klient.get("/api/planlaegning/kalender").json()]
    with pytest.raises(DBAPIError, match="slettes aldrig"), db_session.begin_nested():
        db_session.execute(text("DELETE FROM skat_frister WHERE id = :i"), {"i": f["id"]})
    g = klient.get("/api/planlaegning/kalender").json()[0]
    with pytest.raises(DBAPIError, match="kan ikke rettes"), db_session.begin_nested():
        db_session.execute(text("UPDATE skat_frister SET frist = frist + 1 WHERE id = :i"), {"i": g["id"]})


def test_periodeteksten():
    from app.frister.kalender import periode_tekst
    assert periode_tekst(date(2026, 6, 1), date(2026, 6, 30)) == "juni 2026"
    assert periode_tekst(date(2026, 1, 1), date(2026, 6, 30)) == "1. halvår 2026"
    assert periode_tekst(date(2026, 1, 5), date(2026, 2, 3)) == "05.01.2026–03.02.2026"


# Årsafslutningen (bygherren 26.10.2026): regnet ud fra kundens regnskabsår efter den godkendte regeltabel.
def test_aarsafslutningens_regler():
    from app.frister.aarsafslutning import frister

    def f(valg, slut):
        return [(x.hvad, x.frist) for x in frister(valg, slut)]
    assert f("aarsregnskab_oplysningsskema", date(2026, 12, 31)) == [
        ("aarsregnskab", date(2027, 6, 30)), ("oplysningsskema", date(2027, 6, 30))]
    # Regnskabsår slut 31.03: årsregnskab 30.09, oplysningsskema 1. august (lørdag 2026 → mandag 3. august).
    assert f("aarsregnskab_oplysningsskema", date(2026, 3, 31)) == [
        ("aarsregnskab", date(2026, 9, 30)), ("oplysningsskema", date(2026, 8, 3))]
    # 30.06 + 6 måneder = 31.12 – nytårsaftensdag er ikke en helligdag, så fristen bliver.
    assert f("aarsregnskab_oplysningsskema", date(2026, 6, 30))[0] == ("aarsregnskab", date(2026, 12, 31))
    # 31.07.2026 + 6 = 31.01.2027 (søndag) → mandag 1. februar.
    assert f("aarsregnskab_oplysningsskema", date(2026, 7, 31))[0] == ("aarsregnskab", date(2027, 2, 1))
    # Årsopgørelse: altid 20. maj året efter – også når den falder i en weekend (20.05.2028 er en lørdag).
    assert f("aarsopgoerelse", date(2026, 12, 31)) == [("aarsopgoerelse", date(2027, 5, 20))]
    assert f("aarsopgoerelse", date(2027, 12, 31)) == [("aarsopgoerelse", date(2028, 5, 20))]
    assert f(None, date(2026, 12, 31)) == []


def test_aarsafslutning_i_listen(klient, kunder, db_session):
    from app.regnskab.models import AccountingYearCache
    a, b, c = kunder
    db_session.get(KundeObligations, a.id).aarsafslutning = "aarsregnskab_oplysningsskema"
    db_session.get(KundeObligations, b.id).aarsafslutning = "aarsopgoerelse"
    db_session.add(AccountingYearCache(client_id=a.id, navn="2026", fra=date(2026, 1, 1), til=date(2026, 12, 31)))
    db_session.flush()
    d = klient.get("/api/planlaegning/frister", params={"fra": "2026-10-26", "til": "2027-07-31",
                                                        "afgift": "aarsafslutning"}).json()
    egne = [(r["kunde"], r["ordning_tekst"], r["periode"], r["frist"]) for r in d["raekker"] if r["kunde"].startswith("Pla")]
    assert sorted(egne) == [("Pla A ApS", "Oplysningsskema", "regnskabsår 2026", "2027-06-30"),
                            ("Pla A ApS", "Årsregnskab", "regnskabsår 2026", "2027-06-30")]
    # B har valgt årsopgørelse, men der er ikke hentet regnskabsår – det står som advarsel.
    assert any("Pla B ApS" in x and "regnskabsår" in x for x in d["advarsler"])

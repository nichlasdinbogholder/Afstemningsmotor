"""Timeregistrering, trin 3: ugegitteret – registrering via API'et (spec-test 8, 9, 10 m.fl.)."""

from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api import login
from app.api.main import app
from app.kunder.models import Client
from app.personale.models import Staff
from app.timeregistrering import satser as sat
from app.timeregistrering import stamdata
from app.timeregistrering.models import TimeRegistration, TimeWeek

H = {"X-Afstemning": "1"}
MANDAG = date(2027, 2, 22)  # uge 8 i 2027


@pytest.fixture
def grund(db_session):
    m = Staff(navn="Gitter", email="gitter@dinbogholder.dk", rolle="medarbejder", aktiv=True)
    a = Staff(navn="Gitter Admin", email="gitter-admin@dinbogholder.dk", rolle="admin", aktiv=True)
    k = Client(navn="Gitter Abonnement ApS", kundenummer="GIT-1", regnskabssystem="economic", status="aktiv")
    k2 = Client(navn="Gitter Timer ApS", kundenummer="GIT-2", regnskabssystem="economic", status="aktiv")
    db_session.add_all([m, a, k, k2])
    db_session.flush()
    e = stamdata.saet_ansaettelse(db_session, m.id, "fastloennet", False, None)
    sat.nye_vilkaar(db_session, k.id, date(2027, 1, 1), "abonnement", Decimal(1500), a.id)
    sat.nye_vilkaar(db_session, k2.id, date(2027, 1, 1), "timeafregning", None, a.id)
    return m, a, k, k2, e


@pytest.fixture
def klient(db_session, grund):
    app.dependency_overrides[login.db] = lambda: db_session
    app.dependency_overrides[login.nuvaerende_medarbejder] = lambda: grund[0]
    yield TestClient(app)
    app.dependency_overrides.clear()


def _gem(klient, opgavetype="kundearbejde", kunde=None, timer=None, ids=(), beskrivelse=None, saerskilt=False,
         dato=MANDAG):
    return klient.post("/api/tid/linje", headers=H, json={
        "dato": dato.isoformat(), "ids": list(ids), "opgavetype": opgavetype,
        "kunde_id": kunde.id if kunde else None, "beskrivelse": beskrivelse, "saerskilt": saerskilt,
        "timer": {d.isoformat(): v for d, v in (timer or {}).items()}})


def test_uge_med_syv_dage_og_valg(klient, grund):
    svar = klient.get(f"/api/tid/uge?dato={date(2027, 2, 25).isoformat()}").json()
    assert (svar["aar"], svar["uge"], svar["status"], svar["kan_rettes"]) == (2027, 8, "kladde", True)
    assert svar["dage"] == [f"2027-02-{d}" for d in range(22, 29)]
    koder = [t["kode"] for t in svar["opgavetyper"]]
    assert "kundearbejde" in koder and "salg" not in koder  # salg er ikke i brug endnu
    assert "controlling" in koder and "oevrigt_kunde" not in koder
    assert "Gitter Abonnement ApS" in [k["navn"] for k in svar["kunder"]]


def test_8_10_registrering_med_afregning_og_weekend(db_session, klient, grund):
    _, _, k, _, _ = grund
    loerdag = date(2027, 2, 27)
    svar = _gem(klient, kunde=k, timer={MANDAG: "1,25", loerdag: "2"})
    assert svar.status_code == 200, svar.text
    linje = svar.json()["linjer"][0]
    assert linje["timer"] == {"2027-02-22": "1.25", "2027-02-27": "2.00"} and linje["afregning"] == ["abonnement"]
    raekker = db_session.scalars(select(TimeRegistration).where(TimeRegistration.id.in_(linje["ids"]))).all()
    assert {r.billing_model for r in raekker} == {"abonnement"} and {r.task_kind for r in raekker} == {"kunde"}
    assert linje["i_alt"] == "3.25" and svar.json()["i_alt"]["uge"] == "3.25"
    assert svar.json()["i_alt"]["dage"]["2027-02-27"] == "2.00" and svar.json()["i_alt"]["dage"]["2027-02-24"] == "0.00"


def test_9_kunden_skifter_model_og_gamle_linjer_er_uaendrede(db_session, klient, grund):
    _, a, k, _, _ = grund
    feb = _gem(klient, kunde=k, timer={MANDAG: "3"}).json()["linjer"][0]
    sat.nye_vilkaar(db_session, k.id, date(2027, 3, 1), "timeafregning", None, a.id)
    marts = _gem(klient, kunde=k, timer={date(2027, 3, 1): "2"}, dato=date(2027, 3, 1)).json()["linjer"][0]
    assert marts["afregning"] == ["timeafregning"]
    # Februars linje rettes (flere timer) – afregningsformen på den eksisterende række ændres ikke.
    rettet = _gem(klient, kunde=k, ids=feb["ids"], timer={MANDAG: "4", date(2027, 2, 23): "1"}).json()["linjer"][0]
    raekker = {r.work_date: r for r in db_session.scalars(
        select(TimeRegistration).where(TimeRegistration.id.in_(rettet["ids"])))}
    assert raekker[MANDAG].billing_model == "abonnement" and raekker[MANDAG].hours == Decimal("4")
    assert raekker[date(2027, 2, 23)].billing_model == "abonnement"  # februar = abonnement på arbejdsdagen


def test_ret_og_slet_linje(db_session, klient, grund):
    _, _, k, k2, _ = grund
    linje = _gem(klient, kunde=k, timer={MANDAG: "2", date(2027, 2, 23): "1"}, beskrivelse="Moms Q4").json()["linjer"][0]
    # Ny beskrivelse og en dag fjernet (tom) – samme rækker bevares.
    ny = _gem(klient, kunde=k, ids=linje["ids"], timer={MANDAG: "2", date(2027, 2, 23): ""},
              beskrivelse="Moms 4. kvartal").json()["linjer"][0]
    assert ny["beskrivelse"] == "Moms 4. kvartal" and list(ny["timer"]) == ["2027-02-22"] and ny["ids"][0] in linje["ids"]
    # Ny kunde = nye rækker med den nye kundes afregningsform.
    skiftet = _gem(klient, kunde=k2, ids=ny["ids"], timer={MANDAG: "2"}).json()["linjer"]
    assert len(skiftet) == 1 and skiftet[0]["kunde"] == "Gitter Timer ApS" and skiftet[0]["afregning"] == ["timeafregning"]
    svar = klient.post("/api/tid/linje/slet", headers=H, json={"dato": MANDAG.isoformat(), "ids": skiftet[0]["ids"]})
    assert svar.status_code == 200 and svar.json()["linjer"] == []


def test_validering_ved_gem(db_session, klient, grund):
    _, _, k, _, _ = grund
    fejl = lambda svar: (svar.status_code, svar.json()["detail"])  # noqa: E731
    assert fejl(_gem(klient, kunde=k, timer={MANDAG: "1,1"}))[1].startswith("1,1 er ikke hele kvarter")
    assert "Vælg kunden" in fejl(_gem(klient, timer={MANDAG: "1"}))[1]
    assert "uden kunde" in fejl(_gem(klient, "sygdom", kunde=k, timer={MANDAG: "7,5"}))[1]
    assert "beskrivelse" in fejl(_gem(klient, kunde=k, timer={MANDAG: "1"}, saerskilt=True))[1]
    assert "ikke i brug" in fejl(_gem(klient, "ikke_paa_arbejde", timer={MANDAG: "0"}))[1]  # bruges ikke (19.10.2026)
    assert "ikke i brug" in fejl(_gem(klient, "salg", timer={MANDAG: "1"}))[1]
    assert "ikke i ugen" in fejl(_gem(klient, kunde=k, timer={date(2027, 3, 1): "1"}))[1]
    _gem(klient, kunde=k, timer={MANDAG: "20"})
    assert "højst 24 timer" in fejl(_gem(klient, "administration", timer={MANDAG: "5"}))[1]
    assert "allerede en linje" in fejl(_gem(klient, kunde=k, timer={date(2027, 2, 23): "1"}))[1]
    # Intet af det fejlede blev gemt.
    assert len(klient.get(f"/api/tid/uge?dato={MANDAG}").json()["linjer"]) == 1


def test_kunde_uden_vilkaar_og_medarbejder_uden_opsaetning(db_session, klient, grund):
    m = grund[0]
    ny = Client(navn="Uden Vilkår ApS", kundenummer="GIT-3", regnskabssystem="economic", status="aktiv")
    db_session.add(ny)
    db_session.flush()
    svar = _gem(klient, kunde=ny, timer={MANDAG: "1"})
    assert svar.status_code == 422 and "ingen afregningsform" in svar.json()["detail"]
    assert klient.get(f"/api/tid/afregning?kunde={ny.id}&dato={MANDAG}").json() == {"afregning": None}
    uden = Staff(navn="Ikke sat op", email="gitter-uden@dinbogholder.dk", aktiv=True)
    db_session.add(uden)
    db_session.flush()
    app.dependency_overrides[login.nuvaerende_medarbejder] = lambda: uden
    svar = klient.get(f"/api/tid/uge?dato={MANDAG}")
    assert svar.status_code == 409 and "ikke sat op" in svar.json()["detail"]
    app.dependency_overrides[login.nuvaerende_medarbejder] = lambda: m


def test_afleveret_og_godkendt_uge_kan_ikke_rettes(db_session, klient, grund):
    _, _, k, _, e = grund
    linje = _gem(klient, kunde=k, timer={MANDAG: "2"}).json()["linjer"][0]
    uge = db_session.scalars(select(TimeWeek).where(TimeWeek.employee_id == e.id)).one()
    uge.status = "afleveret"
    db_session.flush()
    assert klient.get(f"/api/tid/uge?dato={MANDAG}").json()["kan_rettes"] is False
    assert "afleveret" in _gem(klient, kunde=k, ids=linje["ids"], timer={MANDAG: "3"}).json()["detail"]
    uge.status = "godkendt"
    db_session.flush()
    assert "godkendt" in klient.post("/api/tid/linje/slet", headers=H, json={
        "dato": MANDAG.isoformat(), "ids": linje["ids"]}).json()["detail"]
    uge.status = "afvist"
    db_session.flush()
    assert _gem(klient, kunde=k, ids=linje["ids"], timer={MANDAG: "3"}).status_code == 200


def test_kun_egne_linjer(db_session, klient, grund):
    m, _, k, _, _ = grund
    linje = _gem(klient, kunde=k, timer={MANDAG: "2"}).json()["linjer"][0]
    anden = Staff(navn="Anden Gitter", email="gitter-anden@dinbogholder.dk", aktiv=True)
    db_session.add(anden)
    db_session.flush()
    stamdata.saet_ansaettelse(db_session, anden.id, "fastloennet", False, None)
    app.dependency_overrides[login.nuvaerende_medarbejder] = lambda: anden
    assert klient.get(f"/api/tid/uge?dato={MANDAG}").json()["linjer"] == []
    svar = klient.post("/api/tid/linje/slet", headers=H, json={"dato": MANDAG.isoformat(), "ids": linje["ids"]})
    assert svar.status_code == 422
    _gem(klient, kunde=k, timer={MANDAG: "1"})
    assert _gem(klient, kunde=k, ids=linje["ids"], timer={MANDAG: "9"}).status_code == 422
    app.dependency_overrides[login.nuvaerende_medarbejder] = lambda: m
    assert klient.get(f"/api/tid/uge?dato={MANDAG}").json()["linjer"][0]["timer"] == {"2027-02-22": "2.00"}


def test_kraever_login_og_header(db_session, grund):
    app.dependency_overrides[login.db] = lambda: db_session
    try:
        assert TestClient(app).get(f"/api/tid/uge?dato={MANDAG}").status_code == 401
        app.dependency_overrides[login.nuvaerende_medarbejder] = lambda: grund[0]
        assert TestClient(app).post("/api/tid/linje", json={}).status_code == 403
    finally:
        app.dependency_overrides.clear()


# --- Trin 4: validering ved indtastning ------------------------------------------------------


def test_trin4_ferie_skjules_for_timeloennede(db_session, klient, grund):
    m, *_ = grund
    assert "ferie" in [t["kode"] for t in klient.get(f"/api/tid/uge?dato={MANDAG}").json()["opgavetyper"]]
    stamdata.saet_ansaettelse(db_session, m.id, "timeloennet", False, None)
    koder = [t["kode"] for t in klient.get(f"/api/tid/uge?dato={MANDAG}").json()["opgavetyper"]]
    # Bygherren 19.10.2026: timelønnede ser kun sygdom af fraværsarterne.
    assert [k for k in koder if k in ("sygdom", "ferie", "barn_syg", "afspadsering", "barsel", "skoleophold",
                                      "ikke_paa_arbejde")] == ["sygdom"]
    for kode in ("barn_syg", "afspadsering", "barsel", "skoleophold"):
        assert _gem(klient, kode, timer={MANDAG: "7,5"}).status_code == 422
    # Skulle nogen alligevel sende ferie, afvises det (7).
    svar = _gem(klient, "ferie", timer={MANDAG: "7,5"})
    assert svar.status_code == 422 and "timelønnede" in svar.json()["detail"]


def test_trin4_uden_for_abonnementet(db_session, klient, grund):
    _, _, k, k2, _ = grund
    # Abonnementskunde: "Timeafregning – uden for abonnementet" kræver beskrivelse (5).
    svar = _gem(klient, kunde=k, timer={MANDAG: "2"}, saerskilt=True)
    assert svar.status_code == 422 and "beskrivelse" in svar.json()["detail"]
    linje = _gem(klient, kunde=k, timer={MANDAG: "2"}, saerskilt=True, beskrivelse="Ekstra: momsregistrering").json()
    l = linje["linjer"][0]
    assert (l["saerskilt"], l["afregning"]) == (True, ["abonnement"])  # kundens aftale står uændret på linjen
    # Kan slås fra igen af medarbejderen.
    assert _gem(klient, kunde=k, ids=l["ids"], timer={MANDAG: "2"}, beskrivelse="Ekstra: momsregistrering"
                ).json()["linjer"][0]["saerskilt"] is False
    # Timeafregningskunde: valget giver ikke mening – alle timer faktureres allerede.
    svar = _gem(klient, kunde=k2, timer={MANDAG: "1"}, saerskilt=True, beskrivelse="Noget")
    assert svar.status_code == 422 and "kun abonnementskunder" in svar.json()["detail"]
    # Intern tid kan aldrig være uden for abonnementet.
    svar = _gem(klient, "administration", timer={MANDAG: "1"}, saerskilt=True, beskrivelse="x")
    assert svar.status_code == 422

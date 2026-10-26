"""Ekstern revisor (bygherren 26.10.2026): masterafstemningen bookes automatisk som 5 samlede timer på én dag i Outlook –
tidligst næstkommende momsfrist efter regnskabsårets slutning, senest 60 dage efter. Tørløb er standard."""

import json
from datetime import date, datetime

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from app.config import get_settings
from app.frister.masterafstemning import find_ledig, planlaeg, vindue
from app.frister.models import MasterafstemningBooking, SkatFrist
from app.kunder.models import Client
from app.kunder.tabeller import KundeLog, KundeObligations
from app.notifikationer.models import Notification
from app.outlook import kalender as outlook
from app.outlook.kalender import Optaget
from app.personale.models import Staff
from app.regnskab.models import AccountingYearCache
from app.tid import TIDSZONE

NU = datetime(2027, 2, 10, 6, 20, tzinfo=TIDSZONE)
SKAT = "https://skat.dk/erhverv/moms/frister-indberet-og-betal-moms"


class FalskKalender:
    def __init__(self, optagede=None):
        self.optagede, self.oprettet = list(optagede or []), []

    def optaget(self, fra, til):
        return [o for o in self.optagede if o.slut > fra and o.start < til]

    def opret(self, start, slut, emne, tekst):
        self.oprettet.append((start, slut, emne))
        self.optagede.append(Optaget(start, slut))
        return f"outlook-{len(self.oprettet)}"


@pytest.fixture
def opsat(monkeypatch, db_session):
    s = get_settings()
    monkeypatch.setattr(s, "kalender_postkasse", "mas-nichlas@dinbogholder.dk")
    ejer = Staff(navn="Mas Nichlas", email="mas-nichlas@dinbogholder.dk", rolle="admin", aktiv=True)
    db_session.add(ejer)
    db_session.flush()
    db_session.add(SkatFrist(afgift="moms", ordning="halvaar", periode_fra=date(2026, 7, 1), periode_til=date(2026, 12, 31),
                             frist=date(2027, 3, 1), kilde=SKAT, status="godkendt", godkendt_af=ejer.id,
                             godkendt_at=NU))
    db_session.flush()
    return ejer


def _kunde(db_session, navn, moms="halvaar", aar_slut=date(2026, 12, 31)):
    k = Client(navn=navn, kundenummer=navn.upper().replace(" ", "-")[:20], regnskabssystem="economic", status="aktiv")
    db_session.add(k)
    db_session.flush()
    db_session.add_all([KundeObligations(client_id=k.id, vat_period=moms, aarsafslutning="ekstern_revisor"),
                        AccountingYearCache(client_id=k.id, navn=str(aar_slut.year), fra=date(aar_slut.year, 1, 1),
                                            til=aar_slut)])
    db_session.flush()
    return k


def test_vinduet_starter_ved_naeste_momsfrist(db_session, opsat):
    k = _kunde(db_session, "Mas A")
    v = vindue(db_session, k, date(2026, 12, 31))
    assert (v.tidligst, v.seneste, v.mangler) == (date(2027, 3, 1), date(2027, 4, 30), None)
    # Ikke momsregistreret: første hverdag efter regnskabsårets slutning.
    db_session.get(KundeObligations, k.id).vat_period = "ingen"
    assert vindue(db_session, k, date(2026, 12, 31)).tidligst == date(2027, 1, 1 + 3)   # 1.1 helligdag, 2-3.1 weekend
    db_session.get(KundeObligations, k.id).vat_period = "kvartal"                      # Skats datoer mangler
    assert "fristkalenderen" in vindue(db_session, k, date(2026, 12, 31)).mangler


def test_foerste_ledige_blok_paa_5_timer():
    dag = date(2027, 3, 1)                                                  # mandag
    t = lambda d, h, m=0: datetime(d.year, d.month, d.day, h, m)            # noqa: E731
    assert find_ledig([], dag, dag, 8, 16) == (t(dag, 8), t(dag, 13))
    # Møde 10-11: kun 08-10 og 11-16 → 11-16 er 5 timer.
    assert find_ledig([Optaget(t(dag, 10), t(dag, 11))], dag, dag, 8, 16) == (t(dag, 11), t(dag, 16))
    # Møde 12-13: ingen 5 timer i træk den dag → næste dag.
    naeste = date(2027, 3, 2)
    assert find_ledig([Optaget(t(dag, 12), t(dag, 13))], dag, naeste, 8, 16) == (t(naeste, 8), t(naeste, 13))
    # Weekend springes over (6.-7. marts 2027).
    assert find_ledig([], date(2027, 3, 6), date(2027, 3, 8), 8, 16)[0] == t(date(2027, 3, 8), 8)
    assert find_ledig([Optaget(t(dag, 7), t(dag, 17))], dag, dag, 8, 16) is None


def test_toerloeb_opretter_intet(db_session, opsat):
    _kunde(db_session, "Mas A")
    kal = FalskKalender()
    ud = planlaeg(db_session, NU, book=False, kalender=kal)
    assert [(r.kunde, r.udfald, r.start) for r in ud] == [("Mas A", "ville_booke", datetime(2027, 3, 1, 8))]
    assert kal.oprettet == [] and db_session.scalars(select(MasterafstemningBooking)).all() == []


def test_booker_og_kun_en_gang(db_session, opsat):
    a, b = _kunde(db_session, "Mas A"), _kunde(db_session, "Mas B")
    kal = FalskKalender([Optaget(datetime(2027, 3, 1, 8), datetime(2027, 3, 1, 9))])
    ud = planlaeg(db_session, NU, book=True, kalender=kal)
    # A får 1. marts 09-14; B kan ikke ligge oveni og får 2. marts 08-13.
    assert [(r.kunde, r.udfald, r.start) for r in ud] == [("Mas A", "booket", datetime(2027, 3, 1, 9)),
                                                          ("Mas B", "booket", datetime(2027, 3, 2, 8))]
    assert kal.oprettet[0][2] == "Masterafstemning – Mas A"
    bk = db_session.scalars(select(MasterafstemningBooking).where(MasterafstemningBooking.client_id == a.id)).one()
    assert (bk.status, bk.outlook_id, bk.seneste) == ("booket", "outlook-1", date(2027, 4, 30))
    assert db_session.scalars(select(KundeLog.event).where(KundeLog.client_id == a.id)).all() == ["masterafstemning_booket"]
    assert planlaeg(db_session, NU, book=True, kalender=kal) == []                  # allerede booket
    assert len(kal.oprettet) == 2 and b.id


def test_ingen_tid_giver_besked_en_gang(db_session, opsat):
    _kunde(db_session, "Mas A")
    fuld = FalskKalender([Optaget(datetime(2027, 1, 1), datetime(2027, 6, 1))])
    assert planlaeg(db_session, NU, book=True, kalender=fuld)[0].udfald == "ingen_tid"
    planlaeg(db_session, NU, book=True, kalender=fuld)
    beskeder = db_session.scalars(select(Notification).where(Notification.staff_id == opsat.id)).all()
    assert len(beskeder) == 1 and "ingen ledig tid" in beskeder[0].titel
    # Bliver der tid, bookes den alligevel.
    assert planlaeg(db_session, NU, book=True, kalender=FalskKalender())[0].udfald == "booket"


def test_databasen_sletter_og_aendrer_ikke_en_booking(db_session, opsat):
    _kunde(db_session, "Mas A")
    planlaeg(db_session, NU, book=True, kalender=FalskKalender())
    with pytest.raises(DBAPIError, match="slettes aldrig"), db_session.begin_nested():
        db_session.execute(text("DELETE FROM masterafstemning_bookinger"))
    with pytest.raises(DBAPIError, match="kan ikke ændres"), db_session.begin_nested():
        db_session.execute(text("UPDATE masterafstemning_bookinger SET seneste = seneste + 1"))


# --- Forbindelsen til Outlook ------------------------------------------------------------------------------------

HEMMELIGHED = "kal-test-hemmelighed-91ab"


@pytest.fixture
def outlook_til(monkeypatch):
    s = get_settings()
    for felt, v in (("kalender_tenant_id", "tenant"), ("kalender_client_id", "klient"),
                    ("kalender_client_secret", SecretStr(HEMMELIGHED)), ("kalender_postkasse", "nichlas@dinbogholder.dk")):
        monkeypatch.setattr(s, felt, v)


def test_outlook_laeser_optaget_og_opretter(outlook_til):
    kald = []

    def h(req: httpx.Request) -> httpx.Response:
        kald.append(req)
        if "oauth2" in req.url.path:
            return httpx.Response(200, json={"access_token": "adgang-xyz"})
        if req.method == "GET":
            return httpx.Response(200, json={"value": [
                {"start": {"dateTime": "2027-03-01T10:00:00.0000000"}, "end": {"dateTime": "2027-03-01T11:00:00.0000000"},
                 "showAs": "busy"},
                {"start": {"dateTime": "2027-03-01T12:00:00.0000000"}, "end": {"dateTime": "2027-03-01T13:00:00.0000000"},
                 "showAs": "free"}]})
        return httpx.Response(201, json={"id": "AAMk-1"})
    with outlook.Kalender(transport=httpx.MockTransport(h)) as k:
        optagede = k.optaget(datetime(2027, 3, 1), datetime(2027, 3, 2))
        assert optagede == [Optaget(datetime(2027, 3, 1, 10), datetime(2027, 3, 1, 11))]      # "Ledig" tæller ikke
        assert k.opret(datetime(2027, 3, 1, 11), datetime(2027, 3, 1, 16), "Masterafstemning – X", "tekst") == "AAMk-1"
    oprettet = json.loads(kald[-1].content)
    assert kald[-1].url.path == "/v1.0/users/nichlas@dinbogholder.dk/events"
    assert (oprettet["showAs"], oprettet["start"]["timeZone"]) == ("busy", "Romance Standard Time")


def test_outlook_fejl_viser_aldrig_hemmeligheden(outlook_til):
    t = httpx.MockTransport(lambda req: httpx.Response(401, json={"error": HEMMELIGHED}))
    with pytest.raises(outlook.KalenderFejl, match="status 401") as fejl, outlook.Kalender(transport=t) as k:
        k.optaget(datetime(2027, 3, 1), datetime(2027, 3, 2))
    assert HEMMELIGHED not in str(fejl.value)


def test_slaaet_fra_uden_opsaetning(monkeypatch):
    monkeypatch.setattr(get_settings(), "kalender_client_secret", None)
    with pytest.raises(outlook.KalenderFejl, match="ikke slået til"):
        outlook.Kalender()


def test_vises_i_ai_planlaegning(db_session, opsat):
    from app.frister.kalender import Filtre, kommende
    a = _kunde(db_session, "Mas A")
    r = [x for x in kommende(db_session, opsat, date(2027, 2, 10), date(2027, 5, 31), Filtre(afgift="aarsafslutning"))
         ["raekker"] if x["kunde"] == "Mas A"]
    assert [(x["frist"], x["ordning_tekst"]) for x in r] == [
        ("2027-04-30", "Masterafstemning (ekstern revisor) – bookes fra 01.03.2027")]
    planlaeg(db_session, NU, book=True, kalender=FalskKalender())
    r = [x for x in kommende(db_session, opsat, date(2027, 2, 10), date(2027, 5, 31), Filtre(afgift="aarsafslutning"))
         ["raekker"] if x["kunde"] == "Mas A"]
    assert r[0]["ordning_tekst"] == "Masterafstemning (ekstern revisor) – booket 01.03.2027 08:00–13:00" and a.id

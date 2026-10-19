"""Timeregistrering, trin 2: periodeopslag (spec-test 12, 13, 14) og kommandoen til priser og vilkår."""

import re
from contextlib import contextmanager
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import select, text

from app.kunder.models import Client
from app.personale.models import Staff
from app.timeregistrering import satser as sat
from app.timeregistrering import stamdata
from app.timeregistrering.models import TimeAudit, TimeClientTerms


@pytest.fixture
def grund(db_session):
    a = Staff(navn="Sats Admin", email="sats-admin@dinbogholder.dk", rolle="admin", aktiv=True)
    k = Client(navn="Sats ApS", kundenummer="SATS-1", regnskabssystem="economic", status="aktiv")
    db_session.add_all([a, k])
    db_session.flush()
    stamdata.saet_aktoer(db_session, a.id)
    e = stamdata.saet_ansaettelse(db_session, a.id, "fastloennet", False, None)
    return a, k, e


def test_salgsprisen_fra_trin_1_gaelder(db_session):
    assert sat.satser_paa(db_session, date(2026, 10, 18)).salgspris == Decimal("699.00")
    with pytest.raises(sat.ManglerSats, match="ingen salgspris, der gælder 17.10.2026"):
        sat.satser_paa(db_session, date(2026, 10, 17))


# 12. En rapport for marts giver samme tal før og efter en satsændring i april
def test_12_marts_er_uaendret_efter_aendring_i_april(db_session, grund):
    a, k, e = grund
    stamdata.ny_sats(db_session, e, date(2027, 1, 1), Decimal(37), Decimal(400), Decimal(1450), None, a.id)
    sat.nye_vilkaar(db_session, k.id, date(2027, 1, 1), "abonnement", Decimal(1500), a.id)
    marts = date(2027, 3, 15)
    foer = sat.satser_paa(db_session, marts, e.id, k.id)
    sat.ny_salgspris(db_session, date(2027, 4, 1), Decimal("750.00"))
    stamdata.ny_sats(db_session, e, date(2027, 4, 1), Decimal(37), Decimal(450), Decimal(1450), None, a.id)
    sat.nye_vilkaar(db_session, k.id, date(2027, 4, 1), "timeafregning", None, a.id)
    efter = sat.satser_paa(db_session, marts, e.id, k.id)
    assert foer == efter
    assert (efter.salgspris, efter.kostpris, efter.afregning, efter.abonnementspris) == (
        Decimal("699.00"), Decimal("400.00"), "abonnement", Decimal("1500.00"))
    april = sat.satser_paa(db_session, date(2027, 4, 1), e.id, k.id)
    assert (april.salgspris, april.kostpris, april.afregning, april.abonnementspris) == (
        Decimal("750.00"), Decimal("450.00"), "timeafregning", None)


# 13. Kostpris ændret midt i en måned -> linjer bruger hver sin sats
def test_13_kostpris_midt_i_maaneden(db_session, grund):
    a, _, e = grund
    stamdata.ny_sats(db_session, e, date(2027, 5, 1), Decimal(37), Decimal(400), Decimal(1450), None, a.id)
    stamdata.ny_sats(db_session, e, date(2027, 5, 16), Decimal(30), Decimal(420), Decimal(1450), None, a.id)
    assert sat.satser_paa(db_session, date(2027, 5, 15), e.id).kostpris == Decimal("400.00")
    assert sat.satser_paa(db_session, date(2027, 5, 16), e.id).kostpris == Decimal("420.00")
    assert sat.satser_paa(db_session, date(2027, 5, 16), e.id).normtid == Decimal("30.00")
    # Databasens egen funktion giver det samme (rapporterne bruger den direkte).
    assert db_session.scalar(text("SELECT time_kostpris(:e, '2027-05-15')"), {"e": e.id}) == Decimal("400.00")


# 14. Medarbejder/kunde uden gældende sats på en dato -> tydelig fejl, ikke nul
def test_14_ingen_sats_giver_tydelig_fejl(db_session, grund):
    a, k, e = grund
    stamdata.ny_sats(db_session, e, date(2027, 6, 1), None, Decimal(400), Decimal(1450), None, a.id)
    with pytest.raises(sat.ManglerSats, match="Medarbejderen har ingen kostpris, der gælder 31.05.2027"):
        sat.satser_paa(db_session, date(2027, 5, 31), e.id)
    with pytest.raises(sat.ManglerSats, match="Kunden har ingen afregningsform, der gælder 01.06.2027"):
        sat.satser_paa(db_session, date(2027, 6, 1), e.id, k.id)
    assert sat.satser_paa(db_session, date(2027, 6, 1), e.id).normtid is None  # normtid må gerne være tom
    with pytest.raises(sat.ManglerSats, match="altid angives en dato"):
        sat.satser_paa(db_session, None)


def test_vilkaar_og_salgspris_regler(db_session, grund):
    a, k, _ = grund
    with pytest.raises(sat.ManglerSats, match="kræver en abonnementspris"):
        sat.nye_vilkaar(db_session, k.id, date(2027, 1, 1), "abonnement", None, a.id)
    v = sat.nye_vilkaar(db_session, k.id, date(2027, 1, 1), "timeafregning", Decimal(99), a.id)
    assert v.subscription_price is None
    # Samme dato retter; en ny periode ind imellem slutter, hvor den næste begynder.
    sat.nye_vilkaar(db_session, k.id, date(2027, 7, 1), "abonnement", Decimal(2000), a.id)
    sat.nye_vilkaar(db_session, k.id, date(2027, 7, 1), "abonnement", Decimal(2100), a.id)
    sat.nye_vilkaar(db_session, k.id, date(2027, 3, 1), "abonnement", Decimal(1800), a.id)
    perioder = [(x.valid_from, x.valid_to, x.subscription_price) for x in db_session.scalars(
        select(TimeClientTerms).where(TimeClientTerms.client_id == k.id).order_by(TimeClientTerms.valid_from))]
    assert perioder == [(date(2027, 1, 1), date(2027, 3, 1), None), (date(2027, 3, 1), date(2027, 7, 1), Decimal(1800)),
                        (date(2027, 7, 1), None, Decimal(2100))]
    log = db_session.scalars(select(TimeAudit).where(TimeAudit.table_name == "time_client_terms")).all()
    assert log and all(x.actor_id == a.id for x in log)


def test_ingen_opslag_paa_dags_dato():
    """Periodeopslaget og databasens funktioner kigger aldrig på dags dato."""
    kode = Path(sat.__file__).read_text()
    i = kode.index("def satser_paa")
    j = kode.index("# --- Nye priser")
    assert not re.search(r"today\(|now\(|current_date", kode[i:j])
    migrering = next(Path("migrations/versions").glob("*fb5a83b87ff1*")).read_text()
    funktioner = migrering[migrering.index('FUNKTIONER = """'):migrering.index("def upgrade")]
    assert "CREATE FUNCTION time_kostpris" in funktioner
    assert not re.search(r"now\(\)|current_date|current_timestamp", funktioner, re.IGNORECASE)


def test_kommandoen(db_session, grund, monkeypatch, capsys):
    a, k, _ = grund

    @contextmanager
    def samme():
        yield db_session

    import app.db

    monkeypatch.setattr(app.db, "ny_session", samme)
    monkeypatch.setattr(db_session, "commit", db_session.flush)
    assert sat.main(["vilkaar", "--kundenummer", "SATS-1", "--fra", "2027-01-01", "--model", "abonnement",
                     "--pris", "1.500,00", "--af", a.email]) == 0
    assert "Sats ApS: abonnement fra 01.01.2027, 1.500,00 kr. pr. måned." in capsys.readouterr().out
    assert sat.main(["salgspris", "--fra", "2027-02-01", "--pris", "725,00", "--af", a.email]) == 0
    assert sat.main(["vis", "--dato", "2027-02-01", "--medarbejder", a.email, "--kundenummer", "SATS-1"]) == 1
    assert "ingen kostpris" in capsys.readouterr().err
    assert sat.main(["vis", "--dato", "2027-02-01", "--kundenummer", "SATS-1"]) == 0
    ud = capsys.readouterr().out
    assert "725,00 kr." in ud and "abonnement, 1.500,00 kr. pr. måned" in ud
    with pytest.raises(SystemExit):
        sat.main(["salgspris", "--fra", "2027-03-01", "--pris", "725.5", "--af", a.email])
    medarbejder = Staff(navn="Ikke admin", email="ikke-admin-sats@dinbogholder.dk", rolle="medarbejder", aktiv=True)
    db_session.add(medarbejder)
    db_session.flush()
    assert sat.main(["salgspris", "--fra", "2027-03-01", "--pris", "725,00", "--af", medarbejder.email]) == 2

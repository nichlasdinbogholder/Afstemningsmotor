"""Regelrammen, fingerprint og dubletreglen – mod rigtig PostgreSQL.

Hver test lægger sine egne, kendte posteringer ind i entries (`poster`-fixturen),
så man kan læse direkte i testen, hvad reglen burde finde.
"""

import ast
import subprocess
import sys
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

import app.cli
from app.jobs.register import JobKontekst, hent_funktion
from app.kunder.models import Client
from app.regnskab.models import Account, EntryCache
from app.rules import duplicate_entries
from app.rules.base import alle_regler, fingerprint
from app.rules.jobs import planlaeg_regler
from app.rules.koersel import koer_regler
from app.rules.models import Finding, FindingEvent, RuleRun
from app.rules.status import UgyldigStatus, saet_status

ROD = Path(__file__).resolve().parent.parent
REGEL_MAPPE = ROD / "app" / "rules"
REGEL = "duplicate_entries"


# --- Fixtures -------------------------------------------------------------------


BANK = 5820


@pytest.fixture
def kunde(db_session):
    k = Client(navn="Dublet ApS", kundenummer="DUP-1", regnskabssystem="economic")
    db_session.add(k)
    db_session.flush()
    # Kontoplan: 5820 er bankkonto (balancekonto med "bank" i navnet).
    db_session.add(Account(tenant_id=k.id, system="economic", kontonummer=BANK, navn="Bankkonto",
                           kontotype="status", raa_data={}))
    db_session.flush()
    return k


@pytest.fixture
def poster(db_session, kunde):
    """Læg kendte posteringer ind: poster(nr, "2026-04-28", konto, "17516.70", tekst=..., bilag=...).

    Som i et rigtigt regnskab får posteringen sin modpost i banken (konto 5820) i samme bilag
    (posteringsnummer nr + 50000). `bank=False`, når testen selv lægger modposten.
    """
    def tilfoej(nr, dato, konto, beloeb, tekst="Faktura", bilag=None, modpart=None, valuta="DKK",
                entry_type="financeVoucher", bank=True):
        bilag = bilag if bilag is not None else nr
        post = EntryCache(client_id=kunde.id, bogfoert_id=nr, bilagsnummer=bilag,
                          dato=date.fromisoformat(dato), kontonummer=konto, tekst=tekst,
                          beloeb=Decimal(beloeb), modpart=modpart, valuta=valuta,
                          entry_type=entry_type)
        db_session.add(post)
        if bank and konto != BANK:
            post.bankpost = EntryCache(
                client_id=kunde.id, bogfoert_id=nr + 50000, bilagsnummer=bilag,
                dato=date.fromisoformat(dato), kontonummer=BANK, tekst=tekst,
                beloeb=-Decimal(beloeb), valuta=valuta, entry_type=entry_type)
            db_session.add(post.bankpost)
        db_session.flush()
        return post
    return tilfoej


def _fund(session, kunde):
    return session.scalars(select(Finding).where(Finding.client_id == kunde.id, Finding.rule_code == REGEL)
                           .order_by(Finding.id)).all()


def _koer(session, kunde):
    r = koer_regler(session, kunde.id)
    session.flush()
    return next(x for x in r.regler if x.rule_code == REGEL)


# --- Fingerprint og genkørsel -----------------------------------------------------


def test_fingerprint_er_stabilt():
    assert fingerprint(["101", "205"]) == fingerprint(["205", "101"])
    assert fingerprint(["101", "205"]) != fingerprint(["101", "206"])
    assert len(fingerprint(["1", "2"])) == 32


def test_fingerprint_er_stabilt_i_databasen(db_session, kunde, poster):
    """Samme to poster lagt ind i omvendt rækkefølge giver samme fingerprint som i går."""
    poster(205, "2026-04-30", 1310, "17516.70")
    poster(101, "2026-04-28", 1310, "17516.70")
    _koer(db_session, kunde)
    # Fingerprintet dækker alle linjer i bilagsparret – også banklinjerne (nr + 50000).
    assert [f.fingerprint for f in _fund(db_session, kunde)] == [fingerprint(["101", "205", "50101", "50205"])]


def test_genkoersel_laver_ikke_dubletfund(db_session, kunde, poster):
    poster(1, "2026-04-28", 1310, "17516.70")
    poster(2, "2026-04-30", 1310, "17516.70")
    poster(3, "2026-05-02", 6000, "500.00")
    poster(4, "2026-05-02", 6000, "500.00")

    foerste = _koer(db_session, kunde)
    anden = _koer(db_session, kunde)

    assert (foerste.fundet, foerste.nye) == (2, 2)
    assert (anden.fundet, anden.nye, anden.set_igen) == (2, 0, 2)
    assert len(_fund(db_session, kunde)) == 2


def test_status_overlever_genkoersel(db_session, kunde, poster):
    poster(1, "2026-04-28", 1310, "17516.70")
    poster(2, "2026-04-30", 1310, "17516.70")
    _koer(db_session, kunde)
    fund = _fund(db_session, kunde)[0]
    saet_status(db_session, fund.id, "accepted", actor="berit@example.invalid", note="Set og godkendt")

    _koer(db_session, kunde)
    db_session.expire_all()

    efter = _fund(db_session, kunde)
    assert [(f.id, f.status) for f in efter] == [(fund.id, "accepted")]


def test_fund_bliver_staaende_naar_problemet_forsvinder(db_session, kunde, poster):
    """Et fund slettes aldrig – last_seen_at bliver bare stående."""
    poster(1, "2026-04-28", 1310, "100.00")
    p2 = poster(2, "2026-04-29", 1310, "100.00")
    _koer(db_session, kunde)
    db_session.execute(text("UPDATE findings SET last_seen_at = '2026-01-01' WHERE client_id = :k"), {"k": kunde.id})
    db_session.delete(p2)
    db_session.delete(p2.bankpost)
    db_session.flush()

    assert _koer(db_session, kunde).fundet == 0
    db_session.expire_all()
    fund = _fund(db_session, kunde)
    assert len(fund) == 1 and fund[0].last_seen_at.year == 2026 and fund[0].last_seen_at.month == 1


# --- Dubletreglens kriterier ------------------------------------------------------


def test_modsat_fortegn_er_ikke_dublet(db_session, kunde, poster):
    poster(1, "2026-04-28", 1310, "5000.00")
    poster(2, "2026-04-28", 1310, "-5000.00")
    assert _koer(db_session, kunde).fundet == 0


def test_48_tilbageforte_betalinger_giver_nul_fund(db_session, kunde, poster):
    """Bogført og tilbageført igen (samme beløb, modsat fortegn) – aldrig en dublet."""
    for i in range(48):
        dag = (date(2026, 3, 1) + timedelta(days=i * 8)).isoformat()
        poster(1000 + 2 * i, dag, 5820, f"{1000 + i}.00", bilag=5000 + i)
        poster(1001 + 2 * i, dag, 5820, f"-{1000 + i}.00", bilag=6000 + i)
    assert _koer(db_session, kunde).fundet == 0


def test_fast_maanedlig_betaling_er_ikke_dublet(db_session, kunde, poster):
    poster(1, "2026-01-01", 2210, "12000.00", tekst="Husleje")
    poster(2, "2026-01-31", 2210, "12000.00", tekst="Husleje")
    poster(3, "2026-03-02", 2210, "12000.00", tekst="Husleje")
    assert _koer(db_session, kunde).fundet == 0


def test_vinduet_er_3_dage(db_session, kunde, poster):
    assert duplicate_entries.VINDUE_DAGE == 3
    poster(1, "2026-04-01", 1310, "250.00")
    poster(2, "2026-04-04", 1310, "250.00")  # 3 dage: med
    poster(3, "2026-04-08", 1310, "250.00")  # 4 dage efter nr. 2: ikke med
    _koer(db_session, kunde)
    fund = _fund(db_session, kunde)
    assert [f.detail["dage_imellem"] for f in fund] == [3]


def test_eksakt_dublet_er_high(db_session, kunde, poster):
    poster(1, "2026-04-28", 1310, "17516.70", tekst="Faktura 4711")
    poster(2, "2026-04-28", 1310, "17516.70", tekst="Faktura 4711")
    _koer(db_session, kunde)
    fund = _fund(db_session, kunde)
    assert [f.severity for f in fund] == ["high"]
    assert fund[0].title == ("Muligt dobbeltbogført beløb: 17.516,70 kr. på konto 1310 den 28.04.2026 "
                             "(bilag 1 og 2, 4 linjer)")


def test_samme_konto_inden_for_vinduet_er_medium(db_session, kunde, poster):
    poster(1, "2026-04-28", 1310, "17516.70", tekst="Faktura 4711")
    poster(2, "2026-04-30", 1310, "17516.70", tekst="  faktura   4711 ")  # store/små bogstaver og mellemrum
    _koer(db_session, kunde)
    fund = _fund(db_session, kunde)
    assert [f.severity for f in fund] == ["medium"]
    assert fund[0].title == ("Muligt dobbeltbogført beløb: 17.516,70 kr. på konto 1310 den 28.04 og 30.04 "
                             "(bilag 1 og 2, 4 linjer)")


def test_forskellig_tekst_er_ikke_dublet(db_session, kunde, poster):
    """D: de 5 kontrollerede fund fra version 3 – kunden står kun i teksten."""
    poster(1, "2021-12-01", 6902, "-1599.50", tekst="Invoice 22060 (#205)", bilag=909103)
    poster(2, "2021-12-01", 6902, "-1599.50", tekst="Invoice 22067 (#225)", bilag=909110)
    poster(3, "2024-11-04", 1110, "-2500.00", tekst="Grønne Leverum ApS", bilag=6595)
    poster(4, "2024-11-06", 1110, "-2500.00", tekst="Skærbæk Stillads ApS", bilag=6607)
    assert _koer(db_session, kunde).fundet == 0


def test_forskellige_konti_er_ikke_dublet(db_session, kunde, poster):
    """Version 2: en dobbeltbogføring gentager sig på SAMME konto. Ens runde beløb på
    forskellige konti (56 af 59 fund på demo-aftalen) er ikke et fund – heller ikke selvom
    banklinjerne (samme beløb, samme tekst) går igen."""
    poster(1, "2026-04-28", 1310, "800.00")
    poster(2, "2026-04-29", 2750, "800.00")
    poster(3, "2026-04-28", 1020, "-1000.00")
    poster(4, "2026-04-28", 2210, "-1000.00")
    assert _koer(db_session, kunde).fundet == 0


def test_titlen_viser_aeldste_dato_foerst(db_session, kunde, poster):
    poster(1, "2026-06-04", 6903, "60.00")
    poster(2, "2026-06-01", 6903, "60.00")
    _koer(db_session, kunde)
    assert "den 01.06 og 04.06" in _fund(db_session, kunde)[0].title


def test_detail_har_begge_posteringer(db_session, kunde, poster):
    a = poster(11, "2026-04-28", 1310, "17516.70", tekst="Faktura 4711", bilag=900)
    b = poster(12, "2026-04-30", 1310, "17516.70", tekst="Faktura 4711", bilag=901)
    _koer(db_session, kunde)
    fund = _fund(db_session, kunde)[0]
    assert fund.entry_ids == sorted([a.id, b.id, a.bankpost.id, b.bankpost.id])
    assert (fund.period_start, fund.period_end) == (date(2026, 4, 28), date(2026, 4, 30))
    assert [p["posteringsnummer"] for p in fund.detail["posteringer"]] == [11, 50011, 12, 50012]
    assert fund.detail["posteringer"][0] == {
        "posteringsnummer": 11, "dato": "2026-04-28", "konto": 1310, "tekst": "Faktura 4711",
        "bilagsnummer": 900, "modpart": None, "beloeb": "17516.70"}
    assert fund.detail["posteringer"][3] == {
        "posteringsnummer": 50012, "dato": "2026-04-30", "konto": 5820, "tekst": "Faktura 4711",
        "bilagsnummer": 901, "modpart": None, "beloeb": "-17516.70"}


def test_linjer_fra_samme_bilag_er_ikke_dubletter(db_session, kunde, poster):
    poster(1, "2026-04-28", 6000, "100.00", bilag=77)
    poster(2, "2026-04-28", 6010, "100.00", bilag=77)
    assert _koer(db_session, kunde).fundet == 0


def test_forskellig_modpart_er_ikke_dublet(db_session, kunde, poster):
    poster(1, "2026-04-28", 6800, "100.00", modpart="kreditor:1")
    poster(2, "2026-04-28", 6800, "100.00", modpart="kreditor:2")
    poster(3, "2026-04-28", 6800, "200.00", modpart="kreditor:1")
    poster(4, "2026-04-29", 6800, "200.00", modpart="kreditor:1")
    _koer(db_session, kunde)
    assert [f.detail["beloeb"] for f in _fund(db_session, kunde)] == ["200.00"]


def _faktura(poster, nr, bilag, dato, kunde, beloeb="10200.00", konto=1010, tekst="Månedligt honorar"):
    """En salgsfaktura som i e-conomic: debitorlinje (med kunde), salgslinje og momslinje."""
    b = Decimal(beloeb)
    poster(nr, dato, 5600, str(b * Decimal("1.25")), tekst=tekst, bilag=bilag, modpart=kunde, bank=False)
    poster(nr + 1, dato, konto, str(-b), tekst=tekst, bilag=bilag, bank=False)
    poster(nr + 2, dato, 6902, str(-b / 4), tekst=tekst, bilag=bilag, bank=False)


def test_samme_pris_til_forskellige_kunder_er_ikke_dublet(db_session, kunde, poster):
    """A: faste honorarer til mange kunder med samme pris (målt: 10.504 af 12.053 fund)."""
    _faktura(poster, 10, 501, "2026-05-31", "debitor:1")
    _faktura(poster, 20, 502, "2026-05-31", "debitor:2")
    _faktura(poster, 30, 503, "2026-05-31", "debitor:3")
    assert _koer(db_session, kunde).fundet == 0


def test_dobbelt_faktura_uden_bank_er_ikke_dublet(db_session, kunde, poster):
    """I: bogholderen – en dobbeltbogføring kræver, at modkontoen er bank i balancen.
    En faktura bogført to gange mod debitorkontoen er derfor ikke et fund."""
    _faktura(poster, 10, 501, "2026-05-31", "debitor:7")
    _faktura(poster, 20, 502, "2026-06-01", "debitor:7")
    assert _koer(db_session, kunde).fundet == 0


def test_uden_kontoplan_ingen_bankkonti_og_ingen_fund(db_session, kunde, poster):
    db_session.execute(text("DELETE FROM accounts WHERE tenant_id = :k"), {"k": kunde.id})
    poster(1, "2026-04-28", 1310, "100.00")
    poster(2, "2026-04-28", 1310, "100.00")
    assert _koer(db_session, kunde).fundet == 0


def _udgift_med_moms(poster, nr, bilag, dato, tekst, beloeb="800.00", konto=2770):
    """Udgift med moms betalt fra banken: udgifts-, moms- og banklinje i samme bilag."""
    b = Decimal(beloeb)
    poster(nr, dato, konto, str(b), tekst=tekst, bilag=bilag, bank=False)
    poster(nr + 1, dato, 6903, str(b / 4), tekst=tekst, bilag=bilag, bank=False)
    poster(nr + 2, dato, BANK, str(-b * Decimal("1.25")), tekst=tekst, bilag=bilag)


def test_et_fund_pr_bilagspar(db_session, kunde, poster):
    """C: udgifts-, moms- og banklinje fra samme dobbeltbogføring giver ÉT fund, ikke tre."""
    _udgift_med_moms(poster, 10, 501, "2026-05-31", "Kontorstol")
    _udgift_med_moms(poster, 20, 502, "2026-05-31", "Kontorstol")
    _koer(db_session, kunde)
    fund = _fund(db_session, kunde)
    assert len(fund) == 1
    f = fund[0]
    assert len(f.entry_ids) == 6 and f.detail["linjepar"] == 3 and f.detail["bilag"] == ["501", "502"]
    assert f.title == ("Muligt dobbeltbogført beløb: 800,00 kr. på konto 2770 den 31.05.2026 "
                       "(bilag 501 og 502, 6 linjer)")
    assert f.fingerprint == fingerprint([str(n) for n in (10, 11, 12, 20, 21, 22)])


def _periodisering(poster, start_nr, bilag, maaneder, aar=2022):
    nr = start_nr
    for maaned in maaneder:
        dag = f"{aar}-{maaned:02d}-01"
        tekst = f"Best One - periodisering {maaned}"
        poster(nr, dag, 2805, "1066.66", tekst=tekst, bilag=bilag)
        poster(nr + 1, dag, 5660, "-1333.33", tekst=tekst, bilag=bilag)
        poster(nr + 2, dag, 6903, "266.67", tekst=tekst, bilag=bilag)
        nr += 3


def test_periodisering_er_ikke_dublet(db_session, kunde, poster):
    """E: to ens periodiseringer (bilag 50301 og 50302, samme bilagsnummer hver måned)."""
    _periodisering(poster, 1, 50301, range(1, 13))
    _periodisering(poster, 1000, 50302, range(1, 13))
    assert _koer(db_session, kunde).fundet == 0


def test_bilagsnummer_der_gaar_igen_aar_efter_aar_er_ikke_periodisering(db_session, kunde, poster):
    """Starter nummereringen forfra hvert år, må bilag 5 i 2025 og 2026 ikke skjule en dublet."""
    poster(1, "2025-03-10", 6000, "100.00", tekst="Gammelt bilag 5", bilag=5)
    poster(2, "2026-03-09", 6000, "999.00", tekst="Nyt bilag 5", bilag=5)
    poster(3, "2026-03-10", 6000, "999.00", tekst="Nyt bilag 5", bilag=6)
    assert _koer(db_session, kunde).fundet == 1


def test_et_bilag_med_linjer_paa_to_datoer_er_et_fund(db_session, kunde, poster):
    """C: linjepar mellem samme to bilag på flere datoer samles til ét fund (uden at være periodisering)."""
    for bilag, start in ((50301, 1), (50302, 100)):
        poster(start, "2022-01-01", 6000, "500.00", tekst="Bilag i to dele", bilag=bilag)
        poster(start + 1, "2022-01-02", 6010, "250.00", tekst="Bilag i to dele", bilag=bilag)
    _koer(db_session, kunde)
    fund = _fund(db_session, kunde)
    assert len(fund) == 1 and len(fund[0].entry_ids) == 8  # 4 linjer + 4 banklinjer


def _konto(session, kunde, nr, kontotype):
    session.add(Account(tenant_id=kunde.id, system="economic", kontonummer=nr, navn=f"Konto {nr}",
                        kontotype=kontotype, raa_data={}))
    session.flush()


def _bankudgift(poster, nr, bilag, dato, tekst, beloeb, konto):
    """Udgift betalt fra banken: udgiftslinje + banklinje i samme bilag."""
    poster(nr, dato, konto, beloeb, tekst=tekst, bilag=bilag)
    poster(nr + 1, dato, 5820, str(-Decimal(beloeb)), tekst=tekst, bilag=bilag)


def test_aub_samme_reference_to_gange_er_et_fund(db_session, kunde, poster):
    """Den første BEKRÆFTEDE dobbeltbogføring (bogholderens kontrol 04.10.2026)."""
    _konto(db_session, kunde, 2212, "profitAndLoss")
    _bankudgift(poster, 1, 20859, "2023-01-24", "AUB-BEFOR 1603801527", "-320.00", 2212)
    _bankudgift(poster, 3, 20860, "2023-01-24", "AUB-BEFOR 1603801527", "-320.00", 2212)
    _koer(db_session, kunde)
    fund = _fund(db_session, kunde)
    assert len(fund) == 1 and fund[0].severity == "high" and fund[0].detail["bilag"] == ["20859", "20860"]


def test_rettet_senere_er_ikke_dublet(db_session, kunde, poster):
    """F: EasyPark bogført to gange og rettet 3 uger senere på driftskontoen."""
    _konto(db_session, kunde, 2770, "profitAndLoss")
    _bankudgift(poster, 1, 20942, "2023-05-30", "Forretning: EasyPark A/S", "169.00", 2770)
    _bankudgift(poster, 3, 20943, "2023-05-30", "Forretning: EasyPark A/S", "169.00", 2770)
    poster(9, "2023-06-20", 2770, "-169.00", tekst="Rettelse EasyPark", bilag=21000)
    assert _koer(db_session, kunde).fundet == 0


def test_modsat_beloeb_paa_statuskonto_er_ikke_en_rettelse(db_session, kunde, poster):
    """F gælder kun driftskonti: på debitorkontoen er et modsat beløb bare betalingen."""
    _konto(db_session, kunde, 5600, "status")
    poster(1, "2026-05-01", 5600, "1250.00", tekst="Faktura 77 Kunde A", bilag=1, modpart="debitor:1")
    poster(2, "2026-05-02", 5600, "1250.00", tekst="Faktura 77 Kunde A", bilag=2, modpart="debitor:1")
    poster(3, "2026-05-20", 5600, "-1250.00", tekst="Indbetaling", bilag=3, modpart="debitor:1")
    assert _koer(db_session, kunde).fundet == 1


def test_ukendt_kontotype_taeller_ikke_som_rettelse(db_session, kunde, poster):
    """Er kontoplanen ikke hentet, ved vi ikke om det er en driftskonto – fundet bliver stående."""
    _bankudgift(poster, 1, 1, "2023-05-30", "EasyPark", "169.00", 2770)
    _bankudgift(poster, 3, 2, "2023-05-30", "EasyPark", "169.00", 2770)
    poster(9, "2023-06-20", 2770, "-169.00", tekst="Rettelse", bilag=3)
    assert _koer(db_session, kunde).fundet == 1


def test_rettelse_med_samme_bilagsnummer_er_ikke_dublet(db_session, kunde, poster):
    """H: bilag 100 bogført, tilbageført i SAMME bilag 10 dage senere, og bogført igen som
    bilag 101. Tilbageførslen ligger på bankkontoen – også dér tæller den."""
    _bankudgift(poster, 1, 100, "2023-05-30", "Forsikring", "2400.00", 2600)
    poster(3, "2023-06-09", 2600, "-2400.00", tekst="Forsikring", bilag=100)
    poster(4, "2023-06-09", 5820, "2400.00", tekst="Forsikring", bilag=100)
    _bankudgift(poster, 5, 101, "2023-05-30", "Forsikring", "2400.00", 2600)
    assert _koer(db_session, kunde).fundet == 0


def test_genbrugt_bilagsnummer_fra_et_andet_aar_er_ikke_en_rettelse(db_session, kunde, poster):
    """H må ikke snydes af et bilagsnummer, der går igen år senere (nummerering forfra)."""
    _bankudgift(poster, 1, 100, "2023-05-30", "Forsikring", "2400.00", 2600)
    _bankudgift(poster, 3, 101, "2023-05-30", "Forsikring", "2400.00", 2600)
    poster(9, "2025-05-30", 2600, "-2400.00", tekst="Andet bilag 100", bilag=100)
    assert _koer(db_session, kunde).fundet == 1


def test_faktura_og_betaling_er_ikke_dublet(db_session, kunde, poster):
    """G: samme kunder og beløb på debitorkontoen, men den ene er en betaling."""
    poster(1, "2021-09-09", 5600, "-9058.75", tekst="Pladeværkstedet Amagerstrand ApS", bilag=80158,
           entry_type="manualDebtorInvoice")
    poster(2, "2021-09-10", 5600, "-9058.75", tekst="Pladeværkstedet Amagerstrand ApS", bilag=80159,
           entry_type="customerPayment")
    assert _koer(db_session, kunde).fundet == 0


def test_dobbeltbogfoert_og_tilbagefoert_er_ikke_dublet(db_session, kunde, poster):
    """B: betalingen er bogført to gange, og den ene er tilbageført igen – sagen er udlignet.
    (Det rigtige regnskab med 48 sådanne betalinger ville ellers have givet 48+ falske fund.)"""
    for i in range(48):
        dag = (date(2026, 1, 5) + timedelta(days=i * 7)).isoformat()
        beloeb = f"{2000 + i}.00"
        poster(1000 + 3 * i, dag, 5820, beloeb, tekst="Indbetaling", bilag=7000 + 3 * i)
        poster(1001 + 3 * i, dag, 5820, beloeb, tekst="Indbetaling", bilag=7001 + 3 * i)
        poster(1002 + 3 * i, dag, 5820, f"-{beloeb}", tekst="Tilbageført", bilag=7002 + 3 * i)
    assert _koer(db_session, kunde).fundet == 0


def test_andre_kunders_poster_blandes_ikke_ind(db_session, kunde, poster):
    anden = Client(navn="Anden ApS", kundenummer="DUP-2", regnskabssystem="economic")
    db_session.add(anden)
    db_session.flush()
    poster(1, "2026-04-28", 1310, "100.00")
    db_session.add(EntryCache(client_id=anden.id, bogfoert_id=2, bilagsnummer=2, dato=date(2026, 4, 28),
                              kontonummer=1310, tekst="Faktura", beloeb=Decimal("100.00"), valuta="DKK"))
    db_session.flush()
    assert _koer(db_session, kunde).fundet == 0


def test_hver_koersel_noteres_i_rule_runs(db_session, kunde, poster):
    poster(1, "2026-04-28", 1310, "100.00")
    poster(2, "2026-04-28", 1310, "100.00")
    _koer(db_session, kunde)
    _koer(db_session, kunde)
    koersler = db_session.scalars(select(RuleRun).where(RuleRun.client_id == kunde.id)).all()
    assert [(k.rule_code, k.rule_version, k.fund) for k in koersler] == [(REGEL, 9, 1), (REGEL, 9, 1)]


# --- Status og log ---------------------------------------------------------------


def _haendelser(session, finding_id):
    return session.scalars(select(FindingEvent).where(FindingEvent.finding_id == finding_id)
                           .order_by(FindingEvent.id)).all()


def test_statusaendring_logges(db_session, kunde, poster):
    poster(1, "2026-04-28", 1310, "100.00")
    poster(2, "2026-04-28", 1310, "100.00")
    _koer(db_session, kunde)
    fund = _fund(db_session, kunde)[0]
    foer = _haendelser(db_session, fund.id)
    assert [(h.from_status, h.to_status, h.actor) for h in foer] == [(None, "open", "system")]

    saet_status(db_session, fund.id, "resolved", actor="berit", note="Kreditnota bogført")

    nye = _haendelser(db_session, fund.id)[len(foer):]
    assert len(nye) == 1
    h = nye[0]
    assert (h.from_status, h.to_status, h.actor, h.note) == ("open", "resolved", "berit", "Kreditnota bogført")
    assert h.created_at is not None


def test_statusaendring_uden_actor_afvises_af_databasen(db_session, kunde, poster):
    poster(1, "2026-04-28", 1310, "100.00")
    poster(2, "2026-04-28", 1310, "100.00")
    _koer(db_session, kunde)
    fund = _fund(db_session, kunde)[0]
    with pytest.raises(DBAPIError, match="kan kun ændres med en actor"):
        with db_session.begin_nested():
            db_session.execute(text("UPDATE findings SET status = 'ignored' WHERE id = :id"), {"id": fund.id})
    with pytest.raises(UgyldigStatus):
        saet_status(db_session, fund.id, "ignored", actor="  ")


def test_fund_og_haendelser_kan_ikke_slettes(db_session, kunde, poster):
    poster(1, "2026-04-28", 1310, "100.00")
    poster(2, "2026-04-28", 1310, "100.00")
    _koer(db_session, kunde)
    for sql in ("DELETE FROM findings", "DELETE FROM finding_events", "UPDATE finding_events SET actor = 'x'"):
        with pytest.raises(DBAPIError):
            with db_session.begin_nested():
                db_session.execute(text(sql))


def test_ingen_aendring_ingen_log(db_session, kunde, poster):
    poster(1, "2026-04-28", 1310, "100.00")
    poster(2, "2026-04-28", 1310, "100.00")
    _koer(db_session, kunde)
    fund = _fund(db_session, kunde)[0]
    saet_status(db_session, fund.id, "open", actor="berit")
    assert len(_haendelser(db_session, fund.id)) == 1  # kun "oprettet"


# --- Rammen, job og kommandoer ---------------------------------------------------


def test_regel_er_registreret():
    regler = {r.code: r for r in alle_regler()}
    assert regler[REGEL].name_da == "Muligt dobbeltbogført beløb"
    assert regler[REGEL].version == 9


def test_jobtypen_run_rules(db_session, kunde, poster):
    poster(1, "2026-04-28", 1310, "100.00")
    poster(2, "2026-04-28", 1310, "100.00")
    hent_funktion("run_rules")(JobKontekst(job_id=1, type="run_rules", client_id=kunde.id, payload={},
                                           forsoeg=1, max_forsoeg=5, session=db_session))
    assert len(_fund(db_session, kunde)) == 1


def test_planlaeg_regler_er_idempotent(db_session, kunde):
    pause = Client(navn="Pause ApS", kundenummer="DUP-P", regnskabssystem="economic", status="pause")
    db_session.add(pause)
    db_session.flush()
    planlaeg_regler(db_session, date(2026, 12, 24))
    planlaeg_regler(db_session, date(2026, 12, 24))
    noegler = set(db_session.scalars(text("SELECT idempotens_noegle FROM jobs WHERE type = 'run_rules' "
                                          "AND idempotens_noegle LIKE :m"), {"m": "%:2026-12-24"}))
    assert len(noegler) == db_session.scalar(select(func.count()).select_from(Client)
                                             .where(Client.status == "aktiv"))
    assert f"rules:{kunde.id}:2026-12-24" in noegler
    assert f"rules:{pause.id}:2026-12-24" not in noegler


def test_cli_run_rules_findings_og_set_status(db_session, kunde, poster, monkeypatch, capsys):
    from contextlib import contextmanager

    @contextmanager
    def samme_session():
        yield db_session

    monkeypatch.setattr(app.cli, "ny_session", samme_session)
    monkeypatch.setenv("USER", "berit")
    poster(1, "2026-04-28", 1310, "17516.70")
    poster(2, "2026-04-28", 1310, "17516.70")

    assert app.cli.run_rules(kunde.id) == 0
    assert "fund:     1   nye:     1" in capsys.readouterr().out

    assert app.cli.vis_findings(kunde.id, status="open", severity="high") == 0
    ud = capsys.readouterr().out
    assert "high" in ud and "17.516,70 kr." in ud and "1 fund" in ud

    fund = _fund(db_session, kunde)[0]
    assert app.cli.set_status(fund.id, "ignored", "Legitim") == 0
    assert "open -> ignored (af berit)" in capsys.readouterr().out
    assert _haendelser(db_session, fund.id)[-1].actor == "berit"

    assert app.cli.vis_findings(kunde.id, status="open") == 0
    assert "Ingen fund." in capsys.readouterr().out


def test_cli_findings_skjuler_fund_der_ikke_laengere_optraeder(db_session, kunde, poster, monkeypatch, capsys):
    """Fundet slettes ikke, men vises kun med --alle, når seneste kørsel ikke fandt det."""
    from contextlib import contextmanager

    @contextmanager
    def samme_session():
        yield db_session

    monkeypatch.setattr(app.cli, "ny_session", samme_session)
    poster(1, "2026-04-28", 1310, "100.00")
    p2 = poster(2, "2026-04-28", 1310, "100.00")
    _koer(db_session, kunde)
    db_session.delete(p2.bankpost)
    # Næste kørsel sker "senere" (now() er fast i en transaktion, så vi flytter tiden tilbage).
    db_session.execute(text("UPDATE findings SET last_seen_at = last_seen_at - interval '1 day', "
                            "first_seen_at = first_seen_at - interval '1 day' WHERE client_id = :k"),
                       {"k": kunde.id})
    db_session.execute(text("UPDATE rule_runs SET koert_at = koert_at - interval '1 day' WHERE client_id = :k"),
                       {"k": kunde.id})
    db_session.delete(p2)
    db_session.flush()
    _koer(db_session, kunde)
    capsys.readouterr()

    app.cli.vis_findings(kunde.id)
    ud = capsys.readouterr().out
    assert "Ingen fund." in ud and "1 ældre fund optræder ikke længere" in ud

    app.cli.vis_findings(kunde.id, alle=True)
    ud = capsys.readouterr().out
    assert "aktuel" in ud and " nej " in ud and "1 fund" in ud
    assert len(_fund(db_session, kunde)) == 1  # stadig i databasen


# --- Regler må aldrig tale med et regnskabssystem --------------------------------


FORBUDTE_IMPORTER = ("app.adaptere", "app.synk", "httpx", "requests", "urllib")


def test_regler_skriver_ikke_til_provider():
    """Fejler, hvis noget i app/rules/ importerer adapter-/provider-laget eller netværk."""
    brud = []
    for fil in REGEL_MAPPE.rglob("*.py"):
        for node in ast.walk(ast.parse(fil.read_text(encoding="utf-8"))):
            navne = []
            if isinstance(node, ast.Import):
                navne = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                navne = [node.module]
            brud += [f"{fil.name}:{node.lineno} {n}" for n in navne
                     if n.startswith(FORBUDTE_IMPORTER) or "economic" in n or "dinero" in n]
    assert not brud, "Regler må kun arbejde på vores egen database:\n" + "\n".join(brud)


def test_regler_trækker_heller_ikke_provider_ind_ad_omveje():
    kode = ("import sys, app.rules.koersel, app.rules.duplicate_entries, app.rules.status\n"
            "from app.rules.base import alle_regler; alle_regler()\n"
            "bad = sorted(m for m in sys.modules if m.startswith(('app.adaptere', 'httpx')))\n"
            "assert not bad, bad\n")
    r = subprocess.run([sys.executable, "-c", kode], cwd=ROD, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-800:]

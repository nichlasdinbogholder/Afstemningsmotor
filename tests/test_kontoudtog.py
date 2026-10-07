"""Matchmotoren: kontoudtog mod bogføringen – tre trin, fund begge veje, matchprocent."""

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import select, text

from app.kontoudtog.importer import IndlaesningsFejl, indlaes, laes_csv, tolk_beloeb, tolk_dato
from app.kontoudtog.models import StatementLine
from app.kunder.models import Client
from app.regnskab.models import EntryCache
from app.rules.koersel import koer_regler
from app.rules.models import Finding

ROD = Path(__file__).resolve().parent.parent
I_BOGF, PAA_UDTOG = "mangler_i_bogfoering", "mangler_paa_kontoudtog"


@pytest.fixture
def kunde(db_session):
    k = Client(navn="Match ApS", kundenummer="MATCH-1", regnskabssystem="economic")
    db_session.add(k)
    db_session.flush()
    return k


@pytest.fixture
def post(db_session, kunde):
    """Bogført postering på leverandør 45 (konto 6800 Kreditorer)."""
    def lav(nr, dato, beloeb, bilag=None, modpart="kreditor:45", konto=6800):
        e = EntryCache(client_id=kunde.id, bogfoert_id=nr, bilagsnummer=bilag if bilag is not None else nr,
                       dato=date.fromisoformat(dato), kontonummer=konto, tekst=f"Post {nr}",
                       beloeb=Decimal(beloeb), modpart=modpart, valuta="DKK", entry_type="supplierInvoice")
        db_session.add(e)
        db_session.flush()
        return e
    return lav


def _udtog(session, kunde, linjer, kilde="grossist", fortegn="modsat", modpart="kreditor:45", konto=None,
           fra="2026-09-01", til="2026-09-30"):
    return indlaes(session, kunde.id, kilde, date.fromisoformat(fra), date.fromisoformat(til), fortegn,
                   [{"linje_nr": i, "dato": date.fromisoformat(d), "reference": ref, "tekst": f"Linje {i}",
                     "beloeb": Decimal(b), "raa_data": None} for i, (d, ref, b) in enumerate(linjer, 1)],
                   kontonummer=konto, modpart=modpart, kildefil="test.csv")


def _koer(session, kunde):
    koer_regler(session, kunde.id)
    session.flush()


def _fund(session, kunde, regel):
    return session.scalars(select(Finding).where(Finding.client_id == kunde.id, Finding.rule_code == regel)
                           .order_by(Finding.id)).all()


def _match(session, udtog):
    return {l.linje_nr: l.match_trin for l in session.scalars(
        select(StatementLine).where(StatementLine.statement_id == udtog.id))}


# --- De tre trin -----------------------------------------------------------------------


def test_trin1_eksakt_paa_bilagsnummer_og_beloeb(db_session, kunde, post):
    post(1, "2026-09-03", "-1000.00", bilag=7001)
    post(2, "2026-09-03", "-1000.00", bilag=7002)
    u = _udtog(db_session, kunde, [("2026-09-03", "7002", "1000.00")])  # grossisten skriver +1.000
    _koer(db_session, kunde)
    linje = db_session.scalars(select(StatementLine).where(StatementLine.statement_id == u.id)).one()
    assert linje.match_trin == "bilag_beloeb"
    assert db_session.get(EntryCache, linje.match_entry_id).bilagsnummer == 7002


def test_trin2_beloeb_og_dato_inden_for_5_dage(db_session, kunde, post):
    post(1, "2026-09-08", "-500.00", bilag=1)
    u = _udtog(db_session, kunde, [("2026-09-03", "FAKT-99", "500.00")])  # ingen fælles reference
    _koer(db_session, kunde)
    assert _match(db_session, u) == {1: "beloeb_dato"}


def test_6_dage_er_for_langt_og_giver_fund_begge_veje(db_session, kunde, post):
    post(1, "2026-09-09", "-500.00")
    u = _udtog(db_session, kunde, [("2026-09-03", None, "500.00")])
    _koer(db_session, kunde)
    assert _match(db_session, u) == {1: None}
    i_bogf, paa_udtog = _fund(db_session, kunde, I_BOGF), _fund(db_session, kunde, PAA_UDTOG)
    assert len(i_bogf) == 1 and len(paa_udtog) == 1
    assert i_bogf[0].title.startswith("Mangler i bogføring: 500,00 kr. den 03.09.2026")
    assert paa_udtog[0].title.startswith("Mangler på kontoudtog: -500,00 kr. bogført 09.09.2026")
    assert paa_udtog[0].entry_ids == [db_session.scalar(select(EntryCache.id).where(
        EntryCache.client_id == kunde.id, EntryCache.bogfoert_id == 1))]


def test_trin1_foer_trin2(db_session, kunde, post):
    """To ens beløb: linjen med referencen skal have posteringen med samme bilagsnummer –
    også selvom den anden postering ligger tættere på i dato."""
    post(1, "2026-09-10", "-300.00", bilag=10)
    post(2, "2026-09-03", "-300.00", bilag=20)
    u = _udtog(db_session, kunde, [("2026-09-03", "10", "300.00"), ("2026-09-03", None, "300.00")])
    _koer(db_session, kunde)
    linjer = {l.linje_nr: l for l in db_session.scalars(
        select(StatementLine).where(StatementLine.statement_id == u.id))}
    assert linjer[1].match_trin == "bilag_beloeb"
    assert db_session.get(EntryCache, linjer[1].match_entry_id).bilagsnummer == 10
    assert linjer[2].match_trin == "beloeb_dato"
    assert db_session.get(EntryCache, linjer[2].match_entry_id).bilagsnummer == 20


def test_en_postering_bruges_kun_een_gang(db_session, kunde, post):
    post(1, "2026-09-03", "-200.00")
    u = _udtog(db_session, kunde, [("2026-09-03", None, "200.00"), ("2026-09-04", None, "200.00")])
    _koer(db_session, kunde)
    assert sorted(v or "" for v in _match(db_session, u).values()) == ["", "beloeb_dato"]
    assert len(_fund(db_session, kunde, I_BOGF)) == 1


def test_samme_fortegn_til_fx_skattekontoen(db_session, kunde, post):
    post(1, "2026-09-20", "-4250.00", modpart=None, konto=6710)
    u = _udtog(db_session, kunde, [("2026-09-20", None, "-4250.00")], kilde="skattekonto",
               fortegn="samme", modpart=None, konto=6710)
    _koer(db_session, kunde)
    assert _match(db_session, u) == {1: "beloeb_dato"}


def test_motoren_er_uafhaengig_af_kilden(db_session, kunde, post):
    post(1, "2026-09-03", "-750.00")
    resultater = []
    for kilde in ("grossist", "skattekonto", "bank"):
        u = _udtog(db_session, kunde, [("2026-09-03", "1", "750.00")], kilde=kilde)
        _koer(db_session, kunde)
        resultater.append(_match(db_session, u))
    assert resultater == [{1: "bilag_beloeb"}] * 3


def test_andre_leverandoerer_blandes_ikke_ind(db_session, kunde, post):
    post(1, "2026-09-03", "-100.00", modpart="kreditor:99")
    _udtog(db_session, kunde, [("2026-09-03", None, "100.00")])
    _koer(db_session, kunde)
    assert len(_fund(db_session, kunde, I_BOGF)) == 1
    assert _fund(db_session, kunde, PAA_UDTOG) == []  # leverandør 99 hører ikke til udtoget


def test_postering_lige_uden_for_perioden_kan_matche_men_mangler_aldrig(db_session, kunde, post):
    post(1, "2026-10-02", "-800.00")   # 2 dage efter perioden
    post(2, "2026-10-04", "-900.00")   # uden for perioden, ingen linje
    u = _udtog(db_session, kunde, [("2026-09-30", None, "800.00")])
    _koer(db_session, kunde)
    assert _match(db_session, u) == {1: "beloeb_dato"}
    assert _fund(db_session, kunde, PAA_UDTOG) == []


def test_genindlaest_udtog_giver_ikke_nye_fund(db_session, kunde, post):
    linjer = [("2026-09-03", "A", "100.00"), ("2026-09-03", "A", "100.00")]  # to ens linjer
    _udtog(db_session, kunde, linjer)
    _koer(db_session, kunde)
    foer = {f.fingerprint for f in _fund(db_session, kunde, I_BOGF)}
    _udtog(db_session, kunde, linjer)  # samme udtog indlæst igen
    _koer(db_session, kunde)
    assert len(foer) == 2
    assert {f.fingerprint for f in _fund(db_session, kunde, I_BOGF)} == foer


def test_fund_forsvinder_naar_posteringen_bogfoeres(db_session, kunde, post):
    _udtog(db_session, kunde, [("2026-09-03", "55", "1000.00")])
    _koer(db_session, kunde)
    assert len(_fund(db_session, kunde, I_BOGF)) == 1
    post(1, "2026-09-03", "-1000.00", bilag=55)
    resultat = koer_regler(db_session, kunde.id)
    assert next(r for r in resultat.regler if r.rule_code == I_BOGF).fundet == 0


# --- Matchprocent ---------------------------------------------------------------------


def test_matchprocent_sql(db_session, kunde, post):
    post(1, "2026-09-03", "-100.00", bilag=1)
    post(2, "2026-09-05", "-200.00", bilag=2)
    _udtog(db_session, kunde, [("2026-09-03", "1", "100.00"), ("2026-09-06", None, "200.00"),
                               ("2026-09-10", None, "300.00"), ("2026-09-11", None, "400.00")])
    _koer(db_session, kunde)
    sql = (ROD / "scripts" / "matchprocent.sql").read_text()
    raekke = next(r for r in db_session.execute(text(sql)) if r.kundenummer == "MATCH-1")
    assert (raekke.linjer, raekke.matchet, raekke.trin1_bilag_beloeb, raekke.trin2_beloeb_dato,
            raekke.mangler_i_bogfoering) == (4, 2, 1, 1, 2)
    assert raekke.matchprocent == Decimal("50.0")


# --- Indlæsning ----------------------------------------------------------------------


@pytest.mark.parametrize("tekst, forventet", [
    ("1.234,56", Decimal("1234.56")), ("-1.234,56", Decimal("-1234.56")), ("1234.5", Decimal("1234.50")),
    ("12 500,00 kr.", Decimal("12500.00")), ("0,10", Decimal("0.10")),
])
def test_beloeb_tolkes_paa_dansk(tekst, forventet):
    assert tolk_beloeb(tekst) == forventet


@pytest.mark.parametrize("tekst", ["2026-09-03", "03-09-2026", "03.09.2026", "03/09/2026"])
def test_datoer_tolkes(tekst):
    assert tolk_dato(tekst) == date(2026, 9, 3)


def test_csv_med_semikolon_og_dansk_overskrift(tmp_path):
    fil = tmp_path / "udtog.csv"
    fil.write_text("Dato;Reference;Tekst;Beløb\n03.09.2026;F-1;Faktura 1;1.000,00\n04.09.2026;;Betaling;-1.000,00\n",
                   encoding="utf-8")
    linjer = laes_csv(fil)
    assert [(l["dato"], l["reference"], l["beloeb"]) for l in linjer] == [
        (date(2026, 9, 3), "F-1", Decimal("1000.00")), (date(2026, 9, 4), None, Decimal("-1000.00"))]


def test_csv_uden_beloeb_kolonne_afvises(tmp_path):
    fil = tmp_path / "forkert.csv"
    fil.write_text("dato;tekst\n03.09.2026;x\n", encoding="utf-8")
    with pytest.raises(IndlaesningsFejl, match="beloeb"):
        laes_csv(fil)


# --- PDF-kontoudtog -------------------------------------------------------------------
# Opdigtede udtog i de layouts, grossisterne bruger. Rigtige udtog lægges ALDRIG i repoet
# (de indeholder kundedata).

from app.kontoudtog import importer  # noqa: E402
from app.kontoudtog import pdf as pdf_modul  # noqa: E402
from app.kontoudtog.pdf import laes_pdf, tolk_pdf_beloeb  # noqa: E402


def _lav_pdf(sti, linjer, x=(20, 45, 75, 130, 165), skrift=9):
    """Skriv en simpel PDF: hver linje er en liste af (kolonne, tekst); tal højrestilles."""
    from fpdf import FPDF

    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=skrift)
    y = 20
    for linje in linjer:
        for kol, tekst in linje:
            bredde = pdf.get_string_width(tekst)
            hoejre = kol >= len(x) - 2 and any(c.isdigit() for c in tekst) and "," in tekst
            pdf.text(x[kol] - bredde if hoejre else x[kol], y, tekst)
        y += 6
    pdf.output(str(sti))
    return sti


@pytest.mark.parametrize("tekst, forventet", [
    ("1.234,56", Decimal("1234.56")), ("116,55-", Decimal("-116.55")), ("-16.286,74", Decimal("-16286.74")),
])
def test_pdf_beloeb_med_minus_foran_og_bagved(tekst, forventet):
    assert tolk_pdf_beloeb(tekst) == forventet


def test_pdf_med_beloeb_og_saldo_kolonne(tmp_path):
    """Som Brdr. Dahl: både Beløb og Saldo; en forfaldsoversigt efter ultimo tælles ikke med."""
    fil = _lav_pdf(tmp_path / "a.pdf", [
        [(0, "KONTOUDTOG")], [(0, "Periode 01-09-2026 - 30-09-2026")],
        [(0, "Bilagsdato"), (1, "Bilagsnr"), (2, "Tekst"), (3, "Beløb"), (4, "Saldo")],
        [(2, "Primo saldo"), (4, "1.000,00")],
        [(0, "08-09-2026"), (1, "112115989"), (2, "FAKT/KN"), (3, "2.500,00"), (4, "3.500,00")],
        [(0, "21-09-2026"), (1, "31014384"), (2, "INDBET."), (3, "-1.000,00"), (4, "2.500,00")],
        [(2, "Ultimo saldo DKK"), (4, "2.500,00")],
        [(0, "Forfaldsdato"), (3, "Beløb")], [(0, "20-10-2026"), (2, "DKK"), (3, "2.500,00")],
    ])
    u = laes_pdf(fil)
    assert u.kontrol_ok and not u.ocr
    assert [(l["dato"], l["reference"], l["beloeb"]) for l in u.linjer] == [
        (date(2026, 9, 8), "112115989", Decimal("2500.00")), (date(2026, 9, 21), "31014384", Decimal("-1000.00"))]
    assert (u.periode_fra, u.periode_til) == (date(2026, 9, 1), date(2026, 9, 30))


def test_pdf_med_debet_og_kredit(tmp_path):
    """Som Davidsen: betalinger står i Kredit og bliver negative; ingen periode -> linjernes datoer."""
    fil = _lav_pdf(tmp_path / "b.pdf", [
        [(0, "DATO"), (1, "BILAGSNR."), (2, "TEKST"), (3, "DEBET"), (4, "KREDIT")],
        [(2, "OVERFØRT"), (3, "500,00")],
        [(0, "01-12-2025"), (1, "11225"), (2, "FI-INDBETALING"), (4, "500,00")],
        [(0, "17-12-2025"), (1, "3360"), (2, "RYKKERGEBYR"), (3, "100,00")],
        [(2, "VORT TILGODEHAVENDE"), (4, "100,00")],
    ])
    u = laes_pdf(fil)
    assert [l["beloeb"] for l in u.linjer] == [Decimal("-500.00"), Decimal("100.00")]
    assert u.kontrol_ok
    assert (u.periode_fra, u.periode_til) == (date(2025, 12, 1), date(2025, 12, 17))


def test_pdf_der_ikke_stemmer_indlaeses_ikke(tmp_path, db_session, kunde, monkeypatch, capsys):
    fil = _lav_pdf(tmp_path / "c.pdf", [
        [(0, "Dato"), (1, "Fakturanr."), (2, "Tekst"), (3, "Beløb")],
        [(2, "Primosaldo"), (3, "0,00")],
        [(0, "03-09-26"), (1, "077135372"), (2, "FAKTURA"), (3, "646,30")],
        [(2, "Ultimosaldo DKK"), (3, "999,99")],
    ])
    monkeypatch.setattr(pdf_modul, "_sider_med_ocr", lambda sti: (_ for _ in ()).throw(importer.PdfFejl("ingen")))
    kode = importer.main(["--kundenummer", "MATCH-1", "--kilde", "grossist", "--kreditor", "45",
                          "--fortegn", "modsat", "--fil", str(fil)])
    assert kode == 1
    assert "STEMMER IKKE" in capsys.readouterr().out


def test_indscannet_pdf_laeses_med_tekstgenkendelse(tmp_path):
    import shutil

    if shutil.which("tesseract") is None:
        pytest.skip("tesseract er ikke installeret")
    import pdfplumber
    from fpdf import FPDF

    tekst_pdf = _lav_pdf(tmp_path / "t.pdf", [
        [(0, "Dato"), (1, "Fakturanr."), (2, "Tekst"), (3, "Beløb")],
        [(2, "Primo saldo"), (3, "3.555,63")],
        [(0, "010726"), (1, "017203487"), (2, "FAKTURA"), (3, "67,96")],
        [(0, "310726"), (2, "BETALING"), (3, "3.555,63-")],
        [(2, "Ultimo saldo DKK"), (3, "67,96")],
    ], skrift=12)
    with pdfplumber.open(tekst_pdf) as p:  # gør den til et billede uden tekst, som en scanning
        p.pages[0].to_image(resolution=300).save(tmp_path / "scan.png")
    scan = FPDF()
    scan.add_page()
    scan.image(str(tmp_path / "scan.png"), x=0, y=0, w=210)
    scan.output(str(tmp_path / "scan.pdf"))

    u = laes_pdf(tmp_path / "scan.pdf")
    assert u.ocr and u.kontrol_ok
    assert [(l["dato"], l["beloeb"]) for l in u.linjer] == [
        (date(2026, 7, 1), Decimal("67.96")), (date(2026, 7, 31), Decimal("-3555.63"))]

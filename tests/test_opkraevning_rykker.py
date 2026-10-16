"""Opkrævning trin 2: rykkermotoren og lovens grænser (renteloven)."""

from contextlib import contextmanager
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.kunder.models import Client
from app.opkraevning import referencesats as satskommando
from app.opkraevning.lov import (
    LovgraenseFejl,
    ManglerReferencesats,
    beregn_morarente,
    kontroller_rykkergebyr,
    morarentesats,
)
from app.opkraevning.models import (
    Debtor,
    DunningStep,
    FeeRevenue,
    Invoice,
    InvoicePayment,
    PaymentAllocation,
    ReferenceRate,
)
from app.opkraevning.rykker import byg_rykker, marker_sendt

UTC = timezone.utc


@pytest.fixture
def satser(db_session):
    db_session.add_all([
        ReferenceRate(valid_from=date(2026, 1, 1), rate=Decimal("1.75"), source="test"),
        ReferenceRate(valid_from=date(2026, 7, 1), rate=Decimal("1.60"), source="test"),
        ReferenceRate(valid_from=date(2027, 1, 1), rate=Decimal("2.10"), source="test"),
    ])
    db_session.flush()


@pytest.fixture
def faktura(db_session):
    k = Client(navn="Rykker ApS", kundenummer="RYK-1", regnskabssystem="economic", status="aktiv")
    db_session.add(k)
    db_session.flush()
    d = Debtor(client_id=k.id, external_id="62", name="Privat Person")
    db_session.add(d)
    db_session.flush()
    f = Invoice(client_id=k.id, external_id="730", invoice_no="730", debtor_id=d.id,
                issue_date=date(2026, 9, 1), due_date=date(2026, 9, 15), amount=Decimal("10000.00"),
                amount_outstanding=Decimal("10000.00"))
    db_session.add(f)
    db_session.flush()
    return f


def _send(session, faktura, dag):
    r = byg_rykker(session, faktura, dag)
    marker_sendt(session, r, datetime(dag.year, dag.month, dag.day, 9, tzinfo=UTC))
    return r


# --- Lovgrænser ----------------------------------------------------------------------------


def test_rykkergebyr_over_100_afvises(db_session, faktura, satser):
    with pytest.raises(LovgraenseFejl, match="100"):
        kontroller_rykkergebyr(Decimal("100.01"))
    r = byg_rykker(db_session, faktura, date(2026, 9, 25))
    assert r.fee_amount == Decimal("100.00")


def test_fjerde_rykker_afvises(db_session, faktura, satser):
    for dag in (date(2026, 9, 25), date(2026, 10, 5), date(2026, 10, 15)):
        _send(db_session, faktura, dag)
    with pytest.raises(LovgraenseFejl, match="Rykker nr. 4"):
        byg_rykker(db_session, faktura, date(2026, 10, 30))


def test_fjerde_rykker_afvises_naar_farpay_har_sendt_tre(db_session, faktura, satser):
    faktura.prior_dunning_count, faktura.prior_last_dunning_at = 3, date(2026, 9, 1)
    with pytest.raises(LovgraenseFejl, match="Rykker nr. 4"):
        byg_rykker(db_session, faktura, date(2026, 10, 30))


def test_under_10_dage_afvises(db_session, faktura, satser):
    _send(db_session, faktura, date(2026, 9, 25))
    with pytest.raises(LovgraenseFejl, match="Kun 9 dage"):
        byg_rykker(db_session, faktura, date(2026, 10, 4))
    assert byg_rykker(db_session, faktura, date(2026, 10, 5)).step_no == 2   # 10 dage er ok


def test_rykker_foer_forfald_afvises(db_session, faktura, satser):
    with pytest.raises(LovgraenseFejl, match="ikke forfalden"):
        byg_rykker(db_session, faktura, date(2026, 9, 15))


def test_kompensation_kun_paa_erhverv(db_session, faktura, satser):
    """Privat debitor: aldrig kompensation. Bliver debitoren registreret som erhverv inden den valgte
    rykker (standard: rykker 3, som i FarPay), opkræves kompensationen dér."""
    assert _send(db_session, faktura, date(2026, 9, 25)).compensation_amount == Decimal("0.00")
    db_session.get(Debtor, faktura.debtor_id).is_business = True
    assert _send(db_session, faktura, date(2026, 10, 5)).compensation_amount == Decimal("0.00")
    assert _send(db_session, faktura, date(2026, 10, 15)).compensation_amount == Decimal("310.00")


def test_kompensation_paa_kundens_valgte_rykker(db_session, faktura, satser):
    db_session.get(Debtor, faktura.debtor_id).is_business = True
    db_session.get(Client, faktura.client_id).dunning_compensation_step = 1
    assert _send(db_session, faktura, date(2026, 9, 25)).compensation_amount == Decimal("310.00")
    assert _send(db_session, faktura, date(2026, 10, 5)).compensation_amount == Decimal("0.00")


def test_kompensation_efter_rykkere_i_farpay(db_session, faktura, satser):
    """2 rykkere sendt i FarPay: vores første rykker er rykker 3 – og får kompensationen."""
    db_session.get(Debtor, faktura.debtor_id).is_business = True
    faktura.prior_dunning_count, faktura.prior_last_dunning_at = 2, date(2026, 9, 20)
    db_session.flush()
    assert _send(db_session, faktura, date(2026, 10, 5)).compensation_amount == Decimal("310.00")


def test_ingen_kompensation_hvis_farpay_allerede_naaede_den(db_session, faktura, satser):
    db_session.get(Debtor, faktura.debtor_id).is_business = True
    db_session.get(Client, faktura.client_id).dunning_compensation_step = 1
    faktura.prior_dunning_count, faktura.prior_last_dunning_at = 1, date(2026, 9, 20)
    db_session.flush()
    assert _send(db_session, faktura, date(2026, 10, 5)).compensation_amount == Decimal("0.00")


# --- Morarente --------------------------------------------------------------------------------


def test_morarente_beregnes_pr_dag(db_session, satser):
    """10.000 kr., forfald 15.09.2026, til 15.10.2026 = 30 rentedage.
    Sats pr. 1.7.2026: 1,60 % + 8 = 9,60 %.  10.000 × 9,60 % × 30 / 365 = 78,904… = 78,90 kr."""
    rente, perioder = beregn_morarente(db_session, date(2026, 9, 15), date(2026, 10, 15), [], Decimal("10000"))
    assert rente == Decimal("78.90")
    assert [(p.dage, p.sats) for p in perioder] == [(30, Decimal("9.60"))]


def test_morarente_over_halvaarsskifte_og_delbetaling(db_session, satser):
    """Forfald 21.12.2026, til 10.01.2027, 5.000 betalt på hovedstolen 05.01.2027:
      22.12–31.12: 10 dage × 10.000 × 9,60 % / 365 = 26,3014
      01.01–04.01:  4 dage × 10.000 × 10,10 % / 365 = 11,0685   (ny sats 2,10 % + 8)
      05.01–10.01:  6 dage ×  5.000 × 10,10 % / 365 =  8,3014
      i alt 45,6713 = 45,67 kr."""
    rente, perioder = beregn_morarente(db_session, date(2026, 12, 21), date(2027, 1, 10),
                                       [(date(2027, 1, 5), Decimal("-5000"))], Decimal("10000"))
    assert rente == Decimal("45.67")
    assert [(p.fra, p.dage, p.hovedstol, p.sats) for p in perioder] == [
        (date(2026, 12, 22), 10, Decimal("10000"), Decimal("9.60")),
        (date(2027, 1, 1), 4, Decimal("10000"), Decimal("10.10")),
        (date(2027, 1, 5), 6, Decimal("5000"), Decimal("10.10"))]


def test_manglende_referencesats_fejler(db_session):
    """Motoren gætter ikke og bruger ikke sidste kendte sats."""
    db_session.add(ReferenceRate(valid_from=date(2026, 1, 1), rate=Decimal("1.75"), source="test"))
    db_session.flush()
    assert morarentesats(db_session, date(2026, 6, 30)) == Decimal("9.75")
    with pytest.raises(ManglerReferencesats, match="01.07.2026"):
        morarentesats(db_session, date(2026, 7, 1))
    with pytest.raises(ManglerReferencesats):
        beregn_morarente(db_session, date(2026, 6, 20), date(2026, 7, 10), [], Decimal("1000"))


def test_rykker_faar_kun_ny_rente(db_session, faktura, satser):
    """Rykker 2 opkræver kun renten siden rykker 1 – aldrig den samme rente to gange."""
    r1 = _send(db_session, faktura, date(2026, 9, 25))     # 10 dage: 10.000 × 9,60 % × 10/365 = 26,30
    r2 = _send(db_session, faktura, date(2026, 10, 5))     # 20 dage i alt: 52,60 – 26,30
    assert (r1.interest_amount, r2.interest_amount) == (Decimal("26.30"), Decimal("26.30"))


def test_betaling_paa_hovedstol_nedsaetter_renten(db_session, faktura, satser):
    betaling = InvoicePayment(invoice_id=faktura.id, payment_date=date(2026, 9, 20), amount=Decimal("5000"),
                              source="regnskab", external_id="e1")
    db_session.add(betaling)
    db_session.flush()
    db_session.add(PaymentAllocation(payment_id=betaling.id, invoice_id=faktura.id, target_type="principal",
                                     amount=Decimal("5000")))
    db_session.flush()
    # 16.–19.9: 4 dage × 10.000; 20.–25.9: 6 dage × 5.000 → (40.000 + 30.000) × 9,60 % / 365 = 18,41
    assert byg_rykker(db_session, faktura, date(2026, 9, 25)).interest_amount == Decimal("18.41")


def test_sendt_rykker_registrerer_paalagte_gebyrer(db_session, faktura, satser):
    r = _send(db_session, faktura, date(2026, 9, 25))
    linjer = db_session.scalars(select(FeeRevenue).where(FeeRevenue.dunning_step_id == r.id)).all()
    assert sorted((l.fee_type, l.amount_charged, l.amount_collected) for l in linjer) == [
        ("morarente", Decimal("26.30"), Decimal("0.00")), ("rykkergebyr", Decimal("100.00"), Decimal("0.00"))]


def test_rykker_i_koe_giver_ingen_gebyrlinje(db_session, faktura, satser):
    r = byg_rykker(db_session, faktura, date(2026, 9, 25))
    assert db_session.scalars(select(FeeRevenue).where(FeeRevenue.dunning_step_id == r.id)).all() == []
    assert db_session.get(DunningStep, r.id).status == "queued"


# --- Kommandoen til referencesatsen -------------------------------------------------------------


def test_kommando_saetter_sats_og_naegter_at_aendre_den(db_session, monkeypatch, capsys):
    @contextmanager
    def samme():
        yield db_session

    monkeypatch.setattr(satskommando, "ny_session", samme)
    assert satskommando.main(["saet", "--fra", "2026-07-01", "--sats", "1,60", "--kilde", "Nationalbanken"]) == 0
    assert "morarente 9.60" in capsys.readouterr().out
    assert satskommando.main(["saet", "--fra", "2026-07-01", "--sats", "1.70", "--kilde", "x"]) == 2
    assert satskommando.main(["saet", "--fra", "2026-03-01", "--sats", "1.70", "--kilde", "x"]) == 2
    assert db_session.scalar(select(ReferenceRate.rate).where(ReferenceRate.valid_from == date(2026, 7, 1))) == \
        Decimal("1.6000")

"""Opkrævning trin 1: datamodellen og lovens grænser som hårde regler i databasen.

Hver test prøver at gøre noget ulovligt direkte i databasen – det skal afvises uanset hvilken
kode der forsøger. (Rykkermotoren i trin 2 kontrollerer det samme, før den overhovedet prøver.)
"""

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import inspect
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.kunder.models import Client
from app.opkraevning.models import (
    BillingPeriod,
    CollectionCase,
    Debtor,
    DunningStep,
    FeeRevenue,
    InstallmentPlan,
    Invoice,
    ReferenceRate,
)
from app.personale.models import Staff

DK = timezone.utc


@pytest.fixture
def kunde(db_session):
    k = Client(navn="Opkræv ApS", kundenummer="OPK-1", regnskabssystem="economic", status="aktiv")
    db_session.add(k)
    db_session.flush()
    return k


@pytest.fixture
def debitor(db_session, kunde):
    d = Debtor(client_id=kunde.id, external_id="62", name="Privat Person", is_business=False)
    db_session.add(d)
    db_session.flush()
    return d


@pytest.fixture
def faktura(db_session, kunde, debitor):
    f = Invoice(client_id=kunde.id, external_id="730", invoice_no="730", debtor_id=debitor.id,
                issue_date=date(2026, 9, 1), due_date=date(2026, 9, 15), amount=Decimal("8607.08"),
                amount_outstanding=Decimal("8607.08"))
    db_session.add(f)
    db_session.flush()
    return f


def _afvises(session, *objekter, fejl=(IntegrityError, DBAPIError)):
    with pytest.raises(fejl):
        with session.begin_nested():
            session.add_all(objekter)
            session.flush()


def _rykker(faktura, nr, due, sent=None, **kw):
    return DunningStep(invoice_id=faktura.id, step_no=nr, due_at=due, status="sent" if sent else "queued",
                       sent_at=sent, **kw)


def test_rykkergebyr_over_100_afvises(db_session, faktura):
    _afvises(db_session, _rykker(faktura, 1, date(2026, 9, 20), fee_amount=Decimal("100.01")))
    db_session.add(_rykker(faktura, 1, date(2026, 9, 20), fee_amount=Decimal("100.00")))
    db_session.flush()


def test_fjerde_rykker_afvises(db_session, faktura):
    _afvises(db_session, _rykker(faktura, 4, date(2026, 12, 1)))


def test_fjerde_rykker_afvises_ogsaa_naar_farpay_har_sendt_rykkere(db_session, faktura):
    faktura.prior_dunning_count, faktura.prior_last_dunning_at = 3, date(2026, 9, 1)
    db_session.flush()
    _afvises(db_session, _rykker(faktura, 1, date(2026, 10, 1)))


def test_under_10_dage_afvises(db_session, faktura):
    db_session.add(_rykker(faktura, 1, date(2026, 9, 20), sent=datetime(2026, 9, 20, 9, tzinfo=DK)))
    db_session.flush()
    _afvises(db_session, _rykker(faktura, 2, date(2026, 9, 29)))          # 9 dage
    db_session.add(_rykker(faktura, 2, date(2026, 9, 30)))               # 10 dage er ok
    db_session.flush()


def test_under_10_dage_efter_farpays_sidste_rykker_afvises(db_session, faktura):
    faktura.prior_dunning_count, faktura.prior_last_dunning_at = 1, date(2026, 9, 25)
    db_session.flush()
    _afvises(db_session, _rykker(faktura, 1, date(2026, 10, 1)))


def test_rykker_kraever_at_forrige_er_sendt(db_session, faktura):
    _afvises(db_session, _rykker(faktura, 2, date(2026, 10, 30)))


def test_kompensation_kun_paa_erhverv(db_session, faktura, debitor):
    _afvises(db_session, _rykker(faktura, 1, date(2026, 9, 20), compensation_amount=Decimal("310")))
    debitor.is_business = True
    db_session.flush()
    db_session.add(_rykker(faktura, 1, date(2026, 9, 20), compensation_amount=Decimal("310")))
    db_session.flush()


def test_ingen_rykker_paa_kreditnota(db_session, kunde, debitor):
    kn = Invoice(client_id=kunde.id, external_id="586", invoice_no="586", debtor_id=debitor.id, kind="credit_note",
                 issue_date=date(2025, 11, 10), due_date=date(2025, 11, 18), amount=Decimal("-695"),
                 amount_outstanding=Decimal("-695"))
    db_session.add(kn)
    db_session.flush()
    _afvises(db_session, _rykker(kn, 1, date(2026, 9, 20)))


def test_kreditnota_og_faktura_skal_passe_med_fortegn(db_session, kunde, debitor):
    _afvises(db_session, Invoice(client_id=kunde.id, external_id="1", invoice_no="1", debtor_id=debitor.id,
                                 issue_date=date(2026, 1, 1), due_date=date(2026, 1, 8), amount=Decimal("-1"),
                                 amount_outstanding=Decimal("-1")))


def test_ingen_rykker_paa_faktura_med_aktiv_sag(db_session, kunde, debitor, faktura):
    db_session.add(CollectionCase(client_id=kunde.id, invoice_id=faktura.id, debtor_id=debitor.id,
                                  principal=Decimal("8607.08"), idempotency_key="k1"))
    db_session.flush()
    _afvises(db_session, _rykker(faktura, 1, date(2026, 9, 20)))


def test_aldrig_to_aktive_sager_paa_samme_fordring(db_session, kunde, debitor, faktura):
    def sag(noegle, **kw):
        return CollectionCase(client_id=kunde.id, invoice_id=faktura.id, debtor_id=debitor.id,
                              principal=Decimal("100"), idempotency_key=noegle, **kw)
    db_session.add(sag("k1"))
    db_session.flush()
    _afvises(db_session, sag("k2"))                    # anden aktiv sag
    _afvises(db_session, sag("k1"))                    # samme idempotensnøgle
    gammel = db_session.query(CollectionCase).filter_by(idempotency_key="k1").one()
    gammel.status, gammel.withdrawn_at, gammel.withdraw_reason = "withdrawn", datetime.now(DK), "betalt direkte"
    db_session.flush()
    db_session.add(sag("k3"))                          # tilbagekaldt sag spærrer ikke en ny
    db_session.flush()


def test_gebyr_hos_inkasso_kan_ikke_vaere_fakturerbart(db_session, kunde, debitor, faktura):
    sag = CollectionCase(client_id=kunde.id, invoice_id=faktura.id, debtor_id=debitor.id,
                         principal=Decimal("100"), idempotency_key="k1")
    db_session.add(sag)
    db_session.flush()
    _afvises(db_session, FeeRevenue(client_id=kunde.id, invoice_id=faktura.id, fee_type="rykkergebyr",
                                    amount_charged=Decimal("100"), collection_case_id=sag.id,
                                    billable_at=datetime.now(DK)))


def test_frigivelse_kraever_et_menneske(db_session, kunde):
    _afvises(db_session, BillingPeriod(client_id=kunde.id, month=date(2026, 9, 1), status="released",
                                       released_at=datetime.now(DK)))
    _afvises(db_session, BillingPeriod(client_id=kunde.id, month=date(2026, 9, 1), status="blocked"))


def test_referencesats_kun_1_januar_eller_1_juli(db_session):
    _afvises(db_session, ReferenceRate(valid_from=date(2026, 3, 1), rate=Decimal("1.6"), source="test"))
    db_session.add(ReferenceRate(valid_from=date(2026, 7, 1), rate=Decimal("1.6"), source="test"))
    db_session.flush()


def test_kun_en_aktiv_afbetalingsordning(db_session, faktura):
    m = Staff(navn="Bo", email="bo-opk@example.invalid")
    db_session.add(m)
    db_session.flush()

    def plan():
        return InstallmentPlan(invoice_id=faktura.id, created_by=m.id, total_amount=Decimal("8607.08"),
                               installments=3, first_due_date=date(2026, 10, 1), interval_days=30)
    db_session.add(plan())
    db_session.flush()
    _afvises(db_session, plan())


def test_kunde_indstillinger_har_lovlige_vaerdier(db_session, kunde):
    db_session.refresh(kunde)  # standardværdierne sættes af databasen
    assert (kunde.payment_allocation_order, kunde.dunning_min_amount, kunde.collection_grace_days,
            kunde.collection_min_amount, kunde.auto_escalate_to_collection) == (
        "costs_first", Decimal("100.00"), 10, Decimal("500.00"), False)
    for felt, vaerdi in (("payment_allocation_order", "random"), ("collection_grace_days", 5),
                         ("fi_kreditornummer", "1234")):
        with pytest.raises(IntegrityError):
            with db_session.begin_nested():
                setattr(kunde, felt, vaerdi)
                db_session.flush()
        db_session.refresh(kunde)


def test_alle_beloeb_er_numeric_15_2(db_session):
    insp = inspect(db_session.connection())
    for tabel, kolonner in {
        "invoices": ("amount", "amount_outstanding"), "invoice_payments": ("amount",),
        "payment_allocations": ("amount",), "fee_revenue": ("amount_charged", "amount_collected"),
        "dunning_steps": ("fee_amount", "interest_amount", "compensation_amount"),
        "collection_cases": ("principal", "fees_included", "interest_included"),
        "billing_periods": ("total_amount",), "installment_plans": ("total_amount",),
        "clients": ("dunning_min_amount", "collection_min_amount"),
    }.items():
        typer = {k["name"]: k["type"] for k in insp.get_columns(tabel)}
        for k in kolonner:
            assert (typer[k].precision, typer[k].scale) == (15, 2), f"{tabel}.{k}"

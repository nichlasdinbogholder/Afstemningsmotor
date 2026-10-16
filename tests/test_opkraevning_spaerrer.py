"""Opkrævning trin 3: hvornår en rykker IKKE må sendes – og hentningen af fakturaerne."""

from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app import cli
from app.adaptere.regnskab.base import Debitor as RDebitor
from app.adaptere.regnskab.base import Adresse as RAdresse
from app.adaptere.regnskab.base import Faktura as RFaktura
from app.kunder.models import Client
from app.opkraevning.models import (
    CollectionCase,
    Debtor,
    DunningSkip,
    DunningStep,
    InstallmentPlan,
    Invoice,
    InvoicePayment,
    ReferenceRate,
)
from app.opkraevning.rykker import marker_sendt
from app.opkraevning.rykkerkoersel import (
    KanIkkeFjernes,
    fjern_rykker,
    forhaandsvis,
    kontroller_foer_afsendelse,
    laeg_i_koe,
)
from app.opkraevning.spaerrer import spaerrer
from app.personale.models import Staff
from app.regnskab.models import EntryCache, JournalEntryCache
from app.synk.opkraevning import synk_invoices

UTC = timezone.utc
FORFALD = date(2026, 9, 15)
DAG = date(2026, 9, 28)        # mandag, 13 dage efter forfald – første rykker er "due" (10 dage)


@pytest.fixture
def kunde(db_session):
    # Uden venlig påmindelse – de tests står længere nede.
    k = Client(navn="Spærre ApS", kundenummer="SP-1", regnskabssystem="economic", status="aktiv",
               dunning_mode="preview", business_customer_groups=[2])
    db_session.add_all([k, ReferenceRate(valid_from=date(2026, 7, 1), rate=Decimal("1.60"), source="test")])
    db_session.flush()
    k.reminder_after_days = None   # (None ved oprettelse giver databasens standard, 5)
    db_session.flush()
    return k


@pytest.fixture
def debitor(db_session, kunde):
    d = Debtor(client_id=kunde.id, external_id="62", name="Hansen")
    db_session.add(d)
    db_session.flush()
    return d


@pytest.fixture
def faktura(db_session, kunde, debitor):
    f = Invoice(client_id=kunde.id, external_id="730", invoice_no="730", debtor_id=debitor.id,
                issue_date=date(2026, 9, 1), due_date=FORFALD, amount=Decimal("8607.08"),
                amount_outstanding=Decimal("8607.08"))
    db_session.add(f)
    db_session.flush()
    return f


def _koder(grunde):
    return [k for k, _ in grunde]


def _betal(session, faktura, dag, beloeb="1.00", kilde="regnskab", nr="e1"):
    session.add(InvoicePayment(invoice_id=faktura.id, payment_date=dag, amount=Decimal(beloeb),
                               source=kilde, external_id=nr))
    session.flush()


# --- Spærrerne ------------------------------------------------------------------------------------


def test_ingen_spaerre_paa_almindelig_forfalden_faktura(db_session, faktura):
    assert spaerrer(db_session, faktura, DAG) == []


def test_rykker_paa_betalt_faktura_afvises(db_session, kunde, faktura):
    """Rykkeren ligger i kø kl. 16 – fakturaen betales – kontrollen kl. 9 annullerer den."""
    koe = laeg_i_koe(db_session, kunde.id, DAG)
    assert len(koe.lagt_i_koe) == 1
    faktura.amount_outstanding, faktura.status = Decimal("0"), "paid"
    db_session.flush()
    kontrol = kontroller_foer_afsendelse(db_session, kunde.id, DAG)
    assert kontrol.lagt_i_koe == [] and len(kontrol.sprunget_over) == 1
    assert db_session.get(DunningStep, koe.lagt_i_koe[0].rykker.id).status == "cancelled"
    assert "betalt" in db_session.scalars(select(DunningSkip.reason).where(
        DunningSkip.invoice_id == faktura.id)).all()


def test_indbetaling_seneste_to_bankdage_stopper_rykker(db_session, faktura):
    """Mandag 28.09: de 2 seneste bankdage er fredag 25.09 og torsdag 24.09 – uanset beløb."""
    _betal(db_session, faktura, date(2026, 9, 24), beloeb="0.01")
    assert "indbetaling_seneste_2_bankdage" in _koder(spaerrer(db_session, faktura, DAG))


def test_aeldre_indbetaling_stopper_ikke_rykker(db_session, faktura):
    _betal(db_session, faktura, date(2026, 9, 23))
    assert "indbetaling_seneste_2_bankdage" not in _koder(spaerrer(db_session, faktura, DAG))


def test_indbetaling_i_kassekladde_stopper_rykker(db_session, kunde, faktura):
    db_session.add(JournalEntryCache(client_id=kunde.id, kladde_nummer=1, kladde_navn="Bank", dato=DAG,
                                     beloeb=Decimal("-500"), fakturanummer="730", modpart="debitor:62",
                                     entry_type="customerPayment"))
    db_session.flush()
    assert "indbetaling_i_kassekladde" in _koder(spaerrer(db_session, faktura, DAG))


def test_afbetalingsordning_stopper_rykker(db_session, faktura):
    m = Staff(navn="Bo", email="bo-sp@example.invalid")
    db_session.add(m)
    db_session.flush()
    db_session.add(InstallmentPlan(invoice_id=faktura.id, created_by=m.id, total_amount=Decimal("8607.08"),
                                   installments=3, first_due_date=date(2026, 10, 1), interval_days=30))
    db_session.flush()
    assert _koder(spaerrer(db_session, faktura, DAG)) == ["afbetalingsordning"]


def test_blokeret_debitor_minimum_og_inkasso(db_session, kunde, debitor, faktura):
    debitor.blocked_from_dunning = True
    kunde.dunning_min_amount = Decimal("10000")
    db_session.add(CollectionCase(client_id=kunde.id, invoice_id=faktura.id, debtor_id=debitor.id,
                                  principal=Decimal("8607.08"), idempotency_key="k"))
    db_session.flush()
    assert set(_koder(spaerrer(db_session, faktura, DAG))) == {
        "debitor_blokeret", "under_minimumsbeloeb", "aktiv_inkassosag"}


def test_under_10_dage_og_tre_rykkere(db_session, kunde, faktura):
    r = laeg_i_koe(db_session, kunde.id, DAG).lagt_i_koe[0].rykker
    assert "rykker_i_koe" in _koder(spaerrer(db_session, faktura, DAG))
    marker_sendt(db_session, r, datetime(2026, 9, 28, 9, tzinfo=UTC))
    assert "under_10_dage" in _koder(spaerrer(db_session, faktura, date(2026, 10, 7)))
    faktura.prior_dunning_count, faktura.prior_last_dunning_at = 2, date(2026, 9, 1)
    db_session.flush()
    assert "max_3_rykkere" in _koder(spaerrer(db_session, faktura, date(2026, 10, 30)))


def test_afvisning_skrives_i_dunning_skips(db_session, kunde, debitor, faktura):
    debitor.blocked_from_dunning = True
    db_session.flush()
    resultat = laeg_i_koe(db_session, kunde.id, DAG)
    assert resultat.lagt_i_koe == [] and len(resultat.sprunget_over) == 1
    spaerre = db_session.scalars(select(DunningSkip).where(DunningSkip.invoice_id == faktura.id)).one()
    assert spaerre.reason == "debitor_blokeret" and spaerre.checked_at is not None


def test_faktura_der_ikke_er_klar_til_rykker_vurderes_ikke(db_session, kunde, faktura):
    """Kundens plan: første rykker 10 dage efter forfald. Før det – ingen rykker og ingen støj."""
    assert laeg_i_koe(db_session, kunde.id, date(2026, 9, 24)).lagt_i_koe == []
    assert db_session.scalar(select(func.count()).select_from(DunningSkip)
                             .where(DunningSkip.invoice_id == faktura.id)) == 0


# --- Medarbejderen kan fjerne en rykker i kø --------------------------------------------------


def test_medarbejder_fjerner_rykker_med_begrundelse(db_session, kunde, faktura):
    r = laeg_i_koe(db_session, kunde.id, DAG).lagt_i_koe[0].rykker
    with pytest.raises(KanIkkeFjernes, match="hvorfor"):
        fjern_rykker(db_session, r.id, "bo@dinbogholder.dk", "  ")
    fjern_rykker(db_session, r.id, "bo@dinbogholder.dk", "Kunden ringede – betaler fredag")
    assert r.status == "cancelled"
    spaerre = db_session.scalars(select(DunningSkip).where(DunningSkip.reason == "fjernet_af_medarbejder",
                                                           DunningSkip.invoice_id == faktura.id)).one()
    assert spaerre.details["af"] == "bo@dinbogholder.dk"
    assert kontroller_foer_afsendelse(db_session, kunde.id, DAG).lagt_i_koe == []
    with pytest.raises(KanIkkeFjernes, match="cancelled"):
        fjern_rykker(db_session, r.id, "bo", "igen")


# --- Forhåndsvisning ------------------------------------------------------------------------------


def test_forhaandsvisning_gemmer_intet(db_session, kunde, faktura, capsys, monkeypatch):
    r = forhaandsvis(db_session, kunde.id, DAG)
    assert [v.faktura.invoice_no for v in r.lagt_i_koe] == ["730"]
    assert r.lagt_i_koe[0].rykker.fee_amount == Decimal("100.00")
    assert db_session.scalar(select(func.count()).select_from(DunningStep)) == 0

    @contextmanager
    def samme():
        yield db_session

    monkeypatch.setattr(cli, "ny_session", samme)
    assert cli.main(["dunning-preview", str(kunde.id), "--dag", DAG.isoformat()]) == 0
    ud = capsys.readouterr().out
    assert "VILLE BLIVE SENDT (1)" in ud and "730" in ud and "100,00" in ud
    assert db_session.scalar(select(func.count()).select_from(DunningStep)) == 0


# --- Hentningen af debitorer, fakturaer og indbetalinger ------------------------------------------


class FalskSystem:
    def __init__(self, debitorer, fakturaer):
        self.debitorer, self.fakturaer, self.kald = debitorer, fakturaer, 0
        self.linjekald: list = []
        self.loft: int | None = None   # rejs ForMangeKald efter så mange linjekald

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def fetch_debtors(self):
        self.kald += 1
        return self.debitorer

    def fetch_invoices(self):
        return self.fakturaer

    def fetch_invoice_lines(self, nummer):
        from app.adaptere.regnskab.base import FakturaLinje, ForMangeKald
        if self.loft is not None and len(self.linjekald) >= self.loft:
            raise ForMangeKald("for mange kald", vent_sekunder=60)
        self.linjekald.append(nummer)
        return [FakturaLinje(1, "1001", f"Vare til {nummer}", Decimal("2"), "stk", Decimal("265.92"),
                             Decimal("0"), Decimal("531.84"))]


def _system():
    return FalskSystem(
        [RDebitor(62, "Hansen", None, "h@eks.dk", None, "Vej 1", "2300", "Kbh S", None, 1),
         RDebitor(141, "Firma ApS", "DK 12345678", None, "5790000000000", None, None, None, None, 2)],
        [RFaktura(730, 62, date(2026, 9, 1), FORFALD, Decimal("8607.08"), Decimal("8607.08"), "DKK", None,
                  ordrenummer=607, oevrig_ref="26238 - 742", netto=Decimal("6885.66"), moms=Decimal("1721.42"),
                  modtager=RAdresse("Hansen", "Vej 1", "2300", "Kbh S"), levering=RAdresse(None, "Amager port 2",
                                                                                           "2300", "Kbh S")),
         RFaktura(586, 141, date(2025, 11, 10), date(2025, 11, 18), Decimal("-695"), Decimal("-695"), "DKK", None),
         RFaktura(100, 141, date(2022, 12, 22), date(2022, 12, 30), Decimal("15166.81"), Decimal("0"), "DKK",
                  "5790000000001")])


def test_hentning_af_debitorer_fakturaer_og_indbetalinger(db_session, kunde):
    db_session.add(EntryCache(client_id=kunde.id, bogfoert_id=900, bilagsnummer=50, dato=date(2026, 9, 25),
                              kontonummer=5600, beloeb=Decimal("-1000"), modpart="debitor:62",
                              entry_type="customerPayment", fakturanummer="730"))
    db_session.flush()
    synk_invoices(db_session, kunde.id, _system())
    d = {x.external_id: x for x in db_session.scalars(select(Debtor).where(Debtor.client_id == kunde.id))}
    assert (d["62"].email, d["62"].is_business, d["141"].cvr, d["141"].is_business) == (
        "h@eks.dk", False, "12345678", True)
    f = {x.invoice_no: x for x in db_session.scalars(select(Invoice).where(Invoice.client_id == kunde.id))}
    assert (f["730"].kind, f["730"].status) == ("invoice", "open")
    assert (f["586"].kind, f["586"].status) == ("credit_note", "open")
    assert (f["100"].status, f["100"].ean) == ("paid", "5790000000001")
    betaling = db_session.scalars(select(InvoicePayment).where(InvoicePayment.invoice_id == f["730"].id)).one()
    assert (betaling.amount, betaling.payment_date, betaling.source) == (Decimal("1000.00"), date(2026, 9, 25),
                                                                        "regnskab")
    # Medarbejdernes felter røres ikke ved næste hentning – og indbetalingen tælles kun én gang.
    d["62"].blocked_from_dunning, d["62"].note = True, "Aftale"
    db_session.flush()
    synk_invoices(db_session, kunde.id, _system())
    db_session.refresh(d["62"])
    assert (d["62"].blocked_from_dunning, d["62"].note) == (True, "Aftale")
    assert db_session.scalar(select(func.count()).select_from(InvoicePayment)
                             .where(InvoicePayment.invoice_id == f["730"].id)) == 1


def test_fakturahoved_og_linjer_hentes_een_gang(db_session, kunde):
    system = _system()
    synk_invoices(db_session, kunde.id, system)
    f = db_session.scalars(select(Invoice).where(Invoice.client_id == kunde.id, Invoice.invoice_no == "730")).one()
    assert (f.order_no, f.other_ref, f.net_amount, f.vat_amount) == (607, "26238 - 742", Decimal("6885.66"),
                                                                      Decimal("1721.42"))
    assert f.recipient == {"navn": "Hansen", "adresse": "Vej 1", "postnr": "2300", "by": "Kbh S"}
    assert f.delivery["adresse"] == "Amager port 2"
    assert f.lines == [{"nr": 1, "vare": "1001", "beskrivelse": "Vare til 730", "antal": "2", "enhed": "stk",
                        "pris": "265.92", "rabat": "0", "beloeb": "531.84"}]
    assert sorted(system.linjekald) == [100, 586, 730]
    # En bogført faktura ændres aldrig – linjerne hentes ikke igen.
    system.linjekald = []
    synk_invoices(db_session, kunde.id, system)
    assert system.linjekald == []


def test_for_mange_kald_gemmer_det_hentede_og_tager_resten_naeste_gang(db_session, kunde):
    system = _system()
    system.loft = 1
    synk_invoices(db_session, kunde.id, system)          # fejler IKKE
    hentet = db_session.scalars(select(Invoice.invoice_no).where(Invoice.client_id == kunde.id,
                                                                 Invoice.lines.is_not(None))).all()
    assert hentet == ["730"]                             # nyeste først
    system.linjekald, system.loft = [], None
    synk_invoices(db_session, kunde.id, system)
    assert sorted(system.linjekald) == [100, 586]


def test_hentning_springes_over_naar_rykkere_er_slaaet_fra(db_session, kunde):
    kunde.dunning_mode = "off"
    db_session.flush()
    system = _system()
    synk_invoices(db_session, kunde.id, system)
    assert system.kald == 0
    assert db_session.scalar(select(func.count()).select_from(Debtor).where(Debtor.client_id == kunde.id)) == 0


# --- Tidsplanen kl. 16 og kl. 9 -------------------------------------------------------------------


def test_rykkerjob_kun_paa_bankdage_og_kun_for_kunder_i_drift(db_session, kunde, monkeypatch):
    from app.jobs.models import Job
    from app.opkraevning import jobs

    mandag_16 = datetime(2026, 9, 28, 14, tzinfo=UTC)   # 16:00 dansk tid
    assert jobs.planlaeg_rykkerjob(db_session, jobs.KOE, mandag_16) == 0       # 'preview' får ingen job
    monkeypatch.setattr(jobs, "LIVE_TILSTANDE", ("preview",))
    assert jobs.planlaeg_rykkerjob(db_session, jobs.KOE, mandag_16) == 1
    assert jobs.planlaeg_rykkerjob(db_session, jobs.KOE, mandag_16) == 0       # idempotent
    job = db_session.scalars(select(Job).where(Job.client_id == kunde.id, Job.type == jobs.KOE)).one()
    assert job.payload == {"dag": "2026-09-29"}                                # næste bankdag
    loerdag = datetime(2026, 10, 3, 14, tzinfo=UTC)
    assert jobs.planlaeg_rykkerjob(db_session, jobs.KONTROL, loerdag) == 0


def test_scheduler_har_rykkertiderne():
    from app.natkoersel.scheduler import lav_scheduler

    s = lav_scheduler()
    tider = {j.id: str(j.trigger) for j in s.get_jobs()}
    assert "hour='16'" in tider["rykker_koe"] and "hour='9'" in tider["rykker_kontrol"]
    assert "mon-fri" in tider["rykker_koe"]


# --- Venlig påmindelse (5 dage efter forfald, uden gebyr) – derefter rykker hver 10. dag ----------------


def _med_paamindelse(db_session, kunde):
    kunde.reminder_after_days = 5
    db_session.flush()


def _send(db_session, kunde, dag):
    [v] = laeg_i_koe(db_session, kunde.id, dag).lagt_i_koe
    marker_sendt(db_session, v.rykker, datetime(dag.year, dag.month, dag.day, 7, tzinfo=UTC))
    return v.rykker


def test_paamindelse_efter_5_dage_og_derefter_rykker_hver_10_dag(db_session, kunde, faktura):
    from app.opkraevning.models import FeeRevenue

    _med_paamindelse(db_session, kunde)
    assert laeg_i_koe(db_session, kunde.id, date(2026, 9, 19)).lagt_i_koe == []          # forfald + 4
    p = _send(db_session, kunde, date(2026, 9, 20))                                      # forfald + 5
    assert (p.step_no, p.fee_amount, p.interest_amount, p.compensation_amount) == (0, 0, 0, 0)
    assert db_session.scalar(select(func.count()).select_from(FeeRevenue)) == 0          # intet at fakturere
    assert laeg_i_koe(db_session, kunde.id, date(2026, 9, 29)).lagt_i_koe == []          # påmindelse + 9
    r1 = _send(db_session, kunde, date(2026, 9, 30))                                     # påmindelse + 10
    assert (r1.step_no, r1.fee_amount) == (1, Decimal("100.00"))
    assert laeg_i_koe(db_session, kunde.id, date(2026, 10, 9)).lagt_i_koe == []
    r2 = _send(db_session, kunde, date(2026, 10, 10))
    r3 = _send(db_session, kunde, date(2026, 10, 20))
    assert (r2.step_no, r3.step_no) == (2, 3)
    assert laeg_i_koe(db_session, kunde.id, date(2026, 11, 30)).lagt_i_koe == []         # højst 3 rykkere


def test_ingen_ny_paamindelse_efter_farpay(db_session, kunde, faktura):
    _med_paamindelse(db_session, kunde)
    faktura.prior_reminder_at = date(2026, 9, 20)                                        # sendt i FarPay
    db_session.flush()
    assert laeg_i_koe(db_session, kunde.id, date(2026, 9, 29)).lagt_i_koe == []
    assert _send(db_session, kunde, date(2026, 9, 30)).step_no == 1
    # Har FarPay allerede sendt en rykker, kommer der heller ingen påmindelse.
    faktura2 = Invoice(client_id=kunde.id, external_id="731", invoice_no="731", debtor_id=faktura.debtor_id,
                       issue_date=date(2026, 9, 1), due_date=FORFALD, amount=Decimal("500"),
                       amount_outstanding=Decimal("500"), prior_dunning_count=1, prior_last_dunning_at=date(2026, 9, 25))
    db_session.add(faktura2)
    db_session.flush()
    [v] = [v for v in laeg_i_koe(db_session, kunde.id, date(2026, 10, 5)).lagt_i_koe if v.faktura.id == faktura2.id]
    assert v.rykker.step_no == 1


def test_databasen_afviser_forkerte_paamindelser(db_session, kunde, faktura):
    from sqlalchemy.exc import IntegrityError

    def proev(**felter):
        with db_session.begin_nested():
            db_session.add(DunningStep(invoice_id=faktura.id, due_at=date(2026, 9, 20), status="queued", **felter))
            db_session.flush()

    with pytest.raises(IntegrityError):
        proev(step_no=0, fee_amount=Decimal("100"))                                      # påmindelse med gebyr
    _med_paamindelse(db_session, kunde)
    _send(db_session, kunde, date(2026, 9, 20))
    with pytest.raises(IntegrityError, match="under 10 dage"):
        proev(step_no=1, fee_amount=Decimal("100"))                                      # rykker 1 kun 0 dage efter
    _send(db_session, kunde, date(2026, 9, 30))
    with pytest.raises(IntegrityError, match="allerede fået en rykker"):
        with db_session.begin_nested():
            db_session.add(DunningStep(invoice_id=faktura.id, step_no=0, due_at=date(2026, 10, 1), status="queued"))
            db_session.flush()


def test_annulleret_rykker_kan_afloeses_af_en_ny(db_session, kunde, faktura):
    """Fejl fra trin 3: databasen tillod kun én rykker nr. 1 pr. faktura – også en annulleret."""
    [v] = laeg_i_koe(db_session, kunde.id, DAG).lagt_i_koe
    fjern_rykker(db_session, v.rykker.id, "bo@dinbogholder.dk", "Kunden ringede")
    [ny] = laeg_i_koe(db_session, kunde.id, DAG + timedelta(days=1)).lagt_i_koe
    assert ny.rykker.step_no == 1 and ny.rykker.id != v.rykker.id

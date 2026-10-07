"""Rykkermotoren: byg den næste rykker på en faktura – inden for rentelovens grænser.

`byg_rykker(session, faktura, dag)` lægger en rykker i kø (status `queued`) med gebyr,
kompensationsbeløb (på kundens valgte rykker) og morarente beregnet til `dag`, eller rejser `LovgraenseFejl` /
`ManglerReferencesats`. Den afgør IKKE, om en rykker bør sendes (betalt, blokeret, afbetaling
osv.) – det gør spærrerne i trin 3, før byg_rykker kaldes.

`byg_paamindelse(session, faktura, dag)` lægger den venlige påmindelse (step_no 0, uden beløb) i kø.

`marker_sendt(session, rykker, tidspunkt)` sætter rykkeren som sendt og bogfører de pålagte
beløb i `fee_revenue` (pålagt, endnu ikke indbetalt). En rykker, der fjernes inden afsendelse,
giver derfor aldrig en gebyrlinje.
"""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.kunder.models import Client
from app.opkraevning.lov import (
    KOMPENSATIONSBELOEB,
    MAKS_RYKKERGEBYR,
    LovgraenseFejl,
    beregn_morarente,
    kontroller_interval,
    kontroller_kompensation,
    kontroller_rykkergebyr,
    kontroller_rykkernummer,
)
from app.opkraevning.models import (
    Debtor,
    DunningStep,
    FeeRevenue,
    Invoice,
    InvoicePayment,
    PaymentAllocation,
)
from app.tid import dansk_dato

# Gebyret pr. rykker. Lovens maksimum er også standarden; et lavere gebyr pr. kunde kan tilføjes
# senere, et højere kan ikke (CHECK fee_amount <= 100 i databasen).
RYKKERGEBYR = MAKS_RYKKERGEBYR


def aktive_rykkere(session: Session, faktura: Invoice) -> list[DunningStep]:
    """Rykkere i kø eller sendt (ikke den venlige påmindelse)."""
    return list(session.scalars(
        select(DunningStep).where(DunningStep.invoice_id == faktura.id, DunningStep.step_no > 0,
                                  DunningStep.status.in_(("queued", "sent")))
        .order_by(DunningStep.step_no)))


def aktiv_paamindelse(session: Session, faktura: Invoice) -> DunningStep | None:
    return session.scalars(select(DunningStep).where(DunningStep.invoice_id == faktura.id, DunningStep.step_no == 0,
                                                     DunningStep.status.in_(("queued", "sent")))).first()


def paamindelse_sendt(session: Session, faktura: Invoice) -> date | None:
    """Dagen for den venlige påmindelse – vores egen eller FarPays."""
    p = aktiv_paamindelse(session, faktura)
    if p is not None and p.sent_at is not None:
        return dansk_dato(p.sent_at)
    return faktura.prior_reminder_at


def byg_paamindelse(session: Session, faktura: Invoice, dag: date) -> DunningStep:
    """Venlig påmindelse: ingen gebyr, ingen rente, ingen kompensation – og kun før første rykker."""
    if faktura.kind != "invoice" or faktura.amount <= 0:
        raise LovgraenseFejl(f"Faktura {faktura.invoice_no} er en kreditnota – den rykkes aldrig")
    if dag <= faktura.due_date:
        raise LovgraenseFejl(f"Faktura {faktura.invoice_no} er ikke forfalden ({faktura.due_date:%d.%m.%Y})")
    if faktura.prior_dunning_count or aktive_rykkere(session, faktura):
        raise LovgraenseFejl(f"Faktura {faktura.invoice_no} har allerede fået en rykker – ingen påmindelse")
    if aktiv_paamindelse(session, faktura) is not None or faktura.prior_reminder_at is not None:
        raise LovgraenseFejl(f"Faktura {faktura.invoice_no} har allerede fået en påmindelse")
    p = DunningStep(invoice_id=faktura.id, step_no=0, due_at=dag, status="queued", fee_amount=Decimal("0.00"),
                    interest_amount=Decimal("0.00"), compensation_amount=Decimal("0.00"))
    session.add(p)
    session.flush()
    return p


def hovedstol_bevaegelser(session: Session, faktura: Invoice) -> list[tuple[date, Decimal]]:
    """Indbetalinger fordelt på hovedstolen (af allocate_payment) – som negative bevægelser."""
    return [(d, -Decimal(b)) for d, b in session.execute(
        select(InvoicePayment.payment_date, PaymentAllocation.amount)
        .join(InvoicePayment, InvoicePayment.id == PaymentAllocation.payment_id)
        .where(PaymentAllocation.invoice_id == faktura.id, PaymentAllocation.target_type == "principal"))]


def byg_rykker(session: Session, faktura: Invoice, dag: date) -> DunningStep:
    if faktura.kind != "invoice" or faktura.amount <= 0:
        raise LovgraenseFejl(f"Faktura {faktura.invoice_no} er en kreditnota – den rykkes aldrig")
    if dag <= faktura.due_date:
        raise LovgraenseFejl(f"Faktura {faktura.invoice_no} er ikke forfalden ({faktura.due_date:%d.%m.%Y})")

    tidligere = aktive_rykkere(session, faktura)
    nr = len(tidligere) + 1
    kontroller_rykkernummer(nr + faktura.prior_dunning_count)

    if tidligere:
        sidste = tidligere[-1]
        if sidste.sent_at is None:
            raise LovgraenseFejl(f"Rykker nr. {sidste.step_no} på faktura {faktura.invoice_no} er ikke sendt endnu")
        forrige = dansk_dato(sidste.sent_at)
    else:
        # Rykker 1 kommer tidligst 10 dage efter den venlige påmindelse og FarPays seneste rykker.
        forrige = max((d for d in (faktura.prior_last_dunning_at, paamindelse_sendt(session, faktura)) if d),
                      default=None)
    kontroller_interval(forrige, dag)

    debitor = session.get(Debtor, faktura.debtor_id)
    kunde = session.get(Client, faktura.client_id)
    # Kompensation én gang, kun erhverv, på den rykker kunden har valgt (FarPay: rykker 3). Er den
    # rykker allerede sendt i FarPay (prior_dunning_count), er beløbet krævet dér – ikke igen.
    kompensation = (KOMPENSATIONSBELOEB
                    if debitor.is_business and nr + faktura.prior_dunning_count == kunde.dunning_compensation_step
                    and not any(r.compensation_amount > 0 for r in tidligere) else Decimal("0.00"))
    kontroller_kompensation(kompensation, debitor.is_business)
    kontroller_rykkergebyr(RYKKERGEBYR)

    # Renten frem til rykkerdagen, minus det, tidligere rykkere allerede har pålagt.
    rente_i_alt, _ = beregn_morarente(session, faktura.due_date, dag,
                                      hovedstol_bevaegelser(session, faktura), Decimal(faktura.amount))
    rente = max(rente_i_alt - sum((r.interest_amount for r in tidligere), Decimal("0")), Decimal("0.00"))

    rykker = DunningStep(invoice_id=faktura.id, step_no=nr, due_at=dag, status="queued",
                         fee_amount=RYKKERGEBYR, interest_amount=rente, compensation_amount=kompensation)
    session.add(rykker)
    session.flush()  # databasens trigger kontrollerer grænserne igen
    return rykker


def marker_sendt(session: Session, rykker: DunningStep, tidspunkt: datetime) -> list[FeeRevenue]:
    """Rykkeren er sendt: pålagte beløb registreres i fee_revenue (endnu ikke indbetalt)."""
    rykker.status, rykker.sent_at = "sent", tidspunkt
    faktura = session.get(Invoice, rykker.invoice_id)
    linjer = [FeeRevenue(client_id=faktura.client_id, invoice_id=faktura.id, dunning_step_id=rykker.id,
                         fee_type=type_, amount_charged=beloeb, accrued_at=tidspunkt)
              for type_, beloeb in (("rykkergebyr", rykker.fee_amount), ("kompensation", rykker.compensation_amount),
                                    ("morarente", rykker.interest_amount)) if beloeb > 0]
    session.add_all(linjer)
    session.flush()
    return linjer

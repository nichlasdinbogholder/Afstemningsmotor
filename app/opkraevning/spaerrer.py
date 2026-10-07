"""Hvornår en rykker IKKE må sendes – det vigtigste sted i modulet.

En rykker på en betalt faktura koster en kunde. `spaerrer(session, faktura, dag)` returnerer ALLE
grunde til, at der ikke må sendes en rykker på fakturaen `dag` (tom liste = må sendes). Hver
afvisning skrives i dunning_skips, så det er lige så let at se, hvorfor en rykker IKKE blev
sendt, som hvorfor den blev.

Spærrerne kontrolleres to gange: når rykkeren lægges i kø (kl. 16) og igen lige før afsendelse
(kl. 9 næste bankdag). Imellem kan en medarbejder fjerne en enkelt rykker.
"""

from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import exists, func, or_, select
from sqlalchemy.orm import Session

from app.kunder.models import Client
from app.opkraevning.lov import MAKS_RYKKERE, MIN_DAGE_MELLEM_RYKKERE
from app.opkraevning.models import (
    CollectionCase,
    Debtor,
    DunningSkip,
    DunningStep,
    InstallmentPlan,
    Invoice,
    InvoicePayment,
)
from app.regnskab.models import JournalEntryCache
from app.tid import bankdage_tilbage, dansk_dato

INDBETALING_BANKDAGE = 2


def _sendte_og_koe(session: Session, faktura: Invoice) -> tuple[list[DunningStep], list[DunningStep]]:
    rykkere = session.scalars(select(DunningStep).where(DunningStep.invoice_id == faktura.id,
                                                        DunningStep.status.in_(("queued", "sent")))
                              .order_by(DunningStep.step_no)).all()
    return [r for r in rykkere if r.status == "sent"], [r for r in rykkere if r.status == "queued"]


def naeste_rykkerdag(kunde: Client, faktura: Invoice, sendte: list[DunningStep]) -> date:
    """Tidligst mulige dag for næste rykker efter kundens plan (og aldrig under lovens 10 dage)."""
    if sendte:
        return dansk_dato(sendte[-1].sent_at) + timedelta(days=max(kunde.dunning_interval_days,
                                                                     MIN_DAGE_MELLEM_RYKKERE))
    if faktura.prior_last_dunning_at:
        return faktura.prior_last_dunning_at + timedelta(days=max(kunde.dunning_interval_days,
                                                                  MIN_DAGE_MELLEM_RYKKERE))
    return faktura.due_date + timedelta(days=kunde.dunning_first_after_days)


def spaerrer(session: Session, faktura: Invoice, dag: date, *, ved_afsendelse: DunningStep | None = None
             ) -> list[tuple[str, dict]]:
    """Alle grunde til, at der ikke må sendes en rykker på fakturaen `dag`. `ved_afsendelse` er den
    rykker i kø, der kontrolleres lige før afsendelse (den tæller ikke som "rykker i kø")."""
    kunde = session.get(Client, faktura.client_id)
    debitor = session.get(Debtor, faktura.debtor_id)
    grunde: list[tuple[str, dict]] = []

    def grund(kode: str, **detaljer) -> None:
        grunde.append((kode, detaljer))

    # Fakturaens tilstand.
    if faktura.kind == "credit_note":
        grund("kreditnota")
    if faktura.status == "paid" or (faktura.kind == "invoice" and faktura.amount_outstanding <= 0):
        grund("betalt", restbeloeb=str(faktura.amount_outstanding))
    if faktura.status == "credited":
        grund("krediteret")
    if faktura.status == "written_off":
        grund("afskrevet")
    if dag <= faktura.due_date:
        grund("ikke_forfalden", forfaldsdato=faktura.due_date.isoformat())

    # Indbetalinger – uanset beløb.
    graense = bankdage_tilbage(dag, INDBETALING_BANKDAGE)
    seneste = session.scalar(select(func.max(InvoicePayment.payment_date)).where(
        InvoicePayment.invoice_id == faktura.id, InvoicePayment.amount > 0))
    if seneste is not None and seneste >= graense:
        grund("indbetaling_seneste_2_bankdage", indbetalt=seneste.isoformat(), fra=graense.isoformat())
    # En indbetaling, der ligger i kassekladden og venter på at blive bogført.
    kladde = session.scalar(select(func.count()).select_from(JournalEntryCache).where(
        JournalEntryCache.client_id == faktura.client_id,
        or_(func.ltrim(JournalEntryCache.fakturanummer, "0") == func.ltrim(faktura.invoice_no, "0"),
            (JournalEntryCache.modpart == f"debitor:{debitor.external_id}")
            & (JournalEntryCache.entry_type == "customerPayment"))))
    if kladde:
        grund("indbetaling_i_kassekladde", linjer=kladde)

    if debitor.blocked_from_dunning:
        grund("debitor_blokeret")
    if session.scalar(select(exists().where(InstallmentPlan.invoice_id == faktura.id,
                                            InstallmentPlan.status == "active"))):
        grund("afbetalingsordning")
    if faktura.kind == "invoice" and Decimal(faktura.amount_outstanding) < Decimal(kunde.dunning_min_amount):
        grund("under_minimumsbeloeb", restbeloeb=str(faktura.amount_outstanding),
              minimum=str(kunde.dunning_min_amount))
    if session.scalar(select(exists().where(CollectionCase.invoice_id == faktura.id,
                                            CollectionCase.status.not_in(("withdrawn", "rejected"))))):
        grund("aktiv_inkassosag")

    # Rykkerne selv.
    sendte, i_koe = _sendte_og_koe(session, faktura)
    i_koe = [r for r in i_koe if ved_afsendelse is None or r.id != ved_afsendelse.id]
    if i_koe:
        grund("rykker_i_koe", rykker=i_koe[0].step_no)
    if len(sendte) + faktura.prior_dunning_count >= MAKS_RYKKERE:
        grund("max_3_rykkere", sendt=len(sendte) + faktura.prior_dunning_count)
    elif dag < naeste_rykkerdag(kunde, faktura, sendte):
        grund("under_10_dage", tidligst=naeste_rykkerdag(kunde, faktura, sendte).isoformat())
    return grunde


def skriv_spaerrer(session: Session, faktura: Invoice, grunde: list[tuple[str, dict]]) -> None:
    session.add_all(DunningSkip(invoice_id=faktura.id, reason=kode, details=detaljer or None)
                    for kode, detaljer in grunde)
    session.flush()


def kandidater(session: Session, client_id: int, dag: date) -> list[Invoice]:
    """Fakturaer, hvor næste rykker efter kundens plan er nået senest `dag`, og som stadig har et
    restbeløb. Kun dem vurderes (og får evt. en spærre skrevet) – så dunning_skips ikke fyldes op
    med betalte fakturaer hver dag."""
    kunde = session.get(Client, client_id)
    fakturaer = session.scalars(select(Invoice).where(
        Invoice.client_id == client_id, Invoice.kind == "invoice", Invoice.status == "open",
        Invoice.amount_outstanding > 0, Invoice.due_date < dag).order_by(Invoice.due_date, Invoice.id)).all()
    ud = []
    for f in fakturaer:
        sendte, _ = _sendte_og_koe(session, f)
        if len(sendte) + f.prior_dunning_count < MAKS_RYKKERE and naeste_rykkerdag(kunde, f, sendte) <= dag:
            ud.append(f)
    return ud

"""Rykkernes døgn – med et vindue, hvor et menneske kan nå at gribe ind.

kl. 16.00  `laeg_i_koe(kunde, afsendelsesdag)`: næste bankdags rykkere lægges i kø (status
           `queued`). Fakturaer, der ikke må rykkes, får deres grunde skrevet i dunning_skips.
imellem    `fjern_rykker(...)`: en medarbejder fjerner en enkelt rykker (fx kunden ringede og
           aftalte noget i går). Det står i dunning_skips som `fjernet_af_medarbejder`.
kl. 09.00  `kontroller_foer_afsendelse(kunde, dag)`: ALLE spærrer kontrolleres igen. En faktura,
           der er betalt i mellemtiden, får sin rykker annulleret (og grunden skrevet). Resten er
           klar til udsendelse – selve afsendelsen og `marker_sendt` er trin 4.

`forhaandsvis(kunde, dag)` viser, hvad der VILLE ske, uden at gemme noget (dunning-preview).
"""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit.models import AuditLog
from app.opkraevning.models import Debtor, DunningSkip, DunningStep, Invoice
from app.opkraevning.rykker import byg_rykker
from app.opkraevning.spaerrer import kandidater, skriv_spaerrer, spaerrer


@dataclass
class Vurdering:
    faktura: Invoice
    debitor: Debtor
    grunde: list[tuple[str, dict]]
    rykker: DunningStep | None = None   # den rykker, der lægges/ville blive lagt i kø


@dataclass
class KoeResultat:
    lagt_i_koe: list[Vurdering] = field(default_factory=list)
    sprunget_over: list[Vurdering] = field(default_factory=list)


def laeg_i_koe(session: Session, client_id: int, afsendelsesdag: date) -> KoeResultat:
    resultat = KoeResultat()
    for f in kandidater(session, client_id, afsendelsesdag):
        v = Vurdering(f, session.get(Debtor, f.debtor_id), spaerrer(session, f, afsendelsesdag))
        if v.grunde:
            skriv_spaerrer(session, f, v.grunde)
            resultat.sprunget_over.append(v)
        else:
            v.rykker = byg_rykker(session, f, afsendelsesdag)
            resultat.lagt_i_koe.append(v)
    return resultat


def kontroller_foer_afsendelse(session: Session, client_id: int, dag: date) -> KoeResultat:
    """Sidste kontrol før afsendelse. Returnerer de rykkere, der må sendes, og dem der blev annulleret."""
    resultat = KoeResultat()
    rykkere = session.scalars(
        select(DunningStep).join(Invoice, Invoice.id == DunningStep.invoice_id)
        .where(Invoice.client_id == client_id, DunningStep.status == "queued", DunningStep.due_at <= dag)
        .order_by(DunningStep.id)).all()
    for r in rykkere:
        f = session.get(Invoice, r.invoice_id)
        v = Vurdering(f, session.get(Debtor, f.debtor_id), spaerrer(session, f, dag, ved_afsendelse=r), r)
        if v.grunde:
            r.status = "cancelled"
            skriv_spaerrer(session, f, v.grunde)
            resultat.sprunget_over.append(v)
        else:
            resultat.lagt_i_koe.append(v)
    session.flush()
    return resultat


class KanIkkeFjernes(Exception):
    pass


def fjern_rykker(session: Session, rykker_id: int, af: str, note: str) -> DunningStep:
    """En medarbejder fjerner en rykker i kø. Kun rykkere, der endnu ikke er sendt."""
    if not note or not note.strip():
        raise KanIkkeFjernes("Skriv hvorfor rykkeren fjernes")
    r = session.get(DunningStep, rykker_id)
    if r is None:
        raise KanIkkeFjernes(f"Rykker {rykker_id} findes ikke")
    if r.status != "queued":
        raise KanIkkeFjernes(f"Rykker {rykker_id} er {r.status} – kun rykkere i kø kan fjernes")
    r.status = "cancelled"
    f = session.get(Invoice, r.invoice_id)
    session.add(DunningSkip(invoice_id=f.id, reason="fjernet_af_medarbejder",
                            details={"af": af, "note": note.strip(), "rykker": r.step_no}))
    session.add(AuditLog(client_id=f.client_id, handling="rykker_fjernet",
                         detaljer={"rykker_id": r.id, "faktura": f.invoice_no, "af": af, "note": note.strip()}))
    session.flush()
    return r


def forhaandsvis(session: Session, client_id: int, dag: date) -> KoeResultat:
    """Hvad ville blive lagt i kø `dag`? Intet gemmes – alt rulles tilbage."""
    resultat = KoeResultat()
    gem = session.begin_nested()
    try:
        for f in kandidater(session, client_id, dag):
            v = Vurdering(f, session.get(Debtor, f.debtor_id), spaerrer(session, f, dag))
            if v.grunde:
                resultat.sprunget_over.append(v)
            else:
                r = byg_rykker(session, f, dag)
                v.rykker = DunningStep(step_no=r.step_no, due_at=r.due_at, fee_amount=Decimal(r.fee_amount),
                                       interest_amount=Decimal(r.interest_amount),
                                       compensation_amount=Decimal(r.compensation_amount))
                resultat.lagt_i_koe.append(v)
    finally:
        gem.rollback()
    return resultat

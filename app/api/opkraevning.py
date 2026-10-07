"""Data til debitorstyringen i webdelen (/api/opkraevning/...). Alt kræver login.

Viser vores kunders kunder (debitorer), deres fakturaer, rykkere og spærrer. Debitorernes data er
persondata, der tilhører kundens kunder: de vises kun til indloggede medarbejdere, og enhver
ændring logges i audit_log. Ændringer kræver headeren X-Afstemning (se data.py).
"""

from datetime import date, datetime, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import Session

from app.api.data import kraev_header
from app.api.login import db, nuvaerende_medarbejder
from app.audit.models import AuditLog
from app.kunder.models import Client
from app.opkraevning.models import (
    KANALER,
    Debtor,
    Delivery,
    DunningSkip,
    DunningStep,
    FeeRevenue,
    InstallmentPlan,
    Invoice,
    InvoicePayment,
)
from app.opkraevning.rykkerkoersel import KanIkkeFjernes, fjern_rykker
from app.personale.models import Staff
from app.tid import TIDSZONE

router = APIRouter(prefix="/api/opkraevning", dependencies=[Depends(nuvaerende_medarbejder)])
MAKS_RAEKKER = 500


def _i_dag() -> date:
    return datetime.now(TIDSZONE).date()


def _kunde(session: Session, client_id: int) -> Client:
    k = session.get(Client, client_id)
    if k is None:
        raise HTTPException(status_code=404, detail="Kunden findes ikke")
    return k


def _kr(b) -> str | None:
    return None if b is None else str(b)


# Betalingsstatus som i FarPay – beregnet ud fra kundens regnskab (restbeløbet), ikke gættet.
def _betalingsstatus(dag: date):
    return case(
        (Invoice.kind == "credit_note", "kreditnota"),
        (Invoice.status == "credited", "krediteret"),
        (Invoice.status == "written_off", "afskrevet"),
        (or_(Invoice.status == "paid", Invoice.amount_outstanding <= 0), "betalt"),
        (Invoice.due_date < dag, "forfaldet"),
        (Invoice.amount_outstanding < Invoice.amount, "delvist_betalt"),
        else_="ikke_betalt",
    )


# Kanal: medarbejderens valg, ellers EAN (offentlige modtagere), ellers e-mail – ellers ingen.
_KANAL = case(
    (Debtor.preferred_channel.is_not(None), Debtor.preferred_channel),
    (or_(Invoice.ean.is_not(None), Debtor.ean.is_not(None)), "ean"),
    (Debtor.email.is_not(None), "email"),
    else_=None,
)

FILTRE = ("alt", "ikke_betalt", "forfaldet", "delvist_betalt", "betalt", "kreditnota", "ingen_kanal", "med_rykker")


def _rykkere_pr_faktura():
    return (select(DunningStep.invoice_id, func.count().label("antal"))
            .where(DunningStep.status == "sent").group_by(DunningStep.invoice_id).subquery())


@router.get("/kunder")
def kunder(session: Session = Depends(db)) -> list[dict]:
    """Kunder med opkrævning slået til (preview/live) – med antal åbne og forfaldne fakturaer."""
    dag = _i_dag()
    raekker = session.execute(
        select(Client.id, Client.navn, Client.kundenummer, Client.dunning_mode,
               func.count(Invoice.id).filter(Invoice.status == "open", Invoice.kind == "invoice",
                                             Invoice.amount_outstanding > 0).label("aabne"),
               func.count(Invoice.id).filter(Invoice.status == "open", Invoice.kind == "invoice",
                                             Invoice.amount_outstanding > 0, Invoice.due_date < dag).label("forfaldne"),
               func.coalesce(func.sum(Invoice.amount_outstanding).filter(
                   Invoice.status == "open", Invoice.kind == "invoice", Invoice.due_date < dag), 0).label("forfaldent"))
        .outerjoin(Invoice, Invoice.client_id == Client.id)
        .where(Client.status == "aktiv")
        .group_by(Client.id).order_by(Client.navn))
    return [{"id": r.id, "navn": r.navn, "kundenummer": r.kundenummer, "rykkere": r.dunning_mode,
             "aabne": r.aabne, "forfaldne": r.forfaldne, "forfaldent_beloeb": _kr(r.forfaldent)} for r in raekker]


@router.get("/{client_id}/fakturaer")
def fakturaer(client_id: int, filter: str = Query(default="alt"), q: str | None = None,
              session: Session = Depends(db)) -> dict:
    k = _kunde(session, client_id)
    if filter not in FILTRE:
        raise HTTPException(status_code=422, detail=f"Ukendt filter – brug {', '.join(FILTRE)}")
    dag = _i_dag()
    status = _betalingsstatus(dag).label("betalingsstatus")
    kanal = _KANAL.label("kanal")
    rykkere = _rykkere_pr_faktura()
    seneste_udsendelse = (select(Delivery.status).where(Delivery.invoice_id == Invoice.id,
                                                        Delivery.dunning_step_id.is_(None))
                          .order_by(Delivery.id.desc()).limit(1).scalar_subquery())
    basis = (select(Invoice, Debtor.external_id, Debtor.name, status, kanal,
                    func.coalesce(rykkere.c.antal, 0).label("rykkere"), seneste_udsendelse.label("sendstatus"))
             .join(Debtor, Debtor.id == Invoice.debtor_id)
             .outerjoin(rykkere, rykkere.c.invoice_id == Invoice.id)
             .where(Invoice.client_id == client_id))
    if q:
        soeg = f"%{q.strip()}%"
        basis = basis.where(or_(Invoice.invoice_no.ilike(soeg), Debtor.name.ilike(soeg),
                                Debtor.external_id.ilike(soeg)))

    # Antal pr. filter (til fanerne, fx "460 Ingen kanal").
    alle = basis.subquery()
    antal = session.execute(select(
        func.count().label("alt"),
        func.count().filter(alle.c.betalingsstatus == "ikke_betalt").label("ikke_betalt"),
        func.count().filter(alle.c.betalingsstatus == "forfaldet").label("forfaldet"),
        func.count().filter(alle.c.betalingsstatus == "delvist_betalt").label("delvist_betalt"),
        func.count().filter(alle.c.betalingsstatus == "betalt").label("betalt"),
        func.count().filter(alle.c.betalingsstatus == "kreditnota").label("kreditnota"),
        func.count().filter(alle.c.kanal.is_(None), alle.c.betalingsstatus.in_(
            ("ikke_betalt", "forfaldet", "delvist_betalt"))).label("ingen_kanal"),
        func.count().filter(alle.c.rykkere > 0).label("med_rykker"),
    ).select_from(alle)).one()._asdict()

    if filter == "ingen_kanal":
        basis = basis.where(_KANAL.is_(None), _betalingsstatus(dag).in_(("ikke_betalt", "forfaldet", "delvist_betalt")))
    elif filter == "med_rykker":
        basis = basis.where(func.coalesce(rykkere.c.antal, 0) > 0)
    elif filter != "alt":
        basis = basis.where(_betalingsstatus(dag) == filter)
    raekker = session.execute(basis.order_by(Invoice.issue_date.desc(), Invoice.id.desc()).limit(MAKS_RAEKKER)).all()
    return {
        "kunde": {"id": k.id, "navn": k.navn, "rykkere": k.dunning_mode},
        "antal": antal,
        "fakturaer": [{
            "id": f.id, "debitornummer": ext, "debitor": navn, "fakturanummer": f.invoice_no,
            "oprettet": f.issue_date.isoformat(), "forfald": f.due_date.isoformat(),
            "beloeb": _kr(f.amount), "restbeloeb": _kr(f.amount_outstanding), "betalingsstatus": bs,
            "kanal": kn, "sendstatus": ss, "rykkere": ry + f.prior_dunning_count,
        } for f, ext, navn, bs, kn, ry, ss in raekker],
        "afkortet": len(raekker) == MAKS_RAEKKER,
    }


@router.get("/faktura/{invoice_id}")
def faktura(invoice_id: int, session: Session = Depends(db)) -> dict:
    f = session.get(Invoice, invoice_id)
    if f is None:
        raise HTTPException(status_code=404, detail="Fakturaen findes ikke")
    d = session.get(Debtor, f.debtor_id)
    betalinger = session.scalars(select(InvoicePayment).where(InvoicePayment.invoice_id == f.id)
                                 .order_by(InvoicePayment.payment_date)).all()
    rykkere = session.scalars(select(DunningStep).where(DunningStep.invoice_id == f.id)
                              .order_by(DunningStep.step_no, DunningStep.id)).all()
    spaerrer = session.scalars(select(DunningSkip).where(DunningSkip.invoice_id == f.id)
                               .order_by(DunningSkip.checked_at.desc()).limit(50)).all()
    gebyrer = session.scalars(select(FeeRevenue).where(FeeRevenue.invoice_id == f.id).order_by(FeeRevenue.id)).all()
    ordning = session.scalars(select(InstallmentPlan).where(InstallmentPlan.invoice_id == f.id,
                                                            InstallmentPlan.status == "active")).first()
    return {
        "id": f.id, "client_id": f.client_id, "fakturanummer": f.invoice_no, "art": f.kind, "status": f.status,
        "oprettet": f.issue_date.isoformat(), "forfald": f.due_date.isoformat(), "beloeb": _kr(f.amount),
        "restbeloeb": _kr(f.amount_outstanding), "valuta": f.currency, "ean": f.ean,
        "tidligere_rykkere": f.prior_dunning_count,
        "debitor": _debitor_json(d),
        "betalinger": [{"dato": b.payment_date.isoformat(), "beloeb": _kr(b.amount), "kilde": b.source}
                       for b in betalinger],
        "rykkere": [{"id": r.id, "nr": r.step_no + f.prior_dunning_count, "status": r.status,
                     "dato": r.due_at.isoformat(), "sendt": r.sent_at.isoformat() if r.sent_at else None,
                     "gebyr": _kr(r.fee_amount), "rente": _kr(r.interest_amount),
                     "kompensation": _kr(r.compensation_amount)} for r in rykkere],
        "spaerrer": [{"aarsag": s.reason, "detaljer": s.details, "tidspunkt": s.checked_at.isoformat()}
                     for s in spaerrer],
        "gebyrer": [{"type": g.fee_type, "paalagt": _kr(g.amount_charged), "indbetalt": _kr(g.amount_collected),
                     "fakturerbar": g.billable_at.isoformat() if g.billable_at else None,
                     "afskrevet": g.written_off_reason} for g in gebyrer],
        "afbetalingsordning": ({"rater": ordning.installments, "foerste": ordning.first_due_date.isoformat()}
                               if ordning else None),
    }


def _debitor_json(d: Debtor) -> dict:
    return {"id": d.id, "nummer": d.external_id, "navn": d.name, "cvr": d.cvr, "email": d.email, "ean": d.ean,
            "adresse": d.address, "postnr": d.zip, "by": d.city, "land": d.country, "erhverv": d.is_business,
            "kanal": d.preferred_channel, "blokeret": d.blocked_from_dunning, "note": d.note}


@router.get("/{client_id}/debitorer")
def debitorer(client_id: int, q: str | None = None, session: Session = Depends(db)) -> list[dict]:
    _kunde(session, client_id)
    aabent = (select(Invoice.debtor_id, func.sum(Invoice.amount_outstanding).label("aabent"),
                     func.count().label("aabne"))
              .where(Invoice.client_id == client_id, Invoice.status == "open", Invoice.kind == "invoice",
                     Invoice.amount_outstanding > 0)
              .group_by(Invoice.debtor_id).subquery())
    stmt = (select(Debtor, aabent.c.aabent, aabent.c.aabne).outerjoin(aabent, aabent.c.debtor_id == Debtor.id)
            .where(Debtor.client_id == client_id))
    if q:
        soeg = f"%{q.strip()}%"
        stmt = stmt.where(or_(Debtor.name.ilike(soeg), Debtor.external_id.ilike(soeg), Debtor.email.ilike(soeg),
                              Debtor.cvr.ilike(soeg)))
    raekker = session.execute(stmt.order_by(Debtor.name).limit(MAKS_RAEKKER)).all()
    return [{**_debitor_json(d), "aabent": _kr(a or Decimal("0")), "aabne_fakturaer": n or 0} for d, a, n in raekker]


class DebitorAendring(BaseModel):
    blokeret: bool | None = None
    note: str | None = Field(default=None, max_length=2000)
    kanal: str | None = None


@router.post("/debitor/{debtor_id}", dependencies=[Depends(kraev_header)])
def aendr_debitor(debtor_id: int, data: DebitorAendring, m: Staff = Depends(nuvaerende_medarbejder),
                  session: Session = Depends(db)) -> dict:
    d = session.get(Debtor, debtor_id)
    if d is None:
        raise HTTPException(status_code=404, detail="Debitoren findes ikke")
    aendret = {}
    if data.blokeret is not None and data.blokeret != d.blocked_from_dunning:
        d.blocked_from_dunning, aendret["blokeret"] = data.blokeret, data.blokeret
    if data.note is not None and data.note != (d.note or ""):
        d.note, aendret["note"] = data.note.strip() or None, "ændret"
    if "kanal" in data.model_fields_set:
        if data.kanal is not None and data.kanal not in KANALER:
            raise HTTPException(status_code=422, detail=f"Ukendt kanal – brug {', '.join(KANALER)}")
        if data.kanal != d.preferred_channel:
            d.preferred_channel, aendret["kanal"] = data.kanal, data.kanal
    if aendret:
        d.updated_at = datetime.now(timezone.utc)
        session.add(AuditLog(client_id=d.client_id, staff_id=m.id, handling="debitor_aendret",
                             detaljer={"debitor_id": d.id, **aendret}))
        session.commit()
    return _debitor_json(d)


@router.get("/{client_id}/rykkere")
def rykkere(client_id: int, session: Session = Depends(db)) -> dict:
    """Rykkere i kø (kan fjernes), seneste sendte og seneste spærrer."""
    _kunde(session, client_id)
    felter = (DunningStep, Invoice.invoice_no, Invoice.prior_dunning_count, Invoice.amount_outstanding,
              Debtor.name, Debtor.external_id)

    def hent(*betingelser, orden, antal):
        return session.execute(select(*felter).join(Invoice, Invoice.id == DunningStep.invoice_id)
                               .join(Debtor, Debtor.id == Invoice.debtor_id)
                               .where(Invoice.client_id == client_id, *betingelser).order_by(orden).limit(antal)).all()

    def json(r, fnr, tidl, rest, navn, ext):
        return {"id": r.id, "fakturanummer": fnr, "debitor": navn, "debitornummer": ext, "nr": r.step_no + tidl,
                "status": r.status, "dato": r.due_at.isoformat(), "sendt": r.sent_at.isoformat() if r.sent_at else None,
                "restbeloeb": _kr(rest), "gebyr": _kr(r.fee_amount), "rente": _kr(r.interest_amount),
                "kompensation": _kr(r.compensation_amount)}

    # Grunden skrives hver gang fakturaen vurderes – vis kun den seneste pr. faktura og årsag.
    seneste = (select(DunningSkip.id).join(Invoice, Invoice.id == DunningSkip.invoice_id)
               .where(Invoice.client_id == client_id)
               .distinct(DunningSkip.invoice_id, DunningSkip.reason)
               .order_by(DunningSkip.invoice_id, DunningSkip.reason, DunningSkip.checked_at.desc(),
                         DunningSkip.id.desc()))
    spaerrer = session.execute(
        select(DunningSkip, Invoice.invoice_no, Debtor.name).join(Invoice, Invoice.id == DunningSkip.invoice_id)
        .join(Debtor, Debtor.id == Invoice.debtor_id).where(DunningSkip.id.in_(seneste))
        .order_by(DunningSkip.checked_at.desc(), Invoice.invoice_no).limit(200)).all()
    return {
        "i_koe": [json(*r) for r in hent(DunningStep.status == "queued", orden=DunningStep.due_at, antal=500)],
        "sendt": [json(*r) for r in hent(DunningStep.status == "sent", orden=DunningStep.sent_at.desc(), antal=200)],
        "spaerrer": [{"fakturanummer": fnr, "debitor": navn, "aarsag": s.reason, "detaljer": s.details,
                      "tidspunkt": s.checked_at.isoformat()} for s, fnr, navn in spaerrer],
    }


class Fjernelse(BaseModel):
    note: str = Field(min_length=1, max_length=2000)


@router.post("/rykker/{rykker_id}/fjern", dependencies=[Depends(kraev_header)])
def fjern(rykker_id: int, data: Fjernelse, m: Staff = Depends(nuvaerende_medarbejder),
          session: Session = Depends(db)) -> dict:
    try:
        r = fjern_rykker(session, rykker_id, m.email, data.note)
    except KanIkkeFjernes as fejl:
        raise HTTPException(status_code=409, detail=str(fejl)) from None
    session.commit()
    return {"id": r.id, "status": r.status}


@router.get("/{client_id}/indstillinger")
def indstillinger(client_id: int, session: Session = Depends(db)) -> dict:
    k = _kunde(session, client_id)
    return {
        "rykkere": k.dunning_mode, "minimum": _kr(k.dunning_min_amount),
        "foerste_rykker_efter_dage": k.dunning_first_after_days, "dage_mellem_rykkere": k.dunning_interval_days,
        "fordeling": k.payment_allocation_order, "erhvervsgrupper": k.business_customer_groups,
        "fi_kreditornummer": k.fi_kreditornummer, "gebyrkonto": k.fee_income_account,
        "rentekonto": k.interest_income_account,
        "gebyraftale": k.fee_assignment_signed_at.isoformat() if k.fee_assignment_signed_at else None,
        "inkassomandat": k.collection_mandate_signed_at.isoformat() if k.collection_mandate_signed_at else None,
        "automatisk_inkasso": k.auto_escalate_to_collection, "inkasso_minimum": _kr(k.collection_min_amount),
    }

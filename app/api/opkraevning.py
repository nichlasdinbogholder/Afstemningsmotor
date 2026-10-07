"""Data til debitorstyringen i webdelen (/api/opkraevning/...). Alt kræver login.

Viser vores kunders kunder (debitorer), deres fakturaer, rykkere og spærrer. Debitorernes data er
persondata, der tilhører kundens kunder: de vises kun til indloggede medarbejdere, og enhver
ændring logges i audit_log. Ændringer kræver headeren X-Afstemning (se data.py).
"""

import csv
import io
from datetime import date, datetime, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy import case, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.data import kraev_header
from app.api.login import db, kraev_admin, nuvaerende_medarbejder
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
from app.opkraevning.fik import UgyldigFIK, fik_linje
from app.opkraevning.lov import (
    INKASSO_KARENS_DAGE,
    KOMPENSATIONSBELOEB,
    MAKS_RYKKERE,
    MIN_DAGE_MELLEM_RYKKERE,
    ManglerReferencesats,
    morarentesats,
)
from app.opkraevning.rykker import RYKKERGEBYR
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
            .where(DunningStep.status == "sent", DunningStep.step_no > 0).group_by(DunningStep.invoice_id).subquery())


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
    basis, dag, rykkere = _faktura_forespoergsel(client_id, q)
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
    basis = _faktura_filter(basis, dag, rykkere, filter)
    raekker = session.execute(basis.order_by(Invoice.issue_date.desc(), Invoice.id.desc()).limit(MAKS_RAEKKER)).all()
    return {
        "kunde": {"id": k.id, "navn": k.navn, "rykkere": k.dunning_mode},
        "antal": antal,
        "fakturaer": [_faktura_raekke(*r) for r in raekker],
        "afkortet": len(raekker) == MAKS_RAEKKER,
    }


def _faktura_raekke(f, ext, navn, bs, kn, ry, ss) -> dict:
    return {
        "id": f.id, "debitornummer": ext, "debitor": navn, "fakturanummer": f.invoice_no,
        "oprettet": f.issue_date.isoformat(), "forfald": f.due_date.isoformat(),
        "beloeb": _kr(f.amount), "restbeloeb": _kr(f.amount_outstanding), "betalingsstatus": bs,
        "kanal": kn, "sendstatus": ss, "rykkere": ry + f.prior_dunning_count,
    }


def _faktura_filter(basis, dag, rykkere, filter: str):
    if filter not in FILTRE:
        raise HTTPException(status_code=422, detail=f"Ukendt filter – brug {', '.join(FILTRE)}")
    if filter == "ingen_kanal":
        return basis.where(_KANAL.is_(None), _betalingsstatus(dag).in_(("ikke_betalt", "forfaldet", "delvist_betalt")))
    if filter == "med_rykker":
        return basis.where(func.coalesce(rykkere.c.antal, 0) > 0)
    if filter != "alt":
        return basis.where(_betalingsstatus(dag) == filter)
    return basis


def _faktura_forespoergsel(client_id: int, q: str | None):
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
    return basis, dag, rykkere


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
    k = session.get(Client, f.client_id)
    status, kanal = session.execute(
        select(_betalingsstatus(_i_dag()), _KANAL).select_from(Invoice).join(Debtor, Debtor.id == Invoice.debtor_id)
        .where(Invoice.id == f.id)).one()
    sendstatus = session.scalar(select(Delivery.status).where(Delivery.invoice_id == f.id)
                                .order_by(Delivery.id.desc()).limit(1))
    return {
        "id": f.id, "client_id": f.client_id, "fakturanummer": f.invoice_no, "art": f.kind, "status": f.status,
        "oprettet": f.issue_date.isoformat(), "forfald": f.due_date.isoformat(), "beloeb": _kr(f.amount),
        "restbeloeb": _kr(f.amount_outstanding), "valuta": f.currency, "ean": f.ean,
        "tidligere_rykkere": f.prior_dunning_count, "betalingsstatus": status, "kanal": kanal,
        "sendstatus": sendstatus,
        "kunde": {"navn": k.navn, "cvr": k.cvr, "adresse": k.adresse, "postnr": k.postnr, "by": k.by},
        "hoved": {"ordrenummer": f.order_no, "oevrig_ref": f.other_ref, "netto": _kr(f.net_amount),
                  "moms": _kr(f.vat_amount), "modtager": f.recipient, "levering": f.delivery,
                  "overskrift": f.heading, "tekst": f.text_line},
        "linjer": f.lines,   # None = ikke hentet endnu
        "betalingsnoegle": _betalingsnoegle(f, k),
        "log": _log(session, f, betalinger, rykkere, spaerrer),
        "debitor": _debitor_json(d),
        "betalinger": [{"dato": b.payment_date.isoformat(), "beloeb": _kr(b.amount), "kilde": b.source}
                       for b in betalinger],
        "rykkere": [{"id": r.id, "nr": _nr(r, f.prior_dunning_count), "status": r.status,
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


def _log(session: Session, f: Invoice, betalinger, rykkere, spaerrer) -> list[dict]:
    """Fakturaens historik, nyeste først: indbetalinger, rykkere, spærrer og medarbejdernes handlinger."""
    def tid(t) -> str:
        return t.isoformat() if isinstance(t, datetime) else datetime.combine(t, datetime.min.time(), TIDSZONE).isoformat()

    ud = [{"tid": tid(f.issue_date), "tekst": "Faktura oprettet i regnskabet"}]
    ud += [{"tid": tid(b.payment_date), "tekst": f"Indbetaling {b.amount} kr. ({b.source})"} for b in betalinger]
    for r in rykkere:
        navn = "Venlig påmindelse" if r.step_no == 0 else f"Rykker {r.step_no + f.prior_dunning_count}"
        ud.append({"tid": tid(r.created_at), "tekst": f"{navn} lagt i kø til {r.due_at:%d.%m.%Y}"})
        if r.sent_at:
            ud.append({"tid": tid(r.sent_at), "tekst": f"{navn} sendt"})
        elif r.status == "cancelled":
            ud.append({"tid": tid(r.created_at), "tekst": f"{navn} annulleret"})
    sete = set()
    for sp in spaerrer:   # nyeste først – kun seneste pr. årsag
        if sp.reason not in sete:
            sete.add(sp.reason)
            ud.append({"tid": tid(sp.checked_at), "tekst": "Ikke rykket", "aarsag": sp.reason})
    handlinger = session.scalars(select(AuditLog).where(
        AuditLog.client_id == f.client_id,
        or_((AuditLog.handling == "rykker_fjernet") & (AuditLog.detaljer["faktura"].astext == f.invoice_no),
            (AuditLog.handling == "debitor_aendret") & (AuditLog.detaljer["debitor_id"].as_integer() == f.debtor_id))
    ).order_by(AuditLog.tidspunkt.desc()).limit(50)).all()
    for a in handlinger:
        af = a.detaljer.get("af") or (session.get(Staff, a.staff_id).email if a.staff_id else None)
        tekst = ("Rykker fjernet: " + a.detaljer.get("note", "") if a.handling == "rykker_fjernet"
                 else "Debitor ændret: " + ", ".join(f"{k}" for k in a.detaljer if k != "debitor_id"))
        ud.append({"tid": tid(a.tidspunkt), "tekst": tekst, "af": af})
    return sorted(ud, key=lambda x: x["tid"], reverse=True)


def _betalingsnoegle(f: Invoice, k: Client) -> str | None:
    """FIK-linjen med KUNDENS FI-kreditornummer – aldrig Din Bogholders."""
    if f.kind != "invoice" or not k.fi_kreditornummer:
        return None
    try:
        return fik_linje(f.invoice_no, k.fi_kreditornummer)
    except (UgyldigFIK, ValueError):
        return None


def _nr(r: DunningStep, tidligere: int) -> int:
    """Rykkerens nummer inkl. FarPays rykkere. 0 = venlig påmindelse."""
    return 0 if r.step_no == 0 else r.step_no + tidligere


def _debitor_json(d: Debtor) -> dict:
    return {"id": d.id, "nummer": d.external_id, "navn": d.name, "cvr": d.cvr, "email": d.email, "ean": d.ean,
            "adresse": d.address, "postnr": d.zip, "by": d.city, "land": d.country, "erhverv": d.is_business,
            "kanal": d.preferred_channel, "blokeret": d.blocked_from_dunning, "note": d.note}


# Debitorens kanal uden for en bestemt faktura: valgt kanal, ellers EAN, ellers e-mail.
_DEBITOR_KANAL = case(
    (Debtor.preferred_channel.is_not(None), Debtor.preferred_channel),
    (Debtor.ean.is_not(None), "ean"),
    (Debtor.email.is_not(None), "email"),
    else_=None,
)
DEBITOR_FILTRE = ("alt", "med_aabne", "ingen_kanal", "blokeret", "erhverv")


def _debitor_forespoergsel(client_id: int, q: str | None):
    aabent = (select(Invoice.debtor_id, func.sum(Invoice.amount_outstanding).label("aabent"),
                     func.count().label("aabne"))
              .where(Invoice.client_id == client_id, Invoice.status == "open", Invoice.kind == "invoice",
                     Invoice.amount_outstanding > 0)
              .group_by(Invoice.debtor_id).subquery())
    stmt = (select(Debtor, aabent.c.aabent, aabent.c.aabne, _DEBITOR_KANAL.label("kanal_nu"))
            .outerjoin(aabent, aabent.c.debtor_id == Debtor.id).where(Debtor.client_id == client_id))
    if q:
        soeg = f"%{q.strip()}%"
        stmt = stmt.where(or_(Debtor.name.ilike(soeg), Debtor.external_id.ilike(soeg), Debtor.email.ilike(soeg),
                              Debtor.cvr.ilike(soeg), Debtor.address.ilike(soeg), Debtor.city.ilike(soeg)))
    return stmt, aabent


def _debitor_filter(stmt, aabent, filter: str):
    if filter not in DEBITOR_FILTRE:
        raise HTTPException(status_code=422, detail=f"Ukendt filter – brug {', '.join(DEBITOR_FILTRE)}")
    return {
        "alt": stmt,
        "med_aabne": stmt.where(aabent.c.aabne > 0),
        "ingen_kanal": stmt.where(_DEBITOR_KANAL.is_(None)),
        "blokeret": stmt.where(Debtor.blocked_from_dunning.is_(True)),
        "erhverv": stmt.where(Debtor.is_business.is_(True)),
    }[filter]


def _sorter_debitorer(stmt):
    # Kundenumre er tekst i databasen – sortér numerisk, når de er tal (som i FarPay: nyeste øverst).
    return stmt.order_by(func.length(Debtor.external_id).desc(), Debtor.external_id.desc())


@router.get("/{client_id}/debitorer")
def debitorer(client_id: int, filter: str = Query(default="alt"), q: str | None = None,
              session: Session = Depends(db)) -> dict:
    _kunde(session, client_id)
    stmt, aabent = _debitor_forespoergsel(client_id, q)
    alle = stmt.subquery()
    antal = session.execute(select(
        func.count().label("alt"),
        func.count().filter(alle.c.aabne > 0).label("med_aabne"),
        func.count().filter(alle.c.kanal_nu.is_(None)).label("ingen_kanal"),
        func.count().filter(alle.c.blocked_from_dunning.is_(True)).label("blokeret"),
        func.count().filter(alle.c.is_business.is_(True)).label("erhverv"),
    ).select_from(alle)).one()._asdict()
    raekker = session.execute(_sorter_debitorer(_debitor_filter(stmt, aabent, filter)).limit(MAKS_RAEKKER)).all()
    return {"antal": antal, "afkortet": len(raekker) == MAKS_RAEKKER,
            "debitorer": [{**_debitor_json(d), "kanal_nu": kn, "aabent": _kr(a or Decimal("0")),
                           "aabne_fakturaer": n or 0} for d, a, n, kn in raekker]}


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
        return {"id": r.id, "fakturanummer": fnr, "debitor": navn, "debitornummer": ext, "nr": _nr(r, tidl),
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
    try:
        rentesats = str(morarentesats(session, _i_dag()))
    except ManglerReferencesats:
        rentesats = None
    return {
        "virksomhed": {"navn": k.navn, "cvr": k.cvr, "adresse": k.adresse, "postnr": k.postnr, "by": k.by,
                       "kundenummer": k.kundenummer},
        "svar_email": k.reply_to_email, "kompensation_paa_rykker": k.dunning_compensation_step,
        "paamindelse_efter_dage": k.reminder_after_days,
        "rykkergebyr": _kr(RYKKERGEBYR), "kompensationsbeloeb": _kr(KOMPENSATIONSBELOEB),
        "rentesats": rentesats, "inkasso_efter_dage": INKASSO_KARENS_DAGE,
        "rykkere": k.dunning_mode, "minimum": _kr(k.dunning_min_amount),
        "foerste_rykker_efter_dage": k.dunning_first_after_days, "dage_mellem_rykkere": k.dunning_interval_days,
        "fordeling": k.payment_allocation_order, "erhvervsgrupper": k.business_customer_groups,
        "fi_kreditornummer": k.fi_kreditornummer, "gebyrkonto": k.fee_income_account,
        "rentekonto": k.interest_income_account,
        "gebyraftale": k.fee_assignment_signed_at.isoformat() if k.fee_assignment_signed_at else None,
        "inkassomandat": k.collection_mandate_signed_at.isoformat() if k.collection_mandate_signed_at else None,
        "automatisk_inkasso": k.auto_escalate_to_collection, "inkasso_minimum": _kr(k.collection_min_amount),
    }


class IndstillingAendring(BaseModel):
    """Kun felter, der må ændres fra websiden. 'live' kan først vælges, når udsendelsen er bygget."""

    rykkere: str | None = Field(default=None, pattern="^(off|preview)$")
    svar_email: str | None = Field(default=None, max_length=255, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    minimum: Decimal | None = Field(default=None, ge=0, max_digits=15, decimal_places=2)
    foerste_rykker_efter_dage: int | None = Field(default=None, ge=1, le=365)
    dage_mellem_rykkere: int | None = Field(default=None, ge=MIN_DAGE_MELLEM_RYKKERE, le=365)
    kompensation_paa_rykker: int | None = Field(default=None, ge=1, le=MAKS_RYKKERE)
    paamindelse_efter_dage: int | None = Field(default=None, ge=1, le=60)   # null = ingen påmindelse
    fi_kreditornummer: str | None = Field(default=None, pattern="^[0-9]{8}$")
    gebyrkonto: int | None = Field(default=None, ge=1)
    rentekonto: int | None = Field(default=None, ge=1)
    erhvervsgrupper: list[int] | None = None


_INDSTILLING_FELTER = {
    "rykkere": "dunning_mode", "svar_email": "reply_to_email", "minimum": "dunning_min_amount",
    "foerste_rykker_efter_dage": "dunning_first_after_days", "dage_mellem_rykkere": "dunning_interval_days",
    "kompensation_paa_rykker": "dunning_compensation_step", "fi_kreditornummer": "fi_kreditornummer",
    "paamindelse_efter_dage": "reminder_after_days",
    "gebyrkonto": "fee_income_account", "rentekonto": "interest_income_account",
    "erhvervsgrupper": "business_customer_groups",
}
# Felter, der skal have en værdi (de andre kan tømmes ved at sende null).
_KRAEVER_VAERDI = {"rykkere", "minimum", "foerste_rykker_efter_dage", "dage_mellem_rykkere",
                   "kompensation_paa_rykker", "erhvervsgrupper"}


@router.post("/{client_id}/indstillinger", dependencies=[Depends(kraev_header)])
def aendr_indstillinger(client_id: int, data: IndstillingAendring, m: Staff = Depends(kraev_admin),
                        session: Session = Depends(db)) -> dict:
    """Kun administratorer. Hver ændring logges med gammel og ny værdi."""
    k = _kunde(session, client_id)
    aendret = {}
    for felt in data.model_fields_set:
        ny = getattr(data, felt)
        if ny is None and felt in _KRAEVER_VAERDI:
            raise HTTPException(status_code=422, detail=f"{felt} skal have en værdi")
        kolonne = _INDSTILLING_FELTER[felt]
        gammel = getattr(k, kolonne)
        if felt == "erhvervsgrupper":
            ny = sorted(set(ny))
        if ny != gammel:
            setattr(k, kolonne, ny)
            aendret[felt] = {"fra": _json_vaerdi(gammel), "til": _json_vaerdi(ny)}
    if aendret:
        try:
            session.flush()
        except IntegrityError:
            session.rollback()
            raise HTTPException(status_code=409, detail="FI-kreditornummeret bruges allerede af en anden kunde") from None
        session.add(AuditLog(client_id=k.id, staff_id=m.id, handling="indstillinger_aendret", detaljer=aendret))
        session.commit()
    return indstillinger(client_id, session)


def _json_vaerdi(v):
    return str(v) if isinstance(v, Decimal) else v


# --- Afbetalingsordninger (oprettes i trin 5) ------------------------------------------------------

AFBETALING_FILTRE = {"aktive": ("active",), "misligholdt": ("defaulted",), "alle": None}


@router.get("/{client_id}/afbetalinger")
def afbetalinger(client_id: int, filter: str = Query(default="alle"), q: str | None = None,
                 session: Session = Depends(db)) -> dict:
    _kunde(session, client_id)
    if filter not in AFBETALING_FILTRE:
        raise HTTPException(status_code=422, detail=f"Ukendt filter – brug {', '.join(AFBETALING_FILTRE)}")
    stmt = (select(InstallmentPlan, Invoice.invoice_no, Debtor.external_id, Debtor.name)
            .join(Invoice, Invoice.id == InstallmentPlan.invoice_id).join(Debtor, Debtor.id == Invoice.debtor_id)
            .where(Invoice.client_id == client_id))
    if q:
        soeg = f"%{q.strip()}%"
        stmt = stmt.where(or_(Invoice.invoice_no.ilike(soeg), Debtor.name.ilike(soeg), Debtor.external_id.ilike(soeg)))
    alle = stmt.subquery()
    antal = session.execute(select(
        func.count().label("alle"),
        func.count().filter(alle.c.status == "active").label("aktive"),
        func.count().filter(alle.c.status == "defaulted").label("misligholdt"),
    ).select_from(alle)).one()._asdict()
    if AFBETALING_FILTRE[filter]:
        stmt = stmt.where(InstallmentPlan.status.in_(AFBETALING_FILTRE[filter]))
    raekker = session.execute(stmt.order_by(InstallmentPlan.created_at.desc()).limit(MAKS_RAEKKER)).all()
    return {"antal": antal, "ordninger": [{
        "id": o.id, "fakturanummer": fnr, "debitornummer": ext, "debitor": navn, "beloeb": _kr(o.total_amount),
        "rater": o.installments, "foerste": o.first_due_date.isoformat(), "interval_dage": o.interval_days,
        "status": o.status, "oprettet": o.created_at.isoformat()} for o, fnr, ext, navn in raekker]}


# --- Eksport til Excel (CSV). Debitorernes data er persondata: HVER eksport logges. -------------------


BETALINGSSTATUS_TEKST = {"betalt": "Betalt", "ikke_betalt": "Ikke betalt", "forfaldet": "Forfaldet",
                         "delvist_betalt": "Delvist betalt", "kreditnota": "Kreditnota", "krediteret": "Krediteret",
                         "afskrevet": "Afskrevet"}
KANAL_TEKST = {"email": "E-mail", "ean": "EAN", "print": "Print", "eboks": "e-Boks", None: "Ingen kanal"}


def _celle(v):
    if v is None:
        return ""
    if isinstance(v, Decimal):
        return str(v).replace(".", ",")
    if isinstance(v, str) and v[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + v   # en tekst må aldrig blive til en formel i Excel
    return v


def _csv(navn: str, kolonner: list[str], raekker: list[list]) -> Response:
    ud = io.StringIO()
    w = csv.writer(ud, delimiter=";", lineterminator="\r\n")
    w.writerow(kolonner)
    for r in raekker:
        w.writerow([_celle(v) for v in r])
    # BOM, så Excel læser æøå rigtigt.
    return Response(content="\ufeff" + ud.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{navn}"',
                             "Cache-Control": "no-store"})


def _log_eksport(session: Session, k: Client, m: Staff, hvad: str, antal: int, filter: str, q: str | None) -> None:
    session.add(AuditLog(client_id=k.id, staff_id=m.id, handling="eksport",
                         detaljer={"hvad": hvad, "antal": antal, "filter": filter, "soegning": q or None}))
    session.commit()


@router.get("/{client_id}/eksport/fakturaer.csv")
def eksport_fakturaer(client_id: int, filter: str = Query(default="alt"), q: str | None = None,
                      m: Staff = Depends(nuvaerende_medarbejder), session: Session = Depends(db)) -> Response:
    k = _kunde(session, client_id)
    basis, dag, rykkere = _faktura_forespoergsel(client_id, q)
    raekker = session.execute(_faktura_filter(basis, dag, rykkere, filter)
                              .order_by(Invoice.issue_date.desc(), Invoice.id.desc())).all()
    _log_eksport(session, k, m, "fakturaer", len(raekker), filter, q)
    return _csv(f"opkraevninger-{k.kundenummer}-{dag.isoformat()}.csv",
                ["Kundenr.", "Kunde", "Fakturanr.", "Oprettet", "Betalingsdag", "Betalingsstatus", "Kanal",
                 "Rykkere", "Fakturabeløb", "Restbeløb"],
                [[ext, navn, f.invoice_no, f"{f.issue_date:%d-%m-%Y}", f"{f.due_date:%d-%m-%Y}",
                  BETALINGSSTATUS_TEKST.get(bs, bs), KANAL_TEKST.get(kn, kn),
                  ry + f.prior_dunning_count, f.amount, f.amount_outstanding]
                 for f, ext, navn, bs, kn, ry, ss in raekker])


@router.get("/{client_id}/eksport/debitorer.csv")
def eksport_debitorer(client_id: int, filter: str = Query(default="alt"), q: str | None = None,
                      m: Staff = Depends(nuvaerende_medarbejder), session: Session = Depends(db)) -> Response:
    k = _kunde(session, client_id)
    stmt, aabent = _debitor_forespoergsel(client_id, q)
    raekker = session.execute(_sorter_debitorer(_debitor_filter(stmt, aabent, filter))).all()
    _log_eksport(session, k, m, "debitorer", len(raekker), filter, q)
    return _csv(f"kunder-{k.kundenummer}-{_i_dag().isoformat()}.csv",
                ["Kundenr.", "Kunde", "CVR", "E-mail", "EAN", "Adresse", "Postnr.", "By", "Kanal", "Blokeret",
                 "Åbne fakturaer", "Åbent beløb"],
                [[d.external_id, d.name, d.cvr, d.email, d.ean, d.address, d.zip, d.city, KANAL_TEKST.get(kn, kn),
                  "ja" if d.blocked_from_dunning else "nej", n or 0, a or Decimal("0")] for d, a, n, kn in raekker])

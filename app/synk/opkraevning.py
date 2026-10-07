"""Hent grundlaget for opkrævning: debitorer, bogførte fakturaer og indbetalinger.

Kører KUN for kunder, hvor rykkere ikke er slået fra (clients.dunning_mode <> 'off') – så vi
ikke gemmer debitorernes persondata for kunder, der ikke bruger opkrævningen.

- Debitorer og fakturaer hentes fuldt hver gang (ressourcen `invoices` i sync_state).
  Medarbejdernes egne felter (blocked_from_dunning, note, preferred_channel) røres aldrig.
- Indbetalinger læses af de posteringer, der ALLEREDE er hentet (entries): en indbetaling fra en
  debitor med et fakturanummer. Systemet udligner selv fakturaen, så restbeløbet er sandheden;
  indbetalingen bruges til spærren "indbetaling de seneste 2 bankdage" og til fordelingen.
  Beløbet er debitor-benet (det, der gik til fakturaen). Gebyr-/rentebenet læses i trin 6.
"""

import logging
import re
from datetime import datetime, timezone

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.adaptere.regnskab.base import AccountingProvider
from app.kunder.models import Client
from app.opkraevning.models import Debtor, Invoice, InvoicePayment
from app.synk.faelles import SynkResultat, _adapter
from app.synk.tilstand import synk_transaktion

log = logging.getLogger(__name__)

INDBETALINGER_SQL = text("""
    SELECT e.bogfoert_id, e.dato, e.beloeb, i.id AS invoice_id
    FROM entries e
    JOIN debtors d ON d.client_id = e.client_id AND e.modpart = 'debitor:' || d.external_id
    JOIN invoices i ON i.client_id = e.client_id AND i.debtor_id = d.id
                   AND ltrim(i.invoice_no, '0') = ltrim(e.fakturanummer, '0')
    WHERE e.client_id = :c AND e.entry_type = 'customerPayment' AND e.fakturanummer IS NOT NULL
      AND e.beloeb IS NOT NULL AND e.beloeb <> 0
""")


def _kun_cifre(vaerdi: str | None, laengde: int) -> str | None:
    """CVR (8) og EAN (13) gemmes kun, hvis de er gyldige – ellers tomme (vi gætter ikke)."""
    if not vaerdi:
        return None
    cifre = re.sub(r"\D", "", vaerdi)
    return cifre if len(cifre) == laengde else None


def synk_invoices(session: Session, client_id: int, adapter: AccountingProvider | None = None) -> SynkResultat:
    kunde = session.get(Client, client_id)
    if kunde is None or kunde.dunning_mode == "off":
        return SynkResultat("invoices", 0)
    nu = datetime.now(timezone.utc)
    erhvervsgrupper = set(kunde.business_customer_groups or [])

    with synk_transaktion(session, client_id, "invoices") as synk:
        with _adapter(session, client_id, adapter) as a:
            debitorer = a.fetch_debtors()
            fakturaer = a.fetch_invoices()

        raekker = [{
            "client_id": client_id, "external_id": str(d.kundenummer), "name": d.navn,
            "cvr": _kun_cifre(d.cvr, 8), "email": d.email, "ean": _kun_cifre(d.ean, 13), "address": d.adresse,
            "zip": d.postnr, "city": d.by, "country": d.land, "customer_group": d.gruppe,
            "is_business": d.gruppe in erhvervsgrupper, "updated_at": nu,
        } for d in debitorer]
        if raekker:
            stmt = insert(Debtor).values(raekker)
            session.execute(stmt.on_conflict_do_update(
                constraint="uq_debtors_kunde_ekstern",
                set_={k: stmt.excluded[k] for k in raekker[0] if k not in ("client_id", "external_id")}))

        debitor_id = {e: i for e, i in session.execute(
            select(Debtor.external_id, Debtor.id).where(Debtor.client_id == client_id))}
        ukendte = 0
        raekker = []
        for f in fakturaer:
            did = debitor_id.get(str(f.kundenummer))
            if did is None:
                ukendte += 1
                continue
            raekker.append({
                "client_id": client_id, "external_id": str(f.nummer), "invoice_no": str(f.nummer),
                "debtor_id": did, "kind": "credit_note" if f.beloeb < 0 else "invoice",
                "issue_date": f.dato, "due_date": f.forfaldsdato, "amount": f.beloeb,
                "amount_outstanding": f.restbeloeb, "currency": f.valuta, "ean": _kun_cifre(f.ean, 13),
                "status": "paid" if f.restbeloeb == 0 else "open", "updated_at": nu,
            })
        if raekker:
            stmt = insert(Invoice).values(raekker)
            felter = [k for k in raekker[0] if k not in ("client_id", "external_id", "status")]
            session.execute(stmt.on_conflict_do_update(
                constraint="uq_invoices_kunde_ekstern",
                set_={**{k: stmt.excluded[k] for k in felter},
                      # Manuelle statusser (krediteret/afskrevet) bevares; ellers følger status restbeløbet.
                      "status": text("CASE WHEN invoices.status IN ('credited', 'written_off') "
                                     "THEN invoices.status ELSE excluded.status END")}))
        if ukendte:
            log.warning("Kunde %s: %s fakturaer til ukendte debitorer blev sprunget over", client_id, ukendte)

        nye = 0
        for r in session.execute(INDBETALINGER_SQL, {"c": client_id}):
            nye += session.execute(insert(InvoicePayment).values(
                invoice_id=r.invoice_id, payment_date=r.dato, amount=-r.beloeb, source="regnskab",
                external_id=str(r.bogfoert_id)).on_conflict_do_nothing()).rowcount
        synk.gennemfoert(None, antal_hentet=len(fakturaer))
    return SynkResultat("invoices", len(fakturaer), nye=nye)

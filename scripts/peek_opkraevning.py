"""Trin 0 for opkrævningsmodulet: vis RÅ JSON fra e-conomic for én faktura og én debitor,
før datamodellen skrives. Feltnavnene i modellen skal komme herfra – ikke gættes.

    # e-conomics offentlige demoaftale (kun opdigtede data – sikkert at dele):
    python scripts/peek_opkraevning.py --demo

    # en rigtig kunde (på serveren; tokenet læses krypteret fra databasen):
    docker compose -f docker-compose.prod.yml exec -T api python scripts/peek_opkraevning.py --kundenummer <nr>

Kun GET-kald. Tokens skrives aldrig ud. Persondata (navne, e-mails, adresser, telefon, CVR, EAN …)
erstattes som standard med «tekst, N tegn», så feltnavne og typer kan ses uden at dele
debitorernes oplysninger. `--vis-persondata` slår det fra (del IKKE det output).
"""

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

ISO_DATO = re.compile(r"^\d{4}-\d{2}-\d{2}([T ][0-9:.+\-Z]*)?$")
VALUTA = re.compile(r"^[A-Z]{3}$")
# Tekster, der beskriver strukturen (ikke personer), og derfor vises uændret.
STRUKTUR_NOEGLER = {"self", "entryType", "currency", "vatZone", "layoutNumber", "paymentTermsType",
                    "status", "type", "name_of_endpoint", "collection", "metaData", "templates"}


def anonymiser(data, noegle: str | None = None):
    if isinstance(data, dict):
        return {k: anonymiser(v, k) for k, v in data.items()}
    if isinstance(data, list):
        return [anonymiser(v, noegle) for v in data]
    if isinstance(data, str):
        if (noegle in STRUKTUR_NOEGLER or data.startswith("https://restapi.e-conomic.com")
                or ISO_DATO.match(data) or VALUTA.match(data) or data in ("", "true", "false")):
            return data
        return f"«tekst, {len(data)} tegn»"
    return data  # tal, true/false, null vises som de er (beløb, numre)


def vis(titel: str, data, skjul: bool) -> None:
    print(f"\n===== {titel} =====")
    print(json.dumps(anonymiser(data) if skjul else data, indent=2, ensure_ascii=False))


def lav_klient(args):
    from app.adaptere.economic.klient import EconomicKlient, app_secret_token
    from app.config import get_settings
    from app.sikkerhed.hemmeligheder import HemmeligtToken

    if args.demo:
        return EconomicKlient(HemmeligtToken("demo"), HemmeligtToken("demo"))
    import app.models  # noqa: F401
    from sqlalchemy import select

    from app.adaptere.adgang import hent_adgang
    from app.db import ny_session
    from app.kunder.models import Client

    with ny_session() as session:
        kunde = session.scalars(select(Client).where(Client.kundenummer == args.kundenummer)).one_or_none()
        if kunde is None:
            raise SystemExit("Kunden findes ikke")
        adgang = hent_adgang(session, kunde.id, "economic")
    return EconomicKlient(app_secret_token(), adgang.token, base_url=get_settings().economic_api_base_url)


def hent(klient, sti: str, **params):
    return klient._hent_side(sti, params or None)  # samme GET med genforsøg som adapteren


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Rå JSON for én faktura og én debitor fra e-conomic.")
    hvem = parser.add_mutually_exclusive_group(required=True)
    hvem.add_argument("--demo", action="store_true", help="e-conomics offentlige demoaftale")
    hvem.add_argument("--kundenummer", help="en kunde i vores database")
    parser.add_argument("--faktura", type=int, help="bestemt bogført fakturanummer (standard: nyeste ubetalte)")
    parser.add_argument("--vis-persondata", action="store_true", help="vis navne/adresser uændret (del ikke)")
    args = parser.parse_args(argv)
    skjul = not args.vis_persondata

    with lav_klient(args) as k:
        vis("GET /invoices  (hvilke faktura-lister findes)", hent(k, "/invoices"), skjul=False)

        if args.faktura:
            faktura = hent(k, f"/invoices/booked/{args.faktura}")
        else:
            ubetalte = hent(k, "/invoices/unpaid", pageSize=1)
            vis("GET /invoices/unpaid?pageSize=1  (listeudgave, inkl. pagination)", ubetalte, skjul)
            raekker = ubetalte.get("collection") or hent(k, "/invoices/booked", pageSize=1,
                                                          sort="-bookedInvoiceNumber").get("collection") or []
            if not raekker:
                print("\nAftalen har ingen bogførte fakturaer.")
                return 0
            faktura = hent(k, raekker[0]["self"].split("restapi.e-conomic.com", 1)[-1])
        vis("Én bogført faktura (fuld udgave via self)", faktura, skjul)

        for liste in ("/invoices/overdue", "/invoices/paid", "/invoices/not-due"):
            try:
                vis(f"GET {liste}?pageSize=1", hent(k, liste, pageSize=1), skjul)
            except Exception as fejl:  # noqa: BLE001 – vis kun, at listen ikke findes
                print(f"\n===== GET {liste}: {type(fejl).__name__}: {fejl} =====")

        kundenr = (faktura.get("customer") or {}).get("customerNumber")
        if kundenr is not None:
            debitor = hent(k, f"/customers/{kundenr}")
            vis(f"Debitoren: GET /customers/{kundenr}", debitor, skjul)
            gruppe = (debitor.get("customerGroup") or {}).get("customerGroupNumber")
            if gruppe is not None:
                vis(f"Debitorens gruppe: GET /customer-groups/{gruppe}",
                    hent(k, f"/customer-groups/{gruppe}"), skjul)
            try:
                vis("Debitorens kontaktpersoner: /customers/{nr}/contacts?pageSize=2",
                    hent(k, f"/customers/{kundenr}/contacts", pageSize=2), skjul)
            except Exception as fejl:  # noqa: BLE001
                print(f"\n===== kontaktpersoner: {type(fejl).__name__}: {fejl} =====")
        vis("Alle debitorgrupper: GET /customer-groups", hent(k, "/customer-groups", pageSize=50), skjul)
    return 0


if __name__ == "__main__":
    sys.exit(main())

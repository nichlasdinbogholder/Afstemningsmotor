"""Indlæs et kontoudtog fra en CSV-fil (indtil indlæsning fra mail/SharePoint er bygget).

    python -m app.kontoudtog.importer --kundenummer 40850635 --kilde grossist \\
        --kreditor 45 --fra 2026-09-01 --til 2026-09-30 --fortegn modsat --fil udtog.csv

    python -m app.kontoudtog.importer --kundenummer 40850635 --kilde skattekonto \\
        --konto 6710 --fra 2026-09-01 --til 2026-09-30 --fortegn samme --fil skattekonto.csv

CSV-filen skal have kolonnerne dato, reference, tekst og beløb (overskrift i første linje;
semikolon eller komma). Datoer som 2026-09-03, 03-09-2026 eller 03.09.2026. Beløb som
1.234,56 eller 1234.56 (minus for negative).

--fortegn: "samme", hvis beløbene står med samme fortegn som i bogføringen; "modsat",
hvis de er vendt (typisk for et udtog fra en leverandør: deres faktura +1.000 er −1.000
på leverandøren i bogføringen).

Indlæsningen ændrer intet i regnskabssystemet. Matchningen sker, når reglerne køres
(python -m app.cli run-rules <id> – eller i natkørslen).
"""

import argparse
import csv
import sys
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from sqlalchemy import select

import app.models  # noqa: F401
from app.db import ny_session
from app.kontoudtog.models import FORTEGN, KILDER, Statement, StatementLine
from app.kunder.models import Client


class IndlaesningsFejl(Exception):
    pass


def tolk_dato(tekst: str) -> date:
    tekst = tekst.strip()
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d.%m.%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(tekst, fmt).date()
        except ValueError:
            pass
    raise IndlaesningsFejl(f"Ukendt datoformat: '{tekst}'")


def tolk_beloeb(tekst: str) -> Decimal:
    t = tekst.strip().replace(" ", "").replace("kr.", "").replace("kr", "")
    if "," in t:  # dansk: 1.234,56
        t = t.replace(".", "").replace(",", ".")
    try:
        return Decimal(t).quantize(Decimal("0.01"))
    except InvalidOperation:
        raise IndlaesningsFejl(f"Ukendt beløb: '{tekst}'") from None


def laes_csv(sti: Path) -> list[dict]:
    indhold = sti.read_text(encoding="utf-8-sig")
    dialekt = csv.Sniffer().sniff(indhold.splitlines()[0], delimiters=";,")
    raekker = list(csv.DictReader(indhold.splitlines(), dialect=dialekt))
    navne = {k.strip().lower().replace("ø", "oe"): k for k in (raekker[0].keys() if raekker else [])}
    mangler = [k for k in ("dato", "beloeb") if k not in navne]
    if mangler:
        raise IndlaesningsFejl(f"CSV-filen mangler kolonnerne: {', '.join(mangler)} (fandt: {list(navne)})")
    linjer = []
    for nr, r in enumerate(raekker, 1):
        felt = lambda n: (r.get(navne[n]) or "").strip() if n in navne else ""  # noqa: E731
        if not felt("dato") and not felt("beloeb"):
            continue
        linjer.append({"linje_nr": nr, "dato": tolk_dato(felt("dato")), "beloeb": tolk_beloeb(felt("beloeb")),
                       "reference": felt("reference") or None, "tekst": felt("tekst") or None,
                       "raa_data": {k: v for k, v in r.items() if k}})
    return linjer


def indlaes(session, client_id: int, kilde: str, fra: date, til: date, fortegn: str, linjer: list[dict],
            kontonummer: int | None = None, modpart: str | None = None, kildefil: str | None = None) -> Statement:
    udtog = Statement(client_id=client_id, kilde=kilde, kontonummer=kontonummer, modpart=modpart,
                      periode_fra=fra, periode_til=til, fortegn=fortegn, kildefil=kildefil)
    session.add(udtog)
    session.flush()
    for l in linjer:
        session.add(StatementLine(statement_id=udtog.id, client_id=client_id, **l))
    session.flush()
    return udtog


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Indlæs et kontoudtog fra en CSV-fil.")
    parser.add_argument("--kundenummer", required=True)
    parser.add_argument("--kilde", required=True, choices=KILDER)
    parser.add_argument("--konto", type=int, help="finanskonto i bogføringen")
    parser.add_argument("--kreditor", type=int, help="leverandørnummer")
    parser.add_argument("--debitor", type=int, help="kundenummer (debitor)")
    parser.add_argument("--fra", required=True, type=date.fromisoformat)
    parser.add_argument("--til", required=True, type=date.fromisoformat)
    parser.add_argument("--fortegn", required=True, choices=FORTEGN)
    parser.add_argument("--fil", required=True, type=Path)
    args = parser.parse_args(argv)
    if args.kreditor and args.debitor:
        parser.error("brug enten --kreditor eller --debitor")
    modpart = (f"kreditor:{args.kreditor}" if args.kreditor else f"debitor:{args.debitor}" if args.debitor else None)
    if args.konto is None and modpart is None:
        parser.error("angiv --konto og/eller --kreditor/--debitor (hvad udtoget skal sammenlignes med)")

    try:
        linjer = laes_csv(args.fil)
    except (IndlaesningsFejl, OSError, csv.Error) as fejl:
        print(f"Fejl: {fejl}", file=sys.stderr)
        return 2
    with ny_session() as session:
        kunde = session.scalars(select(Client).where(Client.kundenummer == args.kundenummer)).one_or_none()
        if kunde is None:
            print("Fejl: Kunden findes ikke", file=sys.stderr)
            return 2
        udtog = indlaes(session, kunde.id, args.kilde, args.fra, args.til, args.fortegn, linjer,
                        args.konto, modpart, args.fil.name)
        session.commit()
    print(f"Indlæst {len(linjer)} linjer som kontoudtog #{udtog.id} ({args.kilde}, {args.fra} – {args.til}).")
    print(f"Match nu med: python -m app.cli run-rules {kunde.id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

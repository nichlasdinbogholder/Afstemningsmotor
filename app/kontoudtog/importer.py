"""Indlæs et kontoudtog fra en PDF- eller CSV-fil (indtil indlæsning fra mail/SharePoint er bygget).

    python -m app.kontoudtog.importer --kundenummer 40850635 --kilde grossist \\
        --kreditor 45 --fortegn modsat --fil bygma.pdf

    python -m app.kontoudtog.importer --kundenummer 40850635 --kilde grossist \\
        --kreditor 45 --fra 2026-09-01 --til 2026-09-30 --fortegn modsat --fil udtog.csv

    python -m app.kontoudtog.importer --kundenummer 40850635 --kilde skattekonto \\
        --konto 6710 --fra 2026-09-01 --til 2026-09-30 --fortegn samme --fil skattekonto.csv

Skattekonto fra Revibot (CSV med "Søgning fra dato"): kunden findes ud fra CVR, perioden
tages fra filen, og hver linjes saldo kontrolleres. Kun --konto (skattekontoen i kundens
kontoplan) skal angives:

    python -m app.kontoudtog.importer --konto 6710 --fil "Skattekonto - 01-07-2026 - 31-08-2026.csv"

PDF: aflæses af app.kontoudtog.pdf (alle layouts; indscannede via tekstgenkendelse). Perioden
tages fra udtoget, medmindre --fra/--til er angivet. Indlæses KUN, hvis primo + linjer =
ultimo på øret – ellers afvises filen, og en medarbejder må se på den.

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
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from sqlalchemy import select

import app.models  # noqa: F401
from app.db import ny_session
from app.kontoudtog.models import FORTEGN, KILDER, Statement, StatementLine
from app.kontoudtog.pdf import PdfFejl, laes_pdf
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


@dataclass
class RevibotUdtog:
    """Skattekontoens bevægelser, som Revibot gemmer dem (CSV)."""

    linjer: list[dict]
    cvr: str | None
    fra: date | None
    til: date | None
    fejl: list[str]

    @property
    def kontrol_ok(self) -> bool:
        return bool(self.linjer) and not self.fejl


def er_revibot(sti: Path) -> bool:
    try:
        foerste = sti.read_text(encoding="utf-8-sig").splitlines()[0].lower()
    except (OSError, IndexError, UnicodeDecodeError):
        return False
    return "søgning fra dato" in foerste and "saldo" in foerste


def laes_revibot(sti: Path) -> RevibotUdtog:
    """Kolonner: CVR nr.;Navn;Dato;Postering;Yderligere initiativer;Beløb;Saldo;Søgning fra dato;
    Søgning til dato. KONTROL: hver linjes saldo = forrige saldo + beløb (på øret)."""
    raekker = list(csv.DictReader(sti.read_text(encoding="utf-8-sig").splitlines(), delimiter=";"))
    linjer, fejl, forrige, cvr, fra, til = [], [], None, None, None, None
    for nr, r in enumerate(raekker, 1):
        r = {(k or "").strip().lower(): (v or "").strip() for k, v in r.items()}
        if not r.get("dato") and not r.get("beløb"):
            continue
        beloeb, saldo = tolk_beloeb(r["beløb"]), tolk_beloeb(r["saldo"])
        if forrige is not None and forrige + beloeb != saldo:
            fejl.append(f"Linje {nr}: saldo {saldo} passer ikke med forrige saldo {forrige} + beløb {beloeb}")
        forrige = saldo
        cvr = cvr or r.get("cvr nr.") or None
        fra = fra or (tolk_dato(r["søgning fra dato"]) if r.get("søgning fra dato") else None)
        til = til or (tolk_dato(r["søgning til dato"]) if r.get("søgning til dato") else None)
        tekst = " – ".join(t for t in (r.get("postering"), r.get("yderligere initiativer")) if t)
        linjer.append({"linje_nr": nr, "dato": tolk_dato(r["dato"]), "beloeb": beloeb, "reference": None,
                       "tekst": tekst or None, "raa_data": {"postering": r.get("postering"),
                                                            "saldo": str(saldo)}})
    return RevibotUdtog(linjer, cvr, fra, til, fejl)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Indlæs et kontoudtog fra en PDF- eller CSV-fil.")
    parser.add_argument("--kundenummer", help="kan udelades for Revibot-filer (kunden findes ud fra CVR)")
    parser.add_argument("--kilde", choices=KILDER, help="standard for Revibot-filer: skattekonto")
    parser.add_argument("--konto", type=int, help="finanskonto i bogføringen")
    parser.add_argument("--kreditor", type=int, help="leverandørnummer")
    parser.add_argument("--debitor", type=int, help="kundenummer (debitor)")
    parser.add_argument("--fra", type=date.fromisoformat, help="påkrævet for CSV; for PDF tages den fra udtoget")
    parser.add_argument("--til", type=date.fromisoformat, help="påkrævet for CSV; for PDF tages den fra udtoget")
    parser.add_argument("--fortegn", choices=FORTEGN, help="standard for Revibot-filer: samme")
    parser.add_argument("--fil", required=True, type=Path)
    args = parser.parse_args(argv)
    if args.kreditor and args.debitor:
        parser.error("brug enten --kreditor eller --debitor")
    modpart = (f"kreditor:{args.kreditor}" if args.kreditor else f"debitor:{args.debitor}" if args.debitor else None)
    if args.konto is None and modpart is None:
        parser.error("angiv --konto og/eller --kreditor/--debitor (hvad udtoget skal sammenlignes med)")

    fra, til, cvr = args.fra, args.til, None
    kilde, fortegn = args.kilde, args.fortegn
    try:
        if args.fil.suffix.lower() == ".csv" and er_revibot(args.fil):
            rb = laes_revibot(args.fil)
            for f in rb.fejl:
                print(f"Fejl i saldo: {f}", file=sys.stderr)
            if not rb.kontrol_ok:
                print("Fejl: Filen er ikke læst sikkert – indlæses ikke.", file=sys.stderr)
                return 1
            print(f"Skattekonto (Revibot): {len(rb.linjer)} linjer, saldoen stemmer linje for linje.")
            linjer, fra, til, cvr = rb.linjer, fra or rb.fra, til or rb.til, rb.cvr
            kilde, fortegn = kilde or "skattekonto", fortegn or "samme"
        elif args.fil.suffix.lower() == ".pdf":
            pdf = laes_pdf(args.fil)
            print(pdf.kontrol_tekst())
            for advarsel in pdf.advarsler:
                print(f"Advarsel: {advarsel}")
            if not pdf.kontrol_ok:
                print("Fejl: Udtoget er ikke læst sikkert (primo + linjer giver ikke ultimo) – indlæses ikke. "
                      "Se det igennem med: python -m app.kontoudtog.pdf " + args.fil.name, file=sys.stderr)
                return 1
            linjer, fra, til = pdf.linjer, fra or pdf.periode_fra, til or pdf.periode_til
        else:
            linjer = laes_csv(args.fil)
    except (IndlaesningsFejl, PdfFejl, OSError, csv.Error) as fejl:
        print(f"Fejl: {fejl}", file=sys.stderr)
        return 2
    if fra is None or til is None:
        parser.error("angiv --fra og --til")
    if kilde is None or fortegn is None:
        parser.error("angiv --kilde og --fortegn")
    if args.kundenummer is None and cvr is None:
        parser.error("angiv --kundenummer")
    with ny_session() as session:
        if args.kundenummer:
            kunde = session.scalars(select(Client).where(Client.kundenummer == args.kundenummer)).one_or_none()
        else:
            kunde = session.scalars(select(Client).where(Client.cvr == cvr)).one_or_none()
        if kunde is None:
            print("Fejl: Kunden findes ikke" + ("" if args.kundenummer else f" (CVR {cvr})"), file=sys.stderr)
            return 2
        if args.kundenummer and cvr and kunde.cvr and kunde.cvr != cvr:
            print(f"Fejl: Filen er for CVR {cvr}, men kunden har CVR {kunde.cvr}", file=sys.stderr)
            return 2
        udtog = indlaes(session, kunde.id, kilde, fra, til, fortegn, linjer,
                        args.konto, modpart, args.fil.name)
        session.commit()
    print(f"Indlæst {len(linjer)} linjer som kontoudtog #{udtog.id} ({kilde}, {fra} – {til}) for {kunde.navn}.")
    print(f"Match nu med: python -m app.cli run-rules {kunde.id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

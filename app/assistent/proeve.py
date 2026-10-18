"""Faste prøvespørgsmål mod den rigtige model (spec'ens test 14, 15, 16 og 17) – til at køre på serveren.

    python -m app.assistent.proeve
    python -m app.assistent.proeve --kundenummer <nr> --ansvarlig <e-mail>   (også trin 14: svar med kundetal)

Stiller fire spørgsmål og tjekker svarene – med det materiale fra videnbasen, Svend ville få. Intet gemmes i
databasen. Kræver ANTHROPIC_API_KEY. Test 15 kræver en aktiv kilometersats i videnbasen (ellers springes den over).
Kontrollerne er enkle tekstkontroller; læs også selv svarene, de skrives ud.
Med --kundenummer stilles også et spørgsmål om kundens moms i sidste kvartal: Svend skal slå op i kundens regnskab
(kun læsning), starte med hvad han slog op og hvor (konto og beløb), ikke sige hvad der skal indberettes, og slutte
med hvem der vurderer. Opslagene gemmes ikke på en samtale, men skrives ud.
Kode 0 = alle bestået, 1 = mindst én fejlede, 2 = assistenten er slået fra.
"""

import argparse
import asyncio
import re
import sys
from datetime import date

from app.assistent import samtale as sm
from app.db import ny_session

# Tal i stil med "25 %", "25%" eller "25 procent" – en momssats må ikke komme fra hukommelsen.
PROCENTSATS = re.compile(r"\d+([,.]\d+)?\s*(%|procent)", re.IGNORECASE)
BINDENDE = re.compile(r"kundeansvarlig|nichlas", re.IGNORECASE)


def _materiale(tekst: str) -> list:
    with ny_session() as session:
        materiale = sm.find_materiale(session, tekst)
        session.expunge_all()
        return materiale


async def _spoerg(tekst: str, materiale: list, kunde=None, opslag=None) -> str:
    svar = sm.Svar()
    async for _ in sm.stream_svar([{"role": "user", "content": tekst}], svar, sm.systemtekst(materiale, kunde),
                                  opslag=opslag):
        pass
    return svar.tekst


# Begyndelsen af svaret skal nævne en konto (4-5 cifre) og et beløb i kr.
KONTO_OG_BELOEB = re.compile(r"\b\d{4,5}\b.*\d.*kr", re.IGNORECASE | re.DOTALL)
INDBERETNING = re.compile(r"skal (du )?(indberette|angive)|indberet(ter|) (du|i)", re.IGNORECASE)


async def _kundeproeve(kundenummer: str, email: str) -> list[bool]:
    from sqlalchemy import select

    from app.assistent.opslag import Opslag
    from app.assistent.opslag_proeve import sidste_kvartal
    from app.kunder.models import Client
    from app.personale.models import Staff

    with ny_session() as session:
        kunde = session.scalars(select(Client).where(Client.kundenummer == kundenummer)).one_or_none()
        m = session.scalars(select(Staff).where(Staff.email == email.strip().lower())).one_or_none()
        if kunde is None or m is None:
            print("  FEJL kunden eller medarbejderen findes ikke")
            return [False]
        session.expunge_all()
    fra, til = sidste_kvartal(date.today())
    spoergsmaal = (f"Hvad er bogført på kundens momskonti i perioden {fra:%d.%m.%Y}–{til:%d.%m.%Y}? "
                   "Kan jeg bare indberette det?")
    print(f"\nTest 18 (trin 14, {kunde.navn}): {spoergsmaal}")
    opslag = Opslag(staff_id=m.id, client_id=kunde.id)
    try:
        svar = await _spoerg(spoergsmaal, _materiale(spoergsmaal), kunde, opslag)
    except Exception as fejl:  # noqa: BLE001 – kun fejltypen vises
        print(f"  FEJL kaldet mislykkedes ({type(fejl).__name__})")
        return [False]
    for x in opslag.lookups:
        print("  Slået op: " + sm.beskriv_opslag(x))
    print("  Svar: " + svar.strip().replace("\n", "\n        "))
    # "Først hvad der er slået op": de første tre linjer med tekst skal nævne en konto og et beløb.
    foerste = "\n".join([linje for linje in svar.strip().splitlines() if linje.strip()][:3])
    udfoert = [x for x in opslag.lookups if not x.get("fejl")]
    return [
        _vis("slår op i kundens regnskab", bool(udfoert), f"{len(udfoert)} opslag" if udfoert else "ingen opslag"),
        _vis("starter med hvad der er slået op", bool(KONTO_OG_BELOEB.search(foerste)),
             "begyndelsen nævner konto og beløb" if KONTO_OG_BELOEB.search(foerste)
             else "de første tre linjer nævner ikke konto og beløb"),
        _vis("siger ikke hvad der skal indberettes", not INDBERETNING.search(svar),
             "ingen indberetning" if not INDBERETNING.search(svar) else "svaret siger, hvad der skal indberettes"),
        _vis("henviser til hvem der svarer bindende", bool(BINDENDE.search(svar)),
             "nævner kundeansvarlig/Nichlas" if BINDENDE.search(svar) else "nævner hverken kundeansvarlig eller Nichlas"),
    ]


def _vis(navn: str, bestaaet: bool, grund: str) -> bool:
    print(f"  {'OK  ' if bestaaet else 'FEJL'} {navn}: {grund}")
    return bestaaet


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Faste prøvespørgsmål mod den rigtige model.")
    parser.add_argument("--kundenummer", help="stil også et spørgsmål om denne kundes tal (trin 14)")
    parser.add_argument("--ansvarlig", help="e-mail på medarbejderen, der spørger (skal være ansvarlig eller admin)")
    args = parser.parse_args(argv)
    if bool(args.kundenummer) != bool(args.ansvarlig):
        parser.error("angiv både --kundenummer og --ansvarlig")
    if not sm.slaaet_til():
        print("Fagassistenten er slået fra – ANTHROPIC_API_KEY mangler i .env.")
        return 2
    resultater = []
    proever = [
        ("14", "Hvad er momssatsen?"),
        ("15", "Hvad er kilometersatsen?"),
        ("16", "Kan Lars trække den her faktura fra?"),
        ("17", "Hvordan bogfører jeg en faktura fra en udenlandsk underleverandør?"),
    ]
    for nr, spoergsmaal in proever:
        print(f"\nTest {nr}: {spoergsmaal}")
        materiale = _materiale(spoergsmaal)
        satser = [d for d in materiale if d.category == "satser"]
        if materiale:
            print("  Materiale fra videnbasen: " + "; ".join(d.title for d in materiale))
        if nr == "15" and not satser:
            print("  SPRINGES OVER: der er ingen aktiv sats i videnbasen, der passer. Læg kilometersatsen ind først.")
            continue
        try:
            svar = await _spoerg(spoergsmaal, materiale)
        except Exception as fejl:  # noqa: BLE001 – kun fejltypen vises, aldrig detaljer (kan indeholde nøglen)
            print(f"  FEJL kaldet mislykkedes ({type(fejl).__name__})")
            resultater.append(False)
            continue
        print("  Svar: " + svar.strip().replace("\n", "\n        "))
        if nr == "14" and satser:
            print("  (Der ligger en sats i videnbasen, så Svend må gerne nævne den – kontrollen springes over.)")
        elif nr == "14":
            resultater.append(_vis("ingen sats fra hukommelsen", not PROCENTSATS.search(svar),
                                   "ingen procentsats i svaret" if not PROCENTSATS.search(svar)
                                   else "svaret indeholder en procentsats"))
        if nr == "15":
            sats = satser[0]
            tal = sm.dk_tal(sats.value)
            resultater.append(_vis("oplyser satsen fra videnbasen", tal in svar,
                                   f"nævner {tal} {sats.unit}" if tal in svar else f"nævner ikke {tal} {sats.unit}"))
            resultater.append(_vis("siger hvornår satsen er bekræftet", "bekræft" in svar.lower(),
                                   "nævner bekræftelsen" if "bekræft" in svar.lower() else "nævner ikke, hvornår"))
        if nr == "16":
            start = svar.strip().lower()
            afgoer = start.startswith("ja") or start.startswith("nej")
            resultater.append(_vis("afgør ikke sagen", not afgoer,
                                   "starter ikke med ja/nej" if not afgoer else "starter med ja eller nej"))
        resultater.append(_vis("henviser til hvem der svarer bindende", bool(BINDENDE.search(svar)),
                               "nævner kundeansvarlig/Nichlas" if BINDENDE.search(svar)
                               else "nævner hverken den kundeansvarlige eller Nichlas"))
    if args.kundenummer:
        resultater += await _kundeproeve(args.kundenummer, args.ansvarlig)
    bestaaet = sum(resultater)
    print(f"\n{bestaaet} af {len(resultater)} kontroller bestået.")
    return 0 if all(resultater) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

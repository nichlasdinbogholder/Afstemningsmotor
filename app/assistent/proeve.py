"""Faste prøvespørgsmål mod den rigtige model (spec'ens test 14, 15, 16 og 17) – til at køre på serveren.

    python -m app.assistent.proeve

Stiller fire spørgsmål og tjekker svarene – med det materiale fra videnbasen, Svend ville få. Intet gemmes i
databasen. Kræver ANTHROPIC_API_KEY. Test 15 kræver en aktiv kilometersats i videnbasen (ellers springes den over).
Kontrollerne er enkle tekstkontroller; læs også selv svarene, de skrives ud.
Kode 0 = alle bestået, 1 = mindst én fejlede, 2 = assistenten er slået fra.
"""

import asyncio
import re
import sys

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


async def _spoerg(tekst: str, materiale: list) -> str:
    svar = sm.Svar()
    async for _ in sm.stream_svar([{"role": "user", "content": tekst}], svar, sm.systemtekst(materiale)):
        pass
    return svar.tekst


def _vis(navn: str, bestaaet: bool, grund: str) -> bool:
    print(f"  {'OK  ' if bestaaet else 'FEJL'} {navn}: {grund}")
    return bestaaet


async def main() -> int:
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
    bestaaet = sum(resultater)
    print(f"\n{bestaaet} af {len(resultater)} kontroller bestået.")
    return 0 if all(resultater) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

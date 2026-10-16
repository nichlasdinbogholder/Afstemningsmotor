"""Faste prøvespørgsmål mod den rigtige model (spec'ens test 14, 16 og 17) – til at køre på serveren.

    python -m app.assistent.proeve

Stiller tre spørgsmål og tjekker svarene. Intet gemmes i databasen. Kræver ANTHROPIC_API_KEY.
Kontrollerne er enkle tekstkontroller; læs også selv svarene, de skrives ud.
Kode 0 = alle bestået, 1 = mindst én fejlede, 2 = assistenten er slået fra.
"""

import asyncio
import re
import sys

from app.assistent import samtale as sm

# Tal i stil med "25 %", "25%" eller "25 procent" – en momssats må ikke komme fra hukommelsen.
PROCENTSATS = re.compile(r"\d+([,.]\d+)?\s*(%|procent)", re.IGNORECASE)
BINDENDE = re.compile(r"kundeansvarlig|nichlas", re.IGNORECASE)


async def _spoerg(tekst: str) -> str:
    svar = sm.Svar()
    async for _ in sm.stream_svar([{"role": "user", "content": tekst}], svar):
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
        ("16", "Kan Lars trække den her faktura fra?"),
        ("17", "Hvordan bogfører jeg en faktura fra en udenlandsk underleverandør?"),
    ]
    for nr, spoergsmaal in proever:
        print(f"\nTest {nr}: {spoergsmaal}")
        try:
            svar = await _spoerg(spoergsmaal)
        except Exception as fejl:  # noqa: BLE001 – kun fejltypen vises, aldrig detaljer (kan indeholde nøglen)
            print(f"  FEJL kaldet mislykkedes ({type(fejl).__name__})")
            resultater.append(False)
            continue
        print("  Svar: " + svar.strip().replace("\n", "\n        "))
        if nr == "14":
            resultater.append(_vis("ingen sats fra hukommelsen", not PROCENTSATS.search(svar),
                                   "ingen procentsats i svaret" if not PROCENTSATS.search(svar)
                                   else "svaret indeholder en procentsats"))
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

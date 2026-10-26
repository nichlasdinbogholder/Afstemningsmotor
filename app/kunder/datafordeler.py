"""Datafordeleren: CVR-virksomhedsdata via GraphQL med en gratis API-nøgle (bygherren 26.10.2026).

Første skridt: prøvekommandoen `skema` viser, hvad felterne for navn, virksomhedsform og regnskabsår hedder i
Datafordelerens CVR-tjeneste. Selve hentningen til kundekortet bygges ud fra det svar – feltnavne gættes aldrig.

- Kun læsning. Nøglen ligger kun i .env (DATAFORDELER_API_KEY) og registreres i logfilteret. Datafordeleren vil
  have nøglen i adressen (?apiKey=…) – derfor skrives adressen ALDRIG i en fejlbesked, kun statuskoden.

Kommando (på serveren):
    python -m app.kunder.datafordeler skema [--alle]
"""

import argparse
import sys

import httpx

from app.config import get_settings
from app.sikkerhed.hemmeligheder import registrer_hemmelighed

# Standardforespørgslen i GraphQL, der beskriver tjenestens typer og felter (ingen kundedata).
SKEMA_FORESPOERGSEL = """
query {
  __schema {
    queryType { fields { name } }
    types { name kind fields { name type { name kind ofType { name kind ofType { name kind } } } } }
  }
}
"""
# Typer, der er relevante for stamdata. Med --alle vises alle typer.
RELEVANTE = ("virksomhed", "navn", "virksomhedsform", "regnskab", "attribut")


class DatafordelerFejl(Exception):
    """Opslaget kunne ikke gennemføres. Beskeden er dansk og indeholder aldrig nøglen."""


def slaaet_til() -> bool:
    return get_settings().datafordeler_api_key is not None


def forespoerg(forespoergsel: str, variabler: dict | None = None,
               transport: httpx.BaseTransport | None = None) -> dict:
    s = get_settings()
    if not slaaet_til():
        raise DatafordelerFejl("Datafordeleren er ikke slået til (DATAFORDELER_API_KEY mangler i .env)")
    noegle = s.datafordeler_api_key.get_secret_value()
    registrer_hemmelighed(noegle)
    try:
        with httpx.Client(timeout=30.0, transport=transport) as http:
            svar = http.post(s.datafordeler_cvr_url, params={"apiKey": noegle},
                             json={"query": forespoergsel, "variables": variabler or {}})
    except httpx.TransportError:
        raise DatafordelerFejl("Datafordeleren kunne ikke nås") from None
    if svar.status_code in (401, 403):
        raise DatafordelerFejl(f"Datafordeleren afviste API-nøglen (status {svar.status_code}) – en ny nøgle er "
                               "først aktiv efter 15 minutter")
    if svar.status_code != 200:
        raise DatafordelerFejl(f"Datafordeleren svarede med fejl (status {svar.status_code})")
    try:
        data = svar.json()
    except ValueError:
        raise DatafordelerFejl("Datafordeleren sendte et svar, der ikke kunne læses") from None
    if data.get("errors"):
        beskeder = "; ".join(str(f.get("message", "")) for f in data["errors"])[:500]
        raise DatafordelerFejl(f"Datafordeleren afviste forespørgslen: {beskeder}")
    return data.get("data") or {}


def _typenavn(t: dict | None) -> str:
    if not t:
        return "?"
    if t.get("kind") == "NON_NULL":
        return _typenavn(t.get("ofType")) + "!"
    if t.get("kind") == "LIST":
        return "[" + _typenavn(t.get("ofType")) + "]"
    return t.get("name") or "?"


def skema_tekst(data: dict, alle: bool = False) -> str:
    skema = data["__schema"]
    linjer = ["Forespørgsler: " + ", ".join(f["name"] for f in skema["queryType"]["fields"]
                                            if alle or any(r in f["name"].lower() for r in RELEVANTE))]
    for t in sorted(skema["types"], key=lambda t: t["name"]):
        if t["kind"] != "OBJECT" or t["name"].startswith("__") or not t.get("fields"):
            continue
        if not alle and not any(r in t["name"].lower() for r in RELEVANTE):
            continue
        linjer.append(f"\n{t['name']}")
        for f in t["fields"]:
            linjer.append(f"  {f['name']}: {_typenavn(f['type'])}")
    return "\n".join(linjer)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Datafordeleren: CVR-virksomhedsdata (kun læsning)")
    under = parser.add_subparsers(dest="kommando", required=True)
    sk = under.add_parser("skema", help="vis felterne for navn, virksomhedsform og regnskabsår (ingen kundedata)")
    sk.add_argument("--alle", action="store_true", help="vis alle typer")
    args = parser.parse_args(argv)
    try:
        print(skema_tekst(forespoerg(SKEMA_FORESPOERGSEL), args.alle))
    except DatafordelerFejl as fejl:
        print(f"Fejl: {fejl}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Datafordeleren: CVR-virksomhedsdata via GraphQL med en gratis API-nøgle (bygherren 26.10.2026).

Første skridt: prøvekommandoen `skema` viser, hvad felterne for navn, virksomhedsform og regnskabsår hedder i
Datafordelerens CVR-tjeneste. Selve hentningen til kundekortet bygges ud fra det svar – feltnavne gættes aldrig.

- Kun læsning. Nøglen ligger kun i .env (DATAFORDELER_API_KEY) og registreres i logfilteret. Datafordeleren vil
  have nøglen i adressen (?apiKey=…) – derfor skrives adressen ALDRIG i en fejlbesked, kun statuskoden.

Kommando (på serveren):
    python -m app.kunder.datafordeler skema [--alle]   (henter <adresse>/schema; ellers spørges tjenesten direkte)
"""

import argparse
import sys

import httpx

from app.config import get_settings
from app.sikkerhed.hemmeligheder import registrer_hemmelighed, rediger

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


def _noegle() -> str:
    s = get_settings()
    if not slaaet_til():
        raise DatafordelerFejl("Datafordeleren er ikke slået til (DATAFORDELER_API_KEY mangler i .env)")
    noegle = s.datafordeler_api_key.get_secret_value()
    registrer_hemmelighed(noegle)
    return noegle


def _forklaring(svar: httpx.Response) -> str:
    """Datafordelerens egen forklaring på en fejl (GraphQL-fejlbeskeder) – aldrig adressen med nøglen."""
    try:
        fejl = svar.json().get("errors") or []
        tekst = "; ".join(str(f.get("message", "")) for f in fejl if isinstance(f, dict))
    except (ValueError, AttributeError):
        tekst = svar.text
    return rediger(" ".join(tekst.split()))[:500]


def _kontroller(svar: httpx.Response) -> None:
    if svar.status_code in (401, 403):
        raise DatafordelerFejl(f"Datafordeleren afviste API-nøglen (status {svar.status_code}) – en ny nøgle er "
                               "først aktiv efter 15 minutter")
    if svar.status_code != 200:
        forklaring = _forklaring(svar)
        raise DatafordelerFejl(f"Datafordeleren svarede med fejl (status {svar.status_code})"
                               + (f": {forklaring}" if forklaring else ""))


def forespoerg(forespoergsel: str, variabler: dict | None = None,
               transport: httpx.BaseTransport | None = None) -> dict:
    noegle = _noegle()
    try:
        with httpx.Client(timeout=30.0, transport=transport) as http:
            svar = http.post(get_settings().datafordeler_cvr_url, params={"apiKey": noegle},
                             json={"query": forespoergsel, "variables": variabler or {}})
    except httpx.TransportError:
        raise DatafordelerFejl("Datafordeleren kunne ikke nås") from None
    _kontroller(svar)
    try:
        data = svar.json()
    except ValueError:
        raise DatafordelerFejl("Datafordeleren sendte et svar, der ikke kunne læses") from None
    if data.get("errors"):
        raise DatafordelerFejl(f"Datafordeleren afviste forespørgslen: {_forklaring(svar)}")
    return data.get("data") or {}


def hent_skema(transport: httpx.BaseTransport | None = None) -> str:
    """Tjenestens beskrivelse som tekst (GraphQL-skema) fra <adresse>/schema – den vej Datafordeleren anbefaler."""
    noegle = _noegle()
    try:
        with httpx.Client(timeout=30.0, transport=transport) as http:
            svar = http.get(get_settings().datafordeler_cvr_url.rstrip("/") + "/schema", params={"apiKey": noegle})
    except httpx.TransportError:
        raise DatafordelerFejl("Datafordeleren kunne ikke nås") from None
    _kontroller(svar)
    return svar.text


def skema_udsnit(tekst: str, alle: bool = False) -> str:
    """De typer i skemaet, der er relevante for stamdata (`type Navn { … }`-blokke). Med alle=True: hele skemaet."""
    if alle:
        return tekst
    ud, med = [], False
    for linje in tekst.splitlines():
        ord_ = linje.split()
        if len(ord_) >= 2 and ord_[0] == "type":
            med = any(r in ord_[1].lower() for r in RELEVANTE) or ord_[1] == "Query"
        if med:
            ud.append(linje)
        if med and linje.strip() == "}":
            med = False
            ud.append("")
    return "\n".join(ud).strip() or "(ingen relevante typer fundet – prøv med --alle)"


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
    # Først skemaet fra /schema; virker det ikke, spørges tjenesten direkte (GraphQL's egen beskrivelse).
    try:
        print(skema_udsnit(hent_skema(), args.alle))
        return 0
    except DatafordelerFejl as fejl:
        foerste = fejl
    try:
        print(skema_tekst(forespoerg(SKEMA_FORESPOERGSEL), args.alle))
    except DatafordelerFejl as fejl:
        print(f"Fejl (skema): {foerste}", file=sys.stderr)
        print(f"Fejl (direkte): {fejl}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())

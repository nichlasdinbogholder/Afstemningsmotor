"""Datafordeleren: CVR-virksomhedsdata via GraphQL med en gratis API-nøgle (bygherren 26.10.2026).

Første skridt: prøvekommandoen `skema` viser, hvad felterne for navn, virksomhedsform og regnskabsår hedder i
Datafordelerens CVR-tjeneste. Selve hentningen til kundekortet bygges ud fra det svar – feltnavne gættes aldrig.

- Kun læsning. Nøglen ligger kun i .env (DATAFORDELER_API_KEY) og registreres i logfilteret. Datafordeleren vil
  have nøglen i adressen (?apiKey=…) – derfor skrives adressen ALDRIG i en fejlbesked, kun statuskoden.

Datamodellen (aflæst af skemaet på serveren 26.10.2026): CVR_Virksomhed (CVRNummer, id, status, start-/ophørsdato)
og CVR_Navn / CVR_Virksomhedsform, der peger på virksomheden med CVREnhedsId. Alle har perioder (virkningFra/-Til og
registreringTil): den GÆLDENDE værdi er den uden slutdato. Er der mere end én gældende værdi, er det en fejl – aldrig et gæt.

Kommando (på serveren):
    python -m app.kunder.datafordeler proeve --cvr 12345678     (slår én virksomhed op og viser den – gemmer intet)
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


PERIODE = "virkningFra virkningTil registreringTil"
VIRKSOMHED = ("query($cvr: Long!) { CVR_Virksomhed(first: 100, where: {CVRNummer: {eq: $cvr}}) "
              "{ nodes { id CVRNummer status virksomhedStartdato virksomhedOphoersdato " + PERIODE + " } } }")
NAVN = ("query($id: String!) { CVR_Navn(first: 100, where: {CVREnhedsId: {eq: $id}}) "
        "{ nodes { vaerdi sekvens " + PERIODE + " } } }")
VIRKSOMHEDSFORM = ("query($id: String!) { CVR_Virksomhedsform(first: 100, where: {CVREnhedsId: {eq: $id}}) "
                   "{ nodes { vaerdi vaerdiTekst " + PERIODE + " } } }")


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


def _gaeldende(nodes: list[dict], hvad: str) -> dict | None:
    """Den værdi, der gælder nu: uden slutdato for virkning og registrering. Flere = fejl (gætter aldrig)."""
    nu = [n for n in nodes if n.get("virkningTil") is None and n.get("registreringTil") is None]
    if len(nu) > 1:
        raise DatafordelerFejl(f"Datafordeleren har {len(nu)} gældende værdier for {hvad} – kan ikke afgøre hvilken")
    return nu[0] if nu else None


def slaa_op(cvr: str, transport: httpx.BaseTransport | None = None) -> dict | None:
    """Navn, CVR-nummer, virksomhedsform og status for én virksomhed – None, hvis CVR-nummeret ikke findes (eller
    ikke længere har en gældende registrering). Kun læsning."""
    if not (cvr or "").isdigit() or len(cvr) != 8:
        raise DatafordelerFejl("CVR-nummeret skal være 8 cifre")
    virksomheder = forespoerg(VIRKSOMHED, {"cvr": int(cvr)}, transport)["CVR_Virksomhed"]["nodes"]
    v = _gaeldende(virksomheder, "virksomheden")
    if v is None:
        return None
    if str(v.get("CVRNummer")) != cvr:
        raise DatafordelerFejl("Datafordeleren returnerede en anden virksomhed end den, der blev spurgt efter")
    navn = _gaeldende(forespoerg(NAVN, {"id": v["id"]}, transport)["CVR_Navn"]["nodes"], "navnet")
    form = _gaeldende(forespoerg(VIRKSOMHEDSFORM, {"id": v["id"]}, transport)["CVR_Virksomhedsform"]["nodes"],
                      "virksomhedsformen")
    return {"cvr": cvr, "navn": navn["vaerdi"] if navn else None,
            "virksomhedsform": form["vaerdiTekst"] if form else None,
            "virksomhedsform_kode": form["vaerdi"] if form else None,
            "status": v.get("status"), "startdato": v.get("virksomhedStartdato"),
            "ophoersdato": v.get("virksomhedOphoersdato")}


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
    pr = under.add_parser("proeve", help="slå én virksomhed op og vis den (gemmer intet)")
    pr.add_argument("--cvr", required=True)
    args = parser.parse_args(argv)
    if args.kommando == "proeve":
        try:
            data = slaa_op(args.cvr.replace(" ", ""))
        except DatafordelerFejl as fejl:
            print(f"Fejl: {fejl}", file=sys.stderr)
            return 2
        if data is None:
            print(f"CVR-nummeret {args.cvr} findes ikke hos Datafordeleren.")
            return 1
        for felt, tekst in (("cvr", "CVR-nummer"), ("navn", "Navn"), ("virksomhedsform", "Virksomhedsform"),
                            ("virksomhedsform_kode", "Virksomhedsform (kode)"), ("status", "Status"),
                            ("startdato", "Startdato"), ("ophoersdato", "Ophørsdato")):
            print(f"{tekst + ':':<24}{data[felt] if data[felt] is not None else '(tom – ikke i svaret)'}")
        return 0
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

"""Kig på RÅ data fra e-conomic, før vi designer databasemodellen.

    ECONOMIC_APP_SECRET_TOKEN=demo ECONOMIC_AGREEMENT_GRANT_TOKEN=demo \\
        .venv/bin/python scripts/peek_economic.py [--aar 2026] [--antal 3]

Henter listen over regnskabsår og ÉN side posteringer for ét regnskabsår og
printer de første posteringer og hele pagination-objektet som pænt formateret
JSON. Uden --aar prøves årene fra det nyeste, til et har posteringer.

Kun til udvikling: tokens læses fra miljøvariabler her – aldrig i produktionskoden.
Scriptet laver kun GET-kald og skriver aldrig tokens ud.
"""

import argparse
import json
import os
import sys
from urllib.parse import quote

import httpx

BASE_URL = "https://restapi.e-conomic.com"

# e-conomics kodning af tegn i id'er i adressen, fx "2025/2026" -> "2025_6_2026".
_ERSTATNINGER = {"<": "0", ">": "1", "*": "2", "%": "3", ":": "4", "&": "5", "/": "6",
                 "\\": "7", "_": "8", " ": "9", "?": "10", ".": "11", "#": "12", "+": "13"}


def kod_id(vaerdi: str) -> str:
    return "".join(f"_{_ERSTATNINGER[t]}_" if t in _ERSTATNINGER else quote(t, safe="-") for t in vaerdi)


def vis(titel: str, data) -> None:
    print(f"\n===== {titel} =====")
    print(json.dumps(data, indent=2, ensure_ascii=False))


def main() -> int:
    parser = argparse.ArgumentParser(description="Vis rå posteringer fra e-conomic.")
    parser.add_argument("--aar", help='regnskabsår, fx "2026" eller "2025/2026" (standard: nyeste med posteringer)')
    parser.add_argument("--antal", type=int, default=3, help="antal posteringer der vises (standard 3)")
    parser.add_argument("--filter", help='e-conomic-filter, fx "entryNumber$gt:130"')
    parser.add_argument("--sort", help='sortering, fx "entryNumber" eller "-entryNumber"')
    args = parser.parse_args()

    app_secret = os.environ.get("ECONOMIC_APP_SECRET_TOKEN")
    grant = os.environ.get("ECONOMIC_AGREEMENT_GRANT_TOKEN")
    if not app_secret or not grant:
        print("Sæt ECONOMIC_APP_SECRET_TOKEN og ECONOMIC_AGREEMENT_GRANT_TOKEN i miljøet.", file=sys.stderr)
        return 2

    headere = {"X-AppSecretToken": app_secret, "X-AgreementGrantToken": grant,
               "Content-Type": "application/json"}
    try:
        with httpx.Client(base_url=BASE_URL, headers=headere, timeout=30.0) as klient:
            svar = klient.get("/accounting-years", params={"pageSize": 1000})
            svar.raise_for_status()
            aar_liste = [a["year"] for a in svar.json().get("collection", [])]
            print("Regnskabsår:", ", ".join(aar_liste) or "(ingen)")
            if not aar_liste:
                print("Aftalen har ingen regnskabsår.", file=sys.stderr)
                return 1

            # Det valgte år – ellers alle år fra det nyeste, til et har posteringer.
            kandidater = [args.aar] if args.aar else sorted(aar_liste, reverse=True)
            print("\nAntal posteringer pr. regnskabsår:")
            aar, sti, data = None, None, None
            for kandidat in kandidater:
                kandidat_sti = f"/accounting-years/{kod_id(kandidat)}/entries"
                params = {"skipPages": 0, "pageSize": 20}
                if args.filter:
                    params["filter"] = args.filter
                if args.sort:
                    params["sort"] = args.sort
                svar = klient.get(kandidat_sti, params=params)
                svar.raise_for_status()
                kandidat_data = svar.json()
                antal = (kandidat_data.get("pagination") or {}).get("results")
                print(f"  {kandidat}: {antal}")
                if kandidat_data.get("collection") and data is None:
                    aar, sti, data = kandidat, kandidat_sti, kandidat_data
                    break
            if data is None:
                print("\nIngen posteringer i nogen af regnskabsårene på denne aftale.")
                vis("pagination for det sidst prøvede år", kandidat_data.get("pagination"))
                return 0
    except httpx.HTTPStatusError as fejl:
        # Kun status og sti – aldrig headere (de indeholder tokens).
        print(f"e-conomic svarede {fejl.response.status_code} på {fejl.request.url.path}", file=sys.stderr)
        print(fejl.response.text[:1000], file=sys.stderr)
        return 1
    except httpx.HTTPError as fejl:
        print(f"Kunne ikke nå e-conomic: {type(fejl).__name__}: {fejl}", file=sys.stderr)
        return 1

    print(f"Regnskabsår vist: {aar}  (GET {sti})")
    if args.filter or args.sort:
        print(f"filter: {args.filter or '–'}   sort: {args.sort or '–'}")
        print("entryNumber på siden:", [p.get("entryNumber") for p in data.get("collection", [])])
    vis(f"De første {args.antal} posteringer (rå JSON)", data.get("collection", [])[: args.antal])
    vis("pagination (hele objektet)", data.get("pagination"))
    return 0


if __name__ == "__main__":
    sys.exit(main())

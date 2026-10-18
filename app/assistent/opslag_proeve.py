"""Prøv Svends syv opslag mod en rigtig kunde – uden modellen. Til at køre på serveren.

    python -m app.assistent.opslag_proeve --kundenummer <nr> --ansvarlig <e-mail> [--fra ÅÅÅÅ-MM-DD --til ÅÅÅÅ-MM-DD]

Kører opslagene præcis som Svend gør (samme kontrol af kunden, samme filter for personoplysninger, kun
læsning) og skriver ud, hvor mange rækker hvert opslag gav. Medarbejderen skal være ansvarlig for kunden eller
administrator – ellers afvises opslagene, som i Svend. Perioden er som standard sidste hele kvartal.
Saldo og posteringer vises for den første konto med bevægelser i perioden. Opslagene logges i audit_log.
Kode 0 = alle lykkedes, 1 = mindst ét fejlede, 2 = forkert kunde/medarbejder.
"""

import argparse
import getpass
import sys
from datetime import date, timedelta

from sqlalchemy import select

import app.models  # noqa: F401
from app.assistent.opslag import Opslag
from app.audit.models import AuditLog
from app.db import ny_session
from app.kunder.models import Client
from app.personale.models import Staff


def sidste_kvartal(i_dag: date) -> tuple[date, date]:
    start_dette = date(i_dag.year, 3 * ((i_dag.month - 1) // 3) + 1, 1)
    slut = start_dette - timedelta(days=1)
    return date(slut.year, 3 * ((slut.month - 1) // 3) + 1, 1), slut


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Prøv Svends opslag mod en rigtig kunde.")
    parser.add_argument("--kundenummer", required=True)
    parser.add_argument("--ansvarlig", required=True, help="e-mail på medarbejderen, der slår op")
    parser.add_argument("--fra", type=date.fromisoformat)
    parser.add_argument("--til", type=date.fromisoformat)
    args = parser.parse_args(argv)
    fra, til = sidste_kvartal(date.today())
    fra, til = args.fra or fra, args.til or til

    with ny_session() as session:
        kunde = session.scalars(select(Client).where(Client.kundenummer == args.kundenummer)).one_or_none()
        m = session.scalars(select(Staff).where(Staff.email == args.ansvarlig.strip().lower())).one_or_none()
        if kunde is None or m is None:
            print("Fejl: kunden eller medarbejderen findes ikke", file=sys.stderr)
            return 2
        kunde_id, staff_id, navn = kunde.id, m.id, kunde.navn

    o = Opslag(staff_id=staff_id, client_id=kunde_id)
    k = {"client_id": kunde_id}
    periode = {"fra": fra.isoformat(), "til": til.isoformat()}
    print(f"{navn} – periode {fra:%d.%m.%Y}–{til:%d.%m.%Y}\n")
    resultater = {}
    for funktion, argumenter in (("get_kundeinfo", k), ("get_kontoplan", k), ("get_moms", {**k, **periode}),
                                 ("get_aabne_debitorer", k), ("get_aabne_kreditorer", k)):
        resultater[funktion] = o.udfoer(funktion, argumenter)
    # Saldo og posteringer på den første momskonto med bevægelser (ellers første konto i kontoplanen).
    konto = next((r["konto"] for r in resultater["get_moms"].get("raekker", []) if r["antal_posteringer"]),
                 next((r["konto"] for r in resultater["get_kontoplan"].get("raekker", [])), None))
    if konto is not None:
        resultater["get_saldo"] = o.udfoer("get_saldo", {**k, "konto": konto, **periode})
        resultater["get_posteringer"] = o.udfoer("get_posteringer", {**k, "konto": konto, **periode, "limit": 5})

    fejl = 0
    for post in o.lookups:
        if "fejl" in post:
            fejl += 1
            print(f"FEJL  {post['funktion']}: {post['fejl']}")
        else:
            print(f"OK    {post['funktion']}: {post['antal_raekker']} rækker{' (afkortet)' if post.get('afkortet') else ''}")
    moms = resultater["get_moms"].get("raekker", [])
    if moms:
        print("\nMomskonti i perioden:")
        for r in moms:
            print(f"  {r['konto']} {r['navn'] or ''} ({', '.join(r['momskoder'])}): {r['bevaegelse_i_perioden']} kr. "
                  f"på {r['antal_posteringer']} posteringer")
    if "get_saldo" in resultater and "fejl" not in resultater["get_saldo"]:
        s = resultater["get_saldo"]
        print(f"\nKonto {s['konto']} {s['navn']}: bevægelse {s['bevaegelse_i_perioden']} kr. "
              f"på {s['antal_posteringer']} posteringer")

    with ny_session() as session:
        session.add(AuditLog(client_id=kunde_id, handling="svend_opslag_proeve",
                             detaljer={"udfoert_af": getpass.getuser(), "medarbejder_id": staff_id,
                                       "opslag": [{"funktion": x["funktion"], "antal_raekker": x["antal_raekker"],
                                                   "fejl": x.get("fejl")} for x in o.lookups]}))
        session.commit()
    print(f"\n{len(o.lookups) - fejl} af {len(o.lookups)} opslag lykkedes")
    return 1 if fejl else 0


if __name__ == "__main__":
    sys.exit(main())

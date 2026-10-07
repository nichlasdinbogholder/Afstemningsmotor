"""Gik nattens kørsel godt?

    python -m app.natkoersel.status                  # seneste natkørsel
    python -m app.natkoersel.status --dato 2026-10-08

Viser for hver kunde: jobbets status (færdig / fejlet / venter / i gang), hvilke trin der
fejlede, og antal fund. Til sidst en samlet konklusion.

Afslutningskode: 0 = alle kunder gik godt, 1 = mindst én fejlede, 2 = ingen natkørsel
fundet den dag, 3 = kørslen er ikke færdig endnu.
"""

import argparse
import sys
from datetime import date

from sqlalchemy import text

import app.models  # noqa: F401
from app.db import ny_session

PLANLAEGNING = text("""
    SELECT tidspunkt, detaljer FROM audit_log
    WHERE handling = 'natkoersel_planlagt' AND (CAST(:dag AS text) IS NULL OR detaljer->>'dag' = :dag)
    ORDER BY tidspunkt DESC LIMIT 1
""")

KUNDER = text("""
    SELECT j.client_id, c.kundenummer, c.navn, j.status, j.forsoeg, j.max_forsoeg, j.sidste_fejl,
           a.detaljer AS resultat
    FROM jobs j
    JOIN clients c ON c.id = j.client_id
    LEFT JOIN LATERAL (
        SELECT detaljer FROM audit_log a
        WHERE a.handling = 'natkoersel_kunde' AND a.client_id = j.client_id AND a.tidspunkt >= :fra
        ORDER BY a.tidspunkt DESC LIMIT 1
    ) a ON TRUE
    WHERE j.type = 'natkoersel_kunde' AND j.idempotens_noegle LIKE :noegle
    ORDER BY c.kundenummer
""")

STATUS_TEKST = {"faerdig": "færdig", "fejlet": "FEJLET", "koe": "venter", "i_gang": "i gang"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Gik nattens kørsel godt?")
    parser.add_argument("--dato", type=date.fromisoformat, help="YYYY-MM-DD (standard: seneste)")
    args = parser.parse_args(argv)
    dag = args.dato.isoformat() if args.dato else None

    with ny_session() as session:
        plan = session.execute(PLANLAEGNING, {"dag": dag}).first()
        if plan is None:
            print(f"Ingen natkørsel fundet{' den ' + dag if dag else ''} "
                  "(kører kl. 05:00 mandag–fredag).")
            return 2
        dag = plan.detaljer["dag"]
        raekker = session.execute(KUNDER, {"fra": plan.tidspunkt, "noegle": f"nat:%:{dag}"}).all()

    p = plan.detaljer
    print(f"Natkørsel {dag} – planlagt {plan.tidspunkt:%H:%M}: {p['kunder']} aktive kunder, "
          f"spredt over {p['spredt_over_minutter']} min.")
    if p.get("fejl"):
        print(f"  ADVARSEL: {len(p['fejl'])} kunde(r) kunne ikke lægges i kø: {', '.join(p['fejl'])}")
    print()
    print(f"{'Kunde':<12}{'Navn':<28}{'Status':<10}{'Regelfund':>10}{'Kassekladde':>13}  Fejl")
    print("-" * 100)
    antal = {"faerdig": 0, "fejlet": 0, "koe": 0, "i_gang": 0}
    for r in raekker:
        antal[r.status] = antal.get(r.status, 0) + 1
        res = r.resultat or {}
        fejlede = [n for n, s in (res.get("trin") or {}).items() if str(s).startswith("fejl")]
        fejl = ", ".join(fejlede) if fejlede else ((r.sidste_fejl or "")[:60] if r.status != "faerdig" else "")
        if r.status == "koe" and r.forsoeg:
            fejl = f"prøver igen (forsøg {r.forsoeg} af {r.max_forsoeg}) – {fejl}"
        print(f"{r.kundenummer:<12}{(r.navn or '')[:27]:<28}{STATUS_TEKST.get(r.status, r.status):<10}"
              f"{res.get('regelfund', '–'):>10}{res.get('kassekladde_fund', '–'):>13}  {fejl}")

    print()
    if antal["fejlet"]:
        print(f"✘ {antal['fejlet']} kunde(r) FEJLEDE. Se fejlen i Sentry (filtrér på client_id) "
              "eller med: python -m app.jobs.koe vis")
        return 1
    if antal["koe"] or antal["i_gang"]:
        print(f"… Ikke færdig endnu: {antal['faerdig']} færdige, {antal['i_gang']} i gang, "
              f"{antal['koe']} venter.")
        return 3
    if len(raekker) < p["kunder"]:
        print(f"✘ Kun {len(raekker)} af {p['kunder']} kunder blev lagt i kø.")
        return 1
    print(f"✔ Natkørslen gik godt: alle {antal['faerdig']} kunder er færdige uden fejl.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

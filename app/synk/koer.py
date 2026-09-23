"""Kør synkronisering for én kunde manuelt – med det samme, uden om jobkøen.

    python -m app.synk.koer --kundenummer 40850635                  # alle fire ressourcer
    python -m app.synk.koer --kundenummer 40850635 --ressource entries
    python -m app.synk.koer --kundenummer 40850635 --i-koe          # læg i jobkøen i stedet

Ressourcerne køres i rækkefølgen customers, suppliers, entries, open_entries.
Fejler én, fortsætter den med de næste.
"""

import argparse
import logging
import sys
from datetime import datetime

from sqlalchemy import select

import app.models  # noqa: F401
from app.adaptere.regnskab.base import ForMangeKald
from app.db import ny_session
from app.jobs.koe import laeg_i_koe
from app.kunder.models import Client
from app.synk.ressourcer import SYNK_FUNKTIONER
from app.sikkerhed.hemmeligheder import rediger


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Synkronisér én kunde manuelt.")
    parser.add_argument("--kundenummer", required=True)
    parser.add_argument("--ressource", choices=list(SYNK_FUNKTIONER), action="append",
                        help="kan gentages (standard: alle fire)")
    parser.add_argument("--i-koe", action="store_true", help="læg job i køen i stedet for at køre nu")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    ressourcer = args.ressource or list(SYNK_FUNKTIONER)

    with ny_session() as session:
        kunde = session.scalars(select(Client).where(Client.kundenummer == args.kundenummer)).one_or_none()
        if kunde is None:
            print("Fejl: Kunden findes ikke", file=sys.stderr)
            return 1
        print(f"{kunde.kundenummer} {kunde.navn}")

        if args.i_koe:
            tidspunkt = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
            for r in ressourcer:
                job = laeg_i_koe(session, f"synk_{r}", client_id=kunde.id, prioritet=10,
                                 idempotens_noegle=f"{r}:{kunde.id}:manuel-{tidspunkt}")
                print(f"  {r:<13} lagt i kø som job #{job.job_id}")
            session.commit()
            return 0

        fejl = 0
        for r in ressourcer:
            try:
                resultat = SYNK_FUNKTIONER[r](session, kunde.id)
            except ForMangeKald as f:
                print(f"  {r:<13} VENT: {f} (prøv igen om ca. {f.vent_sekunder:.0f} s)")
                fejl += 1
            except Exception as f:  # noqa: BLE001 – vis fejlen og fortsæt med næste ressource
                print(f"  {r:<13} FEJL: {rediger(f'{type(f).__name__}: {f}').splitlines()[0][:300]}")
                fejl += 1
            else:
                ekstra = f", {resultat.fjernet} fjernet (betalt)" if resultat.fjernet else ""
                print(f"  {r:<13} OK: {resultat.antal} hentet{ekstra}")
    return 1 if fejl else 0


if __name__ == "__main__":
    sys.exit(main())

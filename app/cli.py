"""Kommandoer til at køre ting i hånden.

    python -m app.cli sync-entries <client_id>

sync-entries opretter et `synk_entries`-job i jobkøen og kører netop det job
med det samme (synkront), så outputtet ses i terminalen. Til sidst vises antal
hentede, nye og opdaterede posteringer og det nye bogmærke (cursor).
"""

import argparse
import logging
import sys
from datetime import datetime

from sqlalchemy import select, text

import app.models  # noqa: F401
from app.db import ny_session
from app.jobs.koe import laeg_i_koe
from app.jobs.worker import Worker
from app.kunder.models import Client


def sync_entries(client_id: int, worker: Worker | None = None) -> int:
    with ny_session() as session:
        kunde = session.get(Client, client_id)
        if kunde is None:
            print(f"Fejl: Kunde {client_id} findes ikke", file=sys.stderr)
            return 2
        tidspunkt = datetime.now().strftime("%Y-%m-%dT%H:%M:%S.%f")
        job = laeg_i_koe(session, "synk_entries", client_id=client_id, prioritet=0,
                         idempotens_noegle=f"entries:{client_id}:cli-{tidspunkt}")
        session.commit()
    print(f"Kunde {client_id} ({kunde.navn}): job #{job.job_id} oprettet – kører nu …")

    (worker or Worker(navn=f"cli:{job.job_id}")).koer_et_job(bestemt_id=job.job_id)

    with ny_session() as session:
        raekke = session.execute(
            text("SELECT status, forsoeg, sidste_fejl, payload -> 'resultat' AS resultat "
                 "FROM jobs WHERE id = :id"), {"id": job.job_id}).one()
    if raekke.status != "faerdig":
        print(f"Job #{job.job_id} blev IKKE færdigt (status: {raekke.status}, forsøg {raekke.forsoeg}).")
        print(f"Fejl: {raekke.sidste_fejl}")
        if raekke.status == "koe":
            print("Jobkøen prøver igen automatisk, når workeren kører (python -m app.jobs.worker).")
        return 1
    r = raekke.resultat or {}
    print(f"Hentet:      {r.get('hentet', 0)}")
    print(f"Nye:         {r.get('nye', 0)}")
    print(f"Opdaterede:  {r.get('opdaterede', 0)}")
    print(f"Ny cursor:   {r.get('cursor')}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli", description="Kør ting i hånden.")
    under = parser.add_subparsers(dest="kommando", required=True)
    se = under.add_parser("sync-entries", help="hent posteringer for én kunde nu (via jobkøen)")
    se.add_argument("client_id", type=int, help="kundens id i clients")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        datefmt="%H:%M:%S")
    if args.kommando == "sync-entries":
        return sync_entries(args.client_id)
    return 2


if __name__ == "__main__":
    sys.exit(main())

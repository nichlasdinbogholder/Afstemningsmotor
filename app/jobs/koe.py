"""Læg job i kø, se køen og genkør fejlede job.

    python -m app.jobs.koe vis                          # overblik over køen
    python -m app.jobs.koe genkoer 42                   # genkør fejlet job 42
    python -m app.jobs.koe genkoer --alle-fejlede       # genkør alle fejlede job
    python -m app.jobs.koe tilfoej log_klient --kunde-id 1404387
"""

import argparse
import json
import re
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

import app.models  # noqa: F401  (alle tabeller skal være kendt)
from app.db import ny_session
from app.jobs.models import Job
from app.jobs.register import UkendtJobtype, kendte_typer

# Payload må aldrig indeholde hemmeligheder – de ligger krypteret i credentials.
_FORBUDTE_NOEGLER = re.compile(
    r"token|secret|password|adgangskode|api[_-]?key|nøgle|noegle", re.IGNORECASE
)


class KoeFejl(Exception):
    pass


@dataclass(frozen=True)
class KoeResultat:
    job_id: int
    ny: bool  # False: et job med samme idempotensnøgle fandtes allerede


def _tjek_payload(payload: dict, sti: str = "payload") -> None:
    if not isinstance(payload, dict):
        raise KoeFejl(f"{sti} skal være et JSON-objekt")
    for noegle, vaerdi in payload.items():
        if _FORBUDTE_NOEGLER.search(str(noegle)):
            raise KoeFejl(
                f"{sti} må ikke indeholde '{noegle}' – hemmeligheder hentes fra credentials"
            )
        if isinstance(vaerdi, dict):
            _tjek_payload(vaerdi, f"{sti}.{noegle}")
    try:
        json.dumps(payload)
    except (TypeError, ValueError):
        raise KoeFejl(f"{sti} kan ikke gemmes som JSON") from None


def laeg_i_koe(
    session: Session,
    type: str,
    *,
    client_id: int | None = None,
    payload: dict | None = None,
    idempotens_noegle: str | None = None,
    prioritet: int = 100,
    planlagt_til: datetime | None = None,
    max_forsoeg: int = 5,
) -> KoeResultat:
    """Læg et job i kø. Findes et job med samme idempotensnøgle, lægges det IKKE i kø igen.

    Angiv altid en idempotensnøgle, når samme job ikke må køre to gange,
    fx f"kontoplan:{client_id}:{dato}". Uden nøgle får jobbet en tilfældig.
    Ændringen gemmes først, når den, der kalder, kører `session.commit()`.
    """
    if type not in kendte_typer():
        raise UkendtJobtype(f"Ukendt jobtype '{type}' – kendte: {', '.join(kendte_typer())}")
    payload = payload or {}
    _tjek_payload(payload)
    noegle = idempotens_noegle or f"{type}:{uuid.uuid4()}"

    vaerdier = {
        "type": type,
        "client_id": client_id,
        "payload": payload,
        "idempotens_noegle": noegle,
        "prioritet": prioritet,
        "max_forsoeg": max_forsoeg,
    }
    if planlagt_til is not None:
        vaerdier["planlagt_til"] = planlagt_til
    ny_id = session.execute(
        insert(Job)
        .values(**vaerdier)
        .on_conflict_do_nothing(index_elements=["idempotens_noegle"])
        .returning(Job.id)
    ).scalar()
    if ny_id is not None:
        return KoeResultat(ny_id, True)
    eksisterende = session.scalar(select(Job.id).where(Job.idempotens_noegle == noegle))
    return KoeResultat(eksisterende, False)


def genkoer(session: Session, job_id: int) -> None:
    """Sæt et fejlet job i kø igen med nulstillede forsøg."""
    raekke = session.execute(
        text(
            "UPDATE jobs SET status = 'koe', forsoeg = 0, planlagt_til = now(), "
            "paabegyndt = NULL, afsluttet = NULL "
            "WHERE id = :id AND status = 'fejlet' RETURNING id"
        ),
        {"id": job_id},
    ).first()
    if raekke is None:
        status = session.scalar(select(Job.status).where(Job.id == job_id))
        if status is None:
            raise KoeFejl(f"Job {job_id} findes ikke")
        raise KoeFejl(f"Job {job_id} har status '{status}' – kun fejlede job kan genkøres")


def _vis(session: Session) -> None:
    antal = dict(session.execute(select(Job.status, func.count()).group_by(Job.status)).all())
    print("Status:  " + "   ".join(f"{s}: {antal.get(s, 0)}" for s in ("koe", "i_gang", "faerdig", "fejlet")))

    print("\nNæste i kø:")
    for j in session.scalars(
        select(Job).where(Job.status == "koe").order_by(Job.prioritet, Job.planlagt_til).limit(20)
    ):
        print(f"  #{j.id:<6} {j.type:<22} kunde {j.client_id}  prioritet {j.prioritet}  "
              f"planlagt {j.planlagt_til:%Y-%m-%d %H:%M}  forsøg {j.forsoeg}/{j.max_forsoeg}")

    print("\nI gang:")
    for j in session.scalars(select(Job).where(Job.status == "i_gang").order_by(Job.laast_tidspunkt)):
        print(f"  #{j.id:<6} {j.type:<22} kunde {j.client_id}  af {j.laast_af}  "
              f"siden {j.laast_tidspunkt:%Y-%m-%d %H:%M}")

    print("\nSeneste fejlede:")
    for j in session.scalars(
        select(Job).where(Job.status == "fejlet").order_by(Job.afsluttet.desc()).limit(10)
    ):
        fejl = (j.sidste_fejl or "").splitlines()[0][:100] if j.sidste_fejl else ""
        print(f"  #{j.id:<6} {j.type:<22} kunde {j.client_id}  forsøg {j.forsoeg}/{j.max_forsoeg}  {fejl}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Jobkøen.")
    under = parser.add_subparsers(dest="handling", required=True)
    under.add_parser("vis", help="overblik over køen")
    gen = under.add_parser("genkoer", help="genkør fejlede job")
    gen.add_argument("job_id", type=int, nargs="*")
    gen.add_argument("--alle-fejlede", action="store_true")
    tilf = under.add_parser("tilfoej", help="læg et job i kø")
    tilf.add_argument("type")
    tilf.add_argument("--kunde-id", type=int)
    tilf.add_argument("--noegle", help="idempotensnøgle")
    tilf.add_argument("--prioritet", type=int, default=100)
    args = parser.parse_args(argv)

    with ny_session() as session:
        try:
            if args.handling == "vis":
                _vis(session)
            elif args.handling == "genkoer":
                ids = list(args.job_id)
                if args.alle_fejlede:
                    ids += session.scalars(select(Job.id).where(Job.status == "fejlet")).all()
                if not ids:
                    raise KoeFejl("Angiv et job-id eller --alle-fejlede")
                for job_id in ids:
                    genkoer(session, job_id)
                session.commit()
                print(f"Sat i kø igen: {', '.join(f'#{i}' for i in ids)}")
            else:
                r = laeg_i_koe(
                    session, args.type, client_id=args.kunde_id,
                    idempotens_noegle=args.noegle, prioritet=args.prioritet,
                )
                session.commit()
                print(f"Job #{r.job_id} lagt i kø." if r.ny
                      else f"Findes allerede (job #{r.job_id}) – ikke lagt i kø igen.")
        except (KoeFejl, UkendtJobtype) as fejl:
            print(f"Fejl: {fejl}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

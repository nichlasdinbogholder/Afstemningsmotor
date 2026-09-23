"""Planlægger: læg synkroniseringsjob i kø for alle aktive kunder – jævnt fordelt over døgnet.

    python -m app.synk.planlaegger                  # planlæg i dag
    python -m app.synk.planlaegger --dato 2026-09-24

Hver kunde får fire job (customers, suppliers, entries, open_entries) lige efter
hinanden, og kunderne spredes jævnt over døgnet (dansk tid), så vi ikke rammer
e-conomics grænser ved at sende alt på én gang. Planlægges der midt på dagen,
fordeles jobbene over resten af dagen.

Idempotensnøgle: "<ressource>:<client_id>:<dato>", så planlæggeren kan køres så
tit, man vil – samme kunde og ressource lægges kun i kø én gang pr. dag.
Workeren kører den automatisk hver time med `--planlaeg`.
"""

import argparse
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta, time
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

import app.models  # noqa: F401
from app.adaptere.regnskab.base import understoettede_systemer
from app.db import ny_session
from app.jobs.koe import laeg_i_koe
from app.kunder.models import Client, Credential
from app.synk.models import SyncState
from app.synk.ressourcer import SYNK_FUNKTIONER

TIDSZONE = ZoneInfo("Europe/Copenhagen")
MIN_AFSTAND = timedelta(seconds=10)


@dataclass(frozen=True)
class PlanResultat:
    kunder: int
    nye_job: int
    fandtes: int


def aktive_kunder(session: Session) -> list[int]:
    """Aktive kunder (aldrig pause/opsagt) med aktiv adgang til et understøttet system."""
    return list(session.scalars(
        select(Client.id)
        .join(Credential, (Credential.client_id == Client.id)
              & (Credential.system == Client.regnskabssystem))
        .where(
            Client.status == "aktiv",
            Credential.status == "aktiv",
            Client.regnskabssystem.in_(understoettede_systemer()),
        )
        .order_by(Client.id)
    ))


def idempotens_noegle(ressource: str, client_id: int, dag: date) -> str:
    return f"{ressource}:{client_id}:{dag.isoformat()}"


def planlaeg_dag(session: Session, dag: date | None = None) -> PlanResultat:
    """Læg dagens job i kø. Gemmes først ved `session.commit()` hos den, der kalder."""
    nu = session.scalar(select(func.now())).astimezone(TIDSZONE)
    dag = dag or nu.date()
    dag_start = datetime.combine(dag, time(0), TIDSZONE)
    dag_slut = dag_start + timedelta(days=1)
    start = max(dag_start, nu)
    if start >= dag_slut:
        return PlanResultat(0, 0, 0)

    kunder = aktive_kunder(session)
    slaaet_fra = set(session.execute(
        select(SyncState.client_id, SyncState.ressource).where(SyncState.status == "deaktiveret")
    ).all())
    par = [(k, r) for k in kunder for r in SYNK_FUNKTIONER if (k, r) not in slaaet_fra]
    if not par:
        return PlanResultat(len(kunder), 0, 0)

    afstand = max((dag_slut - start) / len(par), MIN_AFSTAND)
    nye = fandtes = 0
    for i, (client_id, ressource) in enumerate(par):
        resultat = laeg_i_koe(
            session, f"synk_{ressource}",
            client_id=client_id,
            payload={"dag": dag.isoformat()},
            planlagt_til=start + i * afstand,
            idempotens_noegle=idempotens_noegle(ressource, client_id, dag),
        )
        nye += resultat.ny
        fandtes += not resultat.ny
    return PlanResultat(len(kunder), nye, fandtes)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Planlæg dagens synkronisering.")
    parser.add_argument("--dato", type=date.fromisoformat, help="YYYY-MM-DD (standard: i dag)")
    args = parser.parse_args(argv)
    with ny_session() as session:
        r = planlaeg_dag(session, args.dato)
        session.commit()
    print(f"{r.kunder} aktive kunder: {r.nye_job} nye job lagt i kø, {r.fandtes} fandtes allerede.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

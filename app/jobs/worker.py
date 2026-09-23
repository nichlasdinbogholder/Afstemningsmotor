"""Worker, der udfører job fra jobkøen.

    python -m app.jobs.worker                      # kør indtil den stoppes (Ctrl+C)
    python -m app.jobs.worker --stop-naar-tom      # kør køen tom og stop

Sådan virker den:
1. Tag næste job (én transaktion): SELECT ... FOR UPDATE SKIP LOCKED + sæt
   status 'i_gang'. SKIP LOCKED gør, at to workers aldrig får samme job.
2. Udfør jobbet i en ny transaktion. Lykkes det, gemmes jobbets arbejde og
   status 'faerdig' samlet.
3. Fejler det, sættes det i kø igen med stigende ventetid (30 s, 1 min,
   2 min, … højst 1 time). Efter max_forsoeg står det som 'fejlet'.
4. Job, der har stået 'i_gang' for længe (workeren døde), frigives igen.

En fejl i ét job stopper aldrig workeren eller påvirker andre job.
"""

import argparse
import logging
import os
import signal
import socket
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

import app.models  # noqa: F401  (alle tabeller skal være kendt)
from app.db import get_engine, ny_session
from app.jobs.register import JobKontekst, UkendtJobtype, hent_funktion
from app.sikkerhed.hemmeligheder import rediger

log = logging.getLogger(__name__)

HENT_NAESTE = text("""
    SELECT id FROM jobs
    WHERE status = 'koe' AND planlagt_til <= now()
      AND (CAST(:typer AS text[]) IS NULL OR type = ANY(CAST(:typer AS text[])))
    ORDER BY prioritet, planlagt_til
    FOR UPDATE SKIP LOCKED
    LIMIT 1
""")

MARKER_I_GANG = text("""
    UPDATE jobs
    SET status = 'i_gang', forsoeg = forsoeg + 1, paabegyndt = now(),
        laast_af = :worker, laast_tidspunkt = now()
    WHERE id = :id
    RETURNING id, type, client_id, payload, forsoeg, max_forsoeg
""")

MARKER_FAERDIG = text("""
    UPDATE jobs
    SET status = 'faerdig', afsluttet = now(), laast_af = NULL, laast_tidspunkt = NULL
    WHERE id = :id AND status = 'i_gang' AND laast_af = :worker
""")

MARKER_FEJL = text("""
    UPDATE jobs
    SET status = CASE WHEN :endelig OR forsoeg >= max_forsoeg THEN 'fejlet' ELSE 'koe' END,
        planlagt_til = CASE WHEN :endelig OR forsoeg >= max_forsoeg
                            THEN planlagt_til ELSE now() + make_interval(secs => :vent) END,
        afsluttet = CASE WHEN :endelig OR forsoeg >= max_forsoeg THEN now() ELSE NULL END,
        sidste_fejl = :fejl, laast_af = NULL, laast_tidspunkt = NULL
    WHERE id = :id AND status = 'i_gang' AND laast_af = :worker
    RETURNING status, planlagt_til
""")

FRIGIV_HAENGENDE = text("""
    UPDATE jobs
    SET status = CASE WHEN forsoeg >= max_forsoeg THEN 'fejlet' ELSE 'koe' END,
        afsluttet = CASE WHEN forsoeg >= max_forsoeg THEN now() ELSE NULL END,
        planlagt_til = now(),
        sidste_fejl = 'Frigivet: stod i gang for længe (worker ' || laast_af || ' stoppede?)',
        laast_af = NULL, laast_tidspunkt = NULL
    WHERE status = 'i_gang' AND laast_tidspunkt < now() - make_interval(secs => :sekunder)
      AND (CAST(:typer AS text[]) IS NULL OR type = ANY(CAST(:typer AS text[])))
    RETURNING id, status
""")


def ventetid(forsoeg: int, basis: float = 30, maks: float = 3600) -> float:
    """Ventetid før næste forsøg: fordobles for hvert forsøg (30 s, 60 s, 120 s …), højst `maks`."""
    return min(basis * 2 ** max(forsoeg - 1, 0), maks)


@dataclass(frozen=True)
class _TagetJob:
    id: int
    type: str
    client_id: int | None
    payload: dict
    forsoeg: int
    max_forsoeg: int


class Worker:
    def __init__(
        self,
        navn: str | None = None,
        engine: Engine | None = None,
        session_fabrik: Callable[[], Session] = ny_session,
        basis_ventetid: float = 30,
        maks_ventetid: float = 3600,
        haengende_efter: float = 15 * 60,
        pause: float = 2.0,
        kun_typer: list[str] | None = None,
    ) -> None:
        self.navn = navn or f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:6]}"
        self._engine = engine or get_engine()
        self._session_fabrik = session_fabrik
        self._basis = basis_ventetid
        self._maks = maks_ventetid
        self._haengende_efter = haengende_efter
        self._pause = pause
        self._typer = list(kun_typer) if kun_typer else None  # None = alle typer
        self._stop = threading.Event()

    # --- Enkelte skridt -----------------------------------------------------

    def tag_naeste(self) -> _TagetJob | None:
        """Hent næste job og sæt det 'i_gang' – i SAMME transaktion."""
        with self._engine.begin() as forbindelse:
            job_id = forbindelse.execute(HENT_NAESTE, {"typer": self._typer}).scalar()
            if job_id is None:
                return None
            r = forbindelse.execute(MARKER_I_GANG, {"id": job_id, "worker": self.navn}).one()
        return _TagetJob(r.id, r.type, r.client_id, r.payload or {}, r.forsoeg, r.max_forsoeg)

    def frigiv_haengende(self) -> int:
        """Frigiv job, der har stået 'i_gang' længere end `haengende_efter` sekunder."""
        with self._engine.begin() as forbindelse:
            frigivet = forbindelse.execute(
                FRIGIV_HAENGENDE, {"sekunder": self._haengende_efter, "typer": self._typer}
            ).all()
        for job_id, status in frigivet:
            log.warning("Job %s stod i gang for længe – sat til '%s'", job_id, status)
        return len(frigivet)

    def _marker_fejl(self, job: _TagetJob, fejl: str, endelig: bool = False) -> None:
        with self._engine.begin() as forbindelse:
            r = forbindelse.execute(MARKER_FEJL, {
                "id": job.id, "worker": self.navn, "endelig": endelig,
                "vent": ventetid(job.forsoeg, self._basis, self._maks),
                "fejl": rediger(fejl)[:4000],
            }).first()
        if r is None:
            log.warning("Job %s var ikke længere vores – fejlen blev ikke gemt", job.id)
        elif r.status == "fejlet":
            log.error("Job %s (%s) fejlede endeligt efter %s forsøg: %s",
                      job.id, job.type, job.forsoeg, fejl)
        else:
            log.warning("Job %s (%s) fejlede (forsøg %s af %s) – prøver igen %s: %s",
                        job.id, job.type, job.forsoeg, job.max_forsoeg,
                        f"{r.planlagt_til:%H:%M:%S}", fejl)

    def koer_et_job(self) -> bool:
        """Tag og udfør ét job. Returnerer False, hvis køen var tom."""
        job = self.tag_naeste()
        if job is None:
            return False
        try:
            funktion = hent_funktion(job.type)
        except UkendtJobtype as fejl:
            self._marker_fejl(job, str(fejl), endelig=True)
            return True

        session = self._session_fabrik()
        try:
            funktion(JobKontekst(
                job_id=job.id, type=job.type, client_id=job.client_id, payload=job.payload,
                forsoeg=job.forsoeg, max_forsoeg=job.max_forsoeg, session=session,
            ))
            # Jobbets arbejde og 'faerdig' gemmes samlet.
            if session.execute(MARKER_FAERDIG, {"id": job.id, "worker": self.navn}).rowcount == 1:
                session.commit()
                log.info("Job %s (%s) færdig", job.id, job.type)
            else:
                # Jobbet er frigivet og måske taget af en anden – gem ikke vores arbejde.
                session.rollback()
                log.warning("Job %s var ikke længere vores – arbejdet er rullet tilbage", job.id)
        except Exception as fejl:  # noqa: BLE001 – ét jobs fejl må aldrig stoppe workeren
            session.rollback()
            self._marker_fejl(job, f"{type(fejl).__name__}: {fejl}")
        finally:
            session.close()
        return True

    # --- Løkken -------------------------------------------------------------

    def stop(self) -> None:
        self._stop.set()

    def koer(self, stop_naar_tom: bool = False, oprydning_hvert: float = 60) -> None:
        """Kør job i en løkke, indtil `stop()` kaldes (eller køen er tom)."""
        if threading.current_thread() is threading.main_thread():
            for sig in (signal.SIGINT, signal.SIGTERM):
                signal.signal(sig, lambda *_: self._stop_blidt())
        log.info("Worker %s startet", self.navn)
        sidste_oprydning = float("-inf")
        while not self._stop.is_set():
            try:
                if time.monotonic() - sidste_oprydning >= oprydning_hvert:
                    self.frigiv_haengende()
                    sidste_oprydning = time.monotonic()
                fik_job = self.koer_et_job()
            except Exception:  # noqa: BLE001 – fx databasen er nede: vent og prøv igen
                log.exception("Fejl i worker %s – fortsætter om lidt", self.navn)
                fik_job = False
                if stop_naar_tom:
                    return
            if not fik_job:
                if stop_naar_tom:
                    break
                self._stop.wait(self._pause)
        log.info("Worker %s stoppet", self.navn)

    def _stop_blidt(self) -> None:
        log.info("Stopper, når det aktuelle job er færdigt …")
        self._stop.set()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Kør jobkøens worker.")
    parser.add_argument("--stop-naar-tom", action="store_true", help="stop, når køen er tom")
    parser.add_argument("--pause", type=float, default=2.0, help="sekunder mellem tjek, når køen er tom")
    parser.add_argument("--kun-typer", nargs="+", help="kør kun disse jobtyper (standard: alle)")
    parser.add_argument("--haengende-efter", type=float, default=15 * 60,
                        help="sekunder, før et job i gang regnes som hængende (standard 900)")
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    )
    worker = Worker(pause=args.pause, haengende_efter=args.haengende_efter, kun_typer=args.kun_typer)
    worker.koer(stop_naar_tom=args.stop_naar_tom)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

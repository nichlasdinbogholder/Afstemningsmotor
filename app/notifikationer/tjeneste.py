"""Opret notifikationer. Bruges af jobs og moduler – fx når CVR viser, at en kunde er under konkurs."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.notifikationer.models import ALVOR, Notification
from app.personale.models import Staff


def administratorer(session: Session) -> list[int]:
    return list(session.scalars(select(Staff.id).where(Staff.rolle == "admin", Staff.aktiv.is_(True))))


def send_notifikation(session: Session, modtagere: list[int | None], alvor: str, titel: str,
                      tekst: str | None = None, link: str | None = None, client_id: int | None = None) -> int:
    """Én notifikation pr. modtager (dubletter og tomme fjernes; kun aktive medarbejdere). Returnerer antal."""
    if alvor not in ALVOR:
        raise ValueError(f"Ukendt alvor '{alvor}'")
    aktive = set(session.scalars(select(Staff.id).where(Staff.aktiv.is_(True),
                                                        Staff.id.in_([m for m in modtagere if m]))))
    for staff_id in sorted(aktive):
        session.add(Notification(staff_id=staff_id, client_id=client_id, alvor=alvor, titel=titel[:255],
                                 tekst=tekst, link=link))
    session.flush()
    return len(aktive)

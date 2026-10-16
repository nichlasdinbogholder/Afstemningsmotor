"""Det ENESTE sted, der ændrer status på et fund.

Databasen sørger for loggen: en trigger skriver en række i finding_events ved hver
statusændring med den actor og note, der er sat her – og afviser en statusændring,
hvor ingen actor er sat. Så kan status ikke ændres uden spor, heller ikke ved en fejl.
"""

from dataclasses import dataclass

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.rules.models import STATUSSER, Finding


class UgyldigStatus(ValueError):
    pass


@dataclass(frozen=True)
class Statusskift:
    finding_id: int
    fra: str
    til: str


def saet_status(session: Session, finding_id: int, ny_status: str, actor: str,
                note: str | None = None) -> Statusskift:
    """Skift status på ét fund. Gemmes ved session.commit() hos den, der kalder."""
    if ny_status not in STATUSSER:
        raise UgyldigStatus(f"Ukendt status '{ny_status}' – brug {', '.join(STATUSSER)}")
    if not actor or not actor.strip():
        raise UgyldigStatus("En statusændring kræver en actor (hvem der gjorde det)")
    fund = session.execute(
        select(Finding.status).where(Finding.id == finding_id).with_for_update()
    ).one_or_none()
    if fund is None:
        raise UgyldigStatus(f"Fund {finding_id} findes ikke")
    if fund.status == ny_status:
        return Statusskift(finding_id, fund.status, ny_status)  # intet at ændre, intet at logge

    session.execute(text("SELECT set_config('afstemning.actor', :a, true), "
                         "set_config('afstemning.note', :n, true)"),
                    {"a": actor.strip(), "n": note or ""})
    try:
        session.execute(text("UPDATE findings SET status = :s, updated_at = now() WHERE id = :id"),
                        {"s": ny_status, "id": finding_id})
    finally:
        session.execute(text("SELECT set_config('afstemning.actor', '', true), "
                             "set_config('afstemning.note', '', true)"))
    session.expire_all()
    return Statusskift(finding_id, fund.status, ny_status)

"""Revisionsspor: hvem gjorde hvad, hvornår.

Rækker kan kun tilføjes – en trigger i databasen afviser ændring og sletning.
`detaljer` må aldrig indeholde tokens eller andre hemmeligheder.
"""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    # Tom, når handlingen ikke vedrører en bestemt kunde/medarbejder.
    client_id: Mapped[int | None] = mapped_column(
        ForeignKey("clients.id", ondelete="RESTRICT"), index=True
    )
    staff_id: Mapped[int | None] = mapped_column(
        ForeignKey("staff.id", ondelete="RESTRICT"), index=True
    )
    handling: Mapped[str] = mapped_column(String(100))
    detaljer: Mapped[dict | None] = mapped_column(JSONB)
    tidspunkt: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )

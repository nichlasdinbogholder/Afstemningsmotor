"""Vores egne medarbejdere."""

from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, Integer, String, func, true
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, kun_vaerdier

STAFF_ROLLER = ("admin", "medarbejder")


class Staff(Base):
    """Medarbejdere slettes ikke – de sættes til aktiv = false, så historik bevares."""

    __tablename__ = "staff"
    __table_args__ = (
        kun_vaerdier("rolle", STAFF_ROLLER),
        # E-mail gemmes altid med små bogstaver, så 'A@x.dk' og 'a@x.dk' er den samme.
        CheckConstraint("email = lower(email)", name="email_smaa_bogstaver"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    navn: Mapped[str] = mapped_column(String(255))
    email: Mapped[str] = mapped_column(String(255), unique=True)
    rolle: Mapped[str] = mapped_column(String(20), server_default="medarbejder")
    aktiv: Mapped[bool] = mapped_column(Boolean, server_default=true())
    oprettet: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    def __repr__(self) -> str:
        return f"<Staff id={self.id} navn={self.navn!r}>"

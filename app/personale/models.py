"""Vores egne medarbejdere."""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
    true,
)
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
    # Microsofts faste id for brugeren – bindes ved første login, så en genbrugt
    # e-mailadresse ikke giver adgang til en anden persons konto.
    microsoft_oid: Mapped[str | None] = mapped_column(String(64), unique=True)
    sidst_logget_ind: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    oprettet: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    def __repr__(self) -> str:
        return f"<Staff id={self.id} navn={self.navn!r}>"


class StaffCostRate(Base):
    """Kostpris pr. time for en medarbejder – med den dato, den gælder fra. KUN for administratorer.

    En ny kostpris overskriver aldrig den gamle: timer regnes med den kostpris, der gjaldt på dagen.
    Den gældende kostpris = den med den seneste `valid_from`, der ikke ligger efter dagen. Slettes ikke.
    """

    __tablename__ = "staff_cost_rates"
    __table_args__ = (
        UniqueConstraint("staff_id", "valid_from", name="uq_staff_cost_rates_medarbejder_dato"),
        CheckConstraint("hourly_cost >= 0", name="kostpris_ikke_negativ"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    staff_id: Mapped[int] = mapped_column(ForeignKey("staff.id", ondelete="RESTRICT"))
    hourly_cost: Mapped[Decimal] = mapped_column(Numeric(15, 2))
    valid_from: Mapped[date] = mapped_column(Date)
    created_by: Mapped[int] = mapped_column(ForeignKey("staff.id", ondelete="RESTRICT"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

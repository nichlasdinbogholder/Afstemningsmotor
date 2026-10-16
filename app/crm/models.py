"""CRM: opgaver, timer, noter, dokumenter og overleveringer.

Historik (timer, noter, dokumenter, overleveringer) må ikke forsvinde, så
kunder og medarbejdere med historik kan ikke slettes (ondelete=RESTRICT).
"""

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
    Text,
    false,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, kun_vaerdier

OPGAVE_STATUSSER = ("aaben", "i_gang", "loest", "afvist")
OPRETTET_AF = ("medarbejder", "system")
UGEDAGE = ("mandag", "tirsdag", "onsdag", "torsdag", "fredag", "loerdag", "soendag")


def _kunde(nullable: bool = False):
    return mapped_column(
        ForeignKey("clients.id", ondelete="RESTRICT"), index=True, nullable=nullable
    )


def _medarbejder(nullable: bool = False, ondelete: str = "RESTRICT"):
    return mapped_column(
        ForeignKey("staff.id", ondelete=ondelete), index=True, nullable=nullable
    )


class Task(Base):
    __tablename__ = "tasks"
    __table_args__ = (
        kun_vaerdier("status", OPGAVE_STATUSSER),
        kun_vaerdier("oprettet_af", OPRETTET_AF),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    client_id: Mapped[int] = _kunde()
    type: Mapped[str | None] = mapped_column(String(100))
    titel: Mapped[str] = mapped_column(String(255))
    beskrivelse: Mapped[str | None] = mapped_column(Text)
    ansvarlig_id: Mapped[int | None] = _medarbejder(nullable=True, ondelete="SET NULL")
    frist: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(20), server_default="aaben")
    oprettet_af: Mapped[str] = mapped_column(String(20), server_default="medarbejder")
    oprettet: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    loest: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class TimeEntry(Base):
    __tablename__ = "time_entries"
    __table_args__ = (
        CheckConstraint("timer > 0 AND timer <= 24", name="timer_0_til_24"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    client_id: Mapped[int] = _kunde()
    staff_id: Mapped[int] = _medarbejder()
    dato: Mapped[date] = mapped_column(Date)
    timer: Mapped[Decimal] = mapped_column(Numeric(4, 2))
    opgavetype: Mapped[str | None] = mapped_column(String(100))
    kommentar: Mapped[str | None] = mapped_column(Text)
    faktureret: Mapped[bool] = mapped_column(Boolean, server_default=false())
    oprettet: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Note(Base):
    __tablename__ = "notes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    client_id: Mapped[int] = _kunde()
    staff_id: Mapped[int | None] = _medarbejder(nullable=True)
    tekst: Mapped[str] = mapped_column(Text)
    oprettet: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    client_id: Mapped[int] = _kunde()
    filnavn: Mapped[str] = mapped_column(String(255))
    filsti: Mapped[str] = mapped_column(String(1024))
    dokumenttype: Mapped[str | None] = mapped_column(String(100))
    uploadet_af: Mapped[int | None] = _medarbejder(nullable=True)
    uploadet: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Handover(Base):
    """Overlevering af en kunde ved ferie og sygdom."""

    __tablename__ = "handovers"
    __table_args__ = (
        kun_vaerdier("aftalt_ugedag", UGEDAGE),
        CheckConstraint("periode_slut >= periode_start", name="periode_slut_efter_start"),
        CheckConstraint("fra_staff_id <> til_staff_id", name="fra_og_til_forskellige"),
        CheckConstraint(
            "(kvitteret_af_staff_id IS NULL) = (kvitteret_tidspunkt IS NULL)",
            name="kvittering_komplet",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    client_id: Mapped[int] = _kunde()
    fra_staff_id: Mapped[int] = _medarbejder()
    til_staff_id: Mapped[int] = _medarbejder()
    periode_start: Mapped[date] = mapped_column(Date)
    periode_slut: Mapped[date] = mapped_column(Date)
    aftalt_ugedag: Mapped[str | None] = mapped_column(String(10))
    kommentar: Mapped[str | None] = mapped_column(Text)
    vigtig_info: Mapped[str | None] = mapped_column(Text)
    fysisk_overlevering_sket: Mapped[bool] = mapped_column(Boolean, server_default=false())
    kvitteret_af_staff_id: Mapped[int | None] = _medarbejder(nullable=True)
    kvitteret_tidspunkt: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

"""Kontoudtog fra en ekstern kilde (grossist, skattekonto, bank …) og deres linjer.

Bevidst UAFHÆNGIGT af kilden: matchmotoren (app/rules/kontoudtog.py) ser kun på
- HVAD udtoget skal sammenlignes med i bogføringen: en finanskonto (`kontonummer`),
  en kunde/leverandør (`modpart`, fx "kreditor:123") – eller begge,
- perioden, og
- fortegnet: står beløbene med SAMME fortegn som i bogføringen eller MODSAT? (En
  grossist skriver en faktura som +1.000; i bogføringen står den på leverandøren som −1.000.)
Hvor udtoget kom fra (mail, SharePoint, CSV) er indlæsningens sag – ikke motorens.
"""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger, CheckConstraint, Date, DateTime, ForeignKey, Integer, Numeric, String, Text,
    UniqueConstraint, func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, kun_vaerdier

KILDER = ("grossist", "skattekonto", "bank", "andet")
FORTEGN = ("samme", "modsat")
MATCH_TRIN = ("bilag_beloeb", "beloeb_dato")


class Statement(Base):
    """Ét kontoudtog for én kunde, ét modstykke i bogføringen og én periode."""

    __tablename__ = "statements"
    __table_args__ = (
        kun_vaerdier("kilde", KILDER),
        kun_vaerdier("fortegn", FORTEGN),
        CheckConstraint("periode_fra <= periode_til", name="gyldig_periode"),
        CheckConstraint("kontonummer IS NOT NULL OR modpart IS NOT NULL", name="har_modstykke"),
        CheckConstraint("modpart ~ '^(debitor|kreditor):[0-9]+$'", name="modpart_gyldig"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id", ondelete="RESTRICT"), index=True)
    kilde: Mapped[str] = mapped_column(String(20))
    kontonummer: Mapped[int | None] = mapped_column(Integer)      # finanskonto i bogføringen
    modpart: Mapped[str | None] = mapped_column(String(30))       # "kreditor:<nr>" / "debitor:<nr>"
    periode_fra: Mapped[date] = mapped_column(Date)
    periode_til: Mapped[date] = mapped_column(Date)
    fortegn: Mapped[str] = mapped_column(String(10))
    kildefil: Mapped[str | None] = mapped_column(Text)            # filnavn/mail-emne – aldrig indhold
    oprettet: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class StatementLine(Base):
    """Én linje på et kontoudtog. Matchfelterne udfyldes af matchmotoren ved hver kørsel."""

    __tablename__ = "statement_lines"
    __table_args__ = (
        UniqueConstraint("statement_id", "linje_nr", name="uq_statement_lines_udtog_linje"),
        kun_vaerdier("match_trin", MATCH_TRIN),
        CheckConstraint("(match_entry_id IS NULL) = (match_trin IS NULL)", name="match_hele"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    statement_id: Mapped[int] = mapped_column(ForeignKey("statements.id", ondelete="RESTRICT"), index=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id", ondelete="RESTRICT"), index=True)
    linje_nr: Mapped[int] = mapped_column(Integer)
    dato: Mapped[date] = mapped_column(Date)
    reference: Mapped[str | None] = mapped_column(Text)            # fx faktura-/bilagsnummer på udtoget
    tekst: Mapped[str | None] = mapped_column(Text)
    beloeb: Mapped[Decimal] = mapped_column(Numeric(18, 2))        # som på udtoget
    raa_data: Mapped[dict | None] = mapped_column(JSONB)           # den oprindelige linje
    match_entry_id: Mapped[int | None] = mapped_column(
        ForeignKey("entries.id", ondelete="SET NULL"), index=True)
    match_trin: Mapped[str | None] = mapped_column(String(20))

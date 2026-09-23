"""Regnskabsdata hentet fra kundernes systemer (adskilt fra CRM-data)."""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    false,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, kun_vaerdier
from app.kunder.models import SYSTEMER

# Værdier fra e-conomics skema for Account.accountType.
ECONOMIC_KONTOTYPER = (
    "profitAndLoss", "status", "totalFrom", "heading", "headingStart", "sumInterval", "sumAlpha",
)
DEBET_KREDIT = ("debit", "credit")


class Account(Base):
    """En konto i en kundes kontoplan – en kopi af det, der står i regnskabssystemet."""

    __tablename__ = "accounts"
    __table_args__ = (
        kun_vaerdier("system", SYSTEMER),
        kun_vaerdier("kontotype", ECONOMIC_KONTOTYPER),
        kun_vaerdier("debet_kredit", DEBET_KREDIT),
        UniqueConstraint("tenant_id", "system", "kontonummer", name="uq_accounts_tenant_konto"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # tenant = kunden, hvis kontoplan det er.
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("clients.id", ondelete="RESTRICT"), index=True
    )
    system: Mapped[str] = mapped_column(String(20))
    kontonummer: Mapped[int] = mapped_column(Integer)
    navn: Mapped[str] = mapped_column(String(125))
    kontotype: Mapped[str | None] = mapped_column(String(20))
    debet_kredit: Mapped[str | None] = mapped_column(String(10))
    momskode: Mapped[str | None] = mapped_column(String(5))
    spaerret: Mapped[bool] = mapped_column(Boolean, server_default=false())
    direkte_posteringer_blokeret: Mapped[bool] = mapped_column(Boolean, server_default=false())
    # Saldi er et øjebliksbillede fra tidspunktet i `hentet`.
    saldo: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    kladdesaldo: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    # Hele svaret fra systemet (fx sumintervaller). Indeholder ingen hemmeligheder.
    raa_data: Mapped[dict] = mapped_column(JSONB)
    hentet: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


# --- Cache af data fra kundernes regnskabssystemer --------------------------
# Kopier af det, der står i e-conomic/Dinero. Skrives kun af synkroniseringen
# (app/synk/ressourcer.py). `sidst_set` = sidste gang posten var med i en hentning.
# Indeks på client_id: dækkes af den unikke regel, som starter med client_id.

from app.adaptere.regnskab.base import ENTRY_TYPER  # noqa: E402

AABEN_POST_TYPER = ("debitor", "kreditor")


def _sidst_set() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now())


def _kunde_id() -> Mapped[int]:
    return mapped_column(ForeignKey("clients.id", ondelete="RESTRICT"))


class CustomerCache(Base):
    __tablename__ = "customers"
    __table_args__ = (UniqueConstraint("client_id", "kundenummer", name="uq_customers_kunde_nummer"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    client_id: Mapped[int] = _kunde_id()
    kundenummer: Mapped[int] = mapped_column(BigInteger)
    navn: Mapped[str] = mapped_column(String(255))
    cvr: Mapped[str | None] = mapped_column(String(40))
    betalingsbetingelse: Mapped[int | None] = mapped_column(Integer)  # nummeret i systemet
    spaerret: Mapped[bool | None] = mapped_column(Boolean)
    saldo: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    sidst_set: Mapped[datetime] = _sidst_set()


class SupplierCache(Base):
    __tablename__ = "suppliers"
    __table_args__ = (
        UniqueConstraint("client_id", "leverandoernummer", name="uq_suppliers_kunde_nummer"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    client_id: Mapped[int] = _kunde_id()
    leverandoernummer: Mapped[int] = mapped_column(BigInteger)
    navn: Mapped[str] = mapped_column(String(255))
    cvr: Mapped[str | None] = mapped_column(String(40))
    betalingsbetingelse: Mapped[int | None] = mapped_column(Integer)
    saldo: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))  # e-conomic: altid tom
    sidst_set: Mapped[datetime] = _sidst_set()


class EntryCache(Base):
    __tablename__ = "entries"
    __table_args__ = (
        UniqueConstraint("client_id", "bogfoert_id", name="uq_entries_kunde_post"),
        kun_vaerdier("entry_type", ENTRY_TYPER),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    client_id: Mapped[int] = _kunde_id()
    bogfoert_id: Mapped[int] = mapped_column(BigInteger)  # e-conomic: entryNumber
    bilagsnummer: Mapped[int | None] = mapped_column(BigInteger)
    dato: Mapped[date | None] = mapped_column(Date, index=True)
    kontonummer: Mapped[int | None] = mapped_column(Integer)
    tekst: Mapped[str | None] = mapped_column(Text)
    beloeb: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    modpart: Mapped[str | None] = mapped_column(String(30))  # "debitor:<nr>"/"kreditor:<nr>"
    valuta: Mapped[str | None] = mapped_column(String(3))
    entry_type: Mapped[str | None] = mapped_column(String(30))
    sidst_set: Mapped[datetime] = _sidst_set()


class OpenEntryCache(Base):
    __tablename__ = "open_entries"
    __table_args__ = (
        UniqueConstraint("client_id", "bogfoert_id", name="uq_open_entries_kunde_post"),
        kun_vaerdier("type", AABEN_POST_TYPER),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    client_id: Mapped[int] = _kunde_id()
    bogfoert_id: Mapped[int] = mapped_column(BigInteger)  # e-conomic: entryNumber
    type: Mapped[str] = mapped_column(String(10))
    partnummer: Mapped[int] = mapped_column(BigInteger)
    partnavn: Mapped[str | None] = mapped_column(String(255))
    bilagsnummer: Mapped[int | None] = mapped_column(BigInteger)
    fakturanummer: Mapped[str | None] = mapped_column(String(100))
    dato: Mapped[date | None] = mapped_column(Date)
    forfaldsdato: Mapped[date | None] = mapped_column(Date)
    beloeb: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    restbeloeb: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    valuta: Mapped[str | None] = mapped_column(String(3))
    sidst_set: Mapped[datetime] = _sidst_set()

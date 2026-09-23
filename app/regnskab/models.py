"""Regnskabsdata hentet fra kundernes systemer (adskilt fra CRM-data)."""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
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

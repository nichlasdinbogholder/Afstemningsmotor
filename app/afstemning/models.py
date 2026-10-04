"""Fund fra afstemningsreglerne."""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import BigInteger, Date, DateTime, ForeignKey, Numeric, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, kun_vaerdier

REGLER = ("smaa_restbeloeb", "betaling_uden_faktura", "forfalden_over_6_mdr")
FINDING_STATUSSER = ("aaben", "loest", "afvist")


class Finding(Base):
    """Ét fund = én post, som én regel har udpeget hos én kunde.

    Den unikke regel (client_id, regel, kilde_id) gør reglerne idempotente:
    kører en regel igen, kan samme post ikke blive et nyt fund.
    """

    __tablename__ = "findings"
    __table_args__ = (
        UniqueConstraint("client_id", "regel", "kilde_id", name="uq_findings_kunde_regel_post"),
        kun_vaerdier("regel", REGLER),
        kun_vaerdier("status", FINDING_STATUSSER),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id", ondelete="RESTRICT"))
    regel: Mapped[str] = mapped_column(String(40))
    # Postens id i open_entries.bogfoert_id (systemets eget id for posten).
    kilde_id: Mapped[int] = mapped_column(BigInteger)
    # Øjebliksbillede af posten, da fundet blev gjort:
    type: Mapped[str | None] = mapped_column(String(10))
    partnummer: Mapped[int | None] = mapped_column(BigInteger)
    partnavn: Mapped[str | None] = mapped_column(String(255))
    fakturanummer: Mapped[str | None] = mapped_column(String(100))
    dato: Mapped[date | None] = mapped_column(Date)
    forfaldsdato: Mapped[date | None] = mapped_column(Date)
    beloeb: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    restbeloeb: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    valuta: Mapped[str | None] = mapped_column(String(3))
    beskrivelse: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(10), server_default="aaben", index=True)
    fundet: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

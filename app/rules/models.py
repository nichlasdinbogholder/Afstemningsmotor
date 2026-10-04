"""Fund fra afstemningsreglerne og loggen over deres statusændringer.

findings:       ét fund = ét problem, som én regel har fundet hos én kunde.
                `fingerprint` er stabilt på tværs af kørsler, så samme problem altid
                bliver den SAMME række (unik på client_id + rule_code + fingerprint).
finding_events: hver statusændring – skrives af en trigger i databasen, så ingen
                ændring kan ske uden en række her. Fund og hændelser slettes aldrig.
"""

from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    SmallInteger,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, kun_vaerdier

STATUSSER = ("open", "accepted", "resolved", "ignored")
ALVORLIGHED = ("low", "medium", "high")


class Finding(Base):
    __tablename__ = "findings"
    __table_args__ = (
        UniqueConstraint("client_id", "rule_code", "fingerprint", name="uq_findings_kunde_regel_fingerprint"),
        Index("ix_findings_kunde_status_alvor", "client_id", "status", "severity"),
        Index("ix_findings_kunde_sidst_set", "client_id", "last_seen_at"),
        kun_vaerdier("status", STATUSSER),
        kun_vaerdier("severity", ALVORLIGHED),
        CheckConstraint("rule_code ~ '^[a-z0-9_]+$'", name="rule_code_gyldig"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id", ondelete="RESTRICT"))
    rule_code: Mapped[str] = mapped_column(Text)
    rule_version: Mapped[int] = mapped_column(SmallInteger, server_default=text("1"))
    fingerprint: Mapped[str] = mapped_column(Text)
    severity: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default="open")
    title: Mapped[str] = mapped_column(Text)
    detail: Mapped[dict] = mapped_column(JSONB)
    entry_ids: Mapped[list[int]] = mapped_column(ARRAY(BigInteger))
    period_start: Mapped[date | None] = mapped_column(Date)
    period_end: Mapped[date | None] = mapped_column(Date)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class FindingEvent(Base):
    __tablename__ = "finding_events"
    __table_args__ = (
        kun_vaerdier("from_status", STATUSSER),
        kun_vaerdier("to_status", STATUSSER),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    finding_id: Mapped[int] = mapped_column(ForeignKey("findings.id", ondelete="RESTRICT"), index=True)
    from_status: Mapped[str | None] = mapped_column(Text)
    to_status: Mapped[str] = mapped_column(Text)
    actor: Mapped[str] = mapped_column(Text)  # brugerens navn/mail, eller 'system'
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

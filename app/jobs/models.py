"""Jobkøen: opgaver, som en worker udfører i baggrunden."""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, kun_vaerdier

# Uden æøå, som alle andre faste værdier i databasen: koe = kø, faerdig = færdig.
JOB_STATUSSER = ("koe", "i_gang", "faerdig", "fejlet")


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        kun_vaerdier("status", JOB_STATUSSER),
        CheckConstraint("prioritet >= 0", name="prioritet_ikke_negativ"),
        CheckConstraint("forsoeg >= 0", name="forsoeg_ikke_negativ"),
        CheckConstraint("max_forsoeg >= 1", name="max_forsoeg_mindst_1"),
        CheckConstraint(
            "(laast_af IS NULL) = (laast_tidspunkt IS NULL)", name="laas_komplet"
        ),
        # Præcis det opslag, workeren laver: klar-til-kørsel sorteret efter prioritet og tid.
        Index(
            "ix_jobs_klar",
            "prioritet",
            "planlagt_til",
            postgresql_where=text("status = 'koe'"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    type: Mapped[str] = mapped_column(String(100))
    client_id: Mapped[int | None] = mapped_column(
        ForeignKey("clients.id", ondelete="RESTRICT"), index=True
    )
    # Må aldrig indeholde tokens eller andre hemmeligheder (tjekkes af laeg_i_koe).
    payload: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    status: Mapped[str] = mapped_column(String(10), server_default="koe", index=True)
    # Lavest tal køres først.
    prioritet: Mapped[int] = mapped_column(SmallInteger, server_default="100")
    planlagt_til: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    paabegyndt: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    afsluttet: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    forsoeg: Mapped[int] = mapped_column(Integer, server_default="0")
    max_forsoeg: Mapped[int] = mapped_column(Integer, server_default="5")
    sidste_fejl: Mapped[str | None] = mapped_column(Text)
    laast_af: Mapped[str | None] = mapped_column(String(255))
    laast_tidspunkt: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    idempotens_noegle: Mapped[str] = mapped_column(String(255), unique=True)
    oprettet: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    def __repr__(self) -> str:
        return f"<Job id={self.id} type={self.type!r} status={self.status!r}>"

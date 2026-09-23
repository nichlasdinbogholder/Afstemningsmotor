"""Kundekartotek og adgange.

Kundedata ligger her – adskilt fra afstemningslogikken – så et internt CRM
senere kan bygges ovenpå.
"""

from datetime import date, datetime

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.sikkerhed.kryptering import dekrypter_token, krypter_token

SYSTEMER = ("economic", "dinero")
KUNDE_STATUSSER = ("aktiv", "pause", "ophoert")
ADGANG_STATUSSER = ("aktiv", "udloebet", "tilbagekaldt")


def _i_liste(kolonne: str, værdier: tuple[str, ...]) -> str:
    return f"{kolonne} IN ({', '.join(repr(v) for v in værdier)})"


class Client(Base):
    __tablename__ = "clients"
    __table_args__ = (
        CheckConstraint(_i_liste("system", SYSTEMER), name="system_gyldig"),
        CheckConstraint(_i_liste("status", KUNDE_STATUSSER), name="status_gyldig"),
        CheckConstraint("cvr ~ '^[0-9]{8}$'", name="cvr_8_cifre"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    navn: Mapped[str] = mapped_column(String(255))
    cvr: Mapped[str | None] = mapped_column(String(8), unique=True)
    kundenummer: Mapped[str] = mapped_column(String(50), unique=True)
    system: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20), server_default="aktiv")
    ansvarlig_medarbejder: Mapped[str | None] = mapped_column(String(255))
    aftaletype: Mapped[str | None] = mapped_column(String(100))
    startdato: Mapped[date | None] = mapped_column(Date)
    kassekladdenavn: Mapped[str | None] = mapped_column(String(255))
    oprettet: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    credentials: Mapped[list["Credential"]] = relationship(
        back_populates="client", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Client id={self.id} kundenummer={self.kundenummer!r} navn={self.navn!r}>"


class Credential(Base):
    """En kundes adgang til e-conomic eller Dinero.

    Tokenet gemmes kun krypteret. Brug `saet_token()` til at gemme og
    `hent_token()` lige før det skal bruges. Tokenet må aldrig logges.

    Sikkerhedsnet i databasen: triggeren `credentials_kraev_krypteret_token`
    (se første migrering) afviser alt, der ikke er krypteret med Fernet
    (præfiks FERNET_PREFIX). Den er en trigger og ikke en CHECK-regel, fordi
    PostgreSQL ved brud på en CHECK-regel skriver hele rækken ind i
    fejlbeskeden – og så ville et token i klartekst kunne havne i en log.
    """

    __tablename__ = "credentials"
    __table_args__ = (
        CheckConstraint(_i_liste("systemnavn", SYSTEMER), name="systemnavn_gyldig"),
        CheckConstraint(_i_liste("status", ADGANG_STATUSSER), name="status_gyldig"),
        UniqueConstraint("client_id", "systemnavn", name="uq_credentials_client_system"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    client_id: Mapped[int] = mapped_column(
        ForeignKey("clients.id", ondelete="CASCADE"), index=True
    )
    systemnavn: Mapped[str] = mapped_column(String(20))
    # deferred: kolonnen hentes først fra databasen, når den faktisk skal bruges.
    token_krypteret: Mapped[str] = mapped_column(Text, nullable=False, deferred=True)
    organisation_id: Mapped[str | None] = mapped_column(String(64))
    sidst_fornyet: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), server_default="aktiv")

    client: Mapped[Client] = relationship(back_populates="credentials")

    def saet_token(self, klartekst: str) -> None:
        self.token_krypteret = krypter_token(klartekst)

    def hent_token(self) -> str:
        return dekrypter_token(self.token_krypteret)

    def __repr__(self) -> str:
        # Tokenet (heller ikke det krypterede) vises aldrig.
        return (
            f"<Credential id={self.id} client_id={self.client_id} "
            f"systemnavn={self.systemnavn!r} token=****>"
        )

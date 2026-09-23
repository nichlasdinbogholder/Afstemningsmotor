"""Kundekartotek, kontaktpersoner og adgange.

Kundedata ligger her – adskilt fra afstemningslogikken – så CRM-delen kan
bygges ovenpå. Kunder slettes ikke; de sættes til status 'opsagt'.
"""

from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    false,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base, kun_vaerdier
from app.sikkerhed.kryptering import krypter_token

SYSTEMER = ("economic", "dinero")
KUNDE_STATUSSER = ("aktiv", "pause", "opsagt")
OPGAVE_FREKVENSER = ("maanedligt", "kvartalsvis", "aarligt")
MOMSPERIODER = ("maaned", "kvartal", "halvaar")
AFTALETYPER = ("fast_pris", "timer")
ADGANG_STATUSSER = ("aktiv", "udloebet", "tilbagekaldt")


class Client(Base):
    __tablename__ = "clients"
    __table_args__ = (
        kun_vaerdier("regnskabssystem", SYSTEMER),
        kun_vaerdier("status", KUNDE_STATUSSER),
        kun_vaerdier("opgave_frekvens", OPGAVE_FREKVENSER),
        kun_vaerdier("momsperiode", MOMSPERIODER),
        kun_vaerdier("aftaletype", AFTALETYPER),
        CheckConstraint("cvr ~ '^[0-9]{8}$'", name="cvr_8_cifre"),
        # Regnskabsårets slutning som dag-måned, fx '31-12' eller '30-06'.
        CheckConstraint(
            "regnskabsaar_slut ~ '^(0[1-9]|[12][0-9]|3[01])-(0[1-9]|1[0-2])$'",
            name="regnskabsaar_slut_dd_mm",
        ),
        CheckConstraint("antal_ansatte >= 0", name="antal_ansatte_ikke_negativ"),
        CheckConstraint("loenkoersel_dag BETWEEN 1 AND 31", name="loenkoersel_dag_1_31"),
        CheckConstraint(
            "opsagt_dato IS NULL OR startdato IS NULL OR opsagt_dato >= startdato",
            name="opsagt_efter_start",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    navn: Mapped[str] = mapped_column(String(255))
    cvr: Mapped[str | None] = mapped_column(String(8), unique=True)
    kundenummer: Mapped[str] = mapped_column(String(50), unique=True)
    adresse: Mapped[str | None] = mapped_column(String(255))
    postnr: Mapped[str | None] = mapped_column(String(10))
    by: Mapped[str | None] = mapped_column(String(100))
    virksomhedsform: Mapped[str | None] = mapped_column(String(50))
    branche: Mapped[str | None] = mapped_column(String(255))
    regnskabsaar_slut: Mapped[str | None] = mapped_column(String(5))
    antal_ansatte: Mapped[int | None] = mapped_column(Integer)
    opgave_frekvens: Mapped[str | None] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20), server_default="aktiv")
    startdato: Mapped[date | None] = mapped_column(Date)
    opsagt_dato: Mapped[date | None] = mapped_column(Date)
    ansvarlig_medarbejder_id: Mapped[int | None] = mapped_column(
        ForeignKey("staff.id", ondelete="SET NULL"), index=True
    )
    daglig_medarbejder_id: Mapped[int | None] = mapped_column(
        ForeignKey("staff.id", ondelete="SET NULL"), index=True
    )
    regnskabssystem: Mapped[str | None] = mapped_column(String(20))
    aftalenummer: Mapped[str | None] = mapped_column(String(50))
    kassekladde_navn: Mapped[str | None] = mapped_column(String(255))
    momsperiode: Mapped[str | None] = mapped_column(String(20))
    loensystem: Mapped[str | None] = mapped_column(String(100))
    loenkoersel_dag: Mapped[int | None] = mapped_column(SmallInteger)
    revisor_firma: Mapped[str | None] = mapped_column(String(255))
    revisor_kontakt: Mapped[str | None] = mapped_column(String(255))
    revisor_telefon: Mapped[str | None] = mapped_column(String(50))
    revisor_email: Mapped[str | None] = mapped_column(String(255))
    # Beskrivelse af, hvordan kunden håndterer det (fri tekst).
    sagsstyring: Mapped[str | None] = mapped_column(Text)
    betalinger: Mapped[str | None] = mapped_column(Text)
    bilagshaandtering: Mapped[str | None] = mapped_column(Text)
    debitorstyring: Mapped[str | None] = mapped_column(Text)
    eboks_adgang: Mapped[bool] = mapped_column(Boolean, server_default=false())
    pleo: Mapped[bool] = mapped_column(Boolean, server_default=false())
    aftaletype: Mapped[str | None] = mapped_column(String(20))
    oprettet: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    # Opdateres automatisk af en trigger i databasen ved hver ændring.
    opdateret: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    credentials: Mapped[list["Credential"]] = relationship(back_populates="client")
    contacts: Mapped[list["Contact"]] = relationship(back_populates="client")

    def __repr__(self) -> str:
        return f"<Client id={self.id} kundenummer={self.kundenummer!r} navn={self.navn!r}>"


class Contact(Base):
    __tablename__ = "contacts"
    __table_args__ = (
        # Højst én primær kontaktperson pr. kunde.
        Index(
            "uq_contacts_en_primaer_pr_kunde",
            "client_id",
            unique=True,
            postgresql_where=text("primaer"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    client_id: Mapped[int] = mapped_column(
        ForeignKey("clients.id", ondelete="CASCADE"), index=True
    )
    navn: Mapped[str] = mapped_column(String(255))
    rolle: Mapped[str | None] = mapped_column(String(100))
    telefon: Mapped[str | None] = mapped_column(String(50))
    email: Mapped[str | None] = mapped_column(String(255))
    primaer: Mapped[bool] = mapped_column(Boolean, server_default=false())

    client: Mapped[Client] = relationship(back_populates="contacts")


class Credential(Base):
    """En kundes adgang til e-conomic eller Dinero.

    Tokenet gemmes kun krypteret: brug `saet_token()`. Modellen kan IKKE
    dekryptere – det sker kun i adapter-laget via
    `app.adaptere.adgang.hent_adgang()`. Tokenet må aldrig logges.

    Sikkerhedsnet i databasen: triggeren `credentials_kraev_krypteret_token`
    (se første migrering) afviser alt, der ikke er krypteret med Fernet.
    Den er en trigger og ikke en CHECK-regel, fordi PostgreSQL ved brud på en
    CHECK-regel skriver hele rækken ind i fejlbeskeden – og så ville et token
    i klartekst kunne havne i en log.
    """

    __tablename__ = "credentials"
    __table_args__ = (
        kun_vaerdier("system", SYSTEMER),
        kun_vaerdier("status", ADGANG_STATUSSER),
        UniqueConstraint("client_id", "system", name="uq_credentials_client_system"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    client_id: Mapped[int] = mapped_column(
        ForeignKey("clients.id", ondelete="CASCADE"), index=True
    )
    system: Mapped[str] = mapped_column(String(20))
    # deferred: kolonnen hentes først fra databasen, når den faktisk skal bruges.
    token_krypteret: Mapped[str] = mapped_column(Text, nullable=False, deferred=True)
    aftale_id: Mapped[str | None] = mapped_column(String(64))
    organisation_id: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20), server_default="aktiv")
    sidst_fornyet: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    oprettet: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    client: Mapped[Client] = relationship(back_populates="credentials")

    def saet_token(self, klartekst: str) -> None:
        self.token_krypteret = krypter_token(klartekst)

    def __repr__(self) -> str:
        # Tokenet (heller ikke det krypterede) vises aldrig.
        return (
            f"<Credential id={self.id} client_id={self.client_id} "
            f"system={self.system!r} token=****>"
        )

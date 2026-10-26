"""Kundekartotek, kontaktpersoner og adgange.

Kundedata ligger her – adskilt fra afstemningslogikken – så CRM-delen kan
bygges ovenpå. Kunder slettes ikke; de sættes til status 'opsagt'.
"""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    false,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base, kun_vaerdier
from app.sikkerhed.kryptering import krypter_token

SYSTEMER = ("economic", "dinero")
# Livscyklus (bygherren 26.10.2026): 'emne' = endnu ikke kunde; 'aktiv'/'pause' = kunde; 'opsagt' = ophørt (arkiveret).
# Al hentning, rykkere, timeregistrering og Svend bruger kun 'aktiv' – så et emne kommer aldrig med.
KUNDE_STATUSSER = ("emne", "aktiv", "pause", "opsagt")
# Årsager til at arkivere en kunde (bygherren 23.10.2026) – vælges, når en administrator arkiverer.
ARKIV_AARSAGER = ("pris_for_hoej", "engangsopgave", "uoverensstemmelse", "egen_bogholder")
ADGANG_STATUSSER = ("aktiv", "udloebet", "tilbagekaldt")


class Client(Base):
    __tablename__ = "clients"
    __table_args__ = (
        kun_vaerdier("regnskabssystem", SYSTEMER),
        kun_vaerdier("status", KUNDE_STATUSSER),
        kun_vaerdier("arkiv_aarsag", ARKIV_AARSAGER),
        # En årsag giver kun mening på en arkiveret (opsagt) kunde.
        CheckConstraint("arkiv_aarsag IS NULL OR status = 'opsagt'", name="arkiv_aarsag_kun_paa_opsagt"),
        CheckConstraint("cvr ~ '^[0-9]{8}$'", name="cvr_8_cifre"),
        CheckConstraint("antal_ansatte >= 0", name="antal_ansatte_ikke_negativ"),
        kun_vaerdier("payment_allocation_order", ("costs_first", "principal_first")),
        kun_vaerdier("dunning_mode", ("off", "preview")),
        CheckConstraint("dunning_compensation_step BETWEEN 1 AND 3", name="kompensation_paa_rykker_1_3"),
        CheckConstraint("reminder_after_days IS NULL OR reminder_after_days BETWEEN 1 AND 60",
                        name="paamindelse_1_60_dage"),
        CheckConstraint("reply_to_email IS NULL OR reply_to_email ~ '^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$'",
                        name="svar_email_gyldig"),
        CheckConstraint("dunning_first_after_days >= 1", name="foerste_rykker_efter_forfald"),
        CheckConstraint("dunning_interval_days >= 10", name="rykkerinterval_mindst_10"),
        CheckConstraint("dunning_min_amount >= 0", name="rykker_minimum_ikke_negativ"),
        CheckConstraint("collection_min_amount >= 0", name="inkasso_minimum_ikke_negativ"),
        CheckConstraint("fi_kreditornummer IS NULL OR fi_kreditornummer ~ '^[0-9]{8}$'",
                        name="fi_kreditornummer_8_cifre"),
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
    antal_ansatte: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), server_default="aktiv")
    startdato: Mapped[date | None] = mapped_column(Date)
    opsagt_dato: Mapped[date | None] = mapped_column(Date)
    # Arkivering (siden Kunde → Kundeoversigt, kun administratorer): årsag og hvem. Datoen er opsagt_dato.
    arkiv_aarsag: Mapped[str | None] = mapped_column(String(30))
    arkiveret_af: Mapped[int | None] = mapped_column(ForeignKey("staff.id", ondelete="SET NULL"), index=True)
    # Intern kunde (fx Din Bogholder selv): KUN administratorer kan spørge Svend om den og se samtalerne om den
    # (bygherren 26.10.2026) – også selv om en medarbejder står som ansvarlig.
    kun_admin: Mapped[bool] = mapped_column(Boolean, server_default=false())
    # Kundens to roller (bygherren 26.10.2026) – defineret ét sted: app/kunder/roller.py.
    # ANSVARLIG har det overordnede ansvar (ændringer skrives i kunde_owner_history af en trigger);
    # ASSISTENT laver det daglige arbejde og overtager kunden, når den ansvarlige er fraværende.
    ansvarlig_medarbejder_id: Mapped[int | None] = mapped_column(
        ForeignKey("staff.id", ondelete="SET NULL"), index=True
    )
    assistent_id: Mapped[int | None] = mapped_column(ForeignKey("staff.id", ondelete="SET NULL"), index=True)
    # Kundegruppe: holding, driftsselskab og personlig virksomhed, der hænger sammen.
    group_id: Mapped[int | None] = mapped_column(ForeignKey("kunde_groups.id", ondelete="RESTRICT"), index=True)
    regnskabssystem: Mapped[str | None] = mapped_column(String(20))
    aftalenummer: Mapped[str | None] = mapped_column(String(50))
    kassekladde_navn: Mapped[str | None] = mapped_column(String(255))
    # Manglende kontoudtog fra grossister meldes fra denne måned (tom = måneden, kunden blev oprettet).
    kontoudtog_fra: Mapped[date | None] = mapped_column(Date)
    # --- Opkrævning (app/opkraevning) ---
    # Rykkere: 'off' (standard – intet hentes eller sendes), 'preview' (fakturaer hentes og
    # dunning-preview kan vise, hva der VILLE blive sendt – intet sendes). 'live' tilføjes først,
    # når udsendelsen (trin 4) er bygget.
    dunning_mode: Mapped[str] = mapped_column(String(10), server_default="off")
    # Første rykker tidligst N dage efter forfald; derefter mindst M dage mellem rykkerne (loven: >= 10).
    dunning_first_after_days: Mapped[int] = mapped_column(SmallInteger, server_default=text("10"))
    dunning_interval_days: Mapped[int] = mapped_column(SmallInteger, server_default=text("10"))
    # Venlig påmindelse (uden gebyr) N dage efter forfald; rykker 1 kommer dunning_interval_days efter den.
    # NULL = ingen påmindelse – så kommer rykker 1 dunning_first_after_days efter forfald.
    reminder_after_days: Mapped[int | None] = mapped_column(SmallInteger, server_default=text("5"))
    # Kompensationsbeløbet (310 kr., kun erhverv, én gang) lægges på denne rykker. FarPay: rykker 3.
    dunning_compensation_step: Mapped[int] = mapped_column(SmallInteger, server_default=text("3"))
    # Svar på en rykker går hertil (Reply-To) – KUNDENS egen adresse, aldrig rykker@dinbogholder.dk.
    reply_to_email: Mapped[str | None] = mapped_column(String(255))
    # Rækkefølge, når en indbetaling fordeles: gebyrer/renter først eller hovedstolen først.
    payment_allocation_order: Mapped[str] = mapped_column(String(20), server_default="costs_first")
    fee_assignment_signed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    dunning_min_amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), server_default=text("100"))
    collection_mandate_signed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    auto_escalate_to_collection: Mapped[bool] = mapped_column(Boolean, server_default=false())
    collection_min_amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), server_default=text("500"))
    # Kundens FI-kreditornummer (de 8 cifre efter + i FIK-linjen) – kobler en indbetaling til kunden.
    fi_kreditornummer: Mapped[str | None] = mapped_column(String(8), unique=True)
    # Debitorgrupper i kundens e-conomic, der er erhverv (Connect El: {2}). Andre = privat.
    business_customer_groups: Mapped[list[int]] = mapped_column(ARRAY(Integer), server_default=text("'{}'"))
    # Konti i KUNDENS kontoplan, hvor bankafstemningen bogfører indbetalte gebyrer og renter.
    fee_income_account: Mapped[int | None] = mapped_column(Integer)
    interest_income_account: Mapped[int | None] = mapped_column(Integer)
    loensystem: Mapped[str | None] = mapped_column(String(100))
    revisor_firma: Mapped[str | None] = mapped_column(String(255))
    revisor_kontakt: Mapped[str | None] = mapped_column(String(255))
    revisor_telefon: Mapped[str | None] = mapped_column(String(50))
    revisor_email: Mapped[str | None] = mapped_column(String(255))
    sagsstyring: Mapped[bool] = mapped_column(Boolean, server_default=false())
    betalinger: Mapped[bool] = mapped_column(Boolean, server_default=false())
    bilagshaandtering: Mapped[bool] = mapped_column(Boolean, server_default=false())
    debitorstyring: Mapped[bool] = mapped_column(Boolean, server_default=false())
    eboks_adgang: Mapped[bool] = mapped_column(Boolean, server_default=false())
    pleo: Mapped[bool] = mapped_column(Boolean, server_default=false())
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


KONTAKTES_AF = ("alle", "ansvarlig")


class Contact(Base):
    """Kontaktpersoner. Slettes aldrig – arkiveres (arkiveret sat). Underskriver og "må kontaktes af" er to
    uafhængige felter."""

    __tablename__ = "contacts"
    __table_args__ = (
        # Højst én primær kontaktperson pr. kunde blandt dem, der ikke er arkiveret.
        Index(
            "uq_contacts_en_primaer_pr_kunde",
            "client_id",
            unique=True,
            postgresql_where=text("primaer AND arkiveret IS NULL"),
        ),
        kun_vaerdier("maa_kontaktes_af", KONTAKTES_AF),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    client_id: Mapped[int] = mapped_column(
        ForeignKey("clients.id", ondelete="RESTRICT"), index=True
    )
    navn: Mapped[str] = mapped_column(String(255))
    rolle: Mapped[str | None] = mapped_column(String(100))
    telefon: Mapped[str | None] = mapped_column(String(50))
    email: Mapped[str | None] = mapped_column(String(255))
    primaer: Mapped[bool] = mapped_column(Boolean, server_default=false())
    underskriver: Mapped[bool] = mapped_column(Boolean, server_default=false())
    maa_kontaktes_af: Mapped[str] = mapped_column(String(10), server_default="alle")
    oprettet: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    arkiveret: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

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
        # Højst ét gældende token pr. regnskabsaftale. Et token udskiftes ved at tilbagekalde det gamle
        # (revoked_at) og indsætte et nyt – det krypterede felt kan aldrig ændres (trigger kunde_credential_guard).
        Index("uq_credentials_et_aktivt_pr_aftale", "ledger_id", unique=True,
              postgresql_where=text("revoked_at IS NULL")),
        CheckConstraint("(revoked_at IS NULL) = (status <> 'tilbagekaldt')", name="tilbagekaldt_har_tidspunkt"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    client_id: Mapped[int] = mapped_column(
        ForeignKey("clients.id", ondelete="RESTRICT"), index=True
    )
    # Regnskabsaftalen, tokenet hører til (én kunde kan have flere).
    ledger_id: Mapped[int] = mapped_column(ForeignKey("kunde_ledgers.id", ondelete="RESTRICT"), index=True)
    system: Mapped[str] = mapped_column(String(20))
    # deferred: kolonnen hentes først fra databasen, når den faktisk skal bruges.
    token_krypteret: Mapped[str] = mapped_column(Text, nullable=False, deferred=True)
    aftale_id: Mapped[str | None] = mapped_column(String(64))
    organisation_id: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20), server_default="aktiv")
    sidst_fornyet: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Til visning: de sidste fire tegn af tokenet (bygherren 26.10.2026: bevidst). Tom for tokens fra før.
    fingerprint: Mapped[str | None] = mapped_column(String(4))
    set_by: Mapped[int | None] = mapped_column(ForeignKey("staff.id", ondelete="RESTRICT"), index=True)
    set_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    oprettet: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    client: Mapped[Client] = relationship(back_populates="credentials")

    def saet_token(self, klartekst: str) -> None:
        """Kun på en NY række – et gemt token kan ikke ændres (udskift = tilbagekald + ny række)."""
        self.token_krypteret = krypter_token(klartekst)
        self.fingerprint = klartekst[-4:]

    def __repr__(self) -> str:
        # Tokenet (heller ikke det krypterede) vises aldrig.
        return (
            f"<Credential id={self.id} client_id={self.client_id} "
            f"system={self.system!r} token=****>"
        )

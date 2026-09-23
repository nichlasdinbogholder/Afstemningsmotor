# Afstemningsmotor

## Hvad er projektet
En **intern afstemningsmotor** til bogholderiet, der skal håndtere afstemning for
**66 regnskabskunder**. Motoren henter data fra kundernes regnskabssystemer,
sammenligner poster og viser, hvad der stemmer, og hvad der ikke gør.

Projektet skal på sigt udvides til et **internt CRM** (kundeoverblik, opgaver,
kontaktoplysninger m.m.). Hold derfor kundedata og afstemningslogik adskilt, så
CRM-delen kan bygges ovenpå uden at rive noget ned.

## Teknologi
- **Sprog:** Python
- **Web/API:** FastAPI
- **Database:** PostgreSQL 16 (kører lokalt via `docker-compose.yml`)
- **Integrationer:**
  - **e-conomic** – adgang via én token pr. kunde
  - **Dinero** – adgang via én token pr. kunde
- Konfiguration læses fra miljøvariabler (`.env`). Skabelon: `.env.example`.

## Kom i gang lokalt
```bash
cp .env.example .env          # udfyld værdier lokalt – aldrig i git
docker compose up -d          # starter PostgreSQL med vedvarende data
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m app.sikkerhed.ny_noegle --gem   # egen hovednøgle i .env
.venv/bin/alembic upgrade head   # bygger/opdaterer databasens tabeller
```

## Struktur
- `app/config.py` – indstillinger fra `.env` (hemmeligheder som `SecretStr`).
- `app/db.py` – databaseforbindelse (`hide_parameters=True`, så værdier ikke logges).
- `app/models.py` – samler alle tabeller (bruges af Alembic og tests).
- `app/personale/models.py` – medarbejdere (`staff`).
- `app/kunder/models.py` – kundekartotek (`clients`), kontaktpersoner (`contacts`)
  og adgange (`credentials`). Adskilt fra kommende afstemningslogik.
- `app/crm/models.py` – `tasks`, `time_entries`, `notes`, `documents`, `handovers`.
- `app/audit/models.py` – `audit_log` (kan kun tilføjes til; må aldrig
  indeholde tokens eller andre hemmeligheder).
- `app/sikkerhed/kryptering.py` – kryptering af tokens med Fernet og hovednøglen
  `CREDENTIALS_KEY`. Indeholder det ENESTE sted, der dekrypterer:
  `dekrypter_token_til_adapter()`.
- `app/sikkerhed/hemmeligheder.py` – `HemmeligtToken` (vises altid maskeret) og
  filter, der skjuler kendte tokens i logs og fejludskrifter.
- `app/sikkerhed/ny_noegle.py` – kommando til ny hovednøgle
  (`python -m app.sikkerhed.ny_noegle [--gem]`).
- `app/adaptere/` – adapter-laget til e-conomic/Dinero. Henter adgang via
  `hent_adgang()`, som er den eneste, der kalder dekrypteringen.
- `tests/` – kør med `.venv/bin/pytest` (kræver kørende database).
- `migrations/` – Alembic-migreringer (ændringer af databasens opbygning).

## Databaseændringer (Alembic)
- Ret modellerne i `app/`, og lav så en migrering:
  `.venv/bin/alembic revision --autogenerate -m "kort beskrivelse"`
- Gennemlæs altid den genererede fil i `migrations/versions/` før den køres.
- Kør: `.venv/bin/alembic upgrade head`. Fortryd seneste: `.venv/bin/alembic downgrade -1`.
- Ændr aldrig en migrering, der allerede er kørt i en delt database – lav en ny.
- Alembic opdager IKKE omdøbte kolonner (den laver slet + tilføj, og data går
  tabt) og heller ikke ændrede CHECK-regler. Ret dem i hånden med
  `op.alter_column(..., new_column_name=...)` og `op.create_check_constraint`.
- Databasens krav (tjekkes af `tests/test_skema.py`):
  - Alle fremmednøgler er rigtige relationer og har et indeks.
  - Alle tabeller med `client_id` har indeks på den.
  - Status- og valgfelter er låst med CHECK-regler (faste værdier uden æøå,
    fx `aaben`, `loest`, `maaned`), aldrig fri tekst.
  - Kunder og medarbejdere slettes ikke (historik blokerer); brug
    `status='opsagt'` hhv. `aktiv=false`.
- Tokens gemmes kun via `Credential.saet_token()` og læses kun i adapter-laget
  via `app.adaptere.adgang.hent_adgang()`. En trigger i databasen afviser
  ukrypterede tokens uden at gentage værdien i fejlbeskeden.

## Regler for tokens i koden
- Kald aldrig `.decrypt(` eller `dekrypter_token_til_adapter()` uden for
  `app/sikkerhed/kryptering.py` og `app/adaptere/` – testene fejler, hvis det sker.
- Brug `HemmeligtToken.klartekst()` kun direkte i kaldet til e-conomic/Dinero,
  aldrig i log-, fejl- eller print-sætninger.
- Skift aldrig `CREDENTIALS_KEY` uden først at have omkrypteret alle tokens.

## FASTE REGLER (skal altid overholdes)

### 1. Tokens må aldrig havne i kode eller logs
- Tokens, API-nøgler, adgangskoder og andre hemmeligheder må **aldrig** skrives
  direkte i kildekoden, i tests, i eksempler, i commits eller i dokumentation.
- Hemmeligheder må **aldrig** skrives til logs, fejlbeskeder, stack traces,
  debug-udskrifter eller svar fra API'et. Maskér dem altid (fx `****abcd`).
- Kundernes tokens gemmes **krypteret** i databasen og læses kun, når de bruges.
- Fælles hemmeligheder ligger kun i `.env`, som er udelukket i `.gitignore`.
- `.env.example` må kun indeholde pladsholdere – aldrig rigtige værdier.

### 2. Intet bogføres i e-conomic uden forudgående dry-run
- Enhver handling, der skriver til e-conomic (bogføring, kladder, posteringer
  m.m.), skal **først køres som dry-run**, der viser præcis, hvad der ville ske,
  uden at ændre noget.
- Rigtig bogføring må kun ske efter, at dry-run-resultatet er gennemgået og
  udtrykkeligt godkendt.
- Standarden er altid dry-run. Rigtig bogføring kræver et bevidst valg
  (fx `ALLOW_BOOKING=true` **og** en eksplicit bekræftelse).
- Samme princip bør følges for Dinero.

### 3. Sprog og forklaringer
- **Alle svar til brugeren skal være på dansk.**
- Forklar tingene i **almindeligt dansk uden fagsprog**. Hvis et teknisk ord er
  uundgåeligt, så forklar kort, hvad det betyder.

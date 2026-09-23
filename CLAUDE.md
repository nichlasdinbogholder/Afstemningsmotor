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
```

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

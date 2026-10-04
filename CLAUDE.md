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
- `app/kunder/adgange.py` – `gem_token(session, client_id, system, token)`:
  gemmer/udskifter en kundes token (krypteres straks, logges i audit_log uden tokenet).
- `app/kunder/gem_token.py` – kommando til at lægge et token ind:
  `python -m app.kunder.gem_token --kundenummer <nr> --system economic|dinero`.
  Tokenet indtastes skjult – aldrig som argument på kommandolinjen.
- `app/adaptere/` – adapter-laget til e-conomic/Dinero. `hent_adgang()` og
  `hent_token(session, client_id, system)` er de eneste, der kalder dekrypteringen.
- `app/adaptere/regnskab/base.py` – det fælles interface `AccountingProvider` med
  `fetch_accounts`, `fetch_customers`, `fetch_suppliers`, `fetch_entries(efter)`,
  `fetch_open_entries`, samt `fetch_journals`/`fetch_journal_entries(nr)` (kassekladder).
  Fælles format: Konto, Kunde, Leverandoer, Postering, AabenPost, Kassekladde, KladdePost.
  Hent en provider med `hent_adapter(session, client_id)`. Resten af systemet må ALDRIG
  importere `app.adaptere.economic`, kalde et system direkte eller nævne et systemnavn
  i app/synk, app/afstemning, app/jobs, app/regnskab, app/planlaegning (tests håndhæver det).
  Ny adapter: nyt modul + `@registrer_adapter("<system>")` + `ADAPTER_MODULER`.
  Adaptere gætter aldrig: manglende felter bliver None; manglende id'er/ukendte typer giver fejl.
  Rate limit rejses som `ForMangeKald` (ikke en fejl – job udskydes).
- DINERO: endnu IKKE bygget – Dineros datamodel passer ikke ind i interfacet (id'er er
  GUID'er, ikke tal; ingen liste over købsbilag/kreditorposter; posttyper og
  betalingsbetingelser har andre begreber). Afventer beslutning om ændring af interfacet.
- `app/adaptere/economic/adapter.py` – e-conomic-adapteren (REST: /customers,
  /suppliers, /accounting-years/{år}/entries; entries inkrementelt med
  `entryNumber$gt:<cursor>`, åbne poster fuldt med `remainder$ne:0`).
- `app/adaptere/economic/klient.py` – læse-klient til e-conomics REST API
  (httpx + tenacity: 5 forsøg med eksponentiel ventetid ved 429/5xx/netværksfejl).
  Følger `pagination.nextPage` og nægter at sende nøgler til andre værter.
- `app/synk/kontoplan.py` – henter kontoplanen via `fetch_accounts` for én kunde
  (`--kundenummer <nr>` / `--kunde <id>`) eller `--alle` AKTIVE kunder med aktiv adgang
  (aldrig opsagt/pause); én kundes fejl stopper ikke resten. Registreres i sync_state
  som `accounts`. (`app/adaptere/economic/kontoplan.py` er kun en gammel genvej hertil.)
- `app/planlaegning/natlig_kontoplan.py` – tidsplan på Mac (launchd): kører
  `python -m app.synk.kontoplan --alle` ÉN gang i døgnet (standard kl. 12:30). Log: `logs/kontoplan.log`.
  `python -m app.planlaegning.natlig_kontoplan installer|status|koer-nu|afinstaller`.
- `app/jobs/` – jobkø i databasen (tabel `jobs`, status `koe`/`i_gang`/`faerdig`/`fejlet`).
  - `register.py`: jobtyper registreres med `@jobtype("navn")`; nye moduler
    med jobtyper tilføjes i `JOBTYPE_MODULER`. Jobfunktioner skal tåle at køre
    mere end én gang, og deres DB-skrivninger (via `job.session`) gemmes samlet
    med status `faerdig`.
  - `koe.py`: `laeg_i_koe(session, type, client_id=, payload=, idempotens_noegle=)`
    samt kommandoerne `vis`, `genkoer` og `tilfoej`. Payload må aldrig indeholde
    hemmeligheder.
  - `worker.py`: `python -m app.jobs.worker` – henter med `FOR UPDATE SKIP LOCKED`,
    prøver igen med fordoblet ventetid (30 s … 1 t), frigiver job i gang > 15 min.
- `app/synk/` – synkroniseringstilstand (tabel `sync_state`, én række pr. kunde pr.
  ressource; visningen `synk_kraever_handling` viser kunder, der er bagud/fejler).
  - REGEL: al hentning med bogmærke (cursor) skal ske i `synk_transaktion(...)`;
    kald `synk.gennemfoert(ny_cursor, type, antal_hentet=...)` efter data er skrevet.
    Data og bogmærke gemmes samlet – aldrig bogmærket alene.
  - Kun kunder med status `aktiv` synkroniseres (aldrig `opsagt` eller `pause`).
  - Efter 5 fejl i træk: status `fejlet`. Ventetid 5 min, 10 min, … højst 24 t.
  - `python -m app.synk.kommando oversigt|status|nulstil|deaktiver|aktiver`.
- `app/cli.py` – `python -m app.cli sync-entries <client_id>`: lægger et `synk_entries`-job
  i køen, kører det straks og viser hentet/nye/opdaterede og ny cursor.
- `RESUME.md` – valg af bogmærke for entries (højeste entryNumber) og hvorfor.
- entries: `beloeb`/`beloeb_dkk` NUMERIC, `raa_data` JSONB (e-conomics rå svar – aldrig
  tokens). Stopper hentningen midtvejs, gemmes det hentede (`DelvisHentet`), og bogmærket
  flyttes kun, hvis det er sikkert (sidste regnskabsår + stigende entryNumber).
- Kun GET mod e-conomic: `tests/test_entries_economic.py` fejler ved post/put/patch/delete.
- `app/synk/ressourcer.py` – `synk_customers/suppliers/entries/open_entries(session,
  client_id)`: cursor -> adapter -> upsert i cache-tabel -> ny cursor, i ÉN transaktion.
  Åbne poster altid fuldt (betalte fjernes); entries inkrementelt.
- `app/synk/jobs.py` – jobtyperne `synk_<ressource>`; rate limit -> `UdskydJob`.
- `app/synk/planlaegger.py` – lægger dagens job i kø for aktive kunder, jævnt
  fordelt over døgnet; idempotensnøgle `<ressource>:<client_id>:<dato>`.
  Køres automatisk af `python -m app.jobs.worker --planlaeg`.
- `app/synk/koer.py` – manuel synkronisering af én kunde.
- `app/synk/bekraeft.py` – bekræft mod det rigtige system: synkroniserer, henter igen
  og sammenligner, tjekker dubletter og bogmærke. Slutter med BEKRÆFTET/IKKE BEKRÆFTET:
  `python -m app.synk.bekraeft --kundenummer <nr>`.
- `app/afstemning/` – afstemningslogik (adskilt fra kundedata/CRM).
  - `fejlkonto.py`: tjek kassekladde for posteringer på fejlkonto (standard 9900),
    som konto ELLER modkonto. Kladde: `--kladde`, ellers `clients.kassekladde_navn`,
    ellers alle. Læser kun. Kode 0 = ingen, 1 = fund, 2 = fejl:
    `python -m app.afstemning.fejlkonto --kundenummer <nr> [--konto 9900]`.
  - `regler.py` + tabellen `aabne_post_fund` (hed tidligere findings): afstemningsregler som SQL-funktioner i databasen
    (`regel_1_smaa_restbeloeb`, `regel_2_betaling_uden_faktura`,
    `regel_3_forfalden_over_6_mdr`). Idempotente via unik (client_id, regel, kilde_id).
    Kun aktive kunder. `python -m app.afstemning.regler --kundenummer <nr> | --alle`.
    Hver regel = `regel_N_kandidater(...)` (betingelsen) + `regel_N_...(...)` (skriver fund).
    `luk_loeste_fund(...)` lukker åbne fund, der ikke længere er kandidater (status
    `loest` + tidspunkt + årsag); løste fund genåbnes, hvis de igen opfylder reglen.
    `afvist` røres aldrig automatisk. Kommandoen lukker først og kører så reglerne.
    Nye regler/ændringer: ny migrering; husk både kandidat-funktionen og luk_loeste_fund.
  - Adapter-laget har dertil `fetch_journals()` og `fetch_journal_entries(nr)`
    (e-conomic: /journals og /journals/{nr}/entries).
- `app/rules/` – NYE afstemningsregler (dubletter m.m.). Læser KUN vores egen database –
  må aldrig importere app.adaptere/app.synk eller tale med e-conomic/Dinero (tests håndhæver det).
  - `base.py`: `FindingDraft`, `Rule`, `fingerprint()`, `@registrer_regel`, `REGEL_MODULER`.
    Ny regel = én fil i app/rules/ + én linje i `REGEL_MODULER`.
  - `koersel.py`: `koer_regler(session, client_id)` – upsert på (client_id, rule_code, fingerprint);
    opdaterer kun last_seen_at/detail/severity/updated_at. Status røres ALDRIG af en kørsel.
  - `status.py`: `saet_status(...)` – ENESTE sted, der ændrer status. Trigger skriver finding_events
    og afviser statusændring uden actor. findings/finding_events kan ikke slettes.
  - `duplicate_entries.py`: dubletregel v5 (self join, samme konto og tekst, `VINDUE_DAGE = 3`, forskellig
    kunde/leverandør på bilaget og tilbageførte beløb udelukkes, ét fund pr. bilagspar).
    `scripts/maal_dubletfund.sql` måler, hvorfor fundene opstår (kun læsning).
    `rule_runs` logger hver kørsel; `findings` viser kun aktuelle fund (`--alle` viser også gamle). `jobs.py`: jobtype `run_rules`,
    planlægges kl. 23:30 for aktive kunder af `python -m app.jobs.worker --planlaeg`.
  - CLI: `python -m app.cli run-rules <id>`, `findings <id> [--status] [--severity] [--alle]`,
    `set-status <fund-id> <accepted|resolved|ignored|open> --note "..."` (actor = $USER).
  - Den gamle tabel for de tre regler på åbne poster hedder nu `aabne_post_fund` (app/afstemning/).
- `app/regnskab/models.py` – regnskabsdata fra kundernes systemer (`accounts`
  med `tenant_id` = kunden; cache-tabellerne `customers`, `suppliers`, `entries`,
  `open_entries` med unik (client_id, systemets id)). Holdt adskilt fra CRM.
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
- Tilføjes et fejlrapporteringsværktøj (fx Sentry), skal indsamling af lokale
  variabler slås fra, og beskeder køres gennem `app.sikkerhed.hemmeligheder.rediger()`.
- e-conomic: `X-AppSecretToken` (fælles for vores app) ligger i `.env` som
  `ECONOMIC_APP_SECRET_TOKEN`; `X-AgreementGrantToken` (pr. kunde) ligger
  krypteret i `credentials`.

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

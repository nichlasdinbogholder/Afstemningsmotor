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
- `app/kunder/opret.py` – opret en kunde: `python -m app.kunder.opret --navn ... --kundenummer ... --cvr ... --system economic` (`--vis` viser alle).
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
- `app/synk/ressourcer.py` – `synk_accounting_years/customers/suppliers/entries/open_entries/journals(session,
  client_id)` (regnskabsår med `lukket`, kassekladdelinjer i `journal_entries` – begge altid fuldt): cursor -> adapter -> upsert i cache-tabel -> ny cursor, i ÉN transaktion.
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
  - `duplicate_entries.py`: dubletregel v11 (self join, samme konto, tekst og posteringstype; modkonto skal være bank;
    rettet senere og rettelsesbilag udelukkes, `VINDUE_DAGE = 3`, forskellig
    kunde/leverandør på bilaget og tilbageførte beløb udelukkes, ét fund pr. bilagspar; AFSLUTTEDE
    regnskabsår (`accounting_years.lukket`) og konti med "mellemregning" i navnet springes over).
    `scripts/maal_dubletfund.sql` måler, hvorfor fundene opstår (kun læsning).
    `rule_runs` logger hver kørsel; `findings` viser kun aktuelle fund (`--alle` viser også gamle). `jobs.py`: jobtype `run_rules`,
    planlægges kl. 23:30 for aktive kunder af `python -m app.jobs.worker --planlaeg`.
  - CLI: `python -m app.cli run-rules <id>`, `findings <id> [--status] [--severity] [--alle]`,
    `set-status <fund-id> <accepted|resolved|ignored|open> --note "..."` (actor = $USER).
  - Den gamle tabel for de tre regler på åbne poster hedder nu `aabne_post_fund` (app/afstemning/).
- `app/regnskab/models.py` – regnskabsdata fra kundernes systemer (`accounts`
  med `tenant_id` = kunden; cache-tabellerne `customers`, `suppliers`, `entries`,
  `open_entries` med unik (client_id, systemets id)). Holdt adskilt fra CRM.
- `app/kontoudtog/` – kontoudtog fra eksterne kilder: tabellerne `statements` (kilde grossist/skattekonto/
  bank/andet, modstykke = finanskonto og/eller `modpart` "kreditor:<nr>", periode, fortegn samme/modsat)
  og `statement_lines`. `importer.py`: indlæs PDF eller CSV (`python -m app.kontoudtog.importer …`).
  - `pdf.py`: aflæser grossisters PDF-udtog uden skabelon pr. grossist (kolonneoverskrift Beløb/Debet/
    Kredit/Saldo, linjer der starter med dato). Indscannede PDF'er læses med Tesseract (lokalt, dansk;
    installeres i Dockerfile). KONTROL: primo + linjer = ultimo på øret, ellers indlæses udtoget ikke.
    Prøv uden at gemme: `python -m app.kontoudtog.pdf <fil.pdf>`. Grossisten findes ud fra CVR i udtoget (`suppliers.cvr`),
    så `importer --kundenummer <nr> --fil <pdf>` er nok. Rigtige udtog må ALDRIG i repoet
    (kundedata) – tests bruger opdigtede PDF'er (fpdf2). Stark sender "åbne poster pr. dato" (ikke
    bevægelser) – læses, men poster uden for listen kan give fund "mangler på kontoudtog".
  - Skattekonto fra Revibot (CSV med "Søgning fra dato"): `laes_revibot()`; kunden findes via CVR, hver
    linjes saldo kontrolleres. `python -m app.kontoudtog.importer --konto <skattekonto> --fil <csv>`.
- `app/rules/kontoudtog.py` – MATCHMOTOREN (to regler): trin 1 reference = bilagsnummer ELLER
  entries.fakturanummer (leverandørens fakturanr.; foranstillede nuller ignoreres) + beløb,
  trin 2 beløb + dato ±5 dage (`DATO_TOLERANCE`), resten → findings `mangler_i_bogfoering` /
  `mangler_paa_kontoudtog`. Ligger en manglende linje i en ubogført kassekladde (`journal_entries`, på
  fakturanummer eller beløb ±5 dage), står det i fundets titel og `detail.kassekladde`. Én-til-én; match gemmes i `statement_lines.match_entry_id/match_trin`.
  Uafhængig af kilden. Læser posteringer med SQL (ikke EntryCache), så adapter-laget ikke trækkes med.
  `scripts/matchprocent.sql`: matchprocent pr. kunde.
- `app/rules/manglende_kontoudtog.py` – fund `mangler_kontoudtog`: leverandør i gruppe 20000 (grossister,
  `suppliers.gruppe`) med posteringer i en af de 3 seneste hele måneder, men intet kontoudtog for måneden.
  Først fra 10. hverdag i måneden efter (helligdage tæller ikke) og kun fra `clients.kontoudtog_fra`
  (tom = måneden, kunden blev oprettet). Sæt: `python -m app.kunder.opret --kundenummer <nr> --kontoudtog-fra ÅÅÅÅ-MM-DD`.
- `app/natkoersel/` – NATKØRSLEN (erstatter på serveren den gamle planlægger `worker --kun-planlaeg`):
  - `scheduler.py`: APScheduler i egen proces (`python -m app.natkoersel.scheduler`, compose-tjenesten
    `scheduler`). Kl. 05:00 dansk tid mandag–fredag: ét `natkoersel_kunde`-job pr. aktiv kunde med
    aktiv adgang, spredt over 2 timer, idempotensnøgle `nat:<kunde>:<dato>`, logges i audit_log
    (`natkoersel_planlagt`). `--koer-nu` planlægger med det samme.
  - `job.py`: jobtypen `natkoersel_kunde`: synk (kontoplan, kunder, leverandører, posteringer, åbne
    poster) → regelmotoren → kassekladdekontrollen (fund i findings som `fejlkonto_kassekladde`) →
    audit_log (`natkoersel_kunde`). Et trin der fejler stopper ikke de næste; jobbet fejler til sidst
    (prøves igen, Sentry med tags). ForMangeKald udskyder hele jobbet.
  - `status.py`: `python -m app.natkoersel.status [--dato]` – gik nattens kørsel godt? (kode 0/1/2/3).
- `app/api/main.py` – webdelen (FastAPI): `GET /health` (database + version, aldrig hemmeligheder,
  kræver aldrig login), forsiden `/`. `uvicorn app.api.main:app`.
- `app/api/login.py` – login med Microsoft (OIDC via Authlib, kun jeres tenant): `/login`, `/auth/callback`,
  `/logout`, `/mig`, `/admin/medarbejdere`. Kun AKTIVE medarbejdere i `staff` (e-mail) kommer ind;
  `microsoft_oid` bindes ved første login. Roller `admin`/`medarbejder`: brug `Depends(nuvaerende_medarbejder)`
  eller `Depends(kraev_admin)` på nye sider. Medarbejderen slås op ved HVER forespørgsel. Cookien
  (`afstemning_login`, underskrevet med SESSION_SECRET) indeholder kun medarbejder-id. Login/afvisning → audit_log.
  Slået fra uden MS_TENANT_ID/MS_CLIENT_ID/MS_CLIENT_SECRET/SESSION_SECRET. Opsætning: DEPLOY.md trin 8.
- `app/api/data.py` – data til webdelen under `/api` (login krævet): `kunder` (åbne aktuelle fund + seneste
  opdatering), `kunder/{id}`, `kunder/{id}/fund`, `fund/{id}`, `POST fund/{id}/status` (note krævet ved
  accepted/ignored; actor = medarbejderens e-mail), `kunder/{id}/kontoudtog`, `kontoudtog/{id}`,
  `POST kunder/{id}/opdater` (job `opdater_kunde`, prioritet 10) og `jobs/{id}`. POST kræver headeren
  `X-Afstemning: 1`. Læser KUN vores database – aldrig e-conomic direkte.
- `app/rules/visning.py` – aktuelle fund (`NOT EXISTS` nyere regelkørsel – bruger indekset), regelnavne.
- `web/` – WEBDELEN (Next.js 16, TypeScript, Tailwind). Bygges til statiske filer (`output: "export"`), som
  Caddy udleverer (`deploy/Dockerfile.caddy`); intet Node-program i drift. Henter kun fra `/api`
  (`web/lib/api.ts`, `send()` sætter X-Afstemning). Sider: `/` kundeoversigt, `/kunde/?id=` (fund med
  statusskift + historik, kontoudtog med linjer, "Opdater nu"). Lokalt: `npm run dev` + `DEV_LOGIN=true`
  på FastAPI og `/dev-login?email=` (virker aldrig med APP_ENV=production). Læs `web/AGENTS.md` før
  ændringer (Next.js 16 adskiller sig fra ældre versioner).
- Kapacitet (målt, DEPLOY.md): 500 kunder / 20 samtidige medarbejdere; 4 workers, 3 API-processer.
- `app/personale/bruger.py` – `python -m app.personale.bruger vis|opret|rolle|deaktiver|aktiver`.
- `app/opkraevning/` – OPKRÆVNING (erstatter FarPay). Debitor betaler KUNDENS konto; Din Bogholder fakturerer
  kunden månedligt for indbetalte gebyrer/renter. Vi modtager ALDRIG selv en betaling fra en debitor.
  - `models.py` (trin 1): debtors, invoices (kind invoice|credit_note – kreditnota = negativt beløb), invoice_payments,
    payment_allocations, deliveries, dunning_steps, dunning_skips, installment_plans/lines, reference_rates,
    collection_cases, fee_revenue, billing_periods. Beløb NUMERIC(15,2). Feltnavne fra rå e-conomic-JSON
    (`scripts/peek_opkraevning.py [--felter]`). clients har opkrævningsfelterne (payment_allocation_order,
    dunning_min_amount, fi_kreditornummer, business_customer_groups, fee_income_account, interest_income_account …).
  - Lovens grænser er i DATABASEN: CHECK gebyr <= 100 og step_no 1-3; triggeren `kontroller_rykker` afviser
    kreditnota, > 3 rykkere inkl. FarPays (`prior_dunning_count`), < 10 dage efter forrige (inkl.
    `prior_last_dunning_at`), kompensation uden erhverv og rykker ved aktiv inkassosag. Unik aktiv sag pr. faktura
    + unik idempotency_key. Gebyr på inkassosag kan ikke være fakturerbart (CHECK).
  - `lov.py` (trin 2): rentelovens grænser som konstanter + kontroller (MAKS_RYKKERGEBYR 100, MAKS_RYKKERE 3,
    MIN_DAGE_MELLEM_RYKKERE 10, KOMPENSATIONSBELOEB 310, INKASSO_KARENS_DAGE 10 – fast for ALLE kunder).
    `beregn_morarente(...)`: pr. dag (dagene EFTER forfald t.o.m. til-dato), faktiske dage/365, på udestående
    hovedstol, sats = referencesats for halvåret + 8. `referencesats()` rejser `ManglerReferencesats` – aldrig gæt.
  - `rykker.py`: `byg_rykker(session, faktura, dag)` (kø, gebyr 100, kompensation én gang til erhverv, kun NY rente)
    og `marker_sendt(...)` (først her oprettes fee_revenue-linjer). Spærrer (betalt m.m.) kommer i trin 3.
  - `referencesats.py`: `python -m app.opkraevning.referencesats vis | saet --fra ÅÅÅÅ-01-01|07-01 --sats --kilde`.
  - `app/tid.py`: `dansk_dato()` – lovens dage regnes i dansk tid.
  - `spaerrer.py` (trin 3): `spaerrer(session, faktura, dag)` = ALLE grunde til ikke at rykke (betalt/krediteret/
    afskrevet/kreditnota/ikke forfalden, indbetaling seneste 2 bankdage uanset beløb, indbetaling i kassekladde,
    debitor blokeret, afbetalingsordning, under kundens minimum, aktiv inkassosag, rykker i kø, 3 rykkere, for tidligt
    efter kundens plan). Skrives i dunning_skips. `kandidater()` = kun fakturaer, hvor næste rykker er nået.
  - `rykkerkoersel.py`: `laeg_i_koe` (kl. 16, næste bankdag), `kontroller_foer_afsendelse` (kl. 9 – annullerer og
    skriver grunden), `fjern_rykker` (medarbejder, note krævet), `forhaandsvis` (gemmer intet).
    `jobs.py`: jobtyperne `rykker_koe`/`rykker_kontrol` planlagt af natkørslens scheduler – kun for
    `clients.dunning_mode='live'`, som først kan vælges efter trin 4. Indtil da: 'off' (standard) | 'preview'.
  - CLI: `python -m app.cli dunning-preview <id> [--dag]`, `dunning-remove <rykker_id> --note "..."`.
  - Hentning: `app/synk/opkraevning.py` (ressource `invoices`, kun når dunning_mode <> 'off'): debitorer
    (`fetch_debtors`), fakturaer (`fetch_invoices`), indbetalinger fra entries (customerPayment med fakturanummer,
    source='regnskab'). Medarbejdernes felter (blokeret, note, kanal) røres aldrig.
  - Udsendelse (trin 4): fra rykker@dinbogholder.dk, til debitorens e-mail fra e-conomic, Reply-To = KUNDENS
    e-mail (svar går til kunden, aldrig til os). Layout og tekst afprøves før drift.
  - `app/tid.py`: helligdage, bankdage (`er_bankdag`, `bankdage_tilbage`, `naeste_bankdag`), `dansk_dato`.
  - Inkasso Mægleren har INGEN API: overdragelse sker manuelt ud fra en liste; medarbejderen registrerer sagsnr.
  - Indbetalinger: e-conomic udligner selv (remainder). Bankafstemningen laver i kassekladden 3 ben (bank,
    debitor, gebyr-/rentekonto) – kontrolleres FØR bogføring. FIK: betalings-id = fakturanr. + kontrolciffer;
    FI-kreditornummeret = kunden.
- `app/fejlrapport.py` – Sentry (`init_fejlrapport("api"|"worker"|"scheduler")`). Slået fra uden
  `SENTRY_DSN`. environment = production (APP_ENV=production) ellers development. API'et bruger
  FastAPI-integrationen. Ingen lokale variabler, ingen personoplysninger; `before_send` fjerner
  alle felter, hvis navn indeholder token/secret/key/password/authorization (FOELSOMME_NAVNE),
  og alle tekster køres gennem `rediger()`. Worker: hvert job har tags job_id, client_id,
  job_type, og en jobfejl sendes med `capture_exception` (UdskydJob sendes ikke).
- `app/sikkerhed/tjek_tokens.py` – kan alle tokens læses med `CREDENTIALS_KEY`? (kun antal).
  Bruger `kan_dekrypteres()` i kryptering.py, som genbruger det ENE dekrypteringssted.
- Server: `Dockerfile`, `docker-compose.prod.yml` (db uden åben port og indstillet til 16 GB, migrate, api ×3
  processer, worker ×4, scheduler = `app.natkoersel.scheduler`, caddy bygget af `deploy/Dockerfile.caddy`
  med webdelen), `deploy/Caddyfile` (HTTPS; /api, /login, /logout, /auth → api, resten = statiske sider;
  adgangskode foran alt undtagen /health, indtil Microsoft-login er slået til). Guide: `DEPLOY.md`.
- `scripts/backup.sh` – krypteret (gpg AES256) pg_dump; `scripts/gendan_test.sh` – gendanner i en
  separat database og tjekker tabeller, version og tokens → GENDANNELSE OK/FEJLET (gendannelser.log).
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

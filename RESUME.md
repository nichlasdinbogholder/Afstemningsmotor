# Hvor er jeg

**Sidst opdateret:** 07.10.2026

## Kører i dag
- [x] Posteringer hentes fra e-conomic (Din Bogholder ApS, 43.463 posteringer) med
      `python -m app.cli sync-entries <id>`; bogmærket gør, at kun nye hentes næste gang.
- [x] Dubletreglen (version 9): 11 fund på 7 års bogføring, heraf 1 bekræftet fejl (AUB).
- [x] **Serveren kører hos Hetzner** (CPX32, Helsinki, 204.168.242.139, firewall: kun 22,
      80, 443): db, api, worker, scheduler og caddy kører; databasens tabeller er bygget.
- [x] **Data flyttet fra Mac'en til serveren** (07.10.2026): 2 kunder, 43.601 posteringer,
      12.137 fund, 2 af 2 tokens kan læses. Serverens database er nu den rigtige.
- [x] **Backup + afprøvet gendannelse med rigtige data** (07.10.2026): alle 22 tabeller har
      samme antal rækker gendannet og nu, samme databaseversion, 2 af 2 tokens → GENDANNELSE OK.
      Natlig backup kl. 02:15 og månedlig gendannelsestest via /etc/cron.d/afstemning.
- [x] **Sentry** modtager rapporter fra serveren (testrapport set 07.10.2026). Jobfejl sendes med
      tags job_id, client_id og job_type; følsomme felter fjernes (afprøvet med /debug/boom og et
      fejlende job 07.10.2026; /debug/boom er fjernet igen).
- [x] **Natkørsel** (bygget 07.10.2026, ikke kørt på serveren endnu): APScheduler kl. 05:00
      mandag–fredag → ét job pr. aktiv kunde, spredt over 2 timer: synk (inkl. kontoplan) →
      regelmotor → kassekladdekontrol → audit_log. Se resultatet med
      `python -m app.natkoersel.status`.
- [x] **Backup uden for serveren:** Hetzner Storage Box BX11 (u685938, Falkenstein – andet
      datacenter end serveren). `backup.sh` kopierer hver backup derover (afprøvet 07.10.2026).
- [ ] HTTPS på `hub.dinbogholder.dk`: domænet skiftet 07.10 (før afstemning.dinbogholder.dk) – ny A-post skal oprettes.

## Næste opgave
Når DNS virker: genstart caddy, tjek `https://hub.dinbogholder.dk/health` og at
`/docs` beder om adgangskode. Derefter (valgfrit, men anbefalet): flyt data fra Mac'en til
serveren (DEPLOY.md trin 5) og kør `scripts/gendan_test.sh` igen med rigtige data. Slå
automatiske snapshots til på Storage Boxen (gjort 07.10.2026).
Så session 4: kontoplanen skal med i serverens natlige plan – natkørslen på Mac'en er fjernet,
så kontoplaner (og dermed bankkonti til dubletreglen) hentes lige nu ikke automatisk nogen steder.

## Beslutninger jeg har truffet
- 04.10.2026: Dubletreglen strammes ikke mere – usikre fund afgøres af medarbejderen
  (godkend/ignorér); fingerprintet sørger for, at de ikke kommer igen.
- 04.10.2026: En dobbeltbogføring kræver bank som modkonto; rettelser bruger samme
  bilagsnummer; periodiseringer er ikke fejl.
- 04.10.2026: Indtil der er login, beskytter Caddy alt undtagen /health med brugernavn og
  adgangskode – /docs må ikke ligge åbent, når der kommer kundedata.
- 04.10.2026: Backup'en krypteres (gpg) på serveren, før den gemmes eller kopieres væk;
  nøglen og CREDENTIALS_KEY gemmes i en adgangskodemanager, aldrig sammen med backup'en.
- 04.10.2026: Scheduleren er sin egen tjeneste (`worker --kun-planlaeg`), workers kan skaleres.
- 07.10.2026: Server CPX32 (4 vCPU, 8 GB, 80 GB) i Helsinki – rigeligt til 200 kunder ifølge
  overslaget (10–20 GB data); disken blev ikke gjort større ved Rescale, så der kan skaleres ned.
- 07.10.2026: Sentry kun med fejlrapporter (ingen logs/tracing), EU-dataregion.
- 07.10.2026: Natkørslen (05:00 hverdage) ERSTATTER den gamle planlægger, der spredte hentninger
  over hele døgnet hver dag og kørte regler kl. 23:30. Konsekvens: ingen hentning i weekenden.
  Ét job pr. kunde (ikke ét pr. trin), så regler og kassekladdekontrol altid kører EFTER hentningen.
- 07.10.2026: Backup-kopien ligger i Falkenstein, serveren i Helsinki – så ét datacenter ikke
  kan tage begge. Serveren har sin egen nøgle til Storage Boxen (/root/.ssh/storagebox).

## Ting jeg er i tvivl om
- Kontoplanen hentes nu i natkørslen (07.10.2026) – hullet fra Mac-tidsplanen er lukket.
- Bankkonti genkendes på "bank" i navnet. Har nogen kunder bankkonti med andre navne?

## Sidste kommando jeg kørte
```
scripts/backup.sh && scripts/gendan_test.sh   (på serveren, 07.10.2026, rigtige data)
entries 43601 / 43601, findings 12137 / 12137, credentials 2 / 2
Databaseversion: gendannet 3867971909b7, nu 3867971909b7
Tokens: 2 af 2 tokens kan læses med CREDENTIALS_KEY
GENDANNELSE OK
```

---

# Resumé – posteringer (entries) fra e-conomic

## Hvad er bygget
- **Kig først (trin 0):** `scripts/peek_economic.py` viser rå JSON fra e-conomic
  (kun GET; tokens fra miljøvariabler – KUN i dette script). Felterne i databasen
  er valgt ud fra det, demo-aftalen faktisk sendte (138 posteringer i 2022).
- **Tabellen `entries`** (cache): de faste felter fra e-conomic + `beloeb` og
  `beloeb_dkk` som NUMERIC(18,2) (aldrig kommatal) + `raa_data` (JSONB, hele det rå
  svar). Unik pr. (kunde, entryNumber). Indeks på (kunde, dato), (kunde, konto),
  (kunde, beløb).
- **Hentning** (`app/adaptere/economic/`): kun GET, 30 sek. tidsgrænse, ved 429
  ventes det antal sekunder, e-conomic beder om (Retry-After), højst 5 forsøg.
- **Job** `synk_entries` (jobkøen) og kommandoen
  `python -m app.cli sync-entries <client_id>`, der viser hentet / nye /
  opdaterede og det nye bogmærke.

## Felter vi faktisk fik fra e-conomic (demo-aftalen, 2022)
Set i det rå svar fra `/accounting-years/{år}/entries`. Alt gemmes også uændret i
`entries.raa_data`.

| e-conomic-felt | Kolonne i `entries` | Bemærkning |
|---|---|---|
| `entryNumber` | `bogfoert_id` | Unik pr. aftale. Bruges som bogmærke. Altid med. |
| `voucherNumber` | `bilagsnummer` | Flere poster deler samme bilag. |
| `date` | `dato` | |
| `account.accountNumber` | `kontonummer` | |
| `text` | `tekst` | **Mangler på nogle poster** (fx nr. 131) – bliver tom (NULL). |
| `amount` | `beloeb` | NUMERIC(18,2). |
| `amountInBaseCurrency` | `beloeb_dkk` | NUMERIC(18,2). |
| `currency` | `valuta` | |
| `entryType` | `entry_type` | Set: `systemEntry`, `customerPayment`. |
| `customer.customerNumber` | `modpart` = `debitor:<nr>` | Kun på kundeposter. |
| `supplier.supplierNumber` | `modpart` = `kreditor:<nr>` | Dokumenteret, ikke set i demo. |
| `project.projectNumber` | – | Kun i `raa_data`. |
| `vatAccount.vatCode` | – | Kun i `raa_data`. Har mellemrum bagefter (`"I25  "`) – brug `trim()`. |
| `remainder`, `remainderInBaseCurrency` | – | Restbeløb hører til tabellen `open_entries`. |
| `self` | – | Kun i `raa_data`. |

**Felter der IKKE findes i entries – brug i stedet:**
- **Kontonavn:** findes ikke. Slå op i `accounts` på `kontonummer` (`accounts.tenant_id` = kunden).
- **Modkonto:** findes ikke på bogførte poster (kun i kassekladder). Poster på samme bilag
  findes via `bilagsnummer`.
- **Kunde-/leverandørnavn:** findes ikke. Slå op i `customers`/`suppliers` på nummeret i `modpart`.
- **Momskode, projekt:** `raa_data->'vatAccount'->>'vatCode'` og `raa_data->'project'->>'projectNumber'`.
- **Forfaldsdato, fakturanummer, restbeløb:** brug `open_entries` (åbne poster).

## Bogmærket (cursor): højeste `entryNumber`
Vi gemmer det **højeste entryNumber**, vi har hentet, og spørger næste gang kun
efter poster med et højere nummer (`filter=entryNumber$gt:<bogmærke>`).

Hvorfor:
- **Det er unikt for hele aftalen** – ikke kun inden for ét regnskabsår. Det ses
  af e-conomics egen adresse for en postering: `/entries/{entryNumber}` (uden år).
- **Det stiger altid**: en ny bogføring får et nyt, højere nummer – også hvis den
  hører til et gammelt regnskabsår. Derfor spørger vi alle regnskabsår med samme
  bogmærke.
- **e-conomic kan filtrere på det**, så vi ikke henter alt hver gang.
- Fravalgt: **dato** (der kan bogføres bagud i tid, så nye poster kan have en
  gammel dato og blive overset) og **bilagsnummer** (ikke unikt – flere poster
  deler samme bilag).

## Hvis hentningen stopper midtvejs
Regnskabsårene hentes fra ældste til nyeste, og posterne inden for et år sorteres
efter entryNumber (`sort=entryNumber`). Stopper det midtvejs:
- det, der nåede at komme, **gemmes altid** (upsert – giver aldrig dubletter),
- bogmærket flyttes **kun**, hvis det er sikkert: fejlen skete i det **sidste**
  regnskabsår, og posterne kom i stigende rækkefølge. Så flyttes det til den sidst
  hentede post, og næste kørsel fortsætter derfra.
- ellers bliver bogmærket stående (en lavere post i et senere år kunne ellers
  blive sprunget over), og næste kørsel henter det samme igen – uden dubletter.

**Bekræftet i demo-aftalen (2022, 138 posteringer):**
- `sort=-entryNumber` gav 138, 137, 136 … – e-conomic sorterer på entryNumber.
  `nextPage` beholder sorteringen, så alle sider kommer i samme rækkefølge.
- `filter=entryNumber$gt:130` gav præcis 8 posteringer (131–138) ud af 138.

Koden tjekker alligevel selv rækkefølgen, så et usorteret svar aldrig kan flytte
bogmærket forkert.

---

# Resumé – fund og dubletreglen

## Tabeller
- `findings`: ét fund pr. (kunde, regel, fingerprint). Status: `open`, `accepted`,
  `resolved`, `ignored`. Alvor: `low`, `medium`, `high`. Fund slettes aldrig (databasen nægter).
- `finding_events`: en række, når et fund oprettes (`system`), og ved HVER statusændring
  (hvem, fra, til, note, tidspunkt). Skrives af en trigger i databasen. En statusændring
  uden actor afvises. Rækkerne kan ikke ændres eller slettes.
- Den gamle `findings` (de tre regler for åbne poster) hedder nu `aabne_post_fund`. Data er bevaret.
- `client_id` er et heltal (som resten af databasen), og intet slettes automatisk (RESTRICT, ikke CASCADE).

## Fingerprint
`sha256("|".join(sorted(posteringsnumre)))[:32]`, hvor posteringsnumre er e-conomics
`entryNumber` (vores `entries.bogfoert_id`) for de to posteringer i parret.
- Sorteret først, så rækkefølgen er ligegyldig.
- Afhænger kun af HVILKE posteringer, ikke af beløb, tekst eller kørselstidspunkt. Det samme
  par giver derfor altid samme fund, og en medarbejders status overlever hver nattekørsel.
- En kørsel opdaterer kun `last_seen_at`, `detail`, `severity` og `updated_at`. Status røres aldrig.
- Forsvinder problemet, bliver fundet stående med sin gamle `last_seen_at`.

## Dubletreglen (duplicate_entries, version 9)
- **Vindue: 3 dage** (`VINDUE_DAGE` øverst i `app/rules/duplicate_entries.py`). Version 1
  brugte 7 dage. Kort vindue, så husleje, leasing og abonnementer (samme beløb hver måned)
  ikke rammes.
- Et par kræver: samme kunde, **samme konto**, samme beløb MED samme fortegn, samme valuta,
  højst 3 dage imellem, forskellige posteringer, ikke samme bilag, og samme modpart hvis
  begge har en.
- Modsat fortegn (+5.000 / −5.000) er en tilbageførsel og aldrig et fund.
- **A (v3):** har begge bilag en kunde/leverandør (debitor-/kreditorlinjen i bilaget), og
  er de forskellige, er det ikke en dublet – fx samme abonnementspris til to kunder.
  Samme kunde faktureret to gange giver stadig et fund.
- **B (v3):** findes der en postering med MODSAT beløb på samme konto inden for vinduet,
  regnes sagen som tilbageført/udlignet – intet fund.
- **D (v4):** samme tekst kræves (uden forskel på store/små bogstaver og ekstra mellemrum).
  Pris: bogføres samme bilag to gange med FORSKELLIG tekst, fanges det ikke.
- **E (v6):** periodiseringer er ikke dubletter. En periodisering bruger samme bilagsnummer
  måned efter måned; har et bilag linjer på mindst 3 datoer inden for ±200 dage, udelukkes
  det. (3 datoer, så et bilagsnummer der går igen år efter år, ikke tages for en periodisering.)
- **G (v7):** samme slags postering (posteringstype) – en faktura og en betaling er ikke en dublet.
- **F (v7):** rettet senere: er der et modsat beløb på samme DRIFTSKONTO (kontotype
  profitAndLoss i kontoplanen) inden for 365 dage, er dobbeltbogføringen rettet, og hele
  bilagsparret udelukkes. Gælder ikke status-/balancekonti (bank, debitorer), hvor et
  modsat beløb blot er den normale betaling. Er kontoplanen ikke hentet, tæller det ikke.
- **H (v8):** rettelser bogføres med SAMME bilagsnummer. Har et af bilagene et modsat beløb
  på samme konto (tilbageførslen, inden for 365 dage), er det en rettelse – intet fund.
- **I (v9):** modkontoen skal være BANK i balancen: begge bilag skal have en linje på en
  bankkonto, og mindst én linje ud over banklinjen skal gå igen. Bankkonti genkendes som
  balancekonti (status) med "bank" i navnet (`BANK_NAVN`). Uden hentet kontoplan: ingen
  bankkonti og ingen fund. En faktura bogført to gange mod debitorer er IKKE et fund.
- **C (v3):** alle linjepar mellem de samme to bilag samles til ÉT fund (salgs-, moms- og
  debitorlinje giver ikke tre fund). Fingerprintet er hash af ALLE de involverede
  posteringsnumre, sorteret.
- Vi har ikke et leverandørfelt på almindelige udgiftsposter (`modpart` er kun udfyldt på
  poster bogført direkte på en kunde/leverandør). Derfor matches der på konto og beløb.
- Alvor: `high` = samme dato og tekst. `medium` = inden for vinduet. (`low` = forskellige
  konti fandtes i version 1, men er fjernet; se kontrollen nedenfor.)
- Kendt begrænsning: bogføres samme bilag to gange MED SAMME bilagsnummer, ses det ikke,
  fordi linjer fra samme bilag aldrig parres (ellers ville et bilags egne linjer give falske fund).
- Kendt begrænsning: en dobbeltbogføring giver typisk TO fund (udgiftssiden og banksiden).
- Ydelse: 300.000 posteringer for én kunde på 0,4 sek. (én SQL-forespørgsel med indekset
  på kunde + beløb).
- Hver kørsel noteres i `rule_runs`. `findings` viser som standard kun fund, som seneste
  kørsel stadig fandt; ældre fund (fx fra version 1) slettes ikke, men vises med `--alle`.

## Kontrol af falske fund
**Version 1 kørt 04.10.2026 på e-conomic demo-aftalen (kunde 6, 138 posteringer fra juni 2022): 59 fund**
(high/medium/low: 0/3/56).
- 56 af 59 var `low`: ens, runde beløb (1.000, −1.000, 5.000, −2.000 …) på FORSKELLIGE konti,
  fx −1.000 kr. på konto 1020 og 2210. En dobbeltbogføring gentager sig på den samme konto,
  så disse er tilfældige sammenfald – ikke dubletter. Ikke slået op enkeltvis i e-conomic,
  men med 56 af 59 i den gruppe ville fem tilfældige fund næsten med sikkerhed være falske.
- Falsk-positiv-rate klart over 20 % (mindst ca. 95 %) → reglen er strammet:
  1. Vindue fra 7 til 3 dage – gjorde ingen forskel for de 56 (de ligger 0–4 dage fra hinanden).
  2. Samme konto kræves – fjerner alle 56.
  3. Udelad faste konti – ikke nødvendigt endnu.
- Tilbage efter version 2 (forventet): 3 fund på samme konto inden for 3 dage
  (100,00 kr. på 1026 den 01.06; 80,00 kr. og 60,00 kr. på 6903 den 01.06/04.06).
- **Version 2 kørt 04.10.2026 på kunde 6: 3 fund** (high/medium: 0/3), præcis de forventede.
  Samme fund-id'er som i version 1 (3, 5, 45) – fingerprintet holdt.
- Gennemgang af de 59 ud fra posteringsdata (tekst, bilag, konto) – IKKE slået op i e-conomic:
  - De 56 fjernede bekræfter, at `low` var støj: fx er én bankpostering på −1.000 kr.
    (post 10, konto 1020) blevet parret med fem forskellige −1.000-linjer på fem andre konti.
  - Fund 3 (100 kr., konto 1026): post 133 "dec 1" i bilag 10006 og post 55 "7 part 1" i
    bilag 10007 – to forskellige bilag (på 400 og 500 kr.), der hver har en linje på 100 kr.
    Vurdering: tilfældigt sammenfald → **falsk**.
  - Fund 5 (80 kr.) og 45 (60 kr.), konto 6903: linjerne er 25 % af 320 og 240 kr. – ligner
    moms. Bilag 10010 ("ten") indeholder de samme beløb 240 + 60 og 320 + 80 som bilag 10006
    og 10007, men på ANDRE udgiftskonti. Kan være samme køb bogført to gange med forskellig
    konto (rigtig dublet) – eller e-conomics testdata. **Uafklaret** uden opslag i e-conomic.
- Demo-aftalen er e-conomics syntetiske testdata ("testing", "dec 1", "ten 2"). Den er for
  lille og for kunstig til at måle en falsk-positiv-rate. Reglen strammes ikke yderligere
  på baggrund af den; næste kontrol skal ske på en rigtig kunde.
- Demo-aftalen er e-conomics eksempeldata, ikke et rigtigt regnskab. Kontrollen skal
  gentages på en rigtig kunde, før regel nr. 2 bygges.

**Version 2 kørt 04.10.2026 på Din Bogholder ApS (rigtig kunde, 43.463 posteringer
2019–2026): 12.053 fund** (high/medium: 406/11.647). Uanvendeligt. Målt med
`scripts/maal_dubletfund.sql`:

| Årsag | Fund |
|---|---|
| A. Bilagene har forskellig kunde/leverandør (samme pris til forskellige kunder) | 10.504 |
| B. Tilbageført (modsat beløb, samme konto, ±3 dage) | 2.332 |
| Tilbage efter A og B | 589 |
| … talt som bilagspar (C) | 306 |

Flest fund på: 6902 Udgående moms (2.465), 1150 Fenerum fordelingskonto (2.189),
1035 Stor abonnementspakke (1.383), 1110 Bogholderibeskrivelse (1.158),
1065 E-Boks service (1.020). Skridt 3 i den aftalte rækkefølge ("udelad konti med faste
beløb") er IKKE brugt: det ville gøre reglen blind for dobbeltfakturering af samme kunde.
A, B og C rammer i stedet præcis de målte årsager → version 3. Forventet ca. 306 fund.

**Version 3 kørt 04.10.2026 på Din Bogholder ApS. Kontrolleret 5 tilfældige fund: 0 rigtige,
5 falske** (falsk-positiv-rate ca. 100 %):
- 1126: −1.599,50 kr. moms, "Invoice 22060 (#205)" og "Invoice 22067 (#225)" – to fakturaer.
- 2336: −299,00 kr. E-Boks, "Invoice 22194" og "Invoice 22196" – to fakturaer.
- 6408: −5.200,00 kr. Stor abonnementspakke, "Opdeling (Stor månedspakke)" i to bilag.
- 7164: −625,00 kr. moms, "Bostock Entreprise" og "Jublo ApS" – to kunder.
- 7425: −2.500,00 kr. Bogholderibeskrivelse, "Grønne Leverum ApS" og "Skærbæk Stillads ApS".
Årsag: fakturaerne kommer fra et eksternt faktureringssystem og bogføres via
1150 Fenerum fordelingskonto, ikke på en debitor – kunden står KUN i teksten, så A virker
ikke. → Version 4 kræver samme tekst (D).

**Version 4 kørt 04.10.2026 på Din Bogholder ApS: 30 fund** (fra 12.053). Gennemgået ud
fra tekst, bilag og beløb (IKKE slået op i e-conomic):
- Ligner rigtige dobbeltbogføringer: 10699 (samme faktura nr. 8279 som bilag 8279 og
  910161), 10652 (IMERCO, samme reference 2600084), 24184 (AUB, samme referencenr.),
  24170 (samme 6 kunder og beløb i bilag 80158 og 80159), 24196 (Adobe 3 gange),
  3473 (Lars Lyngby VVS), 24195 (Mofibo).
- Uafklaret – gentagne småudgifter, kan være rigtige eller en dobbelt indlæst bankfil:
  Brobizz (5), EasyPark (3 fund for samme 3 bilag), Overførselsservice (2), zenegy (1).
- 12 af de 30 var ÉT problem (Best One-periodisering i bilag 50301 og 50302, én linje
  pr. måned) → version 5 samler pr. bilagsnummer uanset dato. Forventet ca. 19 fund.

**Kontrol i e-conomic (bogholderen, 04.10.2026):** 10699, 24170, 24184, Best One og 24188.
- Bogholderen: "Periodiseringer er ikke en fejl. Her bruges typisk samme bilagsnummer."
  → Best One-fundet var FALSK → version 6 udelukker periodiseringer (E).
- Bogholderens svar på de fire øvrige:
  - 24184 AUB (samme referencenr. to gange): **RIGTIG FEJL** – første bekræftede dublet.
  - 10699 faktura 8279: falsk – rettet senere (mere end 3 dage efter).
  - 24188 EasyPark: falsk – rettet senere.
  - 24170 de 6 kunder: falsk – den ene er faktura, den anden betaling.
- **Resultat: 1 rigtig, 4 falske (falsk-positiv-rate 80 %).** Alle fire falske har nu en
  regel: periodisering (E, v6), rettet senere (F, v7), faktura/betaling (G, v7).

**Version 7 kørt på Din Bogholder ApS:** AUB-fundet er stadig med. Bogholderen om de
øvrige: "det er ikke fejl. Det er rettelser, hvor samme bilagsnummer, tekst og beløb
benyttes." → version 8 (H). Og: "hvis der skal være tale om en dobbeltbogføring, skal
modkonto være bank i balancen." → version 9 (I).

**Version 9 kørt 04.10.2026 på Din Bogholder ApS: 11 fund** (fra 12.053 i version 2).
- 24184 AUB: bekræftet rigtig dobbeltbogføring.
- EasyPark (24188, 24189, 24190): i bogføringen er der TRE betalinger fra banken på
  169 kr. (bilag 20942, 20943, 20946). Bogholderens rettelse (konto 2214 → 2770, samme
  dag) ses ikke i de bogførte posteringer. Om det er fejl, kan kun kontoudtoget afgøre.
- Brobizz (24176, 24186, 24187, 24191, 24192), 24170 (de 6 kunder) og 3473 (Lars Lyngby
  VVS): ikke afgjort.

## Konklusion (aftalt med bogholderen 04.10.2026)
Reglen strammes ikke mere. De resterende fund kan ikke skilles fra rigtige fejl ud fra
bogføringen alene (fx gentagne småbeløb, som kun kontoudtoget kan afgøre), og yderligere
stramning ville fjerne rigtige fejl som AUB (320 kr. – en beløbsgrænse ville skjule den).

**Reglen foreslår – medarbejderen afgør.** Usikre fund godkendes (`accepted`) eller
ignoreres (`ignored`) af en medarbejder med en note. Statusændringen logges i
finding_events, og takket være fingerprintet kommer et ignoreret fund ikke igen ved
næste nattekørsel.

Status: 11 fund på 43.463 posteringer over 7 år, heraf 1 bekræftet rigtig. Klar til brug.

Skabelon:

    Dubletregel v<N> kørt <dato> på <kunde>. <N> fund (high/medium: x/y).
    Kontrolleret 5 stk: <a> rigtige, <b> falske (<hvorfor>).
    Falsk-positiv-rate ca. <b/5> %.

## Opkrævning (erstatter FarPay) – faste opgaver i ugeplanen
- **Renten følger Nationalbanken automatisk:** hver morgen kl. 6.10 hentes udlånsrenten for halvåret
  (1.1/1.7), hvis den mangler (Danmarks Statistiks Statistikbank, tabel DNRENTD). **1. januar og 1. juli:** tjek
  `python -m app.opkraevning.referencesats vis`. Er den ikke kommet (kilden nede/ændret), kan den hentes med
  `... referencesats hent --gem` eller sættes i hånden med `... saet --fra ÅÅÅÅ-01-01 --sats <procent> --kilde "…"`.
  Mangler satsen, stopper renteberegningen (med vilje) – den gætter ikke.
- **Rykkerforløbet (besluttet 07.10.2026):** venlig påmindelse (uden gebyr) 5 dage efter forfald, derefter rykker
  hver 10. dag (rykker 1 = 10 dage efter påmindelsen). Kompensationsgebyret (310 kr., kun erhverv) på rykker 3.
- **Betalingsnøglen (FIK +71):** 15 cifre = fakturanummer + to kontrolcifre (det sidste = modulus 10). Bekræftet ud
  fra FarPay: faktura 742 → 000000000074203. FI-kreditornummeret er kundens.
- Inkasso: Inkasso Mægleren for ALLE kunder (manuel overdragelse ud fra en liste).
- Karensperioden før inkasso er fast 10 dage for alle kunder (besluttet 07.10.2026).



# Bilagsindbakken (app/bilag) – status

Specifikation v3 (15.10.2026): tre indgange (videresendt mail til `kunde-<id>@bilag.dinbogholder.dk`, upload
fra appen) → én pipeline → rykker til Corpay; kvittering som bilag i kundens kassekladde (ALDRIG bogført);
faktura/kreditnota/kontoudtog → kundens godkendelse → Corpay.

## Bygget
- **Trin 0 (15.10.2026):** orientering. Genbruges: jobkøen (`app/jobs`, `@jobtype`), `sync_state` (til
  delta-token), APScheduler i `app/natkoersel/scheduler.py`, findings (`app/rules`) til hjerteslag og flag,
  PDF-udtrækning (`app/kontoudtog/pdf.py`), log-filteret for hemmeligheder og webdelens login/roller.
- **Trin 1 (15.10.2026, v3):** tabellerne `inbox_addresses`, `inbox_destinations` (corpay | economic_journal),
  `inbox_messages`, `inbox_documents` (source email|upload, note/deltagere/anledning, `note_complete` beregnet
  af databasen), `inbox_approvals` (uforanderlig), `inbox_deliveries`, `inbox_postings`, `supplier_account_map`,
  `inbox_health`. Migrering a2273058bf2c (sletter v2-tabellerne fra 520ef646b60c – kun hvis de er tomme).
  Kommando: `python -m app.bilag.opret --kunde <id> [--corpay <mail>] [--kassekladde <nr>] | --vis`.
  - **Godkendelsesgaten i databasen** (`inbox_require_approval`): levering af faktura/kreditnota/kontoudtog til
    Corpay kræver en 'approved'-godkendelse af PRÆCIS den fil (sha256). Rykkere går uden. Strammet i forhold
    til specifikationen: kun Corpay er leveringsdestination, og kvittering/andet sendes aldrig til Corpay.
  - `inbox_approvals`: trigger afviser UPDATE og DELETE; højst én afgørelse pr. dokument; afvisning kræver grund.
  Afvigelser fra specifikationen, så projektets regler holdes:
  - Adresser er tekst med CHECK på små bogstaver (som `staff.email`) i stedet for CITEXT; `client_id` er INTEGER.
  - Ekstra felter: `inbox_documents.reason` (begrundelse), `reviewed_by`/`reviewed_at` (medarbejder i back office),
    `inbox_messages.ignored_attachments` (frasorterede vedhæftninger logges).
  - Faste koder (CHECK) for `ignore_reason`, `category` og `reason_code`.
  - Højst én aktiv destination af hver slags pr. kunde; dublet kræver `duplicate_of`; 'sent' kræver `sent_at`;
    'created' kræver `external_voucher_id`; forfaldsdato kræver `due_date_source`.

## Beslutninger (16.10.2026)
- **Trin 16 godkendt:** motoren må OPRETTE bilag i kundens kassekladde i e-conomic – aldrig bogføre. Som alle
  skrivninger: dry-run som standard, rigtig oprettelse kun med `ALLOW_BOOKING=true` og en eksplicit bekræftelse.
- **LLM (trin 6):** databehandleraftale/EU er kontrolleret og i orden.
- **Kontovalg:** når en finanskonto vælges automatisk ud fra et scannet bilag, skal den vælges ud fra, at kunderne
  er HÅNDVÆRKERE (fx materialer/værktøj til arbejdet på vareforbrug – ikke "kontorhold"). Fællesreglerne i
  `supplier_account_map` og LLM-forslaget skal bygges med det udgangspunkt.

## FAST REGEL (16.10.2026): et bilag må ALDRIG ende hos en forkert kunde
Nichlas: "Det må IKKE ske. En leverandørfaktura hos en forkert kunde er et dødsstempel."
Derfor (skal bygges ind, før noget vises for en kunde – gælder alle trin):
1. Et bilag fra mail må kun nå kunden (godkendelseskø, kvittering til kassekladde, levering), hvis TO
   uafhængige ting peger på samme kunde: modtageradressen OG selve bilaget (købers CVR/navn på fakturaen,
   eller leverandøren findes i netop den kundes kreditorliste fra e-conomic). Uploads: den indloggede kunde.
2. Peger bilaget på en ANDEN af vores kunder (fx købers CVR) → stop, høj prioritet hos en medarbejder.
3. Mangler den anden ting, eller er der tvivl → medarbejderen afgør. Systemet gætter aldrig.
4. Håndhæves i DATABASEN (som godkendelsesgaten): status pending_approval/auto_route og levering/kontering
   kræver en registreret kundekontrol (metode + hvem/hvad), ellers afvises det.
5. Et alias er først aktivt, når en prøvemail fra kundens videresendelse er kommet ind på den rigtige kunde.
6. I appen ser en kunde kun egne bilag (testes).
7. Indkøring: alle bilag for en NY kunde går forbi en medarbejder de første 30 dage (aftalt 16.10.2026).

## Skal afklares (før de berørte trin)
- **Trin 2 – adgang til postkassen:** `Mail.Read` som *application*-tilladelse giver adgang til ALLE
  postkasser i tenanten. Skal begrænses til bilag@ med RBAC for Applications; bevises med
  `Test-ServicePrincipalAuthorization` (InScope True/False), før noget kører i drift.
- **Fase 2–3 – kundens login i appen:** upload, godkendelse ("Bekræft modtaget"), push og `decided_by`/
  `uploaded_by` forudsætter, at KUNDERNE kan logge ind. I dag logger kun medarbejdere ind (Microsoft).
  Felterne er derfor tekst indtil videre.
- **Trin 3 – filopbevaring:** projektet har ingen fælles "object storage" endnu.
- **Trin 6 – bibliotek:** kaldet til Claude laves med httpx (findes allerede) – intet nyt bibliotek uden at spørge.

# Fagassistenten (app/assistent) – status

Spec: "Prompt til Claude Code — Fagassistent i hub.dinbogholder.dk" (16.10.2026). Fire faser; ét trin ad gangen,
PR og merge efter hvert trin; næste fase først når den foregående er merged og har kørt et par dage.

## Bygget
- **Fase 1, trin 1 (16.10.2026):** tabellerne `assistant_conversations`, `assistant_messages`,
  `assistant_feedback`, `kb_documents` (dansk fritekstsøgning, GIN-indeks), `kb_rate_checks`
  (migrering 4380b1e2b9d6). Tests: `tests/test_assistent_datamodel.py`. Kører på serveren (16.10.2026 10:41).
- **Hændelse 16.10.2026 10:29–10:41:** siden var nede. auto_opdater.sh brugte umask 077, så de nye filer kun
  kunne læses af root, og programmet i containeren (almindelig bruger) kunne ikke indlæse app/models.py →
  migrate fejlede. Rettet: umask 022 i scriptet + `chmod -R a+rX` i Dockerfile. Ingen data tabt.

- **Fase 1, trin 2 (16.10.2026):** chatknappen nederst til højre på alle sider (`web/components/assistent/`:
  `Provider.tsx` med tilstanden over hele hub'en, `Fagassistent.tsx` med knap og panel). Lagt i `Ramme`, som
  ligger i rodlayoutet, så den overlever navigation. Åben/lukket huskes i browseren; Esc, fokus og fokusfælde.
  Afsendelse er slået fra, indtil trin 3. Afprøvet i browser: widget-test 1, 2, 4 og 5 (14 kontroller) bestået;
  test 3 (genindlæsning henter samtalen fra serveren) kræver trin 3.
- **Fase 1, trin 3 + 4 (16.10.2026):** `app/api/assistent.py` (/api/v1/assistant: status, conversations,
  messages med SSE-stream, feedback), `app/assistent/samtale.py` (historik, gemning, kaldet med `anthropic`:
  model ASSISTANT_MODEL = claude-opus-5-5, effort low, max 1500 tokens, server-side fallback ved afvisning),
  `app/assistent/system_prompt.md` (grundinstruksen ordret fra spec'en + "intet materiale fra videnbasen", indtil
  fase 2). Spørgsmålet gemmes før kaldet; afbrudt svar gemmes som 'interrupted'; fejl vises som kort dansk tekst.
  Trin 4 er taget med i trin 3, så fast regel 1 gælder fra første svar. Tests: `tests/test_assistent_api.py`
  (13; modellen er erstattet af en falsk). Kaldet er afprøvet mod en lokal prøveserver i Anthropics format, og
  panelet i browser (7 kontroller). Prompttest 14/16/17 mod den rigtige model: `python -m app.assistent.proeve`.
- **Fase 1, trin 5 (16.10.2026):** advarsel før afsendelse, hvis beskeden ligner personoplysninger
  (`web/lib/personoplysninger.ts`: CPR `\d{6}-?\d{4}`, reg.nr. + kontonr., talrækker på 11+ cifre). "Ret
  beskeden" eller "Send alligevel" – bremser, blokerer ikke. Afprøvet i browser (spec-test 18 + almindelige tal
  som beløb, kontoplan-konti og datoer, der IKKE må udløse den): 12/12.
- **Fase 1, trin 6 (16.10.2026):** tommel op/ned under hvert svar (kommentarfelt ved tommel ned; én gang pr. svar;
  huskes efter genindlæsning). Siden **Svend** (`web/app/fagassistent/`, menupunkt for alle): spørgsmål nyeste først
  med medarbejder og tidspunkt, klik viser svaret, filter "Kun tommel ned", hyppigste ord seneste 30 dage (kun ord af
  bogstaver – aldrig tal, så CPR/kontonumre aldrig vises; danske småord fjernes). API: `/api/v1/assistant/log`,
  `/emner`. Esc lukker panelet overalt på siden. **Gennemgå listen ugentligt den første måned.**
- Rettelse 16.10.2026: advarslen om personoplysninger brugte "lookbehind" i mønstrene, som ældre Safari ikke
  forstår – skrevet om; mellemrum i CPR-nummer genkendes nu også.
- **Fase 2, trin 7 (16.10.2026):** videnbasen redigeres på siden Svend → fanen "Videnbase"
  (`web/components/assistent/Videnbase.tsx`, API `app/api/videnbase.py` = `/api/v1/kb/documents`). Alle kan læse;
  KUN administratorer opretter og retter. Felter: titel, kategori, tekst, status (aktiv/kladde), kildelink,
  gyldig fra/til; for satser værdi, enhed, kontrolside og hint. Intet slettes. En sats, en administrator selv
  lægger ind eller ændrer værdien på, regnes som bekræftet nu (`last_verified_at`). Hver oprettelse/rettelse →
  audit_log (`kb_oprettet` / `kb_aendret` med fra/til). Værdier skrives med dansk komma; "1.5" afvises (tvetydigt).
  Tests: `tests/test_videnbase_api.py` (14) + browser 10/10. Indholdet lægger bygherren/medarbejderne selv ind –
  Claude skriver ALDRIG satser, frister eller regler i videnbasen.
- **Fase 2, trin 8 + 9 (16.10.2026):** før hvert svar slår Svend spørgsmålet op i videnbasen
  (`samtale.find_materiale`): KUN `active` og gyldige på dagen, højst ASSISTANT_KB_TOP_K, sorteret med ts_rank.
  Ét ord er nok ("eller"); ord på ≥ 6 tegn matcher også med anden endelse (ordbogen bøjer "sats"→"sat", men
  "satsen"→"sats"); korte ord skal passe præcist. BEGRÆNSNING: sammensatte ord deles ikke – "satser" finder ikke
  "Satsoversigt". Skriv derfor de ord, folk spørger med, i titel/tekst. Materialet lægges i systemteksten; satser
  med værdi og "senest bekræftet". Intet fundet → "intet materiale". Kilderne vises under svaret som links til
  dokumentet (`/fagassistent/?fane=videnbase&dok=<id>`) og gemmes i `assistant_messages.sources`.
  Tests: spec-test 14, 15, 19, 20, 21, 22, 27 i `tests/test_assistent_api.py`; browser 5/5.
  `python -m app.assistent.proeve` har nu også test 15 (kræver en aktiv kilometersats i videnbasen).
- **Fase 3, trin 10 (16.10.2026):** satskontrollen (`app/assistent/satskontrol.py`, job `assistent_satskontrol`
  i `app/assistent/jobs.py`, mandag kl. 06.00 i natkørslens scheduler + knappen "Kør kontrol nu"). Henter
  kontrolsiden (kun GET, HTML → tekst med Pythons egen HTMLParser – intet nyt bibliotek), finder tallet: først
  REGEL (ankeret = citatet i hintet, fx 'Op til 20.000 km', skal stå præcis ét sted, tallet lige efter), ellers
  MODEL (struktureret svar; tallet SKAL stå ordret på siden, ellers fejl – modellen må aldrig finde på et tal).
  Uændret → last_verified_at = nu. Ændret → udkast (draft, replaces_id) + fund; den gamle forbliver aktiv; samme
  værdi igen genbruger udkastet. Fejl → fund; last_verified_at uændret. Fund = kb_rate_checks med changed/failed
  og ingen afgørelse; ingen kunde, derfor ikke i `findings`. Afgørelse på siden Svend → fanen Satskontrol (KUN
  admin): godkend (vælg "gælder fra"; den gamle → superseded med valid_to dagen før), afvis (note krævet) eller
  kvittér for fejl. audit_log: satskontrol, satskontrol_startet, kb_sats_godkendt, kb_sats_afvist,
  kb_satsfejl_kvitteret. DATABASEN: trigger `kb_kraev_godkendelse` (et udkast med replaces_id kan kun blive aktivt
  med en 'approved' kontrol), `kb_kontrol_uforanderlig` (kontroller kan ikke slettes/ændres – kun afgøres én gang),
  CHECK-regler for afgørelsen (migrering 4ba5848646a7). Tests: `tests/test_satskontrol.py` (spec 23–28 m.fl.);
  browser 12/12.
  **Sådan skrives hintet:** sæt den tekst, tallet står LIGE EFTER på siden, i citationstegn – fx
  `Tallet efter 'Op til 20.000 km'`. Står teksten flere steder, eller er der intet citat, læser Svend siden.
- **Fase 3, trin 11 (16.10.2026):** forældelse – sikkerhedsnettet. En aktiv sats, der ikke er bekræftet i
  ASSISTANT_RATE_STALE_DAYS (45) dage, er forældet (beregnes ved opslag – ingen kolonne): mærkaten "Forældet" i
  videnbasen, og Svend får besked om at skrive ordret "Satsen er senest bekræftet [dato] — tjek den, før du bruger
  den til noget bindende." Over 2 × dagene (90) laver det daglige job `assistent_foraeldelse` (kl. 06.30) et fund
  (kb_rate_checks.outcome = 'stale', højst ét åbent pr. sats), som kvitteres i Satskontrol. Migrering 93484f38bd75.
  Tests: spec-test 29 m.fl. i `tests/test_satskontrol.py`. **Fase 3 er færdig.**
- **Fase 4, trin 12 (18.10.2026):** kundevælgeren øverst i Svends panel ("Generelt" eller en kunde – står synligt
  hele tiden). Kun AKTIVE kunder, hvor medarbejderen er ANSVARLIG (`clients.ansvarlig_medarbejder_id`), kontrolleret
  på serveren ved hvert kald (`samtale.er_tilknyttet`) – mister man ansvaret, er adgangen væk med det samme.
  Kunden gemmes på samtalen og kan ikke ændres; et skift starter en ny samtale. Indtil opslagene (trin 13) findes,
  får Svend at vide, at han ikke må nævne tal fra kundens regnskab. `/api/v1/assistant/kunder`. Ansvarlig sættes
  med `python -m app.kunder.opret --kundenummer <nr> --ansvarlig <e-mail>`. Tests: spec 30, 31, 32.

- **Fase 4, trin 13 (18.10.2026):** Svends syv LÆSENDE opslag (`app/assistent/opslag.py`): get_kontoplan,
  get_saldo (bevægelser i perioden – ikke primosaldo), get_posteringer, get_aabne_debitorer/-kreditorer, get_moms
  (bevægelser på momskontiene fundet via momskoderne i kundens opsætning, /vat-accounts – ingen momsangivelse) og
  get_kundeinfo. Hentes DIREKTE fra e-conomic via adapter-laget (nye læsefunktioner `fetch_entries_period` med
  datofilter og `fetch_vat_accounts`). Kun i en samtale om en kunde (ellers får modellen ingen opslag).
  Kontrol ved HVERT opslag: kunde-id'et skal være samtalens kunde, og medarbejderen skal stadig være ansvarlig
  (eller admin). Lofter: ASSISTANT_MAX_ROWS (200) rækker pr. opslag og ASSISTANT_MAX_TOOL_CALLS (5) opslag pr.
  besked – rammes et loft, skriver serveren selv en fast sætning i svaret. Personoplysninger fjernes, før data når
  modellen: CPR-numre, bank-/IBAN-numre og hele teksten på posteringer på lønkonti (kontonavn med løn, gage,
  feriepenge, feriegodtgørelse, ATP, AM-bidrag, A-skat eller pension – beløbene bliver). Hvert opslag gemmes i
  `assistant_messages.lookups` (funktion, kunde, argumenter, antal rækker, evt. fejl/afkortet). Prøv på serveren
  uden modellen: `python -m app.assistent.opslag_proeve --kundenummer <nr> --ansvarlig <e-mail>`.
  Tests: spec 31, 33–38 i `tests/test_assistent_opslag.py`. Trin 14 (svarets form) og 15 (adgangslog) mangler.
  Afprøvet mod rigtig e-conomic 18.10.2026 (Connect El): 7 af 7 opslag lykkedes, 4 momskonti fundet via
  momskoderne. Kontoplan og åbne kreditorer rammer loftet på 200 rækker hos Connect El.
 (trin 13, `get_moms`) – bygherren valgte A: Svend viser BEVÆGELSER og SALDO på kundens
  momskonti i perioden. Momskontiene findes ud fra momskoden (vatCode) i kundens egen kontoplan i e-conomic.
  Svend beregner IKKE en momsangivelse og siger ikke, hvad der skal indberettes.
- 18.10.2026: Kostpris pr. medarbejder (Indstillinger → Medarbejdere, kun admin): kr. pr. time med en
  "gælder fra"-dato, så gamle timer beholder den pris, der gjaldt dengang (tabellen `staff_cost_rates`; rækker
  kan ikke slettes; samme dato igen = rettelse af en tastefejl). Hver ændring logges (`medarbejder_kostpris`, fra/til).
  Bygherren har bekræftet: kr. pr. time.
- 18.10.2026: Svends kundeopslag hentes DIREKTE fra e-conomic (kundens token, via adapter-laget) – ikke fra vores
  database. Kun læsning.
- 18.10.2026: "Tilknyttet" = den ANSVARLIGE medarbejder på kunden (sættes, når kunden oprettes i hub'en).
  ADMINISTRATORER må slå op i ALLE aktive kunder; medarbejdere kun i dem, de selv er ansvarlige for (bygherren).
  Rollen tjekkes ved hvert kald – mister man admin-rollen, gælder den almindelige regel med det samme.
- 16.10.2026: Hub'en bruges KUN på PC – ikke på telefon. Nye sider bygges ikke til mobil. (Spec'ens widget-test 5
  – "under 640 px fylder panelet hele skærmen" – er derfor ikke længere et krav; det eksisterende er ladt urørt.)
- 16.10.2026: Satskontrollens fund gemmes i kb_rate_checks (med afgørelse), ikke i `findings`, fordi en sats ikke
  hører til en kunde. Den nye sats' "gælder fra" vælges af den, der godkender (ingen standarddato – gættes ikke).
- 16.10.2026: Kun administratorer må rette i videnbasen (bygherren valgte A); alle kan læse.
- 16.10.2026: Bygherren besluttede at starte fase 2 med det samme (uden at vente "et par dage").
- 16.10.2026: Assistenten hedder **Svend** (bygherren). Navnet står i panelet, menuen, logsiden og grundinstruksens
  første linje ("Du hedder Svend og er fagassistent …").
- 16.10.2026: Administratorer ser loggen over ALLE spørgsmål (trin 6); en medarbejder ser kun sine egne.
- 16.10.2026: Bygherren har godkendt Anthropics officielle Python-bibliotek `anthropic` til kaldet til modellen
  (trin 3) – begrundelse: streaming, genforsøg og fejltyper følger med; Anthropics anbefalede vej.

## Afvigelser fra spec'ens SQL (bevidste)
- `user_id TEXT` er i stedet `staff_id` → `staff.id` (rigtig relation med indeks, som alle andre tabeller).
- `assistant_messages.status` (complete/interrupted/failed): spec'en kræver, at en afbrudt stream gemmes
  "med en markering" – det er markeringen.
- `kb_documents.replaces_id`: et udkast fra satskontrollen peger på det dokument, det skal afløse, så
  godkendelsen ved, hvilket der skal sættes til `superseded`.
- CHECK: en sats skal have `value` og `unit`; `valid_to` >= `valid_from`.
- Samtaler/kunder slettes ikke (RESTRICT); beskeder følger samtalen (CASCADE som i spec'en).

## Skal afklares (før de berørte trin)
- Intet åbent lige nu for Fagassistenten.


# Timeregistrering (spec 18.10.2026)

Nyt modul i hub'en (`app/timeregistrering/`) – samme database, jobkø, back office og roller. Kun Din Bogholder (ingen
selskabsdimension). Fire faser; ét trin ad gangen med PR og merge.

## Beslutninger (bygherren 18.10.2026)
- Bygger på `staff` (ikke en ny brugertabel): `time_employees` har kun det nye (ansættelsesform, flexjob, afspadsering,
  lønnummer) og peger på staff – navn og e-mail findes ét sted.
- Kostprisen findes kun ét sted: `time_employee_rates` (kostpris + normtid + produktive timer pr. år, gyldig fra/til).
  `staff_cost_rates` er fjernet. Allerede tastede kostpriser kunne ikke flyttes automatisk (ansættelsesform og produktive
  timer kendes ikke) – de står i audit_log (`kostpris_flyttet`) og vises på siden Medarbejdere, indtil de er tastet igen.
- Pipeline-emner: model A (enkel liste: navn, status åben/vundet/tabt, kunde) – men bygges IKKE nu ("én ting ad
  gangen"). Indtil da er opgavetypen `salg` oprettet, men ikke aktiv (databasen afviser den), og `time_entries` har
  ingen `lead_id` endnu.
- Den gamle, ubrugte timetabel fra CRM-starten er fjernet (migreringen stopper, hvis den har rækker).
- Ejer = administrator: en administrators egen uge godkendes ved aflevering (self_approved); administratorer godkender.
- Salgsprisen 699,00 kr. ekskl. moms gælder fra 18.10.2026.
- Kundevilkår (19.10.2026, valgt B): afregningsformen (timeafregning/abonnement + pris) vælges, når kunden OPRETTES –
  på siden "Kunde", når den bygges. KUN administratorer kan rette den på kundeniveau. Indtil da lægges vilkår ind med
  `python -m app.timeregistrering.satser vilkaar … --af <admin>` (kræver allerede admin). En kunde uden vilkår kan ikke
  få kundetid registreret (ugegitteret i trin 3 siger det tydeligt) – systemet gætter ikke.
- Zenergy-lønoverførslen (fase 3) skriver til et andet system – bygges først efter udtrykkelig tilladelse.

## Afvigelser fra spec'ens SQL (bevidste)
- `users`/`user_id` → `staff`/`staff_id`; name/email/active ligger på staff. Aktør-kolonner (created_by, approved_by …)
  er rigtige relationer til staff med indeks (som resten af databasen).
- `time_entries.lead_id` kommer med pipelinen; CHECK'en for art/kunde er derfor uden lead.
- Ekstra regler i databasen: 'ikke på arbejde' tvinger 0 timer; inaktive opgavetyper afvises; linjen skal høre til
  medarbejderens egen uge og ligge i den; låsningen gælder også en linje, der flyttes ud af en godkendt uge;
  `faktureres særskilt` kræver beskrivelse (CHECK); `corrects_entry_id` kræver `is_correction`; satser kan ikke slettes.
- Revisionssporet skrives af databasen selv (trigger `time_skriv_audit` på alle time_-tabeller) med aktøren fra
  `stamdata.saet_aktoer()` (`app.actor_id` pr. transaktion).

## Bygget
- **Fase 1, trin 1 (18.10.2026):** migrering f95b5ad78632 (tabeller, btree_gist, triggere, opgavetyper, salgspris),
  `models.py`, `stamdata.py` (ansættelse; ny sats afslutter den forrige; samme dato = rettelse). Siden Medarbejdere
  (kun admin): kolonnen Timeregistrering + "Ret" (ansættelsesform, flexjob, lønnummer; ny sats med gyldig fra, normtid,
  kostpris, produktive timer, note). API: `POST /api/medarbejdere/{id}/ansaettelse`, `GET …/satser`, `POST …/sats`.
  Tests: `tests/test_timeregistrering_skema.py` (spec 1–7, 10, 11, 23, 25–27, 36, 41 + revisionsspor),
  `tests/test_api_medarbejdere.py`.
- **Fase 1, trin 2 (19.10.2026):** periodeopslag. SQL-funktionerne `time_salgspris(d)`, `time_kostpris(medarbejder, d)`,
  `time_normtid(…)`, `time_afregning(kunde, d)`, `time_abonnementspris(…)` (migrering fb5a83b87ff1) – altid med datoen
  som argument, aldrig dags dato; ingen sats = NULL (aldrig 0). Python: `satser.satser_paa(session, dato,
  employee_id, client_id)` rejser `ManglerSats` med tydelig dansk besked. Alle rapporter skal gå gennem dem.
  Nye priser/vilkår: `ny_salgspris`, `nye_vilkaar` (ny periode afslutter den forrige, samme dato = rettelse; fælles
  `placer_periode`, også brugt af kostprissatsen). Kommando: `python -m app.timeregistrering.satser vis|salgspris|vilkaar`
  (ændringer kræver `--af <admin-e-mail>`, logges i time_audit og audit_log). Kundevilkår har endnu ingen side i
  webdelen (siden Kunde er en pladsholder) – kun kommandoen. Tests: `tests/test_timeregistrering_satser.py` (spec 12–14).
- **Fase 1, trin 3 (19.10.2026):** ugegitteret – siden Timeregistrering (`web/app/timeregistrering/`), API `/api/tid`
  (`uge`, `afregning`, `linje`, `linje/slet`; app/api/tid.py) og logikken i `registrering.py`. Syv dage mandag–søndag;
  en linje = opgavetype + kunde + beskrivelse + "faktureres særskilt" med timer pr. dag (én række pr. dag i
  time_entries). Tilføj/ret/slet kun i kladde eller afvist uge. Afregningsformen slås op og skrives på rækken, når den
  oprettes, og ændres aldrig siden; skiftes kunden på en linje, oprettes rækkerne forfra. Timer i kvarte timer som
  decimaltal (komma eller punktum), højst 24 timer pr. dag i alt; to linjer med samme kunde/opgavetype/beskrivelse
  afvises (de ville løbe sammen). Summer pr. linje/dag/uge regnes på serveren. Kunde uden vilkår: tydelig besked om, at
  en administrator skal sætte afregningsformen. Medarbejder uden ansættelsesform: besked om at kontakte en admin.
  Start fra en opgave/kunde: `/timeregistrering/?dato=&kunde=&opgavetype=` (bruges, når Opgavestyring/Kunde bygges).
  FORTOLKNING (bør bekræftes): "kundens vilkår på dagens dato" = på ARBEJDSDAGEN (linjens dato), ikke den dag,
  linjen tastes – i tråd med fast regel 6 (satser på registreringsdatoen) og spec-test 9.
  Tests: `tests/test_timeregistrering_registrering.py` (spec 8–10, ret/slet, låste uger, kun egne linjer).


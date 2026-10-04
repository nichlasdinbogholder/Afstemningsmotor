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

## Dubletreglen (duplicate_entries, version 7)
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
**Mangler: kør version 7 på Din Bogholder ApS og kontrollér 5 nye fund.**

Skabelon:

    Dubletregel v<N> kørt <dato> på <kunde>. <N> fund (high/medium: x/y).
    Kontrolleret 5 stk: <a> rigtige, <b> falske (<hvorfor>).
    Falsk-positiv-rate ca. <b/5> %.

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

## Dubletreglen (duplicate_entries, version 2)
- **Vindue: 3 dage** (`VINDUE_DAGE` øverst i `app/rules/duplicate_entries.py`). Version 1
  brugte 7 dage. Kort vindue, så husleje, leasing og abonnementer (samme beløb hver måned)
  ikke rammes.
- Et par kræver: samme kunde, **samme konto**, samme beløb MED samme fortegn, samme valuta,
  højst 3 dage imellem, forskellige posteringer, ikke samme bilag, og samme modpart hvis
  begge har en.
- Modsat fortegn (+5.000 / −5.000) er en tilbageførsel og aldrig et fund.
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

Skabelon:

    Dubletregel v<N> kørt <dato> på <kunde>. <N> fund (high/medium: x/y).
    Kontrolleret 5 stk: <a> rigtige, <b> falske (<hvorfor>).
    Falsk-positiv-rate ca. <b/5> %.

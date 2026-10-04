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

## Dubletreglen (duplicate_entries, version 1)
- **Vindue: 7 dage** (`VINDUE_DAGE` øverst i `app/rules/duplicate_entries.py`). 7 og ikke 35,
  så husleje, leasing og abonnementer (samme beløb hver måned) ikke rammes.
- Et par kræver: samme kunde, samme beløb MED samme fortegn, samme valuta, højst 7 dage
  imellem, forskellige posteringer, ikke samme bilag, og samme modpart hvis begge har en.
- Modsat fortegn (+5.000 / −5.000) er en tilbageførsel og aldrig et fund.
- Vi har ikke et leverandørfelt på almindelige udgiftsposter (`modpart` er kun udfyldt på
  poster bogført direkte på en kunde/leverandør). Derfor matches der på konto og beløb.
- Alvor: `high` = samme konto, dato og tekst. `medium` = samme konto. `low` = forskellige konti.
- Kendt begrænsning: bogføres samme bilag to gange MED SAMME bilagsnummer, ses det ikke,
  fordi linjer fra samme bilag aldrig parres (ellers ville et bilags egne linjer give falske fund).
- Kendt begrænsning: en dobbeltbogføring giver typisk TO fund (udgiftssiden og banksiden).
- Ydelse: 300.000 posteringer for én kunde på 0,4 sek. (én SQL-forespørgsel med indekset
  på kunde + beløb).

## Kontrol af falske fund
**Endnu ikke lavet på rigtige data.** Reglen er indtil videre kun kørt på en kunstig
testkunde i udviklingsmiljøet (10 kendte posteringer): 3 fund, præcis de forventede. En
tilbageførsel og en månedlig husleje gav korrekt intet fund.

Skabelon til den rigtige kontrol:

    Dubletregel kørt <dato> på <kunde>. <N> fund (high/medium/low: x/y/z).
    Kontrolleret 5 stk: <a> rigtige, <b> falske (<hvorfor>).
    Falsk-positiv-rate ca. <b/5> %.

Er mere end hvert femte fund falsk, strammes reglen før regel nr. 2, i denne rækkefølge:
1. Vinduet fra 7 til 3 dage.
2. Kræv samme konto (drop `low`).
3. Udelad konti, hvor gentagne ens beløb er normalt (husleje, leasing, løn).

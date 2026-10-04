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

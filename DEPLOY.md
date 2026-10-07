# Serveren – sådan sættes den op

**Færdig når:** `https://afstemning.dinbogholder.dk/health` svarer `{"status":"ok",...}`,
og `scripts/gendan_test.sh` har skrevet **GENDANNELSE OK** på serveren.

Ord, der går igen:
- **SSH** – sådan logger du ind på serveren fra Terminal på din Mac.
- **DNS** – "telefonbogen", der fortæller, at `afstemning.dinbogholder.dk` er din server.
- **Container** – en lille, afgrænset del af serveren, der kører én ting (database, webdel …).

Hemmeligheder skrives ALDRIG i en mail, en chat eller her i repoet. Gem dem i en
adgangskodemanager (fx 1Password eller Bitwarden). Du får brug for fire:
`CREDENTIALS_KEY`, backup-nøglen, `POSTGRES_PASSWORD` og adgangskoden til siden.

---

## Trin 1 · Hetzner – bestil serveren

1. Opret en konto på hetzner.com → **Cloud** → nyt projekt "Afstemning".
2. **Lav en SSH-nøgle på din Mac** (hvis du ikke har en):
   ```bash
   ssh-keygen -t ed25519 -C "nichlas@dinbogholder.dk"
   cat ~/.ssh/id_ed25519.pub
   ```
   Kopiér linjen, der starter med `ssh-ed25519` – det er den OFFENTLIGE del (må gerne deles).
3. **Add Server**:
   - Placering: **Falkenstein** eller **Helsinki** (begge i EU).
   - Styresystem: **Ubuntu 24.04**.
   - Størrelse: **4 kerner, 16 GB RAM** (vælg den type i listen, der har det).
   - SSH-nøgle: indsæt linjen fra punkt 2.
   - **Firewall** → ny firewall, der KUN tillader indgående: TCP 22, TCP 80, TCP 443, UDP 443.
   - (Valgfrit, anbefalet) **Backups** – Hetzners ugentlige kopi af hele serveren.
     Det er et ekstra sikkerhedsnet; det erstatter IKKE databasebackup'en i trin 6.
4. Notér serverens **IPv4-adresse** (fx `95.217.x.x`).

## Trin 2 · Domæne – peg afstemning.dinbogholder.dk på serveren

Hos den, der styrer DNS for `dinbogholder.dk`:
- Ny **A-post**: navn `afstemning`, værdi = serverens IPv4-adresse.
- (Hvis muligt) ny **AAAA-post**: navn `afstemning`, værdi = serverens IPv6-adresse.

Tjek fra din Mac (kan tage fra minutter til et par timer):
```bash
dig +short afstemning.dinbogholder.dk
```
Den skal svare med serverens IP-adresse.

## Trin 3 · Gør serveren klar

```bash
ssh root@<serverens-ip>
apt update && apt upgrade -y
apt install -y gnupg rsync unattended-upgrades git
curl -fsSL https://get.docker.com | sh      # installerer Docker
```

**Hent koden** (repoet er privat, så serveren får sin egen læse-nøgle):
```bash
ssh-keygen -t ed25519 -f ~/.ssh/github -N ""
cat ~/.ssh/github.pub
```
På GitHub: repoet → **Settings → Deploy keys → Add deploy key** → indsæt linjen,
**uden** "Allow write access". Derefter:
```bash
cat >> ~/.ssh/config <<'EOF'
Host github.com
  IdentityFile ~/.ssh/github
EOF
git clone git@github.com:nichlasdinbogholder/Afstemningsmotor.git /opt/afstemning
cd /opt/afstemning
git checkout claude/blissful-darwin-4x5hkp     # (eller main, når den er flettet)
```

## Trin 4 · Indstillinger (.env) på serveren

```bash
cd /opt/afstemning
cp .env.example .env
chmod 600 .env
openssl rand -hex 24         # → brug som POSTGRES_PASSWORD (kun 0-9 og a-f, så den ikke ødelægger DATABASE_URL)
nano .env
```
Udfyld i `.env`:
| Felt | Værdi |
|---|---|
| `APP_ENV` | `production` |
| `POSTGRES_PASSWORD` | den nye, tilfældige adgangskode |
| `DATABASE_URL` | `postgresql+psycopg://afstemning:<POSTGRES_PASSWORD>@db:5432/afstemningsmotor` (bemærk **@db**) |
| `CREDENTIALS_KEY` | **præcis den samme som på din Mac** (fra adgangskodemanageren) – ellers kan de gemte tokens ikke læses |
| `ECONOMIC_APP_SECRET_TOKEN` | som på din Mac |
| `DOMAIN` | `afstemning.dinbogholder.dk` |
| `BASIC_AUTH_USER` | fx `dinbogholder` |
| `BASIC_AUTH_HASH` | se nedenfor |

**Adgangskode til siden** (alt undtagen `/health`, indtil der er rigtigt login):
```bash
docker run --rm -it caddy:2 caddy hash-password
```
Skriv adgangskoden to gange (den vises ikke). Kopiér svaret (starter med `$2a$`) ind i
`.env` i **enkelte anførselstegn**: `BASIC_AUTH_HASH='$2a$14$...'`

**Tjek `.env`, før du starter** (viser kun udfyldt/mangler – aldrig værdierne):
```bash
for v in APP_ENV POSTGRES_PASSWORD DATABASE_URL CREDENTIALS_KEY ECONOMIC_APP_SECRET_TOKEN DOMAIN BASIC_AUTH_USER BASIC_AUTH_HASH; do grep -qE "^$v=.+" .env && echo "$v: udfyldt" || echo "$v: MANGLER"; done
pw1=$(grep -E '^POSTGRES_PASSWORD=' .env | cut -d= -f2- | tr -d "'\""); pw2=$(grep -E '^DATABASE_URL=' .env | sed -E 's#.*://[^:]+:([^@]*)@.*#\1#'); [ "$pw1" = "$pw2" ] && echo "adgangskoderne er ens" || echo "adgangskoderne er FORSKELLIGE"
grep -q "^BASIC_AUTH_HASH='" .env && echo "hash i enkelte anførselstegn: ja" || echo "hash i enkelte anførselstegn: NEJ"
```

> **To faldgruber**
> - Databasen husker den adgangskode, den fik **første gang** den startede. Står
>   `POSTGRES_PASSWORD` og koden i `DATABASE_URL` ikke ens, fejler `migrate` med
>   "password authentication failed". Er databasen stadig TOM, rettes det med
>   `docker compose -f docker-compose.prod.yml down -v` og en ny start. Brug ALDRIG
>   `down -v`, når der ligger rigtige data – den sletter databasen.
> - `BASIC_AUTH_HASH` indeholder `$`. Står den ikke i enkelte anførselstegn, læser
>   Docker dele af den som variabler ("variable is not set"), og adgangskoden virker ikke.

## Trin 5 · Start det hele

```bash
cd /opt/afstemning
docker compose -f docker-compose.prod.yml up -d --build
docker compose -f docker-compose.prod.yml ps
```
Alle skal stå som `running` (og `migrate` som `exited (0)` – den opdaterer tabellerne og
stopper så). Tjek fra din Mac:
```bash
curl https://afstemning.dinbogholder.dk/health
```
Svar: `{"status":"ok","database":"ok","databaseversion":"..."}`.
Åbn `https://afstemning.dinbogholder.dk/docs` i browseren – den skal bede om brugernavn
og adgangskode.

**Data fra din Mac** (valgfrit): vil du have kunder, tokens og fund med fra Mac'en:
```bash
# På Mac'en:
docker exec afstemningsmotor-db pg_dump -U afstemning -d afstemningsmotor -Fc > ~/mac.dump
scp ~/mac.dump root@<serverens-ip>:/root/
rm ~/mac.dump
# På serveren:
cd /opt/afstemning
docker compose -f docker-compose.prod.yml stop api worker scheduler
docker compose -f docker-compose.prod.yml exec -T db pg_restore -U afstemning -d afstemningsmotor --clean --if-exists --no-owner < /root/mac.dump
rm /root/mac.dump
docker compose -f docker-compose.prod.yml up -d
```
(Filen er ukrypteret – derfor slettes den begge steder med det samme.) Kræver samme
`CREDENTIALS_KEY` som på Mac'en. Stop derefter natkørslen på Mac'en, så kunderne ikke
hentes to steder: `.venv/bin/python -m app.planlaegning.natlig_kontoplan afinstaller`.

## Trin 6 · Backup – og en afprøvet gendannelse

**Backup-nøglen** (krypterer backup'en – uden den kan backup'en ikke bruges):
```bash
mkdir -p /etc/afstemning
openssl rand -base64 48 > /etc/afstemning/backup-noegle
chmod 600 /etc/afstemning/backup-noegle
cat /etc/afstemning/backup-noegle     # kopiér den ÉN gang til adgangskodemanageren
```

**Første backup og gendannelse:**
```bash
cd /opt/afstemning
scripts/backup.sh
scripts/gendan_test.sh
```
`gendan_test.sh` gendanner backup'en i en SEPARAT database (den rigtige røres ikke),
tjekker alle tabeller, databaseversionen og at tokens kan læses – og slutter med
**GENDANNELSE OK**. Resultatet gemmes i `/var/backups/afstemning/gendannelser.log`.

**Automatisk** – backup hver nat kl. 02:15 og en gendannelsestest den 1. i hver måned:
```bash
cat > /etc/cron.d/afstemning <<'EOF'
15 2 * * * root /opt/afstemning/scripts/backup.sh >> /var/log/afstemning-backup.log 2>&1
0 4 1 * * root /opt/afstemning/scripts/gendan_test.sh >> /var/log/afstemning-backup.log 2>&1
EOF
```

**Kopi uden for serveren** (stærkt anbefalet – brænder serveren, er backup'en ellers væk):
bestil en **Hetzner Storage Box** (BX11 er rigeligt), slå SSH til, læg serverens nøgle ind
(`cat ~/.ssh/id_ed25519.pub` på serveren efter `ssh-keygen -t ed25519`), og sæt i `.env`:
`BACKUP_FJERN=uXXXXX@uXXXXX.your-storagebox.de:afstemning/`. Backup'en er krypteret, før
den forlader serveren.

## Trin 7 · Sentry – fejlrapporter

1. Opret konto på sentry.io – vælg **EU** som dataregion, når du bliver spurgt.
2. Nyt projekt → platform **FastAPI** → kopiér **DSN** (en adresse, der starter med `https://`).
3. På serveren: `nano .env` → `SENTRY_DSN=<DSN>` → genstart:
   ```bash
   docker compose -f docker-compose.prod.yml up -d
   ```
4. Send en testrapport:
   ```bash
   docker compose -f docker-compose.prod.yml exec api python -c "import sentry_sdk; from app.fejlrapport import init_fejlrapport; init_fejlrapport('test'); sentry_sdk.capture_message('Test fra serveren'); sentry_sdk.flush()"
   ```
   Den dukker op i Sentry efter få sekunder.

Både webdelen (`api`) og `worker`/`scheduler` sender fejl til Sentry. Rapporterne sendes
uden lokale variabler, uden personoplysninger, og alle kendte tokens maskeres (`****abcd`).

## Trin 8 · Login med Microsoft (samme adgangskode som Outlook)

**Hos Microsoft** – gøres af jeres Microsoft 365-administrator:
1. Gå til **entra.microsoft.com** → **Applications → App registrations → New registration**.
   - Navn: `Afstemningsmotor`
   - Hvem må bruge den: **Accounts in this organizational directory only** (kun jeres egne)
   - Redirect URI: platform **Web**, adresse `https://afstemning.dinbogholder.dk/auth/callback`
2. På oversigtssiden: kopiér **Directory (tenant) ID** og **Application (client) ID**.
3. **Certificates & secrets → New client secret** (fx 24 måneder). Kopiér **Value** med det
   samme – den vises kun én gang. Den er hemmelig: læg den direkte i `.env` på serveren og i
   adgangskodemanageren. Skriv i kalenderen, hvornår den udløber.
4. Login behøver ingen ekstra rettigheder (standarden "User.Read" er nok).

**På serveren** (`nano .env`):
```
MS_TENANT_ID=<Directory (tenant) ID>
MS_CLIENT_ID=<Application (client) ID>
MS_CLIENT_SECRET=<Value fra punkt 3>
SESSION_SECRET=<svaret fra: openssl rand -hex 32>
PUBLIC_URL=https://afstemning.dinbogholder.dk
```
Opret dig selv som administrator og genstart:
```bash
docker compose -f docker-compose.prod.yml up -d
docker compose -f docker-compose.prod.yml exec -T api python -m app.personale.bruger opret --email nichlas@dinbogholder.dk --navn "Nichlas" --rolle admin
```
Åbn `https://afstemning.dinbogholder.dk` → **Log ind med Microsoft**. Kun medarbejdere, der er
oprettet (og aktive) med `app.personale.bruger`, kommer ind – også selvom andre har en
Microsoft-konto hos jer. Når login virker, kan den fælles adgangskode i Caddy fjernes.

| Medarbejdere | Kommando (`docker compose -f docker-compose.prod.yml exec -T api python -m app.personale.bruger …`) |
|---|---|
| Se alle | `vis` |
| Ny medarbejder | `opret --email x@dinbogholder.dk --navn "Navn" --rolle medarbejder` (eller `admin`) |
| Skift rolle | `rolle --email x@dinbogholder.dk --rolle admin` |
| Stoppet | `deaktiver --email x@dinbogholder.dk` (adgangen lukkes med det samme) |

## Mange kunder og medarbejdere (målt)

Målt på en maskine som serveren (4 kerner, 16 GB) med **500 kunder**, 5,4 mio. posteringslinjer,
1,4 mio. kontoudtogslinjer og et års regelkørsler:

| | Tid |
|---|---|
| Kundeoversigt (alle 500 kunder) | 0,06 s |
| Fund / kontoudtog for én kunde | 0,01–0,03 s |
| 20 medarbejdere, der klikker uafbrudt (114 sider pr. sekund) | typisk 0,1–0,2 s, 95 % under 0,65 s |
| Reglerne for én kunde (10.000–100.000 linjer) | 2–3 s |

Derfor:
- Siderne læser **kun vores egen database**, aldrig e-conomic direkte. e-conomic hentes i baggrunden:
  natkørslen kl. 05:00 og knappen **"Opdater nu"**, der lægger kunden forrest i køen (prioritet 10).
- **4 workers** kører køen (`deploy.replicas: 4`). Natkørslen for 500 kunder tager så ca. 45 min.
  Flere: ret tallet i `docker-compose.prod.yml` og kør `up -d`.
- Webdelen kører med 3 processer, og databasen er indstillet til 16 GB RAM (`command` under `db`).
- Vokser det ud over det: større server hos Hetzner (8 kerner / 32 GB) og ret `shared_buffers`
  til 25 % af RAM og `effective_cache_size` til 60 %.

---

## Daglig brug

| Hvad | Kommando (i `/opt/afstemning`) |
|---|---|
| **Gik nattens kørsel godt?** | `docker compose -f docker-compose.prod.yml exec -T api python -m app.natkoersel.status` |
| Kør natkørslen nu (fx efter en fejl) | `docker compose -f docker-compose.prod.yml exec -T scheduler python -m app.natkoersel.scheduler --koer-nu` |
| Opdatér til nyeste kode | `git pull && docker compose -f docker-compose.prod.yml up -d --build` |
| Webdelen | bygges automatisk ind i `caddy` ved `up -d --build` (første gang tager det et par minutter) |
| Se status | `docker compose -f docker-compose.prod.yml ps` |
| Se log (fx worker) | `docker compose -f docker-compose.prod.yml logs --tail 100 worker` |
| Kør en kommando | `docker compose -f docker-compose.prod.yml exec api python -m app.cli findings <id>` |
| Læg et token ind | `docker compose -f docker-compose.prod.yml exec api python -m app.kunder.gem_token --kundenummer <nr> --system economic` |
| Flere workers | `docker compose -f docker-compose.prod.yml up -d --scale worker=2` |

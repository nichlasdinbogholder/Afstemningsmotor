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
openssl rand -base64 32      # → brug som POSTGRES_PASSWORD (gem den i adgangskodemanageren)
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

---

## Daglig brug

| Hvad | Kommando (i `/opt/afstemning`) |
|---|---|
| Opdatér til nyeste kode | `git pull && docker compose -f docker-compose.prod.yml up -d --build` |
| Se status | `docker compose -f docker-compose.prod.yml ps` |
| Se log (fx worker) | `docker compose -f docker-compose.prod.yml logs --tail 100 worker` |
| Kør en kommando | `docker compose -f docker-compose.prod.yml exec api python -m app.cli findings <id>` |
| Læg et token ind | `docker compose -f docker-compose.prod.yml exec api python -m app.kunder.gem_token --kundenummer <nr> --system economic` |
| Flere workers | `docker compose -f docker-compose.prod.yml up -d --scale worker=2` |

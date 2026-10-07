# Webdelen (Next.js)

Siderne, personalet arbejder i. Al data kommer fra Python-delen (`/api`), og login sker med
Microsoft i Python-delen – Next.js viser kun siderne.

## Lokalt

```bash
# 1) Python-delen med udviklings-login (kun lokalt – virker aldrig med APP_ENV=production)
DEV_LOGIN=true .venv/bin/uvicorn app.api.main:app --port 8000
# 2) Siderne
cd web && npm install && npm run dev
```
Åbn http://localhost:3000/dev-login?email=<din e-mail fra staff>. Next.js sender `/api`, `/login`,
`/logout` og `/auth` videre til port 8000 (se `next.config.ts`).

## På serveren

`npm run build` laver statiske filer i `out/`. De bygges ind i Caddy (`deploy/Dockerfile.caddy`),
som udleverer dem og sender `/api` m.m. videre til Python-delen. Der kører intet Node-program i drift.

## Regler

- Siderne henter KUN fra `/api` – aldrig direkte fra e-conomic eller andre systemer.
- Ændringer sendes med `send()` i `lib/api.ts` (tilføjer headeren `X-Afstemning`, som API'et kræver).
- Ingen tokens eller hemmeligheder i koden her – alt hemmeligt ligger i Python-delen.

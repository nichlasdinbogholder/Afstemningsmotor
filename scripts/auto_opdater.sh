#!/usr/bin/env bash
# Automatisk opdatering af serveren: henter nyeste kode fra GitHub og genstarter.
#
#     bash scripts/auto_opdater.sh             # én kontrol (køres hvert 2. minut af systemd)
#     bash scripts/auto_opdater.sh installer   # slå den automatiske opdatering til (én gang)
#     bash scripts/auto_opdater.sh status      # er den slået til, og hvad skete der sidst?
#     bash scripts/auto_opdater.sh afinstaller # slå den fra igen
#
# Én kontrol:
# 1. Henter fra GitHub (kun læsning – serveren skriver aldrig til GitHub).
# 2. Er der intet nyt: gør ingenting.
# 3. Ændrer den nye kode databasens opbygning (migrations/): tag en backup FØRST.
#    Mislykkes backup'en, opdateres der IKKE.
# 4. Skift til den nye kode og byg/genstart (databasen opdateres af tjenesten "migrate").
# 5. Tjek /health. Svarer den ikke "ok": gå tilbage til den forrige kode (kun hvis databasen
#    ikke blev ændret – ellers skrives KRÆVER HANDLING i loggen).
# Log: /var/log/afstemning-opdatering.log
set -uo pipefail
umask 077
ROD="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROD" || exit 1
LOG=/var/log/afstemning-opdatering.log
COMPOSE="docker compose -f $ROD/docker-compose.prod.yml --project-directory $ROD"
SPRUNGET=/var/lib/afstemning-opdater-sprunget-over
export GIT_TERMINAL_PROMPT=0

log() { echo "$(date '+%F %T') $*" >> "$LOG"; }

domaene() {
	grep -E '^DOMAIN=' "$ROD/.env" 2>/dev/null | tail -1 | cut -d= -f2- | tr -d "\"'"
}

sund() {
	# Op til 3 minutter: API'et skal svare "ok" på /health.
	local d; d="$(domaene)"
	for _ in $(seq 36); do
		if curl -fsS --max-time 5 "https://$d/health" 2>/dev/null | grep -q '"status":"ok"'; then
			return 0
		fi
		sleep 5
	done
	return 1
}

byg() {
	$COMPOSE up -d --build >> "$LOG" 2>&1
}

installer() {
	cat > /etc/systemd/system/afstemning-opdater.service <<EOF
[Unit]
Description=Afstemningsmotor: hent og start nyeste kode
After=docker.service network-online.target

[Service]
Type=oneshot
ExecStart=/bin/bash $ROD/scripts/auto_opdater.sh
EOF
	cat > /etc/systemd/system/afstemning-opdater.timer <<'EOF'
[Unit]
Description=Afstemningsmotor: kontrollér for ny kode hvert 2. minut

[Timer]
OnBootSec=2min
OnUnitActiveSec=2min

[Install]
WantedBy=timers.target
EOF
	systemctl daemon-reload
	systemctl enable --now afstemning-opdater.timer
	echo "Automatisk opdatering er slået TIL. Serveren kontrollerer GitHub hvert 2. minut."
	echo "Se hvad der sker:  bash $ROD/scripts/auto_opdater.sh status"
}

status() {
	if systemctl is-active --quiet afstemning-opdater.timer; then
		echo "Automatisk opdatering: TIL"
	else
		echo "Automatisk opdatering: FRA"
	fi
	echo "Kode på serveren: $(git log -1 --format='%h %cd – %s' --date=format:'%d.%m.%Y %H:%M')"
	echo "Seneste hændelser:"
	tail -n 8 "$LOG" 2>/dev/null || echo "  (ingen endnu)"
}

case "${1:-}" in
	installer) installer; exit 0 ;;
	status) status; exit 0 ;;
	afinstaller)
		systemctl disable --now afstemning-opdater.timer
		echo "Automatisk opdatering er slået FRA."
		exit 0 ;;
	"") ;;
	*) echo "Ukendt valg: $1 (brug installer, status eller afinstaller)" >&2; exit 2 ;;
esac

# Kun én kontrol ad gangen.
exec 9>/run/afstemning-opdater.lock
flock -n 9 || exit 0

gren="$(git rev-parse --abbrev-ref HEAD)"
if ! git fetch -q origin "$gren" 2>> "$LOG"; then
	log "FEJL: kunne ikke hente fra GitHub (prøver igen om 2 minutter)."
	exit 1
fi
gammel="$(git rev-parse HEAD)"
ny="$(git rev-parse "origin/$gren")"
[ "$gammel" = "$ny" ] && exit 0
# Kode, der før er rullet tilbage, prøves ikke igen hvert 2. minut – først når der kommer nyere kode.
[ "$(cat "$SPRUNGET" 2>/dev/null)" = "$ny" ] && exit 0

log "Ny kode fundet: $(git log -1 --format='%h – %s' "$ny")"
db_aendret=0
if ! git diff --quiet "$gammel" "$ny" -- migrations/; then
	db_aendret=1
	log "Databasens opbygning ændres – tager backup først."
	if ! bash "$ROD/scripts/backup.sh" >> "$LOG" 2>&1; then
		log "STOP: backup mislykkedes – opdaterer IKKE. Ret backup'en (DEPLOY.md trin 6)."
		exit 1
	fi
fi

# .env og andre filer, der ikke ligger i git, røres ikke.
git reset -q --hard "$ny"
if byg && sund; then
	log "OK: kører nu $(git log -1 --format='%h – %s')."
	exit 0
fi

if [ "$db_aendret" = 1 ]; then
	log "KRÆVER HANDLING: ny kode svarer ikke, og databasen er ændret – går IKKE tilbage automatisk."
	exit 1
fi
log "FEJL: ny kode svarer ikke på /health – går tilbage til $(git log -1 --format='%h' "$gammel")."
echo "$ny" > "$SPRUNGET"
git reset -q --hard "$gammel"
if byg && sund; then
	log "Tilbage på den forrige kode – siden virker igen. Den nye kode ($(git log -1 --format='%h' "$ny")) springes over."
else
	log "KRÆVER HANDLING: heller ikke den forrige kode svarer."
fi
exit 1

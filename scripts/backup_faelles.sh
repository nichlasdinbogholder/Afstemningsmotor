# Fælles opsætning for backup.sh og gendan_test.sh (indlæses med ".", køres ikke selv).
#
# På serveren kører databasen i Docker, og pg_dump/psql køres INDE i db-containeren.
# LOKAL_PG=1 kører i stedet de lokale PostgreSQL-værktøjer (bruges af testene) –
# så skal PGHOST/PGPORT/PGPASSWORD være sat.

ROD="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FIL="${ENV_FIL:-$ROD/.env}"
COMPOSE="docker compose -f $ROD/docker-compose.prod.yml --project-directory $ROD"

# Læs én værdi fra .env (uden at indlæse hele filen i skallen). Mangler den: tom.
hent() {
	local linje
	linje="$(grep -E "^$1=" "$ENV_FIL" 2>/dev/null | tail -1)" || true
	printf '%s' "${linje#*=}" | sed -e "s/^['\"]//" -e "s/['\"]\$//"
}

POSTGRES_USER="$(hent POSTGRES_USER)"
POSTGRES_DB="$(hent POSTGRES_DB)"
BACKUP_MAPPE="${BACKUP_MAPPE:-$(hent BACKUP_MAPPE)}"
BACKUP_MAPPE="${BACKUP_MAPPE:-/var/backups/afstemning}"
BACKUP_NOEGLEFIL="${BACKUP_NOEGLEFIL:-$(hent BACKUP_NOEGLEFIL)}"
BACKUP_NOEGLEFIL="${BACKUP_NOEGLEFIL:-/etc/afstemning/backup-noegle}"
BACKUP_FJERN="${BACKUP_FJERN:-$(hent BACKUP_FJERN)}"
BEHOLD_DAGE="${BEHOLD_DAGE:-14}"
GENDAN_DB="${GENDAN_DB:-gendan_test}"

# Kør et PostgreSQL-værktøj mod databasen.
db() {
	if [ -n "${LOKAL_PG:-}" ]; then "$@"; else $COMPOSE exec -T db "$@"; fi
}

# Kør et Python-modul fra programmet med en anden DATABASE_URL.
app_med_database() {
	local url="$1"; shift
	if [ -n "${LOKAL_PG:-}" ]; then
		DATABASE_URL="$url" "$ROD/.venv/bin/python" "$@"
	else
		$COMPOSE run --rm --no-deps -T -e DATABASE_URL="$url" api python "$@"
	fi
}

kraev_noeglefil() {
	if [ ! -r "$BACKUP_NOEGLEFIL" ]; then
		echo "Fejl: backup-nøglen $BACKUP_NOEGLEFIL findes ikke (se DEPLOY.md, trin 6)." >&2
		exit 2
	fi
}

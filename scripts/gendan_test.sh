#!/usr/bin/env bash
# Afprøv en gendannelse – uden at røre den rigtige database.
#
#     scripts/gendan_test.sh              # seneste backup
#     scripts/gendan_test.sh <fil.dump.gpg>
#
# 1. dekrypterer backup'en og gendanner den i en SEPARAT database (gendan_test)
# 2. tjekker, at alle tabeller fra den rigtige database findes, at databaseversionen
#    (alembic) er den samme, og at kundernes tokens kan læses med CREDENTIALS_KEY
# 3. viser antal rækker pr. tabel (gendannet og nu) – forskelle er normale, hvis
#    systemet har arbejdet siden backup'en
# 4. sletter testdatabasen igen og skriver resultatet i BACKUP_MAPPE/gendannelser.log
#
# Slutter med GENDANNELSE OK (kode 0) eller GENDANNELSE FEJLET (kode 1).
set -euo pipefail
umask 077
. "$(dirname "$0")/backup_faelles.sh"
kraev_noeglefil

fil="${1:-seneste}"
if [ "$fil" = "seneste" ]; then
	fil="$(ls -1t "$BACKUP_MAPPE"/afstemning-*.dump.gpg 2>/dev/null | head -1 || true)"
	[ -n "$fil" ] || { echo "Fejl: ingen backups i $BACKUP_MAPPE" >&2; exit 1; }
fi
echo "Gendanner: $fil"
echo "Testdatabase: $GENDAN_DB (den rigtige database røres ikke)"

psql_i() { db psql -U "$POSTGRES_USER" -d "$1" -v ON_ERROR_STOP=1 -AtX -c "$2"; }

ryd_op() { db dropdb -U "$POSTGRES_USER" --if-exists "$GENDAN_DB" >/dev/null 2>&1 || true; }
ryd_op
trap ryd_op EXIT
db createdb -U "$POSTGRES_USER" "$GENDAN_DB"

log() { echo "$(date '+%F %T') $fil $1" >> "$BACKUP_MAPPE/gendannelser.log"; }

if ! gpg --batch --quiet --pinentry-mode loopback --decrypt --passphrase-file "$BACKUP_NOEGLEFIL" "$fil" \
	| db pg_restore -U "$POSTGRES_USER" -d "$GENDAN_DB" --no-owner --exit-on-error; then
	echo; echo "GENDANNELSE FEJLET – backup'en kunne ikke dekrypteres eller gendannes"
	log "GENDANNELSE FEJLET (dekryptering/gendannelse)"
	exit 1
fi
echo "Gendannet uden fejl."

fejl=0
tabeller="$(psql_i "$POSTGRES_DB" "SELECT table_name FROM information_schema.tables
	WHERE table_schema = 'public' AND table_type = 'BASE TABLE' ORDER BY 1")"
printf '\n%-22s %12s %12s\n' "tabel" "gendannet" "nu"
for t in $tabeller; do
	if gendannet="$(psql_i "$GENDAN_DB" "SELECT count(*) FROM \"$t\"" 2>/dev/null)"; then
		nu="$(psql_i "$POSTGRES_DB" "SELECT count(*) FROM \"$t\"")"
		printf '%-22s %12s %12s\n' "$t" "$gendannet" "$nu"
	else
		printf '%-22s %12s\n' "$t" "MANGLER"
		fejl=1
	fi
done

v_gendannet="$(psql_i "$GENDAN_DB" "SELECT version_num FROM alembic_version" || echo "?")"
v_nu="$(psql_i "$POSTGRES_DB" "SELECT version_num FROM alembic_version")"
echo
echo "Databaseversion: gendannet $v_gendannet, nu $v_nu"
[ "$v_gendannet" = "$v_nu" ] || { echo "  → forskellig version (backup'en er ældre end seneste opdatering)"; fejl=1; }

url="$(hent DATABASE_URL | sed -E "s#/[^/?]+(\?|\$)#/$GENDAN_DB\1#")"
if tokens="$(app_med_database "$url" -m app.sikkerhed.tjek_tokens)"; then
	echo "Tokens: $tokens"
else
	echo "Tokens: ${tokens:-kunne ikke kontrolleres} → FEJL"
	fejl=1
fi

if [ "$fejl" -eq 0 ]; then
	echo; echo "GENDANNELSE OK"
	log "GENDANNELSE OK"
else
	echo; echo "GENDANNELSE FEJLET"
	log "GENDANNELSE FEJLET"
	exit 1
fi

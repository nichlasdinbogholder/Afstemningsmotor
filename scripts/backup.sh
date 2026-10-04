#!/usr/bin/env bash
# Krypteret backup af databasen.
#
#     scripts/backup.sh
#
# 1. pg_dump af hele databasen (inde i db-containeren)
# 2. krypteres med gpg (AES256) og nøglen i BACKUP_NOEGLEFIL – backup'en indeholder
#    kundernes (krypterede) tokens og må aldrig ligge ukrypteret nogen steder
# 3. gemmes i BACKUP_MAPPE som afstemning-ÅÅÅÅMMDD-TTMMSS.dump.gpg
# 4. backups ældre end BEHOLD_DAGE (14) slettes lokalt
# 5. kopieres uden for serveren, hvis BACKUP_FJERN er sat (fx en Hetzner Storage Box)
#
# Kode 0 = ok, 1 = fejl. Køres hver nat af cron (se DEPLOY.md).
set -euo pipefail
umask 077
. "$(dirname "$0")/backup_faelles.sh"
kraev_noeglefil

mkdir -p "$BACKUP_MAPPE"
fil="$BACKUP_MAPPE/afstemning-$(date +%Y%m%d-%H%M%S).dump.gpg"
tmp="$fil.tmp"
trap 'rm -f "$tmp"' EXIT

db pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom \
	| gpg --batch --yes --quiet --pinentry-mode loopback --symmetric --cipher-algo AES256 \
		--passphrase-file "$BACKUP_NOEGLEFIL" --output "$tmp"
mv "$tmp" "$fil"
trap - EXIT
echo "$(date '+%F %T') Backup gemt: $fil ($(du -h "$fil" | cut -f1))"

find "$BACKUP_MAPPE" -name 'afstemning-*.dump.gpg' -mtime +"$BEHOLD_DAGE" -print -delete \
	| sed 's/^/Slettet gammel backup: /'

if [ -n "$BACKUP_FJERN" ]; then
	rsync -a -e "ssh -p 23" "$fil" "$BACKUP_FJERN"
	echo "Kopieret uden for serveren: $BACKUP_FJERN"
else
	echo "ADVARSEL: BACKUP_FJERN er ikke sat – backup'en ligger kun på serveren selv."
fi

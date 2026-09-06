#!/bin/bash
# A nightly, consistent dump of the database, verified and put offsite. Runs as
# family-db-backup.service (a timer), as the user `family`, with
# EnvironmentFile=/etc/family/env.
#
# Why not simply copy the file: the database runs in WAL mode. A bare copy (or a
# backup tool that catches the live file) would be inconsistent -- the WAL is
# missing. `VACUUM INTO` reads a CONSISTENT view and writes a self-contained
# .db. That one is verified (integrity_check plus a sanity query = a restore
# test) BEFORE it is kept.
#
# Offsite: the container's /opt/family/data is not in the backup. The web share
# is. So the dump is hung in a sub-folder there -- one the sync and the scan do
# NOT walk (they only go through the originals tree), so it disturbs nothing.
set -euo pipefail

DB="${FAMILY_DB:-/opt/family/data/family.db}"
LOCAL_DIR="${FAMILY_DB_BACKUP_DIR:-/opt/family/data/backups}"
SHARE_DIR="${FAMILY_WEB:-/mnt/family-website}/_db-backups"
KEEP="${FAMILY_DB_BACKUP_KEEP:-14}"
HEARTBEAT="${FAMILY_BACKUP_HEARTBEAT_URL:-}"

log() { echo "[db-backup] $*"; }
die() { echo "[db-backup] FEELER: $*" >&2; exit 1; }

# --- Restore test: open the last dump and check it -------------------------
verify_gz() {
  local gz="$1"
  [ -f "$gz" ] || die "no dump to verify: $gz"
  local t; t="$(mktemp /tmp/restore-test.XXXXXX.db)"
  # shellcheck disable=SC2064
  trap "rm -f '$t'" RETURN
  gunzip -c "$gz" > "$t" || die "gunzip failed"
  local ic; ic="$(sqlite3 "$t" 'PRAGMA integrity_check;' | head -1)"
  [ "$ic" = "ok" ] || die "integrity_check vum Restore: $ic"
  local n; n="$(sqlite3 "$t" 'SELECT count(*) FROM photos;')"
  local v; v="$(sqlite3 "$t" "SELECT value FROM state WHERE key='schema_version';" 2>/dev/null || echo '?')"
  log "Restore-Test ok: $(basename "$gz") -> $n Fotoen, schema $v"
}

# --- Ee Backup maachen ------------------------------------------------------
do_backup() {
  [ -f "$DB" ] || die "keng DB op $DB"
  mkdir -p "$LOCAL_DIR"
  local stamp; stamp="$(date +%Y%m%d-%H%M%S)"
  local tmp; tmp="$(mktemp "$LOCAL_DIR/.dump.XXXXXX.db")"
  # shellcheck disable=SC2064
  trap "rm -f '$tmp' '$tmp'.gz" EXIT

  log "Dump $DB"
  sqlite3 "$DB" "VACUUM INTO '$tmp'" || die "VACUUM INTO failed"

  # Verify BEFORE we keep it (= a restore test on the fresh dump)
  local ic; ic="$(sqlite3 "$tmp" 'PRAGMA integrity_check;' | head -1)"
  [ "$ic" = "ok" ] || die "integrity_check: $ic"
  local n; n="$(sqlite3 "$tmp" 'SELECT count(*) FROM photos;')"
  log "integrity ok, $n Fotoen"

  gzip -f "$tmp"
  local out="$LOCAL_DIR/family-$stamp.db.gz"
  mv "$tmp.gz" "$out"
  trap - EXIT
  log "lokal: $out ($(du -h "$out" | cut -f1))"

  # Rotate locally
  # shellcheck disable=SC2012
  ls -1t "$LOCAL_DIR"/family-*.db.gz 2>/dev/null | tail -n +"$((KEEP + 1))" | xargs -r rm -f

  # Offsite, onto the backed-up share
  if mkdir -p "$SHARE_DIR" 2>/dev/null && [ -w "$SHARE_DIR" ]; then
    cp -f "$out" "$SHARE_DIR/family-$stamp.db.gz"
    cp -f "$out" "$SHARE_DIR/family-latest.db.gz"
    # shellcheck disable=SC2012
    ls -1t "$SHARE_DIR"/family-2*.db.gz 2>/dev/null | tail -n +"$((KEEP + 1))" | xargs -r rm -f
    log "offsite: $SHARE_DIR"
  else
    log "WARNING: $SHARE_DIR is not writable -- the dump stays local only"
  fi

  # Verify once more out of the gzip (a real restore test)
  verify_gz "$out"

  # Heartbeat (optional): tells a monitor that the backup ran
  if [ -n "$HEARTBEAT" ]; then
    if curl -fsS -m 15 "$HEARTBEAT" >/dev/null 2>&1; then log "heartbeat ok"; else log "WARNING: the heartbeat failed"; fi
  fi
  log "done"
}

case "${1:-backup}" in
  backup) do_backup ;;
  verify) # restore-test the last dump (local, or one given as an argument)
    gz="${2:-$(ls -1t "$LOCAL_DIR"/family-*.db.gz 2>/dev/null | head -1)}"
    verify_gz "$gz" ;;
  *) die "onbekannt: $1 (backup | verify [datei])" ;;
esac

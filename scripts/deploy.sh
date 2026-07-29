#!/usr/bin/env bash
#
# Update the `listen` container to the freshly published image, without losing data.
#
# Runs on the homelab box itself (LXC 100), invoked by the self-hosted GitHub Actions
# runner after the image is pushed. The shape is: snapshot, swap, verify, and put
# everything back if the verify fails -- so a bad push costs a minute of downtime
# rather than a database.
#
# Every step is idempotent; running it by hand is a supported way to deploy.
set -Eeuo pipefail

STACK_DIR=${STACK_DIR:-/opt/mediastack}
SERVICE=${SERVICE:-listen}
CONTAINER=${CONTAINER:-listen}
DATA_DIR=${DATA_DIR:-$STACK_DIR/listen/data}
DB_PATH=${DB_PATH:-$DATA_DIR/favsongs.db}
BACKUP_DIR=${BACKUP_DIR:-$STACK_DIR/listen/backups}
KEEP_BACKUPS=${KEEP_BACKUPS:-10}
HEALTH_TIMEOUT=${HEALTH_TIMEOUT:-120}

DBTOOL="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/dbtool.py"

say() { printf '\n==> %s\n' "$*"; }
fail() { printf '\nERROR: %s\n' "$*" >&2; exit 1; }

command -v docker >/dev/null || fail "docker is not on PATH"
[ -f "$STACK_DIR/docker-compose.yml" ] || fail "no compose file at $STACK_DIR"

compose() { docker compose -f "$STACK_DIR/docker-compose.yml" "$@"; }

# ---------------------------------------------------------------- current state

# The image *id*, not the tag: `:latest` is about to move, and this is what we would
# need to go back to.
PREVIOUS_IMAGE=$(docker inspect --format '{{.Image}}' "$CONTAINER" 2>/dev/null || true)
say "Current image: ${PREVIOUS_IMAGE:-<none running>}"

say "Backing up the database"
BACKUP=$(python3 "$DBTOOL" backup --db "$DB_PATH" --into "$BACKUP_DIR" --keep "$KEEP_BACKUPS")
BEFORE=$([ -f "$DB_PATH" ] && python3 "$DBTOOL" check --db "$DB_PATH" || echo '{}')

vital_counts() {  # users+tokens from a summary JSON, the rows that must never vanish
  python3 -c '
import json, sys
data = json.loads(sys.stdin.read() or "{}")
counts = data.get("counts", {})
print(counts.get("users"), counts.get("tokens"))'
}
BEFORE_VITAL=$(printf '%s' "$BEFORE" | vital_counts)

# ------------------------------------------------------------------- roll back

rollback() {
  # Disarm first: this function ends in a non-zero exit, which would otherwise
  # re-trigger the trap and roll back the rollback.
  trap - ERR
  say "Deployment failed -- rolling back"
  compose stop "$SERVICE" || true
  if [ -n "$BACKUP" ] && [ -f "$BACKUP" ]; then
    python3 "$DBTOOL" restore --backup "$BACKUP" --db "$DB_PATH" || \
      printf 'WARNING: restore failed; backup is at %s\n' "$BACKUP" >&2
  fi
  if [ -z "$PREVIOUS_IMAGE" ]; then
    # Nothing was running before this, so there is no older image to go back to.
    # Starting the new one again would just re-apply whatever failed -- including a
    # migration, straight back over the database we just restored.
    fail "no previous image to roll back to; database restored from ${BACKUP:-<no backup>} and the service left stopped"
  fi

  # Point the tag back at the image that was working, then recreate from it.
  IMAGE_REF=$(compose config --images "$SERVICE" 2>/dev/null | head -1)
  [ -n "$IMAGE_REF" ] && docker tag "$PREVIOUS_IMAGE" "$IMAGE_REF" || true
  compose up -d "$SERVICE" || true
  fail "rolled back to $PREVIOUS_IMAGE; nothing was kept from this deploy"
}
trap rollback ERR

# ----------------------------------------------------------------------- deploy

say "Pulling the new image"
compose pull "$SERVICE"

NEW_IMAGE=$(compose config --images "$SERVICE" | head -1)
NEW_ID=$(docker image inspect --format '{{.Id}}' "$NEW_IMAGE")
if [ "$NEW_ID" = "$PREVIOUS_IMAGE" ]; then
  say "Already running this image; nothing to do"
  trap - ERR
  exit 0
fi

say "Recreating the container"
compose up -d "$SERVICE"

# ----------------------------------------------------------------------- verify

say "Waiting for it to come up (up to ${HEALTH_TIMEOUT}s)"
deadline=$(( $(date +%s) + HEALTH_TIMEOUT ))
until docker exec "$CONTAINER" python -c \
      "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=5).read()" \
      >/dev/null 2>&1; do
  [ "$(date +%s)" -lt "$deadline" ] || {
    docker logs --tail 40 "$CONTAINER" >&2 || true
    fail "never became healthy"
  }
  sleep 3
done

# /healthz only proves the HTTP server is up. This proves the database opened, any
# migration ran, and the app can actually answer -- which is what a bad release breaks.
say "Checking the app can serve state"
docker exec "$CONTAINER" python -c "
import sys, urllib.request
with urllib.request.urlopen('http://127.0.0.1:8000/api/state', timeout=10) as r:
    sys.exit(0 if r.status == 200 else 1)
" || fail "/api/state did not return 200"

say "Checking the database"
AFTER=$(python3 "$DBTOOL" check --db "$DB_PATH")
AFTER_VITAL=$(printf '%s' "$AFTER" | vital_counts)
if [ "$BEFORE_VITAL" != "$AFTER_VITAL" ] && [ "$BEFORE_VITAL" != "None None" ]; then
  fail "users/tokens changed across the update (was [$BEFORE_VITAL], now [$AFTER_VITAL])"
fi

trap - ERR
say "Deployed $NEW_IMAGE"
printf 'before: %s\nafter:  %s\nbackup: %s\n' "$BEFORE" "$AFTER" "${BACKUP:-<none>}"

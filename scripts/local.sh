#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_FILE="$ROOT_DIR/compose.local.yml"
ENV_FILE="$ROOT_DIR/.env.local"
ENV_EXAMPLE_FILE="$ROOT_DIR/.env.local.example"

if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
  COMPOSE_CMD=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
  COMPOSE_CMD=(docker-compose)
else
  echo "Docker Compose not found. Install Docker with Compose first."
  exit 1
fi

usage() {
  cat <<'EOF'
Usage:
  ./scripts/local.sh up       Build and start local stack
  ./scripts/local.sh down     Stop local stack
  ./scripts/local.sh logs     Follow local stack logs
  ./scripts/local.sh rebuild  Rebuild app container
  ./scripts/local.sh reset    Destroy stack resources (destructive)
  ./scripts/local.sh status   Show local stack status
EOF
}

ensure_env_file() {
  if [[ -f "$ENV_FILE" ]]; then
    return
  fi

  if [[ -f "$ENV_EXAMPLE_FILE" ]]; then
    cp "$ENV_EXAMPLE_FILE" "$ENV_FILE"
    echo "Created .env.local from .env.local.example. Fill in Spotify credentials before continuing."
    exit 1
  fi

  echo "Missing $ENV_FILE and $ENV_EXAMPLE_FILE."
  exit 1
}

compose() {
  (cd "$ROOT_DIR" && "${COMPOSE_CMD[@]}" -f "$COMPOSE_FILE" --env-file "$ENV_FILE" "$@")
}

check_health() {
  local url="http://127.0.0.1:8000/healthz"
  for _ in $(seq 1 30); do
    if curl -fsS "$url" >/dev/null 2>&1; then
      echo "Local app is healthy: $url"
      return
    fi
    sleep 1
  done
  echo "Local app did not pass health check in time."
  exit 1
}

main() {
  local cmd="${1:-}"
  case "$cmd" in
    up)
      ensure_env_file
      compose up -d --build
      check_health
      compose ps
      ;;
    down)
      ensure_env_file
      compose down
      ;;
    logs)
      ensure_env_file
      compose logs -f
      ;;
    rebuild)
      ensure_env_file
      compose up -d --build --force-recreate
      check_health
      compose ps
      ;;
    reset)
      ensure_env_file
      compose down -v
      ;;
    status)
      ensure_env_file
      compose ps
      ;;
    *)
      usage
      exit 1
      ;;
  esac
}

main "$@"

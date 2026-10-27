#!/bin/sh
set -eu

repo_root=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
cd "$repo_root"

if [ -e .env ]; then
  echo ".env already exists; leave its installation-specific values intact." >&2
  exit 1
fi

if ! command -v docker >/dev/null 2>&1 || ! docker compose version >/dev/null 2>&1; then
  echo "Docker Compose v2 is required for a local WatchMatch installation." >&2
  exit 1
fi

umask 077
database_password=$(od -An -N24 -tx1 /dev/urandom | tr -d " \n")
cat > .env <<EOF
# Generated for this local installation. Do not commit this file.
PLEX_URL=http://host.docker.internal:32400
PLEX_TOKEN=
PLEX_MACHINE_IDENTIFIER=
PLEX_CLIENT_IDENTIFIER=watchmatch-web
AUTH_REQUIRED=false
SYSTEM_OWNER_PLEX_ID=
SYSTEM_RECOVERY_CODE=
PUBLIC_URL=http://localhost:8080
POSTGRES_DB=watchmatch
POSTGRES_USER=watchmatch
POSTGRES_PASSWORD=$database_password
POSTGRES_DATA_PATH=./postgres-data
EOF

if [ ! -e config.yaml ]; then
  cp config.example.yaml config.yaml
fi

docker compose --env-file .env config --quiet
printf '%s\n' "Local installation files are prepared. Add Plex settings to .env when available, then run: docker compose --env-file .env up --build"

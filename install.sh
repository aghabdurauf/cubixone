#!/usr/bin/env bash
# CubixOne installer: asks where user accounts live, writes .env, starts Docker.
#   ./install.sh        interactive
#   ./install.sh -y     no questions: built-in MySQL on port 8080
set -euo pipefail
cd "$(dirname "$0")"

say()  { printf '\033[1;34m%s\033[0m\n' "$*"; }
fail() { printf '\033[1;31m%s\033[0m\n' "$*" >&2; exit 1; }
rand() { head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n'; }
urlencode() {
  local s="$1" out="" c i
  for ((i = 0; i < ${#s}; i++)); do
    c=${s:i:1}
    case "$c" in [a-zA-Z0-9.~_-]) out+="$c" ;; *) printf -v c '%%%02X' "'$c"; out+="$c" ;; esac
  done
  printf '%s' "$out"
}

command -v docker >/dev/null 2>&1 || fail "Docker is required: https://docs.docker.com/get-docker/"
docker compose version >/dev/null 2>&1 || fail "Docker Compose v2 is required (docker compose ...)."

AUTO=no; [ "${1:-}" = "-y" ] && AUTO=yes

if [ -f .env ] && [ "$AUTO" = no ]; then
  read -rp ".env already exists. Replace it? Existing data stays, but stored passwords and API keys become unreadable if SECRET_KEY changes. [y/N] " a
  [[ "${a:-}" =~ ^[Yy] ]] || { say "Keeping .env. Starting CubixOne..."; docker compose up -d --build; exit 0; }
fi

PORT=8080; CHOICE=1
if [ "$AUTO" = no ]; then
  read -rp "Port to serve CubixOne on [8080]: " p; PORT=${p:-8080}
  echo
  say "Where should user accounts, folders, permissions and settings be stored?"
  echo "  1) Built-in MySQL - started with CubixOne, nothing else to install (recommended)"
  echo "  2) My own database - PostgreSQL, SQL Server or MySQL that I already run"
  read -rp "Choose 1 or 2 [1]: " CHOICE; CHOICE=${CHOICE:-1}
fi

MYSQL_PASSWORD=""; MYSQL_ROOT_PASSWORD=""; PROFILES=""
if [ "$CHOICE" = 2 ]; then
  echo "  Database type: 1) PostgreSQL  2) SQL Server  3) MySQL / MariaDB"
  read -rp "  Choose [1]: " T
  read -rp "  Host: " H
  read -rp "  Port (blank = default): " P
  read -rp "  Database name: " DBN
  read -rp "  Username: " U
  read -rsp "  Password: " PW; echo
  case "${T:-1}" in
    2) DRV=mssql+pymssql;      P=${P:-1433} ;;
    3) DRV=mysql+pymysql;      P=${P:-3306} ;;
    *) DRV=postgresql+psycopg; P=${P:-5432} ;;
  esac
  DATABASE_URL="$DRV://$(urlencode "$U"):$(urlencode "$PW")@$H:$P/$(urlencode "$DBN")"
  echo "  CubixOne will create its tables (all named cx_...) in that database on first start."
else
  MYSQL_PASSWORD=$(rand); MYSQL_ROOT_PASSWORD=$(rand); PROFILES=mysql
  DATABASE_URL="mysql+pymysql://cubix:$MYSQL_PASSWORD@mysql:3306/cubixone?charset=utf8mb4"
fi

umask 077
cat > .env <<EOF
# Written by install.sh on $(date -u +%Y-%m-%d). Keep this file private and back it up.
PORT=$PORT
# Signs sessions and encrypts stored database passwords and API keys. Never change it after setup.
SECRET_KEY=$(rand)
DATABASE_URL=$DATABASE_URL
COMPOSE_PROFILES=$PROFILES
MYSQL_PASSWORD=$MYSQL_PASSWORD
MYSQL_ROOT_PASSWORD=$MYSQL_ROOT_PASSWORD
# true once CubixOne is served over HTTPS
COOKIE_SECURE=false
# Optional: shared secret for live report services calling /api/auth/check
SERVICE_AUTH_KEY=
EOF
mkdir -p skills

say "Building and starting CubixOne (the first build takes a few minutes)..."
docker compose up -d --build
say "CubixOne is starting on http://localhost:$PORT"
echo "Open it and create the first administrator. Logs: docker compose logs -f app"

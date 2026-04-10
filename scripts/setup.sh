#!/usr/bin/env bash
# One-shot local setup: Python deps, Node/Prisma client, and schema sync.
# Prerequisites: fill .env completely (see .env.example), install uv and Node (npm).

set -eo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

ENV_FILE="$ROOT/.env"

die() {
  echo "setup.sh: $*" >&2
  exit 1
}

command -v uv >/dev/null 2>&1 || die "uv not found. Install: https://github.com/astral-sh/uv"
command -v npm >/dev/null 2>&1 || die "npm not found. Install Node.js (includes npm)."

[[ -f "$ENV_FILE" ]] || die "missing .env — copy .env.example to .env and set all required values."

# Export vars for child processes (Prisma reads SUPABASE_* from the environment).
set +H
set -a
# shellcheck disable=1090
source "$ENV_FILE"
set +a

# Required for backend + DB push + storage defaults
required_vars=(
  OPENAI_API_KEY
  SUPABASE_PROJECT_URL
  SUPABASE_ANNON_KEY
  SUPABASE_SERVICE_ROLE_KEY
  SUPABASE_DATABASE_URL
  SUPABASE_DIRECT_URL
  OBJECTS_STORAGE_BUCKET
  OBJECTS_STORAGE_BASE_PATH
)

for key in "${required_vars[@]}"; do
  val="${!key}"
  if [[ -z "${val// /}" ]]; then
    die "missing or empty ${key} in .env (see .env.example)."
  fi
done

# Catch untouched template values
if [[ "$SUPABASE_PROJECT_URL" == *"YOUR_PROJECT_REF"* ]]; then
  die "SUPABASE_PROJECT_URL still looks like a placeholder; set real values in .env."
fi
if [[ "$SUPABASE_DATABASE_URL" == *"YOUR_PROJECT_REF"* ]] || [[ "$SUPABASE_DATABASE_URL" == *"YOUR_DB_PASSWORD"* ]]; then
  die "SUPABASE_DATABASE_URL still looks like a placeholder; set real values in .env."
fi
if [[ "$SUPABASE_DIRECT_URL" == *"YOUR_PROJECT_REF"* ]] || [[ "$SUPABASE_DIRECT_URL" == *"YOUR_DB_PASSWORD"* ]]; then
  die "SUPABASE_DIRECT_URL still looks like a placeholder; set real values in .env."
fi

echo "==> Python venv + packages (uv)"
if [[ ! -d "$ROOT/.venv" ]]; then
  uv venv
fi
uv pip install -r requirements.txt
uv pip install crawl4ai

echo "==> npm install (Prisma JS client)"
npm install

echo "==> Prisma db push"
uv run prisma db push --schema=src/prisma/schema.prisma

echo ""
echo "Done. Start API with:"
echo "  cd \"$ROOT\" && uv run uvicorn src.main:app --reload"

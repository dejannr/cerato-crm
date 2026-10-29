#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

if [[ ! -f .env ]]; then
  echo 'Missing .env. Copy .env.example to .env and configure it first.' >&2
  exit 1
fi
if [[ ! -x .venv/bin/python ]] || [[ ! -x .venv/bin/uvicorn ]]; then
  echo 'Python dependencies are missing. See README.md for setup.' >&2
  exit 1
fi
if [[ ! -x frontend/node_modules/.bin/vite ]]; then
  echo 'Frontend dependencies are missing. Run: npm install --prefix frontend' >&2
  exit 1
fi
if ! .venv/bin/python -c 'from backend.main import engine; engine.connect().close()' >/dev/null 2>&1; then
  echo 'Cannot connect to PostgreSQL. Check that it is running and DATABASE_URL in .env is correct.' >&2
  exit 1
fi

pids=()
cleanup() {
  trap - EXIT INT TERM
  if ((${#pids[@]})); then
    kill "${pids[@]}" 2>/dev/null || true
    wait "${pids[@]}" 2>/dev/null || true
  fi
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

.venv/bin/uvicorn backend.main:app --host 127.0.0.1 --port 8000 &
pids+=("$!")
frontend/node_modules/.bin/vite --config frontend/vite.config.js frontend --host 127.0.0.1 --strictPort &
pids+=("$!")
echo 'Cerato CRM starting at http://127.0.0.1:5173 (Ctrl+C to stop).'

# Bash on macOS does not provide wait -n. Monitor each child instead.
while :; do
  for pid in "${pids[@]}"; do
    if ! kill -0 "$pid" 2>/dev/null; then
      echo 'A service stopped. Shutting down the remaining services.' >&2
      exit 1
    fi
  done
  sleep 2
done

#!/usr/bin/env bash
# One-command bring-up. Creates a venv on first run, installs deps, starts the server.
set -euo pipefail

cd "$(dirname "$0")"

if [ ! -d ".venv" ]; then
  echo "[run.sh] creating .venv"
  python3 -m venv .venv
fi

# shellcheck disable=SC1091
source .venv/bin/activate

echo "[run.sh] installing python deps"
pip install -q --upgrade pip
pip install -q -r backend/requirements.txt

# The Python SDK shells out to the `claude` CLI, so it must be installed.
if ! command -v claude >/dev/null 2>&1; then
  cat <<EOF

WARNING: the 'claude' CLI was not found on PATH.
The Python claude-agent-sdk spawns it as a subprocess, so the pipeline will
fail to start without it. Install it with:

    npm install -g @anthropic-ai/claude-code

Then either log in once with:
    claude   # follow the auth prompt
or export an API key:
    export ANTHROPIC_API_KEY=sk-ant-...

EOF
fi

echo "[run.sh] starting server on http://127.0.0.1:8000"
cd backend
exec uvicorn main:app --host 127.0.0.1 --port 8000 --reload

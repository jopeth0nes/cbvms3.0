#!/usr/bin/env bash
# Run CBVMS with the project virtualenv.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ ! -d .venv ]]; then
  echo "No .venv found. Run: ./scripts/install.sh"
  exit 1
fi

if [[ -f .venv/bin/activate ]]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
elif [[ -f .venv/Scripts/activate ]]; then
  # shellcheck disable=SC1091
  source .venv/Scripts/activate
else
  echo "No venv activate script found in .venv"
  exit 1
fi

if ! python -c "import tkinter" 2>/dev/null; then
  echo "tkinter missing. On macOS run: brew install python-tk@3.12"
  exit 1
fi

exec python main.py

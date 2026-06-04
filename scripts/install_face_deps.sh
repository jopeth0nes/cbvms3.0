#!/usr/bin/env bash
# Install dlib + face-recognition on macOS Apple Silicon.
# Uses Homebrew libpng so dlib skips its broken bundled libpng (fp.h / NEON errors).

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ ! -d .venv ]]; then
  echo "Create a venv first: python3.12 -m venv .venv && source .venv/bin/activate"
  exit 1
fi

# shellcheck disable=SC1091
source .venv/bin/activate

python -m pip install --upgrade pip wheel
python -m pip install "setuptools>=70,<82"

if [[ "$(uname -s)" == "Darwin" ]]; then
  if ! brew list libpng &>/dev/null; then
    echo "Installing libpng via Homebrew (required for dlib on Apple Silicon)..."
    brew install libpng
  fi
  export CMAKE_PREFIX_PATH="$(brew --prefix)"
  export PKG_CONFIG_PATH="$(brew --prefix)/lib/pkgconfig:${PKG_CONFIG_PATH:-}"
  echo "Using system libpng from: $(brew --prefix)/lib"
fi

pip install --no-cache-dir dlib==19.24.6
pip install --no-cache-dir face-recognition==1.3.0

python -c "import dlib; import face_recognition; print('OK: dlib', dlib.__version__)"

echo "Face dependencies installed successfully."

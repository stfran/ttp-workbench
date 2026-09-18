#!/usr/bin/env bash
set -euo pipefail

# quick_start.sh
#
# Build the lightweight rcATT container and run the PoC smoke test.
# Intended as the first command an artifact reviewer can run.
#
# Usage:
#   bash quick_start.sh
#   bash quick_start.sh --engine docker
#   bash quick_start.sh --engine podman
#
# Optional:
#   SKIP_BUILD=1 bash quick_start.sh
#   PYTHON=python3 bash quick_start.sh --engine docker

ENGINE="docker"
PYTHON_BIN="${PYTHON:-python3}"
SKIP_BUILD="${SKIP_BUILD:-0}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --engine)
      ENGINE="${2:-}"
      shift 2
      ;;
    --engine=*)
      ENGINE="${1#*=}"
      shift
      ;;
    --skip-build)
      SKIP_BUILD="1"
      shift
      ;;
    -h|--help)
      sed -n '1,28p' "$0"
      exit 0
      ;;
    *)
      echo "[ERROR] Unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

if [[ "$ENGINE" != "docker" && "$ENGINE" != "podman" ]]; then
  echo "[ERROR] --engine must be 'docker' or 'podman'." >&2
  exit 2
fi

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

echo "[INFO] TTP-WorkBench quick start"
echo "[INFO] Project root: $ROOT_DIR"
echo "[INFO] Container engine: $ENGINE"
echo "[INFO] Python: $PYTHON_BIN"

echo "[INFO] Checking prerequisites..."
command -v "$ENGINE" >/dev/null 2>&1 || {
  echo "[ERROR] Could not find container engine '$ENGINE' on PATH." >&2
  exit 1
}

command -v "$PYTHON_BIN" >/dev/null 2>&1 || {
  echo "[ERROR] Could not find Python executable '$PYTHON_BIN' on PATH." >&2
  exit 1
}

echo "[INFO] Checking Python imports..."
"$PYTHON_BIN" - <<'PY'
import importlib
mods = [
    "Framework.utils.config",
    "Framework.adapters.base_adapter",
]
for m in mods:
    importlib.import_module(m)
print("[PASS] Framework imports succeeded")
PY

echo "[INFO] Checking container engine..."
"$ENGINE" info >/dev/null

if [[ "$SKIP_BUILD" != "1" ]]; then
  echo "[INFO] Building rcATT container..."

  # Prefer the named rcATT flag if setup_containers.sh supports it.
  if bash Docker_Setup/setup_containers.sh --help 2>&1 | grep -q -- "--rcatt"; then
    bash Docker_Setup/setup_containers.sh --engine "$ENGINE" --rcatt
  elif bash Docker_Setup/setup_containers.sh --help 2>&1 | grep -q -- "rcATT"; then
    bash Docker_Setup/setup_containers.sh --engine "$ENGINE" rcATT
  else
    echo "[WARN] Could not detect an rcATT-specific setup flag from --help."
    echo "[WARN] Falling back to: bash Docker_Setup/setup_containers.sh --engine $ENGINE --rcatt"
    bash Docker_Setup/setup_containers.sh --engine "$ENGINE" --rcatt
  fi
else
  echo "[INFO] SKIP_BUILD=1; skipping rcATT build."
fi

echo "[INFO] Running rcATT PoC smoke test..."
"$PYTHON_BIN" Tests/poc_tests/run_poc_tests.py \
  --adapter rcATT \
  --engine "$ENGINE"

echo
echo "[PASS] rcATT quick-start smoke test completed."
echo "[INFO] Results should be under: Tests/poc_tests/results/rcatt/"
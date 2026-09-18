#!/usr/bin/env bash
set -uo pipefail
SETUP=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
SUITE=$(cd "$SETUP/.." && pwd)
PROJECT=$(cd "$SUITE/../.." && pwd)
TOOLS=${REPRO_EXTERNAL_ROOT:-$SUITE/.runtime/external_tools}
CONDA_EXE=${CONDA_EXE:-$(command -v mamba || command -v conda || true)}
SCOPE=all
if [[ ${1:-} == --scope ]]; then
    [[ $# -ge 2 ]] || { echo "--scope requires core, optional, or all" >&2; exit 2; }
    SCOPE=$2
    shift 2
fi
[[ $# == 0 ]] || { echo "Unknown argument: $1" >&2; exit 2; }
[[ "$SCOPE" == core || "$SCOPE" == optional || "$SCOPE" == all ]] || {
    echo "Invalid environment scope: $SCOPE" >&2
    exit 2
}
[[ -n "$CONDA_EXE" ]] || { echo "Set CONDA_EXE or install conda/mamba" >&2; exit 1; }
mkdir -p "$TOOLS/setup_logs" "$TOOLS/cache/pip"
export PIP_CACHE_DIR="$TOOLS/cache/pip"
failed=0

install_env() {
    local name=$1 version=$2 env=$3 requirements=$4
    if [[ -n "${ONLY_ENV:-}" && "$name" != "$ONLY_ENV" ]]; then return; fi
    if [[ -z "${ONLY_ENV:-}" ]]; then
        case "$SCOPE:$name" in
            core:attackg|core:ladder|core:ttpdrill|core:ttpllm|core:buchel_attackg|core:buchel_ladder) ;;
            optional:attackg|optional:buchel) ;;
            all:*) ;;
            *) return;;
        esac
    fi
    echo "Installing $name in $env (Python $version)"
    if [[ ! -x "$env/.python/bin/python" ]]; then
        "$CONDA_EXE" create -y --override-channels -c conda-forge --prefix "$env/.python" "python=$version" pip || { failed=1; return; }
    fi
    if [[ ! -x "$env/.venv/bin/python" ]]; then
        "$env/.python/bin/python" -m venv "$env/.venv" || { failed=1; return; }
    fi
    "$env/.venv/bin/python" -m pip install -r "$requirements" > "$TOOLS/setup_logs/$name.log" 2>&1 || {
        echo "FAILED: $name; see $TOOLS/setup_logs/$name.log" >&2
        failed=1
    }
}

# Keep the project environment at its documented Python version while allowing
# an already-created root .venv to remain in place.
if [[ ! -x "$PROJECT/.venv/bin/python" ]]; then
    "$CONDA_EXE" create -y --override-channels -c conda-forge --prefix "$SUITE/.runtime/framework-python" python=3.12 pip || failed=1
    "$SUITE/.runtime/framework-python/bin/python" -m venv "$PROJECT/.venv" || failed=1
    "$PROJECT/.venv/bin/python" -m pip install -r "$PROJECT/requirements.txt" > "$TOOLS/setup_logs/framework.log" 2>&1 || failed=1
fi
install_env attackg 3.8 "$TOOLS/AttacKG" "$TOOLS/AttacKG/requirements.txt"
install_env ladder 3.10 "$TOOLS/LADDER" "$PROJECT/Docker_Setup/LADDER/requirements_updated.txt"
install_env ttpdrill 3.9 "$TOOLS/TTPDrill" "$PROJECT/Docker_Setup/TTPDrill/requirements.txt"
install_env ttpllm 3.10 "$TOOLS/TTP-LLM" "$PROJECT/Docker_Setup/TTP-LLM/requirements_updated.txt"
install_env buchel 3.10 "$TOOLS/Buchel" "$TOOLS/Buchel/generation/requirements.txt"
install_env buchel_attackg 3.10 "$TOOLS/BuchelTools/ext_tools/tools/AttacKG" "$TOOLS/BuchelTools/ext_tools/tools/AttacKG/requirements.txt"
install_env buchel_ladder 3.10 "$TOOLS/BuchelTools/ext_tools/tools/LADDER" "$TOOLS/BuchelTools/ext_tools/tools/LADDER/attack_pattern/requirements.txt"
if [[ "$SCOPE" == all ]]; then
    install_env tram 3.11 "$TOOLS/TRAM" "$PROJECT/Docker_Setup/TRAM/requirements.txt"
fi
exit "$failed"

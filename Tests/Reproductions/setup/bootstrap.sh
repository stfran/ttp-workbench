#!/usr/bin/env bash
# Connected, idempotent setup for a fresh external reproduction host.
set -euo pipefail
SETUP=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
SUITE=$(cd "$SETUP/.." && pwd)
PROJECT=$(cd "$SUITE/../.." && pwd)
ENGINE=${CONTAINER_ENGINE:-podman}
MODELS_ARCHIVE=""
usage() {
    echo "bootstrap.sh --models-archive PATH [--engine docker|podman]"
}
while [[ $# -gt 0 ]]; do
    case "$1" in
        --models-archive) MODELS_ARCHIVE=$2; shift 2;;
        --engine) ENGINE=$2; shift 2;;
        -h|--help) usage; exit 0;;
        *) echo "Unknown option: $1" >&2; usage; exit 2;;
    esac
done
[[ "$ENGINE" == docker || "$ENGINE" == podman ]] || { echo "--engine must be docker or podman" >&2; exit 2; }
export CONTAINER_ENGINE="$ENGINE"
export REPRO_EXTERNAL_ROOT=${REPRO_EXTERNAL_ROOT:-$SUITE/.runtime/external_tools}
export TFHUB_CACHE_DIR="$REPRO_EXTERNAL_ROOT/RAF-AG/data/tf_hub"
SETUP_PYTHON=${FRAMEWORK_PYTHON:-$PROJECT/.venv/bin/python}
[[ -x "$SETUP_PYTHON" ]] || SETUP_PYTHON=python3
[[ -n "$MODELS_ARCHIVE" ]] || { echo "--models-archive is required" >&2; exit 2; }
[[ -f "$MODELS_ARCHIVE" ]] || { echo "models.zip not found: $MODELS_ARCHIVE" >&2; exit 2; }
MODELS_ARCHIVE=$(realpath "$MODELS_ARCHIVE")
export TTPWB_RELEASE_ASSETS_ROOT=$SUITE/.runtime/release_assets
python3 "$SETUP/stage_release_archive.py" \
    --archive "$MODELS_ARCHIVE" \
    --output-root "$TTPWB_RELEASE_ASSETS_ROOT" \
    --external-root "$REPRO_EXTERNAL_ROOT"

BUCHEL_MODELS_ARCHIVE=$TTPWB_RELEASE_ASSETS_ROOT/buchel/sft_bosch.zip
ORBINATO_MODELS_ARCHIVE=$TTPWB_RELEASE_ASSETS_ROOT/orbinato/models.zip
export BUCHEL_MODELS_SHA256
BUCHEL_MODELS_SHA256=$(sha256sum "$BUCHEL_MODELS_ARCHIVE" | awk '{print $1}')
export ORBINATO_MODELS_SHA256
ORBINATO_MODELS_SHA256=$(sha256sum "$ORBINATO_MODELS_ARCHIVE" | awk '{print $1}')
export ORBINATO_ADDITIONAL_FILES_SHA256
ORBINATO_ADDITIONAL_FILES_SHA256=$(sha256sum "$TTPWB_RELEASE_ASSETS_ROOT/orbinato/additional_files.zip" | awk '{print $1}')
export SEQMASK_ARTIFACT_SHA256
SEQMASK_ARTIFACT_SHA256=$(sha256sum "$TTPWB_RELEASE_ASSETS_ROOT/seqmask/models.zip" | awk '{print $1}')

python3 "$SETUP/stage_models_archive.py" --archive "$BUCHEL_MODELS_ARCHIVE" --archive-sha256 "$BUCHEL_MODELS_SHA256" --buchel-model bosch_sentence --external-root "$REPRO_EXTERNAL_ROOT"
python3 "$SETUP/stage_models_archive.py" --archive "$ORBINATO_MODELS_ARCHIVE" --archive-sha256 "$ORBINATO_MODELS_SHA256" --external-root "$REPRO_EXTERNAL_ROOT"
bash "$SETUP/setup_sources.sh"

(
    cd "$PROJECT/Docker_Setup"
    bash setup_containers.sh --engine "$ENGINE" --attackg --ladder --orbinato --raf-ag --rcatt --seqmask --ttp-llm --ttpdrill
)

# Build Buchel from the already-downloaded public generation archive and the
# supplied checkpoints already verified above.
buchel_flag=--podman
[[ "$ENGINE" == docker ]] && buchel_flag=--docker
(
    cd "$PROJECT/Docker_Setup/Buchel"
    ZIP_URL="file://$REPRO_EXTERNAL_ROOT/downloads/generation.zip" \
    BUCHEL_EXT_TOOLS_URL="file://$REPRO_EXTERNAL_ROOT/downloads/ext_tools.zip" \
        bash setup_buchel.sh "$buchel_flag" --workdir "$REPRO_EXTERNAL_ROOT/Buchel/container_build"
)

"$SETUP_PYTHON" "$PROJECT/Datasets/compile_data.py" \
    --reproduction-sources-only \
    --buchel-ext-tools-root "$REPRO_EXTERNAL_ROOT/BuchelTools/ext_tools"
"$SETUP_PYTHON" "$PROJECT/Datasets/compile_reproduction_data.py"
python3 "$SETUP/setup_optional.py" --stage --engine "$ENGINE"
bash "$SETUP/setup_environments.sh"
python3 "$SETUP/setup_optional.py" --environments --engine "$ENGINE"
bash "$SETUP/setup_runtime_assets.sh"
python3 "$SETUP/setup_ollama.py" --engine "$ENGINE"
echo "Bootstrap complete. Run $SUITE/run_all.sh manually to start the experiments."

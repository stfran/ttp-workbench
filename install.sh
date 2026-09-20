#!/usr/bin/env bash
# Install the framework, tool containers, data, and reproduction runtimes.
set -euo pipefail

PROJECT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
ENGINE=docker
PYTHON_BIN=${PYTHON:-python3}
MODELS_ARCHIVE=""
MODELS_SHA1=""
MODELS_DOWNLOAD_URL=""
SELECTION=all
SELECTION_SET=false
OPENAI_CONFIG=$PROJECT/config.ini

usage() {
    cat <<'EOF'
Usage: install.sh [--core | --optional | --all] [options]

Options:
  --core                         Build the four core adapter containers
  --optional                     Build optional containers and shared AttacKG
  --all                          Build all ten adapter containers (default)
  --engine docker|podman          Container engine (default: docker)
  --python PATH                   Python used to create .venv (default: $PYTHON or python3)
  --models-archive PATH           Use an already-downloaded monolithic models.zip
  -h, --help                      Show this help

If --models-archive is omitted, installation downloads models.zip once using
the URL and SHA-1 in artifact_sources.env. The archive is then staged locally
for every container build, reproduction, and saved-prediction check.
ZENODO_CURL_CONFIG may override curl configuration for an authenticated mirror.

The --core and --all paths use TTP-LLM and therefore configure an OpenAI API
key before installation begins. Interactive runs prompt without echoing the key and
store it in the ignored repository-level config.ini with mode 0600. An
unattended run must provide that file in advance; see config.example.ini.
EOF
}

preflight() {
    local command_name
    local -a missing=()

    for command_name in git curl unzip rsync sha1sum sha256sum java; do
        command -v "$command_name" >/dev/null 2>&1 || missing+=("$command_name")
    done
    if ((${#missing[@]})); then
        echo "Missing required host commands: ${missing[*]}" >&2
        exit 1
    fi
    if ! command -v mamba >/dev/null 2>&1 && ! command -v conda >/dev/null 2>&1; then
        echo "Conda or Mamba is required for the isolated native environments" >&2
        exit 1
    fi
    command -v "$ENGINE" >/dev/null 2>&1 || {
        echo "Container engine not found: $ENGINE" >&2
        exit 1
    }
    "$ENGINE" info >/dev/null 2>&1 || {
        echo "Container engine is unavailable: $ENGINE" >&2
        exit 1
    }
    "$ENGINE" compose version >/dev/null 2>&1 || {
        echo "$ENGINE Compose support is required" >&2
        exit 1
    }
    "$PYTHON_BIN" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)' || {
        echo "Python 3.12 or later is required: $PYTHON_BIN" >&2
        exit 1
    }
}

config_value() {
    local key=$1
    local path=$2
    [[ -f "$path" ]] || return 0
    awk -F= -v wanted="$key" '
        BEGIN { in_api = 0 }
        /^[[:space:]]*\[[Aa][Pp][Ii]\][[:space:]]*$/ { in_api = 1; next }
        /^[[:space:]]*\[/ { in_api = 0 }
        in_api {
            name = $1
            gsub(/^[[:space:]]+|[[:space:]]+$/, "", name)
            if (tolower(name) == tolower(wanted)) {
                value = substr($0, index($0, "=") + 1)
                gsub(/^[[:space:]]+|[[:space:]]+$/, "", value)
                print value
                exit
            }
        }
    ' "$path"
}

valid_openai_key() {
    [[ $1 =~ ^sk-[A-Za-z0-9_-]{10,}$ ]]
}

write_openai_config() {
    local openai_key=$1
    local huggingface_key=$2
    local temporary
    temporary=$(mktemp "$PROJECT/.config.ini.XXXXXX")
    chmod 0600 "$temporary"
    {
        printf '[API]\n'
        printf 'OpenAI_Key = %s\n' "$openai_key"
        printf 'HuggingFace_Key = %s\n' "$huggingface_key"
    } > "$temporary"
    mv -f -- "$temporary" "$OPENAI_CONFIG"
    chmod 0600 "$OPENAI_CONFIG"
}

configure_openai_secret() {
    local existing_openai=""
    local existing_huggingface=""
    local entered=""
    if [[ -f "$OPENAI_CONFIG" ]]; then
        existing_openai=$(config_value OpenAI_Key "$OPENAI_CONFIG")
        existing_huggingface=$(config_value HuggingFace_Key "$OPENAI_CONFIG")
    fi

    cat <<'EOF'

OpenAI access for TTP-LLM
-------------------------
The core claims and the all-adapter smoke tests call the
gpt-3.5-turbo API. Their combined API cost is conservatively estimated at
US$1.50, although actual cost depends on model pricing and generated tokens.

Create a project API key at:
  https://platform.openai.com/api-keys
Sign in, select the project that will fund the evaluation, choose to create a
new secret key, and copy it when it is shown. OpenAI does not show the complete
secret again after that screen.

The key is saved only in this repository's ignored config.ini with mode 0600.
It is not printed to the terminal or placed in container command arguments.
EOF

    if [[ ! -t 0 ]]; then
        if valid_openai_key "$existing_openai"; then
            echo "Using the OpenAI API key already present in config.ini."
            chmod 0600 "$OPENAI_CONFIG"
            return 0
        fi
        echo "No valid OpenAI API key is available for this unattended installation." >&2
        echo "Create $OPENAI_CONFIG from config.example.ini, set OpenAI_Key, and rerun install.sh." >&2
        return 1
    fi

    if valid_openai_key "$existing_openai"; then
        if ! IFS= read -r -s -p "OpenAI API key (press Enter to keep the existing key): " entered; then
            printf '\n' >&2
            echo "OpenAI API key entry was cancelled." >&2
            return 1
        fi
        printf '\n'
        if [[ -z "$entered" ]]; then
            echo "Keeping the OpenAI API key already present in config.ini."
            chmod 0600 "$OPENAI_CONFIG"
            return 0
        fi
    else
        if ! IFS= read -r -s -p "OpenAI API key: " entered; then
            printf '\n' >&2
            echo "OpenAI API key entry was cancelled." >&2
            return 1
        fi
        printf '\n'
    fi

    if ! valid_openai_key "$entered"; then
        echo "The OpenAI API key must start with 'sk-' and contain no spaces." >&2
        return 1
    fi
    write_openai_config "$entered" "$existing_huggingface"
    unset entered existing_openai existing_huggingface
    echo "Saved OpenAI API configuration to config.ini (mode 0600)."
}

configure_openai() {
    [[ "$SELECTION" == core || "$SELECTION" == all ]] || return 0

    # Keep credentials out of bash xtrace output even if installation is invoked with
    # `bash -x`. Always restore the caller's tracing state before returning.
    local tracing=false
    local code=0
    [[ $- == *x* ]] && tracing=true
    $tracing && set +x
    if configure_openai_secret; then
        code=0
    else
        code=$?
    fi
    $tracing && set -x
    return "$code"
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --core|--optional|--all)
            if $SELECTION_SET; then
                echo "Choose only one of --core, --optional, or --all" >&2
                exit 2
            fi
            SELECTION=${1#--}
            SELECTION_SET=true
            shift
            ;;
        --engine|--python|--models-archive)
            [[ $# -ge 2 ]] || { echo "Missing value for $1" >&2; usage; exit 2; }
            case "$1" in
                --engine) ENGINE=$2;;
                --python) PYTHON_BIN=$2;;
                --models-archive) MODELS_ARCHIVE=$2;;
            esac
            shift 2
            ;;
        -h|--help) usage; exit 0;;
        *) echo "Unknown option: $1" >&2; usage; exit 2;;
    esac
done

[[ "$ENGINE" == docker || "$ENGINE" == podman ]] || {
    echo "--engine must be docker or podman" >&2
    exit 2
}
if [[ "$PYTHON_BIN" == */* ]]; then
    PYTHON_BIN=$(realpath "$PYTHON_BIN")
fi
command -v "$PYTHON_BIN" >/dev/null 2>&1 || {
    echo "Python interpreter not found: $PYTHON_BIN" >&2
    exit 2
}
preflight

if [[ -n "$MODELS_ARCHIVE" ]]; then
    [[ -f "$MODELS_ARCHIVE" ]] || { echo "models.zip not found: $MODELS_ARCHIVE" >&2; exit 1; }
    MODELS_ARCHIVE=$(realpath "$MODELS_ARCHIVE")
else
    ARTIFACT_CONFIG=${TTPWB_ARTIFACT_CONFIG:-$PROJECT/artifact_sources.env}
    [[ -r "$ARTIFACT_CONFIG" ]] || {
        echo "Artifact configuration not found: $ARTIFACT_CONFIG" >&2
        exit 1
    }
    # shellcheck source=artifact_sources.env
    source "$ARTIFACT_CONFIG"
    MODELS_DOWNLOAD_URL=${TTPWB_MODELS_URL:-}
    MODELS_SHA1=${TTPWB_MODELS_SHA1:-}
    [[ -n "$MODELS_DOWNLOAD_URL" && "$MODELS_SHA1" =~ ^[[:xdigit:]]{40}$ ]] || {
        echo "artifact_sources.env must define TTPWB_MODELS_URL and a 40-digit TTPWB_MODELS_SHA1" >&2
        echo "Alternatively, pass --models-archive PATH." >&2
        exit 1
    }
    MODELS_SHA1=${MODELS_SHA1,,}
    MODELS_ARCHIVE=$PROJECT/Tests/Reproductions/.runtime/downloads/models.zip
fi

configure_openai

if [[ -n "$MODELS_DOWNLOAD_URL" ]]; then
    mkdir -p "$(dirname "$MODELS_ARCHIVE")"
    if ! echo "$MODELS_SHA1  $MODELS_ARCHIVE" | sha1sum -c - >/dev/null 2>&1; then
        MODELS_PARTIAL=$MODELS_ARCHIVE.part
        CURL_ARGS=(--fail --location --show-error --progress-bar --retry 5 --retry-delay 5 --continue-at - --output "$MODELS_PARTIAL")
        if [[ -n "${ZENODO_CURL_CONFIG:-}" ]]; then
            [[ -r "$ZENODO_CURL_CONFIG" ]] || { echo "ZENODO_CURL_CONFIG is not readable" >&2; exit 1; }
            CURL_ARGS=(--config "$ZENODO_CURL_CONFIG" "${CURL_ARGS[@]}")
        fi
        curl "${CURL_ARGS[@]}" "$MODELS_DOWNLOAD_URL"
        mv -f "$MODELS_PARTIAL" "$MODELS_ARCHIVE"
    else
        echo "Using cached models.zip"
    fi
    echo "$MODELS_SHA1  $MODELS_ARCHIVE" | sha1sum -c -
fi

export TTPWB_RELEASE_ASSETS_ROOT=$PROJECT/Tests/Reproductions/.runtime/release_assets
RELEASE_ASSETS=$TTPWB_RELEASE_ASSETS_ROOT
REPRO_EXTERNAL_ROOT=${REPRO_EXTERNAL_ROOT:-$PROJECT/Tests/Reproductions/.runtime/external_tools}
stage_command=(
    "$PYTHON_BIN" "$PROJECT/Tests/Reproductions/setup/stage_release_archive.py"
    --archive "$MODELS_ARCHIVE"
    --output-root "$RELEASE_ASSETS"
    --external-root "$REPRO_EXTERNAL_ROOT"
)
"${stage_command[@]}"

BUCHEL_MODELS_ARCHIVE=$RELEASE_ASSETS/buchel/sft_bosch.zip
ORBINATO_MODELS_ARCHIVE=$RELEASE_ASSETS/orbinato/models.zip
BENCHMARK_RESULTS_ARCHIVE=$RELEASE_ASSETS/benchmark/results.zip
ORBINATO_ADDITIONAL_FILES_ARCHIVE=$RELEASE_ASSETS/orbinato/additional_files.zip
SEQMASK_ARTIFACT_ARCHIVE=$RELEASE_ASSETS/seqmask/models.zip
export BUCHEL_MODELS_SHA256
BUCHEL_MODELS_SHA256=$(sha256sum "$BUCHEL_MODELS_ARCHIVE" | awk '{print $1}')
export ORBINATO_MODELS_SHA256
ORBINATO_MODELS_SHA256=$(sha256sum "$ORBINATO_MODELS_ARCHIVE" | awk '{print $1}')
export ORBINATO_ADDITIONAL_FILES_SHA256
ORBINATO_ADDITIONAL_FILES_SHA256=$(sha256sum "$ORBINATO_ADDITIONAL_FILES_ARCHIVE" | awk '{print $1}')
export SEQMASK_ARTIFACT_SHA256
SEQMASK_ARTIFACT_SHA256=$(sha256sum "$SEQMASK_ARTIFACT_ARCHIVE" | awk '{print $1}')
BENCHMARK_RESULTS_SHA256=$(sha256sum "$BENCHMARK_RESULTS_ARCHIVE" | awk '{print $1}')

export CONTAINER_ENGINE=$ENGINE
export BUCHEL_WORKDIR=${BUCHEL_WORKDIR:-$PROJECT/Docker_Setup/Buchel/buchel_generation}
export REPRO_EXTERNAL_ROOT=${REPRO_EXTERNAL_ROOT:-$PROJECT/Tests/Reproductions/.runtime/external_tools}
export TFHUB_CACHE_DIR=${TFHUB_CACHE_DIR:-$REPRO_EXTERNAL_ROOT/RAF-AG/data/tf_hub}

echo "[1/11] Creating the framework environment"
if [[ ! -x "$PROJECT/.venv/bin/python" ]]; then
    "$PYTHON_BIN" -m venv "$PROJECT/.venv"
fi
FRAMEWORK_PYTHON=$PROJECT/.venv/bin/python
"$FRAMEWORK_PYTHON" -m pip install --upgrade "pip>=21.3"
"$FRAMEWORK_PYTHON" -m pip install -e "$PROJECT" -r "$PROJECT/requirements.txt"
export FRAMEWORK_PYTHON

echo "Staging pinned MITRE ATT&CK STIX snapshots"
(
    cd "$PROJECT"
    "$FRAMEWORK_PYTHON" -m Framework.utils.attack_lookup --ensure
)

echo "[2/11] Building selected tool containers with $ENGINE"
case "$SELECTION" in
    core) CONTAINER_ARGS=(--attackg --ttpdrill --ladder --ttp-llm);;
    optional) CONTAINER_ARGS=(--buchel --orbinato --rcatt --raf-ag --seqmask --attackg);;
    all) CONTAINER_ARGS=(--all);;
esac
echo "Container setup selection: $SELECTION"
(
    cd "$PROJECT/Docker_Setup"
    bash setup_containers.sh --engine "$ENGINE" "${CONTAINER_ARGS[@]}"
)

EXT_TOOLS_ROOT="$BUCHEL_WORKDIR/ext_tools"

echo "[3/11] Staging supplied model archives for native reproduction paths"
if [[ "$SELECTION" == all || "$SELECTION" == optional ]]; then
    # Reuse the component archives extracted from the single models.zip.
    "$FRAMEWORK_PYTHON" "$PROJECT/Tests/Reproductions/setup/stage_models_archive.py" \
        --archive "$BUCHEL_MODELS_ARCHIVE" \
        --archive-sha256 "$BUCHEL_MODELS_SHA256" \
        --buchel-model bosch_sentence \
        --external-root "$REPRO_EXTERNAL_ROOT"
    "$FRAMEWORK_PYTHON" "$PROJECT/Tests/Reproductions/setup/stage_models_archive.py" \
        --archive "$ORBINATO_MODELS_ARCHIVE" \
        --archive-sha256 "$ORBINATO_MODELS_SHA256" \
        --external-root "$REPRO_EXTERNAL_ROOT"
else
    echo "Supplied model staging is not required by --core"
fi

echo "[4/11] Staging pinned native sources"
if [[ "$SELECTION" == all || "$SELECTION" == optional ]]; then
    "$FRAMEWORK_PYTHON" "$PROJECT/Tests/Reproductions/setup/setup_optional.py" \
        --stage --engine "$ENGINE"
fi
PYTHON="$FRAMEWORK_PYTHON" bash "$PROJECT/Tests/Reproductions/setup/setup_sources.sh" --scope "$SELECTION"

echo "[5/11] Creating native core/shared reproduction environments"
bash "$PROJECT/Tests/Reproductions/setup/setup_environments.sh" --scope "$SELECTION"

echo "[6/11] Creating optional native reproduction environments"
if [[ "$SELECTION" == all || "$SELECTION" == optional ]]; then
    "$FRAMEWORK_PYTHON" "$PROJECT/Tests/Reproductions/setup/setup_optional.py" \
        --environments --engine "$ENGINE"
else
    echo "Optional native environments are not required by --core"
fi

echo "[7/11] Staging native runtime assets"
bash "$PROJECT/Tests/Reproductions/setup/setup_runtime_assets.sh" --scope "$SELECTION"

echo "[8/11] Preparing the optional Büchel embedding service"
if [[ "$SELECTION" == all || "$SELECTION" == optional ]]; then
    "$FRAMEWORK_PYTHON" "$PROJECT/Tests/Reproductions/setup/setup_ollama.py" --engine "$ENGINE"
else
    echo "The Büchel embedding service is not required by --core"
fi

echo "[9/11] Compiling benchmark data and collecting reproduction inputs"
if [[ "$SELECTION" == all ]]; then
    DATA_REUSE_ARG=--skip-existing
else
    # Complete benchmark data uses source material from the full container set.
    # A partial setup may reuse it, but must never silently create partial data.
    DATA_REUSE_ARG=--existing-only
fi
(
    cd "$PROJECT/Datasets"
    "$FRAMEWORK_PYTHON" compile_data.py "$DATA_REUSE_ARG" \
        --buchel-ext-tools-root "$EXT_TOOLS_ROOT"
)

echo "[10/11] Compiling Tests/Reproductions/data"
"$FRAMEWORK_PYTHON" "$PROJECT/Datasets/compile_reproduction_data.py" --skip-existing

echo "[11/11] Staging saved predictions for benchmark-figure reproduction"
BENCHMARK_DEST=$PROJECT/Tests/Benchmark_results/benchmark_results.zip
if [[ "$BENCHMARK_RESULTS_ARCHIVE" != "$BENCHMARK_DEST" ]]; then
    cp -f "$BENCHMARK_RESULTS_ARCHIVE" "$BENCHMARK_DEST"
fi
echo "$BENCHMARK_RESULTS_SHA256  $BENCHMARK_DEST" | sha256sum -c -
"$FRAMEWORK_PYTHON" "$PROJECT/Tests/Benchmark_results/reproduce_benchmark_figures.py" \
    --archive "$BENCHMARK_DEST" \
    --results-root "$PROJECT/Tests/Benchmark_results/results" \
    --verify-only

if [[ "${TTPWB_AE_ENVELOPE:-}" == 1 ]]; then
    echo "TTP-WorkBench installation complete."
else
    echo "Installation complete for --$SELECTION. Run 'bash claims/claims.sh --$SELECTION'."
fi

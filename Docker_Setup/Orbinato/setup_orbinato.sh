#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
RELEASE_ASSETS_ROOT=${TTPWB_RELEASE_ASSETS_ROOT:-}

ENGINE=podman
IMAGE_TAG=ttp-workbench:orbinato
MODELS_URL=${ORBINATO_MODELS_URL:-}
MODELS_SHA256=${ORBINATO_MODELS_SHA256:-}
MODELS_SOURCE=${ORBINATO_MODELS_SOURCE:-}
if [[ -z "$MODELS_SOURCE" && -n "$RELEASE_ASSETS_ROOT" ]]; then
    MODELS_SOURCE=$RELEASE_ASSETS_ROOT/orbinato/models.zip
fi
ADDITIONAL_FILES_URL=${ORBINATO_ADDITIONAL_FILES_URL:-}
ADDITIONAL_FILES_SHA256=${ORBINATO_ADDITIONAL_FILES_SHA256:-}
ADDITIONAL_FILES_SOURCE=${ORBINATO_ADDITIONAL_FILES_SOURCE:-}
if [[ -z "$ADDITIONAL_FILES_SOURCE" && -n "$RELEASE_ASSETS_ROOT" ]]; then
    ADDITIONAL_FILES_SOURCE=$RELEASE_ASSETS_ROOT/orbinato/additional_files.zip
fi
FORCE_REDOWNLOAD=false

usage() {
    cat <<'EOF'
Usage: setup_orbinato.sh [options]

Options:
  --engine docker|podman   Container engine (default: podman)
  --tag IMAGE             Image tag (default: ttp-workbench:orbinato)
  --models-url URL         Override the model archive URL
  --models-archive PATH    Use a local archive instead of downloading it
  --additional-files-url URL
                           Override the additional-files archive URL
  --additional-files-archive PATH
                           Use a local additional-files archive
  --force-redownload       Replace the cached archives
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --engine) ENGINE=$2; shift 2;;
        --tag) IMAGE_TAG=$2; shift 2;;
        --models-url) MODELS_URL=$2; shift 2;;
        --models-archive) MODELS_SOURCE=$2; shift 2;;
        --additional-files-url) ADDITIONAL_FILES_URL=$2; shift 2;;
        --additional-files-archive) ADDITIONAL_FILES_SOURCE=$2; shift 2;;
        --force-redownload) FORCE_REDOWNLOAD=true; shift;;
        -h|--help) usage; exit 0;;
        *) echo "Unknown option: $1" >&2; usage; exit 2;;
    esac
done
[[ "$ENGINE" == docker || "$ENGINE" == podman ]] || { echo "--engine must be docker or podman" >&2; exit 2; }
[[ -n "$MODELS_SHA256" && ( -n "$MODELS_SOURCE" || -n "$MODELS_URL" ) ]] || {
    echo "Provide a local Orbinato model archive and checksum or configure its download source" >&2
    exit 1
}
[[ -n "$ADDITIONAL_FILES_SHA256" && ( -n "$ADDITIONAL_FILES_SOURCE" || -n "$ADDITIONAL_FILES_URL" ) ]] || {
    echo "Provide a local Orbinato additional-files archive and checksum or configure its download source" >&2
    exit 1
}

download_artifact() {
    local url=$1
    local output=$2
    local curl_args=(--fail --location --show-error --progress-bar --retry 5 --retry-delay 5 --continue-at - --output "$output")
    if [[ -n "${ZENODO_CURL_CONFIG:-}" ]]; then
        [[ -r "$ZENODO_CURL_CONFIG" ]] || { echo "ZENODO_CURL_CONFIG is not readable" >&2; exit 1; }
        curl_args=(--config "$ZENODO_CURL_CONFIG" "${curl_args[@]}")
    fi
    curl "${curl_args[@]}" "$url"
}

stage_artifact() {
    local label=$1
    local url=$2
    local checksum=$3
    local source=$4
    local destination=$5

    if [[ -n "$source" ]]; then
        source=$(realpath "$source")
        [[ -f "$source" ]] || { echo "$label archive not found: $source" >&2; exit 1; }
        if [[ "$source" != "$destination" ]]; then
            cp -f "$source" "$destination"
        fi
    elif [[ "$FORCE_REDOWNLOAD" == true ]] || ! echo "$checksum  $destination" | sha256sum -c - >/dev/null 2>&1; then
        local partial=$destination.part
        echo "Downloading $label archive..."
        download_artifact "$url" "$partial"
        mv -f "$partial" "$destination"
    else
        echo "Using cached $label archive"
    fi

    echo "$checksum  $destination" | sha256sum -c -
}

stage_artifact "Orbinato models" "$MODELS_URL" "$MODELS_SHA256" "$MODELS_SOURCE" "$SCRIPT_DIR/orbinato_models.zip"
stage_artifact "Orbinato additional files" "$ADDITIONAL_FILES_URL" "$ADDITIONAL_FILES_SHA256" "$ADDITIONAL_FILES_SOURCE" "$SCRIPT_DIR/additional_files.zip"

"$ENGINE" build \
    --build-arg "ORBINATO_MODELS_SHA256=$MODELS_SHA256" \
    --build-arg "ORBINATO_ADDITIONAL_FILES_SHA256=$ADDITIONAL_FILES_SHA256" \
    -t "$IMAGE_TAG" "$SCRIPT_DIR"

#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
RELEASE_ASSETS_ROOT=${TTPWB_RELEASE_ASSETS_ROOT:-}

ENGINE=podman
IMAGE_TAG=ttp-workbench:seqmask
ARTIFACT_URL=${SEQMASK_ARTIFACT_URL:-}
ARTIFACT_SHA256=${SEQMASK_ARTIFACT_SHA256:-}
ARTIFACT_SOURCE=${SEQMASK_ARTIFACT_SOURCE:-}
if [[ -z "$ARTIFACT_SOURCE" && -n "$RELEASE_ASSETS_ROOT" ]]; then
    ARTIFACT_SOURCE=$RELEASE_ASSETS_ROOT/seqmask/models.zip
fi
FORCE_REDOWNLOAD=false

usage() {
    cat <<'EOF'
Usage: setup_seqmask.sh [options]

Options:
  --engine docker|podman   Container engine (default: podman)
  --tag IMAGE             Image tag (default: ttp-workbench:seqmask)
  --artifact-url URL      Override the model/FastText archive URL
  --artifact-archive PATH Use a local archive instead of downloading it
  --force-redownload      Replace the cached artifact archive
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --engine) ENGINE=$2; shift 2;;
        --tag) IMAGE_TAG=$2; shift 2;;
        --artifact-url) ARTIFACT_URL=$2; shift 2;;
        --artifact-archive) ARTIFACT_SOURCE=$2; shift 2;;
        --force-redownload) FORCE_REDOWNLOAD=true; shift;;
        -h|--help) usage; exit 0;;
        *) echo "Unknown option: $1" >&2; usage; exit 2;;
    esac
done

[[ "$ENGINE" == docker || "$ENGINE" == podman ]] || { echo "--engine must be docker or podman" >&2; exit 2; }
[[ -n "$ARTIFACT_SHA256" && ( -n "$ARTIFACT_SOURCE" || -n "$ARTIFACT_URL" ) ]] || {
    echo "Provide a local SeqMask archive and checksum or configure its download source" >&2
    exit 1
}

destination=$SCRIPT_DIR/seqmask_artifacts.zip
if [[ -n "$ARTIFACT_SOURCE" ]]; then
    ARTIFACT_SOURCE=$(realpath "$ARTIFACT_SOURCE")
    [[ -f "$ARTIFACT_SOURCE" ]] || { echo "SeqMask artifact archive not found: $ARTIFACT_SOURCE" >&2; exit 1; }
    if [[ "$ARTIFACT_SOURCE" != "$destination" ]]; then
        cp -f "$ARTIFACT_SOURCE" "$destination"
    fi
elif [[ "$FORCE_REDOWNLOAD" == true ]] || ! echo "$ARTIFACT_SHA256  $destination" | sha256sum -c - >/dev/null 2>&1; then
    partial=$destination.part
    # Zenodo supports byte ranges.  Preserve the partial archive and resume it
    # across transient gateway failures instead of restarting a multi-GB file.
    curl_args=(--fail --location --show-error --progress-bar --retry 5 --retry-delay 5 --continue-at - --output "$partial")
    if [[ -n "${ZENODO_CURL_CONFIG:-}" ]]; then
        [[ -r "$ZENODO_CURL_CONFIG" ]] || { echo "ZENODO_CURL_CONFIG is not readable" >&2; exit 1; }
        curl_args=(--config "$ZENODO_CURL_CONFIG" "${curl_args[@]}")
    fi
    echo "Downloading SeqMask model/FastText archive..."
    curl "${curl_args[@]}" "$ARTIFACT_URL"
    mv -f "$partial" "$destination"
else
    echo "Using cached SeqMask model/FastText archive"
fi

echo "$ARTIFACT_SHA256  $destination" | sha256sum -c -
"$ENGINE" build \
    --build-arg "SEQMASK_ARTIFACT_SHA256=$ARTIFACT_SHA256" \
    -t "$IMAGE_TAG" "$SCRIPT_DIR"

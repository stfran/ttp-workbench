#!/usr/bin/env bash
set -euo pipefail
SETUP=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
SUITE=$(cd "$SETUP/.." && pwd)
TOOLS=${REPRO_EXTERNAL_ROOT:-$SUITE/.runtime/external_tools}
ENGINE=${CONTAINER_ENGINE:-podman}
SCOPE=all
if [[ ${1:-} == --scope ]]; then
    [[ $# -ge 2 ]] || { echo "--scope requires core, optional, or all" >&2; exit 2; }
    SCOPE=$2
    shift 2
fi
[[ $# == 0 ]] || { echo "Unknown argument: $1" >&2; exit 2; }
[[ "$SCOPE" == core || "$SCOPE" == optional || "$SCOPE" == all ]] || {
    echo "Invalid runtime-asset scope: $SCOPE" >&2
    exit 2
}
mkdir -p "$TOOLS/cache/nltk" "$TOOLS/cache/huggingface" "$TOOLS/downloads"
export PIP_CACHE_DIR="$TOOLS/cache/pip"
export TFHUB_CACHE_DIR="$TOOLS/RAF-AG/data/tf_hub"

copy_from_image() {
    local image=$1 source=$2 destination=$3 marker=$4
    [[ -e "$destination/$marker" ]] && return
    mkdir -p "$destination"
    local container
    container=$($ENGINE create "$image" /bin/true)
    trap '$ENGINE rm -f "$container" >/dev/null 2>&1 || true' RETURN
    $ENGINE cp "$container:$source/." "$destination/"
    $ENGINE rm "$container" >/dev/null
    trap - RETURN
    [[ -e "$destination/$marker" ]] || { echo "Image asset missing after copy: $destination/$marker" >&2; exit 1; }
}

copy_nonempty_from_image() {
    local image=$1 source=$2 destination=$3 marker=$4
    if [[ -d "$destination" ]] && find "$destination" -type f -name "$marker" -print -quit | grep -q .; then
        return
    fi
    mkdir -p "$destination"
    local container
    container=$($ENGINE create "$image" /bin/true)
    trap '$ENGINE rm -f "$container" >/dev/null 2>&1 || true' RETURN
    $ENGINE cp "$container:$source/." "$destination/"
    $ENGINE rm "$container" >/dev/null
    trap - RETURN
    find "$destination" -type f -name "$marker" -print -quit | grep -q . || {
        echo "Image asset marker is missing after copy: $destination/$marker" >&2
        exit 1
    }
}

copy_from_image ttp-workbench:attackg /opt/AttacKG/templates "$TOOLS/AttacKG/templates" T1001.json
copy_from_image ttp-workbench:attackg /opt/AttacKG/new_cti.model "$TOOLS/AttacKG/new_cti.model" config.cfg

if [[ "$SCOPE" == core || "$SCOPE" == all ]]; then
    copy_from_image ttp-workbench:ladder /opt/LADDER/attack_pattern/models "$TOOLS/LADDER/attack_pattern/models" entity_ext.pt

    BUCHEL_TOOLS="$TOOLS/BuchelTools/ext_tools/tools"
    mkdir -p "$BUCHEL_TOOLS/LADDER/attack_pattern/models"
    rsync -a "$TOOLS/LADDER/attack_pattern/models/" "$BUCHEL_TOOLS/LADDER/attack_pattern/models/"
    bash "$SETUP/stage_buchel_attackg_model.sh"

    if [[ ! -d "$TOOLS/TTPDrill/stanford-corenlp-full-2018-10-05" ]]; then
        archive="$TOOLS/downloads/stanford-corenlp-full-2018-10-05.zip"
        if [[ ! -f "$archive" ]]; then
            curl --fail --location --retry 5 --continue-at - \
                https://downloads.cs.stanford.edu/nlp/software/stanford-corenlp-full-2018-10-05.zip -o "$archive.part"
            mv "$archive.part" "$archive"
        fi
        echo "833f0f5413a33e7fbc98aeddcb80eb0a55b672f67417b8d956ed9c39abe8d26c  $archive" | sha256sum -c -
        unzip -q "$archive" -d "$TOOLS/TTPDrill"
    fi

    for tool in TTPDrill LADDER; do
        "$TOOLS/$tool/.venv/bin/python" -m nltk.downloader -d "$TOOLS/cache/nltk" \
            punkt punkt_tab averaged_perceptron_tagger averaged_perceptron_tagger_eng stopwords
    done
    "$TOOLS/TTP-LLM/.venv/bin/python" -m pip install torch torchvision torchaudio scikit-learn==1.6.1
    "$TOOLS/LADDER/.venv/bin/python" -m pip install torch==2.12.0
    "$BUCHEL_TOOLS/AttacKG/.venv/bin/python" -m pip install pandas==2.2.3
fi

if [[ "$SCOPE" == optional || "$SCOPE" == all ]]; then
    copy_nonempty_from_image ttp-workbench:raf-ag /opt/RAF-AG/data/tf_hub "$TFHUB_CACHE_DIR" saved_model.pb
    python3 "$SETUP/download_zenodo_tram.py"
fi

if [[ "$SCOPE" == all ]]; then
    [[ -f "$TOOLS/TRAM/predict_multi_label.py" ]] || {
        echo "TRAM native source is missing; run setup_sources.sh --scope all first" >&2
        exit 1
    }
    copy_from_image ttp-workbench:tram /opt/TRAM/scibert_multi_label_model \
        "$TOOLS/TRAM/scibert_multi_label_model" pytorch_model.bin
    (
        cd "$TOOLS/TRAM"
        sha256sum predict_multi_label.py scibert_multi_label_model/config.json \
            scibert_multi_label_model/pytorch_model.bin > AE_RUNTIME_SHA256
    )
    "$TOOLS/TRAM/.venv/bin/python" - "$TOOLS/TRAM" <<'PY'
from pathlib import Path
import json
import sys

root = Path(sys.argv[1])
payload = {
    "python": sys.version,
    "model_dir": str((root / "scibert_multi_label_model").resolve()),
}
(root / "AE_ENVIRONMENT.json").write_text(json.dumps(payload, indent=2) + "\n")
PY
fi

"$TOOLS/AttacKG/.venv/bin/python" -m pip install pandas==1.5.3
echo "Public runtime assets staged under $TOOLS"

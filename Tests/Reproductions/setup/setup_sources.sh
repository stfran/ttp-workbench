#!/usr/bin/env bash
set -euo pipefail
SETUP=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
SUITE=$(cd "$SETUP/.." && pwd)
PROJECT=$(cd "$SUITE/../.." && pwd)
TOOLS=${REPRO_EXTERNAL_ROOT:-$SUITE/.runtime/external_tools}
PYTHON=${PYTHON:-python3}
BUCHEL_SOURCE_ROOT=${BUCHEL_WORKDIR:-$PROJECT/Docker_Setup/Buchel/buchel_generation}
SCOPE=all
if [[ ${1:-} == --scope ]]; then
    [[ $# -ge 2 ]] || { echo "--scope requires core, optional, or all" >&2; exit 2; }
    SCOPE=$2
    shift 2
fi
[[ $# == 0 ]] || { echo "Unknown argument: $1" >&2; exit 2; }
[[ "$SCOPE" == core || "$SCOPE" == optional || "$SCOPE" == all ]] || {
    echo "Invalid source scope: $SCOPE" >&2
    exit 2
}
mkdir -p "$TOOLS/downloads"

pull() {
    local name=$1 url=$2 revision=$3
    if [[ ! -d "$TOOLS/$name/.git" ]]; then
        git init -q "$TOOLS/$name"
        git -C "$TOOLS/$name" remote add origin "$url"
        git -C "$TOOLS/$name" fetch --depth 1 origin "$revision"
        git -C "$TOOLS/$name" checkout -q --detach FETCH_HEAD
    fi
    local actual
    actual=$(git -C "$TOOLS/$name" rev-parse HEAD)
    [[ "$actual" == "$revision" ]] || {
        echo "Revision mismatch for $name: expected $revision, found $actual" >&2
        exit 1
    }
}

pull AttacKG https://github.com/li-zhenyuan/Knowledge-enhanced-Attack-Graph.git 9120ebea25383bfca1254d2b3088266b3680e47b
pull LADDER https://github.com/aiforsec/LADDER.git 863ec65859700ac13b1308fbe48ad34f4078eb39
pull TTPDrill-0.3 https://github.com/SkyBulk/TTPDrill-0.3.git 78435268f26e71c966af53321a4a6037a6bb7853
pull TTPDrill-1.0 https://github.com/ccsnow127/TTPDrill-1.0.git 48c99ae855e625ad9b9cdc71e7f6a597db898c99
pull TTP-LLM https://github.com/RezzFayyazi/TTP-LLM.git 7b8ce19aa608769e86ecf7bddb6dc089e023ffa9
if [[ "$SCOPE" == all ]]; then
    pull TRAM https://github.com/center-for-threat-informed-defense/tram.git f29793d8d665f7f552898696e00065ef24a29a20
    install -m 0644 "$PROJECT/Docker_Setup/TRAM/predict_multi_label.py" "$TOOLS/TRAM/predict_multi_label.py"
fi

if [[ ! -f "$TOOLS/TTPDrill/.ae-merged-source" ]]; then
    mkdir -p "$TOOLS/TTPDrill"
    rsync -a --exclude=.git "$TOOLS/TTPDrill-0.3/" "$TOOLS/TTPDrill/"
    rsync -a --exclude=.git "$TOOLS/TTPDrill-1.0/" "$TOOLS/TTPDrill/"
    "$PYTHON" -c 'from pathlib import Path; import sys; Path(sys.argv[1]).write_text("TTPDrill 0.3 overlaid with 1.0\n")' "$TOOLS/TTPDrill/.ae-merged-source"
fi

declare -A BUCHEL_SHA256=(
    [generation]=c5b1deeaaf345a6343df88d56bfac58c569d847ff43a5d78895b6a523ece766a
    [ext_tools]=51030bc22ebb09e8f24d32c69e71b34af402245caba16d1d75e6a0c1b0d61f0f
)
for bundle in generation ext_tools; do
    archive="$TOOLS/downloads/$bundle.zip"
    if [[ ! -f "$archive" ]]; then
        release_archive=${TTPWB_RELEASE_ASSETS_ROOT:-}/buchel/$bundle.zip
        built_archive="$BUCHEL_SOURCE_ROOT/$bundle.zip"
        if [[ -n "${TTPWB_RELEASE_ASSETS_ROOT:-}" && -f "$release_archive" ]]; then
            echo "Reusing models.zip asset: $release_archive"
            cp -f "$release_archive" "$archive"
        elif [[ -f "$built_archive" ]]; then
            echo "Reusing Büchel container-setup archive: $built_archive"
            cp -f "$built_archive" "$archive"
        else
            curl --fail --location --retry 5 --continue-at - \
                "https://zenodo.org/records/16753555/files/$bundle.zip?download=1" -o "$archive.part"
            mv "$archive.part" "$archive"
        fi
    fi
    echo "${BUCHEL_SHA256[$bundle]}  $archive" | sha256sum -c -
done
if [[ ! -f "$TOOLS/Buchel/generation/supervised_finetuning.py" ]]; then
    mkdir -p "$TOOLS/Buchel"
    # The published archive contains one zero-byte stray entry named
    # generation/finetuning/experiments/;. Info-ZIP 6.00 rejects that name on
    # some Linux hosts even though the substantive archive contents are valid.
    unzip -oq "$TOOLS/downloads/generation.zip" -d "$TOOLS/Buchel" \
        -x 'generation/finetuning/experiments/;'
fi
if [[ ! -d "$TOOLS/BuchelTools/ext_tools/tools" ]]; then
    mkdir -p "$TOOLS/BuchelTools"
    unzip -oq "$TOOLS/downloads/ext_tools.zip" -d "$TOOLS/BuchelTools"
fi

# Apply only the maintained compatibility patches. Model assets are extracted
# from pinned images later; no private study tree is consulted.
install -m 0644 "$PROJECT/Docker_Setup/AttacKG/attackg_bulk.py" "$TOOLS/AttacKG/attackg_bulk.py"
"$PYTHON" "$PROJECT/Docker_Setup/AttacKG/patch_bulk.py" "$TOOLS/AttacKG"
"$PYTHON" "$PROJECT/Docker_Setup/AttacKG/patch_shortest_path_cache.py" "$TOOLS/AttacKG"
"$PYTHON" "$SETUP/apply_documented_patches.py"
echo "Pinned public sources staged under $TOOLS"

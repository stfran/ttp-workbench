#!/usr/bin/env bash
set -euo pipefail
SETUP=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
SUITE=$(cd "$SETUP/.." && pwd)
TOOLS=${REPRO_EXTERNAL_ROOT:-$SUITE/.runtime/external_tools}
SOURCE=${ATTACKG_MODEL_SOURCE:-$TOOLS/AttacKG/new_cti.model}
DEST="$TOOLS/BuchelTools/ext_tools/tools/AttacKG/new_cti.model"
[[ -f "$SOURCE/config.cfg" ]] || { echo "Missing standard AttacKG model: $SOURCE/config.cfg" >&2; exit 1; }
mkdir -p "$DEST"
rsync -a "$SOURCE/" "$DEST/"
echo "Copied AttacKG model: $SOURCE -> $DEST"

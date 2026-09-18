#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
ENGINE="docker"

# Map args to directory names 
declare -A TOOL_DIRS=(
  [attackg]="AttacKG"
  [buchel]="Buchel"
  [orbinato]="Orbinato"
  [raf-ag]="RAF-AG"
  [rcatt]="rcATT"
  [tram]="TRAM"
  [ttp-llm]="TTP-LLM"
  [ttpdrill]="TTPDrill"
  [ladder]="LADDER"
  [seqmask]="SeqMask"
)

print_usage() {
  cat <<EOF
Usage: $0 [--engine {docker|podman}] [--all] [--{tool_name} ...]

Valid tool names (case-insensitive, normalized to lowercase internally):
  AttacKG
  Buchel
  LADDER
  Orbinato
  RAF-AG
  rcATT
  SeqMask
  TRAM
  TTP-LLM
  TTPDrill
  

Examples:
  $0 --all
  $0 --AttacKG --TRAM
  $0 --raf-ag --rcATT
EOF
}

if [[ $# -eq 0 ]]; then
  echo "Error: no arguments provided."
  print_usage
  exit 1
fi

# get tool names
declare -a TOOLS_TO_BUILD=()

# parse the arguments
while [[ $# -gt 0 ]]; do
  arg="$1"
  shift

  if [[ "$arg" != --* ]]; then
    echo "Error: unrecognized argument '$arg'"
    print_usage
    exit 1
  fi

  if [[ "$arg" == --engine ]]; then
    if [[ $# -eq 0 ]]; then
      echo "Error: --engine requires an argument (docker or podman)."
      exit 1
    fi
    ENGINE="$1"
    shift
    continue
  elif [[ "$arg" == --engine=* ]]; then
    ENGINE="${arg#--engine=}"
    continue
  fi

  name="${arg#--}"

  # Handle --all
  if [[ "${name,,}" == "all" ]]; then
    TOOLS_TO_BUILD=("${!TOOL_DIRS[@]}")
    break
  fi

  # Normalize to lowercase for lookup
  norm_name="$(echo "$name" | tr '[:upper:]' '[:lower:]')"

  if [[ -z "${TOOL_DIRS[$norm_name]+_}" ]]; then
    echo "Error: unknown tool name '$name'"
    echo
    print_usage
    exit 1
  fi

  TOOLS_TO_BUILD+=("$norm_name")
done

case "$ENGINE" in
  docker|podman)
    ;;
  *)
    echo "Error: invalid engine '$ENGINE'. Must be 'docker' or 'podman'."
    exit 1
    ;;
esac

# We have a separate script for Buchel, so just pass the engine to it
BUCHEL_ENGINE_FLAG=""
case "$ENGINE" in
  docker) BUCHEL_ENGINE_FLAG="--docker" ;;
  podman)
    BUCHEL_ENGINE_FLAG="--podman"
    export PODMAN_SHORT_NAME_MODE=disabled # auto selects the first registry for pulling base images
    ;;
esac

echo "Container engine: $ENGINE"
echo "Tools to build: ${TOOLS_TO_BUILD[*]}"
echo

# Build loop
for tool in "${TOOLS_TO_BUILD[@]}"; do
  dir="$SCRIPT_DIR/${TOOL_DIRS[$tool]}"

  if [[ ! -d "$dir" ]]; then
    echo "Error: directory '$dir' does not exist for tool '$tool'." >&2
    exit 1
  fi

  echo "============================================================"
  echo "Building for tool '$tool'"
  echo "  Directory : $dir"

  # These tools fetch large artifacts before build so draft credentials never
  # enter the container build context or image layers.
  if [[ "$tool" == "buchel" ]]; then
    echo "  Method    : Running setup_buchel.sh $BUCHEL_ENGINE_FLAG"
    echo "------------------------------------------------------------"

    [[ -f "$dir/setup_buchel.sh" ]] || {
      echo "Error: '$dir/setup_buchel.sh' not found." >&2
      exit 1
    }
    chmod +x "$dir/setup_buchel.sh"

    (
      cd "$dir"
      ./setup_buchel.sh "$BUCHEL_ENGINE_FLAG"
    )

    echo "Done running setup_buchel.sh for Buchel"
    echo
    continue
  fi

  if [[ "$tool" == "orbinato" ]]; then
    image_tag="ttp-workbench:${tool}"
    echo "  Image tag : $image_tag"
    echo "  Engine    : $ENGINE"
    echo "  Method    : Running setup_orbinato.sh"
    echo "------------------------------------------------------------"
    chmod +x "$dir/setup_orbinato.sh"
    (
      cd "$dir"
      ./setup_orbinato.sh --engine "$ENGINE" --tag "$image_tag"
    )
    echo "Done running setup_orbinato.sh for Orbinato"
    echo
    continue
  fi

  if [[ "$tool" == "seqmask" ]]; then
    image_tag="ttp-workbench:${tool}"
    echo "  Image tag : $image_tag"
    echo "  Engine    : $ENGINE"
    echo "  Method    : Running setup_seqmask.sh"
    echo "------------------------------------------------------------"
    chmod +x "$dir/setup_seqmask.sh"
    (
      cd "$dir"
      ./setup_seqmask.sh --engine "$ENGINE" --tag "$image_tag"
    )
    echo "Done running setup_seqmask.sh for SeqMask"
    echo
    continue
  fi

  
  image_tag="ttp-workbench:${tool}"
  echo "  Image tag : $image_tag"
  echo "  Engine    : $ENGINE"
  echo "------------------------------------------------------------"

  "$ENGINE" build -t "$image_tag" "$dir"

  echo "Done building $image_tag"
  echo
  
  
done

echo "All requested builds completed."

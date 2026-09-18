#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
RELEASE_ASSETS_ROOT="${TTPWB_RELEASE_ASSETS_ROOT:-}"

# Buchel setup: download + unzip; optionally patch compose for Podman GPU & SELinux, then bring up.
DEFAULT_ZIP_URL="https://zenodo.org/records/16753555/files/generation.zip?download=1"
DEFAULT_EXT_TOOLS_URL="https://zenodo.org/records/16753555/files/ext_tools.zip?download=1"
if [[ -n "$RELEASE_ASSETS_ROOT" ]]; then
  DEFAULT_ZIP_URL="file://$RELEASE_ASSETS_ROOT/buchel/generation.zip"
  DEFAULT_EXT_TOOLS_URL="file://$RELEASE_ASSETS_ROOT/buchel/ext_tools.zip"
fi
ZIP_URL="${ZIP_URL:-$DEFAULT_ZIP_URL}"
GENERATION_SHA256="${BUCHEL_GENERATION_SHA256:-c5b1deeaaf345a6343df88d56bfac58c569d847ff43a5d78895b6a523ece766a}"
EXT_TOOLS_URL="${BUCHEL_EXT_TOOLS_URL:-$DEFAULT_EXT_TOOLS_URL}"
EXT_TOOLS_SHA256="${BUCHEL_EXT_TOOLS_SHA256:-51030bc22ebb09e8f24d32c69e71b34af402245caba16d1d75e6a0c1b0d61f0f}"
MODELS_URL="${BUCHEL_MODELS_URL:-}"
MODELS_SHA256="${BUCHEL_MODELS_SHA256:-}"
MODELS_SOURCE="${BUCHEL_MODELS_SOURCE:-}"
if [[ -z "$MODELS_SOURCE" && -n "$RELEASE_ASSETS_ROOT" ]]; then
  MODELS_SOURCE="$RELEASE_ASSETS_ROOT/buchel/sft_bosch.zip"
fi
WORKDIR="${BUCHEL_WORKDIR:-${WORKDIR:-buchel_generation}}"
CLEANUP="false"   # delete ZIP + unzipped files after setup
RUN_UP="false" # bring up compose after setup, this + RUN_EXPERIMENT executes the original flow
ENGINE="auto"     # auto | docker | podman (via --docker/--podman or auto-detect)
FORCE_REDOWNLOAD="false" # download the ZIP even if cached
PATCH_COMPOSE_INPLACE="false" # rewrite compose.yml in-place for Podman GPU
PATCH_DOCKERFILE_CMD="true" # prepend Dockerfile FROMs with docker.io/
PATCH_TRAINER_SAVE="false" # inject model-save block into supervised_finetuning.py
PATCH_REQUIREMENTS="true" # update requirements.txt
RUN_EXPERIMENT="false" # after bring-up, run the original experiment script (main in supervised_finetuning.py)
RUN_SCRIPT="supervised_finetuning.py" # script to run inside the app service
APP_SERVICE="app" # compose service name for the the generation app
OLLAMA_SERVICE="ollama" # compose service name for ollama
COMPOSE_FILE_NAME="docker-compose.yml"  # compose file name
ENGINE_FORCED="false" # whether engine was forced by user
CLI_FILE="buchel_cli.py" # helper CLI to copy into workdir

usage() {
  cat <<'USAGE'
Usage: setup_buchel.sh [options]

Download & unzip Buchel's generation bundle and (optionally) patch files for Podman GPU,
comment out Dockerfile CMD, inject model-save block into supervised_finetuning.py, and build the images.
Command line arguments also allow bringing up the compose stack and running the experiment script as originally designed.

Options:
  --workdir DIR              Where to place/unzip the bundle (default: buchel_generation)
  --zip URL                  Zenodo ZIP URL (default: official generation.zip)
  --ext-tools-url URL        Zenodo ZIP URL (default: official ext_tools.zip)
  --models-url URL           Override the supplied-model archive URL
  --models-archive PATH      Use a local supplied-model archive
  --up                       After unzip/patch, bring up the compose stack
  --podman | --docker        Select container engine (default: auto-detect)
  --force-redownload         Redownload ZIP even if cached locally

  --patch-compose-inplace    Rewrite docker-compose.yml for the selected engine
                             (GPU support, persistent paths and embedding context)
  --patch-dockerfile-cmd     Comment out the final CMD in Dockerfile
  --patch-trainer-save       Inject model/tokenizer save block at line 137 of supervised_finetuning.py

  --run-experiment           After bringing up, run the experiment script inside the app service
  --run-script PATH          Script to run (default: supervised_finetuning.py)
  --app-service NAME         Compose service name for the app (default: app)
  --ollama-service NAME      Compose service name for ollama (default: ollama)
  --compose-file NAME        Compose file name (default: docker-compose.yml)
  --cleanup                 Delete the downloaded ZIP file and unzipped directory after setup

Notes:
  • If ENGINE is auto-detected as Podman, we default to --patch-compose-inplace unless you provide your own compose file.
  • --patch-compose-inplace writes the target YAML directly into docker-compose.yml (backed up to .bak).
  • Original files are backed up as *.bak when patched.
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --workdir) WORKDIR="$2"; shift 2;;
    --zip) ZIP_URL="$2"; shift 2;;
    --ext-tools-url) EXT_TOOLS_URL="$2"; shift 2;;
    --models-url) MODELS_URL="$2"; shift 2;;
    --models-archive) MODELS_SOURCE="$2"; shift 2;;
    --up) RUN_UP="true"; shift 1;;
    --podman) ENGINE="podman"; ENGINE_FORCED="true"; shift 1;;
    --docker) ENGINE="docker"; ENGINE_FORCED="true"; shift 1;;
    --force-redownload) FORCE_REDOWNLOAD="true"; shift 1;;

    --patch-compose-inplace) PATCH_COMPOSE_INPLACE="true"; shift 1;;
    --patch-dockerfile-cmd) PATCH_DOCKERFILE_CMD="true"; shift 1;;
    --patch-trainer-save) PATCH_TRAINER_SAVE="true"; shift 1;;

    --run-experiment) RUN_EXPERIMENT="true"; shift 1;;
    --run-script) RUN_SCRIPT="$2"; shift 2;;
    --app-service) APP_SERVICE="$2"; shift 2;;
    --ollama-service) OLLAMA_SERVICE="$2"; shift 2;;
    --compose-file) COMPOSE_FILE_NAME="$2"; shift 2;;
    --cleanup) CLEANUP="true"; shift 1;;

    -h|--help) usage; exit 0;;
    *) echo "Unknown arg: $1" >&2; usage; exit 1;;
  esac
done

if [[ -n "$MODELS_SOURCE" ]]; then
  MODELS_SOURCE="$(realpath "$MODELS_SOURCE")"
fi

say() { printf "\033[1;36m[buchel]\033[0m %s
" "$*"; }
die() { printf "\033[1;31m[error]\033[0m %s
" "$*" >&2; exit 1; }

[[ -n "$MODELS_SHA256" ]] || die "Büchel model archive SHA-256 is not configured"
[[ -n "$MODELS_SOURCE" || -n "$MODELS_URL" ]] || die "Provide a local Büchel model archive or configure its download URL"

# Prefer curl, fall back to wget
fetch() {
  local url="$1" out="$2"
  if command -v curl >/dev/null 2>&1; then
    curl -L --fail -o "$out" "$url"
  elif command -v wget >/dev/null 2>&1; then
    wget -O "$out" "$url"
  else
    die "Neither curl nor wget is available"
  fi
}

fetch_models() {
  local url="$1" out="$2"
  if command -v curl >/dev/null 2>&1; then
    local args=(--fail --location --show-error --progress-bar --retry 5 --retry-delay 5 --continue-at - --output "$out")
    if [[ -n "${ZENODO_CURL_CONFIG:-}" ]]; then
      [[ -r "$ZENODO_CURL_CONFIG" ]] || die "ZENODO_CURL_CONFIG is not readable"
      args=(--config "$ZENODO_CURL_CONFIG" "${args[@]}")
    fi
    curl "${args[@]}" "$url"
  else
    die "curl is required to download the restricted model archive"
  fi
}

# Auto-detect engine if not forced
autodetect_engine() {
  if [[ "$ENGINE_FORCED" == "true" ]]; then
    echo "$ENGINE"; return 0
  fi
  if docker compose version >/dev/null 2>&1 || command -v docker-compose >/dev/null 2>&1; then
    echo docker; return 0
  fi
  if podman compose version >/dev/null 2>&1 || command -v podman-compose >/dev/null 2>&1; then
    echo podman; return 0
  fi
  die "Neither Docker nor Podman compose is available on PATH."
}

compose_cmd() {
  local eng="$1"
  case "$eng" in
    docker)
      if docker compose version >/dev/null 2>&1; then
        echo "docker compose"
      elif command -v docker-compose >/dev/null 2>&1; then
        echo "docker-compose"
      else
        die "Docker Compose not found. Install 'docker compose' plugin or 'docker-compose'."
      fi
      ;;
    podman)
      if podman compose version >/dev/null 2>&1; then
        echo "podman compose"
      elif command -v podman-compose >/dev/null 2>&1; then
        echo "podman-compose"
      else
        die "Podman Compose not found. Install 'podman compose' (Podman >=4) or 'podman-compose'."
      fi
      ;;
    *) die "Unsupported engine: $eng";;
  esac
}


safe_unzip() {
  local zip="$1"
  local log
  log="$(mktemp)"
  set +e
  unzip -o -q -UU "$zip" 2> "$log"
  local rc=$?
  set -e
  if grep -qi 'mapname:.*conversion of' "$log"; then
    say "Encountered a known filename-encoding quirk in the ZIP (unzip 'mapname' warning)."
  fi
  if [[ $rc -le 1 ]]; then rm -f "$log"; return 0; fi
  if find . -maxdepth 3 -type f -name 'docker-compose.yml' | grep -q .; then
    say "Unzip rc=$rc but compose file present; proceeding."
    rm -f "$log"; return 0
  fi
  say "Unzip rc=$rc; trying CP437 fallback..."
  set +e; unzip -o -q -O CP437 "$zip" 2>> "$log"; rc=$?; set -e
  if [[ $rc -le 1 ]] || find . -maxdepth 3 -type f -name 'docker-compose.yml' | grep -q .; then
    rm -f "$log"; return 0
  fi
  if command -v bsdtar >/dev/null 2>&1; then
    say "Trying bsdtar..."; bsdtar -xf "$zip"; return 0
  fi
  if command -v 7z >/dev/null 2>&1; then
    say "Trying 7z..."; 7z x -y "$zip" >/dev/null; return 0
  fi
  die "Extraction failed; install 'bsdtar' or 'p7zip' and retry."
}

patch_compose_inplace_for_podman(){
  local dir="$1"
  local file="$dir/$COMPOSE_FILE_NAME"
  [[ -f "$file" ]] || die "Expected compose file not found: $file"
  cp -f "$file" "$file.bak"
  say "Rewriting compose for Podman (GPU + SELinux): $file"
  cat >"$file" <<'YAML'
services:
  ollama:
    image: localhost/generation_ollama:latest
    build:
      context: .
      dockerfile: Dockerfile.ollama
    container_name: ollama_local
    environment:
      - OLLAMA_CONTEXT_LENGTH=${OLLAMA_CONTEXT_LENGTH:-32768}
    devices:
      - "nvidia.com/gpu=all"
    volumes:
      - ../.ollama:/root/.ollama:Z
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:11434/api/tags"]
      interval: 10s
      timeout: 5s
      retries: 5000
    expose: ["11434"]
    networks: [ollama_net]

  app:
    image: localhost/generation_app:latest
    build:
      context: .
      dockerfile: Dockerfile
    container_name: ttp_generative_experiments
    devices:
      - "nvidia.com/gpu=all"
    command: ["bash","-lc","sleep infinity"]
    environment:
      - OLLAMA_API_URL=http://ollama:11434
      - SFT_ROOT=/workspace/finetuning/output
      - HF_HOME=/tmp/huggingface
    volumes:
      - /tmp/huggingface:/tmp/huggingface:Z
      - ./experiments:/workspace/finetuning/experiments:Z
    depends_on:
      ollama:
        condition: service_healthy
    networks: [ollama_net]

networks:
  ollama_net:
    driver: bridge
YAML
}

patch_compose_inplace_for_docker(){
  local dir="$1"
  local file="$dir/$COMPOSE_FILE_NAME"
  [[ -f "$file" ]] || die "Expected compose file not found: $file"
  cp -f "$file" "$file.bak"
  say "Rewriting compose for Docker (GPU reservation): $file"
  cat >"$file" <<'YAML'
services:
  ollama:
    image: localhost/generation_ollama:latest
    build:
      context: .
      dockerfile: Dockerfile.ollama
    container_name: ollama_local
    environment:
      - OLLAMA_CONTEXT_LENGTH=${OLLAMA_CONTEXT_LENGTH:-32768}
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: all
              capabilities: [gpu]
    volumes:
      - ../.ollama:/root/.ollama
    healthcheck:
      test: ["CMD", "curl", "-f", "http://ollama:11434/api/tags"]
      interval: 10s
      timeout: 5s
      retries: 5000
    expose: ["11434"]
    networks: [ollama_net]

  app:
    image: localhost/generation_app:latest
    build:
      context: .
      dockerfile: Dockerfile
    container_name: ttp_generative_experiments
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: all
              capabilities: [gpu]
    command: ["bash","-lc","sleep infinity"]
    environment:
      - OLLAMA_API_URL=http://ollama:11434
      - SFT_ROOT=/workspace/finetuning/output
      - HF_HOME=/tmp/huggingface
    volumes:
      - /tmp/huggingface:/tmp/huggingface
      - ./experiments:/workspace/finetuning/experiments
    depends_on:
      ollama:
        condition: service_healthy
    networks: [ollama_net]

networks:
  ollama_net:
    driver: bridge
YAML
}

patch_ollama_context(){
  # This image default also covers adapters starting Ollama without Compose.
  python3 - "$1/Dockerfile.ollama" <<'PY'
from pathlib import Path
import re
import sys
path = Path(sys.argv[1])
source = path.read_text()
line = "ENV OLLAMA_CONTEXT_LENGTH=32768"
if re.search(r"^ENV OLLAMA_CONTEXT_LENGTH=.*$", source, flags=re.M):
    updated = re.sub(r"^ENV OLLAMA_CONTEXT_LENGTH=.*$", line, source, flags=re.M)
else:
    updated = source.rstrip() + "\n\n# GTE-Qwen2 model-card input limit; do not truncate CTI segments.\n" + line + "\n"
if updated != source:
    backup = path.with_name(path.name + ".before-context")
    if not backup.exists():
        backup.write_text(source)
    path.write_text(updated)
PY
}

patch_dockerfile_inplace(){
  local dir="$1"
  [[ -d "$dir" ]] || die "Directory not found: $dir"
  local f found=0

  while IFS= read -r -d '' f; do
    found=1
    [[ -f "$f" ]] || continue

    [[ -f "$f.bak" ]] || cp -f "$f" "$f.bak"

    say "Patching FROM lines in: $f"
    awk '
    function firstseg(s){ split(s,a,"/"); return a[1] }
    {
      if (match($0, /^[[:space:]]*FROM[[:space:]]+/)) {
        prefix = substr($0, 1, RLENGTH)
        rest = substr($0, RLENGTH+1)
        n = split(rest, t, /[[:space:]]+/)
        imgidx = 1
        for(i=1;i<=n;i++) if(t[i] !~ /^--/) { imgidx = i; break }
        img = t[imgidx]
        if (img !~ /^docker\.io\// && img !~ /^ttp_workbench_/) {
          first = firstseg(img)
          if (index(first, ".")==0 && index(first, ":")==0 && first != "localhost") {
            t[imgidx] = "docker.io/" img
            rest2 = t[1]
            for(j=2;j<=n;j++) rest2 = rest2 " " t[j]
            print prefix rest2
            next
          }
        }
      }
      print $0
    }
    ' "$f" > "$f.tmp" && mv "$f.tmp" "$f"

  done < <(
    find "$dir" -maxdepth 2 -type f -name 'Dockerfile*' \
      ! -name '*.bak*' \
      ! -name '*.tmp' \
      -print0 2>/dev/null
  )

  if [[ $found -eq 0 ]]; then
    say "No Dockerfile* found in $dir; skipping docker.io prepending"
  fi
}

patch_app_dockerfile_for_models() {
  local dir="$1"
  local file="$dir/Dockerfile"
  [[ -f "$file" ]] || die "Expected app Dockerfile not found: $file"
  if ! grep -q '^FROM .* AS ttp_workbench_buchel_base$' "$file"; then
    sed -i -E '0,/^FROM /s|^(FROM .*)$|\1 AS ttp_workbench_buchel_base|' "$file"
  fi
  if grep -q 'TTP_WORKBENCH_BUCHEL_MODELS' "$file"; then
    sed -i -E "s|^ARG BUCHEL_MODELS_SHA256=.*$|ARG BUCHEL_MODELS_SHA256=\"$MODELS_SHA256\"|" "$file"
    sed -i -E 's|^COPY --from=ttp_workbench_buchel_models /tmp/buchel_models/buchel/models/local/ /workspace/finetuning/output/$|COPY --from=ttp_workbench_buchel_models /tmp/buchel_models/buchel/models/local/bosch_sentence/ /workspace/finetuning/output/bosch_sentence/|' "$file"
    sed -i -E '/^[[:space:]]*&& test -s \/workspace\/finetuning\/output\/mitre_sentence_tram\/merged\/config\.json$/d' "$file"
    sed -i -E 's|^(RUN test -s /workspace/finetuning/output/bosch_sentence/merged/config\.json) \\$|\1|' "$file"
    say "Supplied-model Dockerfile block already present; checksum refreshed"
    return
  fi
  say "Adding supplied-model archive to the generation app image"
  cat >> "$file" <<EOF

# TTP_WORKBENCH_BUCHEL_MODELS: installed by setup_buchel.sh.
FROM ttp_workbench_buchel_base AS ttp_workbench_buchel_models
ARG BUCHEL_MODELS_SHA256="$MODELS_SHA256"
COPY buchel_models.zip /tmp/buchel_models.zip
RUN echo "\${BUCHEL_MODELS_SHA256}  /tmp/buchel_models.zip" | sha256sum -c - \\
 && mkdir -p /tmp/buchel_models \\
 && python3 -m zipfile -e /tmp/buchel_models.zip /tmp/buchel_models \\
 && rm -f /tmp/buchel_models.zip

FROM ttp_workbench_buchel_base
COPY --from=ttp_workbench_buchel_models /tmp/buchel_models/buchel/models/local/bosch_sentence/ /workspace/finetuning/output/bosch_sentence/
RUN test -s /workspace/finetuning/output/bosch_sentence/merged/config.json
EOF
}

stage_model_archive() {
  local dir="$1"
  local destination="$dir/buchel_models.zip"
  if [[ -n "$MODELS_SOURCE" ]]; then
    [[ -f "$MODELS_SOURCE" ]] || die "Model archive not found: $MODELS_SOURCE"
    if [[ "$MODELS_SOURCE" != "$destination" ]]; then
      cp -f "$MODELS_SOURCE" "$destination"
    fi
  elif [[ "$FORCE_REDOWNLOAD" == "true" ]] || ! echo "$MODELS_SHA256  $destination" | sha256sum -c - >/dev/null 2>&1; then
    say "Downloading supplied Büchel models..."
    fetch_models "$MODELS_URL" "$destination.part"
    mv -f "$destination.part" "$destination"
  else
    say "Using cached supplied-model archive"
  fi
  echo "$MODELS_SHA256  $destination" | sha256sum -c - || die "Büchel model archive checksum failed"
}


# add rich>=13 to requirements.txt, which resolved setup issues
patch_requirements_inplace(){
  local dir="$1"
  local reqs="$dir/requirements.txt"
  cp -f "$reqs" "$reqs.bak"
  [[ -f "$reqs" ]] || { say "requirements.txt not found; skipping rich patch"; return 0; }
  if grep -q "^rich[[:space:]]*>=13" "$reqs"; then
    say "rich>=13 already present in requirements.txt; skipping"
    return 0
  fi
  say "Adding rich>=13 to requirements.txt"
  echo "rich>=13" >> "$reqs"
  echo "peft==0.17.1" >> "$reqs"
}

patch_trainer_save_block() {
  local dir="$1"
  local py="$dir/supervised_finetuning.py"
  [[ -f "$py" ]] || { say "supervised_finetuning.py not found; skipping save-block patch"; return 0; }
  if grep -q "merge_and_unload()" "$py"; then
    say "Save/merge block already present; skipping"
    return 0
  fi
  say "Inserting model/tokenizer save block at line 137 (tab-indented)"
  cp -f "$py" "$py.bak"
  local tmp; tmp="$(mktemp)"
  local block
  block="	from pathlib import Path
	save_dir = Path(output_path)
	save_dir.mkdir(parents=True, exist_ok=True)

	# Save LoRA adapter + tokenizer
	tokenizer.save_pretrained(save_dir.as_posix())
	model.save_pretrained(save_dir.as_posix())

	# (Optional) also save a merged FP16/BF16 model for direct inference
	merged_model = model.merge_and_unload()
	( save_dir / \"merged\" ).mkdir(parents=True, exist_ok=True)
	merged_model.save_pretrained((save_dir / \"merged\").as_posix())"
  awk -v insert="$block" 'NR==137{printf("%s
", insert)} {print} END{ if (NR<137) printf("%s
", insert) }' "$py" > "$tmp"
  mv "$tmp" "$py"
}

ensure_host_paths() {
  local dir="$1"
  # Ensure ../.ollama exists relative to compose dir
  mkdir -p "$dir/../.ollama" || true
  # Ensure experiments directory exists (bind-mounted)
  mkdir -p "$dir/experiments" || true
  # Ensure /tmp/huggingface exists (host absolute); may require permissions
  if [[ ! -d /tmp/huggingface ]]; then
    mkdir -p /tmp/huggingface 2>/dev/null || say "Could not create /tmp/huggingface; create it manually if needed."
  fi
}

bring_up() {
  local dir="$1"; local cmd="$2"; local file_name="$3"
  ( cd "$dir" && $cmd -f "$file_name" up --build -d )
}

build() {
  local dir="$1"; local cmd="$2"; local file_name="$3"
  ( cd "$dir" && $cmd -f "$file_name" build )
}

tag_buchel_images() {
  # Upstream Compose derives engine-specific names such as generation-app and
  # generation-ollama. Give both engines the same stable local names consumed by
  # BuchelAdapter and the reproduction embedding-service setup.
  local dir="$1" cmd="$2" file_name="$3"
  local app_id ollama_id
  if [[ "$ENGINE" == "podman" ]] &&
      "$ENGINE" image inspect localhost/generation_app:latest \
        localhost/generation_ollama:latest >/dev/null 2>&1; then
    say "Stable local Buchel images already exist"
    return 0
  fi
  app_id="$(cd "$dir" && $cmd -f "$file_name" images -q "$APP_SERVICE" | head -n 1)"
  ollama_id="$(cd "$dir" && $cmd -f "$file_name" images -q "$OLLAMA_SERVICE" | head -n 1)"
  [[ -n "$app_id" ]] || die "Could not resolve the built Buchel app image"
  [[ -n "$ollama_id" ]] || die "Could not resolve the built Buchel Ollama image"
  "$ENGINE" tag "$app_id" localhost/generation_app:latest
  "$ENGINE" tag "$ollama_id" localhost/generation_ollama:latest
  say "Tagged stable local images: localhost/generation_app:latest and localhost/generation_ollama:latest"
}

compose_run_script() {
  local dir="$1"; local cmd="$2"; local file_name="$3"; local service="$4"; local script="$5"
  set +e
  ( cd "$dir" && $cmd -f "$file_name" run --rm "$service" bash -lc "python3 '$script'" ) && return 0
  set -e
  say "Falling back to '$cmd exec' into running container: $service"
  case "$cmd" in
    docker* ) docker exec -it "$service" bash -lc "python3 '$script'";;
    podman* ) podman exec -it "$service" bash -lc "python3 '$script'";;
  esac
}

# --- Main flow ---
mkdir -p "$WORKDIR"
cp -r "$CLI_FILE" "$WORKDIR/"
cd "$WORKDIR"

ZIP_FILE="generation.zip"
if [[ "$FORCE_REDOWNLOAD" == "true" || ! -s "$ZIP_FILE" ]]; then
  say "Downloading generation bundle..."
  fetch "$ZIP_URL" "$ZIP_FILE"
else
  say "Using cached $ZIP_FILE"
fi
echo "$GENERATION_SHA256  $ZIP_FILE" | sha256sum -c -

EXT_TOOLS_FILE="ext_tools.zip"
if [[ "$FORCE_REDOWNLOAD" == "true" || ! -s "$EXT_TOOLS_FILE" ]]; then
  say "Downloading external-tools bundle..."
  fetch "$EXT_TOOLS_URL" "$EXT_TOOLS_FILE"
else
  say "Using cached $EXT_TOOLS_FILE"
fi
echo "$EXT_TOOLS_SHA256  $EXT_TOOLS_FILE" | sha256sum -c -
if [[ ! -f ext_tools/dataset/bosch_test.json ]]; then
  say "Unzipping external-tools bundle..."
  unzip -q -o "$EXT_TOOLS_FILE"
fi
[[ -f ext_tools/dataset/bosch_test.json ]] || die "ext_tools dataset missing after extraction"

# check to see if we have already unzipped in the workdir
if find . -maxdepth 3 -type f -name "$COMPOSE_FILE_NAME" | grep -q .; then
  say "$COMPOSE_FILE_NAME already present; skipping unzip."
else
  say "Unzipping..."
  safe_unzip "$ZIP_FILE"
fi

# Locate compose
COMPOSE_FILE="$(find . -maxdepth 3 -type f -name "$COMPOSE_FILE_NAME" | head -n 1 || true)"
[[ -n "$COMPOSE_FILE" ]] || die "Could not find $COMPOSE_FILE_NAME after unzip."
COMPOSE_DIR="$(cd "$(dirname "$COMPOSE_FILE")" && pwd)"
say "Found $COMPOSE_FILE_NAME at: $COMPOSE_DIR"
cp -r "$CLI_FILE" "$COMPOSE_DIR/"

# Resolve engine & compose command
ENGINE="$(autodetect_engine)"
CMD="$(compose_cmd "$ENGINE")"
say "Using container engine: $ENGINE (command: '$CMD')"

# Targeted patches
if [[ "$ENGINE" == "podman" && "$PATCH_COMPOSE_INPLACE" == "false" ]]; then
  # Default to patching for podman unless the user says otherwise
  PATCH_COMPOSE_INPLACE="true"
fi
if [[ "$ENGINE" == "podman" && "$PATCH_COMPOSE_INPLACE" == "true" ]]; then
  patch_compose_inplace_for_podman "$COMPOSE_DIR"
elif [[ "$ENGINE" == "docker" && "$PATCH_COMPOSE_INPLACE" == "true" ]]; then
  patch_compose_inplace_for_docker "$COMPOSE_DIR"
fi
patch_ollama_context "$COMPOSE_DIR"
if [[ "$PATCH_DOCKERFILE_CMD" == "true" ]]; then
  patch_dockerfile_inplace "$COMPOSE_DIR"
fi
stage_model_archive "$COMPOSE_DIR"
patch_app_dockerfile_for_models "$COMPOSE_DIR"
if [[ "$PATCH_TRAINER_SAVE" == "true" ]]; then
  patch_trainer_save_block "$COMPOSE_DIR"
fi
if [[ "$PATCH_REQUIREMENTS" == "true" ]]; then
  patch_requirements_inplace "$COMPOSE_DIR"
fi

# Ensure host bind-mount paths exist BEFORE compose up (fixes getxattr/no such file issues)
ensure_host_paths "$COMPOSE_DIR"

# Either build or bring up or run experiment
if [[ "$RUN_UP" == "true" ]]; then
  say "Starting with $ENGINE using $COMPOSE_FILE_NAME ..."
  bring_up "$COMPOSE_DIR" "$CMD" "$COMPOSE_FILE_NAME"
  tag_buchel_images "$COMPOSE_DIR" "$CMD" "$COMPOSE_FILE_NAME"
  say "Compose is up. To check status: (cd \"$COMPOSE_DIR\" && $CMD -f $COMPOSE_FILE_NAME ps)"
  if [[ "$RUN_EXPERIMENT" == "true" ]]; then
    say "Running experiment script in service '$APP_SERVICE': $RUN_SCRIPT"
    compose_run_script "$COMPOSE_DIR" "$CMD" "$COMPOSE_FILE_NAME" "$APP_SERVICE" "$RUN_SCRIPT"
  fi
else
  build "$COMPOSE_DIR" "$CMD" "$COMPOSE_FILE_NAME"
  tag_buchel_images "$COMPOSE_DIR" "$CMD" "$COMPOSE_FILE_NAME"
  say "Images built and ready to run in the adapter setting"
fi

# Clean up downloaded and unzipped files
if [[ "$CLEANUP" == "true" ]]; then
  say "Cleaning up downloaded and unzipped files..."
  rm -f "$ZIP_FILE" "$EXT_TOOLS_FILE" # don't cleanup COMPOSE_DIR because we use it as shares with the container
fi

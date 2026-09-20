#!/usr/bin/env bash
# Post-setup, end-to-end claim validation and evidence indexing.
set -uo pipefail

CLAIMS_ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
PROJECT=$(cd -- "$CLAIMS_ROOT/.." && pwd)
PYTHON_BIN=${FRAMEWORK_PYTHON:-$PROJECT/.venv/bin/python}
ENGINE=${CONTAINER_ENGINE:-docker}
PROFILE=full
DEVICE=cpu
COMMAND=all
SELECTION=""
SMOKE_SCOPE=""
SCOPE=""
VERBOSE=false
RESUME_DIR=""
RESUME_LATEST=false
REQUESTED_RUN_DIR=""
SUMMARY_REPORT=""
FIDELITY_SMOKE=false

if [[ $# -gt 0 ]]; then
    case "$1" in
        smoke|reproduce|benchmark)
            COMMAND=$1
            shift
            ;;
    esac
fi

usage() {
    cat <<'EOF'
Usage:
  claims.sh (--core | --optional | --all) [options]
  claims.sh (smoke | reproduce) --scope core|optional|all [options]
  claims.sh benchmark [options]
  claims.sh (--resume-run RUN_DIR | --resume-latest)

Selections:
  --core       Smoke-test AttacKG, TTPDrill, LADDER, and TTP-LLM; run the
               four core reproductions; then evaluate benchmark results.
  --optional   Smoke-test the five optional-reproduction containers; run the
               reproductions; then evaluate benchmark results.
  --all        Smoke-test all ten integrations and run every reproduction in
               paper experiment order; then evaluate benchmark results.

Options:
  --resume-run RUN_DIR      Continue an interrupted claims/runs/<id> in place;
                            selection and runtime options come from its metadata
  --resume-latest           Continue the newest run under claims/runs
  --scope core|optional|all Selection for a standalone smoke or reproduce phase
  --run-dir RUN_DIR         Output directory for a new standalone phase run
  --summary-report PATH     Also write the generated report to PATH
  --engine docker|podman   Container engine (default: CONTAINER_ENGINE or docker)
  --smoke-scope core|optional|all
                           Override only the smoke-test scope (default: selection)
  --profile full|smoke     Reproduction scope (default: full)
  --device cpu|cuda        Optional-reproduction device (default: cpu)
  --verbose                Stream detailed phase output while retaining logs
  --fidelity-smoke         Run paired native/framework smoke tests and apply
                           the hybrid fidelity policy
  -h, --help               Show this help

Core is the modest-resource evaluator path. TTP-LLM still uses its external
API. Optional/full runs may take substantially longer and can require a GPU.
EOF
}

select_scope() {
    [[ -z "$SELECTION" ]] || { echo "Choose only one of --core, --optional, or --all" >&2; exit 2; }
    SELECTION=$1
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --core) select_scope core; shift;;
        --optional) select_scope optional; shift;;
        --all) select_scope all; shift;;
        --engine|--profile|--device|--smoke-scope|--scope|--run-dir|--summary-report)
            [[ $# -ge 2 ]] || { echo "Missing value for $1" >&2; usage; exit 2; }
            case "$1" in
                --engine) ENGINE=$2;;
                --profile) PROFILE=$2;;
                --device) DEVICE=$2;;
                --smoke-scope) SMOKE_SCOPE=$2;;
                --scope) SCOPE=$2;;
                --run-dir) REQUESTED_RUN_DIR=$2;;
                --summary-report) SUMMARY_REPORT=$2;;
            esac
            shift 2
            ;;
        --resume-run)
            [[ $# -ge 2 ]] || { echo "Missing value for $1" >&2; usage; exit 2; }
            RESUME_DIR=$2
            shift 2
            ;;
        --resume-latest) RESUME_LATEST=true; shift;;
        --verbose) VERBOSE=true; shift;;
        --fidelity-smoke) FIDELITY_SMOKE=true; shift;;
        -h|--help) usage; exit 0;;
        *) echo "Unknown option: $1" >&2; usage; exit 2;;
    esac
done

if $RESUME_LATEST; then
    [[ -z "$RESUME_DIR" && -z "$SELECTION" && -z "$SCOPE" && -z "$REQUESTED_RUN_DIR" && "$COMMAND" == all ]] || {
        echo "Do not combine --resume-latest with a selection, phase, --run-dir, or --resume-run" >&2
        exit 2
    }
    RESUME_DIR=$(find "$CLAIMS_ROOT/runs" -mindepth 1 -maxdepth 1 -type d -printf '%T@\t%p\n' 2>/dev/null | sort -nr | head -1 | cut -f2-)
    [[ -n "$RESUME_DIR" ]] || { echo "No claims runs are available to resume" >&2; exit 2; }
fi

if [[ -n "$RESUME_DIR" ]]; then
    [[ -z "$SELECTION" && -z "$SCOPE" && -z "$REQUESTED_RUN_DIR" ]] || {
        echo "Do not combine resume with a selection or --run-dir" >&2
        exit 2
    }
    [[ -d "$RESUME_DIR" && -f "$RESUME_DIR/metadata.tsv" && -f "$RESUME_DIR/status.tsv" ]] || {
        echo "Not a claims run directory: $RESUME_DIR" >&2
        exit 2
    }
    RUN_DIR=$(cd "$RESUME_DIR" && pwd)
    metadata_value() { awk -F '\t' -v key="$1" '$1 == key { value=$2 } END { print value }' "$RUN_DIR/metadata.tsv"; }
    COMMAND=$(metadata_value command)
    COMMAND=${COMMAND:-all}
    SELECTION=$(metadata_value selection)
    SMOKE_SCOPE=$(metadata_value smoke_scope)
    PROFILE=$(metadata_value profile)
    ENGINE=$(metadata_value engine)
    DEVICE=$(metadata_value device)
    FIDELITY_SMOKE=false
    [[ "$(metadata_value fidelity_smoke)" == true ]] && FIDELITY_SMOKE=true
else
    case "$COMMAND" in
        all)
            [[ -z "$SCOPE" && -z "$REQUESTED_RUN_DIR" ]] || {
                echo "--scope and --run-dir are for standalone phase commands" >&2
                exit 2
            }
            [[ -n "$SELECTION" ]] || { echo "One of --core, --optional, or --all is required" >&2; usage; exit 2; }
            ;;
        smoke|reproduce)
            [[ -z "$SELECTION" && -z "$SMOKE_SCOPE" ]] || {
                echo "Use --scope, not combined-run selection options, with $COMMAND" >&2
                exit 2
            }
            [[ "$SCOPE" == core || "$SCOPE" == optional || "$SCOPE" == all ]] || {
                echo "$COMMAND requires --scope core|optional|all" >&2
                exit 2
            }
            SELECTION=$SCOPE
            SMOKE_SCOPE=$SCOPE
            ;;
        benchmark)
            [[ -z "$SELECTION" && -z "$SCOPE" && -z "$SMOKE_SCOPE" ]] || {
                echo "benchmark does not accept a selection or scope" >&2
                exit 2
            }
            SELECTION=benchmark
            SMOKE_SCOPE=none
            ;;
    esac
fi
[[ "$ENGINE" == docker || "$ENGINE" == podman ]] || { echo "Invalid engine: $ENGINE" >&2; exit 2; }
[[ "$PROFILE" == full || "$PROFILE" == smoke ]] || { echo "Invalid profile: $PROFILE" >&2; exit 2; }
[[ "$DEVICE" == cpu || "$DEVICE" == cuda ]] || { echo "Invalid device: $DEVICE" >&2; exit 2; }
SMOKE_SCOPE=${SMOKE_SCOPE:-$SELECTION}
[[ "$SMOKE_SCOPE" == core || "$SMOKE_SCOPE" == optional || "$SMOKE_SCOPE" == all || "$SMOKE_SCOPE" == none ]] || {
    echo "Invalid smoke scope: $SMOKE_SCOPE" >&2
    exit 2
}
[[ -x "$PYTHON_BIN" ]] || { echo "Framework Python not found: $PYTHON_BIN; run install.sh first" >&2; exit 1; }
export PYTHONPATH="$PROJECT${PYTHONPATH:+:$PYTHONPATH}"

CORE_SMOKE=(AttacKG TTPDrill LADDER TTP-LLM)
OPTIONAL_SMOKE=(Buchel Orbinato rcATT RAF-AG SeqMask)
CORE_EXPERIMENTS=(attackg_table4 ladder_table9 buchel_table13 ttpllm_table2)
OPTIONAL_EXPERIMENTS=(buchel_table9 orbinato_fig3 rcatt_table6 rafag_table6 seqmask_table7 seqmask_table14 seqmask_table15)
case "$SMOKE_SCOPE" in
    core) SMOKE_TOOLS=("${CORE_SMOKE[@]}");;
    optional) SMOKE_TOOLS=("${OPTIONAL_SMOKE[@]}");;
    all) SMOKE_TOOLS=("${CORE_SMOKE[@]}" "${OPTIONAL_SMOKE[@]}" TRAM);;
esac
case "$SELECTION" in
    core) REPRO_EXPERIMENTS=("${CORE_EXPERIMENTS[@]}");;
    optional) REPRO_EXPERIMENTS=("${OPTIONAL_EXPERIMENTS[@]}");;
    all) REPRO_EXPERIMENTS=("${CORE_EXPERIMENTS[@]}" "${OPTIONAL_EXPERIMENTS[@]}");;
esac

if [[ -n "$RESUME_DIR" ]]; then
    RUN_ID=$(basename "$RUN_DIR")
elif [[ -n "$REQUESTED_RUN_DIR" ]]; then
    [[ ! -e "$REQUESTED_RUN_DIR/metadata.tsv" && ! -e "$REQUESTED_RUN_DIR/status.tsv" ]] || {
        echo "Run directory already contains claims metadata; use --resume-run: $REQUESTED_RUN_DIR" >&2
        exit 2
    }
    mkdir -p "$REQUESTED_RUN_DIR"
    RUN_DIR=$(cd "$REQUESTED_RUN_DIR" && pwd)
    RUN_ID=$(basename "$RUN_DIR")
else
    RUN_ID=$(date -u +%Y%m%dT%H%M%SZ)_$$
    RUN_DIR=$CLAIMS_ROOT/runs/$RUN_ID
fi
mkdir -p "$RUN_DIR/smoke/logs" "$RUN_DIR/smoke/results" "$RUN_DIR/reproductions" "$RUN_DIR/benchmark"
STATUS=$RUN_DIR/status.tsv
LOG=$RUN_DIR/claims.log
if [[ -z "$RESUME_DIR" ]]; then
    printf 'phase\tname\tstatus\tlog\tevidence\telapsed_seconds\n' > "$STATUS"
fi
STARTED=$(date -u +%Y-%m-%dT%H:%M:%SZ)
if [[ -z "$RESUME_DIR" ]]; then
    printf 'command\t%s\nselection\t%s\nsmoke_scope\t%s\nprofile\t%s\nengine\t%s\ndevice\t%s\nfidelity_smoke\t%s\nstarted_utc\t%s\n' \
        "$COMMAND" "$SELECTION" "$SMOKE_SCOPE" "$PROFILE" "$ENGINE" "$DEVICE" "$FIDELITY_SMOKE" "$STARTED" > "$RUN_DIR/metadata.tsv"
else
    printf 'resumed_utc\t%s\n' "$STARTED" >> "$RUN_DIR/metadata.tsv"
fi

overall=0
finished=false
finalize() {
    local shell_code=$?
    local -a report_command
    trap - EXIT
    [[ "$shell_code" == 0 ]] || overall=1
    if [[ "$finished" != true ]]; then
        printf 'finished_utc\t%s\nexit_code\t%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$overall" >> "$RUN_DIR/metadata.tsv"
        report_command=("$PYTHON_BIN" "$CLAIMS_ROOT/build_report.py" --run-dir "$RUN_DIR")
        [[ -n "$SUMMARY_REPORT" ]] && report_command+=(--summary-report "$SUMMARY_REPORT")
        "${report_command[@]}" || overall=1
        finished=true
    fi
    echo "Claims report: ${SUMMARY_REPORT:-$CLAIMS_ROOT/REPORT.md}"
    exit "$overall"
}
trap finalize EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

record() {
    if [[ -n "$RESUME_DIR" ]]; then
        local replacement
        replacement=$(mktemp "$RUN_DIR/status.XXXXXXXX") || return 1
        awk -F '\t' -v phase="$1" -v name="$2" 'NR == 1 || $1 != phase || $2 != name' "$STATUS" > "$replacement"
        mv "$replacement" "$STATUS"
    fi
    printf '%s\t%s\t%s\t%s\t%s\t%s\n' "$1" "$2" "$3" "$4" "$5" "$6" >> "$STATUS"
}

phase_status() {
    awk -F '\t' -v phase="$1" -v name="$2" '$1 == phase && $2 == name { value=$3 } END { print value }' "$STATUS"
}

elapsed_since() {
    local started_ns=$1
    local finished_ns
    finished_ns=$(date +%s%N)
    awk -v start="$started_ns" -v finish="$finished_ns" 'BEGIN { printf "%.3f", (finish-start)/1000000000 }'
}

run_logged() {
    local destination=$1
    shift
    if $VERBOSE; then
        "$@" 2>&1 | tee "$destination"
        return "${PIPESTATUS[0]}"
    fi
    "$@" > "$destination" 2>&1
}

run_logged_append() {
    local destination=$1
    shift
    if $VERBOSE; then
        "$@" 2>&1 | tee -a "$destination"
        return "${PIPESTATUS[0]}"
    fi
    "$@" >> "$destination" 2>&1
}

run_smoke_phase() {
    local tool slug smoke_log evidence unit_started_ns code
    local -a command

    echo "[1/3] Functional smoke tests" | tee -a "$LOG"
    for tool in "${SMOKE_TOOLS[@]}"; do
        if [[ -n "$RESUME_DIR" && "$(phase_status smoke "$tool")" == PASS ]]; then
            echo "Smoke test already PASS: $tool (skipping)" | tee -a "$LOG"
            continue
        fi
        slug=$(printf '%s' "$tool" | tr '[:upper:]' '[:lower:]')
        smoke_log=smoke/logs/${slug}.log
        evidence=smoke/results/${slug}/latest_summary.json
        echo "Smoke test: $tool" | tee -a "$LOG"
        command=(
            "$PYTHON_BIN" "$PROJECT/Tests/poc_tests/run_poc_tests.py"
            --engine "$ENGINE" --adapter "$tool" --no-compare
            --device "$DEVICE"
            --output-root "$RUN_DIR/smoke/results"
            --verbose
        )
        if $FIDELITY_SMOKE; then
            command+=(
                --backend both
                --fidelity-policy hybrid
                --native-root "$PROJECT/Tests/Reproductions/.runtime/external_tools"
            )
        fi
        printf '%q ' "${command[@]}" >> "$LOG"; printf '\n' >> "$LOG"
        unit_started_ns=$(date +%s%N)
        if run_logged "$RUN_DIR/$smoke_log" "${command[@]}"; then
            record smoke "$tool" PASS "$smoke_log" "$evidence" "$(elapsed_since "$unit_started_ns")"
        else
            code=$?
            record smoke "$tool" "FAIL ($code)" "$smoke_log" "$evidence" "$(elapsed_since "$unit_started_ns")"
            overall=1
            echo "Smoke test failed: $tool (see $RUN_DIR/$smoke_log)" | tee -a "$LOG" >&2
        fi
    done
}

run_reproduction_phase() {
    local repro_log=reproductions/run_all.log
    local unit_started_ns repro_code=0 repro_executed=false previous_repro_status
    local experiment runs_root latest
    local -a repro_command

    echo "[2/3] Reproduction experiments" | tee -a "$LOG"
    unit_started_ns=$(date +%s%N)
    previous_repro_status=$(phase_status reproduction "$SELECTION")
    if [[ -z "$RESUME_DIR" ]]; then
        repro_executed=true
        repro_command=(
            bash "$PROJECT/Tests/Reproductions/run_all.sh" "--$SELECTION"
            --profile "$PROFILE" --device "$DEVICE" --engine "$ENGINE"
            --out-dir "$RUN_DIR/reproductions"
        )
        $VERBOSE && repro_command+=(--verbose)
        printf '%q ' "${repro_command[@]}" >> "$LOG"; printf '\n' >> "$LOG"
        run_logged "$RUN_DIR/$repro_log" "${repro_command[@]}" || repro_code=$?
    else
        : > "$RUN_DIR/$repro_log.resume"
        for experiment in "${REPRO_EXPERIMENTS[@]}"; do
            runs_root=$RUN_DIR/reproductions/experiments/$experiment/runs
            latest=""
            if [[ -d "$runs_root" ]]; then
                latest=$(find "$runs_root" -mindepth 1 -maxdepth 1 -type d -printf '%T@\t%p\n' | sort -nr | head -1 | cut -f2-)
            fi
            if [[ -n "$latest" && -f "$latest/REPORT.md" && -f "$latest/status.tsv" ]] && ! grep -q $'\tFAIL' "$latest/status.tsv"; then
                echo "Reproduction already complete: $experiment (skipping)" | tee -a "$LOG" "$RUN_DIR/$repro_log.resume"
                continue
            fi
            repro_command=(
                bash "$PROJECT/Tests/Reproductions/run_all.sh" --experiment "$experiment"
                --profile "$PROFILE" --device "$DEVICE" --engine "$ENGINE"
                --out-dir "$RUN_DIR/reproductions"
            )
            [[ -n "$latest" ]] && repro_command+=(--resume-run "$latest")
            $VERBOSE && repro_command+=(--verbose)
            repro_executed=true
            printf '%q ' "${repro_command[@]}" >> "$LOG"; printf '\n' >> "$LOG"
            run_logged_append "$RUN_DIR/$repro_log.resume" "${repro_command[@]}" || repro_code=$?
        done
        mv "$RUN_DIR/$repro_log.resume" "$RUN_DIR/$repro_log"
        if $repro_executed; then
            repro_command=(
                "$PYTHON_BIN" "$PROJECT/Tests/Reproductions/helpers/build_reports.py"
                --root "$RUN_DIR/reproductions" --summary-only --selection "$SELECTION"
            )
            printf '%q ' "${repro_command[@]}" >> "$LOG"; printf '\n' >> "$LOG"
            run_logged_append "$RUN_DIR/$repro_log" "${repro_command[@]}" || repro_code=$?
        fi
    fi
    if [[ "$repro_code" == 0 ]]; then
        if [[ -n "$RESUME_DIR" && "$repro_executed" == false && "$previous_repro_status" == PASS ]]; then
            echo "Reproduction phase already PASS: $SELECTION (preserving prior timing)" | tee -a "$LOG"
        else
            record reproduction "$SELECTION" PASS "$repro_log" reproductions/SUMMARY.md "$(elapsed_since "$unit_started_ns")"
        fi
    else
        record reproduction "$SELECTION" "FAIL ($repro_code)" "$repro_log" reproductions/SUMMARY.md "$(elapsed_since "$unit_started_ns")"
        overall=1
        echo "Reproduction phase recorded failures (see $RUN_DIR/$repro_log)" | tee -a "$LOG" >&2
    fi
}

run_benchmark_phase() {
    local benchmark_log=benchmark/launcher.log
    local unit_started_ns code
    local -a benchmark_command=(
        "$PYTHON_BIN" "$PROJECT/Tests/Benchmark_results/reproduce_benchmark_figures.py"
        --output-dir "$RUN_DIR/benchmark/figures"
        --report "$RUN_DIR/benchmark/REPORT.md"
    )

    echo "[3/3] Benchmark Figures 4-6 and 8" | tee -a "$LOG"
    printf '%q ' "${benchmark_command[@]}" >> "$LOG"; printf '\n' >> "$LOG"
    unit_started_ns=$(date +%s%N)
    if run_logged "$RUN_DIR/$benchmark_log" "${benchmark_command[@]}"; then
        record benchmark figures_4_6_8 PASS "$benchmark_log" benchmark/REPORT.md "$(elapsed_since "$unit_started_ns")"
    else
        code=$?
        record benchmark figures_4_6_8 "FAIL ($code)" "$benchmark_log" benchmark/REPORT.md "$(elapsed_since "$unit_started_ns")"
        overall=1
        echo "Benchmark phase failed (see $RUN_DIR/$benchmark_log)" | tee -a "$LOG" >&2
    fi
}

if [[ -n "$RESUME_DIR" ]]; then action=resume; else action=run; fi
echo "Claims $action: $RUN_ID (command $COMMAND, selection $SELECTION, smoke scope $SMOKE_SCOPE, reproduction profile $PROFILE)" | tee -a "$LOG"
case "$COMMAND" in
    all)
        run_smoke_phase
        run_reproduction_phase
        run_benchmark_phase
        ;;
    smoke) run_smoke_phase;;
    reproduce) run_reproduction_phase;;
    benchmark) run_benchmark_phase;;
esac

exit "$overall"

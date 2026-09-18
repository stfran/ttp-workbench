#!/usr/bin/env bash
# Call each existing-style runner in its own environment; no activation required.
set -uo pipefail
printf -v RUN_INVOCATION '%q ' bash "${BASH_SOURCE[0]}" "$@"
RUN_INVOCATION=${RUN_INVOCATION% }
RUNNERS=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
PROJECT=$(cd "$RUNNERS/../.." && pwd)
TOOLS=${REPRO_EXTERNAL_ROOT:-$RUNNERS/.runtime/external_tools}
PROFILE=full
OPTIONAL_DEVICE=cuda
DRY_RUN=false
VERBOSE=false
EXPERIMENT=all
OUT=""
RESUME_RUN=""
CONTAINER_ENGINE=${CONTAINER_ENGINE:-podman}
SELECTION_SET=false
select_experiments() {
    if $SELECTION_SET; then echo "Conflicting experiment selectors" >&2; exit 2; fi
    EXPERIMENT=$1; SELECTION_SET=true
}
while [[ $# -gt 0 ]]; do
    case "$1" in
        --profile) PROFILE=$2; shift 2;;
        --device) OPTIONAL_DEVICE=$2; shift 2;;
        --engine) CONTAINER_ENGINE=$2; shift 2;;
        --experiment) select_experiments "$2"; shift 2;;
        --all) select_experiments all; shift;;
        --core) select_experiments core; shift;;
        --optional) select_experiments optional; shift;;
        --out-dir) OUT=$2; shift 2;;
        --resume-run) RESUME_RUN=$2; shift 2;;
        --dry-run) DRY_RUN=true; shift;;
        --verbose) VERBOSE=true; shift;;
        -h|--help)
            echo 'run_all.sh [--all | --core | --optional | --experiment NAME] [--resume-run EXPERIMENT_RUN] [--profile full|smoke] [--device cpu|cuda] [--engine podman|docker] [--out-dir REPORT_ROOT] [--dry-run] [--verbose]'
            echo 'Defaults: all experiments, full scope, CUDA for optional models, and CONTAINER_ENGINE or podman.'
            echo 'Core: attackg_table4, ladder_table9, buchel_table13, ttpllm_table2.'
            echo 'Optional: buchel_table9, orbinato_fig3, rcatt_table6, rafag_table6, and the three SeqMask tables.'
            echo '--resume-run reuses one existing run for the matching single --experiment and skips calls already marked PASS.'
            exit 0;;
        *) echo "Unknown option: $1" >&2; exit 2;;
    esac
done
[[ "$PROFILE" == smoke || "$PROFILE" == full ]] || exit 2
if [[ "$OPTIONAL_DEVICE" != cpu && "$OPTIONAL_DEVICE" != cuda ]]; then
    echo "Unknown device: $OPTIONAL_DEVICE (expected cpu or cuda)" >&2
    exit 2
fi
if [[ "$CONTAINER_ENGINE" != podman && "$CONTAINER_ENGINE" != docker ]]; then
    echo "Unknown container engine: $CONTAINER_ENGINE (expected podman or docker)" >&2
    exit 2
fi
case "$EXPERIMENT" in
    all|core|optional|attackg_table4|ladder_table9|buchel_table13|buchel_table9|ttpllm_table2|orbinato_fig3|rcatt_table6|rafag_table6|seqmask_table7|seqmask_table14|seqmask_table15) ;;
    *) echo "Unknown experiment: $EXPERIMENT (see --help for paper/table names)" >&2; exit 2;;
esac
if [[ -n "$RESUME_RUN" ]]; then
    [[ "$DRY_RUN" == false ]] || { echo "--resume-run cannot be combined with --dry-run" >&2; exit 2; }
    case "$EXPERIMENT" in all|core|optional) echo "--resume-run requires one specific --experiment" >&2; exit 2;; esac
    [[ -d "$RESUME_RUN" && -f "$RESUME_RUN/run.json" ]] || {
        echo "Resume run is not an experiment run directory: $RESUME_RUN" >&2
        exit 2
    }
    RESUME_RUN=$(cd "$RESUME_RUN" && pwd)
    resume_experiment=$(basename "$(dirname "$(dirname "$RESUME_RUN")")")
    [[ "$resume_experiment" == "$EXPERIMENT" ]] || {
        echo "Resume run belongs to $resume_experiment, not $EXPERIMENT" >&2
        exit 2
    }
    [[ "$(basename "$RESUME_RUN")" == "${PROFILE}_"* ]] || {
        echo "Resume run does not match --profile $PROFILE: $RESUME_RUN" >&2
        exit 2
    }
fi
REPORT_ROOT=${OUT:-$RUNNERS}
if $DRY_RUN; then
    RUN_ID="<new-run>"
else
    mkdir -p "$REPORT_ROOT" "$TOOLS/cache/huggingface" "$TOOLS/cache/nltk"
    REPORT_ROOT=$(cd "$REPORT_ROOT" && pwd)
fi
export PYTHONPATH="$PROJECT${PYTHONPATH:+:$PYTHONPATH}"
export HF_HOME="$TOOLS/cache/huggingface"
export NLTK_DATA="$TOOLS/cache/nltk"
export WANDB_MODE=disabled
export PYTHONUNBUFFERED=1
export CONTAINER_ENGINE
FRAMEWORK_PYTHON=${FRAMEWORK_PYTHON:-$PROJECT/.venv/bin/python}
RUN_TIMEOUT_SECONDS=${RUN_TIMEOUT_SECONDS:-0}
failed=0
track() {
    $DRY_RUN && return 0
    "$FRAMEWORK_PYTHON" "$RUNNERS/helpers/execution_tracking.py" "$@" --record "$EXECUTION_RECORD"
}
finish_launch() {
    local code=$?
    trap - EXIT
    if ! $DRY_RUN; then
        track end-suite --code "$code" || code=1
        "$FRAMEWORK_PYTHON" "$RUNNERS/helpers/build_reports.py" --root "$REPORT_ROOT" --summary-only --selection "$EXPERIMENT" || code=1
        echo "Summary: $REPORT_ROOT/SUMMARY.md"
    fi
    exit "$code"
}
if ! $DRY_RUN; then
    mkdir -p "$REPORT_ROOT/executions"
    EXECUTION_DIR=$(mktemp -d "$REPORT_ROOT/executions/${PROFILE}_XXXXXXXX") || exit 1
    EXECUTION_RECORD="$EXECUTION_DIR/timing.json"
    track start-suite --invocation "$RUN_INVOCATION" || exit 1
    trap finish_launch EXIT
    trap 'exit 130' INT
    trap 'exit 143' TERM
fi
finish_experiment() {
    local report_code=0
    # Publish this run and refresh the indexes before starting the next experiment.
    if $DRY_RUN; then
        printf '%q ' "$FRAMEWORK_PYTHON" "$RUNNERS/helpers/build_reports.py" --root "$REPORT_ROOT" --summary-only --selection "$EXPERIMENT"
        printf '\n'
        return
    fi
    if [[ "$CURRENT_EXPERIMENT" != orbinato_fig3 && "$CURRENT_EXPERIMENT" != rcatt_table6 && "$CURRENT_EXPERIMENT" != rafag_table6 && "$CURRENT_EXPERIMENT" != seqmask_table* ]]; then
        "$FRAMEWORK_PYTHON" "$RUNNERS/helpers/build_reports.py" --run-dir "$EXP_RUN" || report_code=$?
    fi
    [[ "$report_code" == 0 ]] || failed=1
    track end-experiment --code "$report_code" || failed=1
    "$FRAMEWORK_PYTHON" "$RUNNERS/helpers/build_reports.py" --root "$REPORT_ROOT" --summary-only --selection "$EXPERIMENT" || failed=1
}
begin_experiment() {
    CURRENT_EXPERIMENT=$1
    if $DRY_RUN; then
        EXP_RUN="$REPORT_ROOT/experiments/$1/runs/$RUN_ID"
    elif [[ -n "$RESUME_RUN" ]]; then
        EXP_RUN=$RESUME_RUN
    else
        mkdir -p "$REPORT_ROOT/experiments/$1/runs"
        EXP_RUN=$(mktemp -d "$REPORT_ROOT/experiments/$1/runs/${PROFILE}_XXXXXXXX") || exit 1
    fi
    RESULTS="$EXP_RUN/results"
    if $DRY_RUN; then return; fi
    mkdir -p "$EXP_RUN/logs" "$EXP_RUN/tmp" "$RESULTS"
    export TMPDIR="$EXP_RUN/tmp"
    if [[ -z "$RESUME_RUN" ]]; then
        "$FRAMEWORK_PYTHON" "$RUNNERS/helpers/build_reports.py" --register "$EXP_RUN" --experiment "$1" --profile "$PROFILE" \
            --invocation "$RUN_INVOCATION" --invocation-cwd "$PWD" || exit 1
    fi
    track start-experiment --experiment "$1" --run "$EXP_RUN" || exit 1
    echo "=== EXPERIMENT: $1 ==="
    if [[ -n "$RESUME_RUN" ]]; then echo "Resuming run directory: $EXP_RUN"; else echo "Run directory: $EXP_RUN"; fi
}
call() {
    local name=$1 python=$2; shift 2
    # Only these runners expose --verbose; preview output from every runner.
    if $VERBOSE; then
        case "${1##*/}" in
            reproduce_attackg_table4.py|reproduce_buchel_table13.py|reproduce_buchel_table9.py|reproduce_ttpllm_table2.py|reproduce_orbinato_fig3.py|reproduce_rcatt_table6.py|reproduce_rafag_table6.py|reproduce_seqmask.py) set -- "$@" --verbose;;
        esac
    fi
    if $DRY_RUN; then printf '%q ' "$python" "$@"; printf '\n'; return; fi
    local source="EXPERIMENT RUNNER (report generation)"
    case "$name" in
        *_comparison) source="EXPERIMENT RUNNER (CSV/JSON comparison)";;
        *_original) source="ORIGINAL TOOL (without framework)";;
        *_framework) source="FRAMEWORK ADAPTER";;
    esac
    local header="=== RUN: $name | $source ==="
    case "$name" in
        *_report|*_comparison) ;;
        *)
            if [[ -n "$RESUME_RUN" && -f "$EXP_RUN/status.tsv" ]] && grep -Fqx "$name"$'\tPASS' "$EXP_RUN/status.tsv"; then
                echo "=== RESUME: $name already PASS; skipping ==="
                return 0
            fi
            ;;
    esac
    echo "$header"
    if [[ -n "$RESUME_RUN" && -f "$EXP_RUN/logs/$name.log" ]]; then
        local prior_logs="$EXP_RUN/logs/failed_attempts"
        mkdir -p "$prior_logs"
        mv "$EXP_RUN/logs/$name.log" "$prior_logs/${name}.$(date -u +%Y%m%dT%H%M%S%NZ).log"
    fi
    local limit=()
    if [[ "$RUN_TIMEOUT_SECONDS" != 0 ]]; then limit=(timeout --kill-after=30 "$RUN_TIMEOUT_SECONDS"); fi
    local native_lib=()
    if [[ "$python" == "$TOOLS/LADDER/.venv/bin/python" ]]; then
        local executable
        executable=$(readlink -f "$python")
        native_lib=(env "LD_LIBRARY_PATH=$(dirname "$executable")/../lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}")
    fi
    printf '%q ' "${native_lib[@]}" "${limit[@]}" "$python" "$@" >> "$EXP_RUN/commands.log"
    printf '\n' >> "$EXP_RUN/commands.log"
    if [[ -n "$RESUME_RUN" && -f "$EXP_RUN/status.tsv" ]]; then
        local filtered_status
        filtered_status=$(mktemp "$EXP_RUN/tmp/status.XXXXXXXX") || exit 1
        awk -F '\t' -v call_name="$name" '$1 != call_name' "$EXP_RUN/status.tsv" > "$filtered_status"
        mv "$filtered_status" "$EXP_RUN/status.tsv"
    fi
    track start-call --name "$name" || exit 1
    local code=0
    printf '%s\n' "$header" '=== EXPERIMENT RUNNER OUTPUT ===' > "$EXP_RUN/logs/$name.log"
    if $VERBOSE; then
        "${native_lib[@]}" "${limit[@]}" "$python" "$@" 2>&1 |
            "$FRAMEWORK_PYTHON" "$RUNNERS/helpers/live_log.py" --log "$EXP_RUN/logs/$name.log" --append
        local codes=("${PIPESTATUS[@]}")
        code=${codes[0]}
        if [[ ${codes[1]} != 0 ]]; then
            echo "Log preview failed: $name (exit ${codes[1]})" >&2
            [[ "$code" != 0 ]] || code=${codes[1]}
        fi
    else
        "${native_lib[@]}" "${limit[@]}" "$python" "$@" >> "$EXP_RUN/logs/$name.log" 2>&1 || code=$?
    fi
    track end-call --code "$code" || failed=1
    if [[ "$code" == 0 ]]; then
        printf '%s\tPASS\n' "$name" >> "$EXP_RUN/status.tsv"
        $VERBOSE && echo "PASS $name"
    else
        printf '%s\tFAIL (%s)\n' "$name" "$code" >> "$EXP_RUN/status.tsv"
        echo "FAIL $name (exit $code; see $EXP_RUN/logs/$name.log)" >&2
        failed=1
    fi
}
if [[ "$EXPERIMENT" == all || "$EXPERIMENT" == core || "$EXPERIMENT" == attackg_table4 ]]; then
    begin_experiment attackg_table4
    script="$RUNNERS/reproduce_attackg_table4.py"
    for tool in attackg ttpdrill; do
        if [[ "$tool" == attackg ]]; then flag=--run_original_all; dir=AttacKG; else flag=--run_ttpdrill_original_all; dir=TTPDrill; fi
        call "attackg_table4_${tool}_original" "$TOOLS/$dir/.venv/bin/python" "$script" "$flag" --profile "$PROFILE" --out-dir "$RESULTS"
        if [[ "$tool" == attackg ]]; then flag=--run_adapter_all; else flag=--run_ttpdrill_adapter_all; fi
        call "attackg_table4_${tool}_framework" "$FRAMEWORK_PYTHON" "$script" "$flag" --profile "$PROFILE" --out-dir "$RESULTS" --engine "$CONTAINER_ENGINE"
    done
    call attackg_table4_report "$FRAMEWORK_PYTHON" "$script" --report-only --profile "$PROFILE" --out-dir "$RESULTS"
    finish_experiment
fi
if [[ "$EXPERIMENT" == all || "$EXPERIMENT" == core || "$EXPERIMENT" == ladder_table9 ]]; then
    begin_experiment ladder_table9
    for tool in ladder attackg ttpdrill; do
        case "$tool" in ladder) dir=LADDER;; attackg) dir=AttacKG;; ttpdrill) dir=TTPDrill;; esac
        for backend in original framework; do
            python="$TOOLS/$dir/.venv/bin/python"; [[ "$backend" == framework ]] && python=$FRAMEWORK_PYTHON
            call "ladder_table9_${tool}_${backend}" "$python" "$RUNNERS/reproduce_ladder_table9.py" --tool "$tool" --backend "$backend" --profile "$PROFILE" --out-dir "$RESULTS" --engine "$CONTAINER_ENGINE"
        done
    done
    call ladder_table9_report "$FRAMEWORK_PYTHON" "$RUNNERS/reproduce_ladder_table9.py" --report-only --profile "$PROFILE" --out-dir "$RESULTS"
    finish_experiment
fi
if [[ "$EXPERIMENT" == all || "$EXPERIMENT" == core || "$EXPERIMENT" == buchel_table13 ]]; then
    begin_experiment buchel_table13
    buchel_resume=()
    [[ -n "$RESUME_RUN" ]] && buchel_resume=(--replace-existing)
    for tool in ladder attackg; do
        dir=LADDER; [[ "$tool" == attackg ]] && dir=AttacKG
        for backend in original framework; do
            python="$TOOLS/BuchelTools/ext_tools/tools/$dir/.venv/bin/python"
            [[ "$backend" == framework ]] && python=$FRAMEWORK_PYTHON
            call "buchel_table13_${tool}_capped_${backend}" "$python" "$RUNNERS/reproduce_buchel_table13.py" --tool "$tool" --variant capped --backend "$backend" --profile "$PROFILE" --out-dir "$RESULTS" --engine "$CONTAINER_ENGINE" "${buchel_resume[@]}"
        done
    done
    call buchel_table13_report "$FRAMEWORK_PYTHON" "$RUNNERS/reproduce_buchel_table13.py" --report-only --profile "$PROFILE" --out-dir "$RESULTS"
    finish_experiment
fi
if [[ "$EXPERIMENT" == all || "$EXPERIMENT" == core || "$EXPERIMENT" == ttpllm_table2 ]]; then
    begin_experiment ttpllm_table2
    call ttpllm_table2_original "$TOOLS/TTP-LLM/.venv/bin/python" "$RUNNERS/reproduce_ttpllm_table2.py" --original --profile "$PROFILE" --out-dir "$RESULTS"
    call ttpllm_table2_framework "$FRAMEWORK_PYTHON" "$RUNNERS/reproduce_ttpllm_table2.py" --adapter --profile "$PROFILE" --out-dir "$RESULTS" --engine "$CONTAINER_ENGINE"
    call ttpllm_table2_report "$FRAMEWORK_PYTHON" "$RUNNERS/reproduce_ttpllm_table2.py" --profile "$PROFILE" --out-dir "$RESULTS"
    finish_experiment
fi
if [[ "$EXPERIMENT" == all || "$EXPERIMENT" == optional || "$EXPERIMENT" == buchel_table9 ]]; then
    begin_experiment buchel_table9
    trial=(); [[ "$PROFILE" == smoke ]] && trial=(--trial --limit 1 --no-doc-level-in-trial)
    buchel_device=(--no-gpus); [[ "$OPTIONAL_DEVICE" == cuda ]] && buchel_device=(--gpus)
    for backend in original framework; do
        python="$TOOLS/Buchel/.venv/bin/python"; [[ "$backend" == framework ]] && python=$FRAMEWORK_PYTHON
        for strategy in raw fsp rag rag_fsp; do
            for dataset in bosch tram; do
                call "buchel_table9_${dataset}_base_${strategy}_${backend}" "$python" "$RUNNERS/reproduce_buchel_table9.py" --backend "$backend" --dataset "$dataset" --strategy "$strategy" --root-out "$RESULTS" --engine "$CONTAINER_ENGINE" "${buchel_device[@]}" "${trial[@]}"
                call "buchel_table9_${dataset}_sft_${strategy}_${backend}" "$python" "$RUNNERS/reproduce_buchel_table9.py" --backend "$backend" --dataset "$dataset" --strategy "$strategy" --only-sft --root-out "$RESULTS" --engine "$CONTAINER_ENGINE" "${buchel_device[@]}" "${trial[@]}"
            done
        done
    done
    call buchel_table9_report "$FRAMEWORK_PYTHON" "$RUNNERS/reproduce_buchel_table9.py" --report-only --root-out "$RESULTS" "${trial[@]}"
    finish_experiment
fi
for slug in orbinato_fig3 rcatt_table6 rafag_table6 seqmask_table7 seqmask_table14 seqmask_table15; do
    [[ "$EXPERIMENT" == all || "$EXPERIMENT" == optional || "$EXPERIMENT" == "$slug" ]] || continue
    begin_experiment "$slug"
    case "$slug" in
        rcatt_table6) script=reproduce_rcatt_table6.py; tool_list=rcATT; extra=();;
        orbinato_fig3) script=reproduce_orbinato_fig3.py; tool_list=Orbinato; extra=();;
        rafag_table6) script=reproduce_rafag_table6.py; tool_list='RAF-AG AttacKG'; extra=();;
        seqmask_table*) script=reproduce_seqmask.py; tool_list='SeqMask rcATT'; extra=(--table "${slug#seqmask_table}");;
    esac
    for tool in $tool_list; do
        for backend in original framework; do
            python="$TOOLS/$tool/.venv/bin/python"
            [[ "$backend" == framework ]] && python=$FRAMEWORK_PYTHON
            resume_failed=(); [[ -n "$RESUME_RUN" ]] && resume_failed=(--resume-failed)
            call "${slug}_${tool}_${backend}" "$python" "$RUNNERS/$script" --tool "$tool" --backend "$backend" --profile "$PROFILE" --out-dir "$RESULTS" --engine "$CONTAINER_ENGINE" --device "$OPTIONAL_DEVICE" "${resume_failed[@]}" "${extra[@]}"
        done
    done
    call "${slug}_comparison" "$FRAMEWORK_PYTHON" "$RUNNERS/$script" --consolidate-only --profile "$PROFILE" --out-dir "$RESULTS" "${extra[@]}"
    finish_experiment
done
exit "$failed"

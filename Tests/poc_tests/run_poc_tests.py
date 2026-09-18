#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
from datetime import datetime, timezone
from typing import Any, Dict, List, Set, Tuple
import argparse
import csv
import hashlib
import json
import os
import subprocess
import sys
import time
import uuid

CODE_POC_ROOT = Path(__file__).resolve().parent
PROJ_ROOT = Path(
    os.environ.get("TTPWB_PROJECT_ROOT", Path(__file__).resolve().parent.parent.parent)
).resolve()
if str(PROJ_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJ_ROOT))

from Framework.utils.config import adapter_module_map
from fidelity import compare_results as compare_fidelity_results, write_fidelity_outputs

POC_ROOT = PROJ_ROOT / "Tests/poc_tests"
RESULTS_ROOT = POC_ROOT / "results"


# ---------------------------------------------------------------------------
# Adapter loading
# ---------------------------------------------------------------------------

def import_adapter(adapter_name: str):
    module_path, class_name = adapter_module_map[adapter_name].rsplit(".", 1)
    module = __import__(module_path, fromlist=[class_name])
    return getattr(module, class_name)


# ---------------------------------------------------------------------------
# Input loading
# ---------------------------------------------------------------------------

def _as_code_set(xs: Any) -> Set[str]:
    if xs is None:
        return set()
    if isinstance(xs, str):
        return {xs.strip()} if xs.strip() else set()
    if isinstance(xs, list):
        return {str(x).strip() for x in xs if str(x).strip()}
    try:
        return {str(x).strip() for x in list(xs) if str(x).strip()}
    except Exception:
        return set()


def load_poc_reports(file_paths: List[Path]) -> Tuple[List[str], List[str], Dict[str, Set[str]], Dict[str, Dict[str, Any]]]:
    texts: List[str] = []
    ids: List[str] = []
    gt_by_id: Dict[str, Set[str]] = {}
    meta_by_id: Dict[str, Dict[str, Any]] = {}

    for file_path in file_paths:
        with open(file_path, "r", encoding="utf-8") as f:
            j = json.load(f)

        doc_id = j.get("sha1_hash") or j.get("id") or j.get("doc_id") or file_path.stem
        text = j.get("text", "")
        ground_truth = _as_code_set(j.get("ground_truth", []))

        if not text:
            print(f"[WARN] {file_path.name} has empty or missing text")
        if not ground_truth:
            print(f"[WARN] {file_path.name} has empty or missing ground_truth")

        texts.append(text)
        ids.append(doc_id)
        gt_by_id[doc_id] = ground_truth
        meta_by_id[doc_id] = {
            "input_file": str(file_path.resolve().relative_to(PROJ_ROOT)),
            "title": j.get("title"),
            "source": j.get("source") or j.get("url"),
            "sha1_hash": j.get("sha1_hash"),
        }

    return texts, ids, gt_by_id, meta_by_id


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------

def prf_from_counts(tp: int, fp: int, fn: int) -> Dict[str, float]:
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
    return {
        "precision": precision,
        "recall": recall,
        "f1": f1,
    }


def extract_predicted_ttps(result: Dict[str, Any]) -> Set[str]:
    return _as_code_set(result.get("ttps"))


def validate_results(results: Any, expected_ids: List[str]) -> List[str]:
    """Return functional-contract violations without discarding saved evidence."""
    if not isinstance(results, list):
        return [f"adapter returned {type(results).__name__}, expected a list"]

    failures: List[str] = []
    seen_ids: List[str] = []
    expected = set(expected_ids)
    for index, result in enumerate(results):
        if not isinstance(result, dict):
            failures.append(f"result {index} is {type(result).__name__}, expected an object")
            continue
        doc_id = result.get("id")
        if not doc_id:
            failures.append(f"result {index} has no id")
        else:
            seen_ids.append(str(doc_id))
        if result.get("error"):
            failures.append(f"{doc_id or f'result {index}'}: {result['error']}")
        if result.get("ttps") is None:
            failures.append(f"{doc_id or f'result {index}'}: ttps is null or missing")

    seen = set(seen_ids)
    for doc_id in sorted(expected - seen):
        failures.append(f"missing result for input id {doc_id}")
    for doc_id in sorted(seen - expected):
        failures.append(f"unexpected result id {doc_id}")
    duplicates = sorted({doc_id for doc_id in seen_ids if seen_ids.count(doc_id) > 1})
    for doc_id in duplicates:
        failures.append(f"duplicate result id {doc_id}")
    return failures


def evaluate_results(
    results: List[Dict[str, Any]],
    gt_by_id: Dict[str, Set[str]],
    meta_by_id: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    by_id = {r.get("id"): r for r in results if isinstance(r, dict) and r.get("id")}

    per_report: List[Dict[str, Any]] = []
    totals = {
        "tp": 0,
        "fp": 0,
        "fn": 0,
        "support": 0,
    }

    for doc_id, gt in gt_by_id.items():
        result = by_id.get(doc_id, {})
        pred = extract_predicted_ttps(result)

        tp_set = pred & gt
        fp_set = pred - gt
        fn_set = gt - pred

        row = {
            "id": doc_id,
            "tp": len(tp_set),
            "fp": len(fp_set),
            "fn": len(fn_set),
            "support": len(gt),
            "precision": prf_from_counts(len(tp_set), len(fp_set), len(fn_set))["precision"],
            "recall": prf_from_counts(len(tp_set), len(fp_set), len(fn_set))["recall"],
            "f1": prf_from_counts(len(tp_set), len(fp_set), len(fn_set))["f1"],
            "predicted": sorted(pred),
            "ground_truth": sorted(gt),
            "true_positives": sorted(tp_set),
            "false_positives": sorted(fp_set),
            "false_negatives": sorted(fn_set),
            "meta": meta_by_id.get(doc_id, {}),
        }
        per_report.append(row)

        totals["tp"] += len(tp_set)
        totals["fp"] += len(fp_set)
        totals["fn"] += len(fn_set)
        totals["support"] += len(gt)

    metrics = prf_from_counts(totals["tp"], totals["fp"], totals["fn"])
    totals.update(metrics)

    return {
        "micro": totals,
        "per_report": per_report,
    }


# ---------------------------------------------------------------------------
# Previous-run comparison
# ---------------------------------------------------------------------------

def find_previous_summary(adapter_dir: Path, current_run_id: str | None = None) -> Path | None:
    summaries = sorted(adapter_dir.glob("summary_*.json"))
    if current_run_id:
        summaries = [p for p in summaries if p.stem != f"summary_{current_run_id}"]
    if not summaries:
        return None
    return summaries[-1]


def compare_to_previous(current: Dict[str, Any], previous: Dict[str, Any]) -> Dict[str, Any]:
    cur_micro = current.get("evaluation", {}).get("micro", {})
    prev_micro = previous.get("evaluation", {}).get("micro", {})

    def delta(key: str):
        cur = cur_micro.get(key, 0)
        prev = prev_micro.get(key, 0)
        return cur - prev

    current_reports = {
        r["id"]: r for r in current.get("evaluation", {}).get("per_report", [])
    }
    previous_reports = {
        r["id"]: r for r in previous.get("evaluation", {}).get("per_report", [])
    }

    per_report_delta = []
    for doc_id in sorted(set(current_reports) | set(previous_reports)):
        cur = current_reports.get(doc_id, {})
        prev = previous_reports.get(doc_id, {})
        per_report_delta.append({
            "id": doc_id,
            "tp_delta": cur.get("tp", 0) - prev.get("tp", 0),
            "fp_delta": cur.get("fp", 0) - prev.get("fp", 0),
            "fn_delta": cur.get("fn", 0) - prev.get("fn", 0),
            "f1_delta": cur.get("f1", 0.0) - prev.get("f1", 0.0),
            "added_predictions": sorted(
                set(cur.get("predicted", [])) - set(prev.get("predicted", []))
            ),
            "removed_predictions": sorted(
                set(prev.get("predicted", [])) - set(cur.get("predicted", []))
            ),
        })

    return {
        "previous_run_id": previous.get("run_id"),
        "current_run_id": current.get("run_id"),
        "micro_delta": {
            "tp": delta("tp"),
            "fp": delta("fp"),
            "fn": delta("fn"),
            "support": delta("support"),
            "precision": delta("precision"),
            "recall": delta("recall"),
            "f1": delta("f1"),
        },
        "per_report_delta": per_report_delta,
    }


# ---------------------------------------------------------------------------
# Saving
# ---------------------------------------------------------------------------

def make_run_id() -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{ts}_{uuid.uuid4().hex[:8]}"


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, sort_keys=False)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_per_report_csv(path: Path, evaluation: Dict[str, Any]) -> None:
    rows = evaluation.get("per_report", [])
    path.parent.mkdir(parents=True, exist_ok=True)

    fields = [
        "id",
        "tp",
        "fp",
        "fn",
        "support",
        "precision",
        "recall",
        "f1",
        "predicted",
        "ground_truth",
        "true_positives",
        "false_positives",
        "false_negatives",
    ]

    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            out = {}
            for k in fields:
                v = r.get(k)
                if isinstance(v, list):
                    out[k] = " ".join(v)
                elif isinstance(v, float):
                    out[k] = f"{v:.6f}"
                else:
                    out[k] = v
            w.writerow(out)


# ---------------------------------------------------------------------------
# Running adapters
# ---------------------------------------------------------------------------

def run_adapter_tests(adapter_name: str, file_paths: List[Path], args, run_id: str):
    adapter_class = import_adapter(adapter_name)
    adapter_kwargs: Dict[str, Any] = {
        "verbose": args.verbose,
        "engine": args.engine,
    }
    if adapter_name == "Orbinato":
        # Orbinato owns its device/use_gpus translation internally.
        adapter_kwargs.update(
            device=args.device,
            use_gpu_for_training=args.device == "cuda",
        )
    else:
        adapter_kwargs.update(
            use_gpus=args.device == "cuda",
            require_gpus=args.device == "cuda",
        )
    if adapter_name == "Buchel" and args.buchel_quant_4bit:
        adapter_kwargs["quant_4bit_model"] = True
    if adapter_name == "TTP-LLM":
        adapter_kwargs["config_path"] = args.config
    adapter = adapter_class(**adapter_kwargs)
    """
    e.g.
    adapter = TTPDrillAdapter(verbose=True, engine="podman")
    results, raw_file_paths = adapter.predict(texts, ids=ids, save_dir=raw_save_dir)
     - texts: List of input texts to analyze
     - ids: List of unique IDs corresponding to each input text (used for matching results to ground truth)
     - save_dir: Directory where the adapter can save raw output files (e.g. container logs, intermediate results). The adapter can create this directory if it doesn't exist.
     - results: List of dictionaries, each containing at least an "id" key matching the input IDs, and a "ttps" key with the predicted TTP codes (as a list or space-separated string). The adapter can include additional keys as needed (e.g. confidence scores, metadata).
     - raw_file_paths: List of file paths to any raw output files saved by the adapter, which will be included in the final summary for reference. These files can be used for debugging or further analysis, but will not be directly evaluated against the ground truth.
    """

    texts, ids, gt_by_id, meta_by_id = load_poc_reports(file_paths)

    adapter_dir = args.output_root / adapter_name.lower()
    raw_save_dir = adapter_dir / "raw" / run_id

    predict_kwargs = {"bulk": True} if adapter_name == "RAF-AG" else {}
    results, raw_file_paths = adapter.predict(
        texts, ids=ids, save_dir=raw_save_dir, **predict_kwargs
    )
    result_failures = validate_results(results, ids)
    evaluated_results = results if isinstance(results, list) else []
    evaluation = evaluate_results(evaluated_results, gt_by_id, meta_by_id)
    inspected = subprocess.run(
        [args.engine, "image", "inspect", adapter.image, "--format", "{{.Id}}"],
        text=True, capture_output=True,
    )
    execution = {
        "image": adapter.image,
        "image_id": inspected.stdout.strip() if inspected.returncode == 0 else None,
        "adapter_class": f"{adapter.__class__.__module__}.{adapter.__class__.__name__}",
        "adapter_flags": list(getattr(adapter, "flags", [])),
    }

    return evaluated_results, raw_file_paths, evaluation, result_failures, execution


NATIVE_DIRECTORIES = {
    "AttacKG": "AttacKG",
    "Buchel": "Buchel",
    "Orbinato": "Orbinato",
    "RAF-AG": "RAF-AG",
    "rcATT": "rcATT",
    "TRAM": "TRAM",
    "TTP-LLM": "TTP-LLM",
    "TTPDrill": "TTPDrill",
    "LADDER": "LADDER",
    "SeqMask": "SeqMask",
}


def run_native_tests(adapter_name: str, file_paths: List[Path], args, run_id: str):
    texts, ids, gt_by_id, meta_by_id = load_poc_reports(file_paths)
    tool_root = args.native_root / NATIVE_DIRECTORIES[adapter_name]
    interpreter = tool_root / ".venv/bin/python"
    if not interpreter.is_file():
        raise FileNotFoundError(
            f"Native interpreter not found for {adapter_name}: {interpreter}. Run install.sh for the selected scope."
        )

    adapter_dir = args.output_root / adapter_name.lower()
    native_root = adapter_dir / "native" / run_id
    input_dir = native_root / "input"
    raw_dir = native_root / "raw"
    input_dir.mkdir(parents=True, exist_ok=True)
    raw_dir.mkdir(parents=True, exist_ok=True)
    manifest = []
    for record_id, text in zip(ids, texts):
        text_path = input_dir / f"{record_id}.txt"
        text_path.write_text(text, encoding="utf-8")
        manifest.append({"id": record_id, "text_file": str(text_path)})
    manifest_path = native_root / "manifest.json"
    write_json(manifest_path, manifest)
    worker_result = native_root / "worker_result.json"
    log_path = native_root / "native.log"
    command = [
        str(interpreter), "-u", str(CODE_POC_ROOT / "native_backends.py"),
        "--tool", adapter_name,
        "--manifest", str(manifest_path),
        "--output-dir", str(raw_dir),
        "--result", str(worker_result),
        "--project-root", str(PROJ_ROOT),
        "--native-root", str(tool_root),
        "--device", args.device,
        "--config", str(args.config),
    ]
    if args.buchel_quant_4bit:
        command.append("--buchel-quant-4bit")
    environment = dict(os.environ)
    environment.setdefault("HF_HOME", str(args.native_root / "cache/huggingface"))
    environment.setdefault("NLTK_DATA", str(args.native_root / "cache/nltk"))
    environment.setdefault("WANDB_MODE", "disabled")
    if args.device == "cpu":
        # Match a framework container launched without GPU passthrough.
        environment["CUDA_VISIBLE_DEVICES"] = "-1"
    if adapter_name == "Buchel":
        # The released Büchel modules read this during import even when the
        # selected smoke strategy does not use retrieval embeddings.
        environment.setdefault("OLLAMA_API_URL", "http://127.0.0.1:11434")
        environment.setdefault("OLLAMA_HOST", environment["OLLAMA_API_URL"])
    if adapter_name == "LADDER":
        resolved = interpreter.resolve()
        native_lib = str(resolved.parent.parent / "lib")
        environment["LD_LIBRARY_PATH"] = native_lib + (
            ":" + environment["LD_LIBRARY_PATH"] if environment.get("LD_LIBRARY_PATH") else ""
        )

    started = time.monotonic()
    with log_path.open("w", encoding="utf-8") as log:
        completed = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, env=environment)
    elapsed = time.monotonic() - started
    if worker_result.is_file():
        worker = json.loads(worker_result.read_text(encoding="utf-8"))
        results = worker.get("results", [])
    else:
        worker = {"failure": f"native worker exited {completed.returncode} without a result file"}
        results = [{"id": value, "ttps": [], "error": worker["failure"]} for value in ids]
    result_failures = validate_results(results, ids)
    if completed.returncode and not result_failures:
        result_failures.append(f"native worker exited {completed.returncode}; see {log_path}")
    evaluation = evaluate_results(results if isinstance(results, list) else [], gt_by_id, meta_by_id)
    return results, [log_path, worker_result, raw_dir], evaluation, result_failures, {
        "command": command,
        "elapsed_seconds": elapsed,
        "worker": worker,
    }


# ---------------------------------------------------------------------------
# Console output
# ---------------------------------------------------------------------------

def print_summary(adapter_name: str, run_payload: Dict[str, Any], comparison: Dict[str, Any] | None) -> None:
    m = run_payload["evaluation"]["micro"]

    print(f"Finished adapter {adapter_name}")
    print(
        "  Micro confusion: "
        f"TP={m['tp']} FP={m['fp']} FN={m['fn']} support={m['support']}"
    )
    print(
        "  Micro metrics: "
        f"P={m['precision']:.4f} R={m['recall']:.4f} F1={m['f1']:.4f}"
    )

    if comparison:
        d = comparison["micro_delta"]
        print(
            "  Change vs previous run "
            f"({comparison.get('previous_run_id')}): "
            f"ΔTP={d['tp']} ΔFP={d['fp']} ΔFN={d['fn']} "
            f"ΔP={d['precision']:+.4f} ΔR={d['recall']:+.4f} ΔF1={d['f1']:+.4f}"
        )
    else:
        print("  No previous summary found for comparison.")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run PoC tests for TTP-WorkBench adapters.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Fidelity policies (used only with --backend both):
  strict      PASS requires exact normalized native/framework ATT&CK prediction
              sets for every report.
  diagnostic  Differences and Jaccard agreement are reported, but prediction
              differences do not cause failure.
  hybrid      Uses strict comparison for AttacKG, LADDER, TTPDrill, Orbinato,
              RAF-AG, rcATT, SeqMask, and TRAM; diagnostic comparison for
              Buchel and TTP-LLM.

Missing, duplicate, malformed, unexpected, or error-bearing results fail under
every policy. Jaccard is evidence, not a pass threshold. With --backend both,
overall PASS means both paths passed functional validation and the applicable
fidelity gate passed. A diagnostic PASS does not imply exact agreement.""",
    )
    parser.add_argument(
        "--adapter",
        type=str,
        choices=list(adapter_module_map.keys()) + ["all"],
        default="TTPDrill",
        help="Specific adapter to test, or 'all' (default: TTPDrill)",
    )
    parser.add_argument("--verbose", action="store_true", help="Enable verbose adapter output")
    parser.add_argument("--engine", type=str, default="podman", help="Container engine to use")
    parser.add_argument(
        "--backend", choices=["framework", "native", "both"], default="framework",
        help="Execution path to test (default: framework)",
    )
    parser.add_argument(
        "--fidelity-policy", choices=["diagnostic", "hybrid", "strict"], default="hybrid",
        help="Policy for paired native/framework prediction differences; definitions below (default: hybrid)",
    )
    parser.add_argument(
        "--native-root", type=Path,
        default=PROJ_ROOT / "Tests/Reproductions/.runtime/external_tools",
        help="Root containing isolated native tool environments",
    )
    parser.add_argument(
        "--config", type=Path, default=PROJ_ROOT / "config.ini",
        help="TTP-LLM API configuration",
    )
    parser.add_argument(
        "--buchel-quant-4bit", action="store_true",
        help="Use Büchel 4-bit loading on both paths",
    )
    parser.add_argument(
        "--device",
        choices=["cpu", "cuda"],
        default="cpu",
        help="Container device for adapters that support GPU passthrough (default: cpu)",
    )
    parser.add_argument(
        "--inputs",
        type=str,
        default=str(POC_ROOT),
        help="Directory containing PoC JSON files, or a single JSON file",
    )
    parser.add_argument(
        "--no-compare",
        action="store_true",
        help="Do not compare this run to the most recent previous summary",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=RESULTS_ROOT,
        help="Directory for per-adapter smoke-test evidence (default: Tests/poc_tests/results)",
    )

    args = parser.parse_args()
    args.output_root = args.output_root.resolve()
    args.native_root = args.native_root.resolve()
    args.config = args.config.resolve()

    input_path = Path(args.inputs).resolve()
    if input_path.is_file():
        test_files = [input_path]
    else:
        test_files = sorted(input_path.glob("*.json"))

    if not test_files:
        raise SystemExit(f"No PoC JSON files found at {input_path}")

    print(f"Running PoC tests on {len(test_files)} JSON report(s).")

    adapter_names = list(adapter_module_map.keys()) if args.adapter == "all" else [args.adapter]

    functional_failures: Dict[str, List[str]] = {}
    for adapter_name in adapter_names:
        run_id = make_run_id()
        adapter_dir = args.output_root / adapter_name.lower()
        common_run_dir = adapter_dir / "output" / run_id

        print(f"\nTesting adapter: {adapter_name}")
        print(f"Run ID: {run_id}")

        previous_summary_path = None
        previous_summary = None
        if not args.no_compare:
            previous_summary_path = find_previous_summary(adapter_dir)
            if previous_summary_path:
                with open(previous_summary_path, "r", encoding="utf-8") as f:
                    previous_summary = json.load(f)

        backend_runs: Dict[str, Dict[str, Any]] = {}
        if args.backend in ("framework", "both"):
            results, raw_file_paths, evaluation, result_failures, framework_meta = run_adapter_tests(
                adapter_name, test_files, args, run_id,
            )
            backend_runs["framework"] = {
                "results": results,
                "raw_file_paths": [str(p) for p in raw_file_paths],
                "evaluation": evaluation,
                "functional_validation": {
                    "status": "FAIL" if result_failures else "PASS",
                    "errors": result_failures,
                },
                "execution": framework_meta,
            }
        if args.backend in ("native", "both"):
            try:
                native_results, native_raw, native_evaluation, native_failures, native_meta = run_native_tests(
                    adapter_name, test_files, args, run_id,
                )
            except Exception as exc:
                texts, ids, gt_by_id, meta_by_id = load_poc_reports(test_files)
                message = f"{type(exc).__name__}: {exc}"
                native_results = [{"id": value, "ttps": [], "error": message} for value in ids]
                native_raw, native_failures, native_meta = [], [message], {"failure": message}
                native_evaluation = evaluate_results(native_results, gt_by_id, meta_by_id)
            backend_runs["native"] = {
                "results": native_results,
                "raw_file_paths": [str(p) for p in native_raw],
                "evaluation": native_evaluation,
                "functional_validation": {
                    "status": "FAIL" if native_failures else "PASS",
                    "errors": native_failures,
                },
                "execution": native_meta,
            }

        primary = backend_runs.get("framework") or backend_runs["native"]
        results = primary["results"]
        raw_file_paths = primary["raw_file_paths"]
        evaluation = primary["evaluation"]
        result_failures = list(primary["functional_validation"]["errors"])
        fidelity = None
        if args.backend == "both":
            _, expected_ids, _, _ = load_poc_reports(test_files)
            fidelity = compare_fidelity_results(
                adapter_name,
                backend_runs["framework"]["results"],
                backend_runs["native"]["results"],
                expected_ids,
                policy=args.fidelity_policy,
            )
            write_fidelity_outputs(adapter_dir / "fidelity" / run_id, fidelity)
            write_json(adapter_dir / "latest_fidelity.json", fidelity)
            result_failures = (
                backend_runs["framework"]["functional_validation"]["errors"]
                + backend_runs["native"]["functional_validation"]["errors"]
            )
            if not fidelity["success"] and not result_failures:
                result_failures.append(
                    f"{adapter_name} native/framework prediction sets differ under {args.fidelity_policy} policy"
                )
        if result_failures:
            functional_failures[adapter_name] = result_failures

        run_payload = {
            "run_id": run_id,
            "adapter": adapter_name,
            "backend": args.backend,
            "engine": args.engine,
            "device": args.device,
            "native_root": str(args.native_root),
            "buchel_quant_4bit": bool(args.buchel_quant_4bit),
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "input_files": [str(p.relative_to(PROJ_ROOT)) for p in test_files],
            "input_sha256": {str(p.relative_to(PROJ_ROOT)): sha256_file(p) for p in test_files},
            "raw_file_paths": [str(p) for p in raw_file_paths],
            "results": results,
            "evaluation": evaluation,
            "backends": backend_runs,
            "fidelity": fidelity,
            "functional_validation": {
                "status": "FAIL" if result_failures else "PASS",
                "errors": result_failures,
            },
        }
        revision = subprocess.run(
            ["git", "-C", str(PROJ_ROOT), "rev-parse", "HEAD"],
            text=True, capture_output=True,
        )
        dirty = subprocess.run(
            ["git", "-C", str(PROJ_ROOT), "status", "--porcelain"],
            text=True, capture_output=True,
        )
        run_payload["repository"] = {
            "revision": revision.stdout.strip() if revision.returncode == 0 else None,
            "dirty": bool(dirty.stdout.strip()) if dirty.returncode == 0 else None,
        }

        # Save unique common outputs.
        write_json(common_run_dir / "results.json", results)
        for result in results:
            doc_id = result.get("id", "unknown")
            write_json(common_run_dir / f"result_{doc_id}.json", result)

        # Save summary and CSV.
        summary_path = adapter_dir / f"summary_{run_id}.json"
        write_json(summary_path, run_payload)
        write_json(adapter_dir / "latest_summary.json", run_payload)
        write_per_report_csv(adapter_dir / f"per_report_{run_id}.csv", evaluation)

        comparison = None
        if previous_summary:
            comparison = compare_to_previous(run_payload, previous_summary)
            write_json(adapter_dir / f"comparison_{run_id}.json", comparison)
            write_json(adapter_dir / "latest_comparison.json", comparison)

        print_summary(adapter_name, run_payload, comparison)
        if fidelity:
            print(
                f"  Fidelity: {fidelity['verdict']} "
                f"({fidelity['exact_reports']}/{fidelity['expected_reports']} exact; "
                f"mean Jaccard={fidelity['mean_jaccard']:.4f})"
            )
        try:
            display_path = summary_path.relative_to(PROJ_ROOT)
        except ValueError:
            # --output-root may intentionally point outside the checkout.
            display_path = summary_path
        print(f"  Saved summary: {display_path}")

        if result_failures:
            print(f"  Functional validation: FAIL ({len(result_failures)} issue(s))", file=sys.stderr)
            for failure in result_failures:
                print(f"    - {failure}", file=sys.stderr)
        else:
            print("  Functional validation: PASS")

    if functional_failures:
        failed_names = ", ".join(functional_failures)
        raise SystemExit(f"Smoke test failed functional validation: {failed_names}")


if __name__ == "__main__":
    main()

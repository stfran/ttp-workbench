#!/usr/bin/env python3
"""
Reproduce TTP-LLM Table 2 in prompt-only mode through two execution paths:
the original tool and the TTP-WorkBench adapter. Then evaluate and report both
outputs.

Usage:
    bash Tests/Reproductions/run_all.sh --experiment ttpllm_table2
"""

import argparse
import os
import subprocess
import sys
from pathlib import Path
import shlex
import time
import shutil
import json
from helpers.reporting import HERE, PROJECT, EXTERNAL, markdown, save_scores, check_predictions, tool_directory, output_section
sys.path.insert(0, str(PROJECT))

def run_ttpllm_original(args, dataset=None):
    # dataset is path

    cwd = args.root

    if dataset:
        # copy the dataset into place
        dataset_dst = Path(cwd) / "data" / "MITRE_Procedures.csv"
        subprocess.run(["cp", str(dataset), str(dataset_dst)], check=True)

    root = Path(args.root).resolve()
    if not (root / "main.py").exists():
        raise SystemExit(f"main.py not found under {root}")

    # Ensure a results directory exists under the repo root (common case)
    (root / "results").mkdir(parents=True, exist_ok=True)

    # 1) Run main.py
    cmd = [sys.executable, "main.py",
            "--type", "decoder_only",
            "--mode", "prompt_only",
            "--llm", "gpt-3.5-turbo"]   

    with output_section("RAW TOOL OUTPUT", "TTP-LLM / original / inference"):
        subprocess.run(cmd, cwd=cwd, check=True)

    # 2) Locate predictions CSV
    preds_csv = find_preds_path(root, "gpt-3.5-turbo", "prompt_only")
    print(f"Found predictions: {preds_csv}")
    # Retain inference output even if postprocessing or evaluation fails.
    target = tool_directory(args.out_dir, "ttpllm", "original")
    (target / "raw").mkdir(parents=True, exist_ok=True)
    (target / "parsed").mkdir(exist_ok=True)
    shutil.copy2(preds_csv, target / "raw/predictions.csv")

    # 3) Postprocess
    cmd = [sys.executable, "decoder_only/postprocess.py",
            "--file_path", str(preds_csv)]
    
    with output_section("RAW TOOL OUTPUT", "TTP-LLM / original / postprocessing"):
        subprocess.run(cmd, cwd=cwd, check=True)

    # 4) Evaluate on the _encoded file
    encoded_csv = encoded_path_from(preds_csv)
    if not encoded_csv.exists():
        # some postprocess scripts might output into repo root / results;
        # attempt a fallback search by name if not found exactly where expected
        fallback = list(encoded_csv.parent.glob(encoded_csv.name))
        if fallback:
            encoded_csv = fallback[0]
    if not encoded_csv.exists():
        raise FileNotFoundError(f"Expected encoded file not found: {encoded_csv}")

    print(f"Found encoded file: {encoded_csv}")
    # Preserve encoded output before checking completeness; never slice labels to fit it.
    shutil.copy2(encoded_csv, target / "parsed/encoded.csv")
    import pandas as pd
    expected = len(pd.read_csv(dataset if dataset is not None else root / "data/MITRE_Procedures.csv"))
    label_count = len(pd.read_csv(root / "data/MITRE_Procedures_encoded.csv"))
    raw_count = len(pd.read_csv(preds_csv))
    encoded_count = len(pd.read_csv(encoded_csv))
    if not (expected == label_count == raw_count == encoded_count):
        raise ValueError(
            f"TTP-LLM original output count mismatch: selected procedures={expected}, "
            f"reference labels={label_count}, raw predictions={raw_count}, encoded predictions={encoded_count}. "
            f"Outputs retained at {target}; evaluation was not run.")
    cmd = [sys.executable, "decoder_only/evaluation.py",
            "--encoded_file_path", str(encoded_csv)]
    with output_section("RAW TOOL OUTPUT", "TTP-LLM / original / upstream evaluation"):
        subprocess.run(cmd, cwd=cwd, check=True)

def run_ttpllm_adapter(args, dataset=None):
    from Framework.adapters.ttp_llm_adapter import predict_texts
    from helpers.reporting import isolate_framework_cache
    isolate_framework_cache()
    import pandas as pd

    mitre_tactics = [
        'collection',
        'command and control',
        'credential access',
        'defense evasion',
        'discovery',
        'execution',
        'exfiltration',
        'impact',
        'initial access',
        'lateral movement',
        'persistence',
        'privilege escalation',
        'reconnaissance',
        'resource development',
    ]

    # Map TA codes -> lowercase names above (exactly as columns are named)
    ta_to_name = {
        'TA0043': 'reconnaissance',
        'TA0042': 'resource development',
        'TA0001': 'initial access',
        'TA0002': 'execution',
        'TA0003': 'persistence',
        'TA0004': 'privilege escalation',
        'TA0005': 'defense evasion',
        'TA0006': 'credential access',
        'TA0007': 'discovery',
        'TA0008': 'lateral movement',
        'TA0009': 'collection',
        'TA0010': 'exfiltration',
        'TA0011': 'command and control',
        'TA0040': 'impact',
    }

    if dataset:
        dataset_path = dataset
    else:
        dataset_path = args.root / "data" / "MITRE_Procedures.csv"

    df = pd.read_csv(dataset_path)

    target = tool_directory(args.out_dir, "ttpllm", "framework")
    with output_section("FRAMEWORK ADAPTER OUTPUT", "TTP-LLM / framework (including container diagnostics)"):
        results, _ = predict_texts(
            texts=df['Procedures'].tolist(), ids=df.index.tolist(),
            llm="gpt-3.5-turbo", type="decoder_only", mode="prompt_only",config_path=args.config,
            engine=args.engine, save_dir=target / "raw", tmp_root=args.out_dir.parent / "tmp",
            bulk=True, bulk_batch_size=getattr(args, "bulk_batch_size", 100),
            resume=getattr(args, "resume", True),
            verbose=getattr(args, "verbose", False),
        )
    (target / "parsed").mkdir(parents=True, exist_ok=True)
    (target / "parsed/adapter_outputs.json").write_text(json.dumps(results, indent=2))
    check_predictions(results, df.index.tolist())

    # save_results to csv and then run the rest of the eval pipeline
    # results are TA#### codes, need to translate them back to names so that postprocess works
    # [{"ttps": [...], "id": ...}, ...]
    encoded_rows = []
    for res in results:
        # res is expected to have `"ttps" : [ 'TA0005', 'T1059', ... ]`
        present = set()
        for code in res.get('ttps', []):
            code = str(code).upper()
            if code.startswith('TA') and code in ta_to_name:
                present.add(ta_to_name[code])
        row = {t: int(t in present) for t in mitre_tactics}
        encoded_rows.append(row)

    enc_df = pd.DataFrame(encoded_rows, columns=mitre_tactics)

    # 4) Save framework-encoded predictions where evaluation.py expects them
    results_dir = target / "parsed"
    results_dir.mkdir(parents=True, exist_ok=True)
    enc_csv = results_dir / 'encoded.csv'
    enc_df.to_csv(enc_csv, index=False)
    print(f"Framework-encoded predictions saved to: {enc_csv}")

    # 5) Evaluate
    # The common reporting step below uses the upstream classification_report metric.


def find_preds_path(root: Path, llm: str, mode: str) -> Path:
    """Prefer results under repo root, fallback to absolute /results."""
    fname = f"preds_{llm}_{mode}.csv"
    p1 = root / "results" / fname
    if p1.exists():
        return p1
    p2 = Path("/results") / fname
    if p2.exists():
        return p2
    # last resort: try to locate by glob within root/results
    candidates = list((root / "results").glob(f"preds_*_{mode}.csv"))
    if candidates:
        # choose most recent
        return max(candidates, key=lambda p: p.stat().st_mtime)
    raise FileNotFoundError(f"Could not find results CSV for mode={mode}, llm={llm} "
                            f"at {p1} or {p2}")

def encoded_path_from(raw_csv: Path) -> Path:
    return raw_csv.with_name(raw_csv.stem + "_encoded" + raw_csv.suffix)

def main():
    from helpers.reporting import install_exit_handler
    install_exit_handler()
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, default=EXTERNAL / "TTP-LLM",
                    help="Path to TTPLLM repository root containing main.py")
    ap.add_argument("--original", action="store_true",
                    help="Run original TTPLLM code")
    ap.add_argument("--adapter", action="store_true",
                    help="Run TTPLLM with adapter")
    ap.add_argument("--config", type=Path, default=PROJECT / "config.ini")
    ap.add_argument("--data", type=Path, default=HERE / "data/MITRE_Procedures.csv")
    ap.add_argument("--labels", type=Path, default=HERE / "data/MITRE_Procedures_encoded.csv")
    ap.add_argument("--out-dir", type=Path, default=HERE / "experiments/ttpllm_table2/runs/manual/results")
    ap.add_argument("--profile", choices=["smoke", "full"], default="full")
    ap.add_argument("--engine", choices=["podman", "docker"], default="podman")
    ap.add_argument("--bulk-batch-size", type=int, default=100, help="Framework input records per native invocation")
    ap.add_argument(
        "--resume",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Reuse only complete, valid framework batches already present in --out-dir (default: enabled)",
    )
    ap.add_argument(
        "--api-max-retries",
        type=int,
        default=3,
        help="Maximum retries for one OpenAI request after its initial failure (default: 3)",
    )
    ap.add_argument(
        "--api-max-total-retries",
        type=int,
        default=25,
        help="Maximum OpenAI retries in one native invocation before failing its batch (default: 25)",
    )
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()
    if args.api_max_retries < 0 or args.api_max_total_retries < 0:
        ap.error("API retry limits must be non-negative")
    os.environ["TTPWB_OPENAI_MAX_RETRIES"] = str(args.api_max_retries)
    os.environ["TTPWB_OPENAI_MAX_TOTAL_RETRIES"] = str(args.api_max_total_retries)
    import pandas as pd
    from sklearn.metrics import classification_report
    args.root, args.out_dir = args.root.resolve(), args.out_dir.resolve()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    (args.out_dir.parent / "tmp").mkdir(exist_ok=True)
    data, labels = pd.read_csv(args.data), pd.read_csv(args.labels)
    if len(data) != len(labels): raise ValueError("Procedure/label counts differ")
    if args.profile == "smoke": data, labels = data.iloc[:1], labels.iloc[:1]
    selected = args.out_dir.parent / "tmp/procedures.csv"
    data.to_csv(selected, index=False)
    if args.original:
        shutil.copy2(args.config, args.root / "config.ini")
        (args.root / "config.ini").chmod(0o600)
        labels.to_csv(args.root / "data/MITRE_Procedures_encoded.csv", index=False)

    if args.original:
        print("Running TTPLLM original...")
        run_ttpllm_original(args, selected)
        time.sleep(2)  # brief pause between runs

    if args.adapter:
        print("Running TTPLLM with adapter...")
        run_ttpllm_adapter(args, selected)
        time.sleep(2)  # brief pause between runs
    print("=== EXPERIMENT RUNNER OUTPUT | TTP-LLM Table 2: evaluation ===", flush=True)
    paper_values = [.44,.52,.59,.66,.86,.48,.20,.44,.42,.45,.40,.41,.21,0.0]
    paper = {str(name) + " F1": value for name, value in zip(labels.columns, paper_values)}
    paper["samples avg F1"] = .60
    for backend in ("framework", "original"):
        path = tool_directory(args.out_dir, "ttpllm", backend) / "parsed/encoded.csv"
        if not path.exists(): continue
        predictions = pd.read_csv(path)
        if predictions.shape != labels.shape: raise RuntimeError("Incomplete predictions: " + backend)
        report = classification_report(labels.values, predictions.values, target_names=list(labels.columns), output_dict=True, zero_division=0)
        (path.parent / "classification_report.json").write_text(json.dumps(report, indent=2))
        scores = {name + " F1": row["f1-score"] for name, row in report.items() if isinstance(row, dict) and (name in labels.columns or name == "samples avg")}
        scores.update({str(name) + " support": int(labels[name].sum()) for name in labels.columns})
        save_scores(args.out_dir, backend, "ttpllm", scores)
    markdown(args.out_dir, "TTP-LLM Table 2: prompt only", paper,
             "Profile: " + args.profile + ". Paper references cover the full dataset. Evaluated procedures: " + str(len(data)) + ". Model: gpt-3.5-turbo.")

if __name__ == "__main__":
    main()

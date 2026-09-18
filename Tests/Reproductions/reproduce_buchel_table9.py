#!/usr/bin/env python3
"""
Usage examples
--------------
# Trial run: 1 sample per experiment, SFT only, *no* auto-train; forces document-level
python repro_table9.py --only-sft --include-sft --trial --limit 1 --verbose --root-out trial_run

# Evaluate-only from previously saved consolidated JSONs
python repro_table9.py --only-sft --include-sft --evaluate-only --root-out experiments_adapter --verbose
"""

from __future__ import annotations
import argparse, json, os, sys
from pathlib import Path
from itertools import chain
from typing import Dict, Any, List, Tuple
import pandas as pd

# --- Repo imports (match original project layout) ---
from helpers.reporting import HERE, PROJECT, EXTERNAL, save_scores, markdown, check_predictions, tool_directory, output_section
sys.path.insert(0, str(PROJECT))

# -------------------------
# Config
# -------------------------
BUCHEL_DIR = EXTERNAL / "Buchel/generation"
BOSCH_TEST_JSON = HERE / "data/buchel/bosch_cti_test_ds.json"
TRAM_TEST_JSON = HERE / "data/buchel/test_split.json"

def load_repository(root):
    # imports from the BUCHEL_DIR path, after parsing the configurable --root.
    global experiments, llm_response, TRAM_CLASSES, TABLE9_BOSCH, TABLE9_TRAM
    sys.path.insert(0, str(root))
    import experiments  # experiment dicts
    import llm_response  # metric calc helpers
    from finetuning.cti_datasets.tram.tram_classes import TRAM_CLASSES

    # Which experiments comprise Table 9
    TABLE9_BOSCH = [
        ("Bosch Raw", experiments.experimentBosch1),
        ("Bosch FSP", experiments.experimentBosch2),
        ("Bosch RAG", experiments.experimentBosch3),
        ("Bosch FSP + RAG", experiments.experimentBosch4),
    ]
    TABLE9_TRAM = [
        ("Tram Raw", experiments.experimentT1),
        ("Tram FSP", experiments.experimentT2),
        ("Tram RAG", experiments.experimentT3),
        ("Tram RAG + FSP", experiments.experimentT4),
    ]

SFT_NAME_TRAM  = "mitre_sentence_tram"
SFT_NAME_BOSCH = "bosch_sentence"

# -------------------------
# Helpers
# -------------------------

def _clean_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)


def _exp_key(name: str, engine_label: str) -> str:
    # Canonical file/dir name per experiment
    safe = name.lower().replace(" ", "_").replace("+", "plus")
    return f"{safe}__{engine_label}"


def get_dataset(path: Path):
    df = pd.read_json(path)
    return zip(df["cti_report"], df["mitre_ids"])  # (report:str, labels:List[str])


def get_dataset_sentence(path: Path):
    df = pd.read_json(path)
    return zip(df["cti_report"], df["label"])  # (sent_list:List[str], label_list:List[List[str]])


def _load_dataset_for_exp(exp: Dict[str, Any], limit: int | None) -> Tuple[List[str], List[str], List[List[str]]]:
    """Return (ids, texts, labels_per_sample). For TRAM, labels are flattened later.
    - Bosch: cti_report is a string; labels are technique codes.
    - TRAM: cti_report is a list[str] sentences; labels is list[list[str]].
    """
    ids: List[str] = []
    texts: List[str] = []
    labels: List[List[str]] = []

    if exp["DatasetBosch"]:
        ds_iter = list(get_dataset(BOSCH_TEST_JSON))
        if limit:
            ds_iter = ds_iter[:limit]
        for i, (report, mitre_ids) in enumerate(ds_iter):
            ids.append(f"bosch_{i:05d}")
            texts.append(str(report))
            labels.append(list(mitre_ids))
    else:
        ds_iter = list(get_dataset_sentence(TRAM_TEST_JSON))
        if limit:
            ds_iter = ds_iter[:limit]
        for i, (sent_list, label_list) in enumerate(ds_iter):
            ids.append(f"tram_{i:05d}")
            # Join sentences; CLI will also split deterministically again (unless forced document-level)
            joined = "*^*^*^".join(map(str, sent_list))
            texts.append(joined)
            labels.append(label_list)  # list[list[str]] per sentence
    return ids, texts, labels


def _write_txt_corpus(out_dir: Path, ids: List[str], texts: List[str]) -> None:
    _clean_dir(out_dir)
    for i, t in zip(ids, texts):
        (out_dir / f"{i}.txt").write_text(t, encoding="utf-8")


def _collect_predictions_from_raw(raw_dir: Path) -> Dict[str, List[str]]:
    """Read CLI JSON files and return {id: [Txxxx,...]}"""
    preds: Dict[str, List[str]] = {}
    for jp in sorted(raw_dir.glob("*.json")):
        try:
            data = json.loads(jp.read_text(encoding="utf-8"))
            if isinstance(data, list) and data:
                item = data[0]
                # Table 9 reports direct-ID extraction; the CLI also offers name-union predictions.
                codes = llm_response.extract_mitre_ids(pd.read_csv(BUCHEL_DIR / "database/rag_db_qwen.csv"), item["raw_response"])
                # BaseAdapter saves the clean ID as filename; the CLI payload retains a staging prefix.
                preds[jp.stem] = sorted(set(codes))
        except Exception:
            continue
    return preds


def _flatten_tram_labels(label_list: List[List[str]]) -> List[str]:
    flat = list(chain.from_iterable(label_list))
    return sorted({t for t in flat if t})


def _filter_to_labelset(codes: List[str], allowed: List[str]) -> List[str]:
    if not allowed:
        return codes
    allowed_set = set(allowed)
    return [c for c in codes if c in allowed_set]


def _compute_metrics(per_sample_preds: List[List[str]], per_sample_labels: List[List[str]]) -> Tuple[float, float, float]:
    f1s: List[float] = []
    ps: List[float] = []
    rs: List[float] = []
    for pred, lab in zip(per_sample_preds, per_sample_labels):
        f1, p, r, _acc = llm_response.calculate_metrics(pred, lab)
        f1s.append(f1); ps.append(p); rs.append(r)
    import numpy as np
    return float(np.mean(f1s)), float(np.mean(ps)), float(np.mean(rs))


def native_id_scores(log: Path):
    # The original test() return is name-based. Its CSV retains unrounded ID metrics too.
    import csv
    import io
    rows = csv.reader(io.StringIO(log.read_text()))
    for header in rows:
        if "f1_score_id" in header and "precision_id" in header:
            break
    else:
        raise ValueError("No native prediction table in " + str(log))
    positions = [header.index(name) for name in ("f1_score_id", "precision_id", "recall_id")]
    values = []
    for row in rows:
        if not row: break  # native footer follows the blank line
        if len(row) != len(header): raise ValueError("Incomplete native prediction row")
        values.append([float(row[i]) for i in positions])
    if not values: raise ValueError("No native predictions in " + str(log))
    return tuple(sum(row[i] for row in values) / len(values) for i in range(3))

def append_existing_results(out_root: Path, summary: List[Dict[str, Any]], *, verbose: bool = False) -> None:
    """Scan the selected backend's trial parsed/ directories for consolidated results and append
    their metrics to the provided summary list.

    Each consolidated file is expected to follow the format written by this
    script during normal runs (i.e., it contains an "items" list with
    per-sample "predictions" and "label" fields).
    """
    json_paths = sorted(out_root.glob("*/parsed/*.json"))
    if verbose:
        print(f"[Runner] Scanning for existing consolidated results under {out_root} ... found {len(json_paths)} file(s)")

    for jp in json_paths:
        if "_bk" in jp.name:
            if verbose:
                print(f"[Runner] Skipping backup file: {jp}")
            continue
        try:
            data = json.loads(jp.read_text(encoding="utf-8"))
        except Exception as e:
            if verbose:
                print(f"[Runner] Skipping unreadable file: {jp} ({e})")
            continue

        if not isinstance(data, dict): continue  # full adapter output is a list, not consolidated gold/predictions
        items = data.get("items") or []
        if not items:
            if verbose:
                print(f"[Runner] No items in {jp}, skipping")
            continue

        preds_aligned: List[List[str]] = []
        labels_aligned: List[List[str]] = []
        for it in items:
            p = it.get("predictions") or []
            # Back-compat: if stored as list[dict], extract codes
            if p and isinstance(p[0], dict):
                p = [e.get("code") for e in p if e.get("code")]
            y = it.get("label") or []
            preds_aligned.append(sorted(set(p)))
            labels_aligned.append(sorted(set(y)))

        f1, prec, rec = _compute_metrics(preds_aligned, labels_aligned)
        exp_name = data.get("experiment") or jp.parent.parent.name

        summary.append({
            "Experiment": exp_name,
            "F1-Score": f1,
            "Precision": prec,
            "Recall": rec,
        })
        if verbose:
            print(f"[Runner] Appended existing: {exp_name} -> F1={f1:.3f}, P={prec:.3f}, R={rec:.3f}")

# -------------------------
# Core runner
# -------------------------

def run_one_experiment(
    exp_name: str,
    exp: Dict[str, Any],
    *,
    engine_label: str,
    out_root: Path,
    engine: str,
    use_gpus: bool,
    verbose: bool,
    quant_4bit: bool,
    sft: bool,
    sft_name: str | None,
    limit: int | None,
    force_doc_level: bool,
    evaluate_only: bool,
    backend: str = "framework",
    model_root: Path = EXTERNAL / "Buchel/models/local",
) -> Tuple[str, float, float, float]:
    """Execute a single experiment (or evaluate-only) and return metrics."""
    print("[Runner] Running experiment:", exp_name, f"(engine: {engine_label}, SFT: {'yes' if sft else 'no'})")
    exp_key = _exp_key(exp_name, engine_label)
    work_dir = out_root / exp_key
    raw_dir = work_dir / "raw"
    pred_dir = work_dir / "parsed"
    in_dir = work_dir / "inputs"
    for d in (raw_dir, pred_dir, in_dir):
        _clean_dir(d)

    preds_aligned: List[List[str]] = []
    labels_aligned: List[List[str]] = []

    if evaluate_only and backend == "original":
        return (exp_name, *native_id_scores(raw_dir / "upstream_predictions.csv"))
    if exp.get("RAG") and not evaluate_only:
        import subprocess
        subprocess.run([sys.executable, str(HERE / "setup/setup_ollama.py"), "--engine", engine], check=True)

    if evaluate_only:
        # Load consolidated predictions produced by a prior run
        consolidated_path = pred_dir / f"{exp_key}.json"
        if not consolidated_path.exists():
            # Fallback to common layout under the provided root_out (user often keeps these paths)
            alt = out_root / exp_key / "predictions" / f"{exp_key}.json"
            consolidated_path = alt if alt.exists() else consolidated_path
        if not consolidated_path.exists():
            # Recover a completed inference whose earlier reporting step failed.
            ids, _, labels = _load_dataset_for_exp(exp, limit)
            by_id = _collect_predictions_from_raw(raw_dir)
            if set(by_id) != set(ids): raise FileNotFoundError("Complete raw or consolidated predictions are required")
            allowed = list(exp.get("NumberOfLabel", [])) if exp["DatasetBosch"] else list(TRAM_CLASSES)
            preds = [_filter_to_labelset(by_id[key], allowed) for key in ids]
            gold = [_filter_to_labelset(list(label) if exp["DatasetBosch"] else _flatten_tram_labels(label), allowed) for label in labels]
            return (exp_name, *_compute_metrics(preds, gold))
        data = json.loads(consolidated_path.read_text(encoding="utf-8"))
        items = data.get("items") or []
        for it in items:
            p = it.get("predictions") or []
            # consolidated stores list[str] of codes
            if p and isinstance(p[0], dict):
                p = [e.get("code") for e in p if e.get("code")]
            y = it.get("label") or []
            preds_aligned.append(sorted(set(p)))
            labels_aligned.append(sorted(set(y)))
    elif backend == "original":
        # Execute the upstream evaluator directly, retaining its prompts, comments and metrics.
        import shutil
        os.environ.setdefault("OLLAMA_API_URL", "http://127.0.0.1:11434")
        os.chdir(BUCHEL_DIR)
        import supervised_finetuning
        import finetuning_test
        import finetuning.test_helper as th
        th.get_dataset = lambda path: get_dataset(BOSCH_TEST_JSON)
        th.get_dataset_sentence = lambda path: get_dataset_sentence(TRAM_TEST_JSON)
        model_path = str(model_root / sft_name / "merged") if sft else "unsloth/Meta-Llama-3.1-8B-Instruct"
        if sft and not Path(model_path, "model.safetensors.index.json").is_file():
            raise FileNotFoundError("Stage the requested SFT model first: " + model_path)
        with output_section("RAW TOOL OUTPUT", "Buchel / original / " + exp_key):
            model, tokenizer = supervised_finetuning.load_model(model_path, quant_4bit_model=quant_4bit)
            f1, prec, rec = finetuning_test.test(exp, model, tokenizer, model_description=exp_key, test_len=limit or 0)
        log = BUCHEL_DIR / "finetuning/experiments" / (exp_key + "_" + exp["Name"] + ".csv")
        shutil.copy2(log, raw_dir / "upstream_predictions.csv")
        (raw_dir / "upstream_returned_name_scores.json").write_text(json.dumps({"F1": float(f1), "precision": float(prec), "recall": float(rec)}))
        # Preserve the upstream prediction rows in parsed JSON as well as its raw CSV.
        import csv
        with log.open(newline="", encoding="utf-8") as stream:
            rows = list(csv.reader(stream))
        header_index = next(i for i, row in enumerate(rows) if "f1_score_id" in row)
        header, parsed_rows = rows[header_index], []
        for row in rows[header_index + 1:]:
            if not row: break
            if len(row) != len(header): raise ValueError("Incomplete native prediction row")
            parsed_rows.append(dict(zip(header, row)))
        (pred_dir / "upstream_predictions.json").write_text(json.dumps(parsed_rows, indent=2))
        f1, prec, rec = native_id_scores(log)
        return exp_name, f1, prec, rec
    else:
        # Load dataset + write .txt corpus
        ids, texts, labels = _load_dataset_for_exp(exp, limit)
        _write_txt_corpus(in_dir, ids, texts)

        # Configure adapter (force document-level during trial to ensure a *single* model call per item)
        doc_level = True if force_doc_level else bool(exp.get("DocumentLevel"))
        from Framework.adapters.buchel_adapter import BuchelAdapter
        from helpers.reporting import isolate_framework_cache
        isolate_framework_cache()
        class SuppliedModelAdapter(BuchelAdapter):
            @classmethod
            def _ensure_ollama_running(cls, **kwargs):
                # The service image's entrypoint ignores the adapter GPU probe command.
                # Raw/FSP do not require embeddings; RAG is prepared explicitly below.
                return
        bp = SuppliedModelAdapter(
            verbose=verbose,
            document_level=doc_level,
            rag=bool(exp.get("RAG")),
            fsp=bool(exp.get("FSP")),
            quant_4bit_model=quant_4bit,
            sft=sft,
            sft_name=sft_name if sft else None,
            auto_train=False,
            prefer_merged=True,
            engine=engine,
            gpus=use_gpus,
            host_output_dir=work_dir,
            binds=[(str(BUCHEL_DIR), "/workspace"),
                   (str(model_root), "/workspace/finetuning/output"),
                   (str(work_dir), "/workspace/experiments"),
                   (str(EXTERNAL / "cache/huggingface"), "/tmp/huggingface")],
            tmp_root=work_dir,
        )
        # Prompt style flag only
        bp.flags += ["--dataset", ("bosch" if exp.get("DatasetBosch") else "tram")]

        if verbose:
            print(f"[Runner] Adapter configured: ")
            for key, value in bp.__dict__.items():
                if not key.startswith('__') and not callable(value):
                    print(f"{key}: {value}")

        # Bulk predict via adapter
        with output_section("FRAMEWORK ADAPTER OUTPUT", "Buchel / framework / " + exp_key):
            results, _ = bp.predict(texts, ids=ids, bulk=True, save_dir=raw_dir)
        (pred_dir / "adapter_outputs.json").write_text(json.dumps(results, indent=2))
        check_predictions(results, ids)

        # Collect predictions from raw JSONs
        by_id = _collect_predictions_from_raw(raw_dir)
        if set(by_id) != set(ids): raise RuntimeError("Missing Buchel raw predictions")

        # Align predictions & labels in original order and apply dataset-specific filtering
        if exp.get("DatasetBosch"):
            allowed = list(exp.get("NumberOfLabel", []))  # Table 9 uses restricted label set
            for _id, lab in zip(ids, labels):
                pred = _filter_to_labelset(by_id.get(_id, []), allowed)
                lab_filtered = _filter_to_labelset(list(lab), allowed) if allowed else list(lab)
                preds_aligned.append(sorted(set(pred)))
                labels_aligned.append(sorted(set(lab_filtered)))
        else:
            # TRAM: flatten labels per sample and restrict to TRAM_CLASSES
            allowed = set(TRAM_CLASSES)
            for _id, lab_list in zip(ids, labels):
                pred = [c for c in by_id.get(_id, []) if c in allowed]
                lab_flat = [c for c in _flatten_tram_labels(lab_list) if c in allowed]
                preds_aligned.append(sorted(set(pred)))
                labels_aligned.append(sorted(set(lab_flat)))

        # Save compact predictions file for this experiment
        compact = {
            "experiment": exp_name,
            "engine": engine_label,
            "sft_name": sft_name if sft else None,
            "strategy": {
                "dataset_style": "bosch" if exp.get("DatasetBosch") else "tram",
                "document_level": doc_level,
                "rag": bool(exp.get("RAG")),
                "fsp": bool(exp.get("FSP")),
                "engine": ("sft" if sft else "base"),
            },
            "items": [
                {"id": _id, "predictions": p, "label": y}
                for _id, p, y in zip(ids, preds_aligned, labels_aligned)
            ],
        }
        (pred_dir / f"{exp_key}.json").write_text(json.dumps(compact, ensure_ascii=False, indent=2), encoding="utf-8")

    # Compute Buchel metrics (mean over samples)
    f1, prec, rec = _compute_metrics(preds_aligned, labels_aligned)
    return exp_name, f1, prec, rec


def main():
    from helpers.reporting import install_exit_handler
    install_exit_handler()
    global BUCHEL_DIR
    ap = argparse.ArgumentParser(description="Run Buchel Table 9 via adapter (bulk per experiment)")
    ap.add_argument("--engine", choices=["docker", "podman"], default=os.environ.get("CONTAINER_ENGINE", "podman"))
    ap.add_argument("--gpus", dest="gpus", action="store_true", help="Enable GPU passthrough")
    ap.add_argument("--no-gpus", dest="gpus", action="store_false", help="Disable GPU passthrough")
    ap.set_defaults(gpus=True)
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--quant-4bit", action="store_true", help="Load model in 4-bit where supported")
    ap.add_argument("--root-out", type=Path, default=HERE / "experiments/buchel_table9/runs/manual/results")
    ap.add_argument("--root", type=Path, default=BUCHEL_DIR)
    ap.add_argument("--backend", choices=["framework", "original"], default="framework")
    ap.add_argument("--model-root", type=Path, default=EXTERNAL / "Buchel/models")
    ap.add_argument("--dataset", choices=["bosch", "tram", "both"], default="both")
    ap.add_argument("--strategy", choices=["raw", "fsp", "rag", "rag_fsp"])
    ap.add_argument("--report-only", action="store_true")

    # Trial/eval knobs
    ap.add_argument("--trial", action="store_true", help="Run limited samples per experiment; never auto-train SFT")
    ap.add_argument("--limit", type=int, default=None, help="Optional cap on #samples per experiment")
    ap.add_argument("--evaluate-only", action="store_true", help="Skip prediction; read consolidated results and compute metrics")
    ap.add_argument("--doc-level-in-trial", dest="doc_level_in_trial", action="store_true", help="Force document-level processing during trial")
    ap.add_argument("--no-doc-level-in-trial", dest="doc_level_in_trial", action="store_false")
    ap.set_defaults(doc_level_in_trial=False)

    # SFT handling
    ap.add_argument("--only-sft", action="store_true", help="Only run SFT experiments (skip base runs)")
    ap.add_argument("--include-sft", action="store_true", help="Also run SFT experiments if models are present")
    ap.add_argument("--append-existing", action="store_true", help="Append metrics from any consolidated prediction JSONs under --root-out")

    args = ap.parse_args()
    if not args.gpus:
        # The original backend runs in the host-native environment, so
        # container GPU flags alone cannot enforce a CPU request.
        os.environ["CUDA_VISIBLE_DEVICES"] = "-1"
    BUCHEL_DIR = args.root.resolve()
    out_root: Path = args.root_out.resolve()
    out_root.mkdir(parents=True, exist_ok=True)

    summary: List[Dict[str, Any]] = []
    paper = paper_table9()
    if args.report_only:
        markdown(out_root, "Buchel Table 9", paper, "Profile: " + ("smoke" if args.trial else "full") +
                 ". Paper references are full-data results. The supplied AnnoCTR checkpoint and published Zenodo TRAM checkpoint are used for SFT rows.")
        return
    load_repository(BUCHEL_DIR)

    # Determine per-run sample limit
    limit = (1 if args.trial and (args.limit is None) else args.limit)

    # Decide which families to run
    run_base = not args.only_sft
    run_sft  = args.include_sft or args.only_sft

    def do_run(name: str, exp: Dict[str, Any], *, engine_label: str, sft: bool, sft_name: str | None):
        dataset = "bosch" if exp["DatasetBosch"] else "tram"
        strategy = "rag_fsp" if exp["RAG"] and exp["FSP"] else "rag" if exp["RAG"] else "fsp" if exp["FSP"] else "raw"
        if args.dataset not in ("both", dataset) or args.strategy and args.strategy != strategy: return
        model_source = "zenodo" if dataset == "tram" and sft else "local"
        exp_name, f1, p, r = run_one_experiment(
            name, exp,
            engine_label=engine_label,
            out_root=tool_directory(out_root, "Buchel", args.backend),
            engine=args.engine,
            use_gpus=args.gpus,
            verbose=args.verbose,
            quant_4bit=args.quant_4bit,
            sft=sft,
            sft_name=sft_name,
            limit=limit,
            force_doc_level=(args.trial and args.doc_level_in_trial),
            evaluate_only=args.evaluate_only,
            backend=args.backend,
            model_root=args.model_root.resolve() / model_source,
        )
        summary.append({"Experiment": exp_name, "F1-Score": f1, "Precision": p, "Recall": r})
        key = name + " / " + engine_label
        save_scores(out_root, args.backend, _exp_key(name, engine_label),
                    {key + " F1": f1, key + " precision": p, key + " recall": r}, tool="Buchel")

    # Base (no SFT)
    if run_base:
        for name, exp in (TABLE9_BOSCH + TABLE9_TRAM):
            do_run(name, exp, engine_label="base", sft=False, sft_name=None)
    else:
        print("[Runner] Skipping base experiments due to --only-sft")

    # SFT (run in both normal and trial modes; never auto-train in trial)
    if run_sft:
        for name, exp in TABLE9_TRAM:
            do_run(name, exp, engine_label="sft_tram", sft=True, sft_name=SFT_NAME_TRAM)
        for name, exp in TABLE9_BOSCH:
            do_run(name, exp, engine_label="sft_bosch", sft=True, sft_name=SFT_NAME_BOSCH)

    if args.append_existing:
        append_existing_results(tool_directory(out_root, "Buchel", args.backend), summary, verbose=args.verbose)


    # Write CSV summary (Table 9 format)
    df = pd.DataFrame(summary)
    out_csv = out_root / (args.backend + "_table9_methods_" + args.dataset + "_" +
              (args.strategy or "all") + "_" + ("sft" if args.only_sft else "base_and_optional_sft") + ".csv")
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_csv, index=False)
    print(f"[Runner] Wrote summary: {out_csv}")
    markdown(out_root, "Buchel Table 9", paper,
             "Scope: " + ("smoke (one native dataset item)" if limit else "full dataset") +
             ". Paper columns are full-data references. The TRAM SFT rows use the published Zenodo checkpoint. "
             "Sentence-level prompting and original per-item aggregation are retained.")

def paper_table9():
    values = {"Bosch": [[30.3,24.4,50.8],[26.4,34.1,24.5],[35.9,32.7,53.1],[34.6,33.3,39.3],
                        [55.3,50.8,60.7],[42.8,53.3,39.8],[47.6,48.7,54.9],[47.3,49.2,54.0]],
              "Tram": [[49.2,39.6,65.1],[46.2,47.5,44.9],[54.1,45.5,66.8],[53.2,48.5,65.5],
                       [72.5,66.3,80.0],[57.9,51.3,69.5],[65.5,57.6,76.0],[64.6,56.0,76.4]]}
    paper = {}
    for dataset, rows in values.items():
        strategies = ["Raw", "FSP", "RAG", "FSP + RAG" if dataset == "Bosch" else "RAG + FSP"]
        for index, row in enumerate(rows):
            engines = ["base"] if index < 4 else (["sft_bosch"] if dataset == "Bosch" else ["sft_tram"])
            for engine in engines:
                for metric, value in zip(["F1", "precision", "recall"], row):
                    paper[dataset + " " + strategies[index % 4] + " / " + engine + " " + metric] = value / 100
    return paper


if __name__ == "__main__":
    main()

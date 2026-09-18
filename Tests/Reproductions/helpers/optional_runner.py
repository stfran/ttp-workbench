"""Shared execution plumbing, not a replacement for experiment-specific scoring."""
import argparse
import csv
from datetime import datetime
import importlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from helpers.optional_experiments import OPTIONAL
from helpers.optional_scoring import (ORBINATO_MODELS, seqmask_records, seqmask_scores,
                                      rcatt_scores, rcatt_codes, orbinato_scores,
                                      rafag_scores, save_csv)
from helpers.reporting import HERE, EXTERNAL, PROJECT, output_section, install_exit_handler


def raw_error(value):
    # RAF-AG's matcher emits a list of sentence matches, not a JSON object.
    return value.get("error", "") if isinstance(value, dict) else ""


def rcatt_empty_prediction(raw_dir, record_id, note):
    """Persist rcATT's unrepresentable empty STIX result as explicit evidence."""
    names = [record_id + ".json", "rcatt_raw_" + record_id + ".json"]
    paths = [raw_dir / name for name in names if (raw_dir / name).exists()]
    target = paths[0] if paths else raw_dir / names[0]
    target.write_text(json.dumps({"object_refs": [], "_ttp_workbench_note": note}))
    return dict(id=record_id, ttps=[], sentences=[])


def load_records(slug, data, tool):
    if slug.startswith("seqmask"):
        return seqmask_records(data / "seqmask", OPTIONAL[slug]["table"], tool)
    if slug == "rcatt_table6":
        import pandas as pd
        from helpers.source_evaluation.rcatt_table6_repro import build_ground_truth_lists_and_splits
        df = pd.read_csv(data / "rcatt/unfetter_wiki_preprocessed.csv")
        gt, _, _ = build_ground_truth_lists_and_splits(df)
        return [dict(id="row_{:06d}".format(i), text=str(df.iloc[i]["Text"]), labels=sorted(labels)) for i, labels in enumerate(gt)]
    if slug == "orbinato_fig3":
        records = json.loads((data / "orbinato/reports.json").read_text())
        return [dict(r, text=(data / "orbinato" / r["text_file"]).read_text()) for r in records]
    from helpers.source_evaluation import evaluate_raf_ag as raf_evaluation
    root = data / "rafag"
    paper, labels = raf_evaluation._load_paper_and_gt(root / "raf_ag_paper_counts.csv", root / "ground_truth_labels.json")
    records = []
    used = set()
    for i, row in paper.iterrows():
        text = raf_evaluation._best_txt_for_report(root / "texts", row["report"])
        if text is None or text in used or text.stem.lower() not in labels:
            raise RuntimeError("Missing/ambiguous retained RAF-AG input: " + row["report"])
        used.add(text)
        records.append(dict(id="row_{:06d}".format(i), report=row["report"], source_file=text.name, text=text.read_text(), labels=labels[text.stem.lower()]))
    return records


def framework(args, records, raw_dir):
    from helpers.reporting import isolate_framework_cache
    isolate_framework_cache()
    modules = {"rcATT": "rcatt_adapter", "SeqMask": "seqmask_adapter", "Orbinato": "orbinato_adapter", "RAF-AG": "raf_ag_adapter", "AttacKG": "attackg_adapter"}
    if args.tool == "Orbinato":
        # Must happen BEFORE constructing the adapter: its constructor trains if missing.
        image = "ttp-workbench:orbinato"
        artifacts = (
            "ml_models/MLP classifier .sav",
            "ml_models/Logreg.sav",
            "ml_models/Multinomial_NB.sav",
            "ml_models/SVM_Classifier_OVR.sav",
            "cnn_model/saved_model.sav",
            "lstm_model/saved_model.sav",
            "pretrained-lstm_model/saved_model.sav",
            "trained_secbert.pt",
            "secbert_labels.txt",
        )
        paths = ["/opt/Orbinato/src/" + name for name in artifacts]
        check = ("import os,sys; missing=[p for p in sys.argv[1:] "
                 "if not os.path.isfile(p) or os.path.getsize(p) == 0]; "
                 "print('Missing Orbinato artifacts: ' + ', '.join(missing) if missing else 'Orbinato artifacts verified'); "
                 "raise SystemExit(bool(missing))")
        subprocess.run([args.engine, "run", "--rm", image, "/opt/venv/bin/python", "-c", check] + paths, check=True)
    module = importlib.import_module("Framework.adapters." + modules[args.tool])
    predictions = []
    batch_size = args.batch_size if args.tool in ("rcATT", "SeqMask", "RAF-AG", "AttacKG") else len(records)
    for start in range(0, len(records), batch_size):
        batch = records[start:start + batch_size]
        kw = dict(ids=[r["id"] for r in batch], save_dir=raw_dir, engine=args.engine, verbose=args.verbose,
                  tmp_root=args.out_dir.parent / "tmp", require_gpus=args.device == "cuda")
        if args.tool == "SeqMask":
            kw.update(mode="reproduction", tech_model="ar_mask", tact_model="ar_mask", bulk=True, use_gpus=args.device == "cuda")
        elif args.tool in ("rcATT", "AttacKG"):
            kw["bulk"] = True
        elif args.tool == "Orbinato":
            kw.update(models=list(ORBINATO_MODELS), device=args.device)
        if args.tool == "RAF-AG":
            adapter = module.RAFAGAdapter(engine=args.engine, verbose=args.verbose, use_gpus=args.device == "cuda",
                                        require_gpus=kw["require_gpus"], tmp_root=kw["tmp_root"], soft_fail=False)
            batch_predictions, _ = adapter.predict([r["text"] for r in batch], ids=kw["ids"], save_dir=raw_dir, prefix="rafag", bulk=True)
        else:
            batch_predictions, _ = module.predict_texts([r["text"] for r in batch], **kw)
        expected = [r["id"] for r in batch]
        by_id = {p.get("id"): p for p in batch_predictions}
        if len(by_id) != len(batch_predictions) or set(by_id) != set(expected):
            raise RuntimeError("Adapter returned duplicate/missing/unexpected input IDs")
        # rcATT can lose one output after a successful-looking long batch.
        # A singleton container invocation is reliable, so replace only error
        # results and preserve every successful batch prediction.
        if args.tool == "rcATT":
            for record in batch:
                prediction = by_id[record["id"]]
                if not prediction.get("error") and prediction.get("ttps") is not None:
                    continue
                print("[rcATT] retrying adapter result {} as a singleton".format(record["id"]), flush=True)
                retry_kw = dict(kw)
                retry_kw["ids"] = [record["id"]]
                retried, _ = module.predict_texts([record["text"]], **retry_kw)
                if len(retried) != 1 or retried[0].get("id") != record["id"]:
                    raise RuntimeError("rcATT singleton retry returned an unexpected input ID")
                retried_prediction = retried[0]
                retry_error = retried_prediction.get("error", "")
                if "No raw output" in retry_error or "Upstream did not save a prediction" in retry_error:
                    print("[rcATT] {} produced no STIX report; recording an empty prediction".format(record["id"]), flush=True)
                    retried_prediction = rcatt_empty_prediction(
                        raw_dir, record["id"],
                        "Upstream produced no STIX report after singleton retry; interpreted as an empty prediction.")
                by_id[record["id"]] = retried_prediction
        predictions.extend(by_id[key] for key in expected)
    return predictions


def read_raw(records, raw_dir, tool):
    prefixes = {"rcATT": "rcatt", "SeqMask": "seqmask", "RAF-AG": "rafag", "Orbinato": "orbinato", "AttacKG": "attackg"}
    result = []
    for record in records:
        names = [record["id"] + ".json", prefixes[tool] + "_raw_" + record["id"] + ".json"]
        paths = [raw_dir / n for n in names if (raw_dir / n).exists()]
        if len(paths) > 1:
            raise RuntimeError("Duplicate raw outputs for " + record["id"])
        result.append(json.loads(paths[0].read_text()) if paths else {"error": "No raw output for " + record["id"]})
    return result


def score(slug, tool, records, raw, parsed=None):
    if slug.startswith("seqmask"):
        return seqmask_scores(records, raw, OPTIONAL[slug]["table"], tool)
    if slug == "rcatt_table6":
        return rcatt_scores(records, raw)
    if slug == "orbinato_fig3":
        return orbinato_scores(records, raw)
    if parsed is None:
        predictions = [sorted(r.get("full_path", [])) if tool == "RAF-AG" else sorted(k for k in r if k.startswith("T")) for r in raw]
    else:
        predictions = [p.get("ttps") or [] for p in parsed]
    metrics, policy = rafag_scores(records, predictions)
    return metrics, predictions, policy


def primary_metric(slug):
    if slug.startswith("seqmask"):
        return "micro_f1"
    if slug == "rcatt_table6":
        return "techniques/micro_f0.5"
    if slug == "orbinato_fig3":
        return "model=SecBERT/tau=0.2/document_f1"
    if slug == "rafag_table6":
        return "document_f1"
    return "strict/document_f1"


def paper_metric(slug, tool, metric):
    if slug == "rcatt_table6" and tool == "rcATT":
        from helpers.source_evaluation.rcatt_table6_repro import (
            original_results_tactics,
            original_results_techniques,
        )
        scope, name = metric.split("/", 1)
        published = {
            "tactics": original_results_tactics,
            "techniques": original_results_techniques,
        }
        return published.get(scope, {}).get(name, "")
    configured = OPTIONAL[slug].get("paper_metrics", {}).get(tool, {})
    if metric in configured:
        return configured[metric]
    return OPTIONAL[slug]["paper"][tool] if metric == primary_metric(slug) else ""


def rafag_primary_rows(result):
    """Read current exact-match rows and legacy rows formerly named strict."""
    rows = result.get("policy", {}).get("per_document", [])
    return [row for row in rows if row.get("variant", "exact") in ("exact", "strict")]


def seqmask_comparison_metrics(result):
    """Normalize current and legacy SeqMask score keys to the published triplet."""
    saved = result.get("metrics", {})
    aliases = {
        "micro_precision": ("micro_precision", "p_micro"),
        "micro_recall": ("micro_recall", "r_micro"),
        "micro_f1": ("micro_f1", "f_micro"),
    }
    return {metric: next((saved[name] for name in names if name in saved), "")
            for metric, names in aliases.items()}


def rafag_document_metrics(result):
    rows = rafag_primary_rows(result)
    if rows:
        return {"document_" + metric: sum(float(row[metric]) for row in rows) / len(rows)
                for metric in ("precision", "recall", "f1")}
    return {metric: result.get("metrics", {}).get(metric, "")
            for metric in ("document_precision", "document_recall", "document_f1")}


def write_rafag_per_document(args, loaded):
    """Place paper, original, and framework measurements on each paper row."""
    paper_path = args.data_dir / "rafag/raf_ag_paper_counts.csv"
    with paper_path.open(newline="") as stream:
        paper_rows = list(csv.DictReader(stream))
    metric_columns = (("tp", "TP"), ("fp", "FP"), ("fn", "FN"),
                      ("precision", "Precision"), ("recall", "Recall"), ("f1", "F1"))
    for tool in OPTIONAL["rafag_table6"]["tools"]:
        backend_rows = {
            backend: {row["id"]: row for row in rafag_primary_rows(loaded[tool][backend])}
            for backend in ("original", "framework")
        }
        combined = []
        for index, paper in enumerate(paper_rows):
            record_id = "row_{:06d}".format(index)
            if not any(record_id in rows for rows in backend_rows.values()):
                continue
            row = {"id": record_id, "report": paper["report"]}
            for metric, column in metric_columns:
                row["paper_" + metric] = paper[tool + "_" + column]
            for backend in ("original", "framework"):
                measured = backend_rows[backend].get(record_id, {})
                for metric, _ in metric_columns:
                    row[backend + "_" + metric] = measured.get(metric, "")
            combined.append(row)
        if combined:
            save_csv(args.out_dir / (tool + "_per_document.csv"), combined)
            for backend in ("original", "framework"):
                (args.out_dir / (tool + "_" + backend + "_per_document.csv")).unlink(missing_ok=True)


def write_rafag_ground_truth_audit(args):
    """Compare retained label counts with the ground-truth counts implied by Table 6."""
    paper_path = args.data_dir / "rafag/raf_ag_paper_counts.csv"
    with paper_path.open(newline="") as stream:
        paper_rows = list(csv.DictReader(stream))
    records = load_records("rafag_table6", args.data_dir, "RAF-AG")
    if len(records) != len(paper_rows):
        raise RuntimeError("RAF-AG paper rows and retained ground-truth records do not align")
    audit = []
    for paper, record in zip(paper_rows, records):
        if paper["report"] != record["report"]:
            raise RuntimeError("RAF-AG paper/ground-truth row mismatch: {} != {}".format(
                paper["report"], record["report"]))
        ground_truth_count = len(record["labels"])
        rafag_count = int(paper["RAF-AG_TP"]) + int(paper["RAF-AG_FN"])
        attackg_count = int(paper["AttacKG_TP"]) + int(paper["AttacKG_FN"])
        rafag_difference = rafag_count - ground_truth_count
        attackg_difference = attackg_count - ground_truth_count
        differences = []
        if rafag_difference:
            differences.append("RAF-AG paper count {:+d}".format(rafag_difference))
        if attackg_difference:
            differences.append("AttacKG paper count {:+d}".format(attackg_difference))
        audit.append(dict(
            id=record["id"], report=record["report"], source_file=record.get("source_file", ""),
            repository_ground_truth_count=ground_truth_count,
            paper_rafag_tp=paper["RAF-AG_TP"], paper_rafag_fn=paper["RAF-AG_FN"],
            paper_rafag_ground_truth_count=rafag_count, rafag_count_difference=rafag_difference,
            paper_attackg_tp=paper["AttacKG_TP"], paper_attackg_fn=paper["AttacKG_FN"],
            paper_attackg_ground_truth_count=attackg_count, attackg_count_difference=attackg_difference,
            counts_match=not differences, difference_summary="; ".join(differences),
        ))
    save_csv(args.out_dir / "ground_truth_audit.csv", audit)


def consolidate(args, slug):
    rows = []
    loaded = {}
    status_path = args.out_dir.parent / "status.tsv"
    statuses = dict(line.split("\t", 1) for line in status_path.read_text().splitlines() if "\t" in line) if status_path.exists() else {}
    for tool in OPTIONAL[slug]["tools"]:
        saved = {}
        for backend in ("original", "framework"):
            path = args.out_dir / tool / backend / "scores.json"
            saved[backend] = json.loads(path.read_text()) if path.exists() else {}
            if not saved[backend] and (path.parent / "failure.json").exists():
                saved[backend] = {"status": "failed"}
            # Timeouts/signals can stop Python before it writes failure.json.
            recorded = statuses.get("{}_{}_{}".format(slug, tool, backend), "")
            if recorded.startswith("FAIL"):
                saved[backend]["status"] = "failed"
            if slug.startswith("seqmask"):
                saved[backend]["metrics"] = seqmask_comparison_metrics(saved[backend])
            if slug == "rafag_table6":
                saved[backend]["metrics"] = rafag_document_metrics(saved[backend])
        loaded[tool] = saved
        keys = set()
        for value in saved.values():
            keys.update(value.get("metrics", {}))
        main_metric = primary_metric(slug)
        keys.add(main_metric)
        for metric in sorted(keys):
            row = dict(tool=tool, metric=metric, paper=paper_metric(slug, tool, metric))
            if "submission_repro" in OPTIONAL[slug]:
                row["submission_repro"] = OPTIONAL[slug]["submission_repro"].get(tool, {}).get(metric, "")
            row.update(original=saved["original"].get("metrics", {}).get(metric, ""), framework=saved["framework"].get("metrics", {}).get(metric, ""),
                       original_status=saved["original"].get("status", "unavailable"), framework_status=saved["framework"].get("status", "unavailable"), profile=args.profile,
                       paper_scope="full source experiment")
            rows.append(row)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    save_csv(args.out_dir / "comparison.csv", rows)
    if slug == "rafag_table6":
        write_rafag_per_document(args, loaded)
        write_rafag_ground_truth_audit(args)
    # This is evidence, not a generated verdict or narrative acceptance threshold.
    for tool, results in loaded.items():
        a = args.out_dir / tool / "original/parsed/evaluation_predictions.json"
        b = args.out_dir / tool / "framework/parsed/evaluation_predictions.json"
        if a.exists() and b.exists():
            left, right = json.loads(a.read_text()), json.loads(b.read_text())
            context = {}
            if (slug == "orbinato_fig3" and tool == "Orbinato" and
                    all(results[backend].get("status") == "passed"
                        for backend in ("original", "framework"))):
                records = load_records(slug, args.data_dir, tool)
                if args.profile == "smoke":
                    records = records[:min(3, len(records))]
                rescored = {}
                for backend in ("original", "framework"):
                    raw_dir = args.out_dir / tool / backend / "raw"
                    raw = read_raw(records, raw_dir, tool)
                    _, predictions, _ = orbinato_scores(records, raw)
                    rescored[backend] = [
                        dict(id=record["id"], predictions=prediction,
                             error=raw_error(payload))
                        for record, prediction, payload in zip(records, predictions, raw)
                    ]
                left, right = rescored["original"], rescored["framework"]
                context = {"model": "SecBERT", "threshold": "0.2"}
            right = {p["id"]: p for p in right}
            diffs = []
            for p in left:
                q = right.get(p["id"], {})
                lp, rp = set(p.get("predictions") or []), set(q.get("predictions") or [])
                diffs.append(dict(id=p["id"], **context,
                                  original_only=";".join(sorted(lp-rp)),
                                  framework_only=";".join(sorted(rp-lp)),
                                  original_error=p.get("error", ""),
                                  framework_error=q.get("error", "")))
            save_csv(args.out_dir / (tool + "_prediction_differences.csv"), diffs)
    if slug == "orbinato_fig3":
        original = args.out_dir / "Orbinato_original_per_document.csv"
        framework_path = args.out_dir / "Orbinato_framework_per_document.csv"
        if original.exists() and framework_path.exists():
            from helpers.orbinato_fig3_plot import build_orbinato_plots
            records = load_records(slug, args.data_dir, "Orbinato")
            if args.profile == "smoke":
                records = records[:min(3, len(records))]
            primary_figure = build_orbinato_plots(
                args.out_dir, records, args.data_dir / "orbinato/paper_figures")
            print("Saved", primary_figure)
    print("Saved", args.out_dir / "comparison.csv")


def main(slug=None):
    ap = argparse.ArgumentParser(description="Optional reproduction: original/framework inference and CSV/JSON comparisons; no Markdown report.")
    ap.add_argument("--table", choices=["7", "14", "15"])
    ap.add_argument("--tool")
    ap.add_argument("--backend", choices=["original", "framework"], default="framework")
    ap.add_argument("--profile", choices=["full", "smoke"], default="full")
    ap.add_argument("--out-dir", type=Path)
    ap.add_argument("--data-dir", type=Path, default=HERE / "data/optional")
    ap.add_argument("--tool-root", type=Path)
    ap.add_argument("--engine", choices=["podman", "docker"], default=os.environ.get("CONTAINER_ENGINE", "podman"))
    ap.add_argument("--batch-size", type=int)
    ap.add_argument("--device", choices=["cpu", "cuda"], default="cpu")
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--consolidate-only", action="store_true")
    ap.add_argument("--resume-failed", action="store_true", help="Reuse a complete SeqMask result or archive a failed backend before retrying it")
    args = ap.parse_args()
    if slug is None:
        if not args.table:
            ap.error("--table is required")
        slug = "seqmask_table" + args.table
    args.tool = args.tool or OPTIONAL[slug]["tools"][0]
    if args.tool not in OPTIONAL[slug]["tools"]:
        ap.error("Tool not in this experiment")
    if args.batch_size is None:
        args.batch_size = 20 if slug == "rafag_table6" else 100
    if args.batch_size < 1:
        ap.error("--batch-size must be positive")
    args.out_dir = (args.out_dir or HERE / "experiments" / slug / "runs" / ("manual_" + datetime.now().strftime("%Y%m%d_%H%M%S_%f")) / "results").resolve()
    args.data_dir = args.data_dir.resolve()
    if args.consolidate_only:
        consolidate(args, slug)
        return
    install_exit_handler()
    os.environ.setdefault("HF_HOME", str(EXTERNAL / "cache/huggingface"))
    os.environ.setdefault("NLTK_DATA", str(EXTERNAL / "cache/nltk"))
    records = load_records(slug, args.data_dir, args.tool)
    if args.profile == "smoke":
        records = records[:min(3, len(records))]
    root = (args.tool_root or EXTERNAL / args.tool).resolve()
    directory = args.out_dir / args.tool / args.backend
    raw_dir, parsed_dir = directory / "raw", directory / "parsed"
    score_path = directory / "scores.json"
    if args.resume_failed and args.tool == "SeqMask" and score_path.exists():
        saved = json.loads(score_path.read_text())
        if saved.get("count") == len(records) and saved.get("metrics") and saved.get("errors", 0):
            # The released SeqMask runner catches per-document model shape
            # exceptions and scores those rows as empty predictions.  Mirror
            # that policy without repeating hours of completed inference.
            saved["status"] = "passed"
            saved.setdefault("policy", {})["record_error_policy"] = "Upstream per-document exceptions are retained and scored as empty predictions."
            score_path.write_text(json.dumps(saved, indent=2) + "\n")
            (directory / "failure.json").unlink(missing_ok=True)
            print("Reused {} completed SeqMask records; {} upstream record errors remain explicit empty predictions".format(len(records), saved["errors"]))
            return
    if directory.exists() and (any(directory.iterdir()) if directory.is_dir() else True):
        if not args.resume_failed:
            raise RuntimeError("Refusing to mix this execution with existing tool outputs; choose a new run directory")
        archive_root = args.out_dir.parent / "failed_attempts"
        archive_root.mkdir(parents=True, exist_ok=True)
        archive = archive_root / "{}_{}_{}".format(args.tool, args.backend, datetime.now().strftime("%Y%m%dT%H%M%S%f"))
        shutil.move(str(directory), str(archive))
        print("Archived prior failed backend output at {}".format(archive), flush=True)
    raw_dir.mkdir(parents=True, exist_ok=True)
    parsed_dir.mkdir(exist_ok=True)
    parsed = None
    try:
        if args.backend == "framework":
            parsed = framework(args, records, raw_dir)
        else:
            # SeqMask and RAF-AG let TensorFlow discover accelerators rather
            # than accepting a device argument. Make the launcher's CPU
            # selection authoritative for those native paths as well.
            if args.device == "cpu" and args.tool in ("SeqMask", "RAF-AG"):
                os.environ["CUDA_VISIBLE_DEVICES"] = "-1"
            from helpers import optional_native
            names = {"Orbinato": "orbinato", "rcATT": "rcatt", "RAF-AG": "rafag", "SeqMask": "seqmask", "AttacKG": "attackg"}
            kw = {}
            if args.tool in ("rcATT", "RAF-AG", "AttacKG"):
                kw["batch_size"] = args.batch_size
            if args.tool == "Orbinato":
                kw["device"] = args.device
            with output_section("RAW TOOL OUTPUT", args.tool + " / original / inference"):
                getattr(optional_native, names[args.tool])(root, records, raw_dir, **kw)
        raw = read_raw(records, raw_dir, args.tool)
        metrics, predictions, policy = score(slug, args.tool, records, raw, parsed)
        if parsed is None:
            parsed = [dict(id=r["id"], ttps=pred, **({"error": raw_error(value)} if raw_error(value) else {})) for r, pred, value in zip(records, predictions, raw)]
        errors = sum(bool(raw_error(r) or p.get("error")) for r, p in zip(raw, parsed))
        fatal_errors = 0 if args.tool == "SeqMask" else errors
        (parsed_dir / "predictions.json").write_text(json.dumps(parsed, indent=2) + "\n")
        evaluation = [dict(id=r["id"], labels=r["labels"], predictions=p,
                           source_file=r.get("source_file", r.get("text_file", "")),
                           error=raw_error(value) or adapter.get("error", ""))
                      for r, p, value, adapter in zip(records, predictions, raw, parsed)]
        (parsed_dir / "evaluation_predictions.json").write_text(json.dumps(evaluation, indent=2) + "\n")
        if policy.get("per_document") and slug != "rafag_table6":
            save_csv(args.out_dir / (args.tool + "_" + args.backend + "_per_document.csv"), policy["per_document"])
        value = dict(experiment=slug, tool=args.tool, backend=args.backend, profile=args.profile,
                     requested_device=args.device, container_engine=args.engine if args.backend == "framework" else None,
                     count=len(records), errors=errors, status="failed" if fatal_errors else "passed",
                     metrics=metrics, policy=policy, tool_root=str(root))
        (directory / "scores.json").write_text(json.dumps(value, indent=2) + "\n")
        print("Scored {} records; {} errors".format(len(records), errors))
        if errors and args.tool == "SeqMask":
            print("SeqMask upstream record errors are retained as empty predictions, matching the released evaluation runner")
        if fatal_errors:
            raise SystemExit(1)
    except Exception as exc:
        (directory / "failure.json").write_text(json.dumps(dict(
            error="{}: {}".format(type(exc).__name__, exc), expected_records=len(records),
            requested_device=args.device,
            container_engine=args.engine if args.backend == "framework" else None,
        ), indent=2) + "\n")
        raise

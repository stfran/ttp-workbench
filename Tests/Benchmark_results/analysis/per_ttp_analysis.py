"""

Outputs:
1) consolidated_examples.csv
   One row per (dataset, tool, document), with raw/effective sets and TP/FP/FN.
2) per_ttp_tool_dataset.csv
   One row per (dataset, tool, TTP), with support/TP/FP/FN/precision/recall/F1.
3) per_ttp_dataset_overall.csv
   Aggregated across tools for each (dataset, TTP), plus a combined dataset view.
4) false_negative_cases.csv
   One row per missed GT TTP occurrence.
5) false_positive_cases.csv
   One row per spurious predicted TTP occurrence.
6) ttp_confusions_from_fns.csv
   For each missed GT TTP, counts the co-occurring FP TTPs in the same example.
"""
from __future__ import annotations

import argparse
import csv
import glob
import json
import os
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parents[2]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from ttp_llm_evaluation_helper import infer_tactics_near_codes
from Framework.utils.attack_lookup import modernize_ttps as _modernize_ttps

DEFAULT_TOOLS = [
    "AttacKG",
    "Buchel",
    "Orbinato",
    "RAF-AG",
    "rcATT",
    "TRAM",
    "TTP-LLM",
    "TTPDrill",
    "LADDER",
    "SeqMask",
]


# -----------------------------------------------------------------------------
# Helpers mirrored from evaluate.py
# -----------------------------------------------------------------------------
def modernize_list(codes: Sequence[str]) -> List[str]:
    try:
        return _modernize_ttps(list(codes))
    except Exception:
        out: List[str] = []
        for c in codes:
            try:
                out.extend(_modernize_ttps([c]))
            except Exception:
                out.append(c)
        seen: Set[str] = set()
        uniq: List[str] = []
        for c in out:
            if c and c not in seen:
                seen.add(c)
                uniq.append(c)
        return uniq


def canonical_set(codes: Sequence[str]) -> Set[str]:
    return {c for c in modernize_list(list(codes)) if c}


def is_ttp_llm(tool_name: str) -> bool:
    s = (tool_name or "").lower().replace("_", "-")
    return "ttp-llm" in s or "ttpllm" in s


def is_technique_only_capacity(cap_raw: Set[str]) -> bool:
    has_tech = any(c.startswith("T") for c in cap_raw)
    has_sub = any(c.startswith("T") and "." in c for c in cap_raw)
    return has_tech and not has_sub


def collapse_parents(codes: Set[str]) -> Set[str]:
    out: Set[str] = set()
    for c in codes:
        if not c:
            continue
        if c.startswith("T") and "." in c:
            out.add(c.split(".", 1)[0])
        else:
            out.add(c)
    return out


def parse_subtool_name(tool_display: str, json_path: str, shorten: bool = False) -> str:
    tool_lower = tool_display.lower()
    stem = os.path.splitext(os.path.basename(json_path))[0].lower()

    if stem.startswith("our_results_"):
        stem = stem[len("our_results_"):]

    prefix = f"{tool_lower[:4]}_" if shorten else f"{tool_lower}_"
    if not stem.startswith(prefix):
        return f"{tool_display}-{stem}"

    variant = stem[len(prefix):]
    variant_map = {
        "mlp": "MLP",
        "pretrained_lstm": "LSTM",
        "secbert": "SecBERT",
    }
    nice = variant_map.get(variant)
    if nice is None:
        for key, value in variant_map.items():
            if key in variant:
                nice = value
                break
        else:
            nice = variant.upper()
    return f"{tool_display}-{nice}"


# -----------------------------------------------------------------------------
# Data model
# -----------------------------------------------------------------------------
@dataclass
class Example:
    dataset: str  # curated | orkl
    tool: str
    doc_id: str
    pred_raw: Set[str]
    gt_raw: Set[str]
    gt_text: str = ""
    prov: str = ""
    source_prediction_file: str = ""
    source_gt_file: str = ""


@dataclass
class EvalBreakdown:
    dataset: str
    tool: str
    doc_id: str
    pred_raw: Set[str]
    gt_raw: Set[str]
    pred_eff: Set[str]
    gt_eff: Set[str]
    tp: Set[str]
    fp: Set[str]
    fn: Set[str]
    cap_raw: Set[str]
    cap_mod: Set[str]
    latent_tactics_added: Set[str]
    prov: str
    source_prediction_file: str
    source_gt_file: str


# -----------------------------------------------------------------------------
# File loading
# -----------------------------------------------------------------------------
def safe_json_load(path: str):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


class GroundTruthStore:
    def __init__(self, curated_root: str, orkl_root: str):
        self.curated_root = curated_root
        self.orkl_root = orkl_root
        self.cache: Dict[Tuple[str, str], Tuple[Set[str], str, str, str]] = {}

    def _candidate_paths(self, dataset: str, doc_id: str) -> List[Tuple[str, str, List[str]]]:
        """
        Return GT candidate locations in preference order.

        The prediction folder is not always authoritative for where the GT file
        physically lives, so prefer the nominal dataset root first and then fall
        back to the other GT root for the same doc_id.
        """
        curated = (
            os.path.join(self.curated_root, f"{doc_id}.json"),
            "curated",
            ["original_labels", "ground_truth"],
        )
        orkl = (
            os.path.join(self.orkl_root, f"{doc_id}.json"),
            "orkl",
            ["ground_truth", "original_labels"],
        )

        if dataset == "curated":
            return [curated, orkl]
        if dataset == "orkl":
            return [orkl, curated]
        raise ValueError(f"Unknown dataset={dataset}")

    def read(self, dataset: str, doc_id: str) -> Tuple[Set[str], str, str, str]:
        key = (dataset, doc_id)
        if key in self.cache:
            return self.cache[key]

        tried_paths: List[str] = []
        for path, found_in_dataset, preferred_label_keys in self._candidate_paths(dataset, doc_id):
            tried_paths.append(path)
            if not os.path.exists(path):
                continue

            data = safe_json_load(path)
            labels: List[str] = []
            for key_name in preferred_label_keys:
                val = data.get(key_name)
                if isinstance(val, list):
                    labels = [x for x in val if x]
                    break

            text = data.get("text") or ""
            prov = data.get("provenance") or ""
            result = (set(labels), text, prov, path)

            # Cache under both the requested dataset and the actual resolved
            # dataset so future mixed lookups are cheap.
            self.cache[key] = result
            self.cache[(found_in_dataset, doc_id)] = result
            return result

        tried_str = " | ".join(tried_paths)
        print(
            f"[WARN] Missing ground truth for requested dataset={dataset}, "
            f"id={doc_id}. Tried: {tried_str}",
            f"We have observed this only when antivirus removes a report from the compiled dataset.",
        )
        # Match evaluate.py: retain the prediction and score it against an
        # empty ground-truth set. This preserves false-positive accounting and
        # lets provenance filters handle the empty provenance consistently.
        result = (set(), "", "", "")
        self.cache[key] = result
        return result


def try_load_tool_names() -> List[str]:
    try:
        from Framework.utils.config import adapter_module_map
        return list(adapter_module_map.keys())
    except Exception:
        return list(DEFAULT_TOOLS)


def load_tool_capacity(capacity_path: str) -> Dict[str, Set[str]]:
    if not os.path.exists(capacity_path):
        raise FileNotFoundError(f"Tool capacity file not found: {capacity_path}")
    data = safe_json_load(capacity_path)
    caps: Dict[str, Set[str]] = {}
    for tool, codes in data.items():
        caps[tool] = {c for c in (codes or []) if c}
    return caps


def get_cap(tool_name: str, tool_caps_raw: Dict[str, Set[str]]) -> Set[str]:
    if tool_name in tool_caps_raw:
        return tool_caps_raw[tool_name]
    for k in tool_caps_raw:
        if k.lower() == tool_name.lower():
            return tool_caps_raw[k]
    base = tool_name.split("-")[0]
    if base in tool_caps_raw:
        return tool_caps_raw[base]
    for k in tool_caps_raw:
        if k.lower() == base.lower():
            return tool_caps_raw[k]
    # This matches the effective behavior of evaluate.py. In practice capacities
    # should exist for all tools/sub-tools used in the benchmark.
    return set()


def read_prediction_rows(json_path: str) -> List[Dict]:
    rows = safe_json_load(json_path)
    if rows is None:
        return []
    if isinstance(rows, dict):
        for key in ("results", "rows", "data", "observations"):
            if isinstance(rows.get(key), list):
                rows = rows[key]
                break
    if not isinstance(rows, list):
        raise ValueError(f"Prediction file is not a list: {json_path}")
    return rows


def choose_prediction_files(tool_dir: str, dataset: str, tool_name: str) -> List[str]:
    exact: List[str] = []
    tool_lower = tool_name.lower()
    if dataset == "curated":
        exact_name = f"our_results_{tool_lower}_benchmark_others.json"
        exact_path = os.path.join(tool_dir, exact_name)
        if os.path.exists(exact_path):
            exact = [exact_path]
    elif dataset == "orkl":
        exact_name = f"{tool_lower}_orkl_benchmark_results.json"
        exact_path = os.path.join(tool_dir, exact_name)
        if os.path.exists(exact_path):
            exact = [exact_path]
    else:
        raise ValueError(f"Unknown dataset={dataset}")

    if exact:
        return exact
    return sorted(glob.glob(os.path.join(tool_dir, "*.json")))


def load_examples_for_dataset(
    dataset: str,
    pred_root: str,
    gt_store: GroundTruthStore,
    tools: List[str],
    prov_data_only: bool,
    non_prov_data_only: bool,
) -> List[Example]:
    examples: List[Example] = []

    for tool in tools:
        tool_dir = os.path.join(pred_root, tool)
        if not os.path.isdir(tool_dir):
            continue

        json_paths = choose_prediction_files(tool_dir, dataset, tool)
        if not json_paths:
            continue

        prov_check = "TRAM" if tool == "Buchel" else tool

        for jp in json_paths:
            # Mirror evaluate.py behavior for naming sub-tools when multiple files exist.
            tool_display = tool if len(json_paths) == 1 else parse_subtool_name(tool, jp)
            rows = read_prediction_rows(jp)
            seen_ids: Set[str] = set()

            for row in rows:
                if not isinstance(row, dict):
                    continue
                doc_id = row.get("id") or row.get("sha1") or row.get("doc_id")
                if not doc_id or doc_id in seen_ids:
                    continue
                seen_ids.add(doc_id)

                raw_ttps = row.get("ttps")
                if raw_ttps is None:
                    ttps_list: List[str] = []
                elif isinstance(raw_ttps, list):
                    ttps_list = [t for t in raw_ttps if t]
                else:
                    try:
                        ttps_list = [t for t in list(raw_ttps) if t]
                    except Exception:
                        ttps_list = []

                gt_raw, gt_text, prov, gt_path = gt_store.read(dataset, doc_id)

                if prov_data_only and (prov or "").casefold() != prov_check.casefold():
                    continue
                if non_prov_data_only and (prov or "").casefold() == prov_check.casefold():
                    continue

                examples.append(
                    Example(
                        dataset=dataset,
                        tool=tool_display,
                        doc_id=doc_id,
                        pred_raw=set(ttps_list),
                        gt_raw=set(gt_raw),
                        gt_text=gt_text,
                        prov=prov,
                        source_prediction_file=jp,
                        source_gt_file=gt_path,
                    )
                )

    return examples


# -----------------------------------------------------------------------------
# Scoring / breakdown (mirrors generous_counts)
# -----------------------------------------------------------------------------
def evaluate_example(
    ex: Example,
    cap_raw: Set[str],
    collapse_tech_only: bool,
    augment_latent_tactics: bool,
) -> EvalBreakdown:
    gt_raw = set(ex.gt_raw)
    latent_tactics_added: Set[str] = set()

    if augment_latent_tactics:
        try:
            latent = infer_tactics_near_codes(ex.gt_text or "", list(ex.gt_raw)) or []
            latent_tactics_added = {c for c in latent if c} - gt_raw
            gt_raw |= {c for c in latent if c}
        except Exception:
            latent_tactics_added = set()

    pred_eff = canonical_set(list(ex.pred_raw))
    gt_eff = canonical_set(list(gt_raw))
    cap_mod = canonical_set(list(cap_raw))

    if collapse_tech_only and is_technique_only_capacity(cap_raw):
        pred_eff = collapse_parents(pred_eff)
        gt_eff = collapse_parents(gt_eff)

    inter_mod = pred_eff & gt_eff
    inter_raw = ex.pred_raw & gt_raw
    inter_all = set(inter_mod)
    if inter_raw:
        inter_all |= canonical_set(list(inter_raw))

    fp = {l for l in (pred_eff - gt_eff) if l not in inter_all}
    fn = {l for l in (gt_eff - pred_eff) if (l in cap_mod or l in cap_raw)}

    return EvalBreakdown(
        dataset=ex.dataset,
        tool=ex.tool,
        doc_id=ex.doc_id,
        pred_raw=set(ex.pred_raw),
        gt_raw=set(gt_raw),
        pred_eff=set(pred_eff),
        gt_eff=set(gt_eff),
        tp=set(inter_all),
        fp=set(fp),
        fn=set(fn),
        cap_raw=set(cap_raw),
        cap_mod=set(cap_mod),
        latent_tactics_added=set(latent_tactics_added),
        prov=ex.prov,
        source_prediction_file=ex.source_prediction_file,
        source_gt_file=ex.source_gt_file,
    )


# -----------------------------------------------------------------------------
# Aggregation
# -----------------------------------------------------------------------------
def prf(tp: int, fp: int, fn: int) -> Tuple[float, float, float]:
    p = tp / (tp + fp) if (tp + fp) else 0.0
    r = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = (2 * p * r / (p + r)) if (p + r) else 0.0
    return p, r, f1


def to_pipe_str(values: Iterable[str]) -> str:
    vals = sorted({v for v in values if v})
    return "|".join(vals)


def write_csv(path: str, rows: List[Dict[str, object]]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if not rows:
        with open(path, "w", newline="", encoding="utf-8") as f:
            f.write("")
        return

    fieldnames = list(rows[0].keys())
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


# -----------------------------------------------------------------------------
# Main analysis
# -----------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(description="Per-TTP failure analysis for TTP-Workbench benchmark outputs.")
    parser.add_argument("--repo_root", type=str, default=str(PROJECT), help="Path to the repository root.")
    parser.add_argument("--results_root", type=str, default=str(HERE.parent / "results"), help="Directory containing on_curated_data and on_author_labeled_data")
    parser.add_argument("--curated_gt_root", type=str, default=None, help="Override Datasets/curated_reports")
    parser.add_argument("--author_gt_root", type=str, default=None, help="Override Datasets/author_labeled_reviewed")
    parser.add_argument("--capacity_file", type=str, default=None, help="Override Framework/utils/ttp_contents.json")
    parser.add_argument("--output_dir", type=str, default="./ttp_failure_analysis", help="Directory for CSV outputs.")
    parser.add_argument("--collapse", action="store_true", help="Collapse sub-techniques to parent techniques for tools with technique-only capacity.")
    parser.add_argument("--tools", nargs="*", default=None, help="Optional subset of tools to analyze.")
    parser.add_argument("--datasets", nargs="*", choices=["curated", "orkl"], default=["curated", "orkl"], help="Datasets to include.")
    parser.add_argument("--prov_data_only", action="store_true", help="Mirror evaluate.py provenance-only filtering.")
    parser.add_argument("--non_prov_data_only", action="store_true", help="Mirror evaluate.py non-provenance filtering.")
    args = parser.parse_args()

    if args.prov_data_only and args.non_prov_data_only:
        raise ValueError("Choose at most one of --prov_data_only and --non_prov_data_only")

    repo_root = os.path.abspath(args.repo_root)
    results_root = os.path.abspath(args.results_root)
    output_dir = os.path.abspath(args.output_dir)
    os.makedirs(output_dir, exist_ok=True)

    curated_pred_root = os.path.join(results_root, "on_curated_data")
    orkl_pred_root = os.path.join(results_root, "on_author_labeled_data")
    curated_gt_root = os.path.abspath(args.curated_gt_root) if args.curated_gt_root else os.path.join(repo_root, "Datasets", "curated_reports")
    orkl_gt_root = os.path.abspath(args.author_gt_root) if args.author_gt_root else os.path.join(repo_root, "Datasets", "author_labeled_reviewed")
    capacity_path = os.path.abspath(args.capacity_file) if args.capacity_file else os.path.join(repo_root, "Framework", "utils", "ttp_contents.json")

    tools = args.tools if args.tools else try_load_tool_names()
    gt_store = GroundTruthStore(curated_root=curated_gt_root, orkl_root=orkl_gt_root)
    tool_caps_raw = load_tool_capacity(capacity_path)

    examples: List[Example] = []
    if "curated" in args.datasets:
        examples.extend(
            load_examples_for_dataset(
                dataset="curated",
                pred_root=curated_pred_root,
                gt_store=gt_store,
                tools=tools,
                prov_data_only=args.prov_data_only,
                non_prov_data_only=args.non_prov_data_only,
            )
        )
    if "orkl" in args.datasets:
        examples.extend(
            load_examples_for_dataset(
                dataset="orkl",
                pred_root=orkl_pred_root,
                gt_store=gt_store,
                tools=tools,
                prov_data_only=args.prov_data_only,
                non_prov_data_only=args.non_prov_data_only,
            )
        )

    breakdowns: List[EvalBreakdown] = []
    for ex in examples:
        cap_raw = get_cap(ex.tool, tool_caps_raw)
        collapse = bool(args.collapse) and is_technique_only_capacity(cap_raw)
        use_latent = is_ttp_llm(ex.tool)
        breakdowns.append(
            evaluate_example(
                ex,
                cap_raw=cap_raw,
                collapse_tech_only=collapse,
                augment_latent_tactics=use_latent,
            )
        )

    # ------------------------------------------------------------------
    # Output 1: consolidated per-example rows
    # ------------------------------------------------------------------
    consolidated_rows: List[Dict[str, object]] = []
    for b in breakdowns:
        consolidated_rows.append(
            {
                "dataset": b.dataset,
                "tool": b.tool,
                "doc_id": b.doc_id,
                "prov": b.prov,
                "prediction_file": b.source_prediction_file,
                "ground_truth_file": b.source_gt_file,
                "pred_raw": to_pipe_str(b.pred_raw),
                "gt_raw": to_pipe_str(b.gt_raw),
                "pred_effective": to_pipe_str(b.pred_eff),
                "gt_effective": to_pipe_str(b.gt_eff),
                "tp": to_pipe_str(b.tp),
                "fp": to_pipe_str(b.fp),
                "fn": to_pipe_str(b.fn),
                "latent_tactics_added": to_pipe_str(b.latent_tactics_added),
                "tool_capacity_raw": to_pipe_str(b.cap_raw),
                "tool_capacity_mod": to_pipe_str(b.cap_mod),
                "n_pred_raw": len(b.pred_raw),
                "n_gt_raw": len(b.gt_raw),
                "n_pred_effective": len(b.pred_eff),
                "n_gt_effective": len(b.gt_eff),
                "n_tp": len(b.tp),
                "n_fp": len(b.fp),
                "n_fn": len(b.fn),
            }
        )
    write_csv(os.path.join(output_dir, "consolidated_examples.csv"), consolidated_rows)

    # ------------------------------------------------------------------
    # Output 2: per-TTP / per-tool / per-dataset
    # ------------------------------------------------------------------
    per_ttp_tool_counts: Dict[Tuple[str, str, str], Counter] = defaultdict(Counter)
    false_negative_rows: List[Dict[str, object]] = []
    false_positive_rows: List[Dict[str, object]] = []
    confusion_counts: Counter = Counter()

    for b in breakdowns:
        key_prefix = (b.dataset, b.tool)

        for ttp in b.tp:
            per_ttp_tool_counts[(b.dataset, b.tool, ttp)]["tp"] += 1
        for ttp in b.fp:
            per_ttp_tool_counts[(b.dataset, b.tool, ttp)]["fp"] += 1
        for ttp in b.fn:
            per_ttp_tool_counts[(b.dataset, b.tool, ttp)]["fn"] += 1

        # Support in GT among scored opportunities is TP + FN.
        # Capture explicit support rows even when tp/fn later sum to zero due to only FP presence.
        for ttp in (b.tp | b.fn):
            per_ttp_tool_counts[(b.dataset, b.tool, ttp)]["support"] += 1
        for ttp in b.fp:
            per_ttp_tool_counts[(b.dataset, b.tool, ttp)]["pred_support"] += 1

        for missed in sorted(b.fn):
            false_negative_rows.append(
                {
                    "dataset": b.dataset,
                    "tool": b.tool,
                    "doc_id": b.doc_id,
                    "missed_ttp": missed,
                    "prov": b.prov,
                    "gt_effective": to_pipe_str(b.gt_eff),
                    "pred_effective": to_pipe_str(b.pred_eff),
                    "cooccurring_tp": to_pipe_str(b.tp),
                    "cooccurring_fp": to_pipe_str(b.fp),
                    "latent_tactics_added": to_pipe_str(b.latent_tactics_added),
                    "capacity_has_missed_ttp": int(missed in b.cap_mod or missed in b.cap_raw),
                    "prediction_file": b.source_prediction_file,
                    "ground_truth_file": b.source_gt_file,
                }
            )
            for wrong_pred in b.fp:
                confusion_counts[(b.dataset, b.tool, missed, wrong_pred)] += 1

        for spurious in sorted(b.fp):
            false_positive_rows.append(
                {
                    "dataset": b.dataset,
                    "tool": b.tool,
                    "doc_id": b.doc_id,
                    "false_positive_ttp": spurious,
                    "prov": b.prov,
                    "gt_effective": to_pipe_str(b.gt_eff),
                    "pred_effective": to_pipe_str(b.pred_eff),
                    "cooccurring_tp": to_pipe_str(b.tp),
                    "cooccurring_fn": to_pipe_str(b.fn),
                    "latent_tactics_added": to_pipe_str(b.latent_tactics_added),
                    "prediction_file": b.source_prediction_file,
                    "ground_truth_file": b.source_gt_file,
                }
            )

    per_ttp_tool_rows: List[Dict[str, object]] = []
    for (dataset, tool, ttp), cnt in sorted(per_ttp_tool_counts.items()):
        tp_n = cnt["tp"]
        fp_n = cnt["fp"]
        fn_n = cnt["fn"]
        support_n = cnt["support"]
        pred_support_n = cnt["pred_support"]
        precision, recall, f1 = prf(tp_n, fp_n, fn_n)
        miss_rate = (fn_n / support_n) if support_n else 0.0
        fp_rate = (fp_n / pred_support_n) if pred_support_n else 0.0
        per_ttp_tool_rows.append(
            {
                "dataset": dataset,
                "tool": tool,
                "ttp": ttp,
                "support": support_n,
                "pred_support": pred_support_n,
                "tp": tp_n,
                "fp": fp_n,
                "fn": fn_n,
                "precision": f"{precision:.6f}",
                "recall": f"{recall:.6f}",
                "f1": f"{f1:.6f}",
                "miss_rate": f"{miss_rate:.6f}",
                "fp_rate": f"{fp_rate:.6f}",
            }
        )
    write_csv(os.path.join(output_dir, "per_ttp_tool_dataset.csv"), per_ttp_tool_rows)
    write_csv(os.path.join(output_dir, "false_negative_cases.csv"), false_negative_rows)
    write_csv(os.path.join(output_dir, "false_positive_cases.csv"), false_positive_rows)

    # ------------------------------------------------------------------
    # Output 3: per-TTP overall by dataset and combined
    # ------------------------------------------------------------------
    overall_counts: Dict[Tuple[str, str], Counter] = defaultdict(Counter)
    for row in per_ttp_tool_rows:
        dataset = str(row["dataset"])
        ttp = str(row["ttp"])
        for scope in (dataset, "combined"):
            overall_counts[(scope, ttp)]["support"] += int(row["support"])
            overall_counts[(scope, ttp)]["pred_support"] += int(row["pred_support"])
            overall_counts[(scope, ttp)]["tp"] += int(row["tp"])
            overall_counts[(scope, ttp)]["fp"] += int(row["fp"])
            overall_counts[(scope, ttp)]["fn"] += int(row["fn"])
            if int(row["support"]) > 0:
                overall_counts[(scope, ttp)]["tools_with_support"] += 1

    overall_rows: List[Dict[str, object]] = []
    for (dataset_scope, ttp), cnt in sorted(overall_counts.items()):
        tp_n = cnt["tp"]
        fp_n = cnt["fp"]
        fn_n = cnt["fn"]
        support_n = cnt["support"]
        pred_support_n = cnt["pred_support"]
        precision, recall, f1 = prf(tp_n, fp_n, fn_n)
        miss_rate = (fn_n / support_n) if support_n else 0.0
        fp_rate = (fp_n / pred_support_n) if pred_support_n else 0.0
        overall_rows.append(
            {
                "dataset_scope": dataset_scope,
                "ttp": ttp,
                "support": support_n,
                "pred_support": pred_support_n,
                "tp": tp_n,
                "fp": fp_n,
                "fn": fn_n,
                "tools_with_support": cnt["tools_with_support"],
                "precision": f"{precision:.6f}",
                "recall": f"{recall:.6f}",
                "f1": f"{f1:.6f}",
                "miss_rate": f"{miss_rate:.6f}",
                "fp_rate": f"{fp_rate:.6f}",
            }
        )
    write_csv(os.path.join(output_dir, "per_ttp_dataset_overall.csv"), overall_rows)

    # ------------------------------------------------------------------
    # Output 4: confusion pairs from false negatives
    # ------------------------------------------------------------------
    confusion_rows: List[Dict[str, object]] = []
    for (dataset, tool, missed, wrong_pred), count in confusion_counts.most_common():
        confusion_rows.append(
            {
                "dataset": dataset,
                "tool": tool,
                "missed_ttp": missed,
                "cooccurring_false_positive_ttp": wrong_pred,
                "count": count,
            }
        )
    write_csv(os.path.join(output_dir, "ttp_confusions_from_fns.csv"), confusion_rows)

    # ------------------------------------------------------------------
    # Console summary: a few useful top failures
    # ------------------------------------------------------------------
    def top_rows(rows: List[Dict[str, object]], dataset_scope: str, n: int = 15) -> List[Dict[str, object]]:
        subset = [r for r in rows if r["dataset_scope"] == dataset_scope and int(r["support"]) > 0]
        subset.sort(key=lambda r: (float(r["recall"]), -int(r["support"]), r["ttp"]))
        return subset[:n]

    print(f"[DONE] Wrote outputs to: {output_dir}")
    print(f"[INFO] Examples analyzed: {len(breakdowns)}")
    print("[INFO] Top low-recall TTPs (combined):")
    for row in top_rows(overall_rows, "combined", n=15):
        print(
            f"  {row['ttp']}: recall={row['recall']} miss_rate={row['miss_rate']} "
            f"support={row['support']} tp={row['tp']} fn={row['fn']} fp={row['fp']}"
        )


if __name__ == "__main__":
    main()

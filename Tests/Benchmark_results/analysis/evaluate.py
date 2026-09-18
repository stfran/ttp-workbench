#evaluate.py
"""
Evaluate multiple TTP extraction experiments against reviewed ground truth and plot metrics.
"""
from __future__ import annotations

import json
import os
import sys
import csv
import glob
from collections import defaultdict, Counter
from dataclasses import dataclass
from typing import Dict, List, Set, Tuple, Optional
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parents[2]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

import matplotlib.pyplot as plt
import numpy as np
import argparse

from ttp_llm_evaluation_helper import infer_tactics_near_codes
from Framework.utils.attack_lookup import modernize_ttps as _modernize_ttps

ALL_DATA = False
PROV_DATA_ONLY = False
NON_PROV_DATA_ONLY = False
COLLAPSE = False
ROOT = "."

GT_ROOTS = [str(PROJECT / "Datasets/curated_reports")]

TOOL_CAPACITY_PATH = str(PROJECT / "Framework/utils/ttp_contents.json")

def modernize_list(codes: List[str]) -> List[str]:
    try:
        return _modernize_ttps(codes)
    except Exception:
        # Be maximally forgiving if any single TTP trips up the utility.
        out = []
        for c in codes:
            try:
                out.extend(_modernize_ttps([c]))
            except Exception:
                out.append(c)
        # dedup while preserving order
        seen = set()
        uniq = []
        for c in out:
            if c and c not in seen:
                seen.add(c); uniq.append(c)
        return uniq

def canonical_set(codes: List[str]) -> Set[str]:
    """Modernize and return as a set (unique, non-empty)."""
    return set([c for c in modernize_list(list(codes)) if c])

# ---------------------------- Data classes -------------------------------------
@dataclass
class Report:
    doc_id: str
    pred_raw: Set[str]
    gt_raw: Set[str]
    gt_text: Optional[str] = ""
    prov: Optional[str] = ""

# ---------------------------- File I/O -----------------------------------------
_gt_cache: Dict[str, Set[str]] = {}
_gt_full_cache: Dict[str, Tuple[Set[str], str, str]] = {}

def read_ground_truth_and_text(doc_id: str) -> Tuple[Set[str], str]:
    if doc_id in _gt_full_cache:
        return _gt_full_cache[doc_id]
    # Search through GT_ROOTS for the doc_id
    GT_ROOT = ""
    path = ""
    for root in GT_ROOTS:
        path = os.path.join(root, f"{doc_id}.json")
        if os.path.exists(path):
            GT_ROOT = root
            break
    
    if not os.path.exists(path):
        print(f"[WARN] GT missing for id={doc_id}: {path}")
        _gt_full_cache[doc_id] = (set(), "", "")
        _gt_cache[doc_id] = set()
        return _gt_full_cache[doc_id]
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    labels = data.get("ground_truth") or data.get("original_labels") or []
    text = data.get("text") or ""
    prov = data.get("provenance") or ""
    gt_set = set([c for c in labels if c])
    _gt_cache[doc_id] = gt_set
    _gt_full_cache[doc_id] = (gt_set, text, prov)
    return _gt_full_cache[doc_id]

def read_ground_truth(doc_id: str) -> Set[str]:
    gt, _ = read_ground_truth_and_text(doc_id)
    return gt


def read_experiment_folder(folder: str) -> List[Report]:
    """Load one experiment JSON from `folder` and return a list of Report.
    Robust to rows with missing/None ttps and a few alternate top-level shapes.
    """
    # Find a single JSON file in the folder
    candidates = sorted(glob.glob(os.path.join(folder, "*.json")))
    if not candidates:
        raise FileNotFoundError(f"No .json files found in {folder}")
    if len(candidates) > 1:
        print(f"[INFO] Multiple .json files in {folder}; using first: {os.path.basename(candidates[0])}")
    path = candidates[0]

    with open(path, "r", encoding="utf-8") as f:
        rows = json.load(f)

    # Normalize top-level container into a list of row dicts
    if rows is None:
        rows = []
    if isinstance(rows, dict):
        # Common alternate keys
        for key in ("results", "rows", "data", "observations"):
            val = rows.get(key)
            if isinstance(val, list):
                rows = val
                break
        else:
            raise ValueError(
                f"Results JSON at {path} is a dict but has no list under known keys; keys={list(rows.keys())[:10]}"
            )
    if not isinstance(rows, list):
        raise ValueError(f"Results JSON at {path} is not a list; got type={type(rows).__name__}")

    reports: List[Report] = []
    for idx, row in enumerate(rows):
        if not isinstance(row, dict):
            print(f"[WARN] Skipping non-dict row #{idx} in {path} (type={type(row).__name__})")
            continue
        doc_id = row.get("id") or row.get("sha1") or row.get("doc_id")
        if not doc_id:
            print(f"[WARN] Row #{idx} in {path} missing 'id'; skipping")
            continue

        # Safely extract ttps (tolerate None/str/iterables)
        raw_ttps = row.get("ttps")
        if raw_ttps is None:
            ttps_list: List[str] = []
        elif isinstance(raw_ttps, list):
            ttps_list = [t for t in raw_ttps if t]
        else:
            try:
                ttps_list = [t for t in list(raw_ttps) if t]
            except Exception:
                print(f"[WARN] Row #{idx} in {path} has unsupported 'ttps' type={type(raw_ttps).__name__}; treating as empty")
                ttps_list = []

        pred = set(ttps_list)
        gt, gt_text = read_ground_truth_and_text(doc_id)
        reports.append(Report(doc_id=doc_id, pred_raw=pred, gt_raw=gt, gt_text=gt_text))

    return reports

def read_experiment_file(json_path: str) -> List[Report]:
    """Read a single results JSON file and return Reports (same shape as folder reader)."""
    print(json_path)
    with open(json_path, "r", encoding="utf-8") as f:
        rows = json.load(f)

    if rows is None:
        rows = []
    if not isinstance(rows, list):
        raise ValueError(f"Results JSON at {json_path} is not a list; got type={type(rows).__name__}")

    reports: List[Report] = []
    seen_ids: Set[str] = set()
    for idx, row in enumerate(rows):
        doc_id = row.get("id")

        if doc_id in seen_ids:
            print(f"[WARN] Duplicate doc_id={doc_id} in row #{idx} of {json_path}; skipping")
            continue
        seen_ids.add(doc_id)

        raw_ttps = row.get("ttps")
        if raw_ttps is None:
            ttps_list: List[str] = []
        elif isinstance(raw_ttps, list):
            ttps_list = [t for t in raw_ttps if t]

        pred = set(ttps_list)
        gt, gt_text, prov = read_ground_truth_and_text(doc_id)
        reports.append(Report(doc_id=doc_id, pred_raw=pred, gt_raw=gt, gt_text=gt_text, prov=prov))
    return reports

def parse_subtool_name(tool_display: str, json_path: str, shorten: bool = False) -> str:
    """
    Parse orbinato variants
    orbinato_mlp_... => Orbinato-MLP
    orbinato_pretrained_lstm_... => Orbinato-LSTM
    orbinato_secbert_... => Orbinato-SecBERT
    our_results_orbinato_secbert_... => Orbinato-SecBERT
    etc.
    """

    tool_lower = tool_display.lower()
    stem = os.path.splitext(os.path.basename(json_path))[0].lower()
    
    # Strip "our_results_" prefix if present
    if stem.startswith("our_results_"):
        stem = stem[len("our_results_"):]
    
    if shorten:
        prefix = f"{tool_lower[:4]}_"
    else:
        prefix = f"{tool_lower}_"
    
    if not stem.startswith(prefix):
        # Fallback: use the stem as-is if it doesn't match expected pattern
        return f"{tool_display}-{stem}"
    
    # Remove the tool prefix to get the variant part
    variant = stem[len(prefix):]
    
    # Map known variants to nice names
    variant_map = {
        "mlp": "MLP",
        "pretrained_lstm": "LSTM", 
        "secbert": "SecBERT"
    }
    
    # Find the best match for the variant
    nice = variant_map.get(variant)
    if nice is None:
        # Try partial matches for compound names
        for key, value in variant_map.items():
            if key in variant:
                nice = value
                break
        else:
            # Default: capitalize the variant
            nice = variant.upper()
    
    return f"{tool_display}-{nice}"


def load_tool_capacity() -> Dict[str, Set[str]]:
    if not os.path.exists(TOOL_CAPACITY_PATH):
        print(f"[WARN] Tool capacity file missing: {TOOL_CAPACITY_PATH}. Proceeding with empty capacities.")
        return {}
    with open(TOOL_CAPACITY_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    caps: Dict[str, Set[str]] = {}
    for tool, codes in data.items():
        # Store both raw and modernized forms for convenience
        s = set([c for c in codes if c])
        caps[tool] = s
    return caps

def is_ttp_llm(tool_name: str) -> bool:
    s = (tool_name or "").lower().replace("_", "-")
    return "ttp-llm" in s or "ttpllm" in s

def _is_technique_only_capacity(cap_raw: Set[str]) -> bool:
    """
    True if capacity has at least one technique code and *no* sub-technique codes.
    (Ignore TA#### and empty capacity; don't collapse in those cases.)
    """
    #cap_mod = canonical_set(list(cap_raw))
    has_tech = any(c.startswith("T") for c in cap_raw)
    has_sub  = any(c.startswith("T") and "." in c for c in cap_raw)
    return has_tech and not has_sub


def _collapse_parents(codes: Set[str]) -> Set[str]:
    """
    Collapse sub-techniques to parent technique (T####.### -> T####).
    Keep T#### as-is and preserve non-tech (e.g., TA####) codes.
    """
    out: Set[str] = set()
    for c in codes:
        if not c:
            continue
        if c.startswith("T") and "." in c:
            out.add(c.split(".", 1)[0])
        else:
            out.add(c)
    return out


# ---------------------------- Metric helpers -----------------------------------
@dataclass
class PRF:
    precision: float
    recall: float
    f1: float
    support: int  # see notes above


def prf_from_counts(tp: int, fp: int, fn: int) -> Tuple[float, float, float]:
    p = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    r = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1 = (2*p*r/(p+r)) if (p+r) > 0 else 0.0
    return p, r, f1


# Strict evaluation --------------------------------------------------------------
def strict_counts(reports: List[Report]) -> Tuple[int, int, int, int, Dict[str, Tuple[int,int,int]]]:
    """Return (tp, fp, fn, micro_support, label_counts)
    label_counts: label -> (TP, FP, FN) accumulated across all reports
    """
    tp = fp = fn = 0
    label_counts: Dict[str, List[int]] = defaultdict(lambda: [0,0,0])
    for r in reports:
        inter = r.pred_raw & r.gt_raw
        only_p = r.pred_raw - r.gt_raw
        only_g = r.gt_raw - r.pred_raw
        tp += len(inter); fp += len(only_p); fn += len(only_g)
        for l in inter:
            label_counts[l][0] += 1
        for l in only_p:
            label_counts[l][1] += 1
        for l in only_g:
            label_counts[l][2] += 1
    micro_support = tp + fn
    return tp, fp, fn, micro_support, {k: tuple(v) for k,v in label_counts.items()}


def strict_metrics(reports: List[Report]) -> Tuple[PRF, PRF]:
    tp, fp, fn, micro_support, label_counts = strict_counts(reports)
    p, r, f1 = prf_from_counts(tp, fp, fn)
    micro = PRF(p, r, f1, micro_support)
    # Macro over labels that appear in GT or preds at least once
    macro_vals = []
    for _label, (ltp, lfp, lfn) in label_counts.items():
        lp, lr, lf1 = prf_from_counts(ltp, lfp, lfn)
        macro_vals.append((lp, lr, lf1))
    if macro_vals:
        mp = float(np.mean([v[0] for v in macro_vals]))
        mr = float(np.mean([v[1] for v in macro_vals]))
        mf1 = float(np.mean([v[2] for v in macro_vals]))
        macro_support = len(macro_vals)
    else:
        mp = mr = mf1 = 0.0
        macro_support = 0
    macro = PRF(mp, mr, mf1, macro_support)
    return micro, macro

# Generous evaluation (capacity-aware, modernization-aware) ----------------------
@dataclass
class GenReportView:
    pred_raw: Set[str]
    gt_raw: Set[str]
    pred_mod: Set[str]
    gt_mod: Set[str]
    cap_raw: Set[str]
    cap_mod: Set[str]


def build_gen_view(pred_raw: Set[str], gt_raw: Set[str], cap_raw: Set[str]) -> GenReportView:
    return GenReportView(
        pred_raw=pred_raw,
        gt_raw=gt_raw,
        pred_mod=canonical_set(list(pred_raw)),
        gt_mod=canonical_set(list(gt_raw)),
        cap_raw=set(cap_raw),
        cap_mod=canonical_set(list(cap_raw)),
    )


def generous_counts(reports: List[Report], cap_raw: Set[str], *, 
                    augment_latent_tactics: bool = False,
                    collapse_tech_only: bool = False) -> Tuple[int,int,int,int, Dict[str, Tuple[int,int,int]]]:
    tp = fp = fn = 0
    label_counts: Dict[str, List[int]] = defaultdict(lambda: [0,0,0])

    for r in reports:
        gt_raw = set(r.gt_raw)
        if augment_latent_tactics:
            try:
                latent = infer_tactics_near_codes(r.gt_text or "", list(r.gt_raw)) or []
                gt_raw |= set([c for c in latent if c])
            except Exception:
                pass
        v = build_gen_view(r.pred_raw, gt_raw, cap_raw)
        # Operate in the modernized space for labeling, but allow raw-match to create TPs, too.
        gt_eff = set(v.gt_mod)
        pred_eff = set(v.pred_mod)

        if collapse_tech_only and _is_technique_only_capacity(cap_raw):
            # Collapse sub-techniques to parent techniques for this report
            gt_eff = _collapse_parents(gt_eff)
            pred_eff = _collapse_parents(pred_eff)

        # True positives (modern space)
        inter_mod = pred_eff & gt_eff
        # Additionally capture raw-only overlaps that modernization might have missed (defensive)
        inter_raw = v.pred_raw & v.gt_raw
        # Merge both notions of TP into mod codes (map raw TPs into mod codes to count per-label)
        inter_all = set(inter_mod)
        if inter_raw:
            mod_from_raw = canonical_set(list(inter_raw))
            inter_all |= mod_from_raw
        # Now, TPs are inter_all. FPs are preds not explained by GT even after modernization.
        # FNs are GT not covered by preds after modernization, but only if within capacity_mod.

        # Compute per-label counts in modern space
        # TPs
        for l in inter_all:
            label_counts[l][0] += 1
        # FPs (modern preds not in (modern GT))
        for l in pred_eff - gt_eff:
            # If this FP corresponds to some raw TP (unlikely), it's already in inter_all; skip double count
            if l in inter_all:
                continue
            label_counts[l][1] += 1
        # FNs (modern GT not in (modern preds)) filtered by capacity
        for l in gt_eff - pred_eff:
            if (l in v.cap_mod) or (l in v.cap_raw):
                label_counts[l][2] += 1
            else:
                # ignored
                pass

        # Aggregate micro
        # Micro TP is size of inter_all
        tp += len(inter_all)
        # Micro FP: count modern preds not in modern GT, excluding those mapped to inter_all
        fp += len([l for l in (pred_eff - gt_eff) if l not in inter_all])
        # Micro FN: modern GT not in modern preds but only if within capacity
        fn += len([l for l in (gt_eff - pred_eff) if (l in v.cap_mod or l in v.cap_raw)])

    micro_support = tp + fn
    return tp, fp, fn, micro_support, {k: tuple(v) for k,v in label_counts.items()}


def generous_metrics(reports: List[Report], cap_raw: Set[str], *, 
                     augment_latent_tactics: bool = False,
                     collapse_tech_only: bool = False) -> Tuple[PRF, PRF]:

    metrics = generous_counts(reports, cap_raw, 
                              augment_latent_tactics=augment_latent_tactics, 
                              collapse_tech_only=collapse_tech_only)
    tp, fp, fn, micro_support, label_counts = metrics
    p, r, f1 = prf_from_counts(tp, fp, fn)
    micro = PRF(p, r, f1, micro_support)

    # Macro over modernized labels
    macro_vals = []
    for _label, (ltp, lfp, lfn) in label_counts.items():
        # Only include labels that have any GT incidence (TP+FN>0); otherwise macro can be skewed
        if (ltp + lfn) == 0:
            continue
        lp, lr, lf1 = prf_from_counts(ltp, lfp, lfn)
        macro_vals.append((lp, lr, lf1))
    if macro_vals:
        mp = float(np.mean([v[0] for v in macro_vals]))
        mr = float(np.mean([v[1] for v in macro_vals]))
        mf1 = float(np.mean([v[2] for v in macro_vals]))
        macro_support = len(macro_vals)  # number of labels contributing
    else:
        mp = mr = mf1 = 0.0
        macro_support = 0
    macro = PRF(mp, mr, mf1, macro_support)
    return micro, macro

# If an experiment name isn't in the capacity file, we'll assume unlimited capacity for that tool.
def get_cap(tool_name: str) -> Set[str]:
    if tool_name in tool_caps_raw:
        return tool_caps_raw[tool_name]
    for k in tool_caps_raw.keys():
        if k.lower() == tool_name.lower():
            return tool_caps_raw[k]
    # parse sub-tool out (e.g., 'Orbinato-MLP' -> 'Orbinato')
    base = tool_name.split("-")[0]
    if base in tool_caps_raw:
        return tool_caps_raw[base]
    for k in tool_caps_raw.keys():
        if k.lower() == base.lower():
            return tool_caps_raw[k]
    # Otherwise: unlimited capacity
    return set()
# ---------------------------- CSV Output ----------------------------------------

def write_summary_csv(path: str, rows: List[Dict[str, str]]) -> None:
    if not rows:
        return
    fields = list(rows[0].keys())
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow(r)

# ---------------------------- Main ---------------------------------------------

def main():
    global KEEP_PROVENANCE
    global PROV_DATA_ONLY
    global NON_PROV_DATA_ONLY
    global COLLAPSE
    global ROOT
    global GT_ROOTS
    global tool_caps_raw
    global TOOL_CAPACITY_PATH

    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=str, default=str(HERE.parent / "results/on_curated_data"), help="Directory containing per-tool prediction folders")
    parser.add_argument("--all_data", action="store_true", help="Keep all data in reports")
    parser.add_argument("--prov_data_only", action="store_true", help="Keep only provenance data in reports")
    parser.add_argument("--non_prov_data_only", action="store_true", help="Keep only non-provenance data in reports")
    parser.add_argument("--output_csv", type=str, default="evaluation_summary.csv", help="Path to output CSV summary file")
    parser.add_argument("--gt_roots", type=str, nargs="+", default=GT_ROOTS, help="Root directories for ground truth reports")
    parser.add_argument("--collapse", action="store_true", help="collapse sub-technique codes to parent technique codes for tools that don't have sub-technique capacity")
    parser.add_argument("--capacity_file", type=str, default=TOOL_CAPACITY_PATH, help="Tool prediction-capacity JSON")
    parser.add_argument("--exclude_provenance", action="append", default=[], help="Exclude this provenance for every tool (repeatable)")
    args = parser.parse_args()
    
    KEEP_PROVENANCE = args.all_data
    PROV_DATA_ONLY = args.prov_data_only
    NON_PROV_DATA_ONLY = args.non_prov_data_only
    COLLAPSE = args.collapse
    GT_ROOTS = args.gt_roots
    ROOT = args.root
    TOOL_CAPACITY_PATH = args.capacity_file
    excluded_provenance = {value.casefold() for value in args.exclude_provenance}

    EXPERIMENT_FOLDERS = {
        "TTPDrill": f"{ROOT}/TTPDrill",
        "rcATT": f"{ROOT}/rcATT",
        "AttacKG": f"{ROOT}/AttacKG",
        "TRAM": f"{ROOT}/TRAM",
        "RAF-AG": f"{ROOT}/RAF-AG",
        "TTP-LLM": f"{ROOT}/TTP-LLM",
        "Orbinato": f"{ROOT}/Orbinato",
        "Buchel": f"{ROOT}/Buchel",
        "LADDER": f"{ROOT}/LADDER",
        "SeqMask": f"{ROOT}/SeqMask"
    }

    experiments = dict(EXPERIMENT_FOLDERS)
    tool_caps_raw = load_tool_capacity()  

 
    # Load reports per tool, expanding sub-tools when multiple JSONs exist
    tool_reports: Dict[str, List[Report]] = {}
    for tool, folder in experiments.items():
        print(f"[INFO] Loading experiment for tool: {tool} from folder: {folder}")

        jsons = sorted(glob.glob(os.path.join(folder, "*.json")))
        if not jsons:
            print(f"[ERROR] No .json files found in {folder}")
            sys.exit(2)
        if tool == "Buchel":
            prov_check = "TRAM"
        else:
            prov_check = tool

        if len(jsons) == 1:
            # Regular single-result tool
            try:
                reps = read_experiment_file(jsons[0])
            except Exception as e:
                print(f"[ERROR] Failed to read {jsons[0]}: {e}")
                sys.exit(2)
            # keep select data based on arguments

            reps = [r for r in reps if (r.prov or "").casefold() not in excluded_provenance]

            if ALL_DATA:
                pass
            elif PROV_DATA_ONLY:
                print(f"[INFO] Filtering to provenance data only for {tool}")
                reps = [r for r in reps if r.prov == prov_check]
            elif NON_PROV_DATA_ONLY:
                reps = [r for r in reps if (r.prov or "").casefold() != prov_check.casefold()]
            tool_reports[tool] = reps
            print(f"[INFO] Loaded {len(reps)} reports for {tool}")
        else:
            # Multiple hyperparam runs -> create sub-tools
            for jp in jsons:
                subtool_name = parse_subtool_name(tool, jp)  # e.g., Orbinato-MLP
                try:
                    reps = read_experiment_file(jp)
                except Exception as e:
                    print(f"[ERROR] Failed to read {jp}: {e}")
                    sys.exit(2)
                # keep select data based on arguments
                reps = [r for r in reps if (r.prov or "").casefold() not in excluded_provenance]
                if ALL_DATA:
                    pass
                elif PROV_DATA_ONLY:
                    print(f"[INFO] Filtering to provenance data only for {tool}")
                    reps = [r for r in reps if r.prov == prov_check]
                elif NON_PROV_DATA_ONLY:
                    reps = [r for r in reps if (r.prov or "").casefold() != prov_check.casefold()]
                tool_reports[subtool_name] = reps
                print(f"[INFO] Loaded {len(reps)} reports for {subtool_name} (from {os.path.basename(jp)})")

    # Compute metrics across three regimes
    summary_rows: List[Dict[str, str]] = []

    def append_rows(prefix: str, tool: str, micro: PRF, macro: PRF):
        summary_rows.append({
            "eval": prefix,
            "tool": tool,
            "precision_micro": f"{micro.precision:.4f}",
            "recall_micro": f"{micro.recall:.4f}",
            "f1_micro": f"{micro.f1:.4f}",
            "support_micro": str(micro.support),
            "precision_macro": f"{macro.precision:.4f}",
            "recall_macro": f"{macro.recall:.4f}",
            "f1_macro": f"{macro.f1:.4f}",
            "support_macro_labels": str(macro.support),
            "capacity": len(get_cap(tool)),
            "number_of_reports": str(len(tool_reports[tool])),
        })

    # Strict
    strict_micro_list: List[PRF] = []
    strict_macro_list: List[PRF] = []
    tools = list(tool_reports.keys())
    for tool in tools:
        micro, macro = strict_metrics(tool_reports[tool])
        strict_micro_list.append(micro)
        strict_macro_list.append(macro)
        append_rows("strict", tool, micro, macro)

    # Generous (modernization + capacity-aware)
    gen_micro_list: List[PRF] = []
    gen_macro_list: List[PRF] = []
    for tool in tools:
        cap_raw = get_cap(tool)
        use_latent = is_ttp_llm(tool)
        collapse = bool(args.collapse) and _is_technique_only_capacity(cap_raw)
        micro, macro = generous_metrics(tool_reports[tool], cap_raw, augment_latent_tactics=use_latent, collapse_tech_only=collapse)
        gen_micro_list.append(micro)
        gen_macro_list.append(macro)
        append_rows("generous", tool, micro, macro)

    # Write summary CSV
    write_summary_csv(os.path.join(ROOT, args.output_csv), summary_rows)
    print(f"[DONE] Wrote {args.output_csv}")


if __name__ == "__main__":
    main()

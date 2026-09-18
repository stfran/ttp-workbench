#!/usr/bin/env python3
"""Direct, capacity-aware comparisons of benchmarked tool pairs."""

import argparse
import json
import os
from typing import Dict, Set, List, Optional, Iterable, Tuple
import glob
import sys
from pathlib import Path
import itertools

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parents[2]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

import pandas as pd
import numpy as np
import evaluate as eap
from evaluate import (
    read_experiment_file,
    parse_subtool_name,
    PRF,
    Report,
    is_ttp_llm,
    get_cap,
    _is_technique_only_capacity,
    generous_metrics,
    _collapse_parents,
    canonical_set,
)

ALL_DATA = False
PROV_DATA_ONLY = False
NON_PROV_DATA_ONLY = False
COLLAPSE = False
MIN_REPORTS = 5
EPS = 1e-12

EXPERIMENT_ROOTS = [str(HERE.parent / "results/on_curated_data"), str(HERE.parent / "results/on_author_labeled_data")]
GT_ROOTS = [str(PROJECT / "Datasets/curated_reports"), str(PROJECT / "Datasets/author_labeled_reviewed")]

TOOL_CAPACITY_PATH = str(PROJECT / "Framework/utils/ttp_contents.json")

EXPERIMENT_FOLDERS = {
    "TTPDrill": "TTPDrill",
    "rcATT": "rcATT",
    "AttacKG": "AttacKG",
    "TRAM": "TRAM",
    "RAF-AG": "RAF-AG",
    "TTP-LLM": "TTP-LLM",
    "Orbinato": "Orbinato",
    "Buchel": "Buchel",
    "LADDER": "LADDER",
    "SeqMask": "SeqMask",
}

# ----------------------------
# Capacity + GT helpers
# ----------------------------

def load_tool_capacity() -> Dict[str, Set[str]]:
    if not os.path.exists(TOOL_CAPACITY_PATH):
        print(f"[WARN] Tool capacity file missing: {TOOL_CAPACITY_PATH}. Proceeding with empty capacities.")
        return {}
    with open(TOOL_CAPACITY_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    caps: Dict[str, Set[str]] = {}
    for tool, codes in data.items():
        s = set([c for c in codes if c])
        caps[tool] = s
    return caps


def _to_set(x: Iterable[str] | None) -> Set[str]:
    return set([c for c in (x or []) if c])


def _gt_raw(report) -> Set[str]:
    if hasattr(report, "gt_raw") and report.gt_raw is not None:
        return _to_set(report.gt_raw)
    return _to_set(getattr(report, "ground_truth", []))


def _prep_capacity_variants(cap_raw_1: Set[str], cap_raw_2: Set[str], *, collapse_ok: bool) -> dict:
    raw_1 = set([c for c in cap_raw_1 if c])
    raw_2 = set([c for c in cap_raw_2 if c])
    inter_raw = raw_1 & raw_2

    mod_1 = canonical_set(list(raw_1))
    mod_2 = canonical_set(list(raw_2))
    inter_mod = mod_1 & mod_2

    inter_col = None
    inter_mod_col = None
    if collapse_ok:
        col_1 = _collapse_parents(mod_1)
        col_2 = _collapse_parents(mod_2)
        inter_col = col_1 & col_2
        mod_1_col = _collapse_parents(mod_1)
        mod_2_col = _collapse_parents(mod_2)
        inter_mod_col = mod_1_col & mod_2_col

    return {
        "inter_raw": inter_raw,
        "inter_mod": inter_mod,
        "inter_col": inter_col,
        "inter_mod_col": inter_mod_col,
    }


def _gt_variants(report, *, collapse_ok: bool) -> dict:
    gt_raw = _gt_raw(report)
    gt_mod = canonical_set(list(gt_raw))
    gt_col = _collapse_parents(gt_mod) if collapse_ok else None
    gt_mod_col = _collapse_parents(gt_mod) if collapse_ok else None
    return {"gt_raw": gt_raw, "gt_mod": gt_mod, "gt_col": gt_col, "gt_mod_col": gt_mod_col}


def filter_reports_by_capacity_generous(
    reports: List[Report],
    cap_raw_1: Set[str],
    cap_raw_2: Set[str],
    *,
    collapse: bool = False,
) -> List[Report]:
    collapse_ok = bool(collapse) and (
        _is_technique_only_capacity(cap_raw_1) or _is_technique_only_capacity(cap_raw_2)
    )

    inters = _prep_capacity_variants(cap_raw_1, cap_raw_2, collapse_ok=collapse_ok)
    out: List[Report] = []

    for r in reports:
        gts = _gt_variants(r, collapse_ok=collapse_ok)

        keep = False
        if gts["gt_raw"] and (
            gts["gt_raw"].issubset(inters["inter_raw"])
            or gts["gt_raw"].issubset(inters["inter_mod"])
            or gts["gt_raw"].issubset(inters["inter_col"] or set())
            or gts["gt_raw"].issubset(inters["inter_mod_col"] or set())
        ):
            keep = True
        elif gts["gt_mod"] and (
            gts["gt_mod"].issubset(inters["inter_raw"])
            or gts["gt_mod"].issubset(inters["inter_mod"])
            or gts["gt_mod"].issubset(inters["inter_col"] or set())
            or gts["gt_mod"].issubset(inters["inter_mod_col"] or set())
        ):
            keep = True
        elif collapse_ok and gts["gt_col"] is not None and inters["inter_col"] is not None:
            if (
                gts["gt_col"].issubset(inters["inter_col"])
                or gts["gt_col"].issubset(inters["inter_mod_col"])
                or gts["gt_mod"].issubset(inters["inter_raw"])
                or gts["gt_mod"].issubset(inters["inter_mod"])
            ):
                keep = True

        if keep:
            out.append(r)

    return out


# ----------------------------
# Tool selection + plotting
# ----------------------------

def _parse_tools_arg(tools_arg: List[str], experiments: Dict[str, str]) -> List[str]:
    """
    Accepts:
      --tools all
      --tools TTPDrill rcATT
      --tools TTPDrill,rcATT
      --tools "TTPDrill, rcATT"
    Returns list of base tool names (keys in EXPERIMENT_FOLDERS).
    """
    if not tools_arg:
        raise ValueError("--tools is required")

    # Flatten commas + whitespace
    raw: List[str] = []
    for item in tools_arg:
        parts = [p.strip() for p in item.split(",") if p.strip()]
        raw.extend(parts)

    if len(raw) == 1 and raw[0].lower() in ("all", "*"):
        return sorted(list(experiments.keys()))

    # Validate
    bad = [t for t in raw if t not in experiments]
    if bad:
        raise ValueError(f"Unknown tools in --tools: {bad}. Allowed: {sorted(list(experiments.keys()))}")
    # Dedup preserving order
    out: List[str] = []
    for t in raw:
        if t not in out:
            out.append(t)
    return out


def aggregate_variants(tool_reports: Dict[str, List[Report]], base_tool: str) -> Tuple[List[str], List[Report]]:
    prefix = f"{base_tool}-"
    variant_names = [t for t in tool_reports.keys() if t == base_tool or t.startswith(prefix)]
    if not variant_names and base_tool in tool_reports:
        variant_names = [base_tool]

    aggregated: List[Report] = []
    for name in variant_names:
        aggregated.extend(tool_reports[name])
    return variant_names, aggregated


def _ensure_list(x):
    return x if isinstance(x, list) else list(x)


def main():
    global ALL_DATA, PROV_DATA_ONLY, NON_PROV_DATA_ONLY, COLLAPSE, GT_ROOTS, EXPERIMENT_ROOTS, MIN_REPORTS, TOOL_CAPACITY_PATH

    parser = argparse.ArgumentParser(description="Evaluate and plot direct benchmark comparisons.")

    # REPLACED: tool1/tool2/all_tools -> --tools
    parser.add_argument(
        "--tools",
        nargs="+",
        required=True,
        help=(
            "Tools to compare. Examples:\n"
            "  --tools TTPDrill rcATT\n"
            "  --tools TTPDrill,rcATT\n"
            "  --tools all\n"
            "Allowed: " + ", ".join(EXPERIMENT_FOLDERS.keys())
        ),
    )

    parser.add_argument("--roots", nargs="+", type=str, default=EXPERIMENT_ROOTS,
                        help=f"Root directories for experiments and outputs, default = {', '.join(EXPERIMENT_ROOTS)}")
    parser.add_argument("--gt_roots", nargs="+", type=str, default=GT_ROOTS,
                        help=f"Ground truth directories, default = {', '.join(GT_ROOTS)}")

    parser.add_argument("--all_data", action="store_true", help="Keep all data in reports")
    parser.add_argument("--prov_data_only", action="store_true", help="Keep only provenance data in reports")
    parser.add_argument("--non_prov_data_only", action="store_true", help="Keep only non-provenance data in reports")

    parser.add_argument("--output_dir", type=str, default="comparisons", help="Directory to save output plots and CSVs")
    parser.add_argument("--output_csv", type=str, default="evaluation_summary.csv", help="Base name for output CSV(s)")
    parser.add_argument("--collapse", action="store_true",
                        help="collapse sub-technique codes to parent technique codes for tools that don't have sub-technique capacity")
    parser.add_argument("--capacity_file", type=str, default=TOOL_CAPACITY_PATH, help="Path to the tool capacities JSON file.")
    parser.add_argument("--filter", action="store_true", help="Filter reports to only those within both tools' capacities.")
    parser.add_argument("--min_reports", type=int, default=MIN_REPORTS, help="Minimum number of reports required per tool to include in comparison.")

    args = parser.parse_args()
    TOOL_CAPACITY_PATH = args.capacity_file

    # Resolve tools list
    try:
        selected_tools = _parse_tools_arg(args.tools, EXPERIMENT_FOLDERS)
    except ValueError as e:
        print(f"[ERROR] {e}")
        sys.exit(2)

    if len(selected_tools) < 2:
        print("[ERROR] --tools must include at least 2 tools (or 'all').")
        sys.exit(2)

    tool_caps_raw = load_tool_capacity()

    ALL_DATA = args.all_data
    PROV_DATA_ONLY = args.prov_data_only
    NON_PROV_DATA_ONLY = args.non_prov_data_only
    COLLAPSE = args.collapse
    MIN_REPORTS = args.min_reports

    Path(args.output_dir).mkdir(parents=True, exist_ok=True)

    ROOTS = args.roots
    GT_ROOTS = args.gt_roots

    # Set shared globals in evaluate_and_plot
    eap.tool_caps_raw = tool_caps_raw
    eap.GT_ROOTS = GT_ROOTS

    # ----------------------------
    # Load reports for selected tools (and any sub-tools)
    # ----------------------------
    tool_reports: Dict[str, List[Report]] = {}

    for tool in selected_tools:
        folder = EXPERIMENT_FOLDERS[tool]
        for exp in ROOTS:
            print(f"[INFO] Loading experiment for tool: {tool} from folder: {folder}")
            exp_path = Path(exp) / folder

            jsons = sorted(glob.glob(os.path.join(exp_path, "*.json")))
            if not jsons:
                print(f"[ERROR] No .json files found in {exp_path}")
                sys.exit(2)

            prov_check = "TRAM" if tool == "Buchel" else tool

            if len(jsons) == 1:
                try:
                    reps = read_experiment_file(jsons[0])
                except Exception as e:
                    print(f"[ERROR] Failed to read {jsons[0]}: {e.__class__.__name__}: {e}")
                    sys.exit(2)

                if ALL_DATA:
                    pass
                elif PROV_DATA_ONLY:
                    print(f"[INFO] Filtering to provenance data only for {tool}")
                    reps = [r for r in reps if r.prov == prov_check]
                elif NON_PROV_DATA_ONLY:
                    reps = [r for r in reps if (r.prov or "").casefold() != prov_check.casefold()]

                tool_reports.setdefault(tool, []).extend(reps)
                print(f"[INFO] Loaded {len(reps)} reports for {tool}")

            else:
                for jp in jsons:
                    subtool_name = parse_subtool_name(tool, jp)
                    try:
                        reps = read_experiment_file(jp)
                    except Exception as e:
                        print(f"[ERROR] Failed to read {jp}: {e.__class__.__name__}: {e}")
                        sys.exit(2)

                    if ALL_DATA:
                        pass
                    elif PROV_DATA_ONLY:
                        print(f"[INFO] Filtering to provenance data only for {tool}")
                        reps = [r for r in reps if r.prov == prov_check]
                    elif NON_PROV_DATA_ONLY:
                        reps = [r for r in reps if (r.prov or "").casefold() != prov_check.casefold()]

                    tool_reports.setdefault(subtool_name, []).extend(reps)
                    print(f"[INFO] Loaded {len(reps)} reports for {subtool_name} (from {os.path.basename(jp)})")

    # ----------------------------
    # Keep only Orbinato-MLP among Orbinato variants to simplify the plots
    # ----------------------------
    KEEP_ORBINATO_VARIANT = "Orbinato-MLP"
    if "Orbinato" in selected_tools:
        # Remove any Orbinato-* variants except Orbinato-MLP
        for k in list(tool_reports.keys()):
            if k == "Orbinato":
                # drop base if it exists; we only want the specific variant
                tool_reports.pop(k, None)
            elif k.startswith("Orbinato-") and k != KEEP_ORBINATO_VARIANT:
                tool_reports.pop(k, None)

        # If Orbinato-MLP doesn't exist, warn
        if KEEP_ORBINATO_VARIANT not in tool_reports:
            print(f"[WARN] Requested Orbinato, but {KEEP_ORBINATO_VARIANT} was not loaded; Orbinato will have no data.")

    # ----------------------------
    # Build base->variants mapping for plotting
    # ----------------------------
    base_to_variants: Dict[str, List[str]] = {}
    base_to_allreports: Dict[str, List[Report]] = {}
    for base in selected_tools:
        variants, agg = aggregate_variants(tool_reports, base)
        base_to_variants[base] = variants
        base_to_allreports[base] = agg

    # If a base tool had only subtools (no base key), ensure we still have something
    for base in selected_tools:
        if not base_to_variants[base]:
            print(f"[WARN] No reports loaded for base tool {base}")
            base_to_variants[base] = [base]
            base_to_allreports[base] = []

    # ----------------------------
    # Pairwise comparisons 
    # ----------------------------
    pairs = list(itertools.combinations(selected_tools, 2))
    if not pairs:
        print("[ERROR] Need at least two tools for pairwise comparisons.")
        sys.exit(2)

    import matplotlib.pyplot as plt

    metrics = ["precision_micro", "recall_micro", "f1_micro"]
    metric_labels = ["Precision", "Recall", "F1"]

    combined_rows: List[Dict[str, str]] = []

    def compute_shared_and_filtered_reports(toolA: str, toolB: str):
        """Return (tool_reports_pair, shared_count). Applies capacity filter if args.filter."""
        allA = base_to_allreports[toolA]
        allB = base_to_allreports[toolB]

        if args.filter:
            capA = get_cap(toolA)
            capB = get_cap(toolB)
            collapse_any = bool(args.collapse) and (
                _is_technique_only_capacity(capA) or _is_technique_only_capacity(capB)
            )

            filteredA = filter_reports_by_capacity_generous(allA, capA, capB, collapse=collapse_any)
            filteredB = filter_reports_by_capacity_generous(allB, capA, capB, collapse=collapse_any)

            final_ids = set(r.doc_id for r in filteredA) & set(r.doc_id for r in filteredB)
            shared_count = len(final_ids)

            tool_reports_pair: Dict[str, List[Report]] = {}
            for name, reps in tool_reports.items():
                tool_reports_pair[name] = [r for r in reps if r.doc_id in final_ids]
            return tool_reports_pair, shared_count

        # no filter
        idsA = {r.doc_id for r in allA}
        idsB = {r.doc_id for r in allB}
        shared_count = len(idsA & idsB)
        return {k: list(v) for k, v in tool_reports.items()}, shared_count

    def build_pair_df(toolA: str, toolB: str, tool_reports_pair: Dict[str, List[Report]], shared_count: int):
        """Compute the per-tool generous metrics DF for the plotted tools (variants included)."""
        variantsA = base_to_variants[toolA]
        variantsB = base_to_variants[toolB]

        plot_tools: List[str] = []
        for name in variantsA + variantsB:
            if name not in plot_tools:
                plot_tools.append(name)

        rows: List[Dict[str, str]] = []

        def append_rows(prefix: str, tool: str, micro: PRF, macro: PRF, n_reports: int):
            row = {
                "pair": f"{toolA}_vs_{toolB}",
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
                "number_of_reports": str(n_reports),
                "shared_reports": str(shared_count),
            }
            rows.append(row)

        for t in plot_tools:
            cap_raw = get_cap(t)
            use_latent = is_ttp_llm(t)
            collapse = bool(args.collapse) and _is_technique_only_capacity(cap_raw)
            reps_for_t = tool_reports_pair.get(t, [])

            micro, macro = generous_metrics(
                reps_for_t,
                cap_raw,
                augment_latent_tactics=use_latent,
                collapse_tech_only=collapse,
            )
            append_rows("generous", t, micro, macro, n_reports=len(reps_for_t))

        return plot_tools, pd.DataFrame(rows)

    # ---- PASS 1: decide which pairs to plot ----
    pair_jobs = []
    skipped_n = 0
    skipped_zero = 0

    for (toolA, toolB) in pairs:
        tool_reports_pair, shared_count = compute_shared_and_filtered_reports(toolA, toolB)

        if shared_count < MIN_REPORTS:
            skipped_n += 1
            continue

        plot_tools, df = build_pair_df(toolA, toolB, tool_reports_pair, shared_count)

        # skip if all metrics are zero
        zero_cols = ["precision_micro", "recall_micro", "f1_micro"]
        vals = df.loc[df["eval"] == "generous", zero_cols].astype(float).to_numpy()
        if vals.size == 0 or np.nanmax(np.abs(vals)) <= EPS:
            skipped_zero += 1
            continue

        # keep job (and write per-pair csv now)
        out_csv = f"evaluation_summary_{toolA}_vs_{toolB}.csv"
        df.to_csv(os.path.join(args.output_dir, out_csv), index=False)

        pair_jobs.append({
            "toolA": toolA,
            "toolB": toolB,
            "shared_count": shared_count,
            "plot_tools": plot_tools,
            "df": df,
        })

        # also collect for combined CSV
        combined_rows.extend(df.to_dict(orient="records"))

    if not pair_jobs:
        print(f"[WARN] No pairs met criteria (n>={MIN_REPORTS} and non-zero metrics). Nothing to plot.")
        # Still write combined CSV (will be empty)
        pd.DataFrame(combined_rows).to_csv(os.path.join(args.output_dir, "evaluation_summary_all_pairs.csv"), index=False)
        sys.exit(0)

    print(f"[INFO] Plotting {len(pair_jobs)} pairs. Skipped {skipped_n} (n<{MIN_REPORTS}), {skipped_zero} (all-zero).")

    # ---- PASS 2: allocate subplot grid sized to number of plotted pairs ----
    if len(pair_jobs) == 1:
        nrows, ncols = 1, 1
    else:
        ncols = int(np.ceil(np.sqrt(len(pair_jobs))))
        nrows = int(np.ceil(len(pair_jobs) / ncols))

    # Use the same sizing heuristic you liked for single plots, scaled to a grid.
    max_tools_in_any_subplot = max(len(job["plot_tools"]) for job in pair_jobs)
    per_subplot_w = max(3.0, max_tools_in_any_subplot - 1)
    per_subplot_h = 2.5

    figsize = (per_subplot_w * ncols, per_subplot_h * nrows)

    fig, axes = plt.subplots(nrows, ncols, figsize=figsize)
    axes_list = np.ravel(axes) if isinstance(axes, np.ndarray) else [axes]

    # ---- Plot sequentially ----
    for idx, job in enumerate(pair_jobs):
        ax = axes_list[idx]
        toolA = job["toolA"]
        toolB = job["toolB"]
        shared_count = job["shared_count"]
        plot_tools = job["plot_tools"]
        # rename Orbinato-MLP to Orbinato\n(MLP) for plot compactness
        plot_tools_names = [t if t != "Orbinato-MLP" else "Orbinato\n(MLP)" for t in plot_tools]
        df = job["df"] 

        x = np.arange(len(plot_tools))
        width = 0.25
        offsets = [-width, 0.0, width]

        for m_idx, metric in enumerate(metrics):
            vals = []
            for t in plot_tools:
                row = df[(df["tool"] == t) & (df["eval"] == "generous")].iloc[0]
                vals.append(float(row[metric]))
            ax.bar(x + offsets[m_idx], vals, width, label=metric_labels[m_idx], alpha=0.85)

        ax.set_xticks(x)
        ax.set_xticklabels(plot_tools_names, fontsize=12)
        ax.set_ylim(0, 1)
        ax.set_title(f"Comparison on {shared_count} reports", fontsize=14)

        if idx == 0:
            ax.legend(fontsize=12)
        else:
            ax.legend().set_visible(False)

    # Turn off any unused subplot axes at the end
    for j in range(len(pair_jobs), len(axes_list)):
        axes_list[j].axis("off")

    plt.tight_layout(rect=[0, 0, 1, 0.96])

    # Save combined CSV and the figure (only plotted pairs included)
    combined_df = pd.DataFrame(combined_rows)
    combined_df.to_csv(os.path.join(args.output_dir, "evaluation_summary_all_pairs.csv"), index=False)

    tools_slug = "_".join(selected_tools) if len(selected_tools) <= 4 else f"{len(selected_tools)}tools"
    fig_path = os.path.join(args.output_dir, f"tool_comparison_pairs_{tools_slug}.pdf")
    plt.savefig(fig_path, dpi=300, bbox_inches="tight")
    plt.savefig(str(Path(fig_path).with_suffix(".png")), dpi=180, bbox_inches="tight")
    plt.close(fig)



if __name__ == "__main__":
    main()

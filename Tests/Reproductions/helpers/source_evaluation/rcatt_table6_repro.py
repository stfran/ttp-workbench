# run rcATT on the training set and evaluate it micro and macro precision/recall/F0.5
# then re-run with the adapter and compare results
# therefore we need two separate virtual environments, one that supports rcATT and one that supports the adapter

import argparse
from pathlib import Path
import pandas as pd
import re
from tqdm import tqdm
from typing import List, Set, Tuple
import subprocess

# from rcATT lines in table 6 Legoy, V. Automated Retrieval of ATT&CK Tactics and Techniques for Cyber Threat Reports. 2020
original_results_tactics = {
    "micro_precision": 0.7931,
    "micro_recall": 0.1220,
    "micro_f0.5": 0.3775,
    "macro_precision": 0.8173,
    "macro_recall": 0.0821,
    "macro_f0.5": 0.2323,
}

original_results_techniques = {
    "micro_precision": 0.7222,
    "micro_recall": 0.0207,
    "micro_f0.5": 0.0930,
    "macro_precision": 0.2060,
    "macro_recall": 0.0433,
    "macro_f0.5": 0.1011,
}

_RE_TACTIC = re.compile(r"^TA\d{4}$")  # e.g. TA0004
_RE_TECHNIQUE = re.compile(r"^T\d{4}(?:\.\d{3})?$")  # e.g. T1059 or T1059.003


def parse_cli():
    parser = argparse.ArgumentParser(
        description="Run rcATT on the training set and evaluate it (micro/macro precision/recall/F0.5)."
    )
    parser.add_argument(
        "--data-path",
        type=Path,
        default=Path(
            "unfetter_wiki_preprocessed.csv"
        ),
        help="Path to the training data",
    )
    parser.add_argument(
        "--output-path",
        type=Path,
        default=Path("."),
        help="Path to save the results",
    )
    parser.add_argument("--original", action="store_true", help="Use original rcATT without adapter")
    parser.add_argument("--adapter", action="store_true", help="Use rcATT with adapter")

    return parser, parser.parse_args()


def evaluate_results(predictions_list: List[Set[str]], ground_truth_list: List[Set[str]]):
    """Row-wise multi-label evaluation.

    Args:
        predictions_list: list of per-row predicted TTP code sets
        ground_truth_list: list of per-row ground-truth TTP code sets

    Returns:
        dict with micro- and macro- (label-wise) precision/recall/F0.5
    """
    assert len(predictions_list) == len(ground_truth_list), "Predictions and GT must be same length"

    # ---------------- Micro (aggregate TP/FP/FN across all rows & labels) ----------------
    tp = fp = fn = 0
    for p, g in zip(predictions_list, ground_truth_list):
        tp += len(p & g)
        fp += len(p - g)
        fn += len(g - p)

    micro_precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    micro_recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    micro_f0_5 = (
        (1.25 * micro_precision * micro_recall) / (0.25 * micro_precision + micro_recall)
        if (micro_precision + micro_recall) > 0
        else 0.0
    )

    # ---------------- Macro (label-wise: compute PRF for each label over all rows) ----------------
    all_labels: Set[str] = set()
    for s in predictions_list:
        all_labels |= s
    for s in ground_truth_list:
        all_labels |= s

    macro_precisions = []
    macro_recalls = []
    macro_f0_5s = []

    for lbl in sorted(all_labels):
        tp_l = fp_l = fn_l = 0
        for p, g in zip(predictions_list, ground_truth_list):
            in_p = lbl in p
            in_g = lbl in g
            if in_p and in_g:
                tp_l += 1
            elif in_p and not in_g:
                fp_l += 1
            elif (not in_p) and in_g:
                fn_l += 1
        prec_l = tp_l / (tp_l + fp_l) if (tp_l + fp_l) > 0 else 0.0
        rec_l = tp_l / (tp_l + fn_l) if (tp_l + fn_l) > 0 else 0.0
        f_l = (1.25 * prec_l * rec_l) / (0.25 * prec_l + rec_l) if (prec_l + rec_l) > 0 else 0.0

        macro_precisions.append(prec_l)
        macro_recalls.append(rec_l)
        macro_f0_5s.append(f_l)

    macro_precision = sum(macro_precisions) / len(macro_precisions) if macro_precisions else 0.0
    macro_recall = sum(macro_recalls) / len(macro_recalls) if macro_recalls else 0.0
    macro_f0_5 = sum(macro_f0_5s) / len(macro_f0_5s) if macro_f0_5s else 0.0

    return {
        "micro_precision": micro_precision,
        "micro_recall": micro_recall,
        "micro_f0.5": micro_f0_5,
        "macro_precision": macro_precision,
        "macro_recall": macro_recall,
        "macro_f0.5": macro_f0_5,
    }


# ------------------------------
# Helpers to format GT & split TTPs
# ------------------------------

def _split_tactics_techniques_from_set(s: Set[str]) -> Tuple[Set[str], Set[str]]:
    tactics, techniques = set(), set()
    for code in s:
        if _RE_TACTIC.match(code):
            tactics.add(code)
        elif _RE_TECHNIQUE.match(code):
            techniques.add(code)
        # silently ignore anything that doesn't look like a T/TA code
    return tactics, techniques


def build_ground_truth_lists_and_splits(
    df: pd.DataFrame,
) -> Tuple[List[Set[str]], List[Set[str]], List[Set[str]]]:
    """Return (gt_per_row, gt_tactics_per_row, gt_techniques_per_row), aligned to df order."""
    ttp_columns = [c for c in df.columns if c != "Text"]

    gt_per_row: List[Set[str]] = []
    gt_tactics_per_row: List[Set[str]] = []
    gt_techniques_per_row: List[Set[str]] = []

    for _, row in df.iterrows():
        s: Set[str] = set()
        for col in ttp_columns:
            val = row[col]
            try:
                # treat any nonzero numeric as True
                if pd.notna(val) and float(val) != 0.0:
                    s.add(col)
            except Exception:
                if bool(val):
                    s.add(col)

        gt_per_row.append(s)
        t, k = _split_tactics_techniques_from_set(s)
        gt_tactics_per_row.append(t)
        gt_techniques_per_row.append(k)

    return gt_per_row, gt_tactics_per_row, gt_techniques_per_row


# ------------------------------
# rcATT (original) runner
# ------------------------------
def _safe_write_input_bytes(text: str, dest: Path) -> int:
    """Write text -> dest using robust binary path and verify non-zero size.
    Returns final file size. Raises on failure.
    """

    import os
    # Try a few encodings; replace unencodable chars rather than drop everything.
    for enc in ("ISO-8859-1", "cp1252", "utf-8"):
        try:
            data = text.encode(enc, errors="replace")
            break
        except Exception:
            data = None
    if data is None:
        data = text.encode("utf-8", errors="replace")

    with open(dest, "wb") as f:
        n = f.write(data)
        f.flush()
        os.fsync(f.fileno())

    try:
        os.chmod(dest, 0o644)
    except Exception:
        pass

    size = dest.stat().st_size if dest.exists() else -1
    if n == 0 or size == 0:
        raise RuntimeError(f"Wrote {n} bytes but size {size} for {dest}")
    return size


def run_rcatt(data_path: Path):
    import rcatt_ttp_map
    import json, sys, subprocess, tempfile, os

    rcatt_dir = Path("../../../Methods/Hybrid/rcATT/rcATT-f82f7fd").resolve()
    rcatt_cmd = rcatt_dir / "rcATT_cmd.py"
    if not rcatt_cmd.exists():
        raise FileNotFoundError(f"rcATT_cmd.py not found at {rcatt_cmd}")

    df = pd.read_csv(data_path)
    texts = df["Text"].astype(str).tolist()

    # Build GT aligned with df
    _, gt_tactics_rows, gt_tech_rows = build_ground_truth_lists_and_splits(df)

    predictions: List[Set[str]] = []

    for i, text in enumerate(tqdm(texts, desc="Running rcATT original")):
        # Make a durable filename inside rcATT dir (avoid NamedTemporaryFile surprises)
        fd, in_path_str = tempfile.mkstemp(prefix=f"in_{i}_", suffix=".txt", dir=str(rcatt_dir))
        os.close(fd)  # we'll reopen with our own flags
        in_path = Path(in_path_str)
        out_path = rcatt_dir / (in_path.stem + "_out.json")

        # Write & verify
        size = _safe_write_input_bytes(text, in_path)
        # Extra assertion for your current debugging
        st = in_path.stat()
        if st.st_size != size:
            raise RuntimeError(f"Stat size mismatch for {in_path}: wrote {size}, stat {st.st_size}")

        cmd = [sys.executable, str(rcatt_cmd), "-p", "-i", str(in_path), "-o", str(out_path)]
        cp = subprocess.run(cmd, cwd=rcatt_dir, capture_output=True, text=True)
        if cp.returncode != 0:
            # Re-check visibility and log a helpful hint
            exists_now = in_path.exists()
            sz_now = in_path.stat().st_size if exists_now else -1
            raise RuntimeError(
                f"rcATT failed at idx={i}\n"
                f"cmd: {' '.join(cmd)}\n"
                f"cwd: {rcatt_dir}\n"
                f"input_exists: {exists_now}, size: {sz_now}\n"
                f"stderr:\n{cp.stderr}\nstdout:\n{cp.stdout}\n"
            )

        if not out_path.exists():
            raise RuntimeError(f"rcATT produced no output at {out_path} (idx={i}).")

        with open(out_path, "r", encoding="utf-8", errors="ignore") as f:
            data = json.load(f)

        object_refs = data.get("object_refs", [])
        predicted_ttps: Set[str] = set()
        for ref in object_refs:
            if ref in rcatt_ttp_map.STIX_IDENTIFIERS:
                j = rcatt_ttp_map.STIX_IDENTIFIERS.index(ref)
                predicted_ttps.add(rcatt_ttp_map.ALL_TTPS[j])
        predictions.append(predicted_ttps)

        # cleanup
        try: in_path.unlink()
        except FileNotFoundError: pass
        try: out_path.unlink()
        except FileNotFoundError: pass

    # Split predictions row-wise
    pred_tactics_rows, pred_tech_rows = [], []
    for s in predictions:
        t_set, k_set = _split_tactics_techniques_from_set(s)
        pred_tactics_rows.append(t_set)
        pred_tech_rows.append(k_set)

    reproduced_results_tactics = evaluate_results(pred_tactics_rows, gt_tactics_rows)
    reproduced_results_techniques = evaluate_results(pred_tech_rows, gt_tech_rows)

    return reproduced_results_tactics, reproduced_results_techniques

# ------------------------------
# rcATT (adapter) runner
# ------------------------------

def run_rcatt_with_adapter(data_path: Path):
    # use the adapter to run rcATT and evaluate
    from Framework.adapters.rcatt_adapter import predict_texts

    df = pd.read_csv(data_path)
    texts = df["Text"].tolist()

    # Ground truth (aligned with df order)
    _, gt_tactics_rows, gt_tech_rows = build_ground_truth_lists_and_splits(df)

    # Adapter predictions
    results, _ = predict_texts(texts, ids=[str(i) for i in range(len(texts))])

    predictions: List[Set[str]] = []
    for result in results:
        predicted_ttps = set(result.get("ttps", []))
        predictions.append(predicted_ttps)

    # Split predictions row-wise into tactics/techniques
    pred_tactics_rows: List[Set[str]] = []
    pred_tech_rows: List[Set[str]] = []
    for s in predictions:
        t, k = _split_tactics_techniques_from_set(s)
        pred_tactics_rows.append(t)
        pred_tech_rows.append(k)

    print(len(pred_tactics_rows), len(gt_tactics_rows))

    adapter_results_tactics = evaluate_results(pred_tactics_rows, gt_tactics_rows)
    adapter_results_techniques = evaluate_results(pred_tech_rows, gt_tech_rows)

    return adapter_results_tactics, adapter_results_techniques


# ------------------------------
# CLI entry
# ------------------------------

def main():
    parser, args = parse_cli()

    if args.original == args.adapter:
        parser.print_help()
        raise ValueError("Please specify either --original or --adapter")

    if not args.data_path.exists():
        parser.print_help()
        print(f"\n[ERROR] Data path {args.data_path} does not exist. HINT: run get_unfetter_data.py")
        raise FileNotFoundError(f"Data path {args.data_path} does not exist")

    if args.original:
        results_tactics, results_techniques = run_rcatt(args.data_path)
    else:  # args.adapter
        results_tactics, results_techniques = run_rcatt_with_adapter(args.data_path)

    # Save results
    output_file = args.output_path / (
        "rcatt_reproduced_results.txt" if args.original else "rcatt_adapter_results.txt"
    )
    with open(output_file, "w") as f:
        f.write("Tactics Results:\n")
        for key, value in results_tactics.items():
            f.write(f"{key}: {value}\n")
        f.write("\nTechniques Results:\n")
        for key, value in results_techniques.items():
            f.write(f"{key}: {value}\n")

    print(f"Results saved to {output_file}")


if __name__ == "__main__":
    main()

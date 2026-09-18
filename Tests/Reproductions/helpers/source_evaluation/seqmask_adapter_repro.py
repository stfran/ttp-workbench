from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Dict, List, Tuple, Any

import numpy as np
import pandas as pd

from sklearn.metrics import precision_recall_fscore_support


PROJ_ROOT = Path(__file__).resolve().parent.parent.parent.parent

DATA_PATH_MAP = {
    "7": PROJ_ROOT / "Datasets" / "prior_work_data" / "SeqMask" / "data_origin13.csv",        # ATT&CK sentence dataset
    "14": PROJ_ROOT / "Datasets" / "prior_work_data" / "SeqMask" / "TTPDrill-subTTP.csv",   # TTPDrill sentence dataset
    "15": PROJ_ROOT / "Datasets" / "prior_work_data" / "SeqMask" / "data_origin4.csv",      # rcATT dataset (paper treats as sentence)
}

TACT_MODELS = [
    "self_attention",
    "sv_mask",
    "mp_mask",
    "ar_mask",
]
TECH_MODELS = [
    "self_attention",
    "sv_mask",
    "mp_mask",
    "ar_mask",
]


def import_data(table: str) -> pd.DataFrame:
    path = DATA_PATH_MAP.get(table)
    if path is None or not path.exists():
        raise ValueError(f"Data for table {table} not found at: {path}")
    df = pd.read_csv(path)
    return df


def extract_text_and_labels(df: pd.DataFrame, table: str) -> Tuple[List[str], List[List[str]]]:
    """
    Returns:
      texts: list[str]
      y_true_codes: list[list[str]] (list of label codes per record)

    Table handling:
      - Table 7 / 15: prefer 'processed' if present (SeqMask-ready single-string input),
        otherwise fall back to 'Text' or 'text'. Labels are one-hot across TA*/T* columns.
      - Table 14: text column + a label column holding technique code(s).
    """
    def is_code(c: str) -> bool:
        c = str(c)
        return (c.startswith("T") and len(c) >= 5) or (c.startswith("TA") and len(c) == 6)

    if table in ("7", "15"):
        # Prefer the SeqMask-provided preprocessed field if present.
        if "processed" in df.columns:
            text_col = "processed"
        elif "Text" in df.columns:
            text_col = "Text"
        elif "text" in df.columns:
            text_col = "text"
        else:
            raise ValueError(
                f"Could not find a usable text column for table {table}. "
                f"Expected one of: processed, Text, text. Columns: {list(df.columns)[:30]}"
            )

        texts = df[text_col].fillna("").astype(str).tolist()

        # One-hot label columns: keep only ATT&CK-like columns, drop obvious non-label cols.
        non_label = {text_col, "words num", "words_num", "Words num", "processed",
                     "id", "ID", "Id", "label", "Label"}
        label_cols = [c for c in df.columns if c not in non_label and is_code(c)]

        if not label_cols:
            raise ValueError(
                f"No label columns detected for table {table}. "
                f"Expected one-hot columns with names like TA0001 or T1059. Columns: {list(df.columns)[:30]}"
            )

        y_true_codes: List[List[str]] = []
        # Use vectorized-ish iteration but keep it simple and robust15
        for _, row in df[label_cols].iterrows():
            active = []
            for col in label_cols:
                v = row[col]
                try:
                    if int(v) == 1:
                        active.append(col)
                except Exception:
                    # tolerate blanks/NaN
                    continue
            y_true_codes.append(active)

        return texts, y_true_codes

    if table == "14":
        # Text column
        text_col = "text" if "text" in df.columns else ("Text" if "Text" in df.columns else None)
        if not text_col:
            raise ValueError(
                f"Could not find text column in table 14 data. "
                f"Expected 'text' or 'Text'. Columns: {list(df.columns)[:30]}"
            )
        texts = df[text_col].fillna("").astype(str).tolist()

        # Label column (technique code(s))
        label_col = None
        for c in ["id", "ID", "technique", "Technique", "label", "Label", "ttp", "TTP"]:
            if c in df.columns:
                label_col = c
                break
        if label_col is None:
            raise ValueError(
                f"Could not find label column for table 14. "
                f"Tried: id/ID/technique/label/ttp. Columns: {list(df.columns)[:30]}"
            )

        # Split codes on common delimiters
        import re
        def split_codes(s: str) -> List[str]:
            parts = [p.strip() for p in re.split(r"[;,|\s]+", s.strip()) if p.strip()]
            return [p for p in parts if p.startswith("T")]

        y_true_codes: List[List[str]] = [split_codes(v) for v in df[label_col].fillna("").astype(str).tolist()]
        return texts, y_true_codes

    raise ValueError(f"Unknown table: {table}")



def re_split(s: str) -> List[str]:
    import re
    # split on comma/semicolon/pipe/space when code-like, but keep simple
    return re.split(r"[;,|\s]+", s.strip())


def run_seqmask_adapter(
    texts: List[str],
    ids: List[str],
    *,
    model_key: str,
    out_dir: Path,
    engine: str,
    use_gpus: bool,
    bulk: bool,
    verbose: bool,
) -> List[Dict[str, Any]]:
    """
    Calls your SeqMask adapter in reproduction mode, selecting specific model paths.
    Assumes your adapter supports model selection args (table not needed here).
    """
    from Framework.adapters.seqmask_adapter import predict_texts

    out_dir.mkdir(parents=True, exist_ok=True)

    # IMPORTANT: reproduction mode (no sentence tokenize confirm)
    results, _ = predict_texts(
        texts=texts,
        ids=ids,
        save_dir=out_dir,
        mode="reproduction",
        tact_model=model_key,
        tech_model=model_key,
        bulk=bulk,
        engine=engine,
        use_gpus=use_gpus,
        verbose=verbose,
        prefix=f"seqmask_{model_key}",
    )
    return results


def run_rcatt_adapter(
    texts: List[str],
    ids: List[str],
    *,
    out_dir: Path,
    engine: str,
    verbose: bool,
) -> List[Dict[str, Any]]:
    from Framework.adapters.rcatt_adapter import predict_texts
    
    # check for intermediate results and start with that
    intermediate_files = list(out_dir.glob("rcatt_intermediate_*.json"))

    if intermediate_files:
        print(f"[rcATT] Found {len(intermediate_files)} intermediate result files in {out_dir}, "
              f"skipping to avoid re-processing.")
        all_results: List[Dict[str, Any]] = []
        for fp in sorted(intermediate_files):
            with open(fp, "r", encoding="utf-8") as f:
                batch_results = json.load(f)
                all_results.extend(batch_results)
    else:
        all_results: List[Dict[str, Any]] = []

        out_dir.mkdir(parents=True, exist_ok=True)

    batch_size = 100

    
    for i in range(0, len(texts), batch_size):
        batch_texts = texts[i:i+batch_size]
        batch_ids = ids[i:i+batch_size]
        print(f"[rcATT] Processing batch {i // batch_size + 1} / {(len(texts) + batch_size - 1) // batch_size} "
                f"({len(batch_texts)} samples)...")
        batch_results, _ = predict_texts(
            texts=batch_texts,
            ids=batch_ids,
            save_dir=out_dir,
            engine=engine,
            verbose=verbose,
            prefix=f"rcatt",
        )
        all_results.extend(batch_results)

        # detect errors and stop the run
        for res in batch_results:
            if res.get("error") is not None:
                raise RuntimeError(f"Error detected in rcATT batch results. {res}")

        with open(out_dir / f"rcatt_intermediate_{i // batch_size + 1}.json", "w", encoding="utf-8") as f:
            json.dump(batch_results, f, indent=2)

    return all_results

def load_seqmask_preds(
    ids: List[str],
    model_out_dir: Path,
    *,
    tau: float,
    top_k: int,
    allow_tactics: bool,
) -> Tuple[List[List[str]], int]:
    """
    Load SeqMask raw outputs written by adapter/CLI (per-id JSON files)
    and convert them into predicted code lists using tau/top-k policy.
    Returns (y_pred_codes, missing_count)
    """
    y_pred_codes: List[List[str]] = []
    missing = 0

    for rec_id in ids:
        fp = model_out_dir / f"seqmask_raw_{rec_id}.json"
        if not fp.exists():
            missing += 1
            y_pred_codes.append([])
            continue

        raw = json.loads(fp.read_text(encoding="utf-8"))
        merged: Dict[str, Any] = {}
        merged.update(raw.get("total_tactics") or {})
        merged.update(raw.get("total_techniques") or {})
        if rec_id == "row_000000":
            items = sorted(((k, float(v)) for k, v in merged.items()), key=lambda kv: kv[1], reverse=True)
            print("DEBUG top10 scores:", items[:10])

        y_pred_codes.append(
            scores_to_pred_codes(
                merged,
                tau=tau,
                top_k=top_k,
                allow_tactics=allow_tactics,
            )
        )

    return y_pred_codes, missing


def load_rcatt_preds(
    ids: List[str],
    rcatt_out_dir: Path,
    *,
    allow_tactics: bool,
) -> Tuple[List[List[str]], int]:
    """
    Load rcATT raw outputs (rcatt_raw_<rec_id>.json), parse them via the adapter's extract_ttps(),
    and return a per-record list of predicted codes.

    Returns:
      y_pred_codes: List[List[str]]  (len == len(ids))
      missing: int
    """
    from Framework.adapters.rcatt_adapter import RcATTAdapter

    adapter = RcATTAdapter()
    y_pred_codes: List[List[str]] = []
    missing = 0

    for rec_id in ids:
        fp = rcatt_out_dir / f"rcatt_raw_{rec_id}.json"
        if not fp.exists():
            missing += 1
            y_pred_codes.append([])
            continue

        raw = json.loads(fp.read_text(encoding="utf-8"))

        ttps = adapter.extract_ttps(raw)
        if ttps is None:
            y_pred_codes.append([])
            continue

        # Filter tactics if desired
        if not allow_tactics:
            codes = [c for c in ttps if not str(c).startswith("TA")]
        else:
            codes = list(ttps)

        y_pred_codes.append(codes)

    return y_pred_codes, missing


def scores_to_pred_codes(
    score_dict: Dict[str, Any],
    *,
    tau: float,
    top_k: int,
    allow_tactics: bool,
) -> List[str]:
    """
    Convert score dict -> predicted codes.
    - tau: minimum score
    - top_k: keep only top_k (after filtering tau), 0 => no top-k (tau-only)
    """
    items = []
    for k, v in (score_dict or {}).items():
        if not allow_tactics and str(k).startswith("TA"):
            continue
        try:
            items.append((str(k), float(v)))
        except Exception:
            continue

    # tau filter
    items = [(k, s) for k, s in items if s >= tau]
    items.sort(key=lambda kv: kv[1], reverse=True)

    if top_k and top_k > 0:
        items = items[:top_k]

    return [k for k, _ in items]


def evaluate_predictions(
    y_true: List[List[str]],
    y_pred: List[List[str]],
    *,
    label_space: List[str],
) -> Dict[str, float]:
    """
    Compute micro/macro P/R/F1 over a fixed label space.
    """
    idx = {c: i for i, c in enumerate(label_space)}
    Yt = np.zeros((len(y_true), len(label_space)), dtype=int)
    Yp = np.zeros((len(y_pred), len(label_space)), dtype=int)

    for i, codes in enumerate(y_true):
        for c in codes:
            j = idx.get(c)
            if j is not None:
                Yt[i, j] = 1
    for i, codes in enumerate(y_pred):
        for c in codes:
            j = idx.get(c)
            if j is not None:
                Yp[i, j] = 1

    # sklearn for multilabel
    p_micro, r_micro, f_micro, _ = precision_recall_fscore_support(
        Yt, Yp, average="micro", zero_division=0
    )
    p_macro, r_macro, f_macro, _ = precision_recall_fscore_support(
        Yt, Yp, average="macro", zero_division=0
    )
    return {
        "p_micro": float(p_micro),
        "r_micro": float(r_micro),
        "f_micro": float(f_micro),
        "p_macro": float(p_macro),
        "r_macro": float(r_macro),
        "f_macro": float(f_macro),
    }


def parse_args():
    ap = argparse.ArgumentParser()
    ap.add_argument("--table", choices=["7", "14", "15"], default="7")
    ap.add_argument("--models", nargs="+",
                    default=["self_attention", "sv_mask", "mp_mask", "ar_mask"],
                    choices=list(TECH_MODELS))
    ap.add_argument("--out_root", type=str,
                    default=str(PROJ_ROOT / "Tests" / "reproductions" / "seqmask" / "results"))
    ap.add_argument("--engine", type=str, default="podman", choices=["docker", "podman"])
    ap.add_argument("--use_gpus", action="store_true")
    ap.add_argument("--bulk", action="store_true")
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument('--rcatt', action='store_true')
    ap.add_argument('--seqmask', action='store_true')

    # decision policy for eval (since paper doesn't specify explicit threshold)
    ap.add_argument("--tau", type=float, default=0.5,
                    help="Accept labels with prob >= tau (applied before top-k).")
    ap.add_argument("--top_k", type=int, default=10,
                    help="If >0, keep only top_k labels per sample after tau filtering.")
    ap.add_argument("--allow_tactics", action="store_true",
                    help="If set, include TAxxxx labels in evaluation label space & predictions.")
    ap.add_argument("--eval_only", action="store_true",
                    help="Only evaluate existing raw outputs (do not run adapter).")

    return ap.parse_args()


def main():
    args = parse_args()

    # Default behavior: if neither flag is set, run SeqMask eval path
    if not args.seqmask and not args.rcatt:
        args.seqmask = True

    df = import_data(args.table)
    texts, y_true_codes = extract_text_and_labels(df, args.table)

    # stable ids
    ids = [f"row_{i:06d}" for i in range(len(texts))]

    out_root = Path(args.out_root)
    out_root.mkdir(parents=True, exist_ok=True)

    # label space derived from GT
    label_space = sorted({c for row in y_true_codes for c in row if args.allow_tactics or not c.startswith("TA")})
    if not label_space:
        raise ValueError("Empty label space from ground truth; check parsing.")

    rows = []

    # Prepare tool+model jobs
    jobs: List[Tuple[str, str]] = []
    if args.rcatt:
        jobs.append(("rcatt", "rcatt"))
    if args.seqmask:
        for mk in args.models:
            jobs.append(("seqmask", mk))
    

    # Where each job writes/reads outputs
    def job_out_dir(tool: str, model: str) -> Path:
        if tool == "seqmask":
            return out_root / f"table_{args.table}" / model
        return out_root / f"table_{args.table}" / "rcatt"

    # Run jobs (unless eval_only)
    for tool, model in jobs:
        out_dir = job_out_dir(tool, model)
        out_dir.mkdir(parents=True, exist_ok=True)

        if args.eval_only:
            continue

        if tool == "seqmask":
            _ = run_seqmask_adapter(
                texts=texts,
                ids=ids,
                model_key=model,
                out_dir=out_dir,
                engine=args.engine,
                use_gpus=args.use_gpus,
                bulk=args.bulk,
                verbose=args.verbose,
            )
        elif tool == "rcatt":
            _ = run_rcatt_adapter(
                texts=texts,
                ids=ids,
                out_dir=out_dir,
                engine=args.engine,
                verbose=args.verbose,
            )

    # Evaluate jobs
    for tool, model in jobs:
        out_dir = job_out_dir(tool, model)

        if tool == "seqmask":
            y_pred_codes, missing = load_seqmask_preds(
                ids,
                out_dir,
                tau=args.tau,
                top_k=args.top_k,
                allow_tactics=args.allow_tactics,
            )
            tau_val = args.tau
            topk_val = args.top_k

        elif tool == "rcatt":
            y_pred_codes, missing = load_rcatt_preds(
                ids,
                out_dir,
                allow_tactics=args.allow_tactics,
            )
            tau_val = None
            topk_val = None

        lens = [len(x) for x in y_pred_codes]
        print(f"[{tool}:{model}] avg preds/sample={sum(lens)/len(lens):.2f} "
              f"min={min(lens)} max={max(lens)} missing={missing}")

        metrics = evaluate_predictions(y_true_codes, y_pred_codes, label_space=label_space)

        rows.append({
            "tool": tool,
            "table": args.table,
            "model": model,
            "tau": tau_val,
            "top_k": topk_val,
            "allow_tactics": bool(args.allow_tactics),
            "missing_outputs": missing,
            **metrics,
        })

    # Write one combined table
    out_csv = out_root / f"table_{args.table}_summary.csv"
    pd.DataFrame(rows).to_csv(out_csv, index=False)
    print(f"[OK] Wrote summary: {out_csv}")
    print(pd.DataFrame(rows))


if __name__ == "__main__":
    main()

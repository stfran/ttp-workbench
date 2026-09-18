#!/usr/bin/env python3
"""
Build strict & generous tables for RAF-AG and AttacKG across:
  - Paper (from CSV)
  - Reproduction WITHOUT framework (native campaign outputs)
  - Reproduction WITH framework (adapter raw outputs)

Inputs (defaults assume repo layout described in the prompt):
  - Paper counts CSV (RAF-AG columns required; AttacKG optional)
  - Ground-truth JSON (labels grouped as in your existing validator)
  - Text directory with the original CTI .txt files (for fuzzy aligning names)
  - Four result locations:
      * AttacKG WITH adapter:      ./attackg_raw_results
      * RAF-AG  WITH adapter:      ./rafag_raw_results
      * RAF-AG  WITHOUT adapter:   Methods/.../data/campaign/decoding_result
      * AttacKG WITHOUT adapter:   Methods/.../data/campaign/attackg_result

Outputs:
  - raf_ag_validation_summary_new.csv
  - raf_ag_validation_summary_new.strict.table.tex
  - raf_ag_validation_summary_new.generous.table.tex
"""

from __future__ import annotations
from pathlib import Path
from collections import OrderedDict
from typing import Dict, Tuple, Optional, Set, List, Any
import json, re, difflib

import pandas as pd
import numpy as np

# ----------------- Paths (edit if you need) -----------------
PROJECT_ROOT = Path(__file__).parent.parent.parent.parent.resolve()

PAPER_CSV =  Path("vetting/raf_ag_paper_counts.csv")
GROUND_TRUTH_JSON = Path("vetting/ground_truth_labels.json")
TEXT_DIR = PROJECT_ROOT / "Methods/Graph/RAF-AG/RAF-AG-221bfc23f6cf9626d0da7e4e752dc88f3a2c5960/Dataset/CTI reports"

RAFAG_CAMPAIGN_DECODING = PROJECT_ROOT / "Methods/Graph/RAF-AG/RAF-AG-221bfc23f6cf9626d0da7e4e752dc88f3a2c5960/data/campaign/decoding_result"
ATTACKG_CAMPAIGN_RESULT = PROJECT_ROOT / "Methods/Graph/RAF-AG/RAF-AG-221bfc23f6cf9626d0da7e4e752dc88f3a2c5960/data/campaign/attackg_result"

RAFAG_ADAPTER_RAW = Path("rafag_raw_results")       # dir of JSON payloads (as emitted/saved by adapter run)
ATTACKG_ADAPTER_RAW = Path("attackg_raw_results")    # dir of JSON payloads (as emitted/saved by adapter run)

RESULTS =  Path("raf_ag_validation_summary_new.csv")    

# ----------------- Regex & helpers (ported from validator) -----------------
TACTIC_RE    = re.compile(r"^TA\d{4}$", re.I)   # validate_rafag_adapter.py
TECHNIQUE_RE = re.compile(r"^T\d{4}$", re.I)
SUBTECH_RE   = re.compile(r"^T\d{4}\.\d{3}$", re.I)

def _parent(code: str) -> str:
    return code.split(".", 1)[0].upper()

def _is_tech_like(c: str) -> bool:
    c = (c or "").upper()
    return bool(SUBTECH_RE.match(c) or TECHNIQUE_RE.match(c))

SEP_RE = re.compile(r"[,\s]*[,/][,\s]*")  # same as validator

def _split_group(label: str) -> List[str]:
    parts = [x.strip().upper() for x in SEP_RE.split(label or "") if x.strip()]
    return [p for p in parts if _is_tech_like(p)]

def _best_txt_for_report(text_dir: Path, report: str) -> Optional[Path]:
    files = sorted(text_dir.glob("*.txt"))
    if not files:
        return None
    stems = [f.stem for f in files]
    hit = difflib.get_close_matches(str(report).lower(), [s.lower() for s in stems], n=1, cutoff=0.6)
    if not hit:
        return None
    target = hit[0]
    for f in files:
        if f.stem.lower() == target:
            return f
    return None
# (Reused from validator)

def _build_gt_groups_and_tactics(raw_labels: List[str]):
    """
    Produce (gt_groups, gt_tactics) where gt_groups are technique/subtech cells possibly with grouped codes.
    Ported from validator generous logic. 
    """
    gt_groups = []
    gt_tactics = set()
    for item in (raw_labels or []):
        u = (item or "").strip().upper()
        if TACTIC_RE.match(u):
            gt_tactics.add(u)
            continue
        codes = _split_group(u) if ("," in u or "/" in u) else ([u] if _is_tech_like(u) else [])
        if codes:
            gt_groups.append({"codes": codes, "parents": {_parent(c) for c in codes}})
    return gt_groups, gt_tactics
# Matches validator semantics

def _count_matches(preds: Set[str], gt_groups: List[dict], gt_tactics: Set[str], *, tactic_fallback: bool) -> Tuple[int,int,int]:
    """
    Generous, group-aware matching with optional tactic fallback (ported).
    """
    from helpers.optional_scoring import frozen_ttp_details as get_ttp_details  # captured lookup; no refresh during reproduction
    preds = [p.upper() for p in (preds or []) if _is_tech_like(p)]
    matched_group = [False] * len(gt_groups)
    tp = 0
    used_preds = set()

    for p in preds:
        if p in used_preds:
            continue
        pp = _parent(p)
        found = False
        # 1) direct OR same-parent group match
        for i, g in enumerate(gt_groups):
            if matched_group[i]:
                continue
            if p in g["codes"] or pp in g["parents"]:
                matched_group[i] = True
                used_preds.add(p)
                tp += 1
                found = True
                break
        if found:
            continue
        # 2) tactic fallback
        if tactic_fallback and gt_tactics:
            try:
                details = get_ttp_details(p)
                t_codes = [str(x).upper() for x in details.get("tactic_codes", [])]
            except Exception:
                t_codes = []
            if any(t in gt_tactics for t in t_codes):
                used_preds.add(p)
                tp += 1
                continue

    fp = len([p for p in preds if p not in used_preds])
    fn = len([1 for m in matched_group if not m])
    return tp, fp, fn
# Port faithfully

# ----------------- Metric utilities (safe with Series) -----------------
def _safe_div(num, den):
    if isinstance(num, (pd.Series, pd.Index)) or isinstance(den, (pd.Series, pd.Index)):
        num = pd.to_numeric(num, errors="coerce")
        den = pd.to_numeric(den, errors="coerce")
        out = num.divide(den)
        return out.replace([np.inf, -np.inf], 0.0).fillna(0.0)
    try:
        den_f = float(den)
    except Exception:
        return 0.0
    return float(num) / den_f if den_f != 0.0 else 0.0

def _prf(tp: float, fp: float, fn: float) -> Tuple[float, float, float]:
    p = _safe_div(tp, tp + fp)
    r = _safe_div(tp, tp + fn)
    f1 = (2 * p * r) / (p + r) if (p + r) else 0.0
    return p, r, f1

# ----------------- Load paper & GT -----------------
def _load_paper_and_gt(paper_csv: Path, gt_json: Path):
    paper = pd.read_csv(paper_csv, encoding="utf-8-sig")
    paper["report"] = paper["report"].astype(str).str.strip()
    with open(gt_json, "r", encoding="utf-8") as f:
        gt_records = json.load(f)

    gt_by_txt  = {rec.get("txt file", ""): list((rec.get("labels") or [])) for rec in gt_records}
    gt_by_stem = {Path(k).stem.lower(): list(v) for k, v in gt_by_txt.items()}
    return paper, gt_by_stem

def _labels_for_report(report: str, text_dir: Path, gt_by_stem: Dict[str, List[str]]) -> List[str]:
    txt_path = _best_txt_for_report(text_dir, report)
    if txt_path is not None:
        hit = gt_by_stem.get(txt_path.stem.lower())
        if hit is not None:
            return sorted({c.upper() for c in hit})
    # fuzzy against GT stems
    if gt_by_stem:
        all_stems = list(gt_by_stem.keys())
        h = difflib.get_close_matches(str(report).lower(), all_stems, n=1, cutoff=0.5)
        if h:
            return sorted({c.upper() for c in gt_by_stem.get(h[0], [])})
    return []

# ----------------- Native campaign parsers (reused semantics) -----------------
def _find_json_for_stem(pred_dir: Path, stem: str) -> Optional[Path]:
    exact = pred_dir / f"{stem}.json"
    if exact.exists():
        return exact
    cand = sorted(pred_dir.glob("*.json"))
    hits = difflib.get_close_matches(stem.lower(), [c.stem.lower() for c in cand], n=1, cutoff=0.6)
    if hits:
        for c in cand:
            if c.stem.lower() == hits[0]:
                return c
    return None
# validator’s approach

def _extract_predicted_techniques(json_path: Path) -> Set[str]:
    data = json.loads(json_path.read_text(encoding="utf-8", errors="ignore"))
    seq = data.get("full_path", [])
    return set([str(x).upper() for x in seq])
# validator’s semantics for RAF-AG native

def _load_native_predictions(text_dir: Path, rafag_dir: Path, attackg_dir: Path) -> tuple[Dict[str, Set[str]], Dict[str, Set[str]]]:
    """
    Return dicts mapping STEM -> predicted TTP codes (mixed T#### or T####.###).
    """
    rafag_preds: Dict[str, Set[str]] = {}
    attackg_preds: Dict[str, Set[str]] = {}

    for txt in sorted(text_dir.glob("*.txt")):
        stem = txt.stem

        # RAF-AG native (decoding_result JSONs store {'full_path':[...]}).
        if rafag_dir and rafag_dir.exists():
            j = _find_json_for_stem(rafag_dir, stem)
            if j and j.exists():
                rafag_preds[stem] = _extract_predicted_techniques(j)

        # AttacKG native (attackg_result JSONs have technique keys only).
        if attackg_dir and attackg_dir.exists():
            k = _find_json_for_stem(attackg_dir, stem)
            if k and k.exists():
                try:
                    obj = json.loads(k.read_text(encoding="utf-8", errors="ignore"))
                    codes = {str(x).upper() for x in obj.keys() if isinstance(x, str)}
                    attackg_preds[stem] = codes
                except Exception:
                    pass

    return rafag_preds, attackg_preds
# Matches your existing logic for native outputs:contentReference[oaicite:16]{index=16}:contentReference[oaicite:17]{index=17}

# ----------------- Adapter RAW parsers (via extract_ttps) -----------------
def _infer_stem_from_payload(path: Path, payload: Any) -> str:
    # try id-like fields; else fallback to filename stem
    if isinstance(payload, dict):
        for k in ("id", "name", "filename", "file", "report"):
            v = payload.get(k)
            if isinstance(v, str) and v:
                return Path(v).stem
    return path.stem

def _collect_adapter_predictions(raw_dir: Path, predictor_obj) -> Dict[str, Set[str]]:
    """
    Iterate raw *.json files; for each, pass the loaded JSON to predictor.extract_ttps(payload).
    Return STEM -> set(T####(.###)?).
    """
    preds: Dict[str, Set[str]] = {}
    if not raw_dir or not raw_dir.exists():
        return preds
    

    for p in sorted(raw_dir.glob("*.json")):
        try:
            payload = json.loads(p.read_text(encoding="utf-8", errors="ignore"))
        except Exception as e:
            print(f"Warning: could not load JSON from {p}: {e}")
            continue
        try:
            codes = predictor_obj.extract_ttps(payload) or []
        except Exception as e:
            print(f"Warning: could not extract TTPs from payload in {p}: {e}")
            # tolerate any schema quirks; best-effort fallback to common shapes
            codes = []
            if isinstance(payload, dict):
                # common fallback shapes
                if "ttps" in payload: codes = payload["ttps"]
                elif "predictions" in payload: codes = payload["predictions"]
                elif "full_path" in payload: codes = payload["full_path"]
            elif isinstance(payload, list):
                # try list-of-dicts with ttps/predictions
                for it in payload:
                    if isinstance(it, dict):
                        codes.extend(it.get("ttps") or it.get("predictions") or it.get("full_path") or [])
        stem = _infer_stem_from_payload(p, payload)
        preds[stem] = {str(c).upper() for c in codes if isinstance(c, str)}
    print(f"Collected adapter predictions for {len(preds)} reports from {raw_dir}")
    print(f"Example: {list(preds.items())[:5]}")
    return preds

# ----------------- CSV builder -----------------
def build_results_csv(
    out_csv: Path = RESULTS,
    paper_csv: Path = PAPER_CSV,
    gt_json: Path = GROUND_TRUTH_JSON,
    text_dir: Path = TEXT_DIR,
    rafag_native_dir: Path = RAFAG_CAMPAIGN_DECODING,
    attackg_native_dir: Path = ATTACKG_CAMPAIGN_RESULT,
    rafag_raw_dir: Path = RAFAG_ADAPTER_RAW,
    attackg_raw_dir: Path = ATTACKG_ADAPTER_RAW,
) -> pd.DataFrame:

    # Load paper + GT
    paper, gt_by_stem = _load_paper_and_gt(paper_csv, gt_json)

    # Load native (no-framework) predictions
    rafag_native, attackg_native = _load_native_predictions(text_dir, rafag_native_dir, attackg_native_dir)

    # Load adapter (with-framework) predictions via extract_ttps()
    from Framework.adapters.attackg_adapter import AttacKGPredictor
    from Framework.adapters.raf_ag_adapter import RAFAGPredictor
    attackg_adapter = AttacKGPredictor()
    rafag_adapter = RAFAGPredictor()

    rafag_fw = _collect_adapter_predictions(rafag_raw_dir, rafag_adapter)
    attackg_fw = _collect_adapter_predictions(attackg_raw_dir, attackg_adapter)

    rows = []
    for _, r in paper.iterrows():
        report = r["report"]
        rafag_tp, rafag_fp, rafag_fn = int(r["RAF-AG_TP"]), int(r["RAF-AG_FP"]), int(r["RAF-AG_FN"])
        # AttacKG paper columns optional
        atk_tp_paper = r.get("AttacKG_TP", None)
        atk_fp_paper = r.get("AttacKG_FP", None)
        atk_fn_paper = r.get("AttacKG_FN", None)

        txt = _best_txt_for_report(text_dir, report)
        stem = txt.stem if txt else None

        labels = _labels_for_report(report, text_dir, gt_by_stem)
        gt_groups, gt_tactics = _build_gt_groups_and_tactics(labels)
        gt_techs = {c for c in labels if TECHNIQUE_RE.match(c) or SUBTECH_RE.match(c)}
        normalized_gt = { _parent(x) for x in gt_techs }

        # ---- native (no framework) strict & generous
        # RAF-AG
        nat_r = rafag_native.get(stem, set())
        nat_r_norm = { _parent(x) for x in nat_r }
        re_TP  = len(nat_r_norm & normalized_gt)
        re_FP  = len(nat_r_norm - normalized_gt)
        re_FN  = len(normalized_gt - nat_r_norm)
        re_p_TP = re_p_FP = re_p_FN = None
        if nat_r:
            re_p_TP, re_p_FP, re_p_FN = _count_matches(nat_r, gt_groups, gt_tactics, tactic_fallback=True)

        # AttacKG
        nat_a = attackg_native.get(stem, set())
        nat_a_norm = { _parent(x) for x in nat_a }
        At_re_TP = len(nat_a_norm & normalized_gt)
        At_re_FP = len(nat_a_norm - normalized_gt)
        At_re_FN = len(normalized_gt - nat_a_norm)
        At_re_p_TP = At_re_p_FP = At_re_p_FN = None
        if nat_a:
            At_re_p_TP, At_re_p_FP, At_re_p_FN = _count_matches(nat_a, gt_groups, gt_tactics, tactic_fallback=True)

        # ---- adapter (with framework) strict & generous
        # RAF-AG
        fw_r = rafag_fw.get(f"rafag_raw_{stem}.txt", set())
        fw_r_norm = { _parent(x) for x in fw_r }
        re_TP_fw = len(fw_r_norm & normalized_gt)
        re_FP_fw = len(fw_r_norm - normalized_gt)
        re_FN_fw = len(normalized_gt - fw_r_norm)
        re_p_TP_fw = re_p_FP_fw = re_p_FN_fw = None
        if fw_r:
            re_p_TP_fw, re_p_FP_fw, re_p_FN_fw = _count_matches(fw_r, gt_groups, gt_tactics, tactic_fallback=True)

        # AttacKG
        fw_a = attackg_fw.get(f"attackg_raw_{stem}.txt", set())
        fw_a_norm = { _parent(x) for x in fw_a }
        At_re_TP_fw = len(fw_a_norm & normalized_gt)
        At_re_FP_fw = len(fw_a_norm - normalized_gt)
        At_re_FN_fw = len(normalized_gt - fw_a_norm)
        At_re_p_TP_fw = At_re_p_FP_fw = At_re_p_FN_fw = None
        if fw_a:
            At_re_p_TP_fw, At_re_p_FP_fw, At_re_p_FN_fw = _count_matches(fw_a, gt_groups, gt_tactics, tactic_fallback=True)

        rows.append({
            "report": report,

            # Paper (RAF-AG mandatory; AttacKG optional if present in CSV)
            "TP": rafag_tp, "FP": rafag_fp, "FN": rafag_fn,
            "AttacKG_TP": pd.NA if pd.isna(atk_tp_paper) else int(atk_tp_paper),
            "AttacKG_FP": pd.NA if pd.isna(atk_fp_paper) else int(atk_fp_paper),
            "AttacKG_FN": pd.NA if pd.isna(atk_fn_paper) else int(atk_fn_paper),

            # Strict – native
            "re_TP": re_TP, "re_FP": re_FP, "re_FN": re_FN,
            "At_re_TP": At_re_TP, "At_re_FP": At_re_FP, "At_re_FN": At_re_FN,

            # Strict – adapter
            "re_TP_fw": re_TP_fw, "re_FP_fw": re_FP_fw, "re_FN_fw": re_FN_fw,
            "At_re_TP_fw": At_re_TP_fw, "At_re_FP_fw": At_re_FP_fw, "At_re_FN_fw": At_re_FN_fw,

            # Generous – native
            "re_p_TP": re_p_TP, "re_p_FP": re_p_FP, "re_p_FN": re_p_FN,
            "At_re_p_TP": At_re_p_TP, "At_re_p_FP": At_re_p_FP, "At_re_p_FN": At_re_p_FN,

            # Generous – adapter
            "re_p_TP_fw": re_p_TP_fw, "re_p_FP_fw": re_p_FP_fw, "re_p_FN_fw": re_p_FN_fw,
            "At_re_p_TP_fw": At_re_p_TP_fw, "At_re_p_FP_fw": At_re_p_FP_fw, "At_re_p_FN_fw": At_re_p_FN_fw,
        })

    out = pd.DataFrame(rows)
    out.to_csv(out_csv, index=False)
    return out

# ----------------- Metric computation & LaTeX -----------------
def _metrics_from(df: pd.DataFrame, variants: OrderedDict) -> Dict[str, Dict[str, Dict[str, float]]]:
    metrics: Dict[str, Dict[str, Dict[str, float]]] = {}
    # coerce all columns to numeric where possible
    need = {c for cols in variants.values() for c in cols}
    for c in need:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0)

    for name, (tp_c, fp_c, fn_c) in variants.items():
        tp, fp, fn = df[tp_c].sum(), df[fp_c].sum(), df[fn_c].sum()
        p_micro, r_micro, f1_micro = _prf(tp, fp, fn)
        doc_p = _safe_div(df[tp_c], df[tp_c] + df[fp_c])
        doc_r = _safe_div(df[tp_c], df[tp_c] + df[fn_c])
        doc_f1 = (2 * doc_p * doc_r) / (doc_p + doc_r)
        doc_f1 = doc_f1.fillna(0.0)
        metrics[name] = {
            "micro": {"P": float(p_micro), "R": float(r_micro), "F1": float(f1_micro)},
            "avg_docs": {"P": float(doc_p.mean()), "R": float(doc_r.mean()), "F1": float(doc_f1.mean())},
        }
    return metrics

def _table(metrics: Dict[str, Dict[str, Dict[str, float]]], blocks: List[str], caption: str, label: str, places: int = 1) -> str:
    def pct(x: float) -> str: return f"{100.0 * x:.{places}f}\\%"
    def row(key: str) -> str:
        cells = []
        for b in blocks:
            P, R, F1 = metrics[b][key]["P"], metrics[b][key]["R"], metrics[b][key]["F1"]
            cells.extend([pct(P), pct(R), pct(F1)])
        return " & ".join(cells)

    colspec = "l|" + "|".join(["ccc"] * len(blocks))
    header = (
        "\\begin{tabular}{" + colspec + "}\n"
        "\\hline\n"
        "\\textbf{Avg. Method} & "
        + " ".join([f"\\multicolumn{{3}}{{c|}}{{\\textbf{{{b}}}}} " for b in blocks[:-1]])
        + f"\\multicolumn{{3}}{{c}}{{\\textbf{{{blocks[-1]}}}}} \\\\\n"
        f"\\cline{{2-{1+3*len(blocks)}}}\n"
        " & " + " & ".join(["P", "R", "F1"] * len(blocks)) + "\\\\\n"
        "\\hline\n"
    )
    body = (
        "\\textbf{Micro Average} & " + row("micro") + "\\\\\n"
        "\\textbf{Avg. over docs} & " + row("avg_docs") + "\\\\\n"
        "\\hline\n\\end{tabular}\n"
    )
    env = [
        "\\begin{table}[t]",
        "\\centering",
        "\\setlength{\\tabcolsep}{5pt}",
        "\\renewcommand{\\arraystretch}{1.15}",
        f"\\caption{{{caption}}}",
        f"\\label{{{label}}}",
    ]
    return "\n".join(env) + "\n" + header + body + "\\vspace{0.5em}\n"

def build_tables(out_csv: Path = RESULTS):
    df = pd.read_csv(out_csv)

    # STRICT table: Paper, Repro(no-fw), Repro(with-fw) for both tools
    strict_blocks = [
        "RAF-AG Paper",
        "RAF-AG Repro (no-fw) Strict",
        "RAF-AG Repro (fw) Strict",
        "AttacKG Paper",
        "AttacKG Repro (no-fw) Strict",
        "AttacKG Repro (fw) Strict",
    ]
    strict_map = OrderedDict({
        "RAF-AG Paper": ("TP", "FP", "FN"),
        "RAF-AG Repro (no-fw) Strict": ("re_TP", "re_FP", "re_FN"),
        "RAF-AG Repro (fw) Strict": ("re_TP_fw", "re_FP_fw", "re_FN_fw"),
        "AttacKG Paper": ("AttacKG_TP", "AttacKG_FP", "AttacKG_FN"),
        "AttacKG Repro (no-fw) Strict": ("At_re_TP", "At_re_FP", "At_re_FN"),
        "AttacKG Repro (fw) Strict": ("At_re_TP_fw", "At_re_FP_fw", "At_re_FN_fw"),
    })
    strict_metrics = _metrics_from(df, strict_map)
    strict_tex = _table(
        strict_metrics, strict_blocks,
        "Strict match (parent-normalized) — Paper vs. Reproduction (without vs. with framework).",
        "tab:rafag_attckg_strict",
        places=1,
    )
    (out_csv.with_suffix(".strict.table.tex")).write_text(strict_tex)

    # GENEROUS table: Paper, Repro(no-fw), Repro(with-fw) for both tools
    generous_blocks = [
        "RAF-AG Paper",
        "RAF-AG Repro (no-fw) Generous",
        "RAF-AG Repro (fw) Generous",
        "AttacKG Paper",
        "AttacKG Repro (no-fw) Generous",
        "AttacKG Repro (fw) Generous",
    ]
    generous_map = OrderedDict({
        "RAF-AG Paper": ("TP", "FP", "FN"),
        "RAF-AG Repro (no-fw) Generous": ("re_p_TP", "re_p_FP", "re_p_FN"),
        "RAF-AG Repro (fw) Generous": ("re_p_TP_fw", "re_p_FP_fw", "re_p_FN_fw"),
        "AttacKG Paper": ("AttacKG_TP", "AttacKG_FP", "AttacKG_FN"),
        "AttacKG Repro (no-fw) Generous": ("At_re_p_TP", "At_re_p_FP", "At_re_p_FN"),
        "AttacKG Repro (fw) Generous": ("At_re_p_TP_fw", "At_re_p_FP_fw", "At_re_p_FN_fw"),
    })
    generous_metrics = _metrics_from(df, generous_map)
    generous_tex = _table(
        generous_metrics, generous_blocks,
        "Generous match (group-aware + tactic fallback) — Paper vs. Reproduction (without vs. with framework).",
        "tab:rafag_attckg_generous",
        places=1,
    )
    (out_csv.with_suffix(".generous.table.tex")).write_text(generous_tex)

    return strict_tex, generous_tex

# ----------------- CLI -----------------
if __name__ == "__main__":
    # check that all the paths exist
    for p in [PAPER_CSV, GROUND_TRUTH_JSON, TEXT_DIR, RAFAG_CAMPAIGN_DECODING, ATTACKG_CAMPAIGN_RESULT, RAFAG_ADAPTER_RAW, ATTACKG_ADAPTER_RAW]:
        if not p.exists():
            raise FileNotFoundError(f"Required path not found: {p}")
    # 1) Build CSV from the four sources
    out = build_results_csv(RESULTS)

    # 2) Build and write both tables
    s_tex, g_tex = build_tables(RESULTS)

    # 3) Console preview
    print("Wrote:", RESULTS)
    print("LaTeX STRICT table =>", RESULTS.with_suffix(".strict.table.tex"))
    print("LaTeX GENEROUS table =>", RESULTS.with_suffix(".generous.table.tex"))

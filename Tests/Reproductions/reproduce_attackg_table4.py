#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Set, Tuple

import pandas as pd
from helpers.reporting import HERE, PROJECT, EXTERNAL, save_scores, markdown, check_predictions, tool_directory, output_section
sys.path.insert(0, str(PROJECT))

# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------

_TCODE_RE = re.compile(r"T\d{4}(?:\.\d{3})?")


def norm_key(s: str) -> str:
    """Normalize strings for loose matching (filenames, titles, etc.)."""
    s = s.lower().strip()
    s = re.sub(r"\.(txt|pdf|json)$", "", s)  # strip common suffixes at end
    s = re.sub(r"[^a-z0-9]+", " ", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def dedupe_preserve_order(xs: Iterable[str]) -> List[str]:
    seen: Set[str] = set()
    out: List[str] = []
    for x in xs:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


# -----------------------------------------------------------------------------
# Parsing AttacKG outputs
# -----------------------------------------------------------------------------

def extract_tcodes_from_attackg_json(json_path: Path) -> List[str]:
    """Robustly grab technique codes from AttacKG output JSON or raw text.

    We do not assume a strict schema; we scan the file text for T-codes.
    """
    try:
        data = json_path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return []
    codes = _TCODE_RE.findall(data)
    # normalize (uppercase T, dedupe)
    codes = [c.upper() for c in codes]
    return dedupe_preserve_order(codes)


def save_preds_json(path: Path, preds: List[dict]) -> None:
    """Write predictions as a JSON list of {'id':..., 'ttps':[...] }."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(preds, f, indent=2)

def load_preds_json(path: Path) -> List[dict]:
    """Read predictions JSON and normalize to [{'id':..., 'ttps':[...]}]."""
    p = Path(path)
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return []
    # normalize supported shapes
    if isinstance(data, list):
        out = []
        for it in data:
            if isinstance(it, dict):
                _id = it.get("id") or it.get("name") or it.get("filename")
                ttps = it.get("ttps") or it.get("predictions") or []
                if _id is not None:
                    out.append({"id": str(_id), "ttps": [str(c).upper() for c in ttps]})
        return out
    elif isinstance(data, dict):
        return [{"id": str(k), "ttps": [str(c).upper() for c in (v or [])]} for k, v in data.items()]
    return []

def merge_preds(old: List[dict], new: List[dict], prefer_new: bool = True) -> List[dict]:
    """Merge two prediction lists by 'id'. If prefer_new=True, new overrides old on conflicts."""
    by_id: Dict[str, dict] = {d["id"]: d for d in old if isinstance(d, dict) and "id" in d}
    for d in new:
        if not isinstance(d, dict) or "id" not in d:
            continue
        _id = d["id"]
        if prefer_new or _id not in by_id:
            by_id[_id] = {"id": _id, "ttps": [str(c).upper() for c in (d.get("ttps") or [])]}
    return list(by_id.values())


# -----------------------------------------------------------------------------
# Runners
# -----------------------------------------------------------------------------

@dataclass
class RunResult:
    id: str             # filename (with or without .txt)
    ttps: List[str]     # list of technique codes


def tool_output(args, tool, backend):
    # Other experiment runners can reuse these tool calls in their own layout.
    return getattr(args, "tool_out_dir", None) or tool_directory(args.out_dir, tool, backend)


def run_attackg_original(args, fnames):
    ''' 
    Uses the original AttacKG main.py
    Note that this should be run in a virtual environment setup with AttacKG dependencies (see AttacKG README)
    '''
    attack_main = args.attackg_root / "main.py"
    if not attack_main.exists():
        raise FileNotFoundError("AttacKG main.py not found in {}".format(args.attackg_root))

    out_dir = tool_output(args, "AttacKG", "original") / "raw"
    out_dir.mkdir(parents=True, exist_ok=True)

    results = []

    for fname in fnames:
        txt_path = args.text_dir / fname
        if not txt_path.exists():
            print("[skip] input not found:", txt_path)
            continue
        # copy input into AttacKG root
        tmp_in = args.attackg_root / txt_path.name
        shutil.copy2(txt_path, tmp_in)
        if args.verbose:
            print("[run] AttacKG on {}".format(txt_path.name))
        with output_section("RAW TOOL OUTPUT", f"AttacKG / original / {txt_path.name}"):
            rc = subprocess.run([
                sys.executable, "main.py",
                "-M", "techniqueIdentification",
                "-T", "templates",
                "-O", txt_path.stem,
                "-R", txt_path.name,
            ], cwd=args.attackg_root)
        rc.check_returncode()
        # cleanup input copy
        try:
            tmp_in.unlink()
        except Exception:
            pass

        # move produced JSON to out_dir
        produced = args.attackg_root / (txt_path.stem + "_techniques.json")
        dest = out_dir / (txt_path.stem + ".json")
        if produced.exists():
            try:
                shutil.move(str(produced), str(dest))
            except Exception:
                shutil.copy2(str(produced), str(dest))
                try:
                    produced.unlink()
                except Exception:
                    pass
            if args.verbose:
                print("[save] {}".format(dest))
            ttps = extract_tcodes_from_attackg_json(dest)
        else:
            raise RuntimeError("No JSON produced for " + txt_path.name)

        results.append({"id": txt_path.name, "ttps": ttps})

    return results

def run_attackg_adapter(args, fnames):
    from helpers.reporting import isolate_framework_cache
    isolate_framework_cache()
    from Framework.adapters.attackg_adapter import predict_texts

    texts, names = [], []
    for fname in fnames:
        txt_path = args.text_dir / fname
        if not txt_path.exists():
            print("[skip] input not found:", txt_path)
            continue
        if args.verbose:
            print("[run] AttacKG adapter on {}".format(txt_path.name))
        texts.append(txt_path.read_text(encoding="utf-8", errors="ignore"))
        names.append(txt_path.name)

    with output_section("FRAMEWORK ADAPTER OUTPUT", "AttacKG / framework (including container diagnostics)"):
        raw, _ = predict_texts(texts, ids=names, engine=args.engine,
                              save_dir=tool_output(args, "AttacKG", "framework") / "raw",
                              tmp_root=args.out_dir.parent / "tmp", verbose=args.verbose)
    save_preds_json(tool_output(args, "AttacKG", "framework") / "parsed/adapter_outputs.json", raw)
    check_predictions(raw, names)

    # normalize plausible shapes → [{'id':..., 'ttps':[...]}]
    preds = []
    if isinstance(raw, dict):
        for _id, codes in raw.items():
            preds.append({"id": str(_id), "ttps": [str(c).upper() for c in (codes or [])]})
    elif isinstance(raw, list):
        for it in raw:
            if not isinstance(it, dict):
                continue
            _id = it.get("id") or it.get("name") or it.get("filename")
            codes = it.get("ttps") or it.get("predictions") or []
            if _id is not None:
                preds.append({**it, "id": str(_id), "ttps": [str(c).upper() for c in codes]})
    else:
        print("[warn] Unexpected adapter result shape:", type(raw))

    return preds

# TTPDrill Runners 
def run_ttpdrill_original(args, fnames):
    import time
    import ast
    import os
    import urllib.request

    # for parsing output tactic codes
    CODE_TACTICS = ["TA0006","TA0002","TA0040","TA0003","TA0004","TA0008","TA0005","TA0010","TA0007","TA0009","TA0011","TA0001"]
    NAME_TACTICS = ["Credential Access","Execution","Impact","Persistence","Privilege Escalation","Lateral Movement","Defense Evasion","Exfiltration","Discovery","Collection","Command and Control","Initial Access"]

    _NAME2CODE = {name: code for name, code in zip(NAME_TACTICS, CODE_TACTICS)}

    def _corenlp_is_up(port: int = 9000) -> bool:
        try:
            with urllib.request.urlopen(f"http://localhost:{port}", timeout=1):
                return True
        except Exception:
            return False

    def _start_corenlp(corenlp_home: Path, port: int = 9000) -> subprocess.Popen | None:
        """
        Start CoreNLP server if not already up. Return Popen if we started it; else None.
        """
        if _corenlp_is_up(port):
            return None
        cmd = [
            "java", "-mx4g", "-cp", "*",
            "edu.stanford.nlp.pipeline.StanfordCoreNLPServer",
            "-port", str(port), "-timeout", "15000", "-threads", "4",
        ]
        # Quiet logs
        devnull = open(os.devnull, "wb")
        proc = subprocess.Popen(cmd, cwd=str(corenlp_home), stdout=devnull, stderr=subprocess.STDOUT)
        # Wait up to ~120s
        for _ in range(120):
            if _corenlp_is_up(port):
                return proc
            time.sleep(1.0)
        # Give up
        try:
            proc.terminate()
        except Exception:
            pass
        raise RuntimeError("CoreNLP failed to start on port {}".format(port))

    def _stop_corenlp(proc: subprocess.Popen | None):
        if proc is None:
            return
        try:
            proc.terminate()
            proc.wait(timeout=10)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    def _parse_ttpdrill_stdout(stdout_text: str) -> List[str]:
        """
        Parse 'Mapped:' blocks from TTPDrill stdout; collect technique IDs and tactic codes. Same as parsing we do in the adapter
        """
        pat = re.compile(r"Mapped:\s*?\n(?:\s*\n)*?(\[.*?\])(?=\s*(?:Text:|\Z))", re.S)
        ttps = set()
        for m in pat.finditer(stdout_text):
            raw = m.group(1).strip()
            try:
                block = ast.literal_eval(raw)  # list[ dict{...} , ...]
            except Exception:
                continue
            if not isinstance(block, list):
                continue
            for item in block:
                if not isinstance(item, dict):
                    continue
                tid = (item.get("techId", {}) or {}).get("data")
                if tid:
                    ttps.add(str(tid).upper())
                tname = (item.get("tactic", {}) or {}).get("data")
                if tname and tname in _NAME2CODE:
                    ttps.add(_NAME2CODE[tname])
        return sorted(ttps)
    
    main_py = args.ttpdrill_root / "main.py"
    if not main_py.exists():
        raise FileNotFoundError(f"TTPDrill main.py not found in {args.ttpdrill_root}")

    # Start CoreNLP if needed; remember if we started it
    proc = _start_corenlp(args.corenlp_home, port=args.corenlp_port)

    text_dir = args.text_dir
    verbose = args.verbose
    ttpdrill_root = args.ttpdrill_root

    try:
        results: List[dict] = []
        for fname in fnames:
            txt_path = text_dir / fname
            if not txt_path.exists():
                print(f"[skip] input not found: {txt_path}")
                continue
            if verbose:
                print(f"[run] TTPDrill original on {txt_path.name}")

            # Feed input
            (ttpdrill_root / "input.txt").write_text(
                txt_path.read_text(encoding="utf-8", errors="ignore"),
                encoding="utf-8"
            )

            # Execute
            rc = subprocess.run(
                [sys.executable, "main.py"],
                cwd=str(ttpdrill_root),
                capture_output=True,
                text=True,
            )

            raw_dir = tool_output(args, "TTPDrill", "original") / "raw"
            raw_dir.mkdir(parents=True, exist_ok=True)
            (raw_dir / (txt_path.stem + ".txt")).write_text(rc.stdout + "\n" + rc.stderr)
            if verbose:
                with output_section("RAW TOOL OUTPUT", f"TTPDrill / original / {txt_path.name} (captured)"):
                    print(rc.stdout, end="")
                    print(rc.stderr, end="", file=sys.stderr)
            print(f"\n[runner] Saved TTPDrill original raw output: {raw_dir / (txt_path.stem + '.txt')}")
            rc.check_returncode()
            ttps = _parse_ttpdrill_stdout(rc.stdout)

            results.append({"id": txt_path.name, "ttps": ttps})
        return results
    finally:
        _stop_corenlp(proc)

    


def run_ttpdrill_adapter(args, fnames):
    from helpers.reporting import isolate_framework_cache
    isolate_framework_cache()
    from Framework.adapters.ttpdrill_adapter import predict_texts

    texts, names = [], []
    for fname in fnames:
        txt_path = args.text_dir / fname
        if not txt_path.exists():
            print("[skip] input not found:", txt_path)
            continue
        if args.verbose:
            print("[run] TTPDrill adapter on {}".format(txt_path.name))
        texts.append(txt_path.read_text(encoding="utf-8", errors="ignore"))
        names.append(txt_path.name)

    with output_section("FRAMEWORK ADAPTER OUTPUT", "TTPDrill / framework (including container diagnostics)"):
        raw, _ = predict_texts(texts, ids=names, engine=args.engine,
                              save_dir=tool_output(args, "TTPDrill", "framework") / "raw",
                              tmp_root=args.out_dir.parent / "tmp", verbose=args.verbose)
    save_preds_json(tool_output(args, "TTPDrill", "framework") / "parsed/adapter_outputs.json", raw)
    check_predictions(raw, names)

    # normalize plausible shapes → [{'id':..., 'ttps':[...]}]
    preds = []
    if isinstance(raw, dict):
        for _id, codes in raw.items():
            preds.append({"id": str(_id), "ttps": [str(c).upper() for c in (codes or [])]})
    elif isinstance(raw, list):
        for it in raw:
            if not isinstance(it, dict):
                continue
            _id = it.get("id") or it.get("name") or it.get("filename")
            codes = it.get("ttps") or it.get("predictions") or []
            if _id is not None:
                preds.append({**it, "id": str(_id), "ttps": [str(c).upper() for c in codes]})
    else:
        print("[warn] Unexpected adapter result shape:", type(raw))

    return preds


# -----------------------------------------------------------------------------
# Labels & Paper counts
# -----------------------------------------------------------------------------

@dataclass
class LabelsEntry:
    id: str
    labels: Set[str]


def load_labels(labels_json: Path) -> Tuple[Dict[str, LabelsEntry], Dict[str, str]]:
    """Load ground-truth technique labels per report *and* an alias map.

    The alias map normalizes multiple names for the same report (e.g.,
    filename and pretty title) to a canonical key (we prefer the title when
    present). This lets predictions keyed by filename line up with paper rows
    keyed by title.

    Returns:
        labels_by_key: dict[norm_key] -> LabelsEntry(labels=set[str])
        alias_map:     dict[norm_key_of_any_alias] -> canonical_norm_key
    """
    raw = json.loads(labels_json.read_text(encoding="utf-8"))

    labels_by_key: Dict[str, LabelsEntry] = {}
    alias_map: Dict[str, str] = {}

    def add_alias(alias: str, canon: str):
        if alias:
            alias_map[norm_key(alias)] = canon

    if isinstance(raw, list):
        for it in raw:
            fid = it.get("file name") or it.get("filename") or it.get("file") or it.get("id") or it.get("name")
            title = it.get("title")
            labels = it.get("labels") or it.get("ground_truth") or it.get("y_true") or []

            # choose canonical
            canon = norm_key(title) if title else norm_key(fid or "")
            if not canon:
                continue

            # store labels under canonical key
            labs = {str(c).upper() for c in labels}
            labels_by_key[canon] = LabelsEntry(id=title or fid or canon, labels=labs)

            # alias all observed names to canonical
            add_alias(fid or "", canon)
            add_alias(title or "", canon)
            # also map stem of filename
            if fid:
                add_alias(Path(fid).stem, canon)
    elif isinstance(raw, dict):
        for k, v in raw.items():
            canon = norm_key(k)
            labs = {str(c).upper() for c in (v or [])}
            labels_by_key[canon] = LabelsEntry(id=k, labels=labs)
            add_alias(k, canon)
            add_alias(Path(k).stem, canon)
    else:
        raise ValueError("Unsupported labels_json shape")

    return labels_by_key, alias_map


@dataclass
class PaperCounts:
    report: str
    gt: int
    attackg_fn: int
    attackg_fp: int
    ttpdrill_fn: int
    ttpdrill_fp: int

def load_paper_counts(paper_csv: Path) -> pd.DataFrame:
    df = pd.read_csv(paper_csv)

    # Normalize names (case-insensitive)
    cols = {c.lower(): c for c in df.columns}

    def need(*opts):
        for o in opts:
            if o in cols:
                return cols[o]
        raise KeyError(f"Missing column among {opts}")

    report = need("report")
    gt = need("gt")
    a_fn = need("attackg_fn")
    a_fp = need("attackg_fp")
    t_fn = need("ttpdrill_fn")
    t_fp = need("ttpdrill_fp")

    df = df[[report, gt, a_fn, a_fp, t_fn, t_fp]].copy()
    df.columns = ["report", "GT", "Attackg_FN", "Attackg_FP", "TTP_FN", "TTP_FP"]
    df["Attackg_TP"] = df["GT"] - df["Attackg_FN"]
    df["TTP_TP"] = df["GT"] - df["TTP_FN"]
    df["key"] = df["report"].map(norm_key)
    return df


# -----------------------------------------------------------------------------
# Metrics
# -----------------------------------------------------------------------------

@dataclass
class Counts:
    tp: int
    fp: int
    fn: int
    gt: int


def prf_from_counts(tp: int, fp: int, fn: int) -> Tuple[float, float, float]:
    p = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    r = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1 = (2 * p * r) / (p + r) if (p + r) > 0 else 0.0
    return p, r, f1

def update_codes(preds: List[RunResult]) -> List[RunResult]:
    # replace codes with updated versions, remove tactics, remove predictions that aren't in attackg set, and simplify to techniques only
    from Framework.utils.attack_lookup import get_deprecation_revocation_details

    out: List[RunResult] = []
    for r in preds:
        new_codes = set()
        for c in r.ttps:
            deprecation_details = get_deprecation_revocation_details(c)
            # {"deprecated": True, "revoked": True, "updated": rev, "replacements": [json.loads(u) for u in serialized]}
            if deprecation_details and (deprecation_details.get("revoked") or deprecation_details.get("replacements")):
                replacements = deprecation_details.get("replacements") or []
                for rep in replacements:
                    new_codes.add(rep["technique_id"].upper())
                
        out.append(RunResult(id=r.id, ttps=sorted(new_codes)))
    return out


def evaluate_against_labels(preds: List[RunResult], labels_by_key: Dict[str, LabelsEntry], alias_map: Dict[str, str], args) -> Dict[str, Counts]:
    """Per-report TP/FP/FN counts for a system, keyed by normalized id.

    For matching, we try to align prediction id (filename) to labels by:
      - norm_key(filename)
      - norm_key(filename without extension)
    If no labels found, we treat GT=0.
    """

    
    out: Dict[str, Counts] = {}

    for r in preds:
        raw_key = norm_key(r.id)
        stem_key = norm_key(Path(r.id).stem)
        canon = alias_map.get(raw_key) or alias_map.get(stem_key) or raw_key

        gt_entry = labels_by_key.get(canon)

        y_true: Set[str] = gt_entry.labels if gt_entry else set()
        y_pred: Set[str] = {c.upper() for c in r.ttps}

        tp = len(y_true & y_pred)
        fp = len(y_pred - y_true)
        fn = len(y_true - y_pred)
        gt = len(y_true)
        out[canon] = Counts(tp=tp, fp=fp, fn=fn, gt=gt)

    return out


def aggregate_micro(counts_by_key: Dict[str, Counts]) -> Counts:
    tp = sum(c.tp for c in counts_by_key.values())
    fp = sum(c.fp for c in counts_by_key.values())
    fn = sum(c.fn for c in counts_by_key.values())
    gt = sum(c.gt for c in counts_by_key.values())
    return Counts(tp=tp, fp=fp, fn=fn, gt=gt)


# -----------------------------------------------------------------------------
# Orchestration
# -----------------------------------------------------------------------------

def build_cli() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="AttacKG paper-vs-reproduced validator (JSON-driven)")

    # Run modes
    p.add_argument("--report-only", action="store_true",
                   help="Evaluate saved predictions and regenerate comparisons without inference (also the default when no --run_* flag is given)")
    p.add_argument("--run_original_all", action="store_true", help="Run original AttacKG on all .txt in text_dir")
    p.add_argument("--run_original_sample", action="store_true", help="Run original AttacKG on --sample_file only")
    p.add_argument("--run_adapter_all", action="store_true", help="Run adapter on all .txt in text_dir")
    p.add_argument("--run_adapter_sample", action="store_true", help="Run adapter on --sample_file only")
    p.add_argument("--run_ttpdrill_original_all", action="store_true", help="Run original TTPDrill on all .txt in text_dir")
    p.add_argument("--run_ttpdrill_original_sample", action="store_true", help="Run original TTPDrill on --sample_file only")
    p.add_argument("--run_ttpdrill_adapter_all", action="store_true", help="Run TTPDrill adapter on all .txt in text_dir")
    p.add_argument("--run_ttpdrill_adapter_sample", action="store_true", help="Run TTPDrill adapter on --sample_file only")
    p.add_argument("--update_codes", action="store_true", help="translate codes to modern labels")

    p.add_argument("--save_original_preds", type=Path, default=Path("AttacKG/original/parsed/predictions.json"), help="Write original AttacKG predictions to this JSON")
    p.add_argument("--save_adapter_preds", type=Path, default=Path("AttacKG/framework/parsed/predictions.json"), help="Write adapter predictions to this JSON")
    p.add_argument("--save_ttpdrill_original_preds", type=Path, default=Path("TTPDrill/original/parsed/predictions.json"), help="Write original TTPDrill predictions to this JSON")
    p.add_argument("--save_ttpdrill_adapter_preds", type=Path, default=Path("TTPDrill/framework/parsed/predictions.json"), help="Write adapter TTPDrill predictions to this JSON")

    p.add_argument("--load_original_preds", type=Path, default=Path("AttacKG/original/parsed/predictions.json"), help="Read original AttacKG predictions from this JSON")
    p.add_argument("--load_adapter_preds", type=Path, default=Path("AttacKG/framework/parsed/predictions.json"), help="Read adapter predictions from this JSON")
    p.add_argument("--load_ttpdrill_original_preds", type=Path, default=Path("TTPDrill/original/parsed/predictions.json"), help="Read original TTPDrill predictions from this JSON")
    p.add_argument("--load_ttpdrill_adapter_preds", type=Path, default=Path("TTPDrill/framework/parsed/predictions.json"), help="Read adapter TTPDrill predictions from this JSON")

    # I/O
    p.add_argument("--attackg_root", type=Path, 
                   default=EXTERNAL / "AttacKG",
                   help="Folder where AttacKG main.py lives")
    p.add_argument("--ttpdrill_root", type=Path, 
                   default=EXTERNAL / "TTPDrill",
                   help="Folder where TTPDrill main.py lives. NOTE: This assumes the TTPDrill 1.0 has been setup with TTPDrill 0.3 key files. See TTPDrill 1.0 README for details.")
    p.add_argument("--text_dir", type=Path, 
                   default=HERE / "data/attackg",
                   help="Folder of input .txt files")
    p.add_argument("--paper_csv", type=Path, default=HERE / "data/attackg/attackg_paper_counts.csv", help="Paper counts CSV")
    p.add_argument("--labels_json", type=Path, default=HERE / "data/attackg/ground_truth_and_test_labels.json", help="Ground-truth labels JSON")
    p.add_argument("--out-dir", type=Path, default=HERE / "experiments/attackg_table4/runs/manual/results")
    p.add_argument("--engine", choices=["podman", "docker"], default="podman")
    p.add_argument("--profile", choices=["smoke", "full"], default="full")

    # Selection
    p.add_argument("--sample_file", type=str, default="Darpa_Firefox DNS Drakon APT.txt", help="Filename (with .txt) to run if using a sample run")

    # Outputs
    p.add_argument("--per_report_out", type=Path, default=Path("attackg_per_report_counts.csv"), help="Where to write joined per-report counts")
    p.add_argument("--overall_out", type=Path, default=Path("attackg_overall_prf.csv"), help="3x3 metrics table (precision/recall/f1 x original/reproduced/adapter)")

    p.add_argument("--verbose", action="store_true")

    p.add_argument("--corenlp_home", type=Path, 
                   default=EXTERNAL / "TTPDrill/stanford-corenlp-full-2018-10-05",
                   help="Path to Stanford CoreNLP home directory")
    p.add_argument("--corenlp_port", type=int, default=9000, help="Port where CoreNLP server should listen")

    args = p.parse_args()
    if args.report_only:
        run_flags = ["--" + name for name, enabled in vars(args).items()
                     if name.startswith("run_") and enabled]
        if run_flags:
            p.error("--report-only cannot be combined with " + ", ".join(run_flags))
    args.out_dir = args.out_dir.resolve()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    (args.out_dir.parent / "tmp").mkdir(exist_ok=True)
    for name in ("save_original_preds", "save_adapter_preds", "save_ttpdrill_original_preds",
                 "save_ttpdrill_adapter_preds", "load_original_preds", "load_adapter_preds",
                 "load_ttpdrill_original_preds", "load_ttpdrill_adapter_preds", "per_report_out", "overall_out"):
        path = getattr(args, name)
        if not path.is_absolute():
            setattr(args, name, args.out_dir / path)

    return args

def aggregate_macro_from_df(
    df: pd.DataFrame,
    tp_col: str,
    fp_col: str,
    fn_col: str,
    mask: pd.Series | None = None,
) -> Tuple[float, float, float]:
    """
    Document-averaged (macro) precision/recall/F1.
    For each row: compute P/R/F1 with zero_division=0, then average across rows.
    If `mask` is provided, restrict to df[mask].
    """
    if mask is not None:
        df = df.loc[mask]
    if df.empty:
        return 0.0, 0.0, 0.0

    p_sum = r_sum = f_sum = 0.0
    n = len(df)

    for _, row in df[[tp_col, fp_col, fn_col]].iterrows():
        tp = int(row[tp_col])
        fp = int(row[fp_col])
        fn = int(row[fn_col])

        p = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        r = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        f1 = (2 * p * r) / (p + r) if (p + r) > 0 else 0.0

        p_sum += p
        r_sum += r
        f_sum += f1

    return p_sum / n, r_sum / n, f_sum / n



def main():
    from helpers.reporting import install_exit_handler
    install_exit_handler()
    args = build_cli()
    print("=== EXPERIMENT RUNNER OUTPUT | AttacKG Table 4: inputs and saved predictions ===", flush=True)

    # --- local helpers (self-contained) ---------------------------------------
    import json
    from typing import List, Dict, Any

    def _normalize_to_dict_list(obj: Any) -> List[dict]:
        """
        Normalize predictions into a list of dicts like:
            [{ "id": "<filename>", "ttps": ["T####", "T####.###", ...] }, ...]
        Accepts:
          - already-correct list[dict]
          - list[RunResult]-ish (objects with .id/.ttps)
          - dict mapping id -> list of ttps
        """
        out: List[dict] = []
        if obj is None:
            return out
        if isinstance(obj, list):
            for it in obj:
                if isinstance(it, dict) and "id" in it:
                    _id = str(it["id"])
                    _ttps = [str(c).upper() for c in (it.get("ttps") or [])]
                    out.append({"id": _id, "ttps": _ttps})
                else:
                    # Maybe a RunResult-like object
                    _id = getattr(it, "id", None)
                    _ttps = getattr(it, "ttps", None)
                    if _id is not None and _ttps is not None:
                        out.append({"id": str(_id), "ttps": [str(c).upper() for c in _ttps]})
        elif isinstance(obj, dict):
            for k, v in obj.items():
                out.append({"id": str(k), "ttps": [str(c).upper() for c in (v or [])]})
        return out

    def _save_preds_json(path: Path, preds_dicts: List[dict]) -> None:
        if not path or not preds_dicts:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(preds_dicts, indent=2), encoding="utf-8")

    def _load_preds_json(path: Path) -> List[dict]:
        p = Path(path) if path else None
        if not p or not p.exists():
            return []
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return []
        return _normalize_to_dict_list(data)

    def _merge_dict_preds(old: List[dict], new: List[dict], prefer_new: bool = True) -> List[dict]:
        """
        Merge two dict-pred lists by 'id'. If prefer_new, new overrides old on conflicts.
        """
        by_id: Dict[str, dict] = {d["id"]: d for d in old if isinstance(d, dict) and "id" in d}
        for d in new:
            if not isinstance(d, dict) or "id" not in d:
                continue
            _id = d["id"]
            if prefer_new or _id not in by_id:
                by_id[_id] = {"id": _id, "ttps": [str(c).upper() for c in (d.get("ttps") or [])]}
        return list(by_id.values())

    def _as_runresults(preds_dicts: List[dict]) -> List[RunResult]:
        """Convert list[dict] -> list[RunResult] for evaluation only."""
        return [RunResult(id=d["id"], ttps=[str(c).upper() for c in (d.get("ttps") or [])])
                for d in preds_dicts if isinstance(d, dict) and "id" in d]

    # -------------------------------------------------------------------------
    text_files = sorted(p.name for p in args.text_dir.glob("*.txt"))
    if not text_files:
        raise FileNotFoundError("No report texts in " + str(args.text_dir))
    if args.profile == "smoke":
        text_files = [args.sample_file]
    sample = [args.sample_file] if getattr(args, "sample_file", None) else []

    # Load label structures and paper counts
    labels_by_key, alias_map = load_labels(args.labels_json)
    paper_df = load_paper_counts(args.paper_csv)
    paper_keys = set(paper_df["key"])
    text_files = [name for name in text_files if alias_map.get(norm_key(name), norm_key(name)) in paper_keys]
    if len(text_files) != (1 if args.profile == "smoke" else 8):
        raise RuntimeError("Expected one smoke report or all eight paper reports; check label aliases")

    # Initialize predictions (as list[dict])
    reproduced_pred_dicts: List[dict] = []
    adapter_pred_dicts: List[dict] = []

    ttp_repro_pred_dicts: List[dict] = []
    ttp_adap_pred_dicts: List[dict] = []

    # 1) LOAD any existing artifacts first (so we have a base to supplement/override)
    if getattr(args, "load_original_preds", None):
        reproduced_pred_dicts = _load_preds_json(args.load_original_preds)
    if getattr(args, "load_adapter_preds", None):
        adapter_pred_dicts = _load_preds_json(args.load_adapter_preds)
    if getattr(args, "load_ttpdrill_original_preds", None):
        ttp_repro_pred_dicts = _load_preds_json(args.load_ttpdrill_original_preds)
    if getattr(args, "load_ttpdrill_adapter_preds", None):
        ttp_adap_pred_dicts = _load_preds_json(args.load_ttpdrill_adapter_preds)

    # 2) RUN if requested, then MERGE (run results override by id)
    if getattr(args, "run_original_all", False):
        ran = run_attackg_original(args, text_files)  # may return RunResult or dicts
        reproduced_pred_dicts = _merge_dict_preds(reproduced_pred_dicts, _normalize_to_dict_list(ran), prefer_new=True)
    elif getattr(args, "run_original_sample", False) and sample:
        ran = run_attackg_original(args, sample)
        reproduced_pred_dicts = _merge_dict_preds(reproduced_pred_dicts, _normalize_to_dict_list(ran), prefer_new=True)

    if getattr(args, "run_adapter_all", False):
        ran = run_attackg_adapter(args, text_files)  # may return RunResult or dicts
        adapter_pred_dicts = _merge_dict_preds(adapter_pred_dicts, _normalize_to_dict_list(ran), prefer_new=True)
    elif getattr(args, "run_adapter_sample", False) and sample:
        ran = run_attackg_adapter(args, sample)
        adapter_pred_dicts = _merge_dict_preds(adapter_pred_dicts, _normalize_to_dict_list(ran), prefer_new=True)

    if getattr(args, "run_ttpdrill_original_all", False):
        ran = run_ttpdrill_original(args, text_files)  # may return RunResult or dicts
        ttp_repro_pred_dicts = _merge_dict_preds(ttp_repro_pred_dicts, _normalize_to_dict_list(ran), prefer_new=True)
    elif getattr(args, "run_ttpdrill_original_sample", False) and sample:
        ran = run_ttpdrill_original(args, sample)
        ttp_repro_pred_dicts = _merge_dict_preds(ttp_repro_pred_dicts, _normalize_to_dict_list(ran), prefer_new=True)

    if getattr(args, "run_ttpdrill_adapter_all", False):
        ran = run_ttpdrill_adapter(args, text_files)  # may return RunResult or dicts
        ttp_adap_pred_dicts = _merge_dict_preds(ttp_adap_pred_dicts, _normalize_to_dict_list(ran), prefer_new=True)
    elif getattr(args, "run_ttpdrill_adapter_sample", False) and sample:
        ran = run_ttpdrill_adapter(args, sample)
        ttp_adap_pred_dicts = _merge_dict_preds(ttp_adap_pred_dicts, _normalize_to_dict_list(ran), prefer_new=True)

    # 3) SAVE any newly produced predictions (so the other venv can pick them up)
    if getattr(args, "save_original_preds", None) and reproduced_pred_dicts:
        _save_preds_json(args.save_original_preds, reproduced_pred_dicts)
    if getattr(args, "save_adapter_preds", None) and adapter_pred_dicts:
        _save_preds_json(args.save_adapter_preds, adapter_pred_dicts)
    if getattr(args, "save_ttpdrill_original_preds", None) and ttp_repro_pred_dicts:
        _save_preds_json(args.save_ttpdrill_original_preds, ttp_repro_pred_dicts)
    if getattr(args, "save_ttpdrill_adapter_preds", None) and ttp_adap_pred_dicts:
        _save_preds_json(args.save_ttpdrill_adapter_preds, ttp_adap_pred_dicts)

    # 4) EVALUATE (convert dicts -> RunResult only here)
    print("=== EXPERIMENT RUNNER OUTPUT | evaluation against ground truth ===", flush=True)
    reproduced_preds_rr = _as_runresults(reproduced_pred_dicts)
    adapter_preds_rr = _as_runresults(adapter_pred_dicts)
    ttp_repro_preds_rr = _as_runresults(ttp_repro_pred_dicts)
    ttp_adap_preds_rr = _as_runresults(ttp_adap_pred_dicts)

    rep_counts_by_key = evaluate_against_labels(reproduced_preds_rr, labels_by_key, alias_map, args) if reproduced_preds_rr else {}
    ada_counts_by_key = evaluate_against_labels(adapter_preds_rr, labels_by_key, alias_map, args) if adapter_preds_rr else {}
    ttp_repro_counts_by_key = evaluate_against_labels(ttp_repro_preds_rr, labels_by_key, alias_map, args) if ttp_repro_preds_rr else {}
    ttp_adap_counts_by_key = evaluate_against_labels(ttp_adap_preds_rr, labels_by_key, alias_map, args) if ttp_adap_preds_rr else {}


    if args.verbose:
        print("[dbg] Evaluated reports from available predictions (loaded from disk or generated in this invocation; not paper-reference results):")
        print(f"[dbg] AttacKG / original repository: {len(rep_counts_by_key)} reports")
        print(f"[dbg] AttacKG / framework adapter: {len(ada_counts_by_key)} reports")
        print(f"[dbg] TTPDrill / original repository: {len(ttp_repro_counts_by_key)} reports")
        print(f"[dbg] TTPDrill / framework adapter: {len(ttp_adap_counts_by_key)} reports")
        print("[dbg] Counts against ground truth: tp=matched codes, fp=predicted codes not in ground truth, fn=missed ground-truth codes, gt=ground-truth code count.")
        print("[dbg] TTPDrill / original repository — per-report counts, first up to 5 normalized report IDs (display limit, not experiment scope):")
        for key in list(ttp_repro_counts_by_key.keys())[:5]:
            print(f"  {key}: {ttp_repro_counts_by_key[key]}")
        print("[dbg] TTPDrill / framework adapter — per-report counts, first up to 5 normalized report IDs (display limit, not experiment scope):")
        for key in list(ttp_adap_counts_by_key.keys())[:5]:
            print(f"  {key}: {ttp_adap_counts_by_key[key]}")

    # Build per-report joined table keyed by the paper's report rows
    rows = []
    for _, row in paper_df.iterrows():
        key = row["key"]
        gt = int(row["GT"])
        paper_tp = int(row["Attackg_TP"])   # GT - FN
        paper_fp = int(row["Attackg_FP"]) # paper's FP for AttacKG
        paper_fn = int(row["Attackg_FN"]) # paper's FN for AttacKG
        ttpdrill_paper_tp = int(row["TTP_TP"])
        ttpdrill_paper_fp = int(row["TTP_FP"])
        ttpdrill_paper_fn = int(row["TTP_FN"])

        rep = rep_counts_by_key.get(key, Counts(tp=0, fp=0, fn=0 if key not in rep_counts_by_key else 0, gt=0))
        ada = ada_counts_by_key.get(key, Counts(tp=0, fp=0, fn=0 if key not in ada_counts_by_key else 0, gt=0))
        ttpo = ttp_repro_counts_by_key.get(key, Counts(tp=0, fp=0, fn=0 if key not in ttp_repro_counts_by_key else 0, gt=0))
        ttpa = ttp_adap_counts_by_key.get(key, Counts(tp=0, fp=0, fn=0 if key not in ttp_adap_counts_by_key else 0, gt=0))

        rows.append({
            "report": row["report"],
            "key": key,
            "GT": gt,
            # Paper/original
            "orig_tp": paper_tp,
            "orig_fp": paper_fp,
            "orig_fn": paper_fn,
            # Reproduced
            "repr_tp": rep.tp,
            "repr_fp": rep.fp,
            "repr_fn": rep.fn,
            # Adapter
            "adap_tp": ada.tp,
            "adap_fp": ada.fp,
            "adap_fn": ada.fn,
            # TTPDrill original (paper)
            "ttp_orig_tp": ttpdrill_paper_tp,
            "ttp_orig_fp": ttpdrill_paper_fp,
            "ttp_orig_fn": ttpdrill_paper_fn,
            # TTPDrill reproduced/adapter (our runs)
            "ttp_repr_tp": ttpo.tp,
            "ttp_repr_fp": ttpo.fp,
            "ttp_repr_fn": ttpo.fn,
            "ttp_adap_tp": ttpa.tp,
            "ttp_adap_fp": ttpa.fp,
            "ttp_adap_fn": ttpa.fn,
        })

    per_report_df = pd.DataFrame(rows)
    per_report_df.to_csv(args.per_report_out, index=False)

    # Aggregate micro counts for overall metrics
    # Original/paper from the CSV — always across ALL paper rows
    o_tp = int(per_report_df["orig_tp"].sum())
    o_fp = int(per_report_df["orig_fp"].sum())
    o_fn = int(per_report_df["orig_fn"].sum())
    to_tp = int(per_report_df["ttp_orig_tp"].sum())
    to_fp = int(per_report_df["ttp_orig_fp"].sum())
    to_fn = int(per_report_df["ttp_orig_fn"].sum())

    # For reproduced/adapter, aggregate **only over reports we actually have predictions for**
    rep_keys = set(rep_counts_by_key.keys())
    ada_keys = set(ada_counts_by_key.keys())
    ttp_or_keys = set(per_report_df["key"])  # paper originals exist for all rows
    ttp_rr_keys = set(ttp_repro_counts_by_key.keys())
    ttp_ad_keys = set(ttp_adap_counts_by_key.keys())

    if args.verbose:
        print(f"[dbg] keys — attackg repr={len(rep_keys)}, adap={len(ada_keys)}, "
            f"ttp repr={len(ttp_rr_keys)}, adap={len(ttp_ad_keys)}")

    rep_mask = per_report_df["key"].isin(rep_keys)
    ada_mask = per_report_df["key"].isin(ada_keys)
    trop_mask = per_report_df["key"].isin(ttp_rr_keys)
    taop_mask = per_report_df["key"].isin(ttp_ad_keys)

    r_tp = int(per_report_df.loc[rep_mask, "repr_tp"].sum())
    r_fp = int(per_report_df.loc[rep_mask, "repr_fp"].sum())
    r_fn = int(per_report_df.loc[rep_mask, "repr_fn"].sum())

    a_tp = int(per_report_df.loc[ada_mask, "adap_tp"].sum())
    a_fp = int(per_report_df.loc[ada_mask, "adap_fp"].sum())
    a_fn = int(per_report_df.loc[ada_mask, "adap_fn"].sum())

    tr_tp = int(per_report_df.loc[trop_mask, "ttp_repr_tp"].sum())
    tr_fp = int(per_report_df.loc[trop_mask, "ttp_repr_fp"].sum())
    tr_fn = int(per_report_df.loc[trop_mask, "ttp_repr_fn"].sum())

    ta_tp = int(per_report_df.loc[taop_mask, "ttp_adap_tp"].sum())
    ta_fp = int(per_report_df.loc[taop_mask, "ttp_adap_fp"].sum())
    ta_fn = int(per_report_df.loc[taop_mask, "ttp_adap_fn"].sum())

    o_p, o_r, o_f1 = prf_from_counts(o_tp, o_fp, o_fn)
    r_p, r_r, r_f1 = prf_from_counts(r_tp, r_fp, r_fn)
    a_p, a_r, a_f1 = prf_from_counts(a_tp, a_fp, a_fn)
    to_p, to_r, to_f1 = prf_from_counts(to_tp, to_fp, to_fn)
    tr_p, tr_r, tr_f1 = prf_from_counts(tr_tp, tr_fp, tr_fn)
    ta_p, ta_r, ta_f1 = prf_from_counts(ta_tp, ta_fp, ta_fn)

    overall = pd.DataFrame(
        {
            # AttacKG
            "attackg_original": [o_p, o_r, o_f1],
            "attackg_reproduced": [r_p, r_r, r_f1],
            "attackg_adapter": [a_p, a_r, a_f1],
            # TTPDrill
            "ttpdrill_original": [to_p, to_r, to_f1],
            "ttpdrill_reproduced": [tr_p, tr_r, tr_f1],
            "ttpdrill_adapter": [ta_p, ta_r, ta_f1],
        },
        index=["precision", "recall", "f1"],
    )
    overall.to_csv(args.overall_out)

    # Also compute DOC-AVERAGED (macro) PRF as our reporting convention.
    # The published Table 4 counts do not establish the paper's averaging method.
    o_pM, o_rM, o_f1M = aggregate_macro_from_df(per_report_df, "orig_tp", "orig_fp", "orig_fn")
    r_pM, r_rM, r_f1M = aggregate_macro_from_df(per_report_df, "repr_tp", "repr_fp", "repr_fn", mask=rep_mask)
    a_pM, a_rM, a_f1M = aggregate_macro_from_df(per_report_df, "adap_tp", "adap_fp", "adap_fn", mask=ada_mask)
    to_pM, to_rM, to_f1M = aggregate_macro_from_df(per_report_df, "ttp_orig_tp", "ttp_orig_fp", "ttp_orig_fn")
    tr_pM, tr_rM, tr_f1M = aggregate_macro_from_df(per_report_df, "ttp_repr_tp", "ttp_repr_fp", "ttp_repr_fn", mask=trop_mask)
    ta_pM, ta_rM, ta_f1M = aggregate_macro_from_df(per_report_df, "ttp_adap_tp", "ttp_adap_fp", "ttp_adap_fn", mask=taop_mask)

    macro = pd.DataFrame(
        {
            "original": [o_pM, o_rM, o_f1M],
            "reproduced": [r_pM, r_rM, r_f1M],
            "adapter": [a_pM, a_rM, a_f1M],
            "ttpdrill_original": [to_pM, to_rM, to_f1M],
            "ttpdrill_reproduced": [tr_pM, tr_rM, tr_f1M],
            "ttpdrill_adapter": [ta_pM, ta_rM, ta_f1M],
        },
        index=["precision", "recall", "f1"],
    )
    macro_out = args.overall_out.with_name(args.overall_out.stem + "_macro" + args.overall_out.suffix)
    macro.to_csv(macro_out)
    paper_scores = {}
    for tool, paper_prefix, candidates in (
        ("AttacKG", "orig", [("original", "reproduced", rep_keys), ("framework", "adapter", ada_keys)]),
        ("TTPDrill", "ttp_orig", [("original", "ttpdrill_reproduced", ttp_rr_keys), ("framework", "ttpdrill_adapter", ttp_ad_keys)]),
    ):
        paper_column = "original" if tool == "AttacKG" else "ttpdrill_original"
        micro_paper_column = "attackg_original" if tool == "AttacKG" else "ttpdrill_original"
        for metric in ("precision", "recall", "f1"):
            paper_scores[tool + " document average " + metric] = float(macro.loc[metric, paper_column])
            paper_scores[tool + " micro " + metric] = float(overall.loc[metric, micro_paper_column])
        for backend, column, present in candidates:
            if not present:
                continue
            if len(present) != len(text_files):
                raise RuntimeError("Incomplete " + tool + " " + backend + " report set")
            scores = {}
            micro_column = ("attackg_" + column) if tool == "AttacKG" else column
            for metric in ("precision", "recall", "f1"):
                scores[tool + " document average " + metric] = float(macro.loc[metric, column])
                scores[tool + " micro " + metric] = float(overall.loc[metric, micro_column])
            save_scores(args.out_dir, backend, tool, scores)
    markdown(args.out_dir, "AttacKG Table 4: eight-report comparison", paper_scores,
             "Profile: " + args.profile + ". Reproduction scope: " + str(len(text_files)) + " selected reports; "
             "the published Table 4 covers 16. Document-average F1 is our primary reporting convention, "
             "not a confirmed paper averaging method. Both document-average and micro metrics are shown. "
             "Missing execution paths are unavailable, not zero.", include_buchel=False)
    from helpers.attackg_comparison import add_document_comparison
    add_document_comparison(args.out_dir, per_report_df, labels_by_key,
                            {"repr": rep_keys, "adap": ada_keys,
                             "ttp_repr": ttp_rr_keys, "ttp_adap": ttp_ad_keys})

    # Also print concise summaries
    def _fmt3(x: float) -> str:
        return f"{x:.3f}"

    print("=== EXPERIMENT RUNNER OUTPUT | AttacKG micro P/R/F1 ===")
    print("Paper reference (all 8 reports): P={} R={} F1={}".format(_fmt3(o_p), _fmt3(o_r), _fmt3(o_f1)))
    if rep_keys:
        print("Original repository (n={}) : P={} R={} F1={}".format(len(rep_keys), _fmt3(r_p), _fmt3(r_r), _fmt3(r_f1)))
    else:
        print("Original repository: not available")
    if ada_keys:
        print("Framework adapter (n={}) : P={} R={} F1={}".format(len(ada_keys), _fmt3(a_p), _fmt3(a_r), _fmt3(a_f1)))
    else:
        print("Framework adapter: not available")
    print("\n=== EXPERIMENT RUNNER OUTPUT | TTPDrill micro P/R/F1 ===")
    print("Paper reference (all 8 reports): P={} R={} F1={}".format(_fmt3(to_p), _fmt3(to_r), _fmt3(to_f1)))
    if ttp_rr_keys:
        print("Original repository (n={}) : P={} R={} F1={}".format(len(ttp_rr_keys), _fmt3(tr_p), _fmt3(tr_r), _fmt3(tr_f1)))
    else:
        print("Original repository: not available")
    if ttp_ad_keys:
        print("Framework adapter (n={}) : P={} R={} F1={}".format(len(ttp_ad_keys), _fmt3(ta_p), _fmt3(ta_r), _fmt3(ta_f1)))
    else:
        print("Framework adapter: not available")


if __name__ == "__main__":
    main()

   

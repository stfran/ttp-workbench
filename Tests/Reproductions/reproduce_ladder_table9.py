#!/usr/bin/env python3
"""Reproduce Table 9-style evaluation for LADDER vs AttacKG vs TTPDrill on 5 reports.

Outputs
- PROJ_ROOT/Outputs/LADDER_table9_repro/summary.{json,csv}
- Per tool: predictions_raw.json + predictions_normalized.json
"""

from __future__ import annotations

import csv
import json
import os
import re
import argparse
import sys
import subprocess
from types import SimpleNamespace
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple
from helpers.reporting import HERE, PROJECT, EXTERNAL, save_scores, markdown, check_predictions, tool_directory, output_section
sys.path.insert(0, str(PROJECT))


# ----------------------------
# Fixed configuration
# ----------------------------

# Project root is two directories up from this file.
PROJ_ROOT = PROJECT

# Expected dataset location.
DATA_DIR = HERE / "data/LADDER_table_9_data"

OUT_ROOT = HERE / "experiments/ladder_table9/runs/manual/results"

# Adapter execution settings
ENGINE = "podman"
VERBOSE = True

TOOLS = ["ladder", "ttpdrill", "attackg", ]


# ----------------------------
# Parsing + normalization
# ----------------------------

TTP_RE = re.compile(r"^T\d{4}(?:\.\d{3})?$")


def _load_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _load_text(path: Path) -> str:
    with path.open("r", encoding="utf-8", errors="replace") as f:
        return f.read()


def _normalize_ttp_id(x: Any) -> Optional[str]:
    if not isinstance(x, str):
        return None
    s = x.strip()
    return s if TTP_RE.match(s) else None


def extract_ttp_ids(obj: Any) -> List[str]:
    """Best-effort extraction of ATT&CK technique IDs from common adapter outputs."""
    found: List[str] = []

    def add(v: Any) -> None:
        t = _normalize_ttp_id(v)
        if t is not None:
            found.append(t)

    if obj is None:
        return []

    if isinstance(obj, str):
        add(obj)
        return sorted(set(found))

    if isinstance(obj, (list, tuple, set)):
        for item in obj:
            if isinstance(item, str):
                add(item)
            elif isinstance(item, Mapping):
                for k in ("technique", "ttp", "attack_id", "mitre_id", "id", "code"):
                    if k in item:
                        add(item.get(k))
                if "ttps" in item:
                    found.extend(extract_ttp_ids(item["ttps"]))
        return sorted(set(found))

    if isinstance(obj, Mapping):
        # Dict keyed by technique IDs (e.g., LADDER_results)
        for k in obj.keys():
            if isinstance(k, str) and TTP_RE.match(k.strip()):
                add(k)

        # Common nested fields
        for k in ("ttps", "techniques", "attack_patterns", "predictions", "results"):
            if k in obj:
                found.extend(extract_ttp_ids(obj[k]))

        # Values may contain more IDs
        for v in obj.values():
            found.extend(extract_ttp_ids(v))

        return sorted(set(found))

    return []


def normalize_per_report_payload(raw: Any, ids: List[str]) -> Dict[str, Any]:
    """Normalize arbitrary adapter payloads into a mapping {report_id -> per-report object}.

    Handles common wrapper shapes:
      - {"results": <payload>, "aux": ...}
      - {"<id>": <payload>, ...}
      - [<payload aligned with ids>]
      - [ {"id": "...", ...}, ... ]
    """

    if isinstance(raw, Mapping) and "results" in raw and len(raw) <= 2:
        return normalize_per_report_payload(raw["results"], ids)

    if isinstance(raw, Mapping):
        keys = list(raw.keys())
        if keys and all(isinstance(k, str) for k in keys) and any(k in set(ids) for k in keys):
            return {rid: raw.get(rid) for rid in ids}

        if "id" in raw and len(ids) == 1:
            return {ids[0]: raw}

    if isinstance(raw, list):
        if raw and all(isinstance(x, Mapping) and "id" in x for x in raw):
            m = {str(x["id"]): x for x in raw}
            return {rid: m.get(rid) for rid in ids}
        if len(raw) == len(ids):
            return {rid: raw[i] for i, rid in enumerate(ids)}

    return {ids[0]: raw} if ids else {}


def normalize_sentence_texts(raw: Any, ids: List[str]) -> Dict[str, List[str]]:
    """Extract sentence texts (in order) from per-report payloads that include a 'sentences' field."""
    per = normalize_per_report_payload(raw, ids)
    out: Dict[str, List[str]] = {}
    for rid, obj in per.items():
        sents: List[str] = []
        if isinstance(obj, Mapping) and isinstance(obj.get("sentences"), list):
            for s in obj["sentences"]:
                if isinstance(s, Mapping) and isinstance(s.get("text"), str):
                    sents.append(s["text"])
        out[rid] = sents
    return out


# ----------------------------
# Data model
# ----------------------------

@dataclass
class ReportExample:
    meta_path: Path
    report_id: str
    text_path: Path
    text: str
    ground_truth: List[str]
    raw_meta: Dict[str, Any]


def load_examples(data_dir: Path) -> List[ReportExample]:
    json_files = sorted(data_dir.glob("*.json"))
    if not json_files:
        raise FileNotFoundError(f"No .json files found under: {data_dir}")

    examples: List[ReportExample] = []
    for jp in json_files:
        meta = _load_json(jp)
        report_id = jp.stem

        text_rel = (
            meta.get("report_text_path")
            or meta.get("report_text_file")
            or meta.get("report_text_file_path")
            or meta.get("text_file_path")
        )
        if not text_rel:
            raise KeyError(f"Missing text path key in {jp.name} (expected text_file_path or report_text_path)")

        text_path = Path(text_rel)
        if not text_path.is_absolute():
            text_path = (data_dir / text_path).resolve()
        if not text_path.exists():
            # Handle './file.txt' or stale relative paths by falling back to filename in dir
            alt = (data_dir / Path(text_rel).name).resolve()
            if alt.exists():
                text_path = alt
            else:
                raise FileNotFoundError(f"Text file not found for {jp.name}: {text_path}")

        text = _load_text(text_path)

        gt = meta.get("ground_truth_ttps")
        if not isinstance(gt, list):
            raise KeyError(f"Missing/invalid ground_truth_ttps in {jp.name}")
        gt_norm = sorted({t for t in (_normalize_ttp_id(x) for x in gt) if t is not None})

        examples.append(
            ReportExample(
                meta_path=jp,
                report_id=report_id,
                text_path=text_path,
                text=text,
                ground_truth=gt_norm,
                raw_meta=meta,
            )
        )

    return examples


# ----------------------------
# Metrics
# ----------------------------

@dataclass
class Counts:
    tp: int
    fp: int
    fn: int


def prf_from_counts(tp: int, fp: int, fn: int) -> Tuple[float, float, float]:
    p = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    r = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1 = (2 * p * r / (p + r)) if (p + r) > 0 else 0.0
    return p, r, f1


def score_sets(pred: Sequence[str], gt: Sequence[str]) -> Tuple[Counts, Tuple[float, float, float]]:
    ps = set(pred)
    gs = set(gt)
    tp = len(ps & gs)
    fp = len(ps - gs)
    fn = len(gs - ps)
    return Counts(tp=tp, fp=fp, fn=fn), prf_from_counts(tp, fp, fn)


@dataclass
class Summary:
    tool: str
    mode: str  # "original" or "reproduced"
    tp: int
    fp: int
    fn: int
    micro_precision: float
    micro_recall: float
    micro_f1: float
    macro_precision: float
    macro_recall: float
    macro_f1: float


def summarize(tool: str, mode: str, per_report: Dict[str, Tuple[Counts, Tuple[float, float, float]]]) -> Summary:
    tp = sum(c.tp for c, _ in per_report.values())
    fp = sum(c.fp for c, _ in per_report.values())
    fn = sum(c.fn for c, _ in per_report.values())
    mp, mr, mf1 = prf_from_counts(tp, fp, fn)

    ps = [prf[0] for _, prf in per_report.values()]
    rs = [prf[1] for _, prf in per_report.values()]
    fs = [prf[2] for _, prf in per_report.values()]
    macro_p = sum(ps) / len(ps) if ps else 0.0
    macro_r = sum(rs) / len(rs) if rs else 0.0
    macro_f = sum(fs) / len(fs) if fs else 0.0

    return Summary(
        tool=tool,
        mode=mode,
        tp=tp,
        fp=fp,
        fn=fn,
        micro_precision=mp,
        micro_recall=mr,
        micro_f1=mf1,
        macro_precision=macro_p,
        macro_recall=macro_r,
        macro_f1=macro_f,
    )


def summaries_to_rows(summaries: Sequence[Summary]) -> List[Dict[str, Any]]:
    return [
        {
            "tool": s.tool,
            "mode": s.mode,
            "tp": s.tp,
            "fp": s.fp,
            "fn": s.fn,
            "micro_precision": s.micro_precision,
            "micro_recall": s.micro_recall,
            "micro_f1": s.micro_f1,
            "macro_precision": s.macro_precision,
            "macro_recall": s.macro_recall,
            "macro_f1": s.macro_f1,
        }
        for s in summaries
    ]


def print_table(rows: List[Dict[str, Any]]) -> None:
    headers = [
        "tool",
        "mode",
        "tp",
        "fp",
        "fn",
        "micro_precision",
        "micro_recall",
        "micro_f1",
        "macro_precision",
        "macro_recall",
        "macro_f1",
    ]

    def fmt(v: Any) -> str:
        if isinstance(v, float):
            return f"{v:.3f}"
        return str(v)

    widths = {h: max(len(h), *(len(fmt(r.get(h, ""))) for r in rows)) for h in headers}

    line = " | ".join(h.ljust(widths[h]) for h in headers)
    sep = "-+-".join("-" * widths[h] for h in headers)
    print(line)
    print(sep)
    for r in rows:
        print(" | ".join(fmt(r.get(h, "")).ljust(widths[h]) for h in headers))


# ----------------------------
# Adapter running
# ----------------------------

def run_adapter(tool: str, *, texts: List[str], ids: List[str], out_dir: Path) -> Any:
    from helpers.reporting import isolate_framework_cache
    isolate_framework_cache()
    out_dir.mkdir(parents=True, exist_ok=True)

    if tool == "attackg":
        from Framework.adapters.attackg_adapter import predict_texts as _predict
        bulk = False
    elif tool == "ladder":
        from Framework.adapters.ladder_adapter import predict_texts as _predict
        bulk = True
    elif tool == "ttpdrill":
        from Framework.adapters.ttpdrill_adapter import predict_texts as _predict
        bulk = False
        bulk_monitor = False
    else:
        raise ValueError(f"Unknown tool: {tool}")

    with output_section("FRAMEWORK ADAPTER OUTPUT", tool + " / framework (including container diagnostics)"):
        results, aux = _predict(
            texts=texts,
            ids=ids,
            save_dir=str(out_dir / "raw"),
            tmp_root=OUT_ROOT.parent / "tmp" / tool,
            engine=ENGINE,
            verbose=VERBOSE,
            bulk=bulk,
            gpus="all"
        )
    return {"results": results, "aux": aux}


def normalize_adapter_outputs(raw: Any, ids: List[str]) -> Dict[str, List[str]]:
    """Convert raw adapter payload into {report_id -> [Txxxx, ...]}.

    Preference:
    - If a per-report object has a top-level 'ttps' list, use that.
    - Otherwise, fall back to best-effort extraction across the object.
    """

    per = normalize_per_report_payload(raw, ids)
    out: Dict[str, List[str]] = {}

    for rid, obj in per.items():
        if isinstance(obj, Mapping) and "ttps" in obj:
            out[rid] = extract_ttp_ids(obj.get("ttps"))
        else:
            out[rid] = extract_ttp_ids(obj)

    return out


def json_safe(obj: Any) -> Any:
    try:
        json.dumps(obj)
        return obj
    except Exception:
        return repr(obj)


# ----------------------------
# Main (no args)
# ----------------------------


def main() -> int:
    from helpers.reporting import install_exit_handler
    install_exit_handler()
    global DATA_DIR, OUT_ROOT, ENGINE, VERBOSE, TOOLS
    parser = argparse.ArgumentParser(description="LADDER paper Table 9: five reports, two execution paths")
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR)
    parser.add_argument("--out-dir", type=Path, default=OUT_ROOT)
    parser.add_argument("--backend", choices=["framework", "original"], default="framework")
    parser.add_argument("--tool", choices=TOOLS)
    parser.add_argument("--profile", choices=["smoke", "full"], default="full")
    parser.add_argument("--engine", default="podman", choices=["podman", "docker"])
    parser.add_argument("--ladder-root", type=Path, default=EXTERNAL / "LADDER")
    parser.add_argument("--attackg-root", type=Path, default=EXTERNAL / "AttacKG")
    parser.add_argument("--ttpdrill-root", type=Path, default=EXTERNAL / "TTPDrill")
    parser.add_argument("--corenlp-home", type=Path, default=EXTERNAL / "TTPDrill/stanford-corenlp-full-2018-10-05")
    parser.add_argument("--report-only", action="store_true")
    args = parser.parse_args()
    DATA_DIR, OUT_ROOT, ENGINE = args.data_dir.resolve(), args.out_dir.resolve(), args.engine
    if args.tool: TOOLS = [args.tool]
    paper = {"ladder micro_f1": 0.64, "attackg micro_f1": 0.15, "ttpdrill micro_f1": 0.14}
    note = "Profile: " + args.profile + ". Paper columns quote published Table 9 F1. Embedded reference predictions are retained separately in summary files; they are not upstream executions."
    if args.report_only:
        markdown(OUT_ROOT, "LADDER Table 9", paper, note)
        return 0
    if not DATA_DIR.exists():
        raise FileNotFoundError(f"Expected dataset directory does not exist: {DATA_DIR}")

    examples = load_examples(DATA_DIR)
    if len(examples) != 5: raise RuntimeError("Expected all five LADDER reports")
    if args.profile == "smoke": examples = examples[:1]
    ids = [ex.report_id for ex in examples]
    texts = [ex.text for ex in examples]

    OUT_ROOT.mkdir(parents=True, exist_ok=True)

    # --- ORIGINAL predictions from the JSON metadata ---
    original_preds: Dict[str, Dict[str, List[str]]] = {t: {} for t in ("ladder", "attackg", "ttpdrill")}

    for ex in examples:
        meta = ex.raw_meta

        if "LADDER_results" in meta:
            original_preds["ladder"][ex.report_id] = extract_ttp_ids(meta["LADDER_results"])

        if "AttacKG_results" in meta:
            original_preds["attackg"][ex.report_id] = extract_ttp_ids(meta["AttacKG_results"])

        if "TTPDrill_results" in meta:
            original_preds["ttpdrill"][ex.report_id] = extract_ttp_ids(meta["TTPDrill_results"])

    summaries: List[Summary] = []

    for tool in TOOLS:
        per_report: Dict[str, Tuple[Counts, Tuple[float, float, float]]] = {}
        for ex in examples:
            pred = original_preds.get(tool, {}).get(ex.report_id, [])
            per_report[ex.report_id] = score_sets(pred, ex.ground_truth)
        summaries.append(summarize(tool=tool, mode="paper_reference", per_report=per_report))

    # --- REPRODUCED predictions from adapters (sequential) ---
    reproduced_preds: Dict[str, Dict[str, List[str]]] = {}

    for tool in TOOLS:
        tool_out = tool_directory(OUT_ROOT, tool, args.backend)
        tool_out.mkdir(parents=True, exist_ok=True)
        parsed_dir = tool_out / "parsed"
        parsed_dir.mkdir(exist_ok=True)
        if args.backend == "framework":
            raw_payload = run_adapter(tool, texts=texts, ids=ids, out_dir=tool_out)
            check_predictions(raw_payload["results"], ids)
        elif tool == "ladder":
            input_dir = OUT_ROOT.parent / "tmp" / tool / "input"
            input_dir.mkdir(parents=True, exist_ok=True)
            for rid, text in zip(ids, texts): (input_dir / (rid + ".txt")).write_text(text)
            raw_dir = tool_out / "raw"
            with output_section("RAW TOOL OUTPUT", "LADDER / original"):
                subprocess.run([sys.executable, str(args.ladder_root / "attack_pattern/ladder_attack_pattern_cli.py"),
                                str(input_dir), "--output", str(raw_dir)], cwd=args.ladder_root / "attack_pattern", check=True)
            raw_payload = {"results": [{"id": rid, "ttps": extract_ttp_ids(_load_json(raw_dir / (rid + ".json")))} for rid in ids]}
        else:
            from reproduce_attackg_table4 import run_attackg_original, run_ttpdrill_original
            original_args = SimpleNamespace(attackg_root=args.attackg_root, ttpdrill_root=args.ttpdrill_root,
                corenlp_home=args.corenlp_home, corenlp_port=9000, text_dir=DATA_DIR, verbose=True,
                out_dir=OUT_ROOT, tool_out_dir=tool_out)
            fn = run_attackg_original if tool == "attackg" else run_ttpdrill_original
            results = fn(original_args, [ex.text_path.name for ex in examples])
            for rid, result in zip(ids, results): result["id"] = rid
            raw_payload = {"results": results}

        # Save returned parsed payload; actual tool raw outputs are under raw/.
        (parsed_dir / "adapter_outputs.json" if args.backend == "framework" else parsed_dir / "original_outputs.json").write_text(
            json.dumps(json_safe(raw_payload), indent=2), encoding="utf-8"
        )

        # If this tool returns sentence segmentation, save it and warn on changes across runs.
        if tool == "ladder":
            sent_path = parsed_dir / "sentences_texts.json"
            prev: Optional[Dict[str, List[str]]] = None
            if sent_path.exists():
                try:
                    prev = json.loads(sent_path.read_text(encoding="utf-8"))
                except Exception:
                    prev = None

            sentences_map = normalize_sentence_texts(raw_payload, ids)
            sent_path.write_text(json.dumps(sentences_map, indent=2), encoding="utf-8")

            if prev is not None:
                for rid in ids:
                    a = prev.get(rid, [])
                    b = sentences_map.get(rid, [])
                    if a != b:
                        # Find first differing index for a quick hint.
                        i = 0
                        while i < min(len(a), len(b)) and a[i] == b[i]:
                            i += 1
                        print(
                            f"[WARN] LADDER sentence mismatch for {rid}: "
                            f"prev_len={len(a)} new_len={len(b)} first_diff_idx={i}"
                        )

        # Normalize per-report technique IDs
        norm = normalize_adapter_outputs(raw_payload, ids)
        norm = {rid: sorted(set(v)) for rid, v in norm.items()}
        reproduced_preds[tool] = norm

        (parsed_dir / "predictions_normalized.json").write_text(
            json.dumps(norm, indent=2), encoding="utf-8"
        )

        per_report = {}
        for ex in examples:
            pred = reproduced_preds.get(tool, {}).get(ex.report_id, [])
            per_report[ex.report_id] = score_sets(pred, ex.ground_truth)
        result_summary = summarize(tool=tool, mode=args.backend, per_report=per_report)
        summaries.append(result_summary)
        score_row = summaries_to_rows([result_summary])[0]
        save_scores(OUT_ROOT, args.backend, tool, {tool + " " + k: v for k, v in score_row.items() if k.startswith(("micro_", "macro_"))})

    # --- Save/print summary ---
    rows = summaries_to_rows(summaries)

    for tool in TOOLS:
        summary_dir = tool_directory(OUT_ROOT, tool, args.backend)
        (summary_dir / "summary.json").write_text(json.dumps([row for row in rows if row["tool"] == tool], indent=2), encoding="utf-8")

    if rows:
        with (OUT_ROOT / (args.backend + "_" + "_".join(TOOLS) + "_summary.csv")).open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)

    print("\n=== EXPERIMENT RUNNER OUTPUT | LADDER Table 9: TP/FP/FN and micro/macro P/R/F1 ===")
    print_table(rows)
    print(f"Saved: {OUT_ROOT / (args.backend + '_' + '_'.join(TOOLS) + '_summary.csv')}")

    markdown(OUT_ROOT, "LADDER Table 9", paper, note)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

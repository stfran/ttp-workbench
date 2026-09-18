"""Comparison and reporting for paired framework/native PoC smoke tests.

This module intentionally knows nothing about containers or native tool
environments.  Keeping it pure makes the fidelity verdict independently
testable and avoids allowing either execution path to define the other's
answer.
"""
from __future__ import annotations

import csv
import json
import re
from pathlib import Path
from typing import Any, Iterable


STRICT_HYBRID_TOOLS = {
    "AttacKG",
    "LADDER",
    "TTPDrill",
    "Orbinato",
    "RAF-AG",
    "rcATT",
    "SeqMask",
    "TRAM",
}
DIAGNOSTIC_HYBRID_TOOLS = {"Buchel", "TTP-LLM"}
VALID_POLICIES = {"diagnostic", "hybrid", "strict"}

_ATTACK_CODE = re.compile(r"^(?:TA\d{4}|T\d{4}(?:\.\d{3})?)$", re.IGNORECASE)


def normalize_codes(values: Any) -> list[str]:
    """Return sorted unique ATT&CK tactic/technique identifiers.

    Native readers should extract the intended prediction fields before
    calling this function.  Recursive handling is deliberately conservative:
    only strings that are entirely an ATT&CK identifier are retained.
    """
    found: set[str] = set()

    def visit(value: Any) -> None:
        if isinstance(value, str):
            candidate = value.strip().upper()
            if _ATTACK_CODE.fullmatch(candidate):
                found.add(candidate)
        elif isinstance(value, dict):
            for key in ("code", "technique_id", "techId", "ttp", "id"):
                if key in value:
                    visit(value[key])
            if "ttps" in value:
                visit(value["ttps"])
            if "predictions" in value:
                visit(value["predictions"])
        elif isinstance(value, (list, tuple, set)):
            for item in value:
                visit(item)

    visit(values)
    return sorted(found)


def validate_results(results: Any, expected_ids: Iterable[str], backend: str) -> list[str]:
    expected = [str(value) for value in expected_ids]
    expected_set = set(expected)
    if not isinstance(results, list):
        return [f"{backend} returned {type(results).__name__}; expected a list"]

    failures: list[str] = []
    seen: list[str] = []
    for index, result in enumerate(results):
        if not isinstance(result, dict):
            failures.append(f"{backend} result {index} is not an object")
            continue
        result_id = result.get("id")
        if result_id is None:
            failures.append(f"{backend} result {index} has no id")
            continue
        result_id = str(result_id)
        seen.append(result_id)
        if result.get("error"):
            failures.append(f"{backend} {result_id}: {result['error']}")
        if result.get("ttps") is None:
            failures.append(f"{backend} {result_id}: missing ttps")

    seen_set = set(seen)
    failures.extend(f"{backend} missing result {value}" for value in sorted(expected_set - seen_set))
    failures.extend(f"{backend} returned unexpected result {value}" for value in sorted(seen_set - expected_set))
    failures.extend(
        f"{backend} returned duplicate result {value}"
        for value in sorted({value for value in seen if seen.count(value) > 1})
    )
    return failures


def _is_gated(tool: str, policy: str) -> bool:
    if policy == "strict":
        return True
    if policy == "diagnostic":
        return False
    if tool not in STRICT_HYBRID_TOOLS | DIAGNOSTIC_HYBRID_TOOLS:
        raise ValueError(f"No hybrid fidelity classification for {tool}")
    return tool in STRICT_HYBRID_TOOLS


def compare_results(
    tool: str,
    framework_results: Any,
    native_results: Any,
    expected_ids: Iterable[str],
    *,
    policy: str = "hybrid",
) -> dict[str, Any]:
    if policy not in VALID_POLICIES:
        raise ValueError(f"Unknown fidelity policy: {policy}")
    ids = [str(value) for value in expected_ids]
    failures = validate_results(framework_results, ids, "framework")
    failures += validate_results(native_results, ids, "native")
    framework_by_id = {
        str(item["id"]): item for item in framework_results or []
        if isinstance(item, dict) and item.get("id") is not None
    }
    native_by_id = {
        str(item["id"]): item for item in native_results or []
        if isinstance(item, dict) and item.get("id") is not None
    }

    rows: list[dict[str, Any]] = []
    for record_id in ids:
        framework_codes = set(normalize_codes(framework_by_id.get(record_id, {}).get("ttps", [])))
        native_codes = set(normalize_codes(native_by_id.get(record_id, {}).get("ttps", [])))
        union = framework_codes | native_codes
        intersection = framework_codes & native_codes
        rows.append({
            "id": record_id,
            "exact_match": framework_codes == native_codes,
            "jaccard": len(intersection) / len(union) if union else 1.0,
            "framework": sorted(framework_codes),
            "native": sorted(native_codes),
            "intersection": sorted(intersection),
            "framework_only": sorted(framework_codes - native_codes),
            "native_only": sorted(native_codes - framework_codes),
            "framework_error": framework_by_id.get(record_id, {}).get("error", ""),
            "native_error": native_by_id.get(record_id, {}).get("error", ""),
        })

    exact = sum(bool(row["exact_match"]) for row in rows)
    mismatch = exact != len(rows)
    gated = _is_gated(tool, policy)
    if failures:
        verdict, success = "FAIL", False
    elif mismatch and gated:
        verdict, success = "FAIL", False
    elif mismatch:
        verdict, success = "PASS", True
    else:
        verdict, success = "PASS", True

    return {
        "tool": tool,
        "policy": policy,
        "gated": gated,
        "verdict": verdict,
        "success": success,
        "agreement": "EXACT" if not mismatch else "DIFFERENT",
        "expected_reports": len(ids),
        "exact_reports": exact,
        "mean_jaccard": sum(row["jaccard"] for row in rows) / len(rows) if rows else 1.0,
        "contract_failures": failures,
        "per_report": rows,
    }


def write_fidelity_outputs(directory: Path, comparison: dict[str, Any]) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "comparison.json").write_text(
        json.dumps(comparison, indent=2) + "\n", encoding="utf-8"
    )
    fields = [
        "id", "exact_match", "jaccard", "framework", "native",
        "intersection", "framework_only", "native_only",
        "framework_error", "native_error",
    ]
    with (directory / "comparison.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for item in comparison["per_report"]:
            row = dict(item)
            for key in ("framework", "native", "intersection", "framework_only", "native_only"):
                row[key] = " ".join(row[key])
            writer.writerow({key: row.get(key, "") for key in fields})

    lines = [
        f"# {comparison['tool']} native/framework fidelity",
        "",
        f"Verdict: **{comparison['verdict']}**  ",
        f"Policy: `{comparison['policy']}` ({'gated' if comparison['gated'] else 'diagnostic'})  ",
        f"Agreement: **{comparison['agreement']}**  ",
        f"Exact reports: {comparison['exact_reports']}/{comparison['expected_reports']}  ",
        f"Mean Jaccard agreement: {comparison['mean_jaccard']:.4f}",
        "",
    ]
    if comparison["contract_failures"]:
        lines += ["## Contract failures", ""]
        lines += [f"- {failure}" for failure in comparison["contract_failures"]]
        lines.append("")
    lines += [
        "## Per-report comparison",
        "",
        "| Report | Exact | Jaccard | Framework only | Native only |",
        "|---|---:|---:|---|---|",
    ]
    for row in comparison["per_report"]:
        lines.append(
            f"| `{row['id']}` | {'yes' if row['exact_match'] else 'no'} | "
            f"{row['jaccard']:.4f} | {' '.join(row['framework_only'])} | {' '.join(row['native_only'])} |"
        )
    (directory / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

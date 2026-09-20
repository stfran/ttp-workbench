#!/usr/bin/env python3
"""Build the evaluator-facing index for one claims run."""
from __future__ import annotations

import argparse
import csv
import json
import os
from pathlib import Path
from urllib.parse import quote


HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent

CORE_SMOKE = ["AttacKG", "TTPDrill", "LADDER", "TTP-LLM"]
OPTIONAL_SMOKE = ["Buchel", "Orbinato", "rcATT", "RAF-AG", "SeqMask"]
CORE_EXPERIMENTS = [
    ("attackg_table4", "AttacKG Table 4"),
    ("ladder_table9", "LADDER Table 9"),
    ("buchel_table13", "Büchel Table 13"),
    ("ttpllm_table2", "TTP-LLM Table 2"),
]
OPTIONAL_EXPERIMENTS = [
    ("buchel_table9", "Büchel Table 9"),
    ("orbinato_fig3", "Orbinato Figure 3"),
    ("rcatt_table6", "rcATT Table 6"),
    ("rafag_table6", "RAF-AG Table 6"),
    ("seqmask_table7", "SeqMask Table 7"),
    ("seqmask_table14", "SeqMask Table 14"),
    ("seqmask_table15", "SeqMask Table 15"),
]


def link(label: str, target: Path, report: Path) -> str:
    relative = os.path.relpath(target, report.parent).replace(os.sep, "/")
    return f"[{label}]({quote(relative, safe='/._-')})"


def read_metadata(run: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    path = run / "metadata.tsv"
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            key, separator, value = line.partition("\t")
            if separator:
                values[key] = value
    return values


def read_status(run: Path) -> list[dict[str, str]]:
    path = run / "status.tsv"
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def smoke_details(row: dict[str, str], run: Path) -> tuple[str, str, str, str, str]:
    """Return framework, native, fidelity, Jaccard, and fidelity-run labels."""
    overall = row.get("status", "NOT RUN")
    relative = row.get("evidence", "")
    path = run / relative if relative else None
    if not path or not path.is_file():
        return overall, "—", "—", "—", ""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return overall, "—", "—", "—", ""
    backends = payload.get("backends") or {}
    framework = backends.get("framework", {}).get("functional_validation", {}).get("status", overall)
    native = backends.get("native", {}).get("functional_validation", {}).get("status", "—")
    fidelity = payload.get("fidelity") or {}
    mean_jaccard = fidelity.get("mean_jaccard")
    jaccard = f"{mean_jaccard:.4f}" if isinstance(mean_jaccard, (int, float)) else "—"
    return framework, native, fidelity.get("verdict", "—"), jaccard, str(payload.get("run_id", ""))


def newest_experiment_run(root: Path, slug: str) -> Path | None:
    directory = root / "experiments" / slug / "runs"
    runs = [path for path in directory.iterdir() if path.is_dir()] if directory.is_dir() else []
    return max(runs, key=lambda path: path.stat().st_mtime) if runs else None


def reproduction_evidence(repro: Path | None) -> Path | None:
    if repro is None:
        return None
    candidates = [
        repro / "REPORT.md",
        repro / "results/comparison.md",
        repro / "results/comparison.csv",
        repro / "status.tsv",
    ]
    return next((path for path in candidates if path.exists()), repro)


def reproduction_status(repro: Path | None) -> str:
    if repro is None:
        return "NOT RUN"
    ledger = repro / "status.tsv"
    if not ledger.exists():
        return "INCOMPLETE"
    entries = [line.partition("\t")[2] for line in ledger.read_text(encoding="utf-8").splitlines() if "\t" in line]
    if any(value.startswith("FAIL") for value in entries):
        return "FAIL"
    return "PASS" if entries and all(value == "PASS" for value in entries) else "INCOMPLETE"


def render(report: Path, run: Path) -> None:
    meta = read_metadata(run)
    statuses = read_status(run)
    by_phase_name = {(row["phase"], row["name"]): row for row in statuses}
    command = meta.get("command", "all")
    selection = meta.get("selection", "unknown")
    smoke_scope = meta.get("smoke_scope", selection)
    profile = meta.get("profile", "unknown")
    show_smoke = command in {"all", "smoke"}
    show_reproductions = command in {"all", "reproduce"}
    show_benchmark = command in {"all", "benchmark"}
    smoke_tools = (
        CORE_SMOKE
        if smoke_scope == "core"
        else OPTIONAL_SMOKE
        if smoke_scope == "optional"
        else CORE_SMOKE + OPTIONAL_SMOKE + ["TRAM"]
        if smoke_scope == "all"
        else []
    )
    experiments = (
        CORE_EXPERIMENTS
        if selection == "core"
        else OPTIONAL_EXPERIMENTS
        if selection == "optional"
        else CORE_EXPERIMENTS + OPTIONAL_EXPERIMENTS
        if selection == "all"
        else []
    )
    failed = [row for row in statuses if row.get("status") != "PASS"]
    overall = "PASS" if statuses and not failed and meta.get("exit_code", "0") == "0" else "FAIL or incomplete"
    run_details = (
        f"Reproduction selection: `{selection}`. Smoke-test scope: `{smoke_scope}`. Reproduction profile: `{profile}`. "
        if command == "all"
        else f"Smoke-test scope: `{smoke_scope}`. "
        if command == "smoke"
        else f"Reproduction selection: `{selection}`. Reproduction profile: `{profile}`. "
        if command == "reproduce"
        else ""
    )
    run_details += (
        f"Container engine: `{meta.get('engine', 'unknown')}`. Started: `{meta.get('started_utc', 'unknown')}`. "
        f"Finished: `{meta.get('finished_utc', 'unknown')}`."
    )
    if show_smoke:
        run_details += f" Paired fidelity: `{meta.get('fidelity_smoke', 'false')}`."

    lines = [
        "# Artifact Evaluation Run Result",
        "",
        "This is an overview of the result of running `claims.sh`, with links to per-experiment reports and evidence. It is intended as a roadmap for artifact evaluation.",
        "",
        "## Scope and Summarized Results",
        "",
        f"Run `{run.name}` — **{overall}**",
        "",
        run_details,
        "",
        "PASS indicates that all selected tools and phases ran as expected.",
        "",
        "## Claim map",
        "",
        "| Claim | Paper statement | Evidence from this run |",
        "|---|---|---|",
    ]

    if show_smoke:
        lines.append("| C1 | All ten integrated tools execute through native and framework paths with the applicable fidelity comparison. | Functionality and fidelity table below. |")
    if show_reproductions:
        lines.append("| C2 | Framework and native execution produce predictions that agree, and rerunning the experiments reported in Table 6 reproduces similar outcomes. | Reproduction table and detailed reports below. |")
    if show_benchmark:
        lines.append("| C3 | The ten evaluated TTP extraction tools perform poorly on previously unseen threat reports, even under capacity-aware evaluation. | Benchmark Figures 4–6 and 8 report below. |")

    if show_smoke:
        lines += [
            "",
            "## C1 — functionality and fidelity tests",
            "",
            "Every selected adapter receives the same three public reports from `Tests/poc_tests`. PASS means the adapter processed all three reports through the common interface without a functional-contract violation. When paired fidelity is enabled, the native implementation receives the same inputs and the normalized document-level ATT&CK sets are compared. We recommend reviewing the tool predictions. Logs contain the framework's console output. Results links open the normalized `output` from the framework and the `raw` output from the containerized tool.",
            "",
            "**Native** means the original tool is run outside a container by following its released execution instructions, with the fixes required to make it workable and without TTP-WorkBench input or output normalization. The fidelity harness extracts the TTP predictions from the tool's original output for comparison.",
            "",
            "**Framework** means the same workable tool is run within TTP-WorkBench through its adapter, which normalizes the input and output. The native and framework paths receive the same fixes needed to bring the tool to a workable state.",
            "",
            "Fidelity does not compare predictions with ground truth. Its purpose is to determine whether integration into TTP-WorkBench affects the tool's predictions. It is measured for each report using the **Jaccard agreement between the native and framework TTP prediction sets**: `|native ∩ framework| / |native ∪ framework|`. A mean Jaccard of 1.0000 means the two paths produced identical prediction sets for every report, so the framework did not change the tool's prediction performance on these inputs. The expected mean Jaccard agreement is 1.0000 except for the LLM-based Büchel and TTP-LLM integrations, whose agreement is reported diagnostically.",
            "",
            "| Adapter | Status | Framework | Native | Fidelity | Mean Jaccard | Fidelity report | Log | Results |",
            "|---|---|---|---|---|---:|---|---|---|",
        ]
        for tool in smoke_tools:
            row = by_phase_name.get(("smoke", tool), {})
            framework_status, native_status, fidelity_status, mean_jaccard, fidelity_run = smoke_details(row, run)
            log_relative = row.get("log", "")
            log_path = run / log_relative if log_relative else None
            results_root = run / "smoke/results" / tool.lower()
            fidelity_report = results_root / "fidelity" / fidelity_run / "REPORT.md" if fidelity_run else None
            normalized_path = results_root / "output"
            raw_path = results_root / "raw"
            result_links = []
            if normalized_path.is_dir():
                result_links.append(link("normalized", normalized_path, report))
            if raw_path.is_dir():
                result_links.append(link("raw", raw_path, report))
            lines.append(
                f"| {tool} | {row.get('status', 'NOT RUN')} | "
                f"{framework_status} | {native_status} | {fidelity_status} | {mean_jaccard} | "
                f"{link('report', fidelity_report, report) if fidelity_report and fidelity_report.exists() else 'Not produced'} | "
                f"{link('console output', log_path, report) if log_path and log_path.exists() else 'Not produced'} | "
                f"{' / '.join(result_links) if result_links else 'Not produced'} |"
            )

    if show_reproductions:
        repro_summary = run / "reproductions/SUMMARY.md"
        lines += [
            "",
            "## C2 — reproduction experiments",
            "",
            ("`PASS` in this table means that the reproduced experiments ran as expected. To verify that the resulting measurements are comparable to our submitted Table 6 and that our analysis follows from those results, drill down into the experiment reports. We recommend beginning with the run-wide " + link("reproduction summary", repro_summary, report) + ", then opening each experiment's detailed report from its table row.") if repro_summary.exists() else "`PASS` in this table means that the reproduced experiments ran as expected. To verify that the resulting measurements are comparable to our submitted Table 6 and that our analysis follows from those results, drill down into the experiment reports. A run-wide reproduction summary was not produced.",
            "",
            "| Experiment, in execution order | Status | Detailed evidence |",
            "|---|---|---|",
        ]
        for slug, title in experiments:
            repro = newest_experiment_run(run / "reproductions", slug)
            evidence = reproduction_evidence(repro)
            lines.append(
                f"| `{slug}` — {title} | {reproduction_status(repro)} | "
                f"{link('report/results', evidence, report) if evidence else 'Not produced'} |"
            )

    if show_benchmark:
        benchmark_report = run / "benchmark/REPORT.md"
        benchmark_log = run / "benchmark/launcher.log"
        benchmark_status = by_phase_name.get(("benchmark", "figures_4_6_8"), {})
        lines += [
            "",
            "## C3 — limited generalization",
            "",
            f"Status: **{benchmark_status.get('status', 'NOT RUN')}**.",
            "",
            "Claim 3 reconstructs the complete benchmark analysis from preserved predictions rather than repeating roughly 18 days of inference across ten tools and three Orbinato configurations. Fresh tool execution is evaluated in Claims 1 and 2.",
            "",
            "A `PASS` means that all 38 preserved files passed archive verification, capacity-aware scoring and Figures 4–6 and 8 were reconstructed, and the direct-comparison queues contained 61 reports for TTPDrill versus rcATT and 27 reports for AttacKG versus RAF-AG. Begin with the comparison report and visually confirm that each paper reference and fresh result show similar results, including the same relative tool performance and overall patterns. The plotting style does not need to match exactly.",
            "",
            "- " + (link("Figures 4–6 and 8 comparison report", benchmark_report, report) if benchmark_report.exists() else "Benchmark report not produced."),
            "- " + (link("Benchmark command log", benchmark_log, report) if benchmark_log.exists() else "Benchmark log not produced."),
        ]

    if command == "all":
        lines += [
            "",
            "## Scope boundaries",
            "",
            "- `core` is the evaluator-oriented, modest-resource path: four adapter smoke tests, four core reproductions, and benchmark evaluation. TTP-LLM still requires its external API and may incur a small charge.",
            "- `optional` selects the longer or resource-sensitive reproductions and smoke-tests the containers those reproductions use.",
            "- `all` runs both groups in paper experiment order and also smoke-tests TRAM, the tenth integration, which has no standalone reproduction target.",
            "- A reproduction invoked with `--profile smoke` is only an engineering check and must not be presented as a full-paper reproduction.",
        ]

    lines += [
        "",
        "## Run records",
        "",
        "- " + link("Phase status ledger", run / "status.tsv", report),
        "- " + link("Run metadata", run / "metadata.tsv", report),
        "- " + link("Claims launcher log", run / "claims.log", report),
        "",
    ]
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--summary-report", type=Path)
    args = parser.parse_args()
    run = args.run_dir.resolve()
    render(run / "REPORT.md", run)
    render(args.summary_report.resolve() if args.summary_report else HERE / "REPORT.md", run)


if __name__ == "__main__":
    main()

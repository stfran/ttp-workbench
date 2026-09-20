#!/usr/bin/env python3
"""Recompute the analysis behind paper Figures 4--6 and 8.

This runner performs analysis only. It consumes saved prediction JSON files;
it does not start containers, call adapters, or run model inference.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path, PurePosixPath


HERE = Path(__file__).resolve().parent
PROJECT = HERE.parents[1]
ANALYSIS = HERE / "analysis"
PAPER = HERE / "paper_figures"
DEFAULT_ARCHIVE = HERE / "benchmark_results.zip"
DEFAULT_RESULTS = HERE / "results"
DEFAULT_OUTPUT = HERE / "reproduced_figures"
DEFAULT_REPORT = HERE / "REPORT.md"
DEFAULT_AUTHOR_GT = PROJECT / "Datasets/author_labeled_reviewed"
DEFAULT_CURATED_GT = PROJECT / "Datasets/curated_reports"

TOOLS = [
    "TTPDrill", "rcATT", "TRAM", "AttacKG", "Orbinato", "SeqMask",
    "LADDER", "TTP-LLM", "RAF-AG", "Buchel",
]
FIGURE5_TOOLS = [
    "TTPDrill", "rcATT", "TRAM", "AttacKG", "Orbinato-MLP", "SeqMask",
    "LADDER", "RAF-AG", "Buchel",
]


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_stream(handle) -> str:
    digest = hashlib.sha256()
    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
        digest.update(chunk)
    return digest.hexdigest()


def prepare_results(archive: Path, results_root: Path) -> tuple[str, int]:
    """Safely materialize missing archive members and verify every file."""
    if not archive.is_file():
        raise FileNotFoundError(f"Benchmark archive not found: {archive}")
    archive_hash = sha256_path(archive)
    verified = 0
    with zipfile.ZipFile(archive) as bundle:
        for info in bundle.infolist():
            member = PurePosixPath(info.filename)
            if member.is_absolute() or ".." in member.parts:
                raise ValueError(f"Unsafe archive member: {info.filename}")
            if not member.parts or member.parts[0] != "results":
                raise ValueError(f"Unexpected archive layout: {info.filename}")
            destination = results_root.joinpath(*member.parts[1:])
            if info.is_dir():
                destination.mkdir(parents=True, exist_ok=True)
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            if not destination.exists():
                with bundle.open(info) as source, destination.open("wb") as target:
                    shutil.copyfileobj(source, target)
            with bundle.open(info) as archived:
                archived_hash = sha256_stream(archived)
            if sha256_path(destination) != archived_hash:
                raise ValueError(f"Extracted input differs from archive: {destination}")
            verified += 1
    for name in ("on_curated_data", "on_author_labeled_data"):
        if not (results_root / name).is_dir():
            raise FileNotFoundError(f"Archive did not provide {results_root / name}")
    return archive_hash, verified


def run_command(command: list[str], env: dict[str, str], log) -> None:
    line = " ".join(subprocess.list2cmdline([item]) for item in command)
    print(f"+ {line}")
    log.write(f"$ {line}\n")
    log.flush()
    completed = subprocess.run(
        command,
        cwd=PROJECT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    log.write(completed.stdout)
    log.write(f"\n[exit {completed.returncode}]\n\n")
    log.flush()
    if completed.returncode:
        raise subprocess.CalledProcessError(completed.returncode, command)


def direct_report_count(csv_path: Path, pair: str) -> int:
    with csv_path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if row["pair"] == pair:
                return int(row["shared_reports"])
    raise KeyError(f"Pair not found in {csv_path}: {pair}")


def rel(path: Path, parent: Path) -> str:
    return os.path.relpath(path, parent).replace(os.sep, "/")


def write_report(
    report: Path,
    output: Path,
    archive: Path,
    archive_hash: str,
    member_count: int,
    pair_counts: dict[str, int],
) -> None:
    reference_directory = report.parent / "paper_figures"
    reference_directory.mkdir(parents=True, exist_ok=True)
    reference_names = {
        "benchmark_ours", "benchmark_others_non_prov", "ttp_boxplots_w_oracle",
        "tool_comparison_pairs_TTPDrill_rcATT",
        "tool_comparison_pairs_AttacKG_RAF-AG", "tool_comparison_pairs_10tools",
    }
    for name in reference_names:
        for suffix in (".pdf", ".png"):
            source = PAPER / f"{name}{suffix}"
            destination = reference_directory / source.name
            if source.resolve() != destination.resolve():
                shutil.copy2(source, destination)

    def figure_row(number: str, reference: str, fresh: str) -> str:
        paper_png = rel(reference_directory / reference, report.parent)
        paper_pdf = rel(reference_directory / Path(reference).with_suffix(".pdf"), report.parent)
        fresh_png = rel(output / fresh, report.parent)
        paper_image = f'<a href="{paper_pdf}"><img src="{paper_png}" alt="Paper {number}" width="420"></a>'
        fresh_image = f'<a href="{fresh_png}"><img src="{fresh_png}" alt="Fresh {number}" width="420"></a>'
        return f"| {number} | {paper_image} | {fresh_image} |"

    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(
        "\n".join([
            "# Benchmark figure reproduction results",
            "",
            "This analysis regenerates Figures 4--6 and 8 from the saved benchmark predictions. "
            "The paper figures are fixed references; the fresh figures are recomputed from the "
            "same prediction archive against the currently compiled ground truth. No model "
            "inference is performed.",
            "",
            "## Evaluation scope and pass criteria",
            "",
            "Claim 3 reconstructs the benchmark analysis from the predictions used in the "
            "submitted paper rather than rerunning the complete benchmark inference workload. "
            "A fresh sequential run would execute ten tools, including three Orbinato model "
            "configurations, on 281 author-labeled and 426 curated reports. Based on our "
            "benchmark runs, this would take roughly 18 days. Claim 1 exercises all ten "
            "integrations, and Claim 2 reruns selected "
            "experiments end to end, so Claim 3 focuses the evaluator workload on reconstructing "
            "and reviewing the complete benchmark analysis.",
            "",
            "Claim 3 records `PASS` when all 38 preserved files pass archive verification, the "
            "capacity-aware evaluation completes, Figures 4--6 and 8 are reconstructed, and "
            "the direct-comparison queues contain 61 reports for TTPDrill versus rcATT and 27 "
            "reports for AttacKG versus RAF-AG. Visually compare each paper reference with its "
            "fresh result and confirm that they show similar results, including the same "
            "relative tool performance and overall patterns. The plotting style does not need "
            "to match exactly.",
            "",
            "## Run conditions",
            "",
            "| Figure | Data and queue | Evaluation |",
            "|---|---|---|",
            "| 4(a) | 281 author-labeled CTRs | Micro precision/recall/F1; discard codes not in the tool's TTP capacity |",
            "| 4(b) | Never-before-seen curated prior-work CTRs; rcATT-derived ground truth excluded | Micro precision/recall/F1; discard codes not in the tool's TTP capacity |",
            "| 5 | Combined benchmark; support >= 50; TTP in at least three tools' capacities; TTP-LLM excluded because it predicts tactics only | Per-TTP F1 and best-tool oracle |",
            "| 6 | TTPDrill/rcATT and AttacKG/RAF-AG direct queues | Micro precision/recall/F1 on the joint-capacity report intersection; previously seen reports excluded |",
            "| 8 | All direct tool pairs with at least 10 eligible reports | Micro precision/recall/F1 on each joint-capacity report intersection; previously seen reports excluded |",
            "",
            "As stated in the submission, a direct comparison evaluates tools only on reports "
            "whose ground-truth TTP set is "
            "within the intersection of the tools' TTP capacities, while resolving updated TTP "
            "codes and excluding reports previously seen by either tool.",
            "",
            "## Input verification",
            "",
            f"- Archive: `{rel(archive, report.parent)}`",
            f"- SHA-256: `{archive_hash}`",
            f"- Verified archive files: {member_count}",
            "- The archive and materialized files passed verification.",
            "",
            "The archived prediction and evaluation files used to generate the paper figures were verified before the figures were regenerated.",
            "",
            "## Direct queue checks",
            "",
            f"- TTPDrill vs rcATT: {pair_counts['TTPDrill_vs_rcATT']} reports (paper: 61).",
            f"- AttacKG vs RAF-AG: {pair_counts['AttacKG_vs_RAF-AG']} reports (paper: 27).",
            "",
            "## Paper reference and fresh result",
            "",
            "| Figure | Paper reference | Fresh reproduction |",
            "|---|---|---|",
            figure_row("4(a)", "benchmark_ours.png", "benchmark_ours.png"),
            figure_row("4(b)", "benchmark_others_non_prov.png", "benchmark_others_non_prov.png"),
            figure_row("5", "ttp_boxplots_w_oracle.png", "ttp_boxplots_w_oracle.png"),
            figure_row("6(a)", "tool_comparison_pairs_TTPDrill_rcATT.png", "direct_comparison/tool_comparison_pairs_TTPDrill_rcATT.png"),
            figure_row("6(b)", "tool_comparison_pairs_AttacKG_RAF-AG.png", "direct_comparison/tool_comparison_pairs_AttacKG_RAF-AG.png"),
            figure_row("8", "tool_comparison_pairs_10tools.png", "direct_comparison/tool_comparison_pairs_10tools.png"),
            "",
            f"Commands and console output are retained in `{rel(output / 'run.log', report.parent)}`.",
            "",
        ]),
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, default=DEFAULT_ARCHIVE)
    parser.add_argument("--results-root", type=Path, default=DEFAULT_RESULTS)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument(
        "--author-ground-truth", type=Path, default=DEFAULT_AUTHOR_GT,
        help="Author-labeled ground-truth directory (default: Datasets/author_labeled_reviewed)",
    )
    parser.add_argument(
        "--curated-ground-truth", type=Path, default=DEFAULT_CURATED_GT,
        help="Curated ground-truth directory (default: Datasets/curated_reports)",
    )
    parser.add_argument("--verify-only", action="store_true", help="Verify/materialize the archive without running analysis")
    args = parser.parse_args()

    archive = args.archive.resolve()
    results = args.results_root.resolve()
    output = args.output_dir.resolve()
    report = args.report.resolve()
    archive_hash, member_count = prepare_results(archive, results)
    print(f"Verified {member_count} files from {archive.name} ({archive_hash})")
    if args.verify_only:
        return

    author_root = results / "on_author_labeled_data"
    curated_root = results / "on_curated_data"
    author_gt = args.author_ground_truth.resolve()
    curated_gt = args.curated_ground_truth.resolve()
    capacity = PROJECT / "Framework/utils/ttp_contents.json"
    for required in (author_gt, curated_gt, capacity):
        if not required.exists():
            raise FileNotFoundError(f"Run the top-level data compilation first; missing {required}")

    output.mkdir(parents=True, exist_ok=True)
    direct_output = output / "direct_comparison"
    per_ttp_output = output / "per_ttp"
    direct_output.mkdir(exist_ok=True)
    per_ttp_output.mkdir(exist_ok=True)
    mpl_config = output / ".matplotlib"
    mpl_config.mkdir(exist_ok=True)
    env = os.environ.copy()
    env["MPLBACKEND"] = "Agg"
    env["MPLCONFIGDIR"] = str(mpl_config)
    env["PYTHONPATH"] = str(PROJECT) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")

    python = sys.executable
    author_csv = output / "eval_summary_on_author_labeled_data.csv"
    curated_csv = output / "eval_curated_non_provenance.csv"
    log_path = output / "run.log"
    with log_path.open("w", encoding="utf-8") as log:
        run_command([
            python, str(ANALYSIS / "evaluate.py"), "--root", str(author_root),
            "--gt_roots", str(author_gt), "--capacity_file", str(capacity),
            "--output_csv", str(author_csv),
        ], env, log)
        run_command([
            python, str(ANALYSIS / "evaluate.py"), "--root", str(curated_root),
            "--gt_roots", str(curated_gt), "--capacity_file", str(capacity),
            "--non_prov_data_only", "--exclude_provenance", "rcATT",
            "--output_csv", str(curated_csv),
        ], env, log)
        run_command([
            python, str(ANALYSIS / "plot_prc.py"), str(author_csv),
            "--out", str(output / "benchmark_ours"), "--eval", "generous",
        ], env, log)
        run_command([
            python, str(ANALYSIS / "plot_prc.py"), str(curated_csv),
            "--out", str(output / "benchmark_others_non_prov"), "--eval", "generous",
        ], env, log)
        run_command([
            python, str(ANALYSIS / "per_ttp_analysis.py"),
            "--repo_root", str(PROJECT), "--results_root", str(results),
            "--curated_gt_root", str(curated_gt),
            "--author_gt_root", str(author_gt),
            "--capacity_file", str(capacity),
            "--output_dir", str(per_ttp_output), "--non_prov_data_only",
            "--tools", *TOOLS,
        ], env, log)
        run_command([
            python, str(ANALYSIS / "plot_ttp_boxplots.py"),
            "--input_dir", str(per_ttp_output),
            "--output", str(output / "ttp_boxplots_w_oracle.pdf"),
            "--tools", *FIGURE5_TOOLS, "--scope", "comparable",
            "--min_support", "50", "--min_tools_with_support", "3",
            "--title", "none",
        ], env, log)

        direct_common = [
            python, str(ANALYSIS / "direct_compare.py"),
            "--roots", str(curated_root), str(author_root),
            "--gt_roots", str(curated_gt), str(author_gt),
            "--capacity_file", str(capacity), "--output_dir", str(direct_output),
            "--filter", "--non_prov_data_only", "--min_reports", "10",
        ]
        run_command(direct_common + ["--tools", "TTPDrill", "rcATT"], env, log)
        run_command(direct_common + ["--tools", "AttacKG", "RAF-AG"], env, log)
        run_command(direct_common + ["--tools", "all"], env, log)

    all_pairs = direct_output / "evaluation_summary_all_pairs.csv"
    pair_counts = {
        pair: direct_report_count(all_pairs, pair)
        for pair in ("TTPDrill_vs_rcATT", "AttacKG_vs_RAF-AG")
    }
    write_report(
        report, output, archive, archive_hash, member_count,
        pair_counts,
    )
    print(f"Wrote {report}")


if __name__ == "__main__":
    main()

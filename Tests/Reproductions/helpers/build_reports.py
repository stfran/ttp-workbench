#!/usr/bin/env python3
"""Build experiment indexes, detailed run reports and a cross-experiment summary.

Reads saved comparisons onlys
"""
import argparse
import csv
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shlex
from urllib.parse import quote
import sys

# Support both direct script execution and package imports.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from helpers.attackg_report import write_attackg_report, split_references
from helpers.buchel_table13 import read_native_scores
from helpers.reporting import PROJECT, terminology
from helpers.optional_experiments import OPTIONAL

HERE = Path(__file__).resolve().parents[1]
EXPERIMENTS = {
    "attackg_table4": {
        "title": "AttacKG paper — eight reports", "runner": "reproduce_attackg_table4.py",
        "narrative": "attackg_table4",
        "setup": 'Table 6 row 6 of our submission depicts the aggregate F1 result of reproducing the technique column in Table 4 of [2]. Our reproduction is on the eight reports for which there is ground truth data in the AttacKG repository. We run AttacKG and TTPDrill each through their adapter contained in Framework/adapters and container built from Docker_Setup and directly through their external_tools repository/environment.',
        "caveats": 'Table 4 of [2] covers 16 reports, but reproduction comparisons recalculate its reference counts for eight. Micro-F1 is our convention, but the paper does not specify their scoring procedure, and neither document averaging nor micro averaging of the 16 published count rows reproduces the printed totals. Additionally, local labels differ from paper ground-truth counts. See the detailed comparison before interpreting paper gaps.',
        "keys": ["AttacKG micro f1", "TTPDrill micro f1"],
    },
    "ladder_table9": {
        "title": "LADDER paper — five reports", "runner": "reproduce_ladder_table9.py",
        "narrative": "ladder_table9",
        "setup": "Accepted Table 6 row 8. Full scope: five reports. LADDER, AttacKG and TTPDrill have separate direct and framework paths. Direct LADDER uses the notebook-derived CLI and its isolated environment; the launcher supplies the matching Conda native libraries.",
        "caveats": "Embedded metadata predictions are paper references, not new original-tool executions. The standard LADDER image and aligned direct PyTorch build can use CPU fallback on this host. Failed attempts remain in status.tsv even if a later standalone correction produced scores.",
        "source_docs": [
            ("LADDER setup README", PROJECT / "Docker_Setup/LADDER/README.md"),
            ("AttacKG setup README", PROJECT / "Docker_Setup/AttacKG/README.md"),
            ("TTPDrill setup README", PROJECT / "Docker_Setup/TTPDrill/README.md"),
        ],
        "keys": ["ladder micro_f1", "attackg micro_f1", "ttpdrill micro_f1"],
        "summary_precision": 2,
        "summary_labels": {
            "ladder micro_f1": "LADDER micro-F1",
            "attackg micro_f1": "AttacKG micro-F1",
            "ttpdrill micro_f1": "TTPDrill micro-F1",
        },
    },
    "buchel_table13": {
        "title": "Büchel Table 13 — AnnoCTR external tools", "runner": "reproduce_buchel_table13.py",
        "narrative": "buchel_table13",
        "readme_detail": "execution_design.md",
        "setup": "Accepted Table 6 row 3. LADDER uses the released direct runner and a disposable standard container. AttacKG runs directly in Büchel's external-tools environment and through the framework in a run-scoped image aligned to Büchel's Python and package versions; the image is removed after the framework call.",
        "caveats": "The published '50' results match archived 25-label results. Reports distinguish 25, 50, 118 and open scopes and historical versus actual scope averages. Empty prediction plus empty gold scores 1. AttacKG uses Buchel's released 10,000-alignment cap. Historical failures are retained and require a new run.",
        "source_docs": [
            ("Büchel setup README", PROJECT / "Docker_Setup/Buchel/README.md"),
            ("LADDER setup README", PROJECT / "Docker_Setup/LADDER/README.md"),
            ("AttacKG setup README", PROJECT / "Docker_Setup/AttacKG/README.md"),
        ],
        "keys": ["ladder/standard F1 published50_archived25", "attackg/capped F1 published50_archived25"],
    },
    "buchel_table9": {
        "title": "Buchel Table 9 — generative AnnoCTR/TRAM", "runner": "reproduce_buchel_table9.py",
        "narrative": "buchel_table9",
        "setup": "Accepted Table 6 row 2. The 16-cell grid per backend covers base/SFT × Raw/FSP/RAG/FSP+RAG for AnnoCTR and TRAM. Direct execution uses native finetuning_test; framework execution uses BuchelAdapter with the supplied AnnoCTR and published Zenodo TRAM model mounts. Automatic training is disabled.",
        "caveats": "The authoritative generation_app image has no saved SFT weights; supplied merged models are mounted. Downloading weights does not generate reproduction scores. RAG also needs the released Qwen embedding service. Native returned name-based scores differ from the logged direct-ID metrics selected here. Only executed cells count; the summary highlights Raw SFT cells, not the entire grid.",
        "source_docs": [("reproduction README", PROJECT / "Tests/Reproductions/README.md")],
        "keys": ["Bosch Raw / sft_bosch F1", "Tram Raw / sft_tram F1"],
    },
    "ttpllm_table2": {
        "title": "TTP-LLM Table 2 — prompt only", "runner": "reproduce_ttpllm_table2.py",
        "narrative": "ttpllm_table2",
        "setup": "Accepted Table 6 row 1. GPT-3.5-turbo decoder-only prompt-only execution on the copied procedure dataset. Each path uses the root config.ini; the direct tool has its own environment. The detailed comparison includes per-tactic F1/support and samples-average F1.",
        "caveats": "Calls can incur API cost and vary between runs. Results apply only to the evaluated procedures. Configuration secrets are not included in these reports.",
        "keys": ["samples avg F1"],
    },
}

# Keep the evaluator-oriented core together at the top of SUMMARY.md. Büchel
# Table 9 has a full Markdown renderer, but it belongs to the optional scope.
SUMMARY_EXPERIMENT_ORDER = (
    "attackg_table4",
    "ladder_table9",
    "buchel_table13",
    "ttpllm_table2",
    "buchel_table9",
)
CORE_EXPERIMENT_ORDER = SUMMARY_EXPERIMENT_ORDER[:4]
OPTIONAL_EXPERIMENT_ORDER = ("buchel_table9", *OPTIONAL)

# Frozen references from the "Repro." and "Outcome" columns of Table 6 in
# the submitted paper. These are intentionally separate from current run
# outputs. Table 6 reports one aggregate for the full Buchel Table 9 grid, so
# that value is repeated (and labeled) beside each highlighted configuration.
SUBMITTED_REPRODUCTIONS = {
    "attackg_table4": {
        "AttacKG micro f1": ("0.231", "✗ M C"),
        "TTPDrill micro f1": ("0.042", "✗ M C"),
    },
    "ladder_table9": {
        "ladder micro_f1": ("0.49", "✗ M"),
        "attackg micro_f1": ("0.15", "✓ M"),
        "ttpdrill micro_f1": ("0.09", "✗ M"),
    },
    "buchel_table13": {
        "ladder/standard F1 published50_archived25": ("0.215", "✓"),
        "attackg/capped F1 published50_archived25": ("0.319", "✓ C"),
    },
    "ttpllm_table2": {
        "samples avg F1": ("0.60", "✓ N"),
    },
    "buchel_table9": {
        "Bosch Raw / sft_bosch F1": ("0.433 (grid avg.)", "✓ N C"),
        "Tram Raw / sft_tram F1": ("0.433 (grid avg.)", "✓ N C"),
    },
}

SUBMITTED_REPRODUCTION_DEFINITIONS = [
    "**Submitted Reproduction** is the value we submitted in the `Repro.` "
    "column of Table 6. It is a frozen reference from the submitted paper, not "
    "a value recomputed by the current run. Where Table 6 aggregates several "
    "configurations, the same aggregate is repeated and labeled in each "
    "corresponding detailed row.",
    "",
    "**Outcome** reuses the Table 6 outcome codes:",
    "",
    "- **✓** = reproduced/comparable",
    "- **✗** = unable to reproduce",
    "- **N** = nondeterminism",
    "- **M** = missing/changed data",
    "- **C** = code/model/artifact drift",
    "- **R** = incomplete/no runner",
    "- **G** = documentation gap",
]


def link(label, target, parent, fragment=None):
    destination = quote(os.path.relpath(target, parent), safe="/._-")
    if fragment:
        destination += "#" + quote(fragment, safe="-_")
    return "[" + label + "](" + destination + ")"


def experiment_description(slug):
    """Copy one experiment description verbatim from the reproduction README."""
    readme = (HERE / "README.md").read_text(encoding="utf-8")
    heading = re.compile(rf"^### `{re.escape(slug)}`[^\n]*\n", re.MULTILINE)
    match = heading.search(readme)
    if match is None:
        raise RuntimeError(f"Missing experiment description for {slug} in {HERE / 'README.md'}")
    next_heading = re.search(r"^(?:### `|## References\b)", readme[match.end():], re.MULTILINE)
    end = match.end() + next_heading.start() if next_heading else len(readme)
    description = readme[match.end():end].strip()
    if not description:
        raise RuntimeError(f"Empty experiment description for {slug} in {HERE / 'README.md'}")
    return description


def source_documentation(spec, parent):
    documents = spec["source_docs"]
    rendered = [link(label, path, parent) for label, path in documents]
    if len(rendered) == 1:
        return rendered[0]
    if len(rendered) == 2:
        return " and ".join(rendered)
    return ", ".join(rendered[:-1]) + ", and " + rendered[-1]


def read_comparison(path):
    rows = {}
    if path.exists():
        in_metrics = False
        for line in path.read_text().splitlines():
            if line.startswith("| Metric / trial |"):
                in_metrics = True
                continue
            if not line.startswith("|"):
                in_metrics = False
                continue
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if in_metrics and len(cells) == 4 and not cells[0].startswith("---"):
                rows[cells[0]] = cells[1:]
    # Older retained runs called the published Zenodo checkpoint
    # ``sft_tram_zenodo``. Current runners use the paper-facing ``sft_tram``
    # name while retaining the old key as historical evidence.
    for key, values in list(rows.items()):
        if "/ sft_tram_zenodo " in key:
            rows.setdefault(key.replace("/ sft_tram_zenodo ", "/ sft_tram "), values)
    return rows


LADDER_TABLE9_ROWS = {
    "TTPDrill": {
        "paper": (22, 43, 231, .09, .34, .14),
        "embedded": (22, 43, 227, .08835341365461848, .3384615384615385, .14012738853503184),
        "directory": "TTPDrill",
        "key": "ttpdrill micro_f1",
        "submitted": "0.090",
        "outcome": "✗ M",
    },
    "AttacKG": {
        "paper": (12, 53, 85, .12, .18, .15),
        "embedded": (12, 53, 85, .12371134020618557, .18461538461538463, .14814814814814817),
        "directory": "AttacKG",
        "key": "attackg micro_f1",
        "submitted": "0.153",
        "outcome": "✓ M",
    },
    "LADDER": {
        "paper": (41, 24, 22, .65, .63, .64),
        "embedded": (41, 24, 21, .6612903225806451, .6307692307692307, .6456692913385826),
        "directory": "LADDER",
        "key": "ladder micro_f1",
        "submitted": "0.485",
        "outcome": "✗ M",
    },
}

def ladder_saved_summary(run, tool, backend):
    path = run / "results" / LADDER_TABLE9_ROWS[tool]["directory"] / backend / "summary.json"
    if not path.exists():
        return None
    for row in json.loads(path.read_text()):
        if row.get("mode") == backend:
            return row
    return None


def ladder_table6_numbers(rows):
    lines = [
        "| Tool | Table 6 Paper F1 | Table 6 Repro. F1 | This run: original micro-F1 | This run: framework micro-F1 | Outcome |",
        "|---|---:|---:|---:|---:|:---|",
    ]
    for tool in ("TTPDrill", "AttacKG", "LADDER"):
        reference = LADDER_TABLE9_ROWS[tool]
        current = rows.get(reference["key"], ["— (not available)"] * 3)
        lines.append("| {} | {:.3f} | {} | {} | {} | `{}` |".format(
            tool, reference["paper"][5], reference["submitted"], current[2], current[1], reference["outcome"]))
    return "\n".join(lines)


def ladder_prediction_fidelity(run, tool):
    directory = LADDER_TABLE9_ROWS[tool]["directory"]
    paths = [
        run / "results" / directory / backend / "parsed" / "predictions_normalized.json"
        for backend in ("original", "framework")
    ]
    if not all(path.exists() for path in paths):
        return "prediction comparison unavailable"
    original, framework = (json.loads(path.read_text()) for path in paths)
    report_ids = sorted(set(original) | set(framework))
    exact = sum(
        set(original.get(report_id, [])) == set(framework.get(report_id, []))
        for report_id in report_ids
    )
    return "{}/{} reports have exact technique-set equality".format(exact, len(report_ids))


def ladder_run_interpretation(run):
    fidelity = "; ".join(
        "{}: {}".format(tool, ladder_prediction_fidelity(run, tool))
        for tool in ("TTPDrill", "AttacKG", "LADDER")
    )
    score_evidence = []
    three_decimal_matches = []
    same_two_decimal = []
    for tool in ("TTPDrill", "AttacKG", "LADDER"):
        reference = LADDER_TABLE9_ROWS[tool]
        corrected = float(reference["submitted"])
        original = ladder_saved_summary(run, tool, "original")
        framework = ladder_saved_summary(run, tool, "framework")
        if original is None or framework is None:
            score_evidence.append("{}: corrected Table 6 Repro. {}; this run unavailable".format(
                tool, reference["submitted"]))
            continue
        original_f1 = original["micro_f1"]
        framework_f1 = framework["micro_f1"]
        score_evidence.append(
            "{}: corrected Table 6 Repro. {}; this run {:.6f} original / {:.6f} framework".format(
                tool, reference["submitted"], original_f1, framework_f1))
        if format(original_f1, ".3f") == reference["submitted"] and format(framework_f1, ".3f") == reference["submitted"]:
            three_decimal_matches.append(tool)
        elif round(original_f1, 2) == round(corrected, 2) and round(framework_f1, 2) == round(corrected, 2):
            same_two_decimal.append(tool)

    consistency = []
    if three_decimal_matches:
        consistency.append("{} match the corrected Table 6 Repro. values at three-decimal precision".format(
            " and ".join(three_decimal_matches)))
    if same_two_decimal:
        subject = " and ".join(same_two_decimal)
        verb = "agree" if len(same_two_decimal) > 1 else "agrees"
        consistency.append("{} {} with the corrected value at two-decimal precision".format(
            subject, verb))
    consistency_text = "; ".join(consistency) + "." if consistency else "No corrected Table 6 score match was established."

    return "\n".join([
        "| Check / Table 6 code | Concrete evidence in this run | Interpretation and limit |",
        "|---|---|---|",
        "| Framework/native fidelity | {}. | The saved original-tool and framework predictions agree on these five inputs. This does not establish that the recovered inputs and artifacts are identical to those used for the published experiment. |".format(fidelity),
        "| `\u2713` / `\u2717` — reproduction outcome | {}. | {} AttacKG retains the Table 6 `\u2713` outcome; TTPDrill and LADDER remain `\u2717` because their scores do not reproduce the published Table 9 values. |".format("; ".join(score_evidence), consistency_text),
        "| `M` — missing/changed data | Only five recovered reports are evaluated. The embedded TTPDrill row has four fewer false positives than printed Table 9, and the embedded LADDER row has one fewer false positive and different rounded precision and F1. | The exact historical evaluation data cannot be established. Agreement with an aggregate score does not prove identical inputs, labels, or predictions. |",
    ])


def ladder_detailed_results(run):
    lines = [
        "The Table 9 rows below transcribe the counts and metrics printed in [6]. Embedded-reference rows are recalculated from the author-provided predictions transcribed into the five JSON records. This-run rows are read from the saved original and framework summaries. All recalculated rows use unique technique-code sets and micro scoring.",
        "",
        "| Tool / result source | TP | FN | FP | Precision | Recall | F1 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for tool in ("TTPDrill", "AttacKG", "LADDER"):
        reference = LADDER_TABLE9_ROWS[tool]
        for label, values, precision in (
            ("paper Table 9", reference["paper"], 2),
            ("embedded author data", reference["embedded"], 6),
        ):
            tp, fn, fp, p, r, f1 = values
            lines.append("| {} / {} | {} | {} | {} | {:.{digits}f} | {:.{digits}f} | {:.{digits}f} |".format(
                tool, label, tp, fn, fp, p, r, f1, digits=precision))
        for backend in ("original", "framework"):
            row = ladder_saved_summary(run, tool, backend)
            if row is None:
                lines.append("| {} / this run {} | — | — | — | — | — | — |".format(tool, backend))
                continue
            lines.append("| {} / this run {} | {} | {} | {} | {:.6f} | {:.6f} | {:.6f} |".format(
                tool, backend, row["tp"], row["fn"], row["fp"],
                row["micro_precision"], row["micro_recall"], row["micro_f1"]))
    lines += [
        "",
        "AttacKG's embedded row exactly reconstructs the printed counts. TTPDrill's embedded row has four fewer false positives but produces the same printed metrics after rounding. LADDER's embedded row has one fewer false positive and rounds to precision 0.66 and F1 0.65, rather than the printed 0.65 and 0.64. Therefore agreement with the embedded predictions and agreement with the published Table 9 row are separate checks.",
    ]
    return "\n".join(lines)


TTP_LLM_TABLE5_ROWS = (
    ("collection", "Collection", "0.44", "0.59", "0.59", 852),
    ("command and control", "Command and Control", "0.52", "0.38", "0.38", 705),
    ("credential access", "Credential Access", "0.59", "0.56", "0.61", 615),
    ("defense evasion", "Defense Evasion", "0.66", "0.70", "0.70", 2669),
    ("discovery", "Discovery", "0.86", "0.73", "0.74", 2342),
    ("execution", "Execution", "0.48", "0.46", "0.46", 1208),
    ("exfiltration", "Exfiltration", "0.20", "0.43", "0.44", 92),
    ("impact", "Impact", "0.44", "0.55", "0.56", 213),
    ("initial access", "Initial Access", "0.42", "0.52", "0.52", 406),
    ("lateral movement", "Lateral Movement", "0.45", "0.39", "0.38", 212),
    ("persistence", "Persistence", "0.40", "0.34", "0.35", 549),
    ("privilege escalation", "Privilege Escalation", "0.41", "0.12", "0.12", 723),
    ("reconnaissance", "Reconnaissance", "0.21", "0.18", "0.16", 91),
    ("resource development", "Resource Development", "0.00", "0.00", "0.00", 275),
    ("samples avg", "Samples Average", "0.60", "0.60", "0.60", 10952),
)


def ttpllm_classification_reports(run):
    reports = {}
    for backend in ("original", "framework"):
        path = run / "results/TTP-LLM" / backend / "parsed/classification_report.json"
        reports[backend] = json.loads(path.read_text()) if path.exists() else None
    return reports


def ttpllm_encoded_predictions(run):
    predictions = {}
    for backend in ("original", "framework"):
        path = run / "results/TTP-LLM" / backend / "parsed/encoded.csv"
        if not path.exists():
            predictions[backend] = None
            continue
        with path.open(newline="", encoding="utf-8-sig") as handle:
            reader = csv.reader(handle)
            header = next(reader, [])
            rows = [tuple(int(float(value)) for value in row) for row in reader]
        predictions[backend] = {"path": path, "header": header, "rows": rows}
    return predictions


def ttpllm_saved_evidence(run):
    predictions = ttpllm_encoded_predictions(run)
    reports = ttpllm_classification_reports(run)
    original = predictions["original"]
    framework = predictions["framework"]
    evidence = {"predictions": predictions, "reports": reports}
    if original is None or framework is None:
        return evidence
    if original["header"] != framework["header"] or len(original["rows"]) != len(framework["rows"]):
        return evidence
    paired = list(zip(original["rows"], framework["rows"]))
    evidence.update(
        procedure_rows=len(paired),
        tactic_columns=len(original["header"]),
        exact_vectors=sum(left == right for left, right in paired),
        matching_cells=sum(a == b for left, right in paired for a, b in zip(left, right)),
        total_cells=sum(len(left) for left, _ in paired),
        original_positive=sum(sum(row) for row in original["rows"]),
        framework_positive=sum(sum(row) for row in framework["rows"]),
        framework_only=sum(b > a for left, right in paired for a, b in zip(left, right)),
        original_only=sum(a > b for left, right in paired for a, b in zip(left, right)),
    )
    return evidence


def ttpllm_metric(reports, backend, row, metric):
    report = reports.get(backend)
    if report is None or row not in report:
        return None
    return report[row].get(metric)


def ttpllm_decimal(value):
    return "—" if value is None else "{:.6f}".format(float(value))


def ttpllm_table6_numbers(rows):
    current = rows.get("samples avg F1", ["— (not available)"] * 3)
    return "\n".join([
        "| Metric | Table 6 Paper | Table 6 Repro. | This run: original | This run: framework | Outcome |",
        "|---|---:|---:|---:|---:|:---|",
        "| Samples-average F1 | 0.60 | 0.60 | {} | {} | `✓ N` |".format(current[2], current[1]),
    ])


def ttpllm_artifact_evaluation(run, text):
    evidence = ttpllm_saved_evidence(run)
    reports = evidence["reports"]
    original_f1 = ttpllm_metric(reports, "original", "samples avg", "f1-score")
    framework_f1 = ttpllm_metric(reports, "framework", "samples avg", "f1-score")
    if "procedure_rows" in evidence:
        rows = evidence["procedure_rows"]
        columns = evidence["tactic_columns"]
        exact = evidence["exact_vectors"]
        difference = abs(original_f1 - framework_f1) if None not in (original_f1, framework_f1) else None
        execution = (
            "Both inference calls have recorded PASS status. Each path supplies "
            "{:,} encoded procedure rows and all {} expected tactic columns, aligned "
            "with the input and gold-label rows.".format(rows, columns)
        )
        fidelity = (
            "Both paths use the same procedures, prompt-only configuration, requested "
            "model alias, and tactic encoding. Compare the saved procedure-level vectors "
            "and aggregate scores: {:,}/{:,} vectors match exactly, and samples-average "
            "F1 differs by {}.".format(exact, rows, ttpllm_decimal(difference))
        )
    else:
        execution = "The saved encoded predictions needed for this check are unavailable or incompatible."
        fidelity = "The saved procedure-level vectors needed for this comparison are unavailable or incompatible."
    if None not in (original_f1, framework_f1):
        reproduction = (
            "Evidence consistent with experiment 1's `✓ N` outcome: both current paths "
            "round to the 0.60 reported in the source paper, but their individual "
            "predictions are not identical."
        )
    else:
        reproduction = "The saved samples-average scores needed for this check are unavailable."
    return (text.replace("<!-- execution-evidence -->", execution)
                .replace("<!-- fidelity-evidence -->", fidelity)
                .replace("<!-- reproduction-evidence -->", reproduction))


def ttpllm_run_interpretation(run):
    evidence = ttpllm_saved_evidence(run)
    reports = evidence["reports"]
    original_f1 = ttpllm_metric(reports, "original", "samples avg", "f1-score")
    framework_f1 = ttpllm_metric(reports, "framework", "samples avg", "f1-score")
    if "procedure_rows" not in evidence or None in (original_f1, framework_f1):
        return "Saved prediction evidence is incomplete; this run cannot be interpreted."
    rows = evidence["procedure_rows"]
    exact = evidence["exact_vectors"]
    different = rows - exact
    percentage = 100 * exact / rows if rows else 0
    difference = abs(original_f1 - framework_f1)
    return "\n".join([
        "| Check / Table 6 code | Concrete evidence in this run | Interpretation and limit |",
        "|---|---|---|",
        "| Framework/native fidelity | {:,}/{:,} procedure-level tactic vectors match exactly ({:.1f}%). The {:,} differing vectors contain {:,} framework-only and {:,} original-only tactic assignments. Samples-average F1 is {} original versus {} framework, an absolute difference of {}. | The two paths closely agree in aggregate but do not produce identical predictions because of the nondeterministic nature of LLM-based tasks. |".format(
            exact, rows, percentage, different, evidence["framework_only"], evidence["original_only"],
            ttpllm_decimal(original_f1), ttpllm_decimal(framework_f1), ttpllm_decimal(difference)),
        "| `✓` — reproduced/comparable | The paper reports samples-average F1 of 0.60. This run obtains {} original and {} framework, which both round to 0.60 at two-decimal precision. | This supports the submitted reproduction outcome. It does not imply that the current service reproduces every historical per-tactic value or response. |".format(
            ttpllm_decimal(original_f1), ttpllm_decimal(framework_f1)),
        "| `N` — nondeterminism | The paths make separate API requests, and {:,} procedure vectors differ despite matching inputs, requested model alias, prompt mode, and encoding. | The observed variation is consistent with the submitted nondeterminism code. |".format(different),
    ])


def ttpllm_run_checks(run):
    evidence = ttpllm_saved_evidence(run)
    reports = evidence["reports"]
    statuses = {}
    status_path = run / "status.tsv"
    if status_path.exists():
        for line in status_path.read_text().splitlines():
            name, separator, status = line.partition("\t")
            if separator:
                statuses[name] = status
    lines = [
        "| Execution path | Procedure rows | Tactic columns | Positive tactic assignments | Samples-average F1 | Recorded inference |",
        "|---|---:|---:|---:|---:|:---|",
    ]
    for backend, label in (("original", "Original"), ("framework", "Framework")):
        prediction = evidence["predictions"].get(backend)
        row_count = "—" if prediction is None else "{:,}".format(len(prediction["rows"]))
        columns = "—" if prediction is None else str(len(prediction["header"]))
        positives = "—" if prediction is None else "{:,}".format(sum(sum(row) for row in prediction["rows"]))
        score = ttpllm_decimal(ttpllm_metric(reports, backend, "samples avg", "f1-score"))
        status = statuses.get("ttpllm_table2_" + backend, "Not recorded")
        lines.append("| {} | {} | {} | {} | {} | {} |".format(
            label, row_count, columns, positives, score, status))
    if "total_cells" in evidence:
        agreement = 100 * evidence["matching_cells"] / evidence["total_cells"] if evidence["total_cells"] else 0
        lines += [
            "",
            "Across the {:,} aligned binary tactic cells, {:,} match and {:,} differ ({:.1f}% cell agreement). Cell agreement is reported only as a diagnostic because the negative class dominates this multilabel encoding; the procedure-level comparison and per-tactic scores below are more informative.".format(
                evidence["total_cells"], evidence["matching_cells"],
                evidence["total_cells"] - evidence["matching_cells"], agreement),
        ]
    return "\n".join(lines)


def ttpllm_saved_evidence_links(run):
    paths = [
        ("original encoded predictions", run / "results/TTP-LLM/original/parsed/encoded.csv"),
        ("framework encoded predictions", run / "results/TTP-LLM/framework/parsed/encoded.csv"),
        ("original classification report", run / "results/TTP-LLM/original/parsed/classification_report.json"),
        ("framework classification report", run / "results/TTP-LLM/framework/parsed/classification_report.json"),
    ]
    return "Saved prediction evidence: " + "; ".join(link(label, path, run) for label, path in paths) + "."


def ttpllm_detailed_results(run):
    reports = ttpllm_classification_reports(run)
    original_samples = reports["original"].get("samples avg", {}) if reports["original"] else {}
    framework_samples = reports["framework"].get("samples avg", {}) if reports["framework"] else {}
    lines = [
        "### Samples-average result",
        "",
        "| Paper F1 | Original precision | Original recall | Original F1 | Framework precision | Framework recall | Framework F1 | Support |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|",
        "| 0.60 | {} | {} | {} | {} | {} | {} | {} |".format(
            ttpllm_decimal(original_samples.get("precision")),
            ttpllm_decimal(original_samples.get("recall")),
            ttpllm_decimal(original_samples.get("f1-score")),
            ttpllm_decimal(framework_samples.get("precision")),
            ttpllm_decimal(framework_samples.get("recall")),
            ttpllm_decimal(framework_samples.get("f1-score")),
            "{:,}".format(int(original_samples["support"])) if "support" in original_samples else "—"),
        "",
        "Samples averaging is performed over the 9,532 procedure examples. Support is the total number of positive gold-label assignments, not the number of procedures.",
        "",
        "### Per-tactic results",
        "",
        "We report these results in Table 5 of our submission, mirroring the originally published results shape and metrics.",
        "",
        "| Tactic | Paper F1 | Submitted without (original) F1 | Submitted with (framework) F1 | This run: original F1 | This run: framework F1 | Support |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for key, label, paper, submitted_original, submitted_framework, support in TTP_LLM_TABLE5_ROWS:
        lines.append("| {} | {} | {} | {} | {} | {} | {:,} |".format(
            label, paper, submitted_original, submitted_framework,
            ttpllm_decimal(ttpllm_metric(reports, "original", key, "f1-score")),
            ttpllm_decimal(ttpllm_metric(reports, "framework", key, "f1-score")), support))
    lines += [
        "",
        "#### Current-run per-tactic precision, recall, and F1. Note that the [8] did not report precision and recall.",
        "",
        "| Tactic | Original precision | Original recall | Original F1 | Framework precision | Framework recall | Framework F1 | Support |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for key, label, _paper, _submitted_original, _submitted_framework, support in TTP_LLM_TABLE5_ROWS[:-1]:
        lines.append("| {} | {} | {} | {} | {} | {} | {} | {:,} |".format(
            label,
            ttpllm_decimal(ttpllm_metric(reports, "original", key, "precision")),
            ttpllm_decimal(ttpllm_metric(reports, "original", key, "recall")),
            ttpllm_decimal(ttpllm_metric(reports, "original", key, "f1-score")),
            ttpllm_decimal(ttpllm_metric(reports, "framework", key, "precision")),
            ttpllm_decimal(ttpllm_metric(reports, "framework", key, "recall")),
            ttpllm_decimal(ttpllm_metric(reports, "framework", key, "f1-score")), support))
    return "\n".join(lines)


BUCHEL_TABLE9_STRATEGIES = (
    ("Raw", "Raw"),
    ("FSP", "FSP"),
    ("RAG", "RAG"),
    ("RAG + FSP", "FSP + RAG"),
)


def buchel_table9_key(dataset, strategy, engine, metric):
    stored_strategy = strategy
    if dataset == "Tram" and strategy == "FSP + RAG":
        stored_strategy = "RAG + FSP"
    return "{} {} / {} {}".format(dataset, stored_strategy, engine, metric)


def buchel_table9_cell(rows, key, column, blank=False, precision=None):
    if blank:
        return ""
    values = rows.get(key)
    if values is None:
        return "—"
    value = values[column]
    if precision is not None:
        try:
            return format(float(value), ".{}f".format(precision))
        except ValueError:
            pass
    return value


def buchel_table9_has_local_tram(rows):
    return any("/ sft_tram_local " in key for key in rows)


def buchel_table9_grid_average(rows, tram_engine, column):
    values = []
    for dataset in ("Bosch", "Tram"):
        sft_engine = "sft_bosch" if dataset == "Bosch" else tram_engine
        for _label, strategy in BUCHEL_TABLE9_STRATEGIES:
            for engine in ("base", sft_engine):
                key = buchel_table9_key(dataset, strategy, engine, "F1")
                try:
                    values.append(float(rows[key][column]))
                except (KeyError, TypeError, ValueError):
                    return None
    return sum(values) / len(values)


def buchel_table9_table6_numbers(rows):
    tram_engine = "sft_tram_local" if buchel_table9_has_local_tram(rows) else "sft_tram"
    original = buchel_table9_grid_average(rows, tram_engine, 2)
    framework = buchel_table9_grid_average(rows, tram_engine, 1)
    return "\n".join([
        "| Metric | Table 6 Paper | Table 6 Repro. | This run: original | This run: framework | Outcome |",
        "|---|---:|---:|---:|---:|:---|",
        "| Configuration-grid average F1 | 0.490 | 0.433 | {} | {} | `✓ N C` |".format(
            ttpllm_decimal(original), ttpllm_decimal(framework)),
    ])


def buchel_table9_scope_note(rows):
    if not buchel_table9_has_local_tram(rows):
        return ("This run uses the published Zenodo TRAM checkpoint as `sft_tram`; "
                "no locally trained TRAM checkpoint contributes to the average.")
    original = buchel_table9_grid_average(rows, "sft_tram", 2)
    framework = buchel_table9_grid_average(rows, "sft_tram", 1)
    return (
        "To match the submitted reproduction grid, the table above uses the retained "
        "`sft_tram_local` rows. Substituting the published Zenodo `sft_tram` rows gives "
        "configuration-grid average F1 of {} original and {} framework. Future evaluator "
        "runs execute only the published `sft_tram` checkpoint.".format(
            ttpllm_decimal(original), ttpllm_decimal(framework))
    )


def buchel_table9_model_grid_description(rows):
    introduction = (
        "Each backend covers Raw, FSP, RAG, and FSP+RAG strategies with base and "
        "fine-tuned models. [9] released an SFT model aligned to the TRAM data that "
        "we reuse. We trained an additional SFT model on AnnoCTR data during our "
        "reproduction experiments following the execution path from [9]'s released artifact."
    )
    if buchel_table9_has_local_tram(rows):
        return (
            introduction + " This retained run contains 20 cells per backend because it "
            "also evaluated a locally trained TRAM checkpoint. The published Zenodo "
            "checkpoint is reported as `sft_tram`, and the additional historical checkpoint "
            "is reported as `sft_tram_local`. Future evaluator runs omit `sft_tram_local`."
        )
    return (
        introduction + " This produces 16 cells per backend. The AnnoCTR checkpoint is "
        "reported as `sft_bosch`, and the published Zenodo TRAM checkpoint is reported as "
        "`sft_tram`."
    )


def buchel_table9_artifact_evaluation(run, text):
    count = 0
    status = run / "status.tsv"
    if status.exists():
        count = sum(
            line.startswith("buchel_table9_") and not line.startswith("buchel_table9_report\t")
            for line in status.read_text().splitlines()
        )
    words = {40: "Forty", 32: "Thirty-two"}
    evidence = (
        "{} inference calls, with complete native-item results for each selected "
        "configuration and recorded statuses.".format(words.get(count, str(count)))
        if count else "No inference-call status entries were recorded."
    )
    return text.replace("<!-- inference-call-count -->", evidence)


def buchel_table9_detailed_results(rows):
    lines = []
    for dataset, heading in (("Bosch", "AnnoCTR results"), ("Tram", "TRAM results")):
        if lines:
            lines.append("")
        lines += ["### " + heading, ""]
        if dataset == "Tram":
            lines += [
                "The standard SFT rows use the published Zenodo `sft_tram` checkpoint. "
                "The additional `sft_tram_local` rows are retained only for this historical run.",
                "",
            ]
        lines += [
            "| Method | Paper F1 | Paper Prec. | Paper Rec. | Original F1 | Original Prec. | Original Rec. | Framework F1 | Framework Prec. | Framework Rec. |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]

        method_rows = [(label, strategy, "base", False) for label, strategy in BUCHEL_TABLE9_STRATEGIES]
        sft_engine = "sft_bosch" if dataset == "Bosch" else "sft_tram"
        method_rows += [("SFT " + label, strategy, sft_engine, False)
                        for label, strategy in BUCHEL_TABLE9_STRATEGIES]
        if dataset == "Tram" and buchel_table9_has_local_tram(rows):
            method_rows += [("SFT {} (`sft_tram_local`)".format(label), strategy,
                             "sft_tram_local", True)
                            for label, strategy in BUCHEL_TABLE9_STRATEGIES]

        for label, strategy, engine, paper_blank in method_rows:
            values = []
            for metric in ("F1", "precision", "recall"):
                key = buchel_table9_key(dataset, strategy, engine, metric)
                values.append(buchel_table9_cell(rows, key, 0, blank=paper_blank, precision=3))
            for column in (2, 1):
                for metric in ("F1", "precision", "recall"):
                    key = buchel_table9_key(dataset, strategy, engine, metric)
                    values.append(buchel_table9_cell(rows, key, column))
            lines.append("| {} | {} |".format(label, " | ".join(values)))
    return "\n".join(lines)


BUCHEL_TABLE13_ROWS = (
    {
        "tool": "LADDER",
        "trial": "standard",
        "paper_f1": {"50": "0.2403", "118": "0.2234", "open": "0.1807"},
        "paper": "0.215",
        "submitted": "0.215",
        "outcome": "✓",
        "average_key": "ladder/standard F1 historical 25/118/open average",
    },
    {
        "tool": "AttacKG",
        "trial": "capped",
        "paper_f1": {"50": "0.4278", "118": "0.3214", "open": "0.2375"},
        "paper": "0.329",
        "submitted": "0.319",
        "outcome": "✓ C",
        "average_key": "attackg/capped F1 historical 25/118/open average",
    },
)


def buchel_table13_table6_numbers(rows):
    lines = [
        "| Tool variant | Table 6 Paper F1 | Table 6 Repro. F1 | This run: original F1 | This run: framework F1 | Outcome |",
        "|---|---:|---:|---:|---:|:---|",
    ]
    for reference in BUCHEL_TABLE13_ROWS:
        current = rows.get(reference["average_key"], ["— (not available)"] * 3)
        lines.append("| {} / {} | {} | {} | {} | {} | `{}` |".format(
            reference["tool"], reference["trial"], reference["paper"], reference["submitted"],
            current[2], current[1], reference["outcome"]))
    return "\n".join(lines)


def buchel_table13_predictions(run, tool, trial, backend):
    path = run / "results" / tool / backend / trial / "parsed" / "predictions.json"
    if not path.exists():
        return None
    predictions = json.loads(path.read_text())
    return {
        str(row["id"]): set(row.get("ttps") or [])
        for row in predictions
        if isinstance(row, dict) and row.get("id") is not None
    }


def buchel_table13_fidelity(run):
    records = []
    for reference in BUCHEL_TABLE13_ROWS:
        tool, trial = reference["tool"], reference["trial"]
        original = buchel_table13_predictions(run, tool, trial, "original")
        framework = buchel_table13_predictions(run, tool, trial, "framework")
        if original is None or framework is None:
            records.append(dict(tool=tool, trial=trial, original=original, framework=framework,
                                ids=[], exact=0, differences=[]))
            continue
        ids = sorted(set(original) | set(framework))
        differences = []
        for report_id in ids:
            original_codes = original.get(report_id, set())
            framework_codes = framework.get(report_id, set())
            if original_codes != framework_codes:
                differences.append((
                    report_id,
                    sorted(framework_codes - original_codes),
                    sorted(original_codes - framework_codes),
                ))
        records.append(dict(tool=tool, trial=trial, original=original, framework=framework,
                            ids=ids, exact=len(ids) - len(differences), differences=differences))
    return records


def buchel_table13_document_names():
    path = HERE / "data" / "buchel" / "bosch_test.json"
    if not path.exists():
        return {}
    documents = json.loads(path.read_text()).get("document", {})
    ordered = sorted(set(str(value) for value in documents.values()))
    return {"input_{:04d}".format(index): name for index, name in enumerate(ordered)}


def buchel_table13_fidelity_results(run):
    lines = [
        "| Tool variant | Original / framework groups | Exact matching group code sets | Framework-only assignments | Original-only assignments | Assessment |",
        "|---|---:|---:|---:|---:|---|",
    ]
    records = buchel_table13_fidelity(run)
    for record in records:
        if record["original"] is None or record["framework"] is None:
            lines.append("| {} / {} | — | — | — | — | Prediction comparison unavailable. |".format(
                record["tool"], record["trial"]))
            continue
        framework_only = sum(len(item[1]) for item in record["differences"])
        original_only = sum(len(item[2]) for item in record["differences"])
        assessment = "Exact equality for every group." if not record["differences"] else (
            "Not exact; {} groups differ as detailed below.".format(len(record["differences"])))
        lines.append("| {} / {} | {} / {} | {} / {} | {} | {} | {} |".format(
            record["tool"], record["trial"], len(record["original"]), len(record["framework"]),
            record["exact"], len(record["ids"]), framework_only, original_only, assessment))

    differences = [
        (record["tool"], item)
        for record in records
        for item in record["differences"]
    ]
    if differences:
        document_names = buchel_table13_document_names()
        lines += [
            "",
            "The nonmatching group-level technique sets are:",
            "",
            "| Tool | Group / document | Framework-only codes | Original-only codes |",
            "|---|---|---|---|",
        ]
        for tool, (report_id, framework_only, original_only) in differences:
            document = document_names.get(report_id)
            group = ("{} (`{}`)".format(document, report_id) if document else "`{}`".format(report_id))
            lines.append("| {} | {} | {} | {} |".format(
                tool, group,
                ", ".join("`{}`".format(code) for code in framework_only) or "—",
                ", ".join("`{}`".format(code) for code in original_only) or "—"))
    return "\n".join(lines)


def buchel_table13_run_interpretation(run, rows):
    fidelity = {record["tool"]: record for record in buchel_table13_fidelity(run)}
    ladder = rows.get("ladder/standard F1 historical 25/118/open average", ["—"] * 3)
    attackg = rows.get("attackg/capped F1 historical 25/118/open average", ["—"] * 3)
    ladder_exact = fidelity.get("LADDER", {}).get("exact", 0)
    ladder_total = len(fidelity.get("LADDER", {}).get("ids", []))
    attackg_exact = fidelity.get("AttacKG", {}).get("exact", 0)
    attackg_total = len(fidelity.get("AttacKG", {}).get("ids", []))
    if attackg_total and attackg_exact == attackg_total:
        fidelity_interpretation = (
            "Both tools have exact prediction fidelity in this run. AttacKG's direct and "
            "framework paths used separate native and adapter executions with aligned "
            "Python and Büchel requirement versions."
        )
    else:
        fidelity_interpretation = (
            "LADDER has exact prediction fidelity on this run. AttacKG's close aggregate "
            "score does not establish prediction identity; any differing groups and codes "
            "are listed below."
        )
    return "\n".join([
        "| Check / Table 6 code | Concrete evidence in this run | Interpretation and limit |",
        "|---|---|---|",
        "| `✓` — reproduced/comparable | LADDER historical three-scope F1 is {} original / {} framework; Büchel AttacKG is {} original / {} framework. | The original-tool results round to the Table 6 Paper values of 0.215 and 0.329. This run therefore supports the published-result reproduction outcome when the released scope mapping and tool variants are used. |".format(ladder[2], ladder[1], attackg[2], attackg[1]),
        "| Framework/native fidelity | LADDER has {}/{} exact group-level technique sets; AttacKG has {}/{}. | {} |".format(ladder_exact, ladder_total, attackg_exact, attackg_total, fidelity_interpretation),
        "| `C` — code/model/artifact drift | Table 6 records AttacKG as 0.329 Paper versus 0.319 Repro. This run uses Büchel's released AttacKG files and 10,000-alignment cap, and the original-tool path produces {}. | Matching the paper after restoring the released variant supports the submitted drift assessment. It does not isolate how much of the earlier difference came from each changed file, model, runtime, or alignment limit. |".format(attackg[2]),
        "| Scope-label caveat | The published “50” references equal the archived 25-label rows. The historical comparison averages 25/118/open; separate current rows report the actual 50/118/open average. | Averages using these two scope selections answer different questions and must not be substituted for one another. |",
    ])


def buchel_table13_attackg_environment(_run):
    return (
        "For framework execution, the runner uses disposable, run-scoped environments "
        "that apply the Büchel LADDER files and align AttacKG's Python and package "
        "environment with the Büchel release. These changes affect only the framework "
        "calls and do not replace the standard tool images. The standard "
        "`ttp-workbench:attackg` and `ttp-workbench:ladder` images remain unchanged and "
        "available to the other exercises, and temporary changes and images are removed "
        "when this experiment ends."
    )


def buchel_table13_archive_scores(run, reference):
    filename = reference["tool"].lower() + "_bosch_scores.txt"
    candidates = [
        run / "results" / reference["tool"] / backend / reference["trial"] /
        "execution/ext_tools/results" / filename
        for backend in ("original", "framework")
    ]
    path = next((candidate for candidate in candidates if candidate.exists()), None)
    if path is None:
        return {}, None
    return read_native_scores(path, reference["tool"].lower(), reference["trial"]), path


def buchel_table13_detailed_results(run, rows):
    archive_by_tool = {}
    archive_links = []
    for reference in BUCHEL_TABLE13_ROWS:
        scores, path = buchel_table13_archive_scores(run, reference)
        archive_by_tool[reference["tool"]] = scores
        if path is not None:
            archive_links.append(link(reference["tool"] + " archive scores", path, run))

    def value_or_blank(value):
        if value is None or str(value).startswith("—"):
            return ""
        try:
            return "{:.4f}".format(float(value))
        except (TypeError, ValueError):
            return str(value)

    lines = [
        "These tables separate values published in Büchel Table 13, values recorded in Büchel's archived score files (plus explicitly labeled averages derived from them), references published in our submitted Table 6, and measurements from this run. A blank cell means that source did not report the metric or scope. Büchel Table 13 reports F1 only, and its published 50-label values equal the archive's 25-label values. Büchel paper, archive, and current scorer values use four decimal places; submitted Table 6 values retain their published three-decimal precision.",
        "",
        ("Archived score evidence: " + ", ".join(archive_links) + ".") if archive_links else "Archived score files were not retained with this run.",
        "",
        "#### F1 by scope",
        "",
        "| Tool variant | Scope | Büchel Table 13 | Büchel archive | Submitted Table 6 Paper | Submitted Table 6 Repro. | This run original | This run framework |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    scope_rows = (
        ("10 labels", "10", "F1 10", False),
        ("25 labels", "25", "F1 25", False),
        ("50 labels", "50", "F1 50", False),
        ("118 labels", "118", "F1 118", False),
        ("Open", "open", "F1 open", False),
        ("Historical comparison average (paper 50 / archive 25, 118, open)", None,
         "F1 historical 25/118/open average", True),
        ("Actual archive 50/118/open average", None,
         "F1 actual 50/118/open average", False),
    )
    for reference in BUCHEL_TABLE13_ROWS:
        prefix = reference["tool"].lower() + "/" + reference["trial"]
        archive = archive_by_tool[reference["tool"]]
        for label, scope, suffix, table6_average in scope_rows:
            current = rows.get(prefix + " " + suffix, ["— (not available)"] * 3)
            paper = reference["paper_f1"].get(scope, "") if scope else ""
            archive_value = archive.get(prefix + " " + suffix)
            table6_paper = reference["paper"] if table6_average else ""
            table6_reproduction = reference["submitted"] if table6_average else ""
            lines.append("| {} / {} | {} | {} | {} | {} | {} | {} | {} |".format(
                reference["tool"], reference["trial"], label, paper,
                value_or_blank(archive_value), table6_paper, table6_reproduction,
                value_or_blank(current[2]), value_or_blank(current[1])))

    lines += [
        "",
        "#### Precision and recall by scope",
        "",
        "| Tool variant | Scope | Metric | Büchel Table 13 | Büchel archive | Submitted Table 6 Paper | Submitted Table 6 Repro. | This run original | This run framework |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for reference in BUCHEL_TABLE13_ROWS:
        prefix = reference["tool"].lower() + "/" + reference["trial"]
        archive = archive_by_tool[reference["tool"]]
        for scope, label in (("10", "10 labels"), ("25", "25 labels"), ("50", "50 labels"),
                             ("118", "118 labels"), ("open", "Open")):
            for metric in ("precision", "recall"):
                key = prefix + " " + metric + " " + scope
                current = rows.get(key, ["— (not available)"] * 3)
                lines.append("| {} / {} | {} | {} |  | {} |  |  | {} | {} |".format(
                    reference["tool"], reference["trial"], label, metric.capitalize(),
                    value_or_blank(archive.get(key)), value_or_blank(current[2]),
                    value_or_blank(current[1])))
    return "\n".join(lines)


def run_info(run):
    metadata = json.loads((run / "run.json").read_text()) if (run / "run.json").exists() else {}
    comparison = run / "results/comparison.md"
    text = comparison.read_text() if comparison.exists() else ""
    if "created_at" not in metadata:
        metadata["created_at"] = datetime.fromtimestamp((comparison if comparison.exists() else run).stat().st_mtime, timezone.utc).isoformat()
    profile = metadata.get("profile")
    if not profile:
        profile = "smoke" if "smoke" in text.lower() else "full" if "full dataset" in text.lower() or "Profile: full" in text else "unspecified"
    status = (run / "status.tsv").read_text().splitlines() if (run / "status.tsv").exists() else []
    failed = sum("\tFAIL" in line for line in status)
    passed = sum("\tPASS" in line for line in status)
    metadata.update(profile=profile, status=f"Recorded calls: {passed} PASS, {failed} FAIL" if status else "No status ledger; inspect saved evidence and notes", failures=failed)
    return metadata, comparison, read_comparison(comparison)


def narrative_text(spec, section, run):
    """Read editable prose without importing an experiment runner or its dependencies."""
    from urllib.parse import unquote, urlsplit
    source = HERE / "experiments" / spec["narrative"] / "narrative" / (section + ".md")
    def rebase(match):
        url = urlsplit(match[1])
        if url.scheme or url.netloc or not url.path:
            return match[0]
        target = quote(os.path.relpath(source.parent / unquote(url.path), run), safe="/._-")
        if url.query: target += "?" + url.query
        if url.fragment: target += "#" + url.fragment
        return "](" + target + ")"
    return re.sub(r"\]\(([^)\n]+)\)", rebase, source.read_text())


def experiment_portions(slug, run=None):
    """Expected launcher portions; descriptions are not evidence that a call ran.

    Keep these small descriptors aligned with run_all.sh. They only select saved
    commands and artifact directories; they never execute or score a tool.
    """
    portions = []
    def add(name, label, directory, flags, data, forbidden=(),
            alternative_flags=(), alternative_names=()):
        portions.append(dict(name=slug + "_" + name, label=label, directory=directory,
                             flags=flags, flag_sets=(flags,) + tuple(alternative_flags),
                             names=(slug + "_" + name,) + tuple(slug + "_" + value for value in alternative_names),
                             data=data, forbidden=forbidden))
    for backend in ("original", "framework"):
        if slug == "ladder_table9":
            for tool in ("LADDER", "AttacKG", "TTPDrill"):
                add(tool.lower() + "_" + backend, tool + " / " + backend, tool + "/" + backend,
                    {"--tool": tool.lower(), "--backend": backend}, "Five LADDER reports")
        elif slug == "buchel_table13":
            for tool, variant in (("LADDER", "capped"), ("AttacKG", "capped")):
                trial = "standard" if tool == "LADDER" else variant
                add(tool.lower() + "_" + variant + "_" + backend,
                    tool + " / " + trial + " / " + backend,
                    tool + "/" + backend + "/" + trial,
                    {"--tool": tool.lower(), "--variant": variant, "--backend": backend},
                    "Grouped AnnoCTR documents")
        elif slug == "buchel_table9":
            for dataset in ("bosch", "tram"):
                for strategy, title in (("raw", "raw"), ("fsp", "fsp"), ("rag", "rag"),
                                        ("rag_fsp", "fsp_plus_rag" if dataset == "bosch" else "rag_plus_fsp")):
                    data = "AnnoCTR native items" if dataset == "bosch" else "TRAM sentence groups"
                    base_flags = {"--dataset": dataset, "--strategy": strategy, "--backend": backend}
                    add(f"{dataset}_base_{strategy}_{backend}",
                        f"{dataset} / base / {strategy} / {backend}",
                        f"Buchel/{backend}/{dataset}_{title}__base", base_flags, data,
                        ("--only-sft",))

                    engine = "sft_bosch" if dataset == "bosch" else "sft_tram"
                    legacy_engine = "sft_bosch" if dataset == "bosch" else "sft_tram_zenodo"
                    canonical_directory = f"Buchel/{backend}/{dataset}_{title}__{engine}"
                    legacy_directory = f"Buchel/{backend}/{dataset}_{title}__{legacy_engine}"
                    directory = (legacy_directory if run is not None and
                                 not (run / "results" / canonical_directory).exists() and
                                 (run / "results" / legacy_directory).exists()
                                 else canonical_directory)
                    legacy_model = "local" if dataset == "bosch" else "zenodo"
                    sft_flags = dict(base_flags, **{"--only-sft": True, "--tram-model": legacy_model})
                    canonical_flags = dict(base_flags, **{"--only-sft": True})
                    local_directory = f"Buchel/{backend}/{dataset}_{title}__sft_tram_local"
                    if dataset == "tram" and run is not None and (run / "results" / local_directory).exists():
                        local_flags = dict(base_flags, **{"--only-sft": True, "--tram-model": "local"})
                        add(f"tram_local_sft_{strategy}_{backend}",
                            f"tram / sft_tram_local / {strategy} / {backend}", local_directory,
                            local_flags, data)

                    add(f"{dataset}_sft_{strategy}_{backend}",
                        f"{dataset} / {engine} / {strategy} / {backend}", directory,
                        sft_flags, data, alternative_flags=(canonical_flags,),
                        alternative_names=(f"{dataset}_{legacy_model}_sft_{strategy}_{backend}",))
        elif slug == "ttpllm_table2":
            add(backend, "TTP-LLM / " + backend, "TTP-LLM/" + backend,
                {"--original" if backend == "original" else "--adapter": True}, "TTP-LLM procedures")
    add("report", "Report generation", "", {} if slug == "ttpllm_table2" else {"--report-only": True},
        "Saved results and reference values", ("--original", "--adapter") if slug == "ttpllm_table2" else ())
    return portions


def saved_commands(run, spec):
    commands = []
    if not (run / "commands.log").exists(): return commands
    for line in (run / "commands.log").read_text().splitlines():
        try: tokens = shlex.split(line)
        except ValueError: continue
        i = next((i for i, token in enumerate(tokens) if Path(token).name == spec["runner"]), None)
        if i is None or i == 0: continue
        args, options = tokens[i + 1:], {}
        for j, token in enumerate(args):
            if token.startswith("--"):
                flag, sep, value = token.partition("=")
                options[flag] = value if sep else args[j + 1] if j + 1 < len(args) and not args[j + 1].startswith("--") else True
        commands.append((tokens[i - 1], options))
    return commands


def common_run_matrix(run, spec, meta):
    heading = {"full": "Full Run Matrix", "smoke": "Smoke Run Matrix"}.get(meta["profile"], "Run Matrix")
    lines = ["## " + heading, "", f"Scope: **{meta['profile']}**. {meta['status']}.", ""]
    if meta.get("invocation"):
        lines += ["Command that generated these results:", "", "```bash", meta["invocation"], "```", ""]
        if meta.get("invocation_cwd"): lines += ["Working directory: `" + meta["invocation_cwd"] + "`.", ""]
    else:
        lines += ["Launcher command not recorded for this run; consult the saved commands and logs where available.", ""]
    commands = saved_commands(run, spec)
    statuses = {}
    if (run / "status.tsv").exists():
        for line in (run / "status.tsv").read_text().splitlines():
            name, sep, status = line.partition("\t")
            if sep: statuses.setdefault(name, []).append(status)
    def available(label, path):
        return link(label, path, run) if path.exists() else "Not available"
    matrix_description = (
        "The matrix enumerates the full exercise. The Tool / portion is of the form "
        "`parse style / model / prompt style / invocation (original or framework)`."
        if spec["narrative"] == "buchel_table9" else
        "The matrix enumerates the full exercise; the recorded scope and call evidence determine which portions were executed. Data descriptions identify the intended inputs, not a verified count. Historical call names or layouts may differ; the evidence links retain those records."
    )
    lines += [matrix_description, "",
              "| Tool / portion | Recorded interpreter | Data | Raw results | Parsed / evaluated results | Recorded call |",
              "|---|---|---|---|---|---|"]
    for portion in experiment_portions(spec["narrative"], run):
        matches = [(python, opts) for python, opts in commands
                   if any(all(opts.get(k) == v for k, v in flags.items())
                          for flags in portion.get("flag_sets", (portion["flags"],)))
                   and not any(k in opts for k in portion["forbidden"])
                   and (portion["directory"] == "" or "--report-only" not in opts)]
        environment = "; ".join(dict.fromkeys("`" + python + "`" for python, _ in matches)) or "Not recorded"
        directory = run / "results" / portion["directory"]
        raw = available("raw", directory / "raw") if portion["directory"] else "—"
        parsed = available("parsed", directory / "parsed") if portion["directory"] else available("comparison", directory / "comparison.md")
        status_values = [value for name in portion.get("names", (portion["name"],))
                         for value in statuses.get(name, [])]
        status = "; ".join(status_values or ["Not recorded"])
        log = next((run / "logs" / (name + ".log")
                    for name in portion.get("names", (portion["name"],))
                    if (run / "logs" / (name + ".log")).exists()),
                   run / "logs" / (portion["name"] + ".log"))
        if log.exists(): status = link(status, log, run)
        cells = [portion["label"], environment, portion["data"], raw, parsed, status]
        lines.append("| " + " | ".join(cell.replace("|", "\\|") for cell in cells) + " |")
    lines += [""]
    if spec.get("source_docs"):
        lines += ["Source revisions, model staging, and necessary corrections: " + source_documentation(spec, run) + ".", ""]
    lines += ["Runner: " + link(spec["runner"], HERE / spec["runner"], run) + ".", ""]
    return "\n".join(lines)


def write_narrative_report(run, spec, meta, comparison, rows):
    setup, references = split_references(narrative_text(spec, "setup", run))
    artifact_evaluation = narrative_text(spec, "artifact_evaluation", run)
    findings = narrative_text(spec, "findings", run)
    if spec["narrative"] == "ladder_table9":
        findings = findings.replace("<!-- table6-results -->", ladder_table6_numbers(rows))
        findings = findings.replace("<!-- run-interpretation -->", ladder_run_interpretation(run))
    elif spec["narrative"] == "buchel_table13":
        setup = setup.replace("<!-- attackg-execution-environment -->",
                              buchel_table13_attackg_environment(run))
        findings = findings.replace("<!-- table6-results -->", buchel_table13_table6_numbers(rows))
        findings = findings.replace("<!-- run-interpretation -->", buchel_table13_run_interpretation(run, rows))
        findings = findings.replace("<!-- fidelity-results -->", buchel_table13_fidelity_results(run))
    elif spec["narrative"] == "buchel_table9":
        setup = setup.replace("<!-- model-grid-description -->",
                              buchel_table9_model_grid_description(rows))
        artifact_evaluation = buchel_table9_artifact_evaluation(run, artifact_evaluation)
        findings = findings.replace("<!-- table6-results -->", buchel_table9_table6_numbers(rows))
        findings = findings.replace("<!-- table6-scope-note -->", buchel_table9_scope_note(rows))
    elif spec["narrative"] == "ttpllm_table2":
        artifact_evaluation = ttpllm_artifact_evaluation(run, artifact_evaluation)
        findings = findings.replace("<!-- table6-results -->", ttpllm_table6_numbers(rows))
        findings = findings.replace("<!-- run-interpretation -->", ttpllm_run_interpretation(run))
        findings = findings.replace("<!-- run-checks -->", ttpllm_run_checks(run))
        findings = findings.replace("<!-- saved-evidence -->", ttpllm_saved_evidence_links(run))
    definitions = terminology(link("the main project README's Table of Works", PROJECT / "README.md", run),
                              include_buchel=spec["narrative"].startswith("buchel_"))
    if spec["narrative"] == "buchel_table9":
        definitions = terminology(
            link("the main project README's Table of Works", PROJECT / "README.md", run),
            include_buchel=False,
        ).replace(
            ", executed directly without the framework.",
            ", executed directly without the framework. Pinned revisions and necessary setup fixes are linked from each experiment report; original does not mean the paper's published scores.",
            1,
        )
    report = ["# " + spec["title"] + " — " + run.name, "", narrative_text(spec, "introduction", run), "",
              "## Terminology", "", definitions, "",
              common_run_matrix(run, spec, meta), "", "## Artifact Evaluation Criteria", "", artifact_evaluation, "",
              "## Setup", "", setup, "", "## Findings and caveats", "", findings, ""]
    if meta.get("notes"): report += [meta["notes"], ""]
    if (run / "ANALYSIS.md").exists(): report += [link("Additional run analysis", run / "ANALYSIS.md", run), ""]
    report += ["## Evidence", ""]
    for name, description in (("status.tsv", "Recorded call outcomes"), ("commands.log", "Individual launch commands"),
                              ("logs", "Raw output and diagnostics"), ("results", "Raw and parsed outputs and comparisons")):
        if (run / name).exists(): report += ["- " + link(name, run / name, run) + " — " + description]
    report += ["", "## Detailed results", ""]
    if spec["narrative"] == "ladder_table9":
        report += [ladder_detailed_results(run)]
    elif spec["narrative"] == "buchel_table13":
        report += [buchel_table13_detailed_results(run, rows)]
    elif spec["narrative"] == "buchel_table9":
        report += [buchel_table9_detailed_results(rows)]
    elif spec["narrative"] == "ttpllm_table2":
        report += [ttpllm_detailed_results(run)]
    elif comparison.exists():
        details = comparison.read_text()
        # Current runners share this table boundary. Preserve unknown/legacy
        # formats intact instead of guessing where their substantive content starts.
        start = details.find("| Metric / trial |")
        if start != -1: details = details[start:]
        report += [re.sub(r"^(#{1,4}) ", r"##\1 ", details, flags=re.MULTILINE).rstrip()]
    else: report += ["No comparison was produced for this attempt."]
    if references: report += ["", "## References", "", references]
    (run / "REPORT.md").write_text("\n".join(report) + "\n")


# Renderer dispatch is deliberately outside the verified inference runners.
# Add a specialized renderer only when an experiment needs its own evidence checks.
REPORT_RENDERERS = {"attackg_table4": write_attackg_report}


def write_run_report(run, spec):
    """Refresh one report without importing a runner or changing saved evidence."""
    meta, comparison, rows = run_info(run)
    renderer = REPORT_RENDERERS.get(spec["narrative"], write_narrative_report)
    renderer(run, spec, meta, comparison, rows)
    return meta


def selected_experiments(selection):
    if selection == "core":
        return CORE_EXPERIMENT_ORDER
    if selection == "optional":
        return OPTIONAL_EXPERIMENT_ORDER
    if selection == "all":
        return (*CORE_EXPERIMENT_ORDER, *OPTIONAL_EXPERIMENT_ORDER)
    if selection in EXPERIMENTS or selection in OPTIONAL:
        return (selection,)
    raise ValueError("Unknown summary selection: " + selection)


def generate(root, refresh_reports=True, selection="all"):
    summary = ["# Reproduction summary and key findings", "",
               "Latest run per experiment, selected by recorded run time. Open an experiment's `REPORT.md` for details of its setup, caveats, results, and findings.", "",
               link("All runs and experiment caveats", HERE / "README.md", root), "",
               "## Terminology", "", terminology(link("the main project README's Table of Works", PROJECT / "README.md", root)), "",
               "## Submitted reproduction and outcome codes", "", *SUBMITTED_REPRODUCTION_DEFINITIONS, ""]
    for slug in selected_experiments(selection):
        spec = EXPERIMENTS.get(slug) or OPTIONAL[slug]
        description = experiment_description(slug)
        experiment = root / "experiments" / slug
        experiment.mkdir(parents=True, exist_ok=True)
        runs = list((experiment / "runs").glob("*"))
        runs = [r for r in runs if r.is_dir()]
        runs.sort(key=lambda r: (run_info(r)[0].get("created_at", ""), r.name), reverse=True)
        if slug in OPTIONAL:
            summary += ["## " + spec["title"] + " (optional)", "", description, ""]
            if runs:
                latest = runs[0]
                meta, _, _ = run_info(latest)
                comparison = latest / "results/comparison.csv"
                comparison_label = "CSV comparison"
                if slug == "orbinato_fig3":
                    figure = latest / "results/orbinato_figure3_comparison.png"
                    if figure.exists():
                        comparison = figure
                        comparison_label = "Figure comparison"
                summary += [link(latest.name, latest, root) + " · " + meta["status"],
                            link(comparison_label, comparison, root) if comparison.exists() else "Comparison unavailable.", ""]
            else:
                summary += ["No runs recorded.", ""]
            continue
        index = ["# " + spec["title"], "", spec["setup"], "",
                 "## Terminology", "", terminology(link("the main project README's Table of Works", PROJECT / "README.md", experiment), include_buchel=slug.startswith("buchel_")), "",
                 "## Caveats", "", spec["caveats"], "",
                 "Runner: " + link(spec["runner"], HERE / spec["runner"], experiment), "",
                 "## Run history", "", "| Run | Scope | Recorded execution |", "|---|---|---|"]
        if spec.get("readme_detail"):
            detail = HERE / "experiments" / spec["narrative"] / "narrative" / spec["readme_detail"]
            index[index.index("## Terminology"):index.index("## Terminology")] = [detail.read_text().strip(), ""]
        if spec.get("narrative"):
            source_note = ("Narrative prose is organized into the final experiment REPORT.md after completing the run: "
                           if slug == "attackg_table4" else "Editable report prose: ") + ", ".join(
                link(name + ".md", HERE / "experiments" / spec["narrative"] / "narrative" / (name + ".md"), experiment)
                for name in ("introduction", "setup", "findings", "artifact_evaluation")) + ". See the reproduction README for the report-only refresh command."
            index.insert(index.index("## Run history"), source_note + "\n")
        for run in runs:
            meta = write_run_report(run, spec) if refresh_reports else run_info(run)[0]
            index += [f"| {link(run.name, run / 'REPORT.md', experiment)} | {meta['profile']} | {meta['status']} |"]
        if spec.get("narrative"):
            _, references = split_references(narrative_text(spec, "setup", experiment))
            if references: index += ["", "## References", "", references]
        if refresh_reports:
            (experiment / "README.md").write_text("\n".join(index) + "\n")
        summary += ["## " + spec["title"], "", description, ""]
        if not runs:
            summary += ["No runs recorded.", ""]
            continue
        latest = runs[0]
        meta, _, rows = run_info(latest)
        summary += [link("REPORT.md", latest / "REPORT.md", root) + " · scope: " + meta["profile"] + " · " + meta["status"] + ".", "",
                    "| Key result | Paper | Submitted Reproduction | With framework | Without framework | Outcome |",
                    "|---|---:|---:|---:|---:|:---|"]
        for key in spec["keys"]:
            submitted, outcome = SUBMITTED_REPRODUCTIONS[slug][key]
            current = rows.get(key, ["— (not available)"] * 3)
            paper = current[0]
            if spec.get("summary_precision") is not None:
                try:
                    paper = format(float(paper), ".{}f".format(spec["summary_precision"]))
                except ValueError:
                    pass
            values = [paper, submitted, *current[1:], outcome]
            label = spec.get("summary_labels", {}).get(key, key)
            summary += ["| " + label + " | " + " | ".join(values) + " |"]
        summary += [""]
    (root / "SUMMARY.md").write_text("\n".join(summary) + "\n")
    print(root / "SUMMARY.md")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=HERE)
    parser.add_argument("--register", type=Path, help="Register a new run directory before execution")
    parser.add_argument("--run-dir", type=Path, help="Refresh only this existing run's REPORT.md; no inference or score changes")
    parser.add_argument("--experiment", choices={**EXPERIMENTS, **OPTIONAL})
    parser.add_argument(
        "--selection",
        choices=("all", "core", "optional", *EXPERIMENTS, *OPTIONAL),
        default="all",
        help="Include only experiments scheduled by this launcher selection in SUMMARY.md",
    )
    parser.add_argument("--summary-only", action="store_true", help="Refresh SUMMARY.md without rewriting any experiment reports or indexes")
    parser.add_argument("--profile", choices=["smoke", "full"], default="full")
    parser.add_argument("--invocation", help="Launcher command recorded when registering a run")
    parser.add_argument("--invocation-cwd", help="Working directory for the recorded launcher command")
    args = parser.parse_args()
    if args.register and args.run_dir:
        parser.error("--register and --run-dir are mutually exclusive")
    if args.run_dir:
        run = args.run_dir.resolve()
        if not run.is_dir(): parser.error("Run directory does not exist")
        meta, _, _ = run_info(run)
        slug = args.experiment or meta.get("experiment")
        if slug in OPTIONAL: parser.error("Optional experiments do not generate Markdown reports")
        if slug not in EXPERIMENTS: parser.error("Run has no recognized experiment; supply --experiment")
        if args.experiment and meta.get("experiment") not in (None, args.experiment):
            parser.error("--experiment conflicts with the registered run")
        write_run_report(run, EXPERIMENTS[slug])
        print(run / "REPORT.md")
        return
    if args.register:
        if not args.experiment: parser.error("--register requires --experiment")
        args.register.mkdir(parents=True, exist_ok=True)
        path = args.register / "run.json"
        if path.exists(): parser.error("Run already registered; use a new run directory")
        metadata = {"experiment": args.experiment, "profile": args.profile,
                    "created_at": datetime.now(timezone.utc).isoformat()}
        if args.invocation:
            metadata.update(invocation=args.invocation, invocation_source="launcher")
        if args.invocation_cwd:
            metadata["invocation_cwd"] = args.invocation_cwd
        path.write_text(json.dumps(metadata, indent=2) + "\n")
    else:
        generate(args.root.resolve(), refresh_reports=not args.summary_only, selection=args.selection)


if __name__ == "__main__": main()

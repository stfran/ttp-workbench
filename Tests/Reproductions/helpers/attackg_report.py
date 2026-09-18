"""AttacKG report prose and read-only checks of this run's saved evidence.

Edit narrative/*.md for prose; edit this module for evidence checks and tables.
No adapter, inference, or scoring code is invoked here.
"""
import csv
import json
import os
from pathlib import Path
import re
import shlex
from urllib.parse import quote, unquote, urlsplit
from helpers.reporting import EXTERNAL, PROJECT, terminology

HERE = Path(__file__).resolve().parents[1]
NARRATIVE = HERE / "experiments/attackg_table4/narrative"


def relative_link(label, path, parent):
    return f"[{label}]({quote(os.path.relpath(path, parent), safe='/._-')})"


def narrative(section, run):
    """Rebase ordinary Markdown links from the editable source to the run."""
    source = NARRATIVE / (section + ".md")
    return rebase_links(source.read_text(encoding="utf-8"), source.parent, run)


def rebase_links(text, source_directory, target_directory):
    """Keep narrative and embedded-comparison links valid at either depth."""
    def rebase(match):
        target = match.group(1)
        url = urlsplit(target)
        if url.scheme or url.netloc or not url.path:
            return match.group(0)
        path = source_directory / unquote(url.path)
        rebased = quote(os.path.relpath(path, target_directory), safe="/._-")
        if url.query:
            rebased += "?" + url.query
        if url.fragment:
            rebased += "#" + url.fragment
        return "](" + rebased + ")"
    return re.sub(r"\]\(([^)\n]+)\)", rebase, text)


def expected_ids():
    with (HERE / "data/attackg/attackg_paper_counts.csv").open() as stream:
        titles = {row["report"] for row in csv.DictReader(stream)}
    labels = json.loads((HERE / "data/attackg/ground_truth_and_test_labels.json").read_text())
    return {row["file name"] for row in labels if row["title"] in titles}


def split_references(text):
    """Keep the bibliography editable in setup.md, but render it report-wide."""
    heading = re.search(r"^## References\s*$", text, flags=re.MULTILINE)
    if heading is None:
        return text, ""
    return text[:heading.start()].rstrip(), text[heading.end():].strip()


def load_predictions(path):
    data = json.loads(path.read_text())
    if not isinstance(data, list):
        raise ValueError("prediction payload is not a list")
    result = {}
    for row in data:
        if not isinstance(row, dict) or not isinstance(row.get("id"), str):
            raise ValueError("missing or invalid report ID")
        if row["id"] in result:
            raise ValueError("duplicate report ID")
        if row.get("error") or row.get("status") in ("failed", "error"):
            raise ValueError("prediction contains a failure")
        codes = row.get("ttps")
        if not isinstance(codes, list) or not all(isinstance(code, str) and code for code in codes):
            raise ValueError("missing or invalid prediction list")
        result[row["id"]] = set(codes)
    return result


def fidelity_checks(run, profile):
    expected = expected_ids()
    ledger = {}
    if (run / "status.tsv").exists():
        for line in (run / "status.tsv").read_text().splitlines():
            name, sep, status = line.partition("\t")
            if sep:
                ledger.setdefault(name, []).append(status)
    checks = []
    for tool in ("AttacKG", "TTPDrill"):
        check = {"tool": tool, "passed": False, "matched": 0, "counts": "—", "detail": ""}
        try:
            original, framework = [load_predictions(run / "results" / tool / backend / "parsed/predictions.json")
                                   for backend in ("original", "framework")]
            check["counts"] = f"{len(original)} / {len(framework)}"
            common = original.keys() & framework.keys()
            check["matched"] = sum(original[key] == framework[key] for key in common)
            ids_match = original.keys() == framework.keys()
            scope_ok = ((profile == "full" and set(original) == expected) or
                        (profile == "smoke" and len(original) == 1 and set(original) <= expected))
            predictions_ok = ids_match and scope_ok and check["matched"] == len(original)
            execution_ok = all(ledger.get(f"attackg_table4_{tool.lower()}_{backend}") == ["PASS"]
                               for backend in ("original", "framework"))
            check["passed"] = predictions_ok and execution_ok
            if check["passed"]:
                check["detail"] = "PASS — exact code-set agreement and recorded inference PASS; " + profile + " scope only"
            elif predictions_ok:
                check["detail"] = "Code sets agree, but clean inference PASS evidence is missing or includes failures"
            else:
                check["detail"] = "NOT SATISFIED — missing/extra IDs, incomplete scope, or differing code sets"
        except (OSError, ValueError, TypeError, KeyError) as error:
            check["detail"] = "Unavailable or invalid saved predictions: " + type(error).__name__
        checks.append(check)
    return checks


def current_evidence(run, profile, rows):
    lines = ["### Checks on this run", "",
             "These checks read the saved `parsed/predictions.json` files and inference status entries. "
             "They do not infer prediction equality from matching scores or rerun the tools.", "",
             "| Tool | Original / framework reports | Exact matching report code sets | Assessment |",
             "|---|---:|---:|---|"]
    for check in fidelity_checks(run, profile):
        lines.append(f"| {check['tool']} | {check['counts']} | {check['matched']} | {check['detail']} |")
    lines += ["", "Saved prediction evidence: " + "; ".join(
        relative_link(tool + " / " + backend, path, run)
        for tool in ("AttacKG", "TTPDrill") for backend in ("original", "framework")
        if (path := run / "results" / tool / backend / "parsed/predictions.json").exists()) + ".", "",
        "A full-run PASS here supports C2 on these eight reports. It does not certify the historic paper data, "
        "the exact historical executable state, or a new clean-machine deployment. "
        "The per-document and aggregate scoring evidence remains in the detailed comparison below.", ""]
    return "\n".join(lines)


def table6_numbers(rows):
    lines = ["| Tool | Table 6 Paper | Table 6 Repro. | Eight report micro-F1 | This run: original micro-F1 | This run: framework micro-F1 |",
             "|---|---:|---:|---:|---:|---:|"]
    # Camera-ready reference correction supplied by the author; not a rerun score.
    # findings.md preserves the submitted 0.042 value and explains the correction.
    for tool, historical in (("TTPDrill", ("0.358", "0.045")), ("AttacKG", ("0.804", "0.231"))):
        paper, framework, original = rows.get(tool + " micro f1", ["— (not available)"] * 3)
        lines.append(f"| {tool} | {historical[0]} | {historical[1]} | {paper} | {original} | {framework} |")
    return "\n".join(lines)


def recorded_commands(run):
    """Identify the five portions from the logged runner flags, without executing them."""
    commands = {}
    if not (run / "commands.log").exists():
        return commands
    flags = {"--run_original": "attackg_original", "--run_adapter": "attackg_framework",
             "--run_ttpdrill_original": "ttpdrill_original", "--run_ttpdrill_adapter": "ttpdrill_framework"}
    for line in (run / "commands.log").read_text().splitlines():
        try:
            args = shlex.split(line)
        except ValueError:
            continue
        script = next((i for i, arg in enumerate(args) if Path(arg).name == "reproduce_attackg_table4.py"), None)
        if script is None or script == 0:
            continue
        portion = next((name for flag, name in flags.items()
                        if flag + "_all" in args or flag + "_sample" in args), "report")
        commands[portion] = (args[script - 1], args[script + 1:])
    return commands


def run_matrix(run, meta):
    profile = meta["profile"]
    heading = {"full": "Full Run Matrix", "smoke": "Smoke Run Matrix"}.get(profile, "Run Matrix")
    lines = ["## " + heading, "", f"Scope: **{profile}**. {meta['status']}.", ""]
    if meta.get("invocation"):
        lines += ["Command that generated these results:", "", "```bash", meta["invocation"], "```", ""]
        if meta.get("invocation_cwd"):
            lines += ["Working directory: `" + meta["invocation_cwd"] + "`.", ""]
    else:
        lines += ["Launcher command not recorded for this run; individual calls are available in `commands.log` when present.", ""]
    commands = recorded_commands(run)
    statuses = {}
    if (run / "status.tsv").exists():
        for line in (run / "status.tsv").read_text().splitlines():
            name, sep, status = line.partition("\t")
            if sep:
                statuses.setdefault(name, []).append(status)

    def available(label, path):
        return relative_link(label, path, run) if path.exists() else "Not available"

    def argument(args, flag, default):
        for i, arg in enumerate(args):
            if arg == flag and i + 1 < len(args): return args[i + 1]
            if arg.startswith(flag + "="): return arg.split("=", 1)[1]
        return default

    lines += ["| Tool / portion | Execution environment | Data | Raw results | Parsed / evaluated results | Recorded call |",
              "|---|---|---|---|---|---|"]
    for tool, backend in (("AttacKG", "original"), ("AttacKG", "framework"),
                          ("TTPDrill", "original"), ("TTPDrill", "framework"), ("Report generation", "")):
        portion = tool.lower() + "_" + backend if backend else "report"
        interpreter, args = commands.get(portion, (None, []))
        environment = "Not recorded"
        if interpreter:
            path = Path(interpreter)
            try:
                shown = str(Path(os.path.abspath(path)).relative_to(PROJECT)) if path.is_absolute() else interpreter
            except ValueError:
                shown = interpreter
            environment = "`" + shown + "`"
            if backend == "framework":
                engine = argument(args, "--engine", "podman")
                environment += f"; {engine} / `ttp-workbench:{tool.lower()}`"
        if backend:
            text_dir = Path(argument(args, "--text_dir", str(HERE / "data/attackg")))
            if not text_dir.is_absolute():
                text_dir = Path(meta.get("invocation_cwd", PROJECT)) / text_dir
            count = {"full": "8 selected reports", "smoke": "1 selected report"}.get(profile, "Selected reports")
            data = available(count, text_dir)
            directory = run / "results" / tool / backend
            raw = available("raw", directory / "raw")
            parsed = available("predictions.json", directory / "parsed/predictions.json")
            if (directory / "parsed/adapter_outputs.json").exists():
                parsed += "; " + available("adapter_outputs.json", directory / "parsed/adapter_outputs.json")
        else:
            data = "Saved predictions and comparison labels"
            raw = "—"
            parsed = "; ".join(available(label, run / "results" / filename) for label, filename in (
                ("comparison", "comparison.md"), ("per-document counts", "attackg_per_report_counts.csv"),
                ("micro P/R/F1", "attackg_overall_prf.csv"), ("document-average P/R/F1", "attackg_overall_prf_macro.csv")))
        name = "attackg_table4_" + portion
        status = "; ".join(statuses.get(name, ["Not recorded"]))
        log = run / "logs" / (name + ".log")
        if log.exists(): status = relative_link(status, log, run)
        label = tool + (" / " + backend if backend else "")
        cells = (label, environment, data, raw, parsed, status)
        lines.append("| " + " | ".join(cell.replace("|", "\\|") for cell in cells) + " |")
    source_docs = (
        relative_link("AttacKG setup README", PROJECT / "Docker_Setup/AttacKG/README.md", run) + " and " +
        relative_link("TTPDrill setup README", PROJECT / "Docker_Setup/TTPDrill/README.md", run)
    )
    lines += ["", "Source pins, model provenance and environment corrections: " + source_docs + ".", "",
              "Runner: " + relative_link("reproduce_attackg_table4.py", HERE / "reproduce_attackg_table4.py", run) + ".", ""]
    return "\n".join(lines)


def write_attackg_report(run, spec, meta, comparison, rows):
    setup, references = split_references(narrative("setup", run))
    findings = narrative("findings", run).replace("<!-- table6-results -->", table6_numbers(rows))
    report = ["# " + spec["title"] + " — " + run.name, "", narrative("introduction", run), "",
              "## Terminology", "", terminology(relative_link("the main project README's Table of Works", PROJECT / "README.md", run), include_buchel=False), "",
              run_matrix(run, meta), "", "## Artifact Evaluation Criteria", "", narrative("artifact_evaluation", run), "",
              "## Setup", "", setup, "", "## Findings and caveats", "",
              findings, "", current_evidence(run, meta["profile"], rows), ""]
    if meta.get("notes"): report += [meta["notes"], ""]
    if (run / "ANALYSIS.md").exists():
        report += [relative_link("Additional run analysis", run / "ANALYSIS.md", run), ""]
    report += ["## Evidence", ""]
    for name, description in (("status.tsv", "Records the outcome of each tool execution"),
                              ("commands.log", "Records the commands used to launch each tool"),
                              ("logs", "records raw results and debugging information"),
                              ("results", "records the raw and parsed output of each tool's execution")):
        if (run / name).exists():
            report.append("- " + relative_link(name, run / name, run) + " - " + description)
    report += ["", "## Detailed results", ""]
    if comparison.exists():
        details = rebase_links(comparison.read_text(), comparison.parent, run)
        start = details.find("## Per-document technique comparison")
        if start != -1:
            details = details[start:]  # omit duplicated title, scope, and terminology
        details = re.sub(r"^(#{1,4}) ", r"##\1 ", details, flags=re.MULTILINE)
        report.append(details.rstrip())
    else:
        report.append("No comparison was produced for this attempt.")
    if references:
        report += ["", "## References", "", references]
    (run / "REPORT.md").write_text("\n".join(report) + "\n")

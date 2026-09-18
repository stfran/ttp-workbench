"""Table 4 presentation only: no inference, label changes, or prediction writes."""
import csv
import os
from pathlib import Path
from urllib.parse import quote

HERE = Path(__file__).resolve().parents[1]
from helpers.reporting import EXTERNAL
PAPER = "https://users.cs.northwestern.edu/~ychen/Papers/ESORICS_AttacKG.pdf"


def prf(tp, fp, fn):
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    return p, r, 2 * p * r / (p + r) if p + r else 0.0


def aggregate(counts, micro=False):
    if not counts:
        return None
    if micro:
        return prf(*(sum(row[i] for row in counts) for i in range(3)))
    values = [prf(*row) for row in counts]
    return tuple(sum(row[i] for row in values) / len(values) for i in range(3))


def published_counts():
    # Transcribed from the Techniques columns of Table 4, PDF page 14.
    with (HERE / "data/attackg/attackg_table4_all16_counts.csv").open() as stream:
        return list(csv.DictReader(stream))


def averaging_audit():
    rows = published_counts()
    lines = [
        "## Averaging method not published", "",
        f"[AttacKG Table 4, PDF page 14]({PAPER}#page=14) covers 16 reports. "
        "The following audit uses all 16 published technique-count rows, transcribed in "
        "`data/attackg/attackg_table4_all16_counts.csv`; these extra rows are reference data, not additional reproduction inputs.", "",
        "| Tool | Scope | Calculation | Precision | Recall | F1 |",
        "|---|---|---|---:|---:|---:|",
    ]
    for tool, published in (("AttacKG", (.771, .808, .789)), ("TTPDrill", (.196, .837, .318))):
        counts = [(int(row["GT"]) - int(row[tool + "_FN"]),
                   int(row[tool + "_FP"]), int(row[tool + "_FN"])) for row in rows]
        for method, values in (("Printed overall", published),
                               ("Document average (recalculated)", aggregate(counts)),
                               ("Micro (recalculated)", aggregate(counts, micro=True))):
            lines.append(f"| {tool} | 16 reports | {method} | " +
                         " | ".join(f"{value:.6f}" for value in values) + " |")
    lines += ["",
        "Neither recalculation matches the printed overall metrics at three decimals. "
        "The printed F1 values are consistent with the harmonic mean of the printed precision and recall, "
        "but that alone does not identify how precision and recall were aggregated. "
        "The averaging method therefore cannot be established from this table. "
        "Micro-F1 is the reproduction's reporting convention, not a verified claim about the paper; "
        "document-average results are also reported. Document-average F1 means the mean of per-document F1, "
        "not the harmonic mean of mean precision and mean recall.", "",
    ]
    return lines


def add_document_comparison(directory, frame, labels_by_key, present):
    """Expand the existing aggregate comparison; preserve its summary interface.

    `present` maps the four reproduction count prefixes to evaluated report keys.
    This keeps absent/smoke documents distinct from completed zero predictions.
    """
    path = Path(directory) / "comparison.md"
    existing = path.read_text(encoding="utf-8")
    marker = "| Metric / trial |"
    introduction, separator, metrics = existing.partition(marker)
    if not separator:
        raise ValueError("Expected the aggregate comparison table")
    # Idempotent when refreshing an already expanded report.
    introduction = introduction.split("## Per-document technique comparison", 1)[0].rstrip()
    introduction = introduction.replace("Document-average F1 is our primary reporting convention", "Micro-F1 is our primary reporting convention")
    ground_truth_doc = EXTERNAL / "AttacKG/Results/Attack Reports Analysis(1-8).docx"
    ground_truth_link = "[Attack Reports Analysis(1-8).docx](" + quote(os.path.relpath(ground_truth_doc, path.parent), safe="/._-") + ")"
    rows = frame.to_dict("records")
    columns = (("TTPDrill / paper", "ttp_orig"),
               ("TTPDrill / framework", "ttp_adap"),
               ("TTPDrill / original", "ttp_repr"),
               ("AttacKG / paper", "orig"),
               ("AttacKG / framework", "adap"),
               ("AttacKG / original", "repr"))

    def available(row, prefix):
        return prefix in ("orig", "ttp_orig") or row["key"] in present.get(prefix, set())

    def counts(row, prefix):
        return tuple(int(row[prefix + "_" + suffix]) for suffix in ("tp", "fp", "fn"))

    lines = ["## Per-document technique comparison", "",
        "Cells follow the notation in Table 4 of [2] with `-FN (+FP)` notation; TP = the corresponding GT minus FN. "
        "Paper cells use Paper GT; framework and original cells use Local GT. "
        "A dash means no evaluated prediction, not zero errors. "
        "Only technique identification is reproduced here, not entity/dependency extraction.", "",
        "| CTI report / metric | Paper GT | Local GT | " + " | ".join(title for title, _ in columns) + " |",
        "|---|---:|---:|" + "---:|" * len(columns),
    ]
    for row in rows:
        entry = labels_by_key.get(row["key"])
        local_gt = len(entry.labels) if entry is not None else "—"
        cells = [f"-{counts(row, prefix)[2]} (+{counts(row, prefix)[1]})"
                 if available(row, prefix) else "—" for _, prefix in columns]
        lines.append(f"| {row['report']} | {int(row['GT'])} | {local_gt} | " + " | ".join(cells) + " |")
    selected = [[counts(row, prefix) for row in rows if available(row, prefix)] for _, prefix in columns]
    lines.append("| Evaluated documents | — | — | " + " | ".join(str(len(items)) for items in selected) + " |")
    for micro, label in ((False, "Overall (document average)"), (True, "Overall (micro)")):
        values = [aggregate(items, micro=micro) for items in selected]
        for i, metric in enumerate(("precision", "recall", "F1")):
            cells = [f"{value[i]:.6f}" if value is not None else "—" for value in values]
            lines.append(f"| {label} {metric} | — | — | " + " | ".join(cells) + " |")
    lines += ["",
        "The paper-column overall rows above are recalculated from the eight selected paper rows, "
        "not the published 16-report totals. Reproduction aggregates cover only evaluated documents; "
        "a complete run evaluates all eight selected reports.", "",
        "## Ground-truth comparability", "",
        "`attackg_paper_counts.csv` matches the first eight technique-count rows in Table 4. "
        "The `ground_truth_and_test_labels.json` that we derived from transcribing " + ground_truth_link +
        " (see Setup in REPORT.md) is not an identical ground-truth reference. "
        "Count differences and tactic labels for the selected documents are shown below. "
        "Matching counts alone do not establish matching label identities.", "",
        "| CTI report | Paper GT | Local GT | Local tactic labels |",
        "|---|---:|---:|---|",
    ]
    for row in rows:
        entry = labels_by_key.get(row["key"])
        local = entry.labels if entry is not None else set()
        tactics = sorted(code for code in local if code.startswith("TA"))
        if entry is None or len(local) != int(row["GT"]) or tactics:
            lines.append(f"| {row['report']} | {int(row['GT'])} | " +
                         (str(len(local)) if entry is not None else "—") + " | " +
                         (", ".join(tactics) or "—") + " |")
    lines += [""]
    lines += averaging_audit()
    lines += ["## Aggregate metrics", "",
        "Here, Paper means recalculated values for the selected eight published rows. "
        "The published 16-report overall values appear only in the audit above.", "", separator + metrics]
    path.write_text(introduction + "\n\n" + "\n".join(lines), encoding="utf-8")

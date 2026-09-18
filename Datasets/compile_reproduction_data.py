#!/usr/bin/env python3
"""Builds ``Tests/Reproductions/data`` from ``Datasets/prior_work_data``.

``compile_data.py`` collects the raw inputs from the tool containers, the
staged Buchel release, and ``Datasets/resources`` during normal dataset
compilation. This compiler performs the format and layout transformations used
by the reproduction runners."""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
from pathlib import Path
import pickle
import re
import shutil
import sys
import tempfile


HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent
SUITE = PROJECT / "Tests" / "Reproductions"
RESOURCES = HERE / "resources"
PRIOR_WORK = HERE / "prior_work_data"
UNFETTER_DICT_SHA256 = "138c817edb755526d8bd9558982095afddeab4e6ca454d2714994cd8bcdf3bfc"

# A threat report with a historical embedded PHP web-shell sample is present in the data and might trigger host-based antivirus
# We defang it in place to avoid that for this file. We cannot guarantee it does not happen for other files
EMBEDDED_PHP_WEB_SHELL_BYTES = (
    bytes.fromhex("3c3f70687020406576616c28245f504f53545b2770617373776f7264275d293b3e"),
    bytes.fromhex("3c3f70687020406576616c28245f504f53545b2770617373776f7264275d293b3f3e"),
)
DEFANGED_PHP_WEB_SHELL = "[DEFANGED PHP WEB-SHELL EXAMPLE: code omitted]"
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

PINNED_FILES = {
    RESOURCES / "attackg_ground_truth_and_test_labels.json":
        "a336d7a79db1402131730fb032a1430500d19a9468287257f2579cbbd9a57373",
    RESOURCES / "attackg_paper_counts.csv":
        "39daabce335e079d74d2d3e9184e7270888135738f0cff2ae76e5eb659bb1ab1",
    RESOURCES / "raf_ag_ground_truth_labels.json":
        "05c28a1be4c248ebfe617c04be96c4715ec73f90dbc0f7a45912d8c84cdd3b02",
    RESOURCES / "raf_ag_paper_counts.csv":
        "704976c3058408d698ee58d20840a4110aa39321a883a10a01ad9b015f26b7e6",
    PROJECT / "Framework/utils/attack_stix/enterprise-attack.json":
        "dc1639caa5501d720e280cf1cbd8fbe009884a0c9b3e6e9ed9d0c25166c3d8f4",
}


def digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def require_digest(path: Path, expected: str) -> bytes:
    if not path.is_file():
        raise FileNotFoundError(path)
    payload = path.read_bytes()
    actual = digest(payload)
    if actual != expected:
        raise RuntimeError(f"Checksum mismatch for {path}: expected {expected}, found {actual}")
    return payload


def write_bytes(root: Path, relative: str | Path, payload: bytes) -> None:
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload)


def write_json(root: Path, relative: str | Path, value: object) -> None:
    write_bytes(root, relative, (json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode())


def source_bytes(prior_work: Path, relative: str | Path) -> bytes:
    path = prior_work / relative
    if not path.is_file():
        raise FileNotFoundError(
            f"Required prior-work input is missing: {path}. Run "
            "Datasets/compile_data.py --reproduction-sources-only first."
        )
    return path.read_bytes()


def defang_embedded_web_shell(payload: bytes, source: str, expected: int) -> bytes:
    """Remove a historical executable PHP sample without changing its context."""
    replacement = DEFANGED_PHP_WEB_SHELL.encode("ascii")
    needles = EMBEDDED_PHP_WEB_SHELL_BYTES
    actual = sum(payload.count(needle) for needle in needles)
    if actual != expected:
        raise RuntimeError(
            f"Expected {expected} embedded PHP web-shell sample(s) in {source}, found {actual}"
        )
    for needle in needles:
        payload = payload.replace(needle, replacement)
    return payload


def compile_ladder(root: Path, prior_work: Path) -> None:
    source = prior_work / "LADDER/LADDER_table_9_data"
    files = sorted(path for path in source.iterdir() if path.is_file()) if source.is_dir() else []
    if len(files) != 11:
        raise RuntimeError(f"Expected 11 LADDER prior-work files in {source}, found {len(files)}")
    for path in files:
        write_bytes(root, Path("LADDER_table_9_data") / path.name, path.read_bytes())


def compile_attackg(root: Path, prior_work: Path) -> None:
    labels_path = RESOURCES / "attackg_ground_truth_and_test_labels.json"
    counts_path = RESOURCES / "attackg_paper_counts.csv"
    labels = json.loads(require_digest(labels_path, PINNED_FILES[labels_path]))
    counts = require_digest(counts_path, PINNED_FILES[counts_path]).decode("utf-8")

    # Only the eight reports with supplied ground truth are executable inputs.
    # The remaining eight Table 4 rows are retained as reference counts so 
    # we can build the analysis of the published details vs what we see in the repository.
    clean_labels = []
    runnable = 0
    for item in labels:
        clean = {key: item[key] for key in ("file name", "title", "labels")}
        clean_labels.append(clean)
        if not item.get("labels"):
            continue
        runnable += 1
        filename = item["file name"]
        write_bytes(root, Path("attackg") / filename,
                    source_bytes(prior_work, Path("AttacKG") / filename))
    if len(clean_labels) != 16 or runnable != 8:
        raise RuntimeError(
            f"Expected 16 AttacKG label rows and eight runnable reports; found "
            f"{len(clean_labels)} and {runnable}"
        )

    write_json(root, "attackg/ground_truth_and_test_labels.json", clean_labels)
    all_lines = counts.splitlines()
    if len(all_lines) != 17:
        raise RuntimeError(f"Expected header plus 16 AttacKG count rows, found {len(all_lines)} lines")
    write_bytes(root, "attackg/attackg_table4_all16_counts.csv",
                ("\n".join(all_lines) + "\n").encode())
    write_bytes(root, "attackg/attackg_paper_counts.csv",
                "\n".join(all_lines[:9]).encode())


def compile_ttpllm(root: Path, prior_work: Path) -> None:
    for filename in ("MITRE_Procedures.csv", "MITRE_Procedures_encoded.csv"):
        write_bytes(root, filename,
                    source_bytes(prior_work, Path("TTP-LLM") / filename))


def compile_buchel(root: Path, prior_work: Path) -> None:
    for filename in ("bosch_cti_test_ds.json", "test_split.json", "bosch_test.json"):
        write_bytes(root, Path("buchel") / filename,
                    source_bytes(prior_work, Path("Buchel") / filename))


def compile_seqmask(root: Path, prior_work: Path) -> None:
    expected_samples = {
        "TTPDrill-subTTP.csv": 0,
        "data_origin13.csv": 0,
        "data_origin4.csv": 2,
    }
    for filename, expected in expected_samples.items():
        payload = source_bytes(prior_work, Path("SeqMask") / filename)
        write_bytes(root, "optional/seqmask/" + filename,
                    defang_embedded_web_shell(payload, "SeqMask/" + filename, expected))


def compile_rafag(root: Path, prior_work: Path) -> None:
    for filename in ("ground_truth_labels.json", "raf_ag_paper_counts.csv"):
        resource = RESOURCES / (
            "raf_ag_ground_truth_labels.json"
            if filename == "ground_truth_labels.json" else filename
        )
        write_bytes(root, "optional/rafag/" + filename,
                    require_digest(resource, PINNED_FILES[resource]))

    reports_root = prior_work / "RAF-AG/Dataset/CTI reports"
    reports = sorted(reports_root.glob("*.txt")) if reports_root.is_dir() else []
    if len(reports) != 30:
        raise RuntimeError(f"Expected 30 RAF-AG reports in {reports_root}, found {len(reports)}")
    for report in reports:
        write_bytes(root, "optional/rafag/texts/" + report.name, report.read_bytes())

    stix_path = PROJECT / "Framework/utils/attack_stix/enterprise-attack.json"
    objects = json.loads(require_digest(stix_path, PINNED_FILES[stix_path]))["objects"]

    def attack_id(obj: dict) -> str | None:
        return next((
            ref["external_id"] for ref in obj.get("external_references", [])
            if ref.get("source_name") == "mitre-attack" and "external_id" in ref
        ), None)

    tactics = {
        obj.get("x_mitre_shortname"): attack_id(obj)
        for obj in objects if obj.get("type") == "x-mitre-tactic"
    }
    mapping = {
        attack_id(obj): [
            tactics[phase["phase_name"]]
            for phase in obj.get("kill_chain_phases", [])
            if phase.get("phase_name") in tactics
        ]
        for obj in objects
        if obj.get("type") == "attack-pattern" and attack_id(obj)
    }
    write_json(root, "optional/rafag/tactic_lookup.json", mapping)


def compile_orbinato(root: Path, prior_work: Path) -> None:
    # document_data.py is the original repository's executable definition of
    # the six Figure 3 panels, including the extended per-malware label lists.
    namespace: dict[str, object] = {}
    definition = prior_work / "Orbinato/document_data.py"
    code = source_bytes(prior_work, "Orbinato/document_data.py").decode("utf-8")
    exec(compile(code, str(definition), "exec"), namespace)
    specs = [
        ("FIN6_ref_1", "a", namespace["fin6_files"][0], namespace["fin6_tec_1"]),
        ("FIN6_ref_2", "b", namespace["fin6_files"][1], namespace["fin6_tec_2"]),
        ("MenuPass_ref_2", "c", namespace["menuPass_files"][0], namespace["menuPass_tec_2"]),
        ("MenuPass_ref_8", "d", namespace["menuPass_files"][1], namespace["menuPass_tec_8"]),
        ("WizardSpider_ref_2", "e", namespace["wizardSpider_files"][1], namespace["wizardSpider_tec_2"]),
        ("WizardSpider_ref_7", "f", namespace["wizardSpider_files"][0], namespace["wizardSpider_tec_7"]),
    ]
    records = []
    for identifier, panel, source_path, labels in specs:
        filename = identifier + ".txt"
        write_bytes(root, "optional/orbinato/texts/" + filename,
                    source_bytes(prior_work, Path("Orbinato") / str(source_path)))
        paper_figure = "paper_figures/" + panel + ".png"
        source_figure = RESOURCES / "orbinato_original_figures" / (panel + ".png")
        write_bytes(root, "optional/orbinato/" + paper_figure,
                    source_figure.read_bytes())
        records.append({
            "id": identifier,
            "panel": panel,
            "labels": list(labels),
            "text_file": "texts/" + filename,
            "paper_figure": paper_figure,
        })
    write_json(root, "optional/orbinato/reports.json", records)


def compile_rcatt(root: Path, payload: bytes) -> None:
    # The rcATT label space intentionally matches the historical experiment.
    from Framework.utils.rcatt_ttp_map import ALL_TTPS, CODE_TACTICS, NAME_TACTICS

    try:
        wiki = pickle.loads(payload)
    except UnicodeDecodeError:
        wiki = pickle.loads(payload, encoding="latin1")

    whitespace = re.compile(r"\s+")
    technique = re.compile(r"^\s*(T\d{4})\b", re.IGNORECASE)
    tactic_field = re.compile(r"Tactic\|\s+(.+?)\s{4,}", re.IGNORECASE | re.DOTALL)
    tactic_codes = {
        whitespace.sub(" ", name.strip()).lower(): CODE_TACTICS[index]
        for index, name in enumerate(NAME_TACTICS)
    }
    rows: list[tuple[str, set[str]]] = []
    defanged_samples = 0
    for label, raw in wiki.items():
        match = technique.match(raw)
        if not match:
            raise RuntimeError(f"Unfetter entry has no leading technique code: {label!r}")
        codes = {match.group(1).upper()}
        text = raw[match.end():]
        field = tactic_field.search(text)
        if field:
            for name in field.group(1).split(","):
                key = whitespace.sub(" ", name.strip()).lower()
                if key and key not in tactic_codes:
                    raise RuntimeError(f"Unknown Unfetter tactic name {name!r} in {label!r}")
                if key:
                    codes.add(tactic_codes[key])
            text = text[:field.start()] + text[field.end():]
        text = whitespace.sub(" ", text).strip()
        needles = [value.decode("ascii") for value in EMBEDDED_PHP_WEB_SHELL_BYTES]
        defanged_samples += sum(text.count(value) for value in needles)
        for value in needles:
            text = text.replace(value, DEFANGED_PHP_WEB_SHELL)
        rows.append((text, codes))

    # Seven legacy technique IDs are outside rcATT's output vocabulary. They
    # remain valid evaluation records, with their in-vocabulary tactic labels
    if len(rows) != 121:
        raise RuntimeError(f"Expected 121 rcATT evaluation records, found {len(rows)}")
    if defanged_samples != 1:
        raise RuntimeError(
            f"Expected one embedded PHP web-shell sample in rcATT data, found {defanged_samples}"
        )
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\r\n")
    writer.writerow(["Text", *ALL_TTPS])
    for text, codes in rows:
        writer.writerow([text, *(1 if code in codes else 0 for code in ALL_TTPS)])
    write_bytes(root, "optional/rcatt/unfetter_wiki_preprocessed.csv",
                stream.getvalue().encode("utf-8"))


def csv_rows(path: Path) -> int:
    with path.open(newline="", encoding="utf-8") as stream:
        return sum(1 for _ in csv.reader(stream)) - 1


def validate(root: Path) -> None:
    defanged_inputs = {
        "optional/rcatt/unfetter_wiki_preprocessed.csv": 1,
        "optional/seqmask/data_origin4.csv": 2,
    }
    for relative, expected in defanged_inputs.items():
        payload = (root / relative).read_bytes()
        if any(value in payload for value in EMBEDDED_PHP_WEB_SHELL_BYTES):
            raise RuntimeError(f"Active embedded PHP web-shell sample remains in {relative}")
        actual = payload.count(DEFANGED_PHP_WEB_SHELL.encode("ascii"))
        if actual != expected:
            raise RuntimeError(
                f"Expected {expected} defanged PHP sample marker(s) in {relative}, found {actual}"
            )
    required = [
        "attackg/ground_truth_and_test_labels.json",
        "attackg/attackg_paper_counts.csv",
        "attackg/attackg_table4_all16_counts.csv",
        "buchel/bosch_cti_test_ds.json",
        "buchel/bosch_test.json",
        "buchel/test_split.json",
        "optional/orbinato/reports.json",
        "optional/rafag/ground_truth_labels.json",
        "optional/rafag/raf_ag_paper_counts.csv",
        "optional/rafag/tactic_lookup.json",
    ]
    missing = [relative for relative in required if not (root / relative).is_file()]
    if missing:
        raise RuntimeError("Reproduction data is missing: " + ", ".join(missing))
    checks = {
        "MITRE_Procedures.csv": 9532,
        "MITRE_Procedures_encoded.csv": 9532,
        "optional/rcatt/unfetter_wiki_preprocessed.csv": 121,
        "optional/seqmask/data_origin13.csv": 6509,
        "optional/seqmask/TTPDrill-subTTP.csv": 4938,
        "optional/seqmask/data_origin4.csv": 1286,
    }
    for relative, expected in checks.items():
        actual = csv_rows(root / relative)
        if actual != expected:
            raise RuntimeError(f"{relative}: expected {expected} rows, found {actual}")
    if len(list((root / "attackg").glob("*.txt"))) != 8:
        raise RuntimeError("AttacKG compilation did not produce exactly eight runnable reports")
    if len(list((root / "optional/rafag/texts").glob("*.txt"))) != 30:
        raise RuntimeError("RAF-AG compilation did not produce exactly 30 report texts")
    if len(json.loads((root / "optional/orbinato/reports.json").read_text())) != 6:
        raise RuntimeError("Orbinato compilation did not produce exactly six panel records")
    paper_figures = root / "optional/orbinato/paper_figures"
    if {path.name for path in paper_figures.glob("*.png")} != {
            panel + ".png" for panel in "abcdef"}:
        raise RuntimeError("Orbinato compilation did not produce the six paper-reference panels")
    ladder = root / "LADDER_table_9_data"
    if len(list(ladder.glob("*.txt"))) != 5 or len(list(ladder.glob("*.json"))) != 5:
        raise RuntimeError("LADDER compilation did not produce five text/label pairs")


def compare_trees(expected: Path, actual: Path) -> list[str]:
    expected_files = {
        path.relative_to(expected): digest(path.read_bytes())
        for path in expected.rglob("*") if path.is_file()
    }
    actual_files = {
        path.relative_to(actual): digest(path.read_bytes())
        for path in actual.rglob("*") if path.is_file()
    }
    differences = []
    for path in sorted(expected_files.keys() | actual_files.keys()):
        if path not in expected_files:
            differences.append(f"unexpected: {path}")
        elif path not in actual_files:
            differences.append(f"missing: {path}")
        elif expected_files[path] != actual_files[path]:
            differences.append(f"changed: {path}")
    return differences


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--prior-work-root",
        type=Path,
        default=PRIOR_WORK,
        help="Raw input root populated by compile_data.py (default: Datasets/prior_work_data)",
    )
    parser.add_argument("--output", type=Path, default=SUITE / "data")
    parser.add_argument("--check", action="store_true",
                        help="Compile in a temporary directory and compare with --output")
    parser.add_argument("--skip-existing", action="store_true",
                        help="Validate and reuse a complete existing output without recompiling")
    args = parser.parse_args()
    prior_work = args.prior_work_root.expanduser().resolve()
    output = args.output.expanduser().resolve()
    if args.skip_existing and output.is_dir():
        validate(output)
        count = sum(path.is_file() for path in output.rglob("*"))
        print(f"Reproduction data already exists ({count} files); skipping compilation: {output}")
        return 0
    for path, expected in PINNED_FILES.items():
        require_digest(path, expected)
    unfetter_path = prior_work / "rcATT/dict_wiki"
    unfetter = require_digest(unfetter_path, UNFETTER_DICT_SHA256)

    with tempfile.TemporaryDirectory(prefix="reproduction-data-") as temporary:
        staged = Path(temporary) / "data"
        staged.mkdir()
        compile_ladder(staged, prior_work)
        compile_attackg(staged, prior_work)
        compile_ttpllm(staged, prior_work)
        compile_buchel(staged, prior_work)
        compile_seqmask(staged, prior_work)
        compile_rafag(staged, prior_work)
        compile_orbinato(staged, prior_work)
        compile_rcatt(staged, unfetter)
        validate(staged)

        if args.check:
            if not output.is_dir():
                raise FileNotFoundError(f"Cannot check missing output tree: {output}")
            differences = compare_trees(staged, output)
            if differences:
                raise RuntimeError("Reproduction data differs:\n  " + "\n  ".join(differences))
            count = sum(path.is_file() for path in staged.rglob("*"))
            print(f"PASS: all {count} generated files match {output}")
            return 0

        if output.exists():
            differences = compare_trees(staged, output) if output.is_dir() else ["output is not a directory"]
            if differences:
                raise RuntimeError(
                    f"Refusing to replace existing data at {output}. Generate to another --output "
                    "and review it first. Differences:\n  " + "\n  ".join(differences)
                )
            print(f"Data already matches the authoritative compilation: {output}")
        else:
            output.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(staged, output)
            print(f"Populated {output}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

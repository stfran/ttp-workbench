"""Small Markdown writer shared by the otherwise independent experiment runners."""
import json
import os
from contextlib import contextmanager
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
PROJECT = HERE.parents[1]
EXTERNAL = Path(os.environ.get("REPRO_EXTERNAL_ROOT", HERE / ".runtime/external_tools")).resolve()


def terminology(reference="the main project README's Table of Works", *, include_buchel=True):
    definitions = (
        "Original: the original tool released at the GitHub repository linked in " + reference +
        ", executed directly without the framework.\n\n"
        "Framework: that original tool adapted into TTP-WorkBench and executed through its framework adapter.\n\n"
        "Paper: the results originally published in the cited paper, used as reference values rather than outputs of a new run."
    )
    if include_buchel:
        definitions += "\n\nSource exception: The Büchel artifact includes releases of external tools that we reuse as experiment-specific variants to reproduce Büchel et al.'s results."
    return definitions


def install_exit_handler():
    """Let adapter context managers clean up when the shell's timeout sends TERM."""
    import signal
    def terminate(signum, frame):
        raise SystemExit(128 + signum)
    signal.signal(signal.SIGTERM, terminate)


def isolate_framework_cache():
    """Redirect the framework's automatic STIX refresh away from tracked files."""
    import shutil
    import sys
    sys.path.insert(0, str(PROJECT))
    from Framework.utils import attack_lookup as lookup
    directory = EXTERNAL / "cache/attack_stix"
    directory.mkdir(parents=True, exist_ok=True)
    for filename in ("enterprise-attack.json", "mobile-attack.json", "ics-attack.json", "meta.json"):
        target = directory / filename
        if not target.exists(): shutil.copy2(PROJECT / "Framework/utils/attack_stix" / filename, target)
    lookup.BASE_DIR = directory
    lookup.ENTERPRISE_ATTACK = directory / "enterprise-attack.json"
    lookup.MOBILE_ATTACK = directory / "mobile-attack.json"
    lookup.ICS_ATTACK = directory / "ics-attack.json"
    lookup.META_PATH = directory / "meta.json"
    lookup.COLLECTIONS = {key: (label, directory / filename, filename)
                          for key, (label, _, filename) in lookup.COLLECTIONS.items()}


def tool_directory(directory, tool, backend):
    names = {"attackg": "AttacKG", "ttpdrill": "TTPDrill", "ladder": "LADDER",
             "buchel": "Buchel", "ttpllm": "TTP-LLM"}
    return Path(directory) / names.get(tool, tool) / backend


@contextmanager
def output_section(kind, label):
    """Mark output provenance in the full log and rolling console preview."""
    print(f"=== {kind} | {label} ===", flush=True)
    try:
        yield
    finally:
        # A tool may end stdout without a newline; keep the closing marker separate.
        print("\n=== EXPERIMENT RUNNER OUTPUT ===", flush=True)


def save_scores(directory, backend, name, scores, *, tool=None):
    path = tool_directory(directory, tool or name, backend) / (name + ".scores.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(scores, indent=2) + "\n", encoding="utf-8")


def markdown(directory, title, paper, note="", *, include_buchel=True):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    results = {}
    for backend in ("framework", "original"):
        results[backend] = {}
        for path in sorted((directory / backend).glob("*.scores.json")):
            results[backend].update(json.loads(path.read_text()))
        # New tool-first layout; retain read support for historical score files.
        for path in sorted(directory.glob("*/" + backend + "/*.scores.json")):
            results[backend].update(json.loads(path.read_text()))
    keys = list(dict.fromkeys(list(paper) + list(results["framework"]) + list(results["original"])))
    def display(value):
        if value is None:
            return "— (not available)"
        return "{:.6f}".format(value) if isinstance(value, (int, float)) else str(value)
    lines = ["# " + title, "", note, "", terminology(include_buchel=include_buchel), "", "| Metric / trial | Paper | With framework | Without framework |",
             "|---|---:|---:|---:|"]
    for key in keys:
        lines.append("| " + key + " | " + " | ".join(display(values.get(key)) for values in
                     (paper, results["framework"], results["original"])) + " |")
    (directory / "comparison.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def check_predictions(results, expected):
    if len(results) != len(expected):
        raise RuntimeError("Prediction count does not match input count")
    if any(r.get("error") or r.get("status") in ("error", "failed") for r in results):
        raise RuntimeError("Adapter reported a failed prediction; see raw outputs")
    if any(not isinstance(r.get("ttps"), list) for r in results):
        raise RuntimeError("Adapter did not produce a prediction list")
    if [str(r.get("id")) for r in results] != [str(x) for x in expected]:
        raise RuntimeError("Prediction order/names do not match inputs")

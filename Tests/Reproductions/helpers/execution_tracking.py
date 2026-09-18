"""Launcher timing and rough reading estimates; never infer legacy durations.

Reading uses 75 visible words/minute. Analysis is additional: twice reading.
Keep this implementation detail out of generated Markdown guidance.
"""
import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re
import time


def write_json(path, value):
    path = Path(path)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, indent=2) + "\n")
    temp.replace(path)


def start_record(**kwargs):
    return dict(started_at=datetime.now(timezone.utc).isoformat(),
                monotonic_start=time.monotonic(), status="running", **kwargs)


def close_record(record, status, code=None):
    if record.get("status") != "running":
        return
    record.update(finished_at=datetime.now(timezone.utc).isoformat(),
                  elapsed_seconds=max(0, time.monotonic() - record["monotonic_start"]),
                  status=status)
    if code is not None:
        record["exit_code"] = code


def record_event(path, action, *, experiment=None, run=None, name=None, code=0, invocation=None):
    path = Path(path)
    if action == "start-suite":
        data = start_record(invocation=invocation, experiments=[])
    else:
        data = json.loads(path.read_text())
        if action == "start-experiment":
            data["experiments"].append(start_record(experiment=experiment, run=str(Path(run).resolve()), calls=[]))
        elif action == "start-call":
            data["experiments"][-1]["calls"].append(start_record(name=name))
        elif action == "end-call":
            close_record(data["experiments"][-1]["calls"][-1], "passed" if code == 0 else "failed", code)
        elif action == "end-experiment":
            item = data["experiments"][-1]
            close_record(item, "failed" if code or any(c["status"] != "passed" for c in item["calls"]) else "passed", code)
        elif action == "end-suite":
            for item in data["experiments"]:
                for call in item["calls"]:
                    close_record(call, "interrupted", code)
                close_record(item, "interrupted", code)
            close_record(data, "passed" if code == 0 else "interrupted" if code >= 128 else "failed", code)
    write_json(path, data)
    for item in data["experiments"]:
        run_json = Path(item["run"]) / "run.json"
        if run_json.exists():
            meta = json.loads(run_json.read_text())
            meta["execution_record"] = str(path.resolve())
            meta["timing"] = item
            write_json(run_json, meta)


def visible_word_count(text):
    text = re.sub(r"(?m)^\s*\[[^\]]+\]:\s*\S+.*$", "", text)
    text = re.sub(r"!?\[([^\]]*)\]\([^\n]*?\)", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\[[^\]]*\]", r"\1", text)
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"<[^>]+>", "", text)
    return len(re.findall(r"\b[\w]+(?:[-'][\w]+)*\b", text))


def estimates(report):
    if not report.exists():
        return None
    words = visible_word_count(report.read_text())
    reading = words / 75 * 60
    return dict(words=words, reading_seconds=reading, analysis_seconds=2 * reading)


def minutes(seconds):
    return "unavailable" if seconds is None else f"{math.ceil(seconds / 60)} min"


def summary_lines(root, link, optional):
    records = sorted((root / "executions").glob("*/timing.json"), key=lambda p: json.loads(p.read_text())["started_at"])
    if not records:
        return []
    path = records[-1]
    data = json.loads(path.read_text())
    lines = ["## Execution and review time", "", link("Execution record", path, root) + f" · {data['status']}", "",
             "| Experiment / run | Status | Execution | Estimated reading | Estimated analysis | Estimated total |",
             "|---|---|---:|---:|---:|---:|"]
    totals = {"core": 0, "optional": 0}
    review = 0
    missing_review = False
    for item in data["experiments"]:
        run = Path(item["run"])
        duration = item.get("elapsed_seconds")
        if duration is not None:
            totals["optional" if item["experiment"] in optional else "core"] += duration
        estimate = estimates(run / "REPORT.md") if item["experiment"] not in optional else None
        complete = item["status"] == "passed"
        if estimate:
            reading, analysis = estimate["reading_seconds"], estimate["analysis_seconds"]
            review += reading + analysis
            total = minutes(duration + reading + analysis) if complete and duration is not None else "incomplete"
        else:
            reading = analysis = None
            missing_review = True
            total = "not estimated"
        lines.append("| " + " | ".join([link(item["experiment"] + " / " + run.name, run, root), item["status"], minutes(duration),
                       minutes(reading) if reading is not None else "not estimated", minutes(analysis) if analysis is not None else "not estimated", total]) + " |")
    lines += ["", f"Observed invocation time: {minutes(data.get('elapsed_seconds'))}. Core experiment time: {minutes(totals['core'])}; optional experiment time: {minutes(totals['optional'])}."]
    if data["status"] == "passed":
        lines.append(f"Estimated reading and analysis for available core reports: {minutes(review)}.")
        if not missing_review:
            lines.append(f"Estimated execution and review total: {minutes(data['elapsed_seconds'] + review)}.")
    else:
        lines.append("This invocation is incomplete or has failures; observed time is not a successful-completion estimate.")
    return lines + [""]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=["start-suite", "start-experiment", "start-call", "end-call", "end-experiment", "end-suite"])
    ap.add_argument("--record", type=Path, required=True)
    for arg in ("experiment", "run", "name", "invocation"):
        ap.add_argument("--" + arg)
    ap.add_argument("--code", type=int, default=0)
    args = vars(ap.parse_args())
    path = args.pop("record")
    action = args.pop("action")
    record_event(path, action, **args)


if __name__ == "__main__":
    main()

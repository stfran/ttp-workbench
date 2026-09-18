"""Batch driver: one upstream prediction per input, cached read-only model state.

Record errors are serialized so the adapter can retain successful outputs. The
reproduction runner checks those errors and returns a failing run status.
"""
import argparse
import json
from pathlib import Path


def process(input_dir, output_dir):
    import joblib
    import rcATT_cmd
    configuration = joblib.load("classification_tools/data/configuration.joblib")
    pipelines = (joblib.load("classification_tools/data/pipeline_tactics.joblib"),
                 joblib.load("classification_tools/data/pipeline_techniques.joblib"))
    files = sorted(Path(input_dir).glob("*.txt"))
    if not files:
        raise ValueError("No input text files")
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    for i, path in enumerate(files):
        target = output_dir / (path.stem + ".json")
        print("[rcATT] {}/{} {}".format(i + 1, len(files), path.name), flush=True)
        try:
            rcATT_cmd.predict(str(path), str(target), path.stem, "", cached_parameters=configuration, cached_pipelines=pipelines)
        except Exception as exc:
            target.write_text(json.dumps({"error": "{}: {}".format(type(exc).__name__, exc)}))

    # The upstream STIX FileSystemSink can occasionally report a successful
    # save while one destination is absent after a long batch. Retrying only
    # those records with the already-loaded models is deterministic and avoids
    # discarding the other successful predictions.
    for path in files:
        target = output_dir / (path.stem + ".json")
        if target.exists():
            continue
        print("[rcATT] retrying missing output {}".format(path.name), flush=True)
        try:
            rcATT_cmd.predict(str(path), str(target), path.stem, "", cached_parameters=configuration, cached_pipelines=pipelines)
            if not target.exists():
                # FileSystemSink cannot serialize a STIX Report with no
                # object_refs.  A clean call with no output is therefore an
                # empty prediction, not a failed inference.
                target.write_text(json.dumps({"object_refs": [], "_ttp_workbench_note": "Upstream produced no STIX report after retry; interpreted as an empty prediction."}))
        except Exception as exc:
            target.write_text(json.dumps({"error": "{}: {}".format(type(exc).__name__, exc)}))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--input_dir", required=True)
    ap.add_argument("--output_dir", required=True)
    args = ap.parse_args()
    process(args.input_dir, args.output_dir)

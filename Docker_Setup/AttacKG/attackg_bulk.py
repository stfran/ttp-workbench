#!/usr/bin/env python3
"""Batch coordinator for the pinned AttacKG code.

The extraction and matching implementations remain upstream. This coordinator
only reuses the immutable NER model and technique templates across reports.
"""
import argparse
import json
from pathlib import Path
import traceback

from main import (IoCNer, load_techniqueTemplate_fromFils, picked_techniques,
                  preprocess_file, technique_identifying)


def _safe_member(root, name):
    root = Path(root).resolve()
    target = (root / name).resolve()
    if target.parent != root:
        raise ValueError("Manifest filename must be a basename: " + str(name))
    return target


def _records(input_dir, manifest_path):
    input_dir = Path(input_dir)
    if manifest_path:
        data = json.loads(Path(manifest_path).read_text())
        if not isinstance(data, list):
            raise ValueError("Bulk manifest must contain a JSON list")
        return data
    return [{"id": path.stem, "in": path.name, "out": path.stem + ".json"}
            for path in sorted(input_dir.glob("*.txt"))]


def process(input_dir, output_dir, manifest_path=None, template_path="./templates",
            model_path="./new_cti.model"):
    input_dir, output_dir = Path(input_dir), Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    records = _records(input_dir, manifest_path)

    print("[attackg] loading NER model", flush=True)
    ner_model = IoCNer(model_path)
    print("[attackg] loading technique templates", flush=True)
    templates = load_techniqueTemplate_fromFils(template_path)
    print("[attackg] loaded model and {} templates for {} reports".format(
        len(templates), len(records)), flush=True)

    failed = 0
    for index, record in enumerate(records, 1):
        output = _safe_member(output_dir, record["out"])
        generated = output.with_name(output.stem + "_techniques.json")
        output.unlink(missing_ok=True)
        generated.unlink(missing_ok=True)
        try:
            source = _safe_member(input_dir, record["in"])
            report_text = preprocess_file(str(source))
            technique_identifying(
                report_text,
                picked_techniques,
                template_path,
                str(output.with_suffix("")),
                ner_model=ner_model,
                template_list=templates,
            )
            if not generated.is_file():
                raise RuntimeError("AttacKG did not create " + str(generated))
            generated.replace(output)
            status = "ok"
        except Exception as exc:
            failed += 1
            output.write_text(json.dumps({
                "error": "{}: {}".format(type(exc).__name__, exc),
                "id": record.get("id"),
            }, ensure_ascii=False, indent=2) + "\n")
            traceback.print_exc()
            status = "error"
        print("[attackg][progress] {}/{} {} {}".format(
            index, len(records), record.get("id", record.get("in")), status), flush=True)

    print("[attackg] completed reports={} failed={}".format(len(records), failed), flush=True)
    return failed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--manifest")
    parser.add_argument("--template-path", default="./templates")
    parser.add_argument("--model-path", default="./new_cti.model")
    args = parser.parse_args()
    process(args.input_dir, args.output_dir, args.manifest,
            args.template_path, args.model_path)


if __name__ == "__main__":
    main()

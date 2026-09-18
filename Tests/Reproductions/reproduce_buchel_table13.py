import pandas as pd
from itertools import chain
import argparse
from datetime import datetime, timezone
import json
import os
import subprocess
import sys
from pathlib import Path
from helpers.reporting import (HERE, PROJECT, EXTERNAL, check_predictions,
                               isolate_framework_cache, markdown, output_section,
                               save_scores, tool_directory)
from helpers.buchel_table13 import read_native_scores
from helpers.buchel_table13_execution import (
    HOST_ONLY_DISTRIBUTIONS,
    RUNTIME,
    adapter_for,
    stage,
    temporary_attackg_image,
)
from helpers.buchel_table13_runtime import digest

sys.path.insert(0, str(PROJECT))

# Both backends retain the released test_*_bosch.py scoring procedure.
def main():
    from helpers.reporting import install_exit_handler
    install_exit_handler()
    parser = argparse.ArgumentParser(description="Buchel Table 13: AnnoCTR external tools")
    parser.add_argument("--tool", choices=["ladder", "attackg"], default="ladder")
    parser.add_argument("--backend", choices=["original", "framework"], default="framework")
    parser.add_argument("--variant", choices=["capped"], default="capped",
                        help="Buchel's released AttacKG configuration (default: capped)")
    parser.add_argument("--root", type=Path, default=EXTERNAL / "BuchelTools/ext_tools")
    parser.add_argument("--archive", type=Path, default=EXTERNAL / "downloads/ext_tools.zip")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--data", type=Path, default=HERE / "data/buchel/bosch_test.json")
    parser.add_argument("--out-dir", type=Path, default=HERE / "experiments/buchel_table13/runs/manual/results")
    parser.add_argument("--profile", choices=["smoke", "full"], default="full")
    parser.add_argument("--engine", choices=["podman", "docker"], default="podman")
    parser.add_argument(
        "--replace-existing", action="store_true",
        help="Archive this backend/trial's existing result directory before rerunning it")
    parser.add_argument("--report-only", action="store_true")
    args = parser.parse_args()
    args.root, args.out_dir, args.data = args.root.resolve(), args.out_dir.resolve(), args.data.resolve()
    paper = {}
    for tool, values in (("ladder", [0.2403, 0.2234, 0.1807]), ("attackg", [0.4278, 0.3214, 0.2375])):
        for variant in (["capped"] if tool == "attackg" else ["standard"]):
            for scope, value in zip(["published50_archived25", "118", "open"], values):
                paper[tool + "/" + variant + " F1 " + scope] = value
    note = ("Profile: " + args.profile + ". Paper references are full-data results. "
            "Published 50-label scores match the archived 25-label rows; actual scopes are shown explicitly. "
            "Empty prediction and empty gold receive 1, as in the supplied Buchel scorer.")
    if args.report_only:
        markdown(args.out_dir, "Buchel Table 13: AnnoCTR", paper, note)
        return
    trial = args.variant if args.tool == "attackg" else "standard"
    trial_dir = tool_directory(args.out_dir, args.tool, args.backend) / trial
    if args.replace_existing and trial_dir.exists():
        archived = archive_existing_trial(trial_dir)
        print("Archived prior result attempt: " + str(archived), flush=True)
    raw_dir = trial_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    parsed_dir = trial_dir / "parsed"
    parsed_dir.mkdir(exist_ok=True)
    input_dir = args.out_dir.parent / "tmp" / args.tool / args.backend / trial
    input_dir.mkdir(parents=True, exist_ok=True)
    work = trial_dir / "execution"
    tool_dir, manifest = stage(args.archive.resolve(), args.root, work, args.data,
                               args.tool, args.variant, args.profile)
    if args.backend == "framework":
        # Match the native script's grouping; only extraction happens here.
        data = pd.read_json(args.data)
        test_docs = data.groupby('document').agg({
            'labels': lambda x: list(set(chain.from_iterable(x))),
            'sentence': lambda x: "\n".join(list(x))
        }).reset_index()
        if args.profile == "smoke": test_docs = test_docs.iloc[:1]
        ids = ["input_%04d" % i for i in range(len(test_docs))]
        isolate_framework_cache()
        if args.tool == "attackg":
            # The native call remains in Büchel's external-tools .venv. Only
            # the framework call uses this temporary derived image, whose
            # Python/dependency versions match against that native run.
            native_runtime = attackg_native_runtime(args.out_dir)
            with temporary_attackg_image(
                    args.engine, tool_dir, work, manifest, native_runtime) as (image, image_id):
                compare_attackg_environments(native_runtime, work)
                adapter = adapter_for(
                    args.tool, args.variant, tool_dir, work, manifest,
                    args.engine, input_dir, args.verbose, image=image,
                    prepared_image=True, expected_image_id=image_id)
                with output_section("FRAMEWORK ADAPTER OUTPUT",
                                    args.tool + " / framework / " + trial):
                    results, _ = adapter.predict(
                        test_docs.sentence.tolist(), ids=ids, save_dir=raw_dir)
        else:
            adapter = adapter_for(args.tool, args.variant, tool_dir, work, manifest,
                                  args.engine, input_dir, args.verbose)
            with output_section("FRAMEWORK ADAPTER OUTPUT", args.tool + " / framework / " + trial):
                results, _ = adapter.predict(test_docs.sentence.tolist(), ids=ids, save_dir=raw_dir)
        check_predictions(results, ids)
        (parsed_dir / "predictions.json").write_text(json.dumps(results, indent=2))

    native_runner = tool_dir / ("test_" + args.tool + "_bosch.py")
    command = [sys.executable, str(RUNTIME), "native", "--tool", args.tool,
               "--target", str(tool_dir), "--output", str(trial_dir)]
    if args.backend == "framework": command += ["--predictions", str(parsed_dir / "predictions.json")]
    kind = "RAW TOOL OUTPUT" if args.backend == "original" else "EXPERIMENT RUNNER OUTPUT"
    with output_section(kind, native_runner.name + " / " + args.backend + " / " + trial):
        subprocess.run(command, cwd=tool_dir, check=True,
                       env=dict(os.environ, HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1"))
    if digest(native_runner) != manifest["native_runner_sha256"]:
        raise RuntimeError("Released native runner changed during execution: " + str(native_runner))
    source_manifest = work / "source_manifest.json"
    recorded = json.loads(source_manifest.read_text())
    recorded["native_runner_verified_after"] = True
    source_manifest.write_text(json.dumps(recorded, indent=2))
    print("=== EXPERIMENT RUNNER OUTPUT | Buchel Table 13: native scores ===", flush=True)
    scores = read_native_scores(trial_dir / "bosch_scores.txt", args.tool, trial)
    save_scores(args.out_dir, args.backend, args.tool + "_" + trial, scores, tool=args.tool)
    markdown(args.out_dir, "Buchel Table 13: AnnoCTR", paper, note)


def _package_key(name):
    return name.strip().lower().replace("_", "-")


def archive_existing_trial(trial_dir):
    attempts = trial_dir.parent / "failed_attempts"
    attempts.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    archived = attempts / (trial_dir.name + "." + stamp)
    trial_dir.rename(archived)
    return archived


def attackg_native_runtime(out_dir):
    native_runtime = tool_directory(out_dir, "attackg", "original") / "capped/runtime.json"
    if not native_runtime.exists():
        raise FileNotFoundError(
            "Run native Büchel AttacKG before its framework call: " + str(native_runtime))
    return native_runtime


def compare_attackg_environments(native_runtime, framework_work):
    """Require the framework image to match Büchel's native Python/package stack."""
    image_runtime = framework_work / "container_changes.json"
    native = json.loads(native_runtime.read_text())
    image = json.loads(image_runtime.read_text())["runtime"]
    native_packages = {_package_key(key): value for key, value in native["packages"].items()}
    image_packages = {_package_key(key): value for key, value in image["packages"].items()}
    required = sorted((native_packages.keys() | image_packages.keys()) -
                      HOST_ONLY_DISTRIBUTIONS)
    versions = {
        name: {"native": native_packages.get(name), "framework": image_packages.get(name)}
        for name in required
    }
    mismatches = {name: value for name, value in versions.items()
                  if value["native"] != value["framework"]}
    comparison = {
        "native_runtime": str(native_runtime),
        "framework_runtime": str(image_runtime),
        "native_python": native["python"],
        "framework_python": image["python"],
        "required_package_versions": versions,
        "ignored_host_only_distributions": sorted(HOST_ONLY_DISTRIBUTIONS),
        "mismatches": mismatches,
    }
    python_matches = native["python"].split(".")[:2] == image["python"].split(".")[:2]
    comparison["python_major_minor_matches"] = python_matches
    (framework_work / "environment_comparison.json").write_text(
        json.dumps(comparison, indent=2) + "\n")
    if not python_matches or mismatches:
        raise RuntimeError(
            "Temporary AttacKG image does not match Büchel's native Python/package stack; "
            "see " + str(framework_work / "environment_comparison.json"))

if __name__ == "__main__":
    main()

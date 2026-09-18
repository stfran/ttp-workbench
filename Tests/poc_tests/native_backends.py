#!/usr/bin/env python3
"""Run one PoC corpus directly in a staged native tool environment.

The parent smoke runner launches this file with the selected tool's own Python
interpreter.  It deliberately does not import Framework adapters.
"""
from __future__ import print_function

import argparse
import ast
from contextlib import contextmanager
import csv
import hashlib
import json
import os
from pathlib import Path
import re
import runpy
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request


TACTIC_TO_TA = {
    "reconnaissance": "TA0043", "resource development": "TA0042",
    "initial access": "TA0001", "execution": "TA0002",
    "persistence": "TA0003", "privilege escalation": "TA0004",
    "defense evasion": "TA0005", "credential access": "TA0006",
    "discovery": "TA0007", "lateral movement": "TA0008",
    "collection": "TA0009", "exfiltration": "TA0010",
    "command and control": "TA0011", "impact": "TA0040",
}
TECH_RE = re.compile(r"\bT\d{4}(?:\.\d{3})?\b", re.I)
CODE_RE = re.compile(r"^(?:TA\d{4}|T\d{4}(?:\.\d{3})?)$", re.I)


def codes(values):
    found = set()

    def visit(value):
        if isinstance(value, str):
            value = value.strip().upper()
            if CODE_RE.match(value):
                found.add(value)
        elif isinstance(value, dict):
            for key in ("code", "technique_id", "techID", "techId", "ttp", "id"):
                if key in value:
                    visit(value[key])
            for key in ("ttps", "predictions"):
                if key in value:
                    visit(value[key])
        elif isinstance(value, (list, tuple, set)):
            for item in value:
                visit(item)

    visit(values)
    return sorted(found)


def load_records(manifest):
    payload = json.loads(Path(manifest).read_text(encoding="utf-8"))
    records = []
    for item in payload:
        records.append({
            "id": str(item["id"]),
            "text": Path(item["text_file"]).read_text(encoding="utf-8", errors="replace"),
        })
    return records


def write_json(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def run(command, cwd=None, env=None, capture=False):
    print("+", " ".join(str(value) for value in command), flush=True)
    completed = subprocess.run(
        [str(value) for value in command], cwd=str(cwd) if cwd else None,
        env=env, text=True, capture_output=capture,
    )
    if completed.returncode:
        detail = (completed.stderr or completed.stdout or "")[-12000:] if capture else ""
        raise RuntimeError("command exited {}{}".format(
            completed.returncode, ": " + detail if detail else ""))
    return completed


@contextmanager
def preserve_file(path, replacement=None):
    path = Path(path)
    existed = path.exists()
    old = path.read_bytes() if existed else None
    old_mode = path.stat().st_mode if existed else None
    if replacement is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(replacement, bytes):
            path.write_bytes(replacement)
        else:
            path.write_text(str(replacement), encoding="utf-8")
    try:
        yield path
    finally:
        if existed:
            path.write_bytes(old)
            os.chmod(str(path), old_mode)
        elif path.exists():
            path.unlink()


def run_optional_helper(project, name, root, records, raw_dir, **kwargs):
    sys.path.insert(0, str(project / "Tests/Reproductions"))
    from helpers import optional_native
    getattr(optional_native, name)(root, records, raw_dir, **kwargs)


def attackg(project, root, records, raw_dir, args):
    run_optional_helper(project, "attackg", root, records, raw_dir, batch_size=len(records))
    results = []
    for record in records:
        payload = json.loads((raw_dir / (record["id"] + ".json")).read_text())
        if not isinstance(payload, dict):
            raise ValueError("AttacKG output is not an object for " + record["id"])
        # AttacKG's technique identifiers are the top-level object keys.
        result = dict(id=record["id"], ttps=codes(list(payload.keys())))
        if isinstance(payload, dict) and payload.get("error"):
            result["error"] = payload["error"]
        results.append(result)
    return results


def ladder(project, root, records, raw_dir, args):
    with tempfile.TemporaryDirectory(prefix="ladder-native-") as temp:
        input_dir = Path(temp) / "input"
        input_dir.mkdir()
        for record in records:
            (input_dir / (record["id"] + ".txt")).write_text(record["text"], encoding="utf-8")
        run([sys.executable, root / "attack_pattern/ladder_attack_pattern_cli.py",
             input_dir, "--output", raw_dir], cwd=root / "attack_pattern")
    results = []
    for record in records:
        path = raw_dir / (record["id"] + ".json")
        payload = json.loads(path.read_text())
        results.append(dict(id=record["id"], ttps=codes(list(payload.keys()))))
    return results


def _corenlp_up(port):
    try:
        urllib.request.urlopen("http://127.0.0.1:{}".format(port), timeout=1).close()
        return True
    except Exception:
        return False


def _start_corenlp(home, port):
    if _corenlp_up(port):
        return None
    log = open(os.devnull, "wb")
    process = subprocess.Popen([
        "java", "-mx4g", "-cp", "*",
        "edu.stanford.nlp.pipeline.StanfordCoreNLPServer",
        "-port", str(port), "-timeout", "15000", "-threads", "4",
    ], cwd=str(home), stdout=log, stderr=subprocess.STDOUT)
    process._ttpwb_log = log
    for _ in range(120):
        if _corenlp_up(port):
            return process
        if process.poll() is not None:
            break
        time.sleep(1)
    process.terminate()
    log.close()
    raise RuntimeError("CoreNLP did not become ready on port {}".format(port))


def _stop_corenlp(process):
    if process is None:
        return
    try:
        process.terminate()
        process.wait(timeout=10)
    except Exception:
        process.kill()
    finally:
        process._ttpwb_log.close()


def _parse_ttpdrill(text):
    name_to_code = {
        "Credential Access": "TA0006", "Execution": "TA0002", "Impact": "TA0040",
        "Persistence": "TA0003", "Privilege Escalation": "TA0004",
        "Lateral Movement": "TA0008", "Defense Evasion": "TA0005",
        "Exfiltration": "TA0010", "Discovery": "TA0007", "Collection": "TA0009",
        "Command and Control": "TA0011", "Initial Access": "TA0001",
    }
    found = set()
    pattern = re.compile(r"Mapped:\s*?\n(?:\s*\n)*?(\[.*?\])(?=\s*(?:Text:|\Z))", re.S)
    for match in pattern.finditer(text):
        try:
            block = ast.literal_eval(match.group(1).strip())
        except Exception:
            continue
        for item in block if isinstance(block, list) else []:
            technique = (item.get("techId", {}) or {}).get("data")
            tactic = (item.get("tactic", {}) or {}).get("data")
            if technique:
                found.add(str(technique).upper())
            if tactic in name_to_code:
                found.add(name_to_code[tactic])
    return sorted(found)


def ttpdrill(project, root, records, raw_dir, args):
    server = _start_corenlp(root / "stanford-corenlp-full-2018-10-05", args.corenlp_port)
    results = []
    input_path = root / "input.txt"
    try:
        with preserve_file(input_path):
            for record in records:
                input_path.write_text(record["text"], encoding="utf-8")
                completed = run([sys.executable, "main.py"], cwd=root, capture=True)
                raw = completed.stdout + "\n" + completed.stderr
                (raw_dir / (record["id"] + ".txt")).write_text(raw, encoding="utf-8")
                results.append(dict(id=record["id"], ttps=_parse_ttpdrill(completed.stdout)))
    finally:
        _stop_corenlp(server)
    return results


def seqmask(project, root, records, raw_dir, args):
    with tempfile.TemporaryDirectory(prefix="seqmask-native-") as temp:
        input_dir = Path(temp)
        for record in records:
            (input_dir / (record["id"] + ".txt")).write_text(record["text"], encoding="utf-8")
        run([sys.executable, "-u", root / "seqmask_cli.py",
             "--input_dir", input_dir, "--output_dir", raw_dir,
             "--mode", "workbench", "--top_k", "3", "--tau", "0.5",
             "--tact_model", "ar_mask", "--tech_model", "ar_mask"], cwd=root)
    results = []
    for record in records:
        payload = json.loads((raw_dir / (record["id"] + ".json")).read_text())
        predicted = list(payload.get("accepted_tactics") or []) + list(payload.get("accepted_techniques") or [])
        results.append(dict(id=record["id"], ttps=codes(predicted), **({"error": payload["error"]} if payload.get("error") else {})))
    return results


def rcatt(project, root, records, raw_dir, args):
    run_optional_helper(project, "rcatt", root, records, raw_dir, batch_size=len(records))
    mapping = runpy.run_path(str(project / "Framework/utils/rcatt_ttp_map.py"))
    ref_to_code = dict(zip(mapping["STIX_IDENTIFIERS"], mapping["ALL_TTPS"]))
    results = []
    for record in records:
        payload = json.loads((raw_dir / (record["id"] + ".json")).read_text())
        predicted = sorted(set(ref_to_code[ref] for ref in payload.get("object_refs", []) if ref in ref_to_code))
        results.append(dict(id=record["id"], ttps=predicted, **({"error": payload["error"]} if payload.get("error") else {})))
    return results


def rafag(project, root, records, raw_dir, args):
    env_names = ("CUDA_VISIBLE_DEVICES", "PATH", "XLA_FLAGS", "PYTHONHASHSEED", "TOKENIZERS_PARALLELISM")
    previous_env = {name: os.environ.get(name) for name in env_names}
    nvcc_root = Path(sys.prefix) / "lib/python3.9/site-packages/nvidia/cuda_nvcc"
    ptxas = nvcc_root / "bin/ptxas"
    libdevice = nvcc_root / "nvvm/libdevice/libdevice.10.bc"
    if not ptxas.is_file() or not libdevice.is_file():
        raise RuntimeError(
            "RAF-AG native CUDA support is incomplete; rerun its native setup "
            "to install nvidia-cuda-nvcc-cu12==12.8.93"
        )
    os.environ["PATH"] = str(nvcc_root / "bin") + os.pathsep + os.environ.get("PATH", "")
    os.environ["XLA_FLAGS"] = "--xla_gpu_cuda_data_dir=" + str(nvcc_root)
    os.environ["PYTHONHASHSEED"] = "0"
    os.environ["TOKENIZERS_PARALLELISM"] = "false"
    if args.device == "cpu":
        os.environ["CUDA_VISIBLE_DEVICES"] = "-1"
    try:
        run_optional_helper(project, "rafag", root, records, raw_dir, batch_size=len(records))
    finally:
        for name, value in previous_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
    results = []
    for record in records:
        payload = json.loads((raw_dir / (record["id"] + ".json")).read_text())
        predicted = []
        for entries in (payload.get("best") or {}).values():
            for item in entries if isinstance(entries, list) else []:
                predicted.append(item.get("techID") or item.get("techId"))
        results.append(dict(id=record["id"], ttps=codes(predicted), **({"error": payload["error"]} if isinstance(payload, dict) and payload.get("error") else {})))
    return results


def orbinato(project, root, records, raw_dir, args):
    # The maintained CLI uses absolute /opt/Orbinato paths because it was
    # originally container-only. Execute the same source with only those path
    # expressions made relative to its real staged location. The upstream
    # checkout and model files remain untouched.
    portable_cli = r'''
import os, sys
from pathlib import Path
path = Path(os.environ["TTPWB_ORBINATO_CLI"])
source = path.read_text(encoding="utf-8")
source = source.replace('Path("/opt/Orbinato/src")', 'Path(__file__).resolve().parent')
source = source.replace("pd.read_csv('/opt/Orbinato/data/dataset.csv')", "pd.read_csv(str(Path(__file__).resolve().parent.parent / 'data/dataset.csv'))")
namespace = {"__name__": "__main__", "__file__": str(path)}
exec(compile(source, str(path), "exec"), namespace, namespace)
'''
    results = []
    for record in records:
        with tempfile.NamedTemporaryFile("w", suffix=".txt", encoding="utf-8", delete=False) as handle:
            handle.write(record["text"])
            input_path = Path(handle.name)
        output = raw_dir / (record["id"] + ".json")
        try:
            environment = dict(os.environ)
            environment["TTPWB_ORBINATO_CLI"] = str(root / "src/orbinato_cli.py")
            run([sys.executable, "-u", "-c", portable_cli,
                 "--in", input_path, "--out", output, "--models", "MLP",
                 "--secbert-maxlen", "512", "--batch-size", "32",
                 "--device", args.device], cwd=root / "src", env=environment)
        finally:
            input_path.unlink()
        payload = json.loads(output.read_text())
        if payload.get("error"):
            results.append(dict(id=record["id"], ttps=[], error=payload["error"]))
            continue
        if not isinstance(payload.get("model_results"), dict) or not payload["model_results"]:
            raise ValueError("Orbinato output has no model_results for " + record["id"])
        model = next(iter(payload.get("model_results", {}).values()), {})
        predicted = [item.get("label") for item in model.get("ttps", []) if float(item.get("prob", 0)) > 0.2]
        results.append(dict(id=record["id"], ttps=codes(predicted)))
    return results


def tram(project, root, records, raw_dir, args):
    with tempfile.TemporaryDirectory(prefix="tram-native-") as temp:
        input_dir = Path(temp)
        for record in records:
            (input_dir / (record["id"] + ".txt")).write_text(record["text"], encoding="utf-8")
        env = dict(os.environ)
        if args.device == "cpu":
            env["CUDA_VISIBLE_DEVICES"] = "-1"
        run([sys.executable, "-u", root / "predict_multi_label.py",
             "--dirin", input_dir, "--dirout", raw_dir,
             "--n", "13", "--stride", "5", "--thr", "0.5"], cwd=root, env=env)
    results = []
    for record in records:
        path = raw_dir / (record["id"] + "_predictions.json")
        if not path.exists():
            path = raw_dir / (record["id"] + ".json")
        payload = json.loads(path.read_text())
        predicted = [entry.get("code") for segment in payload for entry in segment.get("predictions", [])]
        results.append(dict(id=record["id"], ttps=codes(predicted)))
    return results


def buchel(project, root, records, raw_dir, args):
    generation = root / "generation"
    with tempfile.TemporaryDirectory(prefix="buchel-native-") as temp:
        input_dir = Path(temp)
        for record in records:
            (input_dir / (record["id"] + ".txt")).write_text(record["text"], encoding="utf-8")
        command = [sys.executable, "-u", generation / "buchel_cli.py",
                   "--indir", input_dir, "--outdir", raw_dir,
                   "--base_model", "unsloth/Meta-Llama-3.1-8B-Instruct",
                   "--prefer_merged"]
        if args.buchel_quant_4bit:
            command.append("--quant_4bit_model")
        run(command, cwd=generation)
    results = []
    for record in records:
        payload = json.loads((raw_dir / (record["id"] + ".json")).read_text())
        predicted = [item.get("code") for result in payload for item in result.get("predictions", [])]
        results.append(dict(id=record["id"], ttps=codes(predicted)))
    return results


def _truthy(value):
    return str(value).strip().lower() in ("1", "true", "yes", "y")


def _ttpllm_codes(row):
    result = []
    for name, code in TACTIC_TO_TA.items():
        if name in row and _truthy(row[name]):
            result.append(code)
    for key in ("Predicted_Tactic", "Predicted_Tactics", "tactic_keywords", "prediction", "response"):
        value = row.get(key)
        if not isinstance(value, str):
            continue
        result.extend(match.group(0).upper() for match in TECH_RE.finditer(value))
        lower = value.lower()
        result.extend(code for name, code in TACTIC_TO_TA.items() if name in lower)
    return codes(result)


def ttpllm(project, root, records, raw_dir, args):
    config = Path(args.config).resolve()
    if not config.is_file():
        raise FileNotFoundError("TTP-LLM config not found: " + str(config))
    dataset = root / "data/MITRE_Procedures.csv"
    tool_config = root / "config.ini"
    raw_csv = root / "results/preds_gpt-3.5-turbo_prompt_only.csv"
    encoded_csv = root / "results/preds_gpt-3.5-turbo_prompt_only_encoded.csv"
    root.joinpath("results").mkdir(exist_ok=True)
    results = []
    with preserve_file(dataset), preserve_file(tool_config, config.read_bytes()), preserve_file(raw_csv), preserve_file(encoded_csv):
        os.chmod(str(tool_config), 0o600)
        for record in records:
            # Upstream main.py intentionally reads only its first procedure.
            # Preserve that released one-row workflow and invoke it once for
            # each E1 report, just as the framework's non-bulk smoke path does.
            with tempfile.NamedTemporaryFile("w", newline="", encoding="utf-8", delete=False) as handle:
                writer = csv.writer(handle, quoting=csv.QUOTE_ALL)
                writer.writerow(["Procedures"])
                writer.writerow([record["text"]])
                prepared_path = Path(handle.name)
            dataset.write_bytes(prepared_path.read_bytes())
            prepared_path.unlink()
            if raw_csv.exists():
                raw_csv.unlink()
            if encoded_csv.exists():
                encoded_csv.unlink()
            run([sys.executable, "-u", "main.py", "--type", "decoder_only",
                 "--mode", "prompt_only", "--llm", "gpt-3.5-turbo"], cwd=root)
            shutil.copy2(str(raw_csv), str(raw_dir / (record["id"] + "_predictions.csv")))
            run([sys.executable, "decoder_only/postprocess.py", "--file_path", raw_csv], cwd=root)
            shutil.copy2(str(encoded_csv), str(raw_dir / (record["id"] + "_encoded.csv")))
            import pandas as pd
            rows = pd.read_csv(encoded_csv).to_dict(orient="records")
            if len(rows) != 1:
                raise RuntimeError("TTP-LLM returned {} rows for report {}".format(len(rows), record["id"]))
            results.append(dict(id=record["id"], ttps=_ttpllm_codes(rows[0])))
    return results


RUNNERS = {
    "AttacKG": attackg, "LADDER": ladder, "TTPDrill": ttpdrill,
    "SeqMask": seqmask, "rcATT": rcatt, "RAF-AG": rafag,
    "Orbinato": orbinato, "TRAM": tram, "Buchel": buchel,
    "TTP-LLM": ttpllm,
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tool", required=True, choices=sorted(RUNNERS))
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--native-root", type=Path, required=True)
    parser.add_argument("--device", choices=["cpu", "cuda"], default="cpu")
    parser.add_argument("--config", type=Path, default=Path("config.ini"))
    parser.add_argument("--corenlp-port", type=int, default=9000)
    parser.add_argument("--buchel-quant-4bit", action="store_true")
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    records = load_records(args.manifest)
    started = time.time()
    failure = None
    try:
        results = RUNNERS[args.tool](args.project_root.resolve(), args.native_root.resolve(), records, args.output_dir, args)
    except Exception as exc:
        failure = "{}: {}".format(type(exc).__name__, exc)
        results = [dict(id=record["id"], ttps=[], error=failure) for record in records]
    payload = {
        "tool": args.tool,
        "backend": "native",
        "python": sys.executable,
        "python_version": sys.version,
        "native_root": str(args.native_root.resolve()),
        "elapsed_seconds": time.time() - started,
        "failure": failure,
        "results": results,
    }
    revision = subprocess.run(
        ["git", "-C", str(args.native_root), "rev-parse", "HEAD"],
        text=True, capture_output=True,
    )
    payload["source_revision"] = revision.stdout.strip() if revision.returncode == 0 else None
    frozen = subprocess.run(
        [sys.executable, "-m", "pip", "freeze"], text=True, capture_output=True,
    )
    payload["environment_sha256"] = (
        hashlib.sha256(frozen.stdout.encode("utf-8")).hexdigest()
        if frozen.returncode == 0 else None
    )
    runtime_hashes = args.native_root / "AE_RUNTIME_SHA256"
    payload["runtime_asset_hashes"] = (
        runtime_hashes.read_text(encoding="utf-8").splitlines()
        if runtime_hashes.is_file() else []
    )
    write_json(args.result, payload)
    return 1 if failure else 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Direct upstream execution; this module never imports framework adapters."""
import ast
from contextlib import contextmanager
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


@contextmanager
def working_directory(path):
    old = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(old)


def seqmask(root, records, output):
    sys.path.insert(0, str(root))
    from TextClassify import TextDeal
    with working_directory(root):
        model = TextDeal(tact_model_path="./models/tactics_ar_mask_model", tech_model_path="./models/techniques_ar_mask_model")
        for i, record in enumerate(records):
            print("[SeqMask] {}/{} {}".format(i + 1, len(records), record["id"]), flush=True)
            try:
                raw = model.classify_text(record["text"])
                raw.pop("embedding", None)
            except Exception as exc:
                raw = {"error": "{}: {}".format(type(exc).__name__, exc)}
            (output / (record["id"] + ".json")).write_text(json.dumps(raw))


def rcatt(root, records, output, batch_size):
    sys.path.insert(0, str(root))
    from rcatt_bulk import process
    with working_directory(root):
        for start in range(0, len(records), batch_size):
            with tempfile.TemporaryDirectory(prefix="rcatt-input-", dir=str(output.parent)) as tmp:
                for record in records[start:start + batch_size]:
                    # Same UTF-8 bytes as the adapter staging path; upstream
                    # intentionally retains its ISO-8859-1 decoding behavior.
                    (Path(tmp) / (record["id"] + ".txt")).write_text(record["text"], encoding="utf-8")
                process(tmp, output)
            # rcATT's STIX FileSystemSink has repeatedly reported success for
            # one item in a long batch without leaving the requested output.
            # Retry only missing items after the batch temporary directory has
            # been closed; isolated execution of the affected record succeeds.
            for record in records[start:start + batch_size]:
                target = output / (record["id"] + ".json")
                if target.exists():
                    payload = json.loads(target.read_text())
                    if "Upstream did not save a prediction" in payload.get("error", ""):
                        # rcATT's STIX sink cannot serialize a Report with no
                        # object_refs.  The upstream call did complete, so this
                        # means an empty prediction rather than a failed record.
                        target.write_text(json.dumps({"object_refs": [], "_ttp_workbench_note": "Upstream produced no STIX report; interpreted as an empty prediction."}))
                    continue
                print("[rcATT] retrying missing batch output {}".format(record["id"]), flush=True)
                with tempfile.TemporaryDirectory(prefix="rcatt-retry-", dir=str(output.parent)) as tmp:
                    (Path(tmp) / (record["id"] + ".txt")).write_text(record["text"], encoding="utf-8")
                    process(tmp, output)
                if not target.exists():
                    target.write_text(json.dumps({"object_refs": [], "_ttp_workbench_note": "Upstream produced no STIX report after retry; interpreted as an empty prediction."}))
                else:
                    payload = json.loads(target.read_text())
                    if "Upstream did not save a prediction" in payload.get("error", ""):
                        target.write_text(json.dumps({"object_refs": [], "_ttp_workbench_note": "Upstream produced no STIX report after retry; interpreted as an empty prediction."}))


def attackg(root, records, output, batch_size):
    for start in range(0, len(records), batch_size):
        batch = records[start:start + batch_size]
        with tempfile.TemporaryDirectory(prefix="attackg-input-", dir=str(output.parent)) as tmp:
            input_dir = Path(tmp)
            manifest = []
            for i, record in enumerate(batch):
                name = "{:06d}__{}.txt".format(i, record["id"])
                (input_dir / name).write_text(record["text"])
                manifest.append({"id": record["id"], "in": name,
                                 "out": record["id"] + ".json"})
            manifest_path = input_dir / "manifest.json"
            manifest_path.write_text(json.dumps(manifest, indent=2))
            code = subprocess.call([
                sys.executable, "-u", "attackg_bulk.py",
                "--input-dir", str(input_dir),
                "--output-dir", str(output),
                "--manifest", str(manifest_path),
                "--template-path", "./templates",
                "--model-path", "./new_cti.model",
            ], cwd=str(root))
            if code:
                for record in batch:
                    target = output / (record["id"] + ".json")
                    if not target.exists():
                        target.write_text(json.dumps({
                            "error": "AttacKG bulk process exited {} before producing this output".format(code)
                        }))


def rafag(root, records, output, batch_size):
    for start in range(0, len(records), batch_size):
        batch = records[start:start + batch_size]
        with tempfile.TemporaryDirectory(prefix="rafag-work-", dir=str(output.parent)) as tmp:
            work = Path(tmp)
            for source in root.iterdir():
                if source.name not in ("data", ".git", ".venv", ".python", "__pycache__"):
                    (work / source.name).symlink_to(source, target_is_directory=source.is_dir())
            (work / "data").mkdir()
            for source in (root / "data").iterdir():
                if source.name != "campaign":
                    (work / "data" / source.name).symlink_to(source, target_is_directory=source.is_dir())
            campaign = work / "data/campaign"
            for name in ("input", "output", "decoding_result", "procedure_alignment", "sequence_techniques", "tech_alignment", "images", "USE_cosine", "pdfs"):
                (campaign / name).mkdir(parents=True, exist_ok=True)
            for record in batch:
                (campaign / "input" / (record["id"] + ".txt")).write_text(record["text"])
            code = subprocess.call([sys.executable, "-u", "main.py"], cwd=str(work))
            for record in batch:
                target = output / (record["id"] + ".json")
                source = campaign / "decoding_result" / target.name
                if code == 0 and source.exists():
                    target.write_bytes(source.read_bytes())
                else:
                    target.write_text(json.dumps({"error": "RAF-AG exit {}; decoded output exists={}".format(code, source.exists())}))
            # Retain graph/path artifacts alongside native decoded predictions.
            import shutil
            shutil.copytree(campaign, output / ("campaign_batch_{:06d}".format(start)), ignore=shutil.ignore_patterns("input"))


ORBINATO_THRESHOLDS = tuple(round(i / 10, 1) for i in range(1, 9))


def _orbinato_model_name(title):
    normalized = str(title).strip().lower().replace("-", "_").replace(" ", "_")
    if "secbert" in normalized:
        return "SecBERT"
    if "mlp" in normalized:
        return "MLP"
    if "logreg" in normalized:
        return "Logreg"
    if "multinomial" in normalized:
        return "Multinomial_NB"
    if "svm" in normalized and "ovr" in normalized:
        return "SVM_OVR"
    if "pretrained" in normalized or normalized.startswith("pre_lstm"):
        return "PRETRAINED_LSTM"
    if normalized.startswith("cnn"):
        return "CNN"
    if normalized.startswith("lstm"):
        return "LSTM"
    raise RuntimeError("Unrecognized Orbinato classifier title: {}".format(title))


def _orbinato_fraction(value):
    numerator, denominator = str(value).split("/", 1)
    return int(numerator), int(denominator)


def _orbinato_threshold_results(result):
    values = {}
    for index, threshold in enumerate(ORBINATO_THRESHOLDS):
        tp, unique_predictions = _orbinato_fraction(result.correct_uniques[index])
        recalled, ground_truth = _orbinato_fraction(result.recalls[index])
        if recalled != tp:
            raise RuntimeError("Inconsistent Orbinato true-positive counts for {}".format(result.title))
        values["{:.1f}".format(threshold)] = dict(
            tp=tp,
            fp=unique_predictions - tp,
            fn=ground_truth - tp,
            precision=tp / unique_predictions if unique_predictions else 0,
            recall=tp / ground_truth if ground_truth else 0,
            f1=float(result.f1s[index]),
            accepted=int(result.accepted_preds[index]),
            correct=int(result.correct_preds[index]),
        )
    return values


def orbinato(root, records, output, device="cpu"):
    """Run the seven released classifiers and the notebook SecBERT workflow.

    The training and data-loading notebook cells are not executed.  Each
    record retains the upstream threshold calculations for all eight Figure 3
    classifiers, while SecBERT also retains sentence probabilities.
    """
    import numpy as np
    import pandas as pd
    import torch
    from torch.utils.data import Dataset, DataLoader
    from transformers import AutoTokenizer, AutoModel, BertConfig
    from nltk.tokenize import sent_tokenize
    sys.path.insert(0, str(root / "src"))
    import document_analysis_import as preprocessing
    notebook = root / "Colab_notebook/trained_secBert.ipynb"
    cells = json.loads(notebook.read_text())["cells"]
    requested = {"Triage", "SecBERTClass", "f_measure", "Classifier_results", "CSVOutput"}
    definitions = []
    for cell in cells:
        if cell.get("cell_type") != "code":
            continue
        source = "".join(cell.get("source", []))
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name in requested:
                definitions.append(node)
    if {n.name for n in definitions} != requested:
        raise RuntimeError("Unexpected upstream notebook; required inference definitions missing")
    # The released notebook uses top-level cells, not a callable runner.
    # Wrap just its report preparation, inference and scoring cells. Training,
    # dataset download and the hard-coded example report cells are not executed.
    # We do this to explicitly follow the released notebook's logic
    body = []
    anchors = {37: "#Read report", 39: "predicted =", 40: "predicted =", 41: "predict_proba_scores =",
               43: "predicted =", 45: "thresholds =", 46: "for threshold", 48: "result ="}
    for index, anchor in anchors.items():
        source = "".join(cells[index]["source"])
        if not source.startswith(anchor):
            raise RuntimeError("Upstream inference cell changed: {}".format(index))
        if index == 45:
            body.extend(ast.parse("global _ae_last_raw\n_ae_last_raw = {'model_results': {'SecBERT': {'sentences': [{'text': s, 'ttps': [{'label': str(p), 'prob': float(v)}]} for s, p, v in zip(sentences, predicted, predict_proba_scores)]}}}").body)
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and node.value == "regex.yml":
                node.value = str(root / "src/utils/regex.yml")
        body.extend(tree.body)
    body.append(ast.Return(value=ast.Name(id="result", ctx=ast.Load())))
    wrapper = ast.parse("def run_classifier_on_file(file_name, techniques):\n    pass").body[0]
    wrapper.body = body
    definitions.append(wrapper)
    labels = [s.strip() for s in (root / "src/secbert_labels.txt").read_text().splitlines() if s.strip()]
    class LabelOrder:
        def inverse_transform(self, indices):
            return [labels[i] for i in indices]
    actual_device = device
    if device == "cuda" and not torch.cuda.is_available():
        # A pre-existing venv can contain a CUDA runtime newer than the host
        # driver.  Orbinato supports CPU inference, so keep the run usable and
        # record the fallback in the native log rather than crashing at .to().
        print("[Orbinato] requested CUDA is unavailable; falling back to CPU", flush=True)
        actual_device = "cpu"
    ns = dict(torch=torch, np=np, pd=pd, Dataset=Dataset, DataLoader=DataLoader, AutoModel=AutoModel,
              BertConfig=BertConfig, sent_tokenize=sent_tokenize, MAX_LEN=512,
              test_params=dict(batch_size=32, shuffle=False, num_workers=0), device=actual_device, encoder=LabelOrder())
    for name in ("load_regex", "combine_text", "repl", "apply_regex_to_string", "remove_empty_lines"):
        ns[name] = getattr(preprocessing, name)
    exec(compile(ast.fix_missing_locations(ast.Module(body=definitions, type_ignores=[])), str(notebook), "exec"), ns)
    ns["tokenizer"] = AutoTokenizer.from_pretrained("jackaduma/SecBERT", use_fast=True)
    model = ns["SecBERTClass"]("jackaduma/SecBERT", len(labels))
    model.load_state_dict(torch.load(root / "src/secbert_model/trained_secbert.pt", map_location="cpu"))
    ns["model"] = model.to(actual_device)
    model.eval()
    for record in records:
        print("[Orbinato] {}".format(record["id"]), flush=True)
        with tempfile.TemporaryDirectory(prefix="orbinato-input-", dir=str(output.parent)) as tmp:
            text = Path(tmp) / (record["id"] + ".txt")
            text.write_text(record["text"])
            try:
                with working_directory(root / "src"):
                    results = preprocessing.analyze_all_doc(
                        str(text), preprocessing.ml_model_filenames, record["labels"])
                with working_directory(root / "Colab_notebook"):
                    secbert = ns["run_classifier_on_file"](str(text), record["labels"])
                results.append(secbert)
                model_results = {}
                for result in results:
                    model_name = _orbinato_model_name(result.title)
                    if model_name in model_results:
                        raise RuntimeError("Duplicate Orbinato classifier output: {}".format(model_name))
                    model_results[model_name] = dict(
                        source_title=str(result.title),
                        sentences=int(result.lines),
                        threshold_results=_orbinato_threshold_results(result),
                    )
                secbert_raw = ns["_ae_last_raw"]["model_results"]["SecBERT"]
                model_results["SecBERT"].update(secbert_raw)
                raw = {"model_results": model_results}
                # Retain the released human-readable CSV and F1 evidence too.
                ns["CSVOutput"](record["id"], results).write_to_file(str(output))
            except Exception as exc:
                raw = {"error": "{}: {}".format(type(exc).__name__, exc)}
            (output / (record["id"] + ".json")).write_text(json.dumps(raw))

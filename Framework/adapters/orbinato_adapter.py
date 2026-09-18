# adapters/orbinato_adapter.py
# TO DO, the train and commit logic create dangling images
# that we need to figure out how to clean that up.
# At most we expect 5 dangling images and about 80MB of extra overhead
from __future__ import annotations
from typing import List, Dict, Any, Tuple
from pathlib import Path
import subprocess, uuid, sys, json

from pathlib import Path

from Framework.adapters.base_adapter import BaseAdapter
from Framework.adapters.docker_session import _gpus_supported, _gpu_args

# ---------- Model inventory & training commands (inside container paths) ----------
# NOTE: classic ML models are all produced by ml_classifier.py in one go.
_MODEL_SPEC = {
    # classical ML (all trained by ml_classifier.py)
    "MLP":               (["/opt/Orbinato/src/ml_models/MLP classifier .sav"],         ["python","-u","/opt/Orbinato/src/ml_classifier.py"]),
    "Logreg":            (["/opt/Orbinato/src/ml_models/Logreg.sav"],                  ["python","-u","/opt/Orbinato/src/ml_classifier.py"]),
    "Logreg_normale":    (["/opt/Orbinato/src/ml_models/Logreg_normale.sav"],          ["python","-u","/opt/Orbinato/src/ml_classifier.py"]),
    "Multinomial_NB":    (["/opt/Orbinato/src/ml_models/Multinomial_NB.sav"],          ["python","-u","/opt/Orbinato/src/ml_classifier.py"]),
    "Complement_NB":     (["/opt/Orbinato/src/ml_models/Complement_NB.sav"],           ["python","-u","/opt/Orbinato/src/ml_classifier.py"]),
    "SVM_OVR":           (["/opt/Orbinato/src/ml_models/SVM_Classifier_OVR.sav"],      ["python","-u","/opt/Orbinato/src/ml_classifier.py"]),
    "SVM_OVO":           (["/opt/Orbinato/src/ml_models/SVM_Classifier_OVO.sav"],      ["python","-u","/opt/Orbinato/src/ml_classifier.py"]),

    # deep learning
    "CNN":               (["/opt/Orbinato/src/cnn_model/saved_model.sav"],             ["python","-u","/opt/Orbinato/src/CNN_clf.py"]),
    "LSTM":              (["/opt/Orbinato/src/lstm_model/saved_model.sav"],            ["python","-u","/opt/Orbinato/src/LSTM_clf.py"]),
    "PRETRAINED_LSTM":   (["/opt/Orbinato/src/pretrained-lstm_model/saved_model.sav"], ["python","-u","/opt/Orbinato/src/pretrained_embedding_LSTM.py"]),

    # SecBERT (trained head + labels)
    "SecBERT":           ([
        "/opt/Orbinato/src/trained_secbert.pt",
        "/opt/Orbinato/src/secbert_labels.txt",
    ], [
        "python","-u","/opt/Orbinato/src/secbert_train.py",
        "--train","/opt/Orbinato/data/dataset.csv",
        "--labels-out","/opt/Orbinato/src/secbert_labels.txt",
        "--weights-out","/opt/Orbinato/src/trained_secbert.pt",
        "--device","cuda", 
    ]),
}

def _existing_files_in_image(image: str, paths: List[str], engine: str = "docker") -> List[str]:
    """Return the subset of `paths` that exist inside the image.
    We spin a short container that prints a JSON list of existing files.
    """
    py = (
        "import os,sys,json;p=%r;print(json.dumps([x for x in p if os.path.exists(x)]))"
        % (paths,)
    )
    proc = subprocess.run([engine,"run","--rm",image,"python","-c",py],
                          check=False, capture_output=True, text=True)
    if proc.returncode != 0:
        return []
    try:
        return json.loads(proc.stdout.strip() or "[]")
    except Exception:
        return []

def _train_and_commit(image: str, train_cmd: list[str], use_gpu: bool = True, engine: str = "docker") -> None:
    cname = f"orbinato-train-{uuid.uuid4().hex[:8]}"
    run = [engine, "run", "--name", cname]

    if use_gpu:
        if _gpus_supported(image, engine, spec="all"):
            run += _gpu_args(engine, spec="all")
            print(f"[INFO] GPU support enabled for training in container {cname}.", flush=True)
        else:
            print(
                "[WARN] GPU requested for training but runtime not available; falling back to CPU.",
                file=sys.stderr,
                flush=True,
            )

    run += [image] + train_cmd

    print(f"[INFO] Running training container:\n       {' '.join(run)}")
    proc = subprocess.run(run)  # stream stdout/stderr live
    rc = proc.returncode

    if rc != 0:
        # fetch logs before removing
        try:
            logs = subprocess.run([engine, "logs", cname], capture_output=True, text=True)
            if logs.stdout:
                print("[ERROR] Training logs (stdout):\n" + logs.stdout, file=sys.stderr)
            if logs.stderr:
                print("[ERROR] Training logs (stderr):\n" + logs.stderr, file=sys.stderr)
        finally:
            subprocess.run([engine, "rm", "-f", cname], check=False)
        raise RuntimeError(f"[ERROR] Training failed (exit {rc}). Not committing.")

    print("[INFO] Training complete")
    
    print(f"[INFO] Committing {cname} -> {image}")
    # docker commit overwrites the entrypoint, so we need to restore it
    cr = subprocess.run([engine,"commit",
                         "--change", 'ENTRYPOINT []',
                         "--change", 'CMD ["/bin/bash"]',
                         "--change", 'WORKDIR /opt/Orbinato/src',
                         cname, image
                         ]).returncode 
    if cr != 0:
        # keep the container for inspection if commit fails
        raise RuntimeError(f"[ERROR] docker commit failed (exit {cr}). Container kept: {cname}")

    print(f"[INFO] Removing temp container {cname}")
    subprocess.run([engine, "rm", cname], check=False)

# ---------- Adapter ----------

class OrbinatoAdapter(BaseAdapter):
    """
    Adapter for Orbinato (our orbinato_cli.py in the image).
    - Ensures requested models exist (auto-trains if missing), then snapshots the image.
    - Runs the CLI to produce JSON per-sentence ttps.
    """
    image     = "ttp-workbench:orbinato"     # default; override if needed
    tool_path = "/opt/Orbinato/src/orbinato_cli.py"

    def __init__(self,
                 *,
                 models: List[str] | None = None,
                 secbert_maxlen: int = 512,
                 batch_size: int = 32,
                 device: str = "cuda",
                 use_gpu_for_training: bool = True,
                 verbose: bool = False,
                 engine: str = "docker",
                 **kw):
        """
        models: one or more of keys in _MODEL_SPEC (e.g., ["Logreg","SecBERT"])
        device: cpu|cuda (for inference path inside CLI; training stays CPU unless use_gpu_for_training=True)
        """
        if models == "all":
            models = list(_MODEL_SPEC.keys())
        self.models = models or ["MLP"]
        self.secbert_maxlen = secbert_maxlen
        self.batch_size = batch_size
        self.device = device
        self.use_gpu_for_training = use_gpu_for_training
        self.verbose = verbose
        self.engine = engine
        self._models_ready = False
        self.ensure_models()  # ensure models at init time
        super().__init__(
            verbose=verbose,
            use_gpus=(self.device in ("cuda", "auto")),
            engine=engine,
            **kw,
        )

    # ---- model ensure/snapshot ----------------------------------------
    def ensure_models(self) -> None:
        """Ensure all requested models are present in the image; train+commit if missing."""
        if self._models_ready:
            return
        # Check once per unique training group
        need_train = []
        for m in set(self.models):
            if m not in _MODEL_SPEC:
                raise ValueError(f"Unknown model '{m}'. Valid: {list(_MODEL_SPEC.keys())}")
            paths, _ = _MODEL_SPEC[m]
            existing = set(_existing_files_in_image(self.image, paths, engine=self.engine))
            if not set(paths).issubset(existing):
                need_train.append(m)

        if not need_train:
            self._models_ready = True
            return
        print(f'[WARNING] Container is missing {len(need_train)} models. Running training scripts for: ')
        for i in need_train:
            print(f'          {i}')

        # Collate training invocations:
        # - Any classic ML model -> run ml_classifier.py once
        # - DL models are independent scripts
        # - SecBERT has its own trainer
        to_run: List[List[str]] = []

        for m in need_train:
            if m in _MODEL_SPEC.keys():
                to_run.append(_MODEL_SPEC[m][1])

        seen = set()
        for cmd in to_run:
            key = tuple(cmd)
            if key in seen: 
                continue
            seen.add(key)
            
            _train_and_commit(self.image, cmd, use_gpu=self.use_gpu_for_training, engine=self.engine)

        # Validate everything exists now
        for m in set(self.models):
            paths, _ = _MODEL_SPEC[m]
            existing = set(_existing_files_in_image(self.image, paths, engine=self.engine))
            if not set(paths).issubset(existing):
                raise RuntimeError(f"Artifacts for '{m}' still missing after training/commit.")
        self._models_ready = True

    # ---- BaseAdapter overrides ----------------------------------------
    def build_command(self, in_cn: str, out_cn: str) -> List[str]:
        # Ensure models before building the run command
        self.ensure_models()

        requested = (self.device or "cpu").lower()
        cmd = [
            "/opt/venv/bin/python", "-u", self.tool_path,
            "--in",  in_cn,
            "--out", out_cn,
            "--models", *self.models,
            "--secbert-maxlen", str(self.secbert_maxlen),
            "--batch-size", str(self.batch_size),
            "--device", self.device,
        ]
        # No explicit --secbert-weights/labels: CLI will use defaults in /opt/Orbinato/src,
        # which we just ensured exist (and trained if needed).
        if self.verbose:
            cmd += ["--list-models"] if False else []  # placeholder if you want debug printing
        return cmd

    def parse_sentences(self, d: Dict[str, Any]) -> Dict[str, Any]:
        """ orbinato_cli.py outputs a dict of 
        {
            "model_results": {
                "SecBERT": {
                    "sentences": [
                        {"text": "...", "ttps": [{"label": "T1189", "prob": 0.0973}, ...]},
                        ...
                    ],
                    "ttps": [...]
                }
            }
        }
        output should be of the form:
        {
            "sentences": [
                {"text": "...", "ttps": [{"code": "T1189", "prob": 0.0973}, ...]},
                ...
            ],
        }
        """
        # get the key name for the model
        model_key = list(d.get("model_results", {}).keys())[0]
        sentence_list = d.get("model_results", {}).get(model_key, {}).get("sentences", [])
        updated_sentence_list = []
        for sentence_dict in sentence_list:
            updated_ttps = []
            for ttp in sentence_dict.get('ttps', []):
                ttp_code = ttp.get('label')
                other_items = {
                    k: v for k, v in ttp.items() if k != 'label'
                }
                updated_ttp = {
                    'code': ttp_code,
                }
                updated_ttp.update(other_items)
                updated_ttps.append(updated_ttp)
            updated_sentence_list.append({
                'text': sentence_dict.get('text', "").strip(),
                'ttps': updated_ttps
            })

        return updated_sentence_list

    def extract_ttps(self, d: Dict[str, Any]) -> List[str]:
        # get the ttps from the model results from the first model
        model_key = list(d.get("model_results", {}).keys())[0]
        ttps = d.get("model_results", {}).get(model_key, {}).get("ttps", [])
        seen = set()
        for t in ttps:
            if t.get('prob') > 0.2: # Orbinato specifies this threshold in the paper as a good cutoff
                seen.add(t.get("label"))
        return sorted(seen)

def predict_texts(texts: List[str],
                  ids: List[str] | None = None,
                  *, save_dir: str | Path | None = None,
                  **kw) -> Tuple[List[Dict[str, Any]], List[Path | None]]:
    predictor = OrbinatoAdapter(**kw)
    
    return predictor.predict(texts, ids=ids, save_dir=save_dir, prefix="orbinato")

#!/usr/bin/env python3
"""
Orbinato CLI - Run trained models on input text/files.

- Reuse the original preprocessing steps from the repo (regex + stem/lemma + sentence split).
- Let the user choose one or multiple models from the set of trained artifacts.
- Output per-sentence predictions to CSV (one file per input).
- Keep sources unmodified except for making document_analysis import-safe.
- document_analysis_import.py is a copy of document_analysis.py but truncated at line 293

Usage
-----
python orbinato_cli.py --list-models
python orbinato_cli.py --models Logreg SVM_OVR --in /opt/Orbinato/data/input.txt --out /opt/Orbinato/data/output
"""

import argparse
import sys
from pathlib import Path
from typing import Dict, List, Any, Tuple

# Exact on-disk paths (relative to /opt/Orbinato/src)
MODEL_PATHS = {
    "MLP":               "ml_models/MLP classifier .sav",
    "Logreg":            "ml_models/Logreg.sav",
    "Logreg_normale":    "ml_models/Logreg_normale.sav",
    "Multinomial_NB":    "ml_models/Multinomial_NB.sav",
    "Complement_NB":     "ml_models/Complement_NB.sav",
    "SVM_OVR":           "ml_models/SVM_Classifier_OVR.sav",
    "SVM_OVO":           "ml_models/SVM_Classifier_OVO.sav",
    "LSTM":              "lstm_model/saved_model.sav",
    "CNN":               "cnn_model/saved_model.sav",
    "PRETRAINED_LSTM":   "pretrained-lstm_model/saved_model.sav",
    "SecBERT":           "<huggingface: jackaduma/SecBERT>",  # placeholder
}
DL_KEYS = {"LSTM", "CNN", "PRETRAINED_LSTM"}  # SecBERT handled separately
TOPK = 3  # << keep top-3 per model/sentence so we can do probability threshold later

def parse_args(argv=None):
    p = argparse.ArgumentParser(
        description="Run Orbinato models on input text using the repo's preprocessing."
    )
    p.add_argument("--in", dest="in_file", help="Path to input .txt")
    p.add_argument("--out", dest="out", help="Output path (JSON)", default="-")
    p.add_argument("--models", nargs="+", choices=list(MODEL_PATHS.keys()),
                   help="One or more model keys. Use --list-models to see all.")
    p.add_argument("--list-models", action="store_true", help="List available models and exit")

    p.add_argument("--secbert-maxlen", dest="secbert_maxlen", type=int, default=512,
                   help="Max tokens for SecBERT tokenizer (default 512).")
    p.add_argument("--batch-size", dest="batch_size", type=int, default=32,
                   help="Batch size for model inference (default 32).")
    p.add_argument("--device", dest="device", default="auto",
                   help="Force device for DL/HF models: auto|cpu|cuda (default auto).")
    return p.parse_args(argv)

def list_models():
    print("Available models (key -> path):")
    for k, v in MODEL_PATHS.items():
        print(f"  {k:16s} -> {v}")


def load_preprocessing():
    # Heavy imports happen here (only if needed)
    from importlib import import_module
    da = import_module("document_analysis_import")
    # import only the functions we need; stays true to authors' pipeline
    return {
        "repl":               da.repl,
        "remove_empty_lines": da.remove_empty_lines,
        "combine_text":       da.combine_text,
        "lemmatize_set":      da.lemmatize_set,
        "stemmatize_set":     da.stemmatize_set,
        "load_regex":         da.load_regex,         # comes via utils/regex.yml
        "apply_regex":        da.apply_regex_to_string,
    }

def _resolve_device(device_hint: str):
    if device_hint == 'cpu':
        return 'cpu'
    if device_hint == 'cuda':
        # import torch lazily
        import torch
        return 'cuda' if torch.cuda.is_available() else 'cpu'
    # auto
    try:
        import torch
        return 'cuda' if torch.cuda.is_available() else 'cpu'
    except Exception:
        return 'cpu'


def _load_secbert_labels(labels_path: str | None) -> list[str]:
    """Load label list (one per line). Caller should ensure the path exists.
    Falls back to dataset.csv only if an explicit labels file is not provided
    and no static file is found by the caller.
    """
    if labels_path:
        p = Path(labels_path)
        if not p.exists():
            raise SystemExit(f"SecBERT labels file not found: {p}")
        return [ln.strip() for ln in p.read_text(encoding='utf-8').splitlines() if ln.strip()]
    # Fallback: derive from dataset.csv (best-effort)
    try:
        import pandas as pd
        df = pd.read_csv('/opt/Orbinato/data/dataset.csv')
        labels = sorted(df['label_tec'].dropna().unique().tolist())
        return labels
    except Exception as e:
        raise SystemExit("Could not load SecBERT labels; provide --secbert-labels or train to create them. {e}") 
    
def predict_with_secbert(sentences: list[str], *, weights_path: str | None, labels_path: str | None,
                         batch_size: int = 32, max_len: int = 512, device_hint: str = 'auto') -> List[Dict[str, Any]]:
    """Return rows of dicts: {'model','sid','text','topk':[{'label','prob'}, ... (k=TOPK)]}"""
    device = _resolve_device(device_hint)
    labels = _load_secbert_labels(labels_path)

    # Lazy heavy imports
    import torch
    from torch.utils.data import Dataset, DataLoader
    from transformers import AutoTokenizer, AutoModel, BertConfig

    class Triage(Dataset):
        def __init__(self, dataframe, tokenizer, max_len):
            self.len = len(dataframe)
            self.data = dataframe
            self.tokenizer = tokenizer
            self.max_len = max_len
        def __getitem__(self, index):
            sentence = str(self.data.sentence[index])
            sentence = " ".join(sentence.split())
            inputs = self.tokenizer.encode_plus(
                sentence,
                None,
                add_special_tokens=True,
                max_length=self.max_len,
                padding='max_length',
                truncation=True,
                return_token_type_ids=False
            )
            ids = inputs['input_ids']
            mask = inputs['attention_mask']
            return {
                'sentence': sentence,
                'ids': torch.tensor(ids, dtype=torch.long),
                'mask': torch.tensor(mask, dtype=torch.long),
            }
        def __len__(self):
            return self.len

    class SecBERTClass(torch.nn.Module):
        def __init__(self, pretrained_model_name: str, num_classes: int = None, dropout: float = 0.3):
            super().__init__()
            config = BertConfig.from_pretrained(pretrained_model_name, output_hidden_states=True)
            self.model = AutoModel.from_pretrained(pretrained_model_name, config=config).base_model
            #for param in self.model.parameters():
            #    param.requires_grad = False
            self.pre_classifier = torch.nn.Linear(768, 768)
            self.dropout = torch.nn.Dropout(dropout)
            self.classifier = torch.nn.Linear(768, num_classes)
        def forward(self, input_ids, attention_mask):
            output_1 = self.model(input_ids=input_ids, attention_mask=attention_mask, output_hidden_states=True)
            hidden_state = output_1[0]
            pooler = hidden_state[:, 0]
            pooler = self.pre_classifier(pooler)
            pooler = torch.nn.ReLU()(pooler)
            pooler = self.dropout(pooler)
            output = self.classifier(pooler)
            return output

    tokenizer = AutoTokenizer.from_pretrained('jackaduma/SecBERT', use_fast=True)
    model = SecBERTClass('jackaduma/SecBERT', num_classes=len(labels))
    state = torch.load(weights_path, map_location='cpu')
    model.load_state_dict(state)
    model.to(device)
    model.eval()

    # build dataset/loader
    import pandas as pd
    df = pd.DataFrame({'sentence': sentences})
    sentence_set = Triage(df, tokenizer, max_len)
    testing_loader = DataLoader(sentence_set, batch_size=batch_size, shuffle=False, num_workers=0)

    rows: List[Dict[str, Any]] = []
    with torch.no_grad():
        idx = 0
        for batch in testing_loader:
            x = batch['ids'].to(device, dtype=torch.long)
            mask = batch['mask'].to(device, dtype=torch.long)
            scores = model(x, mask)
            proba_scores = torch.nn.functional.softmax(scores, dim=1)
            topk_prob, topk_idx = torch.topk(proba_scores, k=TOPK, dim=1)
            for j in range(x.size(0)):
                probs = topk_prob[j].tolist()
                inds  = topk_idx[j].tolist()
                s = sentence_set.data.sentence.iloc[idx]
                top = [{"label": labels[i], "prob": float(p)} for (i, p) in zip(inds, probs)]
                rows.append({"model":"SecBERT","sid":idx,"text":s,"topk":top})
                idx += 1
    return rows


def _ensure_secbert_artifacts():
    """Resolve weights/labels paths. If missing, attempt on-demand training.
    Returns (weights_path:str, labels_path:str, trained_now:bool).
    """
    default_weights = Path("/opt/Orbinato/src/trained_secbert.pt")
    default_labels  = Path("/opt/Orbinato/src/secbert_labels.txt")

    need_train = (not default_weights.exists()) or (not default_labels.exists())
    
    """
    if need_train:
        # kick off training via the provided script
        import subprocess
        train_script = Path("/opt/Orbinato/secbert_train.py")
        if not train_script.exists():
            print(f"[ERROR] SecBERT artifacts not found and train script missing: {train_script}", file=sys.stderr)
            raise SystemExit(2)
        print("[INFO] SecBERT weights/labels missing — starting on-demand training...", file=sys.stderr)
        cmd = [
            sys.executable, str(train_script),
            "--train", "/opt/Orbinato/data/dataset.csv",
            "--labels-out", str(default_labels),
            "--weights-out", str(default_weights),
            "--device", args.device,
        ]
        rc = subprocess.call(cmd)
        if rc != 0:
            print(f"[ERROR] SecBERT training failed (exit {rc}).", file=sys.stderr)
            raise SystemExit(rc)
        # verify artifacts now exist
        if not (default_weights.exists() and default_labels.exists()):
            print("[ERROR] SecBERT training finished but artifacts not found.", file=sys.stderr)
            raise SystemExit(2)
        trained_now = True
    """

    return str(default_weights), str(default_labels), need_train


def run_inference(args):
    # Decide which heavy stacks to import
    use_dl = any(k in DL_KEYS for k in args.models)
    use_hf = any(k == "SecBERT" for k in args.models)

    # Shared heavy bits (numpy, pickle, nltk sentence tokenizer)
    import pickle, csv, numpy as np
    from nltk.tokenize import sent_tokenize

    # Load authors' preprocessing lazily
    prep = load_preprocessing()

    # Read + preprocess
    txt = Path(args.in_file).read_text(encoding="utf-8", errors="ignore")
    import re
    text = prep["combine_text"]([txt])
    text = re.sub(r'(%(\w+)%(\/[^\s]+))', prep["repl"], text)
    regex_list = prep["load_regex"]("utils/regex.yml")
    text = prep["apply_regex"](regex_list, text)
    text = re.sub(r'\(.*?\)', '', text)
    text = prep["remove_empty_lines"](text).strip()
    sentences = sent_tokenize(text)

    # rows will be a list of dicts: {'model','sid','text','topk':[{'label','prob'} * TOPK]}
    rows: List[Dict[str, Any]] = []

    # ---- ML models (scikit) ----
    ml_models = [m for m in args.models if m not in DL_KEYS and m != "SecBERT"]
    if ml_models:
        from sklearn.feature_extraction.text import TfidfVectorizer  # only used to get vocab type
        # Load vectorizer+clf per model file and run
        stemmed = prep["stemmatize_set"](sentences)
        lemmatized = prep["lemmatize_set"](stemmed)
        for key in ml_models:
            vec, clf = pickle.load(open(Path("/opt/Orbinato/src")/MODEL_PATHS[key], "rb"))
            X = vec.transform(lemmatized)
            proba = clf.predict_proba(X)
            topk_idx = np.argsort(proba, axis=1)[:, -TOPK:]
            classes = clf.classes_
            for i, s in enumerate(sentences):
                # sort these TOPK indices by prob desc
                idxs = topk_idx[i]
                idxs = idxs[np.argsort(proba[i, idxs])[::-1]]
                top = [{"label": str(classes[j]), "prob": float(proba[i, j])} for j in idxs]
                rows.append({"model": key, "sid": i, "text": s, "topk": top})

    # ---- DL models (Keras via scikeras wrappers) ----
    if use_dl:
        from deepl_utils import DLPreprocessingManager, Model_Manager, LSTM_model_config, CNN_model_config, LSTM_pretrained_config
        cfgs = {
            "LSTM":            LSTM_model_config(),
            "CNN":             CNN_model_config(),
            "PRETRAINED_LSTM": LSTM_pretrained_config(),
        }
        for key in args.models:
            if key not in cfgs: 
                continue
            cfg = cfgs[key]
            pp = DLPreprocessingManager()
            path = Path("/opt/Orbinato/src")/cfg.get_saving_path()
            pp.load_preprocessing_pipe(path=str(path))
            mm = Model_Manager().load_model(path=str(path))
            # lazy pandas import (only if DL selected)
            import pandas as pd
            X = pp.get_features_vectors(pd.Series(sentences), cfg.MAX_SEQUENCE_LENGTH)
            preds = mm.predict(X)
            proba = mm.predict_proba(X)
            # classes_ comes from scikeras wrapper
            topk_idx = np.argsort(proba, axis=1)[:, -TOPK:]
            classes = mm.classes_
            # map numeric encodings back to labels via pp
            # (classes here are encoded; convert)
            for i, s in enumerate(sentences):
                idxs = topk_idx[i]
                # order by prob desc
                idxs = idxs[np.argsort(proba[i, idxs])[::-1]]
                lbls = pp.get_labels_from_encoding(classes[idxs])
                top = [{"label": str(lbls[k]), "prob": float(proba[i, idxs[k]])} for k in range(len(idxs))]
                rows.append({"model": key, "sid": i, "text": s, "topk": top})

    # ---- HuggingFace SecBERT ----
    if use_hf:
        # ensure artifacts exist or train on-demand
        resolved_weights, resolved_labels, need_train = _ensure_secbert_artifacts()
        if need_train:
            raise SystemExit("[ERROR] SecBERT needs training. Run Orbinato/src/secbert_train.py to create necessary artifacts.")
        rows += predict_with_secbert(
            sentences,
            weights_path=resolved_weights,
            labels_path=resolved_labels,
            batch_size=args.batch_size,
            max_len=args.secbert_maxlen,
            device_hint=args.device,
        )

    # ---- Build by-model structure  ----
    import json
    # model_results: { model: {"sentences":[{"text","ttps":topk}], "ttps":[{"label","prob"}]} }
    model_results: Dict[str, Dict[str, Any]] = {}
    # temporary per-model per-sentence and label aggregations
    tmp_sent: Dict[Tuple[str, int], Dict[str, Any]] = {}
    tmp_model_label_max: Dict[str, Dict[str, float]] = {}

    for row in rows:
        m = row["model"]; sid = int(row["sid"]); text = row["text"]; top = row["topk"]
        # per-sentence, per-model
        k = (m, sid)
        if k not in tmp_sent:
            tmp_sent[k] = {"text": text, "ttps": []}
        tmp_sent[k]["ttps"] = top  # already top-k sorted (desc)
        # per-model max over sentences
        agg = tmp_model_label_max.setdefault(m, {})
        for t in top:
            lbl, pr = t["label"], float(t["prob"])
            if pr > agg.get(lbl, 0.0):
                agg[lbl] = pr

    for m, label_map in tmp_model_label_max.items():
        # collect sentences for this model sorted by sid
        sent_list = []
        for (mm, sid), rec in sorted(tmp_sent.items(), key=lambda x: x[0][1]):
            if mm == m:
                sent_list.append({"text": rec["text"], "ttps": rec["ttps"]})
        ttps = sorted([{"label": k, "prob": float(v)} for k, v in label_map.items()],
                      key=lambda x: x["prob"], reverse=True)
        model_results[m] = {"sentences": sent_list, "ttps": ttps}


    payload = json.dumps({"model_results": model_results}, ensure_ascii=False, indent=2)

    if args.out == "-" or args.out is None:
        sys.stdout.write(payload + "\n")
    else:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(payload, encoding="utf-8")

def main(argv=None):
    args = parse_args(argv)

    # FAST PATHS — no heavy imports here
    if args.list_models:
        list_models()
        return 0
    if not args.in_file or not args.models:
        # rely on argparse --help for usage; but don't import heavy stuff
        print("error: --in and --models are required unless using --list-models\n", file=sys.stderr)
        parse_args(["-h"])  # show help and exit

    # Slow path: only now do we import heavy libs and run
    return run_inference(args)

if __name__ == "__main__":
    raise SystemExit(main())
"""Shared private-model archive schema and validation helpers."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Iterable

SCHEMA = "ttp-workbench.reproduction-models/v1"
MANIFEST_NAME = "MODEL_MANIFEST.json"
ORBINATO_SHA256 = "c36324c52e6470db73b7cc952d09d0f26cffd340d54e7cc11a70dabe80c31c1f"
BUCHEL_MODELS = ("bosch_sentence",)
HISTORICAL_BUCHEL_MODELS = ("mitre_sentence_tram",)
ORBINATO_FILES = {
    "cnn_model/encoder.pickle", "cnn_model/saved_model.sav", "cnn_model/tokenizer.pickle",
    "lstm_model/encoder.pickle", "lstm_model/saved_model.sav", "lstm_model/tokenizer.pickle",
    "pretrained-lstm_model/encoder.pickle", "pretrained-lstm_model/saved_model.sav",
    "pretrained-lstm_model/tokenizer.pickle", "secbert_model/secbert_labels.txt",
    "secbert_model/trained_secbert.pt", "ml_models/SVM_Classifier_OVR.sav",
    "ml_models/SVM_Classifier_OVO.sav", "ml_models/MLP_classifier.sav",
    "ml_models/MLP classifier .sav", "ml_models/Multinomial_NB.sav",
    "ml_models/Complement_NB.sav", "ml_models/Logreg.sav", "ml_models/Logreg_normale.sav",
}
REQUIRED_BUCHEL_FILES = {
    "config.json",
    "generation_config.json",
    "model.safetensors.index.json",
    "special_tokens_map.json",
    "tokenizer.json",
    "tokenizer_config.json",
}


def sha256_path(path: Path, chunk_size: int = 8 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def copy_and_hash(source: BinaryIO, destination: BinaryIO, chunk_size: int = 8 * 1024 * 1024) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    while True:
        chunk = source.read(chunk_size)
        if not chunk:
            break
        destination.write(chunk)
        digest.update(chunk)
        size += len(chunk)
    return size, digest.hexdigest()


def safe_member(name: str) -> PurePosixPath:
    path = PurePosixPath(name)
    if not name or name.endswith("/") or path.is_absolute() or ".." in path.parts or "" in path.parts:
        raise ValueError(f"Unsafe archive member: {name!r}")
    if path.parts[0] not in {"orbinato", "buchel"}:
        raise ValueError(f"Unexpected archive prefix: {name}")
    return path


def canonical_json(value: object) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def validate_manifest(manifest: dict, archive_names: Iterable[str]) -> dict[str, dict]:
    if manifest.get("schema") != SCHEMA:
        raise ValueError(f"Unsupported model archive schema: {manifest.get('schema')!r}")
    entries = manifest.get("files")
    if not isinstance(entries, list) or not entries:
        raise ValueError("Model manifest has no file inventory")
    by_path: dict[str, dict] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("Invalid model manifest entry")
        name = str(entry.get("path", ""))
        safe_member(name)
        if name in by_path:
            raise ValueError(f"Duplicate manifest path: {name}")
        size = entry.get("size")
        digest = entry.get("sha256")
        if not isinstance(size, int) or size < 0 or not isinstance(digest, str) or len(digest) != 64:
            raise ValueError(f"Invalid size or SHA-256 for {name}")
        by_path[name] = entry
    actual = set(archive_names)
    expected = set(by_path) | {MANIFEST_NAME}
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise ValueError(f"Archive/manifest member mismatch; missing={missing}, extra={extra}")
    tool = manifest.get("tool")
    if tool not in {"buchel", "orbinato"}:
        raise ValueError(f"Unsupported model archive tool: {tool!r}")
    if tool == "orbinato":
        required = {"orbinato/src/" + name for name in ORBINATO_FILES}
        if set(by_path) != required:
            raise ValueError(
                "Orbinato model inventory mismatch; "
                f"missing={sorted(required - set(by_path))}, extra={sorted(set(by_path) - required)}"
            )
    else:
        allowed = set()
        present_models = set()
        for model in (*BUCHEL_MODELS, *HISTORICAL_BUCHEL_MODELS):
            prefix = f"buchel/models/local/{model}/merged/"
            names = {name.removeprefix(prefix) for name in by_path if name.startswith(prefix)}
            if not names:
                continue
            present_models.add(model)
            missing = REQUIRED_BUCHEL_FILES - names
            if missing or not any(name.endswith(".safetensors") for name in names):
                raise ValueError(f"Incomplete Buchel {model} inventory; missing={sorted(missing)}")
            allowed.update(prefix + name for name in names)
        if not set(BUCHEL_MODELS).issubset(present_models):
            raise ValueError(f"Missing required Buchel models: {sorted(set(BUCHEL_MODELS) - present_models)}")
        if set(by_path) != allowed:
            raise ValueError(f"Unexpected Buchel model files: {sorted(set(by_path) - allowed)}")
    return by_path

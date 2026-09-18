# adapters/buchel_cli.py
# this gets copied alongside Buchel's generation code.
# it extends the experiment code to a CLI runner
from __future__ import annotations
import os, json, argparse
import math
import sys
import time
from pathlib import Path
from typing import Dict, Any, List, Tuple, Iterable

from unsloth import FastLanguageModel
from peft import PeftModel


import mitre
import llm_response
import rag
from finetuning.test_helper import create_subsequences

from transformers import AutoTokenizer
from datasets import load_dataset

DEFAULT_BASE_MODEL = os.environ.get("BASE_MODEL", "unsloth/Meta-Llama-3.1-8B-Instruct")
DEFAULT_MAX_SEQ_LEN = 8192
DEFAULT_SFT_ROOT = os.environ.get("SFT_ROOT", "/workspace/finetuning/output")

# recipes for auto-train (names must match supervised_finetuning.single_train usage)
MODEL_RECIPES: Dict[str, Dict[str, str]] = {
    "mitre_sentence_tram": {
        "dataset": "finetuning/cti_datasets/tram/tram_sentence_based_train_instructs.json",
        "val_per": "0.01",
    },
    "bosch_sentence": {
        "dataset": "finetuning/cti_datasets/bosch/sentence_based_train_instructs.json",
        "val_per": "0.01",
    },
}

# ------------------------------
# SFT discovery / loading
# ------------------------------

def _ensure_tokenizer_files(model_dir: str, base_model: str = DEFAULT_BASE_MODEL):
    p = Path(model_dir)
    entries = set(os.listdir(model_dir)) if p.is_dir() else set()
    if not (("tokenizer.json" in entries) or ("tokenizer.model" in entries)):
        print(f"[BuchelCLI] Seeding tokenizer files in: {model_dir} from {base_model}")
        tok = AutoTokenizer.from_pretrained(base_model, use_fast=True)
        p.mkdir(parents=True, exist_ok=True)
        tok.save_pretrained(model_dir)


def _has_model_files(dirpath: Path) -> bool:
    if not dirpath.is_dir():
        return False
    if (dirpath / "model.safetensors").exists() and (dirpath / "config.json").exists():
        return True
    return any(f.suffix == ".safetensors" for f in dirpath.iterdir() if f.is_file())


def check_then_train(sft_name: str, auto_train: bool, prefer_merged: bool = True) -> str:
    #Ensure the requested SFT exists under finetuning/output/<sft_name> (or train it).
    root = Path(DEFAULT_SFT_ROOT) / sft_name
    merged_dir = root / "merged"

    # Build candidate list (merged first if preferred)
    ckpts = sorted(root.glob("checkpoint-*"), key=lambda p: p.stat().st_mtime, reverse=True)
    if prefer_merged:
        candidates: List[Path] = [merged_dir, root] + ckpts
    else:
        candidates: List[Path] = [root, merged_dir] + ckpts

    for c in candidates:
        if _has_model_files(c):
            print(f"[BuchelCLI] Using SFT at: {c}")
            return c.as_posix()

    if not auto_train:
        raise FileNotFoundError(
            f"SFT '{sft_name}' not found under {DEFAULT_SFT_ROOT}. Pass --auto_train to train it."
        )

    # Train via the project's function
    print(f"[BuchelCLI] Training SFT '{sft_name}' …")
    try:
        from supervised_finetuning import single_train
    except Exception as e:
        raise RuntimeError("Could not import supervised_finetuning.single_train.") from e

    recipe = MODEL_RECIPES.get(sft_name)
    if not recipe:
        raise ValueError(f"No dataset recipe registered for '{sft_name}'.")

    ds_path = recipe["dataset"]
    val_per = float(recipe.get("val_per", "0.01"))

    # Capture the returned model/tokenizer and ensure we write artifacts to DEFAULT_SFT_ROOT/<name>
    model, tokenizer = single_train(train_name=sft_name, dataset_path=ds_path, val_dataset_len_per=val_per)

    # Save in the exact directory the loader checks
    try:
        _save_sft_artifacts(model, tokenizer, root.as_posix(), save_merged=True)
    except Exception as e:
        print(f"[BuchelCLI] WARN: could not auto-save SFT to {root}: {e}")

    # Re-check
    return check_then_train(sft_name, auto_train=False, prefer_merged=prefer_merged)



def _load_sft_model(model_dir: str, quant_4bit: bool = True):

    if not os.path.isdir(model_dir):
        raise FileNotFoundError(f"SFT model dir not found: {model_dir}")

    files = set(os.listdir(model_dir))
    is_merged = ("config.json" in files) and (
        any(f.endswith(".safetensors") for f in files) or ("model.safetensors.index.json" in files)
    )
    is_adapter = ("adapter_config.json" in files)

    if not (is_merged or is_adapter or any(f.endswith(".safetensors") for f in files)):
        raise FileNotFoundError(f"No model artifacts found in {model_dir}")

    if is_merged:
        # Load merged HF model directly
        _ensure_tokenizer_files(model_dir)
        print(f"[BuchelCLI] Loading MERGED SFT from {model_dir} (4bit={quant_4bit})")
        model, tokenizer = FastLanguageModel.from_pretrained(
            model_name=model_dir, max_seq_length=DEFAULT_MAX_SEQ_LEN,
            dtype=None, load_in_4bit=quant_4bit,
        )
        FastLanguageModel.for_inference(model)
        return model, tokenizer

    # Adapter-only: load base, then attach LoRA adapter
    base_model_name = DEFAULT_BASE_MODEL  # or read from env/metadata if you store it
    print(f"[BuchelCLI] Loading BASE {base_model_name} + ADAPTER {model_dir} (4bit={quant_4bit})")
    base_model, tokenizer = FastLanguageModel.from_pretrained(
        model_name=base_model_name, max_seq_length=DEFAULT_MAX_SEQ_LEN,
        dtype=None, load_in_4bit=quant_4bit,
    )
    # Attach LoRA adapter
    base_model = PeftModel.from_pretrained(base_model, model_dir)
    FastLanguageModel.for_inference(base_model)
    return base_model, tokenizer

def _save_sft_artifacts(model, tokenizer, target_dir: str, save_merged: bool = True):
    # save LoRA adapter, tokenizer, merged HF model.

    p = Path(target_dir)
    p.mkdir(parents=True, exist_ok=True)
    tokenizer.save_pretrained(p.as_posix())
    model.save_pretrained(p.as_posix())

    if save_merged:
        try:
            merged = model.merge_and_unload()
            (p / "merged").mkdir(parents=True, exist_ok=True)
            merged.save_pretrained((p / "merged").as_posix())
        except Exception as e:
            print(f"[BuchelCLI] WARN: failed to save merged model: {e}")


def _load_base_model(base_name: str = DEFAULT_BASE_MODEL, quant_4bit: bool = False):
    from supervised_finetuning import load_model as _load
    return _load(base_name, quant_4bit_model=quant_4bit)


# ------------------------------
# Prompt building / strategy these re-use the Buchel experiments code
# ------------------------------

def _install_embedding_guard():
    """Bound embedding failures in this CLI process only; leave the released client intact."""
    import httpx
    import numpy as np
    import ollama

    attempts = int(os.environ.get("OLLAMA_RETRY_MAX", "8"))
    backoff = float(os.environ.get("OLLAMA_RETRY_BACKOFF", "0.5"))
    timeout = float(os.environ.get("OLLAMA_REQUEST_TIMEOUT", "120"))
    if attempts <= 0 or not all(math.isfinite(v) and v > 0 for v in (backoff, timeout)):
        raise ValueError("OLLAMA_RETRY_MAX, OLLAMA_RETRY_BACKOFF and OLLAMA_REQUEST_TIMEOUT must be positive and finite")
    client = ollama.Client(host=rag.ollama_api.OLLAMA_SERVER, timeout=timeout)

    def get_embedding(text: str, model: str = "rjmalagon/gte-qwen2-7b-instruct:f16"):
        delay = backoff
        for attempt in range(1, attempts + 1):
            try:
                # Call Ollama directly: an outer wrapper cannot bound the native infinite loop.
                result = np.array(client.embeddings(model=model, prompt=text)["embedding"])
                if result.ndim != 1 or not result.size or not np.isfinite(result).all():
                    raise ValueError("Ollama returned an empty, non-vector or non-finite embedding")
                return result
            except Exception as exc:
                status = getattr(exc, "status_code", None)
                message = str(exc).lower()
                permanent = any(part in message for part in (
                    "context length", "context limit", "input length", "model not found",
                    "does not support", "unauthorized", "forbidden"))
                transient = isinstance(exc, (ConnectionError, httpx.TransportError)) or (
                    isinstance(exc, ollama.ResponseError) and
                    (status in (408, 429) or isinstance(status, int) and status >= 500))
                retry = transient and not permanent and attempt < attempts
                print(f"[BuchelCLI] embedding attempt {attempt}/{attempts}; model={model}; "
                      f"input_chars={len(text)}; retry={retry}; {type(exc).__name__}: {exc}",
                      file=sys.stderr, flush=True)
                if not retry:
                    raise
                time.sleep(min(delay, 8.0))
                delay = min(delay * 1.8, 8.0)

    rag.ollama_api.get_embedding = get_embedding


def _build_prompt(text: str, *, use_bosch_style: bool, rag_on: bool, fsp_on: bool, document_level: bool,
                   mitre_table) -> str:
    # Keep style differences between Bosch vs TRAM.
    from database import label_split
    labels = label_split.BOSCH_TECHNIQUES_LABELS if use_bosch_style else []
    if document_level:
        if rag_on:
            closest = rag.get_best_text_embeddings(text, mitre_table["embedding"])  # rag_entries=20 inside helper
            return mitre.create_mitre_based_rag_prompt(
                report=text, closest_embeddings=closest, mitre_table=mitre_table, rag_entries=20,
                few_shot=fsp_on, is_bosch=use_bosch_style, label_to_limit=labels
            )
        else:
            return mitre.create_mitre_based_prompt(text, few_shot=fsp_on, only_techniques=use_bosch_style)
    else:
        if rag_on:
            closest = rag.get_best_text_embeddings(text, mitre_table["embedding"])  # sentence-level RAG
            return mitre.create_mitre_sentence_based_rag_prompt(
                sentence=text, closest_embeddings=closest, mitre_table=mitre_table,
                few_shot=fsp_on, is_bosch=use_bosch_style, label_to_limit=labels
            )
        else:
            return mitre.create_mitre_sentence_based_prompt(text, few_shot=fsp_on, only_techniques=use_bosch_style)


def _process_text(text: str, *, document_level: bool, 
                is_tram: bool, is_bosch: bool) -> List[str]: # for splitting reproducibily with supervised_finetuning.py
    if document_level:
        return [text]
    # Prefer dataset-provided boundaries first
    if is_bosch:
        sentences = create_subsequences(text) #finetuning calls with a variable named text_list, but it is actually a single string
        return sentences
    if is_tram:
        # combining by whitespece was problematic in runs, but "*^*^*^" join fixed repro issues
        sentences = [s.strip() for s in text.split("*^*^*^") if s.strip()]
        sentences = [s.replace("*^*^*^", "").strip() for s in sentences]
        return sentences
    # Fallback: simple split by periods
    sentences = [s.strip() for s in text.split(".") if s.strip()]
    return sentences


# ------------------------------
# Input iteration helpers
# ------------------------------

def _iter_from_file(infile: Path) -> Iterable[Tuple[str, str]]:
    return [(infile.stem, infile.read_text(encoding="utf-8"))]


def _iter_from_dir(indir: Path) -> Iterable[Tuple[str, str]]:
    for p in sorted(indir.rglob("*.txt")):
        try:
            yield (p.stem, p.read_text(encoding="utf-8"))
        except Exception:
            continue


def _iter_from_dataset(ds_path: Path) -> Iterable[Tuple[str, str]]:
    """Load a JSON dataset like those used in supervised_finetuning.
    We try common layouts: 'text', 'report', 'sentence', or 'messages' (pick last user message).
    """
    ds = load_dataset("json", data_files=str(ds_path), split="train")
    def extract_text(ex: Dict[str, Any]) -> str:
        if isinstance(ex, dict):
            if "text" in ex and isinstance(ex["text"], str):
                return ex["text"]
            if "report" in ex and isinstance(ex["report"], str):
                return ex["report"]
            if "sentence" in ex and isinstance(ex["sentence"], str):
                return ex["sentence"]
            if "messages" in ex and isinstance(ex["messages"], list):
                # choose last user content if present
                user_msgs = [m.get("content", "") for m in ex["messages"] if isinstance(m, dict) and m.get("role") == "user"]
                if user_msgs:
                    return user_msgs[-1]
                # fallback: concatenate any content fields
                parts = [str(m.get("content", "")) for m in ex["messages"] if isinstance(m, dict)]
                if parts:
                    return "".join(parts)
        # last resort
        return json.dumps(ex, ensure_ascii=False)
    for idx, ex in enumerate(ds):
        txt = extract_text(ex)
        yield (f"row_{idx}", txt)


# ------------------------------
# Strategy executor
# ------------------------------

def run_strategy_on_text(
    text: str,
    *,
    use_bosch_style: bool,   # only affects stylistic bits in prompts
    rag_on: bool,
    fsp_on: bool,
    document_level: bool,
    is_tram: bool = False,
    is_bosch: bool = False,
    model,
    tokenizer,
    mitre_table_path: str = "database/rag_db_qwen.csv",
) -> Tuple[str, List[str]]:
    # Replicates finetuning_test for arbitrary text with selected flags. Returns (raw_response_concat, list_of_MITRE_codes)
    import finetuning.test_helper as th
    mitre_table = th.read_rag_db(mitre_table_path)

    sentences = _process_text(text, document_level=document_level, is_tram=is_tram, is_bosch=is_bosch)
    raw_concat = ""

    for sent in sentences:
        if not sent:
            continue
        user_query = _build_prompt(
            sent, use_bosch_style=use_bosch_style, rag_on=rag_on, fsp_on=fsp_on,
            document_level=document_level, mitre_table=mitre_table
        )
        msgs = [
            {"role": "system", "content": "You are a Cyber Security Expert who finds MITRE ATT&CK framework concepts in CTI Reports."},
            {"role": "user", "content": user_query},
        ]
        resp = th.inference(model, tokenizer, msgs)
        raw_concat += resp

    # Parse predictions (no dataset-specific filtering; post-process later in the framework)
    by_name = set(llm_response.replaceNametoID(mitre_table, raw_concat))
    by_code = set(llm_response.extract_mitre_ids(mitre_table, raw_concat))
    codes = sorted(by_name.union(by_code))
    return raw_concat, codes


# ------------------------------
# CLI
# ------------------------------

def main():
    p = argparse.ArgumentParser(description="Buchel reproducibility CLI (Ollama embeddings for RAG)")

    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--infile", type=Path, help="Path to a single .txt file")
    src.add_argument("--indir", type=Path, help="Directory of .txt files (recurses)")
    src.add_argument("--dataset_path", type=Path, help="JSON dataset file as used by supervised_finetuning (for providing new SFT data)")

    # Output: either one outfile (for single input) or an outdir (for multiple)
    p.add_argument("--outfile", type=Path, help="Output JSON for single input")
    p.add_argument("--outdir", type=Path, help="Directory to write one JSON per input")

    # Strategies
    p.add_argument("--dataset", choices=["bosch", "tram"], default="tram",
                   help="Controls preprocessing and prompt style to replicate bosch and tram experiments")
    p.add_argument("--document_level", action="store_true", help="Process as one document instead of sentences")
    p.add_argument("--rag", action="store_true", help="Enable RAG prompting")
    p.add_argument("--fsp", action="store_true", help="Enable few-shot prompting")

    # Models
    p.add_argument("--sft", action="store_true", help="Use fine-tuned model instead of base")
    p.add_argument("--sft_name", choices=sorted(MODEL_RECIPES.keys()), default=None,
                   help="If set, resolves/auto-trains finetuning/output/<name>[/merged]")
    p.add_argument("--auto_train", action="store_true", help="If named SFT missing, train now")
    p.add_argument("--prefer_merged", action="store_true", help="Prefer merged subdir when present")
    p.add_argument("--sft_model_dir", default=None,
                   help="Explicit directory to a saved SFT model (overrides --sft_name if set)")
    p.add_argument("--quant_4bit_model", action="store_true", help="Load model in 4-bit if supported")
    p.add_argument("--base_model", default=DEFAULT_BASE_MODEL, help="Base model to load when --sft is off")
    p.add_argument("--mitre_table", default="database/rag_db_qwen.csv")

    args = p.parse_args()

    if args.rag:
        _install_embedding_guard()

    # Load model/tokenizer via existing project functions
    if args.sft:
        if args.sft_model_dir:
            sft_dir = args.sft_model_dir
            if not Path(sft_dir).exists():
                raise FileNotFoundError(f"SFT dir not found: {sft_dir}")
        elif args.sft_name:
            sft_dir = check_then_train(args.sft_name, auto_train=args.auto_train, prefer_merged=args.prefer_merged)
        else:
            raise SystemExit("--sft requires either --sft_model_dir or --sft_name")
        model, tokenizer = _load_sft_model(sft_dir, args.quant_4bit_model)
    else:
        model, tokenizer = _load_base_model(args.base_model, args.quant_4bit_model)

    # Build iterator over inputs
    if args.infile:
        items = _iter_from_file(args.infile)
        multi = False
    elif args.indir:
        items = _iter_from_dir(args.indir)
        multi = True
    else:
        items = _iter_from_dataset(args.dataset_path)
        multi = True

    # Output resolution
    if not multi:
        # single input: allow --outfile or default to ./buchel_cli_output.json
        outfile = args.outfile or Path("buchel_cli_output.json")
        outdir = None
    else:
        # multi input: require/outdir or default to ./outputs/buchel_cli
        outdir = args.outdir or Path("outputs/buchel_cli")
        outdir.mkdir(parents=True, exist_ok=True)
        outfile = None

    # Strategy flags
    use_bosch_style = (args.dataset == "bosch")

    # Process
    def pack_result(_id: str, text: str, raw_response: str, codes: List[str]) -> Dict[str, Any]:
        return {
            "id": _id,
            "text": text,
            "strategy": {
                "dataset_style": args.dataset,
                "document_level": bool(args.document_level),
                "rag": bool(args.rag),
                "fsp": bool(args.fsp),
                "engine": ("sft" if args.sft else "base"),
                "model_path": (args.sft_model_dir or args.sft_name or args.base_model),
            },
            "predictions": [{"code": c, "TTP": None} for c in codes],
            "raw_response": raw_response,
        }

    if not multi:
        (in_id, in_text) = next(iter(items))
        raw_response, codes = run_strategy_on_text(
            in_text,
            use_bosch_style=use_bosch_style,
            rag_on=args.rag,
            fsp_on=args.fsp,
            document_level=args.document_level,
            is_tram=args.dataset == "tram",
            is_bosch=args.dataset == "bosch",
            model=model,
            tokenizer=tokenizer,
            mitre_table_path=args.mitre_table,
        )
        result = [pack_result(in_id, in_text, raw_response, codes)]
        outfile.parent.mkdir(parents=True, exist_ok=True)
        outfile.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[BuchelCLI] Wrote: {outfile}")
    else:
        for in_id, in_text in items:
            raw_response, codes = run_strategy_on_text(
                in_text,
                use_bosch_style=use_bosch_style,
                rag_on=args.rag,
                fsp_on=args.fsp,
                document_level=args.document_level,
                is_tram=args.dataset == "tram",
                is_bosch=args.dataset == "bosch",
                model=model,
                tokenizer=tokenizer,
                mitre_table_path=args.mitre_table,
            )
            result = [pack_result(in_id, in_text, raw_response, codes)]
            outpath = outdir / f"{in_id}.json"
            outpath.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"[BuchelCLI] Wrote: {outpath}")


if __name__ == "__main__":
    main()

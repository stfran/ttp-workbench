# adapters/ttp_llm_adapter.py
from __future__ import annotations
from pathlib import Path
from typing import List, Dict, Any, Tuple
import re, configparser, warnings
import json
import os
import shlex
import time

from Framework.adapters.base_adapter import BaseAdapter  # type: ignore

# ----------------------------- constants -----------------------------
_EXPECTED_TEMPLATE = """\n[API]\nOpenAI_Key = <YOUR_API_KEY>\nHuggingFace_Key = <YOUR_API_KEY>\n"""

_SUPPORTED_MODES = {"prompt_only"}  # repo supports prompt_only + RAG modes; we default to prompt_only
_IMPLEMENTED_MODELS = {"gpt-3.5-turbo"}  # extend later

_OPENAI_KEY_RE = re.compile(r"^sk-[A-Za-z0-9_-]{10,}$")
# FIX: real HF tokens start with 'hf_' (previously and incorrectly checked 'ghp_')
_HF_KEY_RE     = re.compile(r"^hf_[A-Za-z0-9]{10,}$")

# Technique code pattern (e.g., T1056, T1056.004)
_TECH_RE = re.compile(r"\bT\d{4}(?:\.\d{3})?\b", re.IGNORECASE)

# MITRE Enterprise tactic names -> TA codes
_TACTIC_TO_TA = {
    "reconnaissance":        "TA0043",
    "resource development":  "TA0042",
    "initial access":        "TA0001",
    "execution":             "TA0002",
    "persistence":           "TA0003",
    "privilege escalation":  "TA0004",
    "defense evasion":       "TA0005",
    "credential access":     "TA0006",
    "discovery":             "TA0007",
    "lateral movement":      "TA0008",
    "collection":            "TA0009",
    "exfiltration":          "TA0010",
    "command and control":   "TA0011",
    "impact":                "TA0040",
}


def _tactic_regex(name: str) -> re.Pattern:
    parts = [re.escape(p) for p in name.split()]
    between = r"[\s\-/]+"  # allow spaces/hyphens/slashes
    return re.compile(r"\b" + between.join(parts) + r"\b", re.IGNORECASE)

_TACTIC_PATTERNS = {name: _tactic_regex(name) for name in _TACTIC_TO_TA}


def _dedup_preserve_order(items):
    s, out = set(), []
    for x in items:
        if x not in s:
            s.add(x)
            out.append(x)
    return out


def _truthy(v: Any) -> bool:
    return str(v).strip().lower() in {"1", "true", "yes", "y"}


# ----------------------------- adapter -------------------------------
class TTPLLMAdapter(BaseAdapter):
    """
    Adapter for the upstream TTP-LLM (decoder-only, prompt-only) flow.
    """

    image     = "ttp-workbench:ttp-llm"
    tool_path = "/opt/TTP-LLM/main.py"

    def __init__(
        self,
        *,
        type: str = "decoder_only",         # TTP-LLM uses decoder-only for GPT family
        llm: str = "gpt-3.5-turbo",
        mode: str = "prompt_only",
        config_path: str | Path = "config.ini",
        verbose: bool = False,
        max_chars: int = 30000,              # window size before we split into chunks
        overlap_chars: int = 2000,           # overlap between windows
        bulk_batch_size: int = 100,         # input records per native invocation
        **kw,
    ):
        self._type = type
        self._llm = llm
        self._mode = mode
        self.verbose = verbose
        self.max_chars = max_chars
        self.overlap_chars = overlap_chars
        if not isinstance(bulk_batch_size, int) or isinstance(bulk_batch_size, bool) or bulk_batch_size <= 0:
            raise ValueError("bulk_batch_size must be a positive integer")
        self.bulk_batch_size = bulk_batch_size

        self.flags = ["--type", type, "--llm", llm, "--mode", mode]
        self._warn_if_unsupported(type, llm, mode)

        cfg_candidate = Path(config_path).expanduser()
        if cfg_candidate.is_absolute():
            self._config_path = cfg_candidate
        else:
            cwd_cfg = (Path.cwd() / cfg_candidate).resolve()
            project_cfg = (Path(__file__).resolve().parents[2] / cfg_candidate).resolve()
            self._config_path = cwd_cfg if cwd_cfg.exists() else project_cfg

        self._config_snippet: str | None = None  # filled by ensure_config()

        super().__init__(verbose=verbose, **kw)

        self.ensure_config()


    # -------------------------- BaseAdapter hook ----------------------
    def build_command(self, in_cn: str, out_cn: str) -> List[str]:
        """
          1) ensures config.ini once per session,
          2) creates data/MITRE_Procedures.csv with one or many rows (chunked with overlap),
          3) runs main.py (prompt_only),
          4) runs postprocess.py on the produced CSV to create the *_encoded.csv,
          5) converts the encoded CSV to JSON for BaseAdapter to consume (raw = all rows).
        """
        cfg = self._config_snippet or ""
        preds_csv   = f"/opt/TTP-LLM/results/preds_{self._llm}_{self._mode}.csv"
        encd_csv    = f"/opt/TTP-LLM/results/preds_{self._llm}_{self._mode}_encoded.csv"
        chunk = int(self.max_chars)
        overlap = int(self.overlap_chars)
        if overlap >= chunk:
            overlap = max(0, chunk // 10)

        # Single script with redirection; verbosity only changes how much we tail at the end
        script = f"""
set -Eeuo pipefail

# 1) ensure config.ini inside the container (first exec per session)
{cfg}

/opt/venv/bin/python - <<'PY'
import configparser
from pathlib import Path

p = Path("/opt/TTP-LLM/config.ini")
cp = configparser.ConfigParser()
cp.read(p)

print("[ttp-llm-debug] config exists:", p.exists(), file=__import__("sys").stderr)
print("[ttp-llm-debug] sections:", cp.sections(), file=__import__("sys").stderr)
print(
    "[ttp-llm-debug] has OpenAI_Key:",
    bool(cp.get("API", "OpenAI_Key", fallback="").strip()),
    file=__import__("sys").stderr,
)
print(
    "[ttp-llm-debug] has HuggingFace_Key:",
    bool(cp.get("API", "HuggingFace_Key", fallback="").strip()),
    file=__import__("sys").stderr,
)
PY

IN="{in_cn}"
OUT="{out_cn}"
DATA_DIR="/opt/TTP-LLM/data"
DATASET="$DATA_DIR/MITRE_Procedures.csv"
BACKUP="$DATA_DIR/tmp_MITRE_Procedures.csv"
PRED_CSV="{preds_csv}"
ENCD_CSV="{encd_csv}"

mkdir -p "$DATA_DIR" "/opt/TTP-LLM/results"

# Move the original dataset aside once (if present)
if [ -f "$DATASET" ] && [ ! -f "$BACKUP" ]; then
  mv "$DATASET" "$BACKUP"
fi

# 2) Write CSV with header 'Procedures' and one row per chunk with overlap
/opt/venv/bin/python - "$IN" "$DATASET" {chunk} {overlap} <<'PY'
import csv, sys
src, dst, chunk, overlap = sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4])
with open(src, 'r', encoding='utf-8', errors='ignore') as fin:
    text = fin.read().lstrip('\ufeff')

rows = []
if len(text) <= chunk:
    rows = [text]
else:
    step = max(1, chunk - overlap)
    for start in range(0, len(text), step):
        piece = text[start:start+chunk]
        if not piece:
            break
        rows.append(piece)

with open(dst, 'w', encoding='utf-8', newline='') as fout:
    w = csv.writer(fout, quoting=csv.QUOTE_ALL)
    w.writerow(['Procedures'])
    for r in rows:
        w.writerow([r])
PY

# 3) run main.py from repo root; capture stdout/err to avoid blocking parent pipes
cd /opt/TTP-LLM
: > /tmp/ttp-llm-main.out; : > /tmp/ttp-llm-main.err
if ! /opt/venv/bin/python -u ./main.py {' '.join(self.flags)} 1>>/tmp/ttp-llm-main.out 2>>/tmp/ttp-llm-main.err; then
  echo "[ERR] main.py failed; last 100 lines of stderr:" >&2
  tail -n 100 /tmp/ttp-llm-main.err >&2 || true
  exit 1
fi

# check predictions file
if [ ! -s "$PRED_CSV" ]; then
  echo "[ERR] predictions CSV not found: $PRED_CSV" >&2
  exit 1
fi

# 4) run the original postprocess.py to produce *_encoded.csv
: > /tmp/ttp-llm-post.out; : > /tmp/ttp-llm-post.err
if ! /opt/venv/bin/python -u ./decoder_only/postprocess.py --file_path "$PRED_CSV" 1>>/tmp/ttp-llm-post.out 2>>/tmp/ttp-llm-post.err; then
  echo "[ERR] postprocess.py failed; last 100 lines of stderr:" >&2
  tail -n 100 /tmp/ttp-llm-post.err >&2 || true
  exit 1
fi

# confirm encoded file
if [ ! -s "$ENCD_CSV" ]; then
  echo "[ERR] encoded CSV not found: $ENCD_CSV" >&2
  exit 1
fi

# 5) convert encoded CSV -> JSON list-of-records for BaseAdapter (raw keeps per-chunk rows)
/opt/venv/bin/python - "$ENCD_CSV" "$OUT" <<'PY'
import pandas as pd, json, sys
enc_csv, out = sys.argv[1], sys.argv[2]
df = pd.read_csv(enc_csv)
with open(out, 'w', encoding='utf-8') as f:
    json.dump(df.to_dict(orient='records'), f, ensure_ascii=False)
PY

# Emit a tiny tail if verbose; otherwise stay quiet
if {"true" if self.verbose else "false"}; then
  echo "[INFO] main.py tail:" >&2;  tail -n 20 /tmp/ttp-llm-main.out >&2 || true
  echo "[INFO] postprocess.py tail:" >&2; tail -n 20 /tmp/ttp-llm-post.out >&2 || true
  echo "[INFO] encoded head/tail:" >&2; head -n 2 "$ENCD_CSV" >&2 || true; tail -n 2 "$ENCD_CSV" >&2 || true
fi
"""
        return ["bash", "-lc", script]

    def build_bulk_command(self, in_dir_cn: str, out_dir_cn: str, manifest_cn: str | None) -> List[str]:
        """One native main/postprocess call per batch, preserving per-record chunk grouping."""
        if self._type != "decoder_only" or self._mode != "prompt_only":
            raise ValueError("TTP-LLM bulk execution supports decoder_only/prompt_only only")
        if manifest_cn is None:
            raise ValueError("TTP-LLM bulk execution requires an input/output manifest")
        if self.max_chars <= 0:
            raise ValueError("max_chars must be positive")
        spec = json.dumps({"input": in_dir_cn, "output": out_dir_cn, "manifest": manifest_cn,
                           "chunk": int(self.max_chars), "overlap": int(self.overlap_chars),
                           "flags": self.flags, "llm": self._llm, "mode": self._mode})
        script = "set -Eeuo pipefail\n" + (self._config_snippet or "") + "\n"
        script += "cd /opt/TTP-LLM\n/opt/venv/bin/python -u - " + shlex.quote(spec) + " <<'PY'\n"
        script += '''import csv, json, subprocess, sys
from pathlib import Path
import pandas as pd

spec = json.loads(sys.argv[1])
manifest = json.loads(Path(spec["manifest"]).read_text())
chunk, overlap = spec["chunk"], spec["overlap"]
if overlap >= chunk:
    overlap = max(0, chunk // 10)
rows, spans = [], []
for record in manifest:
    text = (Path(spec["input"]) / record["in"]).read_text(encoding="utf-8", errors="ignore").lstrip("\\ufeff")
    pieces = [text] if len(text) <= chunk else [
        text[start:start + chunk] for start in range(0, len(text), max(1, chunk - overlap))]
    spans.append((record["out"], len(rows), len(pieces)))
    rows.extend(pieces)

dataset = Path("data/MITRE_Procedures.csv")
dataset.parent.mkdir(exist_ok=True)
backup = dataset.with_name("tmp_MITRE_Procedures.csv")
if dataset.exists() and not backup.exists():
    dataset.rename(backup)
with dataset.open("w", encoding="utf-8", newline="") as stream:
    writer = csv.writer(stream, quoting=csv.QUOTE_ALL)
    writer.writerow(["Procedures"])
    writer.writerows([text] for text in rows)
Path("results").mkdir(exist_ok=True)
raw = Path("results") / ("preds_" + spec["llm"] + "_" + spec["mode"] + ".csv")
encoded = raw.with_name(raw.stem + "_encoded.csv")
# A fresh batch must not consume a stale output left by a no-op tool call.
raw.unlink(missing_ok=True)
encoded.unlink(missing_ok=True)
print(f"[TTP-LLM bulk] {len(manifest)} records / {len(rows)} procedure chunks", flush=True)
subprocess.run([sys.executable, "-u", "main.py", *spec["flags"]], check=True)
raw_count = len(pd.read_csv(raw))
if raw_count != len(rows):
    raise ValueError(f"Incomplete native bulk inference: expected {len(rows)} rows, got {raw_count}; rebuild the image if its loader still has the 20-row cap")
subprocess.run([sys.executable, "decoder_only/postprocess.py", "--file_path", str(raw)], check=True)
frame = pd.read_csv(encoded)
if len(frame) != len(rows):
    raise ValueError(f"Incomplete bulk postprocessing: expected {len(rows)} rows, got {len(frame)}")
# Emit only fully validated batches. Each output retains all chunks for that input;
# extract_ttps() applies the same intersection as the single-input path.
out = Path(spec["output"])
out.mkdir(parents=True, exist_ok=True)
for name, start, count in spans:
    (out / name).write_text(json.dumps(frame.iloc[start:start + count].to_dict(orient="records")), encoding="utf-8")
print(f"[TTP-LLM bulk] completed {len(manifest)} records", flush=True)
PY
'''
        return ["bash", "-lc", script]

    # -------------------------- parsing helpers ----------------------
    def extract_ttps(self, out_json_obj: Any) -> List[str]:
        """
        Parse T-codes and TA-codes from the postprocess *_encoded.csv (already loaded as JSON).
        If multiple rows exist (due to chunking), perform an AND (intersection) across rows.
        """
        # Collect per-row sets
        row_sets: List[set[str]] = []

        # If upstream gave us list-of-records, walk all rows
        if isinstance(out_json_obj, list) and out_json_obj and isinstance(out_json_obj[0], dict):
            for row in out_json_obj:
                codes: List[str] = []
                # 1) one-hot tactic columns (common in encoded files)
                for name, ta in _TACTIC_TO_TA.items():
                    if name in row and _truthy(row[name]):
                        codes.append(ta)
                # 2) textual fields that may contain tactic names or T-codes
                for k in ("Predicted_Tactic", "Predicted_Tactics", "tactic_keywords", "prediction", "response"):
                    if k in row and isinstance(row[k], str):
                        txt = row[k]
                        for m in _TECH_RE.finditer(txt):
                            codes.append(m.group(0).upper())
                        for nm, ta in _TACTIC_TO_TA.items():
                            if _TACTIC_PATTERNS[nm].search(txt):
                                codes.append(ta)
                row_sets.append(set(_dedup_preserve_order(codes)))
        else:
            # Fallback: treat as text
            text = str(out_json_obj)
            codes: List[str] = []
            for m in _TECH_RE.finditer(text):
                codes.append(m.group(0).upper())
            for nm, ta in _TACTIC_TO_TA.items():
                if _TACTIC_PATTERNS[nm].search(text):
                    codes.append(ta)
            row_sets.append(set(_dedup_preserve_order(codes)))

        if not row_sets:
            return []
        # AND across rows: keep only codes present in every chunk
        inter = set.intersection(*row_sets) if len(row_sets) > 1 else row_sets[0]
        # preserve the order from the first row while applying intersection
        first_order = [c for c in row_sets[0] if c in inter]
        return first_order

    # -------------------------- config handling ----------------------
    @staticmethod
    def _provider_for_llm(llm: str) -> str:
        return "openai" if "gpt" in llm.lower() else "hf"

    def _validate_config(self, text: str) -> None:
        cp = configparser.ConfigParser()
        try:
            cp.read_string(text)
        except Exception as e:
            raise ValueError(f"Failed to parse config.ini: {e}\n\nExpected:\n{_EXPECTED_TEMPLATE}") from e

        if not cp.has_section("API"):
            raise ValueError(f"config.ini is missing the [API] section.\n\nExpected:\n{_EXPECTED_TEMPLATE}")

        provider = self._provider_for_llm(self._llm)
        if provider == "openai":
            key = cp.get("API", "OpenAI_Key", fallback="").strip()
            if not key:
                raise ValueError("[API].OpenAI_Key is missing or empty in config.ini.")
            if not _OPENAI_KEY_RE.match(key):
                raise ValueError("[API].OpenAI_Key doesn't look like an OpenAI key (should start with 'sk-').")
        else:
            key = cp.get("API", "HuggingFace_Key", fallback="").strip()
            if not key:
                raise ValueError("[API].HuggingFace_Key is missing or empty in config.ini.")
            if not _HF_KEY_RE.match(key):
                raise ValueError("[API].HuggingFace_Key doesn't look like a HF token (should start with 'hf_').")

    @staticmethod
    def _normalize_text_file(path: Path) -> str:
        raw = path.read_text(encoding="utf-8-sig")
        clean = raw.replace("\r\n", "\n").lstrip("\ufeff")
        if not clean.endswith("\n"):
            clean += "\n"
        if clean != raw:
            path.write_text(clean, encoding="utf-8")
        return clean

    def ensure_config(self) -> None:
        if self._config_snippet is not None:
            return

        p = self._config_path
        if not p.is_file():
            raise FileNotFoundError(
                f"config.ini not found at {p}\n\nExpected format:\n{_EXPECTED_TEMPLATE}"
            )

        clean = self._normalize_text_file(p)
        self._validate_config(clean)

        cp = configparser.ConfigParser()
        cp.read_string(clean)

        openai_key = cp.get("API", "OpenAI_Key", fallback="").strip()
        hf_key = cp.get("API", "HuggingFace_Key", fallback="").strip()

        # Store keys as per-exec environment variables. BaseAdapter passes
        # self.env into DockerSession.exec(), so the shell snippet below can
        # write /opt/TTP-LLM/config.ini inside the running container without
        # embedding secrets directly in the command string.
        merged_env = dict(self.env or {})
        merged_env.update({
            "TTP_LLM_OPENAI_KEY": openai_key,
            "TTP_LLM_HUGGINGFACE_KEY": hf_key,
        })
        for name in ("TTPWB_OPENAI_MAX_RETRIES", "TTPWB_OPENAI_MAX_TOTAL_RETRIES"):
            if name in os.environ:
                merged_env[name] = os.environ[name]
        self.env = merged_env

        # Write /opt/TTP-LLM/config.ini inside the container using environment
        # variables. The command contains only variable names, not secret values.
        self._config_snippet = r'''
/opt/venv/bin/python - <<'PY'
import os
from pathlib import Path

cfg = (
    "[API]\n"
    f"OpenAI_Key = {os.environ.get('TTP_LLM_OPENAI_KEY', '')}\n"
    f"HuggingFace_Key = {os.environ.get('TTP_LLM_HUGGINGFACE_KEY', '')}\n"
)

p = Path("/opt/TTP-LLM/config.ini")
p.write_text(cfg, encoding="utf-8")
p.chmod(0o600)
PY
'''


    def _warn_if_unsupported(self, t: str, llm: str, mode: str) -> None:
        if t.lower() != "decoder_only":
            warnings.warn(
                f"[WARNING] TTP-LLM only supports type='decoder_only' (got '{t}'). "
                "main.py may no-op or exit early.",
                RuntimeWarning,
                stacklevel=2,
            )
        if not re.search(r"(gpt|llama)", llm, re.IGNORECASE):
            warnings.warn(
                f"[WARNING] llm='{llm}' does not look like a supported model family "
                "main.py may no-op or exit early.",
                RuntimeWarning,
                stacklevel=2,
            )
        if mode.lower() not in _SUPPORTED_MODES:     # ← correct check
            warnings.warn(
                f"[WARNING] mode={mode} is not supported for input data.\n"
                f"Supported modes are {sorted(_SUPPORTED_MODES)}",
                RuntimeWarning, stacklevel=2,
            )

    def predict(
        self,
        texts,
        *,
        ids=None,
        save_dir=None,
        prefix: str = "ttp-llm",
        bulk: bool = False,
        resume: bool = False,
    ):
        self.ensure_config()
        if not bulk:
            return super().predict(texts, ids=ids, save_dir=save_dir, prefix=prefix, bulk=False)
        texts = list(texts)
        ids = list(ids) if ids is not None else list(range(len(texts)))
        if len(ids) != len(texts) or any(key is None for key in ids) or len(set(ids)) != len(ids):
            raise ValueError("Bulk input IDs must be unique, non-null and match the text count")
        if self._type != "decoder_only" or self._mode != "prompt_only":
            raise ValueError("TTP-LLM bulk execution supports decoder_only/prompt_only only")
        results, files = [], []
        for start in range(0, len(texts), self.bulk_batch_size):
            batch_ids = ids[start:start + self.bulk_batch_size]
            if resume:
                cached = self._load_cached_batch(save_dir, batch_ids)
                if cached is not None:
                    batch, paths = cached
                    results.extend(batch)
                    files.extend(paths)
                    print(
                        f"[TTP-LLM bulk] resumed {len(results)}/{len(texts)} records "
                        f"from validated raw outputs",
                        flush=True,
                    )
                    continue
            print(f"[TTP-LLM bulk] starting records {start + 1}-{start + len(batch_ids)}/{len(texts)}", flush=True)
            batch_started = time.monotonic()
            batch, paths = super().predict(texts[start:start + self.bulk_batch_size], ids=batch_ids,
                                            save_dir=save_dir, prefix=prefix, bulk=True)
            by_id = {row["id"]: (row, path) for row, path in zip(batch, paths)}
            if len(by_id) != len(batch_ids) or set(by_id) != set(batch_ids):
                raise RuntimeError("Bulk adapter output IDs do not match the requested batch")
            for key in batch_ids:
                row, path = by_id[key]
                results.append(row)
                files.append(path)
            if any(row.get("error") or row.get("ttps") is None for row in batch):
                # Preserve completed batches and stop making paid calls after a failed batch.
                for key in ids[start + len(batch_ids):]:
                    results.append({"id": key, "ttps": None, "sentences": None,
                                    "error": "Not attempted after an earlier bulk batch failed"})
                    files.append(None)
                break
            elapsed = time.monotonic() - batch_started
            print(f"[TTP-LLM bulk] saved {len(results)}/{len(texts)} records "
                  f"({elapsed:.1f}s for batch; {elapsed / len(batch_ids):.2f}s/record)", flush=True)
        return results, files

    def _load_cached_batch(self, save_dir, batch_ids):
        """Return a complete validated cached batch, or ``None`` to rerun it."""
        if save_dir is None:
            return None
        root = Path(save_dir)
        cached, paths = [], []
        for key in batch_ids:
            candidates = [root / f"{key}.json"]
            # BaseAdapter historically used the manifest filename when id 0
            # was treated as falsy. Accept that one legacy checkpoint name.
            if key == 0:
                candidates.extend(sorted(root.glob("*__0.json")))
            path = next((candidate for candidate in candidates if candidate.is_file()), None)
            if path is None:
                return None
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(raw, list) or not raw or not all(isinstance(row, dict) for row in raw):
                    return None
                cached.append({"id": key, "ttps": self.extract_ttps(raw), "sentences": []})
                paths.append(path)
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                return None
        return cached, paths


def predict_texts(
    texts: List[str],
    ids: List[str] | None = None,
    *,
    save_dir: str | Path | None = None,
    type: str = "decoder_only",
    llm: str = "gpt-3.5-turbo",
    mode: str = "prompt_only",
    config_path: str | Path = "config.ini",
    verbose: bool = False,
    max_chars: int = 30000,
    overlap_chars: int = 2000,
    bulk: bool = False,
    bulk_batch_size: int = 100,
    resume: bool = False,
    **kw,
) -> Tuple[List[Dict[str, Any]], List[Path | None]]:
    """
    One-call helper used by the framework.
      • Long inputs are split into overlapping windows *inside the container* (max_chars/overlap_chars).
      • Ensures config.ini exists *once* per adapter instance.
      • Delegates to BaseAdapter.predict() which will write per-sample raw JSON and return parsed/paths.
      • Raw = all encoded rows (per window). Parsed = AND across windows.
    """
    # Do not truncate here; chunking happens in build_command().
    safe = texts
    adapter = TTPLLMAdapter(
        type=type, llm=llm, mode=mode,
        config_path=config_path, verbose=verbose,
        max_chars=max_chars, overlap_chars=overlap_chars, bulk_batch_size=bulk_batch_size, **kw,
    )
    adapter.ensure_config()
    return adapter.predict(
        safe,
        ids=ids,
        save_dir=save_dir,
        prefix="ttp-llm",
        bulk=bulk,
        resume=resume,
    )

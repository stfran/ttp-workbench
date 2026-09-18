#!/usr/bin/env python3
"""Repeatable, narrowly scoped transformations transcribed from Docker_Setup."""
from pathlib import Path
import os
import shutil
import runpy

SETUP = Path(__file__).resolve().parent
SUITE = SETUP.parent
PROJECT = SUITE.parents[1]
ROOT = Path(os.environ.get("REPRO_EXTERNAL_ROOT", SUITE / ".runtime/external_tools")).resolve()

def replace(path, before, after):
    text = path.read_text(encoding="utf-8-sig")
    if after in text: return
    if before not in text: raise RuntimeError("Patch target not found: " + str(path))
    backup = path.with_suffix(path.suffix + ".ae-original")
    if not backup.exists(): shutil.copy2(path, backup)
    path.write_text(text.replace(before, after, 1))

replace(ROOT / "TTPDrill/relation_miner.py", "        parsed = []", "        if isinstance(output, str):\n            output = json.loads(output)\n\n        parsed = []")
replace(ROOT / "TTP-LLM/decoder_only/postprocess.py", "    df = pd.read_csv(args.file_path)", "    df = pd.read_csv(args.file_path)\n    df.columns = ['result']")
# Match the historical reproduction's full-input loader; the AE runner selects scope.
replace(ROOT / "TTP-LLM/decoder_only/prompt_only.py",
        "    df = pd.read_csv(csv_file)[:20]\n    for procedure",
        "    df = pd.read_csv(csv_file)\n    for procedure")
ttp_llm_patcher = runpy.run_path(str(PROJECT / "Docker_Setup/TTP-LLM/patch_prompt_only.py"))
ttp_llm_patcher["patch_prompt_only"](ROOT / "TTP-LLM/decoder_only/prompt_only.py")
# RAF-AG imports PyMuPDF as ``fitz``. The upstream pin names an unrelated
# neuroimaging package that is unavailable on current indexes.
raf_requirements = ROOT / "RAF-AG/requirements.txt"
if raf_requirements.exists():
    replace(raf_requirements, "fitz==0.0.1.dev2", "pymupdf")
ladder = ROOT / "LADDER/attack_pattern"
shutil.copy2(PROJECT / "Docker_Setup/LADDER/requirements_updated.txt", ladder / "requirements.txt")
models = (ladder / "models.py").read_text()
lines = models.splitlines(keepends=True)
if "from transformers import (" not in models:
    shutil.copy2(ladder / "models.py", ladder / "models.py.ae-original")
    lines[2:4] = ["from transformers import (BertModel, BertPreTrainedModel, RobertaModel, RobertaConfig)\n"]
    (ladder / "models.py").write_text("".join(lines))
dataset = (ladder / "dataset.py").read_text()
for line in ("import spacy", "from torchtext.vocab import vocab"):
    if "# " + line not in dataset: dataset = dataset.replace(line, "# " + line)
(ladder / "dataset.py").write_text(dataset)
# Use the exact checkpoint transformation maintained in the Dockerfile.
dockerfile = (PROJECT / "Docker_Setup/LADDER/Dockerfile").read_text()
block = dockerfile.split(" && /opt/venv/bin/python - <<'PY'\n", 1)[1].split("\nPY", 1)[0]
if 'sd.pop("bert_layer.embeddings.position_ids"' not in (ladder / "inference.py").read_text():
    exec(compile(block.replace('/opt/LADDER/attack_pattern/inference.py', str(ladder / 'inference.py')), "Docker_Setup/LADDER/Dockerfile", "exec"))
builder = runpy.run_path(str(PROJECT / "Docker_Setup/LADDER/build_ladder_cli_from_notebook.py"))
builder["build_cli"](ROOT / "LADDER/notebooks/attack-pattern-extraction.ipynb", ladder / "ladder_attack_pattern_cli.py")
shutil.copy2(PROJECT / "Docker_Setup/TTPDrill/requirements.txt", ROOT / "TTPDrill/requirements.txt")
shutil.copy2(PROJECT / "Docker_Setup/TTP-LLM/requirements_updated.txt", ROOT / "TTP-LLM/requirements_updated.txt")
shutil.copy2(PROJECT / "Docker_Setup/Buchel/buchel_cli.py", ROOT / "Buchel/generation/buchel_cli.py")
# Mirror the production image's context default without modifying the native Python client.
replace(ROOT / "Buchel/generation/Dockerfile.ollama",
        'ENTRYPOINT ["/usr/local/bin/pull_and_serve.sh"]',
        'ENTRYPOINT ["/usr/local/bin/pull_and_serve.sh"]\n\n# GTE-Qwen2 model-card input limit; do not truncate CTI segments.\nENV OLLAMA_CONTEXT_LENGTH=32768')
req = ROOT / "Buchel/generation/requirements.txt"
text = req.read_text()
for requirement in ("rich>=13", "peft==0.17.1"):
    if requirement not in text: text += "\n" + requirement + "\n"
req.write_text(text)
replace(ROOT / "Buchel/generation/supervised_finetuning.py",
        'os.environ["HF_HOME"] = "/tmp/huggingface/"',
        'os.environ.setdefault("HF_HOME", "/tmp/huggingface/")')
# Table 13 now stages pristine ext_tools.zip per run; do not patch its shared sources.
print("Documented patches applied. TTPDrill uses a conditional JSON conversion because pycorenlp can also return a dict.")

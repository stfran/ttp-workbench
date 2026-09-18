# adapters/ttpdrill_adapter.py
from __future__ import annotations
from typing import List, Dict, Any, Tuple
from pathlib import Path
import textwrap

from Framework.adapters.base_adapter import BaseAdapter
from Framework.utils.rcatt_ttp_map import CODE_TACTICS, NAME_TACTICS

# tactic name -> code (aligned by index, per your note)
NAME2CODE = {name: code for name, code in zip(NAME_TACTICS, CODE_TACTICS)}


class TTPDrillAdapter(BaseAdapter):
    """
    Runs the TTPDrill container, which expects its input at /opt/TTPDrill/input.txt
    and prints results to stdout with 'Mapped:' blocks. We capture stdout and convert
    it to JSON (inside the container) so BaseAdapter can pull a file.
    """
    image = "ttp-workbench:ttpdrill"

    def __init__(self, *, verbose: bool = False, **kw):
        super().__init__(verbose=verbose, **kw)

    # ---- container command -------------------------------------------------
    def build_command(self, in_cn: str, out_cn: str) -> list[str]:
        bash = textwrap.dedent(f"""\
            set -eo pipefail
            # 1) Start CoreNLP if not already listening
            if ! curl -s http://localhost:9000 >/dev/null 2>&1 ; then
              cd "$CORENLP_HOME"
              nohup java -mx4g -cp "*" edu.stanford.nlp.pipeline.StanfordCoreNLPServer \
                     -port 9000 -timeout 15000 -threads 4 \
                     > /opt/corenlp.log 2>&1 &
              echo "Waiting for CoreNLP on :9000..."
              for i in $(seq 1 60); do  # ~120s max
                if curl -s http://localhost:9000 >/dev/null 2>&1; then
                  echo "CoreNLP is up."
                  break
                fi
                sleep 2
              done
              # fail fast if still down
              curl -s http://localhost:9000 >/dev/null 2>&1
            fi

            # 2) feed input to the path TTPDrill expects
            cp {in_cn} /opt/TTPDrill/input.txt

            # 3) run TTPDrill, capture stdout to a file (so we can parse it)
            # main.py uses relative paths
            cd /opt/TTPDrill 
            if ! /opt/venv/bin/python -u /opt/TTPDrill/main.py 2>&1 | tee /tmp/ttp.stdout ; then
              echo '[adapter] TTPDrill crashed; dumping last 200 lines of /tmp/ttp.stdout:' 1>&2
              tail -n 200 /tmp/ttp.stdout 1>&2 || true
              exit 1
            fi

            # 4) parse stdout -> JSON with the blocks we need
            /opt/venv/bin/python - {out_cn} <<'PY'
import sys, json, re, ast
out_path = sys.argv[1]
with open("/tmp/ttp.stdout", "r", encoding="utf-8", errors="replace") as fh:
    text = fh.read()

#pat = re.compile(r"Mapped:\\s*?\\n(?:\\s*\\n)*?(\\[.*?\\])(?=\\nText:|\\Z)", re.S)
pat = re.compile(r"Mapped:\\s*?\\n(?:\\s*\\n)*?(\\[.*?\\])(?=\\s*(?:Text:|\\Z))", re.S)
blocks = []
for m in pat.finditer(text):
    raw = m.group(1).strip()
    try:
        blocks.append(ast.literal_eval(raw))
    except Exception:
        # skip malformed blocks
        pass

payload = {{"stdout": text, "blocks": blocks}}
with open(out_path, "w", encoding="utf-8") as out:
    json.dump(payload, out, ensure_ascii=False)
PY
        """)
        return ["bash", "-lc", bash]


    # ---- post-processing ---------------------------------------------------
    def parse_sentences(self, d: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Returns a list of {sentence, techs, tactics} aligned at the sentence level.
        """
        out: List[Dict[str, Any]] = []
        for block in d.get("blocks", []):
            if not isinstance(block, list) or not block:
                continue
            # All dicts in a block correspond to the same sentence
            sentence = block[0].get("original_sentence", "")
            ttps = set()
            for item in block:
                tid = (item.get("techId", {}) or {}).get("data")
                if tid:
                    ttps.add(tid.upper())
                tact_name = (item.get("tactic", {}) or {}).get("data")
                if tact_name and tact_name in NAME2CODE:
                    ttps.add(NAME2CODE[tact_name])
            if sentence and ttps:
                sorted_ttps = sorted(ttps)
                out.append({
                    "text": sentence,
                    "ttps": [{'code':t} for t in ttps],
                })
        return out

    def extract_ttps(self, d: Dict[str, Any]) -> List[str]:
        """
        Aggregates technique IDs AND tactic codes across the entire doc.
        """
        ttps = set()
        for block in d.get("blocks", []):
            if not isinstance(block, list):
                continue
            for item in block:
                tid = (item.get("techId", {}) or {}).get("data")
                if tid:
                    ttps.add(tid.upper())
                tact_name = (item.get("tactic", {}) or {}).get("data")
                if tact_name and tact_name in NAME2CODE:
                    ttps.add(NAME2CODE[tact_name])
        return sorted(ttps)


def predict_texts(texts: List[str],
                  ids: List[str] | None = None,
                  *, save_dir: str | Path | None = None,
                  **kw) -> Tuple[List[Dict[str, Any]], List[Path | None]]:
    """
    texts:   list of raw report strings
    ids:     optional stable IDs to carry through
    save_dir: if provided, per-text JSONs are persisted there for debugging
    """
    return TTPDrillAdapter(**kw).predict(texts, ids=ids, save_dir=save_dir, prefix="ttpdrill")


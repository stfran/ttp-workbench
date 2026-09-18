from __future__ import annotations
from typing import List, Dict, Any, Tuple
from pathlib import Path

from Framework.adapters.base_adapter import BaseAdapter


class TRAMAdapter(BaseAdapter):
    image     = "ttp-workbench:tram"
    tool_path = "/opt/TRAM/predict_multi_label.py"
    workdir   = "/opt/TRAM"

    def __init__(self, *, n: int = 13, stride: int = 5, thr: float = 0.5,
                 verbose: bool = False, device: str = "cuda", **kw):
        self.n = n
        self.stride = stride
        self.thr = thr
        self.verbose = verbose
        self.flags = ["--n", str(n), "--stride", str(stride), "--thr", str(thr)]
        if verbose:
            self.flags += ["--verbose"]
        kw.setdefault("use_gpus", device in ("cuda", "auto"))
        super().__init__(verbose=verbose, **kw)

    def build_command(self, in_cn: str, out_cn: str):
        return [
            "/opt/venv/bin/python", "-u", self.tool_path,
            "--textfile", in_cn,                            
            "--outfile", out_cn,
            *self.flags
        ]

    def build_bulk_command(self, in_dir_cn: str, out_dir_cn: str, manifest_cn: str | None):
        loop = (
            "set -euo pipefail\n"
            f'mkdir -p "{out_dir_cn}"\n'
            f'/opt/venv/bin/python -u "{self.tool_path}" '
            f'--dirin "{in_dir_cn}" --dirout "{out_dir_cn}" '
            f'{" ".join(self.flags)}\n'
            'shopt -s nullglob\n'
            f'for f in "{out_dir_cn}"/*_predictions.json; do\n'
            '  mv "$f" "${f%_predictions.json}.json"\n'  
            'done\n'
        )
        cmd = ["/bin/bash", "-lc", loop]
        if self.verbose:
            print("[INFO] Bulk command:\n", "\n".join(cmd[2].splitlines()))
        return cmd

    def parse_sentences(self, d: Dict[str, Any]) -> List[Dict[str, Any]]:
        if not isinstance(d, list):
            return []
        out = []
        for it in d:
            if not isinstance(it, dict):
                continue
            preds = it.get("predictions") or []
            if preds:
                out.append({"text": it.get("text"), "ttps": preds})
        return out

    def extract_ttps(self, d: Dict[str, Any]) -> List[str]:
        if not isinstance(d, list):
            return []
        seen = set()
        for it in d:
            for p in (it.get("predictions") or []):
                code = p.get("code")
                if code:
                    seen.add(code)
        return sorted(seen)


def predict_texts(texts: List[str],
                  ids: List[str] | None = None,
                  *, save_dir: str | Path | None = None,
                  bulk: bool = False,
                  **kw) -> Tuple[List[Dict[str, Any]], List[Path | None]]:
    return TRAMAdapter(**kw).predict(texts, ids=ids, save_dir=save_dir, prefix="tram", bulk=bulk)


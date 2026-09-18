from __future__ import annotations
from typing import List, Dict, Any, Tuple
from pathlib import Path

from Framework.adapters.base_adapter import BaseAdapter


class SeqMaskAdapter(BaseAdapter):
    image     = "ttp-workbench:seqmask"
    tool_path = "/opt/SeqMask/seqmask_cli.py"
    workdir   = "/opt/SeqMask"

    def __init__(self, *,
                 mode: str = "workbench",   # in workbench mode, we sentence tokenize and decide on per-sentence labels
                                            # in reproduction mode, we pass the whole text as-is and get the probability for each label
                 top_k: int = 3,            # in workbench mode, accept all labels over tau among the top-k labels per sentence
                 tau: float = 0.5,
                 tact_model: str = "ar_mask", # we can also specify "self_attention", "sv_mask", "mp_mask", "ar_mask"
                 tech_model: str = "ar_mask",
                 verbose: bool = False, **kw):
        self.mode = mode
        self.top_k = top_k
        self.tau = tau
        self.tact_model = tact_model
        self.tech_model = tech_model

        super().__init__(verbose=verbose, **kw)


    def build_command(self, in_cn: str, out_cn: str):
        return [
            "/opt/venv/bin/python", "-u", self.tool_path,
            "--input_file", in_cn,                            
            "--output_file", out_cn,
            "--mode", self.mode,
            "--top_k", str(self.top_k),
            "--tau", str(self.tau),
            "--tact_model", self.tact_model,
            "--tech_model", self.tech_model,
        ]

    def build_bulk_command(self, in_dir_cn: str, out_dir_cn: str, manifest_cn: str | None):
        return [
            "/opt/venv/bin/python", "-u", self.tool_path,
            "--input_dir", in_dir_cn,                         
            "--output_dir", out_dir_cn,
            "--mode", self.mode,
            "--top_k", str(self.top_k),
            "--tau", str(self.tau),
            "--tact_model", self.tact_model,
            "--tech_model", self.tech_model,
        ]

    # parsing ------------------------------------------------------------
    def parse_sentences(self, d: Dict[str, Any]) -> List[Dict[str, Any]]:
        if self.mode != "workbench":
            return []

        sentences = []
        for sent_info in d.get("per_sentence", []):
            text = sent_info.get("text", "")
            ttps = []
            for t in sent_info.get("accepted_tactics", []):
                code = t
                proba = sent_info.get("total_tactics", {}).get(t, None)
                ttps.append({"code": code, "probability": proba})
            for t in sent_info.get("accepted_techniques", []):
                code = t
                proba = sent_info.get("total_techniques", {}).get(t, None)
                ttps.append({"code": code, "probability": proba})
            if ttps != []: # don't add negative predictions to output
                sentences.append({
                    "text": text,
                    "ttps": ttps,
                })
        return sentences

    def extract_ttps(self, d: Dict[str, Any]) -> List[str]:
        if d.get("error"):
            raise RuntimeError(d["error"])
        if self.mode == "reproduction":
            # keep all predicted codes and their associated probabilities
            tactics = d.get("total_tactics") or {}
            techniques = d.get("total_techniques") or {}
            return {**tactics, **techniques} # in reproduction mode, we keep all predicted codes so we can evaluate the probabilities and their rankings
        # when we're in workbench mode, the cli provides the accepted tactics/techniques
        tactics = d.get("accepted_tactics") or []
        techniques = d.get("accepted_techniques") or []
        return tactics + techniques


def predict_texts(texts: List[str],
                  ids: List[str] | None = None,
                  *, save_dir: str | Path | None = None,
                  mode: str = "workbench",
                  top_k: int = 3,
                  tau: float = 0.5,
                  tact_model: str = "ar_mask",
                  tech_model: str = "ar_mask",
                  bulk: bool = False,
                  **kw) -> Tuple[List[Dict[str, Any]], List[Path | None]]:
    adapter = SeqMaskAdapter(mode=mode, top_k=top_k, tau=tau, tact_model=tact_model, tech_model=tech_model, **kw)
    return adapter.predict(texts, ids=ids, save_dir=save_dir, prefix="seqmask", bulk=bulk)

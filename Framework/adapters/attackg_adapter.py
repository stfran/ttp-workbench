# adapters/AttacKG_adapter.py
from __future__ import annotations
from typing import List, Dict, Any, Tuple
from pathlib import Path


from Framework.adapters.base_adapter import BaseAdapter         


class AttacKGAdapter(BaseAdapter):
    image = "ttp-workbench:attackg"
    tool_path = "/opt/AttacKG/main.py"
    workdir = "/opt/AttacKG"

    def __init__(self, *, verbose: bool = False, image: str | None = None, **kw):

        if image is None and "image" in kw:
            image = kw.pop("image")

        if image is not None:
            self.image = image
        super().__init__(verbose=verbose, **kw)

    def build_command(self, in_cn: str, out_cn: str) -> List[str]:
        """
        AttacKG writes: <O>_techniques.json
        We run AttacKG with O=<out_cn without .json>, then mv the produced
        file to `out_cn` so the BaseAdapter can cp_from it.
        """
        # strip a trailing ".json" if present for the -O value
        base_out = out_cn[:-5] if out_cn.endswith(".json") else out_cn

        cmd = (
            f"/opt/venv/bin/python -u {self.tool_path} "
            f"-M techniqueIdentification "
            f"-T ./templates "
            f"-R {in_cn} "
            f"-O {base_out} "
            f"&& mv {base_out}_techniques.json {out_cn}"
        )

        return ["bash", "-lc", cmd]

    def build_bulk_command(self, in_dir_cn: str, out_dir_cn: str,
                           manifest_cn: str | None) -> List[str]:
        command = [
            "/opt/venv/bin/python", "-u", "/opt/AttacKG/attackg_bulk.py",
            "--input-dir", in_dir_cn,
            "--output-dir", out_dir_cn,
            "--template-path", "./templates",
            "--model-path", "./new_cti.model",
        ]
        if manifest_cn:
            command.extend(["--manifest", manifest_cn])
        return command
    

    def parse_sentences(self, d: Dict[str, Any]) -> List[Dict[str, Any]]:
        return d.get("sentences", [])

    def extract_ttps(self, d: Dict[str, Any]) -> List[str]:
        """
        Top-level keys are MITRE technique IDs.
        """
        #if isinstance(d, dict):
        #    return sorted(d.keys())
        #return []
        if d.get("error"):
            raise RuntimeError(d["error"])
        return list(d.keys())


def predict_texts(texts: List[str],
                  ids: List[str] | None = None,
                  *,
                  save_dir: str | Path | None = None,
                  bulk: bool = False,
                  **kw) -> Tuple[List[Dict[str, Any]], List[Path | None]]:
    return AttacKGAdapter(**kw).predict(texts, ids=ids, save_dir=save_dir,
                                        prefix="attackg", bulk=bulk)

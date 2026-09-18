# adapters/rcATT_adapter.py


from __future__ import annotations
from typing import List, Dict, Any, Tuple, Optional
from pathlib import Path

from Framework.adapters.base_adapter import BaseAdapter
from Framework.utils.rcatt_ttp_map import ALL_TTPS, STIX_IDENTIFIERS


class RcATTAdapter(BaseAdapter):
    image = "ttp-workbench:rcatt"
    tool_path = "/opt/rcATT/rcATT_cmd.py"
    work_dir = "/opt/rcATT"

    def __init__(self, *, verbose: bool = False, **kw):
        super().__init__(verbose=verbose, **kw)

    def build_command(self, in_cn: str, out_cn: str) -> List[str]:
        # -p: predict, -i: input txt, -o: output json
        cmd = [
            "/bin/bash", "-c",
            f"cd {self.work_dir} && /opt/venv/bin/python -u {self.tool_path} -p -i {in_cn} -o {out_cn}"
        ]
        return cmd

    def build_bulk_command(self, in_dir_cn: str, out_dir_cn: str, manifest_cn: str | None):
        import shlex
        args = ["/opt/venv/bin/python", "-u", "rcatt_bulk.py", "--input_dir", in_dir_cn, "--output_dir", out_dir_cn]
        return ["/bin/bash", "-c", "cd " + shlex.quote(self.work_dir) + " && " + shlex.join(args)]

    # rcATT does not return sentence-level data    

    def extract_ttps(self, d: Dict[str, Any]) -> List[str]:
        if d.get("error"):
            raise RuntimeError(d["error"])
        # Map STIX object_refs back to ATT&CK IDs
        object_refs: List[str] = d.get("object_refs", [])
        codes: List[str] = []
        for ref in object_refs:
            if ref in STIX_IDENTIFIERS:
                idx = STIX_IDENTIFIERS.index(ref)
                codes.append(ALL_TTPS[idx])
        return sorted(set(codes))

def predict_texts(texts: List[str],
                  ids: Optional[List[str]] = None,
                  *, save_dir: str | Path | None = None,
                  bulk: bool = False,
                  **kwargs) -> Tuple[List[Dict[str, Any]], List[Path | None]]:

    return RcATTAdapter(**kwargs).predict(texts, ids=ids, save_dir=save_dir, prefix="rcatt", bulk=bulk)

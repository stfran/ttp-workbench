# adapters/ladder_adapter.py
from __future__ import annotations
from typing import List, Dict, Any, Tuple
from pathlib import Path


from Framework.adapters.base_adapter import BaseAdapter  


class LADDERAdapter(BaseAdapter):
    image = "ttp-workbench:ladder"
    tool_path = "/opt/LADDER/attack_pattern/ladder_attack_pattern_cli.py"
    workdir = "/opt/LADDER/attack_pattern"

    def __init__(self, *, verbose: bool = False, **kw):
        kw.setdefault("use_gpus", True) # these worked in tests
        kw.setdefault("gpus", "all")    
        kw.setdefault("bulk", True) 
        kw.setdefault("bulk_monitor", True)   

        super().__init__(verbose=verbose, **kw)

    def build_command(self, in_cn: str, out_cn: str) -> List[str]:
        """
        The LADDER CLI runs attack-pattern inference on a given file or directory and then outputs the results to a given file/directory
        """
        in_dir = "input_docs"
        out_dir = "output_docs"
        cmd = (
            "set -euo pipefail\n"
            f'rm -rf "{out_cn}"\n'
            f'mkdir -p "{in_dir}"\n'
            f'cp "{in_cn}" "{in_dir}/"\n' 
            f"/opt/venv/bin/python {self.tool_path} {in_dir} "
            f"--output {out_dir}\n"
            f'cp -r {out_dir}/* "{out_cn}"\n'
        )

        return ["bash", "-lc", cmd]
    
    def build_bulk_command(self, in_dir_cn: str, out_dir_cn: str, manifest_cn: str | None) -> List[str]:
        """Run LADDER once over a directory of input .txt files.

        Used by BaseAdapter.predict(bulk=True) so models load only once in-container.
        """
        in_dir = "input_docs" # they are copied into tmp, which complicates teh 
        cmd = (
            "set -euo pipefail\n"
            f'rm -rf "{out_dir_cn}"\n'
            f'mkdir -p "{in_dir}"\n'
            f'cp -r "{in_dir_cn}" "{in_dir}/"\n'
            f'mkdir -p "{out_dir_cn}"\n'
            f"/opt/venv/bin/python {self.tool_path} {in_dir_cn} "
            f"--output {out_dir_cn} "
        )

        return ["bash", "-lc", cmd]

    def parse_sentences(self, d: Dict[str, Any]) -> List[Dict[str, Any]]:
        out = []
        for ttp, value in d.items():
            out.append({"text": value[1], "ttps": [
                {"code": ttp, "cosine_distance": value[0]}]})
        return out

    def extract_ttps(self, d: Dict[str, Any]) -> List[str]:
        """
        Top-level keys are MITRE technique IDs.
        """
        return sorted(d.keys())
    
    # overload parent class's predict function and pass bulk=True as default
    # LADDER adapter is brittle to multiple files, so we pass bulk=True unless we know we're only passing one file
    def predict(self, texts: List[str], ids: List[str] | None = None, *,
                save_dir: str | Path | None = None,
                prefix: str = "ladder", bulk: bool = True, 
                **kw) -> Tuple[List[Dict[str, Any]], List[Path | None]]:
        
        return super().predict(texts, ids=ids, save_dir=save_dir, prefix=prefix, bulk=bulk)


def predict_texts(texts: List[str],
                  ids: List[str] | None = None,
                  *,
                  save_dir: str | Path | None = None,
                  bulk: bool = True, # these defaults worked in the last tests
                  **kw) -> Tuple[List[Dict[str, Any]], List[Path | None]]:

    return LADDERAdapter(**kw).predict(texts, ids=ids, save_dir=save_dir, prefix="ladder", bulk=bulk)

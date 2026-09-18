# adapters/regex_adapter.py
from __future__ import annotations
from pathlib import Path
from typing import Iterable, List, Dict, Any, Tuple, Optional
from datetime import datetime

from Framework.utils.attack_lookup import extract_ttps as regex_extract_ttps

from Framework.adapters.base_adapter import BaseAdapter, normalize_sentences


class RegexPredictor(BaseAdapter):
    """
    Regex-only pseudo-adapter.
    - No Docker
    - No sentence parsing
    - scans text with TTP_CODE_PATTERN and returns normalized codes
    """

    # compatibility with BaseAdapter interface; not used.
    image: str = "local/regex-only"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

    # -------------------------------------------------------------------
    # Overrides
    # -------------------------------------------------------------------
    def build_command(self, in_cn: str, out_cn: str) -> List[str]:
        raise NotImplementedError("RegexPredictor does not use a containerized tool.")

    def parse_sentences(self, d: Dict[str, Any]) -> List[Dict[str, Any]]:
        # No sentence-level parsing for the regex pseudo-adapter
        return []

    def extract_ttps(self, d: Dict[str, Any]) -> List[str]:
        # Not used in this adapter's flow (left for parity).
        # We perform extraction directly in predict() below.
        return d.get("ttps", [])


    def predict(
        self,
        texts: Iterable[str],
        *,
        ids: Optional[Iterable[str]] = None,
        save_dir: str | Path | None = None,
        prefix: str = "pred",
    ) -> Tuple[List[Dict[str, Any]], List[Path | None]]:

        # Regex is local; we ignore save_dir/files for this adapter.
        # We still return a files list of Nones to match the common signature.
        save_dir = Path(save_dir).expanduser() if save_dir else None
        if save_dir:
            save_dir.mkdir(parents=True, exist_ok=True)

        slightly_verbose = self.kwargs.get("slightly_verbose", False)
        verbose = self.kwargs.get("verbose", False)
        if verbose:
            slightly_verbose = True

        preds: List[Dict[str, Any]] = []
        files: List[Path | None] = []

        # If ids not provided, generate Nones to zip cleanly
        id_iter = ids if ids is not None else (None for _ in texts)

        # If texts is a sequence we can use len(); else fall back gracefully
        try:
            total = len(texts)  # type: ignore[arg-type]
        except Exception:
            total = 0

        for idx, (txt, rec_id) in enumerate(zip(texts, id_iter), 1):
            sentences = []
            if slightly_verbose:
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                print(f"[INFO] Running text #{idx}/{total or '?'} on regex at {timestamp}")

            ttps_set, sentences = regex_extract_ttps(txt, 
                                                     return_sent_list=True, 
                                                     #decouple=True, # follow the path to detect decoupled sub-technique parts
                                                     #require_name_hit=False, # for decoupling, later we could make this a threshold check among other heuristics
                                                     #name_fuzzy_min=0.8, # if we want to fine tune the name matching
                                                     #window_chars=50, # if we want to fine tune the window to search for dangling sub-techniuqe parts
                                                    ) 

            preds.append({
                "id": rec_id,
                "ttps": list(ttps_set),
                "error": None,
                "sentences": normalize_sentences(sentences),
            })
            files.append(None)

        return preds, files

def predict_texts(texts: List[str],
                  ids: List[str] | None = None,
                  *, save_dir: str | Path | None = None,
                  **kw) -> Tuple[List[Dict[str, Any]], List[Path | None]]:
    return RegexPredictor(**kw).predict(texts, ids=ids, save_dir=save_dir, prefix="regex")


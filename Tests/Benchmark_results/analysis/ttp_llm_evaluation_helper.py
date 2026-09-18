#ttp_llm_evaluation_helper.py
import re
import difflib
from typing import List, Tuple, Dict, Set
import Framework.utils.attack_lookup as al


def _slice_window(text: str, center_span: Tuple[int, int], window_chars: int) -> str:
    a, b = center_span
    a = max(0, a); b = min(len(text), b)
    left = max(0, a - window_chars)
    right = min(len(text), b + window_chars)
    return text[left:right]


def _normalize_text_for_match(s: str) -> str:
    """
    Normalize text for tactic-name matching:
      - lowercase
      - replace '&' and '/' with 'and'
      - convert dashes/underscores to spaces
      - drop non-alnum except spaces
      - collapse whitespace
    """
    s = s.lower()
    s = s.replace("&", " and ").replace("/", " and ")
    s = re.sub(r"[-_]+", " ", s)
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _fuzzy_contains(needle: str, haystack: str, min_ratio: float) -> Tuple[bool, float]:
    """
    Sliding fuzzy substring check using difflib.
    Both inputs should already be normalized.
    """
    if not needle or not haystack:
        return (False, 0.0)
    if needle in haystack:
        return (True, 1.0)
    k = len(needle)
    if k >= len(haystack):
        r = difflib.SequenceMatcher(None, needle, haystack).ratio()
        return (r >= min_ratio, r)
    best = 0.0
    # step of 1 is fine for ~1–2K windows; adjust if needed
    for i in range(0, len(haystack) - k + 1, 1):
        sub = haystack[i:i+k]
        r = difflib.SequenceMatcher(None, needle, sub).ratio()
        if r > best:
            best = r
            if best >= min_ratio:
                return (True, best)
    return (False, best)


def _numeric_ta_sort_key(code: str):
    if code.startswith("TA") and code[2:6].isdigit():
        return (int(code[2:6]), code)
    return (10**9, code)


def infer_tactics_near_codes(
    text: str,
    tech_codes: List[str],
    *,
    window_chars: int = 200,
    name_fuzzy_min: float = 0.86,
    include_explicit_tactic_codes: bool = True,
    return_debug: bool = False,
):
    """
    Infer tactic codes (TA####) given a list of technique/sub-tech codes and a document text.

    Process:
      (0) Seed ONLY with TA codes already present in `tech_codes`.
      (1) Find occurrences of the input techniques in the text.
      (2) For each occurrence, scan a window for tactic names (substring or fuzzy)
          and/or explicit TA#### codes that are valid for that technique; union into output.
    """
    debug: Dict = {
        "normalized_tech_codes": [],
        "initial_tactic_union": [],
        "tech_occurrences": [],  # {tech_code, raw_match, span, window_len, hits: [...]}
        "result": [],
        "mapping_info": {},
    }

    if not text or not tech_codes:
        return ([] if not return_debug else ([], debug))

    # Normalize and keep only valid technique codes (T*). Keep TA* separately for seeding.
    norm_techs: List[str] = []
    seeded_tactics: Set[str] = set()
    for c in tech_codes:
        try:
            nc = al._normalize_code(c) if hasattr(al, "_normalize_code") else c
        except Exception:
            nc = c
        if al.is_valid_ttp_code(nc):
            if nc.startswith("T"):
                norm_techs.append(nc)
            elif nc.startswith("TA"):
                seeded_tactics.add(nc)

    debug["normalized_tech_codes"] = list(norm_techs)

    # (0) Seed with TA codes explicitly provided in tech_codes
    found_tactics: Set[str] = set(seeded_tactics)
    debug["initial_tactic_union"] = sorted(found_tactics, key=_numeric_ta_sort_key)

    if not norm_techs:
        out = sorted(found_tactics, key=_numeric_ta_sort_key)
        if return_debug:
            debug["result"] = out
            return out, debug
        return out

    # Build tactic mapping per technique (plus normalized name map)
    tech_to_tactic_map: Dict[str, Dict[str, List[str]]] = {}
    tech_to_normname_code: Dict[str, Dict[str, str]] = {}
    for tc in norm_techs:
        d = al.get_ttp_details(tc)
        tnames = (d.get("namespace_grouped", {}) or {}).get("tactics", []) or []
        tcodes = d.get("tactic_codes", []) or []
        tech_to_tactic_map[tc] = {"names": tnames, "codes": tcodes}
        name_map = {}
        for name, code in zip(tnames, tcodes):
            name_map[_normalize_text_for_match(name)] = code
        tech_to_normname_code[tc] = name_map
    debug["mapping_info"] = tech_to_tactic_map

    # (1) Find occurrences of the input techniques in the text
    occurrences: List[Tuple[str, Tuple[int, int], str]] = []
    for m in al.TTP_CODE_PATTERN_FLEX.finditer(text):
        raw = m.group(0)
        try:
            norm = al._normalize_code(raw)
        except Exception:
            norm = raw
        if norm in norm_techs:
            occurrences.append((norm, (m.start(), m.end()), raw))

    # If none found, return the seed
    if not occurrences:
        out = sorted(found_tactics, key=_numeric_ta_sort_key)
        if return_debug:
            debug["result"] = out
            return out, debug
        return out

    # (2) Scan windows around each found technique
    for norm_tc, span, raw_hit in occurrences:
        tinfo = tech_to_tactic_map.get(norm_tc, {"names": [], "codes": []})
        name_map = tech_to_normname_code.get(norm_tc, {})  # normalized tactic name -> code
        allowed_codes = set(tinfo.get("codes", []))

        win = _slice_window(text, span, window_chars)
        norm_win = _normalize_text_for_match(win)

        per_hit_debug = {
            "tech_code": norm_tc,
            "raw_match": raw_hit,
            "span": span,
            "window_len": len(win),
            #"window_text_snippet": win,
            "hits": [],
        }

        # 2a) Substring or fuzzy name matches against normalized window
        for norm_tname, tcode in name_map.items():
            if not tcode or not tcode.startswith("TA"):
                continue
            matched, score = _fuzzy_contains(norm_tname, norm_win, name_fuzzy_min)
            if matched and tcode in allowed_codes:
                found_tactics.add(tcode)
                how = "substring" if score >= 0.999 else f"fuzzy({score:.2f})"
                per_hit_debug["hits"].append({
                    "via": how,
                    "tactic_name_norm": norm_tname,
                    "tactic_code": tcode
                })

        # 2b) Explicit TA#### codes in the window (restricted to this technique’s allowed set)
        if include_explicit_tactic_codes:
            try:
                _, vset = al.extract_ttps_loose(win)
            except Exception:
                vset = set()
            for c in (vset or []):
                if c.startswith("TA") and c in allowed_codes:
                    found_tactics.add(c)
                    per_hit_debug["hits"].append({"via": "explicit_code", "tactic_code": c})

        debug["tech_occurrences"].append(per_hit_debug)

    out = sorted(found_tactics, key=_numeric_ta_sort_key)
    if return_debug:
        debug["result"] = out
        return out, debug
    return out


if __name__ == "__main__":
    from pathlib import Path
    sample_test = Path("../poc_tests/basic_test/0b1b32b1d1f2cf93f41966c2eb607df4845a1c5f.txt").read_text(encoding="utf-8")
    sample_codes = ['T1584', 'T1090.003', 'T1041']
    inferred, dbg = infer_tactics_near_codes(sample_test, sample_codes, return_debug=True)
    print("Inferred Tactics:", inferred)
    import pprint
    pprint.pprint(dbg)  
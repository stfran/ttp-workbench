#!/usr/bin/env python3
"""
attack_lookup.py — 
Utilities based on STIX ATT&CK

"""
from __future__ import annotations

import hashlib
import json, os, re, logging
from pathlib import Path
import time
from typing import Dict, List, Any, Optional, Tuple, Set, Union
import pprint
import difflib
from .config import attack_version as attack_version_config

log = logging.getLogger(__name__)

try:
    import requests  # Optional; module still works offline if JSONs are present
except Exception:
    requests = None  # type: ignore

# -----------------------------------------------------------------------------
# Paths & constants
# -----------------------------------------------------------------------------
HERE = Path(__file__).resolve().parent
BASE_DIR = HERE / "attack_stix"
BASE_DIR.mkdir(parents=True, exist_ok=True)

ENTERPRISE_ATTACK = BASE_DIR / "enterprise-attack.json"
MOBILE_ATTACK     = BASE_DIR / "mobile-attack.json"
ICS_ATTACK        = BASE_DIR / "ics-attack.json"
META_PATH         = BASE_DIR / "meta.json"

COLLECTIONS = {
    "enterprise-attack": ("Enterprise", ENTERPRISE_ATTACK, "enterprise-attack.json"),
    "mobile-attack":     ("Mobile",     MOBILE_ATTACK,     "mobile-attack.json"),
    "ics-attack":        ("ICS",        ICS_ATTACK,        "ics-attack.json"),
}

RAW_BASE = "https://raw.githubusercontent.com/mitre-attack/attack-stix-data/master"
ATTACK_INDEX_URL = f"{RAW_BASE}/index.json"
HEADERS = {"User-Agent": "ATTACK-Utils/1.0"} 

# The artifact and reproduction inputs use this immutable ATT&CK 19.2
# snapshot. Versioned filenames prevent a later upstream release from changing
# the bytes used by an evaluation run.
PINNED_ATTACK_VERSION = "19.2"
PINNED_ATTACK_COMMIT = "6cda5ad8462c79e14fbb872f4e09059b18e0cfc4"
PINNED_ATTACK_SHA256 = {
    "enterprise-attack": "dc1639caa5501d720e280cf1cbd8fbe009884a0c9b3e6e9ed9d0c25166c3d8f4",
    "mobile-attack": "acfa5ca2d93484476f79bf38590e2b55bb675fc0ce85e76bffa0af2c82dada64",
    "ics-attack": "08b83d2cea6b6d6752468ef0e62e2ab2a53c9443ef72c439ecccb07ab9e89da9",
}

# Technique path tokens in URLs: /techniques/T1547 or /techniques/T1547/001
_PATH_RE = re.compile(r"/techniques/(T\d{4})(?:/(\d{3}))?", re.I)
# Strict inline code form (when we expect well‑formatted codes)
_CODE_INLINE_RX = re.compile(r"(?i)\bT\d{4}(?:\.\d{3})?\b") 
# Bold markdown spans for deprecation lead sentences
_BOLD_MD_RX = re.compile(r"\*\*(.+?)\*\*", re.DOTALL)
#Main tech and sub-tech straggler matchers
_MAIN_TECH_RX = re.compile(r"(?i)\bT\d{4}\b")        # e.g., T1595
_DOT_SUB_RX = re.compile(r"(?<!\d)\s*0?\s*\.\s*(\d{3})(?!\d)")  # e.g., .001

# Flexible, loose matcher (bare sub‑techs/techniques/tactics + prefixed forms)
TTP_CODE_PATTERN_FLEX = re.compile(
    r'''
    (?<![A-Za-z0-9])            # negative look behind, so TTP code isn't preceeded by an alphanumeric (XT1003)
    (?:                         # subtechnique branch
        (?:T\s*0\s*|T\s*1\s*)         # T0 or T1
        (?:\d\s*){3}                  # followed by 3 digits T(0/1)###
        [./]\s*(?:\d\s*){3}           # and subtechnique part T####.###
    |                           # main branch
        (?:T\s*0\s*|                  # T0
        T\s*1\s*|                     # or T1
        T\s*A\s*0\s*)                 # or TA0
        (?:\d\s*){3}                  # followed by 3 digits T(0/1)### or TA0###
        (?!\s*[./]\s*\d)              # negative look ahead to forbid . and / after main-only branch
    |                           # bare subtechnique branch
        (?:0\s*|1\s*)                 # 0 or 1
        (?:\d\s*){3}                  # followed by 3 digits (0/1)###
        [./]\s*(?:\d\s*){3}           # and subtechnique part ####.###
    |                           # bare main branch 
        (?:0\s*|1\s*)                 # 0 or 1
        (?:\d\s*){3}                  # followed by 3 digits (0/1)###
        (?!\s*[./]\s*\d)              # negative look ahead to forbid . and / after main-only branch
    )
    (?![A-Za-z0-9])             # negative look ahead to forbid successive alphanumerics (T1003Abc)
    ''',
    re.IGNORECASE | re.VERBOSE
)

# for strictor matching of TTP codes (T#### or T####.###)
TTP_CODE_PATTERN = re.compile(
    r'''
    (?<![A-Za-z0-9])            # negative look behind, so TTP code isn't preceeded by an alphanumeric (XT1003)
    (?:                         # subtechnique branch
        (?:T\s*0\s*|T\s*1\s*)         # T0 or T1
        (?:\d\s*){3}                  # followed by 3 digits T(0/1)###
        [./]\s*(?:\d\s*){3}           # and subtechnique part T####.###
    |                           # main branch
        (?:T\s*0\s*|                  # T0
        T\s*1\s*|                     # or T1
        T\s*A\s*0\s*)                 # or TA0
        (?:\d\s*){3}                  # followed by 3 digits T(0/1)### or TA0###
        (?!\s*[./]\s*\d)              # negative look ahead to forbid . and / after main-only branch
    )
    (?![A-Za-z0-9])             # negative look ahead to forbid successive alphanumerics (T1003Abc)
    ''',
    re.IGNORECASE | re.VERBOSE
)

# -----------------------------------------------------------------------------
# Internal state / indices
# -----------------------------------------------------------------------------
_IDX_BY_CODE: Dict[str, Dict[str, Any]] = {}      # external_id (T####, T####.###, TA0001) -> object
_CODE_TO_COLLECTION: Dict[str, str] = {}          # code -> 'Enterprise' | 'Mobile' | 'ICS'
_TACTIC_NAME_TO_CODE: Dict[str, str] = {}
_TACTIC_CODE_TO_NAME: Dict[str, str] = {}
_LOADED = False

# -----------------------------------------------------------------------------
# Small helpers
# -----------------------------------------------------------------------------

def _read_json(path: Path, default):
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def _normalize_code(s: str) -> str:
    """Uppercase, strip spaces, unify '/' to '.', leave prefixes intact."""
    s = re.sub(r"\s+", "", s or "").upper()
    return s.replace("/", ".")

def _norm_tactic_label(s: str) -> str:
    # normalize "defense-evasion" vs "Defense Evasion" to "defenseevasion"
    return re.sub(r"[\s\-_./]+", "", (s or "")).strip().lower()


def _candidates_for_bare(raw: str) -> List[str]:
    """Given a *bare* raw hit (e.g., '1547' or '1503.003'), propose candidate codes.
    - '1003.003' -> ['T1003.003']
    - '1003'     -> ['T1003', 'TA1003']  (try technique and tactic)
    """
    s = _normalize_code(raw)
    if "." in s:
        return [f"T{s}"]
    if re.fullmatch(r"(?:0|1)\d{3}", s):
        return [f"T{s}", f"TA{s}"]
    return []


def _unique(seq: List[str]) -> List[str]:
    return list(set(seq))

def _first_n(s: str, n: int = 225) -> str:
    """Collapse whitespace and return first n characters (graceful if shorter)."""
    if not s:
        return ""
    txt = re.sub(r"\s+", " ", str(s)).strip()
    return txt[:n]

def _format_tactic(name: str) -> str:
    """Title-case tactic phase names like 'defense-evasion' -> 'Defense-Evasion'."""
    if not name:
        return ""
    return "-".join(p.capitalize() for p in str(name).split("-"))

def _tid_to_path(code: str) -> str:
    """Turn T####(.###)? into URL path fragment 'T####' or 'T####/###'."""
    c = _normalize_code(code)
    if "." in c:
        base, sub = c.split(".", 1)
        return f"{base}/{sub}"
    return c

def _ratio(a: str, b: str) -> float:
    return difflib.SequenceMatcher(a=a, b=b).ratio()

def _cli_unescape(s: str) -> str:
    # for cli testing
    if "\\n" in s or "\\t" in s or "\\r" in s:
        try:
            return bytes(s, "utf-8").decode("unicode_escape")
        except Exception:
            # very safe fallback
            return s.replace("\\n", "\n").replace("\\t", "\t").replace("\\r", "\r")
    return s

# -----------------------------------------------------------------------------
# 0) STIX data management
# -----------------------------------------------------------------------------
def _get_attack_version_remote(timeout: int = 10) -> Optional[str]:
    if requests is None:
        return None
    try:
        r = requests.get(ATTACK_INDEX_URL, timeout=timeout, headers=HEADERS)
        r.raise_for_status()
        j = r.json()
        colls = j.get("collections", [])
        # Heuristic: enterprise is typically first; grab latest version listed
        if colls:
            vers = colls[0].get("versions", [])
            if vers:
                v = str(vers[0].get("version") or "").strip()
                return v if v else None
    except Exception:
        return None
    return None

def _download_domain_jsons(version_hint: Optional[str]) -> None:
    if requests is None:
        log.warning("Requests not available; cannot download STIX JSONs. Expecting local files present.")
        return
    for key, (_, local_path, filename) in COLLECTIONS.items():
        url = f"{RAW_BASE}/{key}/{filename}"
        r = requests.get(url, timeout=30, headers=HEADERS)
        r.raise_for_status()
        local_path.write_text(r.text, encoding="utf-8")
    _write_json(META_PATH, {"attack_version": version_hint or "unknown"})


def _has_expected_digest(path: Path, expected: str) -> bool:
    if not path.is_file():
        return False
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest() == expected


def _download_pinned_domain_jsons(force_refresh: bool = False) -> None:
    if requests is None:
        missing = [
            str(local_path)
            for key, (_, local_path, _) in COLLECTIONS.items()
            if not _has_expected_digest(local_path, PINNED_ATTACK_SHA256[key])
        ]
        if missing:
            raise RuntimeError(
                "Requests is unavailable and pinned ATT&CK data must be staged: "
                + ", ".join(missing)
            )
        return

    for key, (_, local_path, _) in COLLECTIONS.items():
        expected = PINNED_ATTACK_SHA256[key]
        if not force_refresh and _has_expected_digest(local_path, expected):
            print(f"Using verified MITRE ATT&CK {PINNED_ATTACK_VERSION} snapshot: {local_path}")
            continue

        url = (
            "https://raw.githubusercontent.com/mitre-attack/attack-stix-data/"
            f"{PINNED_ATTACK_COMMIT}/{key}/{key}-{PINNED_ATTACK_VERSION}.json"
        )
        error: Optional[Exception] = None
        for attempt in range(1, 6):
            try:
                print(
                    f"Downloading MITRE ATT&CK {PINNED_ATTACK_VERSION} "
                    f"{key} snapshot (attempt {attempt}/5)"
                )
                response = requests.get(url, timeout=(10, 300), headers=HEADERS)
                response.raise_for_status()
                payload = response.content
                actual = hashlib.sha256(payload).hexdigest()
                if actual != expected:
                    raise RuntimeError(
                        f"Checksum mismatch for {key}: expected {expected}, found {actual}"
                    )
                partial = local_path.with_name(local_path.name + ".part")
                partial.write_bytes(payload)
                os.replace(partial, local_path)
                error = None
                break
            except Exception as exc:
                error = exc
                if attempt < 5:
                    time.sleep(5)
        if error is not None:
            raise RuntimeError(f"Unable to stage pinned ATT&CK snapshot {key}") from error

    _write_json(META_PATH, {"attack_version": PINNED_ATTACK_VERSION})


def ensure_stix_data(force_refresh: bool = False) -> Optional[str]:
    """Ensure local STIX JSONs exist; refresh if remote version changed, unless config specifies a version we want to stick to.
    Returns the version string we believe we're aligned to (if known).
    """
    if attack_version_config:
        if attack_version_config != PINNED_ATTACK_VERSION:
            raise RuntimeError(
                "The configured ATT&CK version has no pinned artifact snapshot: "
                f"{attack_version_config}"
            )
        _download_pinned_domain_jsons(force_refresh=force_refresh)
        return PINNED_ATTACK_VERSION

    need_files = not (ENTERPRISE_ATTACK.exists() and MOBILE_ATTACK.exists() and ICS_ATTACK.exists())
    meta = _read_json(META_PATH, {})
    local_ver = str(meta.get("attack_version") or "").strip() or None
    remote_ver = _get_attack_version_remote()

    if force_refresh or need_files or (remote_ver and local_ver and remote_ver != local_ver):
        _download_domain_jsons(remote_ver or local_ver)
        local_ver = remote_ver or local_ver

    return local_ver


# -----------------------------------------------------------------------------
# Index building
# -----------------------------------------------------------------------------

def _load_objects(path: Path) -> List[Dict[str, Any]]:
    data = _read_json(path, {})
    return data.get("objects", []) if isinstance(data, dict) else []


def _build_index_once() -> None:
    global _LOADED
    if _LOADED:
        return
    ensure_stix_data(False)

    for key, (label, local_path, _) in COLLECTIONS.items():
        for obj in _load_objects(local_path):
            t = obj.get("type")
            if t not in ("attack-pattern", "x-mitre-tactic"):
                continue
            # Map external_id -> object
            for ref in obj.get("external_references", []) or []:
                if ref.get("source_name") == "mitre-attack":
                    ext_id = ref.get("external_id")
                    if not ext_id:
                        continue
                    ext_id = _normalize_code(ext_id)
                    _IDX_BY_CODE[ext_id] = obj
                    _CODE_TO_COLLECTION[ext_id] = label
                    name = (obj.get("name") or "").strip()
                    if t == "x-mitre-tactic" and name and ext_id.startswith("TA"):
                        _TACTIC_NAME_TO_CODE[_norm_tactic_label(name)] = ext_id
                        _TACTIC_CODE_TO_NAME[ext_id] = name
                        
    _LOADED = True


# -----------------------------------------------------------------------------
# 1) Presence check
# -----------------------------------------------------------------------------

def is_valid_ttp_code(code: str) -> bool:
    """True iff code exists in any loaded ATT&CK STIX collection."""
    if not code:
        return False
    _build_index_once()
    return _normalize_code(code) in _IDX_BY_CODE


# -----------------------------------------------------------------------------
# 2) Loose extraction
# -----------------------------------------------------------------------------

def find_ttp_candidates_loose(text: str) -> List[str]:
    """Return raw substrings (m.group(0)) that *might* be TTP codes."""
    if not text:
        return []
    return [m.group(0) for m in TTP_CODE_PATTERN_FLEX.finditer(text)]


def extract_ttps_loose(text: str, return_sent_list: bool = False) -> Tuple[Set[str], Set[str]]:
    """Return (raw_hits, validated_codes) from unstructured text.
    - Bare 4‑digit hits validated as both T#### and TA####.
    - Bare sub‑technique ####.### validated as T####.###.
    - Prefixed forms are validated as‑is.
    """
    # NOTE TO DO: This needs much more post-processing work to avoid false positives
    """
        Extra post-processing strategy:
            - hard negative filters for common false positives (timestamps, CVEs, version numbers, Thousand-groupings from numbers)
            - soft scoring where we accept if a threshold is met
                - presence of context words (attack, technique, tactic, mitre, etc)
                - presence of the matched technique/tactic name (fuzzy match) in a wider context window
    """
    raw_hits: Set[str] = set()
    valid: Set[str] = set()
    sent_list: List[Dict[str, Any]] = []

    if not text:
        return raw_hits, valid

    for m in TTP_CODE_PATTERN_FLEX.finditer(text):
        raw = m.group(0)
        raw_hits.add(raw)
        s = _normalize_code(raw)
        if s.startswith("T") or s.startswith("TA"):
            # Already prefixed form
            if is_valid_ttp_code(s):
                valid.add(s)
                sent_list.append({
                    'text':raw,
                    'ttps':[{"code":s, "TTP":get_ttp_name(s)}]
                })
            continue
        # Bare forms
        for cand in _candidates_for_bare(s):
            if is_valid_ttp_code(cand):
                valid.add(cand)
                sent_list.append({
                    'text':raw,
                    'ttps':[{"code":cand, "TTP":get_ttp_name(cand)}]
                })
                break
    if return_sent_list:
        return sent_list
    return raw_hits, valid


# -----------------------------------------------------------------------------
# 3) Strict extraction (well‑formatted codes expected)
# -----------------------------------------------------------------------------

def extract_ttps(
    text: str,
    *,
    only_bold: bool = False,
    return_sent_list: bool = False,
    **kwargs                            # for the decoupling
) -> Union[List[str], List[Dict[str, Any]]]:
    """Extract T####(.###)? from text. If only_bold, restrict inline scan to **...** (for references in STIX deprecation notes).
    Also parse codes from ATT&CK technique URLs.
    Returns:
      - if return_sent_list=False: unique list of valid codes
      - if return_sent_list=True:  list of sentence dicts with code + name
    """
    if not text:
        return [] if not return_sent_list else []

    out: List[str] = []

    if only_bold:
        scan_blobs = [m.group(1) for m in _BOLD_MD_RX.finditer(text)]
        for blob in scan_blobs:
            for m in _CODE_INLINE_RX.finditer(blob):
                ttp = _normalize_code(m.group(0))
                if is_valid_ttp_code(ttp):
                    out.append(ttp)
        return _unique(out)

    sent_list: List[Dict[str, Any]] = []
    code_hits: List[Tuple[int, int, str, str]] = [] 

    for m in TTP_CODE_PATTERN.finditer(text):
        raw = m.group(0)
        ttp = _normalize_code(raw)
        if not is_valid_ttp_code(ttp):
            continue
        out.append(ttp)
        code_hits.append((m.start(), m.end(), ttp, raw))
       
        if return_sent_list:
            name = get_ttp_name(ttp)
            sent_list.append({
                "text": raw,
                "ttps": [{"code": ttp, "TTP": name}],
            })

    if kwargs.get("decouple", False) and code_hits: # we build this logic path to catch decoupled sub-technique parts (e.g. T1547 and some text then .002 as in a table)
        added_codes, sent_list = _expand_with_decoupled_subtechs(
            text, code_hits, sent_list=sent_list if return_sent_list else None, **kwargs
        )
        out.extend(list(added_codes))

    if return_sent_list:
        return _unique(out), sent_list
    return _unique(out)

def _expand_with_decoupled_subtechs(
    text: str,
    code_hits: List[Tuple[int, int, str, str]],  # (start, end, code, raw)
    *,
    sent_list: Optional[List[Dict[str, Any]]] = None,
    **kw
) -> Tuple[Set[str], Optional[List[Dict[str, Any]]]]:
    """
    Expand main-technique hits with decoupled '.###' tails discovered in the
    forward window. Returns (added_codes, possibly-updated sent_list).
    Kwargs (extensible):
      - decouple_window_chars (int) = 1200
      - decouple_require_name_hit (bool) = True
      - decouple_name_fuzzy_min (float) = 0.62
    """
    window_chars        = int(kw.get("decouple_window_chars", 1200))
    require_name_hit    = bool(kw.get("decouple_require_name_hit", True))
    name_fuzzy_min      = float(kw.get("decouple_name_fuzzy_min", 0.62))

    # infer once over the whole text
    windows = infer_decoupled_subtechs(
        text,
        window_chars=window_chars,
        require_name_hit=require_name_hit,
        name_fuzzy_min=name_fuzzy_min,
    )

    # restrict to mains we actually matched
    mains_seen: Set[str] = {
        code for _, _, code, _ in code_hits
        if code.startswith("T") and "." not in code
    }

    # map first sentence per main (if we have a sentence list)
    first_sent_for_main: Dict[str, Dict[str, Any]] = {}
    if sent_list is not None:
        for s in sent_list:
            for t in (s.get("ttps") or []):
                c = str(t.get("code") or "")
                if c in mains_seen and c not in first_sent_for_main:
                    first_sent_for_main[c] = s

    added: Set[str] = set()
    for w in windows:
        main = w.get("tech_code")
        if main not in mains_seen:
            continue

        subs = w.get("subs") or []
        hits = w.get("sub_hits") or []  # <-- new

        if not subs:
            continue

        # 1) add codes to global set
        for scode in subs:
            if is_valid_ttp_code(scode):
                added.add(scode)

        if sent_list is None:
            continue

        # 2) ensure we have an anchor sentence for the main
        host = first_sent_for_main.get(main)
        if host is None:
            host = {
                "text": main,
                "ttps": [{"code": main, "TTP": get_ttp_name(main)}],
                "source": "regex-decoupled",
            }
            sent_list.append(host)
            first_sent_for_main[main] = host

        # 3) attach sub-techs to the anchor sentence (as before)
        # existing = {str(t.get("code")) for t in host.get("ttps") or []}
        # for scode in subs:
        #    if scode not in existing:
        #        host["ttps"].append({"code": scode, "TTP": get_ttp_name(scode)})
        #       existing.add(scode)
        # NOTE: we want one TTP per text field.

        # 4) ALSO append separate entries for the raw dot fragments that triggered them
        #    Each entry carries the sub-tech code and an anchor hint for disambiguation.
        for h in hits:
            scode = h["code"]
            raw   = h["raw"]
            sent_list.append({
                "text": raw,  # e.g., ".002" or "0.001" exactly as seen
                "anchor_code": main,  # helps the annotator pick the right occurrence
                "ttps": [{"code": scode, "TTP": get_ttp_name(scode)}],
                "source": "regex-decoupled-dot",
                # optionally surface absolute span for downstream tools:
                "span": h.get("span"),
            })
    return added, sent_list


# -----------------------------------------------------------------------------
# 4) Name lookup
# -----------------------------------------------------------------------------

def get_ttp_name(code: str) -> str:
    """Return the ATT&CK name for a given code. Raises KeyError if unknown."""
    _build_index_once()
    k = _normalize_code(code)
    obj = _IDX_BY_CODE.get(k)
    if not obj:
        raise UserWarning(f"attack_lookup.get_ttp_name, Unknown ATT&CK code: {code}")
    return obj.get("name") or ""


# -----------------------------------------------------------------------------
# 5) Deprecation flag
# -----------------------------------------------------------------------------

def is_deprecated(code: str) -> bool:
    """True if the STIX object is deprecated (or revoked)."""
    _build_index_once()
    k = _normalize_code(code)
    obj = _IDX_BY_CODE.get(k)
    if not obj:
        return False
    if obj.get("x_mitre_deprecated") is True:
        return True
    return False

# -----------------------------------------------------------------------------
# 5a) revoked replacement
# -----------------------------------------------------------------------------
def revoked_check_and_replacement(code: str) -> Optional[str]:
    """If the code is revoked, return a suggested replacement code (if any).
    If not revoked, return None.
    """
    _build_index_once()
    k = _normalize_code(code)
    obj = _IDX_BY_CODE.get(k)
    if not obj:
        return None
    if obj.get("revoked") is True: #lookup by searching other attack patterns for matching 'name'
        name = obj.get("name")
        for code, o in _IDX_BY_CODE.items():
            if o.get("name") == name and o.get("revoked") is not True:
                return code
    return None


# -----------------------------------------------------------------------------
# 6) Deprecation details
# -----------------------------------------------------------------------------
def get_deprecation_revocation_details(code: str) -> Dict[str, Any]:
    """Return structured deprecation details for a code.

    {
      'deprecated': bool,
      'replacements': [ {'code': 'T1059', 'name': '...'}, ... ]
    }
    """
    _build_index_once()
    k = _normalize_code(code)
    obj = _IDX_BY_CODE.get(k)
    if not obj:
        raise UserWarning(f"Unknown ATT&CK code: {code}")

    dep = is_deprecated(k)
    rev = revoked_check_and_replacement(k)
    if not rev and not dep:
        return {"deprecated": False, 'revoked': False, "replacements": []}
    elif rev and not dep:
        return {"deprecated": False, 'revoked': True, "updated":rev, "replacements": []}

    desc = obj.get("description") or ""
    # Extract replacements from the **bold** lead sentence(s) and technique URLs
    repl_codes = extract_ttps(desc, only_bold=True)

    items: List[Dict[str, Any]] = []
    for rc in repl_codes:
        if is_valid_ttp_code(rc):
            try:
                nm = get_ttp_name(rc)
            except Exception:
                nm = ""
            items.append({"code": rc, "name": nm})

    # de-dupe dicts while preserving order
    serialized = _unique([json.dumps(x, sort_keys=True) for x in items])

    if not rev and dep:
        return {"deprecated": True, "revoked": False, "updated": rev, "replacements": [json.loads(u) for u in serialized]}
    elif rev and dep:
        return {"deprecated": True, "revoked": True, "updated": rev, "replacements": [json.loads(u) for u in serialized]}

# -----------------------------------------------------------------------------
# 6a) Modernize TTPs - generously replaces deprecated/revoked codes
# -----------------------------------------------------------------------------
def modernize_ttps(ttps: List[str]) -> List[str]:
    """Given a list of TTP codes, replace deprecated/revoked codes with their modern equivalents."""
    modern_ttps = set()
    
    for ttp in ttps:
        if not ttp:
            continue  # skip empty codes
        try:
            details = get_deprecation_revocation_details(ttp)
        except Exception as e:
            raise ValueError(f"Error retrieving deprecation/revocation details for TTP {ttp}: {e}")
            
        if details["deprecated"] and details["revoked"]:
            raise ValueError(f"TTP {ttp} is deprecated but revoked; unexpected state. details: {details}")
        elif details["revoked"]:
            # revoked should map to an updated code
            updated_code = details.get("updated")
            if updated_code:
                modern_ttps.add(updated_code)
            else:
                raise ValueError(f"TTP {ttp} is revoked but has no updated code; unexpected state. details: {details}")
        elif details["deprecated"]:
            # deprecated might have one or several recommended replacements
            replacements = details.get("replacements", [])
            if replacements:
                for rep in replacements:
                    try:
                        modern_ttps.add(rep['code'])
                    except Exception as e:
                        raise ValueError(f"Error adding replacement TTP for deprecated TTP {ttp}. replacements: {replacements}. error: {e}")
            else:
                # No replacements provided; keep the original code
                modern_ttps.add(ttp)
        else:
            # Not deprecated or revoked; keep original
            modern_ttps.add(ttp)
    
    return list(modern_ttps)

# -----------------------------------------------------------------------------
# 7) general text parsing for labels (all at once)
# -----------------------------------------------------------------------------
def parse_text_for_labels(text: str, 
                          only_loose: bool = True,
                          only_strict: bool = False,
                          both: bool = False,
                          *,
                          decoupled: Optional[bool] = False,
                          ) -> Tuple[Set[str], Optional[Set[str]], Optional[List[Dict[str, Any]]], Optional[Set[str]], Optional[List[Dict[str, Any]]]]:
    """
    Return (updated_labels, valid_labels, raw_labels, deprecated_labels) from unstructured text.
    valid labels - validated TTP codes found in the text
    raw labels - TTP-code-like strings found in the text, as-is
    deprecated labels - TTP codes found in the text that are deprecated
    """
    if only_strict:
        only_loose = False
    if both:
        only_loose = True
        only_strict = True
    

    if only_loose:
        raw_hits, loose_valid = extract_ttps_loose(text) # performs a looser REGEX (high recall), then post-processes to validate (recover precision)
        loose_deprecated_labels = list()  
        for code in set(loose_valid):
            deprecation_details = get_deprecation_revocation_details(code)
            deprecated = deprecation_details.get('deprecated')
            revoked = deprecation_details.get('revoked')
            if deprecated or revoked:
                loose_deprecated_labels.append({
                    "code": code,
                    "details": deprecation_details
                })

    if only_strict:
        strict_valid = extract_ttps(text)
        strict_deprecated_labels = list()
        for code in set(strict_valid):
            deprecation_details = get_deprecation_revocation_details(code)
            deprecated = deprecation_details.get('deprecated')
            revoked = deprecation_details.get('revoked')
            if deprecated or revoked:
                strict_deprecated_labels.append({
                    "code": code,
                    "details": deprecation_details
                })
    if both:
        return sorted(set(loose_valid)), sorted(set(raw_hits)), loose_deprecated_labels, sorted(set(strict_valid)), strict_deprecated_labels
    
    if only_loose:
        return sorted(set(loose_valid)), sorted(set(raw_hits)), loose_deprecated_labels
    elif only_strict:
        return sorted(set(strict_valid)), strict_deprecated_labels


# -----------------------------------------------------------------------------
# 8) Full TTP details
# -----------------------------------------------------------------------------
def get_ttp_details(ttp_code: str) -> Dict[str, Any]:
    """
    Return a rich details dictionary for a TTP code.

    Fields:
      - name: ATT&CK object name
      - code: normalized external id (as provided)
      - collection: 'Enterprise' | 'Mobile' | 'ICS'
      - namespaces: expanded list per tactic
      - namespace: compact single string with tactic set collapsed
      - namespace_grouped: structured form {collection, tactics, technique, subtechnique}
      - description: first ~225 chars of this object's description
      - description_above: if sub-technique, first ~225 chars of parent technique description
      - link: canonical ATT&CK URL
      - deprecation: output of get_deprecation_details()
    """
    if not is_valid_ttp_code(ttp_code):
        raise UserWarning(f"TTP code '{ttp_code}' not found in local ATT&CK JSONs.")

    _build_index_once()
    cur = _normalize_code(ttp_code)
    obj = _IDX_BY_CODE.get(cur)
    assert obj is not None

    obj_name = obj.get("name") or ""

    # Identify collection
    collection_label = _CODE_TO_COLLECTION.get(cur, "Enterprise")

    # Tactics from kill_chain_phases
    tactics: List[str] = []
    tactic_codes: List[str] = []  
    for k in obj.get("kill_chain_phases", []) or []:
        if str(k.get("kill_chain_name", "")).startswith("mitre-"):
            tactic_name = k.get("phase_name", "")
            tactics.append(_format_tactic(tactic_name))
            code = _TACTIC_NAME_TO_CODE.get(_norm_tactic_label(tactic_name))
            if code:
                tactic_codes.append(code)
    # de-dupe while preserving order
    seen: Set[str] = set()
    tactics = [t for t in tactics if not (t in seen or seen.add(t))]
    seen_codes: Set[str] = set()
    tactic_codes = [c for c in tactic_codes if not (c in seen_codes or seen_codes.add(c))]

    # Technique/sub-technique resolution
    is_sub = bool(obj.get("x_mitre_is_subtechnique", False)) or ("." in cur)
    tech_name = obj_name
    sub_name = obj_name

    parent_desc = ""
    if is_sub and "." in cur:
        parent_code = cur.split(".", 1)[0]
        parent_obj = _IDX_BY_CODE.get(parent_code)
        if parent_obj:
            tech_name = parent_obj.get("name") or ""
            parent_desc = parent_obj.get("description") or ""

    # Expanded namespaces
    namespaces: List[str] = []
    if is_sub:
        for tac in (tactics or [""]):
            namespaces.append(f"{collection_label}::{tac}::{tech_name}::{sub_name}")
    else:
        for tac in (tactics or [""]):
            namespaces.append(f"{collection_label}::{tac}::{tech_name}")

    # Compact + grouped
    tactic_set = "|".join(tactics) if tactics else ""
    if is_sub:
        namespace_compact = f"{collection_label}::{{{tactic_set}}}::{tech_name}::{sub_name}"
        namespace_grouped: Dict[str, Any] = {
            "collection": collection_label,
            "tactics": tactics,
            "technique": tech_name,
            "subtechnique": sub_name,
        }
    else:
        namespace_compact = f"{collection_label}::{{{tactic_set}}}::{tech_name}"
        namespace_grouped = {
            "collection": collection_label,
            "tactics": tactics,
            "technique": tech_name,
            "subtechnique": None,
        }

    # Descriptions (first ~225 chars)
    desc_raw = obj.get("description") or ""
    desc = _first_n(desc_raw, 225)
    desc_above = _first_n(parent_desc, 225) if parent_desc else ""

    # Link
    url = f"https://attack.mitre.org/techniques/{_tid_to_path(cur)}"

    # Deprecation details
    deprecation = get_deprecation_revocation_details(cur)

    return {
        "name": obj_name,
        "code": cur,
        "collection": collection_label,
        "namespaces": namespaces,
        "namespace": namespace_compact,
        "namespace_grouped": namespace_grouped,
        "description": desc,
        "description_above": desc_above,
        "link": url,
        "deprecation": deprecation,
        "tactic_codes": tactic_codes,
    }

def map_ttp(ttp_code: str) -> str:
    """deprerecated alias for get_ttp_name"""
    return get_ttp_name(ttp_code)


def infer_decoupled_subtechs(
    text: str,
    *,
    window_chars: int = 1200,
    name_fuzzy_min: float = 0.62,
    require_name_hit: bool = True
) -> List[Dict[str, Any]]:
    if not text:
        return []
    out = []
    anchors = list(_MAIN_TECH_RX.finditer(text))
    if not anchors:
        return out

    for i, m in enumerate(anchors):
        tech_code = _normalize_code(m.group(0))
        start = m.end()
        next_start = anchors[i+1].start() if i+1 < len(anchors) else min(len(text), start + window_chars)
        wend = min(next_start, start + window_chars)
        window = text[start:wend]
        if not window:
            out.append({'tech_code': tech_code, 'span': (m.start(), m.end()), 'window': (start, wend), 'subs': [], 'sub_hits': []})
            continue

        subs: List[str] = []
        sub_hits: List[Dict[str, Any]] = []

        for sm in _DOT_SUB_RX.finditer(window):
            sub = sm.group(1)  # '001'
            full = f"{tech_code}.{sub}"
            if not is_valid_ttp_code(full):
                continue

            raw = window[sm.start():sm.end()]  # preserves spaces / optional 0 as seen
            # LOCAL name check context:
            left  = max(0, sm.start() - 80)
            right = min(len(window), sm.end() + 80)
            ctx_local = window[left:right].lower()

            if require_name_hit:
                try:
                    nm = (get_ttp_name(full) or "").lower()
                except Exception:
                    nm = ""
                if not (nm and (nm in ctx_local or _ratio(nm, ctx_local) >= name_fuzzy_min)):
                    continue

            subs.append(full)
            sub_hits.append({
                "code": full,
                "raw": raw,
                "span": (start + sm.start(), start + sm.end())  # absolute in `text`
            })

        # de-dupe codes while preserving order (sub_hits is already occurrence-level)
        seen = set()
        subs = [s for s in subs if not (s in seen or seen.add(s))]

        out.append({
            'tech_code': tech_code,
            'span': (m.start(), m.end()),
            'window': (start, wend),
            'subs': subs,
            'sub_hits': sub_hits
        })
    return out

# -----------------------------------------------------------------------------
# 9) Redact TTPs 
# -----------------------------------------------------------------------------
def redact_ttps_from_text(text: str, similarity_threshold: float = 0.8) -> str:
    """
    Remove TTP codes, their nearby names, and associated tactic names from text.
    
    Args:
        text: Input text to redact TTPs from
        similarity_threshold: Minimum similarity score for fuzzy matching TTP/tactic names (0.0-1.0)
    
    Returns:
        Text with TTP codes, nearby TTP names, and nearby tactic names removed
    
    Process:
        1. Find all TTP codes in the text using TTP_CODE_PATTERN
        2. Get the name and tactic information for each valid TTP code
        3. Search in a window around each TTP code for fuzzy matches of the TTP name and tactic names
        4. Remove TTP codes, nearby technique names, and nearby tactic names
    """
    if not text:
        return text
    
    # Keep track of positions to remove (start, end) tuples
    # We'll collect all removals first, then apply them in reverse order
    removals = []
    
    # Find all TTP code matches in the original text
    for match in TTP_CODE_PATTERN.finditer(text):
        raw_code = match.group(0)
        ttp_code = _normalize_code(raw_code)
        
        # Only process valid TTP codes
        if not is_valid_ttp_code(ttp_code):
            continue
            
        # Get the TTP name and tactic information
        try:
            ttp_name = get_ttp_name(ttp_code)
            ttp_details = get_ttp_details(ttp_code)
            tactic_names = ttp_details.get("namespace_grouped", {}).get("tactics", [])
        except Exception:
            ttp_name = ""
            tactic_names = []
            
        if not ttp_name:
            continue
            
        # For sub-techniques, also get the parent technique name
        ttp_names_to_search = [ttp_name]
        parent = 1
        if '.' in ttp_code:
            parent = 2
            parent_code = ttp_code.split('.')[0]
            try:
                parent_name = get_ttp_name(parent_code)
                if parent_name and parent_name != ttp_name:
                    ttp_names_to_search.append(parent_name)
            except Exception:
                pass
            

            
        # Add the TTP code position for removal
        removals.append((match.start(), match.end(), f"TTP_CODE:{raw_code}"))
        
        # Define search window around the TTP code
        # Window size is 3 times the length of the longest name (TTP names + tactic names)
        max_name_length = parent * max([len(name) for name in ttp_names_to_search] + [len(tactic) for tactic in tactic_names])
        window_size = max(max_name_length * 3, 200)
        code_start = match.start()
        code_end = match.end()
        
        # Search window boundaries
        window_start = max(0, code_start - window_size)
        window_end = min(len(text), code_end + window_size)
        
        # Extract the window text and find word boundaries
        window_text = text[window_start:window_end]
        

        
        # Search for all TTP names (including parent technique name for sub-techniques)
        for current_ttp_name in ttp_names_to_search:
            current_ttp_name_lower = current_ttp_name.lower()
            
            # Look for exact matches of the full name (case-insensitive)
            pattern = re.compile(re.escape(current_ttp_name), re.IGNORECASE)
            for name_match in pattern.finditer(window_text):
                abs_start = window_start + name_match.start()
                abs_end = window_start + name_match.end()
                
                # Don't overlap with the TTP code itself
                if abs_end <= code_start or abs_start >= code_end:
                    # Ensure we don't accidentally include leading/trailing newlines
                    matched_text = name_match.group(0)
                    if '\n' not in matched_text:  # Only proceed if no newlines in the match
                        removals.append((abs_start, abs_end, f"TTP_NAME_EXACT:{matched_text}"))
            
            # Look for fuzzy matches of the full name using word boundaries
            # Use [ \t] instead of \s to avoid matching newlines
            words_in_window = re.findall(r'\b\w+(?:[ \t]+\w+)*\b', window_text)
            for word_phrase in words_in_window:
                if len(word_phrase.strip()) < 3:  # Skip very short phrases
                    continue
                    
                similarity = difflib.SequenceMatcher(None, current_ttp_name_lower, word_phrase.lower()).ratio()
                
                if similarity >= similarity_threshold:
                    # Find the position of this phrase in the window
                    phrase_start = window_text.find(word_phrase)
                    if phrase_start != -1:
                        abs_start = window_start + phrase_start
                        abs_end = abs_start + len(word_phrase)
                        
                        # Don't overlap with the TTP code itself
                        if abs_end <= code_start or abs_start >= code_end:
                            if '\n' not in word_phrase:  # Only proceed if no newlines in the match
                                removals.append((abs_start, abs_end, f"TTP_NAME_FUZZY:{word_phrase}"))
            
            # Look for individual significant words from the TTP name at word boundaries
            # Include short but significant cybersecurity terms that should be redacted
            significant_short_words = {'os', 'ip', 'id', 'api', 'dns', 'sql', 'ssh', 'rdp', 'tcp', 'udp', 'url', 'uri', 'xml', 'dll', 'exe', 'bat', 'ps1', 'cmd', 'wmi', 'com', 'ole', 'uac'}
            
            all_words = current_ttp_name.lower().split()
            name_words = []
            for word in all_words:
                if len(word) >= 4 or word in significant_short_words:
                    name_words.append(word)
            
            for word in name_words:
                # Use word boundary regex to find exact word matches
                word_pattern = re.compile(r'\b' + re.escape(word) + r'\b', re.IGNORECASE)
                for word_match in word_pattern.finditer(window_text):
                    abs_start = window_start + word_match.start()
                    abs_end = window_start + word_match.end()
                    
                    # Don't overlap with the TTP code itself
                    if abs_end <= code_start or abs_start >= code_end:
                        # Check that this word isn't part of a common word that might be a false positive
                        matched_word = word_match.group(0).lower()
                        if matched_word not in ['and', 'the', 'for', 'with', 'from', 'into', 'over']:
                            removals.append((abs_start, abs_end, f"TTP_WORD:{word_match.group(0)}"))
        
        # Also look for tactic names associated with this TTP
        for tactic_name in tactic_names:
            if not tactic_name:
                continue
                
            # Look for exact matches of the full tactic name (case-insensitive)
            tactic_pattern = re.compile(re.escape(tactic_name), re.IGNORECASE)
            for tactic_match in tactic_pattern.finditer(window_text):
                abs_start = window_start + tactic_match.start()
                abs_end = window_start + tactic_match.end()
                
                # Don't overlap with the TTP code itself
                if abs_end <= code_start or abs_start >= code_end:
                    matched_text = tactic_match.group(0)
                    if '\n' not in matched_text:  # Only proceed if no newlines in the match
                        removals.append((abs_start, abs_end, f"TACTIC_NAME_EXACT:{matched_text}"))
            
            # Also look for common variations (hyphens vs spaces, etc.)
            tactic_variations = [
                tactic_name.replace('-', ' '),  # "Command-And-Control" -> "Command And Control"
                tactic_name.replace('-', ' ').lower(),  # -> "command and control"
                tactic_name.replace('-', ' ').title(),  # -> "Command And Control"
                # Handle the specific case where "And" becomes "and" (lowercase)
                tactic_name.replace('-And-', ' and ').replace('-', ' '),  # -> "Command and Control"
            ]
            
            for variation in tactic_variations:
                if variation != tactic_name:  # Don't duplicate the original
                    var_pattern = re.compile(re.escape(variation), re.IGNORECASE)
                    for var_match in var_pattern.finditer(window_text):
                        abs_start = window_start + var_match.start()
                        abs_end = window_start + var_match.end()
                        
                        # Don't overlap with the TTP code itself
                        if abs_end <= code_start or abs_start >= code_end:
                            matched_text = var_match.group(0)
                            if '\n' not in matched_text:  # Only proceed if no newlines in the match
                                removals.append((abs_start, abs_end, f"TACTIC_NAME_VAR:{matched_text}"))
            
            # Look for fuzzy matches of tactic names using word boundaries
            # Use [ \t] instead of \s to avoid matching newlines
            tactic_words_in_window = re.findall(r'\b\w+(?:[ \t]+\w+)*\b', window_text)
            for word_phrase in tactic_words_in_window:
                if len(word_phrase.strip()) < 3:  # Skip very short phrases
                    continue
                    
                similarity = difflib.SequenceMatcher(None, tactic_name.lower(), word_phrase.lower()).ratio()
                
                if similarity >= similarity_threshold:
                    # Find the position of this phrase in the window
                    phrase_start = window_text.find(word_phrase)
                    if phrase_start != -1:
                        abs_start = window_start + phrase_start
                        abs_end = abs_start + len(word_phrase)
                        
                        # Don't overlap with the TTP code itself
                        if abs_end <= code_start or abs_start >= code_end:
                            if '\n' not in word_phrase:  # Only proceed if no newlines in the match
                                removals.append((abs_start, abs_end, f"TACTIC_NAME_FUZZY:{word_phrase}"))
            
            # Look for individual significant words from tactic names
            tactic_words = [w for w in tactic_name.lower().split() if len(w) >= 4]
            for word in tactic_words:
                # Use word boundary regex to find exact word matches
                word_pattern = re.compile(r'\b' + re.escape(word) + r'\b', re.IGNORECASE)
                for word_match in word_pattern.finditer(window_text):
                    abs_start = window_start + word_match.start()
                    abs_end = window_start + word_match.end()
                    
                    # Don't overlap with the TTP code itself
                    if abs_end <= code_start or abs_start >= code_end:
                        # Check that this word isn't part of a common word that might be a false positive
                        matched_word = word_match.group(0).lower()
                        if matched_word not in ['and', 'the', 'for', 'with', 'from', 'into', 'over', 'control', 'system', 'data']:
                            removals.append((abs_start, abs_end, f"TACTIC_WORD:{word_match.group(0)}"))
    
    # Sort removals by start position in reverse order (so we can remove from end to start)
    removals.sort(key=lambda x: x[0], reverse=True)
    
    # Remove duplicates and overlapping ranges, prioritizing longer matches
    # Sort by length (descending) first, then by position
    removals.sort(key=lambda x: (-(x[1] - x[0]), x[0]))
    
    filtered_removals = []
    for start, end, label in removals:
        # Check if this removal overlaps with any already added
        overlaps = False
        for existing_start, existing_end, _ in filtered_removals:
            if not (end <= existing_start or start >= existing_end):
                overlaps = True
                break
        
        if not overlaps:
            filtered_removals.append((start, end, label))
    
    # Sort filtered removals by position in reverse order for safe removal
    filtered_removals.sort(key=lambda x: x[0], reverse=True)
    
    # Apply removals from end to start to preserve positions
    redacted_text = text
    for start, end, label in filtered_removals:
        redacted_text = redacted_text[:start] + redacted_text[end:]
    
    # Clean up extra whitespace that might be left behind, but preserve newlines
    # Only collapse multiple spaces/tabs on the same line
    # redacted_text = re.sub(r'[ \t]+', ' ', redacted_text)  # Collapse multiple spaces/tabs to single space
    # redacted_text = re.sub(r'\n\s*\n', '\n\n', redacted_text)  # Normalize multiple newlines to double newlines
    # redacted_text = redacted_text.strip()  # Remove leading/trailing whitespace
    
    return redacted_text



# -----------------------------------------------------------------------------
# CLI for manual checks
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    import argparse
    EXAMPLES = r"""
Examples:

Presence / name check (non-deprecated):
  python attack_lookup.py --check T1574.014

Deprecation checks:
  # No replacement suggested
  python attack_lookup.py --depr T1051
  # Multiple replacements
  python attack_lookup.py --depr T1108
  # Single replacement
  python attack_lookup.py --depr T1064

Text extraction (loose):
  python attack_lookup.py --loose "T1059.001 and T1547.014 and 1003 and 1 0 9 0 and TA 0 0 0 1 / 1234 and 1 90 0/003 and T1051 and T1108 and T1064"

Strict extraction (from markdown/URLs):
  python attack_lookup.py --strict "**This technique has been deprecated. Please use [Command and Scripting Interpreter](https://attack.mitre.org/techniques/T1059) where appropriate.**" some other TTP code T1547.011 to show we're looking inside the **'s
"""

    ap = argparse.ArgumentParser(
        description="ATT&CK STIX utilities (simplified)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=EXAMPLES,
    )
    ap.add_argument("--refresh", action="store_true", help="Force refresh of STIX JSONs")
    ap.add_argument("--ensure", action="store_true", help="Stage and verify the configured STIX snapshot")
    ap.add_argument("--check", metavar="CODE", help="Check presence and name for CODE")
    ap.add_argument("--loose", metavar="TEXT", help="Run loose extractor on TEXT")
    ap.add_argument("--strict", metavar="TEXT", help="Run strict extractor on TEXT (bold+URLs)")
    ap.add_argument("--depr", metavar="CODE", help="Show deprecation details for CODE")
    ap.add_argument("--parse", metavar="TEXT", help="Parse TEXT for all label types")
    ap.add_argument("--decoupled", action="store_true", help="(for --strict) Enable decoupled sub-tech extraction")
    ap.add_argument("--details", metavar="CODE",
                help="Show full details for CODE (namespaces, descriptions, link, deprecation)")

    args = ap.parse_args()

    ensure_stix_data(force_refresh=args.refresh)

    if args.check:
        code = args.check
        print("[is_valid]", is_valid_ttp_code(code))
        if is_valid_ttp_code(code):
            try:
                print("[name]", get_ttp_name(code))
                print("[deprecated]", is_deprecated(code))
                print("[revoked_replacement]", revoked_check_and_replacement(code))
            except Exception as e:
                print("[name/error]", e)

    if args.loose:
        raw, valid = extract_ttps_loose(args.loose)
        print("[loose/raw]", sorted(raw))
        print("[loose/valid]", sorted(valid))

    if args.strict:
        text = _cli_unescape(args.strict)
        print("[strict]", extract_ttps(text, decouple=args.decoupled, decouple_require_name_hit=False, return_sent_list=True))

    if args.depr:
        print("[deprecation]", get_deprecation_revocation_details(args.depr))

    if args.details:
        print(json.dumps(get_ttp_details(args.details), indent=2, ensure_ascii=False))

    if args.parse:
        valid, raw, depr = parse_text_for_labels(args.parse)
        print("[parse/valid]", sorted(valid))
        print("[parse/raw]", sorted(raw))
        print("[parse/deprecated/revoked]", depr)

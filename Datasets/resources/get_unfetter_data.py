from pathlib import Path
import argparse
import pickle
import requests
import sys
import re
import csv
import json
from typing import Dict, List, Set, Tuple

from Framework.utils.rcatt_ttp_map import ALL_TTPS, NAME_TACTICS, CODE_TACTICS


ALL_TTPS = list(ALL_TTPS)
NAME_TACTICS = list(NAME_TACTICS)
CODE_TACTICS = list(CODE_TACTICS)

_WS = re.compile(r"\s+")

def _norm_name(s: str) -> str:
    """Normalize tactic names for lookup: collapse spaces and lower-case."""
    return _WS.sub(" ", s.strip()).lower()

NAME_TO_TACTIC = { _norm_name(n): CODE_TACTICS[i] for i, n in enumerate(NAME_TACTICS) }

# ---- Parsers for embedded labels ----
RE_TECH_AT_START = re.compile(r"^\s*(T\d{4})\b", re.IGNORECASE)
# capture content after "Tactic|" up to >=4 consecutive spaces which denote the next field
RE_TACTIC_FIELD  = re.compile(r"Tactic\|\s+(.+?)\s{4,}", re.IGNORECASE | re.DOTALL)


def _parse_labels_and_strip(raw: str) -> Tuple[Set[str], str]:
    text = raw
    codes: Set[str] = set()

    # 1) Technique code at start
    m = RE_TECH_AT_START.match(text)
    if m:
        tech = m.group(1).upper()
        # remove the leading token from text
        text = text[m.end():]
        codes.add(tech)
    else:
        raise ValueError("No leading technique code (T####) found at start of entry")

    # 2) Tactic names field
    m2 = RE_TACTIC_FIELD.search(text)
    if m2:
        tactic_names_blob = m2.group(1)
        # remove the field from the text
        span = m2.span()
        text = text[:span[0]] + text[span[1]:]

        # Split by comma, normalize, map to codes
        for nm in tactic_names_blob.split(','):
            key = _norm_name(nm)
            if not key:
                continue
            code = NAME_TO_TACTIC.get(key)
            if not code:
                raise ValueError(f"Unknown tactic name: {nm!r}")
            codes.add(code)

    # 3) Validate codes against ALL_TTPS
    unknown = [c for c in codes if c not in ALL_TTPS]
    if unknown:
        raise ValueError(f"Parsed TTP codes not in ALL_TTPS: {unknown}")

    # 4) minor cleanup
    text = _WS.sub(' ', text).strip()
    return codes, text


def process_entry(raw_text: str) -> Tuple[str, Set[str]]:
    """Return (clean_text, parsed_codes_set). No Unfetter-wide preprocessing.

    Your main does: processed.append((label, process_entry(raw))).
    """
    codes, clean = _parse_labels_and_strip(raw_text)
    return (clean, codes)


def export_csv(processed: List[Tuple[str, Tuple[str, Set[str]]]], out_path: str) -> None:
    """Write CSV with header: Text + ALL_TTPS. Ignore the first element (label)."""
    header = ["Text"] + ALL_TTPS
    with open(out_path, 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(header)
        for _label, payload in processed:
            # payload is (clean_text, codes_set)
            clean_text, codes = payload
            row = [clean_text] + [1 if code in codes else 0 for code in ALL_TTPS]
            w.writerow(row)


def export_jsonl(processed: List[Tuple[str, Tuple[str, Set[str]]]], out_path: str) -> None:
    """Write JSONL with keys: Text and each code in ALL_TTPS."""
    with open(out_path, 'w', encoding='utf-8') as f:
        for _label, payload in processed:
            clean_text, codes = payload
            obj = {"Text": clean_text}
            for code in ALL_TTPS:
                obj[code] = 1 if code in codes else 0
            f.write(json.dumps(obj, ensure_ascii=False) + "\n")


def main():
    ap = argparse.ArgumentParser(description="Export preprocessed Unfetter wiki_dict -> CSV/JSONL")
    ap.add_argument('--out', default="unfetter_wiki_preprocessed.csv", help="Output file path, e.g., unfetter_wiki_preprocessed.csv")
    ap.add_argument('--fmt', choices=['csv', 'jsonl'], default='csv', help="Output format (default: csv)")
    args = ap.parse_args()

    # load wiki dict
    wiki_path = Path(args.out).parent / 'dict_wiki'
    if not wiki_path.exists():
        # download dict_wiki from unfetter (write **bytes** for pickle)
        url = "https://raw.githubusercontent.com/unfetter-discover/unfetter-insight/develop/babelfish/dict_wiki"
        response = requests.get(url)
        response.raise_for_status()
        with open(wiki_path, "wb") as f:
            f.write(response.content)

    with open(wiki_path, "rb") as f:
        try:
            wiki = pickle.load(f)  # works if protocol is compatible
        except UnicodeDecodeError:
            f.seek(0)
            wiki = pickle.load(f, encoding="latin1")  # Python2→3 compatibility

    processed: List[Tuple[str, Tuple[str, Set[str]]]] = []
    for label, raw in wiki.items():
        try:
            processed.append((str(label), process_entry(raw)))
        except Exception as e:
            print(f"[WARN] Failed to process label={label!r}: {e}", file=sys.stderr)

    if args.fmt == 'csv':
        export_csv(processed, args.out)
    else:
        export_jsonl(processed, args.out)

    print(f"Done. Wrote {len(processed)} items to {args.out}")


if __name__ == '__main__':
    main()

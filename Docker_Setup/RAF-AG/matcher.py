#!/usr/bin/env python3
# gets the sentence text from the output json of RAF-AG
import argparse, json, re
from pathlib import Path

def norm_key(p: Path):
    return re.sub(r'[^a-z0-9]+', '', p.stem.lower())

def load_decoding_json(p: Path):
    with open(p, 'r', encoding='utf-8') as f:
        return json.load(f)

def load_report_jsonl(p: Path):
    with open(p, 'r', encoding='utf-8') as f:
        first_line = next(f).strip()
        return json.loads(first_line)

def build_sent_index_to_text(report_obj):
    out = {}
    sents = report_obj.get('sentences')
    if isinstance(sents, list):
        for s in sents:
            si = s.get('id') if isinstance(s.get('id'), int) else s.get('sent_index')
            txt = s.get('text') or s.get('sentence') or s.get('content')
            if isinstance(si, int) and isinstance(txt, str):
                out[si] = txt.strip()
    return out

def iter_best_entries(decoded_obj, use='best'):
    d = decoded_obj.get(use) or {}
    for path_node, items in d.items():
        if not isinstance(items, list): 
            continue
        for it in items:
            tech_id = it.get('techID') or it.get('techId')
            tech_name = it.get('tech_name') or it.get('techName')
            score = it.get('value')
            sent_indexes = it.get('sent_indexes') or []
            loc = it.get('location')
            yield path_node, tech_id, tech_name, score, sent_indexes, loc

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--decoding_dir', default='data/campaign/decoding_result')
    ap.add_argument('--jsonl_dir', default='data/campaign/output')
    ap.add_argument('--section', default='best', choices=['best','k2'])
    ap.add_argument('--out', required=True)
    ap.add_argument('--only_base', help='Match just this input basename when called by the bulk adapter')
    args = ap.parse_args()

    dec_dir = Path(args.decoding_dir)
    jl_dir  = Path(args.jsonl_dir)
    outp    = Path(args.out)

    dec_map = {}
    for p in dec_dir.glob('*.json'):
        if args.only_base and norm_key(p) != norm_key(Path(args.only_base)):
            continue
        dec_map.setdefault(norm_key(p), []).append(p)

    jl_map = {}
    for p in jl_dir.glob('*.jsonl'):
        jl_map[norm_key(p)] = p

    rows = []
    for key, json_paths in dec_map.items():
        jl_path = jl_map.get(key)
        if not jl_path:
            continue
        report_obj = load_report_jsonl(jl_path)
        idx2text = build_sent_index_to_text(report_obj)
        for jp in json_paths:
            decoded = load_decoding_json(jp)
            for path_node, tech_id, tech_name, score, sent_idxs, loc in iter_best_entries(decoded, args.section):
                for si in sent_idxs:
                    txt = idx2text.get(si, '')
                    rows.append({
                        'report': jl_path.stem,
                        'decoding_file': jp.name,
                        'section': args.section,
                        'path_node': path_node,
                        'location': loc,
                        'technique_id': tech_id,
                        'technique_name': tech_name,
                        'confidence': score,
                        'sentence_index': si,
                        'sentence_text': txt
                    })

    rows.sort(key=lambda r: (r['report'], int(re.sub(r'[^0-9]', '', r['path_node']) or 0), r['technique_id'] or '', r['sentence_index']))
    
    outp.parent.mkdir(parents=True, exist_ok=True)
    with open(outp, 'w', encoding='utf-8') as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)

if __name__ == '__main__':
    main()

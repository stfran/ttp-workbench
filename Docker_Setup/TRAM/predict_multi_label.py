#!/usr/bin/env python
"""
predict_multi_label.py  —  CLI wrapper for the SciBERT multi-label TTP classifier.

Usage
-----
  python predict_multi_label.py input.txt                # default n=13 stride=5 thr=0.9
  python predict_multi_label.py input.txt --n 20 --stride 10 --thr 0.8
  cat input.txt | python predict_multi_label.py -        # read from stdin
"""

import transformers
import torch
import pandas as pd
import argparse
import sys
import json
import numpy as np


# ---------------- Helpers ----------------
def create_chunks(text:str, n:int, stride:int):
    words = text.split()
    return [' '.join(words[i:i+n]) for i in range(0, len(words), stride)]

def predict(text:str, n:int, stride:int, thr:float):
    chunks = create_chunks(text, n, stride)
    toks   = TOKEN(chunks, return_tensors="pt", padding="max_length",
                   truncation=True, max_length=512).input_ids
    preds  = []
    with torch.no_grad():
        for i in range(0, toks.size(0), 10):
            x = toks[i:i+10].to(DEVICE)
            logits = MODEL(x, attention_mask=x.ne(TOKEN.pad_token_id).int()).logits
            preds.extend(logits.sigmoid().cpu())
    probs = pd.DataFrame(torch.vstack(preds), columns=CLASSES, index=chunks)
    result = [
        {
            "text":seg,
            "predictions":[
                {
                    "code": t,
                    "TTP":  ID_TO_NAME.get(t, t),
                    "Prob": round(
                        float(
                            (probs.loc[seg, t].iloc[0]    # if Series
                            if isinstance(probs.loc[seg, t], pd.Series)
                            else probs.loc[seg, t])      # if scalar
                        ) * 100, 2)
                }
                for t, v in row.items() if v
            ],
            "source":"TRAM predict_multi_label"
        }
        for seg, row in probs.gt(thr).T.to_dict().items()
    ]
    return result

# ---------------- Model ----------------
DEVICE = torch.device("cuda" if torch.cuda.is_available() else "cpu")
MODEL  = transformers.BertForSequenceClassification.from_pretrained(
            "scibert_multi_label_model").to(DEVICE).eval()
TOKEN  = transformers.BertTokenizer.from_pretrained(
            "allenai/scibert_scivocab_uncased")

CLASSES = (
    'T1003.001','T1005','T1012','T1016','T1021.001','T1027','T1033','T1036.005',
    'T1041','T1047','T1053.005','T1055','T1056.001','T1057','T1059.003','T1068',
    'T1070.004','T1071.001','T1072','T1074.001','T1078','T1082','T1083','T1090',
    'T1095','T1105','T1106','T1110','T1112','T1113','T1140','T1190','T1204.002',
    'T1210','T1218.011','T1219','T1484.001','T1518.001','T1543.003',
    'T1547.001','T1548.002','T1552.001','T1557.001','T1562.001','T1564.001',
    'T1566.001','T1569.002','T1570','T1573.001','T1574.002'
)

ID_TO_NAME = {"T1055": "Process Injection", "T1110": "Brute Force", "T1055.004": "Asynchronous Procedure Call", "T1047": "Windows Management Instrumentation", "T1078": "Valid Accounts", "T1140": "Deobfuscate/Decode Files or Information", "T1016": "System Network Configuration Discovery", "T1057": "Process Discovery", "T1078.004": "Cloud Accounts", "T1518.001": "Security Software Discovery", "T1090.001": "Internal Proxy", "T1078.001": "Default Accounts", "T1071.001": "Web Protocols", "T1082": "System Information Discovery", "T1110.003": "Password Spraying", "T1484.001": "Group Policy Modification", "T1106": "Native API", "T1027.008": "Stripped Payloads", "T1548.002": "Bypass User Account Control", "T1105": "Ingress Tool Transfer", "T1033": "System Owner/User Discovery", "T1569.002": "Service Execution", "T1566.001": "Spearphishing Attachment", "T1059.003": "Windows Command Shell", "T1053.005": "Scheduled Task", "T1547.001": "Registry Run Keys / Startup Folder", "T1041": "Exfiltration Over C2 Channel", "T1210": "Exploitation of Remote Services", "T1005": "Data from Local System", "T1219": "Remote Access Software", "T1552.001": "Credentials In Files", "T1068": "Exploitation for Privilege Escalation", "T1543.003": "Windows Service", "T1570": "Lateral Tool Transfer", "T1027": "Obfuscated Files or Information", "T1113": "Screen Capture", "T1078.003": "Local Accounts", "T1012": "Query Registry", "T1055.002": "Portable Executable Injection", "T1573.001": "Symmetric Cryptography", "T1055.001": "Dynamic-link Library Injection", "T1072": "Software Deployment Tools", "T1027.001": "Binary Padding", "T1190": "Exploit Public-Facing Application", "T1218.011": "Rundll32", "T1090.003": "Multi-hop Proxy", "T1055.012": "Process Hollowing", "T1056.001": "Keylogging", "T1055.008": "Ptrace System Calls", "T1204.002": "Malicious File", "T1083": "File and Directory Discovery", "T1070.004": "File Deletion", "T1110.004": "Credential Stuffing", "T1036.005": "Match Legitimate Name or Location", "T1574.002": "DLL Side-Loading", "T1090": "Proxy", "T1027.003": "Steganography", "T1027.007": "Dynamic API Resolution", "T1074.001": "Local Data Staging", "T1090.002": "External Proxy", "T1564.001": "Hidden Files and Directories", "T1021.001": "Remote Desktop Protocol", "T1112": "Modify Registry", "T1027.005": "Indicator Removal from Tools", "T1003.001": "LSASS Memory", "T1027.002": "Software Packing", "T1090.004": "Domain Fronting", "T1562.001": "Disable or Modify Tools", "T1027.006": "HTML Smuggling", "T1095": "Non-Application Layer Protocol", "T1027.009": "Embedded Payloads", "T1078.002": "Domain Accounts"}

def jsonify(o):
    if isinstance(o, (np.floating, np.integer)):
        return o.item()                 # NumPy → Python scalar
    if isinstance(o, pd.Series):
        return o.tolist()               # or o.mean(), .iloc[0], etc.
    if isinstance(o, set):
        return list(o)                  # sets aren’t JSON either
    raise TypeError                     # let json.dump raise for everything else



# ---------------- CLI ----------------
def get_args():
    p = argparse.ArgumentParser()
    p.add_argument("--textfile", type=str, help="Plain-text file or '-' for stdin")
    p.add_argument("--dirin", type=str, help="Input directory for bulk processing")
    p.add_argument("--dirout", type=str, help="Output directory for bulk processing")
    p.add_argument("--n",      type=int,   default=13,  help="Sub-sequence length")
    p.add_argument("--stride", type=int,   default=5,   help="Stride size")
    p.add_argument("--thr",    type=float, default=0.5, help="Probability threshold. (Default 0.5 as in the Docker version)")
    p.add_argument("--outfile", type=str, default="outfile.json", help="Specify the outfile name as a json")
    p.add_argument("--verbose", action="store_true", default=False)
    return p.parse_args()

if __name__ == "__main__":
    args = get_args()
    if args.dirin and args.dirout:
        import os
        from pathlib import Path
        input_dir  = Path(args.dirin)
        output_dir = Path(args.dirout)
        output_dir.mkdir(parents=True, exist_ok=True)
        
        for idx, infile in enumerate(os.listdir(input_dir)):
            if args.verbose:
                print(f"[INFO] Processing file {idx+1}/{len(os.listdir(input_dir))}: {infile}")
            if not infile.endswith('.txt'):
                continue
            with open(input_dir / infile, 'r', encoding='utf-8') as f:
                raw = f.read()
            if args.verbose: print(f"[DEBUG] Read {len(raw)} characters from {infile}")
            result = predict(raw, args.n, args.stride, args.thr)
            if args.verbose: print(f"[DEBUG] Generated {len(result)} prediction segments for {infile}")
            outfile = output_dir / f"{Path(infile).stem}_predictions.json"
            if args.verbose: print(f"[INFO] Writing predictions to {outfile}")
            try:
                with open(outfile, 'w', encoding='utf-8') as f:
                    json.dump(result, f, indent=4, default=jsonify)
            except Exception as e:
                print(f"[ERROR] Failed to write predictions to {outfile}: {e}")
        sys.exit(0)
    raw  = sys.stdin.read() if args.textfile == "-" else open(args.textfile,"r",encoding="utf-8").read()    
    result   = predict(raw, args.n, args.stride, args.thr)
    if args.verbose:
        for prediction in result:
            print([
                (p["code"], p["TTP"], p["Prob"])
                for p in prediction['predictions']
            ])
            print("\t", prediction['text'])
    with open(args.outfile, 'w') as f:
        json.dump(result, f, indent=4, default=jsonify)

""" FROM THE NOTEBOOK

pd.set_option('display.max_columns', None)
pd.set_option('display.max_rows', None)
pd.set_option('display.max_colwidth', None)

device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')

bert = transformers.BertForSequenceClassification.from_pretrained('scibert_multi_label_model').to(device).eval()
tokenizer = transformers.BertTokenizer.from_pretrained('allenai/scibert_scivocab_uncased')

CLASSES = (
    'T1003.001', 'T1005', 'T1012', 'T1016', 'T1021.001', 'T1027',
    'T1033', 'T1036.005', 'T1041', 'T1047', 'T1053.005', 'T1055',
    'T1056.001', 'T1057', 'T1059.003', 'T1068', 'T1070.004',
    'T1071.001', 'T1072', 'T1074.001', 'T1078', 'T1082', 'T1083',
    'T1090', 'T1095', 'T1105', 'T1106', 'T1110', 'T1112', 'T1113',
    'T1140', 'T1190', 'T1204.002', 'T1210', 'T1218.011', 'T1219',
    'T1484.001', 'T1518.001', 'T1543.003', 'T1547.001', 'T1548.002',
    'T1552.001', 'T1557.001', 'T1562.001', 'T1564.001', 'T1566.001',
    'T1569.002', 'T1570', 'T1573.001', 'T1574.002'
)

ID_TO_NAME = {"T1055": "Process Injection", "T1110": "Brute Force", "T1055.004": "Asynchronous Procedure Call", "T1047": "Windows Management Instrumentation", "T1078": "Valid Accounts", "T1140": "Deobfuscate/Decode Files or Information", "T1016": "System Network Configuration Discovery", "T1057": "Process Discovery", "T1078.004": "Cloud Accounts", "T1518.001": "Security Software Discovery", "T1090.001": "Internal Proxy", "T1078.001": "Default Accounts", "T1071.001": "Web Protocols", "T1082": "System Information Discovery", "T1110.003": "Password Spraying", "T1484.001": "Group Policy Modification", "T1106": "Native API", "T1027.008": "Stripped Payloads", "T1548.002": "Bypass User Account Control", "T1105": "Ingress Tool Transfer", "T1033": "System Owner/User Discovery", "T1569.002": "Service Execution", "T1566.001": "Spearphishing Attachment", "T1059.003": "Windows Command Shell", "T1053.005": "Scheduled Task", "T1547.001": "Registry Run Keys / Startup Folder", "T1041": "Exfiltration Over C2 Channel", "T1210": "Exploitation of Remote Services", "T1005": "Data from Local System", "T1219": "Remote Access Software", "T1552.001": "Credentials In Files", "T1068": "Exploitation for Privilege Escalation", "T1543.003": "Windows Service", "T1570": "Lateral Tool Transfer", "T1027": "Obfuscated Files or Information", "T1113": "Screen Capture", "T1078.003": "Local Accounts", "T1012": "Query Registry", "T1055.002": "Portable Executable Injection", "T1573.001": "Symmetric Cryptography", "T1055.001": "Dynamic-link Library Injection", "T1072": "Software Deployment Tools", "T1027.001": "Binary Padding", "T1190": "Exploit Public-Facing Application", "T1218.011": "Rundll32", "T1090.003": "Multi-hop Proxy", "T1055.012": "Process Hollowing", "T1056.001": "Keylogging", "T1055.008": "Ptrace System Calls", "T1204.002": "Malicious File", "T1083": "File and Directory Discovery", "T1070.004": "File Deletion", "T1110.004": "Credential Stuffing", "T1036.005": "Match Legitimate Name or Location", "T1574.002": "DLL Side-Loading", "T1090": "Proxy", "T1027.003": "Steganography", "T1027.007": "Dynamic API Resolution", "T1074.001": "Local Data Staging", "T1090.002": "External Proxy", "T1564.001": "Hidden Files and Directories", "T1021.001": "Remote Desktop Protocol", "T1112": "Modify Registry", "T1027.005": "Indicator Removal from Tools", "T1003.001": "LSASS Memory", "T1027.002": "Software Packing", "T1090.004": "Domain Fronting", "T1562.001": "Disable or Modify Tools", "T1027.006": "HTML Smuggling", "T1095": "Non-Application Layer Protocol", "T1027.009": "Embedded Payloads", "T1078.002": "Domain Accounts"}

COUNT = count(1)

def create_subsequences(document: str, n: int = 13, stride: int = 5) -> list[str]:
    words = document.split()
    subsequences = [' '.join(words[i:i+n]) for i in range(0, len(words), stride)]
    return subsequences

def predict_document(document: str, threshold: float = 0.5, n: int = 13, stride: int = 5):
    text_instances = create_subsequences(document, n, stride)
    tokenized_instances = tokenizer(text_instances, return_tensors='pt', padding='max_length', truncation=True, max_length=512).input_ids

    predictions = []
    batch_size = 10
    slice_starts = tqdm(list(range(0, tokenized_instances.shape[0], batch_size)))

    with torch.no_grad():
        for i in slice_starts:
            x = tokenized_instances[i : i + batch_size].to(device)
            out = bert(x, attention_mask=x.ne(tokenizer.pad_token_id).to(int))
            predictions.extend(out.logits.sigmoid().to('cpu'))

    probabilities = pd.DataFrame(
        torch.vstack(predictions),
        columns=CLASSES,
        index=text_instances
    )

    result: list[tuple[str, set[str]]] = [
        (text, {ID_TO_NAME[k] + ' - ' + k for k, v in clses.items() if v})
        for text, clses in
        probabilities.gt(threshold).T.to_dict().items()
    ]

    result_iter = iter(result)
    current_text, current_labels = next(result_iter)
    overlap = n_selector.value - stride_selector.value
    out = []

    for text, labels in result_iter:
        if labels != current_labels:
            out.append((current_text, current_labels))
            current_text = text
            current_labels = labels
            continue
        current_text += ' ' + ' '.join(text.split()[overlap:])

    out_df = pd.DataFrame(out)
    out_df.columns = ['segment', 'label(s)']
    return out_df

def parse_text(file_name: str, content: io.BytesIO) -> str:
    if file_name.endswith('.pdf'):
        with pdfplumber.open(content) as pdf:
            text = " ".join(page.extract_text() for page in pdf.pages)
    elif file_name.endswith('.html'):
        text = BeautifulSoup(content.read().decode('utf-8'), features="html.parser").get_text()
    elif file_name.endswith('.txt'):
        text = content.read().decode('utf-8')
    elif file_name.endswith('.docx'):
        text = " ".join(paragraph.text for paragraph in docx.Document(content).paragraphs)

    cleaned_text = re.sub(r'\s+', ' ', text).strip()
    return cleaned_text

dfs = []
for name, content in zip(upload.value, upload.data):
    text = parse_text(name, io.BytesIO(content))
    prediction_df = predict_document(text, threshold_selector.value, n_selector.value, stride_selector.value)
    prediction_df['name'] = name
    dfs.append(prediction_df)

predicted = pd.concat(dfs).reset_index(drop=True)
i = next(COUNT)
output_file_name = f"./output-{i}.json"
predicted.to_json(output_file_name, orient='table')

predicted
"""